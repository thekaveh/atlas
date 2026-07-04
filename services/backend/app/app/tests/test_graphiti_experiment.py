"""Backend-only Graphiti experiment guardrails."""

from __future__ import annotations

import os

import pytest


def test_graphiti_group_id_is_strict_per_project_namespace_user(monkeypatch):
    from graphiti_experiment import build_graphiti_group_id

    monkeypatch.setenv("PROJECT_NAME", "Atlas Dev!")
    group_id = build_graphiti_group_id(
        user_id="00000000-0000-4000-8000-000000000001",
        namespace="Long Term Memory",
    )

    assert group_id == (
        "atlas:atlas-dev:backend:long-term-memory:"
        "user:00000000-0000-4000-8000-000000000001"
    )


@pytest.mark.parametrize(
    ("user_id", "namespace"),
    [
        ("not-a-uuid", "default"),
        ("00000000-0000-4000-8000-000000000001", "../shared"),
        ("00000000-0000-4000-8000-000000000001", ""),
    ],
)
def test_graphiti_group_id_rejects_unsafe_inputs(user_id, namespace):
    from graphiti_experiment import build_graphiti_group_id

    with pytest.raises(ValueError):
        build_graphiti_group_id(user_id=user_id, namespace=namespace)


def test_graphiti_config_is_disabled_and_backend_only_by_default(monkeypatch):
    from graphiti_experiment import GraphitiExperimentConfig

    for key in list(os.environ):
        if key.startswith("GRAPHITI_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PROJECT_NAME", "atlas")
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "ollama/qwen3.6:latest")
    monkeypatch.setenv("LITELLM_EMBEDDING_MODEL", "ollama/nomic-embed-text")

    config = GraphitiExperimentConfig.from_env()

    assert config.enabled is False
    assert config.expose_to_agents is False
    assert config.group_id_prefix == "atlas"
    assert config.default_namespace == "langmem"
    assert config.llm_model == "ollama/qwen3.6:latest"
    assert config.embedding_model == "ollama/nomic-embed-text"
    assert config.backend_only is True


def test_graphiti_status_route_does_not_require_graphiti_dependency(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KONG_URL", "http://kong-api-gateway:8000")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "dummy-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:x@localhost/x")
    monkeypatch.setenv("PROJECT_NAME", "atlas")
    monkeypatch.setenv("GRAPHITI_ENABLED", "false")
    monkeypatch.setenv("GRAPHITI_EXPOSE_TO_AGENTS", "false")

    from main import app

    response = TestClient(app).get("/memory/graphiti/status")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["backend_only"] is True
    assert body["group_id_pattern"] == "atlas:<project>:backend:<namespace>:user:<uuid>"
    assert body["agent_exposure"] == {"hermes": False, "openclaw": False}
