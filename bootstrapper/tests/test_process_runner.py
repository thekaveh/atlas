from __future__ import annotations

import signal
import subprocess
from pathlib import Path

import pytest

from core import process_runner


ROOT = Path(__file__).resolve().parents[2]


class _TimedOutProcess:
    pid = 4242
    returncode = -signal.SIGTERM

    def __init__(self) -> None:
        self.communicate_calls = 0

    def communicate(self, *, timeout=None):
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(["slow"], timeout)
        return "", ""


class _InterruptedProcess(_TimedOutProcess):
    def communicate(self, *, timeout=None):
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise KeyboardInterrupt
        return "", ""


def test_run_with_deadline_starts_isolated_session_and_kills_tree(monkeypatch) -> None:
    process = _TimedOutProcess()
    popen_kwargs: dict[str, object] = {}
    signals: list[tuple[int, int]] = []

    def fake_popen(_command, **kwargs):
        popen_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(process_runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(process_runner.os, "name", "posix")
    monkeypatch.setattr(
        process_runner.os, "killpg", lambda pid, signum: signals.append((pid, signum))
    )

    with pytest.raises(subprocess.TimeoutExpired):
        process_runner.run_with_deadline(["slow"], timeout_seconds=0.1)

    assert popen_kwargs["start_new_session"] is True
    assert signals == [(4242, signal.SIGTERM)]
    assert process.communicate_calls == 2


def test_run_with_deadline_rejects_unbounded_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        process_runner.run_with_deadline(["unused"], timeout_seconds=0)


def test_run_with_deadline_kills_tree_when_parent_is_interrupted(monkeypatch) -> None:
    process = _InterruptedProcess()
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        process_runner.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(process_runner.os, "name", "posix")
    monkeypatch.setattr(
        process_runner.os, "killpg", lambda pid, signum: signals.append((pid, signum))
    )

    with pytest.raises(KeyboardInterrupt):
        process_runner.run_with_deadline(["slow"])

    assert signals == [(4242, signal.SIGTERM)]
    assert process.communicate_calls == 2


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
