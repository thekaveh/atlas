"""Weaviate failback quarantine and collection model identity (#1365).

One fact the embedder rejects must not hold memory on pgvector for good, and
the Weaviate collection must name the exact LiteLLM embedding alias.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

GOOD_ID = "00000000-0000-4000-8000-000000000001"
BAD_ID = "00000000-0000-4000-8000-000000000002"
USER_ID = "00000000-0000-4000-8000-000000000010"
QUARANTINE_SLICE = (
    Path(__file__).resolve().parents[4]
    / "supabase/db/scripts/18-backend-memory-vector-quarantine.sql"
)


def _fact(fact_id, *, pending, content="fact"):
    return {
        "id": fact_id, "user_id": USER_ID, "namespace": "default",
        "content": content, "fact_type": "observation", "confidence": 1.0,
        "is_active": True, "weaviate_id": None, "updated_at": f"{fact_id}-v0",
        "vector_sync_pending": pending, "vector_sync_attempts": 0,
        "vector_sync_error": None,
    }


def _http_error(status, body="rejected", method="PUT"):
    request = httpx.Request(method, "http://weaviate/v1/objects/Memory/x")
    response = httpx.Response(status, request=request, text=body)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class FakeMemoryDb:
    """memory_facts plus the schema-state singleton, with the semantics of
    14-backend-memory.sql and 18-backend-memory-vector-quarantine.sql."""

    def __init__(self, rows, threshold, generation=5):
        self.rows = {row["id"]: dict(row) for row in rows}
        self.threshold = threshold
        self.generation = generation
        self.rebuild_required = True
        self.versions = 0

    def _bump(self, row):
        # update_memory_facts_updated_at
        self.versions += 1
        row["updated_at"] = f"{row['id']}-v{self.versions}"

    def _update(self, row, **values):
        old_content = row["content"]
        row.update(values)
        # reset_memory_facts_vector_sync_quarantine (BEFORE UPDATE OF ...)
        if ("vector_sync_pending" in values or "content" in values) and (
            not row["vector_sync_pending"] or row["content"] != old_content
        ):
            row["vector_sync_attempts"] = 0
            row["vector_sync_error"] = None
        self._bump(row)

    def blocking_pending(self):
        return [
            row for row in self.rows.values()
            if row["vector_sync_pending"]
            and row["vector_sync_attempts"] < self.threshold
        ]

    def quarantined(self):
        return [
            row for row in self.rows.values()
            if row["vector_sync_pending"]
            and row["vector_sync_attempts"] >= self.threshold
        ]

    def conn(self):
        return FakeConn(self)


class FakeConn:
    def __init__(self, db):
        self.db = db

    async def fetch(self, query, last_id):
        assert "FROM public.memory_facts" in query
        ordered = sorted(self.db.rows.values(), key=lambda row: row["id"])
        return [
            dict(row) for row in ordered if last_id is None or row["id"] > last_id
        ][:100]

    async def fetchrow(self, query, *args):
        assert "memory_embedding_schema_state" in query
        if "vector_sync_pending = true" not in query:
            pending = False
        elif re.search(r"vector_sync_attempts\s*<\s*%d\b" % self.db.threshold, query):
            pending = bool(self.db.blocking_pending())
        else:
            pending = any(r["vector_sync_pending"] for r in self.db.rows.values())
        return {
            "rebuild_required": self.db.rebuild_required or pending,
            "weaviate_dirty_generation": self.db.generation,
        }

    async def fetchval(self, query, *args):
        assert "complete_memory_weaviate_rebuild" in query
        generation = args[0]
        if generation != self.db.generation or self.db.blocking_pending():
            return False
        self.db.rebuild_required = False
        return True

    async def execute(self, query, *args):
        query = " ".join(query.split())
        if "vector_sync_attempts = vector_sync_attempts + 1" in query:
            fact_id, version, error = args
            row = self.db.rows.get(fact_id)
            if row is None or not row["is_active"] or row["updated_at"] != version:
                return "UPDATE 0"
            self.db._update(
                row, vector_sync_pending=True,
                vector_sync_attempts=row["vector_sync_attempts"] + 1,
                vector_sync_error=error,
            )
            return "UPDATE 1"
        if "SET vector_sync_pending = false" in query and "updated_at = $3" in query:
            fact_id, weaviate_id, version = args
            row = self.db.rows.get(fact_id)
            if row is None or row["updated_at"] != version:
                return "UPDATE 0"
            self.db._update(row, vector_sync_pending=False, weaviate_id=weaviate_id)
            return "UPDATE 1"
        raise AssertionError(f"unexpected statement: {query}")


def _failback_store(monkeypatch, db, store_weaviate):
    import memory_store

    @asynccontextmanager
    async def acquire(_url):
        yield db.conn()

    async def clear_active(_conn, row, weaviate_id):
        stored = db.rows[row["id"]]
        db._update(stored, vector_sync_pending=False, weaviate_id=weaviate_id)
        return True

    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    store.backend = "pgvector"
    store.weaviate_state = "unavailable"
    store._initialized = True
    monkeypatch.setattr(memory_store, "acquire_conn", acquire)
    monkeypatch.setattr(memory_store, "_clear_active_vector_intent", clear_active)
    monkeypatch.setattr(store, "_probe_weaviate_ready", AsyncMock())
    monkeypatch.setattr(store, "_store_weaviate", store_weaviate)
    monkeypatch.setattr(store, "_delete_stale_weaviate_objects", AsyncMock())
    monkeypatch.setattr(store, "_store_pgvector", AsyncMock())
    monkeypatch.setattr(store, "_mark_weaviate_dirty", AsyncMock())
    return store


def test_quarantine_threshold_matches_the_sql_slice():
    import memory_store

    sql = QUARANTINE_SLICE.read_text(encoding="utf-8")
    threshold = memory_store.VECTOR_SYNC_QUARANTINE_ATTEMPTS
    assert re.findall(r"vector_sync_attempts < (\d+)", sql) == [str(threshold)]
    # One probe's bounded passes are enough to quarantine a rejected fact.
    assert threshold <= memory_store.MAX_FAILBACK_REBUILD_ATTEMPTS


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_pending", [True, False], ids=["pending", "synced-before"])
async def test_http_422_fact_is_quarantined_and_failback_leaves_pgvector(
    monkeypatch, bad_pending
):
    import memory_store

    db = FakeMemoryDb(
        [_fact(GOOD_ID, pending=False), _fact(BAD_ID, pending=bad_pending)],
        memory_store.VECTOR_SYNC_QUARANTINE_ATTEMPTS,
    )
    stored = []

    async def store_weaviate(fact_id, *_args):
        if fact_id == BAD_ID:
            raise _http_error(422, "object content rejected")
        stored.append(fact_id)
        return fact_id

    store = _failback_store(monkeypatch, db, store_weaviate)

    assert await store.probe_weaviate() is True

    bad = db.rows[BAD_ID]
    attempts = memory_store.VECTOR_SYNC_QUARANTINE_ATTEMPTS
    # The pass went on past the rejected fact every time, and the stored error
    # never echoes the rejected content.
    assert (
        store.backend, store.weaviate_state, db.rebuild_required,
        bad["vector_sync_pending"], bad["vector_sync_attempts"], bad["vector_sync_error"],
        db.quarantined() == [bad], stored, db.rows[GOOD_ID]["vector_sync_pending"],
    ) == (
        "weaviate", "ready", False,
        True, attempts, "HTTPStatusError: HTTP 422",
        True, [GOOD_ID] * attempts, False,
    )


@pytest.mark.asyncio
async def test_vectorizer_row_rejection_from_the_pgvector_shadow_is_quarantined(
    monkeypatch,
):
    """A pending fact's direct embed can reject it after Weaviate took it."""
    import memory_store

    db = FakeMemoryDb(
        [_fact(BAD_ID, pending=True)], memory_store.VECTOR_SYNC_QUARANTINE_ATTEMPTS
    )
    store = _failback_store(monkeypatch, db, AsyncMock(return_value=BAD_ID))
    monkeypatch.setattr(
        store, "_store_pgvector",
        AsyncMock(side_effect=_http_error(400, "input too long", method="POST")),
    )

    assert await store.probe_weaviate() is True

    assert db.rows[BAD_ID]["vector_sync_error"] == "HTTPStatusError: HTTP 400"
    assert db.quarantined() == [db.rows[BAD_ID]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("weaviate down"),
        httpx.ReadTimeout("weaviate slow"),
        _http_error(503, "unavailable"),
        _http_error(500, "vectorize: invalid model ollama/x", method="POST"),
        _http_error(429, "slow down"),
    ],
    ids=["connect", "timeout", "5xx", "systematic-vectorizer", "429"],
)
async def test_transient_error_aborts_the_pass_without_clearing_pending(
    monkeypatch, error
):
    import memory_store

    db = FakeMemoryDb(
        [_fact(BAD_ID, pending=True), _fact("00000000-0000-4000-8000-000000000003", pending=True)],
        memory_store.VECTOR_SYNC_QUARANTINE_ATTEMPTS,
    )
    before = {key: dict(row) for key, row in db.rows.items()}
    store_weaviate = AsyncMock(side_effect=error)
    store = _failback_store(monkeypatch, db, store_weaviate)

    with pytest.raises(type(error)):
        await store._sync_weaviate_from_postgres()
    # The pass stopped at the first row and touched no durable state.
    assert store_weaviate.await_count == 1
    assert db.rows == before

    assert await store.probe_weaviate() is False
    assert store.backend == "pgvector"
    assert db.rebuild_required is True
    assert all(row["vector_sync_pending"] for row in db.rows.values())
    assert all(row["vector_sync_attempts"] == 0 for row in db.rows.values())


@pytest.mark.asyncio
async def test_quarantined_fact_does_not_force_a_rebuild_on_every_probe(monkeypatch):
    import memory_store

    threshold = memory_store.VECTOR_SYNC_QUARANTINE_ATTEMPTS
    bad = dict(_fact(BAD_ID, pending=True), vector_sync_attempts=threshold)
    db = FakeMemoryDb([bad], threshold)
    db.rebuild_required = False
    store = _failback_store(monkeypatch, db, AsyncMock())
    rebuild = AsyncMock()
    monkeypatch.setattr(store, "_sync_weaviate_from_postgres", rebuild)

    assert await store.probe_weaviate() is True

    rebuild.assert_not_awaited()
    assert store.backend == "weaviate"


@pytest.mark.asyncio
async def test_rejection_is_not_charged_to_a_concurrently_edited_version(monkeypatch):
    import memory_store

    db = FakeMemoryDb(
        [_fact(BAD_ID, pending=True)], memory_store.VECTOR_SYNC_QUARANTINE_ATTEMPTS
    )

    async def store_weaviate(*_args):
        db._update(db.rows[BAD_ID], content="edited", vector_sync_pending=True)
        raise _http_error(422)

    store = _failback_store(monkeypatch, db, store_weaviate)

    await store._sync_weaviate_from_postgres()

    assert db.rows[BAD_ID]["vector_sync_attempts"] == 0
    assert db.rows[BAD_ID]["vector_sync_error"] is None


@pytest.mark.asyncio
async def test_memory_health_reports_quarantined_fact_count(monkeypatch):
    import memory_service
    import memory_store

    queries = []

    class Conn:
        async def fetchrow(self, query, *args):
            queries.append((" ".join(query.split()), args))
            return {"facts_count": 7, "vector_sync_quarantined": 2}

    @asynccontextmanager
    async def acquire():
        yield Conn()

    monkeypatch.setenv("DATABASE_URL", "postgresql://atlas")
    service = memory_service.MemoryService()
    service.enabled = True
    service.store = SimpleNamespace(
        backend="weaviate",
        vector_backend_state=lambda: {
            "reason": "initial_probe_succeeded", "weaviate_state": "ready",
            "embedding_dimension": 768,
        },
    )
    monkeypatch.setattr(service, "_ensure_initialized", AsyncMock())
    monkeypatch.setattr(service, "_acquire", acquire)

    result = await service.health_check()

    assert result["facts_count"] == 7
    assert result["vector_sync_quarantined"] == 2
    query, args = queries[0]
    assert "vector_sync_pending AND vector_sync_attempts >= $1" in query
    assert args == (memory_store.VECTOR_SYNC_QUARANTINE_ATTEMPTS,)

    from memory_models import MemoryHealthResponse

    assert MemoryHealthResponse(**result).vector_sync_quarantined == 2


class _SchemaClient:
    """Captures collection creation and direct embedding requests."""

    def __init__(self, calls, existing=None):
        self.calls = calls
        self.existing = existing

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url):
        if self.existing is None:
            return SimpleNamespace(status_code=404)
        return SimpleNamespace(status_code=200, json=lambda: self.existing)

    async def delete(self, _url):
        self.calls.append(("delete", None))
        return SimpleNamespace(status_code=204)

    async def post(self, url, json, headers=None):
        if url.endswith("/v1/embeddings"):
            self.calls.append(("embed", json["model"]))
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"data": [{"embedding": [0.0] * 768}]},
            )
        self.calls.append(("create", json["moduleConfig"]["text2vec-openai"]["model"]))
        return SimpleNamespace(status_code=201)


MODELS = ["ollama/x", "org/model", "ollama/library/nested", "nomic-embed-text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_collection_model_equals_litellm_embedding_model(monkeypatch, model):
    import memory_store

    monkeypatch.setenv("LITELLM_EMBEDDING_MODEL", model)
    calls = []
    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate", manage_schema=True
    )
    monkeypatch.setattr(memory_store.httpx, "AsyncClient", lambda **_kw: _SchemaClient(calls))
    monkeypatch.setattr(store, "_mark_weaviate_dirty", AsyncMock())

    await store._ensure_weaviate_collection()
    await store._generate_embedding("probe")

    # One identity through both paths: the collection and the direct embed.
    assert calls == [("create", model), ("embed", model)]


def _existing_collection(model):
    import memory_store

    return {
        "vectorizer": "text2vec-openai",
        "moduleConfig": {
            "text2vec-openai": {"model": model, "baseURL": "http://litellm:4000"}
        },
        "properties": [
            {"name": name, "tokenization": "field"}
            for name in memory_store._EXACT_MATCH_PROPERTIES
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model, legacy",
    [("ollama/x", "x"), ("org/model", "model"),
     ("provider-a/custom-embedding", "custom-embedding")],
)
async def test_stripped_or_suffix_collection_model_is_replaced(
    monkeypatch, model, legacy
):
    """A suffix that names another alias cannot satisfy the full identity."""
    import memory_store

    calls = []
    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate",
        embedding_model=model, manage_schema=True,
    )
    monkeypatch.setattr(
        memory_store.httpx, "AsyncClient",
        lambda **_kw: _SchemaClient(calls, _existing_collection(legacy)),
    )
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    await store._ensure_weaviate_collection()

    mark.assert_awaited_once()
    assert calls == [("delete", None), ("create", model)]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
async def test_full_model_collection_is_left_intact(monkeypatch, model):
    import memory_store

    calls = []
    store = memory_store.MemoryStore(
        "postgresql://atlas", weaviate_url="http://weaviate",
        embedding_model=model, manage_schema=True,
    )
    monkeypatch.setattr(
        memory_store.httpx, "AsyncClient",
        lambda **_kw: _SchemaClient(calls, _existing_collection(model)),
    )
    mark = AsyncMock()
    monkeypatch.setattr(store, "_mark_weaviate_dirty", mark)

    await store._ensure_weaviate_collection()

    mark.assert_not_awaited()
    assert calls == []


@pytest.mark.asyncio
async def test_seeded_failback_quarantines_a_rejected_fact_through_the_real_sql():
    """Opt-in: the shipped slices' completion CAS and reset trigger."""
    import memory_store
    from tests.test_memory_dimension_contract import _seeded_memory_fact

    async with _seeded_memory_fact() as (url, conn, fact):
        await conn.execute(
            "SELECT public.ensure_memory_weaviate_identity('test/embed', 768)"
        )
        store = memory_store.MemoryStore(
            url, weaviate_url="http://weaviate", embedding_model="test/embed",
            embedding_dimension=768, manage_schema=True,
        )
        store.backend = "pgvector"
        store._initialized = True
        store._probe_weaviate_ready = AsyncMock()
        store._store_weaviate = AsyncMock(side_effect=_http_error(422))
        store._delete_stale_weaviate_objects = AsyncMock()
        store._deactivate_weaviate = AsyncMock()

        assert await store.probe_weaviate() is True
        assert store.backend == "weaviate"
        row = await conn.fetchrow(
            "SELECT vector_sync_pending, vector_sync_attempts, vector_sync_error"
            " FROM public.memory_facts WHERE id = $1", fact)
        assert tuple(row) == (
            True, memory_store.VECTOR_SYNC_QUARANTINE_ATTEMPTS, "HTTPStatusError: HTTP 422",
        )

        await conn.execute(
            "UPDATE public.memory_facts SET vector_sync_pending = false WHERE id = $1", fact)
        row = await conn.fetchrow(
            "SELECT vector_sync_attempts, vector_sync_error"
            " FROM public.memory_facts WHERE id = $1", fact)
        assert tuple(row) == (0, None)
