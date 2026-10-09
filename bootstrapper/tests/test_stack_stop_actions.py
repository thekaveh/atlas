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
    scr._launched_project = "atlas"  # a real launch pins the name it used
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
            scr._launch_succeeded = True  # stop keys work only after a successful launch
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
            scr._launch_succeeded = True  # stop keys work only after a successful launch
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
            scr._launch_succeeded = True  # stop keys work only after a successful launch
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
            scr._launch_succeeded = True  # stop keys work only after a successful launch
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
            scr._launch_succeeded = True  # stop keys work only after a successful launch
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
            scr._launch_succeeded = True  # stop keys work only after a successful launch
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
            scr._launch_succeeded = True  # stop keys work only after a successful launch
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


def test_a_second_stop_is_refused_while_one_is_running():
    """A cold stop must not start while a normal stop is still running.

    The worker is exclusive, but the stopper runs in a thread that a
    cancelled worker cannot stop: compose down and down -v ran at once,
    and only the second outcome was reported (2026-10-08 run, cycle 37).
    """
    import threading

    release = threading.Event()

    class _SlowStopper(_FakeStopper):
        def stop_services(self, cold_stop: bool, project_name: str) -> bool:
            self.calls.append((cold_stop, project_name))
            release.wait(5)
            return True

    stopper = _SlowStopper()
    scr = _launched_screen_with(stopper)

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr._launch_succeeded = True
            scr.action_stop_stack()
            scr.action_stop_stack()
            await pilot.pause()
            await asyncio.sleep(0.1)
            scr.action_stop_stack_cold()
            scr.action_stop_stack_cold()
            await pilot.pause()
            await asyncio.sleep(0.1)
            calls = list(stopper.calls)
            release.set()
            for _ in range(100):
                await asyncio.sleep(0.05)
                if not scr._teardown_running:
                    break
            # Once the first stop finished, a new stop is accepted again; a
            # guard never cleared refused every later stop (cycle 44).
            scr.action_stop_stack_cold()
            scr.action_stop_stack_cold()
            await pilot.pause()
            for _ in range(100):
                await asyncio.sleep(0.05)
                if len(stopper.calls) > 1:
                    break
            return calls, list(stopper.calls)

    calls, later = _run(scenario)
    assert calls == [(False, calls[0][1])], calls
    assert [cold for cold, _ in later] == [False, True], later


def test_quit_and_interrupt_are_refused_while_a_stop_runs():
    """Ctrl+Q / Ctrl+C during a stop exited the app while compose down kept
    running unseen, with no result reported (2026-10-08 run, cycle 48)."""
    import threading

    release = threading.Event()

    class _SlowStopper(_FakeStopper):
        def stop_services(self, cold_stop: bool, project_name: str) -> bool:
            self.calls.append((cold_stop, project_name))
            release.wait(5)
            return True

    stopper = _SlowStopper()
    scr = _launched_screen_with(stopper)
    exits = []

    async def scenario():
        app = _App(scr)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr._launch_succeeded = True
            scr._launch_detach_ready = True
            app.exit = lambda *a, **k: exits.append(a)  # type: ignore[method-assign]
            scr.action_stop_stack()
            scr.action_stop_stack()
            for _ in range(100):
                await asyncio.sleep(0.02)
                if scr._teardown_running:
                    break
            scr.action_quit_wizard()
            scr._exit_refused_until = 0.0  # the second-press escape is tested separately
            refused = scr.refuse_exit_during_teardown()
            escaped = not scr.refuse_exit_during_teardown()  # a second press within 5 s leaves
            release.set()
            for _ in range(100):
                await asyncio.sleep(0.05)
                if not scr._teardown_running:
                    break
            return refused and escaped

    assert _run(scenario) is True
    assert exits == [], "quit must not exit while the stop runs"


def test_the_stop_targets_the_launched_project_not_a_later_env_edit():
    """The name was re-read from .env at stop time; a `./stop.sh -p atlas`
    in another terminal redirected a cold stop to another stack (cycle 48)."""
    from types import SimpleNamespace

    stopper = _FakeStopper()
    scr = _launched_screen_with(stopper)
    names = iter(["rag", "atlas"])
    scr._starter = SimpleNamespace(config_parser=SimpleNamespace(get_project_name=lambda: next(names)))
    scr._launched_project = scr._starter.config_parser.get_project_name()  # pinned at launch: rag

    async def scenario():
        async with _App(scr).run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase = "launch"
            scr._launch_succeeded = True
            scr.action_stop_stack_cold()
            scr.action_stop_stack_cold()
            await pilot.pause()
            await asyncio.sleep(0.1)
            return stopper.calls

    assert _run(scenario) == [(True, "rag")]


def test_teardown_compose_output_reaches_the_log_pane_not_the_terminal(tmp_path, monkeypatch):
    """The stopper's compose child inherited the terminal fds Textual draws
    on, and its surviving-volume warning went to a discarded print
    (2026-10-08 run, cycle 48)."""
    import subprocess

    from core.docker_manager import DockerManager

    manager = DockerManager(str(tmp_path))
    logged = []
    scr = _screen()
    monkeypatch.setattr(scr, "_safe_log", lambda message, **_k: logged.append(message))
    scr._route_teardown_output(manager)
    from utils import system

    child = ["sh", "-c", "echo 'Container x  Removed' >&2"]
    assert system.run_compose_child(child, str(tmp_path), manager.output_sink) == 0
    assert "Container x  Removed" in logged
    manager._report_surviving_volumes = lambda project, emit: emit("survivor warning") or ["v"]
    monkeypatch.setattr(manager, "execute_compose_command", lambda *a, **k: 0)
    assert manager.perform_cold_stop_cleanup() is False
    assert "survivor warning" in logged


def test_teardown_compose_output_is_bounded_and_decoded(tmp_path, monkeypatch):
    """Streamed and bounded: a wedged daemon showed nothing and never ended
    (cycle 69); the child is killed, not waited out, and non-UTF-8 output is
    replaced, not a crashed reader (cycle 74)."""
    import time as _time

    from core.docker_manager import DockerManager
    from utils import system

    manager = DockerManager(str(tmp_path))
    logged = []
    scr = _screen()
    monkeypatch.setattr(scr, "_safe_log", lambda message, **_k: logged.append(message))
    scr._route_teardown_output(manager)
    monkeypatch.setattr(system, "SINK_COMPOSE_TIMEOUT_SECONDS", 2)

    began = _time.monotonic()
    child = ["sh", "-c", "echo started; exec sleep 30"]  # exec: the kill closes the pipe
    assert system.run_compose_child(child, str(tmp_path), manager.output_sink) == 124
    assert _time.monotonic() - began < 10
    assert logged[0] == "started"
    assert "did not finish" in logged[-1]
    logged.clear()
    assert system.run_compose_child(["sh", "-c", "printf 'caf\\351\\n'"], str(tmp_path), manager.output_sink) == 0
    assert logged == ["caf\ufffd"]


async def _until(condition, *, tries: int = 100, pause: float = 0.05) -> None:
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(pause)


def test_real_ctrl_q_and_ctrl_c_keys_cannot_exit_during_a_stop():
    """Textual's priority ctrl+q binding ran App.action_quit, so a keypress
    never reached the wizard's guard; with a modal open, ctrl+c read the
    modal as the screen and skipped it (2026-10-08 run, cycle 61)."""
    import inspect
    import threading

    from textual.binding import Binding
    from textual.screen import ModalScreen

    from ui.textual import integration
    from ui.textual.screens import wizard_screen

    source = inspect.getsource(integration)
    # Both real apps take the ctrl+q mixin and check the guard on ctrl+c (cycle 63).
    assert source.count("(GuardedQuitMixin, App)") == 2
    assert source.count("if teardown_blocks_exit(self):\n                return") == 2
    release = threading.Event()

    class _SlowStopper(_FakeStopper):
        def stop_services(self, cold_stop: bool, project_name: str) -> bool:
            self.calls.append((cold_stop, project_name))
            release.wait(5)
            return True

    scr = _launched_screen_with(_SlowStopper())
    exits = []

    class _GuardedApp(_App):
        BINDINGS = [Binding("ctrl+c", "interrupt", "Quit", priority=True)]

        def action_interrupt(self) -> None:
            if not wizard_screen.teardown_blocks_exit(self):
                exits.append("interrupt")

        def action_quit(self) -> None:
            wizard_screen.guarded_quit(self)

        def exit(self, *args, **kwargs):  # noqa: A003
            exits.append("exit")

    async def scenario():
        app = _GuardedApp(scr)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            scr._phase, scr._launch_succeeded, scr._launch_detach_ready = "launch", True, True
            scr.action_stop_stack()
            scr.action_stop_stack()
            await _until(lambda: getattr(scr, "_teardown_running", False))
            await pilot.press("ctrl+q")
            scr._exit_refused_until = 0.0  # each guard on its own, not the second-press escape
            app.push_screen(ModalScreen())
            await pilot.pause()
            await pilot.press("ctrl+c")
            release.set()
            await _until(lambda: not getattr(scr, "_teardown_running", False))

    _run(scenario)
    assert exits == [], exits


def test_the_stop_flag_is_set_at_commit_and_the_escape_window_expires(monkeypatch):
    """A quit handled before the worker's first step exited with `down`
    scheduled; the second-press escape must also expire (cycle 74)."""
    from ui.textual.screens import wizard_screen

    scr = _launched_screen_with(_FakeStopper())
    scr._phase, scr._launch_succeeded = "launch", True
    scr.run_worker = lambda work, **_k: work.close()  # the worker never starts
    notes, logs = [], []
    scr.notify = lambda message, **_k: notes.append(message)
    scr._safe_log = lambda message, **_k: logs.append(message)
    clock = {"t": 100.0}
    monkeypatch.setattr(wizard_screen, "_teardown_clock", lambda: clock["t"])
    scr.action_stop_stack()
    scr.action_stop_stack()
    assert scr.refuse_exit_during_teardown() is True  # set before any worker step
    clock["t"] += 6  # past the 5 s window: refused again, not let through
    assert scr.refuse_exit_during_teardown() is True
    clock["t"] += 1
    assert scr.refuse_exit_during_teardown() is False
    assert any("result is unknown" in line for line in logs), logs
