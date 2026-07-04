from __future__ import annotations

import asyncio

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
