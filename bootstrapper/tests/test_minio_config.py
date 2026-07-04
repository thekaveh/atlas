from __future__ import annotations

from unittest.mock import MagicMock

from services.service_config import ServiceConfig


def test_minio_public_endpoint_fallback_ports_match_topology_defaults() -> None:
    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {"MINIO_SOURCE": "container"}
    sc.yaml_config = {
        "source_configurable": {
            "minio": {
                "container": {
                    "scale": 1,
                    "environment": {
                        "MINIO_ENDPOINT": "http://minio:9000",
                        "MINIO_PUBLIC_ENDPOINT": "http://localhost:${MINIO_PORT}",
                        "MINIO_PUBLIC_CONSOLE_ENDPOINT": "http://localhost:${MINIO_CONSOLE_PORT}",
                    },
                }
            }
        }
    }
    sc.config_parser.parse_env_file.return_value = {}

    env = sc._generate_minio_config()

    assert env["MINIO_PUBLIC_ENDPOINT"] == "http://localhost:63020"
    assert env["MINIO_PUBLIC_CONSOLE_ENDPOINT"] == "http://localhost:63021"
