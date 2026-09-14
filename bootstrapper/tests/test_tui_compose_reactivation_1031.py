"""#1031: the TUI's synchronous Compose hook is bounded and cancellable."""

from __future__ import annotations

import asyncio
import inspect
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ui.textual.screens import wizard_screen


class _CommandManager:
    def __init__(self, root_dir: Path, command: list[str]) -> None:
        self.root_dir = root_dir
        self.command = command
        self.project_name_override: str | None = None
        self.calls: list[tuple[list[str], bool, list[str] | None, str | None]] = []

    def _build_compose_command(
        self,
        args: list[str],
        use_env_file: bool = True,
        top_level_flags: list[str] | None = None,
    ) -> list[str]:
        self.calls.append(
            (list(args), use_env_file, top_level_flags, self.project_name_override)
        )
        return list(self.command)


def _process_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return bool(status) and not status.startswith("Z")


def _wait_for_pid(path: Path) -> int:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            value = path.read_text(encoding="ascii").strip()
        except OSError:
            value = ""
        if value:
            return int(value)
        time.sleep(0.01)
    pytest.fail(f"subprocess did not record its PID in {path}")


def _wait_for_process_to_stop(pid: int) -> None:
    deadline = time.monotonic() + 3
    while _process_is_live(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _process_is_live(pid), f"owned process {pid} survived cleanup"


def _stop_control(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


def _executor(
    tmp_path: Path,
    command: list[str],
    messages: list[tuple[str, str, str]],
) -> tuple[object, _CommandManager]:
    manager = _CommandManager(tmp_path, command)

    def log(message: str, *, source: str, level: str) -> None:
        messages.append((message, source, level))

    executor = wizard_screen._ThreadedComposeExecutor(
        manager, asyncio.get_running_loop(), log
    )
    return executor, manager


@pytest.fixture
def short_compose_bounds(monkeypatch):
    # Leave enough time for Python and nested child fixtures to record their
    # readiness even on loaded CI. Wall-clock assertions below still prove the
    # configured deadline and cleanup grace bound every command.
    monkeypatch.setattr(wizard_screen, "_COMPOSE_UP_TIMEOUT_SECONDS", 1.5)
    monkeypatch.setattr(wizard_screen, "_PROCESS_TERMINATION_GRACE_SECONDS", 0.2)


def test_actual_tui_executor_bounds_a_silent_process(
    tmp_path: Path, short_compose_bounds
) -> None:
    messages: list[tuple[str, str, str]] = []

    async def exercise() -> tuple[int, float]:
        executor, manager = _executor(
            tmp_path,
            [sys.executable, "-c", "import time; time.sleep(10)"],
            messages,
        )
        started = time.monotonic()
        rc = await asyncio.to_thread(executor, ["restart", "n8n"])
        assert manager.calls == [
            (["restart", "n8n"], True, ["--ansi=never"], None)
        ]
        return rc, time.monotonic() - started

    rc, elapsed = asyncio.run(exercise())

    assert rc == 124
    assert elapsed < 4
    assert any("timed out" in message.lower() for message, _, _ in messages)
    assert any("exit status 124" in message for message, _, _ in messages)


def test_actual_tui_executor_streams_until_endless_output_times_out(
    tmp_path: Path, short_compose_bounds
) -> None:
    messages: list[tuple[str, str, str]] = []
    script = (
        "import time\n"
        "i = 0\n"
        "while True:\n"
        " print(f'progress-{i}', flush=True)\n"
        " i += 1\n"
        " time.sleep(0.01)\n"
    )

    async def exercise() -> tuple[int, float]:
        executor, _manager = _executor(
            tmp_path, [sys.executable, "-c", script], messages
        )
        started = time.monotonic()
        rc = await asyncio.to_thread(executor, ["restart", "n8n"])
        return rc, time.monotonic() - started

    rc, elapsed = asyncio.run(exercise())
    assert rc == 124
    assert elapsed < 4
    assert any(message.startswith("progress-") for message, _, _ in messages)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_inherited_output_pipe_is_bounded_after_the_leader_exits(
    tmp_path: Path, short_compose_bounds
) -> None:
    child_pid = tmp_path / "pipe-child.pid"
    child = (
        "import os,pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(10)"
    )
    leader = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]])"
    )
    messages: list[tuple[str, str, str]] = []

    async def exercise() -> tuple[int, float]:
        executor, _manager = _executor(
            tmp_path,
            [sys.executable, "-c", leader, child],
            messages,
        )
        started = time.monotonic()
        rc = await asyncio.to_thread(executor, ["restart", "n8n"])
        return rc, time.monotonic() - started

    rc, elapsed = asyncio.run(exercise())
    assert rc == 124
    assert elapsed < 4
    _wait_for_process_to_stop(_wait_for_pid(child_pid))
    assert any("timed out" in message.lower() for message, _, _ in messages)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_timeout_reaps_owned_tree_without_touching_unrelated_process(
    tmp_path: Path, short_compose_bounds
) -> None:
    leader_pid = tmp_path / "leader.pid"
    child_pid = tmp_path / "child.pid"
    child = (
        "import os,pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(10)"
    )
    leader = (
        "import os,pathlib,subprocess,sys,time; "
        f"pathlib.Path({str(leader_pid)!r}).write_text(str(os.getpid())); "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]]); "
        "time.sleep(10)"
    )
    control = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        start_new_session=True,
    )
    messages: list[tuple[str, str, str]] = []

    async def exercise() -> tuple[int, float]:
        executor, _manager = _executor(
            tmp_path,
            [sys.executable, "-c", leader, child],
            messages,
        )
        started = time.monotonic()
        rc = await asyncio.to_thread(executor, ["restart", "n8n"])
        return rc, time.monotonic() - started

    try:
        rc, elapsed = asyncio.run(exercise())
        assert rc == 124
        assert elapsed < 4
        _wait_for_process_to_stop(_wait_for_pid(leader_pid))
        _wait_for_process_to_stop(_wait_for_pid(child_pid))
        assert control.poll() is None
    finally:
        _stop_control(control)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_pipeline_cancellation_reaps_owned_tree_before_propagating(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(wizard_screen, "_COMPOSE_UP_TIMEOUT_SECONDS", 10.0)
    monkeypatch.setattr(wizard_screen, "_PROCESS_TERMINATION_GRACE_SECONDS", 0.05)
    leader_pid = tmp_path / "cancel-leader.pid"
    child_pid = tmp_path / "cancel-child.pid"
    child = (
        "import os,pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(10)"
    )
    leader = (
        "import os,pathlib,subprocess,sys,time; "
        f"pathlib.Path({str(leader_pid)!r}).write_text(str(os.getpid())); "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]]); "
        "time.sleep(10)"
    )
    messages: list[tuple[str, str, str]] = []
    control = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        start_new_session=True,
    )

    async def exercise() -> None:
        executor, _manager = _executor(
            tmp_path,
            [sys.executable, "-c", leader, child],
            messages,
        )
        task = asyncio.create_task(
            executor.run_in_thread(lambda: executor(["restart", "n8n"]))
        )
        await asyncio.to_thread(_wait_for_pid, child_pid)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        _wait_for_process_to_stop(_wait_for_pid(leader_pid))
        _wait_for_process_to_stop(_wait_for_pid(child_pid))

    try:
        asyncio.run(exercise())
        assert control.poll() is None
        assert any("cancelled" in message.lower() for message, _, _ in messages)
    finally:
        _stop_control(control)


def test_nonzero_exit_is_returned_with_stable_redacted_diagnostic(
    tmp_path: Path
) -> None:
    messages: list[tuple[str, str, str]] = []
    secret = "do-not-repeat-this-credential"

    async def exercise() -> int:
        executor, _manager = _executor(
            tmp_path,
            [
                sys.executable,
                "-c",
                "import sys; print('restart refused', flush=True); sys.exit(17)",
                secret,
            ],
            messages,
        )
        return await asyncio.to_thread(executor, ["restart", "n8n"])

    assert asyncio.run(exercise()) == 17
    rendered = [message for message, _, _ in messages]
    errors = [message for message, _, level in messages if level == "error"]
    assert "restart refused" in rendered
    assert "Docker Compose command failed with exit status 17" in errors
    assert all(secret not in message for message in errors)


def test_success_preserves_streamed_progress_without_failure_diagnostic(
    tmp_path: Path
) -> None:
    messages: list[tuple[str, str, str]] = []

    async def exercise() -> int:
        executor, manager = _executor(
            tmp_path,
            [sys.executable, "-c", "print('restarting'); print('started')"],
            messages,
        )
        rc = await asyncio.to_thread(
            executor,
            ["restart", "n8n"],
            False,
            "atlas-fixture",
        )
        assert manager.project_name_override is None
        assert manager.calls == [
            (["restart", "n8n"], False, ["--ansi=never"], "atlas-fixture")
        ]
        return rc

    assert asyncio.run(exercise()) == 0
    rendered = [message for message, _, _ in messages]
    assert "restarting" in rendered
    assert "started" in rendered
    assert not any("failed with exit status" in message for message in rendered)


def test_success_preserves_the_hook_suppression_of_empty_output_lines(
    tmp_path: Path,
) -> None:
    messages: list[tuple[str, str, str]] = []

    async def exercise() -> int:
        executor, _manager = _executor(
            tmp_path,
            [sys.executable, "-c", "print(); print('started')"],
            messages,
        )
        return await asyncio.to_thread(executor, ["restart", "n8n"])

    assert asyncio.run(exercise()) == 0
    assert "" not in [message for message, _, _ in messages]


def test_pipeline_installs_bounded_executor_as_the_actual_compose_hook() -> None:
    source = inspect.getsource(wizard_screen.WizardScreen._run_pipeline_and_stream)

    assert "_ThreadedComposeExecutor(" in source
    assert "docker_manager.execute_compose_command = compose_executor" in source
    assert "compose_executor.run_in_thread(fn)" in source
    assert "Popen(" not in source
