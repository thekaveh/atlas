from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock


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

    try:
        asyncio.run(
            svc.consolidate(user_id="user-1", retry_transient=True)
        )
    except TimeoutError as exc:
        assert str(exc) == "temporary LiteLLM timeout"
    else:
        raise AssertionError("worker-mode consolidation swallowed a transient error")


def test_update_memory_is_scoped_to_owner(monkeypatch):
    import memory_service

    conn = FakeConn()
    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=conn))

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


def test_delete_memory_is_scoped_to_owner(monkeypatch):
    import memory_service

    conn = FakeConn()
    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=conn))

    success = asyncio.run(
        _service().delete_memory(
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
        )
    )

    select_query, select_params = conn.fetchrow_calls[0]
    update_query, update_params = conn.execute_calls[0]
    assert success is True
    assert "WHERE id = $1 AND user_id = $2" in select_query
    assert "WHERE id = $1 AND user_id = $2" in update_query
    assert [str(value) for value in select_params] == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ]
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

    recalled = asyncio.run(
        svc.recall("00000000-0000-4000-8000-000000000002", "Atlas")
    )
    summarized = asyncio.run(
        svc.summarize("00000000-0000-4000-8000-000000000002")
    )

    assert recalled["context_summary"] == "Atlas memory summary"
    assert summarized["summary"] == "Atlas memory summary"
