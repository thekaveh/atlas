import httpx
import pytest

from n8n_client import N8nClient


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
