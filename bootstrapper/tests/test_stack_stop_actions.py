"""Stopping the running stack from the launch screen.

Before this, nothing in the TUI could stop containers. ``ctrl+c`` routes
to ``action_interrupt``, which sets exit code 130 and calls ``app.exit()``;
the surrounding cleanup only SIGTERMs bounded subprocesses, not the
containers. ``ctrl+q`` detaches. Both leave the stack running, so the only
way to stop what you just started was to leave and run ``./stop.sh``.

Two safety properties are pinned here rather than left to review:

* **The cold path destroys data.** Neither key tears anything down on a
  single press — each arms, and only a second press of the SAME key
  commits. A stray keystroke must never remove volumes.
* **Managed hosts are left running.** A managed ComfyUI-MPS / vLLM-Metal
  runtime is a host-global singleton on a fixed loopback port, shared by
  every Atlas consumer on the machine (``AtlasStopper.
  report_managed_hosts_left_running``). A project-scoped stop must not
  terminate one just because this project stopped — and the TUI must not
  claim "stopped" while a GPU-holding host process is still up.

Every test stubs the stopper; none of them may run a real teardown.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

import pytest  # noqa: E402
from textual.app import App  # noqa: E402

from ui.textual.screens.wizard_screen import WizardScreen  # noqa: E402
from ui.textual.widgets.prompt_panel import PromptOption, PromptStep  # noqa: E402


class _App(App):
    def __init__(self, screen: WizardScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


class _FakeStopper:
    """Records calls instead of tearing anything down."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, str]] = []
        self.managed_reported = False
        self.banner = object()

    def stop_services(self, cold_stop: bool, project_name: str) -> bool:
        self.calls.append((cold_stop, project_name))
        return True

    def report_managed_hosts_left_running(self) -> None:
        self.managed_reported = True


_OPEN: list[tuple[WizardScreen, Path | None]] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    while _OPEN:
        scr, path = _OPEN.pop()
        scr._close_launch_log_tee()
        if path is not None:
            path.unlink(missing_ok=True)


def _screen() -> WizardScreen:
    step = PromptStep(
        title="Dummy", step_index=1, step_total=1, heading="H", subtitle="",
        options=[PromptOption(value="a", label="A")], default_value="a",
    )
    scr = WizardScreen(steps=[step], services=[], no_splash=True)
    _OPEN.append((scr, scr._launch_log_path))
    return scr


def _run(scenario):
    return asyncio.run(scenario())


def _launched_screen_with(stopper: _FakeStopper) -> WizardScreen:
    scr = _screen()
    scr._stopper_factory = lambda: stopper
    return scr


def test_stop_is_inert_during_the_wizard():
    """No teardown key may fire before a launch exists."""
    stopper = _FakeStopper()
    scr = _launched_screen_with(stopper)

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr.action_stop_stack()
            scr.action_stop_stack()          # even twice
            scr.action_stop_stack_cold()
            scr.action_stop_stack_cold()
            await pilot.pause()
            return stopper.calls

    assert _run(scenario) == []


def test_a_single_press_arms_but_does_not_tear_down():
    stopper = _FakeStopper()
    scr = _launched_screen_with(stopper)

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr.action_stop_stack()
            await pilot.pause()
            return stopper.calls, scr._pending_teardown

    calls, pending = _run(scenario)
    assert calls == [], "a single press must never stop the stack"
    assert pending is not None, "the first press should arm the confirmation"


def test_a_second_press_commits_a_normal_stop():
    stopper = _FakeStopper()
    scr = _launched_screen_with(stopper)

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr.action_stop_stack()
            scr.action_stop_stack()
            await pilot.pause()
            await asyncio.sleep(0.05)
            return stopper.calls, stopper.managed_reported

    calls, managed = _run(scenario)
    assert len(calls) == 1, calls
    cold, _project = calls[0]
    assert cold is False, "the normal stop must preserve volumes"
    assert managed is True, "managed hosts left running must be reported"


def test_a_second_press_commits_a_cold_stop():
    stopper = _FakeStopper()
    scr = _launched_screen_with(stopper)

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr.action_stop_stack_cold()
            scr.action_stop_stack_cold()
            await pilot.pause()
            await asyncio.sleep(0.05)
            return stopper.calls

    calls = _run(scenario)
    assert len(calls) == 1, calls
    assert calls[0][0] is True, "the cold stop must request volume removal"


def test_arming_one_variant_does_not_commit_the_other():
    """Priming a normal stop must not let a cold press fire immediately.

    Otherwise ctrl+s then ctrl+x — two different keys, one press each —
    would delete volumes with no cold confirmation at all.
    """
    stopper = _FakeStopper()
    scr = _launched_screen_with(stopper)

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr.action_stop_stack()          # arms NORMAL
            scr.action_stop_stack_cold()     # different variant → re-arms
            await pilot.pause()
            await asyncio.sleep(0.05)
            return stopper.calls

    assert _run(scenario) == [], "a cross-variant press must not commit"


def test_the_teardown_worker_uses_its_own_group():
    """exclusive=True must not be able to cancel the launch pipeline.

    Every pre-existing worker on this screen runs in the default group;
    a teardown sharing it would cancel them on start.
    """
    stopper = _FakeStopper()
    scr = _launched_screen_with(stopper)

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            # Record what run_worker is actually asked for. Reading
            # scr.workers instead would race: the worker finishes and is
            # drained from the list before the assertion runs.
            seen: list[dict] = []
            original = scr.run_worker

            def _spy(*args, **kwargs):
                seen.append(kwargs)
                return original(*args, **kwargs)

            scr.run_worker = _spy  # type: ignore[method-assign]
            scr.action_stop_stack()
            scr.action_stop_stack()
            await pilot.pause()
            return seen

    seen = _run(scenario)
    assert len(seen) == 1, seen
    assert seen[0].get("group") == "stack_teardown", seen[0]
    assert seen[0].get("exclusive") is True, seen[0]


def test_teardown_keys_are_advertised_only_once_the_stack_is_up():
    """While starting, ctrl+c already cancels; offering stop too is noise."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr._launch_succeeded = False
            starting = scr._footer_hints()
            scr._launch_succeeded = True
            up = scr._footer_hints()
            return starting, up

    starting, up = _run(scenario)
    keys_starting = {k for keys, _ in starting for k in keys}
    keys_up = {k for keys, _ in up for k in keys}
    assert "ctrl+s" not in keys_starting, starting
    assert "ctrl+s" in keys_up, up
    assert "ctrl+x" in keys_up, up


def test_teardown_keys_are_not_swallowed_by_the_search_whitelist():
    """ctrl+s / ctrl+x are non-printable, so check_action still sees them.

    Printable priority bindings are stripped upstream by Textual once an
    Input has focus; ctrl-modified keys are not, so they must be handled
    by check_action's whitelist rather than assumed unreachable.
    """
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            # Outside the setup phase check_action must not suppress them.
            return (
                scr.check_action("stop_stack", ()),
                scr.check_action("stop_stack_cold", ()),
            )

    stop, cold = _run(scenario)
    assert stop is not False
    assert cold is not False


def test_a_failed_launch_does_not_advertise_teardown():
    """_launch_detach_ready is also set by _mark_launch_failed.

    Offering stop/cold-stop on a failed launch is a separate product
    decision that was deliberately not taken here, so the failure path
    must not pick the hints up by accident through the shared flag.
    """
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr._mark_launch_failed()
            await pilot.pause()
            return scr._launch_detach_ready, scr._launch_succeeded, scr._footer_hints()

    detach_ready, succeeded, hints = _run(scenario)
    assert detach_ready is True, "a failed launch must still free ctrl+q"
    assert succeeded is False
    keys = {k for ks, _ in hints for k in ks}
    assert "ctrl+s" not in keys and "ctrl+x" not in keys, hints
