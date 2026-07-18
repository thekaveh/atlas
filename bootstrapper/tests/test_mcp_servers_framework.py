"""Framework-level tests for the Curated MCP runtime on standalone FastMCP 3 (#598).

These exercise the *real* FastMCP framework — the in-memory ``Client``, the
Streamable HTTP transport at ``/mcp``, and the Host/Origin guard — rather than
calling the underlying Python functions directly (that guard-logic coverage lives
in ``test_mcp_servers_service.py``). ``fastmcp`` is a runtime-image dependency and
is not installed in the bootstrapper test venv, so the whole module skips there
and runs only where the mcp-servers runtime deps are present.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

fastmcp = pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = REPO_ROOT / "services" / "mcp-servers" / "runtime" / "atlas_mcp_server.py"


def _runtime_module():
    spec = importlib.util.spec_from_file_location("atlas_mcp_server_fw", RUNTIME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


EXPECTED_TOOLS = {
    "postgres_query": {
        "props": {"sql", "limit"},
        "desc": "Run a bounded, read-only SQL query against Atlas Postgres.",
    },
    "neo4j_schema": {
        "props": set(),
        "desc": "Inspect the Atlas Neo4j graph schema.",
    },
    "neo4j_read_cypher": {
        "props": {"cypher", "limit"},
        "desc": "Run a bounded, read-only Cypher query against Atlas Neo4j.",
    },
    "searxng_web_search": {
        "props": {"query", "limit"},
        "desc": "Search the in-stack SearXNG instance.",
    },
}


# ── standalone-framework identity ───────────────────────────────────────────

def test_runtime_imports_fastmcp_from_standalone_distribution() -> None:
    srv = _runtime_module()
    assert srv.FastMCP is not None
    assert srv.FastMCP.__module__.split(".")[0] == "fastmcp"


def test_inmemory_client_lists_exactly_the_four_tools_with_schemas() -> None:
    srv = _runtime_module()

    async def go():
        async with Client(srv.build_server()) as client:
            return await client.list_tools()

    tools = _run(go())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == set(EXPECTED_TOOLS)
    for name, expected in EXPECTED_TOOLS.items():
        tool = by_name[name]
        assert tool.description == expected["desc"], name
        props = set((tool.inputSchema or {}).get("properties", {}))
        assert props == expected["props"], name


# ── tool invocation with mocked upstream I/O ────────────────────────────────

def test_searxng_tool_returns_results_and_clamps_limit(monkeypatch) -> None:
    srv = _runtime_module()
    import requests

    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"results": [{"title": f"r{i}"} for i in range(50)]}

    captured: dict = {}

    def _fake_get(url, params=None, timeout=None):
        captured.update(url=url, params=params, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(requests, "get", _fake_get)

    async def go():
        async with Client(srv.build_server()) as client:
            return await client.call_tool("searxng_web_search", {"query": "atlas", "limit": 100})

    result = _run(go())
    data = result.data
    # default MCP_SEARXNG_MAX_RESULTS is 5 → an over-large limit is clamped.
    assert data["limit"] == 5
    assert data["returned"] == 5
    assert data["query"] == "atlas"
    assert captured["params"] == {"q": "atlas", "format": "json"}
    assert captured["url"].endswith("/search")


def test_postgres_tool_runs_read_only_bounded_transaction(monkeypatch) -> None:
    srv = _runtime_module()
    import psycopg

    class _Cursor:
        def __init__(self) -> None:
            self.executed: list = []
            self.fetched_n: int | None = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, query, params=None):
            self.executed.append((query, params))

        def fetchmany(self, size):
            self.fetched_n = size
            return [{"id": 1}, {"id": 2}]

    class _Conn:
        def __init__(self, cursor) -> None:
            self._cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return self._cursor

    cursor = _Cursor()
    monkeypatch.setattr(psycopg, "connect", lambda dsn, row_factory=None: _Conn(cursor))

    async def go():
        async with Client(srv.build_server()) as client:
            return await client.call_tool("postgres_query", {"sql": "select id from public.users", "limit": 2})

    result = _run(go())
    data = result.data
    assert data == {"rows": [{"id": 1}, {"id": 2}], "returned": 2, "limit": 2}

    statements = [query for query, _ in cursor.executed]
    assert statements[0] == "BEGIN READ ONLY"
    assert any("statement_timeout" in stmt for stmt in statements)
    assert "select id from public.users" in statements
    assert statements[-1] == "ROLLBACK"
    # the timeout is applied via the parameter-safe set_config (SET LOCAL); a
    # bare "SET LOCAL statement_timeout = %s" is a syntax error on real Postgres.
    timeout_param = next(params for query, params in cursor.executed if "statement_timeout" in query)
    assert timeout_param == ("15000",)
    assert cursor.fetched_n == 2


def test_neo4j_tool_uses_read_routing_and_bounded_cypher(monkeypatch) -> None:
    srv = _runtime_module()
    import neo4j

    captured: dict = {}

    class _Record:
        def __init__(self, payload):
            self._payload = payload

        def data(self):
            return self._payload

    class _Result:
        def fetch(self, size):
            captured["fetch"] = size
            return [_Record({"n": 1})]

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run(self, query, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            return _Result()

    class _Driver:
        def session(self, **kwargs):
            captured["session_kwargs"] = kwargs
            return _Session()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *a, **k: _Driver())

    async def go():
        async with Client(srv.build_server()) as client:
            return await client.call_tool(
                "neo4j_read_cypher", {"cypher": "MATCH (n) RETURN n", "limit": 3}
            )

    result = _run(go())
    data = result.data
    assert data == {"rows": [{"n": 1}], "returned": 1, "limit": 3}
    assert captured["session_kwargs"]["default_access_mode"] == neo4j.RoutingControl.READ
    # timeout is a transaction timeout carried by the Query object, NOT a Cypher
    # parameter (a bare timeout= kwarg on session.run would be silently ignored).
    assert captured["query"].text == srv.bounded_neo4j_cypher("MATCH (n) RETURN n")
    assert captured["query"].timeout == 15
    assert captured["kwargs"]["atlas_limit"] == 3
    assert "timeout" not in captured["kwargs"]
    assert captured["closed"] is True


def test_tools_reject_writes_and_empty_inputs_with_sanitized_errors() -> None:
    srv = _runtime_module()

    async def call(name, args):
        async with Client(srv.build_server()) as client:
            await client.call_tool(name, args)

    with pytest.raises(ToolError, match="read-only"):
        _run(call("postgres_query", {"sql": "insert into public.users values (1)"}))
    with pytest.raises(ToolError, match="read-only"):
        _run(call("neo4j_read_cypher", {"cypher": "CREATE (n:User)"}))
    with pytest.raises(ToolError, match="non-empty"):
        _run(call("searxng_web_search", {"query": "   "}))


# ── Streamable HTTP transport + Host/Origin guard ───────────────────────────

def _http_app(srv):
    return srv.build_server().http_app(
        path=srv._HTTP_PATH,
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=list(srv._ALLOWED_HOSTS),
    )


def _parse_jsonrpc(response):
    import json

    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
    return response.json()


def test_streamable_http_smoke_initialize_list_and_call(monkeypatch) -> None:
    import httpx

    srv = _runtime_module()
    import requests

    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: type(
            "R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"results": [{"t": 1}]}}
        )(),
    )
    app = _http_app(srv)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
        "Host": "127.0.0.1",
    }

    async def go():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                init = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "smoke", "version": "1"},
                        },
                    },
                )
                listed = await client.post(
                    "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
                )
                called = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "searxng_web_search", "arguments": {"query": "hi", "limit": 1}},
                    },
                )
                return init, listed, called

    init, listed, called = _run(go())
    assert init.status_code == 200
    assert _parse_jsonrpc(init)["result"]["serverInfo"]["name"] == "Atlas Curated MCP Servers"
    assert listed.status_code == 200
    tool_names = {tool["name"] for tool in _parse_jsonrpc(listed)["result"]["tools"]}
    assert tool_names == set(EXPECTED_TOOLS)
    assert called.status_code == 200
    assert _parse_jsonrpc(called)["result"]["structuredContent"]["limit"] == 1


def test_host_origin_guard_allows_atlas_hosts_and_rejects_others() -> None:
    import httpx

    srv = _runtime_module()
    app = _http_app(srv)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    }
    base = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }

    async def statuses(hosts):
        results = {}
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://placeholder") as client:
                for host in hosts:
                    response = await client.post("/mcp", headers={**base, "Host": host}, json=body)
                    results[host] = response.status_code
        return results

    allowed = _run(statuses(["mcp-servers", "mcp-servers:8000", "mcp.localhost", "127.0.0.1", "localhost"]))
    assert all(code != 421 for code in allowed.values()), allowed

    rejected = _run(statuses(["evil.example.com", "attacker.localhost", "comfyui.localhost"]))
    assert all(code == 421 for code in rejected.values()), rejected
