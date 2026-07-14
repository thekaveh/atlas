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
