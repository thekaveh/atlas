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

from tests import seed_harness
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
PSQL_IMAGE = "postgres:15.19-alpine"
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


@pytest.mark.parametrize("check", (False, True))
def test_role_drill_expected_psql_failure_is_neither_retried_nor_renamed(
    fake_role_client_docker, check: bool,
) -> None:
    runs = fake_role_client_docker("denied", "ok")
    database = DisposablePostgres("postgres", "network", "password")
    try:
        result = database.network_sql("SELECT 1", check=check)
    except subprocess.CalledProcessError as exc:
        result = exc
    assert type(result) is (
        subprocess.CalledProcessError if check else subprocess.CompletedProcess
    )
    assert result.returncode == 1 and "permission denied" in result.stderr
    assert len(runs.read_text().splitlines()) == 1


def test_role_drill_process_budget_stall_is_named_with_partial_output(
    fake_role_client_docker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = fake_role_client_docker("stall", "ok")
    monkeypatch.setattr(seed_harness, "PSQL_CLIENT_PROCESS_TIMEOUT", 1)
    database = DisposablePostgres("postgres", "network", "password")
    with pytest.raises(seed_harness.RoleDrillClientStalled) as caught:
        database.network_sql("SELECT 1", password="role-secret")
    assert isinstance(caught.value, subprocess.TimeoutExpired)
    message = str(caught.value)
    assert "in the process-budget phase" in message and "killed after 1s" in message
    assert "command: docker run --rm" in message and "-Atqc SELECT 1" in message
    assert "partial stderr: 'partial output\\n'" in message
    assert "PGPASSWORD=<redacted>" in message and "role-secret" not in message
    assert len(runs.read_text().splitlines()) == 1


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
    # Under the image's own config, pg_cron and pg_net workers stay connected
    # to `postgres`, and a database being accessed cannot be a template: close
    # it to new connections, drop the workers, clone from a template1 session.
    admin = {"password": disposable_postgres.admin_password, "database": "template1"}
    disposable_postgres.sql("ALTER DATABASE postgres WITH ALLOW_CONNECTIONS false", **admin)
    try:
        disposable_postgres.sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = 'postgres' AND pid <> pg_backend_pid()",
            **admin,
        )
        disposable_postgres.sql(
            'CREATE DATABASE "atlas primary ""quoted""" TEMPLATE postgres', **admin,
        )
    finally:
        disposable_postgres.sql("ALTER DATABASE postgres WITH ALLOW_CONNECTIONS true", **admin)
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


def test_a_planted_public_overload_does_not_run_as_superuser(
    disposable_postgres: DisposablePostgres,
) -> None:
    """Roles with CREATE on public (Open WebUI, LightRAG) planted overloads of
    built-ins the superuser-run init calls with non-exact argument types
    (hashtextextended, format(text, name), and operators like =); they ran as
    superuser (CVE-2018-1058 pattern; one captured a plaintext password).
    Init now refuses while such objects exist, and slice 14's SECURITY
    DEFINER resolves nothing through public."""
    db = disposable_postgres
    owui = TEST_SECRETS["OPEN_WEBUI_DB_USER"]
    role = dict(user=owui, password=TEST_SECRETS["OPEN_WEBUI_DB_PASSWORD"])
    backend = dict(user=TEST_SECRETS["BACKEND_DB_USER"], password=TEST_SECRETS["BACKEND_DB_PASSWORD"])
    body = ("LANGUAGE plpgsql AS $p$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_planted_su') "
            "THEN EXECUTE 'CREATE ROLE atlas_planted_su SUPERUSER'; END IF; RETURN {ret}; END $p$")
    plants = [
        "CREATE OR REPLACE FUNCTION public.hashtextextended(t text, s integer) RETURNS bigint " + body.format(ret="1"),
        # Behaves like the built-in, so only the guard (not a broken init)
        # can stop it.
        "CREATE OR REPLACE FUNCTION public.format(f text, a name) RETURNS text "
        + body.format(ret="pg_catalog.format(f, a)"),
    ]
    cleanup = [
        "DROP FUNCTION IF EXISTS public.format(text, name)",
        "DROP FUNCTION IF EXISTS public.hashtextextended(text, integer)",
    ]
    planted = "SELECT count(*) FROM pg_roles WHERE rolname = 'atlas_planted_su'"
    try:
        for statement in plants:
            result = db.sql(statement, check=False, **role)
            assert result.returncode == 0, result.stderr
        with pytest.raises(subprocess.CalledProcessError):
            db.run_init()  # refuses, naming the shadowing objects
        assert db.sql(planted).stdout.strip() == "0", "init ran a planted overload as superuser"
        for statement in cleanup:
            db.sql(statement, check=False)
        db.run_init()
        # Planted after init: the definer qualifies its built-ins.
        assert db.sql(plants[0], check=False, **role).returncode == 0
        state = db.sql(
            "SELECT pgvector_target_model || ',' || target_dimension || ',' || pgvector_target_generation "
            "FROM public.memory_embedding_schema_state"
        ).stdout.strip().split(",")
        call = db.sql(
            f"SELECT public.contract_memory_embedding_contract('{state[0]}', {state[1]}, {state[2]})",
            check=False, **backend,
        )
        assert call.returncode == 0, call.stderr
        assert db.sql(planted).stdout.strip() == "0", "the SECURITY DEFINER ran the planted overload"
    finally:
        db.sql("DROP ROLE IF EXISTS atlas_planted_su", check=False)
        for statement in cleanup:
            db.sql(statement, check=False)


def test_backup_queries_never_resolve_through_public(
    disposable_postgres: DisposablePostgres,
) -> None:
    """backup-all.sh / restore-postgres.sh run catalog queries as the
    superuser; a planted public.convert_to(name, name) ran in them (and
    COPY ... TO PROGRAM ran shell commands in supabase-db). Both scripts now
    pin search_path to pg_catalog, pg_temp."""
    import re

    for script in ("backup-all.sh", "restore-postgres.sh"):
        path = REPO / "services/backup/init/scripts" / script
        text = path.read_text(encoding="utf-8")
        pin = re.search(r'^export PGOPTIONS="\$\{PGOPTIONS:\+\$PGOPTIONS \}-c search_path=pg_catalog,pg_temp"$',
                        text, re.M)
        assert pin and pin.start() < text.index("psql "), script
        # And it is what the script exports: run its preamble up to the pin.
        preamble = text[:pin.end()]
        exported = subprocess.run(["sh", "-c", preamble + '\nprintf %s "$PGOPTIONS"'],
                                  capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        assert exported.stdout.endswith("-c search_path=pg_catalog,pg_temp"), (script, exported.stderr)
    db = disposable_postgres
    owui = TEST_SECRETS["OPEN_WEBUI_DB_USER"]
    role = dict(user=owui, password=TEST_SECRETS["OPEN_WEBUI_DB_PASSWORD"])
    plant = (
        "CREATE OR REPLACE FUNCTION public.convert_to(a name, b name) RETURNS bytea LANGUAGE plpgsql AS $f$ "
        "BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_backup_pwned') "
        "THEN EXECUTE 'CREATE ROLE atlas_backup_pwned'; END IF; "
        "RETURN pg_catalog.convert_to(a::text, b); END $f$"
    )
    query = ("SELECT count(*) FROM (SELECT encode(convert_to(n.nspname, 'UTF8'), 'hex') "
             "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace) s")
    try:
        assert db.sql(plant, check=False, **role).returncode == 0
        result = subprocess.run(
            ["docker", "exec", "-e", "PGOPTIONS=-c search_path=pg_catalog,pg_temp", db.container,
             "psql", "-X", "-w", "-h", "127.0.0.1", "-U", "supabase_admin", "-d", "postgres", "-Atqc", query],
            env={**__import__("os").environ, "PGPASSWORD": db.admin_password}, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert db.sql("SELECT count(*) FROM pg_roles WHERE rolname = 'atlas_backup_pwned'").stdout.strip() == "0"
    finally:
        db.sql("DROP ROLE IF EXISTS atlas_backup_pwned", check=False)
        db.sql("DROP FUNCTION IF EXISTS public.convert_to(name, name)", check=False)


def test_init_names_a_planted_operator_and_refuses(
    disposable_postgres: DisposablePostgres,
) -> None:
    """A planted public.=(varchar, varchar) beat pg_catalog's in 12-comfyui."""
    db = disposable_postgres
    role = dict(user=TEST_SECRETS["OPEN_WEBUI_DB_USER"], password=TEST_SECRETS["OPEN_WEBUI_DB_PASSWORD"])
    try:
        assert db.sql(
            "CREATE OR REPLACE FUNCTION public.atlas_eq(a varchar, b varchar) RETURNS boolean "
            "LANGUAGE sql AS 'SELECT a::text = b::text'", check=False, **role).returncode == 0
        assert db.sql("CREATE OPERATOR public.= (LEFTARG = varchar, RIGHTARG = varchar, FUNCTION = public.atlas_eq)",
                      check=False, **role).returncode == 0
        with pytest.raises(subprocess.CalledProcessError) as refused:
            db.run_init()
        assert "operator" in str(refused.value.stderr or refused.value.output or "")
    finally:
        db.sql("DROP OPERATOR IF EXISTS public.= (varchar, varchar)", check=False)
        db.sql("DROP FUNCTION IF EXISTS public.atlas_eq(varchar, varchar)", check=False)
        db.run_init()


def test_every_security_definer_pins_a_search_path_without_public(
    disposable_postgres: DisposablePostgres,
) -> None:
    """PostGIS's ST_EstimatedExtent definers had no search_path and resolved
    names through public, where co-tenants create objects (#1456)."""
    loose = disposable_postgres.sql(
        "SELECT p.oid::regprocedure FROM pg_proc AS p WHERE p.prosecdef AND NOT EXISTS ("
        "SELECT 1 FROM unnest(coalesce(p.proconfig, '{}')) AS c "
        "WHERE c LIKE 'search_path=%' AND c NOT LIKE '%public%')"
    ).stdout.split("\n")
    assert [row for row in loose if row] == []


def test_init_refuses_an_overload_of_a_superuser_owned_extension_routine(
    disposable_postgres: DisposablePostgres,
) -> None:
    """The guard covered only names in pg_catalog; vector and PostGIS
    routines live in public, so a co-tenant overload such as
    public.vector_dims(text) could resolve first in a superuser-run slice
    (#1456)."""
    db = disposable_postgres
    role = dict(user=TEST_SECRETS["OPEN_WEBUI_DB_USER"], password=TEST_SECRETS["OPEN_WEBUI_DB_PASSWORD"])
    drop = "DROP FUNCTION IF EXISTS public.vector_dims(text)"
    try:
        planted = db.sql(
            "CREATE FUNCTION public.vector_dims(v text) RETURNS integer LANGUAGE sql AS 'SELECT 1'",
            check=False, **role,
        )
        assert planted.returncode == 0, planted.stderr
        with pytest.raises(subprocess.CalledProcessError) as refused:
            db.run_init()
        assert "public.vector_dims(v text)" in (refused.value.stderr or "") + (refused.value.stdout or "")
    finally:
        db.sql(drop, check=False)
    db.run_init()


def test_a_planted_trigger_on_an_init_table_makes_init_refuse(
    disposable_postgres: DisposablePostgres,
) -> None:
    """Client roles held TRIGGER on every public table, and init writes
    several of them as superuser; a planted statement trigger ran with
    superuser rights on the next boot (2026-10-08 run, cycle 5)."""
    db = disposable_postgres
    owui = TEST_SECRETS["OPEN_WEBUI_DB_USER"]
    role = dict(user=owui, password=TEST_SECRETS["OPEN_WEBUI_DB_PASSWORD"])
    for client in ("anon", "authenticated", "service_role"):
        granted = db.sql(f"SELECT has_table_privilege('{client}', 'public.users', 'TRIGGER')").stdout.strip()
        assert granted == "f", client
    try:
        assert db.sql(
            "CREATE FUNCTION public.atlas_planted_trigger() RETURNS trigger LANGUAGE plpgsql "
            "AS $f$ BEGIN RETURN NULL; END $f$", check=False, **role,
        ).returncode == 0
        # TRIGGER is revoked from clients now; plant it the way a role that
        # still held the privilege would have.
        db.sql("CREATE TRIGGER atlas_planted AFTER INSERT ON public.users "
               "FOR EACH STATEMENT EXECUTE FUNCTION public.atlas_planted_trigger()")
        with pytest.raises(subprocess.CalledProcessError) as refused:
            db.run_init()
        output = (refused.value.stderr or "") + (refused.value.stdout or "")
        assert "trigger atlas_planted on public.users" in output
    finally:
        db.sql("DROP TRIGGER IF EXISTS atlas_planted ON public.users", check=False)
        db.sql("DROP FUNCTION IF EXISTS public.atlas_planted_trigger()", check=False)
    db.run_init()


def test_an_overload_in_the_literal_dollar_user_schema_never_runs(
    disposable_postgres: DisposablePostgres,
) -> None:
    """supabase_admin's search_path names a schema literally called "\\$user"
    ahead of public; a co-tenant that created it planted format(text, name),
    which 06-permissions then ran as superuser (cycle 5)."""
    db = disposable_postgres
    owui = TEST_SECRETS["OPEN_WEBUI_DB_USER"]
    role = dict(user=owui, password=TEST_SECRETS["OPEN_WEBUI_DB_PASSWORD"])
    schema = '"\\$user"'
    planted = "SELECT count(*) FROM pg_roles WHERE rolname = 'atlas_dollar_user_pwned'"
    try:
        db.sql(f"CREATE SCHEMA {schema} AUTHORIZATION {owui}")
        assert db.sql(
            f"CREATE FUNCTION {schema}.format(f text, a name) RETURNS text LANGUAGE plpgsql AS $p$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_dollar_user_pwned') "
            "THEN EXECUTE 'CREATE ROLE atlas_dollar_user_pwned'; END IF; "
            "RETURN pg_catalog.format(f, a); END $p$", check=False, **role,
        ).returncode == 0
        db.run_init()
        assert db.sql(planted).stdout.strip() == "0", "init ran the planted overload as superuser"
    finally:
        db.sql("DROP ROLE IF EXISTS atlas_dollar_user_pwned", check=False)
        db.sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE", check=False)


def test_a_service_role_cannot_hijack_init_in_its_own_database(
    disposable_postgres: DisposablePostgres,
) -> None:
    """05-scoped-roles.sh ran unqualified format() as superuser inside each
    service database, whose public schema the service role owns; a planted
    overload made the role cluster superuser (2026-10-08 run, cycle 18)."""
    db = disposable_postgres
    user = TEST_SECRETS["LITELLM_DB_USER"]
    role = dict(user=user, password=TEST_SECRETS["LITELLM_DB_PASSWORD"], database=TEST_SECRETS["LITELLM_DB_NAME"])
    plant = (
        "CREATE EXTENSION IF NOT EXISTS pgcrypto; "
        "CREATE OR REPLACE FUNCTION public.format(f text, a regprocedure, b name) RETURNS text "
        "LANGUAGE plpgsql AS $p$ BEGIN "
        f"EXECUTE 'ALTER ROLE {user} SUPERUSER'; "
        "RETURN pg_catalog.format(f, a, b); END $p$"
    )
    try:
        planted = db.sql(plant, check=False, **role)
        assert planted.returncode == 0, planted.stderr
        db.run_init()
        superuser = db.sql(f"SELECT rolsuper FROM pg_roles WHERE rolname = '{user}'").stdout.strip()
        assert superuser == "f", "init ran the service role's overload as superuser"
    finally:
        db.sql(f"ALTER ROLE {user} NOSUPERUSER", check=False)
        db.sql("DROP FUNCTION IF EXISTS public.format(text, regprocedure, name)",
               check=False, database=TEST_SECRETS["LITELLM_DB_NAME"])


def test_a_procedure_in_a_service_database_does_not_stop_init(
    disposable_postgres: DisposablePostgres,
) -> None:
    """The ownership pass ran ALTER FUNCTION for every routine; a procedure
    raises "is not a function", so one stopped init on every boot (2026-10-08
    run, cycle 26). ALTER ROUTINE covers functions, procedures and aggregates."""
    db = disposable_postgres
    database = TEST_SECRETS["LITELLM_DB_NAME"]
    try:
        db.sql("CREATE PROCEDURE public.atlas_legacy_proc() LANGUAGE sql AS 'SELECT 1'", database=database)
        db.run_init()
        owner = db.sql(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE proname = 'atlas_legacy_proc'", database=database
        ).stdout.strip()
        assert owner == TEST_SECRETS["LITELLM_DB_USER"]
    finally:
        db.sql("DROP PROCEDURE IF EXISTS public.atlas_legacy_proc()", check=False, database=database)


@pytest.mark.parametrize(("schema", "owner_key"), [("n8n", "N8N_DB_USER"), ("lightrag", "LIGHTRAG_DB_USER")])
def test_a_procedure_in_a_service_schema_does_not_stop_init(
    disposable_postgres: DisposablePostgres, schema: str, owner_key: str,
) -> None:
    """Only the per-database site was tested; reverting the n8n or lightrag
    schema pass to ALTER FUNCTION stayed green (2026-10-08 run, cycle 44)."""
    db = disposable_postgres
    try:
        db.sql(f"CREATE PROCEDURE {schema}.atlas_legacy_proc() LANGUAGE sql AS 'SELECT 1'")
        db.run_init()
        owner = db.sql(
            "SELECT pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            f"WHERE p.proname = 'atlas_legacy_proc' AND n.nspname = '{schema}'"
        ).stdout.strip()
        assert owner == TEST_SECRETS[owner_key]
    finally:
        db.sql(f"DROP PROCEDURE IF EXISTS {schema}.atlas_legacy_proc()", check=False)


def test_the_default_bucket_insert_never_runs_a_trigger_as_superuser(
    disposable_postgres: DisposablePostgres,
) -> None:
    """storage.buckets is owned by the storage role, so the init guard does
    not cover it; only 04's SET LOCAL ROLE keeps a trigger planted by that
    owner from running as the init superuser. Removing it stayed green
    (2026-10-08 run, cycle 22)."""
    db = disposable_postgres
    owner = db.sql(
        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'storage.buckets'::regclass"
    ).stdout.strip()
    assert owner and db.sql(f"SELECT rolsuper FROM pg_roles WHERE rolname = '{owner}'").stdout.strip() == "f"
    try:
        db.sql("CREATE TABLE public.atlas_bucket_probe (who name, superuser boolean)")
        db.sql("GRANT INSERT ON public.atlas_bucket_probe TO PUBLIC")
        db.sql(
            "CREATE FUNCTION storage.atlas_bucket_probe() RETURNS trigger LANGUAGE plpgsql AS $f$ "
            "BEGIN INSERT INTO public.atlas_bucket_probe SELECT current_user, "
            "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user); RETURN NULL; END $f$"
        )
        db.sql(f"ALTER FUNCTION storage.atlas_bucket_probe() OWNER TO {owner}")
        db.sql("CREATE TRIGGER atlas_bucket_probe AFTER INSERT ON storage.buckets "
               "FOR EACH STATEMENT EXECUTE FUNCTION storage.atlas_bucket_probe()")
        db.run_init()
        rows = db.sql("SELECT who, superuser FROM public.atlas_bucket_probe").stdout.strip()
        assert rows, "the planted trigger did not fire; the test proves nothing"
        assert all(line.split("|")[-1].strip() == "f" for line in rows.splitlines()), rows
    finally:
        db.sql("DROP TRIGGER IF EXISTS atlas_bucket_probe ON storage.buckets", check=False)
        db.sql("DROP FUNCTION IF EXISTS storage.atlas_bucket_probe()", check=False)
        db.sql("DROP TABLE IF EXISTS public.atlas_bucket_probe", check=False)


def test_a_cast_planted_by_the_auth_users_owner_never_runs_as_superuser(
    disposable_postgres: DisposablePostgres,
) -> None:
    """auth.users belongs to GoTrue's login role, which can retype a column
    and add an implicit cast; the superuser-owned sync trigger and backfill
    then ran that cast as superuser (2026-10-08 run, cycle 58)."""
    import contextlib

    db = disposable_postgres
    owner = db.sql(
        "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'auth.users'::regclass"
    ).stdout.strip()
    as_owner = f"SET ROLE {owner}; "
    plant = [
        "CREATE TABLE public.atlas_cast_probe (who name, superuser boolean)",
        "GRANT INSERT ON public.atlas_cast_probe TO PUBLIC",
        "INSERT INTO auth.users (id, email) VALUES (gen_random_uuid(), 'cast-probe@example.com')",
        as_owner + "DROP TRIGGER IF EXISTS on_auth_user_sync ON auth.users",
        as_owner + "CREATE TYPE auth.atlas_evil AS (j text)",
        as_owner + "CREATE FUNCTION auth.atlas_evil_cast(auth.atlas_evil) RETURNS jsonb LANGUAGE plpgsql AS $f$ "
                   "BEGIN BEGIN RESET ROLE; EXCEPTION WHEN others THEN NULL; END; "
                   "INSERT INTO public.atlas_cast_probe SELECT current_user, "
                   "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user); RETURN '{}'::jsonb; END $f$",
        as_owner + "CREATE CAST (auth.atlas_evil AS jsonb) WITH FUNCTION auth.atlas_evil_cast(auth.atlas_evil) AS IMPLICIT",
        as_owner + "ALTER TABLE auth.users ALTER COLUMN raw_user_meta_data TYPE auth.atlas_evil USING NULL",
    ]
    try:
        for statement in plant:
            db.sql(statement)
        with contextlib.suppress(subprocess.CalledProcessError):
            db.run_init()  # refusing is fine too; running the cast as superuser is not
        # A GoTrue-style insert fires the sync trigger with the planted type.
        db.sql(as_owner + "INSERT INTO auth.users (id, email, raw_user_meta_data) VALUES "
               "(gen_random_uuid(), 'cast-probe2@example.com', ROW('{\"name\":\"z\"}')::auth.atlas_evil)")
        rows = db.sql("SELECT who, superuser FROM public.atlas_cast_probe").stdout.strip()
        assert rows, "the planted cast never ran; the test proves nothing"
        assert all(line.split("|")[-1].strip() == "f" for line in rows.splitlines() if line), rows
        assert db.sql(f"SELECT rolsuper FROM pg_roles WHERE rolname = '{owner}'").stdout.strip() == "f"
    finally:
        db.sql(as_owner + "DROP TRIGGER IF EXISTS on_auth_user_sync ON auth.users", check=False)
        db.sql("DELETE FROM auth.users WHERE email LIKE 'cast-probe%@example.com'", check=False)
        db.sql(as_owner + "ALTER TABLE auth.users ALTER COLUMN raw_user_meta_data TYPE jsonb USING NULL", check=False)
        db.sql(as_owner + "DROP CAST IF EXISTS (auth.atlas_evil AS jsonb)", check=False)
        db.sql(as_owner + "DROP FUNCTION IF EXISTS auth.atlas_evil_cast(auth.atlas_evil)", check=False)
        db.sql(as_owner + "DROP TYPE IF EXISTS auth.atlas_evil", check=False)
        db.sql("DROP TABLE IF EXISTS public.atlas_cast_probe", check=False)
    db.run_init()


def test_users_signed_up_without_claims_get_the_authenticated_role(
    disposable_postgres: DisposablePostgres,
) -> None:
    """supabase-auth set no GOTRUE_JWT_AUD / DEFAULT_GROUP_NAME, so users were
    stored with aud and role "", and their tokens were refused by the backend
    and PostgREST; init repairs them (2026-10-08 run, cycle 56)."""
    db = disposable_postgres
    rows = {"no-claims@example.com": ("''", "''"), "null-claims@example.com": ("NULL", "NULL"),
            "admin-claims@example.com": ("''", "'service_role'")}
    try:
        for email, (aud, role) in rows.items():
            db.sql(f"INSERT INTO auth.users (id, email, aud, role) VALUES (gen_random_uuid(), '{email}', {aud}, {role})")
        db.run_init()
        claims = dict(line.split("|") for line in db.sql(
            "SELECT email, coalesce(aud, '-') || '/' || coalesce(role, '-') FROM auth.users "
            "WHERE email LIKE '%-claims@example.com'").stdout.split())
        assert claims["no-claims@example.com"] == "authenticated/authenticated"
        assert claims["null-claims@example.com"] == "authenticated/authenticated"
        # Only rows with BOTH claims empty are repaired; a set role is kept (cycle 63).
        assert claims["admin-claims@example.com"] == "/service_role"
        # The sync role reads only the synced columns, never the password hash.
        denied = db.sql("SET ROLE atlas_auth_sync; SELECT encrypted_password FROM auth.users LIMIT 1", check=False)
        assert denied.returncode != 0 and "permission denied" in denied.stderr
    finally:
        db.sql("DELETE FROM auth.users WHERE email LIKE '%-claims@example.com'", check=False)


def test_supabase_auth_sets_the_claims_and_is_not_host_published():
    """GoTrue answered any browser origin with signup + autoconfirm on its
    published port, and issued tokens with empty claims (cycle 56)."""
    import yaml

    auth = yaml.safe_load((REPO / "services/supabase/compose.yml").read_text())["services"]["supabase-auth"]
    assert auth["environment"]["GOTRUE_JWT_AUD"] == "authenticated"
    assert auth["environment"]["GOTRUE_JWT_DEFAULT_GROUP_NAME"] == "authenticated"
    assert "ports" not in auth


_RESET_ROLE_PROBE = (
    "BEGIN BEGIN RESET ROLE; EXCEPTION WHEN others THEN NULL; END; "
    "INSERT INTO public.atlas_reset_probe SELECT current_user, "
    "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user); RETURN NEW; END"
)


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("target", ["auth.users", "storage.buckets"])
def test_a_planted_trigger_cannot_reset_role_to_the_init_superuser(
    disposable_postgres: DisposablePostgres, target: str, deferred: bool,
) -> None:
    """Init ran the owner's DML under SET LOCAL ROLE; the session user stayed
    superuser, so a planted BEFORE trigger ran RESET ROLE and acted as it
    (2026-10-08 run, cycle 59). Inside a definer function RESET ROLE fails."""
    db = disposable_postgres
    owner = db.sql(f"SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = '{target}'::regclass").stdout.strip()
    schema = target.split(".")[0]
    as_owner = f"SET ROLE {owner}; "
    try:
        db.sql("CREATE TABLE public.atlas_reset_probe (who name, superuser boolean)")
        db.sql("GRANT INSERT ON public.atlas_reset_probe TO PUBLIC")
        db.sql(as_owner + f"CREATE FUNCTION {schema}.atlas_reset_probe() RETURNS trigger LANGUAGE plpgsql AS "
               f"$f$ {_RESET_ROLE_PROBE} $f$")
        # A deferred constraint trigger fired at commit, back in the superuser
        # session, after the definer function had returned (cycle 67).
        kind = ("CONSTRAINT TRIGGER atlas_reset_probe AFTER INSERT OR UPDATE ON {t} DEFERRABLE INITIALLY DEFERRED"
                if deferred else "TRIGGER atlas_reset_probe BEFORE INSERT OR UPDATE ON {t}").format(t=target)
        db.sql(as_owner + f"CREATE {kind} FOR EACH ROW EXECUTE FUNCTION {schema}.atlas_reset_probe()")
        if target == "auth.users":
            db.sql("INSERT INTO auth.users (id, email, aud, role) VALUES (gen_random_uuid(), 'reset-probe@example.com', '', '')")
        if target == "storage.buckets":
            # An AFTER row trigger fires only when the bucket insert adds a row.
            db.sql("DELETE FROM storage.buckets WHERE id = 'default'")
        db.sql("TRUNCATE public.atlas_reset_probe")
        db.run_init()
        rows = db.sql("SELECT who, superuser FROM public.atlas_reset_probe").stdout.strip()
        assert rows, "the planted trigger never fired; the test proves nothing"
        assert all(line.split("|")[-1].strip() == "f" for line in rows.splitlines()), rows
    finally:
        db.sql(as_owner + f"DROP TRIGGER IF EXISTS atlas_reset_probe ON {target}", check=False)
        db.sql(as_owner + f"DROP FUNCTION IF EXISTS {schema}.atlas_reset_probe()", check=False)
        db.sql("DELETE FROM auth.users WHERE email = 'reset-probe@example.com'", check=False)
        db.sql("DROP TABLE IF EXISTS public.atlas_reset_probe", check=False)


def test_a_cast_planted_on_storage_objects_never_runs_as_superuser(
    disposable_postgres: DisposablePostgres,
) -> None:
    """04 re-adds path_tokens with a generated expression over `name`. The
    storage role owns storage.objects, so it could drop the column, retype
    `name` with its own implicit cast to text and let the rewrite run that
    cast as the init superuser (2026-10-08 run, cycles 59 and 63)."""
    import contextlib

    db = disposable_postgres
    owner = db.sql("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'storage.objects'::regclass").stdout.strip()
    as_owner = f"SET ROLE {owner}; "
    plant = [
        "CREATE TABLE public.atlas_storage_probe (who name, superuser boolean)",
        "GRANT INSERT ON public.atlas_storage_probe TO PUBLIC",
        as_owner + "ALTER TABLE storage.objects DROP COLUMN path_tokens",
        as_owner + "CREATE TYPE storage.atlas_evil AS (j text)",
        as_owner + "CREATE FUNCTION storage.atlas_probe_write() RETURNS void LANGUAGE plpgsql AS $f$ "
                   "BEGIN BEGIN RESET ROLE; EXCEPTION WHEN others THEN NULL; END; "
                   "INSERT INTO public.atlas_storage_probe SELECT current_user, "
                   "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user); END $f$",
        as_owner + "CREATE FUNCTION storage.atlas_evil_text(storage.atlas_evil) RETURNS text LANGUAGE plpgsql "
                   "IMMUTABLE AS $f$ BEGIN PERFORM storage.atlas_probe_write(); RETURN ($1).j; END $f$",
        as_owner + "CREATE CAST (storage.atlas_evil AS text) WITH FUNCTION storage.atlas_evil_text(storage.atlas_evil) AS IMPLICIT",
        as_owner + "ALTER TABLE storage.objects ALTER COLUMN name TYPE storage.atlas_evil USING ROW(name)::storage.atlas_evil",
        as_owner + "INSERT INTO storage.objects (bucket_id, name) VALUES ('default', ROW('a/b')::storage.atlas_evil)",
        "TRUNCATE public.atlas_storage_probe",
    ]
    try:
        for statement in plant:
            db.sql(statement)
        with contextlib.suppress(subprocess.CalledProcessError):
            db.run_init()  # refusing is fine; running the cast as superuser is not
        rows = db.sql("SELECT who, superuser FROM public.atlas_storage_probe").stdout.strip()
        assert rows, "the planted cast never ran; the test proves nothing"
        assert all(line.split("|")[-1].strip() == "f" for line in rows.splitlines()), rows
        assert db.sql(f"SELECT rolsuper FROM pg_roles WHERE rolname = '{owner}'").stdout.strip() == "f"
    finally:
        db.sql(as_owner + "DELETE FROM storage.objects WHERE bucket_id = 'default'", check=False)
        db.sql(as_owner + "ALTER TABLE storage.objects DROP COLUMN IF EXISTS path_tokens", check=False)
        db.sql(as_owner + "ALTER TABLE storage.objects ALTER COLUMN name TYPE text USING (name).j", check=False)
        db.sql(as_owner + "DROP CAST IF EXISTS (storage.atlas_evil AS text)", check=False)
        db.sql(as_owner + "DROP FUNCTION IF EXISTS storage.atlas_evil_text(storage.atlas_evil)", check=False)
        db.sql(as_owner + "DROP TYPE IF EXISTS storage.atlas_evil", check=False)
        db.sql(as_owner + "DROP FUNCTION IF EXISTS storage.atlas_probe_write()", check=False)
        db.sql("DROP TABLE IF EXISTS public.atlas_storage_probe", check=False)
    db.run_init()
