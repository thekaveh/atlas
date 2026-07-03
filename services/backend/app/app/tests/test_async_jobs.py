from __future__ import annotations

import importlib
import os
import sys
from uuid import uuid4


def _stub_required_env(monkeypatch):
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
        ("CELERY_SOURCE", "container"),
        ("CELERY_BROKER_URL", "redis://:redis_password@redis:6379/4"),
        ("CELERY_RESULT_BACKEND", "redis://:redis_password@redis:6379/4"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def _reload_main(monkeypatch):
    _stub_required_env(monkeypatch)
    if "main" in sys.modules:
        return importlib.reload(sys.modules["main"])
    import main  # type: ignore[import]

    return main


def test_memory_consolidate_async_dispatch_returns_job_id(monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    called = {}

    class FakeAsyncResult:
        id = "celery-task-123"

        @property
        def status(self):
            raise AssertionError("enqueue response must not query result backend status")

    def fake_apply_async(*, kwargs):
        called["kwargs"] = kwargs
        return FakeAsyncResult()

    async def fake_to_thread(fn, *args, **kwargs):
        called["to_thread"] = True
        return fn(*args, **kwargs)

    async def fail_if_sync_called(*args, **kwargs):
        raise AssertionError("async dispatch must not call memory_service.consolidate")

    monkeypatch.setattr(main.memory_consolidate_task, "apply_async", fake_apply_async)
    monkeypatch.setattr(main.memory_service, "consolidate", fail_if_sync_called)
    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)

    user_id = str(uuid4())
    client = TestClient(main.app)
    resp = client.post(
        "/memory/consolidate?async_job=true",
        json={"user_id": user_id},
    )

    assert resp.status_code == 202, resp.text
    assert resp.json() == {
        "job_id": "celery-task-123",
        "status": "pending",
        "message": "Memory consolidation queued",
        "task": "memory_consolidate",
        "request": {"user_id": user_id},
    }
    assert called["kwargs"] == {"user_id": user_id}
    assert called["to_thread"] is True


def test_memory_consolidate_sync_path_stays_backward_compatible(monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    expected = {
        "user_id": None,
        "facts_reviewed": 3,
        "facts_merged": 1,
        "facts_superseded": 0,
        "facts_expired": 0,
    }

    async def fake_consolidate(*, user_id):
        assert user_id is None
        return expected

    monkeypatch.setattr(main.memory_service, "consolidate", fake_consolidate)

    client = TestClient(main.app)
    resp = client.post("/memory/consolidate", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json() == expected


def test_memory_consolidate_async_returns_503_when_worker_disabled(monkeypatch):
    monkeypatch.setenv("CELERY_SOURCE", "disabled")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "")
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    resp = client.post("/memory/consolidate?async_job=true", json={})

    assert resp.status_code == 503
    assert "Celery worker tier is disabled" in resp.json()["detail"]


def test_memory_consolidate_async_returns_503_when_queue_dispatch_fails(monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    def fake_apply_async(*, kwargs):
        raise RuntimeError("redis connection refused")

    monkeypatch.setattr(main.memory_consolidate_task, "apply_async", fake_apply_async)

    client = TestClient(main.app)
    resp = client.post("/memory/consolidate?async_job=true", json={})

    assert resp.status_code == 503
    assert "Failed to queue memory consolidation" in resp.json()["detail"]


def test_get_job_status_maps_success_payload(monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    payload = {
        "job_id": "celery-task-123",
        "status": "success",
        "ready": True,
        "successful": True,
        "failed": False,
        "result": {
            "user_id": None,
            "facts_reviewed": 3,
            "facts_merged": 1,
            "facts_superseded": 0,
            "facts_expired": 0,
        },
        "error": None,
        "traceback": None,
    }
    called = {}

    def fake_get_status(job_id):
        called["job_id"] = job_id
        return payload | {"job_id": job_id}

    async def fake_to_thread(fn, *args, **kwargs):
        called["to_thread"] = True
        return fn(*args, **kwargs)

    monkeypatch.setattr(main, "get_celery_job_status", fake_get_status)
    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)

    client = TestClient(main.app)
    resp = client.get("/jobs/celery-task-123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "celery-task-123"
    assert body["status"] == "success"
    assert body["ready"] is True
    assert body["result"]["facts_merged"] == 1
    assert body["error"] is None
    assert called == {"to_thread": True, "job_id": "celery-task-123"}


def test_get_job_status_maps_failure_payload(monkeypatch):
    main = _reload_main(monkeypatch)
    from fastapi.testclient import TestClient

    payload = {
        "job_id": "celery-task-err",
        "status": "failure",
        "ready": True,
        "successful": False,
        "failed": True,
        "result": None,
        "error": "LiteLLM timeout",
        "traceback": "Traceback...",
    }
    monkeypatch.setattr(main, "get_celery_job_status", lambda job_id: payload | {"job_id": job_id})

    client = TestClient(main.app)
    resp = client.get("/jobs/celery-task-err")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failure"
    assert body["failed"] is True
    assert body["error"] == "LiteLLM timeout"
    assert body["result"] is None


def test_memory_consolidate_task_calls_memory_service(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    result = {
        "user_id": None,
        "facts_reviewed": 2,
        "facts_merged": 0,
        "facts_superseded": 1,
        "facts_expired": 0,
    }
    seen = {}

    class FakeMemoryService:
        async def consolidate(self, *, user_id):
            seen["user_id"] = user_id
            return result

    monkeypatch.setattr(celery_tasks, "MemoryService", FakeMemoryService)

    assert celery_tasks.run_memory_consolidate(None) == result
    assert seen["user_id"] is None


def test_job_status_redacts_failure_tracebacks(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_app

    class FakeResult:
        status = "FAILURE"
        result = RuntimeError("database password leaked")
        traceback = "Traceback with internal URLs"

        def ready(self):
            return True

        def successful(self):
            return False

        def failed(self):
            return True

    monkeypatch.setattr(celery_app.celery_app, "AsyncResult", lambda job_id: FakeResult())

    status = celery_app.get_celery_job_status("celery-task-err")

    assert status["status"] == "failure"
    assert status["error"] == "database password leaked"
    assert status["traceback"] is None
