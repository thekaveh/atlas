"""Contract tests for the backend-only Graphiti evaluation scaffold."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_MANIFEST = REPO_ROOT / "services" / "backend" / "service.yml"
BACKEND_COMPOSE = REPO_ROOT / "services" / "backend" / "compose.yml"
BACKEND_README = REPO_ROOT / "services" / "backend" / "README.md"
NEO4J_README = REPO_ROOT / "services" / "neo4j" / "README.md"
GRAPHITI_RESEARCH = REPO_ROOT / "docs" / "research" / "candidates" / "graphiti.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _backend_env() -> dict[str, dict]:
    manifest = yaml.safe_load(BACKEND_MANIFEST.read_text())
    return {entry["name"]: entry for entry in manifest["env"]}


def test_backend_manifest_declares_disabled_graphiti_experiment() -> None:
    env = _backend_env()

    assert env["GRAPHITI_ENABLED"]["default"] is False
    assert env["GRAPHITI_GROUP_ID_PREFIX"]["default"] == "atlas"
    assert env["GRAPHITI_DEFAULT_NAMESPACE"]["default"] == "langmem"
    assert env["GRAPHITI_EXPOSE_TO_AGENTS"]["default"] is False
    assert "Hermes" in env["GRAPHITI_EXPOSE_TO_AGENTS"]["description"]
    assert "OpenClaw" in env["GRAPHITI_EXPOSE_TO_AGENTS"]["description"]


def test_backend_compose_injects_graphiti_config_without_new_service_surface() -> None:
    compose = BACKEND_COMPOSE.read_text()
    env_example = ENV_EXAMPLE.read_text()

    assert "GRAPHITI_ENABLED: ${GRAPHITI_ENABLED:-false}" in compose
    assert "GRAPHITI_GROUP_ID_PREFIX: ${GRAPHITI_GROUP_ID_PREFIX:-atlas}" in compose
    assert "GRAPHITI_EXPOSE_TO_AGENTS: ${GRAPHITI_EXPOSE_TO_AGENTS:-false}" in compose
    assert "GRAPHITI_ENABLED=false" in env_example
    assert "GRAPHITI_GROUP_ID_PREFIX=atlas" in env_example

    assert not (REPO_ROOT / "services" / "graphiti" / "service.yml").exists()
    assert "graphiti.localhost" not in compose


def test_docs_capture_backend_only_namespacing_and_langmem_relationship() -> None:
    docs = "\n".join(
        [
            BACKEND_README.read_text(),
            NEO4J_README.read_text(),
            GRAPHITI_RESEARCH.read_text(),
        ]
    ).lower()

    assert "atlas:<project>:backend:<namespace>:user:<uuid>" in docs
    assert "graphiti_enabled=false" in docs
    assert "graphiti_expose_to_agents=false" in docs
    assert "backend-only" in docs
    assert "hermes" in docs
    assert "openclaw" in docs
    assert "augment" in docs
    assert "langmem remains" in docs
    assert "mcp server" in docs
    assert "defer" in docs or "deferred" in docs
