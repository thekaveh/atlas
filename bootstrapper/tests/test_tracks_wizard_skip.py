"""Tests for _make_track_skip — the per-service skip_if_prev predicate
generator wired into bootstrapper/ui/textual/integration.py.

The predicate reads the picker selection (under
``PICKER_STEP_TITLE``) and returns True (skip) when the service is
neither in the chosen track's services nor in the always-on tier nor
in the explicit-override set.
"""

from __future__ import annotations

import pytest

from tracks import load_tracks, normalize_service_key
from ui.textual.integration import _make_track_skip, PICKER_STEP_TITLE


def test_make_track_skip_returns_callable():
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset(),
        registry=reg,
    )
    assert callable(skip)


def test_predicate_skips_off_track_service():
    """comfyui is NOT in gen-ai-rag's services list → skip = True."""
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset(),
        registry=reg,
    )
    assert skip({PICKER_STEP_TITLE: "gen-ai-rag"}) is True


def test_predicate_does_not_skip_in_track_service():
    """weaviate IS in gen-ai-rag → skip = False."""
    reg = load_tracks()
    skip = _make_track_skip(
        "weaviate",
        always_on=reg.always_on,
        overridden=frozenset(),
        registry=reg,
    )
    assert skip({PICKER_STEP_TITLE: "gen-ai-rag"}) is False


def test_predicate_does_not_skip_always_on_service():
    """LLM Engine is always-on → skip = False for every track."""
    reg = load_tracks()
    skip = _make_track_skip(
        "llm-provider",
        always_on=reg.always_on,
        overridden=frozenset(),
        registry=reg,
    )
    for t in reg.tracks:
        assert skip({PICKER_STEP_TITLE: t.key}) is False, \
            f"llm-provider must NEVER be skipped (track={t.key})"


def test_predicate_does_not_skip_overridden_service():
    """An off-track service that was explicitly overridden via
    --comfyui-source must still appear in the wizard."""
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset({"comfyui"}),
        registry=reg,
    )
    assert skip({PICKER_STEP_TITLE: "gen-ai-rag"}) is False


def test_predicate_handles_all_sentinel():
    """The 'all' track has services=None → no service ever skipped."""
    reg = load_tracks()
    for svc_key in ("comfyui", "spark", "ray-head", "weaviate"):
        skip = _make_track_skip(
            svc_key, always_on=reg.always_on, overridden=frozenset(),
            registry=reg,
        )
        assert skip({PICKER_STEP_TITLE: "all"}) is False


def test_predicate_normalizes_family_aliases():
    """ml-eng lists 'ray' in tracks.yml; the wizard hands the predicate
    'ray-head'. The predicate must normalize before lookup."""
    reg = load_tracks()
    skip = _make_track_skip(
        "ray-head",
        always_on=reg.always_on,
        overridden=frozenset(),
        registry=reg,
    )
    assert skip({PICKER_STEP_TITLE: "ml-eng"}) is False


def test_predicate_normalizes_runtime_sc_divergence():
    """Single-container svc.keys that diverge from folder names normalize
    too (open-web-ui→open-webui, neo4j-graph-db→neo4j)."""
    reg = load_tracks()
    # open-web-ui is in gen-ai-rag (which lists 'open-webui' in YAML).
    skip_owui = _make_track_skip(
        "open-web-ui",
        always_on=reg.always_on,
        overridden=frozenset(),
        registry=reg,
    )
    assert skip_owui({PICKER_STEP_TITLE: "gen-ai-rag"}) is False
    # neo4j-graph-db is in data-eng (which lists 'neo4j' in YAML).
    skip_neo = _make_track_skip(
        "neo4j-graph-db",
        always_on=reg.always_on,
        overridden=frozenset(),
        registry=reg,
    )
    assert skip_neo({PICKER_STEP_TITLE: "data-eng"}) is False


def test_predicate_normalizes_virtual_manifest_aliases():
    """ServiceDiscovery surfaces stt-provider's svc.key as 'stt_provider'
    (underscore). The predicate must normalize to 'stt-provider' before
    looking it up in track.services. Same for tts_provider, doc_processor,
    llm_provider."""
    reg = load_tracks()
    # stt_provider IS in gen-ai-eng, must NOT be skipped
    skip_stt = _make_track_skip(
        "stt_provider", always_on=reg.always_on, overridden=frozenset(),
        registry=reg,
    )
    assert skip_stt({PICKER_STEP_TITLE: "gen-ai-eng"}) is False
    # tts_provider IS in gen-ai-creative, must NOT be skipped
    skip_tts = _make_track_skip(
        "tts_provider", always_on=reg.always_on, overridden=frozenset(),
        registry=reg,
    )
    assert skip_tts({PICKER_STEP_TITLE: "gen-ai-creative"}) is False
    # doc_processor IS in gen-ai-rag, must NOT be skipped
    skip_doc = _make_track_skip(
        "doc_processor", always_on=reg.always_on, overridden=frozenset(),
        registry=reg,
    )
    assert skip_doc({PICKER_STEP_TITLE: "gen-ai-rag"}) is False
    # llm_provider is always-on, must NEVER be skipped
    skip_llm = _make_track_skip(
        "llm_provider", always_on=reg.always_on, overridden=frozenset(),
        registry=reg,
    )
    assert skip_llm({PICKER_STEP_TITLE: "ml-eng"}) is False


def test_predicate_normalizes_overridden_set_folder_form():
    """C1 regression guard: start.py provides overridden_services in
    folder-form (e.g., 'stt-provider', 'spark', 'ray'). The predicate
    receives svc.key in wizard-form ('stt_provider', 'spark-master',
    'ray-head'). Override re-enable must normalize the svc.key before
    the override-set check — otherwise the user's CLI flag is silently
    dropped for these 6 services."""
    reg = load_tracks()
    # User passes --spark-source container with --track gen-ai-rag
    # (spark is NOT in gen-ai-rag). Override should re-enable.
    skip = _make_track_skip(
        "spark-master",
        always_on=reg.always_on,
        overridden=frozenset({"spark"}),   # folder form, as start.py produces
        registry=reg,
    )
    assert skip({PICKER_STEP_TITLE: "gen-ai-rag"}) is False, (
        "spark override must re-enable wizard step despite svc.key/folder mismatch"
    )
    # Same for ray-head/ray
    skip = _make_track_skip(
        "ray-head",
        always_on=reg.always_on,
        overridden=frozenset({"ray"}),
        registry=reg,
    )
    assert skip({PICKER_STEP_TITLE: "gen-ai-rag"}) is False
    # Same for stt_provider/stt-provider
    skip = _make_track_skip(
        "stt_provider",
        always_on=reg.always_on,
        overridden=frozenset({"stt-provider"}),
        registry=reg,
    )
    assert skip({PICKER_STEP_TITLE: "ml-eng"}) is False


def test_predicate_missing_selection_does_not_skip():
    """Before the user has visited the picker, the predicate must
    return False (don't pre-emptively skip)."""
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset(),
        registry=reg,
    )
    assert skip({}) is False


def test_predicate_unknown_track_does_not_skip():
    """If somehow the picker selection is garbage, fail open (don't
    skip) — a buggy predicate must not eat user prompts."""
    reg = load_tracks()
    skip = _make_track_skip(
        "comfyui",
        always_on=reg.always_on,
        overridden=frozenset(),
        registry=reg,
    )
    assert skip({PICKER_STEP_TITLE: "bogus-track"}) is False


# ─── Matrix: every track × every wizard svc.key the spec lists ─────
#
# IMPORTANT — the values below are wizard svc.keys (what
# ServiceDiscovery surfaces in services_info.key), NOT folder names.
# Some diverge:
#   - 'open-web-ui' is the runtime_sc key for the services/open-webui family.
#   - 'neo4j-graph-db' is the runtime_sc key for the services/neo4j family.
# Family heads like 'ray-head', 'spark-master', 'airflow-webserver' are
# also wizard svc.keys, not folder names.

EXPECTED_IN_TRACK: dict[str, set[str]] = {
    "gen-ai-rag": {
        "open-web-ui", "weaviate", "neo4j-graph-db", "lightrag",
        "doc-processor", "tei-reranker", "searxng", "mcp-servers",
        "local-deep-researcher",
        # always-on:
        "llm-provider", "prometheus", "grafana",
    },
    "gen-ai-eng": {
        "open-web-ui", "n8n", "hermes", "openclaw", "jupyterhub", "comfyui",
        "neo4j-graph-db", "stt-provider", "tts-provider", "searxng",
        "mcp-servers", "local-deep-researcher",
        "llm-provider", "prometheus", "grafana",
    },
    "gen-ai-creative": {
        "open-web-ui", "comfyui", "stt-provider", "tts-provider",
        "multi2vec-clip", "doc-processor",
        "llm-provider", "prometheus", "grafana",
    },
    "ml-eng": {
        "spark-master", "ray-head", "jupyterhub", "zeppelin",
        "open-web-ui", "minio", "tei-reranker",
        "llm-provider", "prometheus", "grafana",
    },
    "data-eng": {
        "spark-master", "airflow-webserver", "jupyterhub", "zeppelin",
        "minio", "weaviate", "neo4j-graph-db",
        "llm-provider", "prometheus", "grafana",
    },
}

ALL_DISCOVERED: set[str] = set().union(*EXPECTED_IN_TRACK.values())


@pytest.mark.parametrize("track_key", list(EXPECTED_IN_TRACK.keys()))
def test_track_membership_matrix(track_key: str):
    """For every (track, service) pair: predicate matches the spec table."""
    reg = load_tracks()
    expected = EXPECTED_IN_TRACK[track_key]
    for svc in ALL_DISCOVERED:
        skip = _make_track_skip(
            svc, always_on=reg.always_on, overridden=frozenset(),
            registry=reg,
        )
        is_skipped = skip({PICKER_STEP_TITLE: track_key})
        is_in_expected = svc in expected
        assert is_skipped != is_in_expected, (
            f"track={track_key} svc={svc}: "
            f"expected in_track={is_in_expected} but predicate "
            f"says skip={is_skipped}"
        )


def test_comfyui_substep_respects_track_skip():
    """ComfyUI sub-steps (model picker) must be skipped when ComfyUI
    itself is off-track, even if .env's COMFYUI_SOURCE is non-disabled
    from a previous run. Regression guard for audit finding R1 (partially
    unmitigated risk: ComfyUI sub-step skip_if_prev didn't compose with
    the track-skip predicate)."""
    from ui.textual.integration import _build_steps_and_rows, PICKER_STEP_TITLE
    from core.config_parser import ConfigParser
    from utils.hosts_manager import HostsManager
    cp = ConfigParser()
    steps, _, _, _, _, _ = _build_steps_and_rows(
        cp, HostsManager(),
        track_key="data-eng",  # excludes ComfyUI
        overridden_services=frozenset(),
    )
    # Find any step whose title mentions "ComfyUI" and that has a non-None
    # skip_if_prev. With the fix, calling that predicate against a
    # data-eng selection MUST return True (skip) — even if env values
    # would otherwise allow the sub-step to fire.
    comfyui_steps = [
        s for s in steps
        if "ComfyUI" in (getattr(s, "title", "") or "")
        or "comfyui" in (getattr(s, "title", "") or "").lower()
    ]
    selections_data_eng = {PICKER_STEP_TITLE: "data-eng"}
    for s in comfyui_steps:
        skip = getattr(s, "skip_if_prev", None)
        if skip is None:
            continue
        assert skip(selections_data_eng) is True, (
            f"ComfyUI step {getattr(s, 'title', '?')!r} did not respect "
            f"data-eng track-skip; sub-step skip predicate is leaking ComfyUI "
            f"prompts for off-track runs."
        )
