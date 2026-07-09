"""Tests for the start.py-level override-set + force-disable synthesis
(the no-wizard / --no-tui code path that mirrors _selections_to_args).
"""

from __future__ import annotations

import pytest

from tracks import load_tracks, is_in_track, synthesize_track_source_args


def test_shared_track_synthesis_preserves_overrides_and_disables_off_track():
    """Production helper covers the no-wizard/no-TUI track contract."""
    source_args = {
        "comfyui_source": "container",
        "weaviate_source": None,
        "airflow_source": None,
        "cloud_openai_source": None,
    }
    reg = load_tracks()

    overridden = synthesize_track_source_args(
        source_args,
        track_key="gen-ai-rag",
        registry=reg,
        force_disable=True,
    )

    assert source_args["comfyui_source"] == "container"
    assert source_args["weaviate_source"] is None
    assert source_args["airflow_source"] == "disabled"
    assert source_args["cloud_openai_source"] is None
    assert overridden == {"comfyui"}


def test_shared_track_synthesis_wizard_mode_records_overrides_without_disabling():
    """Wizard mode must not pre-fill source_args with disabled values."""
    source_args = {
        "comfyui_source": "container",
        "airflow_source": None,
    }
    reg = load_tracks()

    overridden = synthesize_track_source_args(
        source_args,
        track_key="gen-ai-rag",
        registry=reg,
        force_disable=False,
    )

    assert source_args["comfyui_source"] == "container"
    assert source_args["airflow_source"] is None
    assert overridden == {"comfyui"}


def test_off_track_flag_in_overridden_set():
    """When --track gen-ai-rag --comfyui-source container is passed
    via CLI, the synthesis block must:
      - add 'comfyui' to overridden_services
      - leave source_args['comfyui_source'] = 'container' (user choice)
      - NOT write 'disabled' for it
    """
    source_args = {
        "comfyui_source": "container",
        "weaviate_source": None,   # off-track, no user override
    }
    reg = load_tracks()
    track_obj = reg.by_key["gen-ai-rag"]
    always_on = reg.always_on
    # gen-ai-rag includes weaviate, excludes comfyui
    overridden_services = synthesize_track_source_args(
        source_args,
        track_key="gen-ai-rag",
        registry=reg,
        force_disable=True,
    )
    assert "comfyui" in overridden_services
    assert source_args["comfyui_source"] == "container"   # user choice preserved
    # weaviate is in-track → not touched
    assert source_args["weaviate_source"] is None


def test_off_track_no_flag_force_disabled():
    """When --track gen-ai-rag is passed alone, comfyui_source goes to
    'disabled' (and is NOT added to overridden_services)."""
    source_args = {"comfyui_source": None, "weaviate_source": None}
    reg = load_tracks()
    overridden_services = synthesize_track_source_args(
        source_args,
        track_key="gen-ai-rag",
        registry=reg,
        force_disable=True,
    )
    assert source_args["comfyui_source"] == "disabled"
    assert "comfyui" not in overridden_services


def test_gen_ai_rag_preserves_n8n_source():
    """n8n is part of the RAG track, so track synthesis must not
    force-disable it when --track gen-ai-rag is selected."""
    source_args = {"n8n_source": None, "comfyui_source": None}
    reg = load_tracks()

    synthesize_track_source_args(
        source_args,
        track_key="gen-ai-rag",
        registry=reg,
        force_disable=True,
    )

    assert source_args["n8n_source"] is None
    assert source_args["comfyui_source"] == "disabled"


def test_explicit_off_track_disabled_flag_is_not_reported_as_enabling_override():
    source_args = {"comfyui_source": "disabled", "weaviate_source": None}
    reg = load_tracks()

    overridden_services = synthesize_track_source_args(
        source_args,
        track_key="gen-ai-rag",
        registry=reg,
        force_disable=True,
    )

    assert source_args["comfyui_source"] == "disabled"
    assert "comfyui" not in overridden_services
