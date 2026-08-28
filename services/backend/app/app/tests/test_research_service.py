from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from research_client import (
    ResearchRequest,
    ResearchResponse,
    ResearchResult,
    ResearchStatus,
)
from research_service import (
    ResearchCapacityError,
    ResearchService,
    _PUBLIC_RESEARCH_FAILURE,
    _log_task_exception,
)


class _Transaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.conn.in_transaction = False


def test_research_session_creation_and_start_log_are_atomic():
    class Conn:
        def __init__(self):
            self.in_transaction = False
            self.execute_calls = 0

        def transaction(self):
            return _Transaction(self)

        async def execute(self, *_args):
            assert self.in_transaction is True
            self.execute_calls += 1

        async def close(self):
            return None

    conn = Conn()
    service = object.__new__(ResearchService)
    service._active_tasks = {}
    service._maintenance_task = None

    async def get_conn():
        return conn

    async def background(*_args):
        return None

    service._get_db_connection = get_conn
    service._run_research_background = background

    async def scenario():
        result = await service.start_research("atlas")
        await service._active_tasks[result["session_id"]]

    asyncio.run(scenario())

    assert conn.execute_calls == 2


def test_research_admission_rejects_before_database_work():
    service = object.__new__(ResearchService)
    service.max_concurrent_research = 1
    service._active_tasks = {"occupied": object()}
    database_called = False

    async def get_conn():
        nonlocal database_called
        database_called = True
        raise AssertionError("capacity rejection must happen before database work")

    service._get_db_connection = get_conn

    async def scenario():
        with pytest.raises(ResearchCapacityError):
            await service.start_research("atlas")

    asyncio.run(scenario())
    assert database_called is False


def test_cancelled_research_retains_capacity_until_cleanup_and_close_waits():
    class Conn:
        def transaction(self):
            return _Transaction(self)

        async def fetchrow(self, *_args):
            return {"id": "session-1"}

        async def execute(self, *_args):
            return None

        async def close(self):
            return None

    service = object.__new__(ResearchService)
    service.max_concurrent_research = 1
    service._maintenance_task = None
    background_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def background():
        try:
            background_started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
            raise
        finally:
            service._active_tasks.pop("session-1", None)

    async def get_conn():
        return Conn()

    service._get_db_connection = get_conn

    async def scenario():
        task = asyncio.create_task(background())
        service._active_tasks = {"session-1": task}
        await background_started.wait()
        assert await service.cancel_research("session-1") is True
        await cleanup_started.wait()
        assert service._active_tasks == {"session-1": task}
        assert task.done() is False

        with pytest.raises(ResearchCapacityError):
            await service.start_research("must remain full")

        closing = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        assert closing.done() is False
        release_cleanup.set()
        await closing
        assert service._active_tasks == {}

    asyncio.run(scenario())


def test_failed_research_creation_releases_admission_slot():
    service = object.__new__(ResearchService)
    service.max_concurrent_research = 1
    service._active_tasks = {}

    async def get_conn():
        raise RuntimeError("database unavailable")

    service._get_db_connection = get_conn

    async def scenario():
        with pytest.raises(RuntimeError, match="database unavailable"):
            await service.start_research("atlas")

    asyncio.run(scenario())
    assert service._active_tasks == {}


def test_cancel_after_research_commit_retains_background_ownership():
    release_started = asyncio.Event()
    release_connection = asyncio.Event()
    background_started = asyncio.Event()

    class Conn:
        def transaction(self):
            return _Transaction(self)

        async def execute(self, *_args):
            return None

    service = object.__new__(ResearchService)
    service.max_concurrent_research = 1
    service._active_tasks = {}
    service._maintenance_task = None

    async def get_conn():
        return Conn()

    async def release_conn(_conn):
        release_started.set()
        await release_connection.wait()

    async def background(*_args):
        background_started.set()
        await asyncio.Event().wait()

    service._get_db_connection = get_conn
    service._release_db_connection = release_conn
    service._run_research_background = background

    async def scenario():
        creation = asyncio.create_task(service.start_research("atlas"))
        await release_started.wait()
        creation.cancel()
        release_connection.set()
        with pytest.raises(asyncio.CancelledError):
            await creation
        await background_started.wait()
        assert len(service._active_tasks) == 1
        owned = next(iter(service._active_tasks.values()))
        assert isinstance(owned, asyncio.Task)
        assert owned.done() is False
        owned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owned

    asyncio.run(scenario())


def test_cancel_during_commit_retains_background_ownership():
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    background_started = asyncio.Event()

    class BlockingCommit(_Transaction):
        async def __aexit__(self, exc_type, exc, tb):
            self.conn.in_transaction = False
            commit_started.set()
            await release_commit.wait()

    class Conn:
        def __init__(self):
            self.in_transaction = False

        def transaction(self):
            return BlockingCommit(self)

        async def execute(self, *_args):
            assert self.in_transaction is True

        async def close(self):
            return None

    service = object.__new__(ResearchService)
    service.max_concurrent_research = 1
    service._active_tasks = {}
    service._maintenance_task = None

    async def get_conn():
        return Conn()

    async def background(*_args):
        background_started.set()
        await asyncio.Event().wait()

    service._get_db_connection = get_conn
    service._run_research_background = background

    async def scenario():
        creation = asyncio.create_task(service.start_research("atlas"))
        await commit_started.wait()
        creation.cancel()
        release_commit.set()
        with pytest.raises(asyncio.CancelledError):
            await creation
        await background_started.wait()
        owned = next(iter(service._active_tasks.values()))
        assert owned.done() is False
        owned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owned

    asyncio.run(scenario())


def test_cancelled_background_task_records_terminal_failure():
    service = object.__new__(ResearchService)
    service._active_tasks = {}
    recorded = []

    async def mark_running(_session_id):
        return True

    async def execute_research(_session_id, _request):
        raise asyncio.CancelledError()

    async def record_failure(session_id, message):
        recorded.append((session_id, message))
        return True

    async def heartbeat(_session_id):
        await asyncio.Event().wait()

    service._mark_research_running = mark_running
    service._execute_research = execute_research
    service._record_research_failure = record_failure
    service._heartbeat_research = heartbeat

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await service._run_research_background(
                "local-session-1", "atlas", 1, "searxng", None
            )

    asyncio.run(scenario())

    assert recorded == [
        ("local-session-1", "Research worker stopped before completion")
    ]


def test_background_failure_persists_stable_public_message(caplog):
    service = object.__new__(ResearchService)
    service._active_tasks = {}
    recorded = []

    async def mark_running(_session_id):
        return True

    async def execute_research(_session_id, _request):
        raise RuntimeError("upstream body contains secret-token")

    async def record_failure(session_id, message):
        recorded.append((session_id, message))
        return True

    async def heartbeat(_session_id):
        await asyncio.Event().wait()

    service._mark_research_running = mark_running
    service._execute_research = execute_research
    service._record_research_failure = record_failure
    service._heartbeat_research = heartbeat

    with caplog.at_level("ERROR"):
        asyncio.run(
            service._run_research_background(
                "local-session-1", "atlas", 1, "searxng", None
            )
        )

    assert recorded == [("local-session-1", _PUBLIC_RESEARCH_FAILURE)]
    assert "secret-token" not in recorded[0][1]
    assert "research execution failed" in caplog.text
    assert "secret-token" not in caplog.text


def test_background_failure_redacts_secondary_persistence_exception(caplog):
    service = object.__new__(ResearchService)
    service._active_tasks = {}

    async def mark_running(_session_id):
        return True

    async def execute_research(_session_id, _request):
        raise RuntimeError("provider secret-token")

    async def record_failure(_session_id, _message):
        raise RuntimeError("database secret-token")

    async def heartbeat(_session_id):
        await asyncio.Event().wait()

    service._mark_research_running = mark_running
    service._execute_research = execute_research
    service._record_research_failure = record_failure
    service._heartbeat_research = heartbeat

    with caplog.at_level("ERROR"):
        asyncio.run(
            service._run_research_background(
                "local-session-1", "atlas", 1, "searxng", None
            )
        )

    assert "secret-token" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_background_task_callback_redacts_exception_message(caplog):
    async def fail():
        raise RuntimeError("task secret-token")

    async def scenario():
        task = asyncio.create_task(fail())
        await asyncio.sleep(0)
        with caplog.at_level("ERROR"):
            _log_task_exception("local-session-1")(task)

    asyncio.run(scenario())

    assert "secret-token" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_health_check_redacts_dependency_exception_messages(caplog):
    service = object.__new__(ResearchService)
    service._active_tasks = {}

    async def db_failure():
        raise RuntimeError("database secret-token")

    class FailedClient:
        async def health_check(self):
            raise RuntimeError("provider secret-token")

    service._get_db_connection = db_failure
    service.research_client = FailedClient()

    with caplog.at_level("WARNING"):
        health = asyncio.run(service.health_check())

    assert health["database"] == "unhealthy"
    assert health["research_client"] == "unhealthy"
    assert "secret-token" not in str(health)
    assert "secret-token" not in caplog.text


@pytest.mark.parametrize("loop_name", ["heartbeat", "maintenance"])
def test_research_loops_redact_dependency_exception_messages(
    monkeypatch, caplog, loop_name
):
    service = object.__new__(ResearchService)
    service.heartbeat_interval = 1
    sleep_calls = 0

    async def bounded_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError()

    async def fail(*_args):
        raise RuntimeError("database secret-token")

    monkeypatch.setattr(asyncio, "sleep", bounded_sleep)
    if loop_name == "heartbeat":
        service._write_research_heartbeat = fail
        coroutine = service._heartbeat_research("local-session-1")
    else:
        service.recover_stale_sessions = fail
        coroutine = service._maintenance_loop()

    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        asyncio.run(coroutine)

    assert "secret-token" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_stale_research_sessions_are_terminalized_with_logs():
    class Conn:
        def __init__(self):
            self.in_transaction = False
            self.logs = []

        def transaction(self):
            return _Transaction(self)

        async def fetch(self, sql, *args):
            assert self.in_transaction is True
            assert "heartbeat_at" in sql
            assert args == (300,)
            return [{"id": "stale-pending"}, {"id": "stale-running"}]

        async def execute(self, sql, *args):
            assert self.in_transaction is True
            assert "INSERT INTO public.research_logs" in sql
            self.logs.append(args)

        async def close(self):
            return None

    conn = Conn()
    service = object.__new__(ResearchService)
    service.lease_seconds = 300

    async def get_conn():
        return conn

    service._get_db_connection = get_conn

    recovered = asyncio.run(service.recover_stale_sessions())

    assert recovered == 2
    assert [args[0] for args in conn.logs] == ["stale-pending", "stale-running"]


def test_research_heartbeat_updates_only_running_session():
    class Conn:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, *args):
            self.calls.append((sql, args))

        async def close(self):
            return None

    conn = Conn()
    service = object.__new__(ResearchService)

    async def get_conn():
        return conn

    service._get_db_connection = get_conn

    asyncio.run(service._write_research_heartbeat("local-session-1"))

    sql, args = conn.calls[0]
    assert "heartbeat_at" in sql
    assert "status = $2" in sql
    assert args == ("local-session-1", ResearchStatus.RUNNING.value)


def test_execute_research_discards_remote_pending_request_when_cancelled_before_wait():
    class FakeResearchClient:
        def __init__(self):
            self.discarded = []

        async def start_research(self, request):
            return ResearchResponse(
                session_id="remote-thread-1",
                status=ResearchStatus.PENDING,
                message="started",
            )

        async def wait_for_completion(self, session_id):
            raise AssertionError("wait should not be reached")

        def discard_pending(self, session_id):
            self.discarded.append(session_id)

    fake_client = FakeResearchClient()
    service = object.__new__(ResearchService)
    service.research_client = fake_client

    async def cancel_before_wait(*args):
        raise asyncio.CancelledError()

    service._append_research_log = cancel_before_wait

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await service._execute_research(
                "local-session-1",
                ResearchRequest(query="atlas"),
            )

    asyncio.run(scenario())

    assert fake_client.discarded == ["remote-thread-1"]


def test_execute_research_discards_remote_pending_request_when_wait_is_cancelled():
    class FakeResearchClient:
        def __init__(self):
            self.discarded = []

        async def start_research(self, request):
            return ResearchResponse(
                session_id="remote-thread-1",
                status=ResearchStatus.PENDING,
                message="started",
            )

        async def wait_for_completion(self, session_id):
            raise asyncio.CancelledError()

        def discard_pending(self, session_id):
            self.discarded.append(session_id)

    fake_client = FakeResearchClient()
    service = object.__new__(ResearchService)
    service.research_client = fake_client

    async def append_log(*args):
        return None

    service._append_research_log = append_log

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await service._execute_research(
                "local-session-1",
                ResearchRequest(query="atlas"),
            )

    asyncio.run(scenario())

    assert fake_client.discarded == ["remote-thread-1"]


def test_background_research_task_cleanup_runs_when_db_connect_fails():
    service = object.__new__(ResearchService)
    service._active_tasks = {}

    async def boom():
        raise RuntimeError("db down")

    service._get_db_connection = boom

    async def scenario():
        task = asyncio.create_task(
            service._run_research_background("local-session-1", "atlas", 1, "duckduckgo", None)
        )
        service._active_tasks["local-session-1"] = task
        await task

    asyncio.run(scenario())

    assert service._active_tasks == {}


def test_background_research_releases_database_before_remote_execution():
    class TrackingConn:
        def __init__(self):
            self.closed = False

        async def fetchrow(self, sql, *args):
            assert "UPDATE public.research_sessions" in sql
            return {"id": args[-1]}

        async def execute(self, *args):
            return None

        async def close(self):
            self.closed = True

    service = object.__new__(ResearchService)
    service._active_tasks = {}
    connections = []

    async def get_conn():
        conn = TrackingConn()
        connections.append(conn)
        return conn

    remote_started_after_close = False

    async def execute_research(session_id, request):
        nonlocal remote_started_after_close
        remote_started_after_close = bool(connections) and all(
            conn.closed for conn in connections
        )

    service._get_db_connection = get_conn
    service._execute_research = execute_research

    asyncio.run(
        service._run_research_background(
            "local-session-1", "atlas", 1, "duckduckgo", None
        )
    )

    assert remote_started_after_close is True
    assert all(conn.closed for conn in connections)


def test_store_research_result_does_not_clobber_cancellation():
    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class CancelledConn:
        def __init__(self):
            self.inserts = []
            self.closed = False

        def transaction(self):
            return Transaction()

        async def fetchrow(self, sql, *args):
            assert "FOR UPDATE" in sql
            return {"status": ResearchStatus.CANCELLED.value}

        async def execute(self, sql, *args):
            self.inserts.append(sql)

        async def close(self):
            self.closed = True

    conn = CancelledConn()
    service = object.__new__(ResearchService)

    async def get_conn():
        return conn

    service._get_db_connection = get_conn
    result = ResearchResult(
        session_id="remote-session-1",
        title="Atlas",
        summary="Summary",
        content="Content",
        sources=[],
        metadata={},
    )

    stored = asyncio.run(service._store_research_result("local-session-1", result))

    assert stored is False
    assert conn.inserts == []
    assert conn.closed is True


def test_cancel_research_does_not_clobber_terminal_status_after_stale_read():
    class RaceConn:
        def __init__(self):
            self.cancel_log_inserted = False

        def transaction(self):
            return _Transaction(self)

        async def fetchrow(self, sql, *args):
            if "SELECT status" in sql:
                return {"status": ResearchStatus.RUNNING.value}
            if "UPDATE public.research_sessions" in sql:
                return None
            raise AssertionError(f"unexpected fetchrow: {sql}")

        async def execute(self, sql, *args):
            if "INSERT INTO public.research_logs" in sql:
                self.cancel_log_inserted = True
            return None

        async def close(self):
            return None

    conn = RaceConn()
    service = object.__new__(ResearchService)
    service._active_tasks = {}

    async def get_conn():
        return conn

    service._get_db_connection = get_conn

    result = asyncio.run(service.cancel_research("00000000-0000-4000-8000-000000000001"))

    assert result is False
    assert conn.cancel_log_inserted is False


def test_cancel_research_rolls_back_before_cancelling_task_when_log_fails():
    class Conn:
        def __init__(self):
            self.in_transaction = False
            self.rolled_back = False

        def transaction(self):
            conn = self

            class Transaction(_Transaction):
                async def __aexit__(self, exc_type, exc, tb):
                    conn.in_transaction = False
                    conn.rolled_back = exc_type is not None
                    return False

            return Transaction(self)

        async def fetchrow(self, *_args):
            assert self.in_transaction is True
            return {"id": "session-1"}

        async def execute(self, *_args):
            assert self.in_transaction is True
            raise RuntimeError("research log unavailable")

        async def close(self):
            return None

    class Task:
        cancel_called = False

        def cancel(self):
            self.cancel_called = True

    conn = Conn()
    task = Task()
    service = object.__new__(ResearchService)
    service._active_tasks = {"session-1": task}

    async def get_conn():
        return conn

    service._get_db_connection = get_conn

    with pytest.raises(RuntimeError, match="research log unavailable"):
        asyncio.run(service.cancel_research("session-1"))

    assert conn.rolled_back is True
    assert task.cancel_called is False


@pytest.mark.parametrize(
    "method_name",
    [
        "get_research_status",
        "get_research_result",
        "cancel_research",
        "get_research_logs",
    ],
)
def test_research_record_access_applies_owner_predicate(method_name):
    class OwnerConn:
        def __init__(self):
            self.calls = []

        def transaction(self):
            return _Transaction(self)

        async def fetchrow(self, sql, *args):
            self.calls.append((sql, args))
            return None

        async def close(self):
            return None

    conn = OwnerConn()
    service = object.__new__(ResearchService)
    service._active_tasks = {}

    async def get_conn():
        return conn

    service._get_db_connection = get_conn
    owner_id = "00000000-0000-4000-8000-000000000001"
    method = getattr(service, method_name)

    asyncio.run(
        method(
            "00000000-0000-4000-8000-000000000099",
            owner_user_id=owner_id,
        )
    )

    sql, args = conn.calls[0]
    assert "user_id" in sql
    assert "::uuid" in sql
    assert UUID(owner_id) in args
