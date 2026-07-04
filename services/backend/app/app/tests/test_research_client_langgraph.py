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
    assert run_call[2]["json"]["assistant_id"] == "agent"
    assert run_call[2]["json"]["input"] == {"research_topic": "atlas"}
    assert run_call[2]["json"]["stream_mode"] == ["values"]
