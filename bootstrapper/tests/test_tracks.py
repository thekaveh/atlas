"""Unit tests for bootstrapper/tracks.py — the track registry loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tracks import (
    Track,
    TrackRegistry,
    load_tracks,
    compute_always_on,
    is_in_track,
    normalize_service_key,
    format_track_list,
    UnknownTrackServiceError,
    TracksLoadError,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKS_YML = REPO_ROOT / "bootstrapper" / "tracks.yml"


# ─── load_tracks ─────────────────────────────────────────────────────

def test_load_tracks_default_path():
    """Default path = bootstrapper/tracks.yml; returns a TrackRegistry."""
    reg = load_tracks()
    assert isinstance(reg, TrackRegistry)
    assert len(reg.tracks) >= 7
    expected = {
        "gen-ai-rag",
        "gen-ai-eng",
        "gen-ai-creative",
        "ml-eng",
        "data-eng",
        "trading",
        "all",
    }
    assert {t.key for t in reg.tracks} >= expected
    assert reg.tracks[0].key == "gen-ai-rag"
    assert reg.tracks[-1].key == "all"


def test_load_tracks_explicit_path(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"key": "xt", "display_name": "X", "description": "desc",
             "services": ["weaviate"]},
        ]
    }))
    reg = load_tracks(p)
    assert len(reg.tracks) == 1
    assert reg.tracks[0].key == "xt"
    assert reg.tracks[0].services == frozenset({"weaviate"})


def test_load_tracks_all_sentinel(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"key": "all", "display_name": "All", "description": "every",
             "services": "*"},
        ]
    }))
    reg = load_tracks(p)
    assert reg.tracks[0].services is None  # "*" → None sentinel


def test_load_tracks_schema_violation_empty(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({"tracks": []}))   # minItems: 1
    with pytest.raises(TracksLoadError) as ei:
        load_tracks(p)
    assert "schema violation" in str(ei.value)


def test_load_tracks_schema_violation_missing_key(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"display_name": "X", "description": "d", "services": ["weaviate"]},
        ]
    }))
    # Must be caught by the jsonschema gate (TracksLoadError "schema violation"),
    # NOT by the later `key = entry["key"]` KeyError — a bare pytest.raises
    # (Exception) would pass even if the schema block were deleted.
    with pytest.raises(TracksLoadError) as ei:
        load_tracks(p)
    assert "schema violation" in str(ei.value)


def test_load_tracks_schema_violation_bad_key_pattern(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"key": "BadKey", "display_name": "X", "description": "d",
             "services": ["weaviate"]},
        ]
    }))
    with pytest.raises(TracksLoadError) as ei:
        load_tracks(p)
    assert "schema violation" in str(ei.value)


def test_load_tracks_duplicate_key_raises(tmp_path: Path):
    """Two tracks with the same `key` must reject — tracks.py builds a
    `by_key` map and ``--track <key>`` would otherwise silently resolve
    to whichever entry sorts last. Defense in depth: the schema's
    `uniqueItems: true` catches an exact copy-paste, the runtime guard
    catches same-key + different display_name/description/services.
    """
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"key": "foo", "display_name": "First", "description": "d",
             "services": ["weaviate"]},
            {"key": "foo", "display_name": "Second", "description": "d",
             "services": ["neo4j-graph-db"]},
        ]
    }))
    with pytest.raises(Exception, match="duplicate"):
        load_tracks(p)


def test_load_tracks_rejects_duplicate_yaml_mapping_keys(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text(
        """
tracks:
  - key: probe
    display_name: First
    display_name: Second
    description: duplicate mapping key
    services: '*'
""".strip()
    )

    with pytest.raises(TracksLoadError, match="duplicate key.*display_name"):
        load_tracks(p)


def test_load_tracks_wraps_unhashable_yaml_key(tmp_path: Path):
    p = tmp_path / "t.yml"
    p.write_text("? [bad, key]\n: value\n", encoding="utf-8")

    with pytest.raises(TracksLoadError, match="unhashable key"):
        load_tracks(p)


def test_load_tracks_unknown_service_raises(tmp_path: Path):
    """Cross-check: a service that doesn't exist as services/<name>/ rejects."""
    p = tmp_path / "t.yml"
    p.write_text(yaml.safe_dump({
        "tracks": [
            {"key": "xt", "display_name": "X", "description": "d",
             "services": ["nonexistent-service"]},
        ]
    }))
    with pytest.raises(UnknownTrackServiceError) as exc:
        load_tracks(p)
    assert "nonexistent-service" in str(exc.value)


def test_load_tracks_real_registry_validates():
    """The committed tracks.yml must pass cross-validation against
    the real services/ tree."""
    reg = load_tracks()  # raises on drift
    assert reg.by_key["gen-ai-rag"].display_name == "Generative AI · RAG"


# ─── normalize_service_key ──────────────────────────────────────────

def test_normalize_service_key_passthrough():
    """Plain folder keys pass through unchanged."""
    assert normalize_service_key("weaviate") == "weaviate"
    assert normalize_service_key("comfyui") == "comfyui"


def test_normalize_service_key_family_aliases():
    """Multi-container family heads normalize to the folder name."""
    assert normalize_service_key("ray-head") == "ray"
    assert normalize_service_key("spark-master") == "spark"
    assert normalize_service_key("airflow-webserver") == "airflow"
    assert normalize_service_key("langfuse-web") == "langfuse"


def test_normalize_service_key_runtime_sc_divergence():
    """Some services' runtime_sc keys diverge from the folder name even
    though they're single-container. Wizard discovery uses the runtime_sc
    key; tracks.yml uses the folder name; the alias map bridges them."""
    # services/neo4j/ has runtime_sc key 'neo4j-graph-db'.
    assert normalize_service_key("neo4j-graph-db") == "neo4j"
    # services/open-webui/ has runtime_sc key 'open-web-ui'.
    assert normalize_service_key("open-web-ui") == "open-webui"


def test_normalize_service_key_virtual_manifests():
    """Click flag names use underscores; ServiceDiscovery surfaces those
    services with underscore svc.keys (`stt_provider`, `tts_provider`,
    `doc_processor`, `llm_provider`). The alias map normalizes them to
    their hyphen folder names. Without these aliases, the wizard would
    incorrectly skip these services for every track that includes them."""
    assert normalize_service_key("llm_provider") == "llm-provider"
    assert normalize_service_key("stt_provider") == "stt-provider"
    assert normalize_service_key("tts_provider") == "tts-provider"
    assert normalize_service_key("doc_processor") == "doc-processor"


# ─── compute_always_on ──────────────────────────────────────────────

def test_compute_always_on_returns_canonical_set():
    """The three services that survive ServiceDiscovery filtering and
    must be exempt from track-skip predicates."""
    from core.config_parser import ConfigParser
    cp = ConfigParser()
    aon = compute_always_on(cp)
    assert aon == frozenset({"llm-provider", "prometheus", "grafana"})


# ─── is_in_track ────────────────────────────────────────────────────

def test_is_in_track_always_on_short_circuits():
    """Always-on service is in every track regardless of services list."""
    reg = load_tracks()
    for t in reg.tracks:
        for svc in ("llm-provider", "prometheus", "grafana"):
            assert is_in_track(t, svc, always_on=reg.always_on), \
                f"{svc} must be in-track for {t.key}"


def test_is_in_track_explicit_member():
    """Service explicitly listed is in-track."""
    reg = load_tracks()
    t = reg.by_key["gen-ai-rag"]
    assert is_in_track(t, "weaviate", always_on=reg.always_on)
    assert is_in_track(t, "n8n", always_on=reg.always_on)


def test_is_in_track_explicit_member_via_alias():
    """Family-head svc.key normalizes through aliases — Ray's head appears
    as `ray-head` from the wizard but ml-eng lists `ray` in tracks.yml."""
    reg = load_tracks()
    ml = reg.by_key["ml-eng"]
    assert is_in_track(ml, "ray-head", always_on=reg.always_on)
    assert is_in_track(ml, "spark-master", always_on=reg.always_on)
    assert is_in_track(ml, "langfuse-web", always_on=reg.always_on)
    # neo4j divergence — gen-ai-rag and data-eng list `neo4j` (folder)
    # but the wizard passes `neo4j-graph-db` (runtime_sc key).
    rag = reg.by_key["gen-ai-rag"]
    assert is_in_track(rag, "neo4j-graph-db", always_on=reg.always_on)
    # open-webui divergence — runtime_sc key is `open-web-ui` (3 hyphens).
    assert is_in_track(rag, "open-web-ui", always_on=reg.always_on)


def test_is_in_track_non_member():
    """Service not listed and not always-on is out-of-track."""
    reg = load_tracks()
    t = reg.by_key["gen-ai-rag"]
    assert not is_in_track(t, "comfyui", always_on=reg.always_on)
    assert not is_in_track(t, "spark", always_on=reg.always_on)


def test_is_in_track_all_sentinel_always_true():
    """The 'all' track has services=None → every service is in-track."""
    reg = load_tracks()
    t = reg.by_key["all"]
    for svc in ("comfyui", "spark", "ray-head", "weaviate", "neo4j-graph-db"):
        assert is_in_track(t, svc, always_on=reg.always_on)


# ─── format_track_list ──────────────────────────────────────────────

def test_format_track_list_contains_every_track():
    reg = load_tracks()
    out = format_track_list(reg)
    for t in reg.tracks:
        assert t.key in out
        assert t.display_name in out


# ─── Seam parity: tracks.yml ↔ source_override_manager.source_mapping ─

def test_every_track_service_resolves_via_source_override_manager():
    """Every service listed in tracks.yml must have a corresponding
    `source_mapping` entry in `SourceOverrideManager` — otherwise the
    off-track-disable synthesis in `start.py` (which iterates
    `source_args.keys()` and writes `*_SOURCE=disabled` for off-track
    entries) silently fails to disable that service, breaking the
    track promise (e.g. "gen-ai-rag excludes ComfyUI").

    Without this guard, a future tracks.yml edit that adds a new
    service without a matching CLI/source_mapping seam would compile
    cleanly, ship green CI, and leak the off-track service into
    every track-restricted launch. Same shipping-class risk as
    project_post_merge_env_staleness.md class A.
    """
    from core.config_parser import ConfigParser
    from utils.source_override_manager import SourceOverrideManager

    reg = load_tracks()
    track_services: set[str] = set()
    for t in reg.tracks:
        if t.services is None:  # "all" sentinel
            continue
        track_services.update(t.services)

    # Build the set of services that `source_mapping` can target,
    # normalising each CLI key the same way start.py's track-disable
    # synthesis does (`cli_key.removesuffix("_source").replace("_", "-")`)
    # and then folding through `normalize_service_key` (family /
    # virtual-manifest aliases).
    mgr = SourceOverrideManager(ConfigParser())
    resolvable: set[str] = set()
    for cli_key in mgr.source_mapping:
        svc_key = cli_key.removesuffix("_source").replace("_", "-")
        resolvable.add(normalize_service_key(svc_key))

    # Always-on services don't need a track-disable seam (they're never
    # force-disabled).
    missing = track_services - resolvable - reg.always_on
    assert not missing, (
        f"tracks.yml lists services with no source_mapping seam: "
        f"{sorted(missing)}. Add them to bootstrapper/utils/"
        f"source_override_manager.py::SourceOverrideManager.source_mapping "
        f"(plus the matching Click @option, _flag_values entry, and "
        f"wizard collector lambda — see project memory "
        f"project_cli_source_flag_three_seams.md)."
    )
