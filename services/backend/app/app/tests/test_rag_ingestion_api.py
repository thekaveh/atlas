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
import threading
from pathlib import Path

import pytest


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

    async def reconcile_objects(
        self, class_name, profile_name, desired_ids, preserve_sources=None
    ):
        return 0


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
    assert got.json()["corpus"] == {"source": "mount", "path": "docs"}
    assert got.json()["profile_snapshot"]["revision"] == "rev1"


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

    def fake_apply_async(*, kwargs, task_id):
        called["kwargs"] = kwargs
        called["task_id"] = task_id
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
    assert called["task_id"] == f"rag-ingestion-{body['ingestion_id']}"


def test_async_dispatch_failure_releases_idempotency_key_for_retry(
    tmp_path, monkeypatch
):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)

    def fail_dispatch(*, kwargs, task_id):
        raise RuntimeError("broker unavailable")

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(main.rag_ingestion_task, "apply_async", fail_dispatch)
    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)
    client = TestClient(main.app)

    failed = client.post(
        "/api/rag/ingestions?async_job=true",
        json={"profile": "showcase-default"},
    )

    assert failed.status_code == 503
    failed_record = service.store.list()[0]
    assert failed_record.status == "failed"
    assert failed_record.errors[-1]["phase"] == "dispatch"

    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)
    retried = client.post(
        "/api/rag/ingestions?async_job=false",
        json={"profile": "showcase-default"},
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "completed"
    assert retried.json()["ingestion_id"] != failed_record.id


def test_dispatch_cleanup_failure_leaves_pending_job_retryable(
    tmp_path, monkeypatch
):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    dispatches = []

    def dispatch(*, kwargs, task_id):
        dispatches.append(kwargs["ingestion_id"])
        if len(dispatches) == 1:
            raise RuntimeError("broker unavailable")
        return type("FakeAsyncResult", (), {"id": "celery-rag-retry"})()

    monkeypatch.setattr(main.rag_ingestion_task, "apply_async", dispatch)
    monkeypatch.setattr(
        service,
        "mark_dispatch_failed",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("store unavailable")),
    )

    async def scenario():
        request = main.RagIngestionRequest(profile="showcase-default")
        with pytest.raises(RuntimeError, match="store unavailable"):
            await main.submit_rag_ingestion(request, True)
        pending = service.store.list()[0]
        pending.dispatch_claimed_at = "2000-01-01T00:00:00+00:00"
        service.store.save(pending)
        response = await main.submit_rag_ingestion(request, True)
        return pending, response

    pending, response = asyncio.run(scenario())

    assert pending.status == "pending"
    assert response.ingestion_id == pending.id
    assert response.job_id == "celery-rag-retry"
    assert dispatches == [pending.id, pending.id]
    stored = service.store.get(pending.id)
    assert stored.dispatch_state == "accepted"
    assert stored.dispatch_job_id == "celery-rag-retry"


def test_existing_accepted_pending_async_job_is_not_redispatched(
    tmp_path, monkeypatch
):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    record, created = service.submit("showcase-default")
    assert created is True
    assert service.claim_dispatch(record.id, "owner") is True
    service.mark_dispatched(record.id, "celery-existing", "owner")
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    monkeypatch.setattr(
        main.rag_ingestion_task,
        "apply_async",
        lambda **_kwargs: pytest.fail("accepted job must not be redispatched"),
    )

    response = asyncio.run(
        main.submit_rag_ingestion(
            main.RagIngestionRequest(profile="showcase-default"), True
        )
    )

    assert response.ingestion_id == record.id
    assert response.job_id == "celery-existing"
    assert response.status == "pending"


def test_existing_running_sync_job_is_recovered_after_lease_loss(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    record, created = service.submit("showcase-default")
    assert created is True
    record.status = "running"
    service.store.save(record)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)
    completed = service.store.get(record.id)
    completed.status = "completed"

    async def recover(*_args):
        service.store.save(completed)
        return completed

    monkeypatch.setattr(service, "run", recover)

    response = asyncio.run(
        main.submit_rag_ingestion(
            main.RagIngestionRequest(profile="showcase-default"), True
        )
    )

    assert response.ingestion_id == record.id
    assert response.job_id is None
    assert response.status == "completed"


def test_existing_dispatching_job_recovers_synchronously_when_celery_is_disabled(
    tmp_path, monkeypatch
):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    record, created = service.submit("showcase-default")
    assert created is True
    assert service.claim_dispatch(record.id, "abandoned-owner") is True
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)

    async def complete(ingestion_id):
        recovered = service.store.get(ingestion_id)
        recovered.status = "completed"
        service.store.save(recovered)
        return recovered

    monkeypatch.setattr(service, "run", complete)
    response = asyncio.run(
        main.submit_rag_ingestion(
            main.RagIngestionRequest(profile="showcase-default"), True
        )
    )

    assert response.ingestion_id == record.id
    assert response.status == "completed"


def test_stale_dispatch_owner_cannot_overwrite_reclaimed_claim(
    tmp_path, monkeypatch
):
    service = _fake_service(tmp_path, monkeypatch)
    record, created = service.submit("showcase-default")
    assert created is True
    assert service.claim_dispatch(record.id, "owner-a") is True
    stale = service.store.get(record.id)
    stale.dispatch_claimed_at = "2000-01-01T00:00:00+00:00"
    service.store.save(stale)
    assert service.claim_dispatch(record.id, "owner-b") is True

    service.mark_dispatched(record.id, "job-a", "owner-a")
    service.mark_dispatch_failed(record.id, "stale failure", "owner-a")
    fenced = service.store.get(record.id)
    assert (
        fenced.status,
        fenced.dispatch_state,
        fenced.dispatch_owner,
        fenced.dispatch_job_id,
    ) == ("pending", "dispatching", "owner-b", None)

    service.mark_dispatched(record.id, "job-b", "owner-b")
    accepted = service.store.get(record.id)
    assert (
        accepted.dispatch_state,
        accepted.dispatch_owner,
        accepted.dispatch_job_id,
    ) == ("accepted", None, "job-b")


def test_broker_acceptance_is_not_reclassified_when_dispatch_state_write_fails(
    tmp_path, monkeypatch
):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    monkeypatch.setattr(
        main.rag_ingestion_task,
        "apply_async",
        lambda **_kwargs: type("Result", (), {"id": "celery-accepted"})(),
    )
    cleanup_calls = []
    monkeypatch.setattr(
        service,
        "mark_dispatched",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("store unavailable")),
    )
    monkeypatch.setattr(
        service,
        "mark_dispatch_failed",
        lambda *_args: cleanup_calls.append(_args),
    )

    with pytest.raises(main.HTTPException) as exc_info:
        asyncio.run(
            main.submit_rag_ingestion(
                main.RagIngestionRequest(profile="showcase-default"), True
            )
        )

    assert exc_info.value.status_code == 503
    assert cleanup_calls == []
    record = service.store.list()[0]
    assert record.status == "pending"
    assert record.dispatch_state == "dispatching"
    response = asyncio.run(
        main.submit_rag_ingestion(
            main.RagIngestionRequest(profile="showcase-default"), True
        )
    )
    assert response.ingestion_id == record.id
    assert response.job_id is None


def test_cancelled_submit_releases_new_idempotency_record(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    started = threading.Event()
    release = threading.Event()
    original_submit = service.submit

    def blocked_submit(*args, **kwargs):
        result = original_submit(*args, **kwargs)
        started.set()
        assert release.wait(timeout=5)
        return result

    service.submit = blocked_submit

    async def scenario():
        request = main.RagIngestionRequest(profile="showcase-default")
        submission = asyncio.create_task(main.submit_rag_ingestion(request, True))
        assert await asyncio.to_thread(started.wait, 5)
        submission.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())

    failed = service.store.list()[0]
    assert failed.status == "failed"
    assert failed.errors[-1]["phase"] == "dispatch"
    service.submit = original_submit
    record, created = service.submit("showcase-default")
    assert created is True
    assert record.id != failed.id


def test_cancelled_dispatch_waits_for_broker_acceptance(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    started = threading.Event()
    release = threading.Event()
    calls = []

    class FakeAsyncResult:
        id = "celery-rag-cancelled"

    def blocked_dispatch(*, kwargs, task_id):
        calls.append(kwargs["ingestion_id"])
        started.set()
        assert release.wait(timeout=5)
        return FakeAsyncResult()

    monkeypatch.setattr(main.rag_ingestion_task, "apply_async", blocked_dispatch)

    async def scenario():
        request = main.RagIngestionRequest(profile="showcase-default")
        submission = asyncio.create_task(main.submit_rag_ingestion(request, True))
        assert await asyncio.to_thread(started.wait, 5)
        submission.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())

    record = service.store.list()[0]
    assert record.status == "pending"
    assert record.dispatch_state == "accepted"
    assert record.dispatch_job_id == "celery-rag-cancelled"
    assert calls == [record.id]
    response = asyncio.run(
        main.submit_rag_ingestion(
            main.RagIngestionRequest(profile="showcase-default"), True
        )
    )
    assert response.ingestion_id == record.id
    assert response.job_id == "celery-rag-cancelled"
    assert calls == [record.id]


def test_cancelled_dispatch_claim_is_reconciled_before_broker_publish(
    tmp_path, monkeypatch
):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    started = threading.Event()
    release = threading.Event()
    original_claim = service.claim_dispatch

    def blocked_claim(*args):
        claimed = original_claim(*args)
        started.set()
        assert release.wait(timeout=5)
        return claimed

    service.claim_dispatch = blocked_claim
    monkeypatch.setattr(
        main.rag_ingestion_task,
        "apply_async",
        lambda **_kwargs: pytest.fail("cancelled claim must not publish"),
    )

    async def scenario():
        submission = asyncio.create_task(
            main.submit_rag_ingestion(
                main.RagIngestionRequest(profile="showcase-default"), True
            )
        )
        assert await asyncio.to_thread(started.wait, 5)
        submission.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())
    record = service.store.list()[0]
    assert record.status == "failed"
    assert record.errors[-1]["phase"] == "dispatch"


def test_concurrent_identical_async_submissions_publish_once(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    dispatches = []
    started = threading.Event()
    release = threading.Event()

    def dispatch(*, kwargs, task_id):
        dispatches.append((kwargs["ingestion_id"], task_id))
        started.set()
        assert release.wait(timeout=5)
        return type("Result", (), {"id": task_id})()

    monkeypatch.setattr(main.rag_ingestion_task, "apply_async", dispatch)

    async def scenario():
        request = main.RagIngestionRequest(profile="showcase-default")
        first = asyncio.create_task(main.submit_rag_ingestion(request, True))
        assert await asyncio.to_thread(started.wait, 5)
        second = await main.submit_rag_ingestion(request, True)
        release.set()
        return await first, second

    first, second = asyncio.run(scenario())
    assert first.ingestion_id == second.ingestion_id
    assert dispatches == [
        (first.ingestion_id, f"rag-ingestion-{first.ingestion_id}")
    ]


def test_cancelled_sync_submission_finishes_owned_run_before_reraising(
    tmp_path, monkeypatch
):
    main = _reload_main(monkeypatch)
    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    monkeypatch.setattr(main, "celery_is_enabled", lambda: False)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    async def blocked_run(ingestion_id):
        started.set()
        await asyncio.to_thread(release.wait, 5)
        record = service.store.get(ingestion_id)
        record.status = "completed"
        service.store.save(record)
        finished.set()
        return record

    monkeypatch.setattr(service, "run", blocked_run)

    async def scenario():
        submission = asyncio.create_task(
            main.submit_rag_ingestion(
                main.RagIngestionRequest(profile="showcase-default"), False
            )
        )
        assert await asyncio.to_thread(started.wait, 5)
        submission.cancel()
        await asyncio.sleep(0)
        submission.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())
    assert finished.is_set()
    assert service.store.list()[0].status == "completed"


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


def test_rag_store_calls_are_offloaded_from_async_routes(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)
    service.submit("showcase-default")
    offloaded = []

    async def tracking_to_thread(fn, *args, **kwargs):
        offloaded.append(getattr(fn, "__name__", repr(fn)))
        return fn(*args, **kwargs)

    monkeypatch.setattr(main.asyncio, "to_thread", tracking_to_thread)

    response = TestClient(main.app).get("/api/rag/ingestions")

    assert response.status_code == 200
    assert "list" in offloaded


def test_get_unknown_ingestion_returns_404(tmp_path, monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    service = _fake_service(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_rag_ingestion_service", lambda: service)

    client = TestClient(main.app)
    assert client.get("/api/rag/ingestions/nope").status_code == 404
