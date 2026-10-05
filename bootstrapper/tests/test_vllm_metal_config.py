"""Tests for _generate_vllm_metal_config() (#379)."""
from __future__ import annotations

from unittest.mock import MagicMock

from services.service_config import ServiceConfig


_BASE_ENV = {
    "PROJECT_NAME": "atlas",
    "VLLM_METAL_LOCALHOST_PORT": "8000",
}


def _make(source: str, host: str = "host.docker.internal", port: str = "8000") -> ServiceConfig:
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = host
    sc.service_sources = {"VLLM_METAL_SOURCE": source}
    env = dict(_BASE_ENV)
    env["VLLM_METAL_LOCALHOST_PORT"] = port
    sc.config_parser.parse_env_file.return_value = env
    return sc


def test_disabled_clears_endpoint_and_scale():
    env = _make("disabled")._generate_vllm_metal_config()
    assert env["VLLM_METAL_ENDPOINT"] == ""
    assert env["VLLM_METAL_SCALE"] == "0"


def test_managed_localhost_resolves_endpoint():
    env = _make("managed-localhost")._generate_vllm_metal_config()
    assert env["VLLM_METAL_ENDPOINT"] == "http://host.docker.internal:8000"
    # Never a container — scale stays 0 in every source.
    assert env["VLLM_METAL_SCALE"] == "0"


def test_managed_localhost_honours_custom_port():
    env = _make("managed-localhost", port="8123")._generate_vllm_metal_config()
    assert env["VLLM_METAL_ENDPOINT"] == "http://host.docker.internal:8123"


def test_managed_localhost_uses_localhost_host_seam():
    # ServiceConfig rewrites host.docker.internal → localhost_host; a bare
    # localhost host must be honoured (mirrors _generate_lightrag_config).
    env = _make("managed-localhost", host="localhost")._generate_vllm_metal_config()
    assert env["VLLM_METAL_ENDPOINT"] == "http://localhost:8000"


def test_ollama_localhost_upstream_resolves_the_configured_port():
    # Compose's .env parser falls back to the :-11434 default when
    # OLLAMA_LOCALHOST_PORT sits after LITELLM_OLLAMA_UPSTREAM (it does in
    # .env.example), so the port must be resolved before it reaches .env.
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = "host.docker.internal"
    sc.service_sources = {"LLM_PROVIDER_SOURCE": "ollama-localhost"}
    sc.config_parser.parse_env_file.return_value = {"OLLAMA_LOCALHOST_PORT": "11500"}
    sc.get_service_config = MagicMock(return_value={
        "environment": {"OLLAMA_ENDPOINT": "http://host.docker.internal:${OLLAMA_LOCALHOST_PORT:-11434}"},
    })
    env = sc._generate_llm_provider_config()
    assert env["LITELLM_OLLAMA_UPSTREAM"] == "http://host.docker.internal:11500"


def test_lightrag_follows_graph_db_user_and_warns_on_missing_backends(capsys):
    from services.service_config import _lightrag_neo4j_username, _warn_lightrag_storage_gaps

    assert _lightrag_neo4j_username({"GRAPH_DB_USER": "graphops"}) == "graphops"
    assert _lightrag_neo4j_username({}) == "neo4j"
    # Neo4j disabled -> blank URI with the default Neo4JStorage selector.
    _warn_lightrag_storage_gaps(
        {"LIGHTRAG_NEO4J_URI": "", "LIGHTRAG_PG_URI": "postgresql://x", "LIGHTRAG_REDIS_URI": "redis://x"},
        {},
    )
    err = capsys.readouterr().err
    assert "LIGHTRAG_GRAPH_STORAGE=Neo4JStorage" in err and "Redis" not in err
    _warn_lightrag_storage_gaps({"LIGHTRAG_NEO4J_URI": ""}, {
        "LIGHTRAG_GRAPH_STORAGE": "NetworkXStorage", "LIGHTRAG_VECTOR_STORAGE": "NanoVectorDBStorage",
        "LIGHTRAG_KV_STORAGE": "JsonKVStorage", "LIGHTRAG_DOC_STATUS_STORAGE": "JsonDocStatusStorage",
    })
    assert capsys.readouterr().err == ""


def test_localhost_stt_endpoint_resolves_the_configured_port():
    # Derived .env lines sit above WHISPER_CPP_LOCALHOST_PORT, where compose
    # substituted the :-default, so Open WebUI/Hermes called the wrong port.
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = "host.docker.internal"
    sc.service_sources = {"STT_PROVIDER_SOURCE": "whisper-cpp-localhost"}
    sc.config_parser.parse_env_file.return_value = {"WHISPER_CPP_LOCALHOST_PORT": "63099"}
    sc.get_service_config = MagicMock(return_value={
        "environment": {"STT_ENDPOINT": "http://host.docker.internal:${WHISPER_CPP_LOCALHOST_PORT:-63042}"},
    })
    env = sc._generate_stt_provider_config()
    assert env["STT_ENDPOINT"] == "http://host.docker.internal:63099"
