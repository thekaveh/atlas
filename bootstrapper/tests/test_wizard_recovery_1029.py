"""Recovery advice must not turn incomplete diagnosis into data destruction."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from textual.app import App

from ui.textual.screens import wizard_screen
from ui.textual.screens.wizard_screen import WizardScreen
from ui.textual.widgets.prompt_panel import PromptOption, PromptStep


def _hints(*lines):
    messages = []
    screen = SimpleNamespace(_write_status=lambda text, **kwargs: messages.append(text))
    WizardScreen._emit_failure_hints(screen, list(lines))
    return "\n".join(messages)


@pytest.mark.parametrize("line", [
    "litellm-init | Permission denied: /litellm-config/config.yaml.tmp",
    "kong-init | Permission denied: /kong-config/kong.yml",
    "worker | [Errno 13] Permission denied: '/some/private/location'",
    "worker | Permission denied",
])
def test_permission_advice_requires_ownership_diagnosis(line):
    text = _hints(line)
    assert "UID/GID" in text
    assert "incomplete" in text.lower()
    assert "chmod" not in text and "chown" not in text
    assert "--cold" not in text
    assert "root-owned" not in text
    assert "retry" in text.lower()


@pytest.mark.parametrize("path", ["/litellm-config/", "/kong-config/"])
def test_known_permission_mount_is_named_without_guessing_host_ownership(path):
    assert path in _hints(f"Permission denied: {path}config.yaml.tmp")


@pytest.mark.parametrize("line", [
    'supabase-db | password authentication failed for user "supabase_admin"',
    "redis | authentication failed",
])
def test_authentication_advice_distinguishes_causes_and_preserves_data(line):
    text = _hints(line).lower()
    for word in ("available", "configuration", "stored credentials", "backup", "retry"):
        assert word in text
    assert "stale supabase-db volume detected" not in text
    assert "--cold" not in text
    assert "does not prove" in text


@pytest.mark.parametrize("line", [
    "service | connection refused",
    "service | could not translate host name db to address",
    "service | connection timed out",
])
def test_unavailable_service_is_not_diagnosed_as_bad_credentials(line):
    text = _hints(line).lower()
    assert "unavailable" in text
    assert "health" in text
    assert "does not prove" in text
    assert "--cold" not in text


def test_multiple_failures_are_not_hidden_and_raw_secrets_are_not_echoed():
    text = _hints(
        'password authentication failed for user "supabase_admin"',
        "Permission denied: /litellm-config/canary-secret-1029.tmp",
        "connection refused postgres://user:canary-secret-1029@db",
    )
    assert "UID/GID" in text and "stored credentials" in text
    assert "unavailable" in text.lower()
    assert "canary-secret-1029" not in text


def test_unrelated_output_does_not_generate_recovery():
    assert _hints("worker | ready", "worker | processed 13 items") == ""


class _App(App):
    def __init__(self, screen):
        super().__init__()
        self.test_screen = screen

    def on_mount(self):
        self.push_screen(self.test_screen)


def _run_screen(scenario):
    screen = WizardScreen(steps=[PromptStep(
        title="Dummy", step_index=1, step_total=1, heading="H", subtitle="",
        options=[PromptOption("a", "A")], default_value="a",
    )], services=[], no_splash=True,
        prefilled_stack_options={"project_name": "recovery-fixture"})
    path = screen._launch_log_path

    async def run():
        async with _App(screen).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen._phase = "launch"
            return await scenario(screen, pilot)

    try:
        return asyncio.run(run())
    finally:
        screen._close_launch_log_tee()
        if path:
            path.unlink(missing_ok=True)


def test_cold_confirmation_enumerates_data_and_preserved_state():
    async def scenario(screen, pilot):
        messages = []
        screen.notify = lambda text, **kwargs: messages.append(text)
        screen._write_status = lambda text, **kwargs: messages.append(text)
        screen.action_stop_stack_cold()
        await pilot.pause()
        return "\n".join(messages)

    text = _run_screen(scenario)
    for word in ("recovery-fixture", "database", "object", "history", "model", "cache",
                 "anonymous", "bind", "external", ".env", "ctrl+x"):
        assert word in text.lower()
    assert "8 seconds" in text


def test_expired_cold_confirmation_does_not_start_teardown(monkeypatch):
    # Patch only the screen clock, not the asyncio event-loop clock.
    now = [100.0]
    monkeypatch.setattr(wizard_screen, "_teardown_clock", lambda: now[0], raising=False)

    async def scenario(screen, pilot):
        calls = []

        async def teardown(*, cold):
            calls.append(cold)

        screen._teardown_worker = teardown
        screen.action_stop_stack_cold()
        now[0] += 9
        screen.action_stop_stack_cold()
        await pilot.pause()
        assert calls == [], "an expired first press must not authorize deletion"
        screen.action_stop_stack_cold()
        await pilot.pause()
        assert calls == [True]

    _run_screen(scenario)


def test_actual_log_renderer_keeps_recovery_visible():
    async def scenario(screen, pilot):
        screen._emit_failure_hints(["worker | [Errno 13] Permission denied"])
        await pilot.pause()
        assert "UID/GID" in screen._log_pane.visible_text()
        # The same displayed status is persisted for diagnosis after detach.
        screen._launch_log_fh.flush()
        return screen._launch_log_path.read_text()

    text = _run_screen(scenario)
    assert "UID/GID" in text
    assert "retry" in text.lower()


def _cold_step(monkeypatch):
    from core.config_parser import ConfigParser
    from ui.textual.integration import _build_steps_and_rows

    monkeypatch.setattr(ConfigParser, "parse_env_file", lambda self: {})
    hosts = SimpleNamespace()
    steps, *_ = _build_steps_and_rows(ConfigParser(), hosts)
    return next(step for step in steps if step.title == "Cold start  ·  rebuild")


def test_cold_start_is_explicit_and_explains_data_and_configuration_loss(monkeypatch):
    cold = _cold_step(monkeypatch)
    assert cold.default_value == "no"
    assert {option.value for option in cold.options} == {"yes", "no"}
    text = cold.subtitle.lower()
    for word in ("project", "database", "object", "history", "model", "cache", ".env", "keys"):
        assert word in text


def test_cold_warning_and_choices_are_actually_rendered(monkeypatch):
    cold = _cold_step(monkeypatch)

    async def scenario(screen, pilot):
        screen._phase = "setup"
        screen._render_step(cold)
        await pilot.pause()
        rows = ["".join(segment.text for segment in strip)
                for strip in screen.app.screen._compositor.render_strips()]
        text = "\n".join(rows)
        for phrase in ("database records", "workflow/chat history", ".env", "Back up",
                       "external volumes", "No — keep existing data", "Yes — rebuild"):
            assert phrase in text, f"warning/choice clipped: {phrase}\n{text}"
        # Revisiting an ordinary prompt must restore its original one-line layout.
        screen._prompt.load_step(PromptStep("Other", 1, 1, "Other", subtitle="Short"))
        await pilot.pause()
        assert screen._prompt._subtitle.size.height == 1

    _run_screen(scenario)
