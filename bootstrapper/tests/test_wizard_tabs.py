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
from textual.worker import Worker, WorkerState  # noqa: E402

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


class _FakeWorker:
    """Minimal stand-in for textual.worker.Worker — on_worker_state_changed
    only reads ``.name`` and ``.error`` off the worker object."""

    def __init__(self, name: str, error: Exception) -> None:
        self.name = name
        self.error = error


def test_setup_phase_worker_error_surfaces_as_toast_not_pane_write():
    """The Logs tab is unreachable until launch (show_tab() gates it behind
    _logs_enabled), so a setup-phase worker failure — e.g. the cloud-provider
    options-fetch worker — must show up as a toast, not a write into the
    hidden pane the user cannot see. Regression test: _log_pane is built
    eagerly now (never None), so on_worker_state_changed must branch on
    self._phase, not on self._log_pane's now-meaningless None-ness.
    """
    scr = _screen()
    notifications: list[tuple[str, str]] = []
    pane_writes: list[str] = []

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            assert scr._phase == "setup"
            scr.notify = lambda msg, **kwargs: notifications.append(
                (msg, kwargs.get("title", ""))
            )
            scr._write_status = lambda msg, **kwargs: pane_writes.append(msg)

            event = Worker.StateChanged(
                _FakeWorker("options-fetch", RuntimeError("boom")),
                WorkerState.ERROR,
            )
            scr.on_worker_state_changed(event)
            await pilot.pause()

    asyncio.run(scenario())

    assert pane_writes == [], "setup-phase failure must not write to the hidden pane"
    assert len(notifications) == 1
    msg, title = notifications[0]
    assert "boom" in msg
    assert "options-fetch" in title


def test_launch_phase_worker_error_writes_to_pane_not_toast():
    """Once launched, the Logs tab is visible, so a worker failure should
    write into the live pane (as before), not pop a toast over it."""
    scr = _screen()
    notifications: list[tuple[str, str]] = []
    pane_writes: list[str] = []

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._phase = "launch"
            scr.notify = lambda msg, **kwargs: notifications.append(
                (msg, kwargs.get("title", ""))
            )
            scr._write_status = lambda msg, **kwargs: pane_writes.append(msg)

            event = Worker.StateChanged(
                _FakeWorker("pipeline", RuntimeError("kaboom")),
                WorkerState.ERROR,
            )
            scr.on_worker_state_changed(event)
            await pilot.pause()

    asyncio.run(scenario())

    assert notifications == [], "launch-phase failure must not pop a toast"
    assert len(pane_writes) == 1
    assert "kaboom" in pane_writes[0]


def test_number_keys_switch_tabs_once_logs_are_enabled():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            # Logs are gated before launch: "2" must be a no-op.
            await pilot.press("2")
            await pilot.pause()
            before = scr.active_tab
            scr._logs_enabled = True
            await pilot.press("2")
            await pilot.pause()
            after = scr.active_tab
            await pilot.press("1")
            await pilot.pause()
            return before, after, scr.active_tab

    before, after, back = asyncio.run(scenario())

    assert before == BrandPanel.TAB_SETUP, "Logs must be gated before launch"
    assert after == BrandPanel.TAB_LOGS
    assert back == BrandPanel.TAB_SETUP, "Setup stays reachable after launch"


def test_clicking_a_tab_label_on_the_border_switches_tabs():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._brand_panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            spans = scr._brand_panel.tab_spans()
            start, end = spans[BrandPanel.TAB_LOGS]
            panel = scr._brand_panel
            y = panel.region.height - 1          # bottom border row
            await pilot.click(BrandPanel, offset=((start + end) // 2, y))
            await pilot.pause()
            return scr.active_tab

    assert asyncio.run(scenario()) == BrandPanel.TAB_LOGS
