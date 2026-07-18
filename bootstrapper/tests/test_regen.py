"""Tests for bootstrapper.docs.regen CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "docs.regen", *args]
    env = {"PYTHONPATH": str(REPO_ROOT / "bootstrapper")}
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, env={**__import__('os').environ, **env})


def test_help_flag_prints_usage_and_exits_zero():
    r = _run("--help")
    assert r.returncode == 0
    assert "usage" in r.stdout.lower()


def test_single_service_writes_three_files(tmp_path, monkeypatch):
    """regen hermes writes README.md (deps section), architecture.html, .svg."""
    r = _run("hermes", "--out-root", str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "hermes" / "README.md").is_file()
    assert (tmp_path / "hermes" / "architecture.html").is_file()
    assert (tmp_path / "hermes" / "architecture.svg").is_file()


def test_section_only_skips_diagrams(tmp_path):
    r = _run("hermes", "--out-root", str(tmp_path), "--section-only")
    assert r.returncode == 0
    assert (tmp_path / "hermes" / "README.md").is_file()
    assert not (tmp_path / "hermes" / "architecture.svg").exists()


def test_dry_run_writes_nothing(tmp_path):
    r = _run("hermes", "--out-root", str(tmp_path), "--dry-run")
    assert r.returncode == 0
    assert not (tmp_path / "hermes").exists()
    assert "would write" in r.stdout.lower()


def test_check_mode_exits_2_on_drift(tmp_path):
    """--check returns 2 when a committed artifact disagrees with current manifests.

    Seed a known-stale README at <out-root>/<svc>/README.md (with placeholder
    content that won't match what regen would produce), then assert --check
    reports drift. Without the seed, --check still exits 2 because the
    missing-artifact path also counts as drift, but that's a weaker contract
    than the docstring implies.
    """
    svc_dir = tmp_path / "hermes"
    svc_dir.mkdir()
    (svc_dir / "README.md").write_text("# stale placeholder — manifest content differs\n")
    r = _run("hermes", "--out-root", str(tmp_path), "--check")
    assert r.returncode == 2, f"expected drift exit code 2, got {r.returncode}: {r.stdout}"


def test_all_processes_21_doc_folders(tmp_path):
    """--all iterates every doc folder under services/ and writes
    artifacts to <out-root>/<doc-folder>/."""
    r = _run("--all", "--out-root", str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    written = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert len(written) >= 20


def test_future_block_with_backslash_splices_literally():
    """Regression: user-authored Future content containing a backslash (a
    `\\d` regex example or a Windows path like C:\\Users) must be spliced
    verbatim. The old `re.sub(r"\\1" + body, ...)` template interpreted
    escapes in `body` — `\\d` raised re.error and aborted the whole
    --all/CI run, and a `\\1` would splice the captured heading mid-body."""
    sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
    from docs.regen import _render_section_with_future
    from docs.deps_resolver import build_doc_graph

    g = build_doc_graph("hermes", REPO_ROOT / "services")
    body = "- Regex `\\d+` and Windows path `C:\\Users\\me` must survive verbatim."
    existing = (
        "## 5. Dependencies & Integrations\n\n"
        "### 5.4 Future — Missing pair integrations\n\n"
        f"{body}\n\n"
        "### 5.5 Future — Candidate new services\n\n"
        "_No high-confidence opportunities identified._\n\n"
        "### 5.6 Future — Unused features in this service\n\n"
        "_No high-confidence opportunities identified._\n"
    )
    # Must not raise re.error, and must splice the body literally.
    out = _render_section_with_future(g, existing)
    assert "`\\d+`" in out
    assert r"C:\Users\me" in out


def test_future_block_with_fenced_heading_is_not_split():
    """Regression: a `Future — …` subsection body containing a column-0 `## ` or
    `### ` line inside a code fence must be preserved verbatim. The old
    fence-unaware slicer treated the fenced heading as a real section boundary,
    truncating the body / splicing the regenerated block inside the fence and
    orphaning the file tail on the next regen write."""
    sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
    from docs.regen import (
        _extract_future_blocks,
        _render_section_with_future,
        _slice_deps_section,
    )
    from docs.deps_resolver import build_doc_graph

    g = build_doc_graph("hermes", REPO_ROOT / "services")
    fenced_body = (
        "Opportunity: expose a config snippet, e.g.\n\n"
        "```yaml\n"
        "## upstream section header inside a fence\n"
        "### nested example heading\n"
        "service: hermes\n"
        "```\n\n"
        "Trailing prose after the fence."
    )
    existing = (
        "## 5. Dependencies & Integrations\n\n"
        "### 5.4 Future — Missing pair integrations\n\n"
        f"{fenced_body}\n\n"
        "### 5.5 Future — Candidate new services\n\n"
        "_No high-confidence opportunities identified._\n\n"
        "### 5.6 Future — Unused features in this service\n\n"
        "_No high-confidence opportunities identified._\n\n"
        "## 6. Troubleshooting\n\n"
        "Some real trailing section.\n"
    )

    # The deps slice must extend past the fenced `## ` to the real `## 6.`,
    # keeping the later Future subsection inside the block (it would be lost
    # without fence-awareness).
    sl = _slice_deps_section(existing)
    assert sl is not None
    block = existing[sl[0]: sl[1]]
    assert "## 6. Troubleshooting" not in block
    assert "### 5.6 Future" in block

    # The full fenced body (both fenced heading lines + trailing prose) is
    # captured for the Missing-pair-integrations subsection.
    future = _extract_future_blocks(block)
    mpi = future["Missing pair integrations"]
    assert "## upstream section header inside a fence" in mpi
    assert "### nested example heading" in mpi
    assert "Trailing prose after the fence." in mpi

    # And the regen splice preserves it verbatim without leaking the real
    # trailing section into the deps block.
    out = _render_section_with_future(g, existing)
    assert "## upstream section header inside a fence" in out
    assert "Trailing prose after the fence." in out
