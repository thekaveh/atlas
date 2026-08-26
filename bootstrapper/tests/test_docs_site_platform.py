from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from PIL import Image
import pytest
import yaml

from scripts.docs.build_docs import render_mkdocs_yml
from scripts.docs.links import find_links, is_forbidden
from scripts.docs.manifest import load_manifest
from services.manifests import load_manifests
from tests.three_surface_test_utils import PROJECTION_ROOT, ensure_generated_docs


ROOT = Path(__file__).resolve().parents[2]
GENERATED = PROJECTION_ROOT
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
    ensure_generated_docs()


def _manifest():
    return load_manifest(ROOT / "docs" / "manifest.yaml", ROOT)


def _mkdocs() -> dict:
    return yaml.safe_load(render_mkdocs_yml(_manifest()))


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

    adapter_row = next(
        line for line in index.splitlines() if "docling-lightrag-adapter" in line
    )
    assert "| all |" in adapter_row
    assert "gen-ai-rag" not in adapter_row


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
        # tracks.md deliberately absent: the reference copy was byte-identical
        # to the nav-section-4 page and was collapsed into it (#838). The
        # surviving page is docs/tracks.md.
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
        assert "Open the full-size diagram" in text, page
        assert re.search(
            rf"!\[[^]]+\]\(\.\./diagrams/img/architecture-{re.escape(page.stem)}\.png\)",
            text,
        ), page
        assert "interactive diagram" not in text, page
        interactive = page.with_suffix(".html").read_text(encoding="utf-8")
        assert "How to read this view" in interactive, page
        assert "architecture-diagram design system" not in interactive, page
        assert "Update trigger" not in interactive, page



def test_source_model_architecture_keeps_source_choices_independent() -> None:
    source_model = (DIAGRAMS_DIR / "source-configuration-model.html").read_text(
        encoding="utf-8"
    )
    assert 'data-source="SOURCE Var" data-target="container"' in source_model
    assert 'data-source="SOURCE Var" data-target="localhost"' in source_model
    assert 'data-source="container" data-target="localhost"' not in source_model



def test_network_architecture_keeps_gateway_and_direct_ports_distinct() -> None:
    network = (DIAGRAMS_DIR / "network-routing-topology.html").read_text(
        encoding="utf-8"
    )
    assert 'data-source="Browser" data-target="*.localhost"' in network
    assert 'data-source="Browser" data-target="Direct Ports"' in network
    assert 'data-source="Kong" data-target="Direct Ports"' not in network
    assert ">host NAT</text>" in network
    assert ">publishes</text>" not in network


def test_llm_architecture_includes_managed_vllm_metal() -> None:
    from bootstrapper.docs.sitegen.pages import (
        ARCHITECTURE_EDGES,
        ARCHITECTURE_LAYOUTS,
        ARCHITECTURE_PERSPECTIVES,
        ARCHITECTURE_SOURCE_FILES,
        _NODE_KINDS,
    )

    assert "vLLM Metal" in ARCHITECTURE_PERSPECTIVES["source-configuration-model"][2]
    assert ("none", "vLLM Metal", "pairs with") in ARCHITECTURE_EDGES[
        "source-configuration-model"
    ]
    assert "vLLM Metal" in ARCHITECTURE_PERSPECTIVES["llm-provider-flow"][2]
    assert ("LiteLLM", "vLLM Metal", "managed local") in ARCHITECTURE_EDGES[
        "llm-provider-flow"
    ]
    for slug in ("source-configuration-model", "llm-provider-flow"):
        assert "services/vllm-metal/service.yml" in ARCHITECTURE_SOURCE_FILES[slug]
    llm_layout = ARCHITECTURE_LAYOUTS["llm-provider-flow"]
    telemetry_y = llm_layout["LiteLLM"][1] + 30
    vllm_y = llm_layout["vLLM Metal"][1]
    assert not vllm_y <= telemetry_y <= vllm_y + 60
    assert _NODE_KINDS.get("vLLM Metal", "generic") == "generic"


def test_track_architecture_models_explicit_disabled_overrides() -> None:
    from bootstrapper.docs.sitegen.pages import (
        ARCHITECTURE_EDGES,
        ARCHITECTURE_PERSPECTIVES,
    )

    edges = ARCHITECTURE_EDGES["track-selection-matrix"]
    assert ("Overrides", "Selected Source", "authoritative") in edges
    assert ("Selected Source", "Enabled", "non-disabled") in edges
    assert ("Selected Source", "Disabled", "explicit") in edges
    assert ("Selected Source", "Force Disabled", "disabled") not in edges
    assert ("Overrides", "Enabled", "authoritative") not in edges
    assert "Disabled" in ARCHITECTURE_PERSPECTIVES["track-selection-matrix"][2]


def test_localhost_port_docs_distinguish_transport_and_route_contracts() -> None:
    text = (ROOT / "docs/deployment/ports-and-routes.md").read_text(encoding="utf-8")
    for expected in (
        "TIKA_LOCALHOST_PORT",
        "COMFYUI_MPS_LOCALHOST_PORT",
        "VLLM_METAL_LOCALHOST_PORT",
        "BLENDER_MCP_LOCALHOST_PORT",
        "TCP",
        "No Kong route",
    ):
        assert expected in text
    assert "Every localhost-source service" not in text
    assert "Hermes API and dashboard" not in text
    assert (
        "| Direct HTTP localhost | Hermes API | `HERMES_LOCALHOST_PORT`"
        in text
    )
    assert (
        "| HTTP + Kong | Hermes dashboard | `HERMES_LOCALHOST_DASHBOARD_PORT`"
        in text
    )


def test_managed_host_docs_and_historical_reference_name_current_surfaces() -> None:
    operations = (ROOT / "docs/operations.md").read_text(encoding="utf-8")
    opening = operations.split("## 8. Managed Host Lifecycle", 1)[1].split("\n\n", 2)[1]
    assert "Blender" in opening
    changelog = (ROOT / "docs/CHANGELOG.md").read_text(encoding="utf-8")
    assert "docs/README.md §1.7" not in changelog
    assert "docs/README.md §1.8" in changelog


def test_observability_architecture_names_each_trace_producer() -> None:
    observability = (DIAGRAMS_DIR / "observability-flow.html").read_text(
        encoding="utf-8"
    )
    expected = {
        'data-source="Backend" data-target="OTel Collector"',
        'data-source="Celery Workers" data-target="OTel Collector"',
        'data-source="LiteLLM" data-target="OTel Collector"',
        'data-source="LiteLLM" data-target="Langfuse"',
        'data-source="Atlas Services" data-target="Prometheus"',
    }
    assert not {edge for edge in expected if edge not in observability}
    assert 'data-source="Services" data-target="OTel Collector"' not in observability



def test_security_architecture_models_route_and_application_auth_separately() -> None:
    security = (DIAGRAMS_DIR / "security-auth-secrets-boundary.html").read_text(
        encoding="utf-8"
    )
    expected = {
        'data-source="Kong" data-target="Kong Route Policies"',
        'data-source="Routed Services" data-target="Backend API"',
        'data-source="Direct Clients" data-target="Backend API"',
        'data-source="Backend API" data-target="Public APIs"',
        'data-source="Supabase Auth" data-target="Backend Identity"',
        'data-source="Backend Identity" data-target="Protected APIs"',
        'data-source="Plugin Key Auth" data-target="Protected APIs"',
    }
    assert not {edge for edge in expected if edge not in security}
    forbidden = {
        'data-source="Kong" data-target="Backend Identity"',
        'data-source="Kong" data-target="Supabase Auth"',
    }
    assert not {edge for edge in forbidden if edge in security}
    security_page = (
        DIAGRAMS_DIR / "security-auth-secrets-boundary.md"
    ).read_text(encoding="utf-8")
    assert "services/backend/app/app/backend_identity.py" in security_page


def test_architecture_edge_labels_do_not_share_coordinates() -> None:
    from bootstrapper.docs.sitegen.pages import (
        ARCHITECTURE_EDGES,
        ARCHITECTURE_INTERPRETATIONS,
        ARCHITECTURE_LAYOUTS,
        ARCHITECTURE_PERSPECTIVES,
        _architecture_diagram_html,
    )

    label_pattern = re.compile(
        r'<text x="([\d.]+)" y="([\d.]+)" fill="#cbd5e1"[^>]*>([^<]+)</text>'
    )
    for slug, (title, description, nodes) in ARCHITECTURE_PERSPECTIVES.items():
        rendered = _architecture_diagram_html(
            title,
            description,
            ARCHITECTURE_INTERPRETATIONS[slug],
            nodes,
            ARCHITECTURE_EDGES[slug],
            ARCHITECTURE_LAYOUTS[slug],
        )
        labels = label_pattern.findall(rendered)
        coordinates = [(x, y) for x, y, _label in labels]
        assert len(coordinates) == len(set(coordinates)), slug


def test_top_level_architecture_long_routes_use_reviewed_gutters_and_endpoints() -> None:
    svg = (ROOT / "docs" / "diagrams" / "architecture.svg").read_text(
        encoding="utf-8"
    )
    html_master = (ROOT / "docs" / "diagrams" / "architecture.html").read_text(
        encoding="utf-8"
    )
    for master in (svg, html_master):
        for crossing in (
            '<line x1="450" y1="360" x2="450" y2="668"',
            '<line x1="700" y1="360" x2="700" y2="788"',
            '<line x1="1200" y1="360" x2="1200" y2="788"',
            '<line x1="700" y1="360" x2="700" y2="928"',
            '<line x1="1200" y1="480" x2="1020" y2="928"',
        ):
            assert crossing not in master
        assert "L 825 510" in master
        assert "L 1325 770" in master
        for current_media_route in (
            "M 200 360 L 75 380 L 75 650 L 200 650 L 200 668",
            "M 700 360 L 825 380 L 825 650 L 950 650 L 950 668",
            "M 200 480 L 325 500 L 325 640 L 450 640 L 450 668",
        ):
            assert current_media_route in master
        assert "M 450 360 L 575 380" not in master
        assert "M 450 480 L 575 500" not in master


def test_llm_docs_and_help_treat_none_as_no_ollama_not_cloud_only() -> None:
    source_config = (ROOT / "docs/deployment/source-configuration.md").read_text(
        encoding="utf-8"
    )
    wizard = (ROOT / "docs/quick-start/interactive-setup-wizard.md").read_text(
        encoding="utf-8"
    )
    litellm = (ROOT / "services/litellm/README.md").read_text(encoding="utf-8")
    ollama_readme = (ROOT / "services/ollama/README.md").read_text(
        encoding="utf-8"
    )
    ollama_manifest = (ROOT / "services/ollama/service.yml").read_text(
        encoding="utf-8"
    )
    roadmap = (ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8")
    cli = (ROOT / "bootstrapper/start.py").read_text(encoding="utf-8")
    tui = (ROOT / "bootstrapper/ui/textual/integration.py").read_text(
        encoding="utf-8"
    )

    for text in (source_config, wizard, litellm):
        assert "vLLM Metal" in text
        assert "no Ollama upstream" in text
    assert 'Use "none" for no Ollama upstream' in cli
    assert 'badges.append("no Ollama")' in tui
    assert "cloud-only" not in ollama_manifest
    assert "no local engine" not in ollama_readme
    assert "vLLM Metal and/or enabled cloud providers" in ollama_readme
    assert "engine=none + vLLM Metal disabled + all cloud disabled" in roadmap


def test_llm_docs_qualify_native_bypasses_and_do_not_promise_failover() -> None:
    source_config = (ROOT / "docs/deployment/source-configuration.md").read_text(
        encoding="utf-8"
    )
    litellm = (ROOT / "services/litellm/README.md").read_text(encoding="utf-8")

    assert "One URL/key for every consumer" not in source_config
    assert "Every consumer service" not in litellm
    assert "because every consumer reads only" not in litellm
    for text in (source_config, litellm):
        assert "provider failover" not in text.lower()
        assert "native-provider" in text


def test_vllm_metal_host_guidance_is_track_aware_and_complete() -> None:
    source_config = (ROOT / "docs/deployment/source-configuration.md").read_text(
        encoding="utf-8"
    )
    wizard = (ROOT / "docs/quick-start/interactive-setup-wizard.md").read_text(
        encoding="utf-8"
    )

    assert "VLLM_METAL_SOURCE" in source_config
    assert "managed-localhost" in source_config
    assert "Generative AI · Engineering" in wizard
    assert "All / Custom" in wizard
    assert "profiles; tracks" not in wizard
    assert "tracks; tracks" not in wizard


def test_submodule_compose_and_kong_examples_respect_project_boundaries() -> None:
    guide = (ROOT / "docs/deployment/submodule-usage.md").read_text(
        encoding="utf-8"
    )
    assert "depends_on:\n      - myproject-supabase-db" not in guide
    assert "separate Compose project" in guide
    assert "services whose manifests declare Kong routes" in guide
    assert "endpoints export" in guide


def test_platform_and_jupyter_docs_do_not_overstate_integration_coverage() -> None:
    overview = (ROOT / "docs/architecture/platform-overview.md").read_text(
        encoding="utf-8"
    )
    home = (ROOT / "docs/index.md").read_text(encoding="utf-8")
    jupyter = (ROOT / "services/jupyterhub/README.md").read_text(encoding="utf-8")

    assert "All model traffic" not in overview
    assert "LiteLLM is the single path" not in home
    assert "access to all Atlas services" not in jupyter
    assert "Auto-configured connections to all services" not in jupyter
    assert "current MCP endpoint" in jupyter


def test_canonical_home_embeds_the_committed_platform_image() -> None:
    home = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    assert re.search(
        r"!\[[^]]+\]\(diagrams/img/atlas-platform\.png\)", home
    )
    assert "](diagrams/architecture.html)" not in home


def test_architecture_interpretation_renders_safe_inline_markup() -> None:
    from bootstrapper.docs.sitegen.pages import _architecture_diagram_html

    rendered = _architecture_diagram_html(
        "Contract view",
        "A focused contract test.",
        "Use `CATALOG_URI`; see [provider flow](./provider-flow.md).",
        ["Source", "Target"],
        [("Source", "Target", "calls")],
        {"Source": (40, 80), "Target": (320, 80)},
    )

    assert "<code>CATALOG_URI</code>" in rendered
    assert '<a href="./provider-flow.md">provider flow</a>' in rendered
    assert "`CATALOG_URI`" not in rendered
    assert "[provider flow]" not in rendered


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


def test_home_and_theme_preserve_the_atlas_clean_systems_visual_contract() -> None:
    config = _mkdocs()
    home = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    css = THEME_CSS.read_text(encoding="utf-8")

    assert config["theme"]["name"] == "material"
    assert config["theme"]["palette"][0]["scheme"] == "slate"
    assert config["theme"]["palette"][0]["toggle"]["name"] == "Switch to light mode"
    assert config["theme"]["palette"][1]["scheme"] == "default"
    assert config["theme"]["palette"][1]["toggle"]["name"] == "Switch to dark mode"
    assert config["theme"]["logo"]
    assert config["theme"]["favicon"]
    assert config["theme"]["font"]["text"] == "Public Sans"
    assert "atlas-home" in home
    assert "assets/atlas-poster-blue.png" in home
    assert "assets/atlas-poster-gold.png" not in home
    assert "screenshots/wizard-running.png" in home
    assert ".md-content--atlas-wide" in css
    # Clean Systems accent is present; the old dark-first "atlas dark" void tokens are gone.
    assert "#2563eb" in css
    assert "#020617" not in css
    assert "atlas-void" not in css
    # Hero stays a two-column grid without pinning the exact minmax() fractions.
    hero_rule = re.search(r"\.atlas-home__hero\s*\{([^}]*)\}", css)
    assert hero_rule is not None
    assert "grid-template-columns:" in hero_rule.group(1)
    assert hero_rule.group(1).count("minmax(") == 2
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
    assert "pages" not in workflow["permissions"]
    assert "id-token" not in workflow["permissions"]
    assert workflow["jobs"]["deploy"]["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert "WIKI_DEPLOY_KEY" in text
    assert "WIKI_KNOWN_HOSTS" in text
    assert "StrictHostKeyChecking=accept-new" not in text
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
    assert "notebook-reproducibility" not in workflow["jobs"]
    assert text.count("python -m scripts.notebook_reproducibility") == 1
    assert workflow["jobs"]["audit-scripts"]["timeout-minutes"] == 45


def test_source_configuration_shell_examples_do_not_comment_after_continuations() -> None:
    source = (ROOT / "docs" / "deployment" / "source-configuration.md").read_text(
        encoding="utf-8"
    )
    assert "\\  #" not in source


def test_services_lint_build_validation_covers_all_local_build_contexts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    expected = {
        (
            path.parent.relative_to(ROOT).as_posix(),
            "Dockerfile",
        )
        for path in (ROOT / "services").glob("*/init/Dockerfile")
    }
    excluded_dockerfiles = {
        "services/docling/provider/gpu/Dockerfile",
        "services/parakeet/provider/gpu/Dockerfile",
    }
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
            dockerfile = str(build_spec.get("dockerfile", "Dockerfile"))
            dockerfile_path = (ROOT / relative / dockerfile).resolve().relative_to(ROOT)
            if dockerfile_path.as_posix() not in excluded_dockerfiles:
                expected.add((relative, dockerfile))
    assert expected
    for context, dockerfile in expected:
        assert f'"{context}|{dockerfile}|' in workflow


def test_adapter_tmpfs_covers_default_concurrent_upload_and_result_budget() -> None:
    compose = yaml.safe_load((ROOT / "services/docling/compose.yml").read_text())
    manifest = yaml.safe_load((ROOT / "services/docling/service.yml").read_text())
    adapter = compose["services"]["docling-lightrag-adapter"]
    environment = adapter["environment"]
    manifest_env = {entry["name"]: entry for entry in manifest["env"]}

    def default_int(name: str) -> int:
        value = environment[name]
        return int(re.search(r":-(\d+)}$", value).group(1))

    tmpfs = adapter["tmpfs"][0]
    assert "${DOCLING_ADAPTER_TMPFS_SIZE:-512m}" in tmpfs
    size_mib = int(
        str(manifest_env["DOCLING_ADAPTER_TMPFS_SIZE"]["default"]).removesuffix("m")
    )
    upload_bytes = default_int("DOCLING_MAX_FILE_SIZE")
    result_bytes = default_int("DOCLING_ADAPTER_MAX_RESULT_BYTES")
    required_bytes = default_int("DOCLING_ADAPTER_MAX_JOBS") * max(
        2 * upload_bytes + 1024 * 1024,
        upload_bytes + result_bytes,
    )
    assert size_mib * 1024 * 1024 >= required_bytes + 64 * 1024 * 1024
    assert "shutil.disk_usage(root).free < required_storage" in (
        ROOT / "services/docling/provider/adapter/app.py"
    ).read_text(encoding="utf-8")


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
