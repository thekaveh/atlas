# bootstrapper/tests/test_tei_reranker_config.py
"""Tests for _generate_tei_reranker_config()."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.service_config import ServiceConfig


_BASE_ENV = {
    "PROJECT_NAME": "atlas",
    "TEI_RERANKER_LOCALHOST_PORT": "63049",
    "TEI_RERANKER_CPU_IMAGE": "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9",
    "TEI_RERANKER_CPU_ARM64_IMAGE": "ghcr.io/huggingface/text-embeddings-inference:cpu-arm64-latest",
    "TEI_RERANKER_GPU_IMAGE": "ghcr.io/huggingface/text-embeddings-inference:1.9",
}


def _make(source: str) -> ServiceConfig:
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = "localhost"
    sc.service_sources = {"TEI_RERANKER_SOURCE": source}
    sc.config_parser.parse_env_file.return_value = dict(_BASE_ENV)
    return sc


def test_disabled_clears_endpoint_and_scale():
    sc = _make("disabled")
    env = sc._generate_tei_reranker_config()
    assert env["TEI_RERANKER_ENDPOINT"] == ""
    assert env["TEI_RERANKER_SCALE"] == "0"


def test_container_cpu_resolves_cpu_image_on_amd64():
    """On amd64 hosts, container-cpu picks the ORT-backend cpu-1.9 image."""
    sc = _make("container-cpu")
    with patch("platform.machine", return_value="x86_64"):
        env = sc._generate_tei_reranker_config()
    assert env["TEI_RERANKER_ENDPOINT"] == "http://tei-reranker:80"
    assert env["TEI_RERANKER_SCALE"] == "1"
    assert env["TEI_RERANKER_IMAGE_RESOLVED"].endswith(":cpu-1.9")


def test_container_cpu_resolves_arm64_image_on_arm64():
    """On arm64 hosts (Apple Silicon), container-cpu picks the candle-backend
    cpu-arm64-latest image so safetensors-only models load natively."""
    sc = _make("container-cpu")
    with patch("platform.machine", return_value="arm64"):
        env = sc._generate_tei_reranker_config()
    assert env["TEI_RERANKER_ENDPOINT"] == "http://tei-reranker:80"
    assert env["TEI_RERANKER_SCALE"] == "1"
    assert env["TEI_RERANKER_IMAGE_RESOLVED"].endswith(":cpu-arm64-latest")


def test_container_gpu_resolves_gpu_image():
    sc = _make("container-gpu")
    env = sc._generate_tei_reranker_config()
    assert env["TEI_RERANKER_ENDPOINT"] == "http://tei-reranker:80"
    assert env["TEI_RERANKER_SCALE"] == "1"
    assert env["TEI_RERANKER_IMAGE_RESOLVED"] == \
        "ghcr.io/huggingface/text-embeddings-inference:1.9"


def test_localhost_uses_localhost_port():
    sc = _make("localhost")
    env = sc._generate_tei_reranker_config()
    assert env["TEI_RERANKER_ENDPOINT"] == "http://localhost:63049"
    assert env["TEI_RERANKER_SCALE"] == "0"
