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


def test_native_windows_fails_closed_before_launch(monkeypatch) -> None:
    monkeypatch.setattr(process_runner.os, "name", "nt")

    def unexpected_launch(*_args, **_kwargs):
        raise AssertionError("native Windows must not launch an unbounded tree")

    monkeypatch.setattr(process_runner.subprocess, "Popen", unexpected_launch)
    with pytest.raises(process_runner.CommandLaunchError):
        process_runner.run_with_deadline(["tool"])


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
    assert "await _stop_process_tree(proc)" in source
    assert "except asyncio.CancelledError:" in source


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
