"""Executable inventory and isolation drills for Atlas PostgreSQL consumers.

The Docker tests use only uniquely named, tmpfs-backed PostgreSQL resources.
They never read ``.env`` and never mount an Atlas database volume.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import pytest
import yaml

from core.config_parser import ConfigParser
from tests import seed_harness
from utils.key_generator import KeyGenerator


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "services/supabase/db/scripts"
POSTGRES_IMAGE = "supabase/postgres:17.6.1.139"
INIT_IMAGE = "postgres:15.18-alpine"
COMMAND_TIMEOUT = 30
POSTGRES_CREATE_TIMEOUT = 60
DATABASE_ROLE_OWNER_LABEL = "com.atlas.database-role-test-token"


class _StorageBlockedStartup(RuntimeError):
    """Retain the disposable resources only for an explicit storage diagnosis."""


@dataclass(frozen=True)
class ConsumerContract:
    services: tuple[str, ...]
    database: str
    schemas: tuple[str, ...]
    tables: tuple[str, ...]
    extensions: tuple[str, ...]
    operations: frozenset[str]
    user_var: str
    password_var: str
    administrative: bool = False


# Hand-derived from the manifests, Compose fragments, service migrations, and
# direct SQL call sites.  This is deliberately explicit: adding a PostgreSQL
# client without extending the fixture is a test failure, not silent privilege
# creep.  Wildcards name upstream-owned table families whose exact membership
# changes as the pinned application runs its own migrations.
POSTGRES_CONSUMERS: dict[str, ConsumerContract] = {
    "database-bootstrap": ConsumerContract(
        ("supabase-db", "supabase-db-init"), "postgres",
        ("auth", "extensions", "public", "realtime", "storage"), ("*",),
        ("vector", "postgis", "pgcrypto"),
        frozenset({"DDL", "DML", "READ", "ROLE_ADMIN", "EXTENSION_ADMIN"}),
        "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD", True,
    ),
    "backup-restore": ConsumerContract(
        ("backup",), "postgres", ("*",), ("*",), ("*",),
        frozenset({"READ", "DATABASE_ADMIN", "RESTORE_ADMIN"}),
        "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD", True,
    ),
    "supabase-auth": ConsumerContract(
        ("supabase-auth",), "postgres", ("auth",), ("auth.*",), (),
        frozenset({"DDL", "DML", "READ"}),
        "SUPABASE_AUTH_DB_USER", "SUPABASE_AUTH_DB_PASSWORD",
    ),
    "supabase-storage": ConsumerContract(
        ("supabase-storage",), "postgres", ("storage",),
        ("storage.buckets", "storage.objects", "storage.*"), (),
        frozenset({"DDL", "DML", "READ"}),
        "SUPABASE_STORAGE_DB_USER", "SUPABASE_STORAGE_DB_PASSWORD",
    ),
    "postgrest": ConsumerContract(
        ("supabase-api",), "postgres", ("public", "storage"), ("published.*",), (),
        frozenset({"READ", "ROLE_SWITCH"}),
        "SUPABASE_API_DB_USER", "SUPABASE_API_DB_PASSWORD",
    ),
    "realtime": ConsumerContract(
        ("supabase-realtime",), "postgres", ("realtime", "public"),
        ("realtime.*", "published.*"), (),
        frozenset({"DDL", "DML", "READ", "REPLICATION"}),
        "SUPABASE_REALTIME_DB_USER", "SUPABASE_REALTIME_DB_PASSWORD",
    ),
    "metadata": ConsumerContract(
        ("supabase-meta", "supabase-studio"), "postgres", ("*",), ("*",), ("*",),
        frozenset({"DDL", "DML", "READ", "METADATA_ADMIN"}),
        "SUPABASE_META_DB_USER", "SUPABASE_META_DB_PASSWORD",
    ),
    "studio-read-only": ConsumerContract(
        ("supabase-studio",), "postgres",
        ("auth", "public", "realtime", "storage", "n8n", "lightrag"),
        ("*",), (), frozenset({"READ"}),
        "SUPABASE_STUDIO_DB_USER", "SUPABASE_META_DB_PASSWORD",
    ),
    "postgres-exporter": ConsumerContract(
        ("postgres-exporter",), "postgres", ("pg_catalog",), ("pg_catalog.*",), (),
        frozenset({"READ", "MONITOR"}),
        "POSTGRES_EXPORTER_DB_USER", "POSTGRES_EXPORTER_DB_PASSWORD",
    ),
    "supavisor": ConsumerContract(
        ("supavisor",), "supavisor", ("public", "pgbouncer"),
        ("supavisor migration tables", "pgbouncer.get_auth"), (),
        frozenset({"DDL", "DML", "READ", "AUTH_LOOKUP"}),
        "SUPAVISOR_DB_ADMIN_USER", "SUPAVISOR_DB_ADMIN_PASSWORD",
    ),
    "backend": ConsumerContract(
        ("backend", "celery-worker"), "postgres", ("public",),
        (
            "public.research_sessions", "public.research_results",
            "public.research_sources", "public.research_logs",
            "public.memory_facts", "public.memory_sessions",
            "public.memory_consolidation_log", "public.media_spend_ledger",
        ),
        ("vector",), frozenset({"DML", "READ", "ADVISORY_LOCK"}),
        "BACKEND_DB_USER", "BACKEND_DB_PASSWORD",
    ),
    "n8n": ConsumerContract(
        ("n8n", "n8n-worker"), "postgres", ("n8n",), ("n8n.*",), (),
        frozenset({"DDL", "DML", "READ"}), "N8N_DB_USER", "N8N_DB_PASSWORD",
    ),
    "open-webui": ConsumerContract(
        ("open-web-ui", "open-webui-init"), "postgres", ("public", "auth"),
        (
            "public.alembic_version", "public.auth", "public.chat",
            "public.channel", "public.channel_member", "public.chatidtag",
            "public.config", "public.document", "public.feedback", "public.file",
            "public.folder", "public.function", "public.group", "public.knowledge",
            "public.memory", "public.message", "public.message_reaction",
            "public.model", "public.note", "public.oauth_session", "public.prompt",
            "public.tag", "public.tool", "public.user", "public.users", "auth.users",
        ),
        (), frozenset({"DDL", "DML", "READ", "TRIGGER_ADMIN"}),
        "OPEN_WEBUI_DB_USER", "OPEN_WEBUI_DB_PASSWORD",
    ),
    "lightrag": ConsumerContract(
        ("lightrag", "lightrag-init"), "postgres", ("lightrag", "public"),
        ("lightrag.*", "public.LIGHTRAG_*"),
        ("vector",), frozenset({"DDL", "DML", "READ"}),
        "LIGHTRAG_DB_USER", "LIGHTRAG_DB_PASSWORD",
    ),
    "litellm": ConsumerContract(
        ("litellm-init", "litellm"), "litellm", ("public",), ("LiteLLM Prisma tables",), (),
        frozenset({"DDL", "DML", "READ"}), "LITELLM_DB_USER", "LITELLM_DB_PASSWORD",
    ),
    "airflow": ConsumerContract(
        ("airflow-webserver", "airflow-scheduler", "airflow-dag-processor", "airflow-init"),
        "airflow", ("public",), ("Airflow metadata tables",), (),
        frozenset({"DDL", "DML", "READ"}), "AIRFLOW_DB_USER", "AIRFLOW_DB_PASSWORD",
    ),
    "airflow-atlas-reader": ConsumerContract(
        ("airflow-init",), "postgres", ("public", "n8n", "storage"), ("*",), (),
        frozenset({"READ"}), "AIRFLOW_ATLAS_DB_USER", "AIRFLOW_ATLAS_DB_PASSWORD",
    ),
    "langfuse": ConsumerContract(
        ("langfuse-init", "langfuse-web", "langfuse-worker"), "langfuse",
        ("public",), ("Langfuse Prisma tables",), (), frozenset({"DDL", "DML", "READ"}),
        "LANGFUSE_DB_USER", "LANGFUSE_DB_PASSWORD",
    ),
    "mlflow": ConsumerContract(
        ("mlflow-init", "mlflow"), "mlflow", ("public",), ("MLflow migration tables",), (),
        frozenset({"DDL", "DML", "READ"}), "MLFLOW_DB_USER", "MLFLOW_DB_PASSWORD",
    ),
    "label-studio": ConsumerContract(
        ("label-studio-init", "label-studio"), "label_studio", ("public",),
        ("Label Studio Django tables",), (), frozenset({"DDL", "DML", "READ"}),
        "LABEL_STUDIO_DB_USER", "LABEL_STUDIO_DB_PASSWORD",
    ),
    "iceberg-rest": ConsumerContract(
        ("iceberg-rest-init", "iceberg-rest"), "iceberg", ("public",),
        ("iceberg_tables", "iceberg_namespace_properties", "iceberg_namespace"), (),
        frozenset({"DDL", "DML", "READ"}), "ICEBERG_DB_USER", "ICEBERG_DB_PASSWORD",
    ),
    "mcp-postgres": ConsumerContract(
        ("mcp-servers",), "postgres", ("public", "n8n", "storage"), ("*",), (),
        frozenset({"READ"}), "MCP_POSTGRES_DB_USER", "MCP_POSTGRES_DB_PASSWORD",
    ),
    "jupyter-postgres": ConsumerContract(
        ("jupyterhub",), "postgres", ("public", "n8n", "storage"), ("*",), (),
        frozenset({"READ"}), "JUPYTER_DB_USER", "JUPYTER_DB_PASSWORD",
    ),
    "zeppelin-postgres": ConsumerContract(
        ("zeppelin",), "postgres", ("public", "n8n", "storage"), ("*",), (),
        frozenset({"READ"}), "ZEPPELIN_DB_USER", "ZEPPELIN_DB_PASSWORD",
    ),
}


POSTGRES_CONSUMER_MARKERS = re.compile(
    r"(postgres(?:ql)?(?:\+[A-Za-z0-9_.-]+)?://|jdbc:postgresql://|"
    r"\bDATABASE_URL\b|\bPG(?:HOST|PORT|USER|PASSWORD|DATABASE)\b|"
    r"\b[A-Z0-9_]*PG_URI\b|"
    r"\bPOSTGRES_(?:USER|PASSWORD|DB)\b|DB_POSTGRESDB_|POSTGRE(?:SQL)?_|"
    r"CATALOG_JDBC_|SUPABASE_DB_|PG_META_DB_|MCP_POSTGRES_DB_|ICEBERG_DB_|"
    r"ZEPPELIN_JDBC_POSTGRES_|DB_HOST:\s*supabase-db)"
)


def _postgres_services_from_compose_documents(documents: list[dict]) -> set[str]:
    found: set[str] = set()
    for document in documents:
        for service, config in (document.get("services") or {}).items():
            if POSTGRES_CONSUMER_MARKERS.search(yaml.safe_dump(config)):
                found.add(service)
    return found


def _compose_services_with_postgres_credentials() -> set[str]:
    documents = [
        yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for path in sorted((REPO / "services").glob("*/compose.yml"))
    ]
    return _postgres_services_from_compose_documents(documents)


def _assert_inventory_matches(discovered: set[str], inventoried: set[str]) -> None:
    assert discovered == inventoried, (
        f"missing inventory: {sorted(discovered - inventoried)}; "
        f"inventory-only: {sorted(inventoried - discovered)}"
    )


def test_inventory_covers_every_compose_postgres_consumer() -> None:
    inventoried = {
        service for contract in POSTGRES_CONSUMERS.values() for service in contract.services
    }
    _assert_inventory_matches(_compose_services_with_postgres_credentials(), inventoried)


def test_inventory_comparison_rejects_synthetic_omitted_consumers() -> None:
    synthetic = {
        "services": {
            "sqlalchemy-omission": {
                "command": ["serve", "postgresql+asyncpg://user:pw@db/atlas"]
            },
            "discrete-omission": {
                "environment": {"PGHOST": "db", "PGUSER": "user"}
            },
        }
    }
    discovered = _postgres_services_from_compose_documents([synthetic])
    assert discovered == {"sqlalchemy-omission", "discrete-omission"}
    with pytest.raises(AssertionError, match="missing inventory"):
        _assert_inventory_matches(discovered, set())


def test_every_non_admin_consumer_has_a_distinct_scoped_role() -> None:
    scoped = [c for c in POSTGRES_CONSUMERS.values() if not c.administrative]
    assert len({c.user_var for c in scoped}) == len(scoped)
    assert all(c.password_var != "SUPABASE_DB_PASSWORD" for c in scoped)


def test_key_generator_backfills_every_scoped_database_secret(tmp_path: Path) -> None:
    password_vars = sorted(
        {
            contract.password_var
            for contract in POSTGRES_CONSUMERS.values()
            if not contract.administrative
        }
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PROJECT_NAME=atlas-task3\n"
        + "\n".join(f"{name}=" for name in password_vars)
        + "\n",
        encoding="utf-8",
    )

    generated_result = KeyGenerator(str(tmp_path)).generate_missing_keys()
    generated = ConfigParser(str(tmp_path)).parse_env_file()

    assert all(generated_result[name] for name in password_vars)
    assert all(generated[name] for name in password_vars)
    assert len({generated[name] for name in password_vars}) == len(password_vars)


def _compose_service_environments() -> dict[str, dict]:
    environments: dict[str, dict] = {}
    for path in sorted((REPO / "services").glob("*/compose.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        services = document.get("services") or {}
        environments.update(
            {
                service: config.get("environment") or {}
                for service, config in services.items()
            }
        )
    return environments


def test_compose_uses_scram_and_withholds_owner_credentials_from_apps() -> None:
    allowed = {"supabase-db", "supabase-db-init", "backup"}
    offenders = sorted(
        service
        for service, environment in _compose_service_environments().items()
        if service not in allowed
        if any(
            owner_variable in yaml.safe_dump(environment)
            for owner_variable in ("SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
        )
    )
    supabase = yaml.safe_load((REPO / "services/supabase/compose.yml").read_text())
    assert supabase["services"]["supabase-db"]["environment"][
        "POSTGRES_HOST_AUTH_METHOD"
    ] == "scram-sha-256"
    assert offenders == []


def test_database_startup_rewrites_legacy_host_hba_before_postgres() -> None:
    supabase = yaml.safe_load((REPO / "services/supabase/compose.yml").read_text())
    database = supabase["services"]["supabase-db"]
    assert database["command"] == ["sh", "/usr/local/bin/enforce-scram-host-auth.sh"]
    assert any(
        "enforce-scram-host-auth.sh:/usr/local/bin/enforce-scram-host-auth.sh:ro"
        in volume
        for volume in database["volumes"]
    )


def test_every_scoped_secret_has_a_compose_fail_fast_guard() -> None:
    compose_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO / "services").glob("*/compose.yml"))
    )
    missing = sorted(
        contract.password_var
        for contract in POSTGRES_CONSUMERS.values()
        if not contract.administrative
        and f"${{{contract.password_var}:?" not in compose_text
    )
    assert missing == []


def test_backup_and_restore_retain_administrative_credentials() -> None:
    backup = yaml.safe_load((REPO / "services/backup/compose.yml").read_text())
    environment = backup["services"]["backup"]["environment"]
    assert environment["SUPABASE_DB_USER"] == "${SUPABASE_DB_USER}"
    assert environment["SUPABASE_DB_PASSWORD"] == "${SUPABASE_DB_PASSWORD}"
    assert (REPO / "services/backup/init/scripts/restore-postgres.sh").is_file()
    assert 'exec sh "$@"' in (
        REPO / "services/backup/init/scripts/entrypoint.sh"
    ).read_text(encoding="utf-8")


def _role_password(label: str) -> str:
    # These credentials only need to remain stable for this test process.
    # Per-run values avoid both cross-run reuse and hardcoded test passwords.
    return f"{label}-{uuid.uuid4().hex}"


_TEST_ROLE_SPECS = (
    ("SUPABASE_AUTH_DB", "supabase_auth_admin", "auth"),
    ("SUPABASE_STORAGE_DB", "supabase_storage_admin", "storage"),
    ("SUPABASE_API_DB", "authenticator", "api"),
    ("SUPABASE_REALTIME_DB", "atlas_realtime", "realtime"),
    ("SUPABASE_META_DB", "atlas_meta", "meta"),
    ("POSTGRES_EXPORTER_DB", "atlas_metrics", "metrics"),
    ("SUPAVISOR_DB_ADMIN", "atlas_supavisor", "supavisor"),
    ("BACKEND_DB", "atlas_backend", "backend"),
    ("N8N_DB", "atlas_n8n", "n8n"),
    ("OPEN_WEBUI_DB", "atlas_open_webui", "openwebui"),
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

TEST_SECRETS = {
    key: value
    for prefix, user, label in _TEST_ROLE_SPECS
    for key, value in (
        (f"{prefix}_USER", user),
        (f"{prefix}_PASSWORD", _role_password(label)),
    )
}
TEST_SECRETS["SUPABASE_STUDIO_DB_USER"] = "atlas_studio_readonly"
TEST_SECRETS.update(
    {
        "LITELLM_DB_NAME": "litellm",
        "LANGFUSE_DB_NAME": "langfuse",
        "MLFLOW_DB_NAME": "mlflow",
        "LABEL_STUDIO_DB_NAME": "label_studio",
    }
)


def test_generated_role_test_credentials_are_complete_and_distinct() -> None:
    password_vars = {
        contract.password_var
        for contract in POSTGRES_CONSUMERS.values()
        if not contract.administrative
    }
    assert password_vars <= TEST_SECRETS.keys()

    passwords = [TEST_SECRETS[variable] for variable in sorted(password_vars)]
    assert len(set(passwords)) == len(passwords)
    assert all(len(password) >= 32 for password in passwords)


@pytest.mark.parametrize(
    ("variable", "hostile_name"),
    [
        (
            "LITELLM_DB_NAME",
            "host=attacker.invalid user=thief port=6432 sslmode=disable dbname=chosen",
        ),
        ("LANGFUSE_DB_NAME", "postgresql://attacker.invalid/chosen"),
        (
            "PGDATABASE",
            "host=attacker.invalid user=thief port=6432 sslmode=disable dbname=chosen",
        ),
    ],
)
def test_scoped_roles_rejects_libpq_conninfo_database_names_before_psql(
    tmp_path: Path, variable: str, hostile_name: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    psql_log = tmp_path / "psql-called"
    fake_psql = fake_bin / "psql"
    fake_psql.write_text(
        f"#!/bin/sh\nprintf called > {psql_log}\nexit 0\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    environment = {
        **os.environ,
        **TEST_SECRETS,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PGHOST": "127.0.0.1",
        "PGUSER": "supabase_admin",
        "PGPASSWORD": "admin-secret",
        "PGDATABASE": "postgres",
    }
    environment[variable] = hostile_name

    result = subprocess.run(
        ["sh", str(SCRIPTS / "05-scoped-roles.sh")],
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert variable in result.stderr
    assert "literal database name" in result.stderr
    assert not psql_log.exists(), "validation must run before privileged psql"


SCOPED_AUTH_DATABASE = "".join(("post", "gres"))

_SCOPED_AUTH_TARGET_SPECS = (
    ("supabase_auth_admin", "SUPABASE_AUTH_DB", SCOPED_AUTH_DATABASE),
    ("supabase_storage_admin", "SUPABASE_STORAGE_DB", SCOPED_AUTH_DATABASE),
    ("authenticator", "SUPABASE_API_DB", SCOPED_AUTH_DATABASE),
    ("atlas_realtime", "SUPABASE_REALTIME_DB", SCOPED_AUTH_DATABASE),
    ("atlas_meta", "SUPABASE_META_DB", SCOPED_AUTH_DATABASE),
    ("atlas_studio_readonly", "SUPABASE_META_DB", SCOPED_AUTH_DATABASE),
    ("atlas_metrics", "POSTGRES_EXPORTER_DB", SCOPED_AUTH_DATABASE),
    ("atlas_supavisor", "SUPAVISOR_DB_ADMIN", "supavisor"),
    ("atlas_backend", "BACKEND_DB", SCOPED_AUTH_DATABASE),
    ("atlas_n8n", "N8N_DB", SCOPED_AUTH_DATABASE),
    ("atlas_open_webui", "OPEN_WEBUI_DB", SCOPED_AUTH_DATABASE),
    ("atlas_lightrag", "LIGHTRAG_DB", SCOPED_AUTH_DATABASE),
    ("litellm", "LITELLM_DB", "litellm"),
    ("airflow", "AIRFLOW_DB", "airflow"),
    ("atlas_airflow_reader", "AIRFLOW_ATLAS_DB", SCOPED_AUTH_DATABASE),
    ("langfuse", "LANGFUSE_DB", "langfuse"),
    ("mlflow", "MLFLOW_DB", "mlflow"),
    ("label_studio", "LABEL_STUDIO_DB", "label_studio"),
    ("iceberg", "ICEBERG_DB", "iceberg"),
    ("atlas_mcp", "MCP_POSTGRES_DB", SCOPED_AUTH_DATABASE),
    ("atlas_jupyter", "JUPYTER_DB", SCOPED_AUTH_DATABASE),
    ("atlas_zeppelin", "ZEPPELIN_DB", SCOPED_AUTH_DATABASE),
)

SCOPED_AUTH_TARGETS = tuple(
    (role, f"{prefix}_PASSWORD", database)
    for role, prefix, database in _SCOPED_AUTH_TARGET_SPECS
)


def _run(*args: str, check: bool = True, timeout: int = COMMAND_TIMEOUT, **kwargs):
    return subprocess.run(
        list(args), check=check, text=True, capture_output=True, timeout=timeout, **kwargs
    )


def _ready_log_count(container: str) -> int:
    logs = _run("docker", "logs", container, check=False, timeout=10)
    return (logs.stdout + logs.stderr).count(
        "database system is ready to accept connections"
    )


def _require_disposable_postgres_runtime() -> None:
    in_ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"}
    if shutil.which("docker") is None:
        if in_ci:
            pytest.fail("Docker CLI is required for database role boundary drills in CI")
        pytest.skip("Docker CLI unavailable")
    try:
        daemon = _run("docker", "info", check=False, timeout=10)
    except (subprocess.SubprocessError, OSError) as exc:
        if not in_ci:
            pytest.skip("Docker daemon unavailable")
        pytest.fail(f"Docker daemon probe failed: {type(exc).__name__}: {exc}")
    if daemon.returncode != 0:
        if not in_ci:
            pytest.skip("Docker daemon unavailable")
        pytest.fail(f"Docker daemon probe failed: {daemon.stderr[-1000:]}")
    for image in (POSTGRES_IMAGE, INIT_IMAGE):
        _require_local_image(image, in_ci=in_ci)


def _require_local_image(image: str, *, in_ci: bool) -> None:
    try:
        result = _run("docker", "image", "inspect", image, check=False, timeout=10)
    except (subprocess.SubprocessError, OSError) as exc:
        pytest.fail(
            f"Docker image probe failed for {image}: {type(exc).__name__}: {exc}"
        )
    if result.returncode == 0:
        return
    if not in_ci and "no such image" in result.stderr.lower():
        pytest.skip(f"required local image absent: {image}")
    pytest.fail(f"Docker image probe failed for {image}: {result.stderr[-1000:]}")


def _start_disposable_postgres(
    *, container: str, network: str, admin_password: str, owner_token: str,
) -> None:
    compose = yaml.safe_load((REPO / "services/supabase/compose.yml").read_text())
    auth_method = compose["services"]["supabase-db"]["environment"].get(
        "POSTGRES_HOST_AUTH_METHOD", "scram-sha-256"
    )
    start = _run(
        "docker", "run", "--detach", "--rm", "--pull=never", "--name", container,
        "--label", f"{DATABASE_ROLE_OWNER_LABEL}={owner_token}",
        "--network", network, "--network-alias", "supabase-db",
        "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=1536m",
        "-v", f"{SCRIPTS / 'enforce-scram-host-auth.sh'}:/usr/local/bin/enforce-scram-host-auth.sh:ro",
        "-e", "POSTGRES_USER=supabase_admin", "-e", f"POSTGRES_PASSWORD={admin_password}",
        "-e", "POSTGRES_DB=postgres", "-e", f"POSTGRES_HOST_AUTH_METHOD={auth_method}",
        POSTGRES_IMAGE, "sh", "-c",
        "while true; do sh /usr/local/bin/enforce-scram-host-auth.sh "
        "-c listen_addresses='*' -c wal_level=logical; rc=$?; "
        "[ -f /tmp/atlas-task3-stop ] && exit $rc; sleep 1; done",
        check=False, timeout=POSTGRES_CREATE_TIMEOUT,
    )
    if start.returncode == 0:
        return
    if "no space left on device" in start.stderr.lower():
        raise _StorageBlockedStartup(
            f"Docker storage exhaustion despite tmpfs: {start.stderr[-2000:]}"
        )
    pytest.fail(f"disposable PostgreSQL failed to start: {start.stderr[-2000:]}")


def _wait_for_disposable_postgres(container: str, admin_password: str) -> None:
    del admin_password  # pg_isready reports server state without authentication.
    seed_harness.wait_for_postgres(
        container, timeout_seconds=45, poll_interval=0.25
    )


def _add_exception_note(exc: BaseException, note: str) -> None:
    if hasattr(exc, "add_note"):
        exc.add_note(note)
        return
    notes = getattr(exc, "__notes__", None)
    if notes is None:
        notes = []
        exc.__notes__ = notes
    notes.append(note)


def _inspect_disposable_resource(kind: str, name: str) -> dict | None:
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
    record = _inspect_disposable_resource(kind, name)
    if record is None:
        return
    labels = record.get("Config", {}).get("Labels") or record.get("Labels") or {}
    actual = record.get("Name", "").lstrip("/")
    assert actual == name
    if labels.get(DATABASE_ROLE_OWNER_LABEL) != token:
        return
    command = (
        ("docker", "rm", "-f", name)
        if kind == "container"
        else ("docker", "network", "rm", name)
    )
    removed = _run(*command, check=False, timeout=10)
    assert removed.returncode == 0, removed.stderr or removed.stdout


def _assert_no_owned_disposable(kind: str, name: str, token: str) -> None:
    record = _inspect_disposable_resource(kind, name)
    if record is None:
        return
    labels = record.get("Config", {}).get("Labels") or record.get("Labels") or {}
    assert labels.get(DATABASE_ROLE_OWNER_LABEL) != token


def _role_cleanup_pass(
    resources: tuple[str, str, str],
) -> list[tuple[str, BaseException]]:
    container, network, token = resources
    failures: list[tuple[str, BaseException]] = []
    for kind, name in (("container", container), ("network", network)):
        try:
            _remove_disposable_if_owned(kind, name, token)
        except BaseException as exc:
            failures.append((f"{kind} removal {name}", exc))
    for kind, name in (("container", container), ("network", network)):
        try:
            _assert_no_owned_disposable(kind, name, token)
        except BaseException as exc:
            failures.append((f"{kind} absence {name}", exc))
    return failures


def _cleanup_disposable_postgres(
    resources: tuple[str, str, str],
    *,
    primary_error: BaseException | None,
    uncertain: bool,
) -> None:
    deferred_error = primary_error
    settle_until, deferred_error = seed_harness.establish_cleanup_deadline(
        POSTGRES_CREATE_TIMEOUT if uncertain else None, deferred_error
    )
    while True:
        errors = _role_cleanup_pass(resources)
        deferred_error = seed_harness.defer_cleanup_failures(deferred_error, errors)
        settle_until, deferred_error = (
            seed_harness.begin_reconciliation_after_interruption(
                settle_until,
                POSTGRES_CREATE_TIMEOUT,
                deferred_error,
                errors,
            )
        )
        expired, deferred_error = seed_harness.cleanup_deadline_expired(
            settle_until, deferred_error
        )
        if expired:
            if not errors:
                seed_harness.raise_deferred_cleanup_error(
                    primary_error, deferred_error
                )
                return
            detail = "; ".join(
                f"{operation}: {type(exc).__name__}: {exc}"
                for operation, exc in errors
            )
            note = f"Disposable database-role cleanup could not be proven: {detail}"
            if deferred_error is not None:
                _add_exception_note(deferred_error, note)
                seed_harness.raise_deferred_cleanup_error(
                    primary_error, deferred_error
                )
                return
            else:
                _add_exception_note(errors[0][1], note)
                raise errors[0][1]
        deferred_error = seed_harness.sleep_for_cleanup(0.1, deferred_error)


def _role_client_cleanup_pass(
    resource: tuple[str, str, int],
) -> list[tuple[str, BaseException]]:
    name, token, _reconcile_seconds = resource
    failures: list[tuple[str, BaseException]] = []
    for operation in (_remove_disposable_if_owned, _assert_no_owned_disposable):
        try:
            operation("container", name, token)
        except BaseException as exc:
            failures.append((f"{operation.__name__} {name}", exc))
    return failures


def _cleanup_disposable_role_client(
    resource: tuple[str, str, int],
    *,
    primary_error: BaseException | None,
    uncertain: bool,
) -> None:
    deferred_error = primary_error
    settle_until, deferred_error = seed_harness.establish_cleanup_deadline(
        resource[2] if uncertain else None, deferred_error
    )
    while True:
        failures = _role_client_cleanup_pass(resource)
        deferred_error = seed_harness.defer_cleanup_failures(deferred_error, failures)
        settle_until, deferred_error = (
            seed_harness.begin_reconciliation_after_interruption(
                settle_until,
                resource[2],
                deferred_error,
                failures,
            )
        )
        expired, deferred_error = seed_harness.cleanup_deadline_expired(
            settle_until, deferred_error
        )
        if expired:
            if not failures:
                seed_harness.raise_deferred_cleanup_error(
                    primary_error, deferred_error
                )
                return
            detail = "; ".join(
                f"{operation}: {type(exc).__name__}: {exc}"
                for operation, exc in failures
            )
            note = f"Disposable database-role client cleanup could not be proven: {detail}"
            if deferred_error is not None:
                _add_exception_note(deferred_error, note)
                seed_harness.raise_deferred_cleanup_error(
                    primary_error, deferred_error
                )
                return
            else:
                _add_exception_note(failures[0][1], note)
                raise failures[0][1]
        deferred_error = seed_harness.sleep_for_cleanup(0.1, deferred_error)


def _run_owned_role_client(
    args: list[str], *, timeout: int, check: bool = True,
) -> subprocess.CompletedProcess[str]:
    token = uuid.uuid4().hex
    name = f"atlas-db-role-client-{token[:12]}"
    resource = (name, token, timeout)
    primary_error: BaseException | None = None
    uncertain = True
    try:
        result = _run(
            "docker", "run", "--rm", "--name", name,
            "--label", f"{DATABASE_ROLE_OWNER_LABEL}={token}",
            *args, check=check, timeout=timeout,
        )
        uncertain = result.returncode == 125
        return result
    except subprocess.CalledProcessError as exc:
        primary_error = exc
        uncertain = exc.returncode == 125
        raise
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        _cleanup_disposable_role_client(
            resource, primary_error=primary_error, uncertain=uncertain
        )


@contextmanager
def owned_role_container(
    role: str, args: list[str], *, timeout: int = COMMAND_TIMEOUT,
) -> Iterator[str]:
    token = uuid.uuid4().hex
    name = f"atlas-{role}-{token[:12]}"
    resource = (name, token, timeout)
    primary_error: BaseException | None = None
    uncertain = True
    try:
        start = _run(
            "docker", "run", "--detach", "--pull=never", "--name", name,
            "--label", f"{DATABASE_ROLE_OWNER_LABEL}={token}",
            *args, check=False, timeout=timeout,
        )
        if start.returncode != 0:
            raise subprocess.CalledProcessError(
                start.returncode, start.args, start.stdout, start.stderr
            )
        uncertain = False
        yield name
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        _cleanup_disposable_role_client(
            resource, primary_error=primary_error, uncertain=uncertain
        )


@dataclass(frozen=True)
class DisposablePostgres:
    container: str
    network: str
    admin_password: str

    def sql(
        self, statement: str, *, user: str = "supabase_admin", password: str | None = None,
        database: str = "postgres", check: bool = True,
    ):
        env_args: list[str] = []
        if password is not None:
            env_args = ["-e", f"PGPASSWORD={password}"]
        return _run(
            "docker", "exec", *env_args, self.container, "psql", "-X", "-w",
            "-h", "127.0.0.1", "-v", "ON_ERROR_STOP=1", "-U", user,
            "-d", database, "-Atqc", statement, check=check,
        )

    def run_init(self) -> None:
        args = [
            "--pull=never", "--network", self.network,
            "-e", "PGHOST=supabase-db", "-e", "PGUSER=supabase_admin",
            "-e", f"PGPASSWORD={self.admin_password}", "-e", "PGDATABASE=postgres",
        ]
        for name, value in TEST_SECRETS.items():
            args.extend(("-e", f"{name}={value}"))
        args.extend(
            (
                "-v", f"{SCRIPTS}:/scripts:ro", "-e", "ATLAS_DB_INIT_USER_SCRIPT_DIR=/absent",
                INIT_IMAGE, "/scripts/db-init-runner.sh",
            )
        )
        _run_owned_role_client(args, timeout=120)

    def network_sql(
        self, statement: str, *, user: str = "supabase_admin", password: str | None = None,
        database: str = "postgres", check: bool = True,
    ):
        args = ["--pull=never", "--network", self.network]
        if password is not None:
            args.extend(("-e", f"PGPASSWORD={password}"))
        args.extend(
            (
                INIT_IMAGE, "psql", "-X", "-w", "-h", "supabase-db",
                "-v", "ON_ERROR_STOP=1", "-U", user, "-d", database,
                "-Atqc", statement,
            )
        )
        return _run_owned_role_client(args, check=check, timeout=20)


@pytest.fixture(scope="module")
def disposable_postgres() -> Iterator[DisposablePostgres]:
    _require_disposable_postgres_runtime()

    owner_token = uuid.uuid4().hex
    suffix = owner_token[:12]
    network = f"atlas-db-roles-{suffix}"
    container = f"atlas-db-roles-pg-{suffix}"
    admin_password = _role_password("admin")
    retain_for_storage_diagnostics = False
    primary_error: BaseException | None = None
    try:
        _run(
            "docker", "network", "create", "--label",
            f"{DATABASE_ROLE_OWNER_LABEL}={owner_token}", network,
        )
        _start_disposable_postgres(
            container=container, network=network, admin_password=admin_password,
            owner_token=owner_token,
        )
        _wait_for_disposable_postgres(container, admin_password)
        database = DisposablePostgres(container, network, admin_password)
        database.run_init()
        yield database
    except _StorageBlockedStartup as exc:
        retain_for_storage_diagnostics = True
        pytest.fail(str(exc))
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        # Preserve only the explicitly classified storage-blocked resource.
        # Every other success, failure, timeout, and interrupt removes both
        # preallocated names without hiding an in-flight primary error.
        if not retain_for_storage_diagnostics:
            _cleanup_disposable_postgres(
                (container, network, owner_token),
                primary_error=primary_error, uncertain=primary_error is not None,
            )


def test_disposable_role_fixture_labels_network_and_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "a" * 32
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(sys.modules[__name__], "_require_disposable_postgres_runtime", lambda: None)
    monkeypatch.setattr(uuid, "uuid4", lambda: type("UUID", (), {"hex": token})())
    monkeypatch.setattr(
        sys.modules[__name__], "_wait_for_disposable_postgres", lambda *_args: None
    )
    monkeypatch.setattr(DisposablePostgres, "run_init", lambda _self: None)

    def run(*args, **_kwargs):
        calls.append(args)
        if args[1:3] in (("container", "inspect"), ("network", "inspect")):
            return subprocess.CompletedProcess(args, 1, "", "not found")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(sys.modules[__name__], "_run", run)
    fixture = disposable_postgres.__wrapped__()
    next(fixture)
    with pytest.raises(StopIteration):
        next(fixture)

    network_create = next(args for args in calls if args[1:3] == ("network", "create"))
    container_run = next(args for args in calls if args[1] == "run")
    expected = f"{DATABASE_ROLE_OWNER_LABEL}={token}"
    assert expected in network_create
    assert expected in container_run


def test_disposable_role_cleanup_preserves_foreign_collisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removals: list[tuple[str, ...]] = []
    ticks = iter((0.0, 61.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        if args[1:3] == ("container", "inspect"):
            record = {
                "Name": "/foreign-container",
                "Config": {"Labels": {DATABASE_ROLE_OWNER_LABEL: "foreign"}},
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        if args[1:3] == ("network", "inspect"):
            record = {
                "Name": "foreign-network",
                "Labels": {DATABASE_ROLE_OWNER_LABEL: "foreign"},
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        if args[1:3] in (("rm", "-f"), ("network", "rm")):
            removals.append(args[1:])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(sys.modules[__name__], "_run", run)
    _cleanup_disposable_postgres(
        ("foreign-container", "foreign-network", "ours"),
        primary_error=RuntimeError("create collision"), uncertain=True,
    )
    assert removals == []


def test_disposable_role_cleanup_reconciles_visibility_after_thirty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "ours"
    network_inspections = 0
    network_visible = False
    removals: list[tuple[str, ...]] = []
    ticks = iter((0.0, 35.0, 61.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        nonlocal network_inspections, network_visible
        if args[1:3] == ("container", "inspect"):
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[1:3] == ("network", "inspect"):
            network_inspections += 1
            if network_inspections >= 3 and not removals:
                network_visible = True
            if network_visible:
                record = {
                    "Name": "late-network",
                    "Labels": {DATABASE_ROLE_OWNER_LABEL: token},
                }
                return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[1:3] == ("network", "rm"):
            removals.append(args[1:])
            network_visible = False
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(sys.modules[__name__], "_run", run)
    _cleanup_disposable_postgres(
        ("absent-container", "late-network", token),
        primary_error=RuntimeError("ambiguous create"), uncertain=True,
    )
    assert removals == [("network", "rm", "late-network")]


def test_disposable_role_cleanup_failure_preserves_primary_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = RuntimeError("primary create failure")
    ticks = iter((0.0, 61.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        sys.modules[__name__], "_run",
        lambda *args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args, 10)
        ),
    )

    _cleanup_disposable_postgres(
        ("container", "network", "ours"),
        primary_error=primary, uncertain=True,
    )

    assert "cleanup could not be proven" in "\n".join(primary.__notes__)


def test_host_tcp_rejects_passwordless_authentication(
    disposable_postgres: DisposablePostgres,
) -> None:
    result = disposable_postgres.network_sql("SELECT 1", password=None, check=False)
    assert result.returncode != 0, "host TCP accepted supabase_admin without a password"
    wrong = disposable_postgres.network_sql(
        "SELECT 1", password="wrong-password", check=False
    )
    assert wrong.returncode != 0, "host TCP accepted an invalid password"
    assert disposable_postgres.network_sql(
        "SELECT 1", password=disposable_postgres.admin_password
    ).stdout == "1\n"


def test_service_role_cannot_read_or_drop_another_services_objects(
    disposable_postgres: DisposablePostgres,
) -> None:
    # Both objects are created by the role that production Compose gives that
    # service.  Under the vulnerable configuration both identities collapse to
    # supabase_admin, making both the SELECT and transactional DROP succeed.
    n8n_env = yaml.safe_load((REPO / "services/n8n/compose.yml").read_text())[
        "services"
    ]["n8n"]["environment"]
    backend_env = yaml.safe_load(
        (REPO / "services/backend/compose.yml").read_text()
    )["services"]["backend"]["environment"]

    def configured_credential(environment: dict, user_var: str, password_var: str):
        # Resolve the credential family selected by the production fragment.
        # This makes the same drill witness today's shared-owner behavior and
        # the migrated scoped behavior without a test-only database setup path.
        rendered = yaml.safe_dump(environment)
        if user_var in rendered and password_var in rendered:
            return TEST_SECRETS[user_var], TEST_SECRETS[password_var]
        return "supabase_admin", disposable_postgres.admin_password

    n8n_user, n8n_password = configured_credential(
        n8n_env, "N8N_DB_USER", "N8N_DB_PASSWORD"
    )
    backend_user, backend_password = configured_credential(
        backend_env, "BACKEND_DB_USER", "BACKEND_DB_PASSWORD"
    )

    disposable_postgres.sql(
        "CREATE TABLE IF NOT EXISTS n8n.task3_boundary(secret text); "
        "TRUNCATE n8n.task3_boundary; INSERT INTO n8n.task3_boundary VALUES ('n8n')",
        user=n8n_user, password=n8n_password,
    )
    read = disposable_postgres.sql(
        "SELECT secret FROM n8n.task3_boundary",
        user=backend_user, password=backend_password,
        check=False,
    )
    drop = disposable_postgres.sql(
        "BEGIN; DROP TABLE n8n.task3_boundary; ROLLBACK",
        user=backend_user, password=backend_password,
        check=False,
    )
    assert read.returncode != 0, "backend role read the n8n-owned schema"
    assert drop.returncode != 0, "backend role could drop an n8n-owned table"


def test_upgrade_transfers_legacy_schema_objects_to_scoped_role(
    disposable_postgres: DisposablePostgres,
) -> None:
    disposable_postgres.sql(
        "CREATE TABLE IF NOT EXISTS n8n.task3_legacy(id bigint); "
        "ALTER TABLE n8n.task3_legacy OWNER TO supabase_admin; "
        "CREATE OR REPLACE FUNCTION n8n.task3_legacy_fn() RETURNS bigint "
        "LANGUAGE sql AS 'SELECT 1::bigint'; "
        "ALTER FUNCTION n8n.task3_legacy_fn() OWNER TO supabase_admin",
        password=disposable_postgres.admin_password,
    )

    disposable_postgres.run_init()

    owners = disposable_postgres.sql(
        "SELECT 'table=' || pg_get_userbyid(c.relowner) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='n8n' AND c.relname='task3_legacy' "
        "UNION ALL SELECT 'function=' || pg_get_userbyid(p.proowner) "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='n8n' AND p.proname='task3_legacy_fn' ORDER BY 1",
        password=disposable_postgres.admin_password,
    ).stdout.splitlines()
    assert owners == ["function=atlas_n8n", "table=atlas_n8n"]


def _scoped_role_verifiers(disposable_postgres: DisposablePostgres) -> str:
    role_names = ",".join(f"'{role}'" for role, _, _ in SCOPED_AUTH_TARGETS)
    return disposable_postgres.sql(
        "SELECT rolname || ':' || rolpassword FROM pg_authid "
        f"WHERE rolname IN ({role_names}) ORDER BY rolname",
        password=disposable_postgres.admin_password,
    ).stdout


def _assert_all_scoped_roles_authenticate(
    disposable_postgres: DisposablePostgres,
) -> None:
    for role, password_var, database in SCOPED_AUTH_TARGETS:
        authenticated = disposable_postgres.network_sql(
            "SELECT current_user",
            user=role,
            password=TEST_SECRETS[password_var],
            database=database,
            check=False,
        )
        assert authenticated.returncode == 0, (
            f"{role} failed authentication after second provisioning: "
            f"{authenticated.stderr}"
        )
        assert authenticated.stdout == f"{role}\n"


def _assert_role_settings_do_not_expose_raw_password_hashes(
    disposable_postgres: DisposablePostgres,
) -> None:
    settings = disposable_postgres.sql(
        "SELECT coalesce(array_to_string(rolconfig, ','), '') FROM pg_roles "
        "WHERE rolname LIKE 'atlas_%' OR rolname IN "
        "('authenticator','supabase_auth_admin','supabase_storage_admin',"
        "'litellm','airflow','langfuse','mlflow','label_studio','iceberg')",
        password=disposable_postgres.admin_password,
    ).stdout
    for _, password_var, _ in SCOPED_AUTH_TARGETS:
        raw_hash = hashlib.sha256(TEST_SECRETS[password_var].encode()).hexdigest()
        assert raw_hash not in settings


def _restart_postgres_and_wait(
    disposable_postgres: DisposablePostgres, ready_count_before: int
) -> None:
    _run(
        "docker", "exec", "--user", "postgres", disposable_postgres.container,
        "pg_ctl", "-D", "/var/lib/postgresql/data", "-m", "fast", "-W", "stop",
        timeout=20,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ready = disposable_postgres.sql(
            "SELECT 1", password=disposable_postgres.admin_password, check=False
        )
        if ready.returncode == 0 and (
            _ready_log_count(disposable_postgres.container) >= ready_count_before + 1
        ):
            return
        time.sleep(0.25)
    pytest.fail("disposable PostgreSQL did not recover within 30s after restart")


def test_role_provisioning_is_idempotent_and_restart_safe(
    disposable_postgres: DisposablePostgres,
) -> None:
    before = _scoped_role_verifiers(disposable_postgres)
    disposable_postgres.run_init()
    after_second_provisioning = _scoped_role_verifiers(disposable_postgres)
    assert before == after_second_provisioning
    assert len(before.splitlines()) == len(SCOPED_AUTH_TARGETS)
    assert all(":SCRAM-SHA-256$" in line for line in before.splitlines())
    _assert_all_scoped_roles_authenticate(disposable_postgres)
    _assert_role_settings_do_not_expose_raw_password_hashes(disposable_postgres)
    # Model an upgraded volume whose HBA was initialized under the old trust
    # setting.  Reload proves the legacy rule is active before the production
    # startup wrapper contracts it on the supervised restart below.
    _run(
        "docker", "exec", disposable_postgres.container, "sh", "-c",
        "sed -i -E '/^[[:space:]]*host/ s/scram-sha-256/trust/' "
        '"${PGDATA:-/var/lib/postgresql/data}/pg_hba.conf"',
    )
    disposable_postgres.sql(
        "SELECT pg_reload_conf()", password=disposable_postgres.admin_password
    )
    assert disposable_postgres.network_sql(
        "SELECT 1", password=None, check=False
    ).returncode == 0
    ready_count_before = _ready_log_count(disposable_postgres.container)
    _restart_postgres_and_wait(disposable_postgres, ready_count_before)
    after = _scoped_role_verifiers(disposable_postgres)
    assert before == after
    assert disposable_postgres.network_sql(
        "SELECT 1", password=None, check=False
    ).returncode != 0
