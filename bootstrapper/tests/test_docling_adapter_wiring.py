"""Compose and source contracts for the isolated LightRAG–Docling adapter."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig


ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ROOT / "docker-compose.yml"
DOCLING_COMPOSE = ROOT / "services" / "docling" / "compose.yml"
PARAKEET_COMPOSE = ROOT / "services" / "parakeet" / "compose.yml"
DOCLING_MANIFEST = ROOT / "services" / "docling" / "service.yml"
LIGHTRAG_MANIFEST = ROOT / "services" / "lightrag" / "service.yml"


def _service_config(env_path: Path) -> ServiceConfig:
    parser = ConfigParser(str(ROOT))
    parser.env_file_path = env_path
    config = ServiceConfig(config_parser=parser)
    config.localhost_host = "localhost"
    return config


@pytest.mark.parametrize(
    "docling_source,lightrag_source,scale,upstream,lightrag_endpoint",
    [
        (
            "docling-container-gpu",
            "container",
            "1",
            "http://docling-gpu:8000/internal/lightrag/bundle",
            "http://docling-lightrag-adapter:8000",
        ),
        (
            "docling-localhost",
            "container",
            "1",
            "http://localhost:18159/internal/lightrag/bundle",
            "http://docling-lightrag-adapter:8000",
        ),
        ("disabled", "container", "0", "", ""),
        ("docling-container-gpu", "disabled", "0", "", ""),
        ("docling-localhost", "disabled", "0", "", ""),
        ("disabled", "disabled", "0", "", ""),
        ("docling-container-gpu", "localhost", "0", "", ""),
    ],
)
def test_adapter_source_permutations(
    env_with_overrides,
    docling_source,
    lightrag_source,
    scale,
    upstream,
    lightrag_endpoint,
):
    config = _service_config(
        env_with_overrides(
            {
                "DOC_PROCESSOR_SOURCE": docling_source,
                "LIGHTRAG_SOURCE": lightrag_source,
                "DOCLING_LOCALHOST_PORT": "18159",
            }
        )
    )

    generated = config.generate_service_environment()

    assert generated["DOCLING_ADAPTER_SCALE"] == scale
    assert generated["DOCLING_ADAPTER_UPSTREAM_ENDPOINT"] == upstream
    assert generated["LIGHTRAG_DOCLING_ENDPOINT"] == lightrag_endpoint


def test_manifests_declare_adapter_runtime_contract():
    docling = yaml.safe_load(DOCLING_MANIFEST.read_text(encoding="utf-8"))
    lightrag = yaml.safe_load(LIGHTRAG_MANIFEST.read_text(encoding="utf-8"))

    assert "docling-lightrag-adapter" in docling["containers"]
    assert any(
        item["var"] == "DOCLING_ADAPTER_IMAGE"
        and item["container"] == "docling-lightrag-adapter"
        for item in docling["images"]
    )
    declared_env = {item["name"] for item in docling["env"]}
    assert "DOCLING_ADAPTER_UPSTREAM_ENDPOINT" in declared_env
    assert (
        lightrag["runtime_adaptive"]["lightrag"]["environment_adaptation"][
            "LIGHTRAG_DOCLING_ENDPOINT"
        ]
        == "http://docling-lightrag-adapter:8000"
    )


def test_provider_ports_default_to_loopback_and_preload_flag_is_retired():
    docling = DOCLING_COMPOSE.read_text(encoding="utf-8")
    parakeet = PARAKEET_COMPOSE.read_text(encoding="utf-8")

    assert '${HOST_BIND_IP:-127.0.0.1:}${DOC_PROCESSOR_PORT:-63051}:8000' in docling
    assert '${HOST_BIND_IP:-127.0.0.1:}${STT_PROVIDER_PORT:-63055}:8000' in parakeet
    assert "PRELOAD_MODEL" not in parakeet


def _render_isolated_stack(tmp_path: Path) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker is unavailable")
    overrides = {
        "COMPOSE_PROFILES": "docling-gpu,doc-gpu,parakeet-gpu,stt-gpu",
        "DOCLING_GPU_SCALE": "1",
        "DOCLING_ADAPTER_SCALE": "1",
        "PARAKEET_GPU_SCALE": "1",
        "DOCLING_ADAPTER_UPSTREAM_ENDPOINT": (
            "http://docling-gpu:8000/internal/lightrag/bundle"
        ),
        "LIGHTRAG_SCALE": "1",
        "LIGHTRAG_DOCLING_ENDPOINT": "http://docling-lightrag-adapter:8000",
        "HOST_BIND_IP": "",
    }
    lines = []
    seen = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in overrides:
            lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            lines.append(line)
    lines.extend(f"{key}={value}" for key, value in overrides.items() if key not in seen)
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-p",
            "atlas-test",
            "-f",
            str(COMPOSE),
            "config",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return yaml.safe_load(result.stdout)


def test_compose_isolates_adapter_and_provider_secret(tmp_path):
    rendered = _render_isolated_stack(tmp_path)
    services = rendered["services"]
    adapter = services["docling-lightrag-adapter"]
    lightrag = services["lightrag"]

    assert "ports" not in adapter
    assert set(adapter["networks"]) == {"docling-lightrag-network"}
    assert adapter["environment"]["DOCLING_API_TOKEN"] == ""
    assert adapter["environment"]["DOCLING_ADAPTER_UPSTREAM_ENDPOINT"].endswith(
        "/internal/lightrag/bundle"
    )
    assert adapter["read_only"] is True
    assert adapter["cap_drop"] == ["ALL"]
    assert any(item.startswith("/tmp:") for item in adapter["tmpfs"])

    assert lightrag["environment"]["DOCLING_ENDPOINT"] == (
        "http://docling-lightrag-adapter:8000"
    )
    assert "DOCLING_API_TOKEN" not in lightrag["environment"]

    members = {
        name
        for name, service in services.items()
        if "docling-lightrag-network" in service.get("networks", {})
    }
    assert members == {"lightrag", "docling-lightrag-adapter", "docling-gpu"}
    assert set(services["docling-gpu"]["networks"]) == {
        "backend-network",
        "docling-lightrag-network",
    }
    assert services["docling-gpu"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert services["parakeet-gpu"]["ports"][0]["host_ip"] == "127.0.0.1"
