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
from research_service import ResearchService


def test_execute_research_discards_remote_pending_request_when_cancelled_before_wait():
    class FakeResearchClient:
        def __init__(self):
            self.discarded = []

        async def start_research(self, request):
            return ResearchResponse(
                session_id="remote-thread-1",
                status=ResearchStatus.RUNNING,
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
                status=ResearchStatus.RUNNING,
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
