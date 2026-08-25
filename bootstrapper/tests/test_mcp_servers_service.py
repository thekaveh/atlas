from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core.config_parser import ConfigParser
from services.service_config import ServiceConfig
from services.topology import get_topology, invalidate_cache
from tracks import is_in_track, load_tracks
from utils.source_override_manager import SourceOverrideManager


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "mcp-servers"
MANIFEST = SERVICE_DIR / "service.yml"
COMPOSE = SERVICE_DIR / "compose.yml"
README = SERVICE_DIR / "README.md"
RUNTIME = SERVICE_DIR / "runtime" / "atlas_mcp_server.py"
SUPABASE_MANIFEST = REPO_ROOT / "services" / "supabase" / "service.yml"
MEMORY_RLS_SQL = (
    REPO_ROOT / "services" / "supabase" / "db" / "scripts" / "14-backend-memory.sql"
)
NEO4J_COMPOSE = REPO_ROOT / "services" / "neo4j" / "compose.yml"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _runtime_module():
    spec = importlib.util.spec_from_file_location("atlas_mcp_server", RUNTIME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_contains(text, fragments):
    missing = tuple(fragment for fragment in fragments if fragment not in text)
    assert missing == ()


def test_mcp_servers_manifest_admission_contract() -> None:
    manifest = _manifest()

    assert manifest["name"] == "mcp-servers"
    assert manifest["category"] == "agents"
    assert manifest["containers"] == ["mcp-servers"]
    assert manifest["sources"]["var"] == "MCP_SERVERS_SOURCE"
    assert manifest["sources"]["default"] == "disabled"
    assert {option["id"] for option in manifest["sources"]["options"]} == {
        "container",
        "disabled",
    }
    assert manifest["depends_on"]["required"] == ["supabase", "neo4j", "searxng"]
    assert manifest["data_flow"]["calls"] == ["supabase", "neo4j", "searxng"]

    env_vars = {entry["name"]: entry for entry in manifest["env"]}
    assert env_vars["MCP_SERVERS_SOURCE"]["default"] == "disabled"
    assert env_vars["MCP_SERVERS_SCALE"]["auto_managed"] is True
    assert "default" not in env_vars["MCP_SERVERS_PORT"]

    row = manifest["rows"][0]
    assert row["display_name"] == "Curated MCP Servers"
    assert row["source_var"] == "MCP_SERVERS_SOURCE"
    assert row["port_var"] == "MCP_SERVERS_PORT"
    assert row["scale_var"] == "MCP_SERVERS_SCALE"
    assert row["alias"] == "mcp.localhost"


def test_mcp_servers_topology_alias_and_env_example_contract() -> None:
    invalidate_cache()
    topology = get_topology(REPO_ROOT / "services")
    rows = [row for row in topology.rows if row.manifest == "mcp-servers"]

    assert len(rows) == 1
    assert rows[0].category == "agents"
    assert rows[0].alias == "mcp.localhost"
    assert "mcp.localhost" in topology.aliases
    assert "MCP_SERVERS_PORT" in topology.port_defaults

    env_example = (REPO_ROOT / ".env.example").read_text()
    for expected in (
        "MCP_SERVERS_SOURCE=disabled",
        "MCP_SERVERS_IMAGE=python:3.12.13-slim",
        "MCP_SERVERS_PORT=",
        "MCP_SERVERS_SCALE=",
        "MCP_POSTGRES_MAX_ROWS=50",
        "MCP_SEARXNG_MAX_RESULTS=5",
        "MCP_TOOL_TIMEOUT_SECONDS=15",
    ):
        assert expected in env_example


def test_mcp_servers_track_membership_is_rag_and_engineering_only() -> None:
    registry = load_tracks()

    for track_key in ("gen-ai-rag", "gen-ai-eng", "all"):
        assert is_in_track(
            registry.by_key[track_key],
            "mcp-servers",
            always_on=registry.always_on,
        )

    for track_key in ("gen-ai-creative", "ml-eng", "data-eng"):
        assert not is_in_track(
            registry.by_key[track_key],
            "mcp-servers",
            always_on=registry.always_on,
        )


def test_mcp_servers_source_cli_mapping_exists() -> None:
    mgr = SourceOverrideManager(ConfigParser(str(REPO_ROOT)))

    assert mgr.source_mapping["mcp_servers_source"] == "MCP_SERVERS_SOURCE"
    assert mgr.collect_overrides(mcp_servers_source="container") == {
        "MCP_SERVERS_SOURCE": "container",
    }


def test_mcp_servers_scale_generation_and_dependency_gates() -> None:
    sc = ServiceConfig(config_parser=MagicMock())

    sc.service_sources = {
        "MCP_SERVERS_SOURCE": "disabled",
        "NEO4J_GRAPH_DB_SOURCE": "disabled",
        "SEARXNG_SOURCE": "disabled",
    }
    assert sc._generate_mcp_servers_config() == {"MCP_SERVERS_SCALE": "0"}

    sc.service_sources = {
        "MCP_SERVERS_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "container",
        "SEARXNG_SOURCE": "container",
    }
    assert sc._generate_mcp_servers_config() == {"MCP_SERVERS_SCALE": "1"}

    sc.service_sources = {
        "MCP_SERVERS_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "disabled",
        "SEARXNG_SOURCE": "container",
    }
    with pytest.raises(ValueError, match="MCP Servers require Neo4j"):
        sc._generate_mcp_servers_config()

    sc.service_sources = {
        "MCP_SERVERS_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "container",
        "SEARXNG_SOURCE": "disabled",
    }
    with pytest.raises(ValueError, match="MCP Servers require SearXNG"):
        sc._generate_mcp_servers_config()

    sc.service_sources = {
        "MCP_SERVERS_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "localhost",
        "SEARXNG_SOURCE": "container",
    }
    with pytest.raises(ValueError, match="MCP Servers require in-stack Neo4j"):
        sc._generate_mcp_servers_config()


def test_mcp_servers_compose_contract() -> None:
    service = _compose()["services"]["mcp-servers"]

    assert service["build"]["context"] == "./runtime"
    assert service["build"]["dockerfile"] == "../build/Dockerfile"
    assert service["build"]["args"]["BASE_IMAGE"] == "${MCP_SERVERS_IMAGE:-python:3.12.13-slim}"
    assert service["image"] == "${PROJECT_NAME}-mcp-servers:local"
    assert service["ports"] == ["127.0.0.1:${MCP_SERVERS_PORT}:8000"]
    assert service["environment"]["MCP_POSTGRES_MAX_ROWS"] == "${MCP_POSTGRES_MAX_ROWS:-50}"
    assert service["environment"]["MCP_SEARXNG_MAX_RESULTS"] == "${MCP_SEARXNG_MAX_RESULTS:-5}"
    assert service["environment"]["SEARXNG_URL"] == "http://searxng:8080"
    assert service["environment"]["NEO4J_URI"] == "${NEO4J_URI:-bolt://neo4j-graph-db:7687}"
    assert service["depends_on"]["supabase-db-init"]["condition"] == "service_completed_successfully"
    assert service["depends_on"]["neo4j-graph-db"]["condition"] == "service_started"
    assert service["depends_on"]["searxng"]["condition"] == "service_started"


def test_mcp_runtime_guards_reject_write_and_unbounded_inputs() -> None:
    runtime = _runtime_module()

    assert runtime.is_safe_postgres_read("select * from public.users")
    assert runtime.is_safe_postgres_read("with rows as (select 1) select * from rows")
    assert runtime.is_safe_postgres_read("show search_path")
    assert not runtime.is_safe_postgres_read("select 1; drop table public.users")
    assert not runtime.is_safe_postgres_read("insert into public.users values (1)")
    assert not runtime.is_safe_postgres_read("/* hidden */ delete from public.users")

    assert runtime.is_safe_neo4j_read("MATCH (n) RETURN n LIMIT 5")
    assert runtime.is_safe_neo4j_read("CALL db.labels()")
    assert not runtime.is_safe_neo4j_read("CREATE (n:User)")
    assert not runtime.is_safe_neo4j_read("MATCH (n) DETACH DELETE n")
    # A MATCH statement ends in RETURN, so it's wrapped with a server-side LIMIT.
    assert runtime.bounded_neo4j_cypher("MATCH (n) RETURN n") == (
        "CALL {\nMATCH (n) RETURN n\n}\nRETURN *\nLIMIT $atlas_limit"
    )
    # A standalone procedure call is passed through UNWRAPPED — wrapping it in a
    # CALL {} subquery is invalid Cypher (no YIELD/RETURN) and broke the schema
    # tools 100%; the row cap is enforced by result.fetch() at the Python layer.
    assert runtime.bounded_neo4j_cypher("CALL db.labels()") == "CALL db.labels()"

    assert runtime.clamp_limit("1000", default=5, maximum=20) == 20
    assert runtime.clamp_limit("-1", default=5, maximum=20) == 5
    assert runtime.clamp_limit("abc", default=5, maximum=20) == 5


def test_mcp_postgres_capability_discloses_owner_rls_bypass_and_read_scope() -> None:
    compose_env = _compose()["services"]["mcp-servers"]["environment"]
    supabase = yaml.safe_load(SUPABASE_MANIFEST.read_text())
    supabase_env = {entry["name"]: entry for entry in supabase["env"]}
    runtime = _runtime_module()
    rls_sql = MEMORY_RLS_SQL.read_text()

    capability = next(
        row
        for row in _manifest()["capabilities"]
        if row["name"] == "Tenant-scoped Postgres reads"
    )
    assert (
        compose_env["SUPABASE_DB_USER"],
        supabase_env["SUPABASE_DB_USER"]["default"],
        runtime.is_safe_postgres_read(
            "SELECT email, encrypted_password FROM auth.users"
        ),
        "supabase_admin connection bypasses RLS (owner)" in rls_sql,
        (capability["status"], capability["verification"]),
    ) == (
        "${SUPABASE_DB_USER}",
        "supabase_admin",
        True,
        True,
        ("not-supported", "tested"),
    )
    note = capability["note"]
    _assert_contains(note, (
        "shared supabase_admin owner",
        "bypasses RLS",
        "SELECT/WITH/SHOW/EXPLAIN",
        "no schema, table, or column allowlist or redaction",
        "trusted operators",
        "least-privilege views or role",
    ))


def test_mcp_database_guardrails_disclose_accepted_privileged_side_effects() -> None:
    runtime = _runtime_module()
    neo4j_env = yaml.safe_load(NEO4J_COMPOSE.read_text())["services"][
        "neo4j-graph-db"
    ]["environment"]

    sql_results = tuple(runtime.is_safe_postgres_read(sql) for sql in (
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_terminate_backend(1234)",
    ))
    cyphers = (
        'CALL db.createLabel("AtlasOwned")',
        "CALL db.checkpoint()",
    )
    cypher_results = tuple(
        (runtime.is_safe_neo4j_read(cypher), runtime.bounded_neo4j_cypher(cypher))
        for cypher in cyphers
    )

    capabilities = {row["name"]: row for row in _manifest()["capabilities"]}
    guardrail = capabilities["Database query guardrails"]
    prevention = capabilities["Write and administration prevention"]
    assert (
        sql_results,
        cypher_results,
        neo4j_env["NEO4J_dbms_security_procedures_unrestricted"],
        (guardrail["status"], guardrail["verification"]),
        "block common direct mutation syntax" in guardrail["note"],
        "do not prevent privileged function or procedure side effects"
        in guardrail["note"],
        (prevention["status"], prevention["verification"]),
    ) == (
        (True, True),
        ((True, cyphers[0]), (True, cyphers[1])),
        "apoc.*",
        ("partial", "tested"),
        True,
        True,
        ("partial", "tested"),
    )
    _assert_contains(prevention["note"], (
        "no dedicated write or administration tool",
        "SELECT functions",
        "CALL db.* procedures",
        "administrator credentials",
        "administration, filesystem, or write side effects",
    ))


def test_mcp_servers_docs_describe_consumers_guardrails_and_deferred_gateways() -> None:
    readme = README.read_text()

    for expected in (
        "MCP_SERVERS_SOURCE=disabled",
        "mcp.localhost",
        "Open WebUI",
        "Hermes",
        "LiteLLM",
        "MetaMCP",
        "Docker MCP Gateway",
        "mcpo",
        "Docling MCP",
        "read-only",
        "consent",
        "credential",
        "namespace",
    ):
        assert expected in readme
