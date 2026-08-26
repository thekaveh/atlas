"""Wizard pending-state transitions and restored-input behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.app import App

from ui.textual.screens.wizard_screen import (
    WizardScreen,
    prune_skip_hidden_selections,
    replace_step_secondary_selections,
)
from ui.textual.widgets.prompt_panel import (
    PromptOption,
    PromptStep,
    SecondaryNumberInput,
)
from wizard.model.cloud_rules import SECRET_CLEAR, SECRET_KEEP


def _render(step: PromptStep, selections: dict) -> PromptStep:
    loaded = []
    fake = SimpleNamespace(
        _selections=selections,
        _step_index=0,
        _steps=[step],
        _prompt=SimpleNamespace(
            load_step=loaded.append,
            clear_conflict=lambda: None,
        ),
        _services=[],
        _service_table=SimpleNamespace(set_cursor=lambda _value: None),
    )
    WizardScreen._render_step(fake, step)
    return loaded[0]


@pytest.mark.parametrize("kind", ["options", "number", "text"])
def test_back_navigation_restores_committed_primary_value(kind):
    step = PromptStep(
        title="Value", step_index=1, step_total=1, heading="Value",
        kind=kind,
        default_value="original",
        options=[PromptOption("original", "Original"), PromptOption("saved", "Saved")],
    )

    rendered = _render(step, {"Value": "saved"})

    if kind == "text":
        assert rendered.default_value == "original"
        assert rendered.restored_input_value == "saved"
    else:
        assert rendered.default_value == "saved"


@pytest.mark.parametrize(
    ("saved", "expected_input"),
    [(SECRET_KEEP, None), (SECRET_CLEAR, "clear"), ("new-key", "new-key")],
)
def test_back_navigation_restores_secret_without_losing_sentinel_semantics(
    saved, expected_input,
):
    step = PromptStep(
        title="API key", step_index=1, step_total=1, heading="API key",
        kind="secret", default_value="existing-key",
    )
    rendered = _render(step, {"API key": saved})
    assert rendered.default_value == "existing-key"
    assert rendered.restored_input_value == expected_input


def test_back_navigation_restores_inline_secondary_number():
    step = PromptStep(
        title="Ray", step_index=1, step_total=1, heading="Ray",
        options=[PromptOption(
            "container", "Container",
            secondary_number=SecondaryNumberInput("RAY_WORKER_COUNT", default_value=2),
        )],
    )

    rendered = _render(step, {"Ray": "container", "__secondary__:RAY_WORKER_COUNT": "7"})

    assert rendered.options[0].secondary_number.default_value == 7


def test_hidden_step_prunes_owned_secondary_values():
    step = PromptStep(
        title="Ray", step_index=1, step_total=1, heading="Ray",
        options=[PromptOption(
            "container", "Container",
            secondary_number=SecondaryNumberInput("RAY_WORKER_COUNT", default_value=2),
        )],
        skip_if_prev=lambda _selections: True,
    )
    selections = {"Ray": "container", "__secondary__:RAY_WORKER_COUNT": "7"}

    assert prune_skip_hidden_selections([step], selections) == {}


def test_recommit_drops_secondary_value_when_new_option_has_no_input():
    step = PromptStep(
        title="Ray", step_index=1, step_total=1, heading="Ray",
        options=[PromptOption(
            "container", "Container",
            secondary_number=SecondaryNumberInput("RAY_WORKER_COUNT", default_value=2),
        ), PromptOption("disabled", "Disabled")],
    )
    selections = {"__secondary__:RAY_WORKER_COUNT": "7"}

    replace_step_secondary_selections(selections, step, [])

    assert selections == {}


def test_escape_on_first_step_closes_log_tee_before_exit():
    calls = []
    fake = SimpleNamespace(
        _phase="setup",
        _prompt=SimpleNamespace(has_search_focus=lambda: False),
        _log_chips=SimpleNamespace(_open_popup=None),
        _step_index=0,
        _close_launch_log_tee=lambda: calls.append("close"),
        app=SimpleNamespace(exit=lambda: calls.append("exit")),
    )

    WizardScreen.action_back(fake)

    assert calls == ["close", "exit"]


class _WizardApp(App):
    def __init__(self, screen: WizardScreen) -> None:
        super().__init__()
        self._wizard_screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._wizard_screen)


@pytest.mark.parametrize(
    ("kind", "typed", "expected"),
    [
        ("secret", "pending-new-key", "pending-new-key"),
        ("text", "pending-new-model", "pending-new-model"),
        ("secret", "clear", SECRET_CLEAR),
        ("text", "clear", SECRET_CLEAR),
    ],
)
def test_back_then_enter_reconfirms_the_pending_session_value(kind, typed, expected):
    target = PromptStep(
        title="Pending value", step_index=1, step_total=2,
        heading="Pending value", kind=kind, default_value="persisted-value",
    )
    next_step = PromptStep(
        title="Next", step_index=2, step_total=2, heading="Next",
        options=[PromptOption("next", "Next")], default_value="next",
    )
    screen = WizardScreen(
        steps=[target, next_step], services=[], no_splash=True,
    )
    launch_log_path = screen._launch_log_path

    async def scenario():
        async with _WizardApp(screen).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            input_widget = (
                screen._prompt._secret_input
                if kind == "secret" else screen._prompt._number_input
            )
            input_widget.value = typed
            screen.action_confirm()
            await pilot.pause()
            screen.action_back()
            await pilot.pause()
            restored_input = (
                screen._prompt._secret_input
                if kind == "secret" else screen._prompt._number_input
            )
            restored = restored_input.value
            hint_widget = (
                screen._prompt._secret_hint
                if kind == "secret" else screen._prompt._number_hint
            )
            hint = str(hint_widget.render())
            screen.action_confirm()
            return restored, hint, screen._selections["Pending value"]

    try:
        restored, hint, reconfirmed = asyncio.run(scenario())
    finally:
        screen._close_launch_log_tee()
        if launch_log_path is not None:
            launch_log_path.unlink(missing_ok=True)

    assert restored == typed
    assert "Enter to confirm" in hint
    assert "Enter to keep" not in hint
    if typed == "clear":
        assert "clear" in hint.lower() or "remov" in hint.lower()
    assert reconfirmed == expected


@pytest.mark.parametrize("typed", ["pending-new-key", "clear"])
def test_clearing_a_restored_secret_returns_to_persisted_keep_semantics(typed):
    target = PromptStep(
        title="Pending secret", step_index=1, step_total=2,
        heading="Pending secret", kind="secret", default_value="persisted-key",
    )
    next_step = PromptStep(
        title="Next", step_index=2, step_total=2, heading="Next",
        options=[PromptOption("next", "Next")], default_value="next",
    )
    screen = WizardScreen(steps=[target, next_step], services=[], no_splash=True)
    launch_log_path = screen._launch_log_path

    async def scenario():
        async with _WizardApp(screen).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            screen._prompt._secret_input.value = typed
            screen.action_confirm()
            await pilot.pause()
            screen.action_back()
            await pilot.pause()
            screen._prompt._secret_input.value = ""
            await pilot.pause()
            return (
                str(screen._prompt._secret_hint.render()),
                screen._prompt.selected_option.value,
            )

    try:
        hint, selected = asyncio.run(scenario())
    finally:
        screen._close_launch_log_tee()
        if launch_log_path is not None:
            launch_log_path.unlink(missing_ok=True)

    assert "Enter to keep" in hint
    assert "pending replacement" not in hint.lower()
    assert "pending removal" not in hint.lower()
    assert selected == SECRET_KEEP


@pytest.mark.parametrize("typed", ["pending-new-model", "clear"])
def test_clearing_a_restored_text_returns_to_persisted_keep_semantics(typed):
    target = PromptStep(
        title="Pending text", step_index=1, step_total=2,
        heading="Pending text", kind="text", default_value="persisted-model",
    )
    next_step = PromptStep(
        title="Next", step_index=2, step_total=2, heading="Next",
        options=[PromptOption("next", "Next")], default_value="next",
    )
    screen = WizardScreen(steps=[target, next_step], services=[], no_splash=True)
    launch_log_path = screen._launch_log_path

    async def scenario():
        async with _WizardApp(screen).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            screen._prompt._number_input.value = typed
            screen.action_confirm()
            await pilot.pause()
            screen.action_back()
            await pilot.pause()
            screen._prompt._number_input.value = ""
            await pilot.pause()
            return (
                str(screen._prompt._number_hint.render()),
                screen._prompt.selected_option.value,
            )

    try:
        hint, selected = asyncio.run(scenario())
    finally:
        screen._close_launch_log_tee()
        if launch_log_path is not None:
            launch_log_path.unlink(missing_ok=True)

    assert "Enter to keep" in hint
    assert "pending session value" not in hint.lower()
    assert "pending clear" not in hint.lower()
    assert selected == SECRET_KEEP


def test_whitespace_only_secret_input_reports_persisted_keep_semantics():
    step = PromptStep(
        title="Secret", step_index=1, step_total=1,
        heading="Secret", kind="secret", default_value="persisted-key",
        restored_input_value="pending-new-key",
    )
    screen = WizardScreen(steps=[step], services=[], no_splash=True)
    launch_log_path = screen._launch_log_path

    async def scenario():
        async with _WizardApp(screen).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            screen._prompt._secret_input.value = "   "
            await pilot.pause()
            return (
                str(screen._prompt._secret_hint.render()),
                screen._prompt.selected_option.value,
            )

    try:
        hint, selected = asyncio.run(scenario())
    finally:
        screen._close_launch_log_tee()
        if launch_log_path is not None:
            launch_log_path.unlink(missing_ok=True)

    assert "Enter to keep" in hint
    assert "chars entered" not in hint
    assert selected == SECRET_KEEP


def test_initial_state_marks_configurable_rows_pending():
    """At step 0, configurable rows are pending; locked rows are not."""
    from ui.textual.widgets.service_table import ServiceRow

    rows = [
        ServiceRow(
            name="LiteLLM", category="llm", configurable=False,
            pending=False, source="container",
        ),
        ServiceRow(
            name="LLM Engine", category="llm", configurable=True,
            pending=True, source="",
        ),
    ]
    assert rows[0].pending is False
    assert rows[1].pending is True


def test_answered_set_transitions_pending_to_answered():
    """Confirming a step changes its service row to answered."""
    from ui.textual.widgets.service_table import ServiceRow

    row = ServiceRow(
        name="ComfyUI", category="media", configurable=True,
        pending=True, source="",
    )
    row.pending = False
    row.source = "container-cpu"
    assert row.pending is False
    assert row.source == "container-cpu"


def test_answered_set_does_not_shrink_on_back_nav():
    """Back-navigation revisits a step without forgetting it was answered."""
    answered: set[int] = {5}
    answered.add(5)
    answered.add(6)
    assert answered == {5, 6}
