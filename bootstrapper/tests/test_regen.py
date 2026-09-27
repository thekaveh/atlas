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


# --- #1190: closed candidates leave every derived index ---------------------


def _future_readme(candidate_body: str) -> str:
    return (
        "## 5. Dependencies & Integrations\n\n"
        "### 5.4 Future — Missing pair integrations\n\n"
        "_No high-confidence opportunities identified._\n\n"
        "### 5.5 Future — Candidate new services\n\n"
        f"{candidate_body}\n\n"
        "### 5.6 Future — Unused features in this service\n\n"
        "_No high-confidence opportunities identified._\n"
    )


def _candidate_block(section: str) -> str:
    start = section.index("Future — Candidate new services")
    return section[start: section.index("Future — Unused features", start)]


def test_candidate_bullets_linking_closed_records_are_dropped():
    sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
    from docs.deps_resolver import build_doc_graph
    from docs.regen import PLACEHOLDER_LINE, _render_section_with_future

    g = build_doc_graph("hermes", REPO_ROOT / "services")
    closed = "- **Closed** ([details](../../docs/research/candidates/closed-one.md)) — gone."
    wrapped = "  continuation of the closed bullet"
    kept = "- **Open** ([details](../../docs/research/candidates/open-one.md)) — stays."

    mixed = _candidate_block(_render_section_with_future(
        g, _future_readme(f"{closed}\n{wrapped}\n{kept}"), frozenset({"closed-one"})
    ))
    emptied = _candidate_block(_render_section_with_future(
        g, _future_readme(closed), frozenset({"closed-one"})
    ))
    untouched = _candidate_block(_render_section_with_future(
        g, _future_readme(closed), frozenset()
    ))

    assert "closed-one.md" not in mixed and wrapped not in mixed
    assert kept in mixed
    assert PLACEHOLDER_LINE in emptied and "closed-one.md" not in emptied
    assert closed in untouched


def _link(slug: str) -> str:
    return f"[details](../../docs/research/candidates/{slug}.md)"


def test_pruning_follows_commonmark_list_items():
    """A loose bullet's paragraphs go with it; fences outside the list and a
    bullet that still links an open record are left alone."""
    sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
    from docs.regen import _drop_closed_candidates

    closed = frozenset({"gone"})
    loose = f"- **Gone** {_link('gone')}\n\n  More about Gone.\n\n- **Kept** {_link('kept')}"
    fenced = (f"- **Gone** {_link('gone')}\n\n```text\n- **Gone** {_link('gone')}\n\n\n"
              f"spacing kept\n```\n\n- **Kept** {_link('kept')}")
    middle = f"- A {_link('kept')}\n\n- B {_link('gone')}\n\n- C {_link('open')}"
    mixed = f"- **Both** {_link('gone')} and {_link('kept')}"

    assert _drop_closed_candidates(loose, closed) == f"- **Kept** {_link('kept')}"
    assert _drop_closed_candidates(fenced, closed) == fenced.split("\n\n", 1)[1]
    assert _drop_closed_candidates(middle, closed) == (
        f"- A {_link('kept')}\n\n- C {_link('open')}"
    )
    assert _drop_closed_candidates(mixed, closed) == mixed


def _copy_derived_tree(tmp_path: Path) -> tuple[Path, Path]:
    import shutil

    research = tmp_path / "research"
    shutil.copytree(REPO_ROOT / "docs" / "research" / "rows", research / "rows")
    shutil.copytree(REPO_ROOT / "docs" / "research" / "candidates", research / "candidates")
    shutil.copy(REPO_ROOT / "docs" / "research" / "integration-matrix.md", research)
    services = tmp_path / "services"
    for readme in (REPO_ROOT / "services").glob("*/README.md"):
        (services / readme.parent.name).mkdir(parents=True)
        shutil.copy(readme, services / readme.parent.name / "README.md")
    return research, services


def _open_linked_candidate(research: Path, services: Path) -> tuple[Path, str]:
    """An open candidate some service README links, and its display name."""
    import yaml

    for record in sorted((research / "candidates").glob("*.md")):
        front = yaml.safe_load(record.read_text().split("\n---\n", 1)[0].lstrip("-\n"))
        link = f"candidates/{record.stem}.md"
        if front.get("lifecycle") in ("shipped", "rejected"):
            continue
        if any(link in readme.read_text() for readme in services.glob("*/README.md")):
            return record, front["name"]
    raise AssertionError("no open candidate is linked from a service README")


def _close(record: Path, lifecycle: str) -> None:
    import re

    text = re.sub(r"(?m)^lifecycle: \w+$", f"lifecycle: {lifecycle}", record.read_text())
    if "\ndecided:" not in text:
        text = text.replace(f"\nlifecycle: {lifecycle}", f"\ndecided: 2026-09-27\nlifecycle: {lifecycle}")
    record.write_text(text)


def test_flipping_a_candidate_to_rejected_clears_every_derived_index(tmp_path):
    """The #1190 acceptance check: one ``regen --all`` pass after a lifecycle
    flip leaves no README Future block and no matrix row naming it."""
    research, services = _copy_derived_tree(tmp_path)
    record, name = _open_linked_candidate(research, services)
    link = f"candidates/{record.stem}.md"
    derived = [research / "integration-matrix.md", *services.glob("*/README.md")]
    assert f"| {name} |" in (research / "integration-matrix.md").read_text()

    _close(record, "rejected")
    r = _run("--all", "--out-root", str(services), "--research-root", str(research),
             "--section-only")

    assert r.returncode == 0, r.stdout + r.stderr
    naming = [str(p) for p in derived if link in p.read_text()]
    assert naming == []
    assert f"| {name} |" not in (research / "integration-matrix.md").read_text()
    assert record.is_file()


def test_check_mode_reports_a_stale_research_index(tmp_path):
    research, services = _copy_derived_tree(tmp_path)
    record, _name = _open_linked_candidate(research, services)
    readmes = [p for p in services.glob("*/README.md") if record.stem + ".md" in p.read_text()]
    _close(record, "shipped")
    matrix = research / "integration-matrix.md"
    before = matrix.read_text()

    r = _run("--all", "--out-root", str(services), "--research-root", str(research),
             "--section-only", "--check")

    assert r.returncode == 2
    assert f"DRIFT: {matrix}" in r.stdout
    for readme in readmes:
        assert f"DRIFT: {readme}" in r.stdout
    assert matrix.read_text() == before


def test_a_missing_research_root_is_an_error_not_an_empty_index(tmp_path):
    r = _run("--all", "--out-root", str(tmp_path), "--research-root",
             str(tmp_path / "no-such-research"), "--check")

    assert r.returncode == 1
    assert "research root not found" in r.stderr


def test_a_relative_out_root_naming_services_still_merges_the_matrix(monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))
    import argparse

    from docs import regen

    calls = []
    monkeypatch.setattr(regen, "run_merge", lambda root, check=False: calls.append((root, check)) or [])
    monkeypatch.chdir(REPO_ROOT)
    args = argparse.Namespace(all=True, research_root=None, out_root=Path("./services"),
                              check=True, dry_run=False)

    assert regen._merge_research_index(args) == 0
    assert calls == [(regen.RESEARCH_ROOT, True)]
