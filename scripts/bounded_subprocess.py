"""Run repository audit commands with a deadline and redacted failures."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Sequence


DEFAULT_TIMEOUT_SECONDS = 300


class CommandTimedOut(RuntimeError):
    """A bounded subprocess exceeded its deadline."""


def run_bounded(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Capture output so credentials and private registry URLs stay private."""
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandTimedOut from exc


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
    if result.returncode != 0:
        print(redacted_failure(args.label, result.returncode))
        return result.returncode
    if result.stdout:
        print(result.stdout, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
