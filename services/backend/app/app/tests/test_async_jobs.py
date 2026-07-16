from __future__ import annotations

import importlib
import os
import sys
from uuid import uuid4

import pytest


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
        async def consolidate(self, *, user_id, retry_transient):
            seen["user_id"] = user_id
            seen["retry_transient"] = retry_transient
            return result

    monkeypatch.setattr(celery_tasks, "MemoryService", FakeMemoryService)

    assert celery_tasks.run_memory_consolidate(None) == result
    assert seen["user_id"] is None
    assert seen["retry_transient"] is True


def test_memory_consolidate_worker_propagates_transient_llm_failure(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    class FakeMemoryService:
        async def consolidate(self, *, user_id, retry_transient):
            assert retry_transient is True
            raise TimeoutError("temporary LiteLLM timeout")

    monkeypatch.setattr(celery_tasks, "MemoryService", FakeMemoryService)

    with pytest.raises(TimeoutError, match="temporary LiteLLM timeout"):
        celery_tasks.run_memory_consolidate("user-1")


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


@pytest.mark.parametrize(
    "name,value",
    (
        ("CELERY_WORKER_CONCURRENCY", "bad"),
        ("CELERY_WORKER_PREFETCH_MULTIPLIER", "0"),
        ("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", "-1"),
        ("CELERY_TASK_TIME_LIMIT_SECONDS", "0"),
        ("CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS", "-1"),
    ),
)
def test_celery_worker_limits_reject_malformed_or_nonpositive_values(
    monkeypatch, name, value
):
    import celery_app

    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        celery_app._load_worker_limits()


@pytest.mark.parametrize(
    "soft,hard,visibility",
    ((900, 900, 3600), (901, 900, 3600), (840, 900, 900), (840, 900, 899)),
)
def test_celery_worker_limits_enforce_deadline_order(
    monkeypatch, soft, hard, visibility
):
    import celery_app

    monkeypatch.setenv("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", str(soft))
    monkeypatch.setenv("CELERY_TASK_TIME_LIMIT_SECONDS", str(hard))
    monkeypatch.setenv(
        "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS", str(visibility)
    )
    with pytest.raises(ValueError):
        celery_app._load_worker_limits()


def test_rag_lease_contention_retries_are_unbounded_but_transients_are_bounded():
    import celery_tasks

    assert celery_tasks.rag_ingestion_task.max_retries is None
    assert celery_tasks.memory_consolidate_task.max_retries == 3


@pytest.mark.parametrize("transient_attempt", (0, 2))
def test_rag_transient_retry_budget_is_independent_of_celery_retry_count(
    monkeypatch, transient_attempt
):
    import celery_tasks
    import rag_ingestion

    class RetryScheduled(RuntimeError):
        pass

    captured = {}

    def fail_ingestion(*_args, **_kwargs):
        raise ConnectionError("temporary upstream outage")

    def capture_retry(**kwargs):
        captured.update(kwargs)
        raise RetryScheduled()

    monkeypatch.setattr(rag_ingestion, "run_rag_ingestion", fail_ingestion)
    monkeypatch.setattr(celery_tasks.rag_ingestion_task, "retry", capture_retry)

    with pytest.raises(RetryScheduled):
        celery_tasks.rag_ingestion_task.run(
            "ingestion-1", transient_attempt=transient_attempt
        )

    assert captured["kwargs"] == {
        "ingestion_id": "ingestion-1",
        "transient_attempt": transient_attempt + 1,
    }
    assert captured["args"] == ()


def test_rag_transient_retry_budget_stops_after_three_retries(monkeypatch):
    import celery_tasks
    import rag_ingestion

    captured = {}

    def terminal_ingestion(ingestion_id, **kwargs):
        captured.update(kwargs)
        return {"id": ingestion_id, "status": "failed"}

    monkeypatch.setattr(rag_ingestion, "run_rag_ingestion", terminal_ingestion)
    monkeypatch.setattr(
        celery_tasks.rag_ingestion_task,
        "retry",
        lambda **_kwargs: pytest.fail("retry budget must be exhausted"),
    )

    result = celery_tasks.rag_ingestion_task.run(
        "ingestion-1", transient_attempt=3
    )

    assert result["status"] == "failed"
    assert captured["retry_transient"] is False


def test_rag_execution_lease_loss_is_rescheduled(monkeypatch):
    import celery_tasks
    import rag_ingestion

    class RetryScheduled(RuntimeError):
        pass

    captured = {}

    def lose_lease(*_args, **_kwargs):
        raise rag_ingestion.IngestionExecutionLeaseLost("lease lost")

    def capture_retry(**kwargs):
        captured.update(kwargs)
        raise RetryScheduled()

    monkeypatch.setattr(rag_ingestion, "run_rag_ingestion", lose_lease)
    monkeypatch.setattr(celery_tasks.rag_ingestion_task, "retry", capture_retry)

    with pytest.raises(RetryScheduled):
        celery_tasks.rag_ingestion_task.run(
            "ingestion-1", transient_attempt=2
        )

    assert captured["kwargs"] == {
        "ingestion_id": "ingestion-1",
        "transient_attempt": 2,
    }
    assert captured["args"] == ()
