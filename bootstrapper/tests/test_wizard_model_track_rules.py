"""Track force-disable rule, extracted from _selections_to_args (#535).

When a track is selected, every source-configurable service that is
out-of-track AND not explicitly overridden gets *_SOURCE=disabled
force-written. Their wizard step was skipped, so without this pass
.env would silently retain the user's prior choice — defeating the
track's force-disable semantic.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from wizard.model.track_rules import track_force_disabled_sources


@dataclass
class _Svc:
    """Minimal ServiceInfo stand-in — the rule only reads .key."""
    key: str


def test_no_track_selected_synthesizes_nothing():
    result = track_force_disabled_sources(
        track_key=None,
        services_info=[_Svc("comfyui"), _Svc("n8n")],
        already_set={},
    )
    assert result == {}


def test_empty_track_key_synthesizes_nothing():
    result = track_force_disabled_sources(
        track_key="",
        services_info=[_Svc("comfyui")],
        already_set={},
    )
    assert result == {}


def test_unknown_track_key_synthesizes_nothing():
    """A track key with no registry entry must not disable everything."""
    result = track_force_disabled_sources(
        track_key="no-such-track",
        services_info=[_Svc("comfyui")],
        already_set={},
    )
    assert result == {}


def test_all_track_synthesizes_nothing():
    """The 'all' track has services=None, meaning no force-disable."""
    result = track_force_disabled_sources(
        track_key="all",
        services_info=[_Svc("comfyui"), _Svc("n8n")],
        already_set={},
    )
    assert result == {}


def test_out_of_track_service_is_disabled():
    """gen-ai-rag excludes comfyui, so it must be force-disabled."""
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("comfyui")],
        already_set={},
    )
    assert result == {"comfyui_source": "disabled"}


def test_in_track_service_is_left_alone():
    """weaviate is in gen-ai-rag, so it must not be synthesized."""
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("weaviate")],
        already_set={},
    )
    assert "weaviate_source" not in result


def test_explicit_override_is_never_clobbered():
    """If the user visited the step, their choice wins."""
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("comfyui")],
        already_set={"comfyui_source": "container-gpu"},
    )
    assert result == {}


def test_hyphenated_service_key_becomes_underscored_cli_key():
    """CLI keys use underscores; service keys may use hyphens.

    label-studio is verified out-of-track for gen-ai-rag and not
    always-on, so it force-disables and the key must be rewritten.
    """
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("label-studio")],
        already_set={},
    )
    assert result == {"label_studio_source": "disabled"}


def test_registry_failure_never_blocks_the_wizard(monkeypatch):
    """A broken track registry must degrade to 'synthesize nothing',
    never raise. This bare-except is deliberate."""
    import wizard.model.track_rules as mod

    def _boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(mod, "load_tracks", _boom, raising=False)
    result = track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("comfyui")],
        already_set={},
    )
    assert result == {}


def test_rule_does_not_mutate_its_input():
    """Returning additions rather than mutating is what makes this
    testable; a regression to in-place mutation must fail here."""
    already = {"n8n_source": "container"}
    track_force_disabled_sources(
        track_key="gen-ai-rag",
        services_info=[_Svc("comfyui")],
        already_set=already,
    )
    assert already == {"n8n_source": "container"}
