from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest


async def _release_fake(conn):
    """Simulate returning a pooled connection: mark the fake relinquished.

    Real pooling *releases* (not closes) a connection, but the fakes here use
    ``close()``/``.closed`` to mean "the app is no longer holding this
    connection" — which is exactly the property the release-before-LLM tests
    assert. Calling the fake's ``close()`` on context exit keeps those
    assertions faithful under the pooled seam."""
    close = getattr(conn, "close", None)
    if close is not None:
        await close()


def _also_route_acquire(monkeypatch, module, get_conn):
    """Route the #804 SAFE pooled path (`acquire_conn`) through the SAME
    connection source as the patched `connect_postgres`.

    SAFE short-lived DB ops (extract-facts inserts, the consolidate facts
    fetch, recall/summarize/list reads, pgvector store/search) now draw from
    the shared pool via ``acquire_conn`` instead of ``connect_postgres``. Tests
    that sequence connections (e.g. ``iter([PendingConn, FactsConn, ApplyConn])``)
    must feed both functions from the same source so the call-order the
    ordering contracts rely on is preserved. ``get_conn`` returns the next
    connection exactly like the ``connect_postgres`` side-effect does."""

    @asynccontextmanager
    async def _acq(_url="", **_kw):
        conn = get_conn()
        try:
            yield conn
        finally:
            await _release_fake(conn)

    monkeypatch.setattr(module, "acquire_conn", _acq)


def _route_acquire_via_connect(monkeypatch, module, connect):
    """Route `acquire_conn` through an async `connect(url)` factory.

    Used by the release-before-LLM tests, whose ``connect`` appends each opened
    connection to a list they later assert is fully closed. Under #804 those
    code paths open via ``acquire_conn``, so the pooled path must call the same
    factory and relinquish (close) the connection on context exit."""

    @asynccontextmanager
    async def _acq(url="", **_kw):
        conn = await connect(url)
        try:
            yield conn
        finally:
            await _release_fake(conn)

    monkeypatch.setattr(module, "acquire_conn", _acq)


class FakeConn:
    def __init__(self):
        self.fetchrow_calls = []
        self.execute_calls = []
        self.row = {
            "id": "00000000-0000-4000-8000-000000000001",
            "user_id": "00000000-0000-4000-8000-000000000002",
            "content": "updated",
            "fact_type": "observation",
            "confidence": 0.9,
            "namespace": "default",
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "metadata": {},
            "weaviate_id": None,
        }

    async def fetchrow(self, query, *params):
        self.fetchrow_calls.append((query, params))
        return self.row

    async def execute(self, query, *params):
        self.execute_calls.append((query, params))
        return "UPDATE 1"

    async def close(self):
        pass


def _service():
    from memory_service import MemoryService

    svc = MemoryService.__new__(MemoryService)
    svc.enabled = True
    svc.database_url = "postgresql://example"
    svc.store = None
    svc._initialized = True
    svc._ensure_initialized = AsyncMock()
    return svc


def test_consolidate_reraises_transient_llm_failure_for_worker(monkeypatch):
    import memory_service

    class FactsConn:
        async def fetch(self, query, *params):
            return [
                {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "content": "first",
                    "fact_type": "observation",
                    "confidence": 0.9,
                    "namespace": "default",
                    "created_at": datetime.now(timezone.utc),
                    "metadata": {},
                },
                {
                    "id": "00000000-0000-4000-8000-000000000002",
                    "content": "second",
                    "fact_type": "observation",
                    "confidence": 0.8,
                    "namespace": "default",
                    "created_at": datetime.now(timezone.utc),
                    "metadata": {},
                },
            ]

        async def close(self):
            pass

    svc = _service()
    svc._get_extraction_model = AsyncMock(return_value="ollama/test")
    svc._litellm_complete = AsyncMock(
        side_effect=TimeoutError("temporary LiteLLM timeout")
    )
    monkeypatch.setattr(
        memory_service, "connect_postgres", AsyncMock(return_value=FactsConn())
    )
    _also_route_acquire(monkeypatch, memory_service, FactsConn)

    try:
        asyncio.run(
            svc.consolidate(
                user_id="00000000-0000-4000-8000-000000000001",
                retry_transient=True,
            )
        )
    except TimeoutError as exc:
        assert str(exc) == "temporary LiteLLM timeout"
    else:
        raise AssertionError("worker-mode consolidation swallowed a transient error")


def test_consolidate_deactivates_weaviate_before_superseding_fact(monkeypatch):
    import memory_service

    events = []
    now = datetime.now(timezone.utc)
    facts = [
        {
            "id": UUID("00000000-0000-4000-8000-000000000001"),
            "content": "older",
            "fact_type": "observation",
            "confidence": 0.8,
            "namespace": "default",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "weaviate_id": "vector-old",
        },
        {
            "id": UUID("00000000-0000-4000-8000-000000000002"),
            "content": "newer",
            "fact_type": "observation",
            "confidence": 0.9,
            "namespace": "default",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "weaviate_id": "vector-new",
        },
    ]

    class PendingConn:
        async def fetch(self, _query, *_params):
            return []

        async def close(self):
            return None

    class FactsConn:
        async def fetch(self, _query, *_params):
            return facts

        async def close(self):
            return None

    class ApplyConn:
        def __init__(self):
            self.pending = []

        async def fetchrow(self, query, *_params):
            if "vector_sync_pending = true" in query:
                events.append("postgres")
                row = {
                    **facts[0],
                    "user_id": UUID("00000000-0000-4000-8000-000000000003"),
                    "is_active": False,
                }
                self.pending.append(row)
                return row

        async def fetch(self, query, *_params):
            if "vector_sync_pending = true" in query:
                return list(self.pending)
            return []

        async def execute(self, query, *_params):
            if "vector_sync_pending = false" in query:
                events.append("clear")
                self.pending.clear()
            return "OK"

        async def fetchval(self, _query, *_params):
            return 1

        async def close(self):
            return None

    connections = iter([PendingConn(), FactsConn(), ApplyConn()])
    monkeypatch.setattr(
        memory_service,
        "connect_postgres",
        AsyncMock(side_effect=lambda _url: next(connections)),
    )
    _also_route_acquire(monkeypatch, memory_service, lambda: next(connections))
    svc = _service()
    svc.max_facts = 100
    svc._get_extraction_model = AsyncMock(return_value="ollama/test")
    svc._litellm_complete = AsyncMock(
        return_value=(
            '[{"action":"supersede","source_indices":[0,1],'
            '"keep_index":1,"reason":"newer"}]'
        )
    )

    async def deactivate(fact_id, weaviate_id):
        events.append((fact_id, weaviate_id))

    svc.store = SimpleNamespace(deactivate_embedding=deactivate)

    result = asyncio.run(
        svc.consolidate(user_id="00000000-0000-4000-8000-000000000003")
    )

    assert result["facts_superseded"] == 1
    assert events == [
        "postgres",
        ("00000000-0000-4000-8000-000000000001", "vector-old"),
        "clear",
    ]


def test_consolidate_deactivates_weaviate_before_retention_expiry(monkeypatch):
    import memory_service

    events = []
    now = datetime.now(timezone.utc)
    facts = [
        {
            "id": UUID("00000000-0000-4000-8000-000000000001"),
            "content": "first",
            "fact_type": "observation",
            "confidence": 0.8,
            "namespace": "default",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "weaviate_id": "vector-old",
        },
        {
            "id": UUID("00000000-0000-4000-8000-000000000002"),
            "content": "second",
            "fact_type": "observation",
            "confidence": 0.9,
            "namespace": "default",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "weaviate_id": "vector-new",
        },
    ]

    class PendingConn:
        async def fetch(self, _query, *_params):
            return []

        async def close(self):
            return None

    class FactsConn:
        async def fetch(self, _query, *_params):
            return facts

        async def close(self):
            return None

    class ApplyConn:
        def __init__(self):
            self.pending = []

        async def fetchrow(self, query, *_params):
            if "vector_sync_pending = true" in query:
                events.append("postgres")
                row = {
                    **facts[0],
                    "user_id": UUID("00000000-0000-4000-8000-000000000003"),
                    "is_active": False,
                }
                self.pending.append(row)
                return row

        async def execute(self, query, *_params):
            if "vector_sync_pending = false" in query:
                events.append("clear")
                self.pending.clear()
            return "OK"

        async def fetchval(self, _query, *_params):
            return 2

        async def fetch(self, _query, *_params):
            if self.pending:
                return list(self.pending)
            return [{"id": facts[0]["id"], "updated_at": facts[0]["updated_at"]}]

        async def close(self):
            return None

    connections = iter([PendingConn(), FactsConn(), ApplyConn()])
    monkeypatch.setattr(
        memory_service,
        "connect_postgres",
        AsyncMock(side_effect=lambda _url: next(connections)),
    )
    _also_route_acquire(monkeypatch, memory_service, lambda: next(connections))
    svc = _service()
    svc.max_facts = 1
    svc._get_extraction_model = AsyncMock(return_value="ollama/test")
    svc._litellm_complete = AsyncMock(return_value="[]")

    async def deactivate(fact_id, weaviate_id):
        events.append((fact_id, weaviate_id))

    svc.store = SimpleNamespace(deactivate_embedding=deactivate)

    result = asyncio.run(
        svc.consolidate(user_id="00000000-0000-4000-8000-000000000003")
    )

    assert result["facts_expired"] == 1
    assert events == [
        "postgres",
        ("00000000-0000-4000-8000-000000000001", "vector-old"),
        "clear",
    ]


def test_pending_vector_sync_is_cleared_only_after_success(monkeypatch):
    svc = _service()
    events = []

    class Conn:
        async def fetch(self, _query, *_params):
            return [
                {
                    "id": UUID("00000000-0000-4000-8000-000000000001"),
                    "user_id": UUID("00000000-0000-4000-8000-000000000003"),
                    "content": "old",
                    "fact_type": "observation",
                    "confidence": 0.8,
                    "namespace": "default",
                    "is_active": False,
                    "weaviate_id": None,
                    "updated_at": datetime.now(timezone.utc),
                }
            ]

        async def execute(self, _query, *_params):
            events.append("clear")

    async def deactivate(fact_id, weaviate_id):
        events.append((fact_id, weaviate_id))

    svc.store = SimpleNamespace(deactivate_embedding=deactivate)

    asyncio.run(svc._reconcile_pending_vectors(Conn()))

    assert events == [
        ("00000000-0000-4000-8000-000000000001", None),
        "clear",
    ]


def test_pending_vector_sync_survives_failed_weaviate_update(caplog):
    svc = _service()
    events = []

    class Conn:
        async def fetch(self, _query, *_params):
            return [
                {
                    "id": UUID("00000000-0000-4000-8000-000000000001"),
                    "user_id": UUID("00000000-0000-4000-8000-000000000003"),
                    "content": "old",
                    "fact_type": "observation",
                    "confidence": 0.8,
                    "namespace": "default",
                    "is_active": False,
                    "weaviate_id": "vector-old",
                }
            ]

        async def execute(self, _query, *_params):
            events.append("clear")

    async def deactivate(_fact_id, _weaviate_id):
        raise ConnectionError("weaviate unavailable")

    svc.store = SimpleNamespace(deactivate_embedding=deactivate)

    asyncio.run(svc._reconcile_pending_vectors(Conn()))

    assert events == []
    assert "error_type=ConnectionError" in caplog.text

    with pytest.raises(ConnectionError, match="weaviate unavailable"):
        asyncio.run(
            svc._reconcile_pending_vectors(Conn(), retry_transient=True)
        )


def test_consolidation_sql_failure_has_no_vector_side_effect(monkeypatch):
    import memory_service

    now = datetime.now(timezone.utc)
    facts = [
        {
            "id": UUID("00000000-0000-4000-8000-000000000001"),
            "content": "older",
            "fact_type": "observation",
            "confidence": 0.8,
            "namespace": "default",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "weaviate_id": "vector-old",
        },
        {
            "id": UUID("00000000-0000-4000-8000-000000000002"),
            "content": "newer",
            "fact_type": "observation",
            "confidence": 0.9,
            "namespace": "default",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "weaviate_id": "vector-new",
        },
    ]

    class PendingConn:
        async def fetch(self, _query, *_params):
            return []

        async def close(self):
            return None

    class FactsConn(PendingConn):
        async def fetch(self, _query, *_params):
            return facts

    class FailingApplyConn(PendingConn):
        async def fetchrow(self, _query, *_params):
            raise RuntimeError("postgres write failed")

    connections = iter([PendingConn(), FactsConn(), FailingApplyConn()])
    monkeypatch.setattr(
        memory_service,
        "connect_postgres",
        AsyncMock(side_effect=lambda _url: next(connections)),
    )
    _also_route_acquire(monkeypatch, memory_service, lambda: next(connections))
    deactivate = AsyncMock()
    svc = _service()
    svc.max_facts = 100
    svc.store = SimpleNamespace(deactivate_embedding=deactivate)
    svc._get_extraction_model = AsyncMock(return_value="ollama/test")
    svc._litellm_complete = AsyncMock(
        return_value=(
            '[{"action":"supersede","source_indices":[0,1],'
            '"keep_index":1,"reason":"newer"}]'
        )
    )

    with pytest.raises(RuntimeError, match="postgres write failed"):
        asyncio.run(
            svc.consolidate(
                user_id="00000000-0000-4000-8000-000000000003"
            )
        )

    deactivate.assert_not_awaited()


def test_update_memory_retires_vector_reactivated_by_concurrent_update(
    monkeypatch,
):
    import memory_service

    memory_id = "00000000-0000-4000-8000-000000000001"
    user_id = "00000000-0000-4000-8000-000000000002"
    now = datetime.now(timezone.utc)
    original = {
        "id": UUID(memory_id),
        "user_id": UUID(user_id),
        "content": "old",
        "fact_type": "observation",
        "confidence": 0.9,
        "namespace": "default",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        "metadata": {},
        "weaviate_id": "legacy-vector",
    }

    class Conn:
        def __init__(self):
            self.calls = 0

        async def fetchrow(self, _query, *_params):
            self.calls += 1
            if self.calls == 1:
                return original
            if self.calls == 2:
                return {**original, "content": "new"}
            return {"is_active": False, "vector_sync_pending": True}

        async def execute(self, *_args):
            return "OK"

        async def fetch(self, _query, *_params):
            return [
                {
                    **original,
                    "content": "new",
                    "is_active": False,
                    "vector_sync_pending": True,
                    "weaviate_id": memory_id,
                }
            ]

        async def close(self):
            return None

    conn = Conn()
    monkeypatch.setattr(
        memory_service, "connect_postgres", AsyncMock(return_value=conn)
    )
    _also_route_acquire(monkeypatch, memory_service, lambda: conn)
    store = SimpleNamespace(
        update_embedding=AsyncMock(return_value=memory_id),
        deactivate_embedding=AsyncMock(),
    )
    svc = _service()
    svc.store = store

    asyncio.run(svc.update_memory(memory_id, user_id, {"content": "new"}))

    store.deactivate_embedding.assert_awaited_once_with(memory_id, memory_id)


@pytest.mark.parametrize("target_active", [False, True])
def test_update_memory_reconciles_public_active_state(monkeypatch, target_active):
    import memory_service

    memory_id = "00000000-0000-4000-8000-000000000001"
    user_id = "00000000-0000-4000-8000-000000000002"
    now = datetime.now(timezone.utc)
    row = {
        "id": UUID(memory_id),
        "user_id": UUID(user_id),
        "content": "fact",
        "fact_type": "observation",
        "confidence": 0.9,
        "namespace": "default",
        "is_active": target_active,
        "created_at": now,
        "updated_at": now,
        "metadata": {},
        "weaviate_id": memory_id,
    }

    class Conn:
        def __init__(self):
            self.fetchrow_calls = 0
            self.update_query = ""

        async def fetchrow(self, query, *_params):
            self.fetchrow_calls += 1
            if self.fetchrow_calls == 1:
                return {**row, "is_active": not target_active}
            self.update_query = query
            return row

        async def fetch(self, _query, *_params):
            return [row]

        async def execute(self, *_args):
            return "OK"

        async def close(self):
            return None

    conn = Conn()
    monkeypatch.setattr(
        memory_service, "connect_postgres", AsyncMock(return_value=conn)
    )
    _also_route_acquire(monkeypatch, memory_service, lambda: conn)
    store = SimpleNamespace(
        update_embedding=AsyncMock(return_value=memory_id),
        deactivate_embedding=AsyncMock(),
    )
    svc = _service()
    svc.store = store

    asyncio.run(
        svc.update_memory(memory_id, user_id, {"is_active": target_active})
    )

    assert "vector_sync_pending = true" in conn.update_query
    if target_active:
        store.update_embedding.assert_awaited_once()
        store.deactivate_embedding.assert_not_awaited()
    else:
        store.deactivate_embedding.assert_awaited_once_with(memory_id, memory_id)
        store.update_embedding.assert_not_awaited()


def test_delete_memory_keeps_pending_marker_when_vector_sync_fails(monkeypatch):
    import memory_service

    memory_id = "00000000-0000-4000-8000-000000000001"
    user_id = "00000000-0000-4000-8000-000000000002"
    events = []

    class Conn:
        async def fetchrow(self, query, *_params):
            assert "vector_sync_pending = true" in query
            return {"id": UUID(memory_id)}

        async def fetch(self, _query, *_params):
            return [
                {
                    "id": UUID(memory_id),
                    "user_id": UUID(user_id),
                    "content": "fact",
                    "fact_type": "observation",
                    "confidence": 0.9,
                    "namespace": "default",
                    "is_active": False,
                    "weaviate_id": memory_id,
                }
            ]

        async def execute(self, *_args):
            events.append("clear")

        async def close(self):
            return None

    async def fail_deactivate(*_args):
        raise ConnectionError("weaviate unavailable")

    monkeypatch.setattr(
        memory_service, "connect_postgres", AsyncMock(return_value=Conn())
    )
    _also_route_acquire(monkeypatch, memory_service, Conn)
    svc = _service()
    svc.store = SimpleNamespace(deactivate_embedding=fail_deactivate)

    assert asyncio.run(svc.delete_memory(memory_id, user_id)) is True
    assert events == []


def test_consolidation_skips_fact_edited_during_llm_round_trip(monkeypatch):
    import memory_service

    now = datetime.now(timezone.utc)
    facts = [
        {
            "id": UUID("00000000-0000-4000-8000-000000000001"),
            "content": "older",
            "fact_type": "observation",
            "confidence": 0.8,
            "namespace": "default",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "weaviate_id": "vector-old",
        },
        {
            "id": UUID("00000000-0000-4000-8000-000000000002"),
            "content": "newer",
            "fact_type": "observation",
            "confidence": 0.9,
            "namespace": "default",
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "weaviate_id": "vector-new",
        },
    ]

    class PendingConn:
        async def fetch(self, _query, *_params):
            return []

        async def close(self):
            return None

    class FactsConn(PendingConn):
        async def fetch(self, _query, *_params):
            return facts

    class ApplyConn(PendingConn):
        async def fetchrow(self, query, *params):
            assert "updated_at = $3" in query
            assert params[2] == now
            return None

        async def fetchval(self, _query, *_params):
            return 2

    connections = iter([PendingConn(), FactsConn(), ApplyConn()])
    monkeypatch.setattr(
        memory_service,
        "connect_postgres",
        AsyncMock(side_effect=lambda _url: next(connections)),
    )
    _also_route_acquire(monkeypatch, memory_service, lambda: next(connections))
    svc = _service()
    svc.max_facts = 100
    svc.store = SimpleNamespace(deactivate_embedding=AsyncMock())
    svc._get_extraction_model = AsyncMock(return_value="ollama/test")
    svc._litellm_complete = AsyncMock(
        return_value=(
            '[{"action":"supersede","source_indices":[0,1],'
            '"keep_index":1,"reason":"newer"}]'
        )
    )

    result = asyncio.run(
        svc.consolidate(user_id="00000000-0000-4000-8000-000000000003")
    )

    assert result["facts_superseded"] == 0
    svc.store.deactivate_embedding.assert_not_awaited()


def test_update_memory_is_scoped_to_owner(monkeypatch):
    import memory_service

    conn = FakeConn()
    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=conn))
    _also_route_acquire(monkeypatch, memory_service, lambda: conn)

    asyncio.run(
        _service().update_memory(
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
            {"content": "updated"},
        )
    )

    select_query, select_params = conn.fetchrow_calls[0]
    update_query, update_params = conn.fetchrow_calls[1]
    assert "WHERE id = $1 AND user_id = $2" in select_query
    assert "AND user_id = $" in update_query
    assert [str(value) for value in select_params] == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ]
    assert [str(value) for value in update_params[-2:]] == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ]


def test_memory_database_boundaries_use_uuid_objects(monkeypatch):
    import memory_service

    user_id = "00000000-0000-4000-8000-000000000002"

    class StrictUuidConn(FakeConn):
        async def fetch(self, query, *params):
            assert isinstance(params[0], UUID)
            return []

        async def fetchval(self, query, *params):
            assert isinstance(params[0], UUID)
            return 0

    conn = StrictUuidConn()
    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=conn))
    _also_route_acquire(monkeypatch, memory_service, lambda: conn)

    listed = asyncio.run(_service().list_memories(user_id))

    assert listed["total"] == 0


def test_delete_memory_is_scoped_to_owner(monkeypatch):
    import memory_service

    conn = FakeConn()
    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=conn))
    _also_route_acquire(monkeypatch, memory_service, lambda: conn)

    success = asyncio.run(
        _service().delete_memory(
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
        )
    )

    update_query, update_params = conn.fetchrow_calls[0]
    assert success is True
    assert "WHERE id = $1 AND user_id = $2" in update_query
    assert [str(value) for value in update_params] == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ]


class ExtractionConn:
    def __init__(self):
        self.closed = False
        self.execute_calls = []
        self.fetchval_calls = []

    async def execute(self, query, *params):
        self.execute_calls.append((query, params))
        return "OK"

    async def fetchval(self, query, *params):
        self.fetchval_calls.append((query, params))
        return 0

    async def fetchrow(self, query, *params):
        return {
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

    def transaction(self):
        conn = self

        class Transaction:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return Transaction()

    async def close(self):
        self.closed = True


def _extraction_service():
    svc = _service()
    svc.max_facts = 1000
    svc._get_extraction_model = AsyncMock(return_value="ollama/test")
    svc.store = SimpleNamespace(store_embedding=AsyncMock(return_value=None))
    return svc


def test_extract_releases_db_during_llm_and_locks_quota_transaction(monkeypatch):
    import memory_service

    connections = []

    async def connect(_url):
        conn = ExtractionConn()
        connections.append(conn)
        return conn

    async def complete(*args, **kwargs):
        assert connections and all(conn.closed for conn in connections)
        return '[{"content":"Uses Atlas","fact_type":"preference","confidence":0.9}]'

    svc = _extraction_service()
    svc._litellm_complete = complete
    monkeypatch.setattr(memory_service, "connect_postgres", connect)
    _route_acquire_via_connect(monkeypatch, memory_service, connect)

    result = asyncio.run(
        svc.extract_facts(
            "00000000-0000-4000-8000-000000000002",
            [{"role": "user", "content": "I use Atlas"}],
        )
    )

    assert result["status"] == "completed"
    assert len(connections) >= 2
    transaction_sql = "\n".join(query for query, _ in connections[1].execute_calls)
    assert "pg_advisory_xact_lock" in transaction_sql


def test_extract_marks_session_failed_for_malformed_fact_shape(monkeypatch):
    import memory_service

    connections = []

    async def connect(_url):
        conn = ExtractionConn()
        connections.append(conn)
        return conn

    svc = _extraction_service()
    svc._litellm_complete = AsyncMock(return_value='["not-an-object"]')
    monkeypatch.setattr(memory_service, "connect_postgres", connect)
    _route_acquire_via_connect(monkeypatch, memory_service, connect)

    result = asyncio.run(
        svc.extract_facts(
            "00000000-0000-4000-8000-000000000002",
            [{"role": "user", "content": "hello"}],
        )
    )

    assert result["status"] == "failed"
    assert any(
        "status = 'failed'" in query
        for conn in connections
        for query, _ in conn.execute_calls
    )


def test_recall_and_summarize_release_db_before_llm(monkeypatch):
    import memory_service

    class ReadConn(ExtractionConn):
        async def fetchrow(self, query, *params):
            return {
                "id": "00000000-0000-4000-8000-000000000001",
                "content": "Uses Atlas",
                "fact_type": "preference",
                "confidence": 0.9,
                "namespace": "default",
                "is_active": True,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "metadata": {},
            }

        async def fetch(self, query, *params):
            if "vector_sync_pending = true" in query:
                return []
            return [
                {
                    "content": "Uses Atlas",
                    "fact_type": "preference",
                    "confidence": 0.9,
                }
            ]

        async def fetchval(self, query, *params):
            return 1

    connections = []

    async def connect(_url):
        conn = ReadConn()
        connections.append(conn)
        return conn

    async def complete(*args, **kwargs):
        assert all(conn.closed for conn in connections)
        return "Atlas memory summary"

    svc = _extraction_service()
    svc._litellm_complete = complete
    svc.store = SimpleNamespace(
        search_similar=AsyncMock(
            return_value=[{"pg_fact_id": "00000000-0000-4000-8000-000000000001"}]
        )
    )
    monkeypatch.setattr(memory_service, "connect_postgres", connect)
    _route_acquire_via_connect(monkeypatch, memory_service, connect)

    recalled = asyncio.run(
        svc.recall("00000000-0000-4000-8000-000000000002", "Atlas")
    )
    summarized = asyncio.run(
        svc.summarize("00000000-0000-4000-8000-000000000002")
    )

    assert recalled["context_summary"] == "Atlas memory summary"
    assert summarized["summary"] == "Atlas memory summary"


@pytest.mark.asyncio
async def test_search_pgvector_scopes_query_to_user_id(monkeypatch):
    # Recall's tenant isolation rests entirely on the vector search's user_id
    # filter (the Postgres re-fetch has none). Lock in that _search_pgvector both
    # filters on AND binds the caller's user_id, so a refactor can't silently
    # drop it → cross-tenant memory leak.
    import memory_store
    from memory_store import _to_uuid

    executed = []

    class Conn:
        async def fetch(self, query, *params):
            executed.append((query, params))
            return []

        async def close(self):
            pass

    store = memory_store.MemoryStore("postgresql://atlas")
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3])
    )
    # #804: _search_pgvector draws from the shared pool via acquire_conn.
    _also_route_acquire(monkeypatch, memory_store, Conn)

    uid = "00000000-0000-4000-8000-000000000009"
    await store._search_pgvector("hello", uid, "default", 5)

    assert executed, "search must execute a query"
    query, params = executed[0]
    assert "user_id = $2" in query
    assert params[1] == _to_uuid(uid)  # $2 is the caller's user_id, canonicalized
