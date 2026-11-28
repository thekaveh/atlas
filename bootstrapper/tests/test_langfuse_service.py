from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "langfuse"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_langfuse_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "langfuse"
    assert manifest["category"] == "infra"
    assert manifest["containers"] == [
        "langfuse-init",
        "langfuse-web",
        "langfuse-worker",
        "langfuse-clickhouse",
    ]
    assert manifest["sources"]["var"] == "LANGFUSE_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == [
        "supabase",
        "redis",
        "silo",
        "litellm",
        "kong",
        "ray",
    ]
    # langfuse does not call litellm — litellm posts traces TO langfuse
    # (success_callback:["langfuse"], asserted below). The edge lives on
    # litellm.calls, not here.
    assert manifest["data_flow"]["calls"] == [
        "supabase",
        "redis",
        "silo",
    ]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["LANGFUSE_SOURCE"]["default"] == "disabled"
    for scale_var in (
        "LANGFUSE_INIT_SCALE",
        "LANGFUSE_WEB_SCALE",
        "LANGFUSE_WORKER_SCALE",
        "LANGFUSE_CLICKHOUSE_SCALE",
    ):
        assert env_vars[scale_var]["auto_managed"] is True
    for secret_var in (
        "LANGFUSE_SALT",
        "LANGFUSE_ENCRYPTION_KEY",
        "LANGFUSE_NEXTAUTH_SECRET",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_CLICKHOUSE_PASSWORD",
    ):
        assert env_vars[secret_var]["secret"] is True
    assert "default" not in env_vars["LANGFUSE_PORT"]

    row = manifest["rows"][0]
    assert row["display_name"] == "Langfuse"
    assert row["source_var"] == "LANGFUSE_SOURCE"
    assert row["port_var"] == "LANGFUSE_PORT"
    assert row["scale_var"] == "LANGFUSE_WEB_SCALE"
    assert row["alias"] == "langfuse.localhost"


def test_langfuse_topology_alias_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "langfuse"]

    assert len(rows) == 1
    assert rows[0].category == "infra"
    assert rows[0].alias == "langfuse.localhost"
    assert "langfuse.localhost" in topology.aliases
    assert "LANGFUSE_PORT" in topology.port_defaults

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "LANGFUSE_SOURCE=disabled",
        "LANGFUSE_IMAGE=langfuse/langfuse:3.115.0",
        "LANGFUSE_WORKER_IMAGE=langfuse/langfuse-worker:3.115.0",
        "LANGFUSE_CLICKHOUSE_IMAGE=clickhouse/clickhouse-server:25.8.33.6",
        "LANGFUSE_INIT_IMAGE=alpine:3.24.4",
        "LANGFUSE_PORT=",
        "LANGFUSE_ENDPOINT=",
        "LANGFUSE_WEB_SCALE=",
        "LANGFUSE_WORKER_SCALE=",
        "LANGFUSE_CLICKHOUSE_SCALE=",
        "SILO_BUCKET_LANGFUSE=langfuse",
    ):
        assert expected in env_example


def test_langfuse_track_membership_excludes_data_eng() -> None:
    registry = load_tracks()

    for track_key in (
        "gen-ai-rag",
        "gen-ai-eng",
        "gen-ai-creative",
        "ml-eng",
        "all",
    ):
        assert is_in_track(
            registry.by_key[track_key],
            "langfuse",
            always_on=registry.always_on,
        )

    assert not is_in_track(
        registry.by_key["data-eng"],
        "langfuse",
        always_on=registry.always_on,
    )


def test_langfuse_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["langfuse_source"] == "LANGFUSE_SOURCE"
    assert mgr.source_mapping["langfuse_web_source"] == "LANGFUSE_SOURCE"
    assert mgr.collect_overrides(langfuse_source="container") == {
        "LANGFUSE_SOURCE": "container",
    }


def test_langfuse_scale_generation_and_dependency_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"LANGFUSE_SOURCE": "disabled", "SILO_SOURCE": "disabled"}
    assert sc._generate_langfuse_config() == {
        "LANGFUSE_INIT_SCALE": "0",
        "LANGFUSE_WEB_SCALE": "0",
        "LANGFUSE_WORKER_SCALE": "0",
        "LANGFUSE_CLICKHOUSE_SCALE": "0",
        "LANGFUSE_ENDPOINT": "",
    }

    sc.service_sources = {"LANGFUSE_SOURCE": "container", "SILO_SOURCE": "container"}
    assert sc._generate_langfuse_config() == {
        "LANGFUSE_INIT_SCALE": "1",
        "LANGFUSE_WEB_SCALE": "1",
        "LANGFUSE_WORKER_SCALE": "1",
        "LANGFUSE_CLICKHOUSE_SCALE": "1",
        "LANGFUSE_ENDPOINT": "http://langfuse-web:3000",
    }

    sc.service_sources = {"LANGFUSE_SOURCE": "container", "SILO_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="Langfuse requires Silo"):
        sc._generate_langfuse_config()


def test_litellm_settings_add_langfuse_success_callback_only_when_enabled() -> None:
    from utils.litellm_settings import base_settings

    disabled = base_settings({"LANGFUSE_SOURCE": "disabled"})
    assert disabled["litellm_settings"]["callbacks"] == ["prometheus"]
    assert "success_callback" not in disabled["litellm_settings"]
    assert "failure_callback" not in disabled["litellm_settings"]

    enabled = base_settings({"LANGFUSE_SOURCE": "container"})
    litellm_settings = enabled["litellm_settings"]
    assert litellm_settings["callbacks"] == ["prometheus"]
    assert litellm_settings["success_callback"] == ["langfuse"]
    assert litellm_settings["failure_callback"] == ["langfuse"]


def test_langfuse_compose_contract() -> None:
    compose = _compose()["services"]
    web = compose["langfuse-web"]
    worker = compose["langfuse-worker"]
    clickhouse = compose["langfuse-clickhouse"]
    init = compose["langfuse-init"]

    assert web["image"] == "${LANGFUSE_IMAGE:-langfuse/langfuse:3.115.0}"
    assert worker["image"] == "${LANGFUSE_WORKER_IMAGE:-langfuse/langfuse-worker:3.115.0}"
    assert clickhouse["image"] == "${LANGFUSE_CLICKHOUSE_IMAGE:-clickhouse/clickhouse-server:25.8.33.6}"
    assert web["ports"] == ["${HOST_BIND_IP-127.0.0.1:}${LANGFUSE_PORT}:3000"]
    assert "ports" not in clickhouse
    assert web["environment"]["LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT"] == "http://silo:9000"
    assert web["environment"]["LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT"] == "http://silo:9000"
    assert web["environment"]["DATABASE_URL"] == (
        "postgresql://${LANGFUSE_DB_USER_URI:?LANGFUSE_DB_USER_URI is required}:${LANGFUSE_DB_PASSWORD_URI:?LANGFUSE_DB_PASSWORD_URI is required}@supabase-db:5432/${LANGFUSE_DB_NAME_URI:?LANGFUSE_DB_NAME_URI is required}"
    )
    assert web["depends_on"]["langfuse-init"]["condition"] == "service_completed_successfully"
    assert worker["depends_on"]["langfuse-init"]["condition"] == "service_completed_successfully"
    assert init["depends_on"]["supabase-db-init"]["condition"] == "service_completed_successfully"
    assert init["depends_on"]["silo-init"]["condition"] == "service_completed_successfully"
    assert not {
        "SILO_ROOT_USER",
        "SILO_ROOT_PASSWORD",
        "SILO_BUCKET_LANGFUSE",
        "SILO_LANGFUSE_ACCESS_KEY",
        "SILO_LANGFUSE_SECRET_KEY",
    } & set(init["environment"]), "Langfuse init must not duplicate silo-init provisioning"


def test_litellm_init_receives_langfuse_source_for_config_render() -> None:
    litellm_compose = yaml.safe_load(
        (REPO_ROOT / "services" / "litellm" / "compose.yml").read_text()
    )
    litellm_init_env = litellm_compose["services"]["litellm-init"]["environment"]
    assert litellm_init_env["LANGFUSE_SOURCE"] == "${LANGFUSE_SOURCE:-disabled}"


def test_langfuse_kong_route_only_when_container() -> None:
    from utils.kong_config_generator import KongConfigGenerator

    def _config(env: dict[str, str]) -> dict:
        cp = ConfigParser(str(REPO_ROOT))
        gen = KongConfigGenerator(cp)
        gen.load_environment_variables = lambda: setattr(gen, "env_vars", env)
        return gen.generate_kong_config()

    enabled = _config({"LANGFUSE_SOURCE": "container"})
    disabled = _config({"LANGFUSE_SOURCE": "disabled"})

    enabled_hosts = {
        host: service
        for service in enabled["services"]
        for route in service.get("routes", [])
        for host in route.get("hosts") or []
    }
    disabled_hosts = {
        host: service
        for service in disabled["services"]
        for route in service.get("routes", [])
        for host in route.get("hosts") or []
    }
    assert enabled_hosts["langfuse.localhost"]["name"] == "langfuse"
    assert enabled_hosts["langfuse.localhost"]["url"] == "http://langfuse-web:3000/"
    assert enabled_hosts["langfuse.localhost"]["routes"][0]["preserve_host"] is True
    assert {plugin["name"] for plugin in enabled_hosts["langfuse.localhost"]["plugins"]} >= {
        "basic-auth",
        "acl",
        "cors",
    }
    assert "langfuse.localhost" not in disabled_hosts


def test_silo_provisions_langfuse_bucket_and_scoped_credentials() -> None:
    silo_manifest = yaml.safe_load(
        (REPO_ROOT / "services" / "silo" / "service.yml").read_text()
    )
    env_vars = {entry["name"]: entry for entry in silo_manifest["env"]}

    assert env_vars["SILO_BUCKET_LANGFUSE"]["default"] == "langfuse"
    assert env_vars["SILO_LANGFUSE_ACCESS_KEY"]["secret"] is True
    assert env_vars["SILO_LANGFUSE_SECRET_KEY"]["secret"] is True

    silo_compose = yaml.safe_load(
        (REPO_ROOT / "services" / "silo" / "compose.yml").read_text()
    )
    silo_init_env = silo_compose["services"]["silo-init"]["environment"]
    assert silo_init_env["SILO_BUCKET_LANGFUSE"] == "${SILO_BUCKET_LANGFUSE}"
    assert silo_init_env["SILO_LANGFUSE_ACCESS_KEY"] == "${SILO_LANGFUSE_ACCESS_KEY}"
    assert silo_init_env["SILO_LANGFUSE_SECRET_KEY"] == "${SILO_LANGFUSE_SECRET_KEY}"

    script = (
        REPO_ROOT / "services" / "silo" / "init" / "scripts" / "init-silo.sh"
    ).read_text()
    assert "langfuse:SILO_BUCKET_LANGFUSE:SILO_LANGFUSE_ACCESS_KEY:SILO_LANGFUSE_SECRET_KEY" in script


def test_key_generator_creates_langfuse_and_silo_credentials(tmp_path) -> None:
    from utils.key_generator import KeyGenerator

    env_file = tmp_path / ".env"
    env_file.write_text(
        "LANGFUSE_SALT=\n"
        "LANGFUSE_ENCRYPTION_KEY=\n"
        "LANGFUSE_NEXTAUTH_SECRET=\n"
        "LANGFUSE_PUBLIC_KEY=\n"
        "LANGFUSE_SECRET_KEY=\n"
        "LANGFUSE_INIT_USER_PASSWORD=\n"
        "LANGFUSE_CLICKHOUSE_PASSWORD=\n",
        encoding="utf-8",
    )

    generator = KeyGenerator(str(tmp_path))
    langfuse_results = generator.generate_and_update_langfuse_secrets()
    silo_results = generator.generate_and_update_silo_consumer_keys()
    values = ConfigParser(str(tmp_path)).parse_env_file()

    assert all(langfuse_results.values())
    assert len(values["LANGFUSE_ENCRYPTION_KEY"]) == 64
    assert values["LANGFUSE_PUBLIC_KEY"].startswith("pk-lf-")
    assert values["LANGFUSE_SECRET_KEY"].startswith("sk-lf-")
    assert silo_results["SILO_LANGFUSE_ACCESS_KEY"] is True
    assert silo_results["SILO_LANGFUSE_SECRET_KEY"] is True
    assert values["SILO_LANGFUSE_ACCESS_KEY"]
    assert values["SILO_LANGFUSE_SECRET_KEY"]


def test_langfuse_docs_describe_observability_scope_and_rollbacks() -> None:
    readme = README.read_text()

    for expected in (
        "LANGFUSE_SOURCE=disabled",
        "langfuse.localhost",
        "LiteLLM",
        "success_callback",
        "Prometheus",
        "Grafana",
        "ClickHouse",
        "Silo",
        "low-scale",
        "rollback",
        "ComfyUI",
        "Hermes",
        "out of scope",
    ):
        assert expected in readme
