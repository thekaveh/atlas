from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


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
