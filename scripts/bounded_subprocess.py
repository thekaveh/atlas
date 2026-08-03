"""Run repository audit commands with a deadline and redacted failures."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
from pathlib import Path
from typing import Mapping, Sequence


DEFAULT_TIMEOUT_SECONDS = 300


class CommandTimedOut(RuntimeError):
    """A bounded subprocess exceeded its deadline."""


class CommandLaunchError(RuntimeError):
    """A bounded subprocess could not be launched."""


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Force-stop the bounded command and descendants without leaking output."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:  # pragma: no cover - Windows CI is not currently used
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=5,
        )
        if process.poll() is None:
            process.kill()


def run_bounded(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run a process group with captured output and a finite deadline."""
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise CommandLaunchError from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        process.communicate()
        raise CommandTimedOut from exc
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
    if result.returncode != 0:
        print(redacted_failure(args.label, result.returncode))
        return result.returncode
    if result.stdout:
        print(result.stdout, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
