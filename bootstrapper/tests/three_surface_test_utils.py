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
