from __future__ import annotations

import pytest

import readiness


@pytest.mark.asyncio
async def test_backend_readiness_reports_each_required_dependency(monkeypatch) -> None:
    async def ready() -> None:
        return None

    monkeypatch.setattr(readiness, "_postgres_ready", ready)
    monkeypatch.setattr(readiness, "_redis_ready", ready)
    monkeypatch.setattr(readiness, "_litellm_ready", ready)

    assert await readiness.check_backend_readiness() == {
        "postgres": "ready",
        "redis": "ready",
        "litellm": "ready",
    }


@pytest.mark.asyncio
async def test_backend_readiness_is_degraded_without_masking_probe_failure(monkeypatch) -> None:
    async def ready() -> None:
        return None

    async def failed() -> None:
        raise OSError("secret-bearing connection detail")

    monkeypatch.setattr(readiness, "_postgres_ready", ready)
    monkeypatch.setattr(readiness, "_redis_ready", failed)
    monkeypatch.setattr(readiness, "_litellm_ready", ready)

    result = await readiness.check_backend_readiness()
    assert result == {
        "postgres": "ready",
        "redis": "unavailable",
        "litellm": "ready",
    }
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_postgres_readiness_rejects_invalid_pool_configuration(monkeypatch) -> None:
    import db_connection

    monkeypatch.setenv("DATABASE_URL", "postgresql://x:x@localhost/x")
    monkeypatch.setattr(db_connection, "_POOL_MIN", 11)
    monkeypatch.setattr(db_connection, "_POOL_MAX", 10)

    with pytest.raises(db_connection.PoolConfigurationError):
        await readiness._postgres_ready()


def test_backend_ready_endpoint_uses_dependency_result(monkeypatch) -> None:
    for key, value in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        monkeypatch.setenv(key, value)

    from fastapi.testclient import TestClient
    import main

    async def degraded() -> dict[str, str]:
        return {"postgres": "ready", "redis": "unavailable", "litellm": "ready"}

    monkeypatch.setattr(main, "check_backend_readiness", degraded)
    response = TestClient(main.app).get("/ready")
    assert response.status_code == 503
    assert response.json()["dependencies"]["redis"] == "unavailable"

    async def ready() -> dict[str, str]:
        return {"postgres": "ready", "redis": "ready", "litellm": "ready"}

    monkeypatch.setattr(main, "check_backend_readiness", ready)
    response = TestClient(main.app).get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
