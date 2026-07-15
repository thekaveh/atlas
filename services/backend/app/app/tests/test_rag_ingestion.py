"""RAG ingestion job engine tests (#413).

Drives the phase orchestrator with fake upstreams (no live services) over a tiny
text corpus, covering: full lifecycle, idempotent re-submit, parser fallback,
capability fail/skip semantics, drain timeout, cancellation, schema drift, path
safety, partial retry, and the Celery task wrapper. The orchestrator is async but
tests call it via ``asyncio.run`` so no ``pytest-asyncio`` is required.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from rag_ingestion.clients import (
    CorpusFile,
    CorpusPathError,
    LightRagClient,
    MinioCorpusReader,
    MountCorpusReader,
    ParserAdapter,
)
from rag_ingestion.profiles import ProfileNotFoundError, load_profiles
from rag_ingestion.service import (
    Deps,
    IngestionExecutionLeaseLost,
    RagIngestionService,
)
from rag_ingestion.models import IngestionRecord
from rag_ingestion.store import InMemoryIngestionStore, RedisIngestionStore


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
    def available(self):
        return self._available
    async def ensure_class(self, class_name):
        self.classes.append(class_name)
    async def write_objects(self, class_name, objects):
        self.written.extend(objects)
        return len(objects)


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
        self, *, content, filename=None, content_type=None, extractor=None
    ):
        raise RuntimeError("docling exploded")


class KeywordOnlyExtractor:
    def __init__(self):
        self.calls = []

    async def extract(self, *, content, filename, content_type, extractor=None):
        self.calls.append(
            {
                "content": content,
                "filename": filename,
                "content_type": content_type,
                "extractor": extractor,
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
    assert weav.classes == ["RagShowcase_showcase-default"]
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
    assert requests[0][1]["json"] == {
        "text": "graph text",
        "file_source": "graph_native/a.txt",
    }
    assert "description" not in requests[0][1]["json"]


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

    assert store.claim_execution(record.id, "worker-a", 60) is True
    assert store.claim_execution(record.id, "worker-b", 60) is False
    claimed = store.get(record.id)
    claimed.status = "running"
    assert store.save_claimed(claimed, "worker-b") is False
    assert store.save_claimed(claimed, "worker-a") is True
    assert store.release_execution(record.id, "worker-a") is True
    assert store.claim_execution(record.id, "worker-b", 60) is True


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
    assert store.claim_execution(record.id, "worker-a", 60) is True

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

    assert store.claim_execution(record.id, "worker-a", 60) is True


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

    assert store.claim_execution(record.id, "worker-a", 60) is True


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
                raise

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
