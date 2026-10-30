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
import re
import subprocess
import sys
import threading
import uuid
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
    ParsedDocument,
    ParserAdapter,
    ParserError,
    WeaviateClient,
)
from rag_ingestion.profiles import LoadedProfile, ProfileNotFoundError, load_profiles
from rag_ingestion.service import (
    Deps,
    IngestionExecutionLeaseLost,
    RagIngestionService,
)
from rag_ingestion.models import (
    PHASES, IngestionRecord, PhaseRecord, ingestion_idempotency_key, weaviate_objects,
)
from rag_ingestion.store import (
    ExecutionClaim, PublicationClaim,
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
    endpoint = ""

    def __init__(self, available=True):
        self._available = available
        self.written = []
        self.classes = []
        self.object_ids = set()
        self.reconciled = []
        self.preserved = []
        self.source_of = {}
        self.objects = {}
        self.class_of = {}
    def available(self):
        return self._available
    async def ensure_class(self, class_name, embedding=None):
        self.classes.append(class_name)
    async def write_objects(self, class_name, objects):
        self.written.extend(objects)
        self.objects.update((obj["id"], obj) for obj in objects)
        self.class_of.update((obj["id"], class_name) for obj in objects)
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
        survivors = {oid for oid in self.object_ids if self.class_of.get(oid) != class_name
                     or self.objects[oid]["properties"].get("profile") != profile_name} | set(desired_ids) | {
            oid for oid in self.object_ids if self.source_of.get(oid) in keep_sources
        }
        self.object_ids.intersection_update(survivors)
        self.objects = {oid: obj for oid, obj in self.objects.items() if oid in survivors}
        return 0


class FakeLightrag:
    endpoint = ""

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

        def __init__(self, file_source):
            self.file_source = file_source

        def json(self):
            return {"detail": (
                f"Document storage already contains '{self.file_source}' "
                "(Status: processed). Delete the existing record before re-inserting."
            )}

        def raise_for_status(self):
            raise AssertionError("duplicate 409 must be accepted")

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, _url, **kwargs):
            return ConflictResponse(kwargs["json"]["file_source"])

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    client = LightRagClient(endpoint="http://lightrag:9621", api_key="secret")

    assert asyncio.run(
        client.upload([{"text": "same", "source": "a.txt"}])
    ) == 1


_LIGHTRAG_BUSY_DETAILS = (
    "Document scan is classifying files. Wait for the classification phase to finish before submitting new work.",
    "Pipeline is clearing or deleting documents. Wait for the running job to finish before submitting new work.",
)


def _lightrag_duplicate_response(request, payload, status):
    return httpx.Response(409, request=request, json={"detail": (
        f"Document storage already contains '{payload['file_source']}' "
        f"(Status: {status}). Delete the existing record before re-inserting."
    )})


def _mock_lightrag_transport(monkeypatch, handler):
    original_client = httpx.AsyncClient

    class MockClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)


@pytest.mark.parametrize("detail", _LIGHTRAG_BUSY_DETAILS)
def test_lightrag_busy_refusal_is_transient_not_uploaded(monkeypatch, detail):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(409, request=request, json={"detail": detail})

    _mock_lightrag_transport(monkeypatch, respond)
    client = LightRagClient("http://lightrag:9621", "secret")
    with pytest.raises(ConnectionError, match="LightRAG"):
        asyncio.run(client.upload([{"source": "a.txt", "text": "same"}]))
    assert len(requests) == 1
    assert requests[0].url.path == "/documents/text"
    assert requests[0].headers["X-API-Key"] == "secret"


@pytest.mark.parametrize("status", [
    "pending", "parsing", "analyzing", "processing", "preprocessed", "processed",
])
def test_lightrag_admitted_duplicate_counts_once(monkeypatch, status):
    def respond(request):
        return _lightrag_duplicate_response(request, json.loads(request.content), status)

    _mock_lightrag_transport(monkeypatch, respond)
    client = LightRagClient("http://lightrag:9621", "secret")
    assert asyncio.run(client.upload([{"source": "a.txt", "text": "same"}])) == 1


@pytest.mark.parametrize("case", [
    "failed", "unknown", "foreign-source", "unrelated", "no-detail", "list",
    "null", "integer-detail", "object-detail", "list-detail", "empty", "html",
])
def test_lightrag_unaccepted_conflict_raises_http_error(monkeypatch, case):
    def respond(request):
        payload = json.loads(request.content)
        if case in ("failed", "unknown"):
            return _lightrag_duplicate_response(request, payload, case)
        if case == "foreign-source":
            return _lightrag_duplicate_response(request, {"file_source": "foreign.txt"}, "processed")
        data = {
            "unrelated": {"detail": "another conflict"}, "no-detail": {}, "list": [],
            "null": None, "integer-detail": {"detail": 1},
            "object-detail": {"detail": {}}, "list-detail": {"detail": []},
        }
        if case in ("empty", "html"):
            return httpx.Response(409, request=request, text="" if case == "empty" else "<html>conflict</html>")
        return httpx.Response(409, request=request, content=json.dumps(data[case]))

    _mock_lightrag_transport(monkeypatch, respond)
    client = LightRagClient("http://lightrag:9621", "secret")
    with pytest.raises(httpx.HTTPStatusError) as error:
        asyncio.run(client.upload([{"source": "a.txt", "text": "same"}]))
    assert error.value.response.status_code == 409


def test_lightrag_partial_batch_retries_same_admitted_source(monkeypatch):
    calls = []
    accepted = set()
    refused = False
    documents = [{"source": name + ".txt", "text": name} for name in ("a", "b", "c")]

    def respond(request):
        nonlocal refused
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.headers["X-API-Key"] == "secret"
        if payload["text"] == "b" and not refused:
            refused = True
            return httpx.Response(409, request=request, json={"detail": _LIGHTRAG_BUSY_DETAILS[0]})
        if payload["file_source"] in accepted:
            return _lightrag_duplicate_response(request, payload, "processed")
        accepted.add(payload["file_source"])
        return httpx.Response(200, request=request, json={"status": "success"})

    _mock_lightrag_transport(monkeypatch, respond)
    client = LightRagClient("http://lightrag:9621", "secret")
    with pytest.raises(ConnectionError):
        asyncio.run(client.upload(documents))
    assert [call["text"] for call in calls] == ["a", "b"]
    assert len(accepted) == 1
    assert asyncio.run(client.upload(documents)) == 3
    assert [call["text"] for call in calls] == ["a", "b", "a", "b", "c"]
    assert calls[0] == calls[2] and calls[1] == calls[3]
    assert accepted == {LightRagClient._file_source(doc) for doc in documents}


def _lightrag_once_busy_handler(detail, calls):
    post_attempts = 0

    def respond(request):
        nonlocal post_attempts
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, request=request, json={"busy": False})
        post_attempts += 1
        if post_attempts == 1:
            return httpx.Response(409, request=request, json={"detail": detail})
        return httpx.Response(200, request=request, json={"status": "success"})

    return respond


@pytest.mark.parametrize("detail", _LIGHTRAG_BUSY_DETAILS)
def test_lightrag_busy_upload_persists_pending_then_retries(tmp_path, monkeypatch, detail):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    calls = []
    _mock_lightrag_transport(monkeypatch, _lightrag_once_busy_handler(detail, calls))
    profiles = _profiles_file(tmp_path, graph=_drain_graph(1))
    service = _service(tmp_path, Deps(
        embedder=FakeEmbedder(), weaviate=FakeWeaviate(),
        lightrag=LightRagClient("http://lightrag:9621", "secret"), poll_interval=0.01,
    ), profiles)
    record, _ = service.submit("showcase-default")
    with pytest.raises(ConnectionError):
        asyncio.run(service.run(record.id, retry_transient=True))
    pending = service.store.get(record.id)
    upload = pending.phase("lightrag_upload")
    assert (pending.status, upload.status, upload.note, pending.counts["documents_uploaded"]) == (
        "pending", "pending", "waiting for Celery retry", 0,
    )
    assert [request.method for request in calls] == ["POST"]
    final = asyncio.run(service.run(record.id, retry_transient=True))
    assert final.status == "completed" and not final.errors
    assert final.counts["documents_uploaded"] == 1
    assert final.phase("lightrag_upload").counts == {"uploaded": 1}
    assert [request.method for request in calls] == ["POST", "POST", "GET"]
    assert calls[0].content == calls[1].content


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_lightrag_refused_duplicate_fails_actual_job(tmp_path, monkeypatch, status):
    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    calls = []

    def respond(request):
        calls.append(request)
        return _lightrag_duplicate_response(request, json.loads(request.content), status)

    _mock_lightrag_transport(monkeypatch, respond)
    profiles = _profiles_file(tmp_path, graph=_drain_graph(1))
    service = _service(tmp_path, Deps(
        embedder=FakeEmbedder(), weaviate=FakeWeaviate(),
        lightrag=LightRagClient("http://lightrag:9621", "secret"), poll_interval=0.01,
    ), profiles)
    _, _, final = _run(service)
    assert final.status == "failed"
    assert final.phase("lightrag_upload").status == "failed"
    assert final.phase("lightrag_upload").error["http_status"] == 409
    assert f"(Status: {status})" in final.phase("lightrag_upload").error["body"]
    assert final.counts["documents_uploaded"] == 0
    assert [request.method for request in calls] == ["POST"]


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


@pytest.mark.parametrize("operation", ["discover", "fingerprint"])
def test_minio_corpus_rejects_oversize_metadata_before_download(monkeypatch, operation):
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
        getattr(MinioCorpusReader(), operation)(
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


@pytest.mark.parametrize("operation", ["discover", "fingerprint"])
def test_minio_corpus_bounds_stream_when_size_metadata_is_missing(monkeypatch, operation):
    import io
    import minio

    response = io.BytesIO(b"12345")
    cleanup = []
    response.close = lambda: cleanup.append("closed")
    response.release_conn = lambda: cleanup.append("released")

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
        getattr(MinioCorpusReader(), operation)(
            {"source": "minio", "bucket": "corpus", "prefix": "docs/"}
        )
    assert cleanup == ["closed", "released"]


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
    # `FAILED if record.errors and record.has_fatal_phase()`, so setting only the
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
    # dies on a TRANSIENT after its Weaviate mutation began, which leaves a
    # dirty publication guard. After the documented operator recovery
    # (simulated by `_recover_joined_synthetic_publication`), the retry of the
    # SAME record must not inherit attempt 1's failures. A new `submit()`
    # would create a new record with an empty errors[], which is why the
    # earlier version of this test could not tell the two implementations
    # apart.
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
    _recover_joined_synthetic_publication(service, record)
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

    profile = SimpleNamespace(consumer="c", name="p", revision="r")
    old = ingestion_idempotency_key(profile, {"path": "x"}, "fp", "ollama/nomic-embed-text")
    new = ingestion_idempotency_key(profile, {"path": "x"}, "fp", "openai/text-embedding-3-large")
    assert old != new


def test_two_profiles_never_produce_the_same_object_id():
    from types import SimpleNamespace

    chunks = [{"content": "t", "source": "doc.md", "index": 0, "vector": [0.1]}]
    first = weaviate_objects("Docs_a_b", SimpleNamespace(name="a-b"), chunks)
    second = weaviate_objects("Docs_a_b", SimpleNamespace(name="a.b"), chunks)
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


@pytest.mark.parametrize("rebuilt_class", ["Rag", "Another"])
def test_a_rebuilt_class_does_not_claim_failed_sources_were_preserved(rebuilt_class):
    from types import SimpleNamespace

    from rag_ingestion.service import RagIngestionService

    async def reconcile_objects(*_a, **_k):
        return None

    service = RagIngestionService.__new__(RagIngestionService)
    service.deps = SimpleNamespace(weaviate=SimpleNamespace(reconcile_objects=reconcile_objects))
    record = IngestionRecord("i", "c", "p", "r", "k", counts={f"class_rebuilt:{rebuilt_class}": 1})
    state = {"failed_sources": {"bad.pdf"}}
    asyncio.run(service._reconcile_kept_sources(record, SimpleNamespace(name="p"), state, "Rag", []))
    note = record.phase("vector_write").note
    assert "bad.pdf" in note
    assert "preserved existing vectors" not in note
    assert ("vector availability is unverified" in note) is (rebuilt_class != "Rag")


class RetryAfterClassCheck(FakeWeaviate):
    def __init__(self, rebuilt):
        super().__init__()
        self.rebuilt = rebuilt
        self.first_write = True
        self.object_ids = {"previous-bad"}
        self.source_of = {"previous-bad": "docs/bad.pdf"}

    async def ensure_class(self, class_name, embedding=None):
        first = not self.classes
        self.classes.append(class_name)
        if first and self.rebuilt:
            self.object_ids.clear()
            self.source_of.clear()
            return True
        return False

    async def write_objects(self, class_name, objects):
        if self.first_write:
            self.first_write = False
            raise httpx.ConnectError("temporary Weaviate write failure")
        return await super().write_objects(class_name, objects)


def test_later_target_does_not_overwrite_an_earlier_rebuild_warning():
    service = RagIngestionService.__new__(RagIngestionService)
    service.deps = SimpleNamespace(weaviate=FakeWeaviate())
    record = IngestionRecord("i", "c", "p", "r", "k", counts={"class_rebuilt:RagA": 1})
    state = {"failed_sources": {"bad.pdf"}}
    for class_name in ("RagA", "RagB"):
        asyncio.run(service._reconcile_kept_sources(record, SimpleNamespace(name="p"), state, class_name, []))
    note = record.phase("vector_write").note
    assert "earlier vectors were removed" in note
    assert "vector availability is unverified" in note
    assert "RagA" in note and "RagB" in note


@pytest.mark.parametrize("rebuilt", [True, False])
def test_class_rebuild_note_survives_a_retried_worker(tmp_path, monkeypatch, rebuilt):
    root = _corpus(tmp_path, monkeypatch, {"good.txt": "good content"})
    (root / "docs" / "bad.pdf").write_bytes(b"%PDF-1.7\x00\xff")
    profiles = _profiles_file(tmp_path, graph=[])
    weaviate = RetryAfterClassCheck(rebuilt)
    deps = Deps(embedder=FakeEmbedder(), weaviate=weaviate, lightrag=FakeLightrag(False))
    first_worker = _service(tmp_path, deps, profiles)
    record, _ = first_worker.submit("showcase-default")
    with pytest.raises(httpx.ConnectError):
        asyncio.run(first_worker.run(record.id, retry_transient=True))
    assert first_worker.store.get(record.id).status == "pending"

    # A fresh worker reloads the serialized record, rather than attempt state.
    next_worker = RagIngestionService(store=first_worker.store, deps=deps, profiles_path=profiles)
    _recover_joined_synthetic_publication(next_worker, record)
    final = asyncio.run(next_worker.run(record.id, retry_transient=True))
    note = final.phase("vector_write").note
    assert final.status == "completed" and "bad.pdf" in note
    if rebuilt:
        assert "earlier vectors were removed" in note and "preserved" not in note
        assert "previous-bad" not in weaviate.object_ids
    else:
        assert "vector availability is unverified" in note
        assert "previous-bad" in weaviate.object_ids


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


def test_one_failed_lease_renewal_does_not_discard_the_run():
    """The first renewal that raised (one slow Redis reply) set lease_lost
    and threw the whole ingestion away while the lease still had two thirds
    of its term (2026-10-08 run, cycle 12)."""
    from rag_ingestion.service import RagIngestionService

    calls = []

    class Store:
        def renew_execution(self, *_args):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("slow redis")
            return True

    service = RagIngestionService.__new__(RagIngestionService)
    service.store = Store()

    async def scenario():
        stop, lost = asyncio.Event(), asyncio.Event()
        beat = asyncio.create_task(service._heartbeat_execution("i", "o", 3, stop, lost))
        await asyncio.sleep(2.6)
        stop.set()
        await beat
        return lost.is_set()

    assert asyncio.run(scenario()) is False
    assert len(calls) >= 2


def test_a_lost_lease_at_the_infrastructure_limit_is_not_retried_again(monkeypatch):
    """Lease-lost retries never counted against any limit, so a recurring
    renewal blip looped the job forever with its record still "running"
    (2026-10-08 run, cycle 12)."""
    import celery_tasks
    import rag_ingestion

    def lose_lease(*_args, **_kwargs):
        raise rag_ingestion.IngestionExecutionLeaseLost("lost")

    def never_retry(**_kwargs):
        raise AssertionError("must not retry past the limit")

    abandoned = []
    monkeypatch.setattr(rag_ingestion, "run_rag_ingestion", lose_lease)
    monkeypatch.setattr(celery_tasks.rag_ingestion_task, "retry", never_retry)
    # The give-up path must also mark the record failed; only the service
    # method was tested, so deleting this call stayed green (cycle 44).
    monkeypatch.setattr(rag_ingestion, "fail_abandoned_ingestion",
                        lambda ingestion_id, **kw: abandoned.append((ingestion_id, kw)))
    with pytest.raises(rag_ingestion.IngestionExecutionLeaseLost):
        celery_tasks.rag_ingestion_task.run(
            "ingestion-1",
            retry_state={"phase_attempt": 0,
                         "infrastructure_attempt": celery_tasks._RAG_INFRASTRUCTURE_RETRY_LIMIT},
        )
    assert [ingestion_id for ingestion_id, _ in abandoned] == ["ingestion-1"]


def test_an_abandoned_run_is_marked_failed_so_a_resubmit_starts_fresh(tmp_path, monkeypatch):
    """At the retry limit the task re-raised and the record stayed "running",
    a dedup status, so every resubmit got the dead job back until the TTL
    (2026-10-08 run, cycle 30). A live owner's run is left alone."""
    from rag_ingestion.store import ExecutionClaim

    _corpus(tmp_path, monkeypatch, {"a.txt": "content"})
    store = InMemoryIngestionStore()
    service = RagIngestionService(store=store, deps=Deps(), profiles_path=_profiles_file(tmp_path))
    record, _ = service.submit("showcase-default")
    record.status = "running"
    store.save(record)

    assert store.claim_execution(record.id, ExecutionClaim("live-worker", 60))
    assert asyncio.run(service.fail_abandoned(record.id, "dead-worker", "lease lost")) is False
    assert store.get(record.id).status == "running"
    store.release_execution(record.id, "live-worker")

    assert asyncio.run(service.fail_abandoned(record.id, "dead-worker", "lease lost")) is True
    assert store.get(record.id).status == "failed"
    resubmitted, created = service.submit("showcase-default")
    assert created and resubmitted.id != record.id
    # The abandon claim is released, not left to block this id for a lease
    # term (cycle 44).
    assert record.id not in store._leases


def test_ensure_target_class_records_a_rebuild_only_when_the_class_was_rebuilt():
    """Rebuild history is recorded by the class check, rather than note tests."""
    from types import SimpleNamespace

    from rag_ingestion.service import RagIngestionService

    for rebuilt in (True, False):
        seen = []

        async def ensure_class(name, *, embedding, _r=rebuilt):
            seen.append((name, embedding))
            return _r

        service = RagIngestionService.__new__(RagIngestionService)
        service.deps = SimpleNamespace(
            weaviate=SimpleNamespace(ensure_class=ensure_class), embedder=SimpleNamespace(model="m"))
        state = {"chunks": [{"vector": [0.1, 0.2]}]}
        record = IngestionRecord("i", "c", "p", "r", "k")
        asyncio.run(service._ensure_target_class(record, state, "Rag"))
        assert seen == [("Rag", ("m", 2))]
        asyncio.run(service._ensure_target_class(record, state, "Rag"))
        assert record.counts == ({"class_rebuilt:Rag": 1} if rebuilt else {})


def _heartbeat_service(renew):
    from rag_ingestion.service import RagIngestionService

    class Store:
        def renew_execution(self, *_args):
            return renew()

    service = RagIngestionService.__new__(RagIngestionService)
    service.store = Store()
    return service


def _run_heartbeat(service, lease_seconds, run_for):
    async def scenario():
        stop, lost = asyncio.Event(), asyncio.Event()
        beat = asyncio.create_task(service._heartbeat_execution("i", "o", lease_seconds, stop, lost))
        await asyncio.sleep(run_for)
        stop.set()
        await asyncio.wait_for(beat, 5)
        return lost.is_set()

    return asyncio.run(scenario())


def test_renewals_that_keep_failing_lose_the_lease_before_it_expires():
    """With the deadline check disabled, renewals that always raised never
    set lease_lost, so a run kept writing after its lease expired (cycle 22)."""
    def renew():
        raise TimeoutError("redis down")

    assert _run_heartbeat(_heartbeat_service(renew), 3, 3.5) is True


def test_a_successful_renewal_restarts_the_lease_deadline():
    """Without the deadline refresh, a failure after earlier successes was
    measured against the first lease and lost a valid run (cycle 22)."""
    calls = []

    def renew():
        calls.append(1)
        if len(calls) in (1, 2):
            return True
        if len(calls) == 3:
            raise TimeoutError("slow redis")
        return True

    assert _run_heartbeat(_heartbeat_service(renew), 3, 4.5) is False
    assert len(calls) >= 4


@pytest.mark.parametrize(("status", "transient"), [(503, True), (500, True), (429, True), (408, True),
                                                   (400, False), (404, False)])
def test_an_embedder_outage_is_retried_but_a_bad_request_is_not(monkeypatch, status, transient):
    """A 503 while the embedding model loads was a plain HTTPStatusError, not
    in TRANSIENT_EXCEPTIONS, so the job failed on its first attempt
    (2026-10-08 run, cycle 50)."""
    from rag_ingestion.clients import Embedder
    from rag_ingestion.service import TRANSIENT_EXCEPTIONS

    def handler(request):
        return httpx.Response(status, json={"error": "x"}, request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    embedder = Embedder.__new__(Embedder)
    embedder._base_url, embedder._api_key, embedder._model = "http://litellm:4000/v1", "", "m"
    with pytest.raises(Exception) as raised:
        asyncio.run(embedder.embed(["text"]))
    assert isinstance(raised.value, TRANSIENT_EXCEPTIONS) is transient, raised.value


def test_the_last_renewal_attempt_ends_before_the_lease_expires():
    """A renewal can block for the store's socket timeout; with a 1 s margin
    the last failing call returned after expiry (2026-10-08 run, cycle 50)."""
    import time as _time

    class SlowStore:
        call_timeout_seconds = 1.5

        def renew_execution(self, *_args):
            _time.sleep(1.5)
            raise TimeoutError("redis stalled")

    started = _time.monotonic()
    service = _heartbeat_service(lambda: None)
    service.store = SlowStore()

    async def scenario():
        stop, lost = asyncio.Event(), asyncio.Event()
        beat = asyncio.create_task(service._heartbeat_execution("i", "o", 4, stop, lost))
        await asyncio.wait_for(beat, 10)
        return lost.is_set(), _time.monotonic() - started

    lost, elapsed = asyncio.run(scenario())
    assert lost and elapsed < 4.0, elapsed


def _mutable_ingestion_corpus(tmp_path, monkeypatch, source):
    if source != "minio":
        root = _corpus(tmp_path, monkeypatch, {"a.txt": "A"})
        if source == "nested-mount":
            (root / "docs/a").mkdir()
            (root / "docs/a/b.txt").write_text("nested")
        return {"source": "mount", "path": "docs"}, (root / "docs/a.txt").write_bytes
    import io
    import minio

    payload = {"content": b"A"}

    class Client:
        def list_objects(self, *_args, **_kwargs):
            return [SimpleNamespace(object_name="docs/a.txt", size=1, etag="unchanged")]

        def get_object(self, *_args):
            response = io.BytesIO(payload["content"])
            response.release_conn = lambda: None
            return response

    monkeypatch.setattr(minio, "Minio", lambda *_args, **_kwargs: Client())
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:9000")
    return {"source": source, "bucket": "corpus", "prefix": "docs/"}, lambda value: payload.update(content=value)


def _ingestion_regression_store(live_redis):
    if live_redis:
        url = os.environ.get("ATLAS_TEST_REDIS_URL")
        if not url:
            pytest.skip("needs disposable ATLAS_TEST_REDIS_URL")
        return RedisIngestionStore(url)
    return InMemoryIngestionStore()


def _corpus_change_service(tmp_path, corpus, store):
    path = _profiles_file(tmp_path, corpus=corpus)
    payload = json.loads(Path(path).read_text())
    payload["profiles"][0]["revision"] = str(tmp_path)
    Path(path).write_text(json.dumps(payload))
    return RagIngestionService(
        store=store,
        deps=Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag()),
        profiles_path=path,
    )


@pytest.mark.parametrize("source", ["mount", "minio"])
@pytest.mark.parametrize("live_redis", [False, True])
def test_changed_queued_corpus_does_not_dedup_a_restored_corpus(
    tmp_path, monkeypatch, source, live_redis
):
    store = _ingestion_regression_store(live_redis)
    corpus, change = _mutable_ingestion_corpus(tmp_path, monkeypatch, source)
    service = _corpus_change_service(tmp_path, corpus, store)
    submitted, _ = service.submit("showcase-default")
    change(b"B")
    final = asyncio.run(service.run(submitted.id))
    assert final.status == "completed"
    change(b"A")
    restored, created = service.submit("showcase-default")
    assert created, "A must be ingested again after the queued job ingested B"
    assert restored.id != submitted.id
    assert asyncio.run(service.run(restored.id)).status == "completed"
    assert service.deps.weaviate.written[-1]["properties"]["content"] == "A"
    assert store.create_if_absent(restored)[1] is False


@pytest.mark.parametrize("source", ["mount", "minio", "nested-mount"])
@pytest.mark.parametrize("live_redis", [False, True])
def test_unchanged_queued_corpus_remains_a_dedup_candidate(
    tmp_path, monkeypatch, source, live_redis
):
    store = _ingestion_regression_store(live_redis)
    corpus, _ = _mutable_ingestion_corpus(tmp_path, monkeypatch, source)
    service = _corpus_change_service(tmp_path, corpus, store)
    submitted, _ = service.submit("showcase-default")
    final = asyncio.run(service.run(submitted.id))
    assert final.status == "completed" and final.is_dedup_candidate
    repeated, created = service.submit("showcase-default")
    assert not created and repeated.id == submitted.id


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("claimed", [False, True])
def test_invalidated_job_cannot_restore_or_delete_a_newer_dedup_entry(live_redis, claimed):
    import uuid

    store = _ingestion_regression_store(live_redis)
    key = str(uuid.uuid4())
    old = _dedup_record(str(uuid.uuid4()))
    old.idempotency_key = key
    store.create_if_absent(old)
    assert store.claim_execution(old.id, ExecutionClaim("owner", 60))
    old.counts["dedup_invalidated"] = 1
    old.status = "running"
    save = (lambda record: store.save_claimed(record, "owner")) if claimed else store.save
    save(old)
    assert store.find_by_idempotency_key(key) is None
    new = _dedup_record(str(uuid.uuid4()))
    new.idempotency_key = key
    assert store.create_if_absent(new)[1]
    old.status = "completed"
    save(old)
    assert store.find_by_idempotency_key(key).id == new.id
    duplicate = _dedup_record(str(uuid.uuid4()))
    duplicate.idempotency_key = key
    found, created = store.create_if_absent(duplicate)
    assert not created and found.id == new.id
    store.release_execution(old.id, "owner")


@pytest.mark.parametrize("live_redis", [False, True])
def test_corpus_invalidation_survives_a_retry_that_rediscovers_original_bytes(
    tmp_path, monkeypatch, live_redis
):
    store = _ingestion_regression_store(live_redis)
    corpus, change = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, store)
    service.deps.weaviate = RetryAfterClassCheck(rebuilt=False)
    submitted, _ = service.submit("showcase-default")
    change(b"B")
    with pytest.raises(httpx.ConnectError):
        asyncio.run(service.run(submitted.id, retry_transient=True))
    saved = store.get(submitted.id)
    assert saved.status == "pending" and not saved.is_dedup_candidate
    assert store.find_by_idempotency_key(submitted.idempotency_key) is None
    change(b"A")
    newer, created = service.submit("showcase-default")
    assert created
    worker = RagIngestionService(
        store=store, deps=service.deps, profiles_path=service._profiles_path
    )
    _recover_joined_synthetic_publication(worker, submitted)
    final = asyncio.run(worker.run(submitted.id))
    assert final.status == "completed" and not final.is_dedup_candidate
    assert store.find_by_idempotency_key(submitted.idempotency_key).id == newer.id


_PROVENANCE_BAD = "docs/bad.pdf"
_PROVENANCE_BAD2 = "docs/bad2.pdf"
_PROVENANCE_GOOD = "docs/good.txt"


class _ProvenanceCorpus:
    def discover(self, *_args):
        return [CorpusFile(name, b"non-empty synthetic bytes") for name in (
            _PROVENANCE_BAD, _PROVENANCE_BAD2, _PROVENANCE_GOOD,
        )]


class _ProvenanceParser:
    def __init__(self, fail=(_PROVENANCE_BAD, _PROVENANCE_BAD2)):
        self.fail = set(fail)
        self.whitespace = set()

    async def parse(self, file, _order):
        if file.name in self.fail:
            raise ParserError("synthetic parser failure")
        text = "   " if file.name in self.whitespace else "synthetic " + file.name
        return ParsedDocument(file.name, text, "fake")


class _ProvenanceEmbedder(FakeEmbedder):
    model = "new-model"


class _ProvenanceHttpWorld:
    """Stateful schema/object protocol; every effect comes from the real client."""

    def __init__(self, classes=("Rag_p",), mode="normal", old=True):
        self.mode = mode
        self.schemas = {name: {"class": name, "description": WeaviateClient.embedding_identity(
            "old-model" if old and index == 0 else "new-model", 3,
        )} for index, name in enumerate(classes)}
        self.objects = {name: {
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}|p|{source}|0")): {
                "properties": {"profile": "p", "source": source}, "vector": [0.0] * 3,
            } for source in (_PROVENANCE_BAD, _PROVENANCE_BAD2, "docs/deleted.txt")
        } for name in classes}
        self.calls = []
        self.failed_once = False
        self.write_started = asyncio.Event()
        self.write_cancelled = False

    @staticmethod
    def response(request, status=200, data=None):
        return httpx.Response(status, json=data, request=request)

    async def handle(self, request):
        path = request.url.path
        payload = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path))
        if path.startswith("/v1/schema"):
            return self.schema(request, payload)
        if path.startswith("/v1/objects"):
            return await self.object_request(request, payload)
        if request.method == "POST" and path == "/v1/graphql":
            name = re.search(r"Get\s*\{\s*(\w+)\(", payload["query"]).group(1)
            rows = [{**obj["properties"], "_additional": {"id": oid}}
                    for oid, obj in self.objects[name].items()]
            return self.response(request, data={"data": {"Get": {name: rows}}})
        raise AssertionError(f"unexpected synthetic HTTP request: {request.method} {path}")

    def schema(self, request, payload):
        name = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return self.response(request, 200, self.schemas[name]) if name in self.schemas else self.response(request, 404)
        if request.method == "DELETE":
            self.schemas.pop(name, None)
            self.objects[name] = {}
            return self.response(request)
        assert request.method == "POST" and request.url.path == "/v1/schema"
        name = payload["class"]
        if self.mode.startswith("schema-ack") and not self.failed_once:
            self.failed_once = True
            if self.mode == "schema-ack-after-create":
                self.schemas[name] = payload
            raise httpx.ConnectError("synthetic recreation acknowledgement lost", request=request)
        self.schemas[name] = payload
        self.objects.setdefault(name, {})
        return self.response(request)

    async def object_request(self, request, payload):
        if request.method == "DELETE":
            _, _, _, name, oid = request.url.path.split("/")
            self.objects[name].pop(oid, None)
            return self.response(request, 204)
        assert request.method == "POST" and request.url.path == "/v1/objects"
        if self.mode == "lease-block" and not self.failed_once:
            self.failed_once = True
            self.write_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.write_cancelled = True
                raise
        if self.mode == "partial-write" and not self.failed_once and payload["properties"]["source"] == _PROVENANCE_GOOD:
            self.failed_once = True
            raise httpx.ConnectError("synthetic failure after partial write", request=request)
        assert payload["id"] not in self.objects[payload["class"]]
        self.objects[payload["class"]][payload["id"]] = payload
        return self.response(request, 201)

    def sources(self, name):
        return sorted(obj["properties"]["source"] for obj in self.objects[name].values())


def _provenance_setup(monkeypatch, world, parser=None):
    monkeypatch.setattr("chunking_service.chunk_text", lambda request: SimpleNamespace(
        chunks=[SimpleNamespace(index=0, content=request.text)],
    ))
    original_client = httpx.AsyncClient

    class Client(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(world.handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    profile = LoadedProfile(
        "synthetic-consumer", "p", "r", {"source": "mount", "path": "docs"},
        ["plain_text"], {"strategy": "recursive", "chunk_size": 64, "overlap": 0},
        [{"backend": "weaviate", "collection_prefix": name[:-2], "on_unavailable": "fail"}
         for name in world.objects], [],
    )
    record = IngestionRecord(
        "synthetic-ingestion", profile.consumer, "p", "r", "synthetic-key",
        phases=[PhaseRecord(name) for name in PHASES], profile_snapshot=profile.to_dict(),
        corpus=profile.corpus, counts={"files_discovered": 0, "documents_parsed": 0,
        "chunks": 0, "vectors_written": 0, "documents_uploaded": 0},
    )
    store = InMemoryIngestionStore()
    store.create_if_absent(record)
    deps = Deps(corpus=_ProvenanceCorpus(), parser=parser or _ProvenanceParser(),
                embedder=_ProvenanceEmbedder(), weaviate=WeaviateClient("http://synthetic-weaviate.invalid"),
                lightrag=FakeLightrag(False))
    return store, record, deps


@pytest.mark.parametrize("mode", ["schema-ack-before-create", "schema-ack-after-create"])
def test_provenance_after_lost_schema_ack_does_not_claim_preserved_vectors(monkeypatch, mode):
    world = _ProvenanceHttpWorld(mode=mode)
    store, record, deps = _provenance_setup(monkeypatch, world)
    with pytest.raises(httpx.ConnectError):
        asyncio.run(RagIngestionService(store=store, deps=deps).run(
            record.id, retry_transient=True, execution_owner="worker-a",
        ))
    saved = store.get(record.id)
    assert (saved.status, saved.counts.get("class_rebuilt:Rag_p"), world.sources("Rag_p")) == ("pending", None, [])
    assert ("Rag_p" in world.schemas) is (mode == "schema-ack-after-create")
    _recover_joined_synthetic_publication(RagIngestionService(store=store, deps=deps), record)
    final = asyncio.run(RagIngestionService(store=store, deps=deps).run(
        record.id, retry_transient=True, execution_owner="worker-b",
    ))
    assert (final.status, world.sources("Rag_p"), final.counts.get("class_rebuilt:Rag_p")) == (
        "completed", [_PROVENANCE_GOOD], None,
    )
    note = final.phase("vector_write").note
    assert "vector availability is unverified" in note
    assert "preserved existing vectors" not in note
    assert world.calls.count(("DELETE", "/v1/schema/Rag_p")) == 1


async def _provenance_lose_owner(world, store, record, deps):
    first = RagIngestionService(store=store, deps=deps)

    async def handoff(ingestion_id, owner, *lifecycle):
        await world.write_started.wait()
        assert store.claim_execution(ingestion_id, ExecutionClaim("new-owner", 30, owner))
        latest = store.get(ingestion_id)
        latest.counts["new-owner-sentinel"] = 42
        assert store.save_claimed(latest, "new-owner")
        lifecycle[-1].set()  # actual lease_lost event supplied by run()

    first._heartbeat_execution = handoff
    with pytest.raises(IngestionExecutionLeaseLost):
        await asyncio.wait_for(first.run(
            record.id, retry_transient=True, execution_owner="worker-a",
        ), 2)
    return store.get(record.id)


def test_provenance_after_lease_loss_preserves_new_owner_without_claiming_vectors(monkeypatch):
    world = _ProvenanceHttpWorld(mode="lease-block")
    store, record, deps = _provenance_setup(monkeypatch, world)
    saved = asyncio.run(_provenance_lose_owner(world, store, record, deps))
    assert (world.write_cancelled, saved.counts.get("class_rebuilt:Rag_p"),
            saved.counts["new-owner-sentinel"], world.sources("Rag_p")) == (True, None, 42, [])
    stale = store.get(record.id)
    stale.counts.update({"new-owner-sentinel": -1, "class_rebuilt:Rag_p": 1})
    assert (store.save_claimed(stale, "worker-a"), store.release_execution(record.id, "worker-a")) == (False, False)
    assert store.get(record.id).counts["new-owner-sentinel"] == 42
    assert store.release_execution(record.id, "new-owner")
    _recover_joined_synthetic_publication(RagIngestionService(store=store, deps=deps), record)
    final = asyncio.run(RagIngestionService(store=store, deps=deps).run(record.id, execution_owner="worker-c"))
    assert (final.status, final.counts["new-owner-sentinel"], world.sources("Rag_p")) == (
        "completed", 42, [_PROVENANCE_GOOD],
    )
    assert "vector availability is unverified" in final.phase("vector_write").note
    assert "preserved existing vectors" not in final.phase("vector_write").note


def test_provenance_partial_writes_and_multiple_classes_keep_real_survivors(monkeypatch):
    world = _ProvenanceHttpWorld(("RagA_p", "RagB_p"), mode="partial-write")
    parser = _ProvenanceParser((_PROVENANCE_BAD2,))
    store, record, deps = _provenance_setup(monkeypatch, world, parser)
    with pytest.raises(httpx.ConnectError):
        asyncio.run(RagIngestionService(store=store, deps=deps).run(record.id, retry_transient=True))
    assert store.get(record.id).counts.get("class_rebuilt:RagA_p") == 1
    parser.fail = {_PROVENANCE_BAD, _PROVENANCE_BAD2}
    _recover_joined_synthetic_publication(RagIngestionService(store=store, deps=deps), record)
    final = asyncio.run(RagIngestionService(store=store, deps=deps).run(record.id, retry_transient=True))
    assert {name: world.sources(name) for name in world.objects} == {
        "RagA_p": [_PROVENANCE_BAD, _PROVENANCE_GOOD],
        "RagB_p": [_PROVENANCE_BAD, _PROVENANCE_BAD2, _PROVENANCE_GOOD],
    }
    assert {key: value for key, value in final.counts.items() if key.startswith("class_rebuilt:")} == {"class_rebuilt:RagA_p": 1}
    note = final.phase("vector_write").note
    assert "RagA_p:" in note and "RagB_p:" in note
    assert "earlier vectors were removed" in note and "may have no vectors" in note


def test_provenance_unchanged_class_keeps_failed_sources_and_removes_stale_objects(monkeypatch):
    world = _ProvenanceHttpWorld(old=False)
    store, record, deps = _provenance_setup(monkeypatch, world)
    final = asyncio.run(RagIngestionService(store=store, deps=deps).run(record.id))
    assert (final.status, world.sources("Rag_p")) == (
        "completed", [_PROVENANCE_BAD, _PROVENANCE_BAD2, _PROVENANCE_GOOD],
    )
    assert "class_rebuilt:Rag_p" not in final.counts
    assert "class rebuilt during this job" not in final.phase("vector_write").note


def test_provenance_whitespace_errors_do_not_predict_survival_before_rebuild(monkeypatch):
    world = _ProvenanceHttpWorld()
    parser = _ProvenanceParser(())
    parser.whitespace = {_PROVENANCE_BAD, _PROVENANCE_BAD2}
    store, record, deps = _provenance_setup(monkeypatch, world, parser)
    final = asyncio.run(RagIngestionService(store=store, deps=deps).run(record.id))
    assert (final.status, world.sources("Rag_p"), final.counts.get("class_rebuilt:Rag_p")) == (
        "completed", [_PROVENANCE_GOOD], 1,
    )
    assert {error["file"] for error in final.errors} == {_PROVENANCE_BAD, _PROVENANCE_BAD2}
    assert all("parsed to whitespace" in error["message"] for error in final.errors)
    assert all("existing vectors preserved" not in error["message"] for error in final.errors)
    assert "may have no vectors" in final.phase("vector_write").note


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("generations", [("A", "B", "A"), ("A", None, "A"), (None, "B", None)])
def test_completed_generation_rollback_publishes_current_contents(
    tmp_path, monkeypatch, live_redis, generations
):
    store = _ingestion_regression_store(live_redis)
    corpus, _ = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, store)
    source = tmp_path / "corpus-root/docs/a.txt"
    records = []
    for content in generations:
        source.unlink(missing_ok=True)
        if content is not None:
            source.write_text(content)
        record, created = service.submit("showcase-default")
        assert created, "a retained historical completion must not suppress restoration"
        assert not service.submit("showcase-default")[1], "pending duplicates fold"
        final = asyncio.run(service.run(record.id))
        assert final.status == "completed", final.errors
        records.append(record)
        repeated, created = service.submit("showcase-default")
        assert not created and repeated.id == record.id, "current completion is reusable"
        assert _current_fake_contents(service.deps.weaviate) == (
            {content} if content is not None else set()
        )
        assert service.deps.weaviate.object_ids == set(service.deps.weaviate.objects)
    _assert_three_publications(store, records, service.deps.weaviate)


def _assert_three_publications(store, records, weaviate):
    assert len(weaviate.reconciled) == 3
    assert len({r.id for r in records}) == 3
    assert all(store.get(r.id).status == "completed" for r in records)


@pytest.mark.parametrize("live_redis", [False, True])
def test_completed_model_rollback_reembeds_and_reuses_current_model(tmp_path, monkeypatch, live_redis):
    corpus, _ = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, _ingestion_regression_store(live_redis))
    models = []
    async def embed(texts):
        models.append(service.deps.embedder.model)
        return [[float(len(models)), 0.0, 0.0] for _ in texts]
    service.deps.embedder.embed = embed
    for model in ("model-A", "model-B", "model-A"):
        service.deps.embedder.model = model
        record, created = service.submit("showcase-default")
        assert created, "restored embedding identity must replace the current model"
        assert asyncio.run(service.run(record.id)).status == "completed"
        assert not service.submit("showcase-default")[1]
    assert models == ["model-A", "model-B", "model-A"]
    assert all(obj["vector"] == [3.0, 0.0, 0.0] for obj in service.deps.weaviate.objects.values())


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("outcome", ["failure", "cancel", "retry", "partial", "rebuild", "reconcile"])
def test_vector_mutation_supersedes_history_before_later_outcomes(
    tmp_path, monkeypatch, live_redis, outcome
):
    corpus, change = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, _ingestion_regression_store(live_redis))
    first, _, final = _run(service)
    assert final.status == "completed"
    change(b"B")
    second, _ = service.submit("showcase-default")
    weaviate = service.deps.weaviate
    originals = (weaviate.ensure_class, weaviate.write_objects, weaviate.reconcile_objects,
                 service._phase_lightrag_upload)
    if outcome == "rebuild":
        async def destructive_rebuild(*_args, **_kwargs):
            service.deps.weaviate.objects.clear()
            service.deps.weaviate.object_ids.clear()
            raise httpx.ConnectError("class deleted before recreation acknowledgement")
        service.deps.weaviate.ensure_class = destructive_rebuild
    elif outcome == "reconcile":
        async def partial_reconcile(*_args, **_kwargs):
            service.deps.weaviate.objects.clear()
            service.deps.weaviate.object_ids.clear()
            raise httpx.ConnectError("reconciliation deletion acknowledgement lost")
        service.deps.weaviate.reconcile_objects = partial_reconcile
    elif outcome == "partial":
        original = service.deps.weaviate.write_objects
        async def fail_after_write(*args):
            await original(*args)
            raise httpx.ConnectError("write acknowledgement lost")
        service.deps.weaviate.write_objects = fail_after_write
    else:
        async def after_vectors(*_args):
            if outcome == "cancel":
                service.store.request_cancel(second.id)
                return
            raise httpx.ConnectError("later graph phase unavailable")
        service._phase_lightrag_upload = after_vectors
    if outcome == "retry":
        with pytest.raises(httpx.ConnectError):
            asyncio.run(service.run(second.id, retry_transient=True))
        assert service.store.get(second.id).status == "pending"
        assert not service.submit("showcase-default")[1], "pending retry remains folded"
    else:
        assert asyncio.run(service.run(second.id)).status == ("cancelled" if outcome == "cancel" else "failed")
    change(b"A")
    restored, created = service.submit("showcase-default")
    assert created and restored.id != first.id, "actual mutation supersedes A even when B never completes"
    _restore_after_failed_generation(service, restored, originals, outcome)


def _restore_after_failed_generation(service, restored, originals, outcome):
    if outcome in {"partial", "rebuild", "reconcile"}:
        _recover_joined_synthetic_publication(service, restored)
    weaviate = service.deps.weaviate
    (weaviate.ensure_class, weaviate.write_objects, weaviate.reconcile_objects,
     service._phase_lightrag_upload) = originals
    final = asyncio.run(service.run(restored.id))
    assert final.status == "completed", final.errors
    assert _current_fake_contents(weaviate) == {"A"}


@pytest.mark.parametrize("live_redis", [False, True])
def test_parse_refusal_preserves_current_generation(tmp_path, monkeypatch, live_redis):
    corpus, change = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, _ingestion_regression_store(live_redis))
    first, _, _ = _run(service)
    change(b"B")
    async def broken(*_args):
        raise ParserError("synthetic parse failure")
    service.deps.parser.parse = broken
    failed, _, final = _run(service)
    assert final.status == "failed"
    assert service.deps.weaviate.objects[next(iter(service.deps.weaviate.object_ids))]["properties"]["content"] == "A"
    change(b"A")
    current, created = service.submit("showcase-default")
    assert not created and current.id == first.id
    assert len(service.deps.weaviate.reconciled) == 1


def _generation_record(targets=None):
    record = _dedup_record(str(uuid.uuid4()))
    record.idempotency_key = str(uuid.uuid4())
    record.publication_targets = targets if targets is not None else [str(uuid.uuid4())]
    return record


def _pending_copy(record):
    incoming = IngestionRecord.from_dict(record.to_dict())
    incoming.id, incoming.status = str(uuid.uuid4()), "pending"
    return incoming


def _test_publication_claim(owner="owner", token="store-test-attempt"):
    return PublicationClaim(owner, token)


def _publish_for_test(store, record):
    assert store.create_if_absent(record)[1]
    assert store.claim_execution(record.id, ExecutionClaim("owner", 60))
    assert store.claim_publications(record.id, _test_publication_claim())
    for target in record.publication_targets:
        assert store.mark_publication(record.id, _test_publication_claim("owner"), (target, False))
        assert store.mark_publication(record.id, _test_publication_claim("owner"), (target, True))
    record.status = "completed"
    assert store.save_claimed(record, "owner")
    store.release_publications(record.id, _test_publication_claim(), record.publication_targets)


def _remove_retained_authority(store, record, boundary):
    if isinstance(store, RedisIngestionStore):
        from rag_ingestion.store import _PUBLICATION_PREFIX, _KEY_PREFIX, _IDX_PREFIX
        key = {"missing-marker": _PUBLICATION_PREFIX + record.publication_targets[0],
               "missing-index": _IDX_PREFIX + record.idempotency_key,
               "missing-record": _KEY_PREFIX + record.id}[boundary]
        store._redis.expire(key, 0)
    elif boundary == "missing-marker":
        store._publications.clear()
    elif boundary == "missing-index":
        store._index.pop(record.idempotency_key)
    else:
        store._records.pop(record.id)


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("boundary", ["missing-marker", "missing-index", "missing-record"])
def test_publication_currentness_fails_closed_across_retention_and_stale_saves(live_redis, boundary):
    store = _ingestion_regression_store(live_redis)
    old = _generation_record()
    _publish_for_test(store, old)
    assert not store.create_if_absent(_pending_copy(old))[1]
    _remove_retained_authority(store, old, boundary)
    store.save(IngestionRecord.from_dict(old.to_dict()))
    assert not store.mark_publication(old.id, _test_publication_claim("owner"), (old.publication_targets[0], True))
    assert store.create_if_absent(_pending_copy(old))[1]


@pytest.mark.parametrize("live_redis", [False, True])
def test_publication_transitions_are_fenced_and_declared(live_redis):
    store = _ingestion_regression_store(live_redis)
    record = _generation_record()
    target = record.publication_targets[0]
    assert store.create_if_absent(record)[1]
    assert store.claim_execution(record.id, ExecutionClaim("owner", 60))
    assert store.claim_publications(record.id, _test_publication_claim())
    assert not store.mark_publication(record.id, _test_publication_claim("wrong-owner"), (target, False))
    assert not store.mark_publication(record.id, _test_publication_claim("owner"), ("undeclared-target", False))
    assert store.mark_publication(record.id, _test_publication_claim("owner"), (target, False))
    record.status = "completed"
    assert store.save_claimed(record, "owner")
    assert not store.mark_publication(record.id, _test_publication_claim("owner"), (target, True))


@pytest.mark.parametrize("live_redis", [False, True])
def test_publication_transitions_are_target_independent(live_redis):
    store = _ingestion_regression_store(live_redis)
    shared = str(uuid.uuid4())
    records = [_generation_record(targets) for targets in (
        [shared, str(uuid.uuid4())], [str(uuid.uuid4())], [shared],
    )]
    for record in records:
        _publish_for_test(store, record)
    assert store.create_if_absent(_pending_copy(records[0]))[1]
    assert not store.create_if_absent(_pending_copy(records[1]))[1]
    assert not store.create_if_absent(_pending_copy(records[2]))[1]


@pytest.mark.parametrize("live_redis", [False, True])
def test_legacy_completed_record_without_currentness_proof_is_reexecuted(live_redis):
    store = _ingestion_regression_store(live_redis)
    old = _generation_record()
    old.publication_targets, old.status = None, "completed"
    store.save(old)
    assert store.create_if_absent(_pending_copy(old))[1]


def _change_generation_targets(profile, variant):
    target = profile["vector_targets"][0]
    if variant in ("intersecting", "target-set-only"):
        profile["vector_targets"] = [{**target, "collection_prefix": prefix} for prefix in ("Y", "Z")]
    elif variant in ("disjoint", "consumer"):
        profile["vector_targets"] = [{**target, "collection_prefix": "Z"}]
    if variant in ("consumer", "consumer-alias"):
        profile["consumer"] += "-other"
    if variant == "profile":
        profile["name"] += "-other"
    if variant == "profile-alias":
        profile["name"] = profile["name"].replace("-", ".")


def _current_fake_contents(weaviate, classes=None):
    return {obj["properties"]["content"] for oid, obj in weaviate.objects.items()
            if obj["properties"]["profile"] == "showcase-default"
            and (classes is None or weaviate.class_of[oid] in classes)}


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("variant", [
    ("intersecting", True), ("disjoint", False), ("consumer", False),
    ("profile", False), ("target-set-only", True), ("consumer-alias", True), ("profile-alias", True),
])
def test_generation_currentness_uses_individual_consumer_profile_targets(
    tmp_path, monkeypatch, live_redis, variant
):
    variant, superseded = variant
    corpus, change = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, _ingestion_regression_store(live_redis))
    path = Path(service._profiles_path)
    document = json.loads(path.read_text())
    profile = document["profiles"][0]
    target = profile["vector_targets"][0]
    profile["vector_targets"] = [{**target, "collection_prefix": prefix} for prefix in ("X", "Y")]
    path.write_text(json.dumps(document))
    first, _, _ = _run(service)
    original = json.loads(path.read_text())
    _change_generation_targets(profile, variant)
    path.write_text(json.dumps(document))
    change(b"A" if variant == "target-set-only" else b"B")
    changed, created = service.submit(profile["name"])
    assert created
    assert asyncio.run(service.run(changed.id)).status == "completed"
    path.write_text(json.dumps(original))
    change(b"A")
    restored, created = service.submit("showcase-default")
    assert created is superseded
    assert asyncio.run(service.run(restored.id)).status == "completed"
    assert _current_fake_contents(service.deps.weaviate, {"X_showcase_default", "Y_showcase_default"}) == {"A"}


def _lose_publication_ownership(store, record, boundary):
    target = record.publication_targets[0]
    if boundary == "lease-replaced":
        assert store.claim_execution(record.id, ExecutionClaim("replacement", 60, "owner"))
    elif isinstance(store, RedisIngestionStore):
        from rag_ingestion.store import _PUBLICATION_PREFIX, _LEASE_PREFIX
        key = (_LEASE_PREFIX + record.id) if boundary == "lease-expired" else (_PUBLICATION_PREFIX + target)
        if boundary == "begin-replaced":
            store._redis.set(key, "another-job:owner", ex=60)
        else:
            store._redis.expire(key, 0)
    elif boundary == "lease-expired":
        store._leases[record.id] = ("owner", 0)
    elif boundary == "begin-expired":
        store._publications.pop(target)
    else:
        store._publications[target] = "another-job:owner"


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("boundary", ["lease-expired", "lease-replaced", "begin-expired", "begin-replaced"])
def test_publication_finish_cannot_restore_authority_after_ownership_loss(live_redis, boundary):
    store = _ingestion_regression_store(live_redis)
    record = _generation_record()
    target = record.publication_targets[0]
    assert store.create_if_absent(record)[1]
    assert store.claim_execution(record.id, ExecutionClaim("owner", 60))
    assert store.claim_publications(record.id, _test_publication_claim())
    assert store.mark_publication(record.id, _test_publication_claim("owner"), (target, False))
    _lose_publication_ownership(store, record, boundary)
    store.mark_publication(record.id, _test_publication_claim("owner"), (target, True))
    if boundary == "lease-expired":
        assert store.claim_execution(record.id, ExecutionClaim("replacement", 60))
    owner = "replacement" if boundary.startswith("lease-") else "owner"
    record.status = "completed"
    assert store.save_claimed(record, owner)
    assert store.create_if_absent(_pending_copy(record))[1], "a stale finish cannot prove current completion"


@pytest.mark.parametrize("live_redis", [False, True])
def test_stale_completion_cannot_take_a_new_pending_candidates_index(live_redis):
    store = _ingestion_regression_store(live_redis)
    old = _generation_record([])
    assert store.create_if_absent(old)[1]
    assert store.claim_execution(old.id, ExecutionClaim("owner", 60))
    store.request_cancel(old.id)
    new = _pending_copy(old)
    assert store.create_if_absent(new)[1]
    old.status = "completed"
    assert store.save_claimed(old, "owner")
    assert store.find_by_idempotency_key(old.idempotency_key).id == new.id
    assert store.create_if_absent(_pending_copy(new))[0].id == new.id


@pytest.mark.parametrize("live_redis", [False, True])
def test_explicit_no_vector_targets_roundtrip_retains_current_reuse(tmp_path, monkeypatch, live_redis):
    corpus, _ = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, _ingestion_regression_store(live_redis))
    path = Path(service._profiles_path)
    document = json.loads(path.read_text())
    document["profiles"][0]["vector_targets"] = []
    path.write_text(json.dumps(document))
    record, _, final = _run(service)
    assert final.status == "completed"
    assert service.store.get(record.id).publication_targets == []
    repeated, created = service.submit("showcase-default")
    assert not created and repeated.id == record.id


def test_restored_generation_dispatches_but_current_completion_is_reused(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    corpus, _ = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, InMemoryIngestionStore())
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)
    runs = []
    original = service.run
    async def track(*args, **kwargs):
        runs.append(args[0])
        return await original(*args, **kwargs)
    monkeypatch.setattr(service, "run", track)
    client = TestClient(main.app)
    path = tmp_path / "corpus-root/docs/a.txt"
    ids = []
    for content in ("A", "B", "A"):
        path.write_text(content)
        result = client.post("/api/rag/ingestions", json={"profile": "showcase-default"})
        assert result.status_code == 202 and result.json()["status"] == "completed"
        ids.append(result.json()["ingestion_id"])
        repeated = client.post("/api/rag/ingestions", json={"profile": "showcase-default"})
        assert repeated.json()["ingestion_id"] == ids[-1]
    assert len(set(ids)) == len(runs) == 3


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("unavailable", ["weaviate", "embedder"])
def test_nonmutating_skips_preserve_old_currentness_without_claiming_new_publication(
    tmp_path, monkeypatch, live_redis, unavailable
):
    corpus, change = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, _ingestion_regression_store(live_redis))
    path = Path(service._profiles_path)
    document = json.loads(path.read_text())
    document["profiles"][0]["vector_targets"][0]["on_unavailable"] = "skip"
    path.write_text(json.dumps(document))
    first, _, _ = _run(service)
    change(b"B")
    getattr(service.deps, unavailable)._available = False
    _, _, final = _run(service)
    assert final.status == "completed"
    assert service.submit("showcase-default")[1], "a skipped configured target has no current-publication proof"
    # Restore the original configured capability before reusing its publication.
    getattr(service.deps, unavailable)._available = True
    change(b"A")
    current, created = service.submit("showcase-default")
    assert not created and current.id == first.id
    assert _current_fake_contents(service.deps.weaviate) == {"A"}


class _DestructiveGenerationWeaviate(FakeWeaviate):
    def __init__(self):
        super().__init__()
        self.schemas = {}
        self.rebuilds = []

    async def ensure_class(self, class_name, embedding=None):
        previous = self.schemas.get(class_name)
        rebuilt = previous is not None and previous != embedding
        if rebuilt:
            removed = {oid for oid in self.object_ids if self.class_of[oid] == class_name}
            self.rebuilds.append((class_name, removed))
            for oid in removed:
                self.objects.pop(oid)
            self.object_ids.difference_update(removed)
        self.schemas[class_name] = embedding
        self.classes.append(class_name)
        return rebuilt


def _assert_whole_class_generation(weaviate, profile_name, model, content):
    class_name = "RagShowcase_showcase_default"
    object_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{class_name}|{profile_name}|docs/a.txt|0"))
    assert weaviate.schemas == {class_name: (model, 3)}
    assert weaviate.object_ids == {object_id}
    assert set(weaviate.objects) == {object_id}
    assert weaviate.objects[object_id]["properties"] == {
        "content": content, "source": "docs/a.txt", "profile": profile_name, "chunkIndex": 0,
    }


@pytest.mark.parametrize("live_redis", [False, True])
def test_model_alias_rollback_rebuilds_and_restores_entire_physical_class(tmp_path, monkeypatch, live_redis):
    corpus, change = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    store = _ingestion_regression_store(live_redis)
    service = _corpus_change_service(tmp_path, corpus, store)
    weaviate = service.deps.weaviate = _DestructiveGenerationWeaviate()
    path = Path(service._profiles_path)
    document = json.loads(path.read_text())
    records, published_ids = [], []
    for profile_name, model, content in (
        ("showcase-default", "model-A", "A"),
        ("showcase.default", "model-B", "B"),
        ("showcase-default", "model-A", "A"),
    ):
        document["profiles"][0]["name"] = profile_name
        path.write_text(json.dumps(document))
        service.deps.embedder.model = model
        change(content.encode())
        record, created = service.submit(profile_name)
        assert created, "restoration must execute after the alias destroyed the class"
        assert asyncio.run(service.run(record.id)).status == "completed"
        _assert_whole_class_generation(weaviate, profile_name, model, content)
        assert not service.submit(profile_name)[1], "the current model/class is reusable"
        records.append(record)
        published_ids.append(set(weaviate.object_ids))
    assert [removed for _class_name, removed in weaviate.rebuilds] == published_ids[:2]
    assert published_ids[0].isdisjoint(published_ids[1])
    assert published_ids[0] == published_ids[2]
    _assert_three_publications(store, records, weaviate)


def _class_generation_objects(weaviate, class_name):
    return {oid: obj for oid, obj in weaviate.objects.items() if weaviate.class_of[oid] == class_name}


def _class_publication_authorities(store, classes):
    from rag_ingestion.store import _PUBLICATION_PREFIX, publication_target

    targets = [publication_target(class_name) for class_name in classes]
    if isinstance(store, RedisIngestionStore):
        values = store._redis.mget([_PUBLICATION_PREFIX + target for target in targets])
    else:
        values = [store._publications.get(target) for target in targets]
    return dict(zip(classes, values))


def _assert_split_target_publication(service, records):
    first, failed = records
    weaviate = service.deps.weaviate
    classes = ("X_showcase_default", "Y_showcase_default")
    assert _class_publication_authorities(service.store, classes) == {
        classes[0]: failed.id, classes[1]: first.id,
    }
    assert {obj["properties"]["content"] for obj in _class_generation_objects(weaviate, classes[0]).values()} == {"B"}
    assert {obj["properties"]["content"] for obj in _class_generation_objects(weaviate, classes[1]).values()} == {"A"}


@pytest.mark.parametrize("live_redis", [False, True])
def test_first_target_publication_before_second_target_failure_restores_both(tmp_path, monkeypatch, live_redis):
    corpus, change = _mutable_ingestion_corpus(tmp_path, monkeypatch, "mount")
    service = _corpus_change_service(tmp_path, corpus, _ingestion_regression_store(live_redis))
    path = Path(service._profiles_path)
    document = json.loads(path.read_text())
    target = document["profiles"][0]["vector_targets"][0]
    document["profiles"][0]["vector_targets"] = [
        {**target, "collection_prefix": prefix} for prefix in ("X", "Y")
    ]
    path.write_text(json.dumps(document))
    first, _, _ = _run(service)
    weaviate = service.deps.weaviate
    original_objects = dict(weaviate.objects)
    change(b"B")
    reconcile = weaviate.reconcile_objects

    async def reconcile_and_disconnect(*args, **kwargs):
        result = await reconcile(*args, **kwargs)
        weaviate._available = False
        return result

    weaviate.reconcile_objects = reconcile_and_disconnect
    second, _, failed = _run(service)
    assert failed.status == "failed"
    assert failed.phase("vector_write").status == "failed"
    _assert_split_target_publication(service, (first, second))
    assert [name for name, _profile, _ids in weaviate.reconciled] == [
        "X_showcase_default", "Y_showcase_default", "X_showcase_default",
    ]
    weaviate.reconcile_objects = reconcile
    weaviate._available = True
    change(b"A")
    restored, created = service.submit("showcase-default")
    assert created, "one superseded target rejects the aggregate old completion"
    assert asyncio.run(service.run(restored.id)).status == "completed"
    assert weaviate.objects == original_objects
    assert weaviate.object_ids == set(original_objects)
    assert _class_publication_authorities(service.store, ("X_showcase_default", "Y_showcase_default")) == {
        "X_showcase_default": restored.id, "Y_showcase_default": restored.id,
    }


def _overlap_publication_trace(service, first, second):
    class_name = "RagShowcase_showcase_default"
    return {
        "first_id": first.id, "second_id": second.id,
        "publication_targets": second.publication_targets,
        "authority": _class_publication_authorities(service.store, [class_name]),
        "vector_ids": sorted(service.deps.weaviate.objects),
        "contents": sorted(_current_fake_contents(service.deps.weaviate)),
    }


async def _overlapping_corpus_generations(tmp_path, monkeypatch):
    """Pause A's accepted write while B publishes the same physical class."""
    from rag_ingestion.service import IngestionExecutionBusy

    root = _corpus(tmp_path, monkeypatch, {"a.txt": "A"})
    (root / "other").mkdir()
    (root / "other/b.txt").write_text("B", encoding="utf-8")
    monkeypatch.setattr("chunking_service.chunk_text", lambda request: SimpleNamespace(
        chunks=[SimpleNamespace(index=0, content=request.text)],
    ))
    write_started, release_write = asyncio.Event(), asyncio.Event()

    class PausedWeaviate(FakeWeaviate):
        async def write_objects(self, class_name, objects):
            written = await super().write_objects(class_name, objects)
            if objects[0]["properties"]["content"] == "A":
                write_started.set()
                await release_write.wait()
            return written

    store = InMemoryIngestionStore()
    service = _corpus_change_service(tmp_path, {"source": "mount", "path": "docs"}, store)
    service.deps.weaviate = PausedWeaviate()
    first, created = service.submit("showcase-default", corpus_path="docs")
    second, second_created = service.submit("showcase-default", corpus_path="other")
    assert created and second_created and first.id != second.id
    pending, created = service.submit("showcase-default", corpus_path="other")
    assert not created and pending.id == second.id, "identical pending work must still dedup"
    first_task = asyncio.create_task(service.run(first.id, execution_owner="generation-a"))
    deferred, trace = False, {}
    try:
        await asyncio.wait_for(write_started.wait(), timeout=2)
        try:
            final = await asyncio.wait_for(
                service.run(second.id, execution_owner="generation-b"), timeout=2,
            )
        except IngestionExecutionBusy:
            deferred = True
            assert _current_fake_contents(service.deps.weaviate) == {"A"}
        else:
            assert final.status == "completed", final.errors
            assert _current_fake_contents(service.deps.weaviate) == {"B"}
        trace["before_a_resumes"] = _overlap_publication_trace(service, first, second)
    finally:
        release_write.set()
        await asyncio.wait_for(first_task, timeout=2)
    if deferred:
        final = await service.run(second.id, execution_owner="generation-b-retry")
        assert final.status == "completed", final.errors
    trace["after_a_finishes"] = _overlap_publication_trace(service, first, second)
    return service, second, trace


@pytest.mark.parametrize("invariant", ["current-vectors", "dedup-requires-vectors"])
def test_overlapping_corpus_generations_preserve_current_publication(
    tmp_path, monkeypatch, invariant,
):
    service, second, trace = asyncio.run(_overlapping_corpus_generations(tmp_path, monkeypatch))
    contents = _current_fake_contents(service.deps.weaviate)
    if invariant == "current-vectors":
        assert contents == {"B"}, json.dumps(trace, sort_keys=True)
    else:
        repeated, created = service.submit("showcase-default", corpus_path="other")
        trace["resubmit_b"] = {"created": created, "id": repeated.id, "status": repeated.status}
        assert created or contents == {"B"}, (
            "completed B reused while vectors missing: " + json.dumps(trace, sort_keys=True)
        )
        if not created:
            assert repeated.id == second.id


@pytest.mark.parametrize("competing_profile", ["showcase-default", "showcase.default"])
def test_publication_begin_excludes_other_active_jobs_for_the_same_physical_class(
    competing_profile,
):
    from rag_ingestion.models import weaviate_class_name
    from rag_ingestion.store import publication_target

    class_name = weaviate_class_name("RagShowcase", "showcase-default")
    assert weaviate_class_name("RagShowcase", competing_profile) == class_name
    target = publication_target(class_name)
    store = InMemoryIngestionStore()
    first, second = _generation_record([target]), _generation_record([target])
    second.profile = competing_profile
    for record, owner in ((first, "owner-a"), (second, "owner-b")):
        assert store.create_if_absent(record)[1]
        assert store.claim_execution(record.id, ExecutionClaim(owner, 60))
    assert store.claim_publications(first.id, _test_publication_claim("owner-a"))
    assert not store.claim_publications(second.id, _test_publication_claim("owner-b"))
    assert store.mark_publication(first.id, _test_publication_claim("owner-a"), (target, False))
    assert not store.mark_publication(second.id, _test_publication_claim("owner-b"), (target, False)), (
        "a different active ingestion must not replace physical-target ownership"
    )


class _AcceptedRemoteWrite(FakeWeaviate):
    """An accepted remote write outlives the HTTP caller's wait."""
    def __init__(self, interruption):
        super().__init__()
        self.interruption = interruption
        self.accepted = asyncio.Event()
        self.finish = asyncio.Event()
        self.remote = None

    async def _complete_remote(self, class_name, objects):
        await self.finish.wait()
        await super().write_objects(class_name, objects)

    async def write_objects(self, class_name, objects):
        if self.remote is None:
            self.remote = asyncio.create_task(self._complete_remote(class_name, objects))
            self.accepted.set()
            if self.interruption == "timeout":
                raise httpx.ReadTimeout("accepted write acknowledgement was lost")
            await asyncio.Event().wait()
        return await super().write_objects(class_name, objects)


def _uncertain_publication_service(tmp_path, monkeypatch, interruption):
    root = _corpus(tmp_path, monkeypatch, {"a.txt": "A"})
    (root / "other").mkdir()
    (root / "other/b.txt").write_text("B", encoding="utf-8")
    monkeypatch.setattr("chunking_service.chunk_text", lambda request: SimpleNamespace(
        chunks=[SimpleNamespace(index=0, content=request.text)],
    ))
    service = _corpus_change_service(
        tmp_path, {"source": "mount", "path": "docs"}, InMemoryIngestionStore(),
    )
    service.deps.weaviate = _AcceptedRemoteWrite(interruption)
    return service


async def _interrupt_accepted_publication(service, record, interruption):
    task = asyncio.create_task(service.run(
        record.id, retry_transient=True, execution_owner="reused-owner",
    ))
    await asyncio.wait_for(service.deps.weaviate.accepted.wait(), 2)
    if interruption == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(httpx.ReadTimeout):
            await task


@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
def test_uncertain_remote_write_blocks_a_later_generation(tmp_path, monkeypatch, interruption):
    from rag_ingestion.service import IngestionExecutionBusy

    async def scenario():
        service = _uncertain_publication_service(tmp_path, monkeypatch, interruption)
        first, _ = service.submit("showcase-default")
        second, _ = service.submit("showcase-default", corpus_path="other")
        try:
            await _interrupt_accepted_publication(service, first, interruption)
            with pytest.raises(IngestionExecutionBusy):
                await service.run(second.id, execution_owner="reused-owner")
            assert service.store.get(second.id).status == "pending"
            assert not service.deps.weaviate.objects
        finally:
            service.deps.weaviate.finish.set()
            if service.deps.weaviate.remote is not None:
                await asyncio.wait_for(service.deps.weaviate.remote, 2)
        assert _current_fake_contents(service.deps.weaviate) == {"A"}
        assert service.store.get(second.id).status != "completed"
    asyncio.run(scenario())


def test_uncertain_remote_write_cannot_be_adopted_by_a_reused_execution_owner(tmp_path, monkeypatch):
    from rag_ingestion.service import IngestionExecutionBusy

    async def scenario():
        service = _uncertain_publication_service(tmp_path, monkeypatch, "timeout")
        record, _ = service.submit("showcase-default")
        try:
            await _interrupt_accepted_publication(service, record, "timeout")
            with pytest.raises(IngestionExecutionBusy):
                await service.run(record.id, execution_owner="reused-owner")
        finally:
            service.deps.weaviate.finish.set()
            if service.deps.weaviate.remote is not None:
                await asyncio.wait_for(service.deps.weaviate.remote, 2)
    asyncio.run(scenario())


def test_publication_begin_reserves_all_physical_targets_without_partial_mutation():
    store = InMemoryIngestionStore()
    held = _generation_record(["shared"])
    incoming = _generation_record(["free", "shared"])
    for record, owner in ((held, "holder"), (incoming, "contender")):
        assert store.create_if_absent(record)[1]
        assert store.claim_execution(record.id, ExecutionClaim(owner, 60))
    assert store.claim_publications(held.id, _test_publication_claim("holder"))
    assert store.mark_publication(held.id, _test_publication_claim("holder"), ("shared", False))
    assert not store.claim_publications(incoming.id, _test_publication_claim("contender"))
    assert "free" not in store._publication_guards
    assert not store.mark_publication(incoming.id, _test_publication_claim("contender"), ("free", False)), (
        "admission must check the entire physical target set before its first mutation"
    )
    assert "free" not in store._publications


def _advance_publication_stage(store, record, claim, stage):
    target = record.publication_targets[0]
    if stage != "reserved":
        assert store.mark_publication(record.id, claim, (target, False))
    if stage == "published":
        assert store.mark_publication(record.id, claim, (target, True))
        record.status = "completed"
        assert store.save_claimed(record, claim.execution_owner)


def _recover_joined_synthetic_publication(service, record):
    """Operator fixture after joined, non-background fake mutations only.

    These synchronous fakes and MockTransport handlers cannot finish a remote
    mutation after returning. Production operators must establish that fact
    independently for every publisher; job termination alone is insufficient.
    """
    from rag_ingestion.store import (
        _PUBLICATION_GUARD_PREFIX, _PUBLICATION_PREFIX,
    )
    from rag_ingestion.service import IngestionExecutionBusy
    with pytest.raises(IngestionExecutionBusy):
        asyncio.run(service.run(record.id, retry_transient=True))
    targets = service.store.get(record.id).publication_targets
    assert any(_publication_guard_value(service.store, target) for target in targets)
    if isinstance(service.store, RedisIngestionStore):
        keys = [prefix + target for target in targets
                for prefix in (_PUBLICATION_GUARD_PREFIX, _PUBLICATION_PREFIX)]
        service.store._redis.delete(*keys)
    else:
        for target in targets:
            service.store._publication_guards.pop(target, None)
            service.store._publications.pop(target, None)


def _publication_guard_value(store, target):
    if isinstance(store, RedisIngestionStore):
        from rag_ingestion.store import _PUBLICATION_GUARD_PREFIX
        key = _PUBLICATION_GUARD_PREFIX + target
        value = store._redis.get(key)
        if value is not None:
            assert store._redis.ttl(key) == -1, "collection guards must not expire"
        return value
    return store._publication_guards.get(target)


def _reserved_publication(store, targets=None):
    record = _generation_record(targets)
    claim = _test_publication_claim(token=str(uuid.uuid4()))
    assert store.create_if_absent(record)[1]
    assert store.claim_execution(record.id, ExecutionClaim(claim.execution_owner, 60))
    assert store.claim_publications(record.id, claim)
    return record, claim


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("stage", ["reserved", "dirty", "published"])
def test_publication_guard_never_adopts_an_existing_attempt(live_redis, stage):
    store = _ingestion_regression_store(live_redis)
    record, claim = _reserved_publication(store)
    target = record.publication_targets[0]
    _advance_publication_stage(store, record, claim, stage)
    before = _publication_guard_value(store, target)
    assert not store.claim_publications(record.id, claim)
    other_attempt = PublicationClaim(claim.execution_owner, str(uuid.uuid4()))
    assert not store.claim_publications(record.id, other_attempt)
    assert not store.create_if_absent(_pending_copy(record))[1]
    contender = _pending_copy(record)
    contender.idempotency_key = str(uuid.uuid4())
    assert store.create_if_absent(contender)[1]
    assert store.claim_execution(contender.id, ExecutionClaim(claim.execution_owner, 60))
    assert not store.claim_publications(contender.id, other_attempt)
    assert _publication_guard_value(store, target) == before


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("boundary", ["missing-record", "missing-marker", "lease-expired", "lease-replaced"])
def test_uncertain_publication_guard_outlives_retention_and_execution(live_redis, boundary):
    store = _ingestion_regression_store(live_redis)
    record, claim = _reserved_publication(store)
    target = record.publication_targets[0]
    assert store.mark_publication(record.id, claim, (target, False))
    before = _publication_guard_value(store, target)
    if boundary.startswith("missing-"):
        _remove_retained_authority(store, record, boundary)
    else:
        _lose_publication_ownership(store, record, boundary)
    store.release_publications(record.id, claim, [target])
    assert _publication_guard_value(store, target) == before
    assert not store.mark_publication(record.id, claim, (target, True))
    contender = _pending_copy(record)
    contender.idempotency_key = str(uuid.uuid4())
    assert store.create_if_absent(contender)[1]
    assert store.claim_execution(contender.id, ExecutionClaim(claim.execution_owner, 60))
    assert not store.claim_publications(contender.id, PublicationClaim(claim.execution_owner, "new-attempt"))
    assert _publication_guard_value(store, target) == before


@pytest.mark.parametrize("live_redis", [False, True])
def test_publication_guard_cleanup_is_exact_attempt_and_clean_only(live_redis):
    store = _ingestion_regression_store(live_redis)
    first, first_claim = _reserved_publication(store)
    target = first.publication_targets[0]
    wrong = PublicationClaim(first_claim.execution_owner, "different-attempt")
    before = _publication_guard_value(store, target)
    store.release_publications(first.id, wrong, [target])
    assert _publication_guard_value(store, target) == before
    store.release_publications(first.id, first_claim, [target])
    assert _publication_guard_value(store, target) is None
    second, second_claim = _reserved_publication(store, [target])
    for published in (False, True):
        assert store.mark_publication(second.id, second_claim, (target, published))
        before = _publication_guard_value(store, target)
        store.release_publications(first.id, first_claim, [target])
        assert _publication_guard_value(store, target) == before
        if not published:
            store.release_publications(second.id, second_claim, [target])
            assert _publication_guard_value(store, target) == before
    store.release_publications(second.id, second_claim, [target])
    assert _publication_guard_value(store, target) is None


@pytest.mark.parametrize("live_redis", [False, True])
def test_atomic_publication_guard_refusal_leaves_free_targets_and_proofs_untouched(live_redis):
    store = _ingestion_regression_store(live_redis)
    held, _claim = _reserved_publication(store)
    shared = held.publication_targets[0]
    free = str(uuid.uuid4())
    previous = _generation_record([free])
    _publish_for_test(store, previous)
    contender = _generation_record([free, shared])
    claim = _test_publication_claim("contender", str(uuid.uuid4()))
    assert store.create_if_absent(contender)[1]
    assert store.claim_execution(contender.id, ExecutionClaim(claim.execution_owner, 60))
    assert not store.claim_publications(contender.id, claim)
    assert _publication_guard_value(store, free) is None
    assert not store.create_if_absent(_pending_copy(previous))[1]


@pytest.mark.parametrize("live_redis", [False, True])
def test_publication_guard_uses_physical_class_for_historical_profile_aliases(live_redis):
    from rag_ingestion.models import weaviate_class_name
    from rag_ingestion.store import publication_target

    store = _ingestion_regression_store(live_redis)
    prefix = "Alias_" + uuid.uuid4().hex
    classes = [weaviate_class_name(prefix, name) for name in ("showcase-default", "showcase.default")]
    assert classes[0] == classes[1]
    first, _claim = _reserved_publication(store, [publication_target(classes[0])])
    second = _generation_record([publication_target(classes[1])])
    second.profile = "showcase.default"
    assert store.create_if_absent(second)[1]
    assert store.claim_execution(second.id, ExecutionClaim("alias-owner", 60))
    assert not store.claim_publications(second.id, PublicationClaim("alias-owner", str(uuid.uuid4())))
    assert _publication_guard_value(store, first.publication_targets[0]) is not None


def test_cancelled_publication_admission_joins_and_releases_clean_guard(tmp_path, monkeypatch):
    service = _uncertain_publication_service(tmp_path, monkeypatch, "timeout")
    service.deps.weaviate = FakeWeaviate()
    record, _ = service.submit("showcase-default")
    started, finish = threading.Event(), threading.Event()
    original = service.store.claim_publications

    def delayed_claim(*args):
        started.set()
        assert finish.wait(5)
        return original(*args)

    monkeypatch.setattr(service.store, "claim_publications", delayed_claim)

    async def scenario():
        task = asyncio.create_task(service.run(record.id))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "late admission must join before cleanup"
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    asyncio.run(scenario())
    assert not service.store._publication_guards
    assert not service.deps.weaviate.classes


def test_cancelled_publication_begin_cannot_dirty_a_released_guard(tmp_path, monkeypatch):
    service = _uncertain_publication_service(tmp_path, monkeypatch, "timeout")
    service.deps.weaviate = FakeWeaviate()
    record, _ = service.submit("showcase-default")
    started, finish = threading.Event(), threading.Event()
    original = service.store.mark_publication
    late_results = []

    def delayed_begin(*args):
        started.set()
        assert finish.wait(5)
        late_results.append(original(*args))

    monkeypatch.setattr(service.store, "mark_publication", delayed_begin)

    async def scenario():
        task = asyncio.create_task(service.run(record.id))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert not service.store._publication_guards
        finally:
            finish.set()
    asyncio.run(scenario())
    assert late_results == [False], "late begin must compare the full clean guard token"
    assert not service.deps.weaviate.classes


def test_completed_vectors_release_clean_guard_when_final_record_save_fails(tmp_path, monkeypatch):
    service = _uncertain_publication_service(tmp_path, monkeypatch, "timeout")
    service.deps.weaviate = FakeWeaviate()
    record, _ = service.submit("showcase-default")
    second, _ = service.submit("showcase-default", corpus_path="other")
    original = service.store.save_claimed

    def failed_final_save(current, owner):
        if current.status == "completed" and current.id == record.id:
            raise RedisConnectionError("final record acknowledgement lost")
        return original(current, owner)

    monkeypatch.setattr(service.store, "save_claimed", failed_final_save)
    with pytest.raises(RedisConnectionError):
        asyncio.run(service.run(record.id))
    assert _current_fake_contents(service.deps.weaviate) == {"A"}
    assert not service.store._publication_guards
    assert asyncio.run(service.run(second.id)).status == "completed"
    assert _current_fake_contents(service.deps.weaviate) == {"B"}


def test_collection_busy_does_not_hide_foreign_execution_lease(tmp_path, monkeypatch):
    service = _uncertain_publication_service(tmp_path, monkeypatch, "timeout")
    record, _ = service.submit("showcase-default")

    def replace_execution(ingestion_id, claim):
        assert service.store.claim_execution(
            ingestion_id, ExecutionClaim("new-owner", 60, claim.execution_owner),
        )
        latest = service.store.get(ingestion_id)
        latest.counts["new-owner-sentinel"] = 42
        assert service.store.save_claimed(latest, "new-owner")
        return False

    monkeypatch.setattr(service.store, "claim_publications", replace_execution)
    with pytest.raises(IngestionExecutionLeaseLost):
        asyncio.run(service.run(record.id, execution_owner="old-owner"))
    assert service.store.get(record.id).counts["new-owner-sentinel"] == 42
    assert not service.store.claim_execution(record.id, ExecutionClaim("contender", 60))
    assert service.store.release_execution(record.id, "new-owner")


def test_sync_collection_busy_stays_pending_and_resubmits_the_same_job(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from rag_ingestion.models import IngestionRecord
    from rag_ingestion.store import ExecutionClaim, PublicationClaim

    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    record, _ = service.submit("showcase-default")
    holder = IngestionRecord.from_dict(record.to_dict())
    holder.id, holder.idempotency_key = "other-physical-writer", "other-corpus-key"
    assert service.store.create_if_absent(holder)[1]
    claim = PublicationClaim("other-owner", "other-attempt")
    assert service.store.claim_execution(holder.id, ExecutionClaim(claim.execution_owner, 60))
    assert service.store.claim_publications(holder.id, claim)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)
    client = TestClient(main.app)
    request = {"profile": "showcase-default"}
    blocked = client.post("/api/rag/ingestions", json=request)
    repeated = client.post("/api/rag/ingestions", json=request)
    assert (blocked.status_code, blocked.json()["status"], repeated.json()["status"]) == (202, "pending", "pending")
    assert blocked.json()["ingestion_id"] == repeated.json()["ingestion_id"] == record.id
    assert blocked.json()["message"] == "RAG ingestion is waiting for collection publication; resubmit to retry."
    assert service.store.get(record.id).phase("vector_write").note == (
        "waiting for collection publication; another job holds the class"
    )
    # This holder never began a mutation; its normal clean release is sufficient.
    service.store.release_publications(holder.id, claim, holder.publication_targets)
    assert service.store.release_execution(holder.id, claim.execution_owner)
    finished = client.post("/api/rag/ingestions", json=request)
    assert (finished.status_code, finished.json()["ingestion_id"], finished.json()["status"]) == (202, record.id, "completed")


def _hold_publication_guard(service, record):
    """Another active attempt reserves the record's physical classes."""
    holder = IngestionRecord.from_dict(record.to_dict())
    holder.id, holder.idempotency_key = str(uuid.uuid4()), str(uuid.uuid4())
    assert service.store.create_if_absent(holder)[1]
    claim = PublicationClaim("holder-owner", str(uuid.uuid4()))
    assert service.store.claim_execution(holder.id, ExecutionClaim(claim.execution_owner, 60))
    assert service.store.claim_publications(holder.id, claim)
    return holder, claim


class _HeldDrainLightrag(FakeLightrag):
    """Keeps a job in drain until the test releases it."""
    def __init__(self):
        super().__init__()
        self.draining = asyncio.Event()
        self.release = asyncio.Event()

    async def pipeline_busy(self):
        self.draining.set()
        return not self.release.is_set()


def test_clean_publication_guards_are_released_when_the_vector_phase_ends(tmp_path, monkeypatch):
    service = _uncertain_publication_service(tmp_path, monkeypatch, "timeout")
    path = Path(service._profiles_path)
    document = json.loads(path.read_text())
    document["profiles"][0]["graph_targets"][0]["timeout_seconds"] = 30
    path.write_text(json.dumps(document))
    weaviate = service.deps.weaviate = FakeWeaviate()
    held = service.deps.lightrag = _HeldDrainLightrag()
    service.deps.poll_interval = 0.01
    other = RagIngestionService(store=service.store, profiles_path=service._profiles_path, deps=Deps(
        embedder=FakeEmbedder(), weaviate=weaviate, lightrag=FakeLightrag(available=False),
    ))
    first, _ = service.submit("showcase-default")
    second, _ = other.submit("showcase-default", corpus_path="other")

    async def scenario():
        task = asyncio.create_task(service.run(first.id))
        try:
            await asyncio.wait_for(held.draining.wait(), 5)
            assert not service.store._publication_guards, "a job in drain must not hold its collection"
            assert (await other.run(second.id)).status == "completed"
            assert _current_fake_contents(weaviate) == {"B"}
        finally:
            held.release.set()
        return await asyncio.wait_for(task, 5)
    assert asyncio.run(scenario()).status == "completed"
    assert not service.store._publication_guards


@pytest.mark.parametrize("then", ["admitted", "cancelled"])
def test_guarded_collection_turns_a_job_away_before_parse_and_embed(tmp_path, monkeypatch, then):
    from rag_ingestion.service import CollectionPublicationBusy

    service = _uncertain_publication_service(tmp_path, monkeypatch, "timeout")
    service.deps.weaviate = FakeWeaviate()
    embedded = []
    real_embed = service.deps.embedder.embed

    async def counting_embed(texts):
        embedded.append(list(texts))
        return await real_embed(texts)

    service.deps.embedder.embed = counting_embed
    record, _ = service.submit("showcase-default")
    holder, claim = _hold_publication_guard(service, record)
    with pytest.raises(CollectionPublicationBusy):
        asyncio.run(service.run(record.id))
    blocked = service.store.get(record.id)
    assert (blocked.status, blocked.phase("discover").status, blocked.counts["files_discovered"]) == ("pending", "pending", 0)
    assert (blocked.phase("vector_write").status, blocked.phase("vector_write").note) == (
        "pending", "waiting for collection publication; another job holds the class",
    )
    assert not embedded, "a guarded collection must refuse the job before any embedding"
    if then == "cancelled":
        # A waiting job stays cancellable: cancellation is checked before admission.
        assert service.store.request_cancel(record.id)
        assert asyncio.run(service.run(record.id)).status == "cancelled"
        assert not embedded
        return
    service.store.release_publications(holder.id, claim, holder.publication_targets)
    assert asyncio.run(service.run(record.id)).status == "completed"
    assert embedded


@pytest.mark.parametrize("disabled", ["weaviate", "embedder"])
def test_guarded_collection_does_not_hold_back_a_job_without_weaviate_or_embedder(
    tmp_path, monkeypatch, disabled,
):
    service = _uncertain_publication_service(tmp_path, monkeypatch, "timeout")
    service.deps.weaviate = FakeWeaviate(available=disabled != "weaviate")
    service.deps.embedder = FakeEmbedder(available=disabled != "embedder")
    record, _ = service.submit("showcase-default")
    _hold_publication_guard(service, record)
    finished = asyncio.run(service.run(record.id))
    # vector_write reports the unavailable target as before; the guard is untouched.
    assert finished.status != "pending"
    assert "waiting for collection publication" not in (finished.phase("vector_write").note or "")
    assert service.deps.weaviate.classes == []


def test_collection_wait_backs_off_without_consuming_retry_budgets(monkeypatch):
    import celery_tasks
    import rag_ingestion
    from rag_ingestion.service import CollectionPublicationBusy

    class RetryScheduled(RuntimeError):
        pass

    captured, backoffs = [], []

    def capture_retry(**kwargs):
        captured.append(kwargs)
        raise RetryScheduled()

    def backoff(**kwargs):
        backoffs.append(kwargs)
        return 7

    monkeypatch.setenv("RAG_INGESTION_EXECUTION_LEASE_SECONDS", "45")
    monkeypatch.setattr(celery_tasks, "get_exponential_backoff_interval", backoff)
    monkeypatch.setattr(celery_tasks.rag_ingestion_task, "retry", capture_retry)
    for busy, waited in ((CollectionPublicationBusy, 0), (CollectionPublicationBusy, 30),
                         (rag_ingestion.IngestionExecutionBusy, 3)):
        def refuse(*_args, _busy=busy, **_kwargs):
            raise _busy("not admitted")

        monkeypatch.setattr(rag_ingestion, "run_rag_ingestion", refuse)
        with pytest.raises(RetryScheduled):
            celery_tasks.rag_ingestion_task.run("ingestion-1", retry_state={
                "phase_attempt": 2, "infrastructure_attempt": 5,
                "recovery_owner": "stale-owner", "publication_wait_attempt": waited,
            })
    budgets = {"phase_attempt": 2, "infrastructure_attempt": 5}
    assert [c["kwargs"]["retry_state"] for c in captured] == [
        {**budgets, "publication_wait_attempt": 1},
        {**budgets, "publication_wait_attempt": 31},
        {**budgets, "publication_wait_attempt": 3},
    ]
    assert [c["countdown"] for c in captured] == [7, 7, 45]
    assert backoffs == [
        {"factor": 1, "retries": 0, "maximum": 600, "full_jitter": True},
        {"factor": 1, "retries": 10, "maximum": 600, "full_jitter": True},
    ]
    with pytest.raises(ValueError, match="retry_state.publication_wait_attempt"):
        celery_tasks.rag_ingestion_task.run("ingestion-1", retry_state={"publication_wait_attempt": -1})


class _RejectingWeaviate(FakeWeaviate):
    """Answers the next class check with an HTTP error status."""
    def __init__(self):
        super().__init__()
        self.reject = None

    async def ensure_class(self, class_name, embedding=None):
        status, self.reject = self.reject, None
        if status is not None:
            request = httpx.Request("POST", "http://weaviate:8080/v1/schema")
            response = httpx.Response(status, request=request, text="rejected")
            raise httpx.HTTPStatusError(f"schema rejected with {status}", request=request, response=response)
        return await super().ensure_class(class_name, embedding)


def _rejected_publication(tmp_path, monkeypatch, corpus_path, status):
    """Publish corpus A, then let Weaviate reject the next job's class check."""
    service = _uncertain_publication_service(tmp_path, monkeypatch, "timeout")
    (tmp_path / "corpus-root" / "empty").mkdir()
    weaviate = service.deps.weaviate = _RejectingWeaviate()
    previous, _, final = _run(service)
    assert final.status == "completed"
    weaviate.reject = status
    _, _, final = _run(service, corpus_path=corpus_path)
    assert (final.status, final.phase("vector_write").status) == ("failed", "failed")
    assert str(status) in json.dumps(final.errors)
    # Either way the rejected attempt invalidated the earlier publication proof.
    restored, created = service.submit("showcase-default")
    assert created and restored.id != previous.id
    return service, restored


@pytest.mark.parametrize("corpus_path", ["other", "empty"])
@pytest.mark.parametrize("status", [422, 400])
def test_definitive_weaviate_rejection_settles_the_guard_but_not_publication(
    tmp_path, monkeypatch, corpus_path, status,
):
    service, restored = _rejected_publication(tmp_path, monkeypatch, corpus_path, status)
    # Weaviate answered, so nothing is in flight: the class is free again.
    assert not service.store._publication_guards
    assert asyncio.run(service.run(restored.id)).status == "completed"
    assert _current_fake_contents(service.deps.weaviate) == {"A"}


@pytest.mark.parametrize("corpus_path", ["other", "empty"])
def test_weaviate_server_error_keeps_the_guard_dirty(tmp_path, monkeypatch, corpus_path):
    from rag_ingestion.models import weaviate_class_name
    from rag_ingestion.service import CollectionPublicationBusy
    from rag_ingestion.store import publication_target

    service, restored = _rejected_publication(tmp_path, monkeypatch, corpus_path, 503)
    target = publication_target(weaviate_class_name("RagShowcase", "showcase-default"))
    assert _publication_guard_value(service.store, target).endswith(":dirty")
    with pytest.raises(CollectionPublicationBusy):
        asyncio.run(service.run(restored.id))
    assert service.store.get(restored.id).phase("vector_write").note == (
        "waiting for collection publication; an interrupted write requires operator recovery"
    )


@pytest.mark.parametrize("live_redis", [False, True])
def test_publication_guard_probe_is_read_only_and_exact(live_redis):
    store = _ingestion_regression_store(live_redis)
    record, claim = _reserved_publication(store)
    target, free = record.publication_targets[0], str(uuid.uuid4())
    probes = [store.publication_guarded(targets) for targets in ([], [free], [free, target])]
    assert probes == [None, None, "clean"]
    assert _publication_guard_value(store, target).endswith(":clean"), "the probe never claims"
    assert store.mark_publication(record.id, claim, (target, False))
    assert store.publication_guarded([free, target]) == "dirty"
    assert store.settle_publication(record.id, claim, target)
    store.release_publications(record.id, claim, [target])
    assert store.publication_guarded([target]) is None


@pytest.mark.parametrize("live_redis", [False, True])
def test_rejection_settle_is_attempt_exact_and_keeps_reuse_invalid(live_redis):
    store = _ingestion_regression_store(live_redis)
    previous = _generation_record()
    target = previous.publication_targets[0]
    _publish_for_test(store, previous)
    record, claim = _reserved_publication(store, [target])
    assert not store.settle_publication(record.id, claim, target), "only a begun mutation settles"
    assert store.mark_publication(record.id, claim, (target, False))
    dirty = _publication_guard_value(store, target)
    refused = [store.settle_publication(record.id, *attempt) for attempt in (
        (PublicationClaim(claim.execution_owner, "other-attempt"), target), (claim, str(uuid.uuid4())),
    )]
    assert (refused, _publication_guard_value(store, target)) == ([False, False], dirty)
    assert store.settle_publication(record.id, claim, target)
    assert _publication_guard_value(store, target) == dirty[:-len(":dirty")] + ":clean"
    assert not store.mark_publication(record.id, claim, (target, True)), "a rejection never proves publication"
    store.release_publications(record.id, claim, [target])
    assert store.create_if_absent(_pending_copy(previous))[1], "the rejected attempt invalidated reuse"


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("boundary", ["lease-expired", "lease-replaced"])
def test_rejection_settle_requires_the_live_execution_lease(live_redis, boundary):
    store = _ingestion_regression_store(live_redis)
    record, claim = _reserved_publication(store)
    target = record.publication_targets[0]
    assert store.mark_publication(record.id, claim, (target, False))
    dirty = _publication_guard_value(store, target)
    _lose_publication_ownership(store, record, boundary)
    assert not store.settle_publication(record.id, claim, target)
    assert _publication_guard_value(store, target) == dirty


# Runtime destination identity is separate from the physical collection mutex.
def _runtime_destination_deps(endpoints):
    vector_url, graph_url = endpoints
    weaviate = FakeWeaviate(available=bool(vector_url))
    weaviate.endpoint = vector_url
    graph = FakeLightrag(available=bool(graph_url))
    graph.endpoint = graph_url
    return Deps(embedder=FakeEmbedder(), weaviate=weaviate, lightrag=graph)


def _runtime_context_service(tmp_path, monkeypatch, targets=(True, True)):
    _corpus(tmp_path, monkeypatch, {"a.txt": "trusted destination identity control"})
    monkeypatch.setattr("chunking_service.chunk_text", lambda request: SimpleNamespace(
        chunks=[SimpleNamespace(index=0, content=request.text)],
    ))
    profile_path = _profiles_file(
        tmp_path, vector=None if targets[0] else [], graph=None if targets[1] else [],
    )
    document = json.loads(Path(profile_path).read_text())
    document["profiles"][0]["revision"] = str(tmp_path)
    Path(profile_path).write_text(json.dumps(document))
    return _service(tmp_path, _runtime_destination_deps(("http://weaviate:8080", "http://lightrag:9621")), profile_path)


def _with_runtime_destinations(service, endpoints):
    return RagIngestionService(
        store=service.store, deps=_runtime_destination_deps(endpoints),
        profiles_path=service._profiles_path,
    )


def test_runtime_destination_identity_reads_the_real_client_endpoints():
    from rag_ingestion.clients import ingestion_runtime_context

    profile = SimpleNamespace(vector_targets=[{}], graph_targets=[{}])
    context = ingestion_runtime_context(
        profile,
        WeaviateClient(url="http://user:secret@weaviate:8080/base/?key=x#frag"),
        LightRagClient(endpoint=""),
    )
    assert context == [("weaviate", "http://weaviate:8080/base", True), ("lightrag", "", False)]


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("case", [
    ((True, False), ("http://weaviate:8080", ""), ("http://host.docker.internal:8080", "")),
    ((False, True), ("", "http://lightrag:9621"), ("", "http://host.docker.internal:63068")),
    ((False, True), ("", ""), ("", "http://lightrag:9621")),
    ((True, True), ("http://weaviate:8080", ""), ("http://weaviate:8080", "http://lightrag:9621")),
])
def test_runtime_destination_change_ingests_the_current_target(tmp_path, monkeypatch, case, live_redis):
    targets, before, after = case
    service = _runtime_context_service(tmp_path, monkeypatch, targets)
    service.store = _ingestion_regression_store(live_redis)
    previous = _with_runtime_destinations(service, before)
    first, _, final = _run(previous)
    assert final.status == "completed"
    preserved = service.store.get(first.id).to_dict()
    current = _with_runtime_destinations(service, after)
    second, created = current.submit("showcase-default")
    assert created and second.id != first.id, "historical completion cannot populate a new destination"
    completed = asyncio.run(current.run(second.id))
    assert completed.status == "completed"
    if targets[0]:
        assert _current_fake_contents(current.deps.weaviate) == {"trusted destination identity control"}
    if targets[1]:
        assert current.deps.lightrag.uploaded == [{"text": "trusted destination identity control", "source": "docs/a.txt"}]
    assert service.store.get(first.id).to_dict() == preserved


@pytest.mark.parametrize("case", [
    ((True, True), ("http://weaviate:8080", "http://lightrag:9621")),
    ((True, False), ("http://weaviate:8080", "http://unrelated-graph:63068")),
    ((False, True), ("http://unrelated-vector:8080", "http://lightrag:9621")),
])
def test_unchanged_declared_runtime_destinations_still_deduplicate(tmp_path, monkeypatch, case):
    targets, endpoints = case
    service = _runtime_context_service(tmp_path, monkeypatch, targets)
    first, _, completed = _run(service)
    current = _with_runtime_destinations(service, endpoints)
    repeated, created = current.submit("showcase-default")
    assert not created and repeated.id == first.id
    assert repeated.to_dict() == completed.to_dict()
    assert current.deps.weaviate.written == [] and current.deps.lightrag.uploaded == []


@pytest.mark.parametrize("live_redis", [False, True])
@pytest.mark.parametrize("status", ["pending", "running"])
def test_pending_ingestion_runtime_destinations_have_separate_identity(tmp_path, monkeypatch, live_redis, status):
    service = _runtime_context_service(tmp_path, monkeypatch, (True, False))
    service.store = _ingestion_regression_store(live_redis)
    first, _ = service.submit("showcase-default")
    first.status = status
    service.store.save(first)
    current = _with_runtime_destinations(service, ("http://host.docker.internal:8080", ""))
    second, created = current.submit("showcase-default")
    assert created and second.id != first.id
    assert service.submit("showcase-default")[0].id == first.id
    assert current.submit("showcase-default")[0].id == second.id
    assert first.status == status and second.status == "pending"


@pytest.mark.parametrize("live_redis", [False, True])
def test_runtime_destination_drift_invalidates_submitted_dedup(tmp_path, monkeypatch, live_redis):
    service = _runtime_context_service(tmp_path, monkeypatch, (True, False))
    service.store = _ingestion_regression_store(live_redis)
    first, _ = service.submit("showcase-default")
    service.deps = _runtime_destination_deps(("http://host.docker.internal:8080", ""))
    completed = asyncio.run(service.run(first.id))
    assert completed.status == "completed"
    assert completed.counts.get("dedup_invalidated") == 1
    assert _current_fake_contents(service.deps.weaviate) == {"trusted destination identity control"}
    replacement, created = service.submit("showcase-default")
    assert created and replacement.id != first.id


@pytest.mark.parametrize("live_redis", [False, True])
def test_context_free_completed_ingestion_is_conservatively_refreshed(tmp_path, monkeypatch, live_redis):
    service = _runtime_context_service(tmp_path, monkeypatch, (True, False))
    old_record, _ = service.submit("showcase-default")
    profile = service._resolve_profile("showcase-default")
    fingerprint = service.deps.corpus.fingerprint(profile.corpus, profile.corpus.get("path"))
    old_record.idempotency_key = ingestion_idempotency_key(profile, old_record.corpus, fingerprint)
    service.store = _ingestion_regression_store(live_redis)
    _publish_for_test(service.store, old_record)
    preserved = service.store.get(old_record.id).to_dict()
    current, created = service.submit("showcase-default")
    assert created and current.id != old_record.id
    assert service.store.get(old_record.id).to_dict() == preserved


@pytest.mark.parametrize("changed", [
    ("http://weaviate:8081", "http://lightrag:9621"),
    ("http://weaviate:8080/other", "http://lightrag:9621"),
    ("http://weaviate:8080", "http://lightrag:9622"),
    ("http://weaviate:8080", "http://lightrag:9621/other"),
])
def test_runtime_destination_path_and_port_are_significant(tmp_path, monkeypatch, changed):
    service = _runtime_context_service(tmp_path, monkeypatch)
    first, _ = service.submit("showcase-default")
    current = _with_runtime_destinations(service, changed)
    second, created = current.submit("showcase-default")
    assert created and second.id != first.id


def test_runtime_destination_identity_excludes_auth_material(tmp_path, monkeypatch):
    service = _runtime_context_service(tmp_path, monkeypatch)
    before = ("http://alice:password-one@weaviate:8080/base?token=query-one#fragment-one",
              "http://bob:password-two@lightrag:9621/base?key=query-two#fragment-two")
    after = ("http://weaviate:8080/base/", "http://lightrag:9621/base/")
    first, _ = _with_runtime_destinations(service, before).submit("showcase-default")
    repeated, created = _with_runtime_destinations(service, after).submit("showcase-default")
    assert not created and repeated.id == first.id
    serialized = json.dumps(first.to_dict())
    for secret in ("alice", "bob", "password-one", "password-two", "query-one", "query-two", "fragment-one", "fragment-two"):
        assert secret not in serialized


def test_runtime_destination_configured_availability_is_significant(tmp_path, monkeypatch):
    service = _runtime_context_service(tmp_path, monkeypatch, (False, True))
    service.deps.lightrag._available = False
    first, _, completed = _run(service)
    assert completed.phase("lightrag_upload").status == "skipped"
    service.deps.lightrag._available = True  # Same URL, injected capability changed.
    second, created = service.submit("showcase-default")
    assert created and second.id != first.id
