from __future__ import annotations

from pathlib import Path

import pytest

from docs.sitegen.model import load_docs_model


ROOT = Path(__file__).resolve().parents[2]


def test_docs_model_indexes_services_and_tracks() -> None:
    model = load_docs_model(ROOT)

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
    assert supabase.track_keys


def test_docs_model_service_access_and_dependencies_are_normalized() -> None:
    model = load_docs_model(ROOT)
    litellm = model.services_by_name["litellm"]

    assert litellm.source_var == "LITELLM_SOURCE"
    assert litellm.source_values
    # A single-valued always-on service lists its env default, not "-".
    assert model.services_by_name["backend"].source_values == ["container"]
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


def test_docs_model_preserves_all_track_as_no_filtering_sentinel() -> None:
    model = load_docs_model(ROOT)

    all_track = model.tracks_by_key["all"]

    assert all_track.all_services is True
    assert all_track.services == []
    assert all_track.services_display == "all services (no filtering)"


def test_docs_track_services_keep_tracks_yml_order() -> None:
    model = load_docs_model(ROOT)

    assert model.tracks_by_key["data-eng"].services[:3] == ["spark", "airflow", "jupyterhub"]


def test_docs_track_loader_is_the_canonical_validator(tmp_path: Path) -> None:
    from docs.sitegen.model import _load_tracks
    from tracks import TracksLoadError

    (tmp_path / "bootstrapper").mkdir()
    (tmp_path / "bootstrapper" / "tracks.yml").write_text(
        "tracks:\n"
        "  - key: dup-one\n    display_name: A\n    description: one\n    services: [redis]\n"
        "  - key: dup-one\n    display_name: A\n    description: two\n    services: [redis]\n"
    )
    with pytest.raises(TracksLoadError, match="duplicate track key 'dup-one'"):
        _load_tracks(tmp_path)


def test_docs_membership_differs_from_the_wizard_predicate_only_for_locked_services() -> None:
    """Intended difference: the docs list locked always-running services and
    the always-prompted LLM Engine in every track. The wizard predicate never
    selects locked services, because it is never prompted for them."""
    from tracks import LLM_ENGINE_FOLDER, is_in_track, load_tracks

    model = load_docs_model(ROOT)
    registry = load_tracks()
    all_keys = {track.key for track in registry.tracks}
    curated = [track for track in registry.tracks if track.services is not None]

    assert set(model.services_by_name["supabase"].track_keys) == all_keys
    assert set(model.services_by_name[LLM_ENGINE_FOLDER].track_keys) == all_keys
    assert not any(
        is_in_track(track, "supabase", always_on=registry.always_on) for track in curated
    )


def test_table_cells_escape_pipes_like_the_other_writers() -> None:
    from docs.sitegen.rendering import table

    assert table(["A"], [["x | y"]]).splitlines()[-1] == "| x \\| y |"
