"""Helpers for feature tests that assert generated documentation content."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.docs.build_docs import render_site, render_wiki
from scripts.docs.manifest import Manifest, Page, load_manifest
from scripts.docs.render_diagrams import render_all


ROOT = Path(__file__).resolve().parents[2]
_PROJECTION_TEMP = TemporaryDirectory(prefix="atlas-test-docs-")
PROJECTION_ROOT = Path(_PROJECTION_TEMP.name)


@lru_cache(maxsize=1)
def _manifest() -> Manifest:
    manifest_path = ROOT / "docs" / "manifest.yaml"
    manifest = load_manifest(manifest_path, ROOT)
    render_site(manifest, ROOT, PROJECTION_ROOT / "site")
    render_wiki(manifest, ROOT, PROJECTION_ROOT / "wiki")
    render_all(
        manifest,
        ROOT,
        PROJECTION_ROOT / "site" / "assets" / "img",
        ROOT / "docs" / "diagrams" / "img",
        PROJECTION_ROOT / "wiki" / "img",
        check_png=True,
    )
    return manifest


def ensure_generated_docs() -> Manifest:
    """Build both generated surfaces once and return their manifest."""
    return _manifest()


def _page_for_source(source: str) -> Page:
    matches = [page for page in _manifest().pages if page.source == source]
    if len(matches) != 1:
        raise AssertionError(f"Expected one manifest page for {source!r}, found {len(matches)}")
    return matches[0]


def surface_text(source: str, surface: str) -> str:
    """Return a canonical page as rendered for ``site`` or ``wiki``."""
    page = _page_for_source(source)
    if surface == "site":
        path = PROJECTION_ROOT / "site" / page.site_path
    elif surface == "wiki":
        path = PROJECTION_ROOT / "wiki" / page.wiki_path
    else:
        raise ValueError(f"Unknown documentation surface: {surface}")
    return path.read_text(encoding="utf-8")


def markdown_block_shape(text: str) -> list[tuple[str, int]]:
    """Record list, quote and code-block containers without their prose."""
    from html.parser import HTMLParser

    class Blocks(HTMLParser):
        def __init__(self):
            super().__init__()
            self.shape = []

        def handle_starttag(self, tag, attrs):
            if tag in {"ul", "ol", "li", "pre", "blockquote"}:
                self.shape.append((tag, 1))

        def handle_endtag(self, tag):
            if tag in {"ul", "ol", "li", "pre", "blockquote"}:
                self.shape.append((tag, -1))

    parser = Blocks()
    parser.feed(text)
    parser.close()
    return parser.shape


def documented_baseline_commands(markdown: str) -> list[str]:
    """Find baseline command examples without fixing headings or fence grouping."""
    import re
    from markdown_it import MarkdownIt

    snippets = [
        token.content for token in MarkdownIt("commonmark").parse(markdown)
        if token.type == "fence" and token.info.strip() in {"bash", "sh"}
        and (
            re.search(r"\bpytest\s+tests/test_fragment_equivalence\.py(?:\s|$)", token.content)
            or "BASELINE.write_text" in token.content
        )
    ]
    assert snippets, "No documented baseline commands found"
    return snippets


def probe_documented_shell_commands(snippets: list[str], tmp_path: Path):
    """Record command directories and arguments without executing their payloads."""
    import json
    import shlex
    import subprocess
    import sys

    checkout = tmp_path / "checkout with spaces"
    (checkout / "bootstrapper").mkdir(parents=True)
    tools = tmp_path / "owned-tools"
    tools.mkdir()
    log = tmp_path / "uv-calls.jsonl"
    recorder = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['ATLAS_DOC_COMMAND_LOG']).open('a') as stream:\n"
        "    stream.write(json.dumps({'cwd': os.getcwd(), 'args': sys.argv[1:]}) + '\\n')\n"
    )
    uv = tools / "uv"
    uv.write_text(
        "#!/bin/sh\nexec "
        + shlex.join([sys.executable, "-I", "-S", "-c", recorder])
        + ' "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-c",
         "\n".join(snippets) + '\nprintf "%s" "$PWD"\n'],
        cwd=checkout,
        env={"PATH": str(tools), "ATLAS_DOC_COMMAND_LOG": str(log)},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls, checkout
