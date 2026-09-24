"""Unit tests for the per-option ``SecondaryNumberInput`` widget contract.

Eligibility is now per-option: a ``PromptOption`` either carries a
``SecondaryNumberInput`` config (eligible — renders an inline textbox)
or doesn't (no textbox slot rendered on that row). Different options
on the same step can carry configs writing to DIFFERENT env vars; the
widget mirrors keystrokes only between siblings sharing an env_var.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from ui.textual.widgets.prompt_panel import (
    PromptOption,
    PromptPanel,
    PromptStep,
    SecondaryNumberInput,
    secondary_entry_error,
)


@dataclass
class _InputStub:
    """Stand-in for textual.widgets.Input — exposes value + env_var."""
    value: str = ""
    associated_env_var: str = ""


class _PanelStub:
    """Stand-in for PromptPanel exposing only what secondary_values and
    _sync_secondary_inputs need.

    Methods are bound LAZILY (looked up on PromptPanel inside each
    forwarder) so this file is collectable by pytest even before T3
    introduces ``PromptPanel.secondary_values``. Without lazy binding,
    the class body would AttributeError at import time, blocking the 3
    dataclass-shape tests that ought to pass after T1.
    """

    def __init__(self, step: PromptStep, *, selected_index: int = 0):
        self._step = step
        self._selected_index = selected_index
        self._secondary_inputs: list[_InputStub] = []

    def secondary_values(self):
        return PromptPanel.secondary_values(self)

    def _sync_secondary_inputs(self, source):
        return PromptPanel._sync_secondary_inputs(self, source)

    @property
    def selected_option(self):
        return PromptPanel.selected_option.fget(self)


def _opt(value: str, *, secondary: SecondaryNumberInput | None = None) -> PromptOption:
    return PromptOption(value=value, label=value, secondary_number=secondary)


def _step(options: list[PromptOption]) -> PromptStep:
    return PromptStep(
        title="test step",
        step_index=1,
        step_total=1,
        heading="",
        options=options,
        kind="options",
    )


# ─── secondary_values: returns ALL eligible rows' values ──────────────

def test_secondary_values_empty_when_no_options_have_config():
    panel = _PanelStub(_step([_opt("a"), _opt("b")]))
    assert panel.secondary_values() == []


def test_secondary_values_returns_one_tuple_for_selected_option():
    """The CURRENTLY-SELECTED option drives whether a tuple is emitted.
    Two options both carrying the same env_var: only the selected
    option's tuple is returned. Sibling sync keeps their values in
    lockstep (T3) so reading from either would be equivalent, but
    spec §7.5 says only the selected one persists."""
    cfg = SecondaryNumberInput(env_var="X", default_value=5, number_min=0, number_max=100)
    panel = _PanelStub(
        _step([_opt("a", secondary=cfg), _opt("b", secondary=cfg), _opt("c")]),
        selected_index=0,  # "a"
    )
    panel._secondary_inputs = [
        _InputStub(value="5", associated_env_var="X"),
        _InputStub(value="5", associated_env_var="X"),
    ]
    assert panel.secondary_values() == [("X", "5")]


def test_secondary_values_distinct_env_vars_only_selected_persists():
    """STT case: parakeet-localhost writes PARAKEET_LOCALHOST_PORT,
    whisper-cpp-localhost writes WHISPER_CPP_LOCALHOST_PORT. Both
    visible inputs RENDER, but only the SELECTED engine's tuple is
    emitted to selections — per spec §7.5."""
    cfg_a = SecondaryNumberInput(env_var="PARAKEET", default_value=63022,
                                 number_min=1024, number_max=65535)
    cfg_b = SecondaryNumberInput(env_var="WHISPER", default_value=63025,
                                 number_min=1024, number_max=65535)
    panel = _PanelStub(
        _step([_opt("p", secondary=cfg_a), _opt("w", secondary=cfg_b)]),
        selected_index=1,  # whisper
    )
    panel._secondary_inputs = [
        _InputStub(value="63022", associated_env_var="PARAKEET"),
        _InputStub(value="63025", associated_env_var="WHISPER"),
    ]
    # Only the selected (whisper) tuple should be returned.
    assert panel.secondary_values() == [("WHISPER", "63025")]


def test_secondary_values_refuses_an_out_of_range_value(  # was: clamped to number_max
):
    """#1181 replaced clamping with refusal. A value above number_max used
    to snap to number_max, silently writing a port the user never chose;
    now nothing is written and the caller keeps the user on the step."""
    cfg = SecondaryNumberInput(env_var="A", default_value=8000,
                               number_min=1024, number_max=65535)
    panel = _PanelStub(_step([_opt("a", secondary=cfg)]))
    panel._secondary_inputs = [_InputStub(value="99999", associated_env_var="A")]
    assert panel.secondary_values() == []
    assert secondary_entry_error("99999", cfg) == "99999 — choose 1024–65535"


def test_secondary_values_refuses_garbage_rather_than_using_the_default(
):
    """#1181: non-numeric input used to become default_value."""
    cfg = SecondaryNumberInput(env_var="X", default_value=8000, number_min=1024, number_max=65535)
    panel = _PanelStub(_step([_opt("a", secondary=cfg)]))
    panel._secondary_inputs = [_InputStub(value="not-a-number", associated_env_var="X")]
    assert panel.secondary_values() == []
    assert secondary_entry_error("not-a-number", cfg) == (
        "'not-a-number' is not a number — choose 1024–65535"
    )


def test_secondary_values_empty_input_uses_default():
    cfg = SecondaryNumberInput(env_var="X", default_value=8000, number_min=1024, number_max=65535)
    panel = _PanelStub(_step([_opt("a", secondary=cfg)]))
    panel._secondary_inputs = [_InputStub(value="", associated_env_var="X")]
    assert panel.secondary_values() == [("X", "8000")]


def test_secondary_values_returns_empty_when_selection_moves_to_ineligible():
    """Per spec §7.5: the user can type a value into an eligible row's
    textbox, then navigate the cursor to a row WITHOUT a secondary
    config. On confirm, secondary_values returns [] — the typed value
    is NOT persisted to .env because the user's final selection wasn't
    an eligible row.

    Mirrors the localhost-port use case: user types 9000 in
    `comfyui-localhost`'s textbox, then picks `comfyui-container`,
    confirms. COMFYUI_LOCALHOST_PORT must not be written.
    """
    cfg = SecondaryNumberInput(env_var="COMFYUI_LOCALHOST_PORT",
                               default_value=8000, number_min=1024, number_max=65535)
    panel = _PanelStub(
        _step([_opt("comfyui-container"), _opt("localhost", secondary=cfg)]),
        selected_index=0,  # container — the NON-eligible row
    )
    # The localhost-row's input exists and has a value the user typed.
    panel._secondary_inputs = [_InputStub(value="9000", associated_env_var="COMFYUI_LOCALHOST_PORT")]
    # But because the user's final selection is the container row
    # (not eligible), nothing is captured.
    assert panel.secondary_values() == []


# ─── _sync_secondary_inputs: keyed by env_var ─────────────────────────

def test_sync_mirrors_value_across_siblings_sharing_env_var():
    panel = _PanelStub(_step([]))
    a = _InputStub(value="2", associated_env_var="X")
    b = _InputStub(value="2", associated_env_var="X")
    c = _InputStub(value="9000", associated_env_var="Y")
    panel._secondary_inputs = [a, b, c]
    a.value = "7"
    panel._sync_secondary_inputs(a)
    assert b.value == "7", "Same-env_var sibling not synced"
    assert c.value == "9000", "Different-env_var sibling should be independent"


def test_sync_is_idempotent_when_values_already_match():
    panel = _PanelStub(_step([]))
    a = _InputStub(value="5", associated_env_var="X")
    b = _InputStub(value="5", associated_env_var="X")
    panel._secondary_inputs = [a, b]
    panel._sync_secondary_inputs(a)
    assert a.value == "5" and b.value == "5"


# ─── dataclass shape ──────────────────────────────────────────────────

def test_secondary_number_input_dropped_show_when_field():
    fields = {f for f in SecondaryNumberInput.__dataclass_fields__}
    assert "show_when" not in fields, (
        f"SecondaryNumberInput should no longer have show_when. Fields: {fields}"
    )


def test_prompt_option_has_secondary_number_field():
    fields = PromptOption.__dataclass_fields__
    assert "secondary_number" in fields, (
        f"PromptOption should carry secondary_number. Fields: {set(fields)}"
    )
    assert fields["secondary_number"].default is None


def test_prompt_step_no_longer_has_secondary_number_field():
    fields = {f for f in PromptStep.__dataclass_fields__}
    assert "secondary_number" not in fields, (
        f"PromptStep should no longer carry secondary_number. Fields: {fields}"
    )
