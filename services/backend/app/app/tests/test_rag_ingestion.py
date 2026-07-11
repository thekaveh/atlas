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
from pathlib import Path

import pytest

from rag_ingestion.clients import CorpusPathError, MountCorpusReader
from rag_ingestion.profiles import ProfileNotFoundError, load_profiles
from rag_ingestion.service import Deps, RagIngestionService
from rag_ingestion.store import InMemoryIngestionStore


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


class RaisingExtractor:
    """Stands in for a reachable-but-failing Docling/Tika endpoint."""
    async def extract(self, content, filename=None, content_type=None):
        raise RuntimeError("docling exploded")


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


def test_idempotent_resubmit_dedups(tmp_path, monkeypatch):
    _corpus(tmp_path, monkeypatch, {"a.txt": "hello world"})
    pf = _profiles_file(tmp_path)
    svc = _service(tmp_path, Deps(embedder=FakeEmbedder(), weaviate=FakeWeaviate(), lightrag=FakeLightrag(), poll_interval=0.01), pf)
    first, created1 = svc.submit("showcase-default")
    asyncio.run(svc.run(first.id))
    second, created2 = svc.submit("showcase-default")
    assert created1 is True and created2 is False
    assert second.id == first.id


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


def test_corpus_path_safety_rejects_escape(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("RAG_INGESTION_CORPUS_ROOT", str(root))
    reader = MountCorpusReader()
    for bad in ("../escape", "/etc/passwd", "~/secrets"):
        with pytest.raises(CorpusPathError):
            reader.discover({"source": "mount", "path": bad})


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
