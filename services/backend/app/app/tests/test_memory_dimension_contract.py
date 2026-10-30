from __future__ import annotations

import asyncio
import json
import math
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _memory_validation_app(monkeypatch):
    from tests.test_fal_media_provider import _fresh_main

    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    monkeypatch.setenv("BACKEND_STATE_STORE_MODE", "memory")
    monkeypatch.setenv("MEDIA_BUDGET_STORE", "memory")
    main = _fresh_main(monkeypatch, fal_source="disabled")
    monkeypatch.setattr(main.memory_service, "recall", AsyncMock(return_value={
        "memories": [], "context_summary": None,
    }))
    monkeypatch.setattr(main.memory_service, "update_memory", AsyncMock(return_value={
        "id": "00000000-0000-4000-8000-000000000003",
    }))
    return main


def _memory_confidence_request(client, kind, raw):
    user_id = "00000000-0000-4000-8000-000000000002"
    if kind == "recall":
        return client.post("/memory/recall", content=(
            '{"user_id":"' + user_id + '","query":"owned","min_confidence":' + raw + '}'
        ).encode(), headers={"Content-Type": "application/json"})
    return client.put("/memory/00000000-0000-4000-8000-000000000003",
        params={"user_id": user_id}, content=('{"confidence":' + raw + '}').encode(),
        headers={"Content-Type": "application/json"})


@pytest.mark.parametrize("kind", ["recall", "update"])
@pytest.mark.parametrize("raw, safe", [("1e999", "inf"), ("-1e999", "-inf"), ("NaN", "nan")])
def test_memory_rejects_nonfinite_confidence_before_service_calls(monkeypatch, kind, raw, safe):
    from fastapi.testclient import TestClient

    main = _memory_validation_app(monkeypatch)
    response = _memory_confidence_request(TestClient(main.app, raise_server_exceptions=False), kind, raw)

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "min_confidence" if kind == "recall" else "confidence"]
    assert error["type"] in {"less_than_equal", "greater_than_equal"}
    assert error["input"] == safe
    main.memory_service.recall.assert_not_awaited()
    main.memory_service.update_memory.assert_not_awaited()


@pytest.mark.parametrize("kind", ["recall", "update"])
def test_memory_accepts_finite_confidence(monkeypatch, kind):
    from fastapi.testclient import TestClient

    main = _memory_validation_app(monkeypatch)
    response = _memory_confidence_request(TestClient(main.app), kind, "0.7")

    assert response.status_code == 200
    method = main.memory_service.recall if kind == "recall" else main.memory_service.update_memory
    method.assert_awaited_once()
    value = (method.await_args.kwargs["min_confidence"] if kind == "recall"
             else method.await_args.args[2]["confidence"])
    assert value == 0.7


@pytest.mark.parametrize("body", [
    '{"query":"owned"}',
    '{"user_id":"bad","query":"owned"}',
    '{"user_id":"00000000-0000-4000-8000-000000000002","query":"","limit":0}',
    '{"user_id":"00000000-0000-4000-8000-000000000002","query":"owned","min_confidence":1.7}',
    '{"user_id":"00000000-0000-4000-8000-000000000002","query":"owned","min_confidence":"bad"}',
    '{"query":"owned",}',
])
def test_memory_ordinary_validation_matches_framework_response(monkeypatch, body):
    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.exceptions import RequestValidationError
    from fastapi.testclient import TestClient

    main = _memory_validation_app(monkeypatch)
    client = TestClient(main.app)
    args = {"content": body.encode(), "headers": {"Content-Type": "application/json"}}
    actual = client.post("/memory/recall", **args)
    main.app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    main.app.middleware_stack = None
    default = client.post("/memory/recall", **args)

    assert actual.status_code == default.status_code == 422
    assert actual.content == default.content
    main.memory_service.recall.assert_not_awaited()


def test_memory_invalid_nested_input_and_other_numeric_field_still_return_422(monkeypatch):
    from fastapi.testclient import TestClient

    main = _memory_validation_app(monkeypatch)
    body = ('{"user_id":"00000000-0000-4000-8000-000000000002",'
            '"query":{"nested":[1e999,-1e999,NaN,0.7,null,true]},"limit":1e999}')
    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/memory/recall", content=body.encode(), headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert errors[0]["input"]["nested"] == ["inf", "-inf", "nan", 0.7, None, True]
    assert errors[1]["loc"] == ["body", "limit"]
    assert errors[1]["input"] == "inf"
    main.memory_service.recall.assert_not_awaited()


def test_request_validation_normalizes_nested_context_without_mutating_errors(monkeypatch):
    from fastapi.exceptions import RequestValidationError

    main = _memory_validation_app(monkeypatch)
    errors = [{"type": "owned", "loc": ("body", "query"), "msg": "unchanged",
               "input": [float("inf"), float("-inf"), float("nan"), -0.0, 12, False, None],
               "ctx": {"maximum": float("inf"), "finite": 0.7}}]
    handler = main.app.exception_handlers[RequestValidationError]
    response = asyncio.run(handler(None, RequestValidationError(errors)))
    rendered = json.loads(response.body)["detail"][0]

    assert response.status_code == 422
    assert rendered["input"] == ["inf", "-inf", "nan", -0.0, 12, False, None]
    assert math.copysign(1, rendered["input"][3]) == -1
    assert rendered["ctx"] == {"maximum": "inf", "finite": 0.7}
    assert rendered["type"] == "owned" and rendered["msg"] == "unchanged"
    assert rendered["loc"] == ["body", "query"]
    assert math.isinf(errors[0]["input"][0]) and math.isnan(errors[0]["input"][2])
    assert math.isinf(errors[0]["ctx"]["maximum"])


@pytest.mark.parametrize("kind, expected", [
    ("http", 409), ("redis", 503), ("state", 503), ("pool", 503), ("runtime", 500),
])
def test_validation_handler_preserves_other_exception_boundaries(monkeypatch, kind, expected):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    main = _memory_validation_app(monkeypatch)
    errors = {
        "http": HTTPException(409, "conflict", headers={"Retry-After": "3"}),
        "redis": main.RedisError("owned fault"),
        "state": main.StateStoreUnavailable("owned fault"),
        "pool": main.PoolSaturatedError(deadline=1, size=2, free=0),
        "runtime": RuntimeError("owned fault"),
    }

    @main.app.get("/_owned_error_control")
    async def fail():
        raise errors[kind]

    response = TestClient(main.app, raise_server_exceptions=False).get("/_owned_error_control")
    assert response.status_code == expected
    _assert_error_control_response(main, kind, errors[kind], response)
    if kind == "runtime":
        assert response.text == "Internal Server Error"


def _assert_error_control_response(main, kind, error, response):
    if kind in {"redis", "state"}:
        assert response.json() == {"detail": main.state_store_detail()}
    if kind == "http":
        assert response.json() == {"detail": "conflict"}
        assert response.headers["Retry-After"] == "3"
    if kind == "pool":
        assert response.json() == {"detail": str(error)}
        assert response.headers["Retry-After"] == "1"


def test_request_validation_does_not_relax_response_serialization(monkeypatch):
    from fastapi.testclient import TestClient

    main = _memory_validation_app(monkeypatch)

    @main.app.get("/_owned_response_control", response_model=int)
    async def fail():
        return "invalid response"

    response = TestClient(main.app, raise_server_exceptions=False).get("/_owned_response_control")
    assert response.status_code == 500


def test_request_validation_preserves_memory_authentication(monkeypatch):
    from fastapi.testclient import TestClient

    main = _memory_validation_app(monkeypatch)
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    response = _memory_confidence_request(TestClient(main.app), "recall", "0.7")
    assert response.status_code == 401
    main.memory_service.recall.assert_not_awaited()


@pytest.mark.parametrize("dimension", [768, 1536, 3072])
def test_memory_store_accepts_supported_schema_dimensions(monkeypatch, dimension):
    import memory_store

    monkeypatch.setenv("LANGMEM_EMBEDDING_DIM", str(dimension))
    store = memory_store.MemoryStore("postgresql://atlas")

    assert store.embedding_dimension == dimension


@pytest.mark.parametrize("value", ["", "0", "-1", "not-an-int", "4001"])
def test_memory_store_rejects_invalid_schema_dimensions(monkeypatch, value):
    import memory_store

    monkeypatch.setenv("LANGMEM_EMBEDDING_DIM", value)
    with pytest.raises(ValueError, match="LANGMEM_EMBEDDING_DIM"):
        memory_store.MemoryStore("postgresql://atlas")


@pytest.mark.asyncio
async def test_embedding_response_dimension_mismatch_fails_before_database_write(
    monkeypatch,
):
    import memory_store

    writes = []

    @asynccontextmanager
    async def acquire(_url):
        writes.append("acquired")
        yield SimpleNamespace(execute=AsyncMock())

    store = memory_store.MemoryStore(
        "postgresql://atlas", embedding_dimension=768
    )
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1] * 1536)
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    with pytest.raises(ValueError, match="returned 1536.*configured schema dimension is 768"):
        await store._store_pgvector(
            "00000000-0000-4000-8000-000000000001", "hello"
        )

    assert writes == []


@pytest.mark.asyncio
async def test_pgvector_marker_failure_happens_before_vector_write(monkeypatch):
    import memory_store

    statements = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchval(self, query, *_args):
            statements.append(query)
            if "mark_memory_weaviate_dirty" in query:
                raise RuntimeError("marker unavailable")

        async def execute(self, query, *_args):
            statements.append(query)

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        embedding_dimension=3,
        manage_schema=True,
    )
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3])
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    with pytest.raises(RuntimeError, match="marker unavailable"):
        await store._store_pgvector("00000000-0000-4000-8000-000000000001", "x")

    assert len(statements) == 1
    assert "SET embedding" not in statements[0]


@pytest.mark.asyncio
async def test_pgvector_generation_and_vector_write_share_one_transaction(monkeypatch):
    import memory_store

    events = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            events.append("begin")
            try:
                yield
            finally:
                events.append("commit")

        async def fetchval(self, query, *_args):
            assert "mark_memory_weaviate_dirty" in query
            events.append("mark")
            return 12

        async def execute(self, query, *_args):
            assert "SET embedding" in query
            events.append("vector")

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas", embedding_dimension=3, manage_schema=True
    )
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3])
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    await store._store_pgvector("00000000-0000-4000-8000-000000000001", "x")

    assert events == ["begin", "mark", "vector", "commit"]


@pytest.mark.asyncio
async def test_healthy_shadow_write_does_not_dirty_generation(monkeypatch):
    import memory_store

    statements = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchval(self, query, *_args):
            statements.append(query)

        async def execute(self, query, *_args):
            statements.append(query)
            return "UPDATE 1"

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas", embedding_dimension=3, manage_schema=True
    )
    generate = AsyncMock(return_value=[0.1, 0.2, 0.3])
    monkeypatch.setattr(store, "_generate_embedding", generate)
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    await store._store_pgvector(
        "00000000-0000-4000-8000-000000000001", "hello", mark_dirty=False
    )

    generate.assert_awaited_once_with("hello")
    assert any("SET embedding" in query for query in statements)
    assert not any("mark_memory_weaviate_dirty" in query for query in statements)


@pytest.mark.asyncio
async def test_pgvector_noop_is_reported_as_superseded_write(monkeypatch):
    import memory_store

    statements = []

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def execute(self, query, *_args):
            statements.append(query)
            return "UPDATE 0"

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas", embedding_dimension=3, manage_schema=True
    )
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3])
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    with pytest.raises(memory_store.MemoryEmbeddingWriteSuperseded):
        await store._store_pgvector(
            "00000000-0000-4000-8000-000000000001",
            "stale content",
            mark_dirty=False,
        )
    assert "memory_embedding_schema_state" in statements[0]
    assert "pgvector_target_model = $4" in statements[0]
    assert "pgvector_target_generation = 1" in statements[0]


@pytest.mark.asyncio
async def test_healthy_weaviate_store_requires_durable_pgvector_shadow(monkeypatch):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(store, "_store_weaviate", AsyncMock(return_value="fact-1"))
    shadow = AsyncMock()
    monkeypatch.setattr(store, "_store_pgvector", shadow)

    result = await store.store_embedding(
        "fact-1", "hello", "user", "default", "observation", 1.0, {}
    )

    assert result == "fact-1"
    shadow.assert_awaited_once_with("fact-1", "hello", mark_dirty=False)


@pytest.mark.asyncio
async def test_shadow_failure_fails_closed_so_caller_keeps_pending(monkeypatch):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(store, "_store_weaviate", AsyncMock(return_value="fact-1"))
    monkeypatch.setattr(
        store,
        "_store_pgvector",
        AsyncMock(side_effect=RuntimeError("shadow unavailable")),
    )

    with pytest.raises(RuntimeError, match="shadow unavailable"):
        await store.store_embedding(
            "fact-1", "hello", "user", "default", "observation", 1.0, {}
        )

    assert store.backend == "weaviate"


@pytest.mark.parametrize("operation", ["store", "update"])
@pytest.mark.asyncio
async def test_healthy_weaviate_interval_is_immediately_recallable_after_outage(
    monkeypatch, operation
):
    import memory_store

    shadow_written = False
    fact_id = "00000000-0000-4000-8000-000000000001"
    user_id = "00000000-0000-4000-8000-000000000002"

    class Conn:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def execute(self, query, *_args):
            nonlocal shadow_written
            assert "SET embedding" in query
            assert "embedding_model = $4" in query
            assert "embedding_generation = 1" in query
            shadow_written = True
            return "UPDATE 1"

        async def fetch(self, query, *_args):
            assert shadow_written, "pgvector fallback must see the healthy shadow"
            assert "ORDER BY embedding::vector(3)" in query
            assert "embedding_model = $5" in query
            assert "embedding_generation = 1" in query
            return [
                {
                    "id": fact_id,
                    "content": "hello",
                    "fact_type": "observation",
                    "confidence": 1.0,
                    "distance": 0.0,
                }
            ]

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        weaviate_url="http://weaviate",
        embedding_dimension=3,
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    generate = AsyncMock(return_value=[0.1, 0.2, 0.3])
    monkeypatch.setattr(store, "_generate_embedding", generate)
    monkeypatch.setattr(store, "_store_weaviate", AsyncMock(return_value=fact_id))
    monkeypatch.setattr(store, "_update_weaviate", AsyncMock(return_value=fact_id))
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    if operation == "store":
        await store.store_embedding(
            fact_id, "hello", user_id, "default", "observation", 1.0, {}
        )
    else:
        await store.update_embedding(fact_id, "hello", user_id=user_id)

    store.backend = "pgvector"
    results = await store.search_similar("hello", user_id)

    assert results[0]["pg_fact_id"] == fact_id
    assert generate.await_count == 2  # one shadow write, one fallback query


@pytest.mark.asyncio
async def test_runtime_identity_and_completion_bind_full_model_and_dimension(
    monkeypatch,
):
    import memory_store

    calls = []

    class Conn:
        async def fetchrow(self, query, *args):
            calls.append((query, args))
            return {
                "weaviate_synced_model": "openai/text-embedding-3-small",
                "weaviate_synced_dimension": 1536,
            }

        async def fetchval(self, query, *args):
            calls.append((query, args))
            return True

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        embedding_model="openai/text-embedding-3-small",
        embedding_dimension=1536,
        manage_schema=True,
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    assert await store._ensure_weaviate_identity() is False
    assert await store._complete_weaviate_rebuild(27) is True

    assert "ensure_memory_weaviate_identity($1, $2)" in calls[0][0]
    assert calls[0][1] == ("openai/text-embedding-3-small", 1536)
    assert "complete_memory_weaviate_rebuild($1, $2, $3)" in calls[1][0]
    assert calls[1][1] == (27, "openai/text-embedding-3-small", 1536)


@pytest.mark.parametrize(
    ("dimension", "expected_expression"),
    [
        (768, "embedding::vector(768) <=> $1::vector(768)"),
        (1536, "embedding::vector(1536) <=> $1::vector(1536)"),
        (3072, "embedding::halfvec(3072) <=> $1::halfvec(3072)"),
    ],
)
@pytest.mark.asyncio
async def test_pgvector_query_uses_selected_dimension_and_index_expression(
    monkeypatch, dimension, expected_expression
):
    import memory_store

    queries = []

    class Conn:
        async def fetch(self, query, *_params):
            queries.append(query)
            return []

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas", embedding_dimension=dimension
    )
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1] * dimension)
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    await store._search_pgvector(
        "hello", "00000000-0000-4000-8000-000000000002", "default", 5
    )

    assert expected_expression in queries[0]
    assert f"vector_dims(embedding) = {dimension}" in queries[0]
    assert "embedding_model = $5" in queries[0]
    assert "embedding_generation = 1" in queries[0]
    assert "vector(768)" not in queries[0] if dimension != 768 else True


@pytest.mark.asyncio
async def test_selected_pgvector_success_is_authoritative_when_weaviate_is_down(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        weaviate_url="http://weaviate",
        embedding_dimension=768,
    )
    store.backend = "pgvector"
    store.weaviate_state = "unavailable"
    store.weaviate_state_reason = "initial_probe_failed"
    store._initialized = True
    monkeypatch.setattr(store, "_store_pgvector", AsyncMock())

    result = await store.update_embedding(
        "00000000-0000-4000-8000-000000000001", "hello"
    )

    assert result is None
    store._store_pgvector.assert_awaited_once()
    assert store.backend == "pgvector"
    assert store.weaviate_state_reason == "initial_probe_failed"


@pytest.mark.asyncio
async def test_weaviate_failback_requires_explicit_probe_and_is_concurrency_safe(
    monkeypatch,
):
    import memory_store

    probes = 0

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            nonlocal probes
            probes += 1
            await asyncio.sleep(0)
            return SimpleNamespace(status_code=200)

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        weaviate_url="http://weaviate",
        embedding_dimension=768,
    )
    store.backend = "pgvector"
    store.weaviate_state = "unavailable"
    store.weaviate_state_reason = "initial_probe_failed"
    store._initialized = True
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kw: Client())
    monkeypatch.setattr(store, "_ensure_weaviate_collection", AsyncMock())
    monkeypatch.setattr(store, "_sync_weaviate_from_postgres", AsyncMock())
    monkeypatch.setattr(store, "_store_pgvector", AsyncMock())

    await store.store_embedding(
        "00000000-0000-4000-8000-000000000001",
        "hello", "user", "default", "observation", 1.0, {},
    )
    assert probes == 0, "ordinary requests must not cause hidden failback probes"
    assert store.backend == "pgvector"

    results = await asyncio.gather(store.probe_weaviate(), store.probe_weaviate())

    assert results == [True, True]
    assert probes == 1
    assert store.backend == "weaviate"
    assert store.weaviate_state == "ready"
    assert store.weaviate_state_reason == "explicit_probe_succeeded"


@pytest.mark.asyncio
async def test_explicit_probe_rebuilds_when_local_weaviate_latch_is_stale(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(store, "_probe_weaviate_ready", AsyncMock())
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(True, 14), (True, 14), (False, 14)]),
    )
    rebuild = AsyncMock()
    monkeypatch.setattr(store, "_sync_weaviate_from_postgres", rebuild)
    complete = AsyncMock(return_value=True)
    monkeypatch.setattr(store, "_complete_weaviate_rebuild", complete)

    assert await store.probe_weaviate() is True

    rebuild.assert_awaited_once()
    complete.assert_awaited_once_with(14)
    assert store.backend == "weaviate"


@pytest.mark.asyncio
async def test_pgvector_write_during_failback_runs_concurrently_and_invalidates_cas(
    monkeypatch,
):
    import memory_store

    rebuild_started = asyncio.Event()
    finish_rebuild = asyncio.Event()

    rebuild_count = 0

    async def rebuild():
        nonlocal rebuild_count
        rebuild_count += 1
        if rebuild_count == 1:
            rebuild_started.set()
            await finish_rebuild.wait()

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store.weaviate_state = "unavailable"
    store._initialized = True
    monkeypatch.setattr(store, "_probe_weaviate_ready", AsyncMock(return_value=None))
    monkeypatch.setattr(store, "_sync_weaviate_from_postgres", rebuild)
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(True, 10), (True, 11), (False, 11)]),
    )
    monkeypatch.setattr(
        store, "_complete_weaviate_rebuild", AsyncMock(side_effect=[False, True])
    )
    monkeypatch.setattr(store, "_store_pgvector", AsyncMock())
    monkeypatch.setattr(store, "_store_weaviate", AsyncMock(return_value="fact-1"))

    probe = asyncio.create_task(store.probe_weaviate())
    await rebuild_started.wait()
    write = asyncio.create_task(
        store.update_embedding("fact-1", "new content")
    )
    await asyncio.wait_for(write, timeout=1)
    store._store_pgvector.assert_awaited_once_with("fact-1", "new content")
    store._store_weaviate.assert_not_awaited()

    finish_rebuild.set()
    assert await probe is True
    assert await write is None
    assert rebuild_count == 2
    store._store_weaviate.assert_not_awaited()


@pytest.mark.asyncio
async def test_deactivation_during_failback_cannot_resurrect_stale_weaviate_fact(
    monkeypatch,
):
    import memory_store

    rebuild_started = asyncio.Event()
    finish_rebuild = asyncio.Event()

    rebuild_count = 0

    async def rebuild():
        nonlocal rebuild_count
        rebuild_count += 1
        if rebuild_count == 1:
            rebuild_started.set()
            await finish_rebuild.wait()

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store.weaviate_state = "unavailable"
    store._initialized = True
    monkeypatch.setattr(store, "_probe_weaviate_ready", AsyncMock(return_value=None))
    monkeypatch.setattr(store, "_sync_weaviate_from_postgres", rebuild)
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(True, 20), (True, 21), (False, 21)]),
    )
    monkeypatch.setattr(
        store, "_complete_weaviate_rebuild", AsyncMock(side_effect=[False, True])
    )
    monkeypatch.setattr(store, "_deactivate_weaviate", AsyncMock())
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    probe = asyncio.create_task(store.probe_weaviate())
    await rebuild_started.wait()
    retirement = asyncio.create_task(store.deactivate_embedding("fact-1", "fact-1"))
    await asyncio.wait_for(retirement, timeout=1)
    mark.assert_awaited_once()
    store._deactivate_weaviate.assert_not_awaited()

    finish_rebuild.set()
    assert await probe is True
    await retirement
    assert rebuild_count == 2
    store._deactivate_weaviate.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_failure_cannot_select_pgvector_without_durable_dirty_marker(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        weaviate_url="http://weaviate",
        manage_schema=True,
    )
    monkeypatch.setattr(
        store, "_probe_weaviate_ready", AsyncMock(side_effect=ConnectionError("down"))
    )
    monkeypatch.setattr(
        store,
        "_mark_weaviate_dirty",
        AsyncMock(side_effect=RuntimeError("database down")),
    )

    with pytest.raises(RuntimeError, match="durably select pgvector"):
        await store._probe_weaviate(explicit=False)

    assert store.backend is None


@pytest.mark.asyncio
async def test_runtime_weaviate_outage_latches_pgvector_without_hidden_reprobes(
    monkeypatch,
):
    import httpx
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    weaviate = AsyncMock(side_effect=httpx.ConnectError("down"))
    pgvector = AsyncMock()
    monkeypatch.setattr(store, "_store_weaviate", weaviate)
    monkeypatch.setattr(store, "_store_pgvector", pgvector)
    monkeypatch.setattr(store, "_mark_weaviate_dirty", AsyncMock())

    for fact_id in ("fact-1", "fact-2"):
        assert await store.store_embedding(
            fact_id, "content", "user", "default", "observation", 1.0, {}
        ) is None

    assert weaviate.await_count == 1
    assert pgvector.await_count == 2
    assert store.backend == "pgvector"
    assert store.weaviate_state_reason == "runtime_operation_failed"


@pytest.mark.asyncio
async def test_runtime_fallback_marker_failure_does_not_write_pgvector(monkeypatch):
    import httpx
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(
        store, "_store_weaviate", AsyncMock(side_effect=httpx.ConnectError("down"))
    )
    pgvector = AsyncMock()
    monkeypatch.setattr(store, "_store_pgvector", pgvector)
    monkeypatch.setattr(
        store,
        "_mark_weaviate_dirty",
        AsyncMock(side_effect=RuntimeError("marker down")),
    )

    with pytest.raises(RuntimeError, match="marker down"):
        await store.store_embedding(
            "fact-1", "content", "user", "default", "observation", 1.0, {}
        )

    assert store.backend == "weaviate"
    pgvector.assert_not_awaited()


@pytest.mark.asyncio
async def test_healthy_weaviate_writes_are_not_serialized_by_probe_barrier(monkeypatch):
    import memory_store

    both_started = asyncio.Event()
    release = asyncio.Event()
    started = 0

    async def write(fact_id, *_args):
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        return fact_id

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(store, "_store_weaviate", write)
    shadow = AsyncMock()
    monkeypatch.setattr(store, "_store_pgvector", shadow)

    writes = [
        asyncio.create_task(
            store.store_embedding(
                fact_id, "x", "user", "default", "observation", 1.0, {}
            )
        )
        for fact_id in ("fact-1", "fact-2")
    ]
    await asyncio.wait_for(both_started.wait(), timeout=1)
    release.set()

    assert await asyncio.gather(*writes) == ["fact-1", "fact-2"]
    assert shadow.await_count == 2


@pytest.mark.parametrize("operation", ["store", "update", "search"])
@pytest.mark.asyncio
async def test_pgvector_authoritative_operations_run_in_parallel(
    monkeypatch, operation
):
    import memory_store

    both_started = asyncio.Event()
    release = asyncio.Event()
    started = 0

    async def pg_operation(*_args, **_kwargs):
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        return [] if operation == "search" else None

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    store.backend = "pgvector"
    store.weaviate_state = "unavailable"
    store._initialized = True
    monkeypatch.setattr(store, "_store_pgvector", pg_operation)
    monkeypatch.setattr(store, "_search_pgvector", pg_operation)

    if operation == "store":
        calls = [
            store.store_embedding(
                f"fact-{index}", "x", "user", "default", "observation", 1.0, {}
            )
            for index in range(2)
        ]
    elif operation == "update":
        calls = [store.update_embedding(f"fact-{index}", "x") for index in range(2)]
    else:
        calls = [store.search_similar("x", "user") for _index in range(2)]

    tasks = [asyncio.create_task(call) for call in calls]
    await asyncio.wait_for(both_started.wait(), timeout=1)
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_healthy_pending_weaviate_write_does_not_divert_recall_to_pgvector(
    monkeypatch,
):
    import memory_store

    calls = []

    async def sync_state(*, include_pending=True):
        calls.append(include_pending)
        # A healthy Weaviate write may briefly own a pending row while the
        # authoritative failback generation itself remains clean.
        return (include_pending, 5)

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(store, "_get_weaviate_sync_state", sync_state)
    weaviate = AsyncMock(return_value=[{"content": "current"}])
    pgvector = AsyncMock(return_value=[{"content": "stale"}])
    monkeypatch.setattr(store, "_search_weaviate", weaviate)
    monkeypatch.setattr(store, "_search_pgvector", pgvector)

    assert await store.search_similar("query", "user") == [
        {"content": "current"}
    ]

    assert calls == [False, False]
    weaviate.assert_awaited_once()
    pgvector.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_replica_store_fences_to_pgvector_before_weaviate(monkeypatch):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(
        store, "_get_weaviate_sync_state", AsyncMock(return_value=(True, 31))
    )
    weaviate = AsyncMock(return_value="fact-1")
    pgvector = AsyncMock()
    monkeypatch.setattr(store, "_store_weaviate", weaviate)
    monkeypatch.setattr(store, "_store_pgvector", pgvector)

    result = await store.store_embedding(
        "fact-1", "new", "user", "default", "observation", 1.0, {}
    )

    assert result is None
    weaviate.assert_not_awaited()
    pgvector.assert_awaited_once_with("fact-1", "new")
    assert store.backend == "pgvector"


@pytest.mark.asyncio
async def test_generation_change_during_store_writes_authoritative_pgvector(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(False, 40), (True, 41)]),
    )
    monkeypatch.setattr(store, "_store_weaviate", AsyncMock(return_value="fact-1"))
    pgvector = AsyncMock()
    monkeypatch.setattr(store, "_store_pgvector", pgvector)
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    result = await store.store_embedding(
        "fact-1", "new", "user", "default", "observation", 1.0, {}
    )

    assert result is None
    pgvector.assert_awaited_once_with("fact-1", "new", mark_dirty=False)
    mark.assert_awaited_once()
    assert store.backend == "pgvector"


@pytest.mark.asyncio
async def test_generation_change_during_update_writes_authoritative_pgvector(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(False, 50), (True, 51)]),
    )
    monkeypatch.setattr(store, "_update_weaviate", AsyncMock(return_value="fact-1"))
    pgvector = AsyncMock()
    monkeypatch.setattr(store, "_store_pgvector", pgvector)
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    result = await store.update_embedding("fact-1", "updated")

    assert result is None
    pgvector.assert_awaited_once_with("fact-1", "updated", mark_dirty=False)
    mark.assert_awaited_once()
    assert store.backend == "pgvector"


@pytest.mark.parametrize("operation", ["store", "update"])
@pytest.mark.asyncio
async def test_waiting_pgvector_mutation_reenters_generation_fence(
    monkeypatch, operation
):
    """A local failback while waiting must not bypass the shared fence."""
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store.weaviate_state = "unavailable"
    store._initialized = True

    class FailbackBarrier:
        async def __aenter__(self):
            store.backend = "weaviate"
            store.weaviate_state = "ready"

        async def __aexit__(self, *_args):
            return None

    store._transition_lock = FailbackBarrier()
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(False, 55), (True, 56)]),
    )
    monkeypatch.setattr(store, "_store_weaviate", AsyncMock(return_value="fact-1"))
    monkeypatch.setattr(store, "_update_weaviate", AsyncMock(return_value="fact-1"))
    pgvector = AsyncMock()
    monkeypatch.setattr(store, "_store_pgvector", pgvector)
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    if operation == "store":
        result = await store.store_embedding(
            "fact-1", "new", "user", "default", "observation", 1.0, {}
        )
    else:
        result = await store.update_embedding("fact-1", "new")

    assert result is None
    pgvector.assert_awaited_once_with("fact-1", "new", mark_dirty=False)
    mark.assert_awaited_once()
    assert store.backend == "pgvector"


@pytest.mark.asyncio
async def test_generation_change_during_deactivation_preserves_dirty_retirement(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(False, 60), (True, 61)]),
    )
    monkeypatch.setattr(store, "_deactivate_weaviate", AsyncMock())
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    await store.deactivate_embedding("fact-1", "fact-1")

    mark.assert_awaited_once()
    assert store.backend == "pgvector"


@pytest.mark.asyncio
async def test_waiting_pgvector_deactivation_reenters_generation_fence(monkeypatch):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store.weaviate_state = "unavailable"
    store._initialized = True

    class FailbackBarrier:
        async def __aenter__(self):
            store.backend = "weaviate"
            store.weaviate_state = "ready"

        async def __aexit__(self, *_args):
            return None

    store._transition_lock = FailbackBarrier()
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(False, 80), (True, 81)]),
    )
    deactivate = AsyncMock()
    mark = AsyncMock()
    monkeypatch.setattr(store, "_deactivate_weaviate", deactivate)
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    await store.deactivate_embedding("fact-1", "fact-1")

    deactivate.assert_awaited_once_with("fact-1", "fact-1")
    mark.assert_awaited_once()
    assert store.backend == "pgvector"


@pytest.mark.asyncio
async def test_generation_change_during_delete_preserves_dirty_retirement(monkeypatch):
    import memory_store

    class Response:
        status_code = 204

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def delete(self, _url):
            return Response()

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kw: Client())
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(False, 70), (True, 71)]),
    )
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    await store.delete_embedding("fact-1", "fact-1")

    mark.assert_awaited_once()
    assert store.backend == "pgvector"


@pytest.mark.asyncio
async def test_waiting_pgvector_delete_reenters_generation_fence(monkeypatch):
    import memory_store

    deleted = []

    class Response:
        status_code = 204

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def delete(self, url):
            deleted.append(url)
            return Response()

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store.weaviate_state = "unavailable"
    store._initialized = True

    class FailbackBarrier:
        async def __aenter__(self):
            store.backend = "weaviate"
            store.weaviate_state = "ready"

        async def __aexit__(self, *_args):
            return None

    store._transition_lock = FailbackBarrier()
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kw: Client())
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(False, 90), (True, 91)]),
    )
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    await store.delete_embedding("fact-1", "fact-1")

    assert len(deleted) == 1
    mark.assert_awaited_once()
    assert store.backend == "pgvector"


@pytest.mark.asyncio
async def test_clean_restart_probe_skips_full_weaviate_rebuild(monkeypatch):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store._initialized = True
    monkeypatch.setattr(store, "_probe_weaviate_ready", AsyncMock())
    monkeypatch.setattr(
        store, "_get_weaviate_sync_state", AsyncMock(return_value=(False, 7))
    )
    rebuild = AsyncMock()
    monkeypatch.setattr(store, "_sync_weaviate_from_postgres", rebuild)

    assert await store.probe_weaviate() is True
    rebuild.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_collection_marks_dirty_before_creation(monkeypatch):
    import memory_store

    events = []

    class Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return Response(404)

        async def post(self, _url, json):
            events.append(("create", json["moduleConfig"]["text2vec-openai"]))
            return Response(201)

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kw: Client())

    async def mark():
        events.append(("dirty", None))

    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    await store._ensure_weaviate_collection(force_recreate=False)

    assert events[0][0] == "dirty"
    assert events[1][0] == "create"


@pytest.mark.parametrize(
    "existing_module",
    [
        {"model": "old-model", "baseURL": "http://litellm:4000"},
        {"model": "nomic-embed-text", "baseURL": "http://other:4000"},
    ],
)
@pytest.mark.asyncio
async def test_collection_model_or_base_url_mismatch_forces_recreation(
    monkeypatch, existing_module
):
    import memory_store

    calls = []

    class Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return Response(
                200,
                {"moduleConfig": {"text2vec-openai": existing_module}},
            )

        async def delete(self, _url):
            calls.append("delete")
            return Response(204)

        async def post(self, _url, json):
            calls.append(("create", json["moduleConfig"]["text2vec-openai"]))
            return Response(201)

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        weaviate_url="http://weaviate",
        litellm_url="http://litellm:4000/v1",
        embedding_model="ollama/nomic-embed-text",
        manage_schema=True,
    )
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kw: Client())
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    await store._ensure_weaviate_collection(force_recreate=False)

    mark.assert_awaited_once()
    assert calls[0] == "delete"
    assert calls[1][0] == "create"
    assert calls[1][1]["model"] == "nomic-embed-text"
    assert calls[1][1]["baseURL"] == "http://litellm:4000"


@pytest.mark.asyncio
async def test_synced_dimension_mismatch_forces_collection_recreation(monkeypatch):
    import memory_store

    calls = []

    class Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return Response(
                200,
                {
                    "moduleConfig": {
                        "text2vec-openai": {
                            "model": "nomic-embed-text",
                            "baseURL": "http://litellm:4000",
                        }
                    }
                },
            )

        async def delete(self, _url):
            calls.append("delete")
            return Response(204)

        async def post(self, _url, json):
            calls.append("create")
            return Response(201)

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        weaviate_url="http://weaviate",
        embedding_model="ollama/nomic-embed-text",
        manage_schema=True,
    )
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kw: Client())
    monkeypatch.setattr(store, "_mark_weaviate_dirty", AsyncMock())

    await store._ensure_weaviate_collection(force_recreate=True)

    assert calls == ["delete", "create"]


@pytest.mark.asyncio
async def test_exact_collection_contract_is_left_intact(monkeypatch):
    import memory_store

    class Response:
        status_code = 200

        def json(self):
            return {
                "vectorizer": "text2vec-openai",
                "moduleConfig": {
                    "text2vec-openai": {
                        "model": "nomic-embed-text",
                        "baseURL": "http://litellm:4000/",
                    }
                },
                "properties": [
                    {"name": name, "tokenization": "field"}
                    for name in memory_store._EXACT_MATCH_PROPERTIES
                ],
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return Response()

        async def delete(self, _url):
            raise AssertionError("exact collection must not be deleted")

        async def post(self, _url, json):
            raise AssertionError("exact collection must not be recreated")

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        weaviate_url="http://weaviate",
        litellm_url="http://litellm:4000/v1/",
        embedding_model="ollama/nomic-embed-text",
        manage_schema=True,
    )
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kw: Client())
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    await store._ensure_weaviate_collection()

    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_failback_retries_when_already_scanned_row_advances_generation(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store._initialized = True
    monkeypatch.setattr(store, "_probe_weaviate_ready", AsyncMock())
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(True, 10), (True, 11), (False, 11)]),
    )
    rebuild = AsyncMock()
    monkeypatch.setattr(store, "_sync_weaviate_from_postgres", rebuild)
    complete = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(store, "_complete_weaviate_rebuild", complete)

    assert await store.probe_weaviate() is True

    assert rebuild.await_count == 2
    assert [call.args for call in complete.await_args_list] == [(10,), (11,)]
    assert store.backend == "weaviate"


@pytest.mark.asyncio
async def test_failback_final_shared_precheck_rejects_dirty_state_after_cas(
    monkeypatch,
):
    """CAS success alone cannot expose W after a later authoritative write."""
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store._initialized = True
    monkeypatch.setattr(store, "_probe_weaviate_ready", AsyncMock())
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(True, 30), (True, 31)]),
    )
    monkeypatch.setattr(store, "_sync_weaviate_from_postgres", AsyncMock())
    monkeypatch.setattr(
        store, "_complete_weaviate_rebuild", AsyncMock(return_value=True)
    )

    assert await store.probe_weaviate() is False

    assert store.backend == "pgvector"
    assert store.weaviate_state_reason == "secondary_dirty_detected"


@pytest.mark.asyncio
async def test_failback_generation_churn_is_bounded_and_stays_pgvector(monkeypatch):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store._initialized = True
    monkeypatch.setattr(store, "_probe_weaviate_ready", AsyncMock())
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(True, value) for value in (10, 11, 12, 13)]),
    )
    rebuild = AsyncMock()
    monkeypatch.setattr(store, "_sync_weaviate_from_postgres", rebuild)
    monkeypatch.setattr(
        store, "_complete_weaviate_rebuild", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(store, "_mark_weaviate_dirty", AsyncMock())

    assert await store.probe_weaviate() is False

    assert rebuild.await_count == memory_store.MAX_FAILBACK_REBUILD_ATTEMPTS
    assert store.backend == "pgvector"
    assert store.weaviate_state_reason == "failback_generation_churn"


@pytest.mark.asyncio
async def test_durable_dirty_marker_moves_stale_process_search_to_pgvector(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(
        store, "_get_weaviate_sync_state", AsyncMock(return_value=(True, 8))
    )
    weaviate = AsyncMock(return_value=[{"content": "stale"}])
    pgvector = AsyncMock(return_value=[{"content": "authoritative"}])
    monkeypatch.setattr(store, "_search_weaviate", weaviate)
    monkeypatch.setattr(store, "_search_pgvector", pgvector)

    results = await store.search_similar("query", "user")

    assert results == [{"content": "authoritative"}]
    assert store.backend == "pgvector"
    assert store.weaviate_state == "unavailable"
    assert store.weaviate_state_reason == "secondary_dirty_detected"
    weaviate.assert_not_awaited()
    pgvector.assert_awaited_once()


@pytest.mark.asyncio
async def test_generation_change_during_weaviate_search_discards_stale_result(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(False, 20), (True, 21)]),
    )
    weaviate = AsyncMock(return_value=[{"content": "stale"}])
    pgvector = AsyncMock(return_value=[{"content": "authoritative"}])
    monkeypatch.setattr(store, "_search_weaviate", weaviate)
    monkeypatch.setattr(store, "_search_pgvector", pgvector)

    results = await store.search_similar("query", "user")

    assert results == [{"content": "authoritative"}]
    weaviate.assert_awaited_once()
    pgvector.assert_awaited_once()
    assert store.backend == "pgvector"
    assert store.weaviate_state_reason == "secondary_dirty_detected"


@pytest.mark.asyncio
async def test_pgvector_retry_error_is_not_reclassified_as_weaviate_failure(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "weaviate"
    store.weaviate_state = "ready"
    store._initialized = True
    monkeypatch.setattr(
        store,
        "_get_weaviate_sync_state",
        AsyncMock(side_effect=[(False, 20), (True, 21)]),
    )
    monkeypatch.setattr(
        store, "_search_weaviate", AsyncMock(return_value=[{"content": "stale"}])
    )
    pgvector = AsyncMock(side_effect=ConnectionError("postgres unavailable"))
    monkeypatch.setattr(store, "_search_pgvector", pgvector)
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    with pytest.raises(ConnectionError, match="postgres unavailable"):
        await store.search_similar("query", "user")

    pgvector.assert_awaited_once()
    mark.assert_not_awaited()


@pytest.mark.asyncio
async def test_failback_rebuild_syncs_active_and_retired_without_holding_db_io(
    monkeypatch,
):
    import memory_store

    connection_held = False
    fetches = 0
    stale_cleanups = []
    rows = [
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "user_id": "00000000-0000-4000-8000-000000000010",
            "namespace": "default",
            "content": "active",
            "fact_type": "observation",
            "confidence": 1.0,
            "is_active": True,
            "weaviate_id": None,
            "updated_at": "v1",
        },
        {
            "id": "00000000-0000-4000-8000-000000000002",
            "user_id": "00000000-0000-4000-8000-000000000010",
            "namespace": "default",
            "content": "retired",
            "fact_type": "observation",
            "confidence": 1.0,
            "is_active": False,
            "weaviate_id": "legacy-2",
            "updated_at": "v2",
        },
    ]

    class Conn:
        async def fetch(self, *_args):
            nonlocal fetches
            fetches += 1
            return rows if fetches == 1 else []

        async def execute(self, *_args):
            return "UPDATE 1"

    @asynccontextmanager
    async def acquire(_url):
        nonlocal connection_held
        assert not connection_held
        connection_held = True
        try:
            yield Conn()
        finally:
            connection_held = False

    async def write_active(*_args):
        assert not connection_held
        return "00000000-0000-4000-8000-000000000001"

    async def retire(*_args):
        assert not connection_held

    async def cleanup(fact_id, *, keep_id):
        assert not connection_held
        stale_cleanups.append((fact_id, keep_id))

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)
    monkeypatch.setattr(store, "_store_weaviate", write_active)
    monkeypatch.setattr(store, "_deactivate_weaviate", retire)
    monkeypatch.setattr(store, "_delete_stale_weaviate_objects", cleanup)

    await store._sync_weaviate_from_postgres()

    assert stale_cleanups == [
        (
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000001",
        )
    ]


@pytest.mark.asyncio
async def test_failback_removes_legacy_duplicates_but_keeps_rebuilt_object(
    monkeypatch,
):
    import memory_store

    deleted = []

    class Response:
        status_code = 204

        def raise_for_status(self):
            raise AssertionError("successful deletes must not raise")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def delete(self, url):
            deleted.append(url.rsplit("/", 1)[-1])
            return Response()

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate"
    )
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kw: Client())
    monkeypatch.setattr(
        store,
        "_weaviate_ids_for_fact",
        AsyncMock(return_value=["fact-1", "legacy-1", "legacy-1", "legacy-2"]),
    )

    await store._delete_stale_weaviate_objects("fact-1", keep_id="fact-1")

    assert deleted == ["legacy-1", "legacy-2"]


@pytest.mark.asyncio
async def test_failed_pgvector_write_remains_pending_and_retryable(monkeypatch):
    import memory_service

    row = {
        "id": "00000000-0000-4000-8000-000000000001",
        "user_id": "00000000-0000-4000-8000-000000000002",
        "namespace": "default",
        "content": "hello",
        "fact_type": "observation",
        "confidence": 1.0,
        "is_active": True,
        "weaviate_id": None,
        "updated_at": "2026-01-01",
    }
    clears = []

    class Conn:
        async def fetch(self, *_args):
            return [row]

        async def execute(self, query, *_args):
            if "vector_sync_pending = false" in query:
                clears.append(query)

        async def close(self):
            return None

    service = memory_service.MemoryService.__new__(memory_service.MemoryService)
    service.database_url = "postgresql://atlas"
    service.store = SimpleNamespace(
        update_embedding=AsyncMock(side_effect=ConnectionError("pgvector down")),
        deactivate_embedding=AsyncMock(),
    )
    monkeypatch.setattr(
        memory_service, "connect_postgres", AsyncMock(return_value=Conn())
    )

    assert await service._reconcile_pending_vectors() == 0
    assert clears == []


@pytest.mark.asyncio
async def test_schema_backfill_contracts_only_after_every_existing_row(monkeypatch):
    import memory_store

    first = {
        "id": "00000000-0000-4000-8000-000000000001",
        "content": "first",
    }
    second = {
        "id": "00000000-0000-4000-8000-000000000002",
        "content": "second",
    }
    remaining = [[first, second], []]
    calls = []
    connection_held = False

    class Conn:
        async def fetchrow(self, query, *_args):
            if "memory_embedding_schema_state" in query:
                return {
                    "active_dimension": 768,
                    "target_dimension": 1536,
                    "phase": "backfill",
                    "pgvector_active_model": "ollama/nomic-embed-text",
                    "pgvector_target_model": "ollama/nomic-embed-text",
                    "pgvector_active_generation": 1,
                    "pgvector_target_generation": 2,
                }
            return None

        async def fetch(self, query, *_args):
            assert "vector_dims(embedding)" in query
            return remaining.pop(0)

        async def execute(self, query, *args):
            calls.append((query, args))
            return "UPDATE 1"

    @asynccontextmanager
    async def acquire(_url):
        nonlocal connection_held
        assert not connection_held
        connection_held = True
        try:
            yield Conn()
        finally:
            connection_held = False

    async def embed(_content):
        assert not connection_held, "provider I/O must not hold a DB connection"
        return [0.1] * 1536

    store = memory_store.MemoryStore(
        "postgresql://atlas", embedding_dimension=1536, manage_schema=True
    )
    monkeypatch.setattr(
        store,
        "_generate_embedding", AsyncMock(side_effect=embed),
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    await store._ensure_pgvector_schema_contract()

    updates = [query for query, _args in calls if "SET embedding" in query]
    contracts = [query for query, _args in calls if "contract_memory" in query]
    assert len(updates) == 2
    assert len(contracts) == 1
    assert calls.index(next(item for item in calls if "contract_memory" in item[0])) > 1


@pytest.mark.asyncio
async def test_schema_backfill_failure_preserves_old_vector_and_does_not_contract(
    monkeypatch,
):
    import memory_store

    calls = []

    class Conn:
        async def fetchrow(self, query, *_args):
            return {
                "active_dimension": 768,
                "target_dimension": 1536,
                "phase": "backfill",
                "pgvector_active_model": "ollama/nomic-embed-text",
                "pgvector_target_model": "ollama/nomic-embed-text",
                "pgvector_active_generation": 1,
                "pgvector_target_generation": 2,
            }

        async def fetch(self, *_args):
            return [{"id": "fact-1", "content": "first"}]

        async def execute(self, query, *args):
            calls.append((query, args))
            return "SELECT 1"

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas", embedding_dimension=1536, manage_schema=True
    )
    monkeypatch.setattr(
        store,
        "_generate_embedding",
        AsyncMock(side_effect=RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await store._ensure_pgvector_schema_contract()

    assert not any("SET embedding" in query for query, _args in calls)
    assert not any("contract_memory" in query for query, _args in calls)


@pytest.mark.asyncio
async def test_schema_backfill_tolerates_replica_winning_optimistic_update(
    monkeypatch,
):
    import memory_store

    row = {"id": "fact-1", "content": "first"}
    batches = [[row], []]
    calls = []

    class Conn:
        async def fetchrow(self, *_args):
            return {
                "active_dimension": 768,
                "target_dimension": 1536,
                "phase": "backfill",
                "pgvector_active_model": "ollama/nomic-embed-text",
                "pgvector_target_model": "ollama/nomic-embed-text",
                "pgvector_active_generation": 1,
                "pgvector_target_generation": 2,
            }

        async def fetch(self, *_args):
            return batches.pop(0)

        async def execute(self, query, *_args):
            calls.append(query)
            if "SET embedding" in query:
                return "UPDATE 0"
            return "SELECT 1"

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas", embedding_dimension=1536, manage_schema=True
    )
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1] * 1536)
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    await store._ensure_pgvector_schema_contract()

    assert sum("SET embedding" in query for query in calls) == 1
    assert sum("contract_memory" in query for query in calls) == 1


@pytest.mark.asyncio
async def test_schema_backfill_stops_when_target_generation_is_superseded(
    monkeypatch,
):
    import memory_store

    row = {"id": "fact-1", "content": "first"}
    target_reads = 0
    fetches = 0
    calls = []

    class Conn:
        async def fetchrow(self, *_args):
            nonlocal target_reads
            target_reads += 1
            generation = 2 if target_reads <= 2 else 3
            return {
                "active_dimension": 768,
                "target_dimension": 1536,
                "phase": "backfill",
                "pgvector_active_model": "provider-a/embed",
                "pgvector_target_model": (
                    "provider-b/embed" if generation == 2 else "provider-c/embed"
                ),
                "pgvector_active_generation": 1,
                "pgvector_target_generation": generation,
            }

        async def fetch(self, *_args):
            nonlocal fetches
            fetches += 1
            return [row]

        async def execute(self, query, *_args):
            calls.append(query)
            return "UPDATE 0"

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        embedding_model="provider-b/embed",
        embedding_dimension=1536,
        manage_schema=True,
    )
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1] * 1536)
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    with pytest.raises(RuntimeError, match="superseded"):
        await asyncio.wait_for(
            store._ensure_pgvector_schema_contract(), timeout=0.1
        )

    assert fetches == 1
    assert store._generate_embedding.await_count == 1
    assert sum("SET embedding" in query for query in calls) == 1
    assert not any("contract_memory" in query for query in calls)


@pytest.mark.asyncio
async def test_same_dimension_model_change_backfills_identity_before_ready(
    monkeypatch,
):
    import memory_store

    row = {
        "id": "00000000-0000-4000-8000-000000000001",
        "content": "semantic space changed",
    }
    batches = [[row], []]
    calls = []

    class Conn:
        async def fetchrow(self, *_args):
            return {
                "active_dimension": 768,
                "target_dimension": 768,
                "phase": "backfill",
                "pgvector_active_model": "provider-a/embed",
                "pgvector_target_model": "provider-b/embed",
                "pgvector_active_generation": 1,
                "pgvector_target_generation": 2,
            }

        async def fetch(self, query, *_args):
            assert "embedding_model" in query
            assert "embedding_generation" in query
            return batches.pop(0)

        async def execute(self, query, *args):
            calls.append((query, args))
            return "UPDATE 1"

    @asynccontextmanager
    async def acquire(_url):
        yield Conn()

    store = memory_store.MemoryStore(
        "postgresql://atlas",
        embedding_model="provider-b/embed",
        embedding_dimension=768,
        manage_schema=True,
    )
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.2] * 768)
    )
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)

    await store._ensure_pgvector_schema_contract()

    update_query, update_args = next(
        item for item in calls if "SET embedding" in item[0]
    )
    assert "embedding_model = $4" in update_query
    assert "embedding_generation = 2" in update_query
    assert "memory_embedding_schema_state" in update_query
    assert "pgvector_target_model = $4" in update_query
    assert "pgvector_target_generation = 2" in update_query
    assert update_args[3] == "provider-b/embed"
    contract_query, contract_args = next(
        item for item in calls if "contract_memory_embedding_contract" in item[0]
    )
    assert contract_args == ("provider-b/embed", 768, 2)
    assert store._pgvector_generation == 2


@pytest.mark.asyncio
async def test_startup_probes_effective_model_dimension_before_ready_schema(
    monkeypatch,
):
    import memory_store

    store = memory_store.MemoryStore(
        "postgresql://atlas", embedding_dimension=768, manage_schema=True
    )
    monkeypatch.setattr(
        store, "_generate_embedding", AsyncMock(return_value=[0.1] * 1536)
    )
    connect = AsyncMock()
    monkeypatch.setattr(memory_store, "acquire_conn", connect)

    with pytest.raises(ValueError, match="returned 1536.*schema dimension is 768"):
        await store.initialize()

    connect.assert_not_awaited()


# Weaviate vectorizer-failure contracts (a LiteLLM embedding error inside a
# healthy Weaviate). Helpers are shared with test_memory_failure_contracts.
from tests.test_memory_failure_contracts import _also_route_acquire, _pending_rows  # noqa: E402


@pytest.mark.parametrize("body,unavailable", [
    ('{"error":[{"message":"vectorize target vector default: update vector: '
     'connection to: OpenAI API failed with status: 500"}]}', False),
    ("internal server error", True),
])
def test_weaviate_vectorizer_failure_does_not_latch_pgvector(body, unavailable):
    """A LiteLLM embedding error surfaces as a Weaviate 5xx; Weaviate itself is up."""
    import httpx

    from memory_store import _weaviate_target_unavailable

    request = httpx.Request("POST", "http://weaviate/v1/objects")
    exc = httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(500, text=body, request=request)
    )
    assert _weaviate_target_unavailable(exc) is unavailable


@pytest.mark.asyncio
async def test_recall_graphql_vectorizer_error_serves_pgvector_without_latching(monkeypatch):
    import memory_store

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json=None, **_kwargs):
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": {"Get": {"Memory": None}},
                    "errors": [{"message": "vectorize params: remote client failed"}],
                },
            )

    store = memory_store.MemoryStore("postgresql://atlas", weaviate_url="http://weaviate")
    store.backend = "weaviate"
    store._initialized = True
    store._clean_weaviate_generation = AsyncMock(return_value=1)
    store._search_pgvector = AsyncMock(return_value=[{"pg_fact_id": "f"}])
    store._latch_pgvector_after_runtime_failure = AsyncMock()
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kwargs: Client())

    assert await store.search_similar("q", "u") == [{"pg_fact_id": "f"}]
    store._latch_pgvector_after_runtime_failure.assert_not_awaited()
    assert store.backend == "weaviate"


def test_vectorizer_failure_is_a_per_row_deferral_in_reconcile():
    import httpx

    from memory_service import _is_target_health_signal

    request = httpx.Request("POST", "http://weaviate/v1/objects")
    exc = httpx.HTTPStatusError(
        "x", request=request,
        response=httpx.Response(500, text="vectorize target vector: update vector failed", request=request),
    )
    assert _is_target_health_signal(exc) is False


@pytest.mark.asyncio
async def test_vectorizer_write_failure_keeps_a_pgvector_shadow_then_raises():
    import httpx
    import memory_store

    request = httpx.Request("POST", "http://weaviate/v1/objects")
    failure = httpx.HTTPStatusError(
        "x", request=request,
        response=httpx.Response(500, text="update vector: remote client failed", request=request),
    )
    store = memory_store.MemoryStore("postgresql://atlas", weaviate_url="http://weaviate")
    store._store_pgvector = AsyncMock()
    store._latch_pgvector_after_runtime_failure = AsyncMock()

    with pytest.raises(httpx.HTTPStatusError):
        await store._after_weaviate_write_failure(failure, "f", "content")
    store._store_pgvector.assert_awaited_once_with("f", "content", mark_dirty=False)
    store._latch_pgvector_after_runtime_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_systematic_vectorizer_failures_halt_reconcile_without_raising(monkeypatch):
    """Recall runs reconcile: a vectorizer failing every row must not retry 100 rows."""
    import httpx
    import memory_service as mod

    calls = {"n": 0}
    request = httpx.Request("POST", "http://weaviate/v1/objects")

    class VectorizerFailingStore:
        async def update_embedding(self, **_kwargs):
            calls["n"] += 1
            raise httpx.HTTPStatusError(
                "x", request=request,
                response=httpx.Response(500, text="update vector: remote client failed", request=request),
            )

    class FakeConn:
        async def fetch(self, *_a, **_k):
            return _pending_rows(100)

        async def execute(self, *_a, **_k):
            return "UPDATE 1"

        async def close(self):
            return None

    svc = mod.MemoryService.__new__(mod.MemoryService)
    svc.store = VectorizerFailingStore()
    svc.database_url = "postgresql://x"
    monkeypatch.setattr(mod, "connect_postgres", AsyncMock(return_value=FakeConn()))
    _also_route_acquire(monkeypatch, mod, FakeConn)

    assert await svc._reconcile_pending_vectors(retry_transient=True) == 0
    assert calls["n"] == mod._RECONCILE_TRANSIENT_STREAK


@pytest.mark.asyncio
async def test_failed_shadow_write_still_raises_the_weaviate_error():
    import httpx
    import memory_store

    request = httpx.Request("POST", "http://weaviate/v1/objects")
    failure = httpx.HTTPStatusError(
        "x", request=request,
        response=httpx.Response(500, text="vectorize failed", request=request),
    )
    store = memory_store.MemoryStore("postgresql://atlas", weaviate_url="http://weaviate")
    # A per-row shadow failure (LiteLLM rejects the content) is not a target
    # outage, so the Weaviate vectorizer error is the one surfaced.
    store._store_pgvector = AsyncMock(side_effect=httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(400, request=request),
    ))

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await store._after_weaviate_write_failure(failure, "f", "content")
    assert raised.value is failure


@pytest.mark.asyncio
async def test_per_row_vectorizer_rejections_do_not_stall_reconcile(monkeypatch):
    """A LiteLLM 400 for one oversize fact, wrapped in Weaviate's 500, is per
    row: three of them at the head of the queue must not block later rows."""
    import httpx
    import memory_service as mod

    request = httpx.Request("POST", "http://weaviate/v1/objects")
    seen = []

    class Store:
        async def update_embedding(self, **kwargs):
            seen.append(kwargs["fact_id"])
            if len(seen) <= 3:
                raise httpx.HTTPStatusError(
                    "x", request=request,
                    response=httpx.Response(
                        500, request=request,
                        text="vectorize: connection to: OpenAI API failed with status: 400 error: context length",
                    ),
                )
            return None

    class FakeConn:
        async def fetch(self, *_a, **_k):
            return _pending_rows(6)

        async def execute(self, *_a, **_k):
            return "UPDATE 1"

        async def close(self):
            return None

    svc = mod.MemoryService.__new__(mod.MemoryService)
    svc.store = Store()
    svc.database_url = "postgresql://x"
    monkeypatch.setattr(mod, "connect_postgres", AsyncMock(return_value=FakeConn()))
    _also_route_acquire(monkeypatch, mod, FakeConn)

    assert await svc._reconcile_pending_vectors() == 3
    assert len(seen) == 6


@pytest.mark.asyncio
async def test_unreachable_provider_during_shadow_write_is_raised_as_target_failure():
    import httpx
    import memory_store

    request = httpx.Request("POST", "http://weaviate/v1/objects")
    failure = httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(500, text="update vector failed", request=request),
    )
    store = memory_store.MemoryStore("postgresql://atlas", weaviate_url="http://weaviate")
    store._store_pgvector = AsyncMock(side_effect=httpx.ConnectError("litellm down"))

    with pytest.raises(httpx.ConnectError):
        await store._after_weaviate_write_failure(failure, "f", "content")


@pytest.mark.parametrize("body,per_row", [
    ("connection to: OpenAI API failed with status: 400 error: context length exceeded", True),
    ("connection to: OpenAI API failed with status: 401 error: invalid api key", False),
    ("connection to: OpenAI API failed with status: 400 error: Invalid model name passed", False),
    ("connection to: OpenAI API failed with status: 404 error: model not found", False),
    ("connection to: OpenAI API failed with status: 4000 error", False),
    ("connection to: OpenAI API failed with status: 400 error: Budget has been exceeded", False),
])
def test_vectorizer_row_rejection_excludes_systematic_4xx(body, per_row):
    import httpx

    from memory_store import _weaviate_vectorizer_row_rejection

    request = httpx.Request("POST", "http://weaviate/v1/objects")
    exc = httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(500, text="vectorize: " + body, request=request),
    )
    assert _weaviate_vectorizer_row_rejection(exc) is per_row



def test_word_tokenized_filter_properties_force_a_rebuild():
    # Word tokenization made namespace == "default" match "default-archive".
    import memory_store

    field = [{"name": n, "tokenization": "field"} for n in memory_store._EXACT_MATCH_PROPERTIES]
    assert memory_store._needs_field_tokenization({"properties": field}) is False
    field[1] = {"name": "namespace", "tokenization": "word"}
    assert memory_store._needs_field_tokenization({"properties": field}) is True


def test_research_heartbeat_stops_a_run_cancelled_on_another_replica():
    """Cancel on replica B only flipped the row; replica A's run streamed on
    for up to 30 minutes holding a slot. The heartbeat now sees the session
    is no longer running and cancels the local run."""
    from research_service import ResearchService

    service = object.__new__(ResearchService)
    service.heartbeat_interval = 0.01
    beats = []

    async def still_running(session_id):
        beats.append(session_id)
        return len(beats) < 2  # cancelled elsewhere after the first beat

    service._write_research_heartbeat = still_running

    async def scenario():
        owner = asyncio.create_task(asyncio.sleep(30))
        service._active_tasks = {"s1": owner}
        await asyncio.wait_for(service._heartbeat_research("s1"), timeout=2)
        await asyncio.sleep(0)
        return owner.cancelled()

    assert asyncio.run(scenario()) is True


def test_visibility_timeout_outlasts_a_busy_memory_retry():
    """With task_time_limit=5000 and visibility 5001 the busy-retry countdown
    (5060) outlived the done-marker and the broker visibility window. Run in
    a subprocess: the values are computed at import."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "CELERY_TASK_TIME_LIMIT_SECONDS": "5000",
           "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS": "4900",
           "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS": "5001",
           "RAG_INGESTION_TASK_TIME_LIMIT_SECONDS": "3900",
           "RAG_INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS": "3840"}
    out = subprocess.run(
        [sys.executable, "-c", "import celery_app as c; print(c._visibility_timeout, c.memory_execution_lease_seconds())"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert int(out[0]) > int(out[1])


from tests.test_memory_service_owner_scope import _service as _owner_scope_service  # noqa: E402


class _RestoreConn:
    """A memory fact restore: inactive row, then the UPDATE under the lock."""

    def __init__(self, row, active_count):
        self.row, self.active_count, self.calls, self.update_query = row, active_count, 0, ""
        self.fresh_active = False

    async def fetchrow(self, query, *_params):
        self.calls += 1
        if self.calls == 1:
            return {**self.row, "is_active": False}
        self.update_query = query
        return self.row

    async def fetch(self, _query, *_params):
        return [self.row]

    async def execute(self, *_args):
        return "OK"

    async def fetchval(self, query, *_args):
        if "FOR UPDATE" in query:
            return self.fresh_active  # the fact's state re-read under the lock
        return self.active_count

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def close(self):
        return None


def _restore(monkeypatch, active_count):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import UUID

    import memory_service

    memory_id, user_id = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"
    now = datetime.now(timezone.utc)
    row = {"id": UUID(memory_id), "user_id": UUID(user_id), "content": "fact", "fact_type": "observation",
           "confidence": 0.9, "namespace": "default", "is_active": True, "created_at": now,
           "updated_at": now, "metadata": {}, "weaviate_id": memory_id}
    conn = _RestoreConn(row, active_count)
    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=conn))
    _also_route_acquire(monkeypatch, memory_service, lambda: conn)
    svc = _owner_scope_service()
    svc.max_facts = 10
    svc.store = SimpleNamespace(update_embedding=AsyncMock(return_value=memory_id), deactivate_embedding=AsyncMock())
    return svc, conn, memory_id, user_id


def test_restoring_a_memory_reconciles_its_vector(monkeypatch):
    svc, conn, memory_id, user_id = _restore(monkeypatch, active_count=3)
    asyncio.run(svc.update_memory(memory_id, user_id, {"is_active": True}))
    assert "vector_sync_pending = true" in conn.update_query
    svc.store.update_embedding.assert_awaited_once()


def test_restoring_a_memory_at_the_fact_cap_is_refused(monkeypatch):
    """A restore ignored LANGMEM_MAX_FACTS_PER_USER; the next consolidation
    then expired other, untouched facts to get back under it."""
    import memory_service

    svc, conn, memory_id, user_id = _restore(monkeypatch, active_count=10)
    with pytest.raises(memory_service.MemoryCapacityError):
        asyncio.run(svc.update_memory(memory_id, user_id, {"is_active": True}))
    assert conn.update_query == ""  # no UPDATE was issued


def test_a_restore_racing_a_delete_is_still_capped(monkeypatch):
    """The snapshot said active (no cap check), a concurrent delete then
    deactivated the fact, and the UPDATE reactivated it past the cap."""
    import memory_service

    svc, conn, memory_id, user_id = _restore(monkeypatch, active_count=10)
    stale_snapshot = conn.fetchrow

    async def snapshot_says_active(query, *params):
        row = await stale_snapshot(query, *params)
        return {**row, "is_active": True} if conn.calls == 1 else row

    conn.fetchrow = snapshot_says_active
    with pytest.raises(memory_service.MemoryCapacityError):
        asyncio.run(svc.update_memory(memory_id, user_id, {"is_active": True}))
    assert conn.update_query == ""


def _research_service_with_slow_thread_delete():
    from research_client import ResearchResponse, ResearchResult, ResearchStatus
    from research_service import ResearchService

    service = object.__new__(ResearchService)
    service.heartbeat_interval = 0.05
    service._cancel_requested_tasks = set()
    state = {"status": "running", "deleted": False, "failure_recorded": None}

    async def store(_sid, _result):
        state["status"] = "completed"
        return True

    async def record_failure(_sid, msg):
        state["failure_recorded"] = msg
        return state["status"] in ("pending", "running")

    class Client:
        async def start_research(self, req):
            return ResearchResponse(session_id="t1", status=ResearchStatus.PENDING, message="")

        async def wait_for_completion(self, sid, max_wait_time):
            return ResearchResponse(session_id=sid, status=ResearchStatus.COMPLETED, message="")

        async def get_research_result(self, sid):
            return ResearchResult(session_id=sid, title="t", summary="s", content="c", sources=[], metadata={})

        async def delete_thread(self, sid):
            await asyncio.sleep(0.2)  # a slow (<=10 s timeout) DELETE /threads
            state["deleted"] = True

        def discard_pending(self, sid):
            pass

    async def ok(*_a):
        return True

    async def heartbeat(_sid):
        return state["status"] == "running"

    service._mark_research_running = ok
    service._write_research_heartbeat = heartbeat
    service._append_research_log = ok
    service._store_research_result = store
    service._record_research_failure = record_failure
    service.research_client = Client()
    return service, state


def test_a_completed_research_run_is_not_cancelled_by_its_own_heartbeat():
    """46d995bd made the heartbeat cancel on UPDATE 0; this replica's own
    COMPLETED write also gives UPDATE 0, so a slow thread delete was cancelled
    and the LangGraph thread leaked."""
    service, state = _research_service_with_slow_thread_delete()

    async def scenario():
        task = asyncio.create_task(service._run_research_background("s1", "q", 1, "searxng", None))
        service._active_tasks = {"s1": task}
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "finished"

    outcome = asyncio.run(scenario())
    assert outcome == "finished" and state["deleted"], (outcome, state)


def test_recall_applies_min_confidence_inside_the_vector_query(monkeypatch):
    """Recall asked the vector store for `limit` hits and dropped low-confidence
    ones afterwards, so low-confidence neighbours took the slots and recall
    returned fewer hits than qualifying facts existed (#1452)."""
    from unittest.mock import AsyncMock

    import httpx

    import memory_store

    store = memory_store.MemoryStore("postgresql://atlas", embedding_dimension=3)
    monkeypatch.setattr(store, "_generate_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3]))
    executed = []

    class Conn:
        async def fetch(self, query, *params):
            executed.append((query, params))
            return []

        async def close(self):
            pass

    _also_route_acquire(monkeypatch, memory_store, Conn)
    asyncio.run(store._search_pgvector("q", "00000000-0000-4000-8000-000000000009", "default", 5, 0.7))
    query, params = executed[0]
    assert "confidence >= $6" in query and params[5] == 0.7

    sent = {}

    class Client:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, json):
            sent["query"] = json["query"]
            return httpx.Response(200, json={"data": {"Get": {memory_store.WEAVIATE_COLLECTION_NAME: []}}},
                                  request=httpx.Request("POST", url))

    monkeypatch.setattr(memory_store.httpx, "AsyncClient", Client)
    store.weaviate_url = "http://weaviate:8080"
    asyncio.run(store._search_weaviate("q", "u", "default", 5, 0.7))
    assert 'path: ["confidence"], operator: GreaterThanEqual, valueNumber: 0.7' in sent["query"]


def test_recall_passes_its_threshold_to_the_store(monkeypatch):
    import memory_service

    svc = memory_service.MemoryService.__new__(memory_service.MemoryService)
    svc.enabled, svc._initialized, svc.namespace = True, True, "default"
    seen = {}

    async def search_similar(**kwargs):
        seen.update(kwargs)
        return []

    async def noop(*_a, **_k):
        return None

    svc.store = SimpleNamespace(search_similar=search_similar)
    svc._ensure_initialized = noop
    svc._reconcile_pending_vectors = noop

    class Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    svc._acquire = lambda: Conn()
    asyncio.run(svc.recall("00000000-0000-4000-8000-000000000001", "q", min_confidence=0.8))
    assert seen["min_confidence"] == 0.8


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["database_restart", "model_not_registered"])
async def test_a_failure_that_hits_every_row_is_retried_next_pass(monkeypatch, failure):
    """Only a row's own rejection may be skipped; a Postgres restart or a
    LiteLLM model not registered yet hit every row, and remembering them hid
    the rows until a backend restart (2026-10-08 run, cycle 24)."""
    import asyncpg
    import httpx

    import memory_service as mod

    request = httpx.Request("POST", "http://litellm/embeddings")
    errors = {
        "database_restart": asyncpg.exceptions.CannotConnectNowError("the database system is starting up"),
        "model_not_registered": httpx.HTTPStatusError(
            "bad", request=request,
            response=httpx.Response(400, request=request, text="Invalid model name passed in model=x"),
        ),
    }

    class Store:
        backend = "weaviate"

        async def initialize(self):
            return None

        async def update_embedding(self, fact_id=None, **kwargs):
            raise errors[failure]

        async def deactivate_embedding(self, *a, **k):
            return None

    rows = _pending_rows(2)
    seen = []

    class FakeConn:
        async def fetch(self, query, *args):
            if "DESC NULLS LAST" in query:
                return rows[-1:]
            ids, stamps = args[:2]
            seen.append(list(ids))
            return rows

        async def execute(self, *_a, **_k):
            return "UPDATE 1"

        async def close(self):
            return None

    svc = mod.MemoryService.__new__(mod.MemoryService)
    svc.store = Store()
    svc.database_url = "postgresql://x"
    monkeypatch.setattr(mod, "connect_postgres", AsyncMock(return_value=FakeConn()))
    _also_route_acquire(monkeypatch, mod, FakeConn)

    await svc._reconcile_pending_vectors()
    await svc._reconcile_pending_vectors()
    assert seen[1] == []


# #1755: a stateful canonical-fact adapter exercises the real reconciliation
# entry point; the opt-in Postgres cases below separately qualify its SQL.
from datetime import datetime, timedelta, timezone  # noqa: E402
from uuid import UUID, uuid4  # noqa: E402


_RECONCILE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _reconcile_fact(number, *, stamp=_RECONCILE_TIME, content="bad", active=True):
    return {
        "id": UUID(int=number), "user_id": UUID(int=2**120),
        "namespace": "test-1755", "content": content, "fact_type": "observation",
        "confidence": 0.9, "is_active": active, "weaviate_id": None,
        "updated_at": stamp, "vector_sync_pending": True,
    }


def _reconcile_order(row):
    stamp = row["updated_at"]
    return stamp is not None, stamp or _RECONCILE_TIME, row["id"]


class _ReconcileFacts:
    def __init__(self, rows):
        self.rows = {row["id"]: row for row in rows}
        self.queries = []
        self.closed = 0
        self.clear_error = None

    def _matching_versions(self, ids, stamps):
        return [{"id": key} for key, stamp in zip(ids, stamps)
                if key in self.rows and self.rows[key]["vector_sync_pending"]
                and self.rows[key]["updated_at"] == stamp]

    @staticmethod
    def _within_interval(rows, bounds):
        cursor_ts, cursor_id, upper_ts, upper_id = bounds
        upper = _reconcile_order({"updated_at": upper_ts, "id": upper_id})
        rows = [r for r in rows if _reconcile_order(r) <= upper]
        if cursor_id is not None:
            cursor = _reconcile_order({"updated_at": cursor_ts, "id": cursor_id})
            rows = [r for r in rows if _reconcile_order(r) > cursor]
        return rows

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        if "AS cached" in query:
            return self._matching_versions(*args)
        pending = sorted((r for r in self.rows.values() if r["vector_sync_pending"]),
                         key=_reconcile_order)
        if "DESC NULLS LAST" in query:
            return [dict(pending[-1])] if pending else []
        return self._uncached_page(pending, args)

    def _uncached_page(self, pending, args):
        rejected = dict(zip(args[0], args[1]))
        rows = [r for r in pending if r["id"] not in rejected
                or rejected[r["id"]] != r["updated_at"]]
        if len(args) == 6:
            rows = self._within_interval(rows, args[2:])
        return [dict(r) for r in rows[:100]]

    async def execute(self, query, *args):
        if self.clear_error:
            raise self.clear_error
        if "WITH vector_owner AS MATERIALIZED" in query:
            assert "AND fact.updated_at = vector_owner.updated_at" in query
            return self._clear_vector_owner(args)
        key, vector_id, stamp = args
        assert "AND updated_at = $3" in query
        row = self.rows[key]
        if stamp is not None and row["updated_at"] == stamp:
            row["vector_sync_pending"] = False
            return "UPDATE 1"
        return "UPDATE 0"

    def _clear_vector_owner(self, args):
        key, _vector_id, *expected = args
        row = self.rows[key]
        actual = [row[field] for field in (
            "user_id", "namespace", "content", "fact_type", "confidence"
        )]
        if (actual != expected or not row["is_active"] or not row["vector_sync_pending"]
                or row["updated_at"] is None):
            return "UPDATE 0"
        row["vector_sync_pending"] = False
        return "UPDATE 1"

    async def close(self):
        self.closed += 1


class _ReconcileTarget:
    def __init__(self):
        self.attempts = []
        self.actions = []
        self.failure = None

    async def update_embedding(self, **kwargs):
        key = UUID(kwargs["fact_id"])
        self.attempts.append(key)
        if self.failure:
            raise self.failure
        if kwargs["content"] == "bad":
            import httpx
            request = httpx.Request("POST", "http://controlled-target/objects")
            raise httpx.HTTPStatusError("row rejected", request=request,
                response=httpx.Response(422, text="content rejected", request=request))
        self.actions.append(("update", key))
        return "new-vector"

    async def deactivate_embedding(self, fact_id, _weaviate_id):
        key = UUID(fact_id)
        self.attempts.append(key)
        self.actions.append(("retire", key))


def _reconcile_service():
    import memory_service
    service = memory_service.MemoryService.__new__(memory_service.MemoryService)
    service.database_url = "postgresql://controlled-fixture"
    service.store = _ReconcileTarget()
    return service


async def _bounded_reconcile(service, conn, **kwargs):
    attempts, queries = len(service.store.attempts), len(conn.queries)
    result = await service._reconcile_pending_vectors(conn=conn, **kwargs)
    assert len(service.store.attempts) - attempts <= 100
    assert len(conn.queries) - queries <= 3
    assert len(service._rejected_rows()) <= 10_000
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    None, ("content", "newer"), ("fact_type", "instruction"),
    ("confidence", 0.5), ("is_active", False), ("namespace", "other"),
    ("user_id", UUID(int=3)),
])
async def test_reconcile_owns_current_vector_state_after_embedding_trigger(change):
    service = _reconcile_service()
    row = _reconcile_fact(1, content="valid")
    conn = _ReconcileFacts([row])
    original = service.store.update_embedding

    async def trigger_then_change(**kwargs):
        vector_id = await original(**kwargs)
        # Every real embedding UPDATE invokes the shipped updated_at trigger.
        row["updated_at"] += timedelta(seconds=1)
        if change is not None:
            field, value = change
            row[field] = value
            row["updated_at"] += timedelta(seconds=1)
        return vector_id

    service.store.update_embedding = trigger_then_change
    assert await service._reconcile_pending_vectors(conn=conn) == 1
    assert row["vector_sync_pending"] is (change is not None)
    assert service.store.attempts == [row["id"]]
    assert row["updated_at"] > _RECONCILE_TIME


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [9999, 10_000, 10_100])
@pytest.mark.parametrize("active", [True, False], ids=["update", "retirement"])
async def test_reconcile_advances_after_real_rejection_history(history, active):
    service = _reconcile_service()
    conn = _ReconcileFacts([_reconcile_fact(i) for i in range(1, history + 1)])
    for _ in range(math.ceil(history / 100)):
        await _bounded_reconcile(service, conn)
    # Every history entry actually hit the target. Unchanged cached facts stay
    # present and pending, so stale cleanup cannot disguise cache saturation.
    assert set(service.store.attempts) == set(conn.rows)
    assert len(service._rejected_rows()) == min(history, 10_000)
    later = _reconcile_fact(history + 102, content="valid", active=active)
    for i in range(history + 1, history + 102):
        row = _reconcile_fact(i)
        conn.rows[row["id"]] = row
    conn.rows[later["id"]] = later
    before = len(service.store.attempts)
    for _ in range(5):
        await _bounded_reconcile(service, conn)
    assert ("update" if active else "retire", later["id"]) in service.store.actions
    assert not later["vector_sync_pending"]
    assert len(set(service.store.attempts[before:])) >= 102


@pytest.mark.asyncio
async def test_reconcile_unique_null_safe_order_and_cached_null():
    service = _reconcile_service()
    rows = [_reconcile_fact(i, content="valid") for i in range(1, 203)]
    rows[0]["updated_at"] = None
    rows[1]["updated_at"] = datetime(1960, 1, 1, tzinfo=timezone.utc)
    rows[2]["updated_at"] = datetime(1970, 1, 1, tzinfo=timezone.utc)
    conn = _ReconcileFacts(reversed(rows))
    service._rejected_rows()[rows[0]["id"]] = None
    for _ in range(3):
        await _bounded_reconcile(service, conn)
    assert service.store.attempts == [r["id"] for r in sorted(rows[1:], key=_reconcile_order)]
    assert conn.rows[rows[0]["id"]]["vector_sync_pending"]
    assert service._rejected_rows() == {rows[0]["id"]: None}


@pytest.mark.asyncio
@pytest.mark.parametrize("watermark_change", ["delete", "change"])
async def test_reconcile_fixed_watermark_wraps_despite_newer_arrivals(watermark_change):
    service = _reconcile_service()
    rows = [_reconcile_fact(i, content="valid") for i in range(1, 202)]
    conn = _ReconcileFacts(rows)
    await _bounded_reconcile(service, conn)
    upper = service._vector_reconcile_upper
    older = _reconcile_fact(500, stamp=_RECONCILE_TIME - timedelta(days=1), content="valid")
    conn.rows[older["id"]] = older
    if watermark_change == "delete":
        del conn.rows[rows[-1]["id"]]
    else:
        conn.rows[rows[-1]["id"]]["updated_at"] += timedelta(days=2)
    for i in range(3):
        new = _reconcile_fact(600+i, stamp=_RECONCILE_TIME + timedelta(days=1), content="valid")
        conn.rows[new["id"]] = new
        await _bounded_reconcile(service, conn)
        if i == 0:
            assert service._vector_reconcile_upper == upper
    assert ("update", older["id"]) in service.store.actions
    assert not older["vector_sync_pending"]


@pytest.mark.asyncio
async def test_reconcile_rotating_cache_reclaims_deleted_and_nonpending_versions():
    service = _reconcile_service()
    rows = [_reconcile_fact(i) for i in range(1, 10_001)]
    conn = _ReconcileFacts(rows)
    service._rejected_rows().update({r["id"]: r["updated_at"] for r in rows})
    deleted, nonpending = rows[201], rows[202]
    del conn.rows[deleted["id"]]
    nonpending["vector_sync_pending"] = False
    await _bounded_reconcile(service, conn)
    assert deleted["id"] in service._rejected_rows()
    assert nonpending["id"] in service._rejected_rows()
    await _bounded_reconcile(service, conn)
    await _bounded_reconcile(service, conn)
    assert deleted["id"] not in service._rejected_rows()
    assert nonpending["id"] not in service._rejected_rows()
    assert service.store.attempts == []
    new = _reconcile_fact(20_000)
    conn.rows[new["id"]] = new
    await _bounded_reconcile(service, conn)
    assert new["id"] in service._rejected_rows()
    assert len(service._rejected_rows()) == 9999
    _assert_cache_validation_bound(conn)


@pytest.mark.asyncio
async def test_reconcile_changed_cached_row_succeeds_and_releases_slot():
    service = _reconcile_service()
    rows = [_reconcile_fact(i) for i in range(1, 10_001)]
    conn = _ReconcileFacts(rows)
    service._rejected_rows().update({r["id"]: r["updated_at"] for r in rows})
    changed = rows[500]
    changed["content"] = "valid"
    changed["updated_at"] += timedelta(seconds=1)
    assert await _bounded_reconcile(service, conn) == 1
    assert service.store.actions == [("update", changed["id"])]
    assert not changed["vector_sync_pending"]
    assert changed["id"] not in service._rejected_rows()
    assert len(service._rejected_rows()) == 9999


@pytest.mark.asyncio
async def test_reconcile_cache_query_failure_preserves_snapshot_and_owned_connection(monkeypatch):
    import memory_service
    service = _reconcile_service()
    row = _reconcile_fact(1)
    conn = _ReconcileFacts([row])
    service._rejected_rows()[row["id"]] = row["updated_at"]
    snapshot = list(service._rejected_rows().items())
    conn.fetch = AsyncMock(side_effect=ConnectionError("validation failed"))
    monkeypatch.setattr(memory_service, "connect_postgres", AsyncMock(return_value=conn))
    with pytest.raises(ConnectionError, match="validation failed"):
        await service._reconcile_pending_vectors()
    assert list(service._rejected_rows().items()) == snapshot
    assert conn.closed == 1
    assert service.store.attempts == []


def test_reconcile_rejection_replaces_existing_key_at_capacity():
    import memory_service
    service = _reconcile_service()
    cache = service._rejected_rows()
    cache.update({UUID(int=i): _RECONCILE_TIME for i in range(1, 10_001)})
    row = _reconcile_fact(500, stamp=None)
    import httpx
    request = httpx.Request("POST", "http://controlled-target/objects")
    error = httpx.HTTPStatusError("bad", request=request,
                                 response=httpx.Response(422, request=request))
    memory_service._remember_rejected(cache, row, error)
    assert cache[row["id"]] is None
    memory_service._remember_rejected(cache, _reconcile_fact(20_000), error)
    assert len(cache) == 10_000
    assert UUID(int=20_000) not in cache


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["retry", "clear", "cancel"])
async def test_reconcile_raised_work_retries_current_row(problem):
    service = _reconcile_service()
    conn = _ReconcileFacts([_reconcile_fact(i, content="valid") for i in range(1, 103)])
    original = service.store.update_embedding

    async def fail_second(**kwargs):
        if UUID(kwargs["fact_id"]) == UUID(int=2):
            if problem == "cancel":
                raise asyncio.CancelledError()
            if problem == "retry":
                raise ConnectionError("target down")
            conn.clear_error = RuntimeError("clear failed")
        return await original(**kwargs)

    service.store.update_embedding = fail_second
    error = {"retry": ConnectionError, "clear": RuntimeError, "cancel": asyncio.CancelledError}[problem]
    with pytest.raises(error):
        await service._reconcile_pending_vectors(conn=conn, retry_transient=True)
    assert service._vector_reconcile_cursor == (_RECONCILE_TIME, UUID(int=1))
    assert service._vector_reconcile_upper == (_RECONCILE_TIME, UUID(int=102))
    assert conn.rows[UUID(int=2)]["vector_sync_pending"]
    assert not conn.closed
    conn.clear_error = None
    service.store.update_embedding = original
    assert await _bounded_reconcile(service, conn) == 100
    assert service._vector_reconcile_cursor[1] == UUID(int=101)
    assert not conn.rows[UUID(int=2)]["vector_sync_pending"]


@pytest.mark.asyncio
async def test_reconcile_nonraising_halt_keeps_unhandled_tail_for_next_pass():
    import memory_service
    service = _reconcile_service()
    rows = [_reconcile_fact(i, content="valid") for i in range(1, 8)]
    conn = _ReconcileFacts(rows)
    service.store.failure = ConnectionError("target down")
    assert await _bounded_reconcile(service, conn) == 0
    count = memory_service._RECONCILE_TRANSIENT_STREAK
    assert service._vector_reconcile_cursor[1] == UUID(int=count)
    assert service._vector_reconcile_upper is not None
    service.store.failure = None
    assert await _bounded_reconcile(service, conn) == len(rows) - count
    assert service.store.attempts[count] == UUID(int=count+1)
    assert service._vector_reconcile_upper is None
    assert await _bounded_reconcile(service, conn) == count
    assert all(not row["vector_sync_pending"] for row in rows)


@pytest.mark.asyncio
async def test_reconcile_serializes_pages_and_acquires_owned_connection_inside_lock(monkeypatch):
    import memory_service
    service = _reconcile_service()
    conn = _ReconcileFacts([_reconcile_fact(i, content="valid") for i in range(1, 103)])
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.store.update_embedding

    async def blocked(**kwargs):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await original(**kwargs)

    service.store.update_embedding = blocked
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(memory_service, "connect_postgres", connect)
    first = asyncio.create_task(service._reconcile_pending_vectors())
    await entered.wait()
    second = asyncio.create_task(service._reconcile_pending_vectors())
    await asyncio.sleep(0)
    assert connect.await_count == 1
    assert len(conn.queries) == 2
    release.set()
    assert await asyncio.gather(first, second) == [100, 2]
    assert len(service.store.attempts) == len(set(service.store.attempts)) == 102
    assert conn.closed == 2
    assert service._vector_reconcile_upper is None
    assert await service._reconcile_pending_vectors(conn=conn) == 0
    assert conn.closed == 2


@asynccontextmanager
async def _postgres_reconcile_facts(rows):
    """Opt-in scan/clear SQL fixture; no vector/trigger/auth/RLS qualification."""
    import os
    import asyncpg
    url = os.environ.get("ATLAS_TEST_MEMORY_RECONCILE_POSTGRES_URL")
    if not url:
        pytest.skip("set ATLAS_TEST_MEMORY_RECONCILE_POSTGRES_URL to an owned scan fixture")
    conn = await asyncpg.connect(url)
    user = uuid4()
    columns = tuple(rows[0])
    for row in rows:
        row["id"], row["user_id"] = uuid4(), user
    try:
        await conn.copy_records_to_table("memory_facts", schema_name="public",
            columns=columns, records=[tuple(row[c] for c in columns) for row in rows])
        yield conn
    finally:
        await conn.execute("DELETE FROM public.memory_facts WHERE user_id = $1", user)
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False], ids=["update", "retirement"])
async def test_reconcile_postgres_full_cache_advances_beyond_rejections(active):
    service = _reconcile_service()
    history = [_reconcile_fact(i) for i in range(1, 10_001)]
    rows = history + [_reconcile_fact(i, stamp=_RECONCILE_TIME + timedelta(seconds=1))
                      for i in range(10_001, 10_101)]
    later = _reconcile_fact(10_101, stamp=_RECONCILE_TIME + timedelta(seconds=2),
                            content="valid", active=active)
    rows.append(later)
    async with _postgres_reconcile_facts(rows) as conn:
        # The fake-history regression above accumulates real target failures;
        # this case qualifies the resulting full-cache SQL and bounded pages.
        service._rejected_rows().update({r["id"]: r["updated_at"] for r in history})
        for _ in range(3):
            before = len(service.store.attempts)
            await service._reconcile_pending_vectors(conn=conn)
            assert len(service.store.attempts) - before <= 100
            assert len(service._rejected_rows()) <= 10_000
        assert ("update" if active else "retire", later["id"]) in service.store.actions
        assert not await conn.fetchval(
            "SELECT vector_sync_pending FROM public.memory_facts WHERE id = $1", later["id"])
        assert await conn.fetchval(
            "SELECT count(*) FROM public.memory_facts WHERE user_id=$1 AND vector_sync_pending",
            later["user_id"]) == 10_100


@pytest.mark.asyncio
@pytest.mark.parametrize("null_only", [False, True])
async def test_reconcile_postgres_null_order_cursor_watermark_and_exclusion(null_only):
    service = _reconcile_service()
    rows = [_reconcile_fact(i, stamp=None, content="valid") for i in range(1, 203)]
    if not null_only:
        for row in rows[101:]:
            row["updated_at"] = datetime(1960, 1, 1, tzinfo=timezone.utc)
        rows[-1]["updated_at"] = datetime(1970, 1, 1, tzinfo=timezone.utc)
    async with _postgres_reconcile_facts(rows) as conn:
        cached = rows[0]
        service._rejected_rows()[cached["id"]] = None
        expected = [r["id"] for r in sorted(rows[1:], key=_reconcile_order)]
        for _ in range(3):
            await service._reconcile_pending_vectors(conn=conn)
        assert service.store.attempts == expected
        assert service._rejected_rows() == {cached["id"]: None}
        # The existing '=' optimistic clear deliberately leaves NULL versions
        # pending. Scheduling them does not repair that independent boundary.
        for row in (cached, rows[1], rows[-1]):
            pending = await conn.fetchval(
                "SELECT vector_sync_pending FROM public.memory_facts WHERE id=$1", row["id"])
            assert pending is (row["updated_at"] is None)


@pytest.mark.asyncio
async def test_reconcile_postgres_rotating_cache_and_changed_success():
    service = _reconcile_service()
    rows = [_reconcile_fact(i) for i in range(1, 206)]
    async with _postgres_reconcile_facts(rows) as conn:
        service._rejected_rows().update({r["id"]: r["updated_at"] for r in rows})
        # Place all stale signatures beyond the first two rotation batches.
        deleted, nonpending, changed = list(sorted(rows, key=lambda r: r["id"]))[-3:]
        # Dictionary order, rather than SQL order, controls cleanup rotation.
        cache = service._rejected_rows()
        for row in (deleted, nonpending, changed):
            cache[row["id"]] = cache.pop(row["id"])
        await conn.execute("DELETE FROM public.memory_facts WHERE id=$1", deleted["id"])
        await conn.execute("UPDATE public.memory_facts SET vector_sync_pending=false WHERE id=$1",
                           nonpending["id"])
        await conn.execute("UPDATE public.memory_facts SET content='valid', updated_at=$2 WHERE id=$1",
                           changed["id"], _RECONCILE_TIME + timedelta(seconds=1))
        assert await service._reconcile_pending_vectors(conn=conn) == 1
        assert ("update", changed["id"]) in service.store.actions
        assert changed["id"] not in cache
        _assert_cached_versions(cache, [deleted, nonpending], present=True)
        for _ in range(2):
            await service._reconcile_pending_vectors(conn=conn)
        _assert_cached_versions(cache, [deleted, nonpending, changed], present=False)
        assert len(cache) == 202


@pytest.mark.asyncio
async def test_reconcile_postgres_competing_edit_preserves_guarded_pending_intent():
    service = _reconcile_service()
    row = _reconcile_fact(1, content="valid")
    async with _postgres_reconcile_facts([row]) as conn:
        original = service.store.update_embedding

        async def concurrent_edit(**kwargs):
            await conn.execute("UPDATE public.memory_facts SET content='newer', updated_at=$2 WHERE id=$1",
                               row["id"], row["updated_at"] + timedelta(seconds=1))
            return await original(**kwargs)

        service.store.update_embedding = concurrent_edit
        await service._reconcile_pending_vectors(conn=conn)
        assert await conn.fetchval(
            "SELECT vector_sync_pending FROM public.memory_facts WHERE id=$1", row["id"])
        service.store.update_embedding = original
        await service._reconcile_pending_vectors(conn=conn)
        assert not await conn.fetchval(
            "SELECT vector_sync_pending FROM public.memory_facts WHERE id=$1", row["id"])
        assert service.store.actions == [("update", row["id"])] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("vector_edit", [False, True], ids=["trigger-only", "content-edit"])
async def test_reconcile_postgres_clear_follows_embedding_trigger_version(vector_edit):
    service = _reconcile_service()
    row = _reconcile_fact(1, content="valid")
    async with _postgres_reconcile_facts([row]) as conn:
        original = service.store.update_embedding

        async def embedding_write(**kwargs):
            vector_id = await original(**kwargs)
            # The fixture installs no trigger: advance the version as the shipped one does.
            await conn.execute("UPDATE public.memory_facts SET updated_at = updated_at"
                               " + interval '1 second' WHERE id=$1", row["id"])
            if vector_edit:
                await conn.execute("UPDATE public.memory_facts SET content='newer',"
                                   " updated_at = updated_at + interval '1 second'"
                                   " WHERE id=$1", row["id"])
            return vector_id

        service.store.update_embedding = embedding_write
        assert await service._reconcile_pending_vectors(conn=conn) == 1
        stored = await conn.fetchrow(
            "SELECT vector_sync_pending, weaviate_id FROM public.memory_facts WHERE id=$1",
            row["id"])
        assert stored["vector_sync_pending"] is vector_edit
        assert stored["weaviate_id"] == (None if vector_edit else "new-vector")


def _assert_cache_validation_bound(conn):
    for sql, args in conn.queries:
        if "AS cached" in sql:
            assert len(args[0]) <= 100


@pytest.mark.asyncio
@pytest.mark.parametrize("newer_version", [False, True])
async def test_reconcile_success_removes_only_its_cached_version(newer_version):
    service = _reconcile_service()
    row = _reconcile_fact(1, content="valid")
    conn = _ReconcileFacts([row])
    original = service.store.update_embedding
    cached_stamp = row["updated_at"] + timedelta(seconds=int(newer_version))

    async def cache_during_write(**kwargs):
        service._rejected_rows()[row["id"]] = cached_stamp
        return await original(**kwargs)

    service.store.update_embedding = cache_during_write
    assert await _bounded_reconcile(service, conn) == 1
    assert (row["id"] in service._rejected_rows()) is newer_version
    if newer_version:
        assert service._rejected_rows()[row["id"]] == cached_stamp
    assert not row["vector_sync_pending"]


@pytest.mark.asyncio
async def test_reconcile_validation_does_not_erase_replaced_version():
    service = _reconcile_service()
    row = _reconcile_fact(1)
    conn = _ReconcileFacts([row])
    old_stamp = row["updated_at"]
    new_stamp = old_stamp + timedelta(seconds=1)
    service._rejected_rows()[row["id"]] = old_stamp
    fetch = conn.fetch

    async def replace_during_validation(query, *args):
        if "AS cached" in query:
            service._rejected_rows()[row["id"]] = new_stamp
            return []
        return await fetch(query, *args)

    conn.fetch = replace_during_validation
    # Isolate validation's conditional removal from the deliberately obsolete
    # entry cleanup before an attempted sync.
    await service._refresh_rejected_rows(conn)
    assert service._rejected_rows() == {row["id"]: new_stamp}


def _assert_cached_versions(cache, rows, *, present):
    for row in rows:
        assert (row["id"] in cache) is present


def _reconcile_http_failure(status):
    import httpx
    request = httpx.Request("POST", "http://controlled-target/objects")
    return httpx.HTTPStatusError("controlled failure", request=request,
        response=httpx.Response(status, text="content rejected", request=request))


def _script_reconcile_failures(service, outcomes):
    original = service.store.update_embedding
    statuses = dict(enumerate(outcomes, 1))

    async def scripted(**kwargs):
        status = statuses.get(UUID(kwargs["fact_id"]).int)
        service.store.failure = _reconcile_http_failure(status) if status else None
        return await original(**kwargs)

    service.store.update_embedding = scripted
    return original


def _assert_mixed_reconcile_halt(service, conn, outcomes):
    stopped = len(outcomes)
    assert service.store.attempts == [UUID(int=i) for i in range(1, stopped+1)]
    assert service._vector_reconcile_cursor == (_RECONCILE_TIME, UUID(int=stopped))
    assert service._vector_reconcile_upper == (_RECONCILE_TIME, UUID(int=len(conn.rows)))
    rejected = {UUID(int=i): _RECONCILE_TIME for i, status in enumerate(outcomes, 1)
                if status == 400}
    assert service._rejected_rows() == rejected
    for i, status in enumerate(outcomes, 1):
        assert conn.rows[UUID(int=i)]["vector_sync_pending"] is (status is not None)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcomes", [
    [503, 503, 400, 503],
    [429, 400, 429, 400, 429],
    [503, 503, None, 503, 400, 503, 503],
], ids=["row-rejection-does-not-count", "row-rejection-does-not-reset", "progress-resets"])
async def test_reconcile_mixed_failure_streak_halts_and_preserves_unhandled_tail(outcomes):
    service = _reconcile_service()
    rows = [_reconcile_fact(i, content="valid") for i in range(1, 12)]
    conn = _ReconcileFacts(rows)
    original = _script_reconcile_failures(service, outcomes)
    assert await _bounded_reconcile(service, conn) == outcomes.count(None)
    _assert_mixed_reconcile_halt(service, conn, outcomes)
    # The halt leaves every prefetched but unhandled row for the next pass.
    service.store.update_embedding = original
    service.store.failure = None
    assert await _bounded_reconcile(service, conn) == len(rows) - len(outcomes)
    assert service.store.attempts[len(outcomes)] == UUID(int=len(outcomes)+1)
    assert service._vector_reconcile_upper is None
