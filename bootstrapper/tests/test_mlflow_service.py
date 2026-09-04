from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.key_generator import KeyGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "mlflow"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_mlflow_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "mlflow"
    assert manifest["category"] == "apps"
    assert manifest["containers"] == ["mlflow-init", "mlflow"]
    assert manifest["sources"]["var"] == "MLFLOW_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["supabase", "minio"]
    assert manifest["depends_on"].get("optional", []) == ["jupyterhub"]
    assert manifest["data_flow"]["calls"] == ["supabase", "minio"]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["MLFLOW_SOURCE"]["default"] == "disabled"
    assert env_vars["MLFLOW_SCALE"]["auto_managed"] is True
    assert env_vars["MLFLOW_INIT_SCALE"]["auto_managed"] is True
    assert env_vars["MLFLOW_DB_PASSWORD"]["secret"] is True
    assert "default" not in env_vars["MLFLOW_PORT"]

    row = manifest["rows"][0]
    assert row["display_name"] == "MLflow"
    assert row["source_var"] == "MLFLOW_SOURCE"
    assert row["port_var"] == "MLFLOW_PORT"
    assert row["scale_var"] == "MLFLOW_SCALE"
    assert row["alias"] == "mlflow.localhost"


def test_mlflow_topology_alias_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "mlflow"]

    assert len(rows) == 1
    assert rows[0].category == "apps"
    assert rows[0].alias == "mlflow.localhost"
    assert "mlflow.localhost" in topology.aliases
    assert "MLFLOW_PORT" in topology.port_defaults

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "MLFLOW_SOURCE=disabled",
        "MLFLOW_IMAGE=ghcr.io/mlflow/mlflow:v3.15.1",
        "MLFLOW_PORT=",
        "MLFLOW_ENDPOINT=",
        "MLFLOW_SCALE=",
        "MLFLOW_INIT_SCALE=",
        "MLFLOW_DB_NAME=mlflow",
        "MLFLOW_DB_USER=mlflow",
        "MLFLOW_DB_PASSWORD=",
        "MINIO_BUCKET_MLFLOW=mlflow",
    ):
        assert expected in env_example


def test_mlflow_track_membership_is_ml_eng_only() -> None:
    registry = load_tracks()

    assert is_in_track(
        registry.by_key["ml-eng"],
        "mlflow",
        always_on=registry.always_on,
    )
    assert is_in_track(
        registry.by_key["all"],
        "mlflow",
        always_on=registry.always_on,
    )
    for track_key in ("gen-ai-rag", "gen-ai-eng", "gen-ai-creative", "data-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "mlflow",
            always_on=registry.always_on,
        )


def test_mlflow_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["mlflow_source"] == "MLFLOW_SOURCE"
    assert mgr.collect_overrides(mlflow_source="container") == {
        "MLFLOW_SOURCE": "container",
    }


def test_mlflow_scale_generation_and_minio_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"MLFLOW_SOURCE": "disabled", "MINIO_SOURCE": "disabled"}
    assert sc._generate_mlflow_config() == {
        "MLFLOW_INIT_SCALE": "0",
        "MLFLOW_SCALE": "0",
        "MLFLOW_ENDPOINT": "",
        "MLFLOW_TRACKING_URI": "",
    }

    sc.service_sources = {"MLFLOW_SOURCE": "container", "MINIO_SOURCE": "container"}
    assert sc._generate_mlflow_config() == {
        "MLFLOW_INIT_SCALE": "1",
        "MLFLOW_SCALE": "1",
        "MLFLOW_ENDPOINT": "http://mlflow:5000",
        "MLFLOW_TRACKING_URI": "http://mlflow:5000",
    }

    sc.service_sources = {"MLFLOW_SOURCE": "container", "MINIO_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="MLflow requires MinIO"):
        sc._generate_mlflow_config()


def test_mlflow_compose_contract() -> None:
    compose = _compose()["services"]
    init = compose["mlflow-init"]
    service = compose["mlflow"]

    assert init["build"]["context"] == "./init"
    assert init["depends_on"]["supabase-db-init"]["condition"] == "service_completed_successfully"
    assert init["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
    assert init["environment"]["MLFLOW_DB_NAME"] == "${MLFLOW_DB_NAME:-mlflow}"
    assert "MINIO_ROOT_USER" not in init["environment"]

    assert service["image"] == "${PROJECT_NAME}-mlflow:local"
    assert service["build"] == {
        "context": ".",
        "dockerfile": "build/Dockerfile",
        "args": {"BASE_IMAGE": "${MLFLOW_IMAGE:-ghcr.io/mlflow/mlflow:v3.15.1}"},
    }
    assert service["ports"] == ["${HOST_BIND_IP:-}${MLFLOW_PORT}:5000"]
    assert service["depends_on"]["mlflow-init"]["condition"] == "service_completed_successfully"
    assert service["environment"]["MLFLOW_S3_ENDPOINT_URL"] == "http://minio:9000"
    assert service["environment"]["AWS_ACCESS_KEY_ID"] == "${MINIO_MLFLOW_ACCESS_KEY}"
    assert service["environment"]["AWS_SECRET_ACCESS_KEY"] == "${MINIO_MLFLOW_SECRET_KEY}"
    assert service["command"] == ["python", "atlas_server.py"]
    assert service["working_dir"] == "/opt/atlas"
    assert "volumes" not in service
    environment = service["environment"]
    database_uri = "postgresql://${MLFLOW_DB_USER_URI:?MLFLOW_DB_USER_URI is required}:${MLFLOW_DB_PASSWORD_URI:?MLFLOW_DB_PASSWORD_URI is required}@supabase-db:5432/${MLFLOW_DB_NAME_URI:?MLFLOW_DB_NAME_URI is required}"
    assert environment["_MLFLOW_SERVER_FILE_STORE"] == database_uri
    assert environment["_MLFLOW_SERVER_REGISTRY_STORE"] == database_uri
    assert environment["_MLFLOW_SERVER_ARTIFACT_ROOT"] == "mlflow-artifacts:/"
    assert environment["_MLFLOW_SERVER_ARTIFACT_DESTINATION"] == "s3://${MINIO_BUCKET_MLFLOW:-mlflow}"
    assert environment["_MLFLOW_SERVER_SERVE_ARTIFACTS"] == "true"
    assert "localhost:5000" in environment["MLFLOW_SERVER_ALLOWED_HOSTS"].split(",")


def test_minio_provisions_mlflow_bucket_and_scoped_credentials() -> None:
    minio_manifest = yaml.safe_load(
        (REPO_ROOT / "services" / "minio" / "service.yml").read_text()
    )
    env_vars = {entry["name"]: entry for entry in minio_manifest["env"]}

    assert env_vars["MINIO_BUCKET_MLFLOW"]["default"] == "mlflow"
    assert env_vars["MINIO_MLFLOW_ACCESS_KEY"]["secret"] is True
    assert env_vars["MINIO_MLFLOW_SECRET_KEY"]["secret"] is True

    minio_compose = yaml.safe_load(
        (REPO_ROOT / "services" / "minio" / "compose.yml").read_text()
    )
    minio_init_env = minio_compose["services"]["minio-init"]["environment"]
    assert minio_init_env["MINIO_BUCKET_MLFLOW"] == "${MINIO_BUCKET_MLFLOW}"
    assert minio_init_env["MINIO_MLFLOW_ACCESS_KEY"] == "${MINIO_MLFLOW_ACCESS_KEY}"
    assert minio_init_env["MINIO_MLFLOW_SECRET_KEY"] == "${MINIO_MLFLOW_SECRET_KEY}"

    script = (
        REPO_ROOT / "services" / "minio" / "init" / "scripts" / "init-minio.sh"
    ).read_text()
    assert "mlflow:MINIO_BUCKET_MLFLOW:MINIO_MLFLOW_ACCESS_KEY:MINIO_MLFLOW_SECRET_KEY" in script


def test_key_generator_creates_mlflow_credentials(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PROJECT_NAME=atlas-test\n"
        "MLFLOW_DB_PASSWORD=\n"
        "MINIO_MLFLOW_ACCESS_KEY=\n"
        "MINIO_MLFLOW_SECRET_KEY=\n"
    )

    results = KeyGenerator(str(tmp_path)).generate_missing_keys()
    generated = ConfigParser(str(tmp_path)).parse_env_file()

    assert results["MLFLOW_DB_PASSWORD"] is True
    assert results["MINIO_MLFLOW_ACCESS_KEY"] is True
    assert results["MINIO_MLFLOW_SECRET_KEY"] is True
    assert generated["MLFLOW_DB_PASSWORD"]
    assert generated["MINIO_MLFLOW_ACCESS_KEY"]
    assert generated["MINIO_MLFLOW_SECRET_KEY"]


def test_jupyterhub_receives_mlflow_tracking_uri_and_client() -> None:
    manifest = yaml.safe_load(
        (REPO_ROOT / "services" / "jupyterhub" / "service.yml").read_text()
    )
    compose = yaml.safe_load(
        (REPO_ROOT / "services" / "jupyterhub" / "compose.yml").read_text()
    )
    adaptation = manifest["runtime_adaptive"]["jupyterhub"]

    assert "mlflow" in adaptation["adapts_to"]
    assert adaptation["environment_adaptation"]["MLFLOW_TRACKING_URI"] == "${MLFLOW_TRACKING_URI}"
    assert (
        compose["services"]["jupyterhub"]["environment"]["MLFLOW_TRACKING_URI"]
        == "${MLFLOW_TRACKING_URI:-}"
    )
    assert "mlflow" in (REPO_ROOT / "services" / "jupyterhub" / "build" / "requirements.txt").read_text()


def test_mlflow_kong_route_only_when_container() -> None:
    from utils.kong_config_generator import KongConfigGenerator

    def _config(env: dict[str, str]) -> dict:
        cp = ConfigParser(str(REPO_ROOT))
        gen = KongConfigGenerator(cp)
        gen.load_environment_variables = lambda: setattr(gen, "env_vars", env)
        return gen.generate_kong_config()

    enabled = _config({"MLFLOW_SOURCE": "container"})
    disabled = _config({"MLFLOW_SOURCE": "disabled"})

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
    assert enabled_hosts["mlflow.localhost"]["name"] == "mlflow"
    assert enabled_hosts["mlflow.localhost"]["url"] == "http://mlflow:5000/"
    assert enabled_hosts["mlflow.localhost"]["routes"][0]["preserve_host"] is True
    assert {plugin["name"] for plugin in enabled_hosts["mlflow.localhost"]["plugins"]} >= {
        "basic-auth",
        "acl",
        "cors",
    }
    assert "mlflow.localhost" not in disabled_hosts


def test_mlflow_docs_describe_scope_and_notebook_smoke() -> None:
    readme = README.read_text()

    assert "MLFLOW_SOURCE=disabled" in readme
    assert "mlflow.localhost" in readme
    assert "MLFLOW_TRACKING_URI" in readme
    assert "MinIO-backed artifact" in readme
    assert "model promotion automations are out of scope" in readme
    assert "mlflow.start_run" in readme
