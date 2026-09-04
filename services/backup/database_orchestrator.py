#!/usr/bin/env python3
"""Host boundary for consistency-safe Atlas database backup and restore.

Only the host may quiesce Compose services or replace their named-volume
contents.  Restores are validated in exact-version disposable volumes before
this module takes an offline rollback copy and performs a bounded copy cutover.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time


NEO4J_IMAGE = "neo4j:5.26.27"
WEAVIATE_IMAGE = "cr.weaviate.io/semitechnologies/weaviate:1.38.13"
HELPER_IMAGE = "alpine:3.24.1"
OWNER_LABEL = "com.atlas.database-restore-token"
SCOPE_LABEL = "com.atlas.database-restore-scope"
ROLE_LABEL = "com.atlas.database-restore-role"
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
TOKEN_RE = re.compile(r"[0-9a-f]{32}\Z")
DEFERRED_RECOVERY_POISON = "recovery failed while cancellation was deferred"


class ContractError(RuntimeError):
    """An operator input, runtime capability, or safety contract failed."""


class _OwnershipMismatch(ContractError):
    """An exact Docker name exists but its labels deny cleanup authority."""


class SignalInterruption(RuntimeError):
    """A handled operator signal interrupted the current database boundary."""


class _RecoverySignalDeferral:
    """Record HUP/INT/TERM while a compensating rollback is in flight."""

    def __init__(self) -> None:
        self.interruption: BaseException | None = None
        self.body_error: BaseException | None = None
        self.body_interruption_precedes_deferred = False
        self._handled = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
        self._previous: dict[int, signal.Handlers] = {}

    def _record(self, signum, _frame) -> None:
        if self.interruption is None:
            self.interruption = SignalInterruption(f"received signal {signum}")

    def __enter__(self) -> _RecoverySignalDeferral:
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, self._handled)
        try:
            for handled in self._handled:
                self._previous[handled] = signal.getsignal(handled)
                signal.signal(handled, self._record)
        except BaseException:
            for handled, previous in self._previous.items():
                signal.signal(handled, previous)
            raise
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.body_error = _exc
        self.body_interruption_precedes_deferred = (
            self.interruption is None
            and isinstance(
                _exc, (SignalInterruption, KeyboardInterrupt, SystemExit)
            )
        )
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, self._handled)
        try:
            for handled, previous in self._previous.items():
                signal.signal(handled, previous)
        finally:
            try:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            except (SignalInterruption, KeyboardInterrupt, SystemExit) as exc:
                if self.body_interruption_precedes_deferred:
                    return False
                if self.interruption is None:
                    self.interruption = exc
                return False
        return False


def _first_interruption(
    current: BaseException | None, exc: BaseException | None
) -> BaseException | None:
    if current is None and isinstance(
        exc, (SignalInterruption, KeyboardInterrupt, SystemExit)
    ):
        return exc
    return current


def _raise_cleanup_outcome(
    primary: BaseException | None,
    interruption: BaseException | None,
    failure: BaseException | None = None,
) -> None:
    if primary is None and interruption is not None:
        raise interruption
    if failure is not None:
        raise failure


def _raise_ownership_mismatch(
    primary: BaseException | None,
    interruption: BaseException | None,
    mismatch: BaseException,
) -> None:
    if primary is None and interruption is not None:
        note = f"Cleanup also refused a foreign resource: {mismatch}"
        if hasattr(interruption, "add_note"):
            interruption.add_note(note)
        else:
            notes = getattr(interruption, "__notes__", [])
            notes.append(note)
            interruption.__notes__ = notes
        raise interruption
    raise mismatch


@dataclass(frozen=True)
class SourcePlan:
    neo4j: bool
    weaviate: bool


@dataclass(frozen=True)
class DatabaseServiceState:
    exists: bool
    running: bool
    healthy: bool


def source_plan(neo4j_source: str, weaviate_source: str) -> SourcePlan:
    values = {"container", "disabled", "localhost"}
    if neo4j_source not in values or weaviate_source not in values:
        raise ContractError("database source must be container, disabled, or localhost")
    if "localhost" in (neo4j_source, weaviate_source):
        raise ContractError("localhost databases require an operator-managed external backup/restore contract")
    return SourcePlan(neo4j_source == "container", weaviate_source == "container")


def validate_backup_timestamp(value: str) -> str:
    if not re.fullmatch(r"[0-9]{8}_[0-9]{6}", value):
        raise ContractError("BACKUP_TIMESTAMP must be a calendar-valid YYYYMMDD_HHMMSS value")
    try:
        parsed = datetime.strptime(value, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractError("BACKUP_TIMESTAMP must be a calendar-valid YYYYMMDD_HHMMSS value") from exc
    if parsed.strftime("%Y%m%d_%H%M%S") != value:
        raise ContractError("BACKUP_TIMESTAMP is not canonical")
    return value


def weaviate_status_kind(status: str) -> str:
    if status in {"STARTED", "TRANSFERRING", "TRANSFERRED", "FINALIZING", "CANCELLING"}:
        return "pending"
    if status == "SUCCESS":
        return "success"
    if status in {"FAILED", "CANCELED"}:
        return "failed"
    raise ContractError(f"unknown Weaviate 1.38.13 backup status: {status!r}")


def _process_start(pid: int) -> str | None:
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        if proc_stat.is_file():
            fields = proc_stat.read_text(encoding="utf-8").split()
            return f"proc:{fields[21]}" if len(fields) > 21 else None
        if sys.platform == "darwin":
            class ProcBsdInfo(ctypes.Structure):
                _fields_ = [
                    ("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32),
                    ("pbi_xstatus", ctypes.c_uint32), ("pbi_pid", ctypes.c_uint32),
                    ("pbi_ppid", ctypes.c_uint32), ("pbi_uid", ctypes.c_uint32),
                    ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32),
                    ("pbi_rgid", ctypes.c_uint32), ("pbi_svuid", ctypes.c_uint32),
                    ("pbi_svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
                    ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32),
                    ("pbi_nfiles", ctypes.c_uint32), ("pbi_pgid", ctypes.c_uint32),
                    ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
                    ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
                    ("pbi_start_tvsec", ctypes.c_uint64),
                    ("pbi_start_tvusec", ctypes.c_uint64),
                ]

            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            libproc.proc_pidinfo.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                ctypes.c_void_p, ctypes.c_int,
            ]
            libproc.proc_pidinfo.restype = ctypes.c_int
            info = ProcBsdInfo()
            received = libproc.proc_pidinfo(
                pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)
            )
            if received == ctypes.sizeof(info) and info.pbi_pid == pid:
                return f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}"
            return None
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        start = " ".join(result.stdout.split())
        return f"ps:{start}" if result.returncode == 0 and start else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _parse_lock(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if len(lines) != 4:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key in values or key not in {"state", "pid", "start", "token"}:
            return {}
        values[key] = value
    if set(values) != {"state", "pid", "start", "token"}:
        return {}
    return values if values["state"] in {"active", "poisoned"} else {}


class OwnedFileLock:
    """Fully-written metadata atomically published with ``link(2)``."""

    def __init__(self, path: Path, *, token: str):
        if not TOKEN_RE.fullmatch(token):
            raise ContractError("lock token must be 32 lowercase hexadecimal characters")
        self.path = path
        self.token = token
        self.pid = os.getpid()
        self.start = _process_start(self.pid)
        if not self.start:
            raise ContractError("could not determine the lock owner's process-start fingerprint")
        self.content = f"state=active\npid={self.pid}\nstart={self.start}\ntoken={self.token}\n"
        self._identity: tuple[int, int] | None = None
        self._detached = False

    def _owner_active(self, text: str) -> bool:
        fields = _parse_lock(text)
        try:
            pid = int(fields.get("pid", ""))
        except ValueError:
            return False
        token = fields.get("token", "")
        return bool(
            pid > 1
            and TOKEN_RE.fullmatch(token)
            and fields.get("start") == _process_start(pid)
        )

    def _reclaim_stale(self) -> None:
        try:
            before_stat = self.path.stat()
            before = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        fields = _parse_lock(before)
        if not fields:
            raise ContractError("database lock metadata is invalid; manual recovery is required")
        if fields["state"] == "poisoned":
            raise ContractError("database lock is poisoned; verify cleanup and remove it manually")
        try:
            recorded_pid = int(fields.get("pid", ""))
        except ValueError:
            recorded_pid = 0
        observed = _process_start(recorded_pid) if recorded_pid > 1 else None
        if observed is None and recorded_pid > 1:
            try:
                os.kill(recorded_pid, 0)
            except ProcessLookupError:
                pass
            except (PermissionError, OSError):
                raise ContractError("cannot verify the existing database lock owner")
            else:
                raise ContractError("cannot verify the active database lock owner's start fingerprint")
        if self._owner_active(before):
            raise ContractError("another backup/restore boundary is active")
        quarantine = self.path.with_name(f"{self.path.name}.stale-{self.token}")
        try:
            os.link(self.path, quarantine)
            after_stat = self.path.stat()
            after = self.path.read_text(encoding="utf-8")
            if (before_stat.st_dev, before_stat.st_ino, before) != (
                after_stat.st_dev,
                after_stat.st_ino,
                after,
            ):
                raise ContractError("database lock changed during stale-owner verification")
            self.path.unlink()
        finally:
            quarantine.unlink(missing_ok=True)

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        candidate = self.path.with_name(f"{self.path.name}.candidate-{self.token}")
        old_umask = os.umask(0o077)
        try:
            with candidate.open("x", encoding="utf-8") as handle:
                handle.write(self.content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.umask(old_umask)
        try:
            for _ in range(4):
                try:
                    os.link(candidate, self.path)
                    stat = self.path.stat()
                    self._identity = (stat.st_dev, stat.st_ino)
                    return
                except FileExistsError:
                    self._reclaim_stale()
            raise ContractError("could not acquire database backup/restore lock")
        finally:
            candidate.unlink(missing_ok=True)

    def detach_for_test(self) -> None:
        self._detached = True

    def poison(self, reason: str) -> None:
        """Atomically prevent automatic reuse when owned cleanup is unproven."""
        if not self._identity:
            return
        safe_reason = re.sub(r"[^A-Za-z0-9 .:_-]", "?", reason)[:160]
        poisoned = (
            f"state=poisoned\npid={self.pid}\nstart={self.start}\ntoken={self.token}\n"
        )
        try:
            stat = self.path.stat()
            current = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ContractError("cannot poison a missing owned database lock") from exc
        if (stat.st_dev, stat.st_ino) != self._identity or current != self.content:
            raise ContractError("cannot poison database lock because ownership changed")
        candidate = self.path.with_name(f"{self.path.name}.poison-{self.token}")
        candidate.write_text(poisoned, encoding="utf-8")
        os.chmod(candidate, 0o600)
        os.replace(candidate, self.path)
        replacement = self.path.stat()
        self._identity = (replacement.st_dev, replacement.st_ino)
        self.content = poisoned
        self._detached = True
        print(f"database lock poisoned: {safe_reason}; manual recovery required", file=sys.stderr)

    def release(self) -> None:
        if not self._identity or self._detached:
            return
        try:
            stat = self.path.stat()
            current = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        if (stat.st_dev, stat.st_ino) == self._identity and current == self.content:
            self.path.unlink()
        self._identity = None


def _process_group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_group_exit(
    process: subprocess.Popen[str], group_id: int, *, timeout: float
) -> bool:
    """Reap the leader and wait a bounded interval for its owned group."""
    deadline = time.monotonic() + timeout
    while True:
        process.poll()
        if not _process_group_exists(group_id):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))


def _terminate_owned_process_group(process: subprocess.Popen[str]) -> bool:
    group_id = process.pid
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as exc:
        print(
            f"database orchestrator: WARNING - could not signal owned "
            f"process group {group_id} with SIGTERM: {exc}",
            file=sys.stderr,
        )
    if _wait_for_process_group_exit(process, group_id, timeout=3):
        return True
    try:
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        print(
            f"database orchestrator: WARNING - could not signal owned "
            f"process group {group_id} with SIGKILL: {exc}",
            file=sys.stderr,
        )
    if not _wait_for_process_group_exit(process, group_id, timeout=3):
        print(
            f"database orchestrator: WARNING - owned process group "
            f"{group_id} survived bounded cleanup",
            file=sys.stderr,
        )
        return False
    return True


class CommandRunner:
    def __init__(self, *, token: str, timeout: int, scope: str = "unscoped"):
        self.token = token
        self.timeout = timeout
        self.scope = scope
        self.containers: set[str] = set()
        self.volumes: set[str] = set()
        self.container_create_timeouts: dict[str, int] = {}
        self.volume_create_timeouts: dict[str, int] = {}
        self.process_group_cleanup_failed = False

    def run(
        self,
        command: list[str],
        *,
        timeout: int | None = None,
        check: bool = True,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            text=True,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
        try:
            if input_text is not None:
                try:
                    assert process.stdin is not None
                    process.stdin.write(input_text)
                    process.stdin.close()
                    process.stdin = None
                except BrokenPipeError:
                    pass
            deadline = time.monotonic() + (timeout or self.timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout or self.timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    # Return to the interpreter frequently so HUP/INT/TERM handlers
                    # cannot remain deferred behind a long blocking communicate().
                    continue
        except BaseException:
            try:
                cleanup_proven = _terminate_owned_process_group(process)
            except BaseException as exc:
                cleanup_proven = False
                print(
                    "database orchestrator: WARNING - owned process-group cleanup "
                    f"failed without replacing the primary error: {exc}",
                    file=sys.stderr,
                )
            if not cleanup_proven:
                self.process_group_cleanup_failed = True
            raise
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check and result.returncode != 0:
            raise ContractError(
                f"command failed ({result.returncode}): {' '.join(command)}\n{stderr.strip()}"
            )
        return result

    def unique_name(self, role: str) -> str:
        safe = re.sub(r"[^a-z0-9-]", "-", role.lower()).strip("-")[:72]
        return f"atlas-db-{safe}-{self.token}"[:128].rstrip("-")

    def register_container(self, name: str, *, timeout: int | None = None) -> None:
        self.containers.add(name)
        self.container_create_timeouts[name] = self.timeout if timeout is None else timeout

    def create_volume(self, role: str) -> str:
        name = self.unique_name(role)
        self.volumes.add(name)
        self.volume_create_timeouts[name] = self.timeout
        self.run(
            [
                "docker", "volume", "create",
                "--label", f"{OWNER_LABEL}={self.token}",
                "--label", f"{SCOPE_LABEL}={self.scope}",
                "--label", f"{ROLE_LABEL}={role}",
                name,
            ]
        )
        self.assert_owned_volume(name, role=role)
        return name

    @staticmethod
    def _sleep_cleanup_retry(
        primary: BaseException | None,
    ) -> BaseException | None:
        try:
            time.sleep(0.2)
        except BaseException as exc:
            if primary is None:
                if isinstance(exc, (SignalInterruption, KeyboardInterrupt, SystemExit)):
                    return exc
                raise
            print(
                "database resource cleanup warning: retry sleep interrupted "
                f"without replacing the primary error: {exc}",
                file=sys.stderr,
            )
        return primary

    def _inspect_json(self, kind: str, name: str) -> dict | None:
        result = self.run(["docker", kind, "inspect", name], check=False, timeout=10)
        if result.returncode != 0:
            if kind == "container":
                listed = self.run(
                    [
                        "docker", "ps", "-a", "--format", "{{.Names}}",
                        "--filter", f"name=^/{name}$",
                    ],
                    check=False,
                    timeout=10,
                )
            elif kind == "volume":
                listed = self.run(
                    ["docker", "volume", "ls", "-q", "--filter", f"name={name}"],
                    check=False,
                    timeout=10,
                )
            else:
                raise ContractError(f"unsupported Docker ownership kind: {kind}")
            if listed.returncode != 0:
                raise ContractError(f"could not prove {kind} absent: {name}")
            if name in listed.stdout.splitlines():
                raise ContractError(f"could not inspect existing {kind}: {name}")
            return None
        try:
            records = json.loads(result.stdout)
            record = records[0]
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ContractError(f"Docker returned malformed {kind} ownership metadata") from exc
        if not isinstance(record, dict):
            raise ContractError(f"Docker returned malformed {kind} ownership metadata")
        return record

    def assert_owned_volume(self, name: str, *, role: str) -> dict:
        record = self._inspect_json("volume", name)
        labels = record.get("Labels") if record else None
        if (
            not record
            or record.get("Name") != name
            or not isinstance(labels, dict)
            or labels.get(OWNER_LABEL) != self.token
            or labels.get(SCOPE_LABEL) != self.scope
            or labels.get(ROLE_LABEL) != role
        ):
            raise ContractError(f"volume ownership verification failed: {name}")
        return record

    def _owned_container(self, name: str) -> bool:
        record = self._inspect_json("container", name)
        labels = record.get("Config", {}).get("Labels") if record else None
        return bool(
            record
            and record.get("Name", "").lstrip("/") == name
            and isinstance(labels, dict)
            and labels.get(OWNER_LABEL) == self.token
            and labels.get(SCOPE_LABEL) == self.scope
        )

    def _resource_tracking(self, kind: str) -> tuple[set[str], dict[str, int]]:
        if kind == "container":
            return self.containers, self.container_create_timeouts
        return self.volumes, self.volume_create_timeouts

    def _assert_owned_record(self, kind: str, name: str, record: dict) -> None:
        labels = (
            record.get("Config", {}).get("Labels")
            if kind == "container"
            else record.get("Labels")
        )
        actual = record.get("Name", "")
        if kind == "container":
            actual = actual.lstrip("/")
        if (
            actual != name
            or not isinstance(labels, dict)
            or labels.get(OWNER_LABEL) != self.token
            or labels.get(SCOPE_LABEL) != self.scope
        ):
            raise _OwnershipMismatch(f"refusing to remove unowned {kind}: {name}")

    def _remove_visible_resource(self, kind: str, name: str, record: dict) -> None:
        self._assert_owned_record(kind, name, record)
        command = (
            ["docker", "rm", "-f", name]
            if kind == "container"
            else ["docker", "volume", "rm", name]
        )
        removed = self.run(command, check=False, timeout=20)
        if removed.returncode != 0:
            raise ContractError(f"owned {kind} removal failed: {name}")
        if self._inspect_json(kind, name) is not None:
            raise ContractError(f"owned {kind} did not disappear: {name}")

    def _remove_owned_resource_once(self, kind: str, name: str) -> bool:
        resources, create_timeouts = self._resource_tracking(kind)
        record = self._inspect_json(kind, name)
        if record is None:
            return False
        self._remove_visible_resource(kind, name, record)
        resources.discard(name)
        create_timeouts.pop(name, None)
        return True

    def _resource_reconcile_step(
        self,
        kind: str,
        name: str,
        context: tuple[
            float | None,
            BaseException | None,
            BaseException | None,
            BaseException | None,
        ],
    ) -> tuple[str, BaseException | None, BaseException | None]:
        deadline, primary, interruption, last_failure = context
        try:
            if last_failure is not None and (
                deadline is None or time.monotonic() >= deadline
            ):
                return "expired", last_failure, interruption
            if self._remove_owned_resource_once(kind, name):
                return "removed", None, interruption
            if deadline is None or time.monotonic() >= deadline:
                return "expired", None, interruption
            interruption = _first_interruption(
                interruption,
                self._sleep_cleanup_retry(primary or interruption),
            )
            return "retry", None, interruption
        except _OwnershipMismatch:
            raise
        except BaseException as exc:
            return "failed", exc, interruption

    def _establish_reconciliation_deadline(
        self, reconcile: int | None, primary: BaseException | None,
    ) -> tuple[float | None, BaseException | None]:
        interruption = _first_interruption(None, primary)
        while True:
            try:
                deadline = (
                    time.monotonic() + reconcile if reconcile is not None else None
                )
                return deadline, interruption
            except (SignalInterruption, KeyboardInterrupt, SystemExit) as exc:
                interruption = _first_interruption(interruption, exc)
                try:
                    interruption = _first_interruption(
                        interruption,
                        self._sleep_cleanup_retry(primary or interruption),
                    )
                except BaseException as sleep_exc:
                    interruption = _first_interruption(interruption, sleep_exc)

    def _remove_owned_resource(self, kind: str, name: str) -> None:
        resources, create_timeouts = self._resource_tracking(kind)
        reconcile = create_timeouts.get(name)
        primary = sys.exc_info()[1]
        deferral = _RecoverySignalDeferral()
        interruption: BaseException | None = None
        terminal_failure: BaseException | None = None
        ownership_mismatch: BaseException | None = None
        with deferral:
            deadline, interruption = self._establish_reconciliation_deadline(
                reconcile, primary
            )
            last_failure: BaseException | None = None
            while True:
                try:
                    state, failure, interruption = self._resource_reconcile_step(
                        kind, name, (deadline, primary, interruption, last_failure)
                    )
                except _OwnershipMismatch as exc:
                    ownership_mismatch = exc
                    break
                if state == "failed":
                    interruption = _first_interruption(interruption, failure)
                    last_failure = failure
                    try:
                        interruption = _first_interruption(
                            interruption,
                            self._sleep_cleanup_retry(primary or interruption),
                        )
                    except BaseException as exc:
                        interruption = _first_interruption(interruption, exc)
                        last_failure = exc
                    continue
                last_failure = failure
                if state == "removed":
                    break
                if state == "expired":
                    terminal_failure = last_failure
                    if terminal_failure is None:
                        resources.discard(name)
                        create_timeouts.pop(name, None)
                    break
        interruption = _first_interruption(interruption, deferral.interruption)
        if ownership_mismatch is not None:
            _raise_ownership_mismatch(primary, interruption, ownership_mismatch)
        _raise_cleanup_outcome(primary, interruption, terminal_failure)

    def remove_container(self, name: str) -> None:
        self._remove_owned_resource("container", name)

    def remove_volume(self, name: str) -> None:
        self._remove_owned_resource("volume", name)

    def assert_no_owned_containers(self) -> None:
        listed = self.run(
            [
                "docker", "ps", "-aq",
                "--filter", f"label={OWNER_LABEL}={self.token}",
                "--filter", f"label={SCOPE_LABEL}={self.scope}",
            ],
            timeout=10,
        )
        names = set(self.containers)
        names.update(line for line in listed.stdout.splitlines() if line)
        present = [name for name in names if self._inspect_json("container", name) is not None]
        if present:
            raise ContractError("owned database job containers remain: " + ", ".join(sorted(present)))

    def cleanup(self, *, retain_volumes: set[str] | None = None) -> None:
        retained = retain_volumes or set()
        errors: list[str] = []
        interruption: BaseException | None = None
        for name in tuple(self.containers):
            try:
                self.remove_container(name)
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                errors.append(str(exc))
                interruption = _first_interruption(interruption, exc)
        for name in tuple(self.volumes):
            if name not in retained:
                try:
                    self.remove_volume(name)
                except (Exception, KeyboardInterrupt, SystemExit) as exc:
                    errors.append(str(exc))
                    interruption = _first_interruption(interruption, exc)
        if interruption is not None:
            print("database resource cleanup warning: " + "; ".join(errors), file=sys.stderr)
            raise interruption
        if errors:
            raise ContractError("; ".join(errors))

    def prune_retained_rollbacks(self, retained: set[str], *, keep: int) -> None:
        """Delete only older rollback volumes in this repository's private scope."""
        if not 1 <= keep <= 20:
            raise ContractError("BACKUP_LOCAL_ROLLBACK_RETENTION_COUNT must be from 1 to 20")
        for role in ("neo4j-rollback", "weaviate-rollback"):
            listed = self.run(
                [
                    "docker", "volume", "ls", "-q",
                    "--filter", f"label={SCOPE_LABEL}={self.scope}",
                    "--filter", f"label={ROLE_LABEL}={role}",
                ],
                timeout=20,
            )
            candidates: list[tuple[str, str]] = []
            for name in listed.stdout.splitlines():
                if not NAME_RE.fullmatch(name):
                    raise ContractError("Docker returned an unsafe rollback volume name")
                inspected = self.run(
                    ["docker", "volume", "inspect", name], check=False, timeout=10
                )
                if inspected.returncode != 0:
                    continue
                try:
                    records = json.loads(inspected.stdout)
                    record = records[0]
                    labels = record["Labels"]
                    created = record["CreatedAt"]
                except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise ContractError("Docker returned malformed rollback volume metadata") from exc
                owner = labels.get(OWNER_LABEL)
                if (
                    record.get("Name") != name
                    or labels.get(SCOPE_LABEL) != self.scope
                    or labels.get(ROLE_LABEL) != role
                    or not isinstance(owner, str)
                    or not TOKEN_RE.fullmatch(owner)
                    or name != f"atlas-db-{role}-{owner}"
                    or not isinstance(created, str)
                ):
                    continue
                candidates.append((created, name))
            candidates.sort(reverse=True)
            for _created, name in candidates[keep:]:
                if name in retained:
                    continue
                self.run(["docker", "volume", "rm", name], timeout=20)
                if self._inspect_json("volume", name) is not None:
                    raise ContractError(f"retained rollback volume did not disappear: {name}")


def _env_file_values(repo: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    path = repo / ".env"
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.split("#", 1)[0].strip().strip('"').strip("'")
    return values


def _setting(values: dict[str, str], name: str, default: str) -> str:
    return os.environ.get(name) or values.get(name) or default


def _validate_docker_name(value: str, label: str) -> str:
    if not NAME_RE.fullmatch(value):
        raise ContractError(f"{label} is not a bounded Docker identifier")
    return value


def parse_prepared_plan(stdout: str, *, token: str, timestamp: str) -> dict[str, str]:
    prefix = "ATLAS_DATABASE_RESTORE_PLAN "
    markers = [line for line in stdout.splitlines() if line.startswith(prefix)]
    if len(markers) != 1:
        raise ContractError("restore preparation must return exactly one plan marker")
    values: dict[str, str] = {}
    for field in markers[0].removeprefix(prefix).split(" "):
        key, separator, value = field.partition("=")
        if not separator or not key or not value or key in values:
            raise ContractError("restore preparation returned malformed plan grammar")
        values[key] = value
    required = {
        "backup_timestamp", "restore_token", "backup_id", "neo4j_state",
        "weaviate_state", "artifact_stage", "weaviate_snapshot_id",
    }
    if set(values) != required:
        raise ContractError("restore preparation did not return the exact plan fields")
    if values["backup_timestamp"] != timestamp or values["restore_token"] != token:
        raise ContractError("restore preparation identity correlation failed")
    if not TOKEN_RE.fullmatch(values["backup_id"]):
        raise ContractError("restore preparation returned an invalid backup id")
    if values["artifact_stage"] != f"restore-{token}":
        raise ContractError("restore preparation returned an invalid artifact stage")
    for database in ("neo4j", "weaviate"):
        if values[f"{database}_state"] not in {"complete", "disabled"}:
            raise ContractError("restore preparation returned an invalid state")
    expected_snapshot = (
        f"atlas-{timestamp}-{values['backup_id']}"
        if values["weaviate_state"] == "complete" else "disabled"
    )
    if values["weaviate_snapshot_id"] != expected_snapshot:
        raise ContractError("restore preparation returned an invalid snapshot id")
    return values


class DatabaseCoordinator:
    def __init__(self, repo: Path, *, token: str, timeout: int):
        self.repo = repo
        self.token = token
        self.timeout = timeout
        values = _env_file_values(repo)
        self.project = _validate_docker_name(_setting(values, "PROJECT_NAME", "atlas"), "PROJECT_NAME")
        scope = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:24]
        self.runner = CommandRunner(token=token, timeout=timeout, scope=scope)
        self.neo_source = _setting(values, "NEO4J_GRAPH_DB_SOURCE", "container")
        self.weaviate_source = _setting(values, "WEAVIATE_SOURCE", "container")
        self.plan = source_plan(self.neo_source, self.weaviate_source)
        self.test_mode = os.environ.get("ATLAS_DATABASE_BACKUP_LIVE_INTEGRATION") == "1"
        test_token = os.environ.get("ATLAS_DATABASE_BACKUP_TEST_TOKEN", "")
        overrides = {
            "ATLAS_NEO4J_LIVE_VOLUME": (f"{self.project}-graph-db-data", "neo-live"),
            "ATLAS_WEAVIATE_LIVE_VOLUME": (f"{self.project}-weaviate-data", "weaviate-live"),
            "ATLAS_NEO4J_BACKUP_VOLUME": (f"{self.project}-neo4j-backups", "neo-backups"),
            "ATLAS_WEAVIATE_BACKUP_VOLUME": (f"{self.project}-weaviate-backups", "weaviate-backups"),
        }
        selected: dict[str, str] = {}
        for variable, (production, role) in overrides.items():
            override = os.environ.get(variable)
            if override is None:
                selected[variable] = production
                continue
            if not self.test_mode:
                raise ContractError(f"{variable} is restricted to explicit live integration tests")
            if not TOKEN_RE.fullmatch(test_token) or test_token != token:
                raise ContractError("live integration volume overrides require the matching full 128-bit test token")
            expected = f"atlas-it-{test_token}-{role}"
            if override != expected:
                raise ContractError(f"{variable} must equal the token-bound test volume name")
            selected[variable] = override
        self.neo_live = _validate_docker_name(selected["ATLAS_NEO4J_LIVE_VOLUME"], "Neo4j live volume")
        self.weaviate_live = _validate_docker_name(selected["ATLAS_WEAVIATE_LIVE_VOLUME"], "Weaviate live volume")
        self.neo_backups = _validate_docker_name(selected["ATLAS_NEO4J_BACKUP_VOLUME"], "Neo4j backup volume")
        self.weaviate_backups = _validate_docker_name(selected["ATLAS_WEAVIATE_BACKUP_VOLUME"], "Weaviate backup volume")
        if self.test_mode and test_token:
            for variable, (_production, role) in overrides.items():
                if os.environ.get(variable) is not None:
                    self.runner.assert_owned_volume(selected[variable], role=f"test-{role}")
        auth = _setting(values, "GRAPH_DB_AUTH", "neo4j/neo4j_password")
        self.neo_user, separator, self.neo_password = auth.partition("/")
        if not separator or not self.neo_user or not self.neo_password:
            raise ContractError("GRAPH_DB_AUTH must be username/password")
        modules = _setting(values, "WEAVIATE_ENABLE_MODULES", "backup-filesystem")
        module_names = [item.strip() for item in modules.split(",") if item.strip()]
        if "backup-filesystem" not in module_names:
            raise ContractError("WEAVIATE_ENABLE_MODULES must include backup-filesystem")
        self.weaviate_modules = ",".join(module_names)
        self.was_running: dict[str, bool] = {}
        self.initial_states: dict[str, DatabaseServiceState] = {}
        self.rollback: dict[str, str] = {}
        self.stage: dict[str, str] = {}
        self.cutover_started = False
        self.boundary_state = "pre-cutover"
        self.poison_reason: str | None = None

    def _bounded_count(self, name: str, default: str, maximum: int) -> int:
        value = os.environ.get(name, default)
        if not value.isdecimal() or value.startswith("0") or not 1 <= int(value) <= maximum:
            raise ContractError(f"{name} must be a canonical integer from 1 to {maximum}")
        return int(value)

    def compose(self, *args: str, check: bool = True, timeout: int | None = None):
        return self.runner.run(["docker", "compose", *args], check=check, timeout=timeout)

    def _service_running(self, service: str) -> bool:
        state = self._service_state(service)
        return state.running

    def _service_state(self, service: str) -> DatabaseServiceState:
        listed = self.compose("ps", "--all", "-q", service, timeout=15)
        identifiers = [line for line in listed.stdout.splitlines() if line]
        if not identifiers:
            return DatabaseServiceState(False, False, False)
        if len(identifiers) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", identifiers[0]):
            raise ContractError(f"could not identify the exact Compose container for {service}")
        inspected = self.runner.run(
            ["docker", "container", "inspect", identifiers[0]], timeout=15
        )
        try:
            records = json.loads(inspected.stdout)
            record = records[0]
            labels = record["Config"]["Labels"]
            state = record["State"]
            running = state["Running"]
            status = state["Status"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ContractError(f"Docker returned malformed service state for {service}") from exc
        if (
            len(records) != 1
            or labels.get("com.docker.compose.service") != service
            or not isinstance(running, bool)
            or not isinstance(status, str)
        ):
            raise ContractError(f"Docker returned mismatched service state for {service}")
        if running:
            health = state.get("Health")
            healthy = isinstance(health, dict) and health.get("Status") == "healthy"
            return DatabaseServiceState(True, True, healthy)
        if status not in {"created", "exited"}:
            raise ContractError(f"service {service} is in unsafe non-running state {status!r}")
        return DatabaseServiceState(True, False, False)

    def _require_stopped(self, enabled: list[tuple[str, str, str]]) -> None:
        for database, _live, _stage in enabled:
            service = "neo4j-graph-db" if database == "neo4j" else "weaviate"
            state = self._service_state(service)
            if state.running:
                raise ContractError(f"service {service} is not proven stopped")

    def _mark_poison(self, reason: str) -> None:
        if self.poison_reason is None:
            self.poison_reason = reason

    def _report_secondary_failures(
        self, *, poison_reason: str, context: str, errors: list[str]
    ) -> None:
        if not errors:
            return
        self._mark_poison(poison_reason)
        print(f"{context}: {'; '.join(errors)}", file=sys.stderr)

    def _remove_owned_container_after(self, name: str, *, preserve_primary: bool) -> None:
        try:
            self.runner.remove_container(name)
        except BaseException as exc:
            self._report_secondary_failures(
                poison_reason="owned container cleanup was not proven",
                context="database boundary cleanup warning",
                errors=[str(exc)],
            )
            if not preserve_primary:
                raise

    def _restore_initial_service_state(
        self, database: str, service: str, *, restart_allowed: bool
    ) -> None:
        initial = self.initial_states[database]
        current = self._service_state(service)
        if not restart_allowed:
            if current.running:
                self.compose("stop", "--timeout", str(self.timeout), service)
                current = self._service_state(service)
            if current.exists != initial.exists or current.running:
                raise ContractError(
                    f"service {service} was not kept stopped after unverified rollback"
                )
            return
        if initial.running:
            if not current.running or not current.healthy:
                self.compose(
                    "up", "-d", "--no-deps", "--wait", "--wait-timeout",
                    str(self.timeout), service,
                )
            restored = self._service_state(service)
            if (
                restored.exists != initial.exists
                or not restored.running
                or not restored.healthy
            ):
                raise ContractError(f"service {service} health was not restored")
        elif current.exists != initial.exists:
            raise ContractError(f"service {service} existence state changed")
        elif current.running:
            raise ContractError(f"initially stopped service {service} was started")

    def _restore_initial_states(
        self, enabled: list[tuple[str, str, str]], *, restartable: set[str]
    ) -> None:
        errors: list[str] = []
        interruption: BaseException | None = None
        for database, _live, _stage in enabled:
            service = "neo4j-graph-db" if database == "neo4j" else "weaviate"
            try:
                self._restore_initial_service_state(
                    database, service, restart_allowed=database in restartable
                )
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                errors.append(str(exc))
                interruption = _first_interruption(interruption, exc)
        if interruption is not None:
            print(
                "database exact-state restoration warning: " + "; ".join(errors),
                file=sys.stderr,
            )
            raise interruption
        if errors:
            raise ContractError("exact initial service state was not restored: " + "; ".join(errors))

    def _restore_exact_initial_states(self, enabled: list[tuple[str, str, str]]) -> None:
        self._restore_initial_states(
            enabled, restartable={database for database, _live, _stage in enabled}
        )

    def _attempt_recovery_stop(self, service: str) -> BaseException | None:
        try:
            current = self._service_state(service)
            if current.running:
                self.compose("stop", "--timeout", str(self.timeout), service)
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            return exc
        return None

    def _recovery_stop_proof(self, service: str) -> BaseException | None:
        try:
            if self._service_state(service).running:
                return ContractError(f"service {service} is not proven stopped")
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            return exc
        return None

    def _finish_recovery_quiesce(
        self, failures: list[BaseException], proof_failures: list[BaseException]
    ) -> BaseException | None:
        combined = failures + proof_failures
        if not combined:
            return None
        print(
            "database recovery quiesce warning: " + "; ".join(map(str, combined)),
            file=sys.stderr,
        )
        interruption: BaseException | None = None
        for failure in combined:
            interruption = _first_interruption(interruption, failure)
        if not proof_failures:
            return interruption
        self._mark_poison("recovery could not prove an exclusive stopped boundary")
        if interruption is not None:
            raise interruption
        raise ContractError(
            "recovery stopped-state proof failed: "
            + "; ".join(map(str, proof_failures))
        )

    def _recover_after_live_mutation(
        self, enabled: list[tuple[str, str, str]]
    ) -> None:
        deferral = _RecoverySignalDeferral()
        recovery_error: BaseException | None = None
        try:
            with deferral:
                self._recover_after_live_mutation_once(enabled)
        except BaseException as exc:
            recovery_error = exc
        if (
            deferral.body_error is not None
            and recovery_error is not deferral.body_error
        ):
            deferral.interruption = _first_interruption(
                deferral.interruption, recovery_error
            )
            recovery_error = deferral.body_error
        if (
            deferral.body_interruption_precedes_deferred
            and recovery_error is not None
        ):
            raise recovery_error
        if deferral.interruption is not None:
            if recovery_error is not None and self.boundary_state != "recovery-proven":
                self._report_secondary_failures(
                    poison_reason=DEFERRED_RECOVERY_POISON,
                    context="database deferred-signal recovery warning",
                    errors=[str(recovery_error)],
                )
            raise deferral.interruption
        if recovery_error is not None:
            raise recovery_error

    def _recover_after_live_mutation_once(
        self, enabled: list[tuple[str, str, str]]
    ) -> None:
        services = [
            "neo4j-graph-db" if database == "neo4j" else "weaviate"
            for database, _live, _stage in enabled
        ]
        failures: list[BaseException] = []
        # Two bounded passes let an ambiguous/effective first stop be proved,
        # and retry an ineffective stop without skipping any peer service.
        for _attempt in range(2):
            for service in services:
                failure = self._attempt_recovery_stop(service)
                if failure is not None:
                    failures.append(failure)
        proof_failures: list[BaseException] = []
        for service in services:
            failure = self._recovery_stop_proof(service)
            if failure is not None:
                proof_failures.append(failure)
        interruption = self._finish_recovery_quiesce(failures, proof_failures)
        self._restore_rollback_after_quiesce(enabled, interruption)

    def _restore_rollback_after_quiesce(
        self,
        enabled: list[tuple[str, str, str]],
        interruption: BaseException | None,
    ) -> None:
        try:
            self.restore_rollback(enabled)
        except BaseException as exc:
            if interruption is not None:
                self._report_secondary_failures(
                    poison_reason="rollback recovery after interruption failed",
                    context="database deferred-interruption recovery warning",
                    errors=[str(exc)],
                )
                raise interruption
            raise
        if interruption is not None:
            raise interruption

    def _recover_cutover_failure(
        self,
        enabled: list[tuple[str, str, str]],
        primary_exc: BaseException,
    ) -> None:
        recovery_errors: list[str] = []
        recovery_interruption: BaseException | None = None
        poison_before_recovery = self.poison_reason
        for attempt in range(2):
            try:
                self._recover_after_live_mutation(enabled)
            except BaseException as exc:
                recovery_interruption = _first_interruption(
                    recovery_interruption, exc
                )
                if (
                    recovery_interruption is not None
                    and self.boundary_state != "recovery-proven"
                    and attempt == 0
                ):
                    continue
                if self.boundary_state != "recovery-proven":
                    recovery_errors.append(str(exc))
            break
        self._clear_provisional_recovery_poison(poison_before_recovery)
        self._report_secondary_failures(
            poison_reason="recovery after live mutation was not fully proven",
            context="database post-mutation recovery warning",
            errors=recovery_errors,
        )
        if (
            _first_interruption(None, primary_exc) is None
            and recovery_interruption is not None
        ):
            raise recovery_interruption

    def _clear_provisional_recovery_poison(
        self, poison_before_recovery: str | None
    ) -> None:
        if (
            self.boundary_state == "recovery-proven"
            and poison_before_recovery is None
            and self.poison_reason == DEFERRED_RECOVERY_POISON
        ):
            self.poison_reason = None

    def _begin_cutover_mutation(self) -> None:
        self.cutover_started = True
        self.boundary_state = "cutover-mutated"

    def _owned_run(self, role: str, command: list[str], *, timeout: int | None = None):
        name = self.runner.unique_name(role)
        if timeout is None:
            self.runner.register_container(name)
        else:
            self.runner.register_container(name, timeout=timeout)
        try:
            result = self.runner.run(
                [
                    "docker", "run", "--pull=never", "--name", name,
                    "--label", f"{OWNER_LABEL}={self.token}",
                    "--label", f"{SCOPE_LABEL}={self.runner.scope}",
                    "--label", f"{ROLE_LABEL}={role}",
                    *command,
                ],
                timeout=timeout,
            )
        except BaseException:
            self._remove_owned_container_after(name, preserve_primary=True)
            raise
        else:
            self._remove_owned_container_after(name, preserve_primary=False)
            return result

    def _copy_volume(self, source: str, target: str, role: str) -> None:
        self._owned_run(
            role,
            [
                "--network", "none",
                "-v", f"{source}:/source:ro",
                "-v", f"{target}:/target",
                HELPER_IMAGE,
                "sh", "-ec",
                "find /target -mindepth 1 -delete; "
                "set -o pipefail; (cd /source && tar cpf - .) | (cd /target && tar xpf -); sync",
            ],
        )

    def _verify_volume_copy(self, source: str, target: str, role: str) -> None:
        self._owned_run(
            role,
            [
                "--network", "none",
                "-v", f"{source}:/source:ro", "-v", f"{target}:/target:ro",
                "--tmpfs", "/compare:rw,noexec,nosuid,size=64m",
                HELPER_IMAGE, "sh", "-ec",
                "manifest() { root=$1; out=$2; "
                "(cd \"$root\" && find . -xdev -type f -print | LC_ALL=C sort | "
                "while IFS= read -r file; do sha256sum \"$file\"; done) >\"$out\"; }; "
                "manifest /source /compare/source; manifest /target /compare/target; "
                "cmp /compare/source /compare/target",
            ],
        )

    def _start_owned(
        self, role: str, command: list[str], *, env: dict[str, str] | None = None
    ) -> str:
        name = self.runner.unique_name(role)
        self.runner.register_container(name)
        try:
            self.runner.run(
                [
                    "docker", "run", "--pull=never", "-d", "--name", name,
                    "--label", f"{OWNER_LABEL}={self.token}",
                    "--label", f"{SCOPE_LABEL}={self.runner.scope}",
                    "--label", f"{ROLE_LABEL}={role}",
                    *command,
                ],
                env=env,
            )
        except BaseException:
            self._remove_owned_container_after(name, preserve_primary=True)
            raise
        return name

    def _wait_exec(
        self, container: str, command: list[str], label: str,
        *, env: dict[str, str] | None = None,
        exec_env: tuple[str, ...] = (),
    ) -> str:
        deadline = time.monotonic() + self.timeout
        last = ""
        while time.monotonic() < deadline:
            result = self.runner.run(
                [
                    "docker", "exec",
                    *(item for name in exec_env for item in ("-e", name)),
                    container, *command,
                ],
                check=False, timeout=15, env=env,
            )
            if result.returncode == 0:
                return result.stdout
            last = result.stderr
            time.sleep(1)
        raise ContractError(f"{label} did not become healthy: {last.strip()}")

    def _validate_neo4j_data_volume(self, volume: str, role: str) -> None:
        child_env = {
            **os.environ,
            "NEO4J_AUTH": f"{self.neo_user}/{self.neo_password}",
            "NEO4J_USERNAME": self.neo_user,
            "NEO4J_PASSWORD": self.neo_password,
        }
        container = self._start_owned(
            role,
            [
                "-e", "NEO4J_AUTH", "-e", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
                "-v", f"{volume}:/data", NEO4J_IMAGE,
            ],
            env=child_env,
        )
        try:
            output = self._wait_exec(
                container,
                [
                    "sh", "-c",
                    'exec cypher-shell -u "$NEO4J_USERNAME" -p "$NEO4J_PASSWORD" '
                    "-d system \"SHOW DATABASES YIELD name,currentStatus WHERE "
                    "name IN ['system','neo4j'] AND currentStatus='online' RETURN name ORDER BY name\"",
                ],
                "staged Neo4j 5.26.27",
                env=child_env,
                exec_env=("NEO4J_USERNAME", "NEO4J_PASSWORD"),
            )
            if not re.search(r"(?m)^\s*\"?neo4j\"?\s*$", output) or not re.search(
                r"(?m)^\s*\"?system\"?\s*$", output
            ):
                raise ContractError("staged Neo4j did not report both databases online")
        except BaseException:
            self._remove_owned_container_after(container, preserve_primary=True)
            raise
        else:
            self._remove_owned_container_after(container, preserve_primary=False)

    def validate_neo4j_stage(self, artifact_volume: str, artifact_stage: str) -> str:
        stage_volume = self.runner.create_volume("neo-stage")
        self.stage["neo4j"] = stage_volume
        scripts = self.repo / "services/neo4j/build/scripts"
        self._owned_run(
            "neo-load",
            [
                "--network", "none",
                "-e", f"BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS={self.timeout}",
                "-v", f"{stage_volume}:/data",
                "-v", f"{artifact_volume}:/restore:ro",
                "--tmpfs", "/reports:rw,noexec,nosuid,size=64m",
                "-v", f"{scripts}:/scripts:ro",
                "-e", "NEO4J_REPORT_ROOT=/reports",
                "--entrypoint", "bash", NEO4J_IMAGE,
                "/scripts/offline-restore.sh", f"/restore/{artifact_stage}/neo4j",
            ],
        )
        self._validate_neo4j_data_volume(stage_volume, "neo-validate")
        return stage_volume

    def _weaviate_json(self, container: str, path: str, *, method: str = "GET", body: str = "") -> dict:
        command = ["wget", "-qO-", "--timeout=10"]
        if method == "POST":
            command += ["--header=Content-Type: application/json", f"--post-data={body}"]
        elif method == "DELETE":
            command += ["--method=DELETE"]
        command.append(f"http://127.0.0.1:8080{path}")
        result = self.runner.run(["docker", "exec", container, *command], timeout=20)
        if len(result.stdout.encode()) > 65536:
            raise ContractError("Weaviate response exceeds 64 KiB")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ContractError("Weaviate returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise ContractError("Weaviate returned a non-object response")
        return value

    def _weaviate_cancel(self, container: str, path: str) -> None:
        result = self.runner.run(
            [
                "docker", "exec", container, "wget", "-qO-", "--timeout=10",
                "--method=DELETE", f"http://127.0.0.1:8080{path}",
            ],
            check=False,
            timeout=20,
        )
        if result.returncode != 0:
            raise ContractError("Weaviate cancellation request failed")
        if result.stdout.strip():
            try:
                response = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise ContractError("Weaviate cancellation returned malformed JSON") from exc
            if not isinstance(response, dict):
                raise ContractError("Weaviate cancellation returned a non-object response")

    def _validate_weaviate_runtime(self, container: str) -> None:
        self._wait_exec(
            container,
            ["wget", "-qO-", "--timeout=10", "http://127.0.0.1:8080/v1/.well-known/ready"],
            "staged Weaviate 1.38.13",
        )
        meta = self._weaviate_json(container, "/v1/meta")
        if meta.get("version") != "1.38.13":
            raise ContractError("staged Weaviate exact-version validation failed")

    def _validate_weaviate_data_api(self, container: str) -> None:
        schema = self._weaviate_json(container, "/v1/schema")
        objects = self._weaviate_json(container, "/v1/objects?limit=1")
        if not isinstance(schema.get("classes"), list):
            raise ContractError("staged Weaviate schema is unreadable")
        if (
            not isinstance(objects.get("objects"), list)
            or not isinstance(objects.get("totalResults"), int)
            or objects["totalResults"] < 0
        ):
            raise ContractError("staged Weaviate object store is unreadable")

    def _validate_weaviate_data_volume(self, volume: str, role: str) -> None:
        container = self._start_owned(
            role,
            [
                "-e", "AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED=true",
                "-e", "PERSISTENCE_DATA_PATH=/var/lib/weaviate",
                "-e", "CLUSTER_HOSTNAME=weaviate",
                "-e", f"ENABLE_MODULES={self.weaviate_modules}",
                "-e", "BACKUP_FILESYSTEM_PATH=/backups",
                "-e", "DEFAULT_VECTORIZER_MODULE=none",
                "--tmpfs", "/backups:rw,noexec,nosuid,size=64m",
                "-v", f"{volume}:/var/lib/weaviate",
                WEAVIATE_IMAGE,
            ],
        )
        try:
            self._validate_weaviate_runtime(container)
            self._validate_weaviate_data_api(container)
        except BaseException:
            self._remove_owned_container_after(container, preserve_primary=True)
            raise
        else:
            self._remove_owned_container_after(container, preserve_primary=False)

    def validate_weaviate_stage(
        self, artifact_volume: str, artifact_stage: str, snapshot_id: str
    ) -> str:
        stage_volume = self.runner.create_volume("weaviate-stage")
        self.stage["weaviate"] = stage_volume
        container = self._start_owned(
            "weaviate-validate",
            [
                "-e", "AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED=true",
                "-e", "PERSISTENCE_DATA_PATH=/var/lib/weaviate",
                "-e", "CLUSTER_HOSTNAME=weaviate",
                "-e", f"ENABLE_MODULES={self.weaviate_modules}",
                "-e", "DEFAULT_VECTORIZER_MODULE=none",
                "-v", f"{stage_volume}:/var/lib/weaviate",
                "-v", f"{artifact_volume}:/restore:ro",
                "-e", f"BACKUP_FILESYSTEM_PATH=/restore/{artifact_stage}/weaviate",
                WEAVIATE_IMAGE,
            ],
        )
        try:
            self._wait_exec(
                container,
                ["wget", "-qO-", "--timeout=10", "http://127.0.0.1:8080/v1/.well-known/ready"],
                "empty staged Weaviate 1.38.13",
            )
            response = self._weaviate_json(
                container,
                f"/v1/backups/filesystem/{snapshot_id}/restore",
                method="POST",
                body="{}",
            )
            status = response.get("status")
            if not isinstance(status, str):
                raise ContractError("Weaviate restore start omitted status")
            deadline = time.monotonic() + self.timeout
            while weaviate_status_kind(status) == "pending":
                if time.monotonic() >= deadline:
                    self._weaviate_cancel(container, f"/v1/backups/filesystem/{snapshot_id}/restore")
                    raise ContractError("staged Weaviate restore timed out and was canceled")
                time.sleep(1)
                response = self._weaviate_json(container, f"/v1/backups/filesystem/{snapshot_id}/restore")
                status = response.get("status")
                if not isinstance(status, str):
                    raise ContractError("Weaviate restore status omitted status")
            if weaviate_status_kind(status) != "success":
                raise ContractError(f"staged Weaviate restore failed: {status}")
            self._validate_weaviate_runtime(container)
            self._validate_weaviate_data_api(container)
        except BaseException:
            self._remove_owned_container_after(container, preserve_primary=True)
            raise
        else:
            self._remove_owned_container_after(container, preserve_primary=False)
        return stage_volume

    def _prepare(self, timestamp: str) -> dict[str, str]:
        artifact_volume = self.runner.create_volume("restore-artifacts")
        self.stage["artifacts"] = artifact_volume
        name = self.runner.unique_name("prepare")
        prepare_timeout = max(self.timeout, 900)
        self.runner.register_container(name, timeout=prepare_timeout)
        result = self.runner.run(
            [
                "docker", "compose", "run", "--pull", "never", "--rm", "--no-deps",
                "--name", name, "--label", f"{OWNER_LABEL}={self.token}",
                "--label", f"{SCOPE_LABEL}={self.runner.scope}",
                "--label", f"{ROLE_LABEL}=prepare",
                "-v", f"{artifact_volume}:/database-restore",
                "-e", f"BACKUP_TIMESTAMP={timestamp}",
                "-e", f"BACKUP_RESTORE_TOKEN={self.token}",
                "-e", "DATABASE_RESTORE_ROOT=/database-restore",
                "-e", f"BACKUP_NEO4J_SOURCE={'container' if self.plan.neo4j else 'disabled'}",
                "-e", f"BACKUP_WEAVIATE_SOURCE={'container' if self.plan.weaviate else 'disabled'}",
                "backup", "/scripts/restore-databases.sh", "prepare",
            ],
            timeout=prepare_timeout,
        )
        self.containers_disappeared_after_compose_run(name)
        return parse_prepared_plan(result.stdout, token=self.token, timestamp=timestamp)

    def containers_disappeared_after_compose_run(self, name: str) -> None:
        # `--rm` removes a successful job. A killed job is still ownership-checked.
        if self.runner._inspect_json("container", name) is not None:
            self.runner.remove_container(name)
        else:
            self.runner.containers.discard(name)
            self.runner.container_create_timeouts.pop(name, None)

    def _finish_compose_job(self, name: str, *, preserve_primary: bool) -> None:
        try:
            if preserve_primary:
                # A failed create-capable command may become daemon-visible after
                # the first absent inspection. Keep its preregistered authority
                # through the full command-specific reconciliation window.
                self.runner.remove_container(name)
            else:
                self.containers_disappeared_after_compose_run(name)
        except BaseException as exc:
            self._report_secondary_failures(
                poison_reason="owned container cleanup was not proven",
                context="database compose-job cleanup warning",
                errors=[str(exc)],
            )
            if not preserve_primary:
                raise

    def _rollback_and_restore(self, enabled: list[tuple[str, str, str]]) -> None:
        errors: list[str] = []
        interruption: BaseException | None = None
        verified: set[str] = set()
        try:
            self.runner.assert_no_owned_containers()
            self._require_stopped(enabled)
        except ContractError as exc:
            self._mark_poison("recovery could not prove an exclusive stopped boundary")
            raise
        for database, live, _stage in enabled:
            rollback = self.rollback.get(database)
            if not rollback:
                errors.append(f"rollback volume is unavailable for {database}")
                continue
            try:
                self._copy_volume(rollback, live, f"{database}-rollback-restore")
                self._verify_volume_copy(rollback, live, f"{database}-rollback-verify")
                verified.add(database)
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                errors.append(str(exc))
                interruption = _first_interruption(interruption, exc)
        try:
            self._restore_initial_states(enabled, restartable=verified)
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            errors.append(str(exc))
            interruption = _first_interruption(interruption, exc)
        if errors:
            self._report_secondary_failures(
                poison_reason="recovery copy, verification, restart, or health proof failed",
                context="database rollback recovery warning",
                errors=errors,
            )
            if interruption is not None:
                raise interruption
            raise ContractError("rollback recovery failed: " + "; ".join(errors))
        self.boundary_state = "recovery-proven"

    def restore_rollback(self, enabled: list[tuple[str, str, str]]) -> None:
        self._rollback_and_restore(enabled)

    def _restart_snapshot_services(
        self, stopped: list[tuple[str, str]], *, preserve_primary: bool
    ) -> None:
        restart_errors: list[str] = []
        interruption: BaseException | None = None
        for _database, service in stopped:
            try:
                self.compose(
                    "up", "-d", "--no-deps", "--wait", "--wait-timeout",
                    str(self.timeout), service,
                )
                restored = self._service_state(service)
                if not restored.running or not restored.healthy:
                    raise ContractError(f"service {service} restart health was not proven")
            except BaseException as exc:
                interruption = _first_interruption(interruption, exc)
                restart_errors.append(str(exc))
        self._report_secondary_failures(
            poison_reason="snapshot retention restart was not fully proven",
            context="database snapshot retention restart warning",
            errors=restart_errors,
        )
        if restart_errors and not preserve_primary:
            if interruption is not None:
                raise interruption
            raise ContractError(
                "snapshot retention restart failed: " + "; ".join(restart_errors)
            )

    def _enabled_snapshot_services(self) -> list[tuple[str, str]]:
        candidates = (
            (self.plan.neo4j, "neo4j", "neo4j-graph-db"),
            (self.plan.weaviate, "weaviate", "weaviate"),
        )
        return [
            (database, service)
            for enabled, database, service in candidates
            if enabled
        ]

    def prune_completed_database_snapshots(self) -> None:
        """Prune completed local artifacts only inside a bounded quiesced boundary."""
        retention = self._bounded_count("BACKUP_LOCAL_SNAPSHOT_RETENTION_COUNT", "3", 100)
        services = self._enabled_snapshot_services()
        initial = {
            database: self._service_state(service) for database, service in services
        }
        for database, state in initial.items():
            if state.running and not state.healthy:
                raise ContractError(f"service {database} is running without proven health")
        stopped: list[tuple[str, str]] = []
        try:
            for database, service in services:
                if initial[database].running:
                    stopped.append((database, service))
                    self.compose("stop", "--timeout", str(self.timeout), service)
                    if self._service_state(service).running:
                        raise ContractError(f"service {service} is not proven stopped")
            name = self.runner.unique_name("snapshot-prune")
            self.runner.register_container(name)
            self.runner.run(
                [
                    "docker", "compose", "run", "--pull", "never", "--rm", "--no-deps",
                    "--name", name, "--label", f"{OWNER_LABEL}={self.token}",
                    "--label", f"{SCOPE_LABEL}={self.runner.scope}",
                    "--label", f"{ROLE_LABEL}=snapshot-prune",
                    "-e", "BACKUP_SOURCE=container",
                    "-e", "BACKUP_DATABASE_SERVICES_QUIESCED=true",
                    "-e", f"BACKUP_LOCAL_SNAPSHOT_RETENTION_COUNT={retention}",
                    "backup", "/scripts/database-snapshots.sh", "prune",
                ],
                timeout=self.timeout,
            )
            self.containers_disappeared_after_compose_run(name)
        except BaseException:
            self._restart_snapshot_services(stopped, preserve_primary=True)
            raise
        else:
            self._restart_snapshot_services(stopped, preserve_primary=False)

    def cutover(self, prepared: dict[str, str]) -> set[str]:
        enabled: list[tuple[str, str, str]] = []
        if self.plan.neo4j:
            enabled.append(("neo4j", self.neo_live, self.stage["neo4j"]))
        if self.plan.weaviate:
            enabled.append(("weaviate", self.weaviate_live, self.stage["weaviate"]))
        try:
            for database, _live, _stage in enabled:
                service = "neo4j-graph-db" if database == "neo4j" else "weaviate"
                state = self._service_state(service)
                if state.running and not state.healthy:
                    raise ContractError(f"service {service} is running without proven health")
                self.initial_states[database] = state
                self.was_running[database] = state.running
            for database, _live, _stage in enabled:
                service = "neo4j-graph-db" if database == "neo4j" else "weaviate"
                if self.was_running[database]:
                    self.compose("stop", "--timeout", str(self.timeout), service)
                    stopped = self._service_state(service)
                    if stopped.running:
                        raise ContractError(f"service {service} is not proven stopped")
            self._require_stopped(enabled)
        except BaseException:
            self._mark_poison("quiesce command or exact stopped-state proof failed")
            if self.initial_states:
                try:
                    self._restore_exact_initial_states(enabled)
                except BaseException:
                    pass
            raise

        pending: dict[str, str] = {}
        try:
            for database, live, _stage in enabled:
                rollback = self.runner.create_volume(f"{database}-rollback")
                pending[database] = rollback
                self._copy_volume(live, rollback, f"{database}-rollback-copy")
                self._verify_volume_copy(live, rollback, f"{database}-rollback-verify")
            self.rollback.update(pending)
            pending.clear()
            self.runner.assert_no_owned_containers()
            self._require_stopped(enabled)
            self.boundary_state = "rollback-ready"
        except BaseException:
            cleanup_errors: list[str] = []
            for rollback in pending.values():
                try:
                    self.runner.remove_volume(rollback)
                except BaseException as exc:
                    cleanup_errors.append(str(exc))
            try:
                self._restore_exact_initial_states(enabled)
            except BaseException as exc:
                cleanup_errors.append(str(exc))
            self._report_secondary_failures(
                poison_reason="rollback preparation cleanup was not fully proven",
                context="database rollback preparation cleanup warning",
                errors=cleanup_errors,
            )
            raise

        try:
            self._begin_cutover_mutation()
            for database, live, stage in enabled:
                self._copy_volume(stage, live, f"{database}-cutover")
                self._verify_volume_copy(stage, live, f"{database}-cutover-verify")
                if database == "neo4j":
                    self._validate_neo4j_data_volume(live, "neo-live-validate")
                else:
                    self._validate_weaviate_data_volume(live, "weaviate-live-validate")
            self.runner.assert_no_owned_containers()
            for database, _live, _stage in enabled:
                service = "neo4j-graph-db" if database == "neo4j" else "weaviate"
                if self.was_running[database]:
                    self.compose("up", "-d", "--no-deps", "--wait", "--wait-timeout", str(self.timeout), service)
            self._restore_exact_initial_states(enabled)
            retained = set(self.rollback.values())
            self.boundary_state = "committed"
        except BaseException as primary_exc:
            self._recover_cutover_failure(enabled, primary_exc)
            raise
        try:
            keep = self._bounded_count("BACKUP_LOCAL_ROLLBACK_RETENTION_COUNT", "1", 20)
            self.runner.prune_retained_rollbacks(retained, keep=keep)
        except ContractError as exc:
            print(f"database restore housekeeping warning: {exc}", file=sys.stderr)
        return retained

    def restore(self, timestamp: str) -> set[str]:
        prepared = self._prepare(timestamp)
        artifacts = self.stage["artifacts"]
        if self.plan.neo4j:
            if prepared["neo4j_state"] != "complete":
                raise ContractError("selected Neo4j container source is absent from this snapshot")
            self.validate_neo4j_stage(artifacts, prepared["artifact_stage"])
        elif prepared["neo4j_state"] != "disabled":
            raise ContractError("Neo4j snapshot state does not match disabled source")
        if self.plan.weaviate:
            if prepared["weaviate_state"] != "complete":
                raise ContractError("selected Weaviate container source is absent from this snapshot")
            self.validate_weaviate_stage(
                artifacts,
                prepared["artifact_stage"],
                prepared["weaviate_snapshot_id"],
            )
        elif prepared["weaviate_state"] != "disabled":
            raise ContractError("Weaviate snapshot state does not match disabled source")
        return self.cutover(prepared)

    def _restore_neo4j_after_backup(
        self, service: str, *, preserve_primary: bool = False
    ) -> None:
        running_now = False
        probe_interruption: BaseException | None = None
        try:
            running_now = self._service_running(service)
        except (SignalInterruption, KeyboardInterrupt, SystemExit) as exc:
            probe_interruption = exc
        except BaseException:
            running_now = False
        if not running_now:
            try:
                self.compose(
                    "up", "-d", "--no-deps", "--wait", "--wait-timeout",
                    str(self.timeout), service,
                )
                restored = self._service_state(service)
                if not restored.running or not restored.healthy:
                    raise ContractError("Neo4j restart health was not proven")
            except BaseException as exc:
                self._mark_poison("backup restart compensation was not proven")
                if preserve_primary or probe_interruption is not None:
                    print(f"database backup restart warning: {exc}", file=sys.stderr)
                if preserve_primary:
                    return
                if probe_interruption is not None:
                    raise probe_interruption
                raise
        if probe_interruption is not None and not preserve_primary:
            raise probe_interruption

    def backup_neo4j(self, timestamp: str) -> None:
        """Install compensation before the first potentially effective stop."""
        service = "neo4j-graph-db"
        initial_state = self._service_state(service)
        if initial_state.running and not initial_state.healthy:
            self._mark_poison("backup initial Neo4j health could not be proven")
            raise ContractError("Neo4j is running without proven health")
        initially_running = initial_state.running
        try:
            if initially_running:
                self.compose("stop", "--timeout", str(self.timeout), service)
            name = self.runner.unique_name("neo-backup")
            self.runner.register_container(name)
            try:
                self.runner.run(
                    [
                        "docker", "compose", "run", "--pull", "never", "--rm", "--no-deps",
                        "--name", name,
                        "--label", f"{OWNER_LABEL}={self.token}",
                        "--label", f"{SCOPE_LABEL}={self.runner.scope}",
                        "--label", f"{ROLE_LABEL}=neo-backup",
                        "-e", f"BACKUP_TIMESTAMP={timestamp}",
                        "-e", f"BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS={self.timeout}",
                        "--entrypoint", "bash", service, "/scripts/offline-backup.sh",
                    ]
                )
            except BaseException:
                self._finish_compose_job(name, preserve_primary=True)
                raise
            else:
                self._finish_compose_job(name, preserve_primary=False)
        except BaseException:
            if initially_running:
                self._restore_neo4j_after_backup(service, preserve_primary=True)
            raise
        else:
            if initially_running:
                self._restore_neo4j_after_backup(service)


def _lock_path(repo: Path) -> Path:
    digest = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:24]
    return Path(os.environ.get("TMPDIR", "/tmp")) / f"atlas-database-boundary-{digest}.lock"


def _signal_as_exception(signum, _frame):
    # Do not use InterruptedError: subprocess treats that OSError subtype as
    # retryable EINTR and can defer cleanup until the child command times out.
    raise SignalInterruption(f"received signal {signum}")


def finalize_boundary_lock(
    lock: OwnedFileLock,
    coordinator: DatabaseCoordinator | None,
    *,
    retained: set[str],
    raise_on_failure: bool = False,
) -> None:
    reasons: list[str] = []
    cleanup_error: Exception | None = None
    if coordinator is not None:
        if coordinator.poison_reason:
            reasons.append(coordinator.poison_reason)
        if getattr(coordinator.runner, "process_group_cleanup_failed", False):
            reasons.append("owned process-group cleanup was not proven")
        try:
            coordinator.runner.cleanup(retain_volumes=retained)
        except Exception as exc:
            cleanup_error = exc
            reasons.append(str(exc))
    if reasons:
        lock.poison("; ".join(reasons))
        if raise_on_failure:
            if cleanup_error is not None:
                raise cleanup_error
            raise ContractError("database boundary requires verified manual recovery")
        return
    lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("backup", "restore"))
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    requested_test_token = os.environ.get("ATLAS_DATABASE_BACKUP_TEST_TOKEN", "")
    if requested_test_token:
        if os.environ.get("ATLAS_DATABASE_BACKUP_LIVE_INTEGRATION") != "1" or not TOKEN_RE.fullmatch(requested_test_token):
            raise ContractError("test token requires explicit live integration mode and full 128-bit hex")
        token = requested_test_token
    else:
        token = secrets.token_hex(16)
    timeout_text = os.environ.get("BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS", "120")
    if not timeout_text.isdecimal() or timeout_text.startswith("0") or not 1 <= int(timeout_text) <= 3600:
        raise ContractError("BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS must be a canonical integer from 1 to 3600")
    timeout = int(timeout_text)
    values = _env_file_values(repo)
    plan = source_plan(
        _setting(values, "NEO4J_GRAPH_DB_SOURCE", "container"),
        _setting(values, "WEAVIATE_SOURCE", "container"),
    )
    if args.operation == "restore" and os.environ.get("BACKUP_RESTORE_MAINTENANCE_MODE") != "confirmed":
        raise ContractError("set BACKUP_RESTORE_MAINTENANCE_MODE=confirmed after quiescing all database writers")
    timestamp = os.environ.get("BACKUP_TIMESTAMP")
    if args.operation == "restore":
        if not timestamp:
            raise ContractError("BACKUP_TIMESTAMP is required for database restore")
        timestamp = validate_backup_timestamp(timestamp)
    else:
        timestamp = validate_backup_timestamp(timestamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))

    lock = OwnedFileLock(_lock_path(repo), token=token)
    coordinator: DatabaseCoordinator | None = None
    retained: set[str] = set()
    for handled in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(handled, _signal_as_exception)
    lock.acquire()
    try:
        coordinator = DatabaseCoordinator(repo, token=token, timeout=timeout)
        if args.operation == "restore":
            if not (plan.neo4j or plan.weaviate):
                print("database restore orchestrator: both database sources are disabled; nothing to do")
                return 0
            retained = coordinator.restore(timestamp)
            print(f"database restore orchestrator: validated cutover complete for {timestamp}")
            for volume in sorted(retained):
                print(f"database restore orchestrator: retained rollback volume {volume}")
            return 0

        # Backup remains source-aware and never starts a disabled dependency.
        if plan.neo4j:
            coordinator.backup_neo4j(timestamp)
        name = coordinator.runner.unique_name("backup")
        backup_timeout = max(timeout, 900)
        coordinator.runner.register_container(name, timeout=backup_timeout)
        coordinator.runner.run([
            "docker", "compose", "run", "--pull", "never", "--rm", "--no-deps",
            "--name", name, "--label", f"{OWNER_LABEL}={token}",
            "--label", f"{SCOPE_LABEL}={coordinator.runner.scope}",
            "--label", f"{ROLE_LABEL}=backup",
            "-e", f"BACKUP_TIMESTAMP={timestamp}",
            "-e", f"BACKUP_NEO4J_SOURCE={'container' if plan.neo4j else 'disabled'}",
            "-e", f"BACKUP_WEAVIATE_SOURCE={'container' if plan.weaviate else 'disabled'}",
            "-e", "BACKUP_DATABASES=true", "backup", "/scripts/backup-all.sh",
        ], timeout=backup_timeout)
        coordinator.containers_disappeared_after_compose_run(name)
        coordinator.prune_completed_database_snapshots()
        print(f"backup orchestrator: complete for {timestamp}")
        return 0
    finally:
        finalize_boundary_lock(
            lock,
            coordinator,
            retained=retained,
            raise_on_failure=sys.exc_info()[0] is None,
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, SignalInterruption) as exc:
        print(f"database orchestrator: {exc}", file=sys.stderr)
        raise SystemExit(64 if isinstance(exc, ContractError) else 130)
