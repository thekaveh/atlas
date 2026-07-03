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
from utils.kong_config_generator import KongConfigGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "label-studio"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"
IMAGE = "heartexlabs/label-studio:1.23.0"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_label_studio_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "label-studio"
    assert manifest["category"] == "apps"
    assert manifest["containers"] == ["label-studio-init", "label-studio"]
    assert manifest["sources"]["var"] == "LABEL_STUDIO_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["supabase", "minio"]
    assert manifest["depends_on"].get("optional", []) == ["jupyterhub", "mlflow"]
    assert manifest["data_flow"]["calls"] == ["supabase", "minio"]

    images = {entry["var"]: entry for entry in manifest["images"]}
    assert images["LABEL_STUDIO_IMAGE"]["default"] == IMAGE

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["LABEL_STUDIO_SOURCE"]["default"] == "disabled"
    assert env_vars["LABEL_STUDIO_DB_NAME"]["default"] == "label_studio"
    assert env_vars["LABEL_STUDIO_DB_USER"]["default"] == "label_studio"
    assert env_vars["LABEL_STUDIO_USERNAME"]["default"] == "admin@atlas.local"
    assert env_vars["LABEL_STUDIO_DB_PASSWORD"]["secret"] is True
    assert env_vars["LABEL_STUDIO_PASSWORD"]["secret"] is True
    assert env_vars["LABEL_STUDIO_USER_TOKEN"]["secret"] is True
    assert env_vars["LABEL_STUDIO_SECRET_KEY"]["secret"] is True
    assert "default" not in env_vars["LABEL_STUDIO_PORT"]
    for auto_var in (
        "LABEL_STUDIO_INIT_SCALE",
        "LABEL_STUDIO_SCALE",
        "LABEL_STUDIO_ENDPOINT",
        "LABEL_STUDIO_API_URL",
    ):
        assert env_vars[auto_var]["auto_managed"] is True

    row = manifest["rows"][0]
    assert row["display_name"] == "Label Studio"
    assert row["source_var"] == "LABEL_STUDIO_SOURCE"
    assert row["port_var"] == "LABEL_STUDIO_PORT"
    assert row["scale_var"] == "LABEL_STUDIO_SCALE"
    assert row["alias"] == "label-studio.localhost"


def test_label_studio_topology_track_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "label-studio"]

    assert len(rows) == 1
    assert rows[0].category == "apps"
    assert rows[0].alias == "label-studio.localhost"
    assert "label-studio.localhost" in topology.aliases
    assert "LABEL_STUDIO_PORT" in topology.port_defaults

    registry = load_tracks()
    for track_key in ("ml-eng", "all"):
        assert is_in_track(
            registry.by_key[track_key],
            "label-studio",
            always_on=registry.always_on,
        )
    for track_key in ("gen-ai-rag", "gen-ai-eng", "gen-ai-creative", "data-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "label-studio",
            always_on=registry.always_on,
        )

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "LABEL_STUDIO_SOURCE=disabled",
        f"LABEL_STUDIO_IMAGE={IMAGE}",
        "LABEL_STUDIO_PORT=",
        "LABEL_STUDIO_ENDPOINT=",
        "LABEL_STUDIO_API_URL=",
        "LABEL_STUDIO_SCALE=",
        "LABEL_STUDIO_INIT_SCALE=",
        "LABEL_STUDIO_DB_NAME=label_studio",
        "LABEL_STUDIO_DB_USER=label_studio",
        "LABEL_STUDIO_DB_PASSWORD=",
        "LABEL_STUDIO_USERNAME=admin@atlas.local",
        "LABEL_STUDIO_PASSWORD=",
        "LABEL_STUDIO_USER_TOKEN=",
        "LABEL_STUDIO_SECRET_KEY=",
        "MINIO_BUCKET_LABEL_STUDIO=label-studio",
    ):
        assert expected in env_example


def test_label_studio_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["label_studio_source"] == "LABEL_STUDIO_SOURCE"
    assert mgr.collect_overrides(label_studio_source="container") == {
        "LABEL_STUDIO_SOURCE": "container",
    }


def test_label_studio_scale_generation_and_minio_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"LABEL_STUDIO_SOURCE": "disabled", "MINIO_SOURCE": "disabled"}
    assert sc._generate_label_studio_config() == {
        "LABEL_STUDIO_INIT_SCALE": "0",
        "LABEL_STUDIO_SCALE": "0",
        "LABEL_STUDIO_ENDPOINT": "",
        "LABEL_STUDIO_API_URL": "",
    }

    sc.service_sources = {"LABEL_STUDIO_SOURCE": "container", "MINIO_SOURCE": "container"}
    assert sc._generate_label_studio_config() == {
        "LABEL_STUDIO_INIT_SCALE": "1",
        "LABEL_STUDIO_SCALE": "1",
        "LABEL_STUDIO_ENDPOINT": "http://label-studio:8080",
        "LABEL_STUDIO_API_URL": "http://label-studio:8080",
    }

    sc.service_sources = {"LABEL_STUDIO_SOURCE": "container", "MINIO_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="Label Studio requires MinIO"):
        sc._generate_label_studio_config()


def test_label_studio_compose_contract() -> None:
    compose = _compose()["services"]
    init = compose["label-studio-init"]
    service = compose["label-studio"]

    assert init["build"]["context"] == "./init"
    assert init["depends_on"]["supabase-db"]["condition"] == "service_healthy"
    assert init["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
    assert init["environment"]["LABEL_STUDIO_DB_NAME"] == "${LABEL_STUDIO_DB_NAME:-label_studio}"
    assert init["environment"]["LABEL_STUDIO_DB_USER"] == "${LABEL_STUDIO_DB_USER:-label_studio}"
    assert init["environment"]["LABEL_STUDIO_DB_PASSWORD"] == "${LABEL_STUDIO_DB_PASSWORD}"

    assert service["image"] == f"${{LABEL_STUDIO_IMAGE:-{IMAGE}}}"
    assert service["ports"] == ["${HOST_BIND_IP:-}${LABEL_STUDIO_PORT}:8080"]
    assert service["depends_on"]["label-studio-init"]["condition"] == "service_completed_successfully"
    assert service["volumes"] == ["label-studio-data:/label-studio/data"]
    env = service["environment"]
    assert env["DJANGO_DB"] == "default"
    assert env["POSTGRE_NAME"] == "${LABEL_STUDIO_DB_NAME:-label_studio}"
    assert env["POSTGRE_USER"] == "${LABEL_STUDIO_DB_USER:-label_studio}"
    assert env["POSTGRE_PASSWORD"] == "${LABEL_STUDIO_DB_PASSWORD}"
    assert env["POSTGRE_HOST"] == "supabase-db"
    assert env["POSTGRE_PORT"] == "5432"
    assert env["LABEL_STUDIO_HOST"] == "http://label-studio.localhost:${KONG_HTTP_PORT:-63000}"
    assert env["CSRF_TRUSTED_ORIGINS"] == "http://label-studio.localhost:${KONG_HTTP_PORT:-63000}"
    assert env["USERNAME"] == "${LABEL_STUDIO_USERNAME:-admin@atlas.local}"
    assert env["PASSWORD"] == "${LABEL_STUDIO_PASSWORD}"
    assert env["USER_TOKEN"] == "${LABEL_STUDIO_USER_TOKEN}"
    assert env["SECRET_KEY"] == "${LABEL_STUDIO_SECRET_KEY}"
    assert env["DISABLE_SIGNUP_WITHOUT_LINK"] == "true"
    assert env["STORAGE_TYPE"] == "s3"
    assert env["STORAGE_AWS_ENDPOINT_URL"] == "http://minio:9000"
    assert env["STORAGE_AWS_BUCKET_NAME"] == "${MINIO_BUCKET_LABEL_STUDIO:-label-studio}"
    assert env["STORAGE_AWS_ACCESS_KEY_ID"] == "${MINIO_LABEL_STUDIO_ACCESS_KEY}"
    assert env["STORAGE_AWS_SECRET_ACCESS_KEY"] == "${MINIO_LABEL_STUDIO_SECRET_KEY}"
    assert env["STORAGE_AWS_S3_USE_SSL"] == "false"
    assert env["STORAGE_AWS_REGION_NAME"] == "${MINIO_REGION:-us-east-1}"


def test_minio_provisions_label_studio_bucket_and_scoped_credentials() -> None:
    minio_manifest = yaml.safe_load(
        (REPO_ROOT / "services" / "minio" / "service.yml").read_text()
    )
    env_vars = {entry["name"]: entry for entry in minio_manifest["env"]}

    assert env_vars["MINIO_BUCKET_LABEL_STUDIO"]["default"] == "label-studio"
    assert env_vars["MINIO_LABEL_STUDIO_ACCESS_KEY"]["secret"] is True
    assert env_vars["MINIO_LABEL_STUDIO_SECRET_KEY"]["secret"] is True

    minio_compose = yaml.safe_load(
        (REPO_ROOT / "services" / "minio" / "compose.yml").read_text()
    )
    minio_init_env = minio_compose["services"]["minio-init"]["environment"]
    assert minio_init_env["MINIO_BUCKET_LABEL_STUDIO"] == "${MINIO_BUCKET_LABEL_STUDIO}"
    assert minio_init_env["MINIO_LABEL_STUDIO_ACCESS_KEY"] == "${MINIO_LABEL_STUDIO_ACCESS_KEY}"
    assert minio_init_env["MINIO_LABEL_STUDIO_SECRET_KEY"] == "${MINIO_LABEL_STUDIO_SECRET_KEY}"

    script = (
        REPO_ROOT / "services" / "minio" / "init" / "scripts" / "init-minio.sh"
    ).read_text()
    assert (
        "label-studio:MINIO_BUCKET_LABEL_STUDIO:"
        "MINIO_LABEL_STUDIO_ACCESS_KEY:MINIO_LABEL_STUDIO_SECRET_KEY"
    ) in script


def test_key_generator_creates_label_studio_credentials(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PROJECT_NAME=atlas-test\n"
        "LABEL_STUDIO_DB_PASSWORD=\n"
        "LABEL_STUDIO_PASSWORD=\n"
        "LABEL_STUDIO_USER_TOKEN=\n"
        "LABEL_STUDIO_SECRET_KEY=\n"
        "MINIO_LABEL_STUDIO_ACCESS_KEY=\n"
        "MINIO_LABEL_STUDIO_SECRET_KEY=\n"
    )

    results = KeyGenerator(str(tmp_path)).generate_missing_keys()
    generated = ConfigParser(str(tmp_path)).parse_env_file()

    for key in (
        "LABEL_STUDIO_DB_PASSWORD",
        "LABEL_STUDIO_PASSWORD",
        "LABEL_STUDIO_USER_TOKEN",
        "LABEL_STUDIO_SECRET_KEY",
        "MINIO_LABEL_STUDIO_ACCESS_KEY",
        "MINIO_LABEL_STUDIO_SECRET_KEY",
    ):
        assert results[key] is True
        assert generated[key]


def test_jupyterhub_receives_label_studio_url_and_client() -> None:
    manifest = yaml.safe_load(
        (REPO_ROOT / "services" / "jupyterhub" / "service.yml").read_text()
    )
    compose = yaml.safe_load(
        (REPO_ROOT / "services" / "jupyterhub" / "compose.yml").read_text()
    )
    adaptation = manifest["runtime_adaptive"]["jupyterhub"]

    assert "label-studio" in adaptation["adapts_to"]
    assert adaptation["environment_adaptation"]["LABEL_STUDIO_URL"] == "${LABEL_STUDIO_ENDPOINT}"
    assert adaptation["environment_adaptation"]["LABEL_STUDIO_API_URL"] == "${LABEL_STUDIO_API_URL}"
    assert adaptation["environment_adaptation"]["LABEL_STUDIO_API_KEY"] == "${LABEL_STUDIO_USER_TOKEN}"
    assert (
        compose["services"]["jupyterhub"]["environment"]["LABEL_STUDIO_URL"]
        == "${LABEL_STUDIO_ENDPOINT:-}"
    )
    assert (
        compose["services"]["jupyterhub"]["environment"]["LABEL_STUDIO_API_URL"]
        == "${LABEL_STUDIO_API_URL:-}"
    )
    assert (
        compose["services"]["jupyterhub"]["environment"]["LABEL_STUDIO_API_KEY"]
        == "${LABEL_STUDIO_USER_TOKEN:-}"
    )
    requirements = (
        REPO_ROOT / "services" / "jupyterhub" / "build" / "requirements.txt"
    ).read_text()
    assert "label-studio-sdk" in requirements


def test_label_studio_kong_route_only_when_container(tmp_path: Path) -> None:
    def _hosts(env_text: str) -> dict[str, dict]:
        env_path = tmp_path / ".env"
        env_path.write_text(
            env_text
            + "\nDASHBOARD_USERNAME=u\nDASHBOARD_PASSWORD=p\nKONG_HTTP_PORT=64000\n",
            encoding="utf-8",
        )
        gen = KongConfigGenerator(ConfigParser(str(tmp_path)))
        config = gen.generate_kong_config()
        return {
            host: service
            for service in config["services"]
            for route in service.get("routes", [])
            for host in route.get("hosts") or []
        }

    enabled_hosts = _hosts("LABEL_STUDIO_SOURCE=container\n")
    disabled_hosts = _hosts("LABEL_STUDIO_SOURCE=disabled\n")

    assert enabled_hosts["label-studio.localhost"]["name"] == "label-studio"
    assert enabled_hosts["label-studio.localhost"]["url"] == "http://label-studio:8080/"
    assert enabled_hosts["label-studio.localhost"]["routes"][0]["preserve_host"] is True
    assert {plugin["name"] for plugin in enabled_hosts["label-studio.localhost"]["plugins"]} >= {
        "basic-auth",
        "acl",
        "cors",
    }
    assert "label-studio.localhost" not in disabled_hosts


def test_label_studio_docs_describe_storage_exports_and_guardrails() -> None:
    readme = README.read_text()

    for expected in (
        "LABEL_STUDIO_SOURCE=disabled",
        "label-studio.localhost",
        "Track: `ml-eng`",
        "Category: `apps`",
        "heartexlabs/label-studio:1.23.0",
        "Postgres",
        "MinIO",
        "S3-compatible",
        "label-studio-sdk",
        "MLflow",
        "Weaviate",
        "SSO",
        "DISABLE_SIGNUP_WITHOUT_LINK",
        "project-specific",
    ):
        assert expected in readme
