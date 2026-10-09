"""RAG ingestion job engine tests (#413).

Drives the phase orchestrator with fake upstreams (no live services) over a tiny
text corpus, covering: full lifecycle, idempotent re-submit, parser fallback,
capability fail/skip semantics, drain timeout, cancellation, schema drift, path
safety, partial retry, and the Celery task wrapper. The orchestrator is async but
tests call it via ``asyncio.run`` so no ``pytest-asyncio`` is required.
"""
from __future__ import annotations

import asyncio
import gc
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from rag_ingestion.clients import (
    CorpusFile,
    CorpusPathError,
    LightRagClient,
    MinioCorpusReader,
    MountCorpusReader,
    ParserAdapter,
    WeaviateClient,
)
from rag_ingestion.profiles import ProfileNotFoundError, load_profiles
from rag_ingestion.service import (
    Deps,
    IngestionExecutionLeaseLost,
    RagIngestionService,
)
from rag_ingestion.models import IngestionRecord
from rag_ingestion.store import (
    ExecutionClaim,
    InMemoryIngestionStore,
    RedisIngestionStore,
)


# ── fakes ────────────────────────────────────────────────────────────

class FakeEmbedder:
    def __init__(self, available=True):
        self._available = available
    def available(self):
        return self._available
    async def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeWeaviate:
    def __init__(self, available=True):
        self._available = available
        self.written = []
        self.classes = []
        self.object_ids = set()
        self.reconciled = []
        self.preserved = []
        self.source_of = {}
    def available(self):
        return self._available
    async def ensure_class(self, class_name, embedding=None):
        self.classes.append(class_name)
    async def write_objects(self, class_name, objects):
        self.written.extend(objects)
        self.object_ids.update(obj["id"] for obj in objects)
        # Track each object's source so the fake can model per-source
        # preservation the way the real client does.
        for obj in objects:
            self.source_of[obj["id"]] = obj.get("properties", {}).get("source")
        return len(objects)
    async def reconcile_objects(
        self, class_name, profile_name, desired_ids, preserve_sources=None
    ):
        keep_sources = set(preserve_sources or ())
        self.reconciled.append((class_name, profile_name, list(desired_ids)))
        self.preserved.append(sorted(keep_sources))
        survivors = set(desired_ids) | {
            oid for oid in self.object_ids if self.source_of.get(oid) in keep_sources
        }
        self.object_ids.intersection_update(survivors)
        return 0


class FakeLightrag:
    def __init__(self, available=True, busy_cycles=0):
        self._available = available
        self._busy_cycles = busy_cycles
        self.uploaded = []
    def available(self):
        return self._available
    async def upload(self, documents):
        self.uploaded.extend(documents)
        return len(documents)
    async def pipeline_busy(self):
        if self._busy_cycles == "forever":
            return True
        if self._busy_cycles > 0:
            self._busy_cycles -= 1
            return True
        return False


class FailingLightrag(FakeLightrag):
    async def upload(self, documents):
        request = httpx.Request("POST", "http://lightrag:9621/documents/text")
        response = httpx.Response(400, request=request, text="x" * 1200)
        raise httpx.HTTPStatusError(
            "LightRAG rejected the document", request=request, response=response
        )


class RaisingExtractor:
    """Stands in for a reachable-but-failing Docling/Tika endpoint."""
    async def extract(
        self, *, content, filename=None, content_type=None, extractor=None, chunking=True
    ):
        raise RuntimeError("docling exploded")


class KeywordOnlyExtractor:
    def __init__(self):
        self.calls = []

    async def extract(self, *, content, filename, content_type, extractor=None, chunking=True):
        self.calls.append(
            {
                "content": content,
                "filename": filename,
                "content_type": content_type,
                "extractor": extractor,
                "chunking": chunking,
            }
        )
        return SimpleNamespace(content=f"parsed by {extractor}", extractor=extractor)


# ── helpers ──────────────────────────────────────────────────────────

def _corpus(tmp_path: Path, monkeypatch, files: dict[str, str]) -> Path:
    root = tmp_path / "corpus-root"
    (root / "docs").mkdir(parents=True)
    for name, content in files.items():
        (root / "docs" / name).write_text(content, encoding="utf-8")
    monkeypatch.setenv("RAG_INGESTION_CORPUS_ROOT", str(root))
    return root


def _profiles_file(tmp_path: Path, *, parser_order=None, vector=None, graph=None, corpus=None) -> str:
    profile = {
        "consumer": "rag-showcase",
        "name": "showcase-default",
        "revision": "rev1",
        "corpus": corpus or {"source": "mount", "path": "docs"},
        "parser_order": parser_order or ["plain_text"],
        "chunker": {"strategy": "recursive", "chunk_size": 64, "overlap": 8},
        "vector_targets": vector if vector is not None else [
            {"backend": "weaviate", "collection_prefix": "RagShowcase", "on_unavailable": "fail"}
        ],
        "graph_targets": graph if graph is not None else [
            {"backend": "lightrag", "mode": "upload_documents", "wait_for_extraction": True,
             "timeout_seconds": 1, "on_unavailable": "skip"}
        ],
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"version": 1, "profiles": [profile]}), encoding="utf-8")
    return str(path)


def _service(tmp_path, deps, profiles_path):
    return RagIngestionService(store=InMemoryIngestionStore(), deps=deps, profiles_path=profiles_path)


def _run(service, profile="showcase-default", corpus_path=None):
    record, created = service.submit(profile, corpus_path=corpus_path)
    final = asyncio.run(service.run(record.id))
    return record, created, final


# ── tests ────────────────────────────────────────────────────────────

def test_end_to_end_completes(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "the quick brown fox jumps over the lazy dog"})
    pf = _profiles_file(tmp_path)
    weav = FakeWeaviate()
    lr = FakeLightrag()
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=weav, lightrag=lr, poll_interval=0.01), pf)
    _, created, final = _run(svc)
    assert created is True
    assert final.status == "completed"
    assert final.counts["files_discovered"] == 1
    assert final.counts["documents_parsed"] == 1
    assert final.counts["chunks"] >= 1
    assert final.counts["vectors_written"] == final.counts["chunks"]
    assert final.counts["documents_uploaded"] == 1
    assert weav.classes == ["RagShowcase_showcase_default"]
    assert final.content_digest
    assert [p.status for p in final.phases if p.name == "drain"] == ["completed"]


def test_parser_adapter_uses_keyword_contract_and_exact_parser_selection():
    extractor = KeywordOnlyExtractor()
    parsed = asyncio.run(
        ParserAdapter(extractor).parse(
            CorpusFile("notes.txt", b"hello", "text/plain"),
            ["tika", "plain_text"],
        )
    )

    assert parsed.text == "parsed by tika"
    assert parsed.parser == "tika"
    assert extractor.calls == [
        {
            "content": b"hello",
            "filename": "notes.txt",
            "content_type": "text/plain",
            "extractor": "tika",
            "chunking": False,
        }
    ]


def test_redis_ingestion_store_configures_bounded_socket_deadlines(monkeypatch):
    import redis

    captured = {}
    sentinel = object()

    def fake_from_url(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return sentinel

    monkeypatch.setattr(redis.Redis, "from_url", fake_from_url)

    store = RedisIngestionStore("redis://redis:6379/0")

    assert store._redis is sentinel
    assert captured["socket_connect_timeout"] == 3
    assert captured["socket_timeout"] == 3


def test_redis_ingestion_page_keeps_cursor_when_full_migration_yields_no_new_score(
    monkeypatch,
):
    """Duplicate legacy members must not terminate a still-bounded migration."""
    import redis

    class FakeRedis:
        def scard(self, _key):
            return 5

        def zintercard(self, _numkeys, _keys):
            return 3  # two set-only members still to migrate

        def eval(self, script, *_args):
            assert "SSCAN" in script
            return [0, "9", 2]

        def zrangebyscore(self, *_args, **_kwargs):
            return []

        def mget(self, _keys):
            raise AssertionError("an empty score page must not issue MGET")

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_args, **_kwargs: FakeRedis())
    store = RedisIngestionStore("redis://redis:6379/0")

    page = store.list_page(cursor="7", limit=2)

    assert page.records == []
    assert page.next_cursor == "7"


def test_sync_discovery_and_chunking_run_off_the_event_loop(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "the quick brown fox"})
    pf = _profiles_file(tmp_path)
    main_thread = threading.get_ident()
    threads = {}

    from rag_ingestion.clients import CorpusReader
    import chunking_service

    corpus = CorpusReader()
    original_discover = corpus.discover
    original_chunk = chunking_service.chunk_text

    def checked_discover(*args, **kwargs):
        threads["discover"] = threading.get_ident()
        return original_discover(*args, **kwargs)

    def checked_chunk(*args, **kwargs):
        threads["chunk"] = threading.get_ident()
        return original_chunk(*args, **kwargs)

    monkeypatch.setattr(corpus, "discover", checked_discover)
    monkeypatch.setattr(chunking_service, "chunk_text", checked_chunk)
    svc = _service(
        tmp_path,
        Deps(
            corpus=corpus,
            embedder=FakeEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(),
            poll_interval=0.01,
        ),
        pf,
    )

    _, _, final = _run(svc)

    assert final.status == "completed"
    assert threads["discover"] != main_thread
    assert threads["chunk"] != main_thread


def test_lightrag_client_uses_current_file_source_contract(monkeypatch):
    requests = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = LightRagClient(endpoint="http://lightrag:9621", api_key="secret")

    uploaded = asyncio.run(
        client.upload([{"text": "graph text", "source": "graph_native/a.txt"}])
    )

    assert uploaded == 1
    payload = requests[0][1]["json"]
    assert payload["text"] == "graph text"
    assert payload["file_source"].startswith("atlas-")
    assert payload["file_source"].endswith(".txt")
    assert "/" not in payload["file_source"]
    assert "description" not in requests[0][1]["json"]


def test_lightrag_file_sources_are_stable_and_path_unique(monkeypatch):
    payloads = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, _url, **kwargs):
            payloads.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = LightRagClient(endpoint="http://lightrag:9621", api_key="secret")
    documents = [
        {"text": "one", "source": "dir1/a.txt"},
        {"text": "two", "source": "dir2/a.txt"},
    ]

    asyncio.run(client.upload(documents))
    first_sources = [payload["file_source"] for payload in payloads]
    payloads.clear()
    asyncio.run(client.upload(documents))

    assert first_sources[0] != first_sources[1]
    assert [payload["file_source"] for payload in payloads] == first_sources


def test_lightrag_duplicate_file_source_is_idempotent(monkeypatch):
    class ConflictResponse:
        status_code = 409

        def raise_for_status(self):
            raise AssertionError("duplicate 409 must be accepted")

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, _url, **_kwargs):
            return ConflictResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = LightRagClient(endpoint="http://lightrag:9621", api_key="secret")

    assert asyncio.run(
        client.upload([{"text": "same", "source": "a.txt"}])
    ) == 1


def test_lightrag_failure_records_bounded_upstream_body(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    monkeypatch.setattr(
        "chunking_service.chunk_text",
        lambda request: type(
            "ChunkResponse",
            (),
            {"chunks": [type("Chunk", (), {"index": 0, "content": request.text})()]},
        )(),
    )
    pf = _profiles_file(tmp_path)
    svc = _service(
        tmp_path,
        Deps(
            embedder=FakeEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FailingLightrag(),
            poll_interval=0.01,
        ),
        pf,
    )

    _, _, final = _run(svc)

    assert final.status == "failed"
    assert final.errors[0]["http_status"] == 400
    assert final.errors[0]["body"] == "x" * 500
    assert final.phase("lightrag_upload").error["body"] == "x" * 500


def test_idempotent_resubmit_dedups(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "hello world"})
    pf = _profiles_file(tmp_path)
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(), poll_interval=0.01), pf)
    first, created1 = svc.submit("showcase-default")
    asyncio.run(svc.run(first.id))
    second, created2 = svc.submit("showcase-default")
    assert created1 is True and created2 is False
    assert second.id == first.id


def test_content_change_at_same_corpus_path_creates_fresh_ingestion(
    tmp_path, monkeypatch
):
    root = _corpus(tmp_path, monkeypatch, {"a.txt": "first body"})
    pf = _profiles_file(tmp_path)
    svc = _service(
        tmp_path,
        Deps(
            embedder=FakeEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(),
            poll_interval=0.01,
        ),
        pf,
    )
    first, created_first = svc.submit("showcase-default")
    asyncio.run(svc.run(first.id))

    (root / "docs" / "a.txt").write_text("other body", encoding="utf-8")
    second, created_second = svc.submit("showcase-default")

    assert created_first is True
    assert created_second is True
    assert second.id != first.id


def test_empty_corpus_reconciles_away_prior_profile_objects(tmp_path, monkeypatch):
    root = _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)
    weaviate = FakeWeaviate()
    service = _service(
        tmp_path,
        Deps(
            embedder=FakeEmbedder(),
            weaviate=weaviate,
            lightrag=FakeLightrag(available=False),
            poll_interval=0.01,
        ),
        profile_path,
    )
    first, _ = service.submit("showcase-default")
    assert asyncio.run(service.run(first.id)).status == "completed"
    assert weaviate.object_ids

    (root / "docs" / "a.txt").unlink()
    second, created = service.submit("showcase-default")
    final = asyncio.run(service.run(second.id))

    assert created is True
    assert final.status == "completed", final.errors
    assert weaviate.object_ids == set()
    assert weaviate.reconciled[-1] == (
        "RagShowcase_showcase_default",
        "showcase-default",
        [],
    )


def test_run_uses_submitted_profile_snapshot_after_registry_changes(
    tmp_path, monkeypatch
):
    root = _corpus(tmp_path, monkeypatch, {"a.txt": "submitted body"})
    (root / "replacement").mkdir()
    (root / "replacement" / "b.txt").write_text(
        "replacement body", encoding="utf-8"
    )
    profile_path = _profiles_file(tmp_path)
    weaviate = FakeWeaviate()
    service = _service(
        tmp_path,
        Deps(
            embedder=FakeEmbedder(),
            weaviate=weaviate,
            lightrag=FakeLightrag(available=False),
            poll_interval=0.01,
        ),
        profile_path,
    )
    record, created = service.submit("showcase-default")

    changed = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    changed["profiles"][0]["revision"] = "rev2"
    changed["profiles"][0]["corpus"]["path"] = "replacement"
    changed["profiles"][0]["vector_targets"][0]["collection_prefix"] = "Changed"
    Path(profile_path).write_text(json.dumps(changed), encoding="utf-8")

    final = asyncio.run(service.run(record.id))

    assert created is True
    assert final.status == "completed", final.errors
    assert final.revision == "rev1"
    assert final.corpus == {"source": "mount", "path": "docs"}
    assert final.profile_snapshot["revision"] == "rev1"
    assert final.counts["files_discovered"] == 1
    assert weaviate.classes == ["RagShowcase_showcase_default"]


def test_run_preserves_submitted_mount_override_after_registry_changes(
    tmp_path, monkeypatch
):
    root = _corpus(tmp_path, monkeypatch, {"default.txt": "default body"})
    (root / "override").mkdir()
    (root / "override" / "selected.txt").write_text(
        "selected body", encoding="utf-8"
    )
    profile_path = _profiles_file(tmp_path)
    service = _service(
        tmp_path,
        Deps(
            embedder=FakeEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(available=False),
            poll_interval=0.01,
        ),
        profile_path,
    )
    record, _ = service.submit("showcase-default", corpus_path="override")
    changed = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    changed["profiles"][0]["corpus"]["path"] = "docs"
    Path(profile_path).write_text(json.dumps(changed), encoding="utf-8")

    final = asyncio.run(service.run(record.id))

    assert final.status == "completed", final.errors
    assert final.corpus == {"source": "mount", "path": "override"}
    assert final.counts["files_discovered"] == 1


def test_parser_fallback_to_plain_text(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "fallback body text here"})
    pf = _profiles_file(tmp_path, parser_order=["docling", "plain_text"])
    from rag_ingestion.clients import ParserAdapter
    deps = Deps(
        parser=ParserAdapter(extractor=RaisingExtractor()),
        embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(), poll_interval=0.01,
    )
    svc = _service(tmp_path, deps, pf)
    _, _, final = _run(svc)
    assert final.status == "completed"
    assert final.counts["documents_parsed"] == 1  # docling failed, plain_text succeeded


def test_concurrent_submit_atomically_claims_one_idempotency_record(
    tmp_path, monkeypatch
):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path)
    barrier = threading.Barrier(2)

    class RacingStore(InMemoryIngestionStore):
        def find_by_idempotency_key(self, key):
            barrier.wait(timeout=5)
            return super().find_by_idempotency_key(key)

    svc = RagIngestionService(
        store=RacingStore(),
        deps=Deps(
            embedder=FakeEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(),
            poll_interval=0.01,
        ),
        profiles_path=pf,
    )
    results = []

    def submit():
        results.append(svc.submit("showcase-default"))

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert len({record.id for record, _ in results}) == 1
    assert sorted(created for _, created in results) == [False, True]


def test_cancellation_survives_a_stale_worker_save(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path)
    store = InMemoryIngestionStore()
    svc = RagIngestionService(
        store=store,
        deps=Deps(
            embedder=FakeEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(),
            poll_interval=0.01,
        ),
        profiles_path=pf,
    )
    record, _ = svc.submit("showcase-default")
    stale_worker_copy = store.get(record.id)

    assert stale_worker_copy is not None
    assert store.request_cancel(record.id) is True
    stale_worker_copy.status = "running"
    store.save(stale_worker_copy)

    persisted = store.get(record.id)
    assert persisted is not None
    assert persisted.cancel_requested is True


def test_execution_claim_fences_non_owner_saves_and_allows_recovery():
    store = InMemoryIngestionStore()
    record = IngestionRecord(
        id="ingestion-1",
        consumer="acme",
        profile="default",
        revision="1",
        idempotency_key="key-1",
    )
    store.create_if_absent(record)

    assert store.claim_execution(record.id, ExecutionClaim("worker-a", 60)) is True
    assert store.claim_execution(record.id, ExecutionClaim("worker-b", 60)) is False
    claimed = store.get(record.id)
    claimed.status = "running"
    assert store.save_claimed(claimed, "worker-b") is False
    assert store.save_claimed(claimed, "worker-a") is True
    assert store.release_execution(record.id, "worker-a") is True
    assert store.claim_execution(record.id, ExecutionClaim("worker-b", 60)) is True


def test_execution_claim_recovery_replaces_only_exact_ambiguous_owner():
    store = InMemoryIngestionStore()
    record = IngestionRecord(
        id="ingestion-recovery",
        consumer="acme",
        profile="default",
        revision="1",
        idempotency_key="key-recovery",
    )
    store.create_if_absent(record)

    assert store.claim_execution(record.id, ExecutionClaim("old", 60)) is True
    assert store.claim_execution(
        record.id, ExecutionClaim("fresh-a", 60, "unrelated")
    ) is False
    assert store.claim_execution(
        record.id, ExecutionClaim("fresh-a", 60, "old")
    ) is True
    assert store.claim_execution(
        record.id, ExecutionClaim("fresh-b", 60, "old")
    ) is False
    assert store.claim_execution(
        record.id, ExecutionClaim("fresh-a", 60, "fresh-a")
    ) is False


def test_run_rejects_concurrent_execution_before_side_effects(tmp_path, monkeypatch):
    from rag_ingestion.service import IngestionExecutionBusy

    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)
    store = InMemoryIngestionStore()
    embedder = FakeEmbedder()
    service = RagIngestionService(
        store=store,
        deps=Deps(
            embedder=embedder,
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(),
            poll_interval=0.01,
        ),
        profiles_path=profile_path,
    )
    record, _ = service.submit("showcase-default")
    assert store.claim_execution(record.id, ExecutionClaim("worker-a", 60)) is True

    with pytest.raises(IngestionExecutionBusy):
        asyncio.run(
            service.run(
                record.id,
                retry_transient=True,
                execution_owner="worker-b",
                execution_lease_seconds=60,
            )
        )

    assert embedder.available() is True
    assert store.get(record.id).status == "pending"


def test_run_propagates_execution_recovery_claim(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)

    class RecordingStore(InMemoryIngestionStore):
        def __init__(self):
            super().__init__()
            self.claims = []

        def claim_execution(self, ingestion_id, claim):
            self.claims.append((ingestion_id, claim))
            return super().claim_execution(ingestion_id, claim)

    store = RecordingStore()
    service = RagIngestionService(
        store=store,
        deps=Deps(
            embedder=FakeEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(available=False),
            poll_interval=0.01,
        ),
        profiles_path=profile_path,
    )
    record, _ = service.submit("showcase-default")

    asyncio.run(
        service.run(
            record.id,
            execution_owner="fresh-owner",
            execution_recovery_owner="ambiguous-owner",
            execution_lease_seconds=60,
        )
    )

    assert store.claims == [
        (
            record.id,
            ExecutionClaim("fresh-owner", 60, "ambiguous-owner"),
        )
    ]


@pytest.mark.parametrize("lease_seconds", (True, 9, 301, 30.0, "30"))
def test_run_rejects_invalid_execution_lease_before_claim(
    tmp_path, monkeypatch, lease_seconds
):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)
    store = InMemoryIngestionStore()
    service = RagIngestionService(
        store=store,
        deps=Deps(
            embedder=FakeEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(),
        ),
        profiles_path=profile_path,
    )
    record, _ = service.submit("showcase-default")

    with pytest.raises(ValueError, match="RAG_INGESTION_EXECUTION_LEASE_SECONDS"):
        asyncio.run(
            service.run(record.id, execution_lease_seconds=lease_seconds)
        )

    assert store.claim_execution(record.id, ExecutionClaim("worker-a", 60)) is True


def test_missing_profile_does_not_strand_execution_claim(tmp_path):
    store = InMemoryIngestionStore()
    record = IngestionRecord(
        id="missing-profile-ingestion",
        consumer="acme",
        profile="missing",
        revision="1",
        idempotency_key="missing-profile-key",
    )
    store.create_if_absent(record)
    service = RagIngestionService(
        store=store,
        deps=Deps(),
        profiles_path=str(tmp_path / "missing-profiles.json"),
    )

    with pytest.raises(ProfileNotFoundError):
        asyncio.run(service.run(record.id))

    assert store.claim_execution(record.id, ExecutionClaim("worker-a", 60)) is True


def test_run_cancels_active_phase_when_execution_lease_is_lost(
    tmp_path, monkeypatch
):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)
    store = InMemoryIngestionStore()

    class LeaseLosingService(RagIngestionService):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.phase_started = asyncio.Event()
            self.phase_cancelled = False

        async def _heartbeat_execution(
            self,
            ingestion_id,
            owner,
            lease_seconds,
            stop,
            lease_lost=None,
        ):
            await self.phase_started.wait()
            if lease_lost is not None:
                lease_lost.set()

        async def _run_phase(self, *args, **kwargs):
            self.phase_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.phase_cancelled = True
                raise RuntimeError("phase cleanup failed")

    service = LeaseLosingService(
        store=store,
        deps=Deps(),
        profiles_path=profile_path,
    )
    record, _ = service.submit("showcase-default")

    with pytest.raises(IngestionExecutionLeaseLost):
        asyncio.run(
            asyncio.wait_for(
                service.run(record.id, execution_lease_seconds=10),
                timeout=1,
            )
        )

    assert service.phase_cancelled is True


def test_parent_cancellation_finishes_phase_before_releasing_execution_lease(
    tmp_path, monkeypatch
):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)
    events: list[str] = []

    class TrackingStore(InMemoryIngestionStore):
        def release_execution(self, ingestion_id, owner):
            events.append("lease_released")
            return super().release_execution(ingestion_id, owner)

    class BlockingService(RagIngestionService):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.phase_started = asyncio.Event()

        async def _run_phase(self, *args, **kwargs):
            self.phase_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("phase_cancelled")
                raise

    store = TrackingStore()
    service = BlockingService(store=store, deps=Deps(), profiles_path=profile_path)
    record, _ = service.submit("showcase-default")

    async def scenario():
        running = asyncio.create_task(service.run(record.id))
        await service.phase_started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        survivors = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert survivors == []

    asyncio.run(scenario())

    assert events == ["phase_cancelled", "lease_released"]


def test_phase_cleanup_failure_does_not_leak_lease_watcher(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)

    class FailingCleanupService(RagIngestionService):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.phase_started = asyncio.Event()

        async def _run_phase(self, *args, **kwargs):
            self.phase_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                raise RuntimeError("phase cleanup failed") from exc

    service = FailingCleanupService(
        store=InMemoryIngestionStore(), deps=Deps(), profiles_path=profile_path
    )
    record, _ = service.submit("showcase-default")

    async def scenario():
        running = asyncio.create_task(service.run(record.id))
        await service.phase_started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        survivors = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert survivors == []

    asyncio.run(scenario())


def test_parent_cancel_racing_phase_failure_consumes_child_exception(
    tmp_path, monkeypatch
):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)

    class RacingService(RagIngestionService):
        parent: asyncio.Task | None = None

        async def _run_phase(self, *args, **kwargs):
            assert self.parent is not None
            asyncio.get_running_loop().call_soon(self.parent.cancel)
            raise RuntimeError("phase failed in completion/cancellation race")

    service = RacingService(
        store=InMemoryIngestionStore(), deps=Deps(), profiles_path=profile_path
    )
    record, _ = service.submit("showcase-default")
    profile = service._resolve_profile(record.profile)

    async def scenario():
        loop = asyncio.get_running_loop()
        unhandled: list[dict] = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        try:
            service.parent = asyncio.current_task()
            with pytest.raises(asyncio.CancelledError):
                await service._run_phase_with_lease(
                    "discover", record, profile, {}, {}, asyncio.Event()
                )
            gc.collect()
            await asyncio.sleep(0)
            assert unhandled == []
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())


def test_vector_target_fail_when_weaviate_disabled(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path, vector=[{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "fail"}])
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(available=False), lightrag=FakeLightrag(), poll_interval=0.01), pf)
    _, _, final = _run(svc)
    assert final.status == "failed"
    assert final.phase("vector_write").status == "failed"
    assert final.errors and final.errors[0]["service"] == "weaviate"


def test_vector_target_skip_when_weaviate_disabled(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path, vector=[{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "skip"}])
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(available=False), lightrag=FakeLightrag(), poll_interval=0.01), pf)
    _, _, final = _run(svc)
    assert final.status == "completed"
    assert final.phase("vector_write").status == "skipped"
    assert final.errors == []  # a skip is not a failure


def test_lightrag_skip_when_disabled(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(
        tmp_path,
        vector=[{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "skip"}],
        graph=[{"backend": "lightrag", "mode": "upload_documents", "wait_for_extraction": True, "timeout_seconds": 1, "on_unavailable": "skip"}],
    )
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(available=False), poll_interval=0.01), pf)
    _, _, final = _run(svc)
    assert final.status == "completed"
    assert final.phase("lightrag_upload").status == "skipped"
    assert final.phase("drain").status == "skipped"


def test_drain_timeout_fails(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(
        tmp_path,
        vector=[{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "skip"}],
        graph=[{"backend": "lightrag", "mode": "upload_documents", "wait_for_extraction": True, "timeout_seconds": 1, "on_unavailable": "fail"}],
    )
    lr = FakeLightrag(busy_cycles="forever")
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=lr, poll_interval=0.01), pf)
    _, _, final = _run(svc)
    assert final.status == "failed"
    assert final.phase("drain").status == "failed"
    assert "did not drain" in final.errors[0]["message"]


def test_drain_waits_until_pipeline_idle(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(
        tmp_path,
        vector=[{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "skip"}],
        graph=[{"backend": "lightrag", "mode": "upload_documents", "wait_for_extraction": True, "timeout_seconds": 5, "on_unavailable": "fail"}],
    )
    lr = FakeLightrag(busy_cycles=2)  # busy twice, then idle
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=lr, poll_interval=0.01), pf)
    _, _, final = _run(svc)
    assert final.status == "completed"
    assert final.phase("drain").status == "completed"


def test_cancel_before_run(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path)
    store = InMemoryIngestionStore()
    svc = RagIngestionService(store=store, deps=Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(), poll_interval=0.01), profiles_path=pf)
    record, _ = svc.submit("showcase-default")
    assert store.request_cancel(record.id) is True
    final = asyncio.run(svc.run(record.id))
    assert final.status == "cancelled"


def test_unknown_profile_raises(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "x"})
    pf = _profiles_file(tmp_path)
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(), poll_interval=0.01), pf)
    with pytest.raises(ProfileNotFoundError):
        svc.submit("does-not-exist")


def test_schema_drift_missing_profiles_file(tmp_path):
    # A missing/garbage profiles file yields no profiles rather than crashing.
    assert load_profiles(str(tmp_path / "nope.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("[not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_profiles(str(bad))


def test_partial_retry_after_failure_creates_fresh_run(tmp_path, monkeypatch):
    # A failed job is not a dedup candidate: re-submitting the same corpus after a
    # failure creates a new run, which succeeds once the target is available.
    _corpus(tmp_path, monkeypatch, {"a.txt": "content body"})
    pf = _profiles_file(tmp_path, vector=[{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "fail"}])
    store = InMemoryIngestionStore()
    down = Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(available=False), lightrag=FakeLightrag(), poll_interval=0.01)
    svc = RagIngestionService(store=store, deps=down, profiles_path=pf)
    rec1, created1 = svc.submit("showcase-default")
    final1 = asyncio.run(svc.run(rec1.id))
    assert created1 and final1.status == "failed"
    # Now the vector store is back; a fresh submit is NOT deduped to the failed run.
    svc.deps = Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(), poll_interval=0.01)
    rec2, created2 = svc.submit("showcase-default")
    assert created2 is True and rec2.id != rec1.id
    final2 = asyncio.run(svc.run(rec2.id))
    assert final2.status == "completed"


def test_celery_task_run_on_unknown_id_raises(tmp_path, monkeypatch):
    # The Celery worker fails loudly (KeyError) on a missing record rather than
    # silently succeeding — a worker-failure signal the operator can see.
    pf = _profiles_file(tmp_path)
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(), poll_interval=0.01), pf)
    with pytest.raises(KeyError):
        asyncio.run(svc.run("00000000-0000-0000-0000-000000000000"))


def test_unexpected_phase_error_marks_failed_not_crash(tmp_path, monkeypatch):
    # A phase raising an unexpected (non-PhaseFatal) error must be recorded as a
    # job failure, never propagate out and crash the worker.
    _corpus(tmp_path, monkeypatch, {"a.txt": "content body"})
    pf = _profiles_file(tmp_path, vector=[{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "skip"}])

    class ExplodingEmbedder:
        def available(self):
            return True
        async def embed(self, texts):
            raise RuntimeError("kaboom in embed")

    svc = _service(tmp_path, Deps(embedder=ExplodingEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(available=False), poll_interval=0.01), pf)
    _, _, final = _run(svc)
    assert final.status == "failed"
    assert final.phase("embed").status == "failed"
    assert any("kaboom" in e["message"] for e in final.errors)


def test_worker_transient_error_is_persisted_for_retry_and_reraised(
    tmp_path, monkeypatch
):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content body"})
    pf = _profiles_file(
        tmp_path,
        vector=[
            {
                "backend": "weaviate",
                "collection_prefix": "P",
                "on_unavailable": "fail",
            }
        ],
    )

    class TransientEmbedder:
        def available(self):
            return True

        async def embed(self, texts):
            raise ConnectionError("temporary LiteLLM outage")

    store = InMemoryIngestionStore()
    svc = RagIngestionService(
        store=store,
        deps=Deps(
            embedder=TransientEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(available=False),
            poll_interval=0.01,
        ),
        profiles_path=pf,
    )
    record, _ = svc.submit("showcase-default")

    with pytest.raises(ConnectionError, match="temporary LiteLLM outage"):
        asyncio.run(svc.run(record.id, retry_transient=True))

    persisted = store.get(record.id)
    assert persisted is not None
    assert persisted.status == "pending"
    assert persisted.phase("embed").status == "pending"
    assert persisted.phase("embed").note == "waiting for Celery retry"
    assert persisted.errors == []

    svc.deps.embedder = FakeEmbedder()
    completed = asyncio.run(svc.run(record.id, retry_transient=True))
    assert completed.status == "completed"
    assert completed.phase("embed").status == "completed"
    assert completed.phase("embed").note is None


def test_release_failure_cannot_mask_primary_transient_error(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content body"})
    profile_path = _profiles_file(tmp_path)

    class FailingReleaseStore(InMemoryIngestionStore):
        def release_execution(self, ingestion_id, owner):
            raise RedisConnectionError("redis unavailable during release")

    class TransientEmbedder:
        def available(self):
            return True

        async def embed(self, _texts):
            raise httpx.ReadTimeout("primary embedding timeout")

    store = FailingReleaseStore()
    service = RagIngestionService(
        store=store,
        deps=Deps(embedder=TransientEmbedder(), poll_interval=0.01),
        profiles_path=profile_path,
    )
    record, _ = service.submit("showcase-default")

    with pytest.raises(httpx.ReadTimeout, match="primary embedding timeout"):
        asyncio.run(service.run(record.id, retry_transient=True))

    persisted = store.get(record.id)
    assert persisted is not None
    assert persisted.status == "pending"


def test_worker_final_transient_attempt_records_terminal_failure(
    tmp_path, monkeypatch
):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content body"})
    pf = _profiles_file(
        tmp_path,
        vector=[
            {
                "backend": "weaviate",
                "collection_prefix": "P",
                "on_unavailable": "fail",
            }
        ],
    )

    class TransientEmbedder:
        def available(self):
            return True

        async def embed(self, _texts):
            raise ConnectionError("temporary LiteLLM outage")

    store = InMemoryIngestionStore()
    svc = RagIngestionService(
        store=store,
        deps=Deps(
            embedder=TransientEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(available=False),
        ),
        profiles_path=pf,
    )
    record, _ = svc.submit("showcase-default")

    final = asyncio.run(svc.run(record.id, retry_transient=False))

    assert final.status == "failed"
    assert final.is_dedup_candidate is False
    assert final.phase("embed").status == "failed"


def test_corpus_path_safety_rejects_escape(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("RAG_INGESTION_CORPUS_ROOT", str(root))
    reader = MountCorpusReader()
    for bad in ("../escape", "/etc/passwd", "~/secrets"):
        with pytest.raises(CorpusPathError):
            reader.discover({"source": "mount", "path": bad})


def test_corpus_symlink_escape_rejected(tmp_path, monkeypatch):
    # Regression (blocking): a symlink planted inside a consumer-controlled mount
    # corpus must NOT let discover() read a file outside the corpus root, even
    # though the top-level directory path itself is contained.
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("SUPER-SECRET-ENV", encoding="utf-8")
    link = root / "docs" / "leak"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        import pytest as _pytest
        _pytest.skip("symlinks unsupported on this platform")
    monkeypatch.setenv("RAG_INGESTION_CORPUS_ROOT", str(root))
    reader = MountCorpusReader()
    with pytest.raises(CorpusPathError):
        reader.discover({"source": "mount", "path": "docs"})


def test_mount_corpus_rejects_file_over_configured_limit(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"large.txt": "12345"})
    monkeypatch.setenv("RAG_INGESTION_MAX_FILE_BYTES", "4")
    monkeypatch.setenv("RAG_INGESTION_MAX_CORPUS_BYTES", "100")

    with pytest.raises(ValueError, match="large.txt.*4 bytes"):
        MountCorpusReader().discover({"source": "mount", "path": "docs"})


def test_mount_corpus_rejects_aggregate_over_configured_limit(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "1234", "b.txt": "5678"})
    monkeypatch.setenv("RAG_INGESTION_MAX_FILE_BYTES", "10")
    monkeypatch.setenv("RAG_INGESTION_MAX_CORPUS_BYTES", "7")

    with pytest.raises(ValueError, match="corpus.*7 bytes"):
        MountCorpusReader().discover({"source": "mount", "path": "docs"})


def test_mount_corpus_rejects_file_count_over_configured_limit(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "", "b.txt": "", "c.txt": ""})
    monkeypatch.setenv("RAG_INGESTION_MAX_FILES", "2")

    with pytest.raises(ValueError, match="more than 2 files"):
        MountCorpusReader().discover({"source": "mount", "path": "docs"})


def test_minio_corpus_rejects_oversize_metadata_before_download(monkeypatch):
    import minio

    get_calls = []

    class Object:
        object_name = "large.bin"
        size = 5

    class FakeClient:
        def list_objects(self, *args, **kwargs):
            return [Object()]

        def get_object(self, *args, **kwargs):
            get_calls.append(args)
            raise AssertionError("oversize object must not be downloaded")

    monkeypatch.setattr(minio, "Minio", lambda *args, **kwargs: FakeClient())
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("RAG_INGESTION_MAX_FILE_BYTES", "4")
    monkeypatch.setenv("RAG_INGESTION_MAX_CORPUS_BYTES", "100")

    with pytest.raises(ValueError, match="large.bin.*4 bytes"):
        MinioCorpusReader().discover(
            {"source": "minio", "bucket": "corpus", "prefix": "docs/"}
        )
    assert get_calls == []


def test_minio_corpus_uses_compiled_scoped_credentials(monkeypatch):
    import minio

    captured = {}

    class FakeClient:
        def list_objects(self, *_args, **_kwargs):
            return []

    def fake_client(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeClient()

    monkeypatch.setattr(minio, "Minio", fake_client)
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("MINIO_RAG_CORPUS_ACCESS_KEY", "scoped-access")
    monkeypatch.setenv("MINIO_RAG_CORPUS_SECRET_KEY", "scoped-secret")

    MinioCorpusReader().discover(
        {
            "source": "minio",
            "bucket": "rag-corpus",
            "prefix": "docs/",
            "access_key_var": "MINIO_RAG_CORPUS_ACCESS_KEY",
            "secret_key_var": "MINIO_RAG_CORPUS_SECRET_KEY",
        }
    )

    assert captured["kwargs"]["access_key"] == "scoped-access"
    assert captured["kwargs"]["secret_key"] == "scoped-secret"


def test_minio_corpus_bounds_stream_when_size_metadata_is_missing(monkeypatch):
    import io
    import minio

    response = io.BytesIO(b"12345")
    response.close = lambda: None
    response.release_conn = lambda: None

    class Object:
        object_name = "unknown-size.bin"
        size = None

    class FakeClient:
        def list_objects(self, *args, **kwargs):
            return [Object()]

        def get_object(self, *args, **kwargs):
            return response

    monkeypatch.setattr(minio, "Minio", lambda *args, **kwargs: FakeClient())
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("RAG_INGESTION_MAX_FILE_BYTES", "4")
    monkeypatch.setenv("RAG_INGESTION_MAX_CORPUS_BYTES", "100")

    with pytest.raises(ValueError, match="unknown-size.bin.*4 bytes"):
        MinioCorpusReader().discover(
            {"source": "minio", "bucket": "corpus", "prefix": "docs/"}
        )


def test_corpus_fingerprint_enforces_the_same_resource_limits(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"large.txt": "12345"})
    monkeypatch.setenv("RAG_INGESTION_MAX_FILE_BYTES", "4")

    with pytest.raises(ValueError, match="large.txt.*4 bytes"):
        MountCorpusReader().fingerprint({"source": "mount", "path": "docs"})


def test_embedder_disabled_is_attributed_to_embedder_not_weaviate(tmp_path, monkeypatch):
    # Regression: when the embedder is disabled but Weaviate is available, the
    # vector_write failure must name the embedder (LiteLLM), not misdiagnose Weaviate.
    _corpus(tmp_path, monkeypatch, {"a.txt": "content body here"})
    pf = _profiles_file(tmp_path, vector=[{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "fail"}])

    class DisabledEmbedder:
        def available(self):
            return False

    svc = _service(tmp_path, Deps(embedder=DisabledEmbedder(), weaviate=FakeWeaviate(available=True), lightrag=FakeLightrag(available=False), poll_interval=0.01), pf)
    _, _, final = _run(svc)
    assert final.status == "failed"
    assert final.errors[0]["service"] == "embedder"
    assert "LITELLM" in final.errors[0]["message"] or "embedder" in final.errors[0]["message"]


def test_corpus_override_only_for_mount(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "x"})
    pf = _profiles_file(tmp_path, corpus={"source": "minio", "bucket": "b", "prefix": "p/"})
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(), poll_interval=0.01), pf)
    with pytest.raises(ValueError, match="only valid for source=mount"):
        svc.submit("showcase-default", corpus_path="docs")


def test_full_record_is_json_serializable(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path)
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(), poll_interval=0.01), pf)
    _, _, final = _run(svc)
    # The status endpoint serializes this; ensure it round-trips.
    blob = json.dumps(final.to_dict())
    assert json.loads(blob)["status"] == "completed"


def test_weaviate_object_422_is_only_idempotent_when_object_exists(monkeypatch):
    request = httpx.Request("POST", "http://weaviate/v1/objects")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return httpx.Response(422, request=request, text="invalid vector")

        async def head(self, url):
            return httpx.Response(404, request=httpx.Request("HEAD", url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(httpx.HTTPStatusError, match="422"):
        asyncio.run(
            WeaviateClient("http://weaviate").write_objects(
                "Rag", [{"id": "object-1", "properties": {}, "vector": [0.1]}]
            )
        )


def test_weaviate_object_422_counts_existing_deterministic_object(monkeypatch):
    request = httpx.Request("POST", "http://weaviate/v1/objects")

    puts = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return httpx.Response(422, request=request, text="already exists")

        async def head(self, url):
            return httpx.Response(204, request=httpx.Request("HEAD", url))

        async def put(self, url, json):
            puts.append((url, json))
            return httpx.Response(200, request=httpx.Request("PUT", url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())

    written = asyncio.run(
        WeaviateClient("http://weaviate").write_objects(
            "Rag", [{"id": "object-1", "properties": {}, "vector": [0.1]}]
        )
    )

    assert written == 1
    assert puts[0][0] == "http://weaviate/v1/objects/Rag/object-1"
    assert puts[0][1]["vector"] == [0.1]


def test_weaviate_existing_object_does_not_hide_invalid_replacement(monkeypatch):
    post_request = httpx.Request("POST", "http://weaviate/v1/objects")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return httpx.Response(422, request=post_request, text="duplicate id")

        async def head(self, url):
            return httpx.Response(204, request=httpx.Request("HEAD", url))

        async def put(self, url, json):
            return httpx.Response(
                422, request=httpx.Request("PUT", url), text="invalid vector"
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(httpx.HTTPStatusError, match="422"):
        asyncio.run(
            WeaviateClient("http://weaviate").write_objects(
                "Rag", [{"id": "object-1", "properties": {}, "vector": [0.1]}]
            )
        )


def test_weaviate_schema_422_requires_the_class_to_exist(monkeypatch):
    post_request = httpx.Request("POST", "http://weaviate/v1/schema")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            return httpx.Response(404, request=httpx.Request("GET", url))

        async def post(self, *_args, **_kwargs):
            return httpx.Response(422, request=post_request, text="invalid schema")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(httpx.HTTPStatusError, match="422"):
        asyncio.run(WeaviateClient("http://weaviate").ensure_class("Rag"))


def test_weaviate_reconciliation_deletes_stale_profile_objects(monkeypatch):
    deleted = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "data": {
                        "Get": {
                            "Rag": [
                                {"profile": "showcase-default",
                                 "_additional": {"id": "keep"}},
                                {"profile": "showcase-default",
                                 "_additional": {"id": "stale"}},
                                # Another profile's object is never stale here.
                                {"profile": "other",
                                 "_additional": {"id": "foreign"}},
                            ]
                        }
                    }
                },
            )

        async def delete(self, url):
            deleted.append(url)
            return httpx.Response(204, request=httpx.Request("DELETE", url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())

    count = asyncio.run(
        WeaviateClient("http://weaviate").reconcile_objects(
            "Rag", "showcase-default", ["keep"]
        )
    )

    assert count == 1
    assert deleted == ["http://weaviate/v1/objects/Rag/stale"]


def test_weaviate_reconciliation_pages_by_cursor_past_the_offset_cap(monkeypatch):
    """Offset paging fails at QUERY_MAXIMUM_RESULTS (10k); the lookup must
    walk the class with `after` cursors instead."""
    queries = []
    pages = [
        [{"profile": "p", "_additional": {"id": f"id-{n:04d}"}} for n in range(1000)],
        [{"profile": "p", "_additional": {"id": "id-last"}}],
    ]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            queries.append(json["query"])
            page = pages[len(queries) - 1]
            return httpx.Response(
                200, request=httpx.Request("POST", url),
                json={"data": {"Get": {"Rag": page}}},
            )

        async def delete(self, url):
            return httpx.Response(204, request=httpx.Request("DELETE", url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())
    keep = [f"id-{n:04d}" for n in range(1000)]
    count = asyncio.run(
        WeaviateClient("http://weaviate").reconcile_objects("Rag", "p", keep)
    )

    assert count == 1
    assert len(queries) == 2
    assert "offset" not in queries[0] and "after" not in queries[0]
    assert 'after: "id-0999"' in queries[1]


# ── #673: drain resilience to transient pipeline_status failures ────────────
def _drain_graph(timeout_seconds):
    return [{"backend": "lightrag", "mode": "upload_documents",
             "wait_for_extraction": True, "timeout_seconds": timeout_seconds,
             "on_unavailable": "fail"}]


def _skip_vector():
    return [{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "skip"}]


class _TransientThenIdleLightrag(FakeLightrag):
    """Raises a transient error for the first N polls, then reports idle."""

    def __init__(self, *, transient_polls, exc=None, available=True):
        super().__init__(available=available)
        self._transient_polls = transient_polls
        self._exc = exc if exc is not None else httpx.ReadTimeout("")
        self.poll_calls = 0

    async def pipeline_busy(self):
        self.poll_calls += 1
        if self._transient_polls > 0:
            self._transient_polls -= 1
            raise self._exc
        return False


class _AlwaysTimeoutLightrag(FakeLightrag):
    def __init__(self, *, exc=None, available=True):
        super().__init__(available=available)
        self._exc = exc if exc is not None else httpx.ReadTimeout("")
        self.poll_calls = 0

    async def pipeline_busy(self):
        self.poll_calls += 1
        raise self._exc


class _HttpErrorLightrag(FakeLightrag):
    def __init__(self, *, status=401, available=True):
        super().__init__(available=available)
        self._status = status
        self.poll_calls = 0

    async def pipeline_busy(self):
        self.poll_calls += 1
        request = httpx.Request("GET", "http://lightrag:9621/documents/pipeline_status")
        response = httpx.Response(self._status, request=request, text="unauthorized")
        raise httpx.HTTPStatusError("auth failed", request=request, response=response)


class _CancelDuringDrainLightrag(FakeLightrag):
    """Requests cancellation (via the store) on the first poll, then keeps
    timing out, so the drain loop must observe the cancel between retries."""

    def __init__(self, *, store, available=True):
        super().__init__(available=available)
        self._store = store
        self.record_id = None
        self.poll_calls = 0

    async def pipeline_busy(self):
        self.poll_calls += 1
        if self.record_id is not None:
            self._store.request_cancel(self.record_id)
        raise httpx.ReadTimeout("")


def test_drain_retries_transient_timeout_then_completes(tmp_path, monkeypatch):
    """AC: a transient ReadTimeout from pipeline_status is retried and a later
    idle poll completes the drain; the retries are recorded as evidence."""
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path, vector=_skip_vector(), graph=_drain_graph(30))
    lr = _TransientThenIdleLightrag(transient_polls=3)
    svc = _service(
        tmp_path,
        Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=lr,
             poll_interval=0.01, drain_backoff_base=0.0, drain_backoff_max=0.0),
        pf,
    )
    _, _, final = _run(svc)
    assert final.status == "completed"
    drain = final.phase("drain")
    assert drain.status == "completed"
    assert drain.counts["transient_retries"] == 3
    assert drain.counts["status_polls"] == 4  # 3 timeouts + 1 idle
    assert "transient" in (drain.note or "")


def test_drain_deadline_exhausted_names_exception_class(tmp_path, monkeypatch):
    """AC: repeated transient failures stop at the profile deadline, and the
    terminal error names the exception class even though str(ReadTimeout) is
    empty."""
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path, vector=_skip_vector(), graph=_drain_graph(0))
    lr = _AlwaysTimeoutLightrag()
    svc = _service(
        tmp_path,
        Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=lr,
             poll_interval=0.01, drain_backoff_base=0.0, drain_backoff_max=0.0),
        pf,
    )
    _, _, final = _run(svc)
    assert final.status == "failed"
    assert final.phase("drain").status == "failed"
    message = final.errors[0]["message"]
    assert "did not drain" in message
    assert "ReadTimeout" in message  # empty str() still diagnosable


def test_drain_non_retryable_http_error_fails_immediately(tmp_path, monkeypatch):
    """AC: a 401 (or other deterministic 4xx) from pipeline_status is NOT
    retried — it fails immediately with bounded, actionable detail."""
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path, vector=_skip_vector(), graph=_drain_graph(30))
    lr = _HttpErrorLightrag(status=401)
    svc = _service(
        tmp_path,
        Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=lr,
             poll_interval=0.01, drain_backoff_base=0.0, drain_backoff_max=0.0),
        pf,
    )
    _, _, final = _run(svc)
    assert final.status == "failed"
    assert final.phase("drain").status == "failed"
    assert lr.poll_calls == 1  # not retried
    error = final.phase("drain").error
    assert error["http_status"] == 401
    assert "pipeline_status failed" in error["message"]
    assert "HTTPStatusError" in error["message"]


def test_drain_cancellation_is_responsive_during_retry(tmp_path, monkeypatch):
    """AC: cancellation stays responsive between transient-failure retries."""
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    pf = _profiles_file(tmp_path, vector=_skip_vector(), graph=_drain_graph(30))
    store = InMemoryIngestionStore()
    lr = _CancelDuringDrainLightrag(store=store)
    svc = RagIngestionService(
        store=store,
        deps=Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=lr,
                  poll_interval=0.01, drain_backoff_base=0.0, drain_backoff_max=0.0),
        profiles_path=pf,
    )
    record, _ = svc.submit("showcase-default")
    lr.record_id = record.id
    final = asyncio.run(svc.run(record.id))
    assert final.status == "cancelled"
    assert lr.poll_calls >= 1


def test_describe_exc_renders_empty_message_as_class():
    from rag_ingestion.service import _describe_exc

    assert _describe_exc(httpx.ReadTimeout("")) == "ReadTimeout"
    assert _describe_exc(ValueError("boom")) == "ValueError: boom"


def test_pipeline_status_timeout_is_configurable(monkeypatch):
    from rag_ingestion.clients import (
        LightRagClient,
        _resolve_pipeline_status_timeout,
    )

    monkeypatch.delenv("LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS", raising=False)
    assert LightRagClient(endpoint="http://x")._pipeline_status_timeout == 30.0

    # Explicit arg wins over env.
    monkeypatch.setenv("LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS", "50")
    assert LightRagClient(endpoint="http://x", pipeline_status_timeout=3.0)._pipeline_status_timeout == 3.0
    assert LightRagClient(endpoint="http://x")._pipeline_status_timeout == 50.0

    # Blank, malformed, non-finite, non-positive, and over-limit values fall
    # back to the finite default.
    for bad in ("", "not-a-number", "nan", "inf", "-5", "0", "3600.1"):
        monkeypatch.setenv("LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS", bad)
        assert _resolve_pipeline_status_timeout(None) == 30.0

    assert _resolve_pipeline_status_timeout(3600.0) == 3600.0
    monkeypatch.setenv("LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS", "50")
    for bad_explicit in (float("nan"), float("inf"), -1.0, 0.0, 3600.1):
        assert _resolve_pipeline_status_timeout(bad_explicit) == 50.0

    monkeypatch.setenv("LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS", "invalid")
    assert _resolve_pipeline_status_timeout(float("inf")) == 30.0


def test_pipeline_status_timeout_safely_coerces_explicit_values(monkeypatch):
    from rag_ingestion.clients import _resolve_pipeline_status_timeout

    class OverflowingFloat:
        def __float__(self):
            raise OverflowError("synthetic overflow")

    class HostileFloat:
        def __float__(self):
            raise RuntimeError("synthetic hostile coercion")

    class ProcessControlFloat:
        def __init__(self, error):
            self.error = error

        def __float__(self):
            raise self.error

    monkeypatch.setenv("LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS", "50")

    # Preserve the existing numeric-string compatibility deliberately, while
    # ordinary ints/floats remain the primary programmatic API.
    for explicit, expected in (
        (1, 1.0),
        (3.5, 3.5),
        ("45", 45.0),
        (" 45 ", 45.0),
    ):
        assert _resolve_pipeline_status_timeout(explicit) == expected

    # bool is not a duration even though Python's float(True) is 1.0. Every
    # other malformed/coercion-failing value also falls through to valid env.
    invalid_explicit_values = (
        True,
        False,
        "garbage",
        "   ",
        object(),
        OverflowingFloat(),
        HostileFloat(),
        float("nan"),
        float("inf"),
        -1,
        0,
        3600.1,
    )
    for invalid in invalid_explicit_values:
        assert _resolve_pipeline_status_timeout(invalid) == 50.0

    monkeypatch.setenv("LIGHTRAG_PIPELINE_STATUS_TIMEOUT_SECONDS", "invalid")
    for invalid in invalid_explicit_values:
        assert _resolve_pipeline_status_timeout(invalid) == 30.0

    # Catch ordinary coercion failures only. Process-control exceptions must
    # remain visible to the caller and must never be converted into defaults.
    for expected in (KeyboardInterrupt, SystemExit):
        with pytest.raises(expected):
            _resolve_pipeline_status_timeout(ProcessControlFloat(expected()))


def test_ingestion_ttl_uses_safe_default_for_invalid_values(monkeypatch):
    from rag_ingestion.store import _ttl_seconds

    monkeypatch.delenv("RAG_INGESTION_TTL_SECONDS", raising=False)
    assert _ttl_seconds() == 7 * 24 * 3600

    monkeypatch.setenv("RAG_INGESTION_TTL_SECONDS", "3600")
    assert _ttl_seconds() == 3600

    monkeypatch.setenv("RAG_INGESTION_TTL_SECONDS", "31536000")
    assert _ttl_seconds() == 31536000

    for bad in (
        "",
        "not-an-integer",
        "0",
        "-1",
        "59",
        "31536001",
        "99999999999999999999999999999999999999999999999999",
    ):
        monkeypatch.setenv("RAG_INGESTION_TTL_SECONDS", bad)
        assert _ttl_seconds() == 7 * 24 * 3600


def test_invalid_ingestion_ttl_cannot_crash_module_import():
    env = os.environ.copy()
    env["RAG_INGESTION_TTL_SECONDS"] = "not-an-integer"
    result = subprocess.run(
        [sys.executable, "-c", "import rag_ingestion.store; print('imported')"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "imported"


def _poison_chunker(monkeypatch):
    """Make chunking fail for any document containing POISON."""
    import chunking_service

    real = chunking_service.chunk_text

    def chunk_text(request, **kwargs):
        if "POISON" in request.text:
            raise chunking_service.ChunkingError("poisoned document")
        return real(request, **kwargs)

    monkeypatch.setattr(chunking_service, "chunk_text", chunk_text)


def test_chunk_phase_isolates_failing_document(tmp_path, monkeypatch):
    # A single document the chunker rejects must not abort the whole job — it
    # is recorded as a chunk-phase error and other documents still chunk
    # (matching _phase_parse's per-file isolation).
    _poison_chunker(monkeypatch)
    _corpus(tmp_path, monkeypatch, {"big.txt": "POISON", "small.txt": "the quick brown fox"})
    pf = _profiles_file(tmp_path)
    svc = _service(
        tmp_path,
        Deps(
            embedder=FakeEmbedder(),
            weaviate=FakeWeaviate(),
            lightrag=FakeLightrag(),
            poll_interval=0.01,
        ),
        pf,
    )

    _, _, final = _run(svc)

    assert final.status == "completed"  # NOT failed by the one oversize doc

    def _field(err, name):
        return err[name] if isinstance(err, dict) else getattr(err, name)

    chunk_errors = [e for e in final.errors if _field(e, "phase") == "chunk"]
    assert any(
        str(_field(e, "file") or "").endswith("big.txt") for e in chunk_errors
    ), final.errors
    assert final.counts.get("chunks", 0) > 0  # small.txt still chunked


def test_corpus_document_over_the_api_text_cap_is_chunked(tmp_path, monkeypatch):
    # The HTTP /chunk body cap (1M chars) silently dropped larger corpus files.
    big = "x " * 600_000
    _corpus(tmp_path, monkeypatch, {"big.txt": big})
    svc = _service(
        tmp_path,
        Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(),
             lightrag=FakeLightrag(), poll_interval=0.01),
        _profiles_file(tmp_path),
    )

    _, _, final = _run(svc)

    assert final.status == "completed"
    assert not [e for e in final.errors if "chunk" in str(e)], final.errors
    assert final.counts.get("chunks", 0) > 1


def test_weaviate_class_name_sanitizes_profile_name():
    import re as _re

    from rag_ingestion.service import weaviate_class_name

    # A hyphenated profile name (the canonical `showcase-default`) must yield a
    # VALID Weaviate class name (^[A-Z][_0-9A-Za-z]*$), not `..._showcase-default`
    # which 422s on ensure_class + 404s on the case-sensitive reconcile query.
    name = weaviate_class_name("RagShowcase", "showcase-default")
    assert name == "RagShowcase_showcase_default"
    assert _re.match(r"^[A-Z][_0-9A-Za-z]*$", name)
    # dots are sanitized too
    assert weaviate_class_name("Docs", "v1.2-beta") == "Docs_v1_2_beta"


# ── a failed run must never be mistaken for an empty corpus ──────────


def test_a_run_where_every_document_fails_does_not_wipe_the_corpus(tmp_path, monkeypatch):
    """`reconcile_objects(cls, profile, [])` deletes EVERY object for a profile.

    Reaching that branch after files were discovered wiped the entire previous
    generation — and the job still reported `status: "completed"`, so a
    consumer polling for completion could not tell it from success.
    Reproducible three ways: every file 5xx from the parser, an
    `overlap >= chunk_size` profile, and documents that parse to whitespace
    (that last one records ZERO errors).
    """
    root = _corpus(tmp_path, monkeypatch, {"a.txt": "content", "b.txt": "more"})
    profile_path = _profiles_file(tmp_path)
    weaviate = FakeWeaviate()
    service = _service(
        tmp_path,
        Deps(
            embedder=FakeEmbedder(),
            weaviate=weaviate,
            lightrag=FakeLightrag(available=False),
            poll_interval=0.01,
        ),
        profile_path,
    )
    first, _ = service.submit("showcase-default")
    assert asyncio.run(service.run(first.id)).status == "completed"
    seeded = set(weaviate.object_ids)
    assert seeded, "precondition: a previous generation exists"

    # Every document now parses to whitespace — files ARE discovered, but no
    # chunk survives.
    for name in ("a.txt", "b.txt"):
        (root / "docs" / name).write_text("   \n  ", encoding="utf-8")

    second, _ = service.submit("showcase-default")
    final = asyncio.run(service.run(second.id))

    assert final.counts.get("files_discovered") == 2
    assert final.counts.get("chunks", 0) == 0
    # ...and it must REPORT the failure. Terminal status is
    # `FAILED if record.errors and _has_fatal_phase(...)`, so setting only the
    # phase status short-circuited to COMPLETED — the run said success while
    # having produced nothing. This assertion was missing from the original.
    assert final.status == "failed", f"a total-failure run reported {final.status!r}"
    assert final.errors, "the failure was not recorded"
    # the previous generation survives, and nothing was reconciled away
    assert weaviate.object_ids == seeded, "the corpus was deleted"
    assert weaviate.reconciled[-1] != (
        "RagShowcase_showcase_default", "showcase-default", [],
    ), "a total-failure run reconciled against an empty desired set"


def test_a_failed_document_keeps_its_previously_ingested_vectors(tmp_path, monkeypatch):
    """The reconcile treats "not in this run's output" as "stale".

    That conflates it with "this run could not produce it", so a document that
    FAILED had its previously ingested vectors deleted while the job reported
    completed — a transient parser blip destroying good data.

    Note the distinction this test is careful about: a file the operator
    EMPTIED should lose its vectors (that is the reconcile doing its job).
    Only a recorded failure is protected, which is why the trigger here is an
    oversize document that lands in `errors[]`.
    """
    files = {"a.txt": "alpha content here", "big.txt": "the quick brown fox"}
    root = _corpus(tmp_path, monkeypatch, files)
    profile_path = _profiles_file(tmp_path)
    weaviate = FakeWeaviate()
    service = _service(
        tmp_path,
        Deps(
            embedder=FakeEmbedder(),
            weaviate=weaviate,
            lightrag=FakeLightrag(available=False),
            poll_interval=0.01,
        ),
        profile_path,
    )
    first, _ = service.submit("showcase-default")
    assert asyncio.run(service.run(first.id)).status == "completed"
    both = set(weaviate.object_ids)
    assert len(both) >= 2, "precondition: both files ingested"

    # big.txt now fails to chunk -> a recorded chunk-phase error, while a.txt
    # is unchanged.
    _poison_chunker(monkeypatch)
    (root / "docs" / "big.txt").write_text("POISON", encoding="utf-8")
    second, _ = service.submit("showcase-default")
    final = asyncio.run(service.run(second.id))

    assert final.errors, "precondition: the oversize document was recorded as an error"
    assert final.counts.get("chunks", 0) > 0, "a.txt should still ingest"
    assert both <= weaviate.object_ids, (
        "the failed document's previously ingested vectors were deleted"
    )


def test_a_genuinely_empty_corpus_still_reconciles(tmp_path, monkeypatch):
    """The guard must not break the legitimate empty-corpus wipe."""
    root = _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)
    weaviate = FakeWeaviate()
    service = _service(
        tmp_path,
        Deps(
            embedder=FakeEmbedder(),
            weaviate=weaviate,
            lightrag=FakeLightrag(available=False),
            poll_interval=0.01,
        ),
        profile_path,
    )
    first, _ = service.submit("showcase-default")
    assert asyncio.run(service.run(first.id)).status == "completed"

    (root / "docs" / "a.txt").unlink()
    second, _ = service.submit("showcase-default")
    final = asyncio.run(service.run(second.id))

    assert final.status == "completed", final.errors
    assert final.counts.get("files_discovered", 0) == 0
    assert weaviate.object_ids == set()


def test_a_permanently_failing_document_does_not_block_stale_cleanup(tmp_path, monkeypatch):
    """Skipping the whole reconcile on any error was the wrong correction.

    One permanently-broken file — a corrupt PDF, an oversize document — then
    disabled stale-object cleanup FOREVER, so vectors of documents the operator
    DELETED stayed searchable indefinitely while the job reported completed.
    Reconcile is per SOURCE: preserve exactly what failed, clean up the rest.
    """
    root = _corpus(tmp_path, monkeypatch, {
        "good.txt": "alpha content", "bad.txt": "beta content", "gone.txt": "gamma content",
    })
    profile_path = _profiles_file(tmp_path)
    weaviate = FakeWeaviate()
    service = _service(
        tmp_path,
        Deps(embedder=FakeEmbedder(), weaviate=weaviate,
             lightrag=FakeLightrag(available=False), poll_interval=0.01),
        profile_path,
    )
    first, _ = service.submit("showcase-default")
    assert asyncio.run(service.run(first.id)).status == "completed"

    _poison_chunker(monkeypatch)
    (root / "docs" / "bad.txt").write_text("POISON", encoding="utf-8")  # always fails
    (root / "docs" / "gone.txt").unlink()                                     # operator deleted

    for _ in range(3):
        nxt, _ = service.submit("showcase-default")
        asyncio.run(service.run(nxt.id))

    sources = {weaviate.source_of.get(oid) for oid in weaviate.object_ids}
    assert any("bad.txt" in (s or "") for s in sources), (
        "the permanently-failing document's vectors were deleted"
    )
    assert not any("gone.txt" in (s or "") for s in sources), (
        "a deleted document's vectors leaked because cleanup was disabled"
    )
    assert weaviate.preserved[-1] and "bad.txt" in weaviate.preserved[-1][0]


def test_a_retry_does_not_inherit_the_previous_attempt_s_failures(tmp_path, monkeypatch):
    """`record.errors` is reloaded with the record and SPANS attempts.

    Deriving the preserved set from it meant a clean Celery retry still skipped
    cleanup, reporting a note claiming documents had failed when none had this
    run. `state["failed_sources"]` is per-attempt by construction.

    The previous version of this test did ONE clean run — with no prior attempt
    there is nothing to inherit, so both implementations produced
    `preserved == []` and reverting the fix left the file green. This drives
    the real retry path: attempt 1 records a per-document failure and then dies
    on a transient, attempt 2 sees a healthy corpus.
    """
    root = _corpus(tmp_path, monkeypatch, {"good.txt": "alpha content", "flaky.txt": "beta content"})
    profile_path = _profiles_file(tmp_path)
    weaviate = FakeWeaviate()
    service = _service(
        tmp_path,
        Deps(embedder=FakeEmbedder(), weaviate=weaviate,
             lightrag=FakeLightrag(available=False), poll_interval=0.01),
        profile_path,
    )

    # Attempt 1: flaky.txt fails to chunk (lands in errors[]), then the run
    # dies on a TRANSIENT so the SAME record is left retryable — this is the
    # Celery retry path, not a fresh submission. A new `submit()` would create
    # a new record with an empty errors[], which is why the earlier version of
    # this test could not tell the two implementations apart.
    _poison_chunker(monkeypatch)
    (root / "docs" / "flaky.txt").write_text("POISON", encoding="utf-8")
    record, _ = service.submit("showcase-default")

    boom = {"raise": True}
    real_reconcile = weaviate.reconcile_objects

    async def flaky_reconcile(*args, **kwargs):
        if boom["raise"]:
            boom["raise"] = False
            raise ConnectionError("transient upstream blip")
        return await real_reconcile(*args, **kwargs)

    weaviate.reconcile_objects = flaky_reconcile
    with pytest.raises(ConnectionError):
        asyncio.run(service.run(record.id, retry_transient=True))

    reloaded = service.store.get(record.id)
    assert reloaded.errors, "precondition: attempt 1's document failure persisted"

    # Attempt 2 of the SAME record: the corpus is healthy again. `record.errors`
    # still carries attempt 1's entry; `failed_sources` must not.
    (root / "docs" / "flaky.txt").write_text("beta content restored", encoding="utf-8")
    asyncio.run(service.run(record.id, retry_transient=True))

    assert weaviate.preserved[-1] == [], (
        f"a clean retry inherited the previous attempt's failures: "
        f"{weaviate.preserved[-1]}"
    )


@pytest.mark.parametrize("content,preserve", [
    (b"beta content", True),    # real bytes that parsed to whitespace = parser FAULT
    (b"", False),               # the operator emptied it
    (b"   \n \t ", False),      # ...likewise
    (None, False),
])
def test_an_emptied_file_is_distinguished_from_a_parser_fault(content, preserve):
    """Both reach the chunk phase with no text, and they want OPPOSITE outcomes.

    An emptied file must LOSE its vectors — that is the reconcile doing its job,
    and the contract stated in `test_a_failed_document_keeps_its_previously_
    ingested_vectors`. A real document a parser turned into whitespace must KEEP
    them, because deleting on a parser blip is the data loss the preserve-set
    exists to prevent.

    Preserving unconditionally meant an emptied file's stale content stayed
    searchable forever, with the run reporting completed and zero errors.
    """
    import types

    from rag_ingestion.service import RagIngestionService

    state = {"files": [types.SimpleNamespace(name="a.txt", content=content)]}
    assert RagIngestionService._source_had_content(state, "a.txt") is preserve


def test_an_emptied_file_loses_its_vectors(tmp_path, monkeypatch):
    """End-to-end for the operator-emptied half."""
    root = _corpus(tmp_path, monkeypatch, {"keep.txt": "alpha content", "subject.txt": "beta content"})
    profile_path = _profiles_file(tmp_path)
    weaviate = FakeWeaviate()
    service = _service(
        tmp_path,
        Deps(embedder=FakeEmbedder(), weaviate=weaviate,
             lightrag=FakeLightrag(available=False), poll_interval=0.01),
        profile_path,
    )
    first, _ = service.submit("showcase-default")
    assert asyncio.run(service.run(first.id)).status == "completed"
    assert any("subject" in (weaviate.source_of.get(o) or "") for o in weaviate.object_ids)

    (root / "docs" / "subject.txt").write_text("   \n ", encoding="utf-8")
    for _ in range(3):
        nxt, _ = service.submit("showcase-default")
        asyncio.run(service.run(nxt.id))

    sources = {weaviate.source_of.get(o) for o in weaviate.object_ids}
    assert not any("subject" in (s or "") for s in sources), (
        "an emptied file's stale vectors survived — they can never be cleaned up"
    )
    assert any("keep" in (s or "") for s in sources)


def test_embedder_batches_requests_and_keeps_input_order(monkeypatch):
    from rag_ingestion.clients import Embedder

    posted = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, headers, json):
            batch = json["input"]
            posted.append(len(batch))
            rows = [
                {"index": i, "embedding": [float(text.split("-")[1])]}
                for i, text in enumerate(batch)
            ]
            return httpx.Response(
                200, request=httpx.Request("POST", url),
                json={"data": list(reversed(rows))},  # any order on the wire
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())
    texts = [f"t-{n}" for n in range(300)]
    vectors = asyncio.run(Embedder("http://litellm", model="m").embed(texts))

    assert posted == [128, 128, 44]
    assert vectors == [[float(n)] for n in range(300)]


def test_embedder_rejects_a_short_response(monkeypatch):
    from rag_ingestion.clients import Embedder

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, headers, json):
            return httpx.Response(
                200, request=httpx.Request("POST", url),
                json={"data": [{"index": 0, "embedding": [0.0]}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())
    with pytest.raises(RuntimeError, match="1 vectors for 2 inputs"):
        asyncio.run(Embedder("http://litellm", model="m").embed(["a", "b"]))


def test_plain_text_never_indexes_binary_bytes_after_a_parser_failure():
    """plain_text closes every parser order; a failed Docling parse of a PDF
    used to index the PDF's raw bytes as text and report success."""
    from rag_ingestion.clients import ParserError

    with pytest.raises(ParserError, match="not text"):
        asyncio.run(
            ParserAdapter(RaisingExtractor()).parse(
                CorpusFile("report.pdf", b"%PDF-1.7\x00\x01\x02binary", "application/pdf"),
                ["docling", "plain_text"],
            )
        )
    png_like = b"\x89PNG\r\n\x1a\n" + bytes(range(1, 32)) * 20
    with pytest.raises(ParserError, match="not text"):
        asyncio.run(ParserAdapter().parse(CorpusFile("x.png", png_like, "image/png"), ["plain_text"]))

    def _parse(raw: bytes) -> str:
        return asyncio.run(
            ParserAdapter().parse(CorpusFile("ok.txt", raw, "text/plain"), ["plain_text"])
        ).text

    # Text corpora that ingested before the binary check still do.
    assert _parse("plain café".encode()) == "plain café"
    assert _parse("“quoted” café".encode("cp1252")) == "“quoted” café"
    assert _parse("helloé".encode("utf-16")) == "helloé"
    assert _parse(b"\xef\xbb\xbfbom text") == "bom text"


def test_missing_corpus_path_is_an_error_not_an_empty_corpus(tmp_path, monkeypatch):
    """An empty discovery reconciles away every vector of the profile."""
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    with pytest.raises(CorpusPathError, match="does not exist"):
        MountCorpusReader().discover({"source": "mount", "path": "docs-typo"})


def test_interrupted_run_is_recorded_failed_not_left_running(tmp_path, monkeypatch):
    """Celery's soft time limit cancels the coroutine via asyncio.run; a job
    left "running" deduplicated every resubmit until its TTL expired."""
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    profile_path = _profiles_file(tmp_path)

    class BlockingService(RagIngestionService):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.phase_started = asyncio.Event()

        async def _run_phase(self, *args, **kwargs):
            self.phase_started.set()
            await asyncio.Event().wait()

    store = InMemoryIngestionStore()
    service = BlockingService(store=store, deps=Deps(), profiles_path=profile_path)
    record, _ = service.submit("showcase-default")

    async def scenario():
        running = asyncio.create_task(service.run(record.id))
        await service.phase_started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(scenario())

    final = store.get(record.id)
    assert final.status == "failed"
    assert any("interrupted" in error["message"] for error in final.errors)


# --- Redis failures end the job instead of retrying forever (#1354) ----------



def test_rag_redis_error_reply_fails_without_retry(monkeypatch):
    """#1354: a Redis error reply (WRONGTYPE) repeats on every attempt."""
    import celery_tasks
    import rag_ingestion
    from redis.exceptions import ResponseError

    def fail_ingestion(*_args, **_kwargs):
        raise ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")

    monkeypatch.setattr(rag_ingestion, "run_rag_ingestion", fail_ingestion)
    monkeypatch.setattr(
        celery_tasks.rag_ingestion_task, "retry",
        lambda **_kw: pytest.fail("a Redis error reply must not be retried"),
    )

    with pytest.raises(ResponseError):
        celery_tasks.rag_ingestion_task.run("ingestion-1")


def test_transient_redis_replies_are_still_retried():
    """#1354: replies Redis sends while it recovers are not permanent."""
    from redis.exceptions import OutOfMemoryError, ReadOnlyError, ResponseError

    from rag_ingestion import is_permanent_redis_reply

    assert is_permanent_redis_reply(ResponseError("WRONGTYPE wrong kind of value"))
    assert not is_permanent_redis_reply(ReadOnlyError("READONLY replica"))
    assert not is_permanent_redis_reply(OutOfMemoryError("OOM command not allowed"))
    assert not is_permanent_redis_reply(ConnectionError("down"))


def test_rag_redis_outage_retries_stop_at_the_cap(monkeypatch):
    import celery_tasks
    import rag_ingestion

    def fail_ingestion(*_args, **_kwargs):
        raise RedisConnectionError("Redis still down")

    monkeypatch.setattr(rag_ingestion, "run_rag_ingestion", fail_ingestion)
    monkeypatch.setattr(
        celery_tasks.rag_ingestion_task, "retry",
        lambda **_kw: pytest.fail("retry past the cap"),
    )

    with pytest.raises(RedisConnectionError):
        celery_tasks.rag_ingestion_task.run(
            "ingestion-1",
            retry_state={
                "phase_attempt": 0,
                "infrastructure_attempt": celery_tasks._RAG_INFRASTRUCTURE_RETRY_LIMIT,
            },
        )


@pytest.mark.parametrize(
    ("failure", "retry_transient"),
    [
        (ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value"), True),
        (RedisConnectionError("Redis still down"), False),
    ],
)
def test_unretryable_redis_failure_leaves_a_failed_record_not_a_pending_one(
    tmp_path, monkeypatch, failure, retry_transient
):
    """A permanent reply, or any failure on the final attempt
    (retry_transient=False), must end with a terminal record: a record left
    "waiting for Celery retry" answers every resubmit as a dead duplicate."""
    _corpus(tmp_path, monkeypatch, {"a.txt": "content body"})
    pf = _profiles_file(
        tmp_path,
        vector=[{"backend": "weaviate", "collection_prefix": "P", "on_unavailable": "fail"}],
    )

    class FailingEmbedder:
        def available(self):
            return True

        async def embed(self, texts):
            raise failure

    store = InMemoryIngestionStore()
    svc = RagIngestionService(
        store=store,
        deps=Deps(embedder=FailingEmbedder(), weaviate=FakeWeaviate(),
                  lightrag=FakeLightrag(available=False), poll_interval=0.01),
        profiles_path=pf,
    )
    record, _ = svc.submit("showcase-default")

    result = asyncio.run(svc.run(record.id, retry_transient=retry_transient))

    assert result.status == "failed"
    assert store.get(record.id).status == "failed"


# --- rag_ingestion task limits (#1352) ---------------------------------------


def test_rag_ingestion_task_has_its_own_limits_and_others_keep_the_global_ones():
    """Import-time config under the default environment (no reload: other
    modules hold the configured app)."""
    import celery_app
    import celery_tasks

    assert (celery_tasks.rag_ingestion_task.soft_time_limit,
            celery_tasks.rag_ingestion_task.time_limit) == (3840, 3900)
    assert celery_tasks.memory_consolidate_task.soft_time_limit is None
    conf = celery_app.celery_app.conf
    assert (conf.task_soft_time_limit, conf.task_time_limit) == (840, 900)
    # Raised past the ingestion hard limit so a running job is never re-delivered.
    assert conf.broker_transport_options["visibility_timeout"] == 4200


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, {"soft": 3840, "hard": 3900}),
        # Global limits an operator raised for large corpora carry over.
        ({"CELERY_TASK_SOFT_TIME_LIMIT_SECONDS": "7140", "CELERY_TASK_TIME_LIMIT_SECONDS": "7200"},
         {"soft": 7140, "hard": 7200}),
        ({"RAG_INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS": "100", "RAG_INGESTION_TASK_TIME_LIMIT_SECONDS": "200"},
         {"soft": 100, "hard": 200}),
    ],
)
def test_rag_ingestion_limits_default_to_the_larger_of_floor_and_global(monkeypatch, env, expected):
    import celery_app

    for name in ("RAG_INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS", "RAG_INGESTION_TASK_TIME_LIMIT_SECONDS",
                 "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", "CELERY_TASK_TIME_LIMIT_SECONDS"):
        if name in env:
            monkeypatch.setenv(name, env[name])
        elif name.startswith("RAG_"):
            monkeypatch.setenv(name, "")  # compose passes the empty default through
        else:
            monkeypatch.delenv(name, raising=False)
    assert celery_app.load_rag_ingestion_limits() == expected


def test_rag_ingestion_limits_must_be_ordered(monkeypatch):
    import celery_app

    monkeypatch.setenv("RAG_INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS", "900")
    monkeypatch.setenv("RAG_INGESTION_TASK_TIME_LIMIT_SECONDS", "900")
    with pytest.raises(ValueError, match="must be less than"):
        celery_app.load_rag_ingestion_limits()
    assert celery_app.effective_visibility_timeout(9000, 3900) == 9000


def test_total_graph_wait_sums_only_draining_targets(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "x"})
    graph = [
        {"backend": "lightrag", "mode": "upload_documents", "wait_for_extraction": True,
         "timeout_seconds": 2000, "on_unavailable": "skip"},
        {"backend": "lightrag", "mode": "upload_documents", "wait_for_extraction": True,
         "timeout_seconds": 1900, "on_unavailable": "skip"},
        {"backend": "lightrag", "mode": "upload_documents", "wait_for_extraction": False,
         "timeout_seconds": 9999, "on_unavailable": "skip"},
    ]
    svc = RagIngestionService(store=InMemoryIngestionStore(), deps=Deps(),
                              profiles_path=_profiles_file(tmp_path, graph=graph))
    assert svc.total_graph_wait("showcase-default") == 3900
    none = RagIngestionService(store=InMemoryIngestionStore(), deps=Deps(),
                               profiles_path=_profiles_file(tmp_path, graph=[]))
    assert none.total_graph_wait("showcase-default") == 0


def _main_with_env(monkeypatch):
    for var, default in (("KONG_URL", "http://kong-api-gateway:8000"),
                         ("SUPABASE_SERVICE_KEY", "dummy-key"),
                         ("DATABASE_URL", "postgresql://x:x@localhost/x")):
        monkeypatch.setenv(var, os.environ.get(var) or default)
    import main

    return main


@pytest.mark.parametrize(("use_celery", "status_code"), [(True, 400), (False, None)])
def test_submission_refuses_an_unreachable_drain_only_on_the_celery_path(
    monkeypatch, use_celery, status_code
):
    from fastapi import HTTPException

    main = _main_with_env(monkeypatch)
    monkeypatch.delenv("RAG_INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS", raising=False)
    submitted = []

    class Service:
        def total_graph_wait(self, _name):
            return 7200

        def submit(self, *_a, **_k):
            submitted.append(True)
            raise RuntimeError("stop after submit")

    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: Service())
    monkeypatch.setattr(main, "celery_is_enabled", lambda: use_celery)
    monkeypatch.setattr(main, "backend_state_store_mode", lambda: "redis")
    request = main.RagIngestionRequest(profile="big-corpus")

    with pytest.raises((HTTPException, RuntimeError)) as caught:
        asyncio.run(main.submit_rag_ingestion(request))
    if status_code:
        assert caught.value.status_code == 400
        assert "7200 s" in caught.value.detail and "3840 s" in caught.value.detail
        assert submitted == []
    else:
        assert submitted == [True]


from tests.test_rag_ingestion_api import _fake_service, _reload_main  # noqa: E402


def test_cancel_of_a_record_that_expired_meanwhile_is_404(tmp_path, monkeypatch):
    """request_cancel succeeded, then the terminal record's TTL ran out before
    the re-read: the route dereferenced None (500)."""
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service.store, "request_cancel", lambda *_a: True)
    monkeypatch.setattr(service.store, "get", lambda *_a: None)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    resp = TestClient(main.app).post("/api/rag/ingestions/gone/cancel")
    assert resp.status_code == 404, resp.text


def _dedup_record(record_id):
    from rag_ingestion.models import IngestionRecord

    return IngestionRecord(id=record_id, consumer="c", profile="p", revision="r", idempotency_key="same-key")


@pytest.mark.parametrize("live_redis", [False, True])
def test_a_resubmit_after_cancel_starts_a_new_ingestion(live_redis):
    """Dedup folded a resubmit into the cancelled (still running) record, so
    the explicit resubmit came back as a job that ends cancelled."""
    import os

    from rag_ingestion.store import InMemoryIngestionStore, RedisIngestionStore

    if live_redis:
        url = os.environ.get("ATLAS_TEST_REDIS_URL")
        if not url:
            pytest.skip("needs ATLAS_TEST_REDIS_URL")
        store = RedisIngestionStore(url)
        store._redis.flushdb()
    else:
        store = InMemoryIngestionStore()
    first, created = store.create_if_absent(_dedup_record("first"))
    assert created
    assert store.create_if_absent(_dedup_record("dup"))[1] is False  # live job: deduped
    store.request_cancel(first.id, "2026-10-07T00:00:00Z")
    second, created = store.create_if_absent(_dedup_record("second"))
    assert created and second.id == "second"


def test_small_memory_and_rag_fixes(monkeypatch):
    """A null confidence no longer discards an extraction; a 1-char Graphiti
    namespace is valid; every parser's error is reported."""
    import asyncio

    import graphiti_experiment
    from memory_service import _fact_confidence

    main = _reload_main(monkeypatch)
    from rag_ingestion.clients import CorpusFile, ParserAdapter, ParserError

    assert (_fact_confidence(None), _fact_confidence("high"), _fact_confidence(float("nan")),
            _fact_confidence(3)) == (0.8, 0.8, 0.8, 1.0)
    assert graphiti_experiment._slug("a", name="ns") == "a"
    assert main._rerank_executor() is main._rerank_executor()  # its own pool

    class TooBig:
        async def extract(self, **_kwargs):
            raise ValueError("exceeds maximum extraction size")

    adapter = ParserAdapter(TooBig())
    file = CorpusFile(name="big.pdf", content=b"%PDF" + b"\x00" * 10, content_type="application/pdf")
    with pytest.raises(ParserError, match="exceeds maximum extraction size"):
        asyncio.run(adapter.parse(file, ["docling", "tika", "plain_text"]))


def test_a_fully_migrated_index_lists_in_ceil_n_over_limit_calls():
    """Writers still SADD the legacy set, so the migration SSCAN never
    finished and padded traversals with empty pages: 5,000 records at limit
    10 took 946 calls (#1452). Needs a real Redis (ZINTERCARD)."""
    import math
    import os

    from rag_ingestion.models import IngestionRecord
    from rag_ingestion.store import RedisIngestionStore

    url = os.environ.get("ATLAS_TEST_REDIS_URL")
    if not url:
        pytest.skip("needs ATLAS_TEST_REDIS_URL")
    store = RedisIngestionStore(url)
    store._redis.flushdb()
    total, limit = 1000, 10  # > 128: a hash-encoded set, scanned in many SSCAN steps
    for index in range(total):
        store.create_if_absent(IngestionRecord(
            id=f"rec-{index}", consumer="c", profile="p", revision="r", idempotency_key=f"k-{index}"))
    calls, cursor, seen = 0, None, 0
    while True:
        page = store.list_page(cursor=cursor, limit=limit)
        calls += 1
        seen += len(page.records)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert seen == total and calls == math.ceil(total / limit)


def test_a_new_embedding_model_rebuilds_the_class_instead_of_appending(monkeypatch):
    """ensure_class accepted any existing class, so vectors of a new model or
    size were appended to the old one (#1364)."""
    schema = {}
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            calls.append(("GET", url))
            if "Rag" in schema:
                return httpx.Response(200, json=schema["Rag"], request=httpx.Request("GET", url))
            return httpx.Response(404, request=httpx.Request("GET", url))

        async def delete(self, url):
            calls.append(("DELETE", url))
            schema.pop("Rag", None)
            return httpx.Response(200, request=httpx.Request("DELETE", url))

        async def post(self, url, json):
            calls.append(("POST", url))
            schema[json["class"]] = json
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())
    client = WeaviateClient("http://weaviate")
    asyncio.run(client.ensure_class("Rag", embedding=("ollama/nomic-embed-text", 768)))
    asyncio.run(client.ensure_class("Rag", embedding=("ollama/nomic-embed-text", 768)))
    assert [c[0] for c in calls] == ["GET", "POST", "GET"]  # same identity: kept

    calls.clear()
    asyncio.run(client.ensure_class("Rag", embedding=("openai/text-embedding-3-large", 3072)))
    assert [c[0] for c in calls] == ["GET", "DELETE", "POST"]
    assert schema["Rag"]["description"] == WeaviateClient.embedding_identity(
        "openai/text-embedding-3-large", 3072)


def test_an_unchanged_corpus_under_a_new_embedding_model_is_a_new_job():
    from types import SimpleNamespace

    from rag_ingestion.service import RagIngestionService

    profile = SimpleNamespace(consumer="c", name="p", revision="r")
    old = RagIngestionService._idempotency_key(profile, {"path": "x"}, "fp", "ollama/nomic-embed-text")
    new = RagIngestionService._idempotency_key(profile, {"path": "x"}, "fp", "openai/text-embedding-3-large")
    assert old != new


def test_two_profiles_never_produce_the_same_object_id():
    from types import SimpleNamespace

    from rag_ingestion.service import RagIngestionService

    chunks = [{"content": "t", "source": "doc.md", "index": 0, "vector": [0.1]}]
    first = RagIngestionService._weaviate_objects("Docs_a_b", SimpleNamespace(name="a-b"), chunks)
    second = RagIngestionService._weaviate_objects("Docs_a_b", SimpleNamespace(name="a.b"), chunks)
    assert first[0]["id"] != second[0]["id"]


def _schema_client(schema, objects, calls):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, params=None):
            calls.append(("GET", url))
            if url.endswith("/v1/objects"):
                return httpx.Response(200, json={"objects": objects}, request=httpx.Request("GET", url))
            if "Rag" in schema:
                return httpx.Response(200, json=schema["Rag"], request=httpx.Request("GET", url))
            return httpx.Response(404, request=httpx.Request("GET", url))

        async def put(self, url, json):
            calls.append(("PUT", url))
            schema["Rag"] = json
            return httpx.Response(200, request=httpx.Request("PUT", url))

        async def delete(self, url):
            calls.append(("DELETE", url))
            schema.pop("Rag", None)
            return httpx.Response(200, request=httpx.Request("DELETE", url))

        async def post(self, url, json):
            calls.append(("POST", url))
            schema[json["class"]] = json
            return httpx.Response(200, request=httpx.Request("POST", url))

    return Client


def test_a_legacy_class_with_matching_vectors_is_adopted_not_dropped(monkeypatch):
    """Every class created before #1364 has no recorded identity; dropping it
    on the first run lost the vectors of sources that failed that run, though
    they were valid (2026-10-08 run, cycle 2)."""
    schema = {"Rag": {"class": "Rag", "vectorizer": "none"}}
    calls = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_k: _schema_client(schema, [{"vector": [0.1, 0.2, 0.3]}], calls)())
    rebuilt = asyncio.run(WeaviateClient("http://weaviate").ensure_class("Rag", embedding=("m", 3)))
    assert rebuilt is False and "DELETE" not in [c[0] for c in calls]
    assert schema["Rag"]["description"] == WeaviateClient.embedding_identity("m", 3)

    schema = {"Rag": {"class": "Rag", "vectorizer": "none"}}
    calls.clear()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_k: _schema_client(schema, [{"vector": [0.1, 0.2]}], calls)())
    rebuilt = asyncio.run(WeaviateClient("http://weaviate").ensure_class("Rag", embedding=("m", 3)))
    assert rebuilt is True and "DELETE" in [c[0] for c in calls]


def test_a_rebuilt_class_does_not_claim_failed_sources_were_preserved():
    from types import SimpleNamespace

    from rag_ingestion.service import RagIngestionService

    async def reconcile_objects(*_a, **_k):
        return None

    service = RagIngestionService.__new__(RagIngestionService)
    service.deps = SimpleNamespace(weaviate=SimpleNamespace(reconcile_objects=reconcile_objects))
    phases = {}
    record = SimpleNamespace(phase=lambda name: phases.setdefault(name, SimpleNamespace(note=None)))
    state = {"failed_sources": {"bad.pdf"}, "class_rebuilt": True}
    asyncio.run(service._reconcile_kept_sources(record, SimpleNamespace(name="p"), state, "Rag", []))
    assert "preserved" not in phases["vector_write"].note and "bad.pdf" in phases["vector_write"].note


def test_legacy_only_count_is_atomic_against_a_concurrent_writer(monkeypatch):
    """SCARD and ZINTERCARD ran as two calls; a create between them made a
    lone set-only member read as 0, ending a listing early (cycle 2)."""
    import os

    import redis as redis_module

    from rag_ingestion import store as store_module

    url = os.environ.get("ATLAS_TEST_REDIS_URL")
    if not url:
        pytest.skip("needs ATLAS_TEST_REDIS_URL")
    store = store_module.RedisIngestionStore(url)
    client = store._redis
    client.delete(store_module._INDEX_SET, store_module._INDEX_ZSET)
    client.sadd(store_module._INDEX_SET, "legacy-only", "both")
    client.zadd(store_module._INDEX_ZSET, {"both": 1})
    writer = redis_module.Redis.from_url(url)

    def inject():
        writer.sadd(store_module._INDEX_SET, "concurrent")
        writer.zadd(store_module._INDEX_ZSET, {"concurrent": 2})

    original_client_scard = type(client).scard
    original_pipe_scard = redis_module.client.Pipeline.scard

    def client_scard(self, *a, **k):
        result = original_client_scard(self, *a, **k)
        inject()
        return result

    def pipe_scard(self, *a, **k):
        result = original_pipe_scard(self, *a, **k)
        inject()
        return result

    monkeypatch.setattr(type(client), "scard", client_scard)
    monkeypatch.setattr(redis_module.client.Pipeline, "scard", pipe_scard)
    try:
        assert store._legacy_only_members() == 1
    finally:
        client.delete(store_module._INDEX_SET, store_module._INDEX_ZSET)
        writer.close()
