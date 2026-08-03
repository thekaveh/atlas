"""Small bounded-process primitive for non-interactive bootstrapper commands."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_COMMAND_TIMEOUT_SECONDS = 300.0
TERMINATION_GRACE_SECONDS = 2.0


def _signal_process_tree(process: subprocess.Popen[str], signum: int) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        return
    try:  # pragma: no cover - native Windows bootstrap is not exercised in CI
        process.terminate() if signum == signal.SIGTERM else process.kill()
    except ProcessLookupError:
        pass


def _stop_and_reap(process: subprocess.Popen[str]) -> None:
    _signal_process_tree(process, signal.SIGTERM)
    try:
        process.communicate(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_process_tree(process, signal.SIGKILL)
        process.communicate()


def run_with_deadline(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Capture a non-interactive command and stop its process tree on timeout."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _stop_and_reap(process)
        raise
    except BaseException:
        _stop_and_reap(process)
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
