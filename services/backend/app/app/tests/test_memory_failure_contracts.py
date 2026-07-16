from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_weaviate_schema_failure_falls_back_to_pgvector(monkeypatch, caplog):
    import memory_store

    class ReadyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return SimpleNamespace(status_code=200)

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        weaviate_url="http://weaviate",
    )
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kwargs: ReadyClient())
    monkeypatch.setattr(
        store,
        "_ensure_weaviate_collection",
        AsyncMock(side_effect=RuntimeError("SENTINEL_SCHEMA_SECRET")),
    )

    with caplog.at_level(logging.WARNING):
        await store.initialize()

    assert store.backend == "pgvector"
    assert store._initialized is True
    assert "SENTINEL_SCHEMA_SECRET" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_extraction_failure_persists_stable_message(monkeypatch, caplog):
    import memory_service

    calls = []

    class Connection:
        async def execute(self, *args):
            calls.append(args)

        async def close(self):
            return None

    monkeypatch.setattr(
        memory_service,
        "connect_postgres",
        AsyncMock(return_value=Connection()),
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://atlas")
    service = memory_service.MemoryService()

    with caplog.at_level(logging.ERROR):
        await service._mark_extraction_failed(
            "00000000-0000-0000-0000-000000000001",
            RuntimeError("SENTINEL_PROVIDER_SECRET"),
        )

    assert calls
    assert calls[0][1] == "Memory extraction failed"
    assert "SENTINEL_PROVIDER_SECRET" not in repr(calls)
    assert "SENTINEL_PROVIDER_SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_memory_health_returns_stable_public_error(monkeypatch):
    import memory_service

    monkeypatch.setenv("DATABASE_URL", "postgresql://atlas")
    service = memory_service.MemoryService()
    service.enabled = True
    monkeypatch.setattr(
        service,
        "_ensure_initialized",
        AsyncMock(side_effect=RuntimeError("SENTINEL_DATABASE_SECRET")),
    )

    result = await service.health_check()

    assert result["status"] == "unhealthy"
    assert result["error"] == "Memory service is unavailable"
    assert "SENTINEL_DATABASE_SECRET" not in repr(result)
