from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.kong_config_generator import KongConfigGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "trino"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _kong_hosts(env_text: str) -> set[str]:
    return {
        host
        for service in _kong_services(env_text)
        for route in service.get("routes", [])
        for host in route.get("hosts", [])
    }


def _kong_services(env_text: str) -> list[dict]:
    parser = ConfigParser(str(REPO_ROOT))
    parser.parse_env_file = MagicMock(return_value={
        "KONG_HTTP_PORT": "63000",
        "TRINO_SOURCE": "disabled",
        **dict(line.split("=", 1) for line in env_text.splitlines() if "=" in line),
    })
    gen = KongConfigGenerator(parser)
    return gen.generate_kong_config()["services"]


def test_trino_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "trino"
    assert manifest["category"] == "data"
    assert manifest["containers"] == ["trino"]
    assert manifest["sources"]["var"] == "TRINO_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["minio", "iceberg-rest"]
    assert manifest["depends_on"]["optional"] == [
        "spark",
        "zeppelin",
        "jupyterhub",
        "airflow",
    ]
    assert manifest["data_flow"]["calls"] == ["iceberg-rest", "minio"]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["TRINO_SOURCE"]["default"] == "disabled"
    assert env_vars["TRINO_SCALE"]["auto_managed"] is True
    assert "default" not in env_vars["TRINO_PORT"]

    images = {entry["var"]: entry for entry in manifest["images"]}
    assert images["TRINO_IMAGE"]["default"] == "trinodb/trino:482"
    assert images["TRINO_IMAGE"]["container"] == "trino"

    row = manifest["rows"][0]
    assert row["display_name"] == "Trino"
    assert row["source_var"] == "TRINO_SOURCE"
    assert row["port_var"] == "TRINO_PORT"
    assert row["scale_var"] == "TRINO_SCALE"
    assert row["alias"] == "trino.localhost"


def test_trino_compose_catalog_contract() -> None:
    service = _compose()["services"]["trino"]

    assert service["image"] == "${TRINO_IMAGE:-trinodb/trino:482}"
    assert service["container_name"] == "${PROJECT_NAME}-trino"
    assert service["ports"] == ["${HOST_BIND_IP:-}${TRINO_PORT}:8080"]
    assert service["deploy"]["replicas"] == "${TRINO_SCALE:-0}"
    assert service["depends_on"]["iceberg-rest"]["condition"] == "service_healthy"
    assert service["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
    assert "./catalog:/etc/trino/catalog:ro" in service["volumes"]

    catalog = (SERVICE_DIR / "catalog" / "lakehouse.properties").read_text()
    assert "connector.name=iceberg" in catalog
    assert "iceberg.catalog.type=rest" in catalog
    assert "iceberg.rest-catalog.uri=http://iceberg-rest:8181" in catalog
    assert "iceberg.rest-catalog.warehouse=s3://lakehouse/" in catalog
    assert "fs.s3.enabled=true" in catalog
    assert "s3.endpoint=http://minio:9000" in catalog
    assert "s3.region=${ENV:MINIO_REGION}" in catalog
    assert "s3.path-style-access=true" in catalog
    assert "s3.aws-access-key=${ENV:MINIO_ICEBERG_ACCESS_KEY}" in catalog
    assert "s3.aws-secret-key=${ENV:MINIO_ICEBERG_SECRET_KEY}" in catalog
    assert "MINIO_ROOT" not in catalog


def test_trino_topology_alias_env_and_track_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "trino"]

    assert len(rows) == 1
    assert rows[0].category == "data"
    assert rows[0].alias == "trino.localhost"
    assert "trino.localhost" in topology.aliases
    assert "TRINO_PORT" in topology.port_defaults

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "TRINO_SOURCE=disabled",
        "TRINO_IMAGE=trinodb/trino:482",
        "TRINO_PORT=",
        "TRINO_SCALE=",
    ):
        assert expected in env_example

    registry = load_tracks()
    assert is_in_track(registry.by_key["data-eng"], "trino", always_on=registry.always_on)
    assert is_in_track(registry.by_key["all"], "trino", always_on=registry.always_on)
    for track_key in ("gen-ai-rag", "gen-ai-eng", "gen-ai-creative", "ml-eng"):
        assert not is_in_track(registry.by_key[track_key], "trino", always_on=registry.always_on)


def test_trino_source_cli_mapping_and_service_config_gate() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))
    assert mgr.source_mapping["trino_source"] == "TRINO_SOURCE"
    assert mgr.collect_overrides(trino_source="container") == {"TRINO_SOURCE": "container"}

    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {
        "TRINO_SOURCE": "disabled",
        "MINIO_SOURCE": "disabled",
        "ICEBERG_REST_SOURCE": "disabled",
    }
    assert sc._generate_trino_config() == {"TRINO_SCALE": "0"}

    sc.service_sources = {
        "TRINO_SOURCE": "container",
        "MINIO_SOURCE": "container",
        "ICEBERG_REST_SOURCE": "container",
    }
    assert sc._generate_trino_config() == {"TRINO_SCALE": "1"}

    sc.service_sources = {
        "TRINO_SOURCE": "container",
        "MINIO_SOURCE": "disabled",
        "ICEBERG_REST_SOURCE": "container",
    }
    with pytest.raises(ValueError, match="Trino requires MinIO"):
        sc._generate_trino_config()

    sc.service_sources = {
        "TRINO_SOURCE": "container",
        "MINIO_SOURCE": "container",
        "ICEBERG_REST_SOURCE": "disabled",
    }
    with pytest.raises(ValueError, match="Trino requires Iceberg REST"):
        sc._generate_trino_config()


def test_trino_kong_route_and_docs_contract() -> None:
    enabled_hosts = _kong_hosts("TRINO_SOURCE=container\n")
    disabled_hosts = _kong_hosts("TRINO_SOURCE=disabled\n")

    assert "trino.localhost" in enabled_hosts
    assert "trino.localhost" not in disabled_hosts

    services = _kong_services("TRINO_SOURCE=container\n")
    trino_service = next(service for service in services if service["name"] == "trino")
    assert trino_service["url"] == "http://trino:8080/"
    assert trino_service["routes"][0]["preserve_host"] is True
    assert trino_service["routes"][0]["hosts"] == ["trino.localhost"]
    plugins = {plugin["name"]: plugin for plugin in trino_service["plugins"]}
    assert {"cors", "basic-auth", "acl"} <= set(plugins)
    assert plugins["acl"]["config"]["allow"] == ["dashboard_user"]

    readme = README.read_text()
    for expected in (
        "TRINO_SOURCE=disabled",
        "trino.localhost",
        "lakehouse.bronze",
        "%trino",
        "http://trino:8080",
        "jdbc:trino://trino:8080/lakehouse",
        "io.trino.jdbc.TrinoDriver",
        "io.trino:trino-jdbc:482",
        "trino.dbapi.connect",
        "CREATE TABLE lakehouse.gold.atlas_trino_ctas_smoke",
        "TRINO_PORT",
        "Iceberg REST",
        "MinIO",
        "trinodb/trino:482",
    ):
        assert expected in readme
    assert "For a future seeded interpreter" not in readme
    assert "Seed a Zeppelin JDBC interpreter" not in readme
