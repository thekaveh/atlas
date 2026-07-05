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


def _nav_labels(items: list) -> set[str]:
    labels: set[str] = set()
    for item in items:
        assert isinstance(item, dict)
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


def test_mkdocs_nav_exists_and_points_to_real_pages() -> None:
    config = _mkdocs()
    nav = _flatten_nav(config["nav"])
    nav_labels = _nav_labels(config["nav"])

    assert config["site_name"] == "Atlas Documentation"
    assert config["site_url"] == "https://thekaveh.github.io/atlas/"
    assert config["docs_dir"] == "docs"
    assert config["site_dir"] == "site"
    assert nav["1. Overview"] == "index.md"
    assert nav["5.1. Index"] == "site/services/index.md"
    assert nav["10.1. Index"] == "site/reference/index.md"
    assert nav["10.2. SOURCE Values"] == "site/reference/source-values.md"
    assert "11. Wiki Export" not in nav
    assert "assets/stylesheets/atlas.css" in config["extra_css"]
    assert config["validation"]["links"]["not_found"] != "ignore"
    assert any(
        label.startswith("5.2.") and target.startswith("site/services/") and target.endswith(".md")
        for label, target in nav.items()
    )

    required_sections = {
        "1. Overview",
        "2. Quick Start",
        "3. Core Concepts",
        "4. Tracks",
        "5. Service Catalog",
        "5.2. Services",
        "6. Architecture",
        "7. Configuration",
        "8. Operations",
        "9. Development",
        "10. Reference",
    }
    assert required_sections <= nav_labels

    for label in nav_labels:
        assert label[0].isdigit(), f"nav label is not numbered: {label!r}"

    for label, target in nav.items():
        assert (ROOT / "docs" / target).exists(), f"{label!r} points at missing {target!r}"


def test_docs_site_indexes_every_service_family() -> None:
    service_index = (DOCS_SITE / "services" / "index.md").read_text(encoding="utf-8")
    mkdocs_text = MKDOCS.read_text(encoding="utf-8")

    for name in sorted(_service_names()):
        assert f"({name}.md)" in service_index or f"../../services/{name}/README.md" in service_index
        assert f"services/{name}.md" in mkdocs_text

    assert "cloud-providers" in service_index
    assert "stt-provider" in service_index

    for name in ("redpanda", "trino"):
        page = (DOCS_SITE / "services" / f"{name}.md").read_text(encoding="utf-8")
        assert (
            f"[services/{name}/README.md]"
            f"(https://github.com/thekaveh/atlas/blob/main/services/{name}/README.md)"
            in page
        )


def test_service_profiles_are_substantial_and_generated_from_model() -> None:
    for name in ["supabase", "open-webui", "litellm", "airflow", "spark"]:
        page = DOCS_SITE / "services" / f"{name}.md"
        text = page.read_text(encoding="utf-8")
        for heading in [
            "## 1. Overview",
            "## 2. Role In Atlas",
            "## 3. Tracks And Category",
            "## 4. Access",
            "## 5. Configuration",
            "## 6. Dependencies And Topology",
            "## 7. Source Values",
            "## 8. Runtime Integration",
            "## 9. Architecture",
            "## 10. Operations",
            "## 11. Source Documentation",
        ]:
            assert heading in text
        assert "Generated service-site entry" not in text
        assert "Source README remains the source of truth" not in text
        assert f"services/{name}/README.md" in text


def test_service_profiles_render_all_source_surfaces_and_canonical_readmes() -> None:
    cloud = (DOCS_SITE / "services" / "cloud-providers.md").read_text(encoding="utf-8")
    for surface in [
        "CLOUD_OPENAI_SOURCE",
        "CLOUD_ANTHROPIC_SOURCE",
        "CLOUD_OPENROUTER_SOURCE",
    ]:
        assert surface in cloud
    assert "[services/litellm/README.md]" in cloud
    assert "(https://github.com/thekaveh/atlas/blob/main/services/litellm/README.md)" in cloud
    assert "services/cloud-providers/README.md" not in cloud

    supabase = (DOCS_SITE / "services" / "supabase.md").read_text(encoding="utf-8")
    for surface in [
        "SUPABASE_DB_SOURCE",
        "SUPABASE_DB_INIT_SOURCE",
        "SUPABASE_META_SOURCE",
        "SUPABASE_STORAGE_SOURCE",
    ]:
        assert surface in supabase


def test_service_catalog_groups_services_by_category_with_tracks_and_sources() -> None:
    index = (DOCS_SITE / "services" / "index.md").read_text(encoding="utf-8")
    assert "## 1. Service Catalog" in index
    assert "### 1." in index
    assert "| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |" in index
    assert "supabase" in index
    assert "open-webui" in index
    assert "cloud-providers" in index
    assert "stt-provider" in index


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
    for surface in [
        "CLOUD_OPENAI_SOURCE",
        "CLOUD_ANTHROPIC_SOURCE",
        "CLOUD_OPENROUTER_SOURCE",
    ]:
        assert surface in source_values

    tracks = (DOCS_SITE / "reference" / "tracks.md").read_text(encoding="utf-8")
    assert "gen-ai-eng" in tracks
    assert "data-eng" in tracks
    assert "all" in tracks
    assert "all services (no filtering)" in tracks

    env_vars = (DOCS_SITE / "reference" / "env-vars.md").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in env_vars
    assert "cloud-providers" in env_vars
    assert "OpenAI API key used by LiteLLM when CLOUD_OPENAI_SOURCE=enabled." in env_vars
    assert "| OPENAI_API_KEY | cloud-providers |" in env_vars
    assert "LITELLM_MASTER_KEY" in env_vars
    assert "Auto-generated by bootstrapper on first run. Doubles as the admin-dashboard password." in env_vars

    deps = (DOCS_SITE / "reference" / "service-dependencies.md").read_text(encoding="utf-8")
    assert "| Service | Required | Optional | Runtime Calls |" in deps
    assert "litellm" in deps
    assert "open-webui" in deps


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


def test_wiki_export_contains_full_companion_page_set() -> None:
    expected_pages = {
        "Home.md",
        "_Sidebar.md",
        "Overview.md",
        "Quick-Start.md",
        "Core-Concepts.md",
        "Tracks.md",
        "Services.md",
        "Architecture.md",
        "Configuration.md",
        "Operations.md",
        "Development.md",
        "Reference.md",
    }
    actual_pages = {path.name for path in WIKI_DIR.glob("*.md")}
    assert expected_pages <= actual_pages

    sidebar = (WIKI_DIR / "_Sidebar.md").read_text(encoding="utf-8")
    for page in [
        "Overview",
        "Quick-Start",
        "Core-Concepts",
        "Tracks",
        "Services",
        "Architecture",
        "Configuration",
        "Operations",
        "Development",
        "Reference",
    ]:
        assert f"]({page})" in sidebar

    services = (WIKI_DIR / "Services.md").read_text(encoding="utf-8")
    assert "## 1. Service Catalog" in services
    assert "| Service | Category | Tracks | SOURCE | Values | Dependencies |" in services

    tracks = (WIKI_DIR / "Tracks.md").read_text(encoding="utf-8")
    assert "all services (no filtering)" in tracks


def test_docs_audit_guidance_lists_required_local_gates() -> None:
    docs_readme = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    contributor_guide = (ROOT / "docs" / "CONTRIBUTING-services.md").read_text(encoding="utf-8")
    development_page = (DOCS_SITE / "development.md").read_text(encoding="utf-8")
    agents_guide = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    required_commands = [
        "uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check",
        "uv run --project bootstrapper python scripts/check_doc_links.py",
        "uv run --project bootstrapper python scripts/check-docs-drift.py",
        "uv run --project bootstrapper python scripts/check-docs-site.py",
        "uv run --project bootstrapper python scripts/export-docs-wiki.py --check",
        "uv run --project bootstrapper python scripts/check-compose-source-deps.py",
        "uv run --project bootstrapper python scripts/check-kong-routes.py",
        "uv run --project bootstrapper python scripts/validate_research_schema.py --all",
        "uv run --project bootstrapper python scripts/check-track-membership.py",
        "uv lock --locked",
    ]

    for command in required_commands:
        assert command in docs_readme
        assert command in contributor_guide
        assert command in development_page

    for command in required_commands:
        assert command in agents_guide


def test_docs_pages_publication_workflow_and_homepage_contract() -> None:
    workflow = PAGES_WORKFLOW.read_text(encoding="utf-8")

    assert '"assets/**"' in workflow
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
    assert '"assets/**"' in workflow
    assert "Build-validation (Dockerfile + requirements.txt installability)" in workflow
    for context in init_contexts:
        assert context in workflow, f"build-validation does not cover {context}"


def test_services_lint_build_validation_covers_local_compose_build_contexts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    excluded_contexts = {
        "services/docling/provider",
        "services/parakeet/provider",
    }
    contexts: set[str] = set()

    for compose in (ROOT / "services").glob("*/compose.yml"):
        data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
        for spec in (data.get("services") or {}).values():
            if not isinstance(spec, dict):
                continue
            build = spec.get("build")
            if not isinstance(build, dict):
                continue
            context = build.get("context")
            if not context or str(context).startswith("http"):
                continue
            relative_context = (compose.parent / context).resolve().relative_to(ROOT).as_posix()
            if relative_context not in excluded_contexts:
                contexts.add(relative_context)

    assert contexts
    for context in sorted(contexts):
        assert context in workflow, f"build-validation does not cover {context}"


def test_contributor_ci_checklist_matches_services_lint_jobs() -> None:
    guide = (ROOT / "docs" / "CONTRIBUTING-services.md").read_text(encoding="utf-8")

    assert "uv run --project bootstrapper pytest bootstrapper/tests -q" in guide
    assert "uv run --python 3.11 --with-requirements app/requirements.txt python -m pytest app/tests -q" in guide
    assert "uv run --project bootstrapper python -m services.env_assembler" in guide
    assert "uv run --project bootstrapper python -m tools.generate_readme_topology" in guide
    assert "uv run --project bootstrapper python -m tools.validate_fragments" in guide
    assert "uv run --project bootstrapper python scripts/check-docs-site.py" in guide
    assert "docker compose --env-file .env.example -f docker-compose.yml config -q" in guide
    assert "cd bootstrapper &&" not in guide
    assert "cd bootstrapper" not in guide
    assert "cp .env.example .env" not in guide
    assert "five-command" not in guide.lower()
    assert "five commands" not in guide.lower()
    assert "sample build" not in guide
    assert "every local non-GPU Compose build context" in guide

    for context in [
        "services/airflow/build",
        "services/backend/app",
        "services/iceberg-rest/build",
        "services/jenkins/build",
        "services/jupyterhub/build",
        "services/local-deep-researcher/build",
        "services/mcp-servers/runtime",
        "services/neo4j/build",
        "services/spark/build",
        "services/zeppelin/build",
    ]:
        assert context in guide


def test_docs_do_not_reference_retired_three_check_ci_set() -> None:
    stale_phrases = [
        "All 3 `services-lint` CI checks",
        "the three required CI checks",
        "the three `services-lint` checks",
        "the 3 `services-lint` checks",
    ]

    paths = list((ROOT / "docs").rglob("*.md")) + [ROOT / "AGENTS.md"]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            assert phrase not in text, f"{path.relative_to(ROOT)} still references retired CI guidance"

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for check in [
        "Manifest lint + unit tests",
        "Compose merge + byte-equivalence + source-permutation matrix",
        "Docs drift + audit scripts",
        "Build-validation (Dockerfile + requirements.txt installability)",
    ]:
        assert check in agents


def test_agents_testing_guidance_is_root_safe_and_complete() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "cd bootstrapper &&" not in agents
    for command in [
        "uv run --project bootstrapper pytest bootstrapper/tests -q",
        "uv run --project bootstrapper pytest bootstrapper/tests/test_docs_drift.py",
        "uv run --project bootstrapper python scripts/check-docs-site.py",
        "uv run --project bootstrapper python scripts/export-docs-wiki.py --check",
        "uv run --project bootstrapper python scripts/check-track-membership.py",
        "(cd services/docling/provider/localhost && uv lock --locked)",
    ]:
        assert command in agents


def test_live_docs_use_root_safe_regen_and_wiki_commands() -> None:
    live_paths = [
        ROOT / "AGENTS.md",
        ROOT / "docs" / "CONTRIBUTING-services.md",
        ROOT / "docs" / "diagrams" / "README.md",
        ROOT / "docs" / "wiki" / "Home.md",
        ROOT / "scripts" / "generate-docs-site.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in live_paths)

    assert "PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen" not in combined
    assert "run `python scripts/export-docs-wiki.py" not in combined
    assert "\npython scripts/export-docs-wiki.py" not in combined
    assert "uv run --project bootstrapper python -m bootstrapper.docs.regen" in combined
    assert "uv run --project bootstrapper python scripts/export-docs-wiki.py --check" in combined


def test_atlas_theme_uses_material_dark_default_with_light_toggle() -> None:
    config = _mkdocs()
    css = THEME_CSS.read_text(encoding="utf-8")
    home = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")

    assert config["theme"]["name"] == "material"
    required_features = {
        "navigation.sections",
        "navigation.indexes",
        "navigation.top",
        "search.suggest",
        "search.highlight",
    }
    assert required_features <= set(config["theme"]["features"])
    palettes = config["theme"]["palette"]
    assert palettes[0]["scheme"] == "slate"
    assert palettes[0]["primary"] == "custom"
    assert palettes[0]["accent"] == "custom"
    assert palettes[0]["toggle"]["name"] == "Switch to light mode"
    assert palettes[1]["scheme"] == "default"
    assert palettes[1]["toggle"]["name"] == "Switch to dark mode"

    for color in ("#020617", "#07111f", "#0ea5e9", "#38bdf8", "#60a5fa", "#7dd3fc"):
        assert color in css
    assert ":root" in css
    assert "[data-md-color-scheme=\"slate\"]" in css
    assert "[data-md-color-scheme=\"default\"]" in css
    assert "@import url(" not in css
    assert "fonts.googleapis.com" not in css
    assert "assets/images/atlas-source.png" in home
    assert THEME_HERO_IMAGE.exists()


def test_generated_site_has_full_information_architecture() -> None:
    required_pages = [
        ROOT / "docs" / "index.md",
        DOCS_SITE / "quick-start.md",
        DOCS_SITE / "core-concepts.md",
        DOCS_SITE / "tracks.md",
        DOCS_SITE / "architecture" / "index.md",
        DOCS_SITE / "configuration.md",
        DOCS_SITE / "operations.md",
        DOCS_SITE / "development.md",
        DOCS_SITE / "reference" / "index.md",
    ]
    for path in required_pages:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# ")
        assert "## 1. " in text

    home = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    assert '<div class="atlas-hero">' in home
    assert "assets/images/atlas-source.png" in home
    assert "assets/atlas-poster.png" in home
    assert "screenshots/wizard-running.png" in home
    assert "Atlas is a self-hosted" in home

    overview = (DOCS_SITE / "overview.md").read_text(encoding="utf-8")
    assert "../assets/atlas-poster.png" in overview

    architecture = (DOCS_SITE / "architecture" / "index.md").read_text(encoding="utf-8")
    assert "../../diagrams/architecture.svg" in architecture

    tracks_page = (DOCS_SITE / "tracks.md").read_text(encoding="utf-8")
    assert "all services (no filtering)" in tracks_page


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
