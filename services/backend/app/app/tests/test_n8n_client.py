import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest

from n8n_client import N8nClient


@contextmanager
def _workflow_gateway(monkeypatch, replies):
    from fastapi.testclient import TestClient
    from tests.test_fal_media_provider import _fresh_main

    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "disabled")
    monkeypatch.setenv("BACKEND_STATE_STORE_MODE", "memory")
    monkeypatch.setenv("MEDIA_BUDGET_STORE", "memory")
    monkeypatch.setenv("N8N_API_KEY", "owned-api-key")
    main = _fresh_main(monkeypatch, fal_source="disabled")
    asyncio.run(main.n8n_client._client.aclose())
    requests = []

    def handler(request):
        requests.append(request)
        reply = replies[min(len(requests) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    main.n8n_client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        yield main, TestClient(main.app, raise_server_exceptions=False), requests
    finally:
        asyncio.run(main.n8n_client.aclose())


@pytest.mark.parametrize("reply", [
    httpx.Response(200, json={}), httpx.Response(200, json={"data": None}),
    httpx.Response(200, json={"data": {}}), httpx.Response(200, json={"data": ""}),
    httpx.Response(200, json=None), httpx.Response(200, json=[]),
    httpx.Response(200, json="wrong root"), httpx.Response(200, content=b"not-json"),
    httpx.Response(200, content=b"\xff"), httpx.Response(200, content=b"null"),
    httpx.Response(200, content=b"[" * 1100 + b"0" + b"]" * 1100),
    httpx.Response(200, content=b'{"data":[],"extra":' + b"7" * 4301 + b"}"),
    httpx.Response(200, content=b'{"data":[],"nextCursor":' + b"7" * 4301 + b"}"),
])
def test_workflow_gateway_rejects_malformed_successful_inventory(monkeypatch, reply):
    with _workflow_gateway(monkeypatch, [reply]) as (_main, client, requests):
        response = client.get("/workflows")

    assert response.status_code == 502
    assert response.json() == {"detail": "List workflows failed"}
    assert len(requests) == 1
    assert requests[0].headers["X-N8N-API-KEY"] == "owned-api-key"


@pytest.mark.parametrize("cursor", [False, 0, 1, [], {}, ["token"], "\ud800"])
def test_workflow_gateway_rejects_wrong_typed_cursors(monkeypatch, cursor):
    reply = httpx.Response(200, content=json.dumps({"data": [], "nextCursor": cursor}).encode("ascii"))
    with _workflow_gateway(monkeypatch, [reply]) as (_main, client, requests):
        response = client.get("/workflows")

    assert response.status_code == 502
    assert response.json() == {"detail": "List workflows failed"}
    assert len(requests) == 1


@pytest.mark.parametrize("terminal", [{}, {"nextCursor": None}, {"nextCursor": ""}])
def test_workflow_gateway_keeps_empty_terminal_inventory(monkeypatch, terminal):
    with _workflow_gateway(monkeypatch, [httpx.Response(200, json={"data": [], **terminal})]) as (
        _main, client, requests
    ):
        response = client.get("/workflows")

    assert response.status_code == 200
    assert response.json() == []
    assert len(requests) == 1


def test_workflow_gateway_keeps_verbatim_cursor_and_timestamp_aliases(monkeypatch):
    cursor = "opaque +/token?&"
    workflow = {"id": "owned", "name": "Owned", "active": False,
                "createdAt": "2026-10-01T00:00:00Z", "updatedAt": "2026-10-02T00:00:00Z"}
    replies = [httpx.Response(200, json={"data": [], "nextCursor": cursor}),
               httpx.Response(200, json={"data": [workflow], "nextCursor": None})]
    with _workflow_gateway(monkeypatch, replies) as (_main, client, requests):
        response = client.get("/workflows")

    assert response.status_code == 200
    assert response.json() == [{"id": "owned", "name": "Owned", "active": False,
                               "created_at": workflow["createdAt"], "updated_at": workflow["updatedAt"]}]
    assert len(requests) == 2
    assert requests[1].url.params["cursor"] == cursor
    assert all(request.headers["X-N8N-API-KEY"] == "owned-api-key" for request in requests)


@pytest.mark.parametrize("cursors, count", [(["A", "A"], 2), (["A", "B", "A"], 3)])
def test_workflow_gateway_rejects_repeated_or_cyclic_page_chain(monkeypatch, cursors, count):
    replies = [httpx.Response(200, json={"data": [], "nextCursor": token}) for token in cursors]
    with _workflow_gateway(monkeypatch, replies) as (_main, client, requests):
        response = client.get("/workflows")

    assert response.status_code == 502
    assert response.json() == {"detail": "List workflows failed"}
    assert len(requests) == count


@pytest.mark.parametrize("terminal", [True, False])
def test_workflow_gateway_checks_completion_at_page_cap(monkeypatch, terminal):
    replies = [httpx.Response(200, json={"data": [], "nextCursor": str(index)}) for index in range(100)]
    if terminal:
        replies[-1] = httpx.Response(200, json={"data": [], "nextCursor": None})
    with _workflow_gateway(monkeypatch, replies) as (_main, client, requests):
        response = client.get("/workflows")

    assert response.status_code == (200 if terminal else 502)
    assert response.json() == ([] if terminal else {"detail": "List workflows failed"})
    assert len(requests) == 100


@pytest.mark.parametrize("reply, status", [
    (httpx.Response(401, json={"detail": "SENTINEL_PROVIDER_BODY"}), 502),
    (httpx.ConnectError("SENTINEL_TRANSPORT"), 503),
    (RuntimeError("SENTINEL_UNRELATED_RUNTIME"), 500),
    (ValueError("SENTINEL_UNRELATED_VALUE"), 500),
])
def test_workflow_gateway_preserves_other_error_categories(monkeypatch, reply, status):
    with _workflow_gateway(monkeypatch, [reply]) as (_main, client, requests):
        response = client.get("/workflows")

    assert response.status_code == status
    assert response.json() == {"detail": "List workflows failed"}
    assert "SENTINEL" not in response.text
    assert len(requests) == 1


def test_workflow_gateway_refuses_unauthenticated_inventory(monkeypatch):
    with _workflow_gateway(monkeypatch, [httpx.Response(200, json={"data": []})]) as (
        _main, client, requests
    ):
        monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
        response = client.get("/workflows")

    assert response.status_code == 401
    assert requests == []


def test_workflow_detail_keeps_encoded_id_and_not_found_mapping(monkeypatch):
    with _workflow_gateway(monkeypatch, [httpx.Response(404, json={"detail": "not found"})]) as (
        _main, client, requests
    ):
        response = client.get("/workflows/owned%3Fname%23tail")

    assert response.status_code == 404
    assert requests[0].url.raw_path == b"/api/v1/workflows/owned%3Fname%23tail"


@pytest.mark.asyncio
async def test_list_workflows_follows_n8n_cursor_pages():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={"data": [{"id": "first"}], "nextCursor": "page-2"},
            )
        return httpx.Response(200, json={"data": [{"id": "second"}]})

    client = N8nClient(base_url="http://n8n.test", api_key="secret")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        workflows = await client.list_workflows()
    finally:
        await client.aclose()

    assert [workflow["id"] for workflow in workflows] == ["first", "second"]
    assert requests[0].url.params["limit"] == "250"
    assert "cursor" not in requests[0].url.params
    assert requests[1].url.params["cursor"] == "page-2"
    assert requests[1].headers["X-N8N-API-KEY"] == "secret"


@pytest.mark.asyncio
async def test_list_workflows_rejects_repeated_cursor():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "nextCursor": "stuck"})

    client = N8nClient(base_url="http://n8n.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="repeated cursor"):
            await client.list_workflows()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_list_workflows_caps_total_pages():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": [], "nextCursor": str(calls)})

    client = N8nClient(base_url="http://n8n.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="exceeded 100 pages"):
            await client.list_workflows()
    finally:
        await client.aclose()

    assert calls == 100
