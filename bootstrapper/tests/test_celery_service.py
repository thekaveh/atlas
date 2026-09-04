from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "celery"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_celery_docs_distinguish_public_and_operator_failure_details() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "generic `Background job failed`" in readme
    assert "detailed errors and raw tracebacks remain in worker logs" in readme
    assert "error text from Redis" not in readme


def test_celery_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "celery"
    assert manifest["category"] == "agents"
    assert manifest["containers"] == ["celery-worker", "flower"]
    assert manifest["sources"]["var"] == "CELERY_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == [
        "redis",
        "backend",
        "supabase",
        "litellm",
    ]
    assert manifest["depends_on"].get("optional", []) == [
        "weaviate",
        "supavisor",
        "docling",
        "tika",
        "lightrag",
        "minio",
        "otel-collector",
    ]
    assert manifest["data_flow"]["calls"] == [
        "redis",
        "supabase",
        "litellm",
        "weaviate",
        "supavisor",
        "docling",
        "tika",
        "lightrag",
        "minio",
        "otel-collector",
    ]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["CELERY_SOURCE"]["default"] == "disabled"
    assert env_vars["CELERY_WORKER_SCALE"]["auto_managed"] is True
    assert env_vars["FLOWER_SCALE"]["auto_managed"] is True
    assert env_vars["CELERY_BROKER_URL"]["auto_managed"] is True
    assert env_vars["CELERY_RESULT_BACKEND"]["auto_managed"] is True
    assert env_vars["FLOWER_PORT"]["description"]
    assert env_vars["CELERY_TASK_TIME_LIMIT_SECONDS"]["default"] == 900
    assert env_vars["CELERY_TASK_SOFT_TIME_LIMIT_SECONDS"]["default"] == 840
    assert env_vars["CELERY_WORKER_CONCURRENCY"]["default"] == 2
    assert env_vars["CELERY_WORKER_PREFETCH_MULTIPLIER"]["default"] == 1
    assert "default" not in env_vars["FLOWER_PORT"]

    rows = manifest["rows"]
    assert rows[0]["display_name"] == "Celery Worker"
    assert rows[0]["source_var"] == "CELERY_SOURCE"
    assert rows[0]["scale_var"] == "CELERY_WORKER_SCALE"
    assert "port_var" not in rows[0]
    assert "alias" not in rows[0]
    assert rows[1]["display_name"] == "Flower"
    assert rows[1]["source_var"] == "CELERY_SOURCE"
    assert rows[1]["port_var"] == "FLOWER_PORT"
    assert rows[1]["scale_var"] == "FLOWER_SCALE"
    assert rows[1]["alias"] == "flower.localhost"


def test_celery_topology_alias_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "celery"]

    assert len(rows) == 2
    assert {row.display_name for row in rows} == {"Celery Worker", "Flower"}
    assert all(row.category == "agents" for row in rows)
    assert "flower.localhost" in topology.aliases
    assert "FLOWER_PORT" in topology.port_defaults
    assert rows[0].port_var is None

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "CELERY_SOURCE=disabled",
        "CELERY_WORKER_SCALE=",
        "FLOWER_SCALE=",
        "FLOWER_PORT=",
        "CELERY_BROKER_URL=",
        "CELERY_RESULT_BACKEND=",
        "CELERY_QUEUE=atlas",
        "CELERY_TASK_TIME_LIMIT_SECONDS=900",
        "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS=840",
        "FLOWER_IMAGE=mher/flower:2.0.1",
    ):
        assert expected in env_example


def test_celery_track_membership_is_rag_eng_and_all() -> None:
    registry = load_tracks()

    for track_key in ("gen-ai-rag", "gen-ai-eng", "all"):
        assert is_in_track(
            registry.by_key[track_key],
            "celery",
            always_on=registry.always_on,
        )

    for track_key in ("gen-ai-creative", "ml-eng", "data-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "celery",
            always_on=registry.always_on,
        )


def test_celery_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["celery_source"] == "CELERY_SOURCE"
    assert mgr.source_mapping["celery_worker_source"] == "CELERY_SOURCE"
    assert mgr.collect_overrides(celery_source="container") == {
        "CELERY_SOURCE": "container",
    }


def test_celery_scale_generation_uses_existing_redis() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"CELERY_SOURCE": "disabled"}
    assert sc._generate_celery_config() == {
        "CELERY_WORKER_SCALE": "0",
        "FLOWER_SCALE": "0",
        "CELERY_BROKER_URL": "",
        "CELERY_RESULT_BACKEND": "",
    }

    sc.service_sources = {"CELERY_SOURCE": "container"}
    assert sc._generate_celery_config() == {
        "CELERY_WORKER_SCALE": "1",
        "FLOWER_SCALE": "1",
        "CELERY_BROKER_URL": "redis://:${REDIS_PASSWORD}@redis:6379/4",
        "CELERY_RESULT_BACKEND": "redis://:${REDIS_PASSWORD}@redis:6379/4",
    }


def test_celery_compose_contract() -> None:
    compose = _compose()["services"]
    worker = compose["celery-worker"]
    flower = compose["flower"]

    assert worker["build"]["context"] == "../backend/app"
    assert worker["deploy"]["replicas"] == "${CELERY_WORKER_SCALE:-0}"
    assert worker["depends_on"]["redis"]["condition"] == "service_healthy"
    assert worker["depends_on"]["supabase-db-init"]["condition"] == "service_completed_successfully"
    assert worker["environment"]["CELERY_BROKER_URL"] == "${CELERY_BROKER_URL:-}"
    assert worker["environment"]["CELERY_RESULT_BACKEND"] == "${CELERY_RESULT_BACKEND:-}"
    assert worker["environment"]["REDIS_URL"] == "${REDIS_URL}"
    for name in (
        "RAG_INGESTION_MAX_FILE_BYTES",
        "RAG_INGESTION_MAX_CORPUS_BYTES",
        "RAG_INGESTION_MAX_FILES",
        "DOCLING_ENDPOINT",
        "TIKA_ENDPOINT",
        "LIGHTRAG_ENDPOINT",
        "LIGHTRAG_API_KEY",
        "MINIO_ENDPOINT",
    ):
        assert name in worker["environment"]
    assert worker["environment"]["DATABASE_URL"] == (
        "${SUPAVISOR_DATABASE_URL:-postgresql://${BACKEND_DB_USER_URI:?BACKEND_DB_USER_URI is required}:${BACKEND_DB_PASSWORD_URI:?BACKEND_DB_PASSWORD_URI is required}@supabase-db:5432/${SUPABASE_DB_NAME_URI:?SUPABASE_DB_NAME_URI is required}}"
    )
    assert "celery_app:celery_app" in " ".join(worker["command"])
    assert "--queues=${CELERY_QUEUE:-atlas}" in worker["command"]

    assert flower["image"] == "${FLOWER_IMAGE:-mher/flower:2.0.1}"
    assert flower["deploy"]["replicas"] == "${FLOWER_SCALE:-0}"
    assert flower["ports"] == ["${HOST_BIND_IP:-}${FLOWER_PORT}:5555"]
    assert flower["depends_on"]["redis"]["condition"] == "service_healthy"
    assert flower["environment"]["CELERY_BROKER_URL"] == "${CELERY_BROKER_URL:-}"
    assert "--basic-auth=${DASHBOARD_USERNAME}:${DASHBOARD_PASSWORD}" in flower["command"]
    assert "--port=5555" in flower["command"]
    assert "http://localhost:5555/healthcheck" in "\n".join(flower["healthcheck"]["test"])


def test_backend_receives_celery_runtime_environment() -> None:
    backend_manifest = yaml.safe_load(
        (REPO_ROOT / "services" / "backend" / "service.yml").read_text()
    )
    backend_compose = yaml.safe_load(
        (REPO_ROOT / "services" / "backend" / "compose.yml").read_text()
    )

    assert "celery" in backend_manifest["depends_on"]["optional"]
    assert "celery" in backend_manifest["data_flow"]["calls"]

    env = backend_compose["services"]["backend"]["environment"]
    assert env["CELERY_SOURCE"] == "${CELERY_SOURCE:-disabled}"
    assert env["CELERY_BROKER_URL"] == "${CELERY_BROKER_URL:-}"
    assert env["CELERY_RESULT_BACKEND"] == "${CELERY_RESULT_BACKEND:-}"


def test_flower_kong_route_only_when_celery_container() -> None:
    from utils.kong_config_generator import KongConfigGenerator

    def _config(env: dict[str, str]) -> dict:
        cp = ConfigParser(str(REPO_ROOT))
        gen = KongConfigGenerator(cp)
        gen.load_environment_variables = lambda: setattr(gen, "env_vars", env)
        return gen.generate_kong_config()

    enabled = _config({"CELERY_SOURCE": "container"})
    disabled = _config({"CELERY_SOURCE": "disabled"})

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
    assert enabled_hosts["flower.localhost"]["name"] == "flower"
    assert enabled_hosts["flower.localhost"]["url"] == "http://flower:5555/"
    assert enabled_hosts["flower.localhost"]["routes"][0]["preserve_host"] is True
    assert {plugin["name"] for plugin in enabled_hosts["flower.localhost"]["plugins"]} >= {
        "basic-auth",
        "acl",
        "cors",
    }
    assert "flower.localhost" not in disabled_hosts


def test_celery_docs_describe_retry_security_and_async_memory_scope() -> None:
    readme = README.read_text()

    for expected in (
        "CELERY_SOURCE=disabled",
        "flower.localhost",
        "memory consolidation",
        "POST /memory/consolidate?async_job=true",
        "GET /jobs/{job_id}",
        "Redis database 4",
        "basic-auth",
        "time limit",
        "visibility timeout",
        "Research start is deferred",
    ):
        assert expected in readme
