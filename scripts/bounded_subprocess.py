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


def forwarded_failure(label: str, returncode: int) -> str:
    """Header for a step that opted into forwarding via --forward-stderr.

    Saying "output redacted" while printing the output is worse than saying
    nothing: it teaches readers the detail below is not there.
    """
    return f"{label} failed (exit {returncode}); output follows:"


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
        help="forward subprocess output, including on failure "
             "(use only for non-secret build logs)",
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
    except Exception:
        print(
            f"{args.label} failed "
            "(internal subprocess error; details redacted)"
        )
        return 1
    if result.returncode != 0:
        returncode = (
            128 - result.returncode
            if result.returncode < 0
            else result.returncode
        )
        print(
            forwarded_failure(args.label, returncode)
            if args.forward_stderr
            else redacted_failure(args.label, returncode)
        )
        # --forward-stderr is the caller asserting "this step's output is
        # non-secret build logs". Honouring it only on SUCCESS made it useless
        # in the one case anyone needs it: a strict MkDocs build that fails on
        # a transient asset fetch printed the redaction line and nothing else,
        # so CI could never say WHICH fetch died. Steps that do not opt in stay
        # fully redacted.
        if args.forward_stderr:
            if result.stdout:
                print(result.stdout, end="")
            # stdout is block-buffered when piped and stderr is not, so without
            # this flush the forwarded streams interleave out of order in a
            # merged CI log — the failure detail can land ABOVE the line that
            # says what failed.
            sys.stdout.flush()
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
                sys.stderr.flush()
        return returncode
    if result.stdout:
        print(result.stdout, end="")
    if args.forward_stderr and result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
