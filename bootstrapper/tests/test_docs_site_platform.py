from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import yaml

from services.manifests import load_manifests


ROOT = Path(__file__).resolve().parents[2]
MKDOCS = ROOT / "mkdocs.yml"
DOCS_SITE = ROOT / "docs" / "site"
DIAGRAMS_DIR = ROOT / "docs" / "architecture"
WIKI_DIR = ROOT / "docs" / "wiki"
CHECK_SCRIPT = ROOT / "scripts" / "check-docs-site.py"
DRIFT_SCRIPT = ROOT / "scripts" / "check-docs-drift.py"
WIKI_SCRIPT = ROOT / "scripts" / "export-docs-wiki.py"
WORKFLOW = ROOT / ".github" / "workflows" / "services-lint.yml"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "docs-pages.yml"
THEME_CSS = ROOT / "docs" / "assets" / "stylesheets" / "atlas.css"
THEME_HERO_IMAGE = ROOT / "docs" / "assets" / "images" / "atlas-source.png"

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
    assert config["site_url"] == "https://thekaveh.github.io/atlas/"
    assert config["docs_dir"] == "docs"
    assert config["site_dir"] == "site"
    assert nav["1. Home"] == "index.md"
    assert nav["7. Service Index"] == "site/services/index.md"
    assert nav["14. SOURCE Reference"] == "site/reference/source-values.md"
    assert "20. Wiki Export" not in nav
    assert "assets/stylesheets/atlas.css" in config["extra_css"]
    assert config["validation"]["links"]["not_found"] != "ignore"

    required_sections = {
        "2. Overview",
        "3. Quick Start",
        "4. Architecture",
        "6. Services",
        "9. Tracks",
        "10. Configuration",
        "11. Operations",
        "12. Development",
        "13. Reference",
    }
    assert required_sections <= set(nav)

    for label in nav:
        assert label[0].isdigit(), f"nav label is not numbered: {label!r}"

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
        assert (
            f"[services/{name}/README.md]"
            f"(https://github.com/thekaveh/atlas/blob/main/services/{name}/README.md)"
            in page
        )


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
        assert "fonts.googleapis.com" not in html_text
        assert page.stat().st_size > 200
        assert f"./{slug}.html" in page_text or f"{slug}.html" in page_text
        assert f"{slug}.md" in catalog
        assert f"architecture/{slug}.md" in nav_text


def test_wiki_export_and_ci_hooks_are_present() -> None:
    wiki_home = (WIKI_DIR / "Home.md").read_text(encoding="utf-8")
    wiki_index = (WIKI_DIR / "_Sidebar.md").read_text(encoding="utf-8")
    wiki_services = (WIKI_DIR / "Services.md").read_text(encoding="utf-8")
    check_script = CHECK_SCRIPT.read_text(encoding="utf-8")
    wiki_script = WIKI_SCRIPT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Generated from the MkDocs source pages" in wiki_home
    assert "Atlas Documentation" in wiki_index
    assert "../site/" not in wiki_home
    assert "../site/" not in wiki_index
    assert "../site/" not in wiki_services
    assert "[1. Overview](Overview)" in wiki_home
    assert "[4. Services](Services)" in wiki_index
    assert "mkdocs build --strict" in check_script
    assert "validate_built_site_links" in check_script
    assert "mkdocs build --strict" in workflow
    assert "check-docs-site.py" in workflow
    assert "export-docs-wiki.py --check" in workflow
    assert "wiki/Home.md" in wiki_script


def test_docs_pages_publication_workflow_and_homepage_contract() -> None:
    workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")

    assert "deploy-pages" in workflow
    assert "upload-pages-artifact" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "scripts/check-docs-site.py" in workflow
    assert "scripts/export-docs-wiki.py --push" in workflow
    assert "https://thekaveh.github.io/atlas/" in workflow


def test_services_lint_build_validation_covers_all_init_dockerfiles() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    init_contexts = sorted(
        path.parent.relative_to(ROOT).as_posix()
        for path in (ROOT / "services").glob("*/init/Dockerfile")
    )

    assert init_contexts
    assert "Build-validation (Dockerfile + requirements.txt installability)" in workflow
    for context in init_contexts:
        assert context in workflow, f"build-validation does not cover {context}"


def test_atlas_theme_uses_dark_atlas_system_with_local_assets() -> None:
    config = _mkdocs()
    css = THEME_CSS.read_text(encoding="utf-8")
    home = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")

    for color in ("#020617", "#07111f", "#0ea5e9", "#38bdf8", "#60a5fa"):
        assert color in css
    assert config["theme"]["color_mode"] == "dark"
    assert config["theme"]["highlightjs"] is False
    assert "@import url(" not in css
    assert "fonts.googleapis.com" not in css
    assert "JetBrains Mono" in css
    assert "border-radius" in css
    assert "body > .container" in css
    assert "max-width: 1480px" in css
    assert "background: #020617" in css
    assert "assets/images/atlas-source.png" in home
    assert THEME_HERO_IMAGE.exists()
    assert "background-size: auto, 44px 44px" not in css
    assert "box-shadow: 0 24px 90px" not in css
    assert "#f8fafc" not in css
    assert "@media (max-width: 767.98px)" in css
    assert ".bs-sidebar" in css
    assert "display: none" in css
    assert "emoji" not in css.lower()


def test_generated_site_pages_use_numbered_hierarchy() -> None:
    for relative in [
        "index.md",
        "site/overview.md",
        "site/quick-start.md",
        "site/architecture/index.md",
        "site/configuration.md",
        "site/operations.md",
        "site/development.md",
        "site/reference/index.md",
        "site/services/index.md",
    ]:
        text = (DOCS_SITE.parent / relative).read_text(encoding="utf-8")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        assert headings, f"{relative} has no section headings"
        assert any(line.startswith("## 1. ") for line in headings), relative


def test_structural_docs_audit_accepts_generated_wiki_links() -> None:
    result = subprocess.run(
        [sys.executable, str(DRIFT_SCRIPT)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
