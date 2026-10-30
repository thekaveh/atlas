"""Scoped-role provisioning regressions: validation, connection safety and ownership."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit

import pytest

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from tests.test_database_role_boundaries import TEST_SECRETS, _add_exception_note
from utils.key_generator import KeyGenerator


REPO = Path(__file__).resolve().parents[2]
ROLE_SCRIPT = REPO / "services/supabase/db/scripts/05-scoped-roles.sh"


def _runtime_module():
    runtime = REPO / "services/mcp-servers/runtime/atlas_mcp_server.py"
    spec = importlib.util.spec_from_file_location("atlas_mcp_server_review", runtime)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "overrides",
    (
        {"BACKEND_DB_USER": "atlas_n8n"},
        {"BACKEND_DB_USER": "supabase_admin"},
        {"BACKEND_DB_USER": "postgres"},
        {"BACKEND_DB_USER": "service_role"},
        {"BACKEND_DB_USER": "dashboard_user"},
        {"BACKEND_DB_USER": "pg_monitor"},
        {"PGUSER": "authenticator", "SUPABASE_API_DB_USER": "authenticator"},
        {
            "PGUSER": "supabase_auth_admin",
            "SUPABASE_AUTH_DB_USER": "supabase_auth_admin",
        },
        {
            "PGUSER": "supabase_storage_admin",
            "SUPABASE_STORAGE_DB_USER": "supabase_storage_admin",
        },
        {"LITELLM_DB_NAME": "postgres"},
        {"LITELLM_DB_NAME": "template0"},
        {"LITELLM_DB_NAME": "template1"},
        {"LITELLM_DB_NAME": "airflow"},
        {"LITELLM_DB_NAME": "tenant database", "LANGFUSE_DB_NAME": "tenant database"},
    ),
    ids=(
        "duplicate-role", "owner-role", "postgres-role", "service-role",
        "dashboard-role", "predefined-role", "pguser-authenticator",
        "pguser-auth-admin", "pguser-storage-admin", "primary-db",
        "template0-db", "template1-db", "duplicate-db",
        "duplicate-whitespace-db",
    ),
)
def test_role_provisioner_rejects_collisions_before_any_database_call(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "psql.log"
    fake_psql = bin_dir / "psql"
    fake_psql.write_text(
        '#!/bin/sh\necho "called" >> "$PSQL_LOG"\nexit 0\n', encoding="utf-8"
    )
    fake_psql.chmod(0o755)
    env = {
        **os.environ,
        **TEST_SECRETS,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PSQL_LOG": str(log),
        "PGHOST": "unused",
        "PGUSER": "supabase_admin",
        "PGPASSWORD": "admin-secret",
        "PGDATABASE": "postgres",
        **overrides,
    }
    result = subprocess.run(
        ["/bin/sh", str(ROLE_SCRIPT)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    # The collision check itself must be what refused: an early exit on a
    # missing variable would also be non-zero with no psql call.
    assert "invalid database role configuration" in result.stderr, result.stderr
    assert not log.exists(), "invalid configuration reached psql and could mutate state"


def test_key_generation_preserves_raw_credentials_and_derives_uri_components(
    tmp_path: Path,
) -> None:
    raw_user = "atlas:user/@%?#"
    raw_password = "pw@:/%?# value"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f'BACKEND_DB_USER="{raw_user}"\nBACKEND_DB_PASSWORD="{raw_password}"\n',
        encoding="utf-8",
    )

    KeyGenerator(str(tmp_path)).generate_missing_keys()
    values = ConfigParser(str(tmp_path)).parse_env_file()

    assert values["BACKEND_DB_USER"] == raw_user
    assert values["BACKEND_DB_PASSWORD"] == raw_password
    assert values.get("BACKEND_DB_USER_URI") == quote(raw_user, safe="")
    assert values.get("BACKEND_DB_PASSWORD_URI") == quote(raw_password, safe="")


def test_key_generation_preserves_raw_database_names_and_derives_path_components(
    tmp_path: Path,
) -> None:
    raw_database = "atlas /?#% database"
    database_vars = (
        "SUPABASE_DB_NAME",
        "LITELLM_DB_NAME",
        "LANGFUSE_DB_NAME",
        "TRUEFORGE_DB_NAME",
        "MLFLOW_DB_NAME",
        "LABEL_STUDIO_DB_NAME",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(f'{name}="{raw_database}"' for name in database_vars) + "\n",
        encoding="utf-8",
    )

    KeyGenerator(str(tmp_path)).generate_missing_keys()
    values = ConfigParser(str(tmp_path)).parse_env_file()

    for name in database_vars:
        assert values[name] == raw_database
        assert values[f"{name}_URI"] == quote(raw_database, safe="")


def test_scoped_uri_userinfo_never_interpolates_raw_credentials() -> None:
    offenders: list[str] = []
    paths = [
        *sorted((REPO / "services").glob("*/compose.yml")),
        *sorted((REPO / "services").glob("*/service.yml")),
        REPO / "bootstrapper/services/service_config.py",
    ]
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "://" not in line:
                continue
            if "_DB_PASSWORD" in line and "_DB_PASSWORD_URI" not in line:
                offenders.append(f"{path.relative_to(REPO)}:{number}")
            if "_DB_USER" in line and "_DB_USER_URI" not in line:
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert offenders == []


def test_scoped_uri_paths_never_interpolate_raw_database_names() -> None:
    raw_path_markers = tuple(
        f"/${{{name}}}"
        for name in (
            "SUPABASE_DB_NAME",
            "LITELLM_DB_NAME",
            "LANGFUSE_DB_NAME",
            "TRUEFORGE_DB_NAME",
            "MLFLOW_DB_NAME",
            "LABEL_STUDIO_DB_NAME",
        )
    )
    offenders: list[str] = []
    paths = [
        *sorted((REPO / "services").glob("*/compose.yml")),
        *sorted((REPO / "services").glob("*/service.yml")),
        REPO / "bootstrapper/services/service_config.py",
    ]
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(marker in line for marker in raw_path_markers):
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert offenders == []


def test_hostile_database_names_round_trip_through_rendered_dsn_paths(
    tmp_path: Path,
) -> None:
    raw_database = "atlas /?#% database"
    source = (REPO / ".env.example").read_text(encoding="utf-8")
    for name in (
        "SUPABASE_DB_NAME",
        "LITELLM_DB_NAME",
        "LANGFUSE_DB_NAME",
        "TRUEFORGE_DB_NAME",
        "MLFLOW_DB_NAME",
        "LABEL_STUDIO_DB_NAME",
    ):
        source = source.replace(f"{name}={name.removesuffix('_DB_NAME').lower()}", f'{name}="{raw_database}"')
    # SUPABASE_DB_NAME's default does not follow the service-name convention.
    source = source.replace("SUPABASE_DB_NAME=postgres", f'SUPABASE_DB_NAME="{raw_database}"')
    env_file = tmp_path / ".env"
    env_file.write_text(source, encoding="utf-8")
    KeyGenerator(str(tmp_path)).generate_missing_keys()

    rendered = subprocess.run(
        [
            "docker", "compose", "--env-file", str(env_file), "-p",
            "atlas-task3-hostile-db", "-f", str(REPO / "docker-compose.yml"),
            "config", "--format", "json",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    urls = (
        services["backend"]["environment"]["DATABASE_URL"],
        # Studio has no DATABASE_URL (unread by the image); its POSTGRES_DB
        # tuple is checked below.
        services["litellm"]["environment"]["DATABASE_URL"],
        services["langfuse-web"]["environment"]["DATABASE_URL"],
        services["mlflow"]["environment"]["_MLFLOW_SERVER_FILE_STORE"],
    )
    assert all(unquote(urlsplit(url).path.removeprefix("/")) == raw_database for url in urls)
    studio = services["supabase-studio"]["environment"]
    assert studio["POSTGRES_DB"] == quote(raw_database, safe="")
    assert services["zeppelin"]["environment"]["ZEPPELIN_JDBC_POSTGRES_URL"].endswith(
        "/" + quote(raw_database, safe="")
    )


@pytest.mark.parametrize("tenant", ("bad.tenant", "bad/tenant", "bad tenant", "bad%tenant"))
def test_supavisor_tenant_rejects_userinfo_delimiters_before_render(
    tmp_path: Path, tenant: str
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f'SUPAVISOR_TENANT_ID="{tenant}"\n', encoding="utf-8")
    config = ServiceConfig(ConfigParser(str(tmp_path)))
    config.service_sources = {"SUPAVISOR_SOURCE": "container"}

    with pytest.raises(ValueError, match="SUPAVISOR_TENANT_ID"):
        config._generate_supavisor_config()


@pytest.mark.parametrize(
    "tenant",
    (" atlas", "atlas ", "\tatlas", "atlas\v", "\u00a0atlas", "atlas\u2003"),
    ids=(
        "leading-space", "trailing-space", "leading-tab", "trailing-vertical-tab",
        "leading-nbsp", "trailing-em-space",
    ),
)
def test_supavisor_tenant_rejects_surrounding_whitespace(
    tmp_path: Path, tenant: str
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f'SUPAVISOR_TENANT_ID="{tenant}"\n', encoding="utf-8")
    config = ServiceConfig(ConfigParser(str(tmp_path)))
    config.service_sources = {"SUPAVISOR_SOURCE": "container"}

    with pytest.raises(ValueError, match="SUPAVISOR_TENANT_ID"):
        config._generate_supavisor_config()


def test_full_service_render_rejects_supavisor_tenant_surrounding_whitespace(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'SUPAVISOR_SOURCE=container\nSUPAVISOR_TENANT_ID=" atlas"\n',
        encoding="utf-8",
    )
    parser = ConfigParser(str(REPO))
    parser.env_file_path = env_file

    with pytest.raises(ValueError, match="SUPAVISOR_TENANT_ID"):
        ServiceConfig(parser).generate_service_environment()


def test_supavisor_docs_use_encoded_url_components_and_tenant_grammar() -> None:
    readme = (REPO / "services/supavisor/README.md").read_text(encoding="utf-8")
    assert (
        "postgresql://${BACKEND_DB_USER_URI}.${SUPAVISOR_TENANT_ID}:"
        "...@supavisor:6543/${SUPABASE_DB_NAME_URI}"
    ) in readme
    assert "[A-Za-z0-9_-]+" in readme
    assert "no surrounding whitespace" in readme


def test_mcp_uses_discrete_connection_parameters_for_arbitrary_userinfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_module()
    values = {
        "MCP_POSTGRES_DB_HOST": "supabase-db",
        "MCP_POSTGRES_DB_PORT": "5432",
        "MCP_POSTGRES_DB_NAME": "postgres",
        "MCP_POSTGRES_DB_USER": "atlas:user/@%?#",
        "MCP_POSTGRES_DB_PASSWORD": "pw@:/%?# value",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    assert runtime._postgres_connection_kwargs() == {
        "host": "supabase-db",
        "port": "5432",
        "dbname": "postgres",
        "user": "atlas:user/@%?#",
        "password": "pw@:/%?# value",
    }



@pytest.mark.parametrize("variable", sorted(
    key for key in TEST_SECRETS if key.endswith(("_USER", "_DB_NAME"))
) + ["PGUSER", "PGDATABASE"])
@pytest.mark.parametrize("identifier", ["x" * 64, "é" * 32, "界" * 22])
def test_role_provisioner_rejects_overlength_identifier_before_any_database_call(
    tmp_path: Path, variable: str, identifier: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "psql.log"
    fake_psql = bin_dir / "psql"
    fake_psql.write_text('#!/bin/sh\nprintf called > "$PSQL_LOG"\nexit 74\n')
    fake_psql.chmod(0o755)
    environment = {
        **os.environ, **TEST_SECRETS,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PSQL_LOG": str(log), "PGHOST": "unused.invalid",
        "PGUSER": "supabase_admin", "PGPASSWORD": "synthetic-owned-admin",
        "PGDATABASE": "postgres", variable: identifier,
    }
    result = subprocess.run(
        ["/bin/sh", str(ROLE_SCRIPT)], env=environment,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 1
    assert "invalid database role configuration" in result.stderr
    assert variable in result.stderr and "63 bytes" in result.stderr
    assert identifier not in result.stderr
    assert not log.exists(), "invalid identifier reached privileged psql"


@pytest.mark.parametrize("variable", ["BACKEND_DB_USER", "PGUSER", "LITELLM_DB_NAME", "PGDATABASE"])
@pytest.mark.parametrize("identifier", [
    "x" * 63, "é" * 31 + "x", "界" * 21,
    "atlas user:/@%?#", "atlas\nowned", "atlas \"quoted\" user",
])
def test_role_provisioner_preserves_valid_identifier_spellings_at_database_boundary(
    tmp_path: Path, variable: str, identifier: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "psql.log"
    fake_psql = bin_dir / "psql"
    fake_psql.write_text('#!/bin/sh\ncat >/dev/null\nprintf called > "$PSQL_LOG"\nexit 74\n')
    fake_psql.chmod(0o755)
    environment = {
        **os.environ, **TEST_SECRETS,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PSQL_LOG": str(log), "PGHOST": "unused.invalid",
        "PGUSER": "supabase_admin", "PGPASSWORD": "synthetic-owned-admin",
        "PGDATABASE": "postgres", variable: identifier,
    }
    result = subprocess.run(
        ["/bin/sh", str(ROLE_SCRIPT)], env=environment,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 74, result.stderr
    assert log.read_text() == "called"
    assert "invalid database role configuration" not in result.stderr

@pytest.fixture
def native_ownership_postgres():
    """Opt-in isolated SQL drill; no Docker, Atlas volumes or TCP listener.

    Set ATLAS_TEST_NATIVE_POSTGRES_BIN to a local PostgreSQL bin directory.
    These catalog/ownership tests do not qualify the pinned Supabase image.
    """
    import tempfile

    configured = os.environ.get("ATLAS_TEST_NATIVE_POSTGRES_BIN")
    if not configured:
        pytest.skip("set ATLAS_TEST_NATIVE_POSTGRES_BIN for isolated ownership SQL")
    binaries = Path(configured)
    for name in ("initdb", "pg_ctl", "psql"):
        if not (binaries / name).is_file():
            pytest.fail(f"native PostgreSQL executable unavailable: {binaries / name}")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("PG")}
    with tempfile.TemporaryDirectory(prefix="atlas-role-", dir="/tmp") as temporary:
        root = Path(temporary)
        data = root / "data"
        socket = root / "socket"
        socket.mkdir()
        subprocess.run(
            [str(binaries / "initdb"), "-D", str(data), "-U", "atlas_test_admin",
             "--auth-local=trust", "--auth-host=reject", "--no-locale", "--no-sync"],
            check=True, capture_output=True, text=True, timeout=30, env=environment,
        )
        try:
            subprocess.run(
                [str(binaries / "pg_ctl"), "-D", str(data), "-l", str(root / "server.log"),
                 "-o", f"-k {socket} -h ''", "-w", "start"],
                check=True, capture_output=True, text=True, timeout=30, env=environment,
            )
            def sql(statement: str):
                return subprocess.run(
                    [str(binaries / "psql"), "-X", "-At", "-v", "ON_ERROR_STOP=1",
                     "-h", str(socket), "-U", "atlas_test_admin", "-d", "postgres"],
                    input=statement, capture_output=True, text=True, timeout=15,
                    env=environment,
                )
            yield sql
        finally:
            _stop_native_ownership_postgres(binaries, data, environment, sys.exc_info()[1])


def _stop_native_ownership_postgres(binaries, data, environment, failure) -> None:
    if not (data / "postmaster.pid").exists():
        return
    try:
        subprocess.run(
            [str(binaries / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
            check=True, capture_output=True, text=True, timeout=30, env=environment,
        )
    except (OSError, subprocess.SubprocessError) as cleanup_error:
        if failure is None:
            raise
        _add_exception_note(failure, f"native PostgreSQL cleanup failed: {cleanup_error}")


def _ownership_loop(marker: str) -> str:
    """Extract executable relation-transfer SQL, including its actual predicate."""
    script = ROLE_SCRIPT.read_text()
    start = script.index(marker)
    start = script.rfind("FOR item IN", 0, start)
    assert start >= 0
    end = script.index("END LOOP;", start) + len("END LOOP;")
    return script[start:end]


@pytest.mark.parametrize("sequences_first", (True, False), ids=("sequences-first", "tables-first"))
@pytest.mark.parametrize(
    "scope",
    (
        ("FROM pg_class c\n    JOIN pg_namespace n ON n.oid = c.relnamespace", "public", "legacy_service"),
        ("WHERE n.nspname=schema_name", "auth", "legacy_auth"),
        ("WHERE n.nspname=schema_name", "storage", "legacy_storage"),
        ("WHERE n.nspname=schema_name", "realtime", "legacy_realtime"),
        ("c.relname = ANY (ARRAY['tenants'", "public", "tenants_legacy"),
        ("WHERE n.nspname = 'n8n' AND c.relkind", "n8n", "legacy_n8n"),
        ("WHERE n.nspname = 'lightrag'\n      AND c.relkind", "lightrag", "legacy_lightrag"),
        ("WHERE n.nspname = 'public' AND lower(c.relname) LIKE 'lightrag%'", "public", "LIGHTRAG_legacy"),
    ),
    ids=("dedicated", "auth", "storage", "realtime", "realtime-public", "n8n", "lightrag", "lightrag-public"),
)
def test_native_legacy_ownership_transfers_attached_and_free_sequences(
    native_ownership_postgres, scope: tuple[str, str, str], sequences_first: bool,
) -> None:
    """Attached sequences follow tables; free sequences and partitions still move."""
    marker, schema, table = scope
    sql = native_ownership_postgres
    loop = _ownership_loop(marker)
    # Force both SQL cursor orders instead of trusting heap layout or prior GRANTs.
    direction = "DESC" if sequences_first else "ASC"
    loop = loop.replace("\n  LOOP", f"\n    ORDER BY (c.relkind = 'S') {direction}, c.oid\n  LOOP", 1)
    if "ORDER BY" not in loop:
        loop = loop.replace("\n    LOOP", f"\n      ORDER BY (c.relkind = 'S') {direction}, c.oid\n    LOOP", 1)
    assert "ORDER BY" in loop
    _seed_legacy_ownership_relations(sql, schema, table)
    statement = (
        "SET search_path = pg_catalog, pg_temp; DO $body$ "
        f"DECLARE target name := 'scoped_owner'; schema_name text := '{schema}'; "
        "item record; kind text; BEGIN " + loop + " END $body$;"
    )
    for _ in range(2):
        transferred = sql(statement)
        assert transferred.returncode == 0, transferred.stderr
        _assert_legacy_relation_owners(sql, schema, table)
    # Dedicated databases own all non-system schemas; other loops remain selective.
    unrelated = sql("SELECT pg_catalog.pg_get_userbyid(relowner) FROM pg_catalog.pg_class "
                    "WHERE oid='unrelated.keep'::regclass")
    expected = "scoped_owner" if table == "legacy_service" else "legacy_owner"
    assert unrelated.stdout.strip() == expected


def _assert_legacy_relation_owners(sql, schema: str, table: str) -> None:
    owners = sql(
        "SELECT c.relname || '=' || pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE n.nspname='{schema}' AND c.relname LIKE '{table}%' "
        "AND c.relkind IN ('r','p','S') ORDER BY c.relname"
    )
    assert owners.returncode == 0, owners.stderr
    assert len(owners.stdout.splitlines()) == 6, owners.stdout
    assert all(row.endswith("=scoped_owner") for row in owners.stdout.splitlines()), owners.stdout


def _seed_legacy_ownership_relations(sql, schema: str, table: str) -> None:
    qualified = f'"{schema}"."{table}"'
    free = f'"{schema}"."{table}_free"'
    partitioned = f'"{schema}"."{table}_partitioned"'
    child = f'"{schema}"."{table}_partitioned_child"'
    setup = sql(
        "CREATE ROLE legacy_owner; CREATE ROLE scoped_owner; "
        f'CREATE SCHEMA IF NOT EXISTS "{schema}"; '
        f"CREATE TABLE {qualified} (id bigserial, identity_id bigint GENERATED ALWAYS AS IDENTITY); "
        f"ALTER TABLE {qualified} OWNER TO legacy_owner; "
        f"CREATE SEQUENCE {free}; ALTER SEQUENCE {free} OWNER TO legacy_owner; "
        f"CREATE TABLE {partitioned} (id int) PARTITION BY RANGE (id); "
        f"CREATE TABLE {child} PARTITION OF {partitioned} FOR VALUES FROM (0) TO (10); "
        f"ALTER TABLE {partitioned} OWNER TO legacy_owner; "
        f"ALTER TABLE {child} OWNER TO legacy_owner; "
        # Existing ACLs must not mask the dependency bug by changing heap order.
        f'GRANT ALL ON ALL TABLES IN SCHEMA "{schema}" TO scoped_owner; '
        f'GRANT ALL ON ALL SEQUENCES IN SCHEMA "{schema}" TO scoped_owner; '
        "CREATE SCHEMA unrelated; CREATE SEQUENCE unrelated.keep; "
        "ALTER SEQUENCE unrelated.keep OWNER TO legacy_owner;"
    )
    assert setup.returncode == 0, setup.stderr


def _shared_ownership_block() -> str:
    """Run the whole production pass, including public Realtime selection."""
    script = ROLE_SCRIPT.read_text()
    start = script.index("DO $body$", script.index("SELECT set_config('atlas.storage_role'"))
    end = script.index("$body$;", start) + len("$body$;")
    return script[start:end]


def _seed_legacy_auth_objects(sql) -> None:
    setup = sql('''
        CREATE ROLE "Auth ""Owner" LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
        CREATE ROLE storage_owner; CREATE ROLE realtime_owner;
        CREATE ROLE atlas_auth_sync NOLOGIN;
        CREATE SCHEMA auth; CREATE SCHEMA storage; CREATE SCHEMA realtime;
        CREATE SCHEMA unrelated;
        CREATE TABLE auth.identities (id bigserial, identity_id bigint GENERATED ALWAYS AS IDENTITY,
                                      payload text);
        INSERT INTO auth.identities(payload) VALUES ('preserved');
        CREATE SEQUENCE auth.free_sequence;
        CREATE TYPE auth.factor_type AS ENUM ('totp');
        CREATE DOMAIN auth.factor_domain AS text;
        CREATE TYPE auth.factor_record AS (value text);
        CREATE FUNCTION auth.uid() RETURNS text LANGUAGE sql AS $$ SELECT 'old'::text $$;
        CREATE FUNCTION auth."role helper"(integer) RETURNS int LANGUAGE sql AS $$ SELECT $1 $$;
        CREATE TYPE auth.extension_type AS ENUM ('extension');
        ALTER EXTENSION plpgsql ADD TYPE auth.extension_type;
        CREATE FUNCTION auth.extension_function() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;
        ALTER EXTENSION plpgsql ADD FUNCTION auth.extension_function();
        CREATE TABLE public.schema_migrations (version text);
        INSERT INTO public.schema_migrations VALUES ('preserved');
        CREATE FUNCTION public.handle_auth_user_sync() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;
        ALTER FUNCTION public.handle_auth_user_sync() OWNER TO atlas_auth_sync;
        CREATE TABLE unrelated.keep (id int);
        CREATE TYPE unrelated.keep_type AS ENUM ('keep');
        CREATE FUNCTION unrelated.keep_function() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;
        GRANT USAGE, CREATE ON SCHEMA auth TO "Auth ""Owner";
        GRANT USAGE ON SCHEMA unrelated TO "Auth ""Owner";
        GRANT ALL ON ALL TABLES IN SCHEMA auth TO "Auth ""Owner";
        GRANT ALL ON ALL SEQUENCES IN SCHEMA auth TO "Auth ""Owner";
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA auth TO "Auth ""Owner";
    ''')
    assert setup.returncode == 0, setup.stderr


def _auth_owner_migrations(sql, *, allowed: bool) -> None:
    # Representative GoTrue v2.191.0 operations: ACL grants authorize none.
    migrations = (
        "COMMENT ON TABLE auth.identities IS 'Identity records';",
        "ALTER TABLE auth.identities ADD COLUMN last_challenged_at timestamptz;",
        "ALTER TYPE auth.factor_type ADD VALUE 'phone';",
        "CREATE OR REPLACE FUNCTION auth.uid() RETURNS text LANGUAGE sql AS $$ SELECT 'new'::text $$;",
    )
    for migration in migrations:
        result = sql('SET ROLE "Auth ""Owner"; ' + migration)
        if allowed:
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0 and "must be owner" in result.stderr, result.stderr


def _auth_ownership_snapshot(sql) -> str:
    result = sql('''
        SELECT 'schema:' || nspname || '=' || pg_catalog.pg_get_userbyid(nspowner)
          FROM pg_catalog.pg_namespace WHERE nspname IN ('auth','public','unrelated')
        UNION ALL
        SELECT 'relation:' || n.nspname || '.' || c.relname || '=' || pg_catalog.pg_get_userbyid(c.relowner)
          FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname IN ('auth','unrelated') AND c.relkind IN ('r','S')
             OR c.oid='public.schema_migrations'::regclass
        UNION ALL
        SELECT 'type:' || n.nspname || '.' || t.typname || '=' || pg_catalog.pg_get_userbyid(t.typowner)
          FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
          WHERE n.nspname IN ('auth','unrelated')
        UNION ALL
        SELECT 'routine:' || n.nspname || '.' || p.proname || '=' || pg_catalog.pg_get_userbyid(p.proowner)
          FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname IN ('auth','unrelated') OR p.proname='handle_auth_user_sync'
        ORDER BY 1;
    ''')
    assert result.returncode == 0, result.stderr
    return result.stdout


def _assert_auth_ownership_boundaries(sql, before: str) -> None:
    expected = []
    for line in before.splitlines():
        application = ':auth.' in line or line.startswith('schema:auth=')
        extension = '.extension_' in line or '._extension_' in line
        owner = 'Auth "Owner' if application and not extension else line.rsplit('=', 1)[1]
        expected.append(line.rsplit('=', 1)[0] + '=' + owner)
    assert _auth_ownership_snapshot(sql).splitlines() == expected
    state = sql('''
        SELECT id, identity_id, payload FROM auth.identities;
        SELECT version FROM public.schema_migrations;
        SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole FROM pg_catalog.pg_roles
          WHERE rolname='Auth "Owner';
        SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname='atlas_auth_sync';
        SELECT pg_catalog.pg_has_role('Auth "Owner', session_user, 'MEMBER');
    ''')
    assert state.stdout.splitlines() == ['1|1|preserved', 'preserved', 't|f|f|f', 'f', 'f']
    denied = sql('SET ROLE "Auth ""Owner"; ALTER TABLE unrelated.keep ADD COLUMN forbidden int;')
    assert denied.returncode != 0 and 'must be owner' in denied.stderr


def test_native_legacy_auth_ownership_allows_pinned_owner_migrations(native_ownership_postgres) -> None:
    sql = native_ownership_postgres
    _seed_legacy_auth_objects(sql)
    before = _auth_ownership_snapshot(sql)
    _auth_owner_migrations(sql, allowed=False)
    statement = '''
        SET search_path = pg_catalog, pg_temp;
        SELECT pg_catalog.set_config('atlas.auth_role', 'Auth "Owner', false);
        SELECT pg_catalog.set_config('atlas.storage_role', 'storage_owner', false);
        SELECT pg_catalog.set_config('atlas.realtime_role', 'realtime_owner', false);
    ''' + _shared_ownership_block()
    for _ in range(2):
        result = sql(statement)
        assert result.returncode == 0, result.stderr
        _assert_auth_ownership_boundaries(sql, before)
    _auth_owner_migrations(sql, allowed=True)
    types = sql('''SET ROLE "Auth ""Owner";
        ALTER DOMAIN auth.factor_domain SET NOT NULL;
        ALTER TYPE auth.factor_record ADD ATTRIBUTE added int;
        CREATE OR REPLACE FUNCTION auth."role helper"(integer) RETURNS int LANGUAGE sql AS $$ SELECT $1 + 1 $$;
        SELECT auth.uid(); SELECT auth."role helper"(1);
    ''')
    assert types.returncode == 0, types.stderr
    assert types.stdout.splitlines()[-2:] == ['new', '2']
    _assert_auth_ownership_boundaries(sql, before)


_SUPABASE_HEALTH_ENV = {
    "SUPABASE_DB_USER": "supabase_admin",
    "SUPABASE_DB_NAME": "postgres",
    "SUPABASE_DB_PASSWORD": "owned health password$literal#suffix",
}


def _run_supabase_healthcheck_fixture(tmp_path, supplied, status=0):
    from string import Template
    import yaml
    from utils.atomic_write import render_env_assignment

    env_file = tmp_path / ".env"
    env_file.write_text("".join(
        f"{key}={render_env_assignment(key, value)}\n" for key, value in supplied.items()
    ), encoding="utf-8")
    parsed = ConfigParser(str(tmp_path)).parse_env_file()
    assert parsed == supplied
    service = yaml.safe_load((REPO / "services/supabase/compose.yml").read_text())["services"]["supabase-db"]
    program = Template(service["healthcheck"]["test"][1]).substitute(parsed)
    environment = {key: Template(value).substitute(parsed) for key, value in service["environment"].items()}
    executable = tmp_path / "pg_isready"
    receipt = tmp_path / "probe.json"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['OWNED_PROBE']).write_text(json.dumps({"
        "'argv': sys.argv[1:], 'password': os.environ.get('POSTGRES_PASSWORD')}))\n"
        "raise SystemExit(int(os.environ['OWNED_STATUS']))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    environment.update(PATH=f"{tmp_path}{os.pathsep}{os.defpath}",
                       OWNED_PROBE=str(receipt), OWNED_STATUS=str(status))
    result = subprocess.run(
        ["/bin/sh", "-c", program], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=10, check=False,
    )
    recorded = json.loads(receipt.read_text()) if receipt.exists() else {}
    return result, recorded


def _assert_supabase_healthcheck_probe(result, recorded, supplied, status):
    assert result.returncode == status
    assert recorded["argv"] == [
        "-h", "localhost", "-U", supplied["SUPABASE_DB_USER"], "-d", supplied["SUPABASE_DB_NAME"],
    ]
    assert recorded["password"] == supplied["SUPABASE_DB_PASSWORD"]
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize("field", ["SUPABASE_DB_USER", "SUPABASE_DB_NAME"])
@pytest.mark.parametrize("identity", [
    "synthetic_name", "synthetic name", "synthetic'name", 'synthetic"name',
    r"synthetic\name", "synthetic$HOME", "synthetic_é界", "",
    "synthetic$(touch owned-marker)", "synthetic`touch owned-marker2`",
])
def test_supabase_healthcheck_preserves_literal_identity(tmp_path, field, identity):
    supplied = {**_SUPABASE_HEALTH_ENV, field: identity}
    result, recorded = _run_supabase_healthcheck_fixture(tmp_path, supplied)
    assert not (tmp_path / "owned-marker").exists()
    assert not (tmp_path / "owned-marker2").exists()
    _assert_supabase_healthcheck_probe(result, recorded, supplied, 0)


@pytest.mark.parametrize("status", [0, 1, 2, 3, 42])
def test_supabase_healthcheck_preserves_probe_exit_status(tmp_path, status):
    result, recorded = _run_supabase_healthcheck_fixture(tmp_path, _SUPABASE_HEALTH_ENV, status)
    _assert_supabase_healthcheck_probe(result, recorded, _SUPABASE_HEALTH_ENV, status)


def test_supabase_healthcheck_preserves_transport_and_schedule():
    import yaml

    healthcheck = yaml.safe_load((REPO / "services/supabase/compose.yml").read_text())["services"]["supabase-db"]["healthcheck"]
    assert healthcheck["test"][0] == "CMD-SHELL"
    assert "pg_isready -h localhost" in healthcheck["test"][1]
    assert {key: value for key, value in healthcheck.items() if key != "test"} == {
        "interval": "10s", "timeout": "5s", "retries": 5, "start_period": "60s",
    }
