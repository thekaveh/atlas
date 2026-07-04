from __future__ import annotations

from pathlib import Path

from docs.sitegen.model import load_docs_model


ROOT = Path(__file__).resolve().parents[2]


def test_docs_model_indexes_services_tracks_and_assets() -> None:
    model = load_docs_model(ROOT)

    assert model.public_url == "https://thekaveh.github.io/atlas/"
    assert model.hero_image == Path("assets/images/atlas-source.png")
    assert model.wizard_screenshot == Path("screenshots/wizard-running.png")
    assert "data-eng" in model.tracks_by_key
    assert "gen-ai-rag" in model.tracks_by_key

    services = model.services_by_name
    assert "supabase" in services
    assert "open-webui" in services
    assert "cloud-providers" in services
    assert "stt-provider" in services

    supabase = services["supabase"]
    assert supabase.title
    assert supabase.category in {"infra", "data", "llm", "media", "agents", "apps", "aggregate"}
    assert supabase.kind in {"container", "virtual", "doc-only"}
    assert supabase.readme == ROOT / "services" / "supabase" / "README.md"
    assert supabase.diagram_svg == ROOT / "services" / "supabase" / "architecture.svg"
    assert supabase.track_keys


def test_docs_model_service_access_and_dependencies_are_normalized() -> None:
    model = load_docs_model(ROOT)
    litellm = model.services_by_name["litellm"]

    assert litellm.source_var == "LITELLM_SOURCE"
    assert litellm.source_values
    assert isinstance(litellm.required_dependencies, list)
    assert isinstance(litellm.optional_dependencies, list)
    assert isinstance(litellm.runtime_calls, list)
    assert isinstance(litellm.kong_aliases, list)
    assert isinstance(litellm.port_vars, list)


def test_docs_model_uses_manifest_docs_paths_when_present() -> None:
    model = load_docs_model(ROOT)

    cloud_providers = model.services_by_name["cloud-providers"]
    supabase = model.services_by_name["supabase"]
    stt_provider = model.services_by_name["stt-provider"]

    assert cloud_providers.readme == ROOT / "services" / "litellm" / "README.md"
    assert supabase.readme == ROOT / "services" / "supabase" / "README.md"
    assert stt_provider.readme == ROOT / "services" / "stt-provider" / "README.md"


def test_docs_model_merges_topology_and_extra_kong_aliases() -> None:
    model = load_docs_model(ROOT)

    minio = model.services_by_name["minio"]
    graph_builder = model.services_by_name["llm-graph-builder"]

    assert minio.kong_aliases == ["minio.localhost", "s3.minio.localhost"]
    assert graph_builder.kong_aliases == [
        "graphbuilder.localhost",
        "graphbuilder-api.localhost",
    ]


def test_docs_model_exposes_multiple_source_surfaces_for_virtual_manifests() -> None:
    model = load_docs_model(ROOT)

    cloud_providers = model.services_by_name["cloud-providers"]

    assert cloud_providers.source_var == "CLOUD_OPENAI_SOURCE"
    assert cloud_providers.source_default == "disabled"
    assert cloud_providers.source_values == ["enabled", "disabled"]
    assert [surface.var for surface in cloud_providers.source_surfaces] == [
        "CLOUD_OPENAI_SOURCE",
        "CLOUD_ANTHROPIC_SOURCE",
        "CLOUD_OPENROUTER_SOURCE",
    ]
    assert all(surface.default == "disabled" for surface in cloud_providers.source_surfaces)
    assert all(surface.values == ["enabled", "disabled"] for surface in cloud_providers.source_surfaces)
