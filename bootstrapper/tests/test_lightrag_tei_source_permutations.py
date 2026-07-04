"""End-to-end env generation across LightRAG + TEI Reranker source values."""
from __future__ import annotations

import pytest
from pathlib import Path
from core.config_parser import ConfigParser
from services.service_config import ServiceConfig


REPO_ROOT = Path(__file__).resolve().parents[2]



def _sc(env_path: Path) -> ServiceConfig:
    cp = ConfigParser(str(REPO_ROOT))
    cp.env_file_path = env_path
    sc = ServiceConfig(config_parser=cp)
    sc.localhost_host = "localhost"
    return sc


@pytest.mark.parametrize("lightrag_source", ["disabled", "container", "localhost"])
def test_lightrag_env_generation_each_source(env_with_overrides, lightrag_source):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": lightrag_source,
        "TEI_RERANKER_SOURCE": "disabled",
    }))
    env = sc.generate_service_environment()
    assert "LIGHTRAG_ENDPOINT" in env
    assert "LIGHTRAG_SCALE" in env
    if lightrag_source == "disabled":
        assert env["LIGHTRAG_ENDPOINT"] == ""
        assert env["LIGHTRAG_SCALE"] == "0"
    elif lightrag_source == "container":
        assert "lightrag" in env["LIGHTRAG_ENDPOINT"]
        assert env["LIGHTRAG_SCALE"] == "1"
    elif lightrag_source == "localhost":
        assert "localhost:63068" in env["LIGHTRAG_ENDPOINT"]
        assert env["LIGHTRAG_SCALE"] == "0"


@pytest.mark.parametrize("tei_source", [
    "disabled", "container-cpu", "container-gpu", "localhost"
])
def test_tei_reranker_env_generation_each_source(env_with_overrides, tei_source):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "disabled",
        "TEI_RERANKER_SOURCE": tei_source,
    }))
    env = sc.generate_service_environment()
    assert "TEI_RERANKER_ENDPOINT" in env
    assert "TEI_RERANKER_SCALE" in env
    if tei_source == "disabled":
        assert env["TEI_RERANKER_ENDPOINT"] == ""
        assert env["TEI_RERANKER_SCALE"] == "0"
    elif tei_source == "localhost":
        assert "localhost:63049" in env["TEI_RERANKER_ENDPOINT"]
    else:  # container variants
        assert env["TEI_RERANKER_ENDPOINT"] == "http://tei-reranker:80"
        assert env["TEI_RERANKER_SCALE"] == "1"


def test_lightrag_adaptive_disables_direct_tei_rerank_when_tei_enabled(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "container",
        "TEI_RERANKER_SOURCE": "container-cpu",
    }))
    env = sc.generate_service_environment()
    # Atlas's TEI endpoint expects {query, texts}; LightRAG's jina/cohere
    # clients send {query, documents}. Until Atlas ships an adapter, do not
    # auto-wire LightRAG query rerank directly to TEI.
    assert env.get("TEI_RERANKER_ENDPOINT") == "http://tei-reranker:80"
    assert env.get("LIGHTRAG_RERANK_BINDING_HOST", "") == ""
    assert env.get("LIGHTRAG_RERANK_BINDING") == "null"


def test_lightrag_adaptive_blanks_rerank_when_tei_disabled(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "container",
        "TEI_RERANKER_SOURCE": "disabled",
    }))
    env = sc.generate_service_environment()
    assert env.get("LIGHTRAG_RERANK_BINDING_HOST", "") == ""
    # Graceful degradation: TEI disabled → binding is the literal `null`
    # (reranking off), NEVER an empty string (LightRAG v1.5.0 crashes on "").
    assert env.get("LIGHTRAG_RERANK_BINDING") == "null"


# ---------------------------------------------------------------------------
# Storage adaptive var tests — 6 newly-emitted vars
# ---------------------------------------------------------------------------

def test_lightrag_pg_uri_populated_when_supabase_enabled(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "container",
        "SUPABASE_SOURCE": "container",
    }))
    env = sc.generate_service_environment()
    assert env.get("LIGHTRAG_PG_URI", "").startswith("postgresql://")
    assert "supabase-db:5432" in env["LIGHTRAG_PG_URI"]


def test_lightrag_pg_uri_blank_when_supabase_disabled(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "container",
        "SUPABASE_SOURCE": "disabled",
    }))
    env = sc.generate_service_environment()
    assert env.get("LIGHTRAG_PG_URI", "") == ""


def test_lightrag_neo4j_uri_populated_when_neo4j_enabled(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "container",
    }))
    env = sc.generate_service_environment()
    # Hostname MUST be `neo4j-graph-db` (the compose service id), NOT bare `neo4j`.
    # Memory: reference_kong_compose_service_id documents the same pattern.
    # The previous assertion asserted `bolt://neo4j:7687` which was a
    # bug-confirming test — both this test and the imperative emission in
    # service_config.py:1106 had the wrong hostname; live smoke surfaced it.
    assert env.get("LIGHTRAG_NEO4J_URI") == "bolt://neo4j-graph-db:7687"
    assert env.get("LIGHTRAG_NEO4J_USERNAME") == "neo4j"
    assert env.get("LIGHTRAG_NEO4J_PASSWORD")  # truthy — non-empty from .env.example


def test_lightrag_neo4j_blank_when_neo4j_disabled(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "disabled",
    }))
    env = sc.generate_service_environment()
    assert env.get("LIGHTRAG_NEO4J_URI", "") == ""
    assert env.get("LIGHTRAG_NEO4J_USERNAME", "") == ""
    assert env.get("LIGHTRAG_NEO4J_PASSWORD", "") == ""


def test_lightrag_redis_uri_populated_when_redis_enabled(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "container",
        "REDIS_SOURCE": "container",
    }))
    env = sc.generate_service_environment()
    assert env.get("LIGHTRAG_REDIS_URI", "").startswith("redis://")
    assert "redis:6379/2" in env["LIGHTRAG_REDIS_URI"]


def test_lightrag_docling_endpoint_mirrors_DOCLING_ENDPOINT(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "container",
        "DOC_PROCESSOR_SOURCE": "docling-container-gpu",
    }))
    env = sc.generate_service_environment()
    assert "docling" in env.get("LIGHTRAG_DOCLING_ENDPOINT", "")


def test_lightrag_storage_blanks_when_lightrag_disabled(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "disabled",
        "SUPABASE_SOURCE": "container",
        "NEO4J_GRAPH_DB_SOURCE": "container",
        "REDIS_SOURCE": "container",
    }))
    env = sc.generate_service_environment()
    # LightRAG disabled → storage adaptive vars are blank
    assert env.get("LIGHTRAG_PG_URI", "") == ""
    assert env.get("LIGHTRAG_NEO4J_URI", "") == ""
    assert env.get("LIGHTRAG_NEO4J_USERNAME", "") == ""
    assert env.get("LIGHTRAG_NEO4J_PASSWORD", "") == ""
    assert env.get("LIGHTRAG_REDIS_URI", "") == ""
    assert env.get("LIGHTRAG_DOCLING_ENDPOINT", "") == ""


def test_lightrag_rerank_blank_when_lightrag_disabled(env_with_overrides):
    sc = _sc(env_with_overrides({
        "LIGHTRAG_SOURCE": "disabled",
        "TEI_RERANKER_SOURCE": "container-cpu",  # tei is on
    }))
    env = sc.generate_service_environment()
    # Even though TEI is on, LightRAG is disabled — its rerank var must be blank
    assert env.get("LIGHTRAG_RERANK_BINDING_HOST", "") == ""
    # Binding still resolves to `null` (never blank) so a re-enable racing the
    # .env rewrite can't boot the container with an empty RERANK_BINDING.
    assert env.get("LIGHTRAG_RERANK_BINDING") == "null"
