from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from PIL import Image
import pytest
import yaml

from scripts.docs.build_docs import build
from scripts.docs.links import find_links, is_forbidden
from scripts.docs.manifest import load_manifest
from services.manifests import load_manifests


ROOT = Path(__file__).resolve().parents[2]
MKDOCS = ROOT / "mkdocs.yml"
GENERATED = ROOT / "generated"
DOCS_SITE = GENERATED / "site"
WIKI_DIR = GENERATED / "wiki"
DIAGRAMS_DIR = ROOT / "docs" / "architecture"
DRIFT_SCRIPT = ROOT / "scripts" / "check-docs-drift.py"
WORKFLOW = ROOT / ".github" / "workflows" / "services-lint.yml"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "docs-pages.yml"
THEME_CSS = ROOT / "docs" / "assets" / "stylesheets" / "atlas.css"
THEME_HERO_IMAGE = ROOT / "docs" / "assets" / "images" / "atlas-source.png"
THEME_POSTER_BLUE = ROOT / "docs" / "assets" / "atlas-poster-blue.png"
THEME_POSTER_GOLD = ROOT / "docs" / "assets" / "atlas-poster-gold.png"
POSTER_VARIANT_SCRIPT = ROOT / "scripts" / "generate-atlas-poster-variants.py"


@pytest.fixture(scope="module", autouse=True)
def _generated_docs() -> None:
    build(
        ROOT / "docs" / "manifest.yaml",
        ROOT,
        site=True,
        wiki=True,
        check=True,
    )


def _manifest():
    return load_manifest(ROOT / "docs" / "manifest.yaml", ROOT)


def _mkdocs() -> dict:
    return yaml.safe_load(MKDOCS.read_text(encoding="utf-8"))


def _flatten_nav(items: list) -> dict[str, str]:
    flattened: dict[str, str] = {}
    for item in items:
        for label, value in item.items():
            if isinstance(value, str):
                flattened[label] = value
            else:
                flattened.update(_flatten_nav(value))
    return flattened


def _nav_labels(items: list) -> set[str]:
    labels: set[str] = set()
    for item in items:
        for label, value in item.items():
            labels.add(label)
            if isinstance(value, list):
                labels.update(_nav_labels(value))
    return labels


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


def test_manifest_drives_numbered_mkdocs_nav_and_real_pages() -> None:
    manifest = _manifest()
    config = _mkdocs()
    nav = _flatten_nav(config["nav"])
    labels = _nav_labels(config["nav"])

    assert config["site_name"] == "Atlas Documentation"
    assert config["site_url"] == "https://thekaveh.github.io/atlas/"
    assert config["docs_dir"] == "generated/site"
    assert config["site_dir"] == "site"
    assert "repo_url" not in config
    assert "repo_name" not in config
    assert "edit_uri" not in config
    assert len(nav) == len(manifest.pages)
    assert nav["1. Overview"] == "index.md"
    assert nav["5.1. Service Catalog"] == "services/index.md"
    assert nav["10.1. Reference Index"] == "reference/index.md"
    assert {"5. Services", "5.2. Service Guides", "10. Reference"} <= labels
    assert all(label[0].isdigit() for label in labels)
    for target in nav.values():
        assert (DOCS_SITE / target).is_file(), target


def test_service_catalog_and_manifest_cover_every_service_family() -> None:
    manifest = _manifest()
    service_pages = {
        Path(page.source).parts[1]: page
        for page in manifest.pages
        if page.source.startswith("services/")
        and len(Path(page.source).parts) == 3
    }
    index = (DOCS_SITE / "services" / "index.md").read_text(encoding="utf-8")

    assert set(service_pages) == _service_names()
    for name, page in service_pages.items():
        assert f"[{name}]({name}.md)" in index
        assert (DOCS_SITE / page.site_path).is_file()
        assert (WIKI_DIR / page.wiki_path).is_file()


def test_service_pages_project_full_canonical_readmes_without_cross_surface_links() -> None:
    for name in ("supabase", "open-webui", "litellm", "airflow", "spark"):
        canonical = (ROOT / "services" / name / "README.md").read_text(encoding="utf-8")
        rendered = (DOCS_SITE / "services" / f"{name}.md").read_text(encoding="utf-8")
        assert "## 1. " in rendered
        assert len(rendered.splitlines()) >= int(len(canonical.splitlines()) * 0.8)
        assert not any(is_forbidden(link.target, "site") for link in find_links(rendered))

    cloud = (DOCS_SITE / "services" / "cloud-providers.md").read_text(encoding="utf-8")
    for variable in ("CLOUD_OPENAI_SOURCE", "CLOUD_ANTHROPIC_SOURCE", "CLOUD_OPENROUTER_SOURCE"):
        assert variable in cloud

    supabase = (DOCS_SITE / "services" / "supabase.md").read_text(encoding="utf-8")
    assert "SUPABASE_DB_SOURCE" in supabase
    env_reference = (DOCS_SITE / "reference" / "env-vars.md").read_text(encoding="utf-8")
    for variable in ("SUPABASE_DB_SOURCE", "SUPABASE_META_SOURCE", "SUPABASE_STORAGE_SOURCE"):
        assert variable in env_reference


def test_generated_reference_pages_cover_core_sources() -> None:
    for name in (
        "source-values.md",
        "env-vars.md",
        "ports-routes.md",
        "tracks.md",
        "service-dependencies.md",
        "manifest-fields.md",
    ):
        text = (DOCS_SITE / "reference" / name).read_text(encoding="utf-8")
        assert "Generated" in text

    source_values = (DOCS_SITE / "reference" / "source-values.md").read_text(encoding="utf-8")
    assert "LLM_PROVIDER_SOURCE" in source_values
    assert "CLOUD_OPENAI_SOURCE" in source_values
    dependencies = (DOCS_SITE / "reference" / "service-dependencies.md").read_text(encoding="utf-8")
    assert "| Service | Required | Optional | Runtime Calls |" in dependencies
    assert "open-webui" in dependencies


def test_diagram_masters_and_surface_assets_are_complete() -> None:
    manifest = _manifest()
    assert len(manifest.diagrams) >= 1
    for diagram in manifest.diagrams:
        master = ROOT / diagram.master
        assert "<svg" in master.read_text(encoding="utf-8")
        png = ROOT / "docs" / "diagrams" / "img" / f"{diagram.id}.png"
        assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert (DOCS_SITE / "assets" / "img" / f"{diagram.id}.svg").is_file()
        assert (WIKI_DIR / "img" / f"{diagram.id}.png").is_file()

    for name in ("supabase", "open-webui", "litellm"):
        site = (DOCS_SITE / "services" / f"{name}.md").read_text(encoding="utf-8")
        wiki_page = next(page for page in manifest.pages if page.source == f"services/{name}/README.md")
        wiki = (WIKI_DIR / wiki_page.wiki_path).read_text(encoding="utf-8")
        assert f"assets/img/service-{name}.svg" in site
        assert f"img/service-{name}.png" in wiki


def test_architecture_pages_explain_the_views_without_publication_instructions() -> None:
    for page in DIAGRAMS_DIR.glob("*.md"):
        if page.name in {"README.md", "index.md"}:
            continue
        text = page.read_text(encoding="utf-8")
        assert "## 2. Notes" in text, page
        assert "## 3. Source Files" in text, page
        assert "## 4. Maintenance" not in text, page
        assert "architecture-diagram design system" not in text, page
        assert "dark slate background" not in text, page
        interactive = page.with_suffix(".html").read_text(encoding="utf-8")
        assert "How to read this view" in interactive, page
        assert "architecture-diagram design system" not in interactive, page
        assert "Update trigger" not in interactive, page

    source_model = (DIAGRAMS_DIR / "source-configuration-model.html").read_text(
        encoding="utf-8"
    )
    assert 'data-source="SOURCE Var" data-target="container"' in source_model
    assert 'data-source="SOURCE Var" data-target="localhost"' in source_model
    assert 'data-source="container" data-target="localhost"' not in source_model

    network = (DIAGRAMS_DIR / "network-routing-topology.html").read_text(
        encoding="utf-8"
    )
    assert 'data-source="Browser" data-target="*.localhost"' in network
    assert 'data-source="Browser" data-target="Direct Ports"' in network
    assert 'data-source="Kong" data-target="Direct Ports"' not in network


def test_wiki_contains_the_complete_manifest_page_set_and_navigation() -> None:
    manifest = _manifest()
    expected = {page.wiki_path.as_posix() for page in manifest.pages} | {"_Sidebar.md", "_Footer.md"}
    actual = {path.name for path in WIKI_DIR.glob("*.md")}
    assert actual == expected
    sidebar = (WIKI_DIR / "_Sidebar.md").read_text(encoding="utf-8")
    assert "**5. Services**" in sidebar
    assert "[5.2.11. comfyui](5.2.11-comfyui)" in sidebar
    for page in manifest.pages:
        text = (WIKI_DIR / page.wiki_path).read_text(encoding="utf-8")
        canonical = (ROOT / page.source).read_text(encoding="utf-8")
        assert text.splitlines()[0] == canonical.splitlines()[0]
        assert text.startswith(f"# {page.number}. ")
        assert not any(is_forbidden(link.target, "wiki") for link in find_links(text))


def test_home_and_theme_preserve_the_atlas_dark_visual_contract() -> None:
    config = _mkdocs()
    home = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    css = THEME_CSS.read_text(encoding="utf-8")

    assert config["theme"]["name"] == "material"
    assert config["theme"]["palette"][0]["scheme"] == "slate"
    assert config["theme"]["palette"][0]["toggle"]["name"] == "Switch to light mode"
    assert config["theme"]["palette"][1]["scheme"] == "default"
    assert "atlas-home" in home
    assert "assets/atlas-poster-blue.png" in home
    assert "assets/atlas-poster-gold.png" not in home
    assert "screenshots/wizard-running.png" in home
    assert ".md-content--atlas-wide" in css
    assert "grid-template-columns: minmax(20rem, 0.75fr) minmax(26rem, 1.25fr)" in css
    assert "fonts.googleapis.com" not in css
    assert THEME_HERO_IMAGE.exists()
    assert THEME_POSTER_BLUE.exists()
    assert THEME_POSTER_GOLD.exists()


def test_poster_variant_generator_preserves_original_block_wordmark() -> None:
    script = POSTER_VARIANT_SCRIPT.read_text(encoding="utf-8")
    assert 'WORDMARK_SOURCE = ROOT / "assets" / "atlas-poster.png"' in script
    assert "WORDMARK_SCALE = 0.56" in script
    assert "WORDMARK_BOTTOM_MARGIN = 4" in script
    assert "_extract_wordmark" in script
    assert "ImageFont" not in script
    assert "draw.text" not in script


def test_blue_poster_border_is_flush_to_image_edge() -> None:
    source = Image.open(ROOT / "assets" / "atlas-source.png").convert("RGBA")
    poster = Image.open(ROOT / "assets" / "atlas-poster-blue.png").convert("RGBA")
    docs_poster = Image.open(THEME_POSTER_BLUE).convert("RGBA")
    assert poster.size == source.size
    assert docs_poster.tobytes() == poster.tobytes()
    width, height = poster.size
    for point in ((width // 2, 0), (width // 2, height - 1), (0, height // 2), (width - 1, height // 2)):
        assert poster.getpixel(point) != source.getpixel(point)


def test_documentation_guidance_uses_the_single_root_safe_gate() -> None:
    for relative in ("AGENTS.md", "docs/README.md", "docs/development.md", "docs/CONTRIBUTING-services.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "make docs-check" in text
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "MkDocs" not in readme
    assert "GitHub Wiki" not in readme
    assert "thekaveh.github.io" not in readme


def test_docs_pages_workflow_is_main_only_pinned_and_uses_a_wiki_deploy_key() -> None:
    workflow = yaml.safe_load(PAGES_WORKFLOW.read_text(encoding="utf-8"))
    text = PAGES_WORKFLOW.read_text(encoding="utf-8")
    assert workflow[True]["push"]["branches"] == ["main"]
    assert workflow["permissions"]["contents"] == "read"
    assert workflow["permissions"]["pages"] == "write"
    assert "WIKI_DEPLOY_KEY" in text
    assert "GITHUB_TOKEN" not in text
    assert "make docs-check" in text
    assert "python -m scripts.docs.build_docs --wiki" in text
    assert "python -m scripts.docs.push_wiki --push" in text
    for sha in (
        "45bfe0192ca1faeb007ade9deae92b16b8254a0d",
        "fc324d3547104276b827a68afc52ff2a11cc49c9",
        "cd2ce8fcbc39b97be8ca5fce6e763baed58fa128",
    ):
        assert sha in text


def test_services_lint_gates_main_and_develop_and_runs_three_surface_check() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    text = WORKFLOW.read_text(encoding="utf-8")
    assert workflow[True]["push"]["branches"] == ["main", "develop"]
    assert workflow[True]["pull_request"]["branches"] == ["main", "develop"]
    assert "make docs-check" in text
    assert "Install Cairo" in text
    assert workflow["jobs"]["notebook-reproducibility"]["name"] == "Notebook source hygiene"
    assert "python -m scripts.notebook_reproducibility" in text


def test_source_configuration_shell_examples_do_not_comment_after_continuations() -> None:
    source = (ROOT / "docs" / "deployment" / "source-configuration.md").read_text(
        encoding="utf-8"
    )
    assert "\\  #" not in source


def test_services_lint_build_validation_covers_all_local_build_contexts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    expected = {
        path.parent.relative_to(ROOT).as_posix()
        for path in (ROOT / "services").glob("*/init/Dockerfile")
    }
    excluded = {"services/docling/provider", "services/parakeet/provider"}
    for compose in (ROOT / "services").glob("*/compose.yml"):
        data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
        for spec in (data.get("services") or {}).values():
            build_spec = spec.get("build") if isinstance(spec, dict) else None
            if not isinstance(build_spec, dict) or not build_spec.get("context"):
                continue
            context = str(build_spec["context"])
            if context.startswith("http"):
                continue
            relative = (compose.parent / context).resolve().relative_to(ROOT).as_posix()
            if relative not in excluded:
                expected.add(relative)
    assert expected
    for context in expected:
        assert context in workflow


def test_docs_do_not_reference_retired_required_check_counts() -> None:
    stale = (
        "All 3 `services-lint` CI checks",
        "the three required CI checks",
        "the three `services-lint` checks",
        "the 3 `services-lint` checks",
    )
    for path in [*list((ROOT / "docs").rglob("*.md")), ROOT / "AGENTS.md"]:
        text = path.read_text(encoding="utf-8")
        assert not any(phrase in text for phrase in stale), path


def test_generated_pages_have_manifest_numbered_h1_and_local_numbered_sections() -> None:
    for page in _manifest().pages:
        text = (DOCS_SITE / page.site_path).read_text(encoding="utf-8")
        canonical = (ROOT / page.source).read_text(encoding="utf-8")
        assert text.splitlines()[0] == canonical.splitlines()[0]
        assert text.startswith(f"# {page.number}. ")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        assert headings, page.source
        if page.source != "docs/CHANGELOG.md":
            assert all(re.match(r"^## \d+\. ", heading) for heading in headings), page.source


def test_structural_docs_audit_accepts_generated_surfaces() -> None:
    result = subprocess.run(
        [sys.executable, str(DRIFT_SCRIPT)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
