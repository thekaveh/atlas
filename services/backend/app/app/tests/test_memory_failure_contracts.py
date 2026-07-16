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


@pytest.mark.asyncio
async def test_deactivate_embedding_patches_weaviate_active_flag(monkeypatch):
    import memory_store

    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, _json=None, **_kwargs):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"data": {"Get": {"Memory": []}}},
            )

        async def patch(self, url, json):
            calls.append((url, json))
            return SimpleNamespace(status_code=204, raise_for_status=lambda: None)

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    # Existing Weaviate IDs must be retired even while new writes have
    # temporarily fallen back to pgvector.
    store.backend = "pgvector"
    store._initialized = True
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kwargs: Client())

    await store.deactivate_embedding("fact-1", "vector-1")

    assert calls == [
        (
            "http://weaviate/v1/objects/Memory/vector-1",
            {"class": "Memory", "properties": {"isActive": False}},
        )
    ]


@pytest.mark.asyncio
async def test_store_embedding_uses_fact_uuid_as_deterministic_weaviate_id(
    monkeypatch,
):
    import memory_store

    payloads = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            payloads.append((url, json))
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"id": json["id"]},
            )

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    store.backend = "weaviate"
    store._initialized = True
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kwargs: Client())

    object_id = await store.store_embedding(
        fact_id="00000000-0000-4000-8000-000000000001",
        content="fact",
        user_id="00000000-0000-4000-8000-000000000002",
        namespace="default",
        fact_type="observation",
        confidence=0.9,
        metadata={},
    )

    assert object_id == "00000000-0000-4000-8000-000000000001"
    assert payloads[0][1]["id"] == object_id


@pytest.mark.asyncio
async def test_deactivate_embedding_finds_legacy_object_when_link_is_missing(
    monkeypatch,
):
    import memory_store

    patches = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            assert url == "http://weaviate/v1/graphql"
            assert "pgFactId" in json["query"]
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": {
                        "Get": {
                            "Memory": [{"_additional": {"id": "legacy-vector"}}]
                        }
                    }
                },
            )

        async def patch(self, url, json):
            patches.append((url, json))
            return SimpleNamespace(status_code=204, raise_for_status=lambda: None)

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    store.backend = "weaviate"
    store._initialized = True
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kwargs: Client())

    await store.deactivate_embedding(
        "00000000-0000-4000-8000-000000000001", None
    )

    assert patches == [
        (
            "http://weaviate/v1/objects/Memory/legacy-vector",
            {"class": "Memory", "properties": {"isActive": False}},
        )
    ]


@pytest.mark.asyncio
async def test_deactivate_embedding_rejects_graphql_errors(monkeypatch):
    import memory_store

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, _json=None, **_kwargs):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"errors": [{"message": "schema unavailable"}]},
            )

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    store.backend = "weaviate"
    store._initialized = True
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(RuntimeError, match="legacy-object lookup failed"):
        await store.deactivate_embedding(
            "00000000-0000-4000-8000-000000000001", None
        )
