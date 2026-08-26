"""Service management and shared host-process compensation primitives."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from utils.atomic_write import atomic_write_text


_LIFECYCLE_LOCK_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class LaunchCompensation:
    terminated: bool
    evidence: str = "none"  # "identity" | "pid" | "none"
    cleanup_errors: tuple[str, ...] = ()


def lifecycle_support_error(
    fcntl_module, os_module, signal_module, label: str
) -> str | None:
    """Describe missing primitives required for safe host process ownership."""
    missing = []
    if fcntl_module is None:
        missing.append("fcntl.flock")
    if not callable(getattr(os_module, "killpg", None)):
        missing.append("os.killpg")
    if getattr(signal_module, "SIGTERM", None) is None:
        missing.append("signal.SIGTERM")
    if getattr(signal_module, "SIGKILL", None) is None:
        missing.append("signal.SIGKILL")
    if not sys.platform.startswith("linux") and _lsof_path() is None:
        missing.append("lsof")
    if not missing:
        return None
    return (
        f"safe {label} lifecycle primitives are unavailable "
        f"({', '.join(missing)}); this host cannot start managed processes"
    )


def add_lifecycle_preflight(result, error: str | None, statuses) -> None:
    """Add the shared lifecycle-capability verdict to a manager preflight."""
    ok_status, fail_status = statuses
    if error:
        result.add("lifecycle", fail_status, error)
    else:
        result.add(
            "lifecycle", ok_status,
            "cross-process lock and process-group teardown available",
        )


def require_lifecycle_support(error: str | None, error_type) -> None:
    if error:
        raise error_type(error)


def refuse_occupied_port(status, port_probe, error_details) -> None:
    """Reject an unmanaged listener immediately before a managed spawn."""
    message, error_type = error_details
    if getattr(status, "port_open", False) or port_probe():
        raise error_type(message)


def await_owned_process_readiness(manager, status, wait_timeout, error_details):
    """Wait for a health proof while repeatedly retaining ownership proof."""
    label, bind, port, error_type, clock = error_details
    deadline = clock.monotonic() + wait_timeout
    while True:
        try:
            health = manager.health(timeout=min(0.5, max(0.05, wait_timeout)))
        except Exception as exc:
            raise error_type(
                f"owned {label} readiness probe failed: {exc}",
                surviving_process=False,
            ) from exc
        current = manager.status()
        if (
            health.get("reachable")
            and health.get("matched", True)
            and current.running
        ):
            current.port_open = True
            return current
        if clock.monotonic() >= deadline:
            raise error_type(
                f"owned {label} process did not become ready on "
                f"{bind}:{port} within {wait_timeout:.0f}s",
                surviving_process=False,
            )
        clock.sleep(0.5)


def await_spawned_process_readiness(manager, process, wait_timeout, context) -> bool:
    """Accept readiness only while the launch child owns the endpoint."""
    clock, label, error_type = context
    try:
        return _poll_spawned_process_readiness(
            manager, process, wait_timeout, clock
        )
    except BaseException as exc:
        _raise_readiness_probe_failure(manager, exc, label, error_type)


def _poll_spawned_process_readiness(manager, process, wait_timeout, clock) -> bool:
    deadline = clock.monotonic() + wait_timeout
    while clock.monotonic() < deadline:
        if process.poll() is not None:
            return False
        health = manager.health(timeout=min(0.5, max(0.05, wait_timeout)))
        if process.poll() is not None:
            return False
        ownership_probe = getattr(manager, "_spawned_endpoint_owned", None)
        endpoint_owned = not callable(ownership_probe) or ownership_probe(process.pid)
        if (
            health.get("reachable")
            and health.get("matched", True)
            and endpoint_owned
        ):
            return True
        clock.sleep(0.5)
    return False


def _raise_readiness_probe_failure(manager, probe_error, label, error_type) -> None:
    cleanup_error = None
    try:
        manager._stop_locked()
    except Exception as exc:
        cleanup_error = exc
    pid, may_survive = tracked_process_may_survive(manager)
    if not isinstance(probe_error, Exception):
        if may_survive:
            pid_text = f" pid {pid}" if pid is not None else ""
            note = (
                f"Atlas could not prove termination of the managed{pid_text} process "
                f"after readiness was interrupted; inspect retained PID evidence and "
                f"terminate the process manually."
            )
            if cleanup_error is not None:
                note += f" Cleanup warning: {cleanup_error}."
            _warn_control_flow(probe_error, note)
        elif cleanup_error is not None:
            _warn_control_flow(
                probe_error,
                f"Managed-process cleanup also failed: {cleanup_error}.",
            )
        raise probe_error
    cleanup_note = (
        f"; cleanup also failed: {cleanup_error}"
        if cleanup_error is not None
        else ""
    )
    raise error_type(
        f"{label} readiness probe failed: {probe_error}{cleanup_note}",
        surviving_process=may_survive,
    ) from probe_error


def _lsof_path() -> str | None:
    discovered = shutil.which("lsof")
    if discovered:
        return discovered
    for candidate in (Path("/usr/sbin/lsof"), Path("/usr/bin/lsof")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _numeric_bind_addresses(bind: str) -> set[str]:
    try:
        return {ipaddress.ip_address(bind.split("%", 1)[0]).compressed}
    except ValueError:
        pass
    try:
        return {
            ipaddress.ip_address(address[0].split("%", 1)[0]).compressed
            for *_prefix, address in socket.getaddrinfo(
                bind, None, type=socket.SOCK_STREAM
            )
        }
    except (OSError, ValueError):
        return set()


def _listener_address_matches(address: str, family: int, bind: str) -> bool:
    candidates = _numeric_bind_addresses(bind)
    family_candidates = {
        candidate
        for candidate in candidates
        if ipaddress.ip_address(candidate).version == family
    }
    wildcard = "0.0.0.0" if family == 4 else "::"
    if address in {"*", wildcard}:
        return bool(family_candidates)
    try:
        normalized = ipaddress.ip_address(address.split("%", 1)[0]).compressed
    except ValueError:
        return False
    if wildcard in family_candidates:
        return normalized == wildcard
    return normalized in family_candidates


def _lsof_output_has_endpoint(output: str, bind: str, port: int) -> bool:
    family = 0
    suffix = f":{port}"
    for line in output.splitlines():
        if line == "tIPv4":
            family = 4
        elif line == "tIPv6":
            family = 6
        elif line.startswith("n") and family and line[1:].endswith(suffix):
            address = line[1:-len(suffix)]
            if address.startswith("[") and address.endswith("]"):
                address = address[1:-1]
            if _listener_address_matches(address, family, bind):
                return True
    return False


def process_group_owns_tcp_listener(pgid: int, bind: str, port: int) -> bool:
    lsof = _lsof_path()
    if lsof:
        try:
            result = subprocess.run(
                [
                    lsof, "-nP", "-a", "-g", str(pgid),
                    f"-iTCP:{port}", "-sTCP:LISTEN", "-Fptn",
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            if not sys.platform.startswith("linux"):
                raise RuntimeError(
                    f"could not verify listener ownership with {lsof}: {exc}"
                ) from exc
        else:
            if result.returncode == 0:
                return _lsof_output_has_endpoint(result.stdout, bind, port)
    return sys.platform.startswith("linux") and _linux_group_owns_listener(
        pgid, bind, port
    )


def _decode_proc_address(address_hex: str, family: int) -> str:
    raw = bytes.fromhex(address_hex)
    if family == 4:
        raw = raw[::-1]
        socket_family = socket.AF_INET
    else:
        if sys.byteorder == "little":
            raw = b"".join(raw[index:index + 4][::-1] for index in range(0, 16, 4))
        socket_family = socket.AF_INET6
    return socket.inet_ntop(socket_family, raw)


def _matching_linux_socket_inode(fields, family: int, bind: str, wanted: str):
    if (
        len(fields) <= 9
        or fields[1].rsplit(":", 1)[-1] != wanted
        or fields[3] != "0A"
    ):
        return None
    try:
        address = _decode_proc_address(fields[1].rsplit(":", 1)[0], family)
    except (OSError, ValueError):
        return None
    if _listener_address_matches(address, family, bind):
        return fields[9]
    return None


def _linux_listening_socket_inodes(bind: str, port: int) -> set[str]:
    wanted = f"{port:04X}"
    inodes: set[str] = set()
    for table, family in (
        (Path("/proc/net/tcp"), 4),
        (Path("/proc/net/tcp6"), 6),
    ):
        try:
            lines = table.read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            inode = _matching_linux_socket_inode(fields, family, bind, wanted)
            if inode is not None:
                inodes.add(inode)
    return inodes


def _linux_process_group_members(pgid: int) -> list[Path]:
    members = []
    for process_dir in Path("/proc").glob("[0-9]*"):
        try:
            stat = process_dir.joinpath("stat").read_text(encoding="utf-8")
            fields = stat.rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid:
                members.append(process_dir)
        except (OSError, ValueError, IndexError):
            continue
    return members


def _linux_group_owns_listener(pgid: int, bind: str, port: int) -> bool:
    inodes = _linux_listening_socket_inodes(bind, port)
    if not inodes:
        return False
    for process_dir in _linux_process_group_members(pgid):
        for fd in process_dir.joinpath("fd").glob("*"):
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                return True
    return False


def acquire_lifecycle_lock(
    handle, fcntl_module, error_details, clock
) -> None:
    """Acquire a POSIX lifecycle lock with a bounded, actionable failure."""
    description, error_type = error_details
    deadline = clock.monotonic() + _LIFECYCLE_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl_module.flock(
                handle.fileno(), fcntl_module.LOCK_EX | fcntl_module.LOCK_NB
            )
            return
        except BlockingIOError as exc:
            if clock.monotonic() >= deadline:
                raise error_type(
                    f"timed out waiting for another {description} lifecycle operation"
                ) from exc
            clock.sleep(0.1)


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


def _warn_control_flow(exc: BaseException, note: str) -> None:
    if hasattr(exc, "add_note"):
        exc.add_note(note)
    else:  # Python 3.10 compatibility
        exc.args = (*exc.args, note)
    print(f"WARNING: {note}", file=sys.stderr, flush=True)


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
    _warn_control_flow(exc, note)


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
