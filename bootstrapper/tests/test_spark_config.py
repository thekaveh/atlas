"""Unit test for ServiceConfig._generate_spark_config()."""
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from services.service_config import ServiceConfig


ROOT = Path(__file__).resolve().parent.parent.parent


def _build_config(source_value: str, worker_count: str = "2", minio_source: str = "container"):
    """Build a ServiceConfig stub for _generate_spark_config.

    MinIO defaults to ``container`` because Spark hard-requires MinIO at
    source-resolution time (see _generate_spark_config docstring and the
    dedicated gate test in tests/test_spark_minio_gating.py). The
    "Spark=disabled" cases pass through the short-circuit before the
    MinIO check, so MinIO can stay disabled there.
    """
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = "host.docker.internal"
    sc.service_sources = {"SPARK_SOURCE": source_value, "MINIO_SOURCE": minio_source}
    sc.yaml_config = {
        "source_configurable": {
            "spark": {
                source_value: {"environment": {}, "scale": 1, "deploy": {}, "extra_hosts": []}
            }
        }
    }
    sc.config_parser.parse_env_file.return_value = {"SPARK_WORKER_COUNT": worker_count}
    return sc._generate_spark_config()


def test_spark_disabled_sets_all_scales_to_zero():
    env_vars = _build_config("disabled")
    assert env_vars["SPARK_MASTER_SCALE"] == "0"
    assert env_vars["SPARK_WORKER_SCALE"] == "0"
    assert env_vars["SPARK_HISTORY_SCALE"] == "0"
    assert env_vars["SPARK_INIT_SCALE"] == "0"
    assert env_vars["SPARK_CONNECT_SCALE"] == "0"


def test_spark_container_with_default_worker_count():
    env_vars = _build_config("container", worker_count="2")
    assert env_vars["SPARK_MASTER_SCALE"] == "1"
    assert env_vars["SPARK_WORKER_SCALE"] == "2"
    assert env_vars["SPARK_HISTORY_SCALE"] == "1"
    assert env_vars["SPARK_INIT_SCALE"] == "1"
    assert env_vars["SPARK_CONNECT_SCALE"] == "1"


def test_spark_container_respects_worker_count_override():
    env_vars = _build_config("container", worker_count="5")
    assert env_vars["SPARK_WORKER_SCALE"] == "5"


def test_spark_container_clamps_worker_count():
    env_vars_low = _build_config("container", worker_count="0")
    assert env_vars_low["SPARK_WORKER_SCALE"] == "1", "below-1 clamped to 1"
    env_vars_high = _build_config("container", worker_count="42")
    assert env_vars_high["SPARK_WORKER_SCALE"] == "8", "above-8 clamped to 8"


def test_spark_manifest_declares_connect_core_cap_env():
    manifest = yaml.safe_load((ROOT / "services" / "spark" / "service.yml").read_text())
    env_by_name = {item["name"]: item for item in manifest["env"]}

    cap = env_by_name["SPARK_CONNECT_CORES_MAX"]
    assert cap["default"] == "1"
    assert cap.get("auto_managed") is not True
    assert "Spark Connect" in cap["description"]
    assert "standalone" in cap["description"]


def test_env_example_includes_spark_connect_core_cap_default():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "SPARK_CONNECT_CORES_MAX=1" in env_example
    assert "Spark Connect" in env_example
    assert "standalone" in env_example
