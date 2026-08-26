"""Service management and shared host-process compensation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import sys

from utils.atomic_write import atomic_write_text


@dataclass(frozen=True)
class LaunchCompensation:
    terminated: bool
    evidence: str = "none"  # "identity" | "pid" | "none"
    cleanup_errors: tuple[str, ...] = ()


def remove_state_directory(path: Path, error_details) -> None:
    """Remove managed state idempotently while surfacing real I/O failures."""
    description, error_type = error_details
    try:
        shutil.rmtree(path)
    except FileNotFoundError as exc:
        try:
            path.lstat()
        except FileNotFoundError:
            return
        except OSError as probe_exc:
            raise error_type(
                f"could not verify removal of {description} {path}: {probe_exc}"
            ) from probe_exc
        raise error_type(f"could not remove {description} {path}: {exc}") from exc
    except OSError as exc:
        raise error_type(f"could not remove {description} {path}: {exc}") from exc


def _cleanup_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _attempt_launch_termination(terminate) -> tuple[bool, tuple[str, ...]]:
    try:
        return bool(terminate()), ()
    except BaseException as exc:
        return False, (_cleanup_error(exc),)


def _discard_pid_evidence(pid_file: Path) -> tuple[str, ...]:
    try:
        pid_file.unlink(missing_ok=True)
    except BaseException as exc:
        return (_cleanup_error(exc),)
    return ()


def _same_pid_has_identity(pid_file: Path, pid: int) -> bool:
    try:
        lines = pid_file.read_text(encoding="utf-8").splitlines()
        recorded_pid = int(lines[0])
    except (OSError, ValueError, IndexError):
        return False
    return recorded_pid == pid and any(
        line.startswith("start_utc=") and line.removeprefix("start_utc=").strip()
        for line in lines[1:]
    )


def _retain_pid_evidence(pid_file: Path, pid: int) -> tuple[str, tuple[str, ...]]:
    if _same_pid_has_identity(pid_file, pid):
        return "identity", ()
    try:
        atomic_write_text(pid_file, f"{pid}\n")
    except BaseException as exc:
        return "none", (_cleanup_error(exc),)
    return "pid", ()


def compensate_failed_launch(pid: int, pid_file: Path, terminate) -> LaunchCompensation:
    """Terminate an unrecorded child or retain the strongest PID evidence."""
    terminated, errors = _attempt_launch_termination(terminate)
    if terminated:
        cleanup = errors + _discard_pid_evidence(pid_file)
        return LaunchCompensation(True, cleanup_errors=cleanup)
    evidence, retention_errors = _retain_pid_evidence(pid_file, pid)
    return LaunchCompensation(False, evidence, errors + retention_errors)


def tracked_process_may_survive(manager) -> tuple[int | None, bool]:
    """Return the raw tracked PID and a fail-closed liveness verdict.

    Managed-host ``status()`` is ownership-qualified: an unstamped or
    mismatched live PID correctly reports *not managed*. Cleanup decisions
    cannot use that view, because deleting state would erase the only evidence
    for the still-live process. This helper deliberately reads the raw PID (or
    an in-memory launch PID), probes liveness without adopting it, and treats
    unreadable retained evidence as potentially live.
    """
    try:
        pid = manager._read_pid() or getattr(manager, "_untracked_pid", None)
    except Exception:  # noqa: BLE001 - cleanup must fail closed
        return None, True
    if pid is None:
        pid_file = getattr(manager, "pid_file", None)
        if pid_file is None:
            return None, False
        try:
            return None, bool(pid_file.exists())
        except OSError:
            return None, True
    try:
        probe = getattr(manager, "_managed_process_alive", None)
        if not callable(probe):
            probe = manager._pid_alive
        return pid, bool(probe(pid))
    except Exception:  # noqa: BLE001 - an unprobeable tracked PID is not safe to erase
        return pid, True


def refuse_untrusted_tracked_pid(
    tracked: tuple[int | None, Path], alive_probe, ownership_probe, error_details,
) -> None:
    """Fail before replacing retained evidence not proven safe to replace."""
    pid, pid_file = tracked
    description, error_type = error_details
    if pid is None:
        try:
            evidence_exists = pid_file.exists()
        except OSError:
            evidence_exists = True
        if evidence_exists:
            raise error_type(
                f"refusing to replace unreadable PID evidence for {description}; "
                "inspect or remove the PID file manually"
            )
        return
    if not alive_probe(pid) or not ownership_probe(pid):
        return
    raise error_type(
        f"refusing to replace tracked pid {pid} for {description}: ownership is "
        "mismatched or unknown; inspect the pid file and process manually"
    )


def _evidence_message(outcome: LaunchCompensation) -> str:
    return {
        "identity": "verified PID identity was retained",
        "pid": "PID-only evidence was retained",
    }.get(outcome.evidence, "PID evidence could not be written")


def _cleanup_message(outcome: LaunchCompensation) -> str:
    if not outcome.cleanup_errors:
        return ""
    return f" Cleanup warning: {'; '.join(outcome.cleanup_errors)}."


def _annotate_surviving_control_flow(
    exc: BaseException, pid: int, outcome: LaunchCompensation,
) -> None:
    if outcome.terminated and not outcome.cleanup_errors:
        return
    if outcome.terminated:
        note = (
            f"Atlas terminated pid {pid}, but launch cleanup was incomplete."
            f"{_cleanup_message(outcome)}"
        )
    else:
        note = (
            f"Atlas could not terminate pid {pid}; {_evidence_message(outcome)}; "
            f"terminate pid {pid} manually.{_cleanup_message(outcome)}"
        )
    if hasattr(exc, "add_note"):
        exc.add_note(note)
    else:  # Python 3.10 compatibility
        exc.args = (*exc.args, note)
    print(f"WARNING: {note}", file=sys.stderr, flush=True)


def raise_launch_recording_failure(
    exc: BaseException,
    pid: int,
    outcome: LaunchCompensation,
    error_details,
) -> None:
    """Preserve control-flow exceptions and report compensation truthfully."""
    record_label, error_type = error_details
    if not isinstance(exc, Exception):
        _annotate_surviving_control_flow(exc, pid, outcome)
        raise exc.with_traceback(exc.__traceback__)
    if outcome.terminated:
        raise error_type(
            f"{record_label} for pid {pid} could not be recorded; the child was "
            f"terminated.{_cleanup_message(outcome)}"
        ) from exc
    raise error_type(
        f"{record_label} for pid {pid} could not be recorded, and the child could "
        f"not be terminated; {_evidence_message(outcome)}; terminate pid {pid} "
        f"manually.{_cleanup_message(outcome)}",
        surviving_process=True,
    ) from exc
