from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import process_runner


ROOT = Path(__file__).resolve().parents[2]


def test_run_with_deadline_starts_isolated_session(monkeypatch) -> None:
    real_popen = subprocess.Popen
    popen_kwargs: dict[str, object] = {}

    def recording_popen(command, **kwargs):
        popen_kwargs.update(kwargs)
        return real_popen(command, **kwargs)

    monkeypatch.setattr(process_runner.subprocess, "Popen", recording_popen)
    result = process_runner.run_with_deadline(
        [sys.executable, "-c", "print('ok')"]
    )

    assert popen_kwargs["start_new_session"] is True
    assert result.stdout == "ok\n"


def test_run_with_deadline_rejects_unbounded_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        process_runner.run_with_deadline(["unused"], timeout_seconds=0)


def test_run_with_deadline_rejects_unbounded_output_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        process_runner.run_with_deadline(["unused"], max_output_bytes=0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_run_with_deadline_kills_term_resistant_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "escaped-descendant"
    descendant = (
        "import pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.4); "
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    leader = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(10)"
    )

    with pytest.raises(subprocess.TimeoutExpired):
        process_runner.run_with_deadline(
            [sys.executable, "-c", leader, descendant],
            timeout_seconds=0.05,
            termination_grace_seconds=0.05,
        )

    time.sleep(0.6)
    assert not marker.exists()


def test_run_with_deadline_rejects_excessive_combined_output() -> None:
    with pytest.raises(process_runner.CommandOutputTooLarge):
        process_runner.run_with_deadline(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 700); "
                "sys.stderr.write('y' * 700)",
            ],
            max_output_bytes=1024,
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_run_with_deadline_handles_sigterm_before_popen_returns(
    monkeypatch, tmp_path: Path
) -> None:
    marker = tmp_path / "launch-race-orphan"
    real_popen = subprocess.Popen

    def interrupted_launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGTERM)
        return process

    monkeypatch.setattr(process_runner.subprocess, "Popen", interrupted_launch)
    command = [
        sys.executable,
        "-c",
        "import pathlib,time; time.sleep(0.4); "
        f"pathlib.Path({str(marker)!r}).touch()",
    ]

    with pytest.raises(SystemExit) as raised:
        process_runner.run_with_deadline(
            command, termination_grace_seconds=0.05
        )

    assert raised.value.code == 128 + signal.SIGTERM
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_run_with_deadline_preserves_sigterm_when_launch_fails(monkeypatch) -> None:
    def interrupted_launch(*_args, **_kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        raise OSError("simulated launch failure")

    monkeypatch.setattr(process_runner.subprocess, "Popen", interrupted_launch)

    with pytest.raises(SystemExit) as raised:
        process_runner.run_with_deadline(["tool"])

    assert raised.value.code == 128 + signal.SIGTERM


def test_native_windows_fails_closed_before_launch(monkeypatch) -> None:
    monkeypatch.setattr(process_runner.os, "name", "nt")

    def unexpected_launch(*_args, **_kwargs):
        raise AssertionError("native Windows must not launch an unbounded tree")

    monkeypatch.setattr(process_runner.subprocess, "Popen", unexpected_launch)
    with pytest.raises(process_runner.CommandLaunchError):
        process_runner.run_with_deadline(["tool"])


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_cleans_process_started_from_asyncio_thread(tmp_path: Path) -> None:
    ready = tmp_path / "threaded-ready"
    escaped = tmp_path / "threaded-descendant"
    descendant = (
        "import pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.4); "
        f"pathlib.Path({str(escaped)!r}).touch()"
    )
    leader = (
        "import pathlib,subprocess,sys,time; "
        f"pathlib.Path({str(ready)!r}).touch(); "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(10)"
    )
    command = [sys.executable, "-c", leader, descendant]
    wrapper = (
        "import asyncio,functools,sys; "
        f"sys.path.insert(0, {str(ROOT / 'bootstrapper')!r}); "
        "from core.process_runner import ("
        "cleanup_active_processes_on_sigterm,run_with_deadline); "
        f"work=functools.partial(run_with_deadline, {command!r}, "
        "termination_grace_seconds=0.05); "
        "scope=cleanup_active_processes_on_sigterm(); scope.__enter__(); "
        "asyncio.run(asyncio.to_thread(work))"
    )
    process = subprocess.Popen([sys.executable, "-c", wrapper], cwd=ROOT)
    deadline = time.monotonic() + 3
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()

    os.kill(process.pid, signal.SIGTERM)
    process.wait(timeout=3)

    time.sleep(0.6)
    assert process.returncode == 128 + signal.SIGTERM
    assert not escaped.exists()


def test_failure_log_capture_is_bounded_and_process_grouped() -> None:
    source = (
        ROOT
        / "bootstrapper"
        / "ui"
        / "textual"
        / "screens"
        / "wizard_screen.py"
    ).read_text(encoding="utf-8")

    assert "_FAILURE_LOG_TIMEOUT_SECONDS =" in source
    assert "start_new_session=os.name == \"posix\"" in source
    assert source.count("proc = await _launch_process(") == 2
    assert "await _stop_process_tree(proc)" in source
    assert "except asyncio.CancelledError:" in source


def test_tui_owns_threaded_process_cleanup_and_compose_deadlines() -> None:
    integration = (
        ROOT / "bootstrapper" / "ui" / "textual" / "integration.py"
    ).read_text(encoding="utf-8")
    wizard = (
        ROOT
        / "bootstrapper"
        / "ui"
        / "textual"
        / "screens"
        / "wizard_screen.py"
    ).read_text(encoding="utf-8")

    assert integration.count("_run_app_with_process_cleanup(") == 3
    assert "with cleanup_active_processes_on_sigterm():" in integration
    assert "_COMPOSE_BUILD_TIMEOUT_SECONDS =" in wizard
    assert "_COMPOSE_UP_TIMEOUT_SECONDS =" in wizard
    assert "return await _run_streamed_command(" in wizard


def test_compose_timeout_policy_keeps_follow_logs_cancellation_driven() -> None:
    from ui.textual.screens.wizard_screen import (
        _COMPOSE_BUILD_TIMEOUT_SECONDS,
        _COMPOSE_UP_TIMEOUT_SECONDS,
        _compose_timeout_seconds,
    )

    assert _compose_timeout_seconds(["build"]) == _COMPOSE_BUILD_TIMEOUT_SECONDS
    assert _compose_timeout_seconds(["up", "-d"]) == _COMPOSE_UP_TIMEOUT_SECONDS
    assert _compose_timeout_seconds(["logs", "--tail=20"]) == (
        _COMPOSE_UP_TIMEOUT_SECONDS
    )
    assert _compose_timeout_seconds(["logs", "-f"]) is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_async_stop_process_tree_kills_term_resistant_descendant(
    tmp_path: Path,
) -> None:
    from ui.textual.screens.wizard_screen import _stop_process_tree

    marker = tmp_path / "async-escaped-descendant"
    descendant = (
        "import pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.4); "
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    leader = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(10)"
    )

    async def exercise() -> None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            leader,
            descendant,
            start_new_session=True,
        )
        await _stop_process_tree(proc, termination_grace_seconds=0.05)

    asyncio.run(exercise())
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_streamed_command_timeout_kills_term_resistant_descendant(
    tmp_path: Path,
) -> None:
    from ui.textual.screens.wizard_screen import _run_streamed_command

    marker = tmp_path / "streamed-timeout-descendant"
    descendant = (
        "import pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.4); "
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    leader = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(10)"
    )

    async def exercise() -> int:
        return await _run_streamed_command(
            [sys.executable, "-c", leader, descendant],
            cwd=tmp_path,
            env=os.environ.copy(),
            on_line=lambda _line: None,
            timeout_seconds=0.05,
            termination_grace_seconds=0.05,
        )

    assert asyncio.run(exercise()) == 124
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_streamed_command_cancellation_kills_term_resistant_descendant(
    tmp_path: Path,
) -> None:
    from ui.textual.screens.wizard_screen import _run_streamed_command

    ready = tmp_path / "streamed-cancel-ready"
    marker = tmp_path / "streamed-cancel-descendant"
    descendant = (
        "import pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.4); "
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    leader = (
        "import pathlib,subprocess,sys,time; "
        f"pathlib.Path({str(ready)!r}).touch(); "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(10)"
    )

    async def exercise() -> None:
        task = asyncio.create_task(
            _run_streamed_command(
                [sys.executable, "-c", leader, descendant],
                cwd=tmp_path,
                env=os.environ.copy(),
                on_line=lambda _line: None,
                timeout_seconds=None,
                termination_grace_seconds=0.05,
            )
        )
        deadline = asyncio.get_running_loop().time() + 3
        while not ready.exists() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert ready.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_streamed_command_launch_cancellation_reaps_process_group(
    monkeypatch, tmp_path: Path
) -> None:
    from ui.textual.screens import wizard_screen

    marker = tmp_path / "streamed-launch-cancel-descendant"
    descendant = (
        "import pathlib,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(0.4); "
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    leader = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(1)"
    )
    launch_started = asyncio.Event()
    real_create = asyncio.create_subprocess_exec

    async def delayed_create(*args, **kwargs):
        proc = await real_create(*args, **kwargs)
        launch_started.set()
        await asyncio.sleep(0.1)
        return proc

    monkeypatch.setattr(
        wizard_screen.asyncio, "create_subprocess_exec", delayed_create
    )

    async def exercise() -> None:
        task = asyncio.create_task(
            wizard_screen._run_streamed_command(
                [sys.executable, "-c", leader, descendant],
                cwd=tmp_path,
                env=os.environ.copy(),
                on_line=lambda _line: None,
                timeout_seconds=10,
                termination_grace_seconds=0.05,
            )
        )
        await asyncio.wait_for(launch_started.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    time.sleep(0.6)
    assert not marker.exists()
