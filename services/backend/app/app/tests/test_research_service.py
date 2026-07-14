from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from research_client import ResearchRequest, ResearchResponse, ResearchStatus
from research_service import ResearchService


class FakeConn:
    async def execute(self, *args, **kwargs):
        return None


class CancellingConn:
    async def execute(self, *args, **kwargs):
        raise asyncio.CancelledError()


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

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await service._execute_research(
                CancellingConn(),
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

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await service._execute_research(
                FakeConn(),
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
