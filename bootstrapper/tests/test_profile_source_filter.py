"""
Tests for Task 4, Plan B: deployment-profile wizard step + prod localhost-source filtering.

Covers:
  - SourceValidator.validate_sources_for_profile: reject dev-only source under prod,
    allow dev-only source under default.
  - Wizard step builder: prod profile produces no localhost options for comfyui / ollama.
  - Profile step auto-skip: when profile is pre-set, the PROFILE_STEP_TITLE step has a
    skip_if_prev predicate that returns True.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


# ─────────────────────────────────────────────────────────────────────────────
# SourceValidator.validate_sources_for_profile
# ─────────────────────────────────────────────────────────────────────────────


def test_validator_rejects_dev_only_source_under_prod():
    """ollama-localhost is dev-only; must be rejected under --profile prod.

    The LLM Engine source var in the manifests is LLM_PROVIDER_SOURCE (the
    ollama manifest uses this var). OLLAMA_SOURCE is not a real SOURCE var.
    """
    from services.source_validator import SourceValidator
    v = SourceValidator()
    v.load_yaml_config()
    v.validation_errors = []
    ok = v.validate_sources_for_profile({"LLM_PROVIDER_SOURCE": "ollama-localhost"}, "prod")
    assert ok is False
    assert any("LLM_PROVIDER_SOURCE" in e for e in v.validation_errors), (
        f"Expected LLM_PROVIDER_SOURCE in errors, got: {v.validation_errors}"
    )


def test_validator_allows_dev_only_source_under_default():
    """ollama-localhost is dev-only but must be allowed under --profile default."""
    from services.source_validator import SourceValidator
    v = SourceValidator()
    v.load_yaml_config()
    v.validation_errors = []
    ok = v.validate_sources_for_profile({"LLM_PROVIDER_SOURCE": "ollama-localhost"}, "default")
    assert ok is True


def test_validator_allows_container_source_under_prod():
    """Container sources are unannotated (all profiles); must pass under prod."""
    from services.source_validator import SourceValidator
    v = SourceValidator()
    v.load_yaml_config()
    v.validation_errors = []
    ok = v.validate_sources_for_profile({"LLM_PROVIDER_SOURCE": "ollama-container-cpu"}, "prod")
    assert ok is True


def test_validator_rejects_comfyui_localhost_under_prod():
    """comfyui localhost is dev-only; must be rejected under prod."""
    from services.source_validator import SourceValidator
    v = SourceValidator()
    v.load_yaml_config()
    v.validation_errors = []
    ok = v.validate_sources_for_profile({"COMFYUI_SOURCE": "localhost"}, "prod")
    assert ok is False
    assert any("COMFYUI_SOURCE" in e for e in v.validation_errors)


def test_validator_skips_empty_values():
    """Empty source values must be skipped (not treated as invalid)."""
    from services.source_validator import SourceValidator
    v = SourceValidator()
    v.load_yaml_config()
    v.validation_errors = []
    ok = v.validate_sources_for_profile({"LLM_PROVIDER_SOURCE": ""}, "prod")
    assert ok is True


# ─────────────────────────────────────────────────────────────────────────────
# Wizard step builder: prod profile hides localhost options
# ─────────────────────────────────────────────────────────────────────────────


def _build_steps(profile=None):
    """Helper: call _build_steps_and_rows with the given profile."""
    from ui.textual.integration import _build_steps_and_rows
    from core.config_parser import ConfigParser
    from utils.hosts_manager import HostsManager
    cp = ConfigParser()
    steps, _, _, _, _, _ = _build_steps_and_rows(
        cp, HostsManager(),
        track_key=None,
        overridden_services=frozenset(),
        profile=profile,
    )
    return steps


def test_prod_profile_hides_ollama_localhost():
    """Under profile=prod the LLM Engine step must contain no 'localhost' options."""
    steps = _build_steps(profile="prod")
    llm_steps = [
        s for s in steps
        if "LLM Engine" in (getattr(s, "title", "") or "")
           and "source" in (getattr(s, "title", "") or "").lower()
    ]
    assert llm_steps, "Expected at least one LLM Engine source step"
    for s in llm_steps:
        opt_values = [o.value for o in (getattr(s, "options", None) or [])]
        localhost_opts = [v for v in opt_values if "localhost" in v]
        assert not localhost_opts, (
            f"prod profile must hide localhost options for LLM Engine, "
            f"but found: {localhost_opts}"
        )


def test_prod_profile_hides_comfyui_localhost():
    """Under profile=prod the ComfyUI step must contain no 'localhost' options."""
    steps = _build_steps(profile="prod")
    comfyui_steps = [
        s for s in steps
        if "ComfyUI" in (getattr(s, "title", "") or "")
           and "source" in (getattr(s, "title", "") or "").lower()
    ]
    assert comfyui_steps, "Expected at least one ComfyUI source step"
    for s in comfyui_steps:
        opt_values = [o.value for o in (getattr(s, "options", None) or [])]
        localhost_opts = [v for v in opt_values if "localhost" in v]
        assert not localhost_opts, (
            f"prod profile must hide localhost options for ComfyUI, "
            f"but found: {localhost_opts}"
        )


def test_in_wizard_prod_selection_hides_later_localhost_options():
    """profile=None starts interactive; later source rows follow selection."""
    from ui.textual.integration import PROFILE_STEP_TITLE

    steps = _build_steps(profile=None)
    llm_step = next(
        s for s in steps
        if "LLM Engine" in (getattr(s, "title", "") or "")
           and "source" in (getattr(s, "title", "") or "").lower()
    )
    provider = getattr(llm_step, "options_provider", None)
    assert provider is not None

    default_values = [o.value for o in provider({PROFILE_STEP_TITLE: "default"})]
    prod_values = [o.value for o in provider({PROFILE_STEP_TITLE: "prod"})]

    assert any("localhost" in v for v in default_values)
    assert not any("localhost" in v for v in prod_values)


def test_default_profile_shows_ollama_localhost():
    """Under profile=default, localhost options must still appear."""
    steps = _build_steps(profile="default")
    llm_steps = [
        s for s in steps
        if "LLM Engine" in (getattr(s, "title", "") or "")
           and "source" in (getattr(s, "title", "") or "").lower()
    ]
    assert llm_steps, "Expected at least one LLM Engine source step"
    for s in llm_steps:
        opt_values = [o.value for o in (getattr(s, "options", None) or [])]
        has_localhost = any("localhost" in v for v in opt_values)
        assert has_localhost, (
            f"default profile must show localhost options for LLM Engine, "
            f"but got: {opt_values}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Profile picker step presence and auto-skip behaviour
# ─────────────────────────────────────────────────────────────────────────────


def test_profile_step_exists_in_wizard():
    """The PROFILE_STEP_TITLE step is always present in the wizard steps list."""
    from ui.textual.integration import PROFILE_STEP_TITLE
    steps = _build_steps(profile=None)
    titles = [getattr(s, "title", "") for s in steps]
    assert PROFILE_STEP_TITLE in titles, (
        f"PROFILE_STEP_TITLE not found in steps: {titles}"
    )


def test_profile_step_autoskips_when_profile_preset():
    """When profile is pre-set via CLI, the step's skip_if_prev returns True."""
    from ui.textual.integration import PROFILE_STEP_TITLE
    steps = _build_steps(profile="prod")
    profile_steps = [
        s for s in steps if getattr(s, "title", "") == PROFILE_STEP_TITLE
    ]
    assert profile_steps, "Profile step not found"
    s = profile_steps[0]
    skip_fn = getattr(s, "skip_if_prev", None)
    assert skip_fn is not None, "Profile step should have skip_if_prev when profile is pre-set"
    assert skip_fn({}) is True, "skip_if_prev must return True when profile was pre-set"


def test_profile_step_no_autoskip_when_no_profile():
    """When no profile is pre-set, the step's skip_if_prev should be None or return False."""
    from ui.textual.integration import PROFILE_STEP_TITLE
    steps = _build_steps(profile=None)
    profile_steps = [
        s for s in steps if getattr(s, "title", "") == PROFILE_STEP_TITLE
    ]
    assert profile_steps, "Profile step not found"
    s = profile_steps[0]
    skip_fn = getattr(s, "skip_if_prev", None)
    if skip_fn is not None:
        assert skip_fn({}) is False, (
            "skip_if_prev must return False when no profile was pre-set"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Profile step default_value
# ─────────────────────────────────────────────────────────────────────────────


def test_profile_step_default_value_follows_cli_profile():
    """When profile='prod' is passed, the step's default_value must be 'prod'."""
    from ui.textual.integration import PROFILE_STEP_TITLE
    steps = _build_steps(profile="prod")
    profile_steps = [
        s for s in steps if getattr(s, "title", "") == PROFILE_STEP_TITLE
    ]
    assert profile_steps
    assert profile_steps[0].default_value == "prod"


def test_profile_step_default_value_is_default_when_unset():
    """When no profile is passed, the step's default_value must be 'default'."""
    from ui.textual.integration import PROFILE_STEP_TITLE
    steps = _build_steps(profile=None)
    profile_steps = [
        s for s in steps if getattr(s, "title", "") == PROFILE_STEP_TITLE
    ]
    assert profile_steps
    assert profile_steps[0].default_value == "default"


def test_cli_pinned_track_and_profile_survive_launch_pruning():
    """--track/--profile skip their steps because the answer is pinned; the
    launch-time prune must not then drop that pinned answer (the launch
    would otherwise resolve to no track and the dev profile)."""
    from ui.textual.integration import (
        PICKER_STEP_TITLE,
        PROFILE_STEP_TITLE,
        _build_steps_and_rows,
    )
    from ui.textual.screens.wizard_screen import prune_skip_hidden_selections
    from core.config_parser import ConfigParser
    from utils.hosts_manager import HostsManager

    steps, *_ = _build_steps_and_rows(
        ConfigParser(), HostsManager(),
        track_key="gen-ai-rag",
        overridden_services=frozenset(),
        profile="prod",
    )
    pinned = {PICKER_STEP_TITLE: "gen-ai-rag", PROFILE_STEP_TITLE: "prod"}

    pruned = prune_skip_hidden_selections(steps, dict(pinned), pinned=pinned)

    assert pruned[PICKER_STEP_TITLE] == "gen-ai-rag"
    assert pruned[PROFILE_STEP_TITLE] == "prod"
