"""Failure-atomic restore drills against an explicitly disposable PostgreSQL.

The harness never reads Atlas configuration or mounts an Atlas volume.  It
creates a uniquely named Docker network and PostgreSQL container, and every
subprocess has a finite timeout.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from tests.seed_harness import (
    begin_reconciliation_after_interruption,
    cleanup_deadline_expired,
    defer_cleanup_failures,
    establish_cleanup_deadline,
    raise_deferred_cleanup_error,
    sleep_for_cleanup,
)


REPO = Path(__file__).resolve().parents[2]


def _stage_backup_script_siblings(tmp_path: Path) -> None:
    """Copy the shell libraries that the backup/restore scripts source.

    Both scripts resolve ``s3-client.sh`` and ``database-snapshots.sh`` relative
    to their own directory, so a copy staged into ``tmp_path`` has to carry them
    as well. The repo-root fallback inside the scripts only fires when pytest is
    invoked from the repository root, which is not how CI runs the suite.
    """
    for sibling in ("s3-client.sh", "database-snapshots.sh"):
        destination = tmp_path / sibling
        if not destination.exists():
            destination.write_text(
                (REPO / "services/backup/init/scripts" / sibling).read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
RESTORE = REPO / "services/backup/init/scripts/restore-postgres.sh"
BACKUP = REPO / "services/backup/init/scripts/backup-all.sh"
S3_CLIENT = REPO / "services/backup/init/scripts/s3-client.sh"
POSTGRES_IMAGE = "postgres:17.10-alpine"
COMMAND_TIMEOUT = 30
POSTGRES_CREATE_TIMEOUT = 60
MANIFEST_HMAC_KEY = "a" * 64
DEPLOYMENT_ID = "atlas-test-deployment"
RESTORE_OWNER_LABEL = "com.atlas.restore-safety-token"


def _add_exception_note(exc: BaseException, note: str) -> None:
    if hasattr(exc, "add_note"):
        exc.add_note(note)
        return
    notes = getattr(exc, "__notes__", None)
    if notes is None:
        notes = []
        exc.__notes__ = notes
    notes.append(note)


def _run(*args: str, check: bool = True, timeout: int = COMMAND_TIMEOUT, **kwargs):
    return subprocess.run(
        list(args),
        check=check,
        text=True,
        capture_output=True,
        timeout=timeout,
        **kwargs,
    )


@dataclass(frozen=True)
class DisposablePostgres:
    container: str
    network: str

    def sql(self, database: str, statement: str, *, check: bool = True):
        return _run(
            "docker",
            "exec",
            self.container,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            database,
            "-Atqc",
            statement,
            check=check,
        )

    def create_database(self, name: str) -> None:
        self.sql("template1", f'CREATE DATABASE "{name}" WITH TEMPLATE template0')

    def drop_database(self, name: str) -> None:
        self.sql("template1", f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _capture_disposable_cleanup(
    failures: list[tuple[str, BaseException]], operation: str, action,
) -> None:
    try:
        action()
    except BaseException as exc:
        failures.append((operation, exc))


def _inspect_disposable(kind: str, name: str) -> dict | None:
    inspected = _run("docker", kind, "inspect", name, check=False, timeout=10)
    if inspected.returncode == 0:
        records = json.loads(inspected.stdout)
        assert len(records) == 1 and isinstance(records[0], dict)
        return records[0]
    if kind == "container":
        listed = _run(
            "docker", "ps", "-a", "--filter", f"name=^/{name}$",
            "--format", "{{.Names}}", check=False, timeout=10,
        )
    else:
        listed = _run(
            "docker", "network", "ls", "--filter", f"name=^{name}$",
            "--format", "{{.Name}}", check=False, timeout=10,
        )
    assert listed.returncode == 0
    assert name not in listed.stdout.splitlines()
    return None


def _remove_disposable_if_owned(kind: str, name: str, token: str) -> None:
    record = _inspect_disposable(kind, name)
    if record is None:
        return
    labels = record.get("Labels") or record.get("Config", {}).get("Labels") or {}
    actual = record.get("Name", "").lstrip("/")
    assert actual == name
    if labels.get(RESTORE_OWNER_LABEL) != token:
        return
    if kind == "container":
        _run("docker", "rm", "-f", name, check=False, timeout=10)
    else:
        _run("docker", "network", "rm", name, check=False, timeout=10)


def _assert_no_owned_disposable(container: str, network: str, token: str) -> None:
    for kind, name in (("container", container), ("network", network)):
        record = _inspect_disposable(kind, name)
        if record is None:
            continue
        labels = record.get("Labels") or record.get("Config", {}).get("Labels") or {}
        assert labels.get(RESTORE_OWNER_LABEL) != token


def _assert_no_owned_restore_helper(name: str, token: str) -> None:
    record = _inspect_disposable("container", name)
    if record is None:
        return
    labels = record.get("Config", {}).get("Labels") or record.get("Labels") or {}
    assert labels.get(RESTORE_OWNER_LABEL) != token


def _report_disposable_cleanup_failures(
    primary: BaseException | None, failures: list[tuple[str, BaseException]],
) -> None:
    detail = "; ".join(
        f"{operation}: {type(exc).__name__}: {exc}"
        for operation, exc in failures
    )
    note = f"Disposable PostgreSQL cleanup could not be proven: {detail}"
    if primary is not None:
        _add_exception_note(primary, note)
        return
    cleanup_error = failures[0][1]
    _add_exception_note(cleanup_error, note)
    raise cleanup_error


def _cleanup_disposable_postgres(
    container: str, network: str, token: str, *, uncertain: bool | None = None,
) -> None:
    primary = sys.exc_info()[1]
    deferred_error = primary
    if uncertain is None:
        uncertain = primary is not None
    settle_until, deferred_error = establish_cleanup_deadline(
        POSTGRES_CREATE_TIMEOUT if uncertain else None, deferred_error
    )
    while True:
        failures: list[tuple[str, BaseException]] = []
        _capture_disposable_cleanup(
            failures, f"container removal {container}",
            lambda: _remove_disposable_if_owned("container", container, token),
        )
        _capture_disposable_cleanup(
            failures, f"network removal {network}",
            lambda: _remove_disposable_if_owned("network", network, token),
        )
        _capture_disposable_cleanup(
            failures, "final absence verification",
            lambda: _assert_no_owned_disposable(container, network, token),
        )
        deferred_error = defer_cleanup_failures(deferred_error, failures)
        settle_until, deferred_error = begin_reconciliation_after_interruption(
            settle_until,
            POSTGRES_CREATE_TIMEOUT,
            deferred_error,
            failures,
        )
        expired, deferred_error = cleanup_deadline_expired(
            settle_until, deferred_error
        )
        if expired:
            if failures:
                _report_disposable_cleanup_failures(deferred_error, failures)
            raise_deferred_cleanup_error(primary, deferred_error)
            return
        deferred_error = sleep_for_cleanup(0.1, deferred_error)


def _cleanup_restore_helper(name: str, token: str, *, uncertain: bool) -> None:
    primary = sys.exc_info()[1]
    deferred_error = primary
    settle_until, deferred_error = establish_cleanup_deadline(
        COMMAND_TIMEOUT if uncertain else None, deferred_error
    )
    while True:
        failures: list[tuple[str, BaseException]] = []
        _capture_disposable_cleanup(
            failures, f"helper removal {name}",
            lambda: _remove_disposable_if_owned("container", name, token),
        )
        _capture_disposable_cleanup(
            failures, f"helper absence {name}",
            lambda: _assert_no_owned_restore_helper(name, token),
        )
        deferred_error = defer_cleanup_failures(deferred_error, failures)
        settle_until, deferred_error = begin_reconciliation_after_interruption(
            settle_until,
            COMMAND_TIMEOUT,
            deferred_error,
            failures,
        )
        expired, deferred_error = cleanup_deadline_expired(
            settle_until, deferred_error
        )
        if expired:
            if failures:
                _report_disposable_cleanup_failures(deferred_error, failures)
            raise_deferred_cleanup_error(primary, deferred_error)
            return
        deferred_error = sleep_for_cleanup(0.1, deferred_error)


@pytest.fixture(scope="module")
def disposable_postgres() -> Iterator[DisposablePostgres]:
    in_ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"}
    if shutil.which("docker") is None:
        if in_ci:
            pytest.fail("docker CLI is required for restore safety drills in CI")
        pytest.skip("docker CLI unavailable")

    try:
        daemon = _run("docker", "info", check=False, timeout=10)
    except (subprocess.SubprocessError, OSError) as exc:
        if not in_ci:
            pytest.skip("docker daemon unavailable")
        pytest.fail(f"Docker daemon probe failure: {type(exc).__name__}: {exc}")
    if daemon.returncode != 0:
        if not in_ci:
            pytest.skip("docker daemon unavailable")
        pytest.fail(f"unexpected Docker daemon probe failure: {daemon.stderr}")

    try:
        image = _run(
            "docker", "image", "inspect", POSTGRES_IMAGE, check=False, timeout=10
        )
    except (subprocess.SubprocessError, OSError) as exc:
        pytest.fail(f"Docker image probe failed: {type(exc).__name__}: {exc}")
    if image.returncode != 0:
        if not in_ci and "no such image" in image.stderr.lower():
            pytest.skip(f"required local image absent: {POSTGRES_IMAGE}")
        pytest.fail(f"Docker image probe failed: {image.stderr}")

    owner_token = uuid.uuid4().hex
    suffix = owner_token[:12]
    network = f"atlas-restore-test-{suffix}"
    container = f"atlas-restore-pg-{suffix}"
    try:
        network_result = _run(
            "docker", "network", "create",
            "--label", f"{RESTORE_OWNER_LABEL}={owner_token}", network,
            check=False,
        )
        if network_result.returncode != 0:
            pytest.fail(
                f"disposable Docker network creation failed: {network_result.stderr}"
            )

        start = _run(
            "docker",
            "run",
            "--pull=never",
            "--detach",
            "--rm",
            "--name",
            container,
            "--label",
            f"{RESTORE_OWNER_LABEL}={owner_token}",
            "--network",
            network,
            "--network-alias",
            "supabase-db",
            "-e",
            "POSTGRES_PASSWORD=restore-test-secret",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=512m",
            "--tmpfs",
            "/atlas-test-tablespaces:rw,size=64m",
            POSTGRES_IMAGE,
            "-c",
            "max_prepared_transactions=10",
            check=False,
            timeout=POSTGRES_CREATE_TIMEOUT,
        )
        if start.returncode != 0:
            if "no space left on device" in start.stderr.lower():
                pytest.fail(f"Docker storage exhaustion: {start.stderr}")
            pytest.fail(f"disposable PostgreSQL failed to start: {start.stderr}")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ready = _run(
                "docker",
                "exec",
                container,
                "pg_isready",
                "-U",
                "postgres",
                check=False,
                timeout=5,
            )
            logs = _run(
                "docker",
                "logs",
                container,
                check=False,
                timeout=5,
            )
            # The official image starts and stops an initialization postmaster
            # before exec'ing the final server; both can briefly pass pg_isready.
            if (
                ready.returncode == 0
                and (logs.stdout + logs.stderr).count(
                    "database system is ready to accept connections"
                )
                >= 2
            ):
                break
            time.sleep(0.25)
        else:
            pytest.fail("disposable PostgreSQL did not become ready within 30 seconds")

        yield DisposablePostgres(container=container, network=network)
    finally:
        _cleanup_disposable_postgres(container, network, owner_token)


@pytest.fixture(autouse=True)
def clean_disposable_databases(
    disposable_postgres: DisposablePostgres,
) -> Iterator[None]:
    """Bound tmpfs growth while keeping one PostgreSQL process for the drills."""
    yield
    names = disposable_postgres.sql(
        "template1",
        "SELECT datname FROM pg_database "
        "WHERE datname NOT IN ('postgres', 'template0', 'template1')",
        check=False,
    ).stdout.splitlines()
    for name in names:
        disposable_postgres.drop_database(name)


def _database_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _write_client_fakes(
    fixture_dir: Path,
    *,
    cutover_mode: str = "",
    restore_delay: int = 0,
    kill_lock_after_restore: bool = False,
) -> None:
    bin_dir = fixture_dir / "bin"
    bin_dir.mkdir()
    timeout = bin_dir / "timeout"
    timeout.write_text("#!/bin/sh\nexec /usr/bin/timeout \"$@\"\n", encoding="utf-8")
    timeout.chmod(0o755)
    mc = bin_dir / "mc"
    mc.write_text(
        """#!/bin/sh
case "$1" in
  alias) exit 0 ;;
  ls) printf '20260829_000000/postgres.complete\n' ;;
  cat) cat "/fixture/$(basename "$2")" ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    mc.chmod(0o755)
    openssl = bin_dir / "openssl"
    openssl.write_text(
        "#!/bin/sh\ncase \"$*\" in *complete.payload*) value=$(cat /fixture/postgres.complete.runtime-hmac);; *) value=$(cat /fixture/postgres.runtime-hmac);; esac\nprintf 'HMAC-SHA2-256= %s\\n' \"$value\"\n",
        encoding="utf-8",
    )
    openssl.chmod(0o755)
    if restore_delay or kill_lock_after_restore:
        pg_restore = bin_dir / "pg_restore"
        after_restore = ""
        if kill_lock_after_restore:
            after_restore = """
/usr/local/bin/psql -X -h supabase-db -U postgres -d template1 -Atqc \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name LIKE 'atlas-restore-lock-%'" >/dev/null
"""
        pg_restore.write_text(
            f"""#!/bin/sh
case "$*" in
  *--list*) exec /usr/local/bin/pg_restore "$@" ;;
  *) sleep {restore_delay}; /usr/local/bin/pg_restore "$@"; rc=$?; {after_restore} exit "$rc" ;;
esac
""",
            encoding="utf-8",
        )
        pg_restore.chmod(0o755)
    if cutover_mode:
        mode = fixture_dir / "cutover-mode"
        mode.write_text(cutover_mode, encoding="utf-8")
        psql = bin_dir / "psql"
        psql.write_text(
            """#!/bin/sh
input="$(mktemp)"
trap 'rm -f "$input" "${input}.out"' 0
cat >"$input"
if grep -q 'first_rename_ok' "$input"; then
  awk -v mode="$(cat /fixture/cutover-mode)" '
    /temp_db.*target_db/ && ! injected {
      if (mode == "timeout" || mode == "signal") print "SELECT pg_sleep(30);"
      if (mode == "failure") {
        print "\\\\set ON_ERROR_STOP on"
        print "SELECT 1/0;"
      }
      injected=1
    }
    { print }
  ' "$input" >"${input}.out"
  exec /usr/local/bin/psql "$@" <"${input}.out"
fi
exec /usr/local/bin/psql "$@" <"$input"
""",
            encoding="utf-8",
        )
        psql.chmod(0o755)


def _dump_database(
    postgres: DisposablePostgres,
    fixture_dir: Path,
    source_database: str,
    *dump_options: str,
) -> None:
    owner_token = uuid.uuid4().hex
    client = f"atlas-restore-client-{owner_token[:12]}"
    uncertain = True
    try:
        _run(
            "docker",
            "run",
            "--pull=never",
            "--rm",
            "--name",
            client,
            "--label",
            f"{RESTORE_OWNER_LABEL}={owner_token}",
            # Write the dump as the invoking user: the container is root by
            # default, and on Linux the resulting root-owned file is unreadable
            # to the test process (macOS Docker remaps ownership and hides it).
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            postgres.network,
            "-e",
            "PGPASSWORD=restore-test-secret",
            "-v",
            f"{fixture_dir}:/fixture",
            POSTGRES_IMAGE,
            "pg_dump",
            "-h",
            "supabase-db",
            "-U",
            "postgres",
            "-d",
            source_database,
            *dump_options,
            "-Fc",
            "-f",
            "/fixture/postgres.dump",
            timeout=60,
        )
        uncertain = False
    finally:
        _cleanup_restore_helper(client, owner_token, uncertain=uncertain)


def _write_restore_sidecar(
    postgres: DisposablePostgres,
    fixture_dir: Path,
    source_database: str,
    target_identity: str,
    *,
    manifest_key: str = MANIFEST_HMAC_KEY,
) -> None:
    inventory = postgres.sql(
        source_database,
        "SELECT encode(convert_to(n.nspname, 'UTF8'), 'hex') || E'\\t' || "
        "encode(convert_to(c.relname, 'UTF8'), 'hex') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p') "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname !~ '^pg_toast' ORDER BY n.nspname, c.relname",
    ).stdout
    tables = fixture_dir / "postgres.tables"
    tables.write_text(inventory, encoding="utf-8")
    dump_sha = hashlib.sha256((fixture_dir / "postgres.dump").read_bytes()).hexdigest()
    tables_sha = hashlib.sha256(tables.read_bytes()).hexdigest()
    table_count = len([line for line in inventory.splitlines() if line])
    owner_token = uuid.uuid4().hex
    client = f"atlas-restore-client-{owner_token[:12]}"
    uncertain = True
    try:
        listing = _run(
            "docker",
            "run",
            "--pull=never",
            "--rm",
            "--name",
            client,
            "--label",
            f"{RESTORE_OWNER_LABEL}={owner_token}",
            "-v",
            f"{fixture_dir}:/fixture:ro",
            POSTGRES_IMAGE,
            "pg_restore",
            "--list",
            "/fixture/postgres.dump",
            timeout=30,
        ).stdout
        uncertain = False
    finally:
        _cleanup_restore_helper(client, owner_token, uncertain=uncertain)
    object_inventory = "\n".join(
        line.strip()
        for line in listing.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ) + "\n"
    objects = fixture_dir / "postgres.objects"
    objects.write_text(object_inventory, encoding="utf-8")
    objects_sha = hashlib.sha256(objects.read_bytes()).hexdigest()
    object_count = len(object_inventory.splitlines())
    manifest_bytes = completion_bytes = 0
    for _ in range(10):
        payload = "\n".join(
            [
                "format_version=3", "backup_timestamp=20260829_000000",
                "backup_id=" + "1" * 32,
                f"deployment_id_hex={DEPLOYMENT_ID.encode().hex()}",
                f"database_name_hex={target_identity.encode().hex()}",
                f"dump_sha256={dump_sha}",
                f"dump_bytes={(fixture_dir / 'postgres.dump').stat().st_size}",
                f"tables_sha256={tables_sha}", f"tables_bytes={tables.stat().st_size}",
                f"table_count={table_count}", f"objects_sha256={objects_sha}",
                f"objects_bytes={objects.stat().st_size}", f"object_count={object_count}",
                f"completion_bytes={completion_bytes}", "server_version_num=170010", "",
            ]
        )
        signature = hmac.new(bytes.fromhex(manifest_key), payload.encode(), hashlib.sha256).hexdigest()
        runtime_signature = hmac.new(bytes.fromhex(MANIFEST_HMAC_KEY), payload.encode(), hashlib.sha256).hexdigest()
        manifest_text = f"{payload}hmac_sha256={signature}\n"
        new_manifest_bytes = len(manifest_text.encode())
        completion_payload = "\n".join(
            [
                "completion_format=1", "backup_timestamp=20260829_000000",
                "backup_id=" + "1" * 32,
                f"manifest_sha256={hashlib.sha256(manifest_text.encode()).hexdigest()}",
                f"manifest_bytes={new_manifest_bytes}",
                f"dump_bytes={(fixture_dir / 'postgres.dump').stat().st_size}",
                f"tables_bytes={tables.stat().st_size}", f"objects_bytes={objects.stat().st_size}", "",
            ]
        )
        completion_signature = hmac.new(bytes.fromhex(manifest_key), completion_payload.encode(), hashlib.sha256).hexdigest()
        runtime_completion_signature = hmac.new(bytes.fromhex(MANIFEST_HMAC_KEY), completion_payload.encode(), hashlib.sha256).hexdigest()
        completion_text = f"{completion_payload}hmac_sha256={completion_signature}\n"
        new_completion_bytes = len(completion_text.encode())
        if (manifest_bytes, completion_bytes) == (new_manifest_bytes, new_completion_bytes):
            break
        manifest_bytes, completion_bytes = new_manifest_bytes, new_completion_bytes
    (fixture_dir / "postgres.runtime-hmac").write_text(
        runtime_signature, encoding="utf-8"
    )
    (fixture_dir / "postgres.manifest").write_text(
        manifest_text, encoding="utf-8"
    )
    (fixture_dir / "postgres.complete.runtime-hmac").write_text(runtime_completion_signature)
    (fixture_dir / "postgres.complete").write_text(completion_text)


def _resign_manifest(fixture_dir: Path) -> None:
    manifest = fixture_dir / "postgres.manifest"
    lines = [
        line for line in manifest.read_text(encoding="utf-8").splitlines()
        if not line.startswith("hmac_sha256=")
    ]
    sizes = {
        "dump_bytes": (fixture_dir / "postgres.dump").stat().st_size,
        "tables_bytes": (fixture_dir / "postgres.tables").stat().st_size,
        "objects_bytes": (fixture_dir / "postgres.objects").stat().st_size,
    }
    lines = [f"{key}={sizes[key]}" if key in sizes else line for line in lines for key in [line.split("=", 1)[0]]]
    payload = "\n".join(lines) + "\n"
    signature = hmac.new(
        bytes.fromhex(MANIFEST_HMAC_KEY), payload.encode(), hashlib.sha256
    ).hexdigest()
    (fixture_dir / "postgres.runtime-hmac").write_text(signature, encoding="utf-8")
    manifest_text = f"{payload}hmac_sha256={signature}\n"
    manifest.write_text(manifest_text, encoding="utf-8")
    completion = fixture_dir / "postgres.complete"
    completion_lines = [
        line for line in completion.read_text().splitlines()
        if not line.startswith(("manifest_sha256=", "manifest_bytes=", "hmac_sha256="))
    ]
    completion_lines = [f"{key}={sizes[key]}" if key in sizes else line for line in completion_lines for key in [line.split("=", 1)[0]]]
    completion_lines.insert(3, f"manifest_sha256={hashlib.sha256(manifest_text.encode()).hexdigest()}")
    completion_lines.insert(4, f"manifest_bytes={len(manifest_text.encode())}")
    completion_payload = "\n".join(completion_lines) + "\n"
    completion_signature = hmac.new(bytes.fromhex(MANIFEST_HMAC_KEY), completion_payload.encode(), hashlib.sha256).hexdigest()
    (fixture_dir / "postgres.complete.runtime-hmac").write_text(completion_signature)
    completion.write_text(f"{completion_payload}hmac_sha256={completion_signature}\n")


def _run_restore(
    postgres: DisposablePostgres,
    fixture_dir: Path,
    target_database: str,
    *,
    command_timeout: int = 20,
    restore_path: Path = RESTORE,
    client_name: str = "",
):
    container_path = os.environ.get(
        "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    owner_token = uuid.uuid4().hex
    client = client_name or f"atlas-restore-client-{owner_token[:12]}"
    uncertain = True
    try:
        result = _run(
            "docker",
            "run",
            "--pull=never",
            "--rm",
            "--name",
            client,
            "--label",
            f"{RESTORE_OWNER_LABEL}={owner_token}",
            "--network",
            postgres.network,
            "-e",
            f"PATH=/fixture/bin:{container_path}",
            "-e",
            "SUPABASE_DB_USER=postgres",
            "-e",
            "SUPABASE_DB_PASSWORD=restore-test-secret",
            "-e",
            f"SUPABASE_DB_NAME={target_database}",
            "-e",
            "MINIO_ROOT_USER=test-access",
            "-e",
            "MINIO_ROOT_PASSWORD=test-secret",
            "-e",
            f"BACKUP_MANIFEST_HMAC_KEY={MANIFEST_HMAC_KEY}",
            "-e",
            f"BACKUP_DEPLOYMENT_ID={DEPLOYMENT_ID}",
            "-e",
            "BACKUP_TIMESTAMP=20260829_000000",
            "-e",
            f"BACKUP_COMMAND_TIMEOUT_SECONDS={command_timeout}",
            "-e",
            "BACKUP_RESTORE_MAINTENANCE_MODE=confirmed",
            "-v",
            f"{restore_path}:/scripts/restore-postgres.sh:ro",
            "-v",
            f"{S3_CLIENT}:/scripts/s3-client.sh:ro",
            "-v",
            f"{fixture_dir}:/fixture:ro",
            "--tmpfs",
            "/tmp:rw,size=64m",
            POSTGRES_IMAGE,
            "sh",
            "/scripts/restore-postgres.sh",
            check=False,
            timeout=90,
        )
        uncertain = result.returncode == 125
        return result
    finally:
        _cleanup_restore_helper(client, owner_token, uncertain=uncertain)


def _seed_state(postgres: DisposablePostgres, database: str, value: str) -> None:
    postgres.sql(
        database,
        f"CREATE TABLE state(value text NOT NULL); INSERT INTO state VALUES ('{value}')",
    )


def _state(postgres: DisposablePostgres, database: str) -> str:
    return postgres.sql(database, "SELECT value FROM state").stdout.strip()


def _temporary_databases(postgres: DisposablePostgres) -> str:
    return postgres.sql(
        "template1",
        "SELECT datname FROM pg_database WHERE datname LIKE 'atlas_restore_%'",
    ).stdout.strip()


def _write_backup_host_wrappers(
    postgres: DisposablePostgres,
    fixture_dir: Path,
    work_dir: Path,
    *,
    inject_concurrent_ddl: bool = True,
) -> Path:
    bin_dir = fixture_dir / "backup-bin"
    bin_dir.mkdir()
    timeout = bin_dir / "timeout"
    timeout.write_text('#!/bin/sh\nshift 5\nexec "$@"\n', encoding="utf-8")
    timeout.chmod(0o755)
    mc = bin_dir / "mc"
    mc.write_text(
        f'''#!/bin/sh
if [ "$1" = cp ] && [ "$(basename "$2")" != "--recursive" ]; then
  case "$(basename "$2")" in atlas-backup-complete-*) cp "$2" "{fixture_dir}/postgres.complete";; esac
fi
exit 0
''',
        encoding="utf-8",
    )
    mc.chmod(0o755)
    sha256sum = bin_dir / "sha256sum"
    sha256sum.write_text(
        """#!/usr/bin/env python3
import hashlib
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}")
""",
        encoding="utf-8",
    )
    sha256sum.chmod(0o755)
    openssl_path = shutil.which("openssl")
    assert openssl_path
    openssl = bin_dir / "openssl"
    openssl.write_text(
        f'#!/bin/sh\nexec "{openssl_path}" "$@"\n', encoding="utf-8"
    )
    openssl.chmod(0o755)
    psql = bin_dir / "psql"
    psql.write_text(
        f"""#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
process = subprocess.Popen(
    ["docker", "exec", "-i", "-e", f"PGPASSWORD={{os.environ['PGPASSWORD']}}",
     "-e", f"PGAPPNAME={{os.environ.get('PGAPPNAME', '')}}",
     "{postgres.container}", "psql", *sys.argv[1:]],
    stdin=subprocess.PIPE,
)
def stop(signum, _frame):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    raise SystemExit(128 + signum)
signal.signal(signal.SIGTERM, stop)
process.communicate(sys.stdin.buffer.read())
raise SystemExit(process.returncode)
""",
        encoding="utf-8",
    )
    psql.chmod(0o755)
    for command in ("pg_dump", "pg_restore"):
        wrapper = bin_dir / command
        concurrent_ddl = ""
        if command == "pg_dump" and inject_concurrent_ddl:
            concurrent_ddl = f"""
subprocess.run(
    ["docker", "exec", "{postgres.container}", "psql", "-X", "-v", "ON_ERROR_STOP=1",
     "-U", "postgres", "-d", os.environ["SUPABASE_DB_NAME"], "-c",
     "CREATE TABLE concurrent_after_snapshot(id integer)"], check=True,
    capture_output=True, timeout=10,
)
"""
        wrapper.write_text(
            f"""#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
import time
import uuid
{concurrent_ddl}
owner_token = uuid.uuid4().hex
client = "atlas-restore-client-" + owner_token[:12]
args = [
    arg.replace("{fixture_dir}", "/fixture")
    for arg in sys.argv[1:]
]
def inspect_owned():
    inspected = subprocess.run(
        ["docker", "container", "inspect", "--format",
         '{{{{.Name}}}} {{{{index .Config.Labels "{RESTORE_OWNER_LABEL}"}}}}', client],
        capture_output=True, text=True, timeout=10,
    )
    if inspected.returncode != 0:
        listed = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=^/" + client + "$",
             "--format", "{{{{.Names}}}}"],
            capture_output=True, text=True, timeout=10,
        )
        if listed.returncode != 0 or client in listed.stdout.splitlines():
            raise RuntimeError("could not prove restore helper absence")
        return False
    actual, observed_token = inspected.stdout.strip().split(maxsplit=1)
    return actual.lstrip("/") == client and observed_token == owner_token
def cleanup(primary=None, uncertain=False):
    deadline = time.monotonic() + {COMMAND_TIMEOUT} if uncertain else None
    failure = None
    while True:
        try:
            if inspect_owned():
                removed = subprocess.run(
                    ["docker", "rm", "-f", client], capture_output=True,
                    text=True, timeout=10,
                )
                if removed.returncode != 0:
                    raise RuntimeError(removed.stderr or removed.stdout)
            if inspect_owned():
                raise RuntimeError("owned restore helper remains")
            failure = None
        except BaseException as exc:
            failure = exc
        if deadline is None or time.monotonic() >= deadline:
            break
        try:
            time.sleep(0.1)
        except BaseException as exc:
            if primary is None:
                raise
            note = "restore helper cleanup sleep interrupted: " + repr(exc)
            if hasattr(primary, "add_note"):
                primary.add_note(note)
            else:
                primary.__notes__ = getattr(primary, "__notes__", []) + [note]
    if failure is None:
        return
    note = "restore helper cleanup could not be proven: " + repr(failure)
    if primary is not None:
        if hasattr(primary, "add_note"):
            primary.add_note(note)
        else:
            primary.__notes__ = getattr(primary, "__notes__", []) + [note]
        return
    raise failure
def stop(signum, _frame):
    raise SystemExit(128 + signum)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    result = subprocess.run(
        ["docker", "run", "--pull=never", "--rm", "--name", client,
         "--label", "{RESTORE_OWNER_LABEL}=" + owner_token,
         "--user", "{os.getuid()}:{os.getgid()}",
         "--network", "{postgres.network}", "-e", "PGPASSWORD=restore-test-secret",
         "-v", "{fixture_dir}:/fixture", "{POSTGRES_IMAGE}", "{command}", *args],
        capture_output=True,
    )
except BaseException as exc:
    cleanup(exc, uncertain=True)
    raise
else:
    cleanup(uncertain=result.returncode == 125)
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    raise SystemExit(result.returncode)
""",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return bin_dir


def test_same_timestamp_backup_producers_are_cluster_serialized(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("backup_publication_lock")
    disposable_postgres.create_database(target)
    _seed_state(disposable_postgres, target, "publication")
    work1 = tmp_path / "work-1"
    work2 = tmp_path / "work-2"
    backup_bin = _write_backup_host_wrappers(
        disposable_postgres,
        tmp_path,
        work1,
        inject_concurrent_ddl=False,
    )
    publication = tmp_path / "publication"
    publication.mkdir()
    trace = tmp_path / "mc-trace"
    mc = backup_bin / "mc"
    mc.write_text(
        f'''#!/bin/sh
printf '%s\n' "$*" >>"{trace}"
case "$1" in
  alias|mb) exit 0 ;;
  ls)
    sleep 2
    [ -f "{publication}/postgres.complete" ] && printf '20260829_000000/postgres.complete\n'
    exit 0
    ;;
  cp)
    case "$2" in
      --recursive) exit 0 ;;
      *)
        case "$(basename "$2")" in
          atlas-backup-complete-*) cp "$2" "{publication}/postgres.complete" ;;
        esac
        ;;
    esac
    ;;
esac
''',
        encoding="utf-8",
    )
    mc.chmod(0o755)
    _stage_backup_script_siblings(tmp_path)
    source = BACKUP.read_text(encoding="utf-8").replace(
        "for d in /volumes/*", f"for d in {tmp_path}/no-volumes/*"
    )
    backup1 = tmp_path / "backup-1.sh"
    backup2 = tmp_path / "backup-2.sh"
    backup1.write_text(
        source.replace('WORK="/tmp/atlas-backup-${backup_id}"', f"WORK={work1}")
    )
    backup2.write_text(
        source.replace('WORK="/tmp/atlas-backup-${backup_id}"', f"WORK={work2}")
    )
    env = {
        **os.environ,
        "PATH": f"{backup_bin}:{os.environ.get('PATH', '')}",
        "SUPABASE_DB_USER": "postgres",
        "SUPABASE_DB_PASSWORD": "restore-test-secret",
        "SUPABASE_DB_NAME": target,
        "MINIO_ROOT_USER": "test-access",
        "MINIO_ROOT_PASSWORD": "test-secret",
        "BACKUP_MANIFEST_HMAC_KEY": MANIFEST_HMAC_KEY,
        "BACKUP_DEPLOYMENT_ID": DEPLOYMENT_ID,
        "BACKUP_TIMESTAMP": "20260829_000000",
        "BACKUP_COMMAND_TIMEOUT_SECONDS": "20",
        "BACKUP_DATABASES": "false",
    }

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            _run, "sh", str(backup1), check=False, timeout=70, env=env
        )
        second_future = pool.submit(
            _run, "sh", str(backup2), check=False, timeout=70, env=env
        )
        results = [first_future.result(timeout=75), second_future.result(timeout=75)]

    assert sorted(result.returncode for result in results) == [0, 75], [
        (result.returncode, result.stderr) for result in results
    ]
    marker = publication.joinpath("postgres.complete").read_text()
    backup_id = next(
        line.split("=", 1)[1]
        for line in marker.splitlines()
        if line.startswith("backup_id=")
    )
    recursive_uploads = [
        line for line in trace.read_text().splitlines() if line.startswith("cp --recursive")
    ]
    assert len(recursive_uploads) == 1
    assert recursive_uploads[0].endswith(
        f"s3/atlas-backups/20260829_000000/{backup_id}/"
    )
    assert disposable_postgres.sql(
        "template1",
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE application_name LIKE 'atlas-backup-publication-%'",
    ).stdout.strip() == "0"


def test_backup_snapshot_stays_restorable_during_concurrent_ddl(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("backup_snapshot")
    disposable_postgres.create_database(target)
    _seed_state(disposable_postgres, target, "snapshot")
    work = tmp_path / "work"
    backup_bin = _write_backup_host_wrappers(disposable_postgres, tmp_path, work)
    _stage_backup_script_siblings(tmp_path)
    backup = tmp_path / "backup-all.sh"
    backup.write_text(
        BACKUP.read_text(encoding="utf-8")
        .replace('WORK="/tmp/atlas-backup-${backup_id}"', f"WORK={work}")
        .replace("for d in /volumes/*", f"for d in {tmp_path}/no-volumes/*"),
        encoding="utf-8",
    )

    result = _run(
        "sh",
        str(backup),
        check=False,
        timeout=60,
        env={
            **os.environ,
            "PATH": f"{backup_bin}:{os.environ.get('PATH', '')}",
            "SUPABASE_DB_USER": "postgres",
            "SUPABASE_DB_PASSWORD": "restore-test-secret",
            "SUPABASE_DB_NAME": target,
            "MINIO_ROOT_USER": "test-access",
            "MINIO_ROOT_PASSWORD": "test-secret",
            "BACKUP_MANIFEST_HMAC_KEY": MANIFEST_HMAC_KEY,
            "BACKUP_DEPLOYMENT_ID": DEPLOYMENT_ID,
            "BACKUP_TIMESTAMP": "20260829_000000",
            "BACKUP_COMMAND_TIMEOUT_SECONDS": "20",
            "BACKUP_DATABASES": "false",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "concurrent_after_snapshot" not in (work / "postgres.objects").read_text()
    assert "636f6e63757272656e745f61667465725f736e617073686f74" not in (
        work / "postgres.tables"
    ).read_text()
    for artifact in (
        "postgres.dump",
        "postgres.manifest",
        "postgres.objects",
        "postgres.tables",
    ):
        shutil.copy2(work / artifact, tmp_path / artifact)
    manifest_hmac = next(
        line.split("=", 1)[1]
        for line in (tmp_path / "postgres.manifest").read_text().splitlines()
        if line.startswith("hmac_sha256=")
    )
    (tmp_path / "postgres.runtime-hmac").write_text(manifest_hmac)
    completion_hmac = next(
        line.split("=", 1)[1]
        for line in (tmp_path / "postgres.complete").read_text().splitlines()
        if line.startswith("hmac_sha256=")
    )
    (tmp_path / "postgres.complete.runtime-hmac").write_text(completion_hmac)
    _write_client_fakes(tmp_path)

    restored = _run_restore(disposable_postgres, tmp_path, target)

    assert restored.returncode == 0, restored.stderr
    assert _state(disposable_postgres, target) == "snapshot"
    assert disposable_postgres.sql(
        target,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname='concurrent_after_snapshot'",
    ).stdout.strip() == "0"


def test_corrupt_archive_fails_in_preflight_without_touching_live_database(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_corrupt")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)
    (tmp_path / "postgres.dump").write_bytes(b"not a postgres archive")

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert "phase preflight" in result.stdout
    assert _state(disposable_postgres, target) == "live"
    assert _temporary_databases(disposable_postgres) == ""


def test_mid_restore_error_never_mutates_live_database(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_mid_error")
    source = _database_name("restore_source")
    missing_owner = _database_name("restore_owner")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    disposable_postgres.sql("template1", f'CREATE ROLE "{missing_owner}"')
    _seed_state(disposable_postgres, source, "archive")
    disposable_postgres.sql(source, f'ALTER TABLE state OWNER TO "{missing_owner}"')
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)
    disposable_postgres.drop_database(source)
    disposable_postgres.sql("template1", f'DROP ROLE "{missing_owner}"')

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert _state(disposable_postgres, target) == "live"
    assert _temporary_databases(disposable_postgres) == ""


def test_failed_validation_never_mutates_live_database(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_bad_validate")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    disposable_postgres.sql(
        source,
        "CREATE TABLE parent(id integer PRIMARY KEY); "
        "CREATE TABLE child(parent_id integer); "
        "ALTER TABLE child ADD CONSTRAINT child_parent_fk "
        "FOREIGN KEY (parent_id) REFERENCES parent(id) NOT VALID",
    )
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert _state(disposable_postgres, target) == "live"
    assert _temporary_databases(disposable_postgres) == ""


def test_generated_staging_name_collision_never_drops_existing_database(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_collision_target")
    source = _database_name("restore_collision_source")
    suffix = "deadbeefdeadbeef"
    staging = f"atlas_restore_{suffix}"
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    disposable_postgres.create_database(staging)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _seed_state(disposable_postgres, staging, "collision")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)
    _stage_backup_script_siblings(tmp_path)
    restore = tmp_path / "restore-postgres.sh"
    restore.write_text(
        RESTORE.read_text(encoding="utf-8").replace(
            'suffix="$(od -An -N8 -tx1 /dev/urandom | tr -d \'[:space:]\')"',
            f'suffix="{suffix}"',
        ),
        encoding="utf-8",
    )

    try:
        result = _run_restore(
            disposable_postgres, tmp_path, target, restore_path=restore
        )

        assert result.returncode != 0
        assert _state(disposable_postgres, target) == "live"
        assert _state(disposable_postgres, staging) == "collision"
    finally:
        disposable_postgres.drop_database(staging)


def test_successful_cutover_preserves_original_as_rollback_database(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_success")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode == 0, result.stderr
    assert _state(disposable_postgres, target) == "archive"
    rollback = disposable_postgres.sql(
        "template1",
        "SELECT datname FROM pg_database "
        f"WHERE datname LIKE 'atlas_rollback_%' AND datname <> '{target}' "
        "ORDER BY datname DESC LIMIT 1",
    ).stdout.strip()
    assert rollback
    assert _state(disposable_postgres, rollback) == "live"


def test_successful_cutover_preserves_pg17_builtin_locale_provider(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_builtin_locale")
    source = _database_name("restore_source")
    disposable_postgres.sql(
        "template1",
        f'CREATE DATABASE "{target}" WITH TEMPLATE template0 '
        "LOCALE_PROVIDER builtin BUILTIN_LOCALE 'C.UTF-8'",
    )
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode == 0, result.stderr
    assert _state(disposable_postgres, target) == "archive"
    assert disposable_postgres.sql(
        "template1",
        f"SELECT datlocprovider, datlocale FROM pg_database WHERE datname='{target}'",
    ).stdout.strip() == "b|C.UTF-8"


def test_repeated_16_byte_database_name_keeps_exact_identity(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = "abcdefghijklmnopabcdefghijklmnop"
    source = _database_name("restore_repeated_name_source")
    disposable_postgres.drop_database(target)
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode == 0, result.stderr
    assert _state(disposable_postgres, target) == "archive"


def test_restore_rejects_corrupt_dump_checksum(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_bad_checksum")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)
    with (tmp_path / "postgres.dump").open("ab") as dump:
        dump.write(b"corrupt")

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert "authenticated download limit" in result.stderr
    assert _state(disposable_postgres, target) == "live"
    assert _temporary_databases(disposable_postgres) == ""


def test_restore_rejects_unsigned_extra_manifest_fields(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_extra_manifest")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)
    manifest = tmp_path / "postgres.manifest"
    manifest.write_text(manifest.read_text() + "unsigned_field=ignored\n")

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert "incomplete or unauthenticated" in result.stderr
    assert _state(disposable_postgres, target) == "live"


def test_restore_rejects_wrong_database_identity(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_wrong_identity")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, "some_other_db")

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert "database identity" in result.stderr
    assert _state(disposable_postgres, target) == "live"


def test_restore_rejects_self_consistent_same_name_archive_without_trusted_hmac(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_forged_same_name")
    source = _database_name("restore_untrusted_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "untrusted")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(
        disposable_postgres,
        tmp_path,
        source,
        target,
        manifest_key="b" * 64,
    )

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert "incomplete or unauthenticated" in result.stderr
    assert _state(disposable_postgres, target) == "live"
    assert _temporary_databases(disposable_postgres) == ""


def test_restore_rejects_self_consistent_partial_dump_without_trusted_hmac(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_forged_partial")
    source = _database_name("restore_complete_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "partial")
    disposable_postgres.sql(
        source,
        "CREATE SEQUENCE required_sequence; "
        "CREATE FUNCTION required_function() RETURNS integer LANGUAGE sql AS 'SELECT 1'; "
        "CREATE VIEW required_view AS SELECT required_function() AS value",
    )
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source, "--table=state")
    _write_restore_sidecar(
        disposable_postgres,
        tmp_path,
        source,
        target,
        manifest_key="b" * 64,
    )

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert "incomplete or unauthenticated" in result.stderr
    assert _state(disposable_postgres, target) == "live"
    assert _temporary_databases(disposable_postgres) == ""


def test_restore_rejects_valid_empty_archive(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_empty")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert "table inventory" in result.stderr
    assert _state(disposable_postgres, target) == "live"


def test_restore_rejects_incomplete_authenticated_inventory(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_incomplete")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)
    tables = tmp_path / "postgres.tables"
    tables.write_text(tables.read_text() + "6578706563746564\t6d697373696e67\n")
    tables_sha = hashlib.sha256(tables.read_bytes()).hexdigest()
    manifest = tmp_path / "postgres.manifest"
    manifest.write_text(
        manifest.read_text()
        .replace("table_count=1", "table_count=2")
        .replace(
            next(
                line
                for line in manifest.read_text().splitlines()
                if line.startswith("tables_sha256=")
            ),
            f"tables_sha256={tables_sha}",
        )
    )
    _resign_manifest(tmp_path)

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert "staged table inventory" in result.stderr
    assert _state(disposable_postgres, target) == "live"
    assert _temporary_databases(disposable_postgres) == ""


def test_restore_rejects_database_bound_prepared_transaction(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_prepared_target")
    source = _database_name("restore_prepared_source")
    transaction = _database_name("restore_prepared_xact")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    disposable_postgres.sql(
        target, f"BEGIN; PREPARE TRANSACTION '{transaction}'"
    )
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    result = _run_restore(disposable_postgres, tmp_path, target)
    disposable_postgres.sql(target, f"ROLLBACK PREPARED '{transaction}'")

    assert result.returncode != 0
    assert "unsupported database-bound" in result.stderr
    assert _state(disposable_postgres, target) == "live"
    assert _temporary_databases(disposable_postgres) == ""


def test_first_lock_acquisition_polls_backend_before_advisory_request(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_lock_start")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)
    _stage_backup_script_siblings(tmp_path)
    delayed_restore = tmp_path / "restore-postgres.sh"
    delayed_restore.write_text(
        RESTORE.read_text().replace(
            "SELECT pg_advisory_lock(hashtextextended('atlas-backup-restore', 0))",
            "SELECT pg_sleep(1); SELECT pg_advisory_lock(hashtextextended('atlas-backup-restore', 0))",
        )
    )

    first = _run_restore(
        disposable_postgres, tmp_path, target, restore_path=delayed_restore
    )

    assert first.returncode == 0, first.stderr
    assert _state(disposable_postgres, target) == "archive"


def test_true_concurrent_restore_is_rejected(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_concurrent")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path, restore_delay=4)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(_run_restore, disposable_postgres, tmp_path, target)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            held = disposable_postgres.sql(
                "template1",
                "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid "
                "WHERE l.locktype='advisory' AND l.granted "
                "AND a.application_name LIKE 'atlas-restore-lock-%'",
            ).stdout.strip()
            if held != "0":
                break
            time.sleep(0.1)
        else:
            pytest.fail("first restore did not acquire advisory lock")
        second = _run_restore(disposable_postgres, tmp_path, target)
        first = first_future.result(timeout=30)

    assert second.returncode == 75
    assert "another restore" in second.stderr
    assert first.returncode == 0, first.stderr


def test_lost_advisory_lock_aborts_before_cutover(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_lock_lost")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path, kill_lock_after_restore=True)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode != 0
    assert "lost advisory lock before cutover" in result.stderr
    assert _state(disposable_postgres, target) == "live"
    assert _temporary_databases(disposable_postgres) == ""


@pytest.mark.parametrize("cutover_mode", ["failure", "timeout"])
def test_cutover_interruption_restores_original_name_and_preserves_staging(
    disposable_postgres: DisposablePostgres, tmp_path: Path, cutover_mode: str
) -> None:
    target = _database_name(f"restore_cutover_{cutover_mode}")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path, cutover_mode=cutover_mode)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    result = _run_restore(
        disposable_postgres,
        tmp_path,
        target,
        command_timeout=2 if cutover_mode == "timeout" else 20,
    )

    assert result.returncode != 0
    assert _state(disposable_postgres, target) == "live"
    match = re.search(r"staging=(atlas_restore_[0-9a-f]{16})", result.stderr)
    assert match
    staging = match.group(1)
    assert _state(disposable_postgres, staging) == "archive"
    assert staging in result.stderr


def test_cutover_signal_restores_original_name_and_preserves_staging(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_cutover_signal")
    source = _database_name("restore_source")
    client = f"atlas-restore-client-{uuid.uuid4().hex[:12]}"
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path, cutover_mode="signal")
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _run_restore,
            disposable_postgres,
            tmp_path,
            target,
            command_timeout=20,
            client_name=client,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            exists = disposable_postgres.sql(
                "template1",
                f"SELECT count(*) FROM pg_database WHERE datname='{target}'",
            ).stdout.strip()
            if exists == "0":
                break
            time.sleep(0.1)
        else:
            pytest.fail("cutover did not reach the post-first-rename pause")
        stopped = _run(
            "docker", "kill", "--signal", "TERM", client, check=False, timeout=10
        )
        assert stopped.returncode == 0, stopped.stderr
        result = future.result(timeout=30)

    assert result.returncode != 0
    assert _state(disposable_postgres, target) == "live"
    match = re.search(r"staging=(atlas_restore_[0-9a-f]{16})", result.stderr)
    assert match
    assert _state(disposable_postgres, match.group(1)) == "archive"


def test_global_deadline_after_first_rename_compensates_and_releases_lock(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_global_deadline")
    source = _database_name("restore_source")
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    _write_client_fakes(tmp_path, cutover_mode="signal")
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)
    _stage_backup_script_siblings(tmp_path)
    restore = tmp_path / "restore-global-deadline.sh"
    restore.write_text(
        RESTORE.read_text().replace(
            '"$GLOBAL_TIMEOUT_SECONDS" sh "$0" "$@"',
            '3 sh "$0" "$@"',
        )
    )

    result = _run_restore(disposable_postgres, tmp_path, target, restore_path=restore)

    assert result.returncode != 0
    assert _state(disposable_postgres, target) == "live"
    assert "cutover recovery target=" in result.stderr
    assert disposable_postgres.sql(
        "template1",
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE application_name LIKE 'atlas-restore-lock-%'",
    ).stdout.strip() == "0"


def test_successful_cutover_preserves_database_settings_acl_and_attributes(
    disposable_postgres: DisposablePostgres, tmp_path: Path
) -> None:
    target = _database_name("restore_metadata")
    source = _database_name("restore_source")
    reader = _database_name("restore_reader")
    owner = _database_name("restore_owner")
    tablespace = _database_name("restore_tablespace")
    tablespace_path = f"/atlas-test-tablespaces/{tablespace}"
    disposable_postgres.create_database(target)
    disposable_postgres.create_database(source)
    _run(
        "docker",
        "exec",
        disposable_postgres.container,
        "mkdir",
        tablespace_path,
    )
    _run(
        "docker",
        "exec",
        disposable_postgres.container,
        "chown",
        "postgres:postgres",
        tablespace_path,
    )
    disposable_postgres.sql(
        "template1", f'CREATE ROLE "{reader}"; CREATE ROLE "{owner}"'
    )
    disposable_postgres.sql(
        "template1", f'CREATE TABLESPACE "{tablespace}" LOCATION \'{tablespace_path}\''
    )
    _seed_state(disposable_postgres, target, "live")
    _seed_state(disposable_postgres, source, "archive")
    disposable_postgres.sql(
        "template1",
        f'ALTER DATABASE "{target}" OWNER TO "{owner}"; '
        f'ALTER DATABASE "{target}" CONNECTION LIMIT 23; '
        f'ALTER DATABASE "{target}" SET statement_timeout TO \'7s\'; '
        f'ALTER ROLE "{reader}" IN DATABASE "{target}" SET lock_timeout TO \'3s\'; '
        f'REVOKE CONNECT ON DATABASE "{target}" FROM PUBLIC; '
        f'GRANT CONNECT, TEMP ON DATABASE "{target}" TO "{reader}"',
    )
    disposable_postgres.sql(
        "template1", f'ALTER DATABASE "{target}" SET TABLESPACE "{tablespace}"'
    )
    before = disposable_postgres.sql(
        "template1",
        "SELECT pg_get_userbyid(datdba), spcname, datconnlimit, pg_encoding_to_char(encoding), "
        "datcollate, datctype, datlocprovider, datistemplate, datallowconn "
        "FROM pg_database d JOIN pg_tablespace t ON t.oid=d.dattablespace "
        f"WHERE datname = '{target}'",
    ).stdout.strip()
    _write_client_fakes(tmp_path)
    _dump_database(disposable_postgres, tmp_path, source)
    _write_restore_sidecar(disposable_postgres, tmp_path, source, target)

    result = _run_restore(disposable_postgres, tmp_path, target)

    assert result.returncode == 0, result.stderr
    after = disposable_postgres.sql(
        "template1",
        "SELECT pg_get_userbyid(datdba), spcname, datconnlimit, pg_encoding_to_char(encoding), "
        "datcollate, datctype, datlocprovider, datistemplate, datallowconn "
        "FROM pg_database d JOIN pg_tablespace t ON t.oid=d.dattablespace "
        f"WHERE datname = '{target}'",
    ).stdout.strip()
    assert after == before
    assert disposable_postgres.sql(
        "template1",
        f"SELECT has_database_privilege('{reader}', '{target}', 'CONNECT'), "
        f"has_database_privilege('{reader}', '{target}', 'TEMP')",
    ).stdout.strip() == "t|t"
    assert disposable_postgres.sql(
        "template1",
        f"SELECT has_database_privilege('public', '{target}', 'CONNECT')",
    ).stdout.strip() == "f"
    settings = disposable_postgres.sql(
        "template1",
        "SELECT COALESCE(r.rolname, ''), unnest(setconfig) FROM pg_db_role_setting s "
        "JOIN pg_database d ON d.oid=s.setdatabase "
        "LEFT JOIN pg_roles r ON r.oid=s.setrole "
        f"WHERE d.datname='{target}' ORDER BY 1, 2",
    ).stdout
    assert "statement_timeout=7s" in settings
    assert f"{reader}|lock_timeout=3s" in settings
