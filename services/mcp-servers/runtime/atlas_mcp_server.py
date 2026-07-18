from __future__ import annotations

import os
import re
from typing import Any

try:
    from fastmcp import FastMCP
except ImportError:  # pragma: no cover - lets guard tests run without runtime deps.
    FastMCP = None  # type: ignore[assignment]


# Streamable HTTP path and the Host allowlist for FastMCP 3's host/origin
# protection. Direct loopback forms (127.0.0.1 / localhost / ::1) are always
# permitted by FastMCP; Atlas additionally reaches this runtime by the Compose
# service hostname (`mcp-servers`, in-network DNS) and the Kong route hostname
# (`mcp.localhost`). Any other Host/Origin is rejected (421/403). Ports are
# stripped before matching, so no port suffixes are needed here.
_HTTP_PATH = "/mcp"
_ALLOWED_HOSTS = ("mcp-servers", "mcp.localhost")


_SQL_FORBIDDEN = re.compile(
    r"\b("
    r"alter|analyze|call|comment|copy|create|delete|do|drop|execute|grant|"
    r"insert|listen|merge|notify|refresh|reindex|revoke|select\s+into|"
    r"truncate|update|vacuum"
    r")\b",
    re.IGNORECASE,
)
_CYPHER_FORBIDDEN = re.compile(
    r"\b("
    r"call\s+dbms|call\s+apoc\.periodic|create|delete|detach|drop|load\s+csv|"
    r"merge|remove|set"
    r")\b",
    re.IGNORECASE,
)


def _without_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n\r]*", " ", text)
    return text.strip()


def clamp_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return min(parsed, maximum)


def is_safe_postgres_read(sql: str) -> bool:
    statement = _without_comments(sql)
    if not statement or ";" in statement:
        return False
    lowered = statement.lower().lstrip()
    if not lowered.startswith(("select", "with", "show", "explain")):
        return False
    return _SQL_FORBIDDEN.search(statement) is None


def is_safe_neo4j_read(cypher: str) -> bool:
    statement = _without_comments(cypher)
    if not statement or ";" in statement:
        return False
    lowered = statement.lower().lstrip()
    if not lowered.startswith(("match", "return", "with", "call db.", "call apoc.meta")):
        return False
    return _CYPHER_FORBIDDEN.search(statement) is None


def bounded_neo4j_cypher(cypher: str) -> str:
    statement = _without_comments(cypher)
    return f"CALL {{\n{statement}\n}}\nRETURN *\nLIMIT $atlas_limit"


def _env_int(name: str, default: int) -> int:
    return clamp_limit(os.getenv(name), default=default, maximum=10_000)


def _postgres_dsn() -> str:
    host = os.getenv("SUPABASE_DB_HOST", "supabase-db")
    port = os.getenv("SUPABASE_DB_PORT", "5432")
    dbname = os.getenv("SUPABASE_DB_NAME", "postgres")
    user = os.getenv("SUPABASE_DB_USER", "supabase_admin")
    password = os.getenv("SUPABASE_DB_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def postgres_query(sql: str, limit: int | None = None) -> dict[str, Any]:
    if not is_safe_postgres_read(sql):
        raise ValueError("Only single-statement read-only Postgres queries are allowed.")

    import psycopg
    from psycopg.rows import dict_row

    max_rows = _env_int("MCP_POSTGRES_MAX_ROWS", 50)
    row_limit = clamp_limit(limit, default=max_rows, maximum=max_rows)
    timeout_ms = _env_int("MCP_TOOL_TIMEOUT_SECONDS", 15) * 1000

    with psycopg.connect(_postgres_dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN READ ONLY")
            # SET does not accept bind parameters — with psycopg's server-side
            # binding "SET LOCAL statement_timeout = %s" is sent as "= $1" and
            # Postgres rejects it with a syntax error, killing every query.
            # set_config(..., is_local=true) is the parameter-safe SET LOCAL.
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),)
            )
            cur.execute(sql)
            rows = cur.fetchmany(row_limit)
            cur.execute("ROLLBACK")
    return {"rows": rows, "returned": len(rows), "limit": row_limit}


def neo4j_read_cypher(cypher: str, limit: int | None = None) -> dict[str, Any]:
    if not is_safe_neo4j_read(cypher):
        raise ValueError("Only read-only Neo4j Cypher queries are allowed.")

    from neo4j import READ_ACCESS, GraphDatabase, Query

    max_rows = _env_int("MCP_NEO4J_MAX_ROWS", _env_int("MCP_POSTGRES_MAX_ROWS", 50))
    row_limit = clamp_limit(limit, default=max_rows, maximum=max_rows)
    timeout = _env_int("MCP_TOOL_TIMEOUT_SECONDS", 15)
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://neo4j-graph-db:7687"),
        auth=(
            os.getenv("GRAPH_DB_USER", "neo4j"),
            os.getenv("GRAPH_DB_PASSWORD", ""),
        ),
    )
    try:
        with driver.session(
            # READ_ACCESS ("READ") is the session access-mode constant; a
            # routing-scheme URI (neo4j://, neo4j+s:// — e.g. external Aura)
            # validates it via check_access_mode and rejects anything else.
            # RoutingControl.READ ("r") is only valid for execute_query's
            # routing_ arg, not here — it raises ConfigurationError on a
            # routing pool (bolt:// ignores access mode, so both "work" there).
            database=os.getenv("NEO4J_DATABASE", "neo4j"),
            default_access_mode=READ_ACCESS,
        ) as session:
            # A bare `timeout=` kwarg on session.run() is merged into the Cypher
            # *parameters* (and silently ignored), not applied as a transaction
            # timeout. Query(..., timeout=) is the driver's per-transaction
            # timeout; atlas_limit remains the query parameter.
            result = session.run(
                Query(bounded_neo4j_cypher(cypher), timeout=timeout),
                atlas_limit=row_limit,
            )
            rows = [record.data() for record in result.fetch(row_limit)]
    finally:
        driver.close()
    return {"rows": rows, "returned": len(rows), "limit": row_limit}


def neo4j_schema() -> dict[str, Any]:
    return neo4j_read_cypher("CALL db.schema.visualization()")


def searxng_web_search(query: str, limit: int | None = None) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        raise ValueError("query must be non-empty.")

    import requests

    max_results = _env_int("MCP_SEARXNG_MAX_RESULTS", 5)
    result_limit = clamp_limit(limit, default=max_results, maximum=max_results)
    timeout = _env_int("MCP_TOOL_TIMEOUT_SECONDS", 15)
    base_url = os.getenv("SEARXNG_URL", "http://searxng:8080").rstrip("/")
    response = requests.get(
        f"{base_url}/search",
        params={"q": query, "format": "json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results", [])[:result_limit]
    return {
        "query": query,
        "results": results,
        "returned": len(results),
        "limit": result_limit,
    }


def build_server():
    if FastMCP is None:
        raise RuntimeError("fastmcp package is not installed.")
    # FastMCP 3: transport settings (host/port/path/stateless/json) belong to
    # run()/http_app(), not the constructor.
    mcp = FastMCP("Atlas Curated MCP Servers")
    mcp.tool(description="Run a bounded, read-only SQL query against Atlas Postgres.")(postgres_query)
    mcp.tool(description="Inspect the Atlas Neo4j graph schema.")(neo4j_schema)
    mcp.tool(description="Run a bounded, read-only Cypher query against Atlas Neo4j.")(neo4j_read_cypher)
    mcp.tool(description="Search the in-stack SearXNG instance.")(searxng_web_search)
    return mcp


def run_server(mcp=None) -> None:
    """Serve over Streamable HTTP with the same `/mcp`, stateless, JSON-response
    behavior as before, now via FastMCP 3's `run()` transport API plus explicit
    Host/Origin protection (Kong Basic Auth + ACL remain the external boundary)."""
    (mcp or build_server()).run(
        transport="http",
        host="0.0.0.0",
        port=8000,
        path=_HTTP_PATH,
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=list(_ALLOWED_HOSTS),
    )


if __name__ == "__main__":
    run_server()
