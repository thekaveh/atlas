"""Textual launch failures must propagate to the CLI exit status."""

import asyncio

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
        # #1032: the cancel hint names what cancelling leaves behind.
        assert notifications == [
            "Startup is still running; Ctrl+C cancels it (containers already "
            "started keep running · configuration written so far kept · no "
            "data deleted)."
        ]
    finally:
        _close_test_screen(screen)


def test_project_name_persistence_failure_restores_launch_state() -> None:
    exit_codes: list[int] = []
    original_banner = object()

    class Starter:
        banner = original_banner

        def __init__(self) -> None:
            self.persist_calls: list[str] = []

        def _persist_project_name(self, project_name: str) -> bool:
            self.persist_calls.append(project_name)
            return False

    starter = Starter()
    screen = WizardScreen(
        steps=[],
        services=[],
        starter=starter,
        prefilled_stack_options={"project_name": "atlas-test"},
        on_launch_result=exit_codes.append,
    )
    screen._phase = "launch"

    try:
        asyncio.run(screen._run_pipeline_and_stream())

        assert starter.persist_calls == ["atlas-test"]
        assert starter.banner is original_banner
        assert exit_codes == [1]
        assert screen._launch_detach_ready is True
        assert screen._launch_succeeded is False
    finally:
        _close_test_screen(screen)


def test_wizard_cold_start_removes_volumes_before_any_env_write() -> None:
    """A wizard "Cold start: yes" used to only rotate keys, leaving volumes
    (and n8n's encryption key) behind; the CLI --cold path cleaned up."""
    from types import SimpleNamespace

    calls: list[tuple] = []

    class Starter:
        banner = object()
        config_parser = SimpleNamespace(get_project_name=lambda: "atlas")
        docker_manager = SimpleNamespace(execute_compose_command=lambda *a, **k: 0)

        def prepare_environment(self, **kwargs):
            calls.append(("prepare_environment", kwargs))
            return False  # stop the pipeline right after the cold step

        def __getattr__(self, name):
            # Every later pipeline step is bound eagerly; any call is a bug.
            def _step(*_args, **_kwargs):
                calls.append((name,))
                return True
            return _step

    exit_codes: list[int] = []
    screen = WizardScreen(
        steps=[],
        services=[],
        starter=Starter(),
        prefilled_stack_options={"cold": True, "cold_cleanup_pending": True, "base_port": 63000},
        on_launch_result=exit_codes.append,
    )
    screen._phase = "launch"
    try:
        asyncio.run(screen._run_pipeline_and_stream())

        assert calls == [(
            "prepare_environment",
            {"cold_start": True, "base_port": 63000, "project_name": None},
        )]
        assert exit_codes and set(exit_codes) == {1}
    finally:
        _close_test_screen(screen)
