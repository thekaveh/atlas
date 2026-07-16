"""Textual launch failures must propagate to the CLI exit status."""

from ui.textual.screens.wizard_screen import WizardScreen


def _close_test_screen(screen: WizardScreen) -> None:
    path = screen._launch_log_path
    screen._close_launch_log_tee()
    if path is not None:
        path.unlink(missing_ok=True)


def test_launch_failure_notifies_the_flow_result_callback():
    exit_codes: list[int] = []
    screen = WizardScreen(
        steps=[],
        services=[],
        on_launch_result=exit_codes.append,
    )

    try:
        screen._mark_launch_failed()

        assert exit_codes == [1]
    finally:
        _close_test_screen(screen)


def test_launch_cannot_detach_before_startup_reaches_log_stream(monkeypatch):
    exit_codes: list[int] = []
    notifications: list[str] = []
    screen = WizardScreen(
        steps=[],
        services=[],
        on_launch_result=exit_codes.append,
    )
    try:
        screen._phase = "launch"
        monkeypatch.setattr(
            screen,
            "notify",
            lambda message, **_kwargs: notifications.append(message),
        )

        screen.action_quit_wizard()

        assert exit_codes == []
        assert notifications == ["Startup is still running; Ctrl+C cancels it."]
    finally:
        _close_test_screen(screen)
