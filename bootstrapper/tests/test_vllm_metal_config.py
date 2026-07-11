"""Tests for _generate_vllm_metal_config() (#379)."""
from __future__ import annotations

from unittest.mock import MagicMock

from services.service_config import ServiceConfig


_BASE_ENV = {
    "PROJECT_NAME": "atlas",
    "VLLM_METAL_LOCALHOST_PORT": "8000",
}


def _make(source: str, host: str = "host.docker.internal", port: str = "8000") -> ServiceConfig:
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = host
    sc.service_sources = {"VLLM_METAL_SOURCE": source}
    env = dict(_BASE_ENV)
    env["VLLM_METAL_LOCALHOST_PORT"] = port
    sc.config_parser.parse_env_file.return_value = env
    return sc


def test_disabled_clears_endpoint_and_scale():
    env = _make("disabled")._generate_vllm_metal_config()
    assert env["VLLM_METAL_ENDPOINT"] == ""
    assert env["VLLM_METAL_SCALE"] == "0"


def test_managed_localhost_resolves_endpoint():
    env = _make("managed-localhost")._generate_vllm_metal_config()
    assert env["VLLM_METAL_ENDPOINT"] == "http://host.docker.internal:8000"
    # Never a container — scale stays 0 in every source.
    assert env["VLLM_METAL_SCALE"] == "0"


def test_managed_localhost_honours_custom_port():
    env = _make("managed-localhost", port="8123")._generate_vllm_metal_config()
    assert env["VLLM_METAL_ENDPOINT"] == "http://host.docker.internal:8123"


def test_managed_localhost_uses_localhost_host_seam():
    # ServiceConfig rewrites host.docker.internal → localhost_host; a bare
    # localhost host must be honoured (mirrors _generate_lightrag_config).
    env = _make("managed-localhost", host="localhost")._generate_vllm_metal_config()
    assert env["VLLM_METAL_ENDPOINT"] == "http://localhost:8000"
