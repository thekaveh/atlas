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


# APOC core is loaded (Neo4j compose NEO4J_PLUGINS): apoc.load.* sends HTTP to
# any backend-network service and apoc.meta.*.of / apoc.cypher.* run Cypher
# strings, all from a "read-only" session. Name-pattern filters were bypassed
# (unicode escapes, string concatenation inside .of), so APOC is allowed only
# as one of these exact argument-free schema calls; any other mention of
# "apoc" (even in a string literal) is rejected.
_APOC_SCHEMA_CALL = re.compile(
    r"\s*call\s+apoc\.meta\.(?:schema|stats|data)\s*\(\s*\)"
    r"(?:\s+yield\s+[a-z_][a-z0-9_]*(?:\s*,\s*[a-z_][a-z0-9_]*)*"
    r"(?:\s+return\s+[a-z_][a-z0-9_]*(?:\s*,\s*[a-z_][a-z0-9_]*)*)?)?\s*",
    re.IGNORECASE,
)


def _normalize_cypher_names(statement: str) -> str:
    """Replace backticks with spaces (deleting them glued `y`SET into one word
    and hid the keyword) and drop spaces around dots (checking only)."""
    return re.sub(r"\s*\.\s*", ".", statement.replace("`", " "))


_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]?[A-Za-z0-9_]*\$")


def _in_word(text: str, i: int) -> bool:
    """True when position ``i`` continues an identifier (`$` included)."""
    return i >= 0 and (text[i].isalnum() or text[i] in "_$")


def _honors_backslash(text: str, start: int, dialect: str) -> bool:
    """Cypher strings and PostgreSQL E'...' strings honor backslash escapes."""
    if dialect == "cypher":
        return True
    return (
        text[start] == "'" and start > 0 and text[start - 1] in "eE"
        and not _in_word(text, start - 2)
    )


def _quoted_end(text: str, start: int, dialect: str) -> int:
    """Index just past the quoted run opening at ``start``."""
    quote, j, n = text[start], start + 1, len(text)
    backslash = _honors_backslash(text, start, dialect)
    while j < n:
        if backslash and text[j] == "\\":
            j += 2
        elif text[j] != quote:
            j += 1
        elif dialect == "sql" and text.startswith(quote * 2, j):
            j += 2  # SQL doubles a quote to escape it
        else:
            return j + 1
    return n


def _comment_end(text: str, start: int, line_comment: str) -> int | None:
    """Index just past a comment opening at ``start``, or None."""
    if text.startswith(line_comment, start):
        # PostgreSQL ends a line comment at CR as well as LF.
        ends = [i for i in (text.find("\n", start), text.find("\r", start)) if i != -1]
        return min(ends) if ends else len(text)
    if text.startswith("/*", start):
        return _block_comment_end(text, start, nested=line_comment == "--")
    return None


def _block_comment_end(text: str, start: int, *, nested: bool) -> int:
    """End of the block comment at ``start``; SQL comments nest, Cypher's don't."""
    depth, j, n = 1, start + 2, len(text)
    while j < n and depth:
        if nested and text.startswith("/*", j):
            depth, j = depth + 1, j + 2
        elif text.startswith("*/", j):
            depth, j = depth - 1, j + 2
        else:
            j += 1
    return j


def _without_comments(text: str, *, dialect: str = "sql") -> str:
    """Drop comments, keeping quoted text verbatim.

    Literal-aware on purpose: a regex that treats ``--`` inside ``'--'`` as a
    comment hid everything after it from the guards while the database still
    ran the original text (``SELECT '--'; COMMIT; …``). Quoted text is kept,
    so a ``;`` or keyword inside a literal still fails the guard closed.
    ``dialect`` selects the comment syntax: ``sql`` (``--``, ``/* */``,
    ``$tag$`` quoting) or ``cypher`` (``//``, ``/* */``, backslash escapes).
    """
    line_comment = "--" if dialect == "sql" else "//"
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        comment_end = _comment_end(text, i, line_comment)
        # `$` continues an identifier (a$b$), so a tag never opens mid-word.
        tag = (
            _DOLLAR_TAG.match(text, i)
            if dialect == "sql" and not _in_word(text, i - 1) else None
        )
        if comment_end is not None:
            out.append(" ")
            i = comment_end
        elif text[i] in "'\"`":
            end = _quoted_end(text, i, dialect)
            out.append(text[i:end])
            i = end
        elif tag:
            close = text.find(tag.group(0), tag.end())
            end = n if close == -1 else close + len(tag.group(0))
            out.append(text[i:end])
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out).strip()


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
    statement = _without_comments(cypher, dialect="cypher")
    if not statement or ";" in statement:
        return False
    # Neo4j decodes \uXXXX in names (and in place of the dot), which hid
    # `\u0061poc.load.json`; reject backslashes outright (fail closed).
    if "\\" in statement:
        return False
    normalized = _normalize_cypher_names(statement)
    lowered = normalized.lower().lstrip()
    if not lowered.startswith(("match", "return", "with", "call db.", "call apoc.meta")):
        return False
    if "apoc" in lowered and not _APOC_SCHEMA_CALL.fullmatch(normalized):
        return False
    return _CYPHER_FORBIDDEN.search(normalized) is None


def bounded_neo4j_cypher(cypher: str) -> str:
    statement = _without_comments(cypher, dialect="cypher")
    # A standalone procedure call (`CALL db.*` / `CALL apoc.meta.*`, allowed by
    # is_safe_neo4j_read for the schema tools) CANNOT be wrapped in a
    # `CALL { … } RETURN *` subquery: inside a subquery a procedure needs an
    # explicit YIELD, and the unit subquery yields no variables, so Neo4j rejects
    # `CALL { CALL db.schema.visualization() } RETURN *` at parse time — which
    # broke neo4j_schema and every CALL-based read 100%. Run such statements
    # unwrapped; the row cap is still enforced by result.fetch(row_limit) at the
    # Python layer. MATCH/WITH statements end in RETURN, so wrapping them applies
    # an additional server-side LIMIT.
    if statement.lstrip().lower().startswith("call "):
        return statement
    return f"CALL {{\n{statement}\n}}\nRETURN *\nLIMIT $atlas_limit"


def _env_int(name: str, default: int) -> int:
    return clamp_limit(os.getenv(name), default=default, maximum=10_000)


def _postgres_connection_kwargs() -> dict[str, str]:
    """Return discrete libpq parameters so arbitrary userinfo stays lossless."""
    return {
        "host": os.getenv("MCP_POSTGRES_DB_HOST", "supabase-db"),
        "port": os.getenv("MCP_POSTGRES_DB_PORT", "5432"),
        "dbname": os.getenv("MCP_POSTGRES_DB_NAME", "postgres"),
        "user": os.environ["MCP_POSTGRES_DB_USER"],
        "password": os.environ["MCP_POSTGRES_DB_PASSWORD"],
    }


def postgres_query(sql: str, limit: int | None = None) -> dict[str, Any]:
    if not is_safe_postgres_read(sql):
        raise ValueError("Only single-statement read-only Postgres queries are allowed.")

    import psycopg
    from psycopg.rows import dict_row

    max_rows = _env_int("MCP_POSTGRES_MAX_ROWS", 50)
    row_limit = clamp_limit(limit, default=max_rows, maximum=max_rows)
    timeout_ms = _env_int("MCP_TOOL_TIMEOUT_SECONDS", 15) * 1000

    # autocommit=True is REQUIRED: with psycopg3's default (autocommit=False) the
    # driver emits its OWN BEGIN before the first execute(), so the explicit
    # "BEGIN READ ONLY" below runs inside an already-open transaction — a no-op
    # ("there is already a transaction in progress") that leaves the session
    # READ WRITE, defeating the read-only guard (e.g. SELECT nextval() would
    # advance a sequence). With autocommit=True our explicit BEGIN opens the
    # transaction and its READ ONLY characteristic actually applies.
    with psycopg.connect(
        **_postgres_connection_kwargs(), autocommit=True, row_factory=dict_row
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN READ ONLY")
            # SET does not accept bind parameters — with psycopg's server-side
            # binding "SET LOCAL statement_timeout = %s" is sent as "= $1" and
            # Postgres rejects it with a syntax error, killing every query.
            # set_config(..., is_local=true) is the parameter-safe SET LOCAL.
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),)
            )
            # Execute exactly the text the guard approved: if the scanner ever
            # disagrees with PostgreSQL about where a literal ends, the result
            # is a syntax error, never hidden statements running.
            cur.execute(_without_comments(sql))
            rows = _json_safe(cur.fetchmany(row_limit))
            cur.execute("ROLLBACK")
    return {"rows": rows, "returned": len(rows), "limit": row_limit}


def neo4j_read_cypher(cypher: str, limit: int | None = None) -> dict[str, Any]:
    if not is_safe_neo4j_read(cypher):
        raise ValueError("Only read-only Neo4j Cypher queries are allowed.")
    uri = (os.getenv("NEO4J_URI") or "").strip()
    if not uri:  # compose passes it blank when NEO4J_GRAPH_DB_SOURCE=disabled
        raise ValueError("Neo4j is not configured (NEO4J_GRAPH_DB_SOURCE=disabled).")

    from neo4j import READ_ACCESS, GraphDatabase, Query

    max_rows = _env_int("MCP_NEO4J_MAX_ROWS", _env_int("MCP_POSTGRES_MAX_ROWS", 50))
    row_limit = clamp_limit(limit, default=max_rows, maximum=max_rows)
    timeout = _env_int("MCP_TOOL_TIMEOUT_SECONDS", 15)
    driver = GraphDatabase.driver(
        uri,
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
            try:
                rows = _fetch_rows(session, Query(bounded_neo4j_cypher(cypher), timeout=timeout), row_limit)
            except Exception as exc:  # noqa: BLE001 - only the alias rejection is retried
                # The server-side LIMIT wrapper (CALL { ... } RETURN *) rejects
                # any unaliased RETURN expression (`RETURN n.name`,
                # `count(*)`), which is what models usually write. Re-run it
                # unwrapped; result.fetch(row_limit) still caps the rows.
                if "must be aliased" not in str(exc):
                    raise
                statement = _without_comments(cypher, dialect="cypher")
                rows = _fetch_rows(session, Query(statement, timeout=timeout), row_limit)
    finally:
        driver.close()
    return {"rows": rows, "returned": len(rows), "limit": row_limit}


def _fetch_rows(session: Any, query: Any, row_limit: int) -> list[dict[str, Any]]:
    result = session.run(query, atlas_limit=row_limit)
    return _json_safe([record.data() for record in result.fetch(row_limit)])


def _json_safe(value: Any) -> Any:
    """Make driver values JSON-serializable for the tool's structured output.

    Neo4j temporal/spatial values and non-UTF-8 bytea failed the whole call
    ("outputSchema defined but no structured output returned").
    """
    # neo4j checks first: Duration and spatial points subclass tuple.
    if hasattr(value, "iso_format"):
        return value.iso_format()  # neo4j.time Date/Time/DateTime/Duration
    if type(value).__module__.startswith("neo4j"):
        return str(value)  # e.g. neo4j.spatial points, keeping the SRID
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()  # PostgreSQL's bytea hex form
    return value


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
