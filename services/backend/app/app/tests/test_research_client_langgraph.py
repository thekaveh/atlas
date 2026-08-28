from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from research_client import ResearchClient, ResearchRequest, ResearchStatus
from research_service import ResearchService


def test_cancel_research_waits_through_commit_cancellation_before_stopping_work():
    commit_started = asyncio.Event()
    allow_commit = asyncio.Event()

    class Conn:
        def transaction(self):
            class Transaction:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    commit_started.set()
                    await allow_commit.wait()
                    return False

            return Transaction()

        async def fetchrow(self, *_args):
            return {"id": "session-1"}

        async def execute(self, *_args):
            return None

        async def close(self):
            return None

    async def scenario():
        service = object.__new__(ResearchService)

        async def get_conn():
            return Conn()

        async def work():
            await asyncio.Future()

        service._get_db_connection = get_conn
        background = asyncio.create_task(work())
        service._active_tasks = {"session-1": background}
        cancellation = asyncio.create_task(service.cancel_research("session-1"))
        await commit_started.wait()
        cancellation.cancel()
        await asyncio.sleep(0)
        assert not cancellation.done() and not background.cancelled()
        allow_commit.set()
        with pytest.raises(asyncio.CancelledError):
            await cancellation
        await asyncio.sleep(0)
        assert background.cancelled()

    asyncio.run(scenario())


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeAsyncClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse(200, {"status": "ok"})

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse(200, {"thread_id": "thread-123"})

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        values = {
            "final_summary": "Finished report",
            "sources_gathered": [{"url": "https://example.test"}],
        }
        return FakeStreamResponse(
            [
                "event: values",
                f"data: {json.dumps({'data': {'values': values}})}",
                "data: [DONE]",
            ]
        )


def test_research_client_uses_langgraph_thread_and_run_stream(monkeypatch):
    import research_client

    FakeAsyncClient.calls = []
    monkeypatch.setattr(research_client.httpx, "AsyncClient", FakeAsyncClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    async def scenario():
        health = await client.health_check()
        start = await client.start_research(
            ResearchRequest(query="atlas", max_loops=2, search_api="searxng")
        )
        done = await client.wait_for_completion(start.session_id)
        result = await client.get_research_result(start.session_id)
        return health, start, done, result

    health, start, done, result = asyncio.run(scenario())

    assert health["status"] == "healthy"
    assert start.session_id == "thread-123"
    # #802: thread creation reports PENDING, not RUNNING — the run is not
    # dispatched until wait_for_completion.
    assert start.status == ResearchStatus.PENDING
    assert done.status == ResearchStatus.COMPLETED
    assert result is not None
    assert result.content == "Finished report"

    assert ("GET", "http://local-deep-researcher:2024/ok", {}) in FakeAsyncClient.calls
    assert ("POST", "http://local-deep-researcher:2024/threads", {"json": {}}) in FakeAsyncClient.calls
    run_call = FakeAsyncClient.calls[-1]
    assert run_call[0] == "POST"
    assert run_call[1] == "http://local-deep-researcher:2024/threads/thread-123/runs/stream"
    assert run_call[2]["json"]["assistant_id"] == "ollama_deep_researcher"
    assert run_call[2]["json"]["on_disconnect"] == "cancel"
    assert run_call[2]["json"]["input"] == {"research_topic": "atlas"}
    assert run_call[2]["json"]["stream_mode"] == ["values"]
    assert run_call[2]["json"]["config"]["configurable"]["max_web_research_loops"] == 2
    assert run_call[2]["json"]["config"]["configurable"]["search_api"] == "searxng"


def test_research_client_completed_result_is_one_shot(monkeypatch):
    import research_client

    monkeypatch.setattr(research_client.httpx, "AsyncClient", FakeAsyncClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    async def scenario():
        start = await client.start_research(ResearchRequest(query="atlas"))
        done = await client.wait_for_completion(start.session_id)
        first = await client.get_research_result(start.session_id)
        second = await client.get_research_result(start.session_id)
        return done, first, second

    done, first, second = asyncio.run(scenario())

    assert done.status == ResearchStatus.COMPLETED
    assert first is not None
    assert second is None
    assert client._completed_results == {}


def test_research_client_defaults_to_atlas_searxng_search():
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    payload = client._run_payload(ResearchRequest(query="atlas"))

    assert payload["config"]["configurable"]["search_api"] == "searxng"


def test_research_client_normalizes_upstream_string_sources():
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    result = client._result_from_langgraph_values(
        "thread-123",
        ResearchRequest(query="atlas"),
        {
            "running_summary": "done",
            "sources_gathered": [
                "Source: Example\nURL: https://example.test/report",
                {"url": "https://structured.example", "title": "Structured"},
            ],
        },
    )

    assert result.sources == [
        {
            "url": "https://example.test/report",
            "title": "Example",
            "metadata": {
                "raw": "Source: Example\nURL: https://example.test/report",
            },
        },
        {"url": "https://structured.example", "title": "Structured"},
    ]


def test_research_client_splits_upstream_bulleted_source_strings():
    sample = "\n".join(
        [
            "* One : https://one.test",
            "* Two : https://two.test/path",
            "* Three : https://three.test/report.",
        ]
    )

    assert ResearchClient._normalize_sources([sample]) == [
        {"url": "https://one.test", "title": "One", "metadata": {"raw": "* One : https://one.test"}},
        {
            "url": "https://two.test/path",
            "title": "Two",
            "metadata": {"raw": "* Two : https://two.test/path"},
        },
        {
            "url": "https://three.test/report",
            "title": "Three",
            "metadata": {"raw": "* Three : https://three.test/report."},
        },
    ]


def test_research_client_marks_empty_langgraph_stream_failed(monkeypatch):
    import research_client

    class EmptyStreamClient(FakeAsyncClient):
        def stream(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeStreamResponse(["event: values", "data: [DONE]"])

    monkeypatch.setattr(research_client.httpx, "AsyncClient", EmptyStreamClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    async def scenario():
        start = await client.start_research(
            ResearchRequest(query="atlas", max_loops=2, search_api="searxng")
        )
        done = await client.wait_for_completion(start.session_id)
        result = await client.get_research_result(start.session_id)
        return done, result

    done, result = asyncio.run(scenario())

    assert done.status == ResearchStatus.FAILED
    assert "no final values" in done.message
    assert result is None
    assert asyncio.run(client.list_active_sessions()) == []


def test_research_client_marks_langgraph_error_event_failed(monkeypatch, caplog):
    import research_client

    class ErrorStreamClient(FakeAsyncClient):
        def stream(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeStreamResponse(
                [
                    "event: error",
                    f"data: {json.dumps({'error': 'search failed secret-token'})}",
                ]
            )

    monkeypatch.setattr(research_client.httpx, "AsyncClient", ErrorStreamClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    async def scenario():
        start = await client.start_research(
            ResearchRequest(query="atlas", max_loops=2, search_api="searxng")
        )
        return await client.wait_for_completion(start.session_id)

    with caplog.at_level(logging.WARNING):
        done = asyncio.run(scenario())

    assert done.status == ResearchStatus.FAILED
    assert done.message == "Research service request failed"
    assert "secret-token" not in done.message
    assert "secret-token" not in caplog.text
    assert asyncio.run(client.list_active_sessions()) == []


def test_research_client_redacts_error_nested_in_values(monkeypatch, caplog):
    import research_client

    class NestedErrorStreamClient(FakeAsyncClient):
        def stream(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeStreamResponse(
                [
                    "event: values",
                    f"data: {json.dumps({'data': {'values': {'error': 'secret-token'}}})}",
                ]
            )

    monkeypatch.setattr(
        research_client.httpx, "AsyncClient", NestedErrorStreamClient
    )
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    async def scenario():
        start = await client.start_research(ResearchRequest(query="atlas"))
        done = await client.wait_for_completion(start.session_id)
        result = await client.get_research_result(start.session_id)
        return done, result

    with caplog.at_level(logging.WARNING):
        done, result = asyncio.run(scenario())

    assert done.status == ResearchStatus.FAILED
    assert done.message == "Research service request failed"
    assert result is None
    assert "secret-token" not in done.model_dump_json()
    assert "secret-token" not in caplog.text


def test_research_client_does_not_log_rejected_response_bodies(monkeypatch, caplog):
    import research_client

    class RejectedClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            return httpx.Response(503, text="provider secret-token", request=request)

        async def get(self, url, **kwargs):
            request = httpx.Request("GET", url)
            return httpx.Response(502, text="status secret-token", request=request)

    monkeypatch.setattr(research_client.httpx, "AsyncClient", RejectedClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    async def scenario():
        started = await client.start_research(ResearchRequest(query="atlas"))
        status = await client.get_research_status("thread-123")
        return started, status

    with caplog.at_level(logging.WARNING):
        started, status = asyncio.run(scenario())

    assert started.status == ResearchStatus.FAILED
    assert status.status == ResearchStatus.FAILED
    assert "secret-token" not in caplog.text
    assert "status=503" in caplog.text
    assert "status=502" in caplog.text


def test_research_client_redacts_client_lifecycle_failures(monkeypatch, caplog):
    import research_client

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("provider secret-token")

    monkeypatch.setattr(research_client.httpx, "AsyncClient", BrokenClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    async def scenario():
        health = await client.health_check()
        started = await client.start_research(ResearchRequest(query="atlas"))
        status = await client.get_research_status("thread-123")
        return health, started, status

    with caplog.at_level(logging.WARNING):
        health, started, status = asyncio.run(scenario())

    assert health["error"] == "Research service is unavailable"
    assert started.message == "Research service is unavailable"
    assert status.message == "Research service is unavailable"
    assert "secret-token" not in caplog.text


def test_research_client_redacts_http_200_failed_state(monkeypatch):
    import research_client

    class FailedStateClient(FakeAsyncClient):
        async def get(self, url, **kwargs):
            return FakeResponse(
                200,
                {
                    "status": "failed",
                    "message": "upstream secret-token",
                    "error": {"detail": "raw secret-token"},
                },
            )

    monkeypatch.setattr(research_client.httpx, "AsyncClient", FailedStateClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    status = asyncio.run(client.get_research_status("thread-123"))

    assert status.status == ResearchStatus.FAILED
    assert status.message == "Research service request failed"
    assert status.data is None
    assert "secret-token" not in status.model_dump_json()


def test_research_client_honors_total_stream_timeout(monkeypatch):
    import research_client

    class InfiniteStreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            while True:
                yield "event: values"
                yield f"data: {json.dumps({'data': {'values': {'running_summary': 'partial'}}})}"
                await asyncio.sleep(0.01)

    class InfiniteStreamClient(FakeAsyncClient):
        def stream(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return InfiniteStreamResponse()

    monkeypatch.setattr(research_client.httpx, "AsyncClient", InfiniteStreamClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")
    client._pending_requests["thread-123"] = ResearchRequest(query="atlas")

    done = asyncio.run(client.wait_for_completion("thread-123", max_wait_time=0.05))

    assert done.status == ResearchStatus.FAILED
    assert "timed out" in done.message
    assert asyncio.run(client.list_active_sessions()) == []


def test_research_client_discard_pending_removes_stranded_request():
    client = ResearchClient(base_url="http://local-deep-researcher:2024")
    client._pending_requests["thread-123"] = ResearchRequest(query="atlas")

    client.discard_pending("thread-123")

    assert asyncio.run(client.list_active_sessions()) == []


def test_stream_research_logs_does_not_start_duplicate_langgraph_run(monkeypatch):
    import research_client

    FakeAsyncClient.calls = []
    monkeypatch.setattr(research_client.httpx, "AsyncClient", FakeAsyncClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")
    client._pending_requests["thread-123"] = ResearchRequest(
        query="atlas",
        max_loops=2,
        search_api="searxng",
        user_id="user-123",
    )

    async def scenario():
        events = []
        async for event in client.stream_research_logs("thread-123"):
            events.append(event)
        return events

    events = asyncio.run(scenario())

    assert events == [
        {
            "type": "unsupported",
            "message": "Research log streaming is not supported for LangGraph runs",
        }
    ]
    assert FakeAsyncClient.calls == []
