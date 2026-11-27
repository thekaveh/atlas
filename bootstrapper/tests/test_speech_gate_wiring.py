"""Speech gate wiring: manifest, compose fragment, STT_ENDPOINT and scale.

The gate sits in front of Speaches only, so STT_ENDPOINT points at it only
for a speaches-container-* STT source with SPEECH_GATE_SOURCE=container.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from services.service_config import SPEECH_GATE_ENDPOINT, ServiceConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_DIR = REPO_ROOT / "services" / "speech-gate"
FRAGMENT = GATE_DIR / "compose.yml"
MANIFEST = GATE_DIR / "service.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
COMPOSE = REPO_ROOT / "docker-compose.yml"


pytestmark = pytest.mark.usefixtures("throwaway_admin_secrets")


def _sc(stt_source: str, gate_source: str, stt_endpoint: str = "http://speaches:8000") -> ServiceConfig:
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = "host.docker.internal"
    sc.service_sources = {"STT_PROVIDER_SOURCE": stt_source, "SPEECH_GATE_SOURCE": gate_source}
    sc.config_parser.parse_env_file.return_value = {"SPEACHES_PORT": "63060"}
    sc.get_service_config = MagicMock(return_value={"environment": {"STT_ENDPOINT": stt_endpoint}})
    return sc


def test_speaches_stt_routes_consumers_through_the_gate() -> None:
    env = _sc("speaches-container-cpu", "container")._generate_stt_provider_config()
    assert env["STT_ENDPOINT"] == SPEECH_GATE_ENDPOINT == "http://speech-gate:8000"


def test_gate_disabled_keeps_consumers_on_speaches() -> None:
    env = _sc("speaches-container-cpu", "disabled")._generate_stt_provider_config()
    assert env["STT_ENDPOINT"] == "http://speaches:8000"


def test_gate_is_not_in_front_of_non_speaches_stt() -> None:
    env = _sc(
        "parakeet-container-gpu", "container", stt_endpoint="http://parakeet-gpu:8000"
    )._generate_stt_provider_config()
    assert env["STT_ENDPOINT"] == "http://parakeet-gpu:8000"


def test_disabled_stt_keeps_an_empty_endpoint() -> None:
    env = _sc("disabled", "container", stt_endpoint="")._generate_stt_provider_config()
    assert env["STT_ENDPOINT"] == ""


@pytest.mark.parametrize(
    ("stt_source", "gate_source", "scale"),
    [
        ("speaches-container-cpu", "container", "1"),
        ("speaches-container-gpu", "container", "1"),
        ("speaches-container-cpu", "disabled", "0"),
        ("parakeet-container-gpu", "container", "0"),
        ("disabled", "container", "0"),
    ],
)
def test_gate_scale_follows_stt_source_and_gate_source(stt_source: str, gate_source: str, scale: str) -> None:
    env = _sc(stt_source, gate_source)._generate_speech_gate_config()
    assert env["SPEECH_GATE_SCALE"] == scale


def test_fragment_declares_one_internal_gate_on_backend_network() -> None:
    fragment = yaml.safe_load(FRAGMENT.read_text())
    service = fragment["services"]["speech-gate"]
    assert service["build"]["context"] == "."
    assert service["build"]["dockerfile"] == "Dockerfile"
    assert service["networks"] == ["backend-network"]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["environment"]["SPEECH_GATE_UPSTREAM_URL"] == "http://speaches:8000"
    assert service["deploy"]["replicas"] == "${SPEECH_GATE_SCALE:-0}"
    assert any("${SPEECH_GATE_PORT:-" in port and port.endswith(":8000") for port in service["ports"])
    assert "/health" in " ".join(service["healthcheck"]["test"])


def test_manifest_declares_source_var_port_and_container() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text())
    assert manifest["name"] == "speech-gate"
    assert manifest["category"] == "media"
    assert manifest["sources"]["var"] == "SPEECH_GATE_SOURCE"
    assert [option["id"] for option in manifest["sources"]["options"]] == ["container", "disabled"]
    assert manifest["containers"] == ["speech-gate"]
    row = manifest["rows"][0]
    assert row["port_var"] == "SPEECH_GATE_PORT"
    assert row["scale_var"] == "SPEECH_GATE_SCALE"
    assert set(manifest["runtime_sc"]["speech-gate"]) == {"container", "disabled"}


def _docker_available() -> bool:
    return shutil.which("docker") is not None


@pytest.mark.skipif(
    not _docker_available() or not ENV_EXAMPLE.is_file(),
    reason="docker not on PATH or .env.example missing",
)
def test_rendered_compose_runs_the_gate_only_when_scale_is_one(tmp_path: Path) -> None:
    base = ENV_EXAMPLE.read_text()
    for scale, expected in (("1", "1"), ("0", "0")):
        env_file = tmp_path / f"env-{scale}"
        env_file.write_text(base + f"\nSPEECH_GATE_SCALE={scale}\n")
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(env_file), "-f", str(COMPOSE), "config", "--format", "json"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        services = json.loads(result.stdout)["services"]
        assert services["speech-gate"]["deploy"]["replicas"] == int(expected)
