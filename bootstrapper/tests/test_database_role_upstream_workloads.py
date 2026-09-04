"""Pinned Supabase migration/health workloads for scoped database owners."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from urllib.parse import quote
import uuid

import pytest

from tests import test_database_role_boundaries as roles
from tests.test_database_role_boundaries import (
    TEST_SECRETS,
    DisposablePostgres,
    disposable_postgres,
)
from utils.key_generator import KeyGenerator


REPO = Path(__file__).resolve().parents[2]
REALTIME_IMAGE = "supabase/realtime:v2.112.0"
STORAGE_IMAGE = "supabase/storage-api:v1.61.5"


def _run(*args: str, check: bool = True, timeout: int = 30):
    return subprocess.run(
        list(args), text=True, capture_output=True, check=check, timeout=timeout
    )


def _wait_for_health(container: str, url: str, *, timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            "docker", "exec", container, "sh", "-c",
            "if command -v curl >/dev/null; then curl -sSL -o /dev/null \"$1\"; "
            "else wget -q -O /dev/null \"$1\"; fi", "sh", url,
            check=False, timeout=5,
        )
        if result.returncode == 0:
            return
        inspect = _run(
            "docker", "inspect", "-f", "{{.State.Running}}", container,
            check=False, timeout=5,
        )
        if inspect.stdout.strip() != "true":
            break
        time.sleep(0.25)
    logs = _run("docker", "logs", "--tail", "160", container, check=False, timeout=10)
    pytest.fail(f"{container} health deadline exceeded:\n{(logs.stdout + logs.stderr)[-8000:]}")


def test_upgrade_transfers_storage_and_realtime_management_objects(
    disposable_postgres: DisposablePostgres,
) -> None:
    disposable_postgres.sql(
        "CREATE TABLE IF NOT EXISTS storage.task3_legacy_table(id bigint); "
        "ALTER TABLE storage.task3_legacy_table OWNER TO supabase_admin; "
        "CREATE SEQUENCE IF NOT EXISTS storage.task3_legacy_seq; "
        "ALTER SEQUENCE storage.task3_legacy_seq OWNER TO supabase_admin; "
        "CREATE OR REPLACE FUNCTION storage.task3_legacy_fn() RETURNS bigint "
        "LANGUAGE sql AS 'SELECT 1::bigint'; "
        "ALTER FUNCTION storage.task3_legacy_fn() OWNER TO supabase_admin; "
        "CREATE TABLE IF NOT EXISTS realtime.task3_legacy_table(id bigint); "
        "ALTER TABLE realtime.task3_legacy_table OWNER TO supabase_admin; "
        "CREATE SEQUENCE IF NOT EXISTS realtime.task3_legacy_seq; "
        "ALTER SEQUENCE realtime.task3_legacy_seq OWNER TO supabase_admin; "
        "CREATE OR REPLACE FUNCTION realtime.task3_legacy_fn() RETURNS bigint "
        "LANGUAGE sql AS 'SELECT 1::bigint'; "
        "ALTER FUNCTION realtime.task3_legacy_fn() OWNER TO supabase_admin",
        password=disposable_postgres.admin_password,
    )

    disposable_postgres.run_init()

    owners = disposable_postgres.sql(
        "SELECT n.nspname || ':schema=' || pg_get_userbyid(n.nspowner) "
        "FROM pg_namespace n WHERE n.nspname IN ('storage','realtime') "
        "UNION ALL SELECT n.nspname || ':' || c.relname || '=' || "
        "pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname IN ('storage','realtime') "
        "AND c.relname LIKE 'task3_legacy_%' "
        "UNION ALL SELECT n.nspname || ':' || p.proname || '=' || "
        "pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n "
        "ON n.oid=p.pronamespace WHERE n.nspname IN ('storage','realtime') "
        "AND p.proname='task3_legacy_fn' ORDER BY 1",
        password=disposable_postgres.admin_password,
    ).stdout.splitlines()
    assert owners == [
        "realtime:schema=atlas_realtime",
        "realtime:task3_legacy_fn=atlas_realtime",
        "realtime:task3_legacy_seq=atlas_realtime",
        "realtime:task3_legacy_table=atlas_realtime",
        "storage:schema=supabase_storage_admin",
        "storage:task3_legacy_fn=supabase_storage_admin",
        "storage:task3_legacy_seq=supabase_storage_admin",
        "storage:task3_legacy_table=supabase_storage_admin",
    ]


def test_pinned_storage_runs_migrations_and_serves_health_as_scoped_owner(
    disposable_postgres: DisposablePostgres,
) -> None:
    database_url = "".join(
        (
            "postgresql://",
            "supabase_storage_admin",
            ":",
            TEST_SECRETS["SUPABASE_STORAGE_DB_PASSWORD"],
            "@supabase-db:5432/postgres",
        )
    )
    args = [
        "--network", disposable_postgres.network,
        "--tmpfs", "/var/lib/storage:rw,noexec,nosuid,size=64m",
        "-e", f"DATABASE_URL={database_url}",
        "-e", f"JWT_SECRET={'j' * 64}",
        "-e", "ANON_KEY=task3-anon",
        "-e", "SERVICE_KEY=task3-service",
        "-e", "REGION=local",
        "-e", "FILE_SIZE_LIMIT=52428800",
        "-e", "STORAGE_BACKEND=file",
        "-e", "FILE_STORAGE_PATH=/var/lib/storage",
        "-e", "FILE_STORAGE_BACKEND_PATH=/var/lib/storage",
        "-e", "TENANT_ID=stub",
        "-e", "PROJECT_REF=stub",
        "-e", f"PGRST_JWT_SECRET={'j' * 64}",
        "-e", "POSTGREST_URL=http://127.0.0.1:9",
        "-e", "GOTRUE_URL=http://127.0.0.1:9",
        STORAGE_IMAGE,
    ]
    with roles.owned_role_container("storage-review", args) as container:
        _wait_for_health(container, "http://127.0.0.1:5000/status")
    migration_owner = disposable_postgres.sql(
        "SELECT tableowner FROM pg_tables WHERE schemaname='storage' "
        "AND tablename='migrations'",
        password=disposable_postgres.admin_password,
    ).stdout.strip()
    assert migration_owner == "supabase_storage_admin"


def test_pinned_realtime_runs_migrations_and_serves_health_as_scoped_owner(
    disposable_postgres: DisposablePostgres,
) -> None:
    args = [
        "--network", disposable_postgres.network,
        "-e", "DB_HOST=supabase-db",
        "-e", "DB_PORT=5432",
        "-e", "DB_NAME=postgres",
        "-e", "DB_USER=atlas_realtime",
        "-e", f"DB_PASSWORD={TEST_SECRETS['SUPABASE_REALTIME_DB_PASSWORD']}",
        "-e", "DB_SLOT=supabase_realtime_slot",
        "-e", "DB_CHANNEL_ENABLED=true",
        "-e", f"JWT_SECRET={'j' * 64}",
        "-e", "JWT_ROLE=service_role",
        "-e", "PORT=4000",
        "-e", "REPLICATION_MODE=RLS",
        "-e", "SECURE_CHANNELS=true",
        "-e", "IP_VERSION=ipv4",
        "-e", f"SECRET_KEY_BASE={'s' * 64}",
        "-e", f"METRICS_JWT_SECRET={'m' * 64}",
        "-e", "RLIMIT_NOFILE=65536",
        "-e", "ERL_AFLAGS=-proto_dist inet_tcp",
        "-e", "HOSTNAME=supabase-realtime",
        "-e", "DNS_NODES=supabase-realtime-noop.invalid",
        "-e", "APP_NAME=realtime",
        REALTIME_IMAGE,
    ]
    with roles.owned_role_container("realtime-review", args) as container:
        _wait_for_health(container, "http://127.0.0.1:4000/")

    management_owners = disposable_postgres.sql(
        "SELECT tablename || '=' || tableowner FROM pg_tables "
        "WHERE schemaname='public' AND tablename IN "
        "('tenants','extensions','feature_flags') ORDER BY tablename",
        password=disposable_postgres.admin_password,
    ).stdout.splitlines()
    assert management_owners == [
        "extensions=atlas_realtime",
        "feature_flags=atlas_realtime",
        "tenants=atlas_realtime",
    ]


def test_studio_render_preserves_complete_encoded_postgres_contract(
    tmp_path: Path,
) -> None:
    raw_user = "atlas:meta/@%?#"
    raw_read_only_user = "atlas:studio-read/@%?#"
    raw_password = "pw@:/%?# value"
    raw_database = "atlas /?#% database"
    env_file = tmp_path / ".env"
    source = (REPO / ".env.example").read_text(encoding="utf-8")
    source = source.replace(
        "SUPABASE_META_DB_USER=atlas_meta",
        f'SUPABASE_META_DB_USER="{raw_user}"',
    ).replace(
        "SUPABASE_META_DB_PASSWORD=atlas-db-password",
        f'SUPABASE_META_DB_PASSWORD="{raw_password}"',
    ).replace(
        "SUPABASE_STUDIO_DB_USER=atlas_studio_readonly",
        f'SUPABASE_STUDIO_DB_USER="{raw_read_only_user}"',
    ).replace(
        "SUPABASE_DB_NAME=postgres",
        f'SUPABASE_DB_NAME="{raw_database}"',
    )
    env_file.write_text(source, encoding="utf-8")
    KeyGenerator(str(tmp_path)).generate_missing_keys()

    rendered = _run(
        "docker", "compose", "--env-file", str(env_file),
        "-p", "atlas-task3-review", "-f", str(REPO / "docker-compose.yml"),
        "config", "--format", "json", timeout=60,
    )
    services = json.loads(rendered.stdout)["services"]
    studio = services["supabase-studio"]["environment"]
    meta = services["supabase-meta"]["environment"]
    db_init = services["supabase-db-init"]["environment"]

    actual = {
        "studio_host": studio["POSTGRES_HOST"],
        "studio_port": studio["POSTGRES_PORT"],
        "studio_database": studio["POSTGRES_DB"],
        "studio_read_write": studio["POSTGRES_USER_READ_WRITE"],
        "studio_read_only": studio["POSTGRES_USER_READ_ONLY"],
        "studio_password": studio["POSTGRES_PASSWORD"],
        "meta_user": meta["PG_META_DB_USER"],
        "meta_password": meta["PG_META_DB_PASSWORD"],
        "meta_database": meta["PG_META_DB_NAME"],
        "init_studio_user": db_init["SUPABASE_STUDIO_DB_USER"],
    }
    assert actual == {
        "studio_host": "supabase-db",
        "studio_port": "5432",
        "studio_database": quote(raw_database, safe=""),
        "studio_read_write": quote(raw_user, safe=""),
        "studio_read_only": quote(raw_read_only_user, safe=""),
        "studio_password": quote(raw_password, safe=""),
        "meta_user": raw_user,
        "meta_password": raw_password,
        "meta_database": raw_database,
        "init_studio_user": raw_read_only_user,
    }


def test_studio_read_only_role_can_query_but_cannot_mutate_or_administer(
    disposable_postgres: DisposablePostgres,
) -> None:
    password = TEST_SECRETS["SUPABASE_META_DB_PASSWORD"]
    user = TEST_SECRETS["SUPABASE_STUDIO_DB_USER"]
    future_tables = (
        ("task3_studio_future_admin", "supabase_admin", disposable_postgres.admin_password),
        ("task3_studio_future_meta", "atlas_meta", TEST_SECRETS["SUPABASE_META_DB_PASSWORD"]),
        (
            "task3_studio_future_realtime", "atlas_realtime",
            TEST_SECRETS["SUPABASE_REALTIME_DB_PASSWORD"],
        ),
        (
            "task3_studio_future_openwebui", "atlas_open_webui",
            TEST_SECRETS["OPEN_WEBUI_DB_PASSWORD"],
        ),
        (
            "task3_studio_future_lightrag", "atlas_lightrag",
            TEST_SECRETS["LIGHTRAG_DB_PASSWORD"],
        ),
    )
    for table, owner, owner_password in future_tables:
        disposable_postgres.network_sql(
            f'CREATE TABLE public."{table}"(value text); '
            f'INSERT INTO public."{table}" VALUES (\'{owner}\')',
            user=owner,
            password=owner_password,
        )

    disposable_postgres.sql(
        "CREATE TABLE IF NOT EXISTS public.task3_studio_read_only(value text); "
        "TRUNCATE public.task3_studio_read_only; "
        "INSERT INTO public.task3_studio_read_only VALUES ('visible')",
        password=disposable_postgres.admin_password,
    )

    read = disposable_postgres.network_sql(
        "SELECT value FROM public.task3_studio_read_only",
        user=user,
        password=password,
        check=False,
    )
    assert read.returncode == 0, read.stderr
    assert read.stdout == "visible\n"

    future_reads = [
        disposable_postgres.network_sql(
            f'SELECT value FROM public."{table}"',
            user=user,
            password=password,
            check=False,
        )
        for table, _, _ in future_tables
    ]
    assert [(result.returncode, result.stdout) for result in future_reads] == [
        (0, f"{owner}\n") for _, owner, _ in future_tables
    ]

    denied_statements = (
        "INSERT INTO public.task3_studio_read_only VALUES ('denied')",
        "UPDATE public.task3_studio_read_only SET value='denied'",
        "DELETE FROM public.task3_studio_read_only",
        "CREATE TABLE public.task3_studio_denied(id bigint)",
        "DROP TABLE public.task3_studio_read_only",
        "CREATE ROLE task3_studio_denied_role NOLOGIN",
        "CREATE DATABASE task3_studio_denied_database",
    )
    for statement in denied_statements:
        denied = disposable_postgres.network_sql(
            statement, user=user, password=password, check=False
        )
        assert denied.returncode != 0, f"read-only Studio executed: {statement}"

    attributes = disposable_postgres.sql(
        "SELECT rolsuper || ':' || rolcreatedb || ':' || rolcreaterole || ':' || "
        "rolreplication || ':' || rolbypassrls FROM pg_roles "
        "WHERE rolname='atlas_studio_readonly'",
        password=disposable_postgres.admin_password,
    ).stdout.strip()
    assert attributes == "false:false:false:false:false"


def test_meta_role_supports_sql_editor_administration_without_superuser(
    disposable_postgres: DisposablePostgres,
) -> None:
    role = f"task3_meta_role_{uuid.uuid4().hex[:8]}"
    database = f"task3_meta_db_{uuid.uuid4().hex[:8]}"
    renamed_database = f"{database}_renamed"
    table = f"task3_meta_table_{uuid.uuid4().hex[:8]}"
    password = TEST_SECRETS["SUPABASE_META_DB_PASSWORD"]

    for statement in (
        f'CREATE ROLE "{role}" NOLOGIN',
        f'ALTER ROLE "{role}" SET statement_timeout = 1000',
        f'DROP ROLE "{role}"',
        f'CREATE DATABASE "{database}"',
        f'ALTER DATABASE "{database}" RENAME TO "{renamed_database}"',
        f'DROP DATABASE "{renamed_database}"',
        f'CREATE TABLE public."{table}"(id bigint)',
        f'ALTER TABLE public."{table}" ADD COLUMN value text',
        f'DROP TABLE public."{table}"',
    ):
        result = disposable_postgres.network_sql(
            statement, user="atlas_meta", password=password, check=False
        )
        assert result.returncode == 0, f"{statement}: {result.stderr}"

    attributes = disposable_postgres.sql(
        "SELECT rolsuper || ':' || rolcreatedb || ':' || rolcreaterole || ':' || "
        "rolreplication || ':' || rolbypassrls FROM pg_roles "
        "WHERE rolname='atlas_meta'",
        password=disposable_postgres.admin_password,
    ).stdout.strip()
    assert attributes == "false:true:true:false:false"
