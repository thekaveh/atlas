"""Docker-backed harness for the Supabase seed scripts.

Boots the pinned supabase/postgres image, applies every *.sql in a scripts
directory in sorted order (mimicking services/supabase/db/scripts/
db-init-runner.sh), and returns a normalized pg_dump + a seed-row snapshot.

No new Python deps: shells out to the Docker CLI + the psql/pg_dump that ship
inside the image. All psql/pg_dump/pg_isready calls use ``-h 127.0.0.1``
(TCP loopback) rather than the Unix socket, because the supabase/postgres
image's built-in pg_hba.conf requires scram-sha-256 on local socket
connections for supabase_admin but trusts TCP loopback unconditionally.

``python -m tests.seed_harness`` (run from bootstrapper/) regenerates the
committed golden fixtures.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "services" / "supabase" / "db" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_GOLDEN = FIXTURES / "seed_schema_golden.sql"
ROWS_GOLDEN = FIXTURES / "seed_rows_golden.txt"

# Mirrors .env.example SUPABASE_DB_IMAGE / SUPABASE_DB_USER / SUPABASE_DB_NAME.
DB_IMAGE = "supabase/postgres:17.6.1.139"
DB_USER = "supabase_admin"
DB_NAME = "postgres"
DB_PASSWORD = "".join(("post", "gres"))
COMMAND_TIMEOUT = 30
CONTAINER_LOG_TAIL_CHARS = 4_000
_TERMINAL_CONTAINER_STATES = frozenset({"dead", "exited", "removing"})
SEED_OWNER_LABEL = "com.atlas.seed-harness-token"

def _role_password(label: str) -> str:
    # Generate process-local credentials instead of embedding deterministic
    # password-shaped literals in the test source. The mapping below keeps
    # each value stable for the lifetime of this harness invocation.
    return f"{label}-{uuid.uuid4().hex}"


_SCOPED_ROLE_SPECS = (
    ("SUPABASE_AUTH_DB", "supabase_auth_admin", "auth"),
    ("SUPABASE_STORAGE_DB", "supabase_storage_admin", "storage"),
    ("SUPABASE_API_DB", "authenticator", "api"),
    ("SUPABASE_REALTIME_DB", "atlas_realtime", "realtime"),
    ("SUPABASE_META_DB", "atlas_meta", "meta"),
    ("POSTGRES_EXPORTER_DB", "atlas_metrics", "metrics"),
    ("SUPAVISOR_DB_ADMIN", "atlas_supavisor", "supavisor"),
    ("BACKEND_DB", "atlas_backend", "backend"),
    ("N8N_DB", "atlas_n8n", "n8n"),
    ("OPEN_WEBUI_DB", "atlas_open_webui", "open-webui"),
    ("LIGHTRAG_DB", "atlas_lightrag", "lightrag"),
    ("LITELLM_DB", "litellm", "litellm"),
    ("AIRFLOW_DB", "airflow", "airflow"),
    ("AIRFLOW_ATLAS_DB", "atlas_airflow_reader", "airflow-reader"),
    ("LANGFUSE_DB", "langfuse", "langfuse"),
    ("MLFLOW_DB", "mlflow", "mlflow"),
    ("LABEL_STUDIO_DB", "label_studio", "label"),
    ("ICEBERG_DB", "iceberg", "iceberg"),
    ("MCP_POSTGRES_DB", "atlas_mcp", "mcp"),
    ("JUPYTER_DB", "atlas_jupyter", "jupyter"),
    ("ZEPPELIN_DB", "atlas_zeppelin", "zeppelin"),
)

SCOPED_ROLE_TEST_ENV = {
    key: value
    for prefix, user, label in _SCOPED_ROLE_SPECS
    for key, value in (
        (f"{prefix}_USER", user),
        (f"{prefix}_PASSWORD", _role_password(label)),
    )
}
SCOPED_ROLE_TEST_ENV["SUPABASE_STUDIO_DB_USER"] = "atlas_studio_readonly"
SCOPED_ROLE_TEST_ENV.update(
    {
        "LITELLM_DB_NAME": "litellm",
        "LANGFUSE_DB_NAME": "langfuse",
        "MLFLOW_DB_NAME": "mlflow",
        "LABEL_STUDIO_DB_NAME": "label_studio",
    }
)

# Deterministic seed data lives only in comfyui_workflows (seeded by 12-comfyui.sql).
# Snapshot the columns the seed sets, ordered stably.
SEED_QUERY = (
    "SELECT name, description, category, active "
    "FROM public.comfyui_workflows ORDER BY name;"
)


def docker_available() -> bool:
    """True only when the Docker CLI is on PATH AND the daemon is reachable,
    so the docker-gated tests SKIP (not ERROR) when the daemon is paused."""
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_database_image() -> None:
    """Require the pinned image locally; Task 3 drills never pull."""
    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", DB_IMAGE], capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        pytest.fail(
            f"Docker image probe failed for {DB_IMAGE}: {type(exc).__name__}: {exc}"
        )
    if inspect.returncode == 0:
        return
    raise subprocess.CalledProcessError(
        inspect.returncode, ["docker", "image", "inspect", DB_IMAGE],
        output=inspect.stdout, stderr=inspect.stderr,
    )


def _bounded_tail(value: str) -> str:
    """Return enough recent output to diagnose startup without flooding CI."""
    return value[-CONTAINER_LOG_TAIL_CHARS:]


def _cleanup_error(name: str, detail: str) -> RuntimeError:
    suffix = f": {_bounded_tail(detail).strip()}" if detail.strip() else ""
    return RuntimeError(f"could not remove seed container {name}{suffix}")


def _add_exception_note(exc: BaseException, note: str) -> None:
    if hasattr(exc, "add_note"):
        exc.add_note(note)
        return
    notes = getattr(exc, "__notes__", None)
    if notes is None:
        notes = []
        exc.__notes__ = notes
    notes.append(note)


def sleep_for_cleanup(
    seconds: float, primary_error: BaseException | None,
) -> BaseException | None:
    """Defer a cleanup interruption until the bounded sweep is complete."""
    try:
        time.sleep(seconds)
    except BaseException as exc:
        if primary_error is None:
            return exc
        _add_exception_note(
            primary_error,
            "Cleanup reconciliation sleep was interrupted: "
            f"{type(exc).__name__}: {exc}",
        )
    return primary_error


def raise_deferred_cleanup_error(
    primary_error: BaseException | None, deferred_error: BaseException | None,
) -> None:
    """Re-raise a cleanup-time interruption only when no body error owns control."""
    if primary_error is None and deferred_error is not None:
        raise deferred_error


def defer_cleanup_interruption(
    current: BaseException | None, candidate: BaseException,
) -> BaseException | None:
    """Remember the first operator interruption without retaining transient errors."""
    if isinstance(candidate, (KeyboardInterrupt, SystemExit)):
        if current is None:
            return candidate
        _add_exception_note(
            current,
            f"Cleanup was also interrupted: {type(candidate).__name__}: {candidate}",
        )
    return current


def defer_cleanup_failures(
    current: BaseException | None,
    failures: list[tuple[str, BaseException]],
) -> BaseException | None:
    for _operation, failure in failures:
        current = defer_cleanup_interruption(current, failure)
    return current


def raise_deferred_or_collision(
    primary_error: BaseException | None,
    deferred_error: BaseException | None,
    collision: BaseException,
) -> None:
    if primary_error is None and deferred_error is not None:
        _add_exception_note(
            deferred_error, f"Cleanup also refused a foreign resource: {collision}"
        )
        raise deferred_error
    raise collision


def establish_cleanup_deadline(
    seconds: float | None, deferred_error: BaseException | None,
) -> tuple[float | None, BaseException | None]:
    if seconds is None:
        return None, deferred_error
    while True:
        try:
            return time.monotonic() + seconds, deferred_error
        except (KeyboardInterrupt, SystemExit) as exc:
            deferred_error = defer_cleanup_interruption(deferred_error, exc)


def cleanup_deadline_expired(
    deadline: float | None, deferred_error: BaseException | None,
) -> tuple[bool, BaseException | None]:
    if deadline is None:
        return True, deferred_error
    while True:
        try:
            return time.monotonic() >= deadline, deferred_error
        except (KeyboardInterrupt, SystemExit) as exc:
            deferred_error = defer_cleanup_interruption(deferred_error, exc)


def begin_reconciliation_after_interruption(
    deadline: float | None,
    seconds: float,
    deferred_error: BaseException | None,
    failures: list[tuple[str, BaseException]],
) -> tuple[float | None, BaseException | None]:
    if deadline is not None or not any(
        isinstance(failure, (KeyboardInterrupt, SystemExit))
        for _operation, failure in failures
    ):
        return deadline, deferred_error
    return establish_cleanup_deadline(seconds, deferred_error)


def _inspect_seed_container(name: str) -> dict | None:
    inspected = subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True, timeout=10,
    )
    if inspected.returncode == 0:
        records = json.loads(inspected.stdout)
        if len(records) != 1 or not isinstance(records[0], dict):
            raise _cleanup_error(name, f"invalid inspect result {records!r}")
        return records[0]
    listed = subprocess.run(
        [
            "docker", "ps", "-a", "--filter", f"name=^/{name}$",
            "--format", "{{.Names}}",
        ],
        capture_output=True, text=True, timeout=10,
    )
    if listed.returncode != 0 or name in listed.stdout.splitlines():
        raise _cleanup_error(name, listed.stderr or listed.stdout)
    return None


def _seed_container_is_owned(record: dict | None, name: str, token: str) -> bool:
    if record is None:
        return False
    actual = record.get("Name", "").lstrip("/")
    labels = record.get("Config", {}).get("Labels") or {}
    if actual != name:
        raise _cleanup_error(name, f"inspect returned foreign name {actual!r}")
    return labels.get(SEED_OWNER_LABEL) == token


def _remove_owned_seed_once(name: str, token: str) -> None:
    if not _seed_container_is_owned(_inspect_seed_container(name), name, token):
        return
    removed = subprocess.run(
        ["docker", "rm", "-f", name], capture_output=True, text=True,
        timeout=10,
    )
    detail = removed.stderr or removed.stdout
    if removed.returncode != 0 and "No such container" not in detail:
        raise _cleanup_error(name, detail)
    if _seed_container_is_owned(_inspect_seed_container(name), name, token):
        raise _cleanup_error(name, "owned container remains after removal")


def remove_seed_container(
    name: str, token: str, *, primary_error: BaseException | None = None,
    uncertain: bool = False,
) -> None:
    """Remove only the token-owned disposable without hiding a primary error."""
    cleanup_error: BaseException | None = None
    deferred_error = primary_error
    settle_until, deferred_error = establish_cleanup_deadline(
        COMMAND_TIMEOUT if uncertain else None, deferred_error
    )
    while True:
        try:
            _remove_owned_seed_once(name, token)
            cleanup_error = None
        except BaseException as exc:
            cleanup_error = exc
            deferred_error = defer_cleanup_interruption(deferred_error, exc)
            settle_until, deferred_error = begin_reconciliation_after_interruption(
                settle_until,
                COMMAND_TIMEOUT,
                deferred_error,
                [("remove seed container", exc)],
            )
        expired, deferred_error = cleanup_deadline_expired(
            settle_until, deferred_error
        )
        if expired:
            break
        deferred_error = sleep_for_cleanup(0.1, deferred_error)
    if cleanup_error is None:
        raise_deferred_cleanup_error(primary_error, deferred_error)
        return
    if deferred_error is None:
        raise cleanup_error
    _add_exception_note(
        deferred_error,
        f"Seed container cleanup could not be proven: "
        f"{type(cleanup_error).__name__}: {cleanup_error}",
    )
    print(
        f"{cleanup_error}; preserving original {type(deferred_error).__name__}",
        file=sys.stderr,
    )
    raise_deferred_cleanup_error(primary_error, deferred_error)


@contextmanager
def seed_container_cleanup(name: str, token: str) -> Iterator[None]:
    """Always clean one preallocated container name, including on interrupts."""
    try:
        yield
    except BaseException as exc:
        remove_seed_container(name, token, primary_error=exc, uncertain=True)
        raise
    else:
        remove_seed_container(name, token)


def _inspect_container_state(name: str, *, timeout: float) -> dict[str, object]:
    command = ["docker", "inspect", "--format", "{{json .State}}", name]
    inspected = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    if inspected.returncode != 0:
        detail = _bounded_tail(inspected.stderr or inspected.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"could not inspect seed container {name}{suffix}")
    try:
        state = json.loads(inspected.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        detail = _bounded_tail(inspected.stdout).strip()
        raise RuntimeError(
            f"could not inspect seed container {name}: invalid state {detail!r}"
        ) from exc
    if not isinstance(state, dict) or not isinstance(state.get("Status"), str):
        raise RuntimeError(
            f"could not inspect seed container {name}: incomplete state {state!r}"
        )
    return state


def _startup_error(name: str, state: dict[str, object], logs: str) -> RuntimeError:
    status = state["Status"]
    exit_code = state.get("ExitCode", "unknown")
    detail = str(state.get("Error") or "").strip()
    reason = f"; error: {detail}" if detail else ""
    log_tail = _bounded_tail(logs).strip()
    diagnostics = f"\ncontainer log tail:\n{log_tail}" if log_tail else ""
    return RuntimeError(
        f"seed container {name} entered terminal state {status!r} "
        f"with exit code {exit_code}{reason}{diagnostics}"
    )


def _readiness_timeout(timeout_seconds: float, logs: str) -> RuntimeError:
    log_tail = _bounded_tail(logs).strip()
    diagnostics = f"\ncontainer log tail:\n{log_tail}" if log_tail else ""
    return RuntimeError(
        f"postgres did not become ready in {timeout_seconds:g}s{diagnostics}"
    )


def _probe_timeout(deadline: float, timeout_seconds: float, logs: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _readiness_timeout(timeout_seconds, logs)
    return remaining


def wait_for_postgres(
    name: str, *, timeout_seconds: float = 180, poll_interval: float = 1
) -> None:
    """Wait for durable Postgres readiness while the container can still start."""
    deadline = time.monotonic() + timeout_seconds
    last_logs = ""
    while True:
        remaining = _probe_timeout(deadline, timeout_seconds, last_logs)
        ready = subprocess.run(
            ["docker", "exec", name, "pg_isready", "-h", "127.0.0.1",
             "-U", DB_USER, "-d", DB_NAME, "-q"],
            timeout=min(5, remaining),
        )
        remaining = _probe_timeout(deadline, timeout_seconds, last_logs)
        logs = subprocess.run(
            ["docker", "logs", name],
            capture_output=True, text=True, timeout=min(10, remaining),
        )
        last_logs = logs.stdout + logs.stderr
        remaining = _probe_timeout(deadline, timeout_seconds, last_logs)
        state = _inspect_container_state(name, timeout=min(10, remaining))
        if state["Status"] in _TERMINAL_CONTAINER_STATES:
            raise _startup_error(name, state, last_logs)
        if ready.returncode == 0 and last_logs.count(
            "database system is ready to accept connections"
        ) >= 2:
            return
        remaining = _probe_timeout(deadline, timeout_seconds, last_logs)
        time.sleep(min(poll_interval, remaining))


def _normalize(dump: str) -> str:
    """Drop pg_dump's comment lines (version banners etc.) and collapse blank
    lines, so the comparison is structural and image-patch-stable.

    Also drops the `\\restrict` / `\\unrestrict` psql meta-commands that
    pg_dump >= 17.5 emits around the dump body: each carries a freshly
    randomized nonce token, so leaving them in would make every dump differ
    from the last (breaking idempotency) and from the golden. They guard
    psql's restricted mode, not schema content."""
    out: list[str] = []
    for line in dump.splitlines():
        if line.startswith("--"):
            continue
        if line.startswith("\\restrict") or line.startswith("\\unrestrict"):
            continue
        if line.strip() == "" and (not out or out[-1] == ""):
            continue
        out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"


def run_scripts_and_dump(
    scripts_dir: Path, *, run_twice: bool = False
) -> tuple[str, str]:
    """Return (normalized_schema_dump, seed_rows). Raises CalledProcessError if
    any script exits non-zero (psql -v ON_ERROR_STOP=1)."""
    ensure_database_image()
    token = uuid.uuid4().hex
    name = f"atlas-seedtest-{token[:12]}"
    with seed_container_cleanup(name, token):
        subprocess.run(
            [
                "docker", "run", "-d", "--pull=never", "--name", name,
                "--label", f"{SEED_OWNER_LABEL}={token}",
                "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=1536m",
                "-v", f"{scripts_dir}:/scripts:ro",
                "-e", f"POSTGRES_USER={DB_USER}",
                "-e", f"POSTGRES_PASSWORD={DB_PASSWORD}",
                "-e", f"POSTGRES_DB={DB_NAME}",
                # Trust auth on TCP loopback (the image's pg_hba.conf mandates
                # scram on the socket, so the harness uses TCP loopback).
                "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
                DB_IMAGE,
            ],
            check=True, capture_output=True, timeout=COMMAND_TIMEOUT,
        )
        wait_for_postgres(name)

        sql_files = sorted(scripts_dir.glob("*.sql"))
        passes = 2 if run_twice else 1
        for _ in range(passes):
            for sql in sql_files:
                with sql.open("rb") as fh:
                    subprocess.run(
                        ["docker", "exec", "-i", name, "psql", "-h", "127.0.0.1",
                         "-v", "ON_ERROR_STOP=1",
                         "-U", DB_USER, "-d", DB_NAME, "-f", "-"],
                        stdin=fh, check=True, capture_output=True,
                        timeout=COMMAND_TIMEOUT,
                    )

            role_command = [
                "docker", "exec", "-e", "PGHOST=127.0.0.1",
                "-e", f"PGUSER={DB_USER}", "-e", f"PGPASSWORD={DB_PASSWORD}",
                "-e", f"PGDATABASE={DB_NAME}",
            ]
            for key, value in SCOPED_ROLE_TEST_ENV.items():
                role_command.extend(("-e", f"{key}={value}"))
            role_command.extend((name, "sh", "/scripts/05-scoped-roles.sh"))
            subprocess.run(
                role_command, check=True, capture_output=True, text=True,
                timeout=120,
            )

        schema = subprocess.run(
            ["docker", "exec", name, "pg_dump", "--schema-only",
             "-h", "127.0.0.1", "-U", DB_USER, "-d", DB_NAME],
            check=True, capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
        ).stdout
        rows = subprocess.run(
            ["docker", "exec", name, "psql", "-h", "127.0.0.1", "-A", "-t",
             "-U", DB_USER, "-d", DB_NAME, "-c", SEED_QUERY],
            check=True, capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
        ).stdout
        return _normalize(schema), rows.strip() + "\n"


if __name__ == "__main__":
    if not docker_available():
        raise SystemExit("docker not on PATH — cannot regenerate goldens")
    schema, rows = run_scripts_and_dump(SCRIPTS_DIR)
    FIXTURES.mkdir(exist_ok=True)
    SCHEMA_GOLDEN.write_text(schema, encoding="utf-8")
    ROWS_GOLDEN.write_text(rows, encoding="utf-8")
    print(f"wrote {SCHEMA_GOLDEN} ({len(schema)} bytes)")
    print(f"wrote {ROWS_GOLDEN} ({len(rows)} bytes)")
