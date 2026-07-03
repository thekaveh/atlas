from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from services.service_config import ServiceConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "zeppelin"
COMPOSE = SERVICE_DIR / "compose.yml"
DOCKERFILE = SERVICE_DIR / "build" / "Dockerfile"
MANIFEST = SERVICE_DIR / "service.yml"
INIT_SCRIPT = SERVICE_DIR / "init" / "scripts" / "seed-spark-interpreter.py"
README = SERVICE_DIR / "README.md"
NOTEBOOK = SERVICE_DIR / "notebooks" / "spark_basics.zpln"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("zeppelin_seed", INIT_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zeppelin_manifest_declares_lakehouse_init_family() -> None:
    manifest = _manifest()

    assert manifest["containers"] == ["zeppelin", "zeppelin-init"]
    assert {image["var"] for image in manifest["images"]} == {
        "ZEPPELIN_IMAGE",
        "ZEPPELIN_INIT_IMAGE",
    }
    assert "iceberg-rest" in manifest["depends_on"]["optional"]
    assert "trino" in manifest["depends_on"]["optional"]
    assert manifest["data_flow"]["calls"] == [
        "spark",
        "supabase",
        "minio",
        "iceberg-rest",
        "redpanda",
        "trino",
    ]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["ZEPPELIN_INIT_SCALE"]["auto_managed"] is True
    assert manifest["runtime_sc"]["zeppelin-init"]["container"]["scale"] == 1
    assert manifest["runtime_sc"]["zeppelin-init"]["disabled"]["scale"] == 0


def test_zeppelin_scale_generation_covers_init_companion() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {"ZEPPELIN_SOURCE": "disabled", "SPARK_SOURCE": "disabled"}
    assert sc._generate_zeppelin_config() == {
        "ZEPPELIN_SCALE": "0",
        "ZEPPELIN_INIT_SCALE": "0",
    }

    sc.service_sources = {"ZEPPELIN_SOURCE": "container", "SPARK_SOURCE": "container"}
    assert sc._generate_zeppelin_config() == {
        "ZEPPELIN_SCALE": "1",
        "ZEPPELIN_INIT_SCALE": "1",
    }


def test_zeppelin_compose_builds_spark_enabled_image_and_init_companion() -> None:
    services = _compose()["services"]
    zeppelin = services["zeppelin"]
    init = services["zeppelin-init"]

    assert zeppelin["build"]["context"] == "./build"
    assert zeppelin["build"]["args"]["BASE_IMAGE"] == "${ZEPPELIN_IMAGE:-apache/zeppelin:0.12.1}"
    assert zeppelin["build"]["args"]["SPARK_IMAGE"] == "${SPARK_IMAGE:-apache/spark:4.1.2}"
    assert zeppelin["image"] == "${PROJECT_NAME}-zeppelin:local"
    assert zeppelin["environment"]["JAVA_HOME"] == "/opt/java/openjdk"
    assert zeppelin["environment"]["SPARK_HOME"] == "/opt/spark"
    assert zeppelin["environment"]["SPARK_MASTER"] == "spark://spark-master:7077"
    assert "spark.sql.catalog.lakehouse.uri=http://iceberg-rest:8181" in zeppelin[
        "environment"
    ]["SPARK_SUBMIT_OPTIONS"]
    assert "MINIO_ICEBERG_ACCESS_KEY" in zeppelin["environment"]
    assert zeppelin["environment"]["TRINO_SOURCE"] == "${TRINO_SOURCE:-disabled}"
    assert zeppelin["environment"]["TRINO_JDBC_URL"] == "jdbc:trino://trino:8080/lakehouse"
    assert zeppelin["environment"]["TRINO_JDBC_DRIVER"] == "io.trino.jdbc.TrinoDriver"
    assert zeppelin["environment"]["TRINO_JDBC_DEPENDENCY"] == "io.trino:trino-jdbc:482"

    assert init["image"] == "${ZEPPELIN_INIT_IMAGE:-python:3.12-alpine}"
    assert init["deploy"]["replicas"] == "${ZEPPELIN_INIT_SCALE:-0}"
    assert init["depends_on"]["zeppelin"]["condition"] == "service_healthy"
    assert init["depends_on"]["spark-init"]["condition"] == "service_completed_successfully"
    assert init["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
    assert init["environment"]["TRINO_SOURCE"] == "${TRINO_SOURCE:-disabled}"
    assert init["environment"]["TRINO_ENDPOINT"] == "http://trino:8080"
    assert init["environment"]["TRINO_JDBC_URL"] == "jdbc:trino://trino:8080/lakehouse"
    assert init["environment"]["TRINO_JDBC_DRIVER"] == "io.trino.jdbc.TrinoDriver"
    assert init["environment"]["TRINO_JDBC_DEPENDENCY"] == "io.trino:trino-jdbc:482"
    assert "ports" not in init
    assert init["entrypoint"] == ["python", "/scripts/seed-spark-interpreter.py"]


def test_zeppelin_dockerfile_bundles_spark_and_lakehouse_jars() -> None:
    dockerfile = DOCKERFILE.read_text()

    assert "FROM ${SPARK_IMAGE} AS spark-runtime" in dockerfile
    assert "FROM ${BASE_IMAGE}" in dockerfile
    assert "iceberg-spark-runtime-4.1_2.13" in dockerfile
    assert "iceberg-aws-bundle" in dockerfile
    assert "hadoop-aws" in dockerfile
    assert "sha512sum -c -" in dockerfile
    assert "COPY --from=spark-runtime /opt/spark /opt/spark" in dockerfile
    assert "openjdk-17-jre-headless" in dockerfile
    assert "JAVA_HOME=/opt/java/openjdk" in dockerfile
    assert "SPARK_HOME=/opt/spark" in dockerfile


def test_zeppelin_seed_properties_are_standalone_spark_not_connect() -> None:
    seed = _load_seed_module()

    props = seed.build_atlas_properties(
        {
            "SPARK_HOME": "/opt/spark",
            "JAVA_HOME": "/opt/java/openjdk",
            "SPARK_MASTER": "spark://spark-master:7077",
            "MINIO_ENDPOINT": "http://minio:9000",
            "MINIO_REGION": "us-east-1",
            "MINIO_ROOT_USER": "root-user",
            "MINIO_ROOT_PASSWORD": "root-password",
            "MINIO_ICEBERG_ACCESS_KEY": "iceberg-user",
            "MINIO_ICEBERG_SECRET_KEY": "iceberg-password",
            "ICEBERG_REST_URI": "http://iceberg-rest:8181",
            "MINIO_BUCKET_ICEBERG_LAKEHOUSE": "lakehouse",
        }
    )

    assert props["JAVA_HOME"] == "/opt/java/openjdk"
    assert props["SPARK_HOME"] == "/opt/spark"
    assert props["spark.master"] == "spark://spark-master:7077"
    assert props["zeppelin.spark.enableSupportedVersionCheck"] == "false"
    assert props["spark.submit.deployMode"] == "client"
    assert props["spark.driver.bindAddress"] == "0.0.0.0"
    assert props["spark.driver.host"] == "zeppelin"
    assert props["spark.hadoop.fs.s3a.endpoint"] == "http://minio:9000"
    assert props["spark.hadoop.fs.s3a.access.key"] == "root-user"
    assert props["spark.eventLog.dir"] == "s3a://spark-history/"
    assert props["spark.sql.catalog.lakehouse.uri"] == "http://iceberg-rest:8181"
    assert props["spark.sql.catalog.lakehouse.warehouse"] == "s3a://lakehouse/"
    assert props["spark.sql.catalog.lakehouse.s3.access-key-id"] == "iceberg-user"
    assert "spark.remote" not in props


def test_zeppelin_seed_merge_preserves_non_atlas_properties_and_restarts() -> None:
    seed = _load_seed_module()
    existing = {
        "spark.executor.memory": {"name": "spark.executor.memory", "value": "2g", "type": "string"},
        "spark.master": {"name": "spark.master", "value": "local[*]", "type": "string"},
        "spark.remote": {"name": "spark.remote", "value": "sc://spark-connect:15002", "type": "string"},
        "SPARK_REMOTE": {"name": "SPARK_REMOTE", "value": "sc://spark-connect:15002", "type": "string"},
    }

    merged, changed = seed.merge_properties(
        existing,
        {
            "spark.master": "spark://spark-master:7077",
            "spark.sql.catalog.lakehouse.uri": "http://iceberg-rest:8181",
        },
    )

    assert changed is True
    assert merged["spark.executor.memory"]["value"] == "2g"
    assert merged["spark.master"]["value"] == "spark://spark-master:7077"
    assert "spark.remote" not in merged
    assert "SPARK_REMOTE" not in merged
    assert merged["spark.sql.catalog.lakehouse.uri"] == {
        "name": "spark.sql.catalog.lakehouse.uri",
        "value": "http://iceberg-rest:8181",
        "type": "string",
    }
    assert seed.needs_restart(existing, merged) is True
    assert seed.needs_restart(merged, merged) is False


def test_zeppelin_trino_jdbc_seed_contract_is_version_pinned() -> None:
    seed = _load_seed_module()

    props = seed.build_trino_jdbc_properties(
        {
            "TRINO_JDBC_URL": "jdbc:trino://trino:8080/lakehouse",
            "TRINO_JDBC_USER": "atlas",
            "TRINO_JDBC_DRIVER": "io.trino.jdbc.TrinoDriver",
        }
    )
    setting = seed.build_trino_jdbc_setting({"properties": {}})

    assert seed.should_seed_trino({"TRINO_SOURCE": "disabled"}) is False
    assert seed.should_seed_trino({"TRINO_SOURCE": "container"}) is True
    assert props == {
        "default.driver": "io.trino.jdbc.TrinoDriver",
        "default.url": "jdbc:trino://trino:8080/lakehouse",
        "default.user": "atlas",
    }
    assert setting["name"] == "trino"
    assert setting["group"] == "jdbc"
    assert setting["dependencies"] == [
        {
            "groupArtifactVersion": "io.trino:trino-jdbc:482",
            "local": False,
        }
    ]
    assert setting["properties"]["default.url"]["value"] == "jdbc:trino://trino:8080/lakehouse"


def test_zeppelin_trino_jdbc_seed_preserves_user_owned_properties() -> None:
    seed = _load_seed_module()

    setting = seed.build_trino_jdbc_setting(
        {
            "properties": {
                "custom.session-property": {
                    "name": "custom.session-property",
                    "value": "kept",
                    "type": "string",
                },
                "spark.remote": {
                    "name": "spark.remote",
                    "value": "user-owned-jdbc-property",
                    "type": "string",
                },
            },
            "dependencies": [
                {
                    "groupArtifactVersion": "example:custom-driver:1",
                    "local": False,
                }
            ],
        }
    )

    assert setting["properties"]["custom.session-property"]["value"] == "kept"
    assert setting["properties"]["spark.remote"]["value"] == "user-owned-jdbc-property"
    assert {
        "groupArtifactVersion": "example:custom-driver:1",
        "local": False,
    } in setting["dependencies"]


def test_zeppelin_starter_notebook_includes_trino_metadata_smoke() -> None:
    notebook = json.loads(NOTEBOOK.read_text())
    texts = "\n".join(paragraph.get("text") or "" for paragraph in notebook["paragraphs"])

    assert "%trino" in texts
    assert "SHOW CATALOGS" in texts
    assert "SHOW SCHEMAS FROM lakehouse" in texts
    assert "TRINO_SOURCE=container" in texts


def test_zeppelin_docs_describe_zero_touch_lakehouse_seed() -> None:
    readme = README.read_text()

    assert "zeppelin-init" in readme
    assert "spark.master=spark://spark-master:7077" in readme
    assert "zeppelin.spark.enableSupportedVersionCheck=false" in readme
    assert "SHOW NAMESPACES IN lakehouse" in readme
    assert "%trino" in readme
    assert "io.trino:trino-jdbc:482" in readme
    assert "jdbc:trino://trino:8080/lakehouse" in readme
    assert "spark.remote=sc://spark-connect:15002" not in readme
