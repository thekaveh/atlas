from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import sys
import threading
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError


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

    def fake_apply_async(*, kwargs, task_id):
        called["kwargs"] = kwargs
        called["task_id"] = task_id
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
    assert called["task_id"].startswith("memory-consolidate-")
    assert called["kwargs"] == {
        "user_id": user_id,
        "idempotency_key": called["task_id"],
    }
    assert called["to_thread"] is True


def test_memory_consolidate_cancelled_dispatch_remains_reconcilable(monkeypatch):
    main = _reload_main(monkeypatch)
    from backend_identity import BackendPrincipal

    started = threading.Event()
    release = threading.Event()
    calls = []

    def blocked_apply_async(*, kwargs, task_id):
        calls.append((kwargs, task_id))
        started.set()
        assert release.wait(timeout=5)
        return type("FakeAsyncResult", (), {"id": task_id})()

    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    monkeypatch.setattr(main.memory_consolidate_task, "apply_async", blocked_apply_async)

    async def scenario():
        request = main.MemoryConsolidateRequest(
            idempotency_key="stable-key"
        )
        submission = asyncio.create_task(
            main.memory_consolidate(
                request,
                async_job=True,
                principal=BackendPrincipal(kind="service", subject="atlas"),
            )
        )
        assert await asyncio.to_thread(started.wait, 5)
        submission.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())
    expected = "memory-consolidate-" + hashlib.sha256(
        b"all-users\0stable-key"
    ).hexdigest()
    assert calls == [
        ({"user_id": None, "idempotency_key": expected}, expected)
    ]


def test_memory_consolidate_retry_republishes_same_stable_job(monkeypatch):
    main = _reload_main(monkeypatch)
    from backend_identity import BackendPrincipal

    monkeypatch.setattr(main, "celery_is_enabled", lambda: True)
    calls = []

    def record_dispatch(*, kwargs, task_id):
        calls.append((kwargs, task_id))
        return type("FakeAsyncResult", (), {"id": task_id})()

    monkeypatch.setattr(main.memory_consolidate_task, "apply_async", record_dispatch)

    async def scenario():
        request = main.MemoryConsolidateRequest(idempotency_key="stable-key")
        principal = BackendPrincipal(kind="service", subject="atlas")
        return [
            await main.memory_consolidate(request, True, principal),
            await main.memory_consolidate(request, True, principal),
        ]

    responses = asyncio.run(scenario())
    expected = "memory-consolidate-" + hashlib.sha256(
        b"all-users\0stable-key"
    ).hexdigest()
    assert [response.status_code for response in responses] == [202, 202]
    assert all(
        f'"job_id":"{expected}"'.encode() in response.body
        for response in responses
    )
    assert calls == [
        ({"user_id": None, "idempotency_key": expected}, expected),
        ({"user_id": None, "idempotency_key": expected}, expected),
    ]


def test_memory_execution_lease_uses_owner_checked_atomic_updates(monkeypatch):
    import celery_app
    import redis

    calls = []

    class Client:
        def set(self, *args, **kwargs):
            calls.append(("set", args, kwargs))
            return True

        def eval(self, *args):
            calls.append(("eval", args, {}))
            return 1

        def close(self):
            calls.append(("close", (), {}))

    client = Client()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *_args, **_kwargs: client)

    assert celery_app.claim_memory_execution("job-1", "owner-1") == (
        "claimed",
        None,
    )
    assert celery_app.release_memory_execution("job-1", "owner-1") is True
    assert celery_app.complete_memory_execution(
        "job-1", "owner-1", {"facts_merged": 1}
    ) is True

    assert calls[0] == (
        "set",
        ("atlas:celery:memory-execution:job-1", "running:owner-1"),
        {"nx": True, "ex": celery_app.memory_execution_lease_seconds()},
    )
    assert calls[2][0] == "eval"
    assert calls[2][1][2:] == (
        "atlas:celery:memory-execution:job-1",
        "running:owner-1",
    )
    assert calls[4][0] == "eval"
    assert calls[4][1][2:4] == (
        "atlas:celery:memory-execution:job-1",
        "running:owner-1",
    )
    assert calls[4][1][-1] == celery_app._visibility_timeout


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

    def fake_apply_async(*, kwargs, task_id):
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


def test_memory_consolidate_worker_does_not_reuse_a_pool_across_task_loops(
    monkeypatch,
):
    _stub_required_env(monkeypatch)
    import celery_tasks
    import db_connection

    db_connection._pools.clear()
    created = []

    class LoopBoundPool:
        def __init__(self):
            self.loop = asyncio.get_running_loop()
            self.closed = False

        def is_closing(self):
            if asyncio.get_running_loop() is not self.loop:
                raise RuntimeError("pool reused from a closed task loop")
            return self.closed

        async def close(self):
            assert asyncio.get_running_loop() is self.loop
            self.closed = True

        def terminate(self):
            self.closed = True

    async def fake_create_pool(*_args, **_kwargs):
        pool = LoopBoundPool()
        created.append(pool)
        return pool

    class FakeMemoryService:
        async def consolidate(self, *, user_id, retry_transient):
            assert retry_transient is True
            pool = await db_connection.get_pg_pool("postgresql://test")
            assert pool.loop is asyncio.get_running_loop()
            return {"user_id": user_id}

    monkeypatch.setattr(db_connection.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(celery_tasks, "MemoryService", FakeMemoryService)

    assert celery_tasks.run_memory_consolidate("first") == {"user_id": "first"}
    assert celery_tasks.run_memory_consolidate("second") == {"user_id": "second"}
    assert len(created) == 2
    assert all(pool.closed for pool in created)
    assert db_connection._pools == {}


def test_memory_consolidate_worker_closes_pools_when_consolidation_fails(
    monkeypatch,
):
    _stub_required_env(monkeypatch)
    import celery_tasks

    closed = []

    class FakeMemoryService:
        async def consolidate(self, *, user_id, retry_transient):
            raise TimeoutError("temporary LiteLLM timeout")

    async def fake_close_pg_pools():
        closed.append(asyncio.get_running_loop())

    monkeypatch.setattr(celery_tasks, "MemoryService", FakeMemoryService)
    monkeypatch.setattr(celery_tasks, "close_pg_pools", fake_close_pg_pools)

    with pytest.raises(TimeoutError, match="temporary LiteLLM timeout"):
        celery_tasks.run_memory_consolidate("user-1")

    assert len(closed) == 1


def test_memory_consolidate_cleanup_failure_preserves_transient_error(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    class FakeMemoryService:
        async def consolidate(self, *, user_id, retry_transient):
            raise TimeoutError("temporary LiteLLM timeout")

    async def fail_close_pg_pools():
        raise RuntimeError("pool close failed")

    monkeypatch.setattr(celery_tasks, "MemoryService", FakeMemoryService)
    monkeypatch.setattr(celery_tasks, "close_pg_pools", fail_close_pg_pools)

    with pytest.raises(TimeoutError, match="temporary LiteLLM timeout"):
        celery_tasks.run_memory_consolidate("user-1")


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


def test_memory_consolidate_worker_returns_completed_idempotent_result(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    completed = {"user_id": "user-1", "facts_merged": 2}
    monkeypatch.setattr(
        celery_tasks,
        "claim_memory_execution",
        lambda *_args: ("done", completed),
    )
    monkeypatch.setattr(
        celery_tasks,
        "run_memory_consolidate",
        lambda *_args: pytest.fail("completed work must not run again"),
    )

    assert celery_tasks.memory_consolidate_task.run(
        "user-1", idempotency_key="stable-job"
    ) == completed


def test_memory_consolidate_worker_retries_busy_execution_lease(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    class RetryScheduled(RuntimeError):
        pass

    captured = {}
    monkeypatch.setattr(
        celery_tasks,
        "claim_memory_execution",
        lambda *_args: ("busy", None),
    )

    def capture_retry(**kwargs):
        captured.update(kwargs)
        raise RetryScheduled()

    monkeypatch.setattr(celery_tasks.memory_consolidate_task, "retry", capture_retry)

    with pytest.raises(RetryScheduled):
        celery_tasks.memory_consolidate_task.run(
            "user-1", idempotency_key="stable-job"
        )

    assert captured["countdown"] == celery_tasks.memory_execution_lease_seconds()
    assert captured["kwargs"]["user_id"] == "user-1"
    assert captured["kwargs"]["idempotency_key"] == "stable-job"
    assert captured["kwargs"]["retry_state"]["attempt"] == 0
    assert captured["kwargs"]["retry_state"]["owner"]


def test_memory_consolidate_worker_completes_claimed_execution(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    result = {"user_id": "user-1", "facts_merged": 1}
    completed = []
    monkeypatch.setattr(
        celery_tasks,
        "claim_memory_execution",
        lambda *_args: ("claimed", None),
    )
    monkeypatch.setattr(celery_tasks, "run_memory_consolidate", lambda _user_id: result)
    monkeypatch.setattr(
        celery_tasks,
        "complete_memory_execution",
        lambda task_id, owner, value: completed.append((task_id, owner, value)) or True,
    )

    assert celery_tasks.memory_consolidate_task.run(
        "user-1", idempotency_key="stable-job"
    ) == result
    assert len(completed) == 1
    assert completed[0][0] == "stable-job"
    assert completed[0][2] == result


def test_memory_consolidate_retries_redis_claim_failure_without_running(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    class RetryScheduled(RuntimeError):
        pass

    captured = {}
    monkeypatch.setattr(
        celery_tasks,
        "claim_memory_execution",
        lambda *_args: (_ for _ in ()).throw(RedisConnectionError("redis down")),
    )
    monkeypatch.setattr(
        celery_tasks,
        "run_memory_consolidate",
        lambda *_args: pytest.fail("claim failure must not run consolidation"),
    )

    def retry(**kwargs):
        captured.update(kwargs)
        raise RetryScheduled()

    monkeypatch.setattr(celery_tasks.memory_consolidate_task, "retry", retry)
    with pytest.raises(RetryScheduled):
        celery_tasks.memory_consolidate_task.run(
            "user-1", idempotency_key="stable-job"
        )

    assert captured["kwargs"]["retry_state"]["attempt"] == 1
    assert captured["kwargs"]["retry_state"]["owner"]
    assert isinstance(captured["exc"], RedisConnectionError)


def test_memory_claim_retry_fences_commit_ambiguous_owner(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    class RetryScheduled(RuntimeError):
        pass

    owners = []
    captured = {}

    recovery_owners = []

    def claim(_task_id, owner, recovery_owner=None):
        owners.append(owner)
        recovery_owners.append(recovery_owner)
        if len(owners) == 1:
            raise RedisConnectionError("SET committed before disconnect")
        return "claimed", None

    def retry(**kwargs):
        captured.update(kwargs)
        raise RetryScheduled()

    result = {"user_id": "user-1", "facts_merged": 0}
    monkeypatch.setattr(celery_tasks, "claim_memory_execution", claim)
    monkeypatch.setattr(celery_tasks.memory_consolidate_task, "retry", retry)
    monkeypatch.setattr(celery_tasks, "run_memory_consolidate", lambda _uid: result)
    monkeypatch.setattr(celery_tasks, "complete_memory_execution", lambda *_a: True)
    with pytest.raises(RetryScheduled):
        celery_tasks.memory_consolidate_task.run(
            "user-1", idempotency_key="stable-job"
        )

    assert captured["kwargs"]["retry_state"]["owner"] == owners[0]
    assert celery_tasks.memory_consolidate_task.run(**captured["kwargs"]) == result
    assert owners[1] != owners[0]
    assert recovery_owners == [None, owners[0]]


def test_memory_consolidate_retries_only_result_commit_after_redis_failure(
    monkeypatch,
):
    _stub_required_env(monkeypatch)
    import celery_tasks

    class RetryScheduled(RuntimeError):
        pass

    result = {"user_id": "user-1", "facts_merged": 1}
    captured = {}
    monkeypatch.setattr(
        celery_tasks, "claim_memory_execution", lambda *_args: ("claimed", None)
    )
    monkeypatch.setattr(
        celery_tasks, "run_memory_consolidate", lambda _user_id: result
    )
    monkeypatch.setattr(
        celery_tasks,
        "complete_memory_execution",
        lambda *_args: (_ for _ in ()).throw(RedisConnectionError("redis down")),
    )

    def retry(**kwargs):
        captured.update(kwargs)
        raise RetryScheduled()

    monkeypatch.setattr(celery_tasks.memory_consolidate_task, "retry", retry)
    with pytest.raises(RetryScheduled):
        celery_tasks.memory_consolidate_task.run(
            "user-1", idempotency_key="stable-job"
        )

    retry_state = captured["kwargs"]["retry_state"]
    assert retry_state["result"] == result
    assert retry_state["owner"]
    assert retry_state["attempt"] == 1


def test_memory_consolidate_completion_retry_never_reruns_consolidation(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    result = {"user_id": "user-1", "facts_merged": 1}
    monkeypatch.setattr(
        celery_tasks,
        "run_memory_consolidate",
        lambda *_args: pytest.fail("completion retry must not rerun consolidation"),
    )
    monkeypatch.setattr(
        celery_tasks, "complete_memory_execution", lambda *_args: True
    )

    assert celery_tasks.memory_consolidate_task.run(
        "user-1",
        idempotency_key="stable-job",
        retry_state={"attempt": 1, "result": result, "owner": "worker-1"},
    ) == result


def test_memory_consolidate_transient_retry_carries_bounded_attempt(monkeypatch):
    _stub_required_env(monkeypatch)
    import celery_tasks

    class RetryScheduled(RuntimeError):
        pass

    captured = {}
    released = []
    monkeypatch.setattr(
        celery_tasks,
        "claim_memory_execution",
        lambda *_args: ("claimed", None),
    )
    monkeypatch.setattr(
        celery_tasks,
        "run_memory_consolidate",
        lambda _user_id: (_ for _ in ()).throw(TimeoutError("temporary")),
    )
    monkeypatch.setattr(
        celery_tasks,
        "release_memory_execution",
        lambda task_id, owner: released.append((task_id, owner)) or True,
    )

    def capture_retry(**kwargs):
        captured.update(kwargs)
        raise RetryScheduled()

    monkeypatch.setattr(celery_tasks.memory_consolidate_task, "retry", capture_retry)

    with pytest.raises(RetryScheduled):
        celery_tasks.memory_consolidate_task.run(
            "user-1",
            idempotency_key="stable-job",
            retry_state={"attempt": 2},
        )

    assert captured["kwargs"]["user_id"] == "user-1"
    assert captured["kwargs"]["idempotency_key"] == "stable-job"
    assert captured["kwargs"]["retry_state"]["attempt"] == 3
    assert captured["kwargs"]["retry_state"]["owner"]
    assert isinstance(captured["exc"], TimeoutError)
    assert released[0][0] == "stable-job"


@pytest.mark.parametrize("error", [TimeoutError("temporary"), ValueError("bad")])
def test_memory_consolidate_release_failure_preserves_task_error(
    monkeypatch, error
):
    _stub_required_env(monkeypatch)
    import celery_tasks

    class RetryScheduled(RuntimeError):
        pass

    monkeypatch.setattr(
        celery_tasks,
        "claim_memory_execution",
        lambda *_args: ("claimed", None),
    )
    monkeypatch.setattr(
        celery_tasks,
        "run_memory_consolidate",
        lambda _user_id: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        celery_tasks,
        "release_memory_execution",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("redis unavailable")),
    )

    def retry(**kwargs):
        assert kwargs["exc"] is error
        raise RetryScheduled(str(error))

    monkeypatch.setattr(celery_tasks.memory_consolidate_task, "retry", retry)

    expected = RetryScheduled if isinstance(error, TimeoutError) else ValueError
    with pytest.raises(expected, match="temporary" if isinstance(error, TimeoutError) else "bad"):
        celery_tasks.memory_consolidate_task.run(
            "user-1", idempotency_key="stable-job"
        )


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
    assert status["error"] == "Background job failed"
    assert "database password leaked" not in repr(status)
    assert "internal URLs" not in repr(status)
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
    assert celery_tasks.memory_consolidate_task.max_retries is None


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

    assert captured["kwargs"]["ingestion_id"] == "ingestion-1"
    assert captured["kwargs"]["retry_state"] == {
        "phase_attempt": transient_attempt + 1,
        "infrastructure_attempt": 0,
    }
    assert captured["args"] == ()


def test_rag_redis_retries_do_not_consume_phase_budget(monkeypatch):
    import celery_tasks
    import rag_ingestion

    class RetryScheduled(RuntimeError):
        pass

    captured = {}
    invoked = {}

    def fail_ingestion(*_args, **kwargs):
        invoked.update(kwargs)
        raise RedisConnectionError("temporary Redis outage")

    def capture_retry(**kwargs):
        captured.update(kwargs)
        raise RetryScheduled()

    monkeypatch.setattr(rag_ingestion, "run_rag_ingestion", fail_ingestion)
    monkeypatch.setattr(celery_tasks.rag_ingestion_task, "retry", capture_retry)

    with pytest.raises(RetryScheduled):
        celery_tasks.rag_ingestion_task.run(
            "ingestion-1",
            retry_state={
                "phase_attempt": 0,
                "infrastructure_attempt": 3,
                "recovery_owner": "ambiguous-owner",
            },
        )

    current_owner = invoked["execution_owner"]
    assert current_owner != "ambiguous-owner"
    assert invoked["execution_recovery_owner"] == "ambiguous-owner"
    assert captured["kwargs"]["retry_state"] == {
        "phase_attempt": 0,
        "infrastructure_attempt": 4,
        "recovery_owner": current_owner,
    }
    assert captured["countdown"] <= 600


@pytest.mark.parametrize(
    "retry_state",
    [
        [],
        {"phase_attempt": True},
        {"phase_attempt": -1},
        {"infrastructure_attempt": True},
        {"infrastructure_attempt": -1},
        {"recovery_owner": 7},
    ],
)
def test_rag_retry_state_rejects_malformed_values(monkeypatch, retry_state):
    import celery_tasks

    with pytest.raises(ValueError, match="retry_state"):
        celery_tasks.rag_ingestion_task.run(
            "ingestion-1", retry_state=retry_state
        )


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


@pytest.mark.parametrize(
    ("exception_name", "carries_owner"),
    [("IngestionExecutionLeaseLost", True), ("IngestionExecutionBusy", False)],
)
def test_rag_execution_claim_retry_is_rescheduled(
    monkeypatch, exception_name, carries_owner
):
    import celery_tasks
    import rag_ingestion

    class RetryScheduled(RuntimeError):
        pass

    captured = {}
    invoked = {}

    def fail_claim(*_args, **kwargs):
        invoked.update(kwargs)
        raise getattr(rag_ingestion, exception_name)("claim unavailable")

    def capture_retry(**kwargs):
        captured.update(kwargs)
        raise RetryScheduled()

    monkeypatch.setattr(rag_ingestion, "run_rag_ingestion", fail_claim)
    monkeypatch.setattr(celery_tasks.rag_ingestion_task, "retry", capture_retry)

    with pytest.raises(RetryScheduled):
        celery_tasks.rag_ingestion_task.run(
            "ingestion-1",
            transient_attempt=2,
            retry_state={"phase_attempt": 2, "recovery_owner": "stale-owner"},
        )

    assert captured["kwargs"]["ingestion_id"] == "ingestion-1"
    state = captured["kwargs"]["retry_state"]
    assert state["phase_attempt"] == 2
    assert state["infrastructure_attempt"] == 0
    assert state.get("recovery_owner") == (
        invoked["execution_owner"] if carries_owner else None
    )
    assert captured["args"] == ()
