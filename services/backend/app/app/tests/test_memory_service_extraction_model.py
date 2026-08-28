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


if __name__ == "__main__":
    unittest.main()
