# bootstrapper/tests/test_tei_reranker_config.py
"""Tests for _generate_tei_reranker_config()."""
from __future__ import annotations

from pathlib import Path
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


_PIN_VARS = ("TEI_RERANKER_CPU_IMAGE", "TEI_RERANKER_CPU_ARM64_IMAGE", "TEI_RERANKER_GPU_IMAGE")


def test_refreshed_pin_beats_stale_env_file(monkeypatch):
    """The refreshed manifest pin in shared_env wins over the on-disk .env
    value, which update_env_file only rewrites after this resolver runs."""
    for var in _PIN_VARS:
        monkeypatch.delenv(var, raising=False)
    sc = _make("container-gpu")
    env = sc._generate_tei_reranker_config(
        shared_env={"TEI_RERANKER_GPU_IMAGE": "example/tei-gpu:refreshed"},
    )
    assert env["TEI_RERANKER_IMAGE_RESOLVED"] == "example/tei-gpu:refreshed"


def test_shell_exported_pin_wins(monkeypatch):
    """The refresher skips shell-exported pins, so the resolver reads them."""
    for var in _PIN_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TEI_RERANKER_CPU_IMAGE", "example/tei-cpu:pinned")
    sc = _make("container-cpu")
    with patch("platform.machine", return_value="x86_64"):
        env = sc._generate_tei_reranker_config(
            shared_env={"TEI_RERANKER_CPU_IMAGE": "example/tei-cpu:manifest"},
        )
    assert env["TEI_RERANKER_IMAGE_RESOLVED"] == "example/tei-cpu:pinned"


def test_first_start_after_manifest_bump_resolves_manifest_image(env_with_overrides, monkeypatch):
    """Regression: the resolver read the stale .env pin, so the first start
    after a manifest bump ran the old image in TEI_RERANKER_IMAGE_RESOLVED."""
    from core.config_parser import ConfigParser

    for var in _PIN_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("services.service_config.resolve_host_gateway_ip", lambda: "host-gateway")
    cp = ConfigParser(str(Path(__file__).resolve().parents[2]))
    cp.env_file_path = env_with_overrides({
        "TEI_RERANKER_SOURCE": "container-gpu",
        "TEI_RERANKER_GPU_IMAGE": "example/old-gpu:1",
    })
    sc = ServiceConfig(config_parser=cp)
    sc.localhost_host = "localhost"
    env = sc.generate_service_environment()
    assert env["TEI_RERANKER_GPU_IMAGE"] != "example/old-gpu:1"
    assert env["TEI_RERANKER_IMAGE_RESOLVED"] == env["TEI_RERANKER_GPU_IMAGE"]
