from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = ROOT / "services" / "openclaw"
COMPOSE = SERVICE_DIR / "compose.yml"
MANIFEST = SERVICE_DIR / "service.yml"
README = SERVICE_DIR / "README.md"
ENV_EXAMPLE = ROOT / ".env.example"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_openclaw_direct_provider_keys_do_not_fall_back_to_stack_openai_key() -> None:
    gateway_env = _compose()["services"]["openclaw-gateway"]["environment"]

    assert gateway_env["OPENAI_API_KEY"] == "${OPENCLAW_OPENAI_API_KEY:-}"
    assert gateway_env["ANTHROPIC_API_KEY"] == "${OPENCLAW_ANTHROPIC_API_KEY:-}"
    assert "OPENAI_API_KEY:-${OPENAI_API_KEY" not in COMPOSE.read_text(encoding="utf-8")

    service_env = {entry["name"]: entry for entry in _manifest()["env"]}
    description = service_env["OPENCLAW_OPENAI_API_KEY"]["description"]
    assert "bypass" in description
    assert "stack-wide OPENAI_API_KEY" not in description


def test_openclaw_container_ports_follow_topology_defaults() -> None:
    ports = _compose()["services"]["openclaw-gateway"]["ports"]

    assert "${HOST_BIND_IP:-}${OPENCLAW_GATEWAY_PORT:-63076}:18789" in ports
    assert "${HOST_BIND_IP:-}${OPENCLAW_BRIDGE_PORT:-63077}:18790" in ports

    readme = README.read_text(encoding="utf-8")
    assert "default 63076" in readme
    assert "63076/63077" in readme
    assert "defaults to 63065" in readme  # localhost source stays intentionally separate.


def test_openclaw_current_data_flow_excludes_future_hermes_bridge() -> None:
    manifest = _manifest()
    readme = README.read_text(encoding="utf-8")

    assert manifest["data_flow"]["calls"] == ["litellm"]
    assert "| hermes | agents |" not in readme
    assert "openclaw ↔ hermes" in readme
    assert "only the bridge wiring is missing" in readme


def test_openclaw_env_example_documents_gateway_and_localhost_ports_separately() -> None:
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "OPENCLAW_GATEWAY_PORT=63076" in env_example
    assert "OPENCLAW_BRIDGE_PORT=63077" in env_example
    assert "OPENCLAW_LOCALHOST_PORT=63065" in env_example
    assert "stack-wide OPENAI_API_KEY" not in env_example
