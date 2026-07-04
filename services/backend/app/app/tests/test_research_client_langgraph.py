from __future__ import annotations

import asyncio
import json

from research_client import ResearchClient, ResearchRequest, ResearchStatus


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
    assert start.status == ResearchStatus.RUNNING
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


def test_research_client_marks_langgraph_error_event_failed(monkeypatch):
    import research_client

    class ErrorStreamClient(FakeAsyncClient):
        def stream(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeStreamResponse(
                [
                    "event: error",
                    f"data: {json.dumps({'error': 'search failed'})}",
                ]
            )

    monkeypatch.setattr(research_client.httpx, "AsyncClient", ErrorStreamClient)
    client = ResearchClient(base_url="http://local-deep-researcher:2024")

    async def scenario():
        start = await client.start_research(
            ResearchRequest(query="atlas", max_loops=2, search_api="searxng")
        )
        return await client.wait_for_completion(start.session_id)

    done = asyncio.run(scenario())

    assert done.status == ResearchStatus.FAILED
    assert "search failed" in done.message
    assert asyncio.run(client.list_active_sessions()) == []


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
