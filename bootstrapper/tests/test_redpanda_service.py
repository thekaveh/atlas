from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.kong_config_generator import KongConfigGenerator
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "redpanda"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"
SPARK_DOCKERFILE = REPO_ROOT / "services" / "spark" / "build" / "Dockerfile"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _service_manifest(service: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "services" / service / "service.yml").read_text())


def _service_compose(service: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "services" / service / "compose.yml").read_text())


def _kong_services(env_text: str) -> list[dict]:
    parser = ConfigParser(str(REPO_ROOT))
    parser.parse_env_file = MagicMock(return_value={
        "KONG_HTTP_PORT": "63000",
        "REDPANDA_SOURCE": "disabled",
        **dict(line.split("=", 1) for line in env_text.splitlines() if "=" in line),
    })
    gen = KongConfigGenerator(parser)
    return gen.generate_kong_config()["services"]


def test_redpanda_manifest_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "redpanda"
    assert manifest["category"] == "data"
    assert manifest["containers"] == ["redpanda", "redpanda-init", "redpanda-console"]
    assert manifest["sources"]["var"] == "REDPANDA_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == []
    assert manifest["depends_on"]["optional"] == [
        "spark",
        "jupyterhub",
        "zeppelin",
        "airflow",
        "iceberg-rest",
        "minio",
    ]
    assert manifest["data_flow"]["calls"] == []

    images = {entry["var"]: entry for entry in manifest["images"]}
    assert images["REDPANDA_IMAGE"]["default"] == "docker.redpanda.com/redpandadata/redpanda:v26.1.12"
    assert images["REDPANDA_CONSOLE_IMAGE"]["default"] == "docker.redpanda.com/redpandadata/console:v3.8.0"

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["REDPANDA_SOURCE"]["default"] == "disabled"
    assert "default" not in env_vars["REDPANDA_KAFKA_PORT"]
    assert "default" not in env_vars["REDPANDA_CONSOLE_PORT"]
    assert env_vars["REDPANDA_DEMO_TOPICS"]["default"] == "atlas_stream_events"
    assert env_vars["REDPANDA_SCALE"]["auto_managed"] is True
    assert env_vars["REDPANDA_INIT_SCALE"]["auto_managed"] is True
    assert env_vars["REDPANDA_CONSOLE_SCALE"]["auto_managed"] is True
    assert env_vars["REDPANDA_BROKERS"]["auto_managed"] is True
    assert env_vars["SPARK_KAFKA_BOOTSTRAP_SERVERS"]["auto_managed"] is True

    row = manifest["rows"][0]
    assert row["display_name"] == "Redpanda Console"
    assert row["source_var"] == "REDPANDA_SOURCE"
    assert row["port_var"] == "REDPANDA_CONSOLE_PORT"
    assert row["scale_var"] == "REDPANDA_CONSOLE_SCALE"
    assert row["alias"] == "redpanda.localhost"


def test_redpanda_compose_and_topic_init_contract() -> None:
    compose = _compose()["services"]
    broker = compose["redpanda"]
    console = compose["redpanda-console"]
    init = compose["redpanda-init"]

    assert broker["image"] == "${REDPANDA_IMAGE:-docker.redpanda.com/redpandadata/redpanda:v26.1.12}"
    assert broker["container_name"] == "${PROJECT_NAME}-redpanda"
    assert broker["deploy"]["replicas"] == "${REDPANDA_SCALE:-0}"
    assert broker["ports"] == ["${HOST_BIND_IP:-}${REDPANDA_KAFKA_PORT}:19092"]
    assert "redpanda-data:/var/lib/redpanda/data" in broker["volumes"]
    command = broker["command"]
    assert "--kafka-addr" in command
    assert "internal://0.0.0.0:9092,external://0.0.0.0:19092" in command
    assert "--advertise-kafka-addr" in command
    assert "internal://redpanda:9092,external://localhost:${REDPANDA_KAFKA_PORT}" in command
    assert "--mode" in command and "dev-container" in command

    assert init["image"] == "${REDPANDA_IMAGE:-docker.redpanda.com/redpandadata/redpanda:v26.1.12}"
    assert init["deploy"]["replicas"] == "${REDPANDA_INIT_SCALE:-0}"
    assert init["depends_on"]["redpanda"]["condition"] == "service_healthy"
    assert "/scripts/init-redpanda.sh" in init["entrypoint"]
    script = (SERVICE_DIR / "init" / "scripts" / "init-redpanda.sh").read_text()
    assert "REDPANDA_DEMO_TOPICS" in script
    assert "rpk topic create" in script
    assert "--if-not-exists" in script
    assert "-X brokers=redpanda:9092" in script

    assert console["image"] == "${REDPANDA_CONSOLE_IMAGE:-docker.redpanda.com/redpandadata/console:v3.8.0}"
    assert console["ports"] == ["${HOST_BIND_IP:-}${REDPANDA_CONSOLE_PORT}:8080"]
    assert console["deploy"]["replicas"] == "${REDPANDA_CONSOLE_SCALE:-0}"
    assert console["depends_on"]["redpanda"]["condition"] == "service_healthy"
    assert "KAFKA_BROKERS" in console["environment"]
    assert console["environment"]["KAFKA_BROKERS"] == "redpanda:9092"


def test_redpanda_topology_env_and_track_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "redpanda"]

    assert len(rows) == 1
    assert rows[0].category == "data"
    assert rows[0].alias == "redpanda.localhost"
    assert "redpanda.localhost" in topology.aliases
    assert "REDPANDA_KAFKA_PORT" in topology.port_defaults
    assert "REDPANDA_CONSOLE_PORT" in topology.port_defaults

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "REDPANDA_SOURCE=disabled",
        "REDPANDA_IMAGE=docker.redpanda.com/redpandadata/redpanda:v26.1.12",
        "REDPANDA_CONSOLE_IMAGE=docker.redpanda.com/redpandadata/console:v3.8.0",
        "REDPANDA_KAFKA_PORT=",
        "REDPANDA_CONSOLE_PORT=",
        "REDPANDA_DEMO_TOPICS=atlas_stream_events",
        "REDPANDA_SCALE=",
        "REDPANDA_INIT_SCALE=",
        "REDPANDA_CONSOLE_SCALE=",
        "REDPANDA_BROKERS=",
        "SPARK_KAFKA_BOOTSTRAP_SERVERS=",
    ):
        assert expected in env_example

    registry = load_tracks()
    assert is_in_track(registry.by_key["data-eng"], "redpanda", always_on=registry.always_on)
    assert is_in_track(registry.by_key["all"], "redpanda", always_on=registry.always_on)
    for track_key in ("gen-ai-rag", "gen-ai-eng", "gen-ai-creative", "ml-eng"):
        assert not is_in_track(registry.by_key[track_key], "redpanda", always_on=registry.always_on)


def test_redpanda_source_cli_mapping_and_service_config() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))
    assert mgr.source_mapping["redpanda_source"] == "REDPANDA_SOURCE"
    assert mgr.collect_overrides(redpanda_source="container") == {"REDPANDA_SOURCE": "container"}

    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {"REDPANDA_SOURCE": "disabled"}
    assert sc._generate_redpanda_config() == {
        "REDPANDA_SCALE": "0",
        "REDPANDA_INIT_SCALE": "0",
        "REDPANDA_CONSOLE_SCALE": "0",
        "REDPANDA_BROKERS": "",
        "SPARK_KAFKA_BOOTSTRAP_SERVERS": "",
    }

    sc.service_sources = {"REDPANDA_SOURCE": "container"}
    assert sc._generate_redpanda_config() == {
        "REDPANDA_SCALE": "1",
        "REDPANDA_INIT_SCALE": "1",
        "REDPANDA_CONSOLE_SCALE": "1",
        "REDPANDA_BROKERS": "redpanda:9092",
        "SPARK_KAFKA_BOOTSTRAP_SERVERS": "redpanda:9092",
    }


def test_redpanda_bootstrap_env_reaches_streaming_consumers() -> None:
    env_name = "SPARK_KAFKA_BOOTSTRAP_SERVERS"
    interpolation = "${SPARK_KAFKA_BOOTSTRAP_SERVERS:-}"

    compose_targets = {
        "spark": ["spark-master", "spark-worker", "spark-connect", "spark-history"],
        "jupyterhub": ["jupyterhub"],
        "zeppelin": ["zeppelin"],
        "airflow": ["airflow-webserver", "airflow-scheduler", "airflow-dag-processor"],
    }
    for service, containers in compose_targets.items():
        compose = _service_compose(service)["services"]
        for container in containers:
            assert compose[container]["environment"][env_name] == interpolation

    runtime_targets = {
        "spark": ["spark-master", "spark-worker", "spark-connect", "spark-history"],
        "jupyterhub": ["jupyterhub"],
        "zeppelin": ["zeppelin"],
        "airflow": ["airflow-webserver", "airflow-scheduler", "airflow-dag-processor"],
    }
    for service, containers in runtime_targets.items():
        runtime_sc = _service_manifest(service)["runtime_sc"]
        for container in containers:
            assert runtime_sc[container]["container"]["environment"][env_name] == interpolation

    for service in ("spark", "jupyterhub", "zeppelin", "airflow"):
        manifest = _service_manifest(service)
        assert "redpanda" in manifest["depends_on"]["optional"]
        assert "redpanda" in manifest["data_flow"]["calls"]


def test_redpanda_kong_route_and_docs_contract() -> None:
    enabled_services = _kong_services("REDPANDA_SOURCE=container\n")
    disabled_services = _kong_services("REDPANDA_SOURCE=disabled\n")

    enabled_hosts = {
        host
        for service in enabled_services
        for route in service.get("routes", [])
        for host in route.get("hosts", [])
    }
    disabled_hosts = {
        host
        for service in disabled_services
        for route in service.get("routes", [])
        for host in route.get("hosts", [])
    }
    assert "redpanda.localhost" in enabled_hosts
    assert "redpanda.localhost" not in disabled_hosts

    redpanda_service = next(service for service in enabled_services if service["name"] == "redpanda-console")
    assert redpanda_service["url"] == "http://redpanda-console:8080/"
    assert redpanda_service["routes"][0]["preserve_host"] is True
    assert redpanda_service["routes"][0]["hosts"] == ["redpanda.localhost"]
    plugins = {plugin["name"]: plugin for plugin in redpanda_service["plugins"]}
    assert {"cors", "basic-auth", "acl"} <= set(plugins)
    assert plugins["acl"]["config"]["allow"] == ["dashboard_user"]

    readme = README.read_text()
    for expected in (
        "REDPANDA_SOURCE=disabled",
        "redpanda.localhost",
        "redpanda:9092",
        "REDPANDA_DEMO_TOPICS=atlas_stream_events",
        "spark.readStream.format(\"kafka\")",
        "s3a://checkpoints/",
        "docker.redpanda.com/redpandadata/redpanda:v26.1.12",
        "docker.redpanda.com/redpandadata/console:v3.8.0",
        "Kafka Connect",
        "Debezium",
    ):
        assert expected in readme


def test_spark_image_bakes_kafka_connector_jars_with_sha512() -> None:
    dockerfile = SPARK_DOCKERFILE.read_text()

    expected = {
        "SPARK_SQL_KAFKA_VERSION": "4.1.2",
        "SPARK_SQL_KAFKA_ARTIFACT": "spark-sql-kafka-0-10_2.13",
        "SPARK_KAFKA_TOKEN_PROVIDER_ARTIFACT": "spark-token-provider-kafka-0-10_2.13",
        "KAFKA_CLIENTS_VERSION": "3.9.1",
        "COMMONS_POOL2_VERSION": "2.12.1",
        "SPARK_SQL_KAFKA_SHA512": "57212eeb69ec417a2ab84dcf9de882fea95eb5554bd9adf10698fb5621fe0127283fd3bad4bb19897598673d06fa087de9fdf0f9614ec4004248e56e8a80ba94",
        "SPARK_KAFKA_TOKEN_PROVIDER_SHA512": "68a83987ad79923effa7b0e1657a8eb987857714502fffc59326a2784312b15bf943fbc88b257215869f68853be79322687c443806f9f78e15a6cae2ceffbe5c",
        "KAFKA_CLIENTS_SHA512": "0b95bb53006888a5409f6fced2d0e03875a4ce19dcccb8b635ba3a67482475236cf8a33c40709641222dd7556fc0ebbff682a046929134383b1a064e78ab12eb",
        "COMMONS_POOL2_SHA512": "186dabefa07a38cc106e5aca3fcec6d2d8c79fc2787d448632c24a61cad3574c53809895f85181ba097012b60a86116eec22ae3ab4c6e47ac7dddac00a0b71a7",
    }
    for key, value in expected.items():
        assert f"ARG {key}={value}" in dockerfile

    for jar in (
        "spark-sql-kafka-0-10_2.13-${SPARK_SQL_KAFKA_VERSION}.jar",
        "spark-token-provider-kafka-0-10_2.13-${SPARK_SQL_KAFKA_VERSION}.jar",
        "kafka-clients-${KAFKA_CLIENTS_VERSION}.jar",
        "commons-pool2-${COMMONS_POOL2_VERSION}.jar",
    ):
        assert jar in dockerfile
