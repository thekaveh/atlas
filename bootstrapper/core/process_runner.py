"""Bounded process-tree execution for non-interactive bootstrapper commands."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import BinaryIO, Iterator, Mapping, Sequence


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


@dataclass(frozen=True)
class _ActiveProcess:
    process: subprocess.Popen[bytes]
    termination_grace_seconds: float


_ACTIVE_PROCESSES: dict[int, _ActiveProcess] = {}
_ACTIVE_PROCESSES_LOCK = threading.RLock()


def _signal_process_tree(process: subprocess.Popen[bytes], signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_and_reap(
    process: subprocess.Popen[bytes], *, termination_grace_seconds: float
) -> None:
    """Give the whole group a grace period, then kill all survivors."""
    _signal_process_tree(process, signal.SIGTERM)
    deadline = time.monotonic() + termination_grace_seconds
    while time.monotonic() < deadline:
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    _signal_process_tree(process, signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive fallback
        process.kill()
        process.wait()


def _stop_registered_processes() -> list[BaseException]:
    with _ACTIVE_PROCESSES_LOCK:
        active = tuple(_ACTIVE_PROCESSES.values())
    errors: list[BaseException] = []
    for item in active:
        try:
            _stop_and_reap(
                item.process,
                termination_grace_seconds=item.termination_grace_seconds,
            )
        except BaseException as exc:
            errors.append(exc)
    return errors


def _report_registered_cleanup_failures(errors: list[BaseException]) -> None:
    for error in errors:
        try:
            print(
                f"Process cleanup failed during SIGTERM: {error!r}",
                file=sys.stderr,
            )
        except BaseException:
            pass


def _report_capture_join_failure(error: BaseException) -> None:
    try:
        print(
            f"Process output reader cleanup failed: {error!r}",
            file=sys.stderr,
        )
    except BaseException:
        pass


def _join_capture_after_failure(capture: _Capture) -> None:
    try:
        capture.join()
    except BaseException as join_error:
        _report_capture_join_failure(join_error)


@contextmanager
def cleanup_active_processes_on_sigterm() -> Iterator[None]:
    """Make a main-thread owner clean worker-launched groups on SIGTERM."""
    if os.name != "posix":
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("SIGTERM process cleanup must be owned by the main thread")
    previous = signal.getsignal(signal.SIGTERM)

    def cleanup(signum, frame):
        _report_registered_cleanup_failures(_stop_registered_processes())
        if callable(previous):
            previous(signum, frame)
            return
        if previous == signal.SIG_IGN:
            return
        raise _CommandInterrupted(signum)

    signal.signal(signal.SIGTERM, cleanup)
    try:
        yield
    finally:
        if signal.getsignal(signal.SIGTERM) is cleanup:
            signal.signal(signal.SIGTERM, previous)


class _SigtermGuard:
    """Preserve SIGTERM across the main-thread Popen launch window."""

    def __init__(self) -> None:
        self.pending: list[tuple[int, FrameType | None]] = []
        self.previous = None

    def __enter__(self) -> _SigtermGuard:
        if threading.current_thread() is threading.main_thread():
            self.previous = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, self._interrupt)
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> None:
        if self.previous is not None:
            signal.signal(signal.SIGTERM, self.previous)
        if not self.pending:
            return
        if exc_type is None:
            self._dispatch_pending()
            return
        try:
            self._dispatch_pending()
        except BaseException:
            pass

    def _interrupt(self, signum: int, frame: FrameType | None) -> None:
        if not self.pending:
            self.pending.append((signum, frame))

    def _dispatch_pending(self) -> None:
        signum, frame = self.pending.pop(0)
        if callable(self.previous):
            self.previous(signum, frame)
        elif self.previous != signal.SIG_IGN:
            raise _CommandInterrupted(signum)

    def raise_if_pending(self) -> None:
        if self.pending:
            self._dispatch_pending()


def _capture_stream(
    stream: BinaryIO,
    chunks: list[bytes],
    *,
    state: list[int],
    lock: threading.Lock,
    overflow: threading.Event,
    failure: threading.Event,
    errors: list[BaseException],
    max_output_bytes: int,
) -> None:
    try:
        while not overflow.is_set() and not failure.is_set():
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
    except BaseException as exc:
        with lock:
            errors.append(exc)
            failure.set()


@dataclass
class _Capture:
    stdout_chunks: list[bytes] = field(default_factory=list)
    stderr_chunks: list[bytes] = field(default_factory=list)
    overflow: threading.Event = field(default_factory=threading.Event)
    failure: threading.Event = field(default_factory=threading.Event)
    errors: list[BaseException] = field(default_factory=list)
    readers: list[threading.Thread] = field(default_factory=list)

    def start(self, process: subprocess.Popen[bytes], max_output_bytes: int) -> None:
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
                    "overflow": self.overflow,
                    "failure": self.failure,
                    "errors": self.errors,
                    "max_output_bytes": max_output_bytes,
                },
                daemon=True,
            )
            for stream, chunks in (
                (process.stdout, self.stdout_chunks),
                (process.stderr, self.stderr_chunks),
            )
        ]
        self.readers = []
        for reader in readers:
            reader.start()
            self.readers.append(reader)

    def join(self) -> None:
        for reader in self.readers:
            reader.join(timeout=5)

    def completed(
        self, command: Sequence[str], returncode: int
    ) -> subprocess.CompletedProcess[str]:
        self.raise_if_failed()
        if self.overflow.is_set():
            raise CommandOutputTooLarge
        stdout = b"".join(self.stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(self.stderr_chunks).decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    def raise_if_failed(self) -> None:
        if self.failure.is_set():
            raise self.errors[0]


def _launch_registered(
    command: Sequence[str],
    *,
    cwd: str | Path | None,
    env: Mapping[str, str] | None,
    termination_grace_seconds: float,
) -> subprocess.Popen[bytes]:
    # Holding this lock closes the worker-thread launch/register signal race:
    # the main-thread SIGTERM handler waits until the new group is registered.
    with _ACTIVE_PROCESSES_LOCK:
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
            raise CommandLaunchError from exc
        _ACTIVE_PROCESSES[process.pid] = _ActiveProcess(
            process, termination_grace_seconds
        )
    return process


def _wait_for_completion(
    process: subprocess.Popen[bytes],
    capture: _Capture,
    guard: _SigtermGuard,
    command: Sequence[str],
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None or any(reader.is_alive() for reader in capture.readers):
        guard.raise_if_pending()
        capture.raise_if_failed()
        if capture.overflow.is_set():
            raise CommandOutputTooLarge
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        time.sleep(0.01)


def _validate_bounds(
    timeout_seconds: float,
    max_output_bytes: int,
    termination_grace_seconds: float,
) -> None:
    _require_positive_finite_real("timeout_seconds", timeout_seconds)
    if (
        type(max_output_bytes) is not int
        or max_output_bytes <= 0
        or max_output_bytes >= sys.maxsize
    ):
        raise ValueError("max_output_bytes must be a platform-sized positive integer")
    _require_positive_finite_real(
        "termination_grace_seconds", termination_grace_seconds
    )


def _require_positive_finite_real(name: str, value: object) -> None:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite positive int or float")
    try:
        finite = math.isfinite(float(value))
    except (OverflowError, ValueError):
        finite = False
    if not finite or value <= 0:
        raise ValueError(f"{name} must be a finite positive int or float")
    if os.name != "posix":
        raise CommandLaunchError(
            "bounded process-tree execution requires POSIX or Windows WSL"
        )


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
    _validate_bounds(timeout_seconds, max_output_bytes, termination_grace_seconds)
    process: subprocess.Popen[bytes] | None = None
    capture = _Capture()
    with _SigtermGuard() as guard:
        try:
            try:
                guard.raise_if_pending()
                try:
                    process = _launch_registered(
                        command,
                        cwd=cwd,
                        env=env,
                        termination_grace_seconds=termination_grace_seconds,
                    )
                except CommandLaunchError:
                    guard.raise_if_pending()
                    raise
                guard.raise_if_pending()
                capture.start(process, max_output_bytes)
                _wait_for_completion(process, capture, guard, command, timeout_seconds)
                _signal_process_tree(process, signal.SIGKILL)
                guard.raise_if_pending()
            except BaseException as primary_error:
                if process is not None:
                    try:
                        _stop_and_reap(
                            process,
                            termination_grace_seconds=termination_grace_seconds,
                        )
                    except BaseException as cleanup_error:
                        raise primary_error from cleanup_error
                raise
            finally:
                if process is not None:
                    with _ACTIVE_PROCESSES_LOCK:
                        _ACTIVE_PROCESSES.pop(process.pid, None)
        except BaseException:
            _join_capture_after_failure(capture)
            raise
        else:
            capture.join()
    assert process is not None
    return capture.completed(command, process.returncode)
