"""
Unit tests for MemoryService._get_extraction_model().

Resolution order (post-B5):
  1. self.extraction_model (LANGMEM_EXTRACTION_MODEL env / explicit arg)
  2. LITELLM_DEFAULT_MODEL env var
  3. RuntimeError — no asyncpg connection is opened

These tests verify correctness and confirm no DB connection is attempted.
They are NOT run by the bootstrapper CI suite (which lives under
bootstrapper/tests/); they require the backend's own dependencies
(asyncpg, httpx) but do not need the full Docker stack.
"""

import asyncio
import os
import re
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import UUID

import pytest


class TestGetExtractionModel(unittest.TestCase):
    """Tests for MemoryService._get_extraction_model (env-var resolution, no DB)."""

    def _make_service(self, extraction_model: str = ""):
        """Construct a MemoryService with minimal env, bypassing store init."""
        # We import here to avoid module-level import errors if asyncpg / httpx
        # are not installed in the test runner environment.
        from memory_service import MemoryService  # type: ignore[import]

        svc = MemoryService.__new__(MemoryService)
        svc.extraction_model = extraction_model
        svc.database_url = "postgresql://user:pw@localhost/test"
        svc.litellm_url = "http://litellm:4000"
        svc.litellm_api_key = ""
        svc.weaviate_url = ""
        svc.namespace = "default"
        svc.max_facts = 1000
        svc.embedding_model = ""
        svc.store = None
        svc._initialized = False
        svc._init_lock = asyncio.Lock()
        svc.enabled = True
        return svc

    def _run(self, coro):
        return asyncio.run(coro)

    def test_explicit_extraction_model_returned(self):
        """When self.extraction_model is set, it is returned immediately."""
        svc = self._make_service(extraction_model="anthropic/claude-sonnet-4-5")
        result = self._run(svc._get_extraction_model())
        self.assertEqual(result, "anthropic/claude-sonnet-4-5")

    def test_env_var_returned_when_no_explicit_model(self):
        """LITELLM_DEFAULT_MODEL env var is returned when extraction_model is empty."""
        svc = self._make_service(extraction_model="")
        with patch.dict(os.environ, {"LITELLM_DEFAULT_MODEL": "ollama/qwen3.6:latest"}):
            result = self._run(svc._get_extraction_model())
        self.assertEqual(result, "ollama/qwen3.6:latest")

    def test_explicit_model_takes_priority_over_env(self):
        """self.extraction_model beats LITELLM_DEFAULT_MODEL when both are set."""
        svc = self._make_service(extraction_model="openai/gpt-4o")
        with patch.dict(os.environ, {"LITELLM_DEFAULT_MODEL": "ollama/qwen3.6:latest"}):
            result = self._run(svc._get_extraction_model())
        self.assertEqual(result, "openai/gpt-4o")

    def test_raises_runtime_error_when_both_unset(self):
        """RuntimeError is raised when neither extraction_model nor env var is set."""
        svc = self._make_service(extraction_model="")
        env = {k: v for k, v in os.environ.items() if k != "LITELLM_DEFAULT_MODEL"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                self._run(svc._get_extraction_model())
        self.assertIn("LITELLM_DEFAULT_MODEL", str(ctx.exception))

    def test_no_asyncpg_connect_called(self):
        """_get_extraction_model must NOT open a DB connection under any path."""
        import asyncpg  # type: ignore[import]

        svc = self._make_service(extraction_model="")
        with patch.dict(os.environ, {"LITELLM_DEFAULT_MODEL": "ollama/qwen3.6:latest"}):
            with patch.object(asyncpg, "connect", new_callable=AsyncMock) as mock_connect:
                self._run(svc._get_extraction_model())
                mock_connect.assert_not_called()

    def test_no_asyncpg_connect_called_on_error_path(self):
        """No DB connection even when both model sources are absent (error path)."""
        import asyncpg  # type: ignore[import]

        svc = self._make_service(extraction_model="")
        env = {k: v for k, v in os.environ.items() if k != "LITELLM_DEFAULT_MODEL"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(asyncpg, "connect", new_callable=AsyncMock) as mock_connect:
                with self.assertRaises(RuntimeError):
                    self._run(svc._get_extraction_model())
                mock_connect.assert_not_called()


def _minimal_service():
    from memory_service import MemoryService

    service = MemoryService.__new__(MemoryService)
    service.enabled = True
    service.database_url = "postgresql://example"
    service.namespace = "default"
    service.store = None
    service._initialized = True
    service._ensure_initialized = AsyncMock()
    return service


def _extraction_service():
    service = _minimal_service()
    service.max_facts = 1000
    service._get_extraction_model = AsyncMock(return_value="ollama/test")
    service.store = SimpleNamespace(store_embedding=AsyncMock(return_value=None))
    return service


@asynccontextmanager
async def _acquire_connection(factory):
    yield factory()


class _PendingConn:
    async def fetch(self, _query, *_params):
        return []

    async def close(self):
        return None


class _GroupConn(_PendingConn):
    async def fetch(self, query, *_params):
        assert "SELECT DISTINCT namespace" in query
        return [{"namespace": "private"}, {"namespace": "project-x"}]


class _FactsConn(_PendingConn):
    def __init__(self, namespace):
        self.namespace = namespace

    async def fetch(self, query, *params):
        assert "namespace = $2" in query and params[1] == self.namespace
        now = datetime.now(timezone.utc)
        offset = 0 if self.namespace == "private" else 2
        return [
            {
                "id": UUID(int=offset + index),
                "content": f"{self.namespace} fact {index}",
                "fact_type": "observation",
                "confidence": 0.8,
                "namespace": self.namespace,
                "created_at": now,
                "updated_at": now,
                "metadata": {},
                "weaviate_id": None,
            }
            for index in (1, 2)
        ]


class _ApplyConn(_PendingConn):
    async def fetchval(self, _query, *_params):
        return 2


class _RecordingConn(_PendingConn):
    def __init__(self, *, fail_insert=False):
        self.fail_insert = fail_insert
        self.calls = []

    def transaction(self):
        return _acquire_connection(lambda: self)

    async def execute(self, query, *params):
        self.calls.append((query, params))
        if self.fail_insert and "INSERT INTO public.memory_sessions" in query:
            raise ConnectionError("response lost after commit")
        return "OK"

    async def fetch(self, query, *params):
        self.calls.append((query, params))
        return []

    async def fetchval(self, query, *params):
        self.calls.append((query, params))
        return 0

    async def fetchrow(self, query, *params):
        self.calls.append((query, params))
        return {"created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)}


def _wire_connections(monkeypatch, memory_service, connections):
    iterator = iter(connections)
    monkeypatch.setattr(
        memory_service, "connect_postgres", AsyncMock(side_effect=lambda _url: next(iterator))
    )
    monkeypatch.setattr(
        memory_service, "acquire_conn", lambda *_args, **_kwargs: _acquire_connection(lambda: next(iterator))
    )


@pytest.mark.parametrize(
    "action_data",
    [
        "not-an-object",
        {"action": "delete", "source_indices": [0, 1], "keep_index": 1},
        {"action": "merge", "source_indices": [0.0, 1], "keep_index": 1},
        {"action": "merge", "source_indices": [0, 1], "keep_index": 2},
        {"action": "merge", "source_indices": [0, 0], "keep_index": 0},
        {"action": "merge", "source_indices": [0, 1], "keep_index": True},
    ],
)
def test_consolidation_rejects_unsafe_llm_actions(action_data):
    from memory_service import _validate_consolidation_action

    assert _validate_consolidation_action(action_data, 3) is None


def test_consolidation_accepts_the_prompt_contract():
    from memory_service import _validate_consolidation_action

    action = {"action": "supersede", "source_indices": [0, 2],
              "keep_index": 2, "reason": "newer"}
    assert _validate_consolidation_action(action, 3) == (
        "supersede", [0, 2], 2, "newer"
    )


def test_consolidation_isolates_namespaces(monkeypatch):
    import memory_service

    connections = [_PendingConn(), _GroupConn(), _FactsConn("private"),
                   _ApplyConn(), _FactsConn("project-x"), _ApplyConn(),
                   _ApplyConn()]
    _wire_connections(monkeypatch, memory_service, connections)
    service = _minimal_service()
    service.max_facts = 100
    service.store = SimpleNamespace(deactivate_embedding=AsyncMock())
    service._get_extraction_model = AsyncMock(return_value="ollama/test")
    prompts = []

    async def complete(**kwargs):
        prompts.append(kwargs["prompt"])
        return "[]"

    service._litellm_complete = complete
    result = asyncio.run(service.consolidate(
        user_id="00000000-0000-4000-8000-000000000005"
    ))
    assert result["facts_reviewed"] == 4 and len(prompts) == 2
    assert "project-x" not in prompts[0] and "private" not in prompts[1]


def test_consolidation_applies_user_fact_limit_across_single_fact_namespaces(
    monkeypatch,
):
    import memory_service

    now = datetime.now(timezone.utc)

    class Groups(_PendingConn):
        async def fetch(self, _query, *_params):
            return [{"namespace": name} for name in ("a", "b", "c")]

    class OneFact(_PendingConn):
        async def fetch(self, _query, *_params):
            return [{"id": UUID(int=1), "content": "fact", "fact_type": "observation",
                     "confidence": 0.8, "namespace": "a", "created_at": now,
                     "updated_at": now, "metadata": {}, "weaviate_id": None}]

    class Retention(_PendingConn):
        async def fetchval(self, _query, *_params):
            return 3

        async def fetch(self, query, *_params):
            if "ORDER BY updated_at ASC" in query:
                return [{"id": UUID(int=1), "updated_at": now}]
            return []

        async def fetchrow(self, _query, *_params):
            return {"id": UUID(int=1), "weaviate_id": None}

    connections = [_PendingConn(), Groups(), OneFact(), OneFact(), OneFact(), Retention()]
    _wire_connections(monkeypatch, memory_service, connections)
    service = _minimal_service()
    service.max_facts = 2
    service.store = SimpleNamespace(deactivate_embedding=AsyncMock())
    result = asyncio.run(service.consolidate(user_id=str(UUID(int=5))))

    assert result["facts_reviewed"] == 3
    assert result["facts_expired"] == 1


@pytest.mark.asyncio
async def test_ambiguous_session_insert_failure_terminalizes_row(monkeypatch):
    import memory_service

    connection = _RecordingConn(fail_insert=True)
    monkeypatch.setattr(memory_service, "acquire_conn",
                        lambda *_a, **_k: _acquire_connection(lambda: connection))
    service = _minimal_service()
    with pytest.raises(ConnectionError, match="response lost after commit"):
        await service.extract_facts(
            str(UUID(int=1)), [{"role": "user", "content": "remember this"}]
        )
    assert any("SET status = 'failed'" in query for query, _ in connection.calls)


@pytest.mark.asyncio
async def test_configured_namespace_is_used_when_omitted(monkeypatch):
    import memory_service

    connection = _RecordingConn()
    monkeypatch.setenv("LANGMEM_NAMESPACE", "private")
    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=connection))
    monkeypatch.setattr(memory_service, "acquire_conn",
                        lambda *_a, **_k: _acquire_connection(lambda: connection))
    service = memory_service.MemoryService()
    service._initialized = True
    service._ensure_initialized = AsyncMock()
    service._get_extraction_model = AsyncMock(return_value="ollama/test")
    service._litellm_complete = AsyncMock(return_value='[{"content":"Uses Atlas"}]')
    service.store = SimpleNamespace(store_embedding=AsyncMock(return_value=None),
                                    search_similar=AsyncMock(return_value=[]))
    user_id = str(UUID(int=1))
    await service.extract_facts(user_id, [{"role": "user", "content": "Atlas"}])
    await service.recall(user_id, "Atlas")
    await service.summarize(user_id)
    await service.list_memories(user_id)
    namespace_params = [params[1] for query, params in connection.calls
                        if "namespace = $2" in query and len(params) > 1]
    assert namespace_params == ["private"] * 4
    assert service.store.store_embedding.await_args.kwargs["namespace"] == "private"
    assert service.store.search_similar.await_args.kwargs["namespace"] == "private"


@pytest.mark.asyncio
async def test_extract_cancellation_waits_for_durable_session_failure(monkeypatch):
    import memory_service

    llm_started = asyncio.Event()
    failure_started = asyncio.Event()
    allow_failure = asyncio.Event()
    failure_completed = False

    class Conn(_RecordingConn):
        async def execute(self, query, *params):
            nonlocal failure_completed
            await super().execute(query, *params)
            if "SET status = 'failed'" in query:
                failure_started.set()
                await allow_failure.wait()
                failure_completed = True
            return "OK"

    async def blocked_llm(**_kwargs):
        llm_started.set()
        await asyncio.Future()

    conn = Conn()
    monkeypatch.setattr(memory_service, "acquire_conn",
                        lambda *_a, **_k: _acquire_connection(lambda: conn))
    service = _extraction_service()
    service._litellm_complete = blocked_llm
    task = asyncio.create_task(service.extract_facts(
        str(UUID(int=1)), [{"role": "user", "content": "remember this"}]
    ))
    await llm_started.wait()
    task.cancel()
    await failure_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    allow_failure.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert failure_completed is True


@pytest.mark.asyncio
async def test_extract_cancellation_during_session_insert_terminalizes_row(monkeypatch):
    import memory_service

    insert_visible = asyncio.Event()
    failure_started = asyncio.Event()
    allow_failure = asyncio.Event()

    class Conn(_RecordingConn):
        async def execute(self, query, *params):
            await super().execute(query, *params)
            if "INSERT INTO public.memory_sessions" in query:
                insert_visible.set()
                await asyncio.Future()
            if "SET status = 'failed'" in query:
                failure_started.set()
                await allow_failure.wait()
            return "OK"

    conn = Conn()
    monkeypatch.setattr(memory_service, "acquire_conn",
                        lambda *_a, **_k: _acquire_connection(lambda: conn))
    task = asyncio.create_task(_extraction_service().extract_facts(
        str(UUID(int=1)), [{"role": "user", "content": "remember this"}]
    ))
    await insert_visible.wait()
    task.cancel()
    await failure_started.wait()
    allow_failure.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_durable_extraction_failure_retries_terminal_update(monkeypatch):
    import memory_service

    attempts = 0

    class Conn(_RecordingConn):
        async def execute(self, query, *params):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("temporary database failure")
            return await super().execute(query, *params)

    monkeypatch.setattr(memory_service, "acquire_conn",
                        lambda *_a, **_k: _acquire_connection(Conn))
    await _extraction_service()._mark_extraction_failed_durably(
        UUID(int=1), asyncio.CancelledError()
    )
    assert attempts == 2


def test_recall_refetch_rejects_cross_tenant_vector_hits(monkeypatch):
    import memory_service

    fetchrows = []

    class Conn(_RecordingConn):
        async def fetchrow(self, query, *params):
            fetchrows.append((query, params))
            return None

    service = _extraction_service()
    service.store = SimpleNamespace(search_similar=AsyncMock(return_value=[
        {"pg_fact_id": str(UUID(int=1))}
    ]))
    monkeypatch.setattr(memory_service, "connect_postgres",
                        AsyncMock(side_effect=lambda _url: Conn()))
    monkeypatch.setattr(memory_service, "acquire_conn",
                        lambda *_a, **_k: _acquire_connection(Conn))
    result = asyncio.run(service.recall(str(UUID(int=2)), "Atlas", namespace="private"))
    assert result["memories"] == []
    query, params = fetchrows[-1]
    assert "user_id = $3" in query and "namespace = $4" in query
    assert params[2:] == (UUID(int=2), "private")


# --- owned-memory review surface (#1206) ------------------------------------


class _FactTable:
    """In-memory public.memory_facts answering exactly the queries the list,
    update, delete, reconcile and recall paths issue."""

    def __init__(self, *rows):
        self.rows = {row["id"]: dict(row) for row in rows}
        self.recall_queries = []

    async def fetch(self, query, *params):
        if "vector_sync_pending = true" in query:
            return [dict(r) for r in self.rows.values() if r["vector_sync_pending"]]
        return [dict(r) for r in self.rows.values() if r["is_active"]]

    async def fetchval(self, query, *params):
        return sum(1 for r in self.rows.values() if r["is_active"])

    async def execute(self, query, *params):
        if "SET vector_sync_pending = false" in query:
            self.rows[params[0]]["vector_sync_pending"] = False

    async def close(self):
        return None

    async def fetchrow(self, query, *params):
        for marker, handler in (
            ("SELECT * FROM public.memory_facts", self._owned_row),
            ("embedding IS NOT NULL", self._state_row),
            ("SET is_active = false, vector_sync_pending = true", self._soft_delete),
            ("UPDATE public.memory_facts SET", self._update),
        ):
            if marker in query:
                return handler(query, params)
        self.recall_queries.append(query)
        return self._recall_row(params)

    def _owned_row(self, _query, params):
        row = self.rows.get(params[0])
        return dict(row) if row and row["user_id"] == params[1] else None

    def _state_row(self, _query, params):
        return dict(self.rows[params[0]])

    def _soft_delete(self, _query, params):
        row = self._owned_row(None, params)
        if row is None:
            return None
        self.rows[params[0]].update(is_active=False, vector_sync_pending=True)
        return {"id": row["id"]}

    def _update(self, query, params):
        row = self.rows[params[-2]]
        for field, index in re.findall(r"(\w+) = \$(\d+)", query.split("WHERE")[0]):
            row[field] = params[int(index) - 1]
        row["vector_sync_pending"] = "vector_sync_pending = true" in query
        return dict(row)

    def _recall_row(self, params):
        row = self.rows.get(params[0])
        return dict(row) if row and row["is_active"] and row["user_id"] == params[2] else None


def _fact_row(number, user, content, **overrides):
    now = datetime.now(timezone.utc)
    row = {"id": UUID(int=number), "user_id": UUID(int=user), "content": content,
           "fact_type": "preference", "confidence": 0.9, "namespace": "default",
           "is_active": True, "created_at": now, "updated_at": now, "metadata": "{}",
           "source_conversation_id": None, "source_message_ids": "[]",
           "weaviate_id": f"w-{number}", "vector_sync_pending": False, "embedding_present": True}
    row.update(overrides)
    return row


def _memory_service_over(monkeypatch, table, vector_hits=()):
    import memory_service

    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=table))
    monkeypatch.setattr(memory_service, "acquire_conn",
                        lambda *_a, **_k: _acquire_connection(lambda: table))
    service = _extraction_service()
    service._litellm_complete = AsyncMock(return_value="summary")
    service.store = SimpleNamespace(
        update_embedding=AsyncMock(return_value="w-new"), deactivate_embedding=AsyncMock(),
        search_similar=AsyncMock(return_value=[{"pg_fact_id": str(h)} for h in vector_hits]),
        weaviate_url="http://weaviate:8080", backend="weaviate",
    )
    return service


def test_listing_shows_provenance_or_says_it_was_not_recorded(monkeypatch):
    conversation = UUID(int=77)
    table = _FactTable(
        _fact_row(1, 9, "likes tea", source_conversation_id=conversation,
                  source_message_ids='["m1", "m2"]'),
        _fact_row(2, 9, "added by hand"),
    )
    service = _memory_service_over(monkeypatch, table)

    facts = {f["content"]: f for f in asyncio.run(service.list_memories(str(UUID(int=9))))["memories"]}

    from memory_models import MemoryFact

    assert MemoryFact(**facts["likes tea"]).model_dump()["source_message_ids"] == ["m1", "m2"]
    assert facts["likes tea"]["source_conversation_id"] == str(conversation)
    assert facts["likes tea"]["origin"] == "recorded"
    assert facts["added by hand"]["origin"] == "not recorded"
    assert facts["added by hand"]["source_conversation_id"] is None


def test_a_corrected_fact_is_what_recall_returns(monkeypatch):
    table = _FactTable(_fact_row(1, 9, "likes coffee"))
    service = _memory_service_over(monkeypatch, table, vector_hits=[UUID(int=1)])
    user = str(UUID(int=9))

    asyncio.run(service.update_memory(str(UUID(int=1)), user, {"content": "likes tea"}))
    recalled = asyncio.run(service.recall(user, "drinks"))

    assert [m["content"] for m in recalled["memories"]] == ["likes tea"]


def test_delete_report_names_every_store_and_what_survives(monkeypatch):
    table = _FactTable(_fact_row(1, 9, "likes coffee"))
    service = _memory_service_over(monkeypatch, table)

    report = asyncio.run(service.delete_memory_report(str(UUID(int=1)), str(UUID(int=9))))

    assert report["deletion"] == "soft"
    assert report["postgres"] == {"is_active": False, "row_retained": True}
    assert report["weaviate"]["object_removed"] is False
    assert report["weaviate"]["deactivated"] is True and report["weaviate"]["sync_pending"] is False
    assert report["pgvector"] == {"embedding_cleared": False, "embedding_present": True}
    for store in ("memory_facts", "Weaviate object", "memory_consolidation_log", "source conversation"):
        assert any(store in item for item in report["retained"]), store
    assert asyncio.run(service.delete_memory_report(str(UUID(int=1)), str(UUID(int=8)))) is None


@pytest.mark.parametrize(("backend", "url", "expected"), [
    # pgvector serves recall: the Weaviate object keeps isActive=true until a rebuild.
    ("pgvector", "http://weaviate:8080",
     {"deactivated": False, "awaiting_rebuild": True}),
    (None, None, {"configured": False}),
])
def test_delete_report_does_not_claim_a_weaviate_deactivation_that_did_not_happen(
    monkeypatch, backend, url, expected
):
    table = _FactTable(_fact_row(1, 9, "likes coffee"))
    service = _memory_service_over(monkeypatch, table)
    service.store.backend, service.store.weaviate_url = backend, url

    report = asyncio.run(service.delete_memory_report(str(UUID(int=1)), str(UUID(int=9))))

    assert {key: report["weaviate"][key] for key in expected} == expected


def test_a_deleted_fact_leaves_recall_at_once_even_with_a_stale_vector(monkeypatch):
    """The documented bound is immediate: recall re-reads is_active, so a
    vector Weaviate or pgvector still returns (here: deactivation failed and
    sync stays pending) cannot bring the fact back."""
    table = _FactTable(_fact_row(1, 9, "likes coffee"))
    service = _memory_service_over(monkeypatch, table, vector_hits=[UUID(int=1)])
    service.store.deactivate_embedding = AsyncMock(side_effect=ConnectionError("weaviate down"))
    user = str(UUID(int=9))

    report = asyncio.run(service.delete_memory_report(str(UUID(int=1)), user))
    recalled = asyncio.run(service.recall(user, "drinks"))

    assert report["weaviate"]["sync_pending"] is True
    assert recalled["memories"] == []
    # The guard is the real query's predicate, not the fake's filtering.
    assert table.recall_queries and all("is_active = true" in q for q in table.recall_queries)


@pytest.mark.asyncio
async def test_extraction_can_recreate_a_deleted_fact(monkeypatch):
    """Documented: extraction does not consult deleted facts, so the same
    conversation can yield the same content again as a NEW fact."""
    import memory_service

    connection = _RecordingConn()
    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=connection))
    monkeypatch.setattr(memory_service, "acquire_conn",
                        lambda *_a, **_k: _acquire_connection(lambda: connection))
    service = _extraction_service()
    service._litellm_complete = AsyncMock(return_value='[{"content":"likes coffee"}]')

    await service.extract_facts(str(UUID(int=9)), [{"role": "user", "content": "I like coffee"}])

    inserts = [params for query, params in connection.calls if "INSERT INTO public.memory_facts" in query]
    assert inserts and inserts[0][3] == "likes coffee"
    assert not any("is_active = false" in query for query, _ in connection.calls)


if __name__ == "__main__":
    unittest.main()
