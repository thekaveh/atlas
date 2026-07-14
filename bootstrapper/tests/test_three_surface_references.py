from pathlib import Path

from bootstrapper.docs.sitegen.pages import ARCHITECTURE_PERSPECTIVES
from scripts.docs.canonical_references import (
    render_canonical_references,
    sync_canonical_references,
)


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_reference_projection_covers_dynamic_public_pages() -> None:
    rendered = render_canonical_references(ROOT)
    architecture = {
        f"docs/architecture/{slug}.{suffix}"
        for slug in ARCHITECTURE_PERSPECTIVES
        for suffix in ("md", "html")
    } | {"docs/architecture/README.md", "docs/architecture/index.md"}

    assert {path.relative_to(ROOT).as_posix() for path in rendered} == {
        "docs/tracks.md",
        "docs/services.md",
        "docs/reference/index.md",
        "docs/reference/source-values.md",
        "docs/reference/env-vars.md",
        "docs/reference/ports-routes.md",
        "docs/reference/tracks.md",
        "docs/reference/service-dependencies.md",
        "docs/reference/manifest-fields.md",
    } | architecture
    assert "../services/comfyui/README.md" in rendered[ROOT / "docs/services.md"]
    assert "../../deployment/" not in rendered[ROOT / "docs/reference/ports-routes.md"]


def test_committed_canonical_references_match_the_live_service_model() -> None:
    assert sync_canonical_references(ROOT, check=True) == []
