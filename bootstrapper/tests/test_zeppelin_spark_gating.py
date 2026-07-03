"""Zeppelin's source-step selection must hard-fail when SPARK_SOURCE=disabled."""
from unittest.mock import MagicMock
import pytest
from services.service_config import ServiceConfig


def test_zeppelin_disabled_returns_scale_zero():
    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {"ZEPPELIN_SOURCE": "disabled", "SPARK_SOURCE": "disabled"}
    out = sc._generate_zeppelin_config()
    assert out["ZEPPELIN_SCALE"] == "0"
    assert out["ZEPPELIN_INIT_SCALE"] == "0"


def test_zeppelin_container_with_spark_container_returns_scale_one():
    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {"ZEPPELIN_SOURCE": "container", "SPARK_SOURCE": "container"}
    out = sc._generate_zeppelin_config()
    assert out["ZEPPELIN_SCALE"] == "1"
    assert out["ZEPPELIN_INIT_SCALE"] == "1"


def test_zeppelin_container_with_spark_disabled_raises():
    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {"ZEPPELIN_SOURCE": "container", "SPARK_SOURCE": "disabled"}
    with pytest.raises(ValueError, match="Zeppelin requires Spark"):
        sc._generate_zeppelin_config()
