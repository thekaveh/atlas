"""Bounded process-tree execution for non-interactive bootstrapper commands."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence


DEFAULT_COMMAND_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
TERMINATION_GRACE_SECONDS = 2.0


class CommandLaunchError(RuntimeError):
    """A bounded process could not be launched safely."""


class CommandOutputTooLarge(RuntimeError):
    """A bounded process exceeded its combined output allowance."""


class _CommandInterrupted(SystemExit):
    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(128 + signum)


def _signal_process_tree(process: subprocess.Popen[bytes], signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _capture_stream(
    stream: BinaryIO,
    chunks: list[bytes],
    *,
    state: list[int],
    lock: threading.Lock,
    overflow: threading.Event,
    max_output_bytes: int,
) -> None:
    while not overflow.is_set():
        chunk = stream.read(64 * 1024)
        if not chunk:
            return
        with lock:
            remaining = max_output_bytes - state[0]
            if remaining <= 0:
                overflow.set()
                return
            chunks.append(chunk[:remaining])
            state[0] += min(len(chunk), remaining)
            if len(chunk) > remaining:
                overflow.set()
                return


def _stop_and_reap(
    process: subprocess.Popen[bytes], *, termination_grace_seconds: float
) -> None:
    """Give the whole group a grace period, then kill all survivors."""
    _signal_process_tree(process, signal.SIGTERM)
    deadline = time.monotonic() + termination_grace_seconds
    while time.monotonic() < deadline:
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    # The leader may already be gone while a TERM-resistant descendant remains.
    # Always escalate against the process group after the complete grace period.
    _signal_process_tree(process, signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive fallback
        process.kill()
        process.wait()


def run_with_deadline(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    termination_grace_seconds: float = TERMINATION_GRACE_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Capture a command with finite time/output bounds and no escaped children."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    if termination_grace_seconds <= 0:
        raise ValueError("termination_grace_seconds must be positive")
    if os.name != "posix":
        raise CommandLaunchError(
            "bounded process-tree execution requires POSIX or Windows WSL"
        )

    process: subprocess.Popen[bytes] | None = None
    pending_sigterm: list[int] = []
    guard_sigterm = threading.current_thread() is threading.main_thread()
    previous_sigterm = None
    readers: list[threading.Thread] = []
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    overflow = threading.Event()

    if guard_sigterm:
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def interrupt(signum, _frame):
            if not pending_sigterm:
                pending_sigterm.append(signum)

        signal.signal(signal.SIGTERM, interrupt)

    try:
        if pending_sigterm:
            raise _CommandInterrupted(pending_sigterm[0])
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            if pending_sigterm:
                raise _CommandInterrupted(pending_sigterm[0]) from exc
            raise CommandLaunchError from exc
        if pending_sigterm:
            raise _CommandInterrupted(pending_sigterm[0])

        assert process.stdout is not None
        assert process.stderr is not None
        state = [0]
        lock = threading.Lock()
        readers = [
            threading.Thread(
                target=_capture_stream,
                args=(stream, chunks),
                kwargs={
                    "state": state,
                    "lock": lock,
                    "overflow": overflow,
                    "max_output_bytes": max_output_bytes,
                },
                daemon=True,
            )
            for stream, chunks in (
                (process.stdout, stdout_chunks),
                (process.stderr, stderr_chunks),
            )
        ]
        for reader in readers:
            reader.start()

        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None or any(reader.is_alive() for reader in readers):
            if pending_sigterm:
                raise _CommandInterrupted(pending_sigterm[0])
            if overflow.is_set():
                raise CommandOutputTooLarge
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            time.sleep(0.01)

        # Successful leaders are not allowed to leave daemonized descendants.
        _signal_process_tree(process, signal.SIGKILL)
        if pending_sigterm:
            raise _CommandInterrupted(pending_sigterm[0])
    except BaseException:
        if process is not None:
            _stop_and_reap(
                process,
                termination_grace_seconds=termination_grace_seconds,
            )
        raise
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
        for reader in readers:
            reader.join(timeout=5)

    assert process is not None
    if overflow.is_set():
        raise CommandOutputTooLarge
    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
