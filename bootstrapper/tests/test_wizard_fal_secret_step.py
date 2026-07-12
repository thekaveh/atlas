"""Tests for the FAL Cloud Media wizard API-token (secret) step (#517).

FAL is prompted with a masked API-token step (enter a key to enable, blank to
keep disabled) instead of a plain enabled/disabled source tile, placed right
after ComfyUI in the media category. These cover the step shape and the
key→source derivation in _selections_to_args.
"""
from __future__ import annotations

from types import SimpleNamespace

from ui.textual.integration import _selections_to_args, PICKER_STEP_TITLE
from ui.textual.widgets.prompt_panel import SECRET_KEEP, SECRET_CLEAR
from wizard.llm_steps import build_fal_secret_step, fal_secret_title


def _noop(_msg: str) -> None:
    pass


# ─────────────────────────── step shape ───────────────────────────
def test_build_fal_secret_step_shape():
    steps = build_fal_secret_step({}, _noop)
    assert len(steps) == 1
    step = steps[0]
    assert step.title == fal_secret_title() == "FAL Cloud Media  ·  API key"
    assert step.kind == "secret"
    # service_name MUST be empty — else the grid-row source handler would write
    # the raw API key into the FAL row's source (a secret leak into the UI).
    assert step.service_name == ""
    assert step.options == []


def test_build_fal_secret_step_prefills_existing_key():
    steps = build_fal_secret_step(
        {"FAL_API_KEY": "fal-key-abc", "FAL_SOURCE": "enabled"}, _noop
    )
    step = steps[0]
    assert step.default_value == "fal-key-abc"
    assert step.secret_keep_hint is not None  # keep/replace/clear affordance


def test_build_fal_secret_step_no_key_no_hint():
    step = build_fal_secret_step({}, _noop)[0]
    assert step.default_value == ""
    assert step.secret_keep_hint is None


# ─────────────────────────── apply logic ───────────────────────────
def _fal_svc():
    # FAL stays discovered (kept in services_info) so its grid row + off-track
    # force-disable survive; only its source *step* is replaced.
    return SimpleNamespace(
        key="fal", display_name="FAL Cloud Media",
        options=["enabled", "disabled"], current_value="disabled",
    )


def test_key_enables_and_persists():
    source_args, opts = _selections_to_args(
        {fal_secret_title(): "fal-key-123"},
        [_fal_svc()], current_base_port=63000, env_vars={},
    )
    assert source_args["fal_source"] == "enabled"
    assert opts["cloud_api_keys"]["FAL_API_KEY"] == "fal-key-123"


def test_blank_disables_and_wipes():
    source_args, opts = _selections_to_args(
        {fal_secret_title(): ""},
        [_fal_svc()], current_base_port=63000, env_vars={"FAL_SOURCE": "enabled", "FAL_API_KEY": "old"},
    )
    assert source_args["fal_source"] == "disabled"
    assert opts["cloud_api_keys"]["FAL_API_KEY"] == ""


def test_clear_disables_and_wipes():
    source_args, opts = _selections_to_args(
        {fal_secret_title(): SECRET_CLEAR},
        [_fal_svc()], current_base_port=63000, env_vars={"FAL_SOURCE": "enabled", "FAL_API_KEY": "old"},
    )
    assert source_args["fal_source"] == "disabled"
    assert opts["cloud_api_keys"]["FAL_API_KEY"] == ""


def test_keep_autopromotes_when_key_saved_but_disabled():
    source_args, _ = _selections_to_args(
        {fal_secret_title(): SECRET_KEEP},
        [_fal_svc()], current_base_port=63000,
        env_vars={"FAL_SOURCE": "disabled", "FAL_API_KEY": "saved-key"},
    )
    assert source_args["fal_source"] == "enabled"


def test_keep_when_already_enabled_leaves_source_alone():
    source_args, _ = _selections_to_args(
        {fal_secret_title(): SECRET_KEEP},
        [_fal_svc()], current_base_port=63000,
        env_vars={"FAL_SOURCE": "enabled", "FAL_API_KEY": "saved-key"},
    )
    # KEEP on an already-enabled provider must not flip it to disabled.
    assert source_args.get("fal_source") != "disabled"


def test_none_selection_leaves_source_untouched():
    # No picker + no fal selection → the fal apply is a no-op (off-track/never
    # visited leaves .env as-is; the generic source loop skips it too).
    source_args, _ = _selections_to_args(
        {}, [_fal_svc()], current_base_port=63000, env_vars={},
    )
    assert "fal_source" not in source_args


def test_no_enabled_with_blank_key_invariant():
    """The footgun this ticket fixes: never FAL_SOURCE=enabled with a blank key."""
    for blank in ("", SECRET_CLEAR):
        source_args, opts = _selections_to_args(
            {fal_secret_title(): blank},
            [_fal_svc()], current_base_port=63000, env_vars={},
        )
        assert source_args["fal_source"] == "disabled"
        assert opts["cloud_api_keys"].get("FAL_API_KEY", "") == ""


def test_off_track_fal_force_disabled_without_secret():
    # gen-ai-rag excludes fal → force-disabled via the track pass, even though
    # its secret step was skipped (no selection).
    source_args, _ = _selections_to_args(
        {PICKER_STEP_TITLE: "gen-ai-rag"},
        [_fal_svc()], current_base_port=63000, env_vars={},
    )
    assert source_args.get("fal_source") == "disabled"
