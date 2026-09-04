"""Framework-level tests for the Curated MCP runtime on standalone FastMCP 3 (#598).

These exercise the *real* FastMCP framework — the in-memory ``Client``, the
Streamable HTTP transport at ``/mcp``, and the Host/Origin guard — rather than
calling the underlying Python functions directly (that guard-logic coverage lives
in ``test_mcp_servers_service.py``). ``fastmcp`` is a runtime-image dependency and
is not installed in the bootstrapper test venv, so the whole module skips there
and runs only where the mcp-servers runtime deps are present.
"""
from __future__ import annotations

import ast
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import inspect
import json
import os
import socket
import sys
from pathlib import Path

import pytest

fastmcp = pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = REPO_ROOT / "services" / "mcp-servers" / "runtime" / "atlas_mcp_server.py"
NOTEBOOK = (
    REPO_ROOT
    / "services"
    / "jupyterhub"
    / "build"
    / "notebooks"
    / "15_mcp_clients.ipynb"
)


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

    monkeypatch.setenv("MCP_POSTGRES_DB_USER", "atlas_mcp_test")
    monkeypatch.setenv("MCP_POSTGRES_DB_PASSWORD", "fixture-password")

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
    connect_kwargs: dict = {}

    def _fake_connect(**kwargs):
        connect_kwargs.update(kwargs)
        return _Conn(cursor)

    monkeypatch.setattr(psycopg, "connect", _fake_connect)

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
    # autocommit=True is required so the explicit BEGIN READ ONLY actually opens
    # the read-only transaction (psycopg3's default would emit its own BEGIN
    # first, making READ ONLY a silent no-op that leaves the session READ WRITE).
    assert connect_kwargs.get("autocommit") is True
    assert connect_kwargs["user"] == "atlas_mcp_test"
    assert connect_kwargs["password"] == "fixture-password"
    assert connect_kwargs["host"] == "supabase-db"
    assert connect_kwargs["port"] == "5432"
    assert connect_kwargs["dbname"] == "postgres"


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
    # READ_ACCESS ("READ") is the correct session access-mode constant; the
    # real routing pool's check_access_mode rejects RoutingControl.READ ("r").
    assert captured["session_kwargs"]["default_access_mode"] == neo4j.READ_ACCESS
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

def _http_app(srv, mcp=None):
    return (mcp or srv.build_server()).http_app(
        path=srv._HTTP_PATH,
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=list(srv._ALLOWED_HOSTS),
    )


@asynccontextmanager
async def _serve_on_prebound_loopback(app, *, socket_factory=socket.socket):
    """Serve ``app`` on an owned ephemeral socket without a port race."""
    import uvicorn

    sock = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
    task = None
    server = None
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_level="error",
                access_log=False,
                lifespan="on",
            )
        )
        server.install_signal_handlers = lambda: None
        task = asyncio.create_task(server.serve(sockets=[sock]))
        deadline = asyncio.get_running_loop().time() + 10
        while not server.started:
            if task.done():
                task.result()
                raise AssertionError("Uvicorn stopped before reporting startup")
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("Uvicorn did not report startup within 10 seconds")
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        if server is not None:
            server.should_exit = True
        try:
            if task is not None:
                await asyncio.wait_for(task, timeout=10)
        finally:
            sock.close()


async def _execute_curated_mcp_notebook(endpoint: str) -> dict[str, object]:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    namespace: dict[str, object] = {"__name__": "__atlas_notebook_smoke__"}
    old_endpoint = os.environ.get("MCP_SERVERS_URL")
    os.environ["MCP_SERVERS_URL"] = endpoint
    try:
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            code = compile(
                "".join(cell["source"]),
                f"{NOTEBOOK.name}:cell-{index}",
                "exec",
                flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            )
            result = eval(code, namespace)
            if inspect.isawaitable(result):
                await result
    finally:
        if old_endpoint is None:
            os.environ.pop("MCP_SERVERS_URL", None)
        else:
            os.environ["MCP_SERVERS_URL"] = old_endpoint
    return namespace


def test_prebound_server_surfaces_task_failure_and_releases_resources(
    monkeypatch,
) -> None:
    import gc
    import uvicorn

    class _BrokenServer:
        def __init__(self, _config) -> None:
            self.started = False
            self.should_exit = False

        async def serve(self, *, sockets) -> None:
            assert sockets and sockets[0].getsockname()[0] == "127.0.0.1"
            raise RuntimeError("fixture server task failed")

    monkeypatch.setattr(uvicorn, "Server", _BrokenServer)
    created_sockets = []

    def tracking_socket(*args, **kwargs):
        tracked = socket.socket(*args, **kwargs)
        created_sockets.append(tracked)
        return tracked

    async def go() -> None:
        with pytest.raises(RuntimeError, match="fixture server task failed"):
            async with _serve_on_prebound_loopback(
                object(), socket_factory=tracking_socket
            ):
                raise AssertionError("broken server must never yield")

    _run(go())
    assert len(created_sockets) == 1
    assert created_sockets[0].fileno() == -1
    # The lane runs with -W error. Force finalization here so an unclosed task
    # or socket is reported by this test rather than after pytest unconfigures.
    gc.collect()


def test_curated_notebook_executes_every_cell_over_real_streamable_http(
    monkeypatch, capsys
) -> None:
    srv = _runtime_module()
    import requests

    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"results": [{"title": "bounded fixture"}]}

    def fake_get(url, params=None, timeout=None):
        captured.update(url=url, params=params, timeout=timeout)
        return _Response()

    monkeypatch.setattr(requests, "get", fake_get)

    async def go():
        async with _serve_on_prebound_loopback(_http_app(srv)) as endpoint:
            return await _execute_curated_mcp_notebook(endpoint)

    namespace = _run(go())
    output = capsys.readouterr().out
    assert "postgres_query: Run a bounded, read-only SQL query" in output
    assert "searxng_web_search" in output
    assert "Caught expected missing-tool error" in output
    assert "Hello, Atlas!" in output
    assert captured["params"] == {"q": "Atlas", "format": "json"}
    assert captured["timeout"] == 15
    search_tool = namespace["MCP_TOOLS"]["searxng_web_search"]
    assert search_tool.name == "searxng_web_search"
    assert search_tool.inputSchema["properties"]["query"]["type"] == "string"
    assert search_tool.inputSchema["properties"]["limit"]["default"] is None
    assert search_tool.inputSchema["required"] == ["query"]


def test_curated_notebook_disabled_mode_executes_every_cell_without_network(
    monkeypatch, capsys
) -> None:
    actual_client = Client

    def local_only_client(target, *args, **kwargs):
        if isinstance(target, str):
            raise AssertionError("disabled notebook must not create an HTTP client")
        return actual_client(target, *args, **kwargs)

    monkeypatch.setattr(fastmcp, "Client", local_only_client)
    namespace = _run(_execute_curated_mcp_notebook(""))
    output = capsys.readouterr().out
    assert "MCP_SERVERS_URL is not set" in output
    assert "MCP disabled — skipping discovery" in output
    assert "MCP disabled — skipping invocation" in output
    assert "MCP disabled — skipping error handling" in output
    assert "Hello, Atlas!" in output
    assert namespace["MCP_TOOLS"] is None


class _DiscoveryFailureContext:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, *_exc_info) -> None:
        return None


def _with_cause(outer: Exception, cause: Exception) -> Exception:
    outer.__cause__ = cause
    return outer


def _fixture_text(*parts: str) -> str:
    return "".join(parts)


def _basic_auth_url(host: str, *credential_parts: str) -> str:
    return "".join(
        ("https://", "atlas", ":", _fixture_text(*credential_parts), "@", host, "/mcp")
    )


@pytest.mark.parametrize(
    ("error", "category"),
    (
        (ConnectionError("connection refused"), "unreachable"),
        (TimeoutError("request timed out"), "timeout"),
        (
            _with_cause(
                RuntimeError("wrapped client failure"),
                RuntimeError(
                    "HTTP 401 Unauthorized at "
                    f"{_basic_auth_url('example.invalid', 'super', 'secret')}"
                    f"?token={_fixture_text('top', 'secret')}"
                ),
            ),
            "authentication",
        ),
        (RuntimeError("HTTP 503 Service Unavailable"), "server"),
        (RuntimeError("record 401 failed validation"), "client/server"),
    ),
)
def test_curated_notebook_categorizes_discovery_failures_and_continues(
    monkeypatch, capsys, error: Exception, category: str
) -> None:
    actual_client = Client

    def routed_client(target, *args, **kwargs):
        if isinstance(target, str):
            return _DiscoveryFailureContext(error)
        return actual_client(target, *args, **kwargs)

    monkeypatch.setattr(fastmcp, "Client", routed_client)
    namespace = _run(
        _execute_curated_mcp_notebook(
            _basic_auth_url("failure.invalid", "endpoint", "secret")
        )
    )
    output = capsys.readouterr().out
    assert f"MCP discovery failed ({category})" in output
    assert "invocation skipped — discovery did not complete" in output
    assert "error-handling probe skipped — discovery did not complete" in output
    assert "Hello, Atlas!" in output
    assert namespace["MCP_TOOLS"] is None
    assert _fixture_text("super", "secret") not in output
    assert _fixture_text("top", "secret") not in output
    assert _fixture_text("endpoint", "secret") not in output


def test_curated_notebook_reports_missing_target_tool_separately_and_continues(
    capsys,
) -> None:
    srv = _runtime_module()
    mcp = fastmcp.FastMCP("missing-target")

    @mcp.tool()
    def identify(name: str) -> str:
        return f"identified:{name}"

    async def go():
        async with _serve_on_prebound_loopback(_http_app(srv, mcp)) as endpoint:
            return await _execute_curated_mcp_notebook(endpoint)

    namespace = _run(go())
    output = capsys.readouterr().out
    assert "MCP target tool missing: searxng_web_search" in output
    assert "MCP tool invocation failed" not in output
    assert "Hello, Atlas!" in output
    assert set(namespace["MCP_TOOLS"]) == {"identify"}


def test_curated_notebook_reports_actual_tool_failure_and_continues(
    monkeypatch, capsys
) -> None:
    srv = _runtime_module()
    import requests

    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.Timeout("fixture timed out")
        ),
    )

    async def go():
        async with _serve_on_prebound_loopback(_http_app(srv)) as endpoint:
            return await _execute_curated_mcp_notebook(endpoint)

    _run(go())
    output = capsys.readouterr().out
    assert "MCP tool invocation failed (timeout)" in output
    assert "MCP target tool missing" not in output
    assert "Caught expected missing-tool error" in output
    assert "Hello, Atlas!" in output


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
