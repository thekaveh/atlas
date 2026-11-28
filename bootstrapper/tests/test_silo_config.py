from __future__ import annotations

from unittest.mock import MagicMock

from services.service_config import ServiceConfig


def test_silo_public_endpoint_fallback_ports_match_topology_defaults() -> None:
    sc = ServiceConfig(config_parser=MagicMock())
    sc.service_sources = {"SILO_SOURCE": "container"}
    sc.yaml_config = {
        "source_configurable": {
            "silo": {
                "container": {
                    "scale": 1,
                    "environment": {
                        "SILO_ENDPOINT": "http://silo:9000",
                        "SILO_PUBLIC_ENDPOINT": "http://localhost:${SILO_PORT}",
                        "SILO_PUBLIC_CONSOLE_ENDPOINT": "http://localhost:${SILO_CONSOLE_PORT}",
                    },
                }
            }
        }
    }
    sc.config_parser.parse_env_file.return_value = {}

    env = sc._generate_silo_config()

    assert env["SILO_PUBLIC_ENDPOINT"] == "http://localhost:63020"
    assert env["SILO_PUBLIC_CONSOLE_ENDPOINT"] == "http://localhost:63021"
