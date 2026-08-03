"""Run repository audit commands with a deadline and redacted failures."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence


DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024 * 1024


class CommandTimedOut(RuntimeError):
    """A bounded subprocess exceeded its deadline."""


class CommandLaunchError(RuntimeError):
    """A bounded subprocess could not be launched."""


class CommandOutputTooLarge(RuntimeError):
    """A bounded subprocess exceeded its combined output allowance."""


class _CommandInterrupted(SystemExit):
    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(128 + signum)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Force-stop the bounded command and descendants without leaking output."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
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


def _stop_and_reap(process: subprocess.Popen[bytes]) -> None:
    _terminate_process_tree(process)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive fallback
        process.kill()
        process.wait()


def run_bounded(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> subprocess.CompletedProcess[str]:
    """Run a process group with captured output and a finite deadline."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    if os.name != "posix":
        raise CommandLaunchError(
            "bounded process-tree execution requires POSIX or Windows WSL"
        )
    process: subprocess.Popen[bytes] | None = None
    pending_sigterm: list[int] = []
    guard_sigterm = threading.current_thread() is threading.main_thread()
    previous_sigterm = None
    if guard_sigterm:
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def interrupt(signum, _frame):
            if not pending_sigterm:
                pending_sigterm.append(signum)

        signal.signal(signal.SIGTERM, interrupt)

    readers: list[threading.Thread] = []
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    overflow = threading.Event()
    try:
        if pending_sigterm:
            raise _CommandInterrupted(pending_sigterm[0])
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
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
        while process.poll() is None or any(
            reader.is_alive() for reader in readers
        ):
            if pending_sigterm:
                raise _CommandInterrupted(pending_sigterm[0])
            if overflow.is_set():
                raise CommandOutputTooLarge
            if time.monotonic() >= deadline:
                raise CommandTimedOut
            time.sleep(0.01)
        # A successful leader may have daemonized children after redirecting
        # inherited pipes. This helper never permits intentional daemonization.
        _terminate_process_tree(process)
        if pending_sigterm:
            raise _CommandInterrupted(pending_sigterm[0])
    except BaseException:
        if process is not None:
            _stop_and_reap(process)
        raise
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
        for reader in readers:
            reader.join(timeout=5)
    assert process is not None
    if pending_sigterm:
        raise _CommandInterrupted(pending_sigterm[0])
    if overflow.is_set():
        raise CommandOutputTooLarge
    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def redacted_failure(label: str, returncode: int) -> str:
    return (
        f"{label} failed (exit {returncode}; subprocess output redacted)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument(
        "--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--forward-stderr",
        action="store_true",
        help="forward successful stderr (use only for non-secret build logs)",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    try:
        result = run_bounded(
            command,
            cwd=args.cwd,
            timeout_seconds=args.timeout_seconds,
        )
    except CommandTimedOut:
        print(f"{args.label} timed out after {args.timeout_seconds} seconds")
        return 124
    except CommandLaunchError:
        print(f"{args.label} could not start (subprocess details redacted)")
        return 126
    except CommandOutputTooLarge:
        print(
            f"{args.label} exceeded its output limit "
            "(subprocess output redacted)"
        )
        return 125
    except _CommandInterrupted as exc:
        print(f"{args.label} interrupted (subprocess details redacted)")
        return 128 + exc.signum
    except KeyboardInterrupt:
        print(f"{args.label} interrupted (subprocess details redacted)")
        return 130
    if result.returncode != 0:
        print(redacted_failure(args.label, result.returncode))
        return result.returncode
    if result.stdout:
        print(result.stdout, end="")
    if args.forward_stderr and result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
