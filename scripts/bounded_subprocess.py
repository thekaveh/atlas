"""Run repository audit commands with a deadline and redacted failures."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

try:
    from bootstrapper.core import process_runner as _process_runner
except ModuleNotFoundError as exc:
    if exc.name != "bootstrapper":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bootstrapper.core import process_runner as _process_runner


DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
TERMINATION_GRACE_SECONDS = 0.05


class CommandTimedOut(RuntimeError):
    """A bounded subprocess exceeded its deadline."""


class CommandLaunchError(RuntimeError):
    """A bounded subprocess could not be launched."""


class CommandOutputTooLarge(RuntimeError):
    """A bounded subprocess exceeded its combined output allowance."""


_CommandInterrupted = _process_runner._CommandInterrupted


def run_bounded(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> subprocess.CompletedProcess[str]:
    """Run an audit command through the repository's bounded process policy."""
    try:
        return _process_runner.run_with_deadline(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            termination_grace_seconds=TERMINATION_GRACE_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise CommandTimedOut from None
    except _process_runner.CommandLaunchError:
        raise CommandLaunchError from None
    except _process_runner.CommandOutputTooLarge:
        raise CommandOutputTooLarge from None


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
