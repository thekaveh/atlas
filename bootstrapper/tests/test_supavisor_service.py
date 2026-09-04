from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.key_generator import KeyGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "supavisor"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_supavisor_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "supavisor"
    assert manifest["category"] == "data"
    assert manifest["containers"] == ["supavisor"]
    assert manifest["sources"]["var"] == "SUPAVISOR_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["supabase"]
    assert set(manifest["depends_on"].get("optional", [])) >= {
        "backend",
        "n8n",
        "celery",
    }
    assert manifest["data_flow"]["calls"] == ["supabase"]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["SUPAVISOR_SOURCE"]["default"] == "disabled"
    assert env_vars["SUPAVISOR_SCALE"]["auto_managed"] is True
    assert env_vars["SUPAVISOR_DATABASE_URL"]["auto_managed"] is True
    assert env_vars["SUPAVISOR_DB_HOST"]["auto_managed"] is True
    assert env_vars["SUPAVISOR_DB_PORT_VALUE"]["auto_managed"] is True
    assert env_vars["SUPAVISOR_DB_USER"]["auto_managed"] is True
    assert env_vars["SUPAVISOR_TENANT_ID"]["default"] == "atlas"
    assert env_vars["SUPAVISOR_DEFAULT_POOL_SIZE"]["default"] == 20
    assert env_vars["SUPAVISOR_MAX_CLIENT_CONN"]["default"] == 100
    assert env_vars["SUPAVISOR_DB_POOL_SIZE"]["default"] == 5
    assert env_vars["SUPAVISOR_SECRET_KEY_BASE"]["secret"] is True
    assert env_vars["SUPAVISOR_VAULT_ENC_KEY"]["secret"] is True
    assert "SUPAVISOR_TRANSACTION_PORT" not in env_vars

    row = manifest["rows"][0]
    assert row["display_name"] == "Supavisor"
    assert row["source_var"] == "SUPAVISOR_SOURCE"
    assert "port_var" not in row
    assert row["scale_var"] == "SUPAVISOR_SCALE"
    assert "alias" not in row


def test_supavisor_topology_track_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "supavisor"]

    assert len(rows) == 1
    assert rows[0].category == "data"
    assert rows[0].alias is None
    assert rows[0].port_var is None
    assert "supavisor.localhost" not in topology.aliases
    assert "SUPAVISOR_TRANSACTION_PORT" not in topology.port_defaults

    registry = load_tracks()
    for track_key in ("gen-ai-rag", "gen-ai-eng", "ml-eng", "data-eng", "all"):
        assert is_in_track(
            registry.by_key[track_key],
            "supavisor",
            always_on=registry.always_on,
        )
    assert not is_in_track(
        registry.by_key["gen-ai-creative"],
        "supavisor",
        always_on=registry.always_on,
    )

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "SUPAVISOR_SOURCE=disabled",
        "SUPAVISOR_IMAGE=supabase/supavisor:2.9.5",
        "SUPAVISOR_SCALE=",
        "SUPAVISOR_TENANT_ID=atlas",
        "SUPAVISOR_DEFAULT_POOL_SIZE=20",
        "SUPAVISOR_MAX_CLIENT_CONN=100",
        "SUPAVISOR_DB_POOL_SIZE=5",
        "SUPAVISOR_DATABASE_URL=",
        "SUPAVISOR_DB_HOST=",
        "SUPAVISOR_DB_PORT_VALUE=",
        "SUPAVISOR_DB_USER=",
        "SUPAVISOR_SECRET_KEY_BASE=",
        "SUPAVISOR_VAULT_ENC_KEY=",
        "SUPAVISOR_API_JWT_SECRET=",
        "SUPAVISOR_METRICS_JWT_SECRET=",
    ):
        assert expected in env_example


def test_supavisor_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["supavisor_source"] == "SUPAVISOR_SOURCE"
    assert mgr.collect_overrides(supavisor_source="container") == {
        "SUPAVISOR_SOURCE": "container",
    }


def test_supavisor_generates_pooler_and_rollback_envs() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"SUPAVISOR_SOURCE": "disabled"}
    sc.config_parser.parse_env_file.return_value = {
        "SUPABASE_DB_USER": "supabase_admin",
        "SUPABASE_DB_NAME": "postgres",
        "SUPAVISOR_TENANT_ID": "atlas",
    }
    assert sc._generate_supavisor_config() == {
        "SUPAVISOR_SCALE": "0",
        "SUPAVISOR_DB_HOST": "supabase-db",
        "SUPAVISOR_DB_PORT_VALUE": "5432",
        "SUPAVISOR_DB_USER": "${N8N_DB_USER}",
        "SUPAVISOR_DATABASE_URL": (
            "postgresql://${BACKEND_DB_USER_URI}:${BACKEND_DB_PASSWORD_URI}"
                "@supabase-db:5432/${SUPABASE_DB_NAME_URI}"
        ),
    }

    sc.service_sources = {"SUPAVISOR_SOURCE": "container"}
    assert sc._generate_supavisor_config() == {
        "SUPAVISOR_SCALE": "1",
        "SUPAVISOR_DB_HOST": "supavisor",
        "SUPAVISOR_DB_PORT_VALUE": "6543",
        "SUPAVISOR_DB_USER": "${N8N_DB_USER}.${SUPAVISOR_TENANT_ID}",
        "SUPAVISOR_DATABASE_URL": (
            "postgresql://${BACKEND_DB_USER_URI}.${SUPAVISOR_TENANT_ID}:"
            "${BACKEND_DB_PASSWORD_URI}@supavisor:6543/${SUPABASE_DB_NAME_URI}"
        ),
    }


def test_supavisor_compose_contract() -> None:
    service = _compose()["services"]["supavisor"]

    assert service["image"] == "${SUPAVISOR_IMAGE:-supabase/supavisor:2.9.5}"
    assert service["deploy"]["replicas"] == "${SUPAVISOR_SCALE:-0}"
    assert "ports" not in service
    assert service["depends_on"]["supabase-db-init"]["condition"] == "service_completed_successfully"
    assert service["environment"]["PORT"] == 4000
    assert service["environment"]["PROXY_PORT_TRANSACTION"] == 6543
    assert service["environment"]["POOLER_POOL_MODE"] == "transaction"
    assert service["environment"]["POOLER_TENANT_ID"] == "${SUPAVISOR_TENANT_ID:-atlas}"
    assert service["environment"]["DATABASE_URL"].startswith(
        "ecto://${SUPAVISOR_DB_ADMIN_USER_URI:?SUPAVISOR_DB_ADMIN_USER_URI is required}:${SUPAVISOR_DB_ADMIN_PASSWORD_URI:?SUPAVISOR_DB_ADMIN_PASSWORD_URI is required}@supabase-db:5432/"
    )
    assert "http://127.0.0.1:4000/api/health" in "\n".join(service["healthcheck"]["test"])
    assert "/app/bin/migrate" in " ".join(service["command"])
    assert "/app/bin/supavisor eval" in " ".join(service["command"])
    assert "/app/bin/server" in " ".join(service["command"])
    assert "/etc/pooler/pooler.exs" in " ".join(service["command"])
    assert service["volumes"] == ["./pooler/pooler.exs:/etc/pooler/pooler.exs:ro"]


def test_supavisor_key_generation_covers_required_secrets(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PROJECT_NAME=atlas-test",
                "SUPAVISOR_SECRET_KEY_BASE=",
                "SUPAVISOR_VAULT_ENC_KEY=",
                "SUPAVISOR_API_JWT_SECRET=",
                "SUPAVISOR_METRICS_JWT_SECRET=",
            ]
        )
        + "\n"
    )

    results = KeyGenerator(str(tmp_path)).generate_missing_keys()
    generated = ConfigParser(str(tmp_path)).parse_env_file()

    for name in (
        "SUPAVISOR_SECRET_KEY_BASE",
        "SUPAVISOR_VAULT_ENC_KEY",
        "SUPAVISOR_API_JWT_SECRET",
        "SUPAVISOR_METRICS_JWT_SECRET",
    ):
        assert results[name] is True
        assert generated[name]
    assert len(generated["SUPAVISOR_VAULT_ENC_KEY"]) == 32


def test_pooler_consumers_use_generated_envs_and_supabase_internals_stay_direct() -> None:
    backend = yaml.safe_load((REPO_ROOT / "services" / "backend" / "compose.yml").read_text())
    celery = yaml.safe_load((REPO_ROOT / "services" / "celery" / "compose.yml").read_text())
    n8n = yaml.safe_load((REPO_ROOT / "services" / "n8n" / "compose.yml").read_text())
    n8n_manifest = yaml.safe_load((REPO_ROOT / "services" / "n8n" / "service.yml").read_text())
    supabase = yaml.safe_load((REPO_ROOT / "services" / "supabase" / "compose.yml").read_text())

    assert "supavisor" in n8n_manifest["data_flow"]["calls"]

    assert backend["services"]["backend"]["environment"]["DATABASE_URL"] == (
        "${SUPAVISOR_DATABASE_URL:-postgresql://${BACKEND_DB_USER_URI:?BACKEND_DB_USER_URI is required}:${BACKEND_DB_PASSWORD_URI:?BACKEND_DB_PASSWORD_URI is required}@supabase-db:5432/${SUPABASE_DB_NAME_URI:?SUPABASE_DB_NAME_URI is required}}"
    )
    assert celery["services"]["celery-worker"]["environment"]["DATABASE_URL"] == (
        "${SUPAVISOR_DATABASE_URL:-postgresql://${BACKEND_DB_USER_URI:?BACKEND_DB_USER_URI is required}:${BACKEND_DB_PASSWORD_URI:?BACKEND_DB_PASSWORD_URI is required}@supabase-db:5432/${SUPABASE_DB_NAME_URI:?SUPABASE_DB_NAME_URI is required}}"
    )
    for service_name in ("n8n", "n8n-worker"):
        env = n8n["services"][service_name]["environment"]
        assert env["DB_POSTGRESDB_HOST"] == "${SUPAVISOR_DB_HOST:-supabase-db}"
        assert env["DB_POSTGRESDB_PORT"] == "${SUPAVISOR_DB_PORT_VALUE:-5432}"
        assert env["DB_POSTGRESDB_USER"] == "${SUPAVISOR_DB_USER:-${N8N_DB_USER:?N8N_DB_USER is required}}"
        assert env["DB_POSTGRESDB_PASSWORD"] == "${N8N_DB_PASSWORD:?N8N_DB_PASSWORD is required}"

    for services, service_name in (
        (backend["services"], "backend"),
        (celery["services"], "celery-worker"),
        (n8n["services"], "n8n"),
        (n8n["services"], "n8n-worker"),
    ):
        supavisor_dep = services[service_name]["depends_on"]["supavisor"]
        assert supavisor_dep["condition"] == "service_healthy"
        assert supavisor_dep["required"] is False

    supa_services = supabase["services"]
    assert "supabase-db:5432" in supa_services["supabase-api"]["environment"]["PGRST_DB_URI"]
    assert supa_services["supabase-realtime"]["environment"]["DB_HOST"] == "supabase-db"
    assert supa_services["supabase-realtime"]["environment"]["DB_PORT"] == 5432
    assert "supabase-db:5432" in supa_services["supabase-auth"]["environment"]["GOTRUE_DB_DATABASE_URL"]
    assert "supabase-db:5432" in supa_services["supabase-storage"]["environment"]["DATABASE_URL"]
    assert "supabase-db:5432" in supa_services["supabase-studio"]["environment"]["DATABASE_URL"]


def test_supavisor_docs_describe_scope_and_rollback() -> None:
    readme = README.read_text()

    for expected in (
        "SUPAVISOR_SOURCE=disabled",
        "transaction mode",
        "internal-only",
        "backend",
        "n8n",
        "Celery worker",
        "PostgREST",
        "Realtime",
        "Rollback",
        "SUPAVISOR_SOURCE=disabled",
        "No Kong alias",
    ):
        assert expected in readme
