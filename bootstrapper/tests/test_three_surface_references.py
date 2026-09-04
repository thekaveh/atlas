from pathlib import Path

from bootstrapper.docs.sitegen.pages import ARCHITECTURE_PERSPECTIVES
from scripts.docs.canonical_references import (
    render_canonical_references,
    sync_canonical_references,
)
from scripts.docs.manifest import load_manifest


ROOT = Path(__file__).resolve().parents[2]


def test_canonical_reference_projection_covers_dynamic_public_pages() -> None:
    rendered = render_canonical_references(ROOT)
    architecture = {
        f"docs/architecture/{slug}.{suffix}"
        for slug in ARCHITECTURE_PERSPECTIVES
        for suffix in ("md", "html")
    } | {"docs/architecture/README.md", "docs/architecture/index.md"}

    assert {path.relative_to(ROOT).as_posix() for path in rendered} == {
        "docs/CONTRIBUTING-services.md",
        "docs/README.md",
        "docs/tracks.md",
        "docs/services.md",
        "docs/reference/index.md",
        "docs/reference/source-values.md",
        "docs/reference/env-vars.md",
        "docs/reference/ports-routes.md",
        "docs/reference/service-dependencies.md",
        "docs/reference/manifest-fields.md",
    } | architecture
    assert "../services/comfyui/README.md" in rendered[ROOT / "docs/services.md"]
    assert "../../deployment/" not in rendered[ROOT / "docs/reference/ports-routes.md"]


def test_committed_canonical_references_match_the_live_service_model() -> None:
    assert sync_canonical_references(ROOT, check=True) == []


def test_the_track_matrix_has_exactly_one_generated_home() -> None:
    """#838: the matrix used to be rendered byte-identically at nav §4
    (``docs/tracks.md``) and §10.5 (``docs/reference/tracks.md``).

    Both were generated from the same ``model.tracks``, so they could never
    drift — which is precisely why the duplication survived unnoticed. The
    reference copy was collapsed into the nav page, which is the one users
    actually browse. This guards the collapse rather than the symptom: a
    second generated home would reintroduce it silently.
    """
    from bootstrapper.docs.sitegen.model import load_docs_model
    from bootstrapper.docs.sitegen.pages import reference_pages, static_pages

    model = load_docs_model(ROOT)
    rendered = {**static_pages(model), **reference_pages(model)}
    homes = [
        path.relative_to(ROOT).as_posix()
        for path, text in rendered.items()
        if "| Track | Description | Services |" in text
    ]
    # static_pages() emits into the docs/site/ staging tree, which the build
    # publishes as docs/tracks.md — the path differs, the count is the point.
    assert homes == ["docs/site/tracks.md"], (
        f"the track matrix should have exactly one generated home, found: {homes}"
    )


def test_documentation_map_delegates_service_inventory_to_generated_catalog() -> None:
    manifest = load_manifest(ROOT / "docs" / "manifest.yaml", ROOT)
    documentation_map = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    service_catalog = (ROOT / "docs" / "services.md").read_text(encoding="utf-8")
    service_sources = [
        page.source
        for page in manifest.pages
        if len(Path(page.source).parts) == 3
        and Path(page.source).parts[0] == "services"
        and Path(page.source).name == "README.md"
    ]

    assert service_sources
    for source in service_sources:
        link = f"../{source}"
        assert link in service_catalog
        assert link not in documentation_map
    assert "[Service catalog](services.md)" in documentation_map
