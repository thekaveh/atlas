"""Tests for scripts/check_doc_links.py — internal-markdown-link validator."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.three_surface_test_utils import PROJECTION_ROOT, ensure_generated_docs

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VALIDATOR = REPO_ROOT / "scripts" / "check_doc_links.py"


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(VALIDATOR), *(str(p) for p in paths)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)


def test_validator_passes_on_clean_tree(tmp_path):
    """A directory of markdown files with valid relative links exits 0."""
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("See [B](./b.md).")
    b.write_text("See [A](./a.md).")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_flags_broken_relative_link(tmp_path):
    """A relative link that doesn't resolve exits non-zero and names the link."""
    a = tmp_path / "a.md"
    a.write_text("See [B](./b.md).")
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "b.md" in result.stdout


def test_validator_ignores_external_links(tmp_path):
    """http(s) and mailto links are not checked."""
    a = tmp_path / "a.md"
    a.write_text(
        "[ext](https://example.com)\n"
        "[mail](mailto:me@example.com)\n"
    )
    result = _run(tmp_path)
    assert result.returncode == 0


def test_validator_checks_anchor_fragments(tmp_path):
    """Anchor fragments are validated against the target's heading slugs
    (GitHub slugger rules) — both `./file.md#anchor` and same-page
    `#anchor` forms."""
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("# Here\n[same](#here) [other](./b.md#2-some-section)")
    b.write_text("## 2. Some Section\nbody")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_validator_flags_dead_anchor(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("[other](./b.md#no-such-heading)")
    b.write_text("## Real Heading\nbody")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "dead anchor" in result.stdout


def test_validator_keeps_underscores_in_slugs(tmp_path):
    """GitHub keeps underscores in heading slugs (`depends_on` →
    depends_on); the slugifier must not strip them as md formatting."""
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("[link](./b.md#9-decision--depends_onrequired--optional)")
    b.write_text("## 9. Decision — (`depends_on.required` / `optional`)\nbody")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_validator_resolves_relative_paths_with_parent_segments(tmp_path):
    """`../foo.md` is resolved relative to the source file."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "child.md").write_text("[parent](../sibling.md)")
    (tmp_path / "sibling.md").write_text("body")
    result = _run(tmp_path)
    assert result.returncode == 0


def test_validator_resolves_github_wiki_extensionless_links():
    """GitHub Wiki links omit `.md`; repo validation should still find pages."""
    ensure_generated_docs()
    result = _run(PROJECTION_ROOT / "wiki")
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_scans_repo_default_paths():
    """When invoked with no args, validator scans README.md + docs/ + CHANGELOG.md."""
    result = _run()
    # Whatever the current state is, the script must run end-to-end (exit
    # code 0 or 1 — but never crash). This guarantees Phase A's pre-migration
    # baseline can be captured.
    assert result.returncode in (0, 1), result.stdout + result.stderr


def test_validator_ignores_links_inside_fenced_code_block(tmp_path):
    """Links inside ``` ... ``` code fences are NOT validated."""
    a = tmp_path / "a.md"
    a.write_text(
        "Prose link must work: see [Other](./other.md).\n\n"
        "```\n"
        "[Example](./does-not-exist.md)\n"
        "```\n"
    )
    (tmp_path / "other.md").write_text("body")
    result = _run(tmp_path)
    assert result.returncode == 0, (
        "Fenced-code-block links should be skipped; got:\n" + result.stdout
    )


def test_validator_catches_broken_link_with_inline_code_label(tmp_path):
    """A real broken link whose label is wrapped in backticks must still be
    reported — the validator should NOT strip the inline code out of the label
    and silently drop the link from validation."""
    a = tmp_path / "a.md"
    a.write_text(
        "Predecessor: [`name.md`](./does-not-exist.md) — broken.\n"
    )
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "does-not-exist.md" in result.stdout


def test_validator_checks_raw_html_anchor_and_image_targets(tmp_path):
    """Raw ``<a href>`` and ``<img src>`` targets are validated like Markdown
    links: a file target must exist relative to the page, a directory target
    must hold a README.md GitHub renders in place, and external, anchor, and
    mailto targets are skipped."""
    a = tmp_path / "a.md"
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "README.md").write_text("# Guide\n")
    (tmp_path / "b.md").write_text("## 2. Real\nbody")
    (tmp_path / "pic.png").write_bytes(b"png")
    a.write_text(
        "# Top\n"
        '<a href="b.md">B</a>\n'
        "<a href='guide/'>Guide</a>\n"
        '<a href="b.md#2-real">Anchor</a>\n'
        '<img src="pic.png" alt="pic">\n'
        '<a href="https://example.com/">ext</a>\n'
        '<a href="mailto:me@example.com">mail</a>\n'
        '<a href="#top">same page</a>\n'
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_validator_flags_broken_raw_html_targets(tmp_path):
    """An HTML anchor pointing at a nonexistent file, a directory with no
    README.md, or a dead fragment fails validation and names the target."""
    a = tmp_path / "a.md"
    (tmp_path / "empty").mkdir()
    (tmp_path / "b.md").write_text("## Real\nbody")
    a.write_text(
        '<a href="missing.md">Missing</a>\n'
        '<a href="empty/">Directory</a>\n'
        '<a href="b.md#no-such">Dead</a>\n'
        '<img src="nope.png" alt="x">\n'
    )
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "missing.md" in result.stdout
    assert "empty/" in result.stdout
    assert "no-such" in result.stdout
    assert "nope.png" in result.stdout


def test_validator_ignores_raw_html_inside_code(tmp_path):
    """HTML quoted in a fence or an inline code span is never rendered, so
    its targets are not validated."""
    a = tmp_path / "a.md"
    a.write_text(
        '```html\n<a href="missing.md">x</a>\n```\n'
        'Keep `<img src="./assets/gone.png">` as-is.\n'
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout


def test_canonical_landing_html_actions_resolve_on_github():
    """The landing page's raw HTML actions must target files GitHub can open,
    not site-only directory URLs (#1048)."""
    result = _run(REPO_ROOT / "docs" / "index.md")
    assert result.returncode == 0, result.stdout + result.stderr
    text = (REPO_ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    for target in ("quick-start/index.md", "services.md", "architecture/index.md"):
        assert f'href="{target}"' in text, target
