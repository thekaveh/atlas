"""FastAPI surface for the RAG ingestion job contract (#413).

Exercises the endpoints through TestClient with a fake-backed service so no live
upstream is needed: submit (sync path + async dispatch + 404 + dedup), status,
list, and cancel.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path


def _stub_required_env(monkeypatch):
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def _reload_main(monkeypatch):
    _stub_required_env(monkeypatch)
    if "main" in sys.modules:
        return importlib.reload(sys.modules["main"])
    import main  # type: ignore[import]

    return main


class _FakeEmbedder:
    def available(self):
        return True

    async def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeWeaviate:
    def available(self):
        return True

    async def ensure_class(self, class_name):
        return None

    async def write_objects(self, class_name, objects):
        return len(objects)


class _FakeLightrag:
    def available(self):
        return True

    async def upload(self, documents):
        return len(documents)

    async def pipeline_busy(self):
        return False


def _fake_service(tmp_path: Path, monkeypatch):
    from rag_ingestion.service import Deps, RagIngestionService
    from rag_ingestion.store import InMemoryIngestionStore

    root = tmp_path / "corpus-root"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_text("hello ingestion world", encoding="utf-8")
    monkeypatch.setenv("RAG_INGESTION_CORPUS_ROOT", str(root))

    profile = {
        "consumer": "rag-showcase",
        "name": "showcase-default",
        "revision": "rev1",
        "corpus": {"source": "mount", "path": "docs"},
        "parser_order": ["plain_text"],
        "chunker": {"strategy": "recursive", "chunk_size": 64, "overlap": 8},
        "vector_targets": [{"backend": "weaviate", "collection_prefix": "RagShowcase", "on_unavailable": "skip"}],
        "graph_targets": [{"backend": "lightrag", "mode": "upload_documents", "wait_for_extraction": True, "timeout_seconds": 1, "on_unavailable": "skip"}],
    }
    pf = tmp_path / "profiles.json"
    pf.write_text(json.dumps({"version": 1, "profiles": [profile]}), encoding="utf-8")

    deps = Deps(embedder=_FakeEmbedder(), weaviate=_FakeWeaviate(), lightrag=_FakeLightrag(), poll_interval=0.01)
    return RagIngestionService(store=InMemoryIngestionStore(), deps=deps, profiles_path=str(pf))


def test_submit_sync_path_runs_and_returns_status(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)  # force sync fallback

    client = TestClient(main.app)
    resp = client.post("/api/rag/ingestions", json={"profile": "showcase-default"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    ingestion_id = body["ingestion_id"]

    got = client.get(f"/api/rag/ingestions/{ingestion_id}")
    assert got.status_code == 200
    assert got.json()["counts"]["documents_parsed"] == 1


def test_submit_unknown_profile_returns_404(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)

    client = TestClient(main.app)
    resp = client.post("/api/rag/ingestions", json={"profile": "nope"})
    assert resp.status_code == 404


def test_submit_dedups_identical_request(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)

    client = TestClient(main.app)
    first = client.post("/api/rag/ingestions", json={"profile": "showcase-default"}).json()
    second = client.post("/api/rag/ingestions", json={"profile": "showcase-default"}).json()
    assert second["ingestion_id"] == first["ingestion_id"]
    assert "Idempotent" in second["message"]


def test_async_dispatch_queues_celery_task(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)

    class FakeAsyncResult:
        id = "celery-rag-1"

    called = {}

    def fake_apply_async(*, kwargs):
        called["kwargs"] = kwargs
        return FakeAsyncResult()

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(main.rag_ingestion_task, "apply_async", fake_apply_async)
    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)

    client = TestClient(main.app)
    resp = client.post("/api/rag/ingestions?async_job=true", json={"profile": "showcase-default"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"] == "celery-rag-1"
    assert body["status"] == "pending"
    assert called["kwargs"]["ingestion_id"] == body["ingestion_id"]


def test_list_and_cancel(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)

    # Seed a pending record without running it (so cancel is meaningful).
    record, _ = service.submit("showcase-default")

    client = TestClient(main.app)
    listing = client.get("/api/rag/ingestions")
    assert listing.status_code == 200
    assert any(r["id"] == record.id for r in listing.json())

    cancelled = client.post(f"/api/rag/ingestions/{record.id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancel_requested"] is True

    missing = client.post("/api/rag/ingestions/does-not-exist/cancel")
    assert missing.status_code == 404


def test_get_unknown_ingestion_returns_404(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)

    client = TestClient(main.app)
    assert client.get("/api/rag/ingestions/nope").status_code == 404
