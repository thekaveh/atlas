from __future__ import annotations

from pathlib import Path

import yaml

from services.manifests import load_manifests


ROOT = Path(__file__).resolve().parents[2]
MKDOCS = ROOT / "mkdocs.yml"
DOCS_SITE = ROOT / "docs" / "site"
DIAGRAMS_DIR = ROOT / "docs" / "architecture"
WIKI_DIR = ROOT / "docs" / "wiki"
CHECK_SCRIPT = ROOT / "scripts" / "check-docs-site.py"
WIKI_SCRIPT = ROOT / "scripts" / "export-docs-wiki.py"
WORKFLOW = ROOT / ".github" / "workflows" / "services-lint.yml"

REQUIRED_DIAGRAMS = {
    "platform-overview",
    "bootstrapper-lifecycle",
    "source-configuration-model",
    "track-selection-matrix",
    "network-routing-topology",
    "data-rag-flow",
    "llm-provider-flow",
    "data-engineering-lakehouse-flow",
    "observability-flow",
    "security-auth-secrets-boundary",
    "service-admission-workflow",
}


def _mkdocs() -> dict:
    return yaml.safe_load(MKDOCS.read_text(encoding="utf-8"))


def _flatten_nav(items: list) -> dict[str, str]:
    flattened: dict[str, str] = {}
    for item in items:
        assert isinstance(item, dict)
        for label, value in item.items():
            if isinstance(value, str):
                flattened[label] = value
            elif isinstance(value, list):
                flattened.update(_flatten_nav(value))
            else:
                raise AssertionError(f"unsupported nav value for {label!r}: {value!r}")
    return flattened


def _service_names() -> set[str]:
    manifest_names = {manifest.name for manifest in load_manifests(ROOT / "services")}
    doc_only_names = {
        path.name
        for path in (ROOT / "services").iterdir()
        if path.is_dir()
        and not path.name.startswith(("_", "."))
        and (path / "README.md").exists()
        and not (path / "service.yml").exists()
    }
    return manifest_names | doc_only_names


def test_mkdocs_nav_exists_and_points_to_real_pages() -> None:
    config = _mkdocs()
    nav = _flatten_nav(config["nav"])

    assert config["site_name"] == "Atlas Documentation"
    assert config["docs_dir"] == "docs"
    assert config["site_dir"] == "site"
    assert nav["Home"] == "site/index.md"
    assert nav["Service Index"] == "site/services/index.md"
    assert nav["SOURCE Reference"] == "site/reference/source-values.md"
    assert nav["Wiki Export"] == "wiki/Home.md"

    required_sections = {
        "Overview",
        "Quick Start",
        "Architecture",
        "Services",
        "Tracks",
        "Configuration",
        "Operations",
        "Development",
        "Reference",
    }
    assert required_sections <= set(nav)

    for label, target in nav.items():
        assert (ROOT / "docs" / target).exists(), f"{label!r} points at missing {target!r}"


def test_docs_site_indexes_every_service_family() -> None:
    service_index = (DOCS_SITE / "services" / "index.md").read_text(encoding="utf-8")
    mkdocs_text = MKDOCS.read_text(encoding="utf-8")

    for name in sorted(_service_names()):
        assert f"../services/{name}.md" in service_index or f"../../services/{name}/README.md" in service_index
        assert f"services/{name}.md" in mkdocs_text

    assert "Virtual manifests" in service_index
    assert "Doc-only service folders" in service_index
    assert "cloud-providers" in service_index
    assert "stt-provider" in service_index

    for name in ("redpanda", "trino"):
        page = (DOCS_SITE / "services" / f"{name}.md").read_text(encoding="utf-8")
        assert f"[services/{name}/README.md](../../../services/{name}/README.md)" in page


def test_generated_reference_pages_cover_core_sources() -> None:
    for name in [
        "source-values.md",
        "env-vars.md",
        "ports-routes.md",
        "tracks.md",
        "service-dependencies.md",
        "manifest-fields.md",
    ]:
        path = DOCS_SITE / "reference" / name
        text = path.read_text(encoding="utf-8")
        assert "Generated" in text

    source_values = (DOCS_SITE / "reference" / "source-values.md").read_text(encoding="utf-8")
    assert "LLM_PROVIDER_SOURCE" in source_values
    assert "container-gpu" in source_values
    assert "disabled" in source_values

    tracks = (DOCS_SITE / "reference" / "tracks.md").read_text(encoding="utf-8")
    assert "gen-ai-eng" in tracks
    assert "data-eng" in tracks
    assert "all" in tracks


def test_required_diagram_catalog_is_linked_and_non_empty() -> None:
    catalog = (DIAGRAMS_DIR / "README.md").read_text(encoding="utf-8")
    nav_text = MKDOCS.read_text(encoding="utf-8")

    for slug in REQUIRED_DIAGRAMS:
        html = DIAGRAMS_DIR / f"{slug}.html"
        page = DIAGRAMS_DIR / f"{slug}.md"
        html_text = html.read_text(encoding="utf-8")
        page_text = page.read_text(encoding="utf-8")

        assert html.stat().st_size > 1000
        assert "<svg" in html_text
        assert "#020617" in html_text
        assert "JetBrains Mono" in html_text
        assert page.stat().st_size > 200
        assert f"./{slug}.html" in page_text or f"{slug}.html" in page_text
        assert f"{slug}.md" in catalog
        assert f"architecture/{slug}.md" in nav_text


def test_wiki_export_and_ci_hooks_are_present() -> None:
    wiki_home = (WIKI_DIR / "Home.md").read_text(encoding="utf-8")
    wiki_index = (WIKI_DIR / "_Sidebar.md").read_text(encoding="utf-8")
    check_script = CHECK_SCRIPT.read_text(encoding="utf-8")
    wiki_script = WIKI_SCRIPT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Generated from the MkDocs source pages" in wiki_home
    assert "Atlas Documentation" in wiki_index
    assert "mkdocs build --strict" in check_script
    assert "mkdocs build --strict" in workflow
    assert "check-docs-site.py" in workflow
    assert "export-docs-wiki.py --check" in workflow
    assert "wiki/Home.md" in wiki_script
