"""Textual launch failures must propagate to the CLI exit status."""

from ui.textual.screens.wizard_screen import WizardScreen


def test_launch_failure_notifies_the_flow_result_callback():
    exit_codes: list[int] = []
    screen = WizardScreen(
        steps=[],
        services=[],
        on_launch_result=exit_codes.append,
    )

    screen._mark_launch_failed()

    assert exit_codes == [1]


def test_launch_cannot_detach_before_startup_reaches_log_stream(monkeypatch):
    exit_codes: list[int] = []
    notifications: list[str] = []
    screen = WizardScreen(
        steps=[],
        services=[],
        on_launch_result=exit_codes.append,
    )
    screen._phase = "launch"
    monkeypatch.setattr(
        screen,
        "notify",
        lambda message, **_kwargs: notifications.append(message),
    )

    screen.action_quit_wizard()

    assert exit_codes == []
    assert notifications == ["Startup is still running; Ctrl+C cancels it."]
