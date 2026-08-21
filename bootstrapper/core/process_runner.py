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
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - D-state leader
            # The leader is wedged in uninterruptible sleep (frozen mount, NFS,
            # kernel lock); SIGKILL takes effect only once it leaves D-state.
            # Don't block the deadline/SIGTERM path indefinitely — the OS reaps
            # it when it wakes, and Popen.__del__ closes the pipes.
            pass


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
                f"Process cleanup failed during signal handling: {error!r}",
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


def _report_signal_dispatch_failure(error: BaseException) -> None:
    try:
        print(
            f"Deferred signal dispatch failed: {error!r}",
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
    """Make a main-thread owner clean worker-launched groups on SIGTERM/SIGHUP."""
    if os.name != "posix":
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("SIGTERM process cleanup must be owned by the main thread")
    # SIGHUP (terminal close / SSH disconnect) is the most common way a long
    # running command loses its parent. Children launched with start_new_session
    # do not receive the parent's SIGHUP, so without an explicit handler they are
    # re-parented to init and keep running — exactly the leak this module exists
    # to prevent. SIGKILL of the parent remains an unavoidable leak.
    managed_signals = (signal.SIGTERM, signal.SIGHUP)
    previous = {sig: signal.getsignal(sig) for sig in managed_signals}

    def cleanup(signum, frame):
        _report_registered_cleanup_failures(_stop_registered_processes())
        prior = previous[signum]
        if callable(prior):
            prior(signum, frame)
            return
        if prior == signal.SIG_IGN:
            return
        raise _CommandInterrupted(signum)

    for sig in managed_signals:
        signal.signal(sig, cleanup)
    try:
        yield
    finally:
        # Isolated per signal, with a SIG_DFL fallback. This is the OUTERMOST
        # guard — it wraps the whole run — and an unguarded restore here is
        # doubly bad: a TypeError raised inside a `finally` REPLACES whatever
        # the body was raising (a real `_CommandInterrupted`, a genuine error),
        # and it skips the remaining signals, stranding them on this dead
        # closure. `previous[sig]` is None when the prior handler came from
        # outside Python's signal module.
        for sig in managed_signals:
            if signal.getsignal(sig) is not cleanup:
                continue
            try:
                signal.signal(sig, previous[sig])
            except (TypeError, ValueError, OSError) as restore_error:
                try:
                    signal.signal(sig, signal.SIG_DFL)
                except (TypeError, ValueError, OSError):
                    _report_signal_dispatch_failure(restore_error)


#: Signals deferred across the launch window. Must match the set
#: `cleanup_active_processes_on_sigterm` manages: deferring SIGTERM but not
#: SIGHUP left the exact leak this module exists to prevent — a SIGHUP landing
#: between `Popen` and the registry insert ran cleanup against a registry that
#: did not yet contain the just-forked group, so it killed nothing, raised, and
#: the child group was re-parented to init.
#:
#: Built defensively: `signal.SIGHUP` does not exist on Windows, and this is a
#: MODULE-level constant, so naming it unconditionally would raise
#: AttributeError at import and take the whole bootstrapper down on that
#: platform. `cleanup_active_processes_on_sigterm` can name it directly because
#: it returns early on `os.name != "posix"` before reaching that line.
_GUARDED_SIGNALS = tuple(
    sig
    for sig in (getattr(signal, name, None) for name in ("SIGTERM", "SIGHUP"))
    if sig is not None
)


def _is_guard_handler(handler) -> bool:
    """True when `handler` is some `_SigtermGuard`'s deferred-signal recorder.

    Restoring one of these hands the signal to a guard that has already exited,
    whose `pending` list nobody will ever drain.
    """
    return getattr(handler, "__func__", None) is _SigtermGuard._interrupt


class _SigtermGuard:
    """Preserve the guarded signals across the main-thread Popen launch window."""

    def __init__(self) -> None:
        self.pending: list[tuple[int, FrameType | None]] = []
        self.previous: dict[int, object] = {}
        self.installed = None
        self.relay_target: dict[int, object] = {}

    def __enter__(self) -> _SigtermGuard:
        if threading.current_thread() is threading.main_thread():
            self.installed = self._interrupt
            for sig in _GUARDED_SIGNALS:
                self.previous[sig] = signal.getsignal(sig)
                self.relay_target[sig] = self.previous[sig]
                signal.signal(sig, self.installed)
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> None:
        try:
            self._drain_pending(finish=True)
        except BaseException as dispatch_error:
            if exc_type is None:
                raise
            _report_signal_dispatch_failure(dispatch_error)

    def _interrupt(self, signum: int, frame: FrameType | None) -> None:
        # Dedup per SIGNAL, not globally. A single shared slot was correct when
        # only SIGTERM was guarded — it collapsed repeat SIGTERMs — but with two
        # distinct guarded signals it makes whichever arrives first swallow the
        # other. A terminal drop (SIGHUP) followed by systemd's SIGTERM inside
        # the launch window would otherwise lose the SIGTERM entirely, and the
        # bootstrapper would keep orchestrating after being told to stop.
        if not any(pending_signum == signum for pending_signum, _ in self.pending):
            self.pending.append((signum, frame))

    def _dispatch_pending(self, handler) -> None:
        signum, frame = self.pending.pop(0)
        if callable(handler):
            handler(signum, frame)
        elif handler != signal.SIG_IGN:
            raise _CommandInterrupted(signum)

    def _record_dispatch_error(
        self, handler, errors: list[BaseException]
    ) -> None:
        try:
            self._dispatch_pending(handler)
        except BaseException as error:
            errors.append(error)

    def _restore_unowned(
        self, started_as_owner: dict, *, finish: bool, borrowed_from: dict | None = None
    ) -> None:
        """Hand each guarded signal back to whoever held it before us.

        Isolated per signal: `signal.signal(sig, None)` raises TypeError when
        the prior handler was installed outside Python's `signal` module, so
        `getsignal` returned None. With one guarded signal that could only fail
        wholesale; with two, an unisolated loop would abort partway and leave
        the other signal pointed at a dead guard's handler forever.
        """
        for sig in _GUARDED_SIGNALS:
            if signal.getsignal(sig) is self.installed and (
                finish or not started_as_owner.get(sig, False)
            ):
                # Give a borrowed signal back to the LIVE guard we took it from,
                # not to our own pre-nesting original.
                target = (borrowed_from or {}).get(sig, self.relay_target.get(sig))
                try:
                    signal.signal(sig, target)
                except (TypeError, ValueError, OSError) as restore_error:
                    # Swallowing here would be WORSE than failing: the signal
                    # stays bound to this dead guard's `_interrupt`, which
                    # appends to a `pending` list nobody will ever drain, so the
                    # process silently stops responding to SIGTERM/SIGHUP for
                    # the rest of its life — `docker stop` would do nothing
                    # until the SIGKILL. Default disposition is always safer
                    # than a dead guard.
                    try:
                        signal.signal(sig, signal.SIG_DFL)
                    except (TypeError, ValueError, OSError):
                        _report_signal_dispatch_failure(restore_error)

    def _drain_pending(self, *, finish: bool) -> None:
        started_as_owner = {
            sig: signal.getsignal(sig) is self.installed for sig in _GUARDED_SIGNALS
        }
        errors: list[BaseException] = []
        borrowed_from: dict = {}
        while True:
            while self.pending:
                sig = self.pending[0][0]
                if signal.getsignal(sig) is not self.installed:
                    displaced = signal.signal(sig, self.installed)
                    # Adopting the displaced handler is DELIBERATE — it is how
                    # a newer external handler installed mid-window survives
                    # (see test_sigterm_guard_preserves_newer_external_handler).
                    # The one thing never to adopt is ANOTHER GUARD's
                    # `_interrupt`: under nesting that makes the inner guard's
                    # recorder the outer guard's "original", so the outer guard
                    # later restores a dead closure whose `pending` list nobody
                    # drains. The process then stops responding to SIGTERM with
                    # no restore ever failing, so the SIG_DFL fallback never
                    # fires either.
                    if _is_guard_handler(displaced):
                        # A LIVE inner guard owns this signal. Do not adopt its
                        # handler as our relay target (restoring it later would
                        # hand the signal to a guard that has exited), but do
                        # give ownership BACK when this drain finishes —
                        # otherwise the inner guard is silently disarmed for the
                        # rest of its window and a signal landing between its
                        # Popen and the registry insert is delivered instead of
                        # deferred, which is the escaped-process-group leak the
                        # guard exists to prevent.
                        borrowed_from[sig] = displaced
                    else:
                        self.relay_target[sig] = displaced
                self._record_dispatch_error(self.relay_target.get(sig), errors)
            self._restore_unowned(started_as_owner, finish=finish,
                                  borrowed_from=borrowed_from)
            if not self.pending:
                break
        for secondary_error in errors[1:]:
            _report_signal_dispatch_failure(secondary_error)
        if errors:
            raise errors[0]

    def raise_if_pending(self) -> None:
        if self.pending:
            self._drain_pending(finish=False)


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
