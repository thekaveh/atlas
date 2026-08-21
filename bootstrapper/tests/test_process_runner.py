from __future__ import annotations

import asyncio
from collections import deque
from decimal import Decimal
from fractions import Fraction
import math
import io
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core import process_runner


ROOT = Path(__file__).resolve().parents[2]


class _IntSubclass(int):
    pass


class _FloatSubclass(float):
    pass


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


@pytest.mark.parametrize(
    ("bound_name", "value"),
    [
        ("timeout_seconds", math.nan),
        ("timeout_seconds", math.inf),
        ("timeout_seconds", Decimal("1")),
        ("timeout_seconds", True),
        ("timeout_seconds", "1"),
        ("timeout_seconds", b"1"),
        ("timeout_seconds", 1 + 0j),
        ("timeout_seconds", Fraction(1, 1)),
        ("timeout_seconds", _IntSubclass(1)),
        ("timeout_seconds", _FloatSubclass(1.0)),
        pytest.param("timeout_seconds", 10**10000, id="timeout-huge-int"),
        ("termination_grace_seconds", math.nan),
        ("termination_grace_seconds", math.inf),
        ("termination_grace_seconds", Decimal("1")),
        ("termination_grace_seconds", True),
        ("termination_grace_seconds", "1"),
        ("termination_grace_seconds", b"1"),
        ("termination_grace_seconds", 1 + 0j),
        ("termination_grace_seconds", Fraction(1, 1)),
        ("termination_grace_seconds", _IntSubclass(1)),
        ("termination_grace_seconds", _FloatSubclass(1.0)),
        pytest.param(
            "termination_grace_seconds", 10**10000, id="grace-huge-int"
        ),
    ],
)
def test_run_with_deadline_rejects_nonfinite_bounds_before_launch(
    monkeypatch, bound_name: str, value: float
) -> None:
    def unexpected_launch(*_args, **_kwargs):
        raise AssertionError("non-finite bounds must fail before launch")

    monkeypatch.setattr(process_runner.subprocess, "Popen", unexpected_launch)
    with pytest.raises(ValueError, match=bound_name):
        process_runner.run_with_deadline(["unused"], **{bound_name: value})


@pytest.mark.parametrize(
    "value",
    [
        math.nan,
        math.inf,
        1.5,
        True,
        _IntSubclass(1),
        pytest.param(10**10000, id="huge-int"),
    ],
)
def test_run_with_deadline_rejects_invalid_output_limit_before_launch(
    monkeypatch, value
) -> None:
    def unexpected_launch(*_args, **_kwargs):
        raise AssertionError("invalid output limits must fail before launch")

    monkeypatch.setattr(process_runner.subprocess, "Popen", unexpected_launch)
    with pytest.raises(ValueError, match="max_output_bytes"):
        process_runner.run_with_deadline(["unused"], max_output_bytes=value)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_run_with_deadline_propagates_reader_failure_and_cleans_up(
    monkeypatch, cleanup_fails: bool
) -> None:
    cleanup_calls: list[object] = []

    class FailingStream:
        def read(self, _size: int) -> bytes:
            raise OSError("simulated pipe read failure")

    class FakeProcess:
        pid = 12345
        stdout = FailingStream()
        stderr = io.BytesIO()
        returncode = None

        def poll(self):
            return self.returncode

    process = FakeProcess()

    def fake_popen(*_args, **_kwargs):
        return process

    def fake_cleanup(stopped_process, **_kwargs):
        cleanup_calls.append(stopped_process)
        if cleanup_fails:
            raise RuntimeError("simulated cleanup failure")
        stopped_process.returncode = -signal.SIGKILL

    monkeypatch.setattr(process_runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(process_runner, "_stop_and_reap", fake_cleanup)

    with pytest.raises(OSError, match="pipe read failure") as raised:
        process_runner.run_with_deadline(
            ["unused"], timeout_seconds=0.05, termination_grace_seconds=0.05
        )

    assert cleanup_calls == [process]
    if cleanup_fails:
        assert isinstance(raised.value.__cause__, RuntimeError)


@pytest.mark.parametrize("join_fails", [False, True])
def test_run_with_deadline_preserves_second_reader_start_failure(
    monkeypatch, join_fails
) -> None:
    cleanup_calls: list[object] = []
    created_threads: list[object] = []
    reported_join_failures: list[BaseException] = []

    class FakeThread:
        def __init__(self, **_kwargs):
            self.number = len(created_threads) + 1
            self.started = False
            self.joined = False
            created_threads.append(self)

        def start(self):
            if self.number == 2:
                raise RuntimeError("simulated second reader start failure")
            self.started = True

        def join(self, **_kwargs):
            if not self.started:
                raise RuntimeError("cannot join thread before it is started")
            self.joined = True
            if join_fails:
                raise OSError("simulated reader join failure")
        def is_alive(self):
            return False
    class FakeProcess:
        pid = 12345
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        returncode = None
    process = FakeProcess()

    monkeypatch.setattr(
        process_runner.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(process_runner.threading, "Thread", FakeThread)

    def fake_cleanup(stopped_process, **_kwargs):
        cleanup_calls.append(stopped_process)
        stopped_process.returncode = -signal.SIGKILL

    monkeypatch.setattr(process_runner, "_stop_and_reap", fake_cleanup)
    monkeypatch.setattr(
        process_runner,
        "_report_capture_join_failure",
        reported_join_failures.append,
    )

    with pytest.raises(RuntimeError, match="second reader start failure"):
        process_runner.run_with_deadline(["unused"])

    assert cleanup_calls == [process]
    assert len(created_threads) == 2
    assert created_threads[0].joined is True
    assert created_threads[1].joined is False
    assert [str(error) for error in reported_join_failures] == (
        ["simulated reader join failure"] if join_fails else []
    )


def test_registered_cleanup_attempts_every_process_after_failure(
    monkeypatch,
) -> None:
    attempts: list[int] = []

    class FakeProcess:
        def __init__(self, pid: int):
            self.pid = pid

    first = FakeProcess(1)
    second = FakeProcess(2)
    monkeypatch.setattr(
        process_runner,
        "_ACTIVE_PROCESSES",
        {
            first.pid: process_runner._ActiveProcess(first, 0.05),
            second.pid: process_runner._ActiveProcess(second, 0.05),
        },
    )

    def fake_cleanup(process, **_kwargs):
        attempts.append(process.pid)
        if process.pid == first.pid:
            raise OSError("simulated first cleanup failure")

    monkeypatch.setattr(process_runner, "_stop_and_reap", fake_cleanup)

    errors = process_runner._stop_registered_processes()

    assert attempts == [first.pid, second.pid]
    assert len(errors) == 1
    assert isinstance(errors[0], OSError)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_cleanup_reports_failures_and_preserves_exit(monkeypatch) -> None:
    cleanup_error = OSError("simulated registered cleanup failure")
    reported: list[BaseException] = []
    monkeypatch.setattr(
        process_runner, "_stop_registered_processes", lambda: [cleanup_error]
    )
    monkeypatch.setattr(
        process_runner,
        "_report_registered_cleanup_failures",
        lambda errors: reported.extend(errors),
    )
    with process_runner.cleanup_active_processes_on_sigterm():
        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(SystemExit) as raised:
            handler(signal.SIGTERM, None)

    assert raised.value.code == 128 + signal.SIGTERM
    assert reported == [cleanup_error]


@pytest.mark.skipif(os.name != "posix", reason="SIGHUP is POSIX-only")
def test_sighup_cleanup_installs_dispatches_and_restores(monkeypatch) -> None:
    # SIGHUP (terminal close / SSH disconnect) must be cleaned up like SIGTERM,
    # or start_new_session children are orphaned when the parent process is lost.
    cleanup_error = OSError("simulated registered cleanup failure")
    reported: list[BaseException] = []
    monkeypatch.setattr(
        process_runner, "_stop_registered_processes", lambda: [cleanup_error]
    )
    monkeypatch.setattr(
        process_runner,
        "_report_registered_cleanup_failures",
        lambda errors: reported.extend(errors),
    )
    previous = signal.getsignal(signal.SIGHUP)
    try:
        with process_runner.cleanup_active_processes_on_sigterm():
            handler = signal.getsignal(signal.SIGHUP)
            assert callable(handler)
            with pytest.raises(SystemExit) as raised:
                handler(signal.SIGHUP, None)
            assert raised.value.code == 128 + signal.SIGHUP
        assert reported == [cleanup_error]
        # Restored to the prior disposition on context exit.
        assert signal.getsignal(signal.SIGHUP) is previous
    finally:
        signal.signal(signal.SIGHUP, previous)


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


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_run_with_deadline_preserves_sigterm_during_final_join(monkeypatch) -> None:
    real_join = process_runner._Capture.join

    def interrupted_join(capture):
        real_join(capture)
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(process_runner._Capture, "join", interrupted_join)

    with pytest.raises(SystemExit) as raised:
        process_runner.run_with_deadline([sys.executable, "-c", "pass"])

    assert raised.value.code == 128 + signal.SIGTERM


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_does_not_mask_active_exception() -> None:
    with pytest.raises(RuntimeError, match="primary failure"):
        with process_runner._SigtermGuard():
            os.kill(os.getpid(), signal.SIGTERM)
            raise RuntimeError("primary failure")


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_honors_ignored_handler() -> None:
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with process_runner._SigtermGuard():
            os.kill(os.getpid(), signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_replays_callable_handler() -> None:
    received: list[int] = []
    previous = signal.signal(
        signal.SIGTERM,
        lambda signum, _frame: received.append(signum),
    )
    try:
        with process_runner._SigtermGuard():
            os.kill(os.getpid(), signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert received == [signal.SIGTERM]


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
@pytest.mark.parametrize("replacement", [signal.SIG_DFL, signal.SIG_IGN])
@pytest.mark.parametrize("dispatch_before_exit", [False, True])
def test_sigterm_guard_preserves_replayed_handler_replacement(
    replacement, dispatch_before_exit
) -> None:
    def replacing_handler(_signum, _frame):
        signal.signal(signal.SIGTERM, replacement)

    previous = signal.signal(signal.SIGTERM, replacing_handler)
    try:
        with process_runner._SigtermGuard() as guard:
            os.kill(os.getpid(), signal.SIGTERM)
            if dispatch_before_exit:
                guard.raise_if_pending()

        assert signal.getsignal(signal.SIGTERM) is replacement
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
@pytest.mark.parametrize("newer_kind", ["callable", "default", "ignore"])
@pytest.mark.parametrize("old_action", ["return", "replace", "raise"])
def test_sigterm_guard_preserves_newer_external_handler(
    newer_kind, old_action
) -> None:
    old_received: list[int] = []
    newer_received: list[int] = []

    def old_handler(_signum, _frame):
        old_received.append(_signum)
        if old_action == "replace":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        elif old_action == "raise":
            raise OSError("old handler failure")

    def newer_handler(signum, _frame):
        newer_received.append(signum)

    newer = {
        "callable": newer_handler,
        "default": signal.SIG_DFL,
        "ignore": signal.SIG_IGN,
    }[newer_kind]
    previous = signal.signal(signal.SIGTERM, old_handler)
    try:
        if newer_kind == "default":
            with pytest.raises(SystemExit) as raised:
                with process_runner._SigtermGuard():
                    os.kill(os.getpid(), signal.SIGTERM)
                    signal.signal(signal.SIGTERM, newer)
            assert raised.value.code == 128 + signal.SIGTERM
        else:
            with process_runner._SigtermGuard():
                os.kill(os.getpid(), signal.SIGTERM)
                signal.signal(signal.SIGTERM, newer)

        assert old_received == []
        assert newer_received == (
            [signal.SIGTERM] if newer_kind == "callable" else []
        )
        assert signal.getsignal(signal.SIGTERM) is newer
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_preserves_newer_handler_and_active_error(capsys) -> None:
    newer_received: list[int] = []

    def old_handler(_signum, _frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise OSError("old handler failure")

    def newer_handler(signum, _frame):
        newer_received.append(signum)

    previous = signal.signal(signal.SIGTERM, old_handler)
    try:
        with pytest.raises(RuntimeError, match="primary failure"):
            with process_runner._SigtermGuard():
                os.kill(os.getpid(), signal.SIGTERM)
                signal.signal(signal.SIGTERM, newer_handler)
                raise RuntimeError("primary failure")

        assert signal.getsignal(signal.SIGTERM) is newer_handler
        assert newer_received == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert capsys.readouterr().err == ""


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
@pytest.mark.parametrize("old_raises", [False, True])
def test_sigterm_guard_dispatches_pending_signal_to_newer_owner(old_raises) -> None:
    received: list[str] = []

    def old_handler(_signum, _frame):
        received.append("old")
        os.kill(os.getpid(), signal.SIGTERM)
        if old_raises:
            raise OSError("old handler failure")

    def newer_handler(_signum, _frame):
        received.append("newer")
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    previous = signal.signal(signal.SIGTERM, old_handler)
    try:
        with process_runner._SigtermGuard():
            os.kill(os.getpid(), signal.SIGTERM)
            signal.signal(signal.SIGTERM, newer_handler)

        assert received == ["newer"]
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_serializes_signal_during_handler_replacement() -> None:
    received: list[str] = []

    def newer_handler(_signum, _frame):
        received.append("newer")

    def old_handler(_signum, _frame):
        received.append("old")
        if len(received) == 1:
            os.kill(os.getpid(), signal.SIGTERM)
            signal.signal(signal.SIGTERM, newer_handler)

    previous = signal.signal(signal.SIGTERM, old_handler)
    try:
        with process_runner._SigtermGuard():
            os.kill(os.getpid(), signal.SIGTERM)

        deadline = time.monotonic() + 1
        while len(received) < 2 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert tuple(received) == ("old", "newer")
        assert signal.getsignal(signal.SIGTERM) is newer_handler
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_remembers_reentrant_owner_across_early_drains() -> None:
    received: list[str] = []

    def newer_handler(_signum, _frame):
        received.append("newer")

    def old_handler(_signum, _frame):
        received.append("old")
        os.kill(os.getpid(), signal.SIGTERM)
        signal.signal(signal.SIGTERM, newer_handler)

    previous = signal.signal(signal.SIGTERM, old_handler)
    try:
        with process_runner._SigtermGuard() as guard:
            os.kill(os.getpid(), signal.SIGTERM)
            guard.raise_if_pending()
            os.kill(os.getpid(), signal.SIGTERM)
            guard.raise_if_pending()

        assert received == ["old", "newer", "newer"]
        assert signal.getsignal(signal.SIGTERM) is newer_handler
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_serializes_signal_during_early_owner_lookup(
    monkeypatch,
) -> None:
    received: list[str] = []
    real_getsignal = signal.getsignal
    triggered = False

    def latest_handler(_signum, _frame):
        received.append("latest")

    def newer_handler(_signum, _frame):
        received.append("newer")
        signal.signal(signal.SIGTERM, latest_handler)

    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def interrupted_lookup(signum):
        nonlocal triggered
        current = real_getsignal(signum)
        if signum == signal.SIGTERM and current is newer_handler and not triggered:
            triggered = True
            os.kill(os.getpid(), signal.SIGTERM)
        return current

    monkeypatch.setattr(process_runner.signal, "getsignal", interrupted_lookup)
    try:
        with process_runner._SigtermGuard() as guard:
            os.kill(os.getpid(), signal.SIGTERM)
            signal.signal(signal.SIGTERM, newer_handler)
            guard.raise_if_pending()

        deadline = time.monotonic() + 1
        while len(received) < 2 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert tuple(received) == ("newer", "latest")
        assert real_getsignal(signal.SIGTERM) is latest_handler
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_callback_child_inherits_unblocked_sigterm() -> None:
    child_observations: list[str] = []

    def handler(_signum, _frame):
        child_observations.append(
            subprocess.check_output(
                [
                    sys.executable,
                    "-c",
                    "import signal; print(signal.SIGTERM in "
                    "signal.pthread_sigmask(signal.SIG_BLOCK, set()))",
                ],
                text=True,
            ).strip()
        )

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        with process_runner._SigtermGuard() as guard:
            os.kill(os.getpid(), signal.SIGTERM)
            guard.raise_if_pending()
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert child_observations == ["False"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_callback_thread_inherits_unblocked_sigterm() -> None:
    thread_observations: list[bool] = []

    def handler(_signum, _frame):
        worker = threading.Thread(
            target=lambda: thread_observations.append(
                signal.SIGTERM
                in signal.pthread_sigmask(signal.SIG_BLOCK, set())
            )
        )
        worker.start()
        worker.join(timeout=1)

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        with process_runner._SigtermGuard() as guard:
            os.kill(os.getpid(), signal.SIGTERM)
            guard.raise_if_pending()
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert thread_observations == [False]


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_replays_handler_without_masking_active_error(capsys) -> None:
    received: list[int] = []

    def failing_handler(signum, _frame):
        received.append(signum)
        raise OSError("handler failure")

    previous = signal.signal(signal.SIGTERM, failing_handler)
    try:
        with pytest.raises(RuntimeError, match="primary failure"):
            with process_runner._SigtermGuard():
                os.kill(os.getpid(), signal.SIGTERM)
                raise RuntimeError("primary failure")
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert received == [signal.SIGTERM]
    assert "handler failure" in capsys.readouterr().err


def test_signal_dispatch_diagnostic_tolerates_broken_stderr(monkeypatch) -> None:
    class BrokenStderr:
        def write(self, _message):
            raise OSError("stderr unavailable")

    monkeypatch.setattr(process_runner.sys, "stderr", BrokenStderr())
    process_runner._report_signal_dispatch_failure(OSError("handler failure"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal contract")
def test_sigterm_guard_replays_outer_cleanup_handler(monkeypatch) -> None:
    cleanup_calls: list[bool] = []
    monkeypatch.setattr(
        process_runner,
        "_stop_registered_processes",
        lambda: cleanup_calls.append(True) or [],
    )

    with pytest.raises(SystemExit) as raised:
        with process_runner.cleanup_active_processes_on_sigterm():
            with process_runner._SigtermGuard():
                os.kill(os.getpid(), signal.SIGTERM)

    assert raised.value.code == 128 + signal.SIGTERM
    assert cleanup_calls == [True]


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
    assert source.count("proc = await _launch_process(") == 1
    assert source.count("await _run_streamed_command(") == 2
    assert "_FAILURE_HINT_BUFFER_BYTES =" in source
    assert source.count("await _stop_process_tree(") >= 3
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


def test_streamed_command_rejects_invalid_grace_before_launch(
    monkeypatch, tmp_path: Path
) -> None:
    from ui.textual.screens import wizard_screen

    async def unexpected_launch(*_args, **_kwargs):
        raise AssertionError("invalid cleanup bounds must fail before launch")

    monkeypatch.setattr(wizard_screen, "_launch_process", unexpected_launch)

    async def exercise() -> None:
        with pytest.raises(ValueError, match="termination_grace_seconds"):
            await wizard_screen._run_streamed_command(
                ["unused"],
                cwd=tmp_path,
                env=os.environ.copy(),
                on_line=lambda _line: None,
                timeout_seconds=1,
                termination_grace_seconds=0,
            )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("bound_name", "value"),
    [
        ("timeout_seconds", math.nan),
        ("timeout_seconds", math.inf),
        ("timeout_seconds", Decimal("1")),
        ("timeout_seconds", True),
        ("timeout_seconds", "1"),
        ("timeout_seconds", b"1"),
        ("timeout_seconds", 1 + 0j),
        ("timeout_seconds", Fraction(1, 1)),
        ("timeout_seconds", _IntSubclass(1)),
        ("timeout_seconds", _FloatSubclass(1.0)),
        pytest.param("timeout_seconds", 10**10000, id="timeout-huge-int"),
        ("termination_grace_seconds", math.nan),
        ("termination_grace_seconds", math.inf),
        ("termination_grace_seconds", Decimal("1")),
        ("termination_grace_seconds", True),
        ("termination_grace_seconds", "1"),
        ("termination_grace_seconds", b"1"),
        ("termination_grace_seconds", 1 + 0j),
        ("termination_grace_seconds", Fraction(1, 1)),
        ("termination_grace_seconds", _IntSubclass(1)),
        ("termination_grace_seconds", _FloatSubclass(1.0)),
        pytest.param(
            "termination_grace_seconds", 10**10000, id="grace-huge-int"
        ),
    ],
)
def test_streamed_command_rejects_nonfinite_bounds_before_launch(
    monkeypatch, tmp_path: Path, bound_name: str, value: float
) -> None:
    from ui.textual.screens import wizard_screen

    async def unexpected_launch(*_args, **_kwargs):
        raise AssertionError("non-finite bounds must fail before launch")

    monkeypatch.setattr(wizard_screen, "_launch_process", unexpected_launch)
    kwargs = {
        "timeout_seconds": 1.0,
        "termination_grace_seconds": 0.05,
        bound_name: value,
    }

    async def exercise() -> None:
        with pytest.raises(ValueError, match=bound_name):
            await wizard_screen._run_streamed_command(
                ["unused"],
                cwd=tmp_path,
                env=os.environ.copy(),
                on_line=lambda _line: None,
                **kwargs,
            )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1"),
        Fraction(1, 1),
        True,
        "1",
        b"1",
        1 + 0j,
        _IntSubclass(1),
        _FloatSubclass(1.0),
        pytest.param(10**10000, id="huge-int"),
    ],
)
def test_async_stop_rejects_invalid_grace_before_signal(
    monkeypatch, value
) -> None:
    from ui.textual.screens import wizard_screen

    def unexpected_signal(*_args, **_kwargs):
        raise AssertionError("invalid grace must fail before signaling")

    class FakeProcess:
        pid = 12345

    monkeypatch.setattr(wizard_screen.os, "killpg", unexpected_signal)

    async def exercise() -> None:
        with pytest.raises(ValueError, match="termination_grace_seconds"):
            await wizard_screen._stop_process_tree(
                FakeProcess(), termination_grace_seconds=value
            )

    asyncio.run(exercise())


def test_streamed_cancellation_preserves_cancellation_when_cleanup_fails(
    monkeypatch, tmp_path: Path
) -> None:
    from ui.textual.screens import wizard_screen

    started = asyncio.Event()
    never = asyncio.Event()

    class WaitingStdout:
        def __aiter__(self):
            return self

        async def __anext__(self):
            started.set()
            await never.wait()
            raise StopAsyncIteration

    class FakeProcess:
        stdout = WaitingStdout()

        async def wait(self):
            await never.wait()
            return 0

    async def fake_launch(*_args, **_kwargs):
        return FakeProcess()

    async def failed_cleanup(*_args, **_kwargs):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(wizard_screen, "_launch_process", fake_launch)
    monkeypatch.setattr(wizard_screen, "_stop_process_tree", failed_cleanup)

    async def exercise() -> None:
        diagnostics: list[dict] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: diagnostics.append(context)
        )
        task = asyncio.create_task(
            wizard_screen._run_streamed_command(
                ["unused"],
                cwd=tmp_path,
                env=os.environ.copy(),
                on_line=lambda _line: None,
                timeout_seconds=None,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        # Python 3.10's Task boundary replaces CancelledError and drops its
        # explicit cause; 3.11+ preserves the cleanup diagnostic.
        if sys.version_info >= (3, 11):
            assert isinstance(raised.value.__cause__, OSError)
        assert len(diagnostics) == 1
        assert isinstance(diagnostics[0]["exception"], OSError)
        assert diagnostics[0]["task"] is not None

    asyncio.run(exercise())


def test_streamed_sink_failure_remains_primary_when_cleanup_fails(
    monkeypatch, tmp_path: Path
) -> None:
    from ui.textual.screens import wizard_screen

    class OneLineStdout:
        def __init__(self):
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            return b"trigger\n"

    class FakeProcess:
        stdout = OneLineStdout()

        async def wait(self):
            return 0

    async def fake_launch(*_args, **_kwargs):
        return FakeProcess()

    async def failed_cleanup(*_args, **_kwargs):
        raise OSError("simulated cleanup failure")

    def fail_sink(_line: str) -> None:
        raise RuntimeError("simulated sink failure")

    monkeypatch.setattr(wizard_screen, "_launch_process", fake_launch)
    monkeypatch.setattr(wizard_screen, "_stop_process_tree", failed_cleanup)

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="sink failure") as raised:
            await wizard_screen._run_streamed_command(
                ["unused"],
                cwd=tmp_path,
                env=os.environ.copy(),
                on_line=fail_sink,
                timeout_seconds=1,
            )
        assert isinstance(raised.value.__cause__, OSError)

    asyncio.run(exercise())


def test_failure_hint_buffer_has_a_byte_ceiling() -> None:
    from ui.textual.screens.wizard_screen import (
        _FAILURE_HINT_BUFFER_BYTES,
        _append_bounded_hint,
    )

    captured: deque[tuple[str, int]] = deque()
    captured_bytes = 0
    for suffix in ("old", "middle", "latest"):
        captured_bytes = _append_bounded_hint(
            captured,
            ("x" * _FAILURE_HINT_BUFFER_BYTES) + suffix,
            captured_bytes,
        )

    assert captured_bytes <= _FAILURE_HINT_BUFFER_BYTES
    assert sum(size for _line, size in captured) == captured_bytes
    assert captured[-1][0].endswith("latest")


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


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_streamed_command_sink_failure_reaps_process_group(tmp_path: Path) -> None:
    from ui.textual.screens.wizard_screen import _run_streamed_command

    marker = tmp_path / "streamed-sink-failure-descendant"
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
        "print('trigger', flush=True); time.sleep(1)"
    )

    def fail_sink(_line: str) -> None:
        raise RuntimeError("simulated log sink failure")

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="sink failure"):
            await _run_streamed_command(
                [sys.executable, "-c", leader, descendant],
                cwd=tmp_path,
                env=os.environ.copy(),
                on_line=fail_sink,
                timeout_seconds=10,
                termination_grace_seconds=0.05,
            )

    asyncio.run(exercise())
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_streamed_timeout_notice_sink_failure_reaps_process_group(
    tmp_path: Path,
) -> None:
    from ui.textual.screens.wizard_screen import _run_streamed_command

    marker = tmp_path / "streamed-timeout-sink-failure-descendant"
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

    def fail_sink(_line: str) -> None:
        raise OSError("simulated timeout-notice sink failure")

    async def exercise() -> None:
        with pytest.raises(OSError, match="timeout-notice sink failure"):
            await _run_streamed_command(
                [sys.executable, "-c", leader, descendant],
                cwd=tmp_path,
                env=os.environ.copy(),
                on_line=fail_sink,
                timeout_seconds=0.05,
                termination_grace_seconds=0.05,
            )

    asyncio.run(exercise())
    time.sleep(0.6)
    assert not marker.exists()


#: Raises SIGHUP between `Popen` and the registry insert, so the guard is
#: exercised at the exact window where a missed signal orphans the child.
_SIGHUP_RACE_SCRIPT = """
import os, signal, subprocess, sys, time
sys.path.insert(0, {bootstrapper_dir!r})
import core.process_runner as pr
real = subprocess.Popen
class Racing(real):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        open({marker!r}, "w").write(str(self.pid))
        os.kill(os.getpid(), signal.SIGHUP)
        time.sleep(0.05)
subprocess.Popen = Racing
# MUST be entered — a bare call returns the context manager without running
# its body, so no handler is installed and the test would assert the orphan
# property in the UNMANAGED configuration instead of the shipped one.
with pr.cleanup_active_processes_on_sigterm():
    try:
        pr.run_with_deadline([sys.executable, "-c", "import time; time.sleep(20)"],
                             timeout_seconds=5)
    except BaseException:
        pass
"""


def test_sighup_in_the_launch_window_does_not_orphan_the_child(tmp_path):
    """The guard must defer every signal `cleanup_...` manages, not just SIGTERM.

    `cleanup_active_processes_on_sigterm` handles (SIGTERM, SIGHUP), but the
    launch guard originally deferred only SIGTERM. A SIGHUP landing between
    `Popen` and the registry insert therefore ran cleanup against a registry
    that did not yet contain the just-forked group: it killed nothing, chained
    to the default action, killed the parent, and left the child re-parented to
    init — the exact leak this module's own comment says it exists to prevent.

    Measured before the fix: parent killed by signal 1, child ORPHANED. After:
    parent exits cleanly and the child is reaped.
    """
    import os
    import signal
    import subprocess
    import sys
    import time

    marker = tmp_path / "child.pid"
    _bootstrapper_dir = str(Path(__file__).resolve().parents[1])
    inner = _SIGHUP_RACE_SCRIPT.format(
        bootstrapper_dir=_bootstrapper_dir, marker=str(marker)
    )
    proc = subprocess.run(
        [sys.executable, "-c", inner], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        # Without this, a regression that stops the deferred signal dispatching
        # hangs CI forever instead of failing — in the test file for the module
        # whose entire purpose is bounding subprocesses.
        timeout=60,
    )
    time.sleep(0.4)

    assert marker.exists(), "the racing Popen never ran"
    child_pid = int(marker.read_text())
    orphaned = True
    try:
        os.kill(child_pid, 0)
    except OSError:
        orphaned = False
    if orphaned:  # never leave a stray process behind, even on failure
        try:
            os.killpg(child_pid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except OSError:
                pass

    assert not orphaned, "SIGHUP in the launch window orphaned the child group"
    assert proc.returncode >= 0, "parent was killed by the signal instead of deferring it"


def test_guarded_signals_survive_a_platform_without_sighup() -> None:
    """`_GUARDED_SIGNALS` is a MODULE-level constant.

    Naming `signal.SIGHUP` unconditionally there raises AttributeError at
    import on Windows and takes the entire bootstrapper down with it —
    `cleanup_active_processes_on_sigterm` can name it directly only because it
    returns early on `os.name != "posix"` before reaching that line.

    Checked in a SUBPROCESS rather than by reloading this module in-process:
    reload rebinds `_CommandInterrupted`, `_ACTIVE_PROCESSES` and its lock, and
    any module that captured them by value (`scripts/bounded_subprocess.py`
    does) would keep a stale object, so `except <stale class>` would stop
    catching the freshly-raised one.
    """
    import subprocess
    import sys

    probe = (
        "import signal, sys\n"
        "del signal.SIGHUP\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "import core.process_runner as pr\n"
        "print(','.join(s.name for s in pr._GUARDED_SIGNALS))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    assert result.returncode == 0, (
        f"process_runner does not import without signal.SIGHUP: "
        f"{result.stderr.strip()[-300:]}"
    )
    assert result.stdout.strip() == "SIGTERM", result.stdout

    # And both are guarded on this POSIX host.
    assert [sig.name for sig in process_runner._GUARDED_SIGNALS] == ["SIGTERM", "SIGHUP"]


def test_a_second_distinct_signal_is_not_swallowed_in_the_launch_window() -> None:
    """The pending queue dedups per SIGNAL, not globally.

    One shared slot was correct when only SIGTERM was guarded — it collapsed
    repeat SIGTERMs. With two distinct guarded signals it made whichever
    arrived first swallow the other: a terminal drop (SIGHUP) followed by
    systemd's SIGTERM inside the launch window lost the SIGTERM entirely, and
    the bootstrapper kept orchestrating after being told to stop.
    """
    import signal as signal_module

    guard = process_runner._SigtermGuard()
    guard._interrupt(signal_module.SIGHUP, None)
    guard._interrupt(signal_module.SIGTERM, None)

    recorded = [signum for signum, _ in guard.pending]
    assert recorded == [signal_module.SIGHUP, signal_module.SIGTERM], recorded

    # ...while a REPEAT of an already-pending signal still collapses.
    guard._interrupt(signal_module.SIGHUP, None)
    guard._interrupt(signal_module.SIGTERM, None)
    assert [signum for signum, _ in guard.pending] == recorded


def test_both_pending_signals_dispatch_even_when_the_first_handler_raises() -> None:
    """Two distinct guarded signals means the queue can hold two entries.

    The dispatch loop must run both, drain the queue, and hand BOTH signals
    back to their prior handlers — including on the path where the first
    handler raises and that error is re-raised out of `__exit__`.
    """
    import signal as signal_module

    ran: list[str] = []

    def failing_hup(_signum, _frame):
        ran.append("HUP")
        raise RuntimeError("handler failed")

    def recording_term(_signum, _frame):
        ran.append("TERM")

    previous_hup = signal_module.signal(signal_module.SIGHUP, failing_hup)
    previous_term = signal_module.signal(signal_module.SIGTERM, recording_term)
    try:
        guard = process_runner._SigtermGuard()
        with pytest.raises(RuntimeError, match="handler failed"):
            with guard:
                guard._interrupt(signal_module.SIGHUP, None)
                guard._interrupt(signal_module.SIGTERM, None)

        assert ran == ["HUP", "TERM"], ran
        assert not guard.pending, "queue not drained"
        assert signal_module.getsignal(signal_module.SIGHUP) is failing_hup
        assert signal_module.getsignal(signal_module.SIGTERM) is recording_term
    finally:
        signal_module.signal(signal_module.SIGHUP, previous_hup)
        signal_module.signal(signal_module.SIGTERM, previous_term)
