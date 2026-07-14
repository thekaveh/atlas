"""Helpers for feature tests that assert generated documentation content."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from scripts.docs.build_docs import build
from scripts.docs.manifest import Manifest, Page, load_manifest


ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _manifest() -> Manifest:
    manifest_path = ROOT / "docs" / "manifest.yaml"
    build(manifest_path, ROOT, site=True, wiki=True, check=False)
    return load_manifest(manifest_path, ROOT)


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
        path = ROOT / "generated" / "site" / page.site_path
    elif surface == "wiki":
        path = ROOT / "generated" / "wiki" / page.wiki_path
    else:
        raise ValueError(f"Unknown documentation surface: {surface}")
    return path.read_text(encoding="utf-8")
