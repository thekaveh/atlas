from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.manifests import load_manifests
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.key_generator import KeyGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_DIR = REPO_ROOT / "services" / "iceberg-rest"


def _manifest() -> dict:
    return yaml.safe_load((SERVICE_DIR / "service.yml").read_text())


def test_iceberg_rest_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "iceberg-rest"
    assert manifest["category"] == "data"
    assert manifest["sources"]["var"] == "ICEBERG_REST_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {opt["id"] for opt in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["minio", "supabase"]
    assert manifest["data_flow"]["calls"] == ["minio", "supabase"]

    row = manifest["rows"][0]
    assert row["display_name"] == "Apache Iceberg REST Catalog"
    assert row["source_var"] == "ICEBERG_REST_SOURCE"
    assert row["port_var"] == "ICEBERG_REST_PORT"
    assert row.get("alias") in ("", None)


def test_iceberg_rest_topology_is_internal_only_data_service() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "iceberg-rest"]

    assert len(rows) == 1
    assert rows[0].category == "data"
    assert rows[0].alias is None
    assert "iceberg-rest.localhost" not in topology.aliases
    assert "ICEBERG_REST_PORT" in topology.port_defaults


def test_iceberg_rest_track_membership_is_data_eng_only() -> None:
    registry = load_tracks()

    assert is_in_track(
        registry.by_key["data-eng"],
        "iceberg-rest",
        always_on=registry.always_on,
    )
    assert is_in_track(
        registry.by_key["all"],
        "iceberg-rest",
        always_on=registry.always_on,
    )
    for track_key in ("gen-ai-rag", "gen-ai-eng", "gen-ai-creative", "ml-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "iceberg-rest",
            always_on=registry.always_on,
        )


def test_iceberg_rest_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))
    assert mgr.source_mapping["iceberg_rest_source"] == "ICEBERG_REST_SOURCE"
    assert mgr.collect_overrides(iceberg_rest_source="container") == {
        "ICEBERG_REST_SOURCE": "container",
    }


def test_iceberg_rest_scale_generation_and_minio_gate() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"ICEBERG_REST_SOURCE": "disabled", "MINIO_SOURCE": "disabled"}
    assert sc._generate_iceberg_rest_config() == {
        "ICEBERG_REST_SCALE": "0",
        "ICEBERG_REST_INIT_SCALE": "0",
    }

    sc.service_sources = {"ICEBERG_REST_SOURCE": "container", "MINIO_SOURCE": "container"}
    assert sc._generate_iceberg_rest_config() == {
        "ICEBERG_REST_SCALE": "1",
        "ICEBERG_REST_INIT_SCALE": "1",
    }

    sc.service_sources = {"ICEBERG_REST_SOURCE": "container", "MINIO_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="Iceberg REST Catalog requires MinIO"):
        sc._generate_iceberg_rest_config()


def test_iceberg_rest_env_example_contract() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text()

    for expected in (
        "ICEBERG_REST_SOURCE=disabled",
        "ICEBERG_REST_PORT=",
        "ICEBERG_REST_IMAGE=apache/iceberg-rest-fixture:1.10.1",
        "ICEBERG_REST_POSTGRES_JDBC_VERSION=42.7.12",
        "ICEBERG_REST_POSTGRES_JDBC_SHA512=3759e7160591863e5100361298943df9",
        "ICEBERG_DB_USER=iceberg",
        "ICEBERG_DB_PASSWORD=",
        "MINIO_BUCKET_ICEBERG_LAKEHOUSE=lakehouse",
        "MINIO_BUCKET_ICEBERG_JARS=jars",
        "MINIO_BUCKET_ICEBERG_CHECKPOINTS=checkpoints",
        "MINIO_BUCKET_ICEBERG_LANDING=landing",
        "MINIO_ICEBERG_ACCESS_KEY=",
        "MINIO_ICEBERG_SECRET_KEY=",
    ):
        assert expected in env_example


def test_iceberg_rest_key_generation_covers_db_and_minio_credentials(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PROJECT_NAME=atlas-test",
                "ICEBERG_DB_PASSWORD=",
                "MINIO_ICEBERG_ACCESS_KEY=",
                "MINIO_ICEBERG_SECRET_KEY=",
            ]
        )
        + "\n"
    )

    generator = KeyGenerator(str(tmp_path))
    results = generator.generate_missing_keys()
    generated = ConfigParser(str(tmp_path)).parse_env_file()

    assert results["ICEBERG_DB_PASSWORD"] is True
    assert results["MINIO_ICEBERG_ACCESS_KEY"] is True
    assert results["MINIO_ICEBERG_SECRET_KEY"] is True
    assert generated["ICEBERG_DB_PASSWORD"]
    assert generated["MINIO_ICEBERG_ACCESS_KEY"]
    assert generated["MINIO_ICEBERG_SECRET_KEY"]


def test_minio_init_provisions_iceberg_buckets_and_scoped_account() -> None:
    script = (REPO_ROOT / "services" / "minio" / "init" / "scripts" / "init-minio.sh").read_text()

    assert "iceberg:MINIO_BUCKET_ICEBERG_LAKEHOUSE:MINIO_ICEBERG_ACCESS_KEY:MINIO_ICEBERG_SECRET_KEY" in script
    for bucket_var in (
        "MINIO_BUCKET_ICEBERG_JARS",
        "MINIO_BUCKET_ICEBERG_CHECKPOINTS",
        "MINIO_BUCKET_ICEBERG_LANDING",
    ):
        assert bucket_var in script


def test_iceberg_rest_compose_contract() -> None:
    compose = yaml.safe_load((SERVICE_DIR / "compose.yml").read_text())
    services = compose["services"]

    assert "iceberg-rest" in services
    assert "iceberg-rest-init" in services

    rest = services["iceberg-rest"]
    assert rest["build"]["context"] == "./build"
    assert rest["build"]["args"]["BASE_IMAGE"] == "${ICEBERG_REST_IMAGE:-apache/iceberg-rest-fixture:1.10.1}"
    assert rest["build"]["args"]["POSTGRES_JDBC_VERSION"] == "${ICEBERG_REST_POSTGRES_JDBC_VERSION:-42.7.12}"
    assert rest["build"]["args"]["POSTGRES_JDBC_SHA512"].startswith(
        "${ICEBERG_REST_POSTGRES_JDBC_SHA512:-3759e7160591863e5100361298943df9"
    )
    assert rest["image"] == "${PROJECT_NAME}-iceberg-rest:local"
    assert rest["ports"] == ["${HOST_BIND_IP:-}${ICEBERG_REST_PORT}:8181"]
    assert rest["depends_on"]["iceberg-rest-init"]["condition"] == "service_completed_successfully"
    assert rest["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"

    env = rest["environment"]
    assert env["CATALOG_CATALOG__IMPL"] == "org.apache.iceberg.jdbc.JdbcCatalog"
    assert env["CATALOG_URI"] == "jdbc:postgresql://supabase-db:5432/iceberg"
    assert env["CATALOG_WAREHOUSE"] == "s3://lakehouse/"
    assert env["CATALOG_IO__IMPL"] == "org.apache.iceberg.aws.s3.S3FileIO"
    assert env["CATALOG_S3_ENDPOINT"] == "http://minio:9000"
    assert env["CATALOG_S3_PATH__STYLE__ACCESS"] == "true"
    assert env["AWS_ACCESS_KEY_ID"] == "${MINIO_ICEBERG_ACCESS_KEY}"
    assert env["AWS_SECRET_ACCESS_KEY"] == "${MINIO_ICEBERG_SECRET_KEY}"


def test_iceberg_rest_build_makes_postgres_driver_readable() -> None:
    dockerfile = (SERVICE_DIR / "build" / "Dockerfile").read_text()

    assert "USER root" in dockerfile
    assert (
        "ARG POSTGRES_JDBC_SHA512="
        "3759e7160591863e5100361298943df94488a6e4ee03936d20723638142fe8038"
        "c9a6a47fa8ee7e424f4ef09bb351edc89dfe2ae4acdfc0f92699a8b00196c5c"
    ) in dockerfile
    assert "postgresql-${POSTGRES_JDBC_VERSION}.jar" in dockerfile
    assert "curl -fsSL" in dockerfile
    assert "sha512sum -c -" in dockerfile
    assert "ADD http" not in dockerfile
    assert "chmod 0644 /usr/lib/iceberg-rest/postgresql.jar" in dockerfile
    assert "chown iceberg:iceberg /usr/lib/iceberg-rest/postgresql.jar" in dockerfile
    assert "USER iceberg" in dockerfile
    assert "org.apache.iceberg.rest.RESTCatalogServer" in dockerfile


def test_iceberg_rest_manifest_loads_with_real_services() -> None:
    manifests = {manifest.name: manifest for manifest in load_manifests(REPO_ROOT / "services")}

    assert "iceberg-rest" in manifests
    assert manifests["iceberg-rest"].category == "data"
