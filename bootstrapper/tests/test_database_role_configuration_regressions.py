"""Configuration-validation and URI-safety regressions for Task 3."""

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
from tests.test_database_role_boundaries import TEST_SECRETS
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
        services["supabase-studio"]["environment"]["DATABASE_URL"],
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
