"""Setup and Logs live in sibling containers; switching toggles display only.

Both bodies stay mounted so the stack overview keeps updating while the user
reads logs, and the log stream keeps appending while the user is on Setup.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from textual.app import App, ComposeResult  # noqa: E402

from ui.textual.screens.wizard_screen import WizardScreen  # noqa: E402
from ui.textual.widgets.block_logo import BrandPanel  # noqa: E402
from ui.textual.widgets.prompt_panel import PromptOption, PromptStep  # noqa: E402


class _App(App):
    def __init__(self, screen: WizardScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _screen() -> WizardScreen:
    # A single dummy step exercises the real (non-auto-launch) setup path
    # instead of an empty steps=[] list, which only ever pairs with
    # auto_launch=True in production (see ui/textual/integration.py) and
    # otherwise trips on_mount's unconditional _load_current_step() call.
    step = PromptStep(
        title="Dummy", step_index=1, step_total=1,
        heading="Dummy step", subtitle="",
        options=[PromptOption(value="a", label="A"), PromptOption(value="b", label="B")],
        default_value="a",
    )
    return WizardScreen(steps=[step], services=[], no_splash=True)


def test_both_tab_bodies_are_mounted_and_only_one_is_visible():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            setup = scr.query_one("#tab-setup")
            logs = scr.query_one("#tab-logs")
            return setup.display, logs.display, scr.active_tab

    setup_visible, logs_visible, active = asyncio.run(scenario())

    assert setup_visible is True
    assert logs_visible is False, "Logs body stays mounted but hidden"
    assert active == BrandPanel.TAB_SETUP


def test_show_tab_swaps_visibility_without_unmounting():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            # show_tab() gates the Logs tab behind _logs_enabled, which only
            # _transition_to_launch() flips. This test exercises the raw swap
            # mechanism, so put the screen in the post-launch state first.
            scr._logs_enabled = True
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            setup = scr.query_one("#tab-setup")
            logs = scr.query_one("#tab-logs")
            return setup.display, logs.display, scr.active_tab, setup.is_mounted

    setup_visible, logs_visible, active, still_mounted = asyncio.run(scenario())

    assert setup_visible is False
    assert logs_visible is True
    assert active == BrandPanel.TAB_LOGS
    assert still_mounted is True, "hidden body must NOT be unmounted"
