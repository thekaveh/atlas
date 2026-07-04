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
