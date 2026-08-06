from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _also_route_acquire(monkeypatch, module, get_conn):
    """Route the #804 SAFE pooled path (`acquire_conn`) to the same fake conn
    source as the patched `connect_postgres`. Short-lived DB ops (extract-facts
    inserts, pgvector writes) draw from the shared pool via ``acquire_conn``;
    the fake's ``close()`` on context exit simulates the pool release."""

    @asynccontextmanager
    async def _acq(_url="", **_kw):
        conn = get_conn()
        try:
            yield conn
        finally:
            close = getattr(conn, "close", None)
            if close is not None:
                await close()

    monkeypatch.setattr(module, "acquire_conn", _acq)


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
    _also_route_acquire(monkeypatch, memory_service, Connection)
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


class _FakeExtractionConnection:
    """Records ``execute`` args and satisfies the ``extract_facts`` DB flow."""

    def __init__(self, executed: list):
        self._executed = executed

    async def execute(self, *args):
        self._executed.append(args)

    def transaction(self):
        conn = self

        class _Txn:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_exc):
                return False

        return _Txn()

    async def fetchval(self, *_args):
        return 0

    async def fetchrow(self, *_args):
        from datetime import datetime, timezone

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return {"created_at": now, "updated_at": now}

    async def close(self):
        return None


def _extraction_service(monkeypatch, executed, *, store_embedding):
    import memory_service

    monkeypatch.setenv("DATABASE_URL", "postgresql://atlas")
    monkeypatch.setattr(
        memory_service,
        "connect_postgres",
        AsyncMock(return_value=_FakeExtractionConnection(executed)),
    )
    _also_route_acquire(
        monkeypatch, memory_service, lambda: _FakeExtractionConnection(executed)
    )
    service = memory_service.MemoryService()
    service.enabled = True
    monkeypatch.setattr(service, "_ensure_initialized", AsyncMock())
    monkeypatch.setattr(service, "_get_extraction_model", AsyncMock(return_value="ollama/test"))
    monkeypatch.setattr(
        service,
        "_litellm_complete",
        AsyncMock(
            return_value='[{"content": "user likes tea", '
            '"fact_type": "preference", "confidence": 0.9}]'
        ),
    )
    service.store = SimpleNamespace(store_embedding=store_embedding)
    return service


def _pending_updates(executed):
    return [
        args
        for args in executed
        if args and isinstance(args[0], str) and "vector_sync_pending = true" in args[0]
    ]


def _weaviate_id_updates(executed):
    return [
        args
        for args in executed
        if args and isinstance(args[0], str) and "SET weaviate_id" in args[0]
    ]


@pytest.mark.asyncio
async def test_extract_facts_flags_vector_sync_pending_on_embedding_failure(monkeypatch):
    """A fact whose embedding fails to persist must be flagged
    vector_sync_pending=true so the reconciler recovers it — otherwise it stays
    weaviate_id=NULL + pending=false and is lost from semantic recall forever."""
    executed: list = []
    service = _extraction_service(
        monkeypatch,
        executed,
        store_embedding=AsyncMock(side_effect=RuntimeError("weaviate down")),
    )

    result = await service.extract_facts(
        user_id="00000000-0000-4000-8000-000000000002",
        messages=[{"role": "user", "content": "I like tea"}],
        conversation_id="00000000-0000-4000-8000-000000000003",
    )

    assert result["status"] == "completed"
    assert result["facts_extracted"] == 1
    assert _pending_updates(executed), "un-embedded fact must be flagged for the reconciler"
    assert not _weaviate_id_updates(executed)


@pytest.mark.asyncio
async def test_extract_facts_sets_weaviate_id_on_embedding_success(monkeypatch):
    """On successful embedding the fact records its weaviate_id and is NOT flagged
    pending (the reconciler must not re-process an already-synced fact)."""
    executed: list = []
    service = _extraction_service(
        monkeypatch,
        executed,
        store_embedding=AsyncMock(return_value="vector-1"),
    )

    result = await service.extract_facts(
        user_id="00000000-0000-4000-8000-000000000002",
        messages=[{"role": "user", "content": "I like tea"}],
        conversation_id="00000000-0000-4000-8000-000000000003",
    )

    assert result["status"] == "completed"
    assert _weaviate_id_updates(executed), "successful embedding must persist weaviate_id"
    assert not _pending_updates(executed)


@pytest.mark.asyncio
async def test_extract_facts_inserts_facts_pending_until_embedding_written(monkeypatch):
    """Facts are inserted with vector_sync_pending=true so an interrupted
    embedding-writeback loop (a transient DB blip on a later fact, before its
    UPDATE runs) still leaves every un-embedded fact flagged for the reconciler.
    Without this default, a mid-loop raise leaves facts at weaviate_id=NULL +
    pending=false and they are permanently invisible to semantic recall."""
    import memory_service
    from datetime import datetime, timezone

    inserts: list[str] = []
    executed: list = []

    class _RecordingConn(_FakeExtractionConnection):
        def __init__(self):
            super().__init__(executed)

        async def fetchrow(self, sql, *_args):
            inserts.append(sql)
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            return {"created_at": now, "updated_at": now}

    monkeypatch.setenv("DATABASE_URL", "postgresql://atlas")
    monkeypatch.setattr(
        memory_service, "connect_postgres", AsyncMock(return_value=_RecordingConn())
    )
    _also_route_acquire(monkeypatch, memory_service, _RecordingConn)
    service = memory_service.MemoryService()
    service.enabled = True
    monkeypatch.setattr(service, "_ensure_initialized", AsyncMock())
    monkeypatch.setattr(service, "_get_extraction_model", AsyncMock(return_value="ollama/test"))
    monkeypatch.setattr(
        service,
        "_litellm_complete",
        AsyncMock(
            return_value='[{"content": "user likes tea", "fact_type": "preference", '
            '"confidence": 0.9}]'
        ),
    )
    service.store = SimpleNamespace(store_embedding=AsyncMock(return_value="vector-1"))

    result = await service.extract_facts(
        user_id="00000000-0000-4000-8000-000000000002",
        messages=[{"role": "user", "content": "I like tea"}],
        conversation_id="00000000-0000-4000-8000-000000000003",
    )

    assert result["status"] == "completed"
    assert inserts, "a fact must be inserted"
    assert any(
        "vector_sync_pending" in sql and "true" in sql for sql in inserts
    ), "INSERT must default vector_sync_pending=true so an interrupted writeback is recoverable"


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


class _FakeReconcileConnection:
    """Serves one page of pending rows to ``_reconcile_pending_vectors`` and
    records the clear-UPDATE args."""

    def __init__(self, rows, executed):
        self._rows = rows
        self._executed = executed

    async def fetch(self, *_args):
        return self._rows

    async def execute(self, *args):
        self._executed.append(args)

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_reconcile_clear_guards_on_updated_at(monkeypatch):
    """The reconcile flag-clear must carry an optimistic updated_at guard so a
    concurrent update_memory that re-flags the fact for new content isn't
    silently wiped against our now-stale vector write (permanent divergence)."""
    import memory_service
    from datetime import datetime, timezone

    monkeypatch.setenv("DATABASE_URL", "postgresql://atlas")
    ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    row = {
        "id": "11111111-1111-4000-8000-000000000001",
        "user_id": "22222222-2222-4000-8000-000000000002",
        "namespace": "default",
        "content": "user likes tea",
        "fact_type": "preference",
        "confidence": 0.9,
        "is_active": True,
        "weaviate_id": None,
        "updated_at": ts,
    }
    executed: list = []
    conn = _FakeReconcileConnection([row], executed)
    monkeypatch.setattr(
        memory_service, "connect_postgres", AsyncMock(return_value=conn)
    )
    service = memory_service.MemoryService()
    service.enabled = True
    service.store = SimpleNamespace(
        update_embedding=AsyncMock(return_value="vec-new"),
        deactivate_embedding=AsyncMock(),
    )

    reconciled = await service._reconcile_pending_vectors()

    assert reconciled == 1
    clears = [
        a for a in executed
        if a and isinstance(a[0], str) and "vector_sync_pending = false" in a[0]
    ]
    assert clears, "reconcile must clear the pending flag"
    query, *params = clears[0]
    assert "AND updated_at = $3" in query, "clear must be guarded on the read updated_at"
    assert params == [row["id"], "vec-new", ts]


@pytest.mark.asyncio
async def test_pgvector_write_casts_bind_param_to_vector(monkeypatch):
    """The pgvector fallback write must cast `$1::vector`. Without the cast
    asyncpg infers the column's `vector` type for the bind param, has no codec
    for it, and raises at execute — breaking every memory write whenever
    Weaviate is disabled/unavailable. Mirrors the read path (`<=> $1::vector`)."""
    import memory_store

    executed = []

    class Conn:
        async def execute(self, query, *params):
            executed.append((query, params))

        async def close(self):
            pass

    store = memory_store.MemoryStore("postgresql://atlas")
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3])
    )
    # #804: _store_pgvector draws from the shared pool via acquire_conn.
    _also_route_acquire(monkeypatch, memory_store, Conn)

    await store._store_pgvector("00000000-0000-4000-8000-000000000001", "hello")

    assert executed, "pgvector write must execute an UPDATE"
    query, _params = executed[0]
    assert "SET embedding = $1::vector" in query
