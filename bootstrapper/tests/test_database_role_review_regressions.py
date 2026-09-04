"""Regression contracts for the Task 3 independent-review findings.

Docker-backed tests reuse the Task 3 disposable PostgreSQL fixture: unique names,
tmpfs data, local pinned images only, and finite command deadlines.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
import uuid

import pytest

from tests import test_database_role_boundaries as roles
from tests.test_database_role_boundaries import (
    INIT_IMAGE,
    POSTGRES_CONSUMERS,
    TEST_SECRETS,
    DisposablePostgres,
    disposable_postgres,
)


REPO = Path(__file__).resolve().parents[2]
ROLE_SCRIPT = REPO / "services/supabase/db/scripts/05-scoped-roles.sh"
LIGHTRAG_MIGRATION = REPO / "services/lightrag/init/scripts/migrate-pgvector.sql"
SUPAVISOR_CONFIG = REPO / "services/supavisor/pooler/pooler.exs"
SUPAVISOR_IMAGE = "supabase/supavisor:2.9.5"
PSQL_IMAGE = "postgres:15.18-alpine"
READERS = (
    ("atlas_airflow_reader", TEST_SECRETS["AIRFLOW_ATLAS_DB_PASSWORD"]),
    ("atlas_mcp", TEST_SECRETS["MCP_POSTGRES_DB_PASSWORD"]),
    ("atlas_jupyter", TEST_SECRETS["JUPYTER_DB_PASSWORD"]),
    ("atlas_zeppelin", TEST_SECRETS["ZEPPELIN_DB_PASSWORD"]),
)
DEDICATED_DATABASES = (
    "litellm",
    "airflow",
    "langfuse",
    "mlflow",
    "label_studio",
    "iceberg",
    "supavisor",
)


@pytest.mark.parametrize("operation", ("run_init", "network_sql"))
def test_disposable_clients_are_named_and_fully_labeled(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    token = "b" * 32
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(uuid, "uuid4", lambda: type("UUID", (), {"hex": token})())

    def run(*args, **_kwargs):
        calls.append(args)
        if args[1:3] == ("container", "inspect"):
            return subprocess.CompletedProcess(args, 1, "", "not found")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(roles, "_run", run)
    database = DisposablePostgres("postgres", "network", "password")
    database.run_init() if operation == "run_init" else database.network_sql("SELECT 1")

    client_run = next(args for args in calls if args[1] == "run")
    assert client_run[client_run.index("--name") + 1] == (
        f"atlas-db-role-client-{token[:12]}"
    )
    assert f"{roles.DATABASE_ROLE_OWNER_LABEL}={token}" in client_run


@pytest.mark.parametrize("operation", ("run_init", "network_sql"))
def test_disposable_client_timeout_reconciles_late_container(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    token = "c" * 32
    name = f"atlas-db-role-client-{token[:12]}"
    state = {"inspections": 0, "removed": False}
    removals: list[tuple[str, ...]] = []
    ticks = iter((0.0, 2.0, 121.0))
    monkeypatch.setattr(uuid, "uuid4", lambda: type("UUID", (), {"hex": token})())
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        if args[1] == "run":
            raise subprocess.TimeoutExpired(args, 20)
        if args[1:3] == ("container", "inspect"):
            state["inspections"] += 1
            if state["inspections"] >= 3 and not state["removed"]:
                record = {
                    "Name": f"/{name}",
                    "Config": {"Labels": {roles.DATABASE_ROLE_OWNER_LABEL: token}},
                }
                return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[1:3] == ("rm", "-f"):
            removals.append(args[1:])
            state["removed"] = True
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(roles, "_run", run)
    database = DisposablePostgres("postgres", "network", "password")
    with pytest.raises(subprocess.TimeoutExpired):
        database.run_init() if operation == "run_init" else database.network_sql("SELECT 1")
    assert removals == [("rm", "-f", name)]


@pytest.mark.parametrize("operation", ("run_init", "network_sql"))
def test_disposable_client_collision_never_removes_foreign_container(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    token = "d" * 32
    name = f"atlas-db-role-client-{token[:12]}"
    removals: list[tuple[str, ...]] = []
    ticks = iter((0.0, 121.0))
    monkeypatch.setattr(uuid, "uuid4", lambda: type("UUID", (), {"hex": token})())
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    def run(*args, **kwargs):
        if args[1] == "run":
            result = subprocess.CompletedProcess(args, 125, "", "name conflict")
            if kwargs.get("check", True):
                raise subprocess.CalledProcessError(125, args, stderr=result.stderr)
            return result
        if args[1:3] == ("container", "inspect"):
            record = {
                "Name": f"/{name}",
                "Config": {"Labels": {roles.DATABASE_ROLE_OWNER_LABEL: "foreign"}},
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        if args[1:3] == ("rm", "-f"):
            removals.append(args[1:])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(roles, "_run", run)
    database = DisposablePostgres("postgres", "network", "password")
    if operation == "run_init":
        with pytest.raises(subprocess.CalledProcessError):
            database.run_init()
    else:
        assert database.network_sql("SELECT 1", check=False).returncode == 125
    assert removals == []


@pytest.mark.parametrize("operation", ("run_init", "network_sql"))
@pytest.mark.parametrize("launch_error", (OSError("docker lost"), KeyboardInterrupt()))
def test_disposable_client_cleanup_never_replaces_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    launch_error: BaseException,
) -> None:
    ticks = iter((0.0, 121.0))
    monkeypatch.setattr(uuid, "uuid4", lambda: type("UUID", (), {"hex": "e" * 32})())
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        if args[1] == "run":
            raise launch_error
        raise subprocess.TimeoutExpired(args, 10)

    monkeypatch.setattr(roles, "_run", run)
    database = DisposablePostgres("postgres", "network", "password")
    with pytest.raises(type(launch_error)) as caught:
        database.run_init() if operation == "run_init" else database.network_sql("SELECT 1")

    assert caught.value is launch_error
    assert "client cleanup could not be proven" in "\n".join(launch_error.__notes__)


@pytest.mark.parametrize(
    "role", ("storage-review", "realtime-review", "supavisor-review")
)
@pytest.mark.parametrize(
    "launch_error",
    (
        subprocess.TimeoutExpired(("docker", "run"), 30),
        KeyboardInterrupt(),
    ),
)
def test_owned_service_launch_reconciles_late_container(
    monkeypatch: pytest.MonkeyPatch, role: str, launch_error: BaseException,
) -> None:
    token = "f" * 32
    name = f"atlas-{role}-{token[:12]}"
    state = {"inspections": 0, "removed": False}
    removals: list[tuple[str, ...]] = []
    ticks = iter((0.0, 2.0, 31.0))
    monkeypatch.setattr(uuid, "uuid4", lambda: type("UUID", (), {"hex": token})())
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        if args[1] == "run":
            assert f"{roles.DATABASE_ROLE_OWNER_LABEL}={token}" in args
            raise launch_error
        if args[1:3] == ("container", "inspect"):
            state["inspections"] += 1
            if state["inspections"] >= 3 and not state["removed"]:
                record = {
                    "Name": f"/{name}",
                    "Config": {"Labels": {roles.DATABASE_ROLE_OWNER_LABEL: token}},
                }
                return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
            return subprocess.CompletedProcess(args, 1, "", "not found")
        if args[1:3] == ("rm", "-f"):
            state["removed"] = True
            removals.append(args[1:])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(roles, "_run", run)
    with pytest.raises(type(launch_error)):
        with roles.owned_role_container(role, ["image"]):
            pass
    assert removals == [("rm", "-f", name)]


@pytest.mark.parametrize(
    "role", ("storage-review", "realtime-review", "supavisor-review")
)
def test_owned_service_collision_never_removes_foreign_container(
    monkeypatch: pytest.MonkeyPatch, role: str,
) -> None:
    token = "1" * 32
    name = f"atlas-{role}-{token[:12]}"
    removals: list[tuple[str, ...]] = []
    ticks = iter((0.0, 31.0))
    monkeypatch.setattr(uuid, "uuid4", lambda: type("UUID", (), {"hex": token})())
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        if args[1] == "run":
            return subprocess.CompletedProcess(args, 125, "", "name conflict")
        if args[1:3] == ("container", "inspect"):
            record = {
                "Name": f"/{name}",
                "Config": {"Labels": {roles.DATABASE_ROLE_OWNER_LABEL: "foreign"}},
            }
            return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")
        if args[1:3] == ("rm", "-f"):
            removals.append(args[1:])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(roles, "_run", run)
    with pytest.raises(subprocess.CalledProcessError):
        with roles.owned_role_container(role, ["image"]):
            pass
    assert removals == []


def test_custom_quoted_primary_database_receives_scoped_connect_grants(
    disposable_postgres: DisposablePostgres,
) -> None:
    database = 'atlas primary "quoted"'
    disposable_postgres.sql(
        'CREATE DATABASE "atlas primary ""quoted""" TEMPLATE postgres',
        password=disposable_postgres.admin_password,
    )
    disposable_postgres.sql(
        'REVOKE CONNECT ON DATABASE "atlas primary ""quoted""" FROM PUBLIC',
        password=disposable_postgres.admin_password,
    )
    args = [
        "--pull=never", "--network",
        disposable_postgres.network,
        "-e", "PGHOST=supabase-db", "-e", "PGUSER=supabase_admin",
        "-e", f"PGPASSWORD={disposable_postgres.admin_password}",
        "-e", f"PGDATABASE={database}",
    ]
    for name, value in TEST_SECRETS.items():
        args.extend(("-e", f"{name}={value}"))
    args.extend(
        ("-v", f"{ROLE_SCRIPT.parent}:/scripts:ro", INIT_IMAGE,
         "sh", "/scripts/05-scoped-roles.sh")
    )

    provisioned = roles._run_owned_role_client(args, check=False, timeout=120)
    assert provisioned.returncode == 0, provisioned.stderr
    privilege = disposable_postgres.sql(
        "SELECT has_database_privilege('atlas_backend', "
        "'atlas primary \"quoted\"', 'CONNECT')",
        password=disposable_postgres.admin_password,
    )
    assert privilege.stdout == "t\n"
def test_readers_do_not_inherit_cluster_wide_read_privileges(
    disposable_postgres: DisposablePostgres,
) -> None:
    memberships = disposable_postgres.sql(
        "SELECT rolname FROM pg_roles WHERE pg_has_role(rolname, 'pg_read_all_data', 'member') "
        "AND rolname IN ('atlas_airflow_reader','atlas_mcp','atlas_jupyter','atlas_zeppelin') "
        "ORDER BY rolname",
        password=disposable_postgres.admin_password,
    )
    assert memberships.stdout.splitlines() == []


@pytest.mark.parametrize(("reader", "password"), READERS)
def test_each_reader_is_denied_auth_and_every_dedicated_database(
    disposable_postgres: DisposablePostgres,
    reader: str,
    password: str,
) -> None:
    auth = disposable_postgres.network_sql(
        "SELECT count(*) FROM auth.users", user=reader, password=password, check=False
    )
    assert auth.returncode != 0, f"{reader} read auth.users"
    for database in DEDICATED_DATABASES:
        connection = disposable_postgres.network_sql(
            "SELECT current_database()",
            user=reader,
            password=password,
            database=database,
            check=False,
        )
        assert connection.returncode != 0, f"{reader} connected to {database}"


@pytest.mark.parametrize(("reader", "password"), READERS)
def test_each_reader_receives_only_owner_specific_future_relation_reads(
    disposable_postgres: DisposablePostgres,
    reader: str,
    password: str,
) -> None:
    disposable_postgres.sql(
        "CREATE TABLE IF NOT EXISTS public.task3_future_public(value text); "
        "TRUNCATE public.task3_future_public; "
        "INSERT INTO public.task3_future_public VALUES ('public')",
        password=disposable_postgres.admin_password,
    )
    disposable_postgres.sql(
        "CREATE TABLE IF NOT EXISTS n8n.task3_future_n8n(value text); "
        "TRUNCATE n8n.task3_future_n8n; "
        "INSERT INTO n8n.task3_future_n8n VALUES ('n8n')",
        user="atlas_n8n",
        password=TEST_SECRETS["N8N_DB_PASSWORD"],
    )
    disposable_postgres.sql(
        "CREATE TABLE IF NOT EXISTS storage.task3_future_storage(value text); "
        "TRUNCATE storage.task3_future_storage; "
        "INSERT INTO storage.task3_future_storage VALUES ('storage')",
        user="supabase_storage_admin",
        password=TEST_SECRETS["SUPABASE_STORAGE_DB_PASSWORD"],
    )
    result = disposable_postgres.network_sql(
        "SELECT value FROM public.task3_future_public UNION ALL "
        "SELECT value FROM n8n.task3_future_n8n UNION ALL "
        "SELECT value FROM storage.task3_future_storage ORDER BY value",
        user=reader,
        password=password,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["n8n", "public", "storage"]


def test_backend_role_can_crud_every_inventoried_backend_table(
    disposable_postgres: DisposablePostgres,
) -> None:
    sql = """
BEGIN;
INSERT INTO public.research_sessions(id, query)
  VALUES ('10000000-0000-0000-0000-000000000001', 'task3 review');
INSERT INTO public.research_results(id, session_id, title, summary, content)
  VALUES ('10000000-0000-0000-0000-000000000002',
          '10000000-0000-0000-0000-000000000001', 'title', 'summary', 'content');
INSERT INTO public.research_sources(id, session_id, result_id, url)
  VALUES ('10000000-0000-0000-0000-000000000003',
          '10000000-0000-0000-0000-000000000001',
          '10000000-0000-0000-0000-000000000002', 'https://example.invalid');
INSERT INTO public.research_logs(id, session_id, step_number, step_type, message)
  VALUES ('10000000-0000-0000-0000-000000000004',
          '10000000-0000-0000-0000-000000000001', 1, 'review', 'created');
INSERT INTO public.memory_facts(id, content)
  VALUES ('10000000-0000-0000-0000-000000000005', 'fact');
INSERT INTO public.memory_sessions(id)
  VALUES ('10000000-0000-0000-0000-000000000006');
INSERT INTO public.memory_consolidation_log(id, action, source_fact_ids)
  VALUES ('10000000-0000-0000-0000-000000000007', 'updated',
          ARRAY['10000000-0000-0000-0000-000000000005'::uuid]);
INSERT INTO public.media_spend_ledger(
  id, operation_id, provider, model, modality, status
) VALUES (
  '10000000-0000-0000-0000-000000000008', 'task3-review',
  'test', 'test', 'image', 'reserved'
);
SELECT count(*) FROM public.research_sessions
  WHERE id = '10000000-0000-0000-0000-000000000001';
UPDATE public.research_logs SET message = 'updated'
  WHERE id = '10000000-0000-0000-0000-000000000004';
DELETE FROM public.research_sources
  WHERE id = '10000000-0000-0000-0000-000000000003';
ROLLBACK;
"""
    result = disposable_postgres.sql(
        sql,
        user="atlas_backend",
        password=TEST_SECRETS["BACKEND_DB_PASSWORD"],
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "1" in result.stdout.splitlines()


def test_lightrag_inventory_includes_init_and_both_relation_namespaces() -> None:
    contract = POSTGRES_CONSUMERS["lightrag"]
    assert contract.services == ("lightrag", "lightrag-init")
    assert contract.schemas == ("lightrag", "public")
    assert contract.tables == ("lightrag.*", "public.LIGHTRAG_*")


def test_lightrag_role_runs_the_real_init_sql_and_owns_its_schema(
    disposable_postgres: DisposablePostgres,
) -> None:
    result = disposable_postgres.sql(
        LIGHTRAG_MIGRATION.read_text(encoding="utf-8"),
        user="atlas_lightrag",
        password=TEST_SECRETS["LIGHTRAG_DB_PASSWORD"],
        check=False,
    )
    assert result.returncode == 0, result.stderr
    owners = disposable_postgres.sql(
        "SELECT 'schema=' || pg_get_userbyid(nspowner) FROM pg_namespace "
        "WHERE nspname='lightrag' UNION ALL "
        "SELECT 'table=' || tableowner FROM pg_tables "
        "WHERE schemaname='lightrag' AND tablename='vectors_meta' ORDER BY 1",
        password=disposable_postgres.admin_password,
    )
    assert owners.stdout.splitlines() == [
        "schema=atlas_lightrag",
        "table=atlas_lightrag",
    ]


def test_supavisor_auth_query_uses_the_scoped_security_definer_function() -> None:
    source = SUPAVISOR_CONFIG.read_text(encoding="utf-8")
    assert '"auth_query" => "SELECT * FROM pgbouncer.get_auth($1);"' in source
    assert "pg_authid" not in source


def _supavisor_container(network: str):
    args = [
            "--network", network,
            # Supavisor's entrypoint raises RLIMIT_NOFILE to 100000 and aborts
            # when it cannot. CI runners cap the inherited hard limit below
            # that, so grant it up front instead of letting limits.sh fail.
            "--ulimit", "nofile=100000:100000",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "-v", f"{SUPAVISOR_CONFIG}:/etc/pooler/pooler.exs:ro",
            "-e", "PORT=4000",
            "-e", "PROXY_PORT_TRANSACTION=6543",
            "-e", (
                "DATABASE_URL=ecto://atlas_supavisor:"
                f"{TEST_SECRETS['SUPAVISOR_DB_ADMIN_PASSWORD']}"
                "@supabase-db:5432/supavisor"
            ),
            "-e", "CLUSTER_POSTGRES=true",
            "-e", f"SECRET_KEY_BASE={'s' * 64}",
            "-e", f"VAULT_ENC_KEY={'v' * 32}",
            "-e", f"API_JWT_SECRET={'a' * 64}",
            "-e", f"METRICS_JWT_SECRET={'m' * 64}",
            "-e", "REGION=local",
            "-e", "ERL_AFLAGS=-proto_dist inet_tcp",
            "-e", "POSTGRES_HOST=supabase-db",
            "-e", "POSTGRES_PORT=5432",
            "-e", "POSTGRES_DB=postgres",
            "-e", "POSTGRES_USER=atlas_supavisor",
            "-e", (
                "POSTGRES_PASSWORD="
                f"{TEST_SECRETS['SUPAVISOR_DB_ADMIN_PASSWORD']}"
            ),
            "-e", "POOLER_TENANT_ID=atlas",
            "-e", "POOLER_DEFAULT_POOL_SIZE=5",
            "-e", "POOLER_MAX_CLIENT_CONN=20",
            "-e", "POOLER_POOL_MODE=transaction",
            "-e", "DB_POOL_SIZE=5",
            SUPAVISOR_IMAGE,
            "/bin/sh", "-c",
            '/app/bin/migrate && /app/bin/supavisor eval "$(cat /etc/pooler/pooler.exs)" && /app/bin/server',
        ]
    return roles.owned_role_container("supavisor-review", args, timeout=30)


def _wait_for_supavisor(container: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        health = subprocess.run(
            [
                "docker", "exec", container, "curl", "-sSfL", "--head",
                "-o", "/dev/null", "http://127.0.0.1:4000/api/health",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        if health.returncode == 0:
            return
        state = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        if state.returncode == 0 and state.stdout.strip() == "false":
            break
        time.sleep(0.25)
    logs = subprocess.run(
        ["docker", "logs", "--tail", "120", container],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    pytest.fail(
        "Supavisor readiness deadline exceeded:\n"
        + (logs.stdout + logs.stderr)[-6000:]
    )


def _pooled_login(
    *, network: str, container: str, role: str, password: str
) -> subprocess.CompletedProcess[str]:
    login_deadline = time.monotonic() + 30
    while True:
        login = roles._run_owned_role_client(
            [
                "--pull=never",
                "--network", network,
                "-e", f"PGPASSWORD={password}",
                PSQL_IMAGE,
                "psql", "-X", "-w", "-h", container, "-p", "6543",
                "-U", f"{role}.atlas", "-d", "postgres", "-Atqc",
                "SELECT current_user",
            ], check=False, timeout=20,
        )
        if login.returncode == 0 or time.monotonic() >= login_deadline:
            return login
        time.sleep(0.25)


def test_supavisor_pooled_logins_resolve_backend_and_n8n_credentials(
    disposable_postgres: DisposablePostgres,
) -> None:
    with _supavisor_container(disposable_postgres.network) as container:
        _wait_for_supavisor(container)
        credentials = (
            ("atlas_backend", TEST_SECRETS["BACKEND_DB_PASSWORD"]),
            ("atlas_n8n", TEST_SECRETS["N8N_DB_PASSWORD"]),
        )
        for role, password in credentials:
            login = _pooled_login(
                network=disposable_postgres.network,
                container=container,
                role=role,
                password=password,
            )
            assert login.returncode == 0, login.stderr
            assert login.stdout == f"{role}\n"
