"""Truthful aggregate health contracts for optional Backend features."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import pytest


def _main(monkeypatch):
    for name, value in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(name):
            monkeypatch.setenv(name, value)

    import main

    return main


@pytest.mark.parametrize(
    ("components", "expected_status"),
    [
        ({"database": "healthy", "research_client": "unhealthy"}, "degraded"),
        ({"database": "unhealthy", "research_client": "healthy"}, "degraded"),
        ({"database": "healthy", "research_client": "healthy"}, "healthy"),
    ],
)
def test_research_health_aggregates_every_required_component(
    monkeypatch, components: dict[str, str], expected_status: str
) -> None:
    main = _main(monkeypatch)

    async def component_health() -> dict[str, Any]:
        return {**components, "active_tasks": 2}

    monkeypatch.setattr(main.research_service, "health_check", component_health)

    result = asyncio.run(main.research_health_check())

    assert result == {
        "service": "research",
        "status": expected_status,
        "details": {**components, "active_tasks": 2},
    }


def test_research_health_redacts_unexpected_service_failure(
    monkeypatch, caplog
) -> None:
    main = _main(monkeypatch)

    async def failed_health():
        raise RuntimeError("secret research target")

    monkeypatch.setattr(main.research_service, "health_check", failed_health)

    with caplog.at_level(logging.ERROR):
        result = asyncio.run(main.research_health_check())

    assert result == {
        "service": "research",
        "status": "unhealthy",
        "error": "Research health check failed",
    }
    assert "secret research target" not in caplog.text


@pytest.mark.parametrize("api_key", ["", "   "])
def test_fal_health_rejects_missing_or_blank_key(monkeypatch, api_key: str) -> None:
    main = _main(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "enabled")
    monkeypatch.setenv("FAL_API_KEY", api_key)
    monkeypatch.delenv("FAL_KEY", raising=False)

    result = asyncio.run(main.comfyui_health_check())

    assert result == {
        "service": "fal",
        "status": "unhealthy",
        "error": "FAL_SOURCE=enabled requires FAL_API_KEY",
    }


def test_fal_health_reports_nonempty_unprobed_key_as_configured_not_healthy(
    monkeypatch,
) -> None:
    main = _main(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "enabled")
    monkeypatch.setenv("FAL_API_KEY", "unverified-or-invalid-provider-key")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux/dev")

    result = asyncio.run(main.comfyui_health_check())

    assert result == {
        "service": "fal",
        "status": "configured",
        "details": {
            "provider": "fal",
            "model": "fal-ai/flux/dev",
            "provider_status": "unknown",
        },
    }


def test_comfyui_health_reports_provider_timeout_as_unhealthy(
    monkeypatch, caplog
) -> None:
    main = _main(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "disabled")
    monkeypatch.setenv("COMFYUI_SOURCE", "container-cpu")

    class TimedOutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def health_check(self):
            raise TimeoutError("secret provider target")

    monkeypatch.setattr(main, "ComfyUIClient", TimedOutClient)

    with caplog.at_level(logging.ERROR):
        result = asyncio.run(main.comfyui_health_check())

    assert result == {
        "service": "comfyui",
        "status": "unhealthy",
        "error": "ComfyUI health check failed",
    }
    assert "secret provider target" not in str(result)
    assert "secret provider target" not in caplog.text


def test_comfyui_health_reports_fully_healthy_provider(monkeypatch) -> None:
    main = _main(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "disabled")
    monkeypatch.setenv("COMFYUI_SOURCE", "localhost")
    provider_health = {
        "status": "healthy",
        "response_time": 0.125,
        "system_stats": {"devices": []},
    }

    class HealthyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def health_check(self):
            return provider_health

    monkeypatch.setattr(main, "ComfyUIClient", HealthyClient)

    result = asyncio.run(main.comfyui_health_check())

    assert result == {
        "service": "comfyui",
        "status": "healthy",
        "details": provider_health,
    }


def test_media_health_does_not_probe_when_no_provider_is_configured(
    monkeypatch,
) -> None:
    main = _main(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "disabled")
    monkeypatch.setenv("COMFYUI_SOURCE", "disabled")

    class UnexpectedClient:
        def __init__(self):
            raise AssertionError("disabled ComfyUI must not be probed")

    monkeypatch.setattr(main, "ComfyUIClient", UnexpectedClient)

    result = asyncio.run(main.comfyui_health_check())

    assert result == {
        "service": "media",
        "status": "disabled",
        "details": {"provider": "none"},
    }
