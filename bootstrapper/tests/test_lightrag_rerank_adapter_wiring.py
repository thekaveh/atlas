"""LightRAG → TEI rerank adapter bootstrapper wiring (#415).

Covers the non-service_config pieces: the generated bearer token (gated on
LightRAG being enabled + idempotent), and the manifest/compose dual-write that
carries the adapter env vars into the backend and the shared token into
LightRAG's RERANK_BINDING_API_KEY.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from utils.key_generator import KeyGenerator


REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_env(tmp_path: Path, body: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    return env


# ── token generation ────────────────────────────────────────────────


def test_adapter_token_generated_when_lightrag_enabled(tmp_path):
    _seed_env(tmp_path, "LIGHTRAG_SOURCE=container\n")
    kg = KeyGenerator(str(tmp_path))
    kg.generate_missing_keys()
    token = kg.get_current_env_value("LIGHTRAG_RERANK_ADAPTER_TOKEN")
    assert token.startswith("sk-lightrag-rerank-")


def test_adapter_token_not_generated_when_lightrag_disabled(tmp_path):
    _seed_env(tmp_path, "LIGHTRAG_SOURCE=disabled\n")
    kg = KeyGenerator(str(tmp_path))
    kg.generate_missing_keys()
    assert not kg.get_current_env_value("LIGHTRAG_RERANK_ADAPTER_TOKEN")


def test_adapter_token_is_idempotent(tmp_path):
    _seed_env(
        tmp_path,
        "LIGHTRAG_SOURCE=container\nLIGHTRAG_RERANK_ADAPTER_TOKEN=sk-lightrag-rerank-existing\n",
    )
    kg = KeyGenerator(str(tmp_path))
    kg.generate_and_update_lightrag_rerank_adapter_token(force=False)
    assert (
        kg.get_current_env_value("LIGHTRAG_RERANK_ADAPTER_TOKEN")
        == "sk-lightrag-rerank-existing"
    )


# ── manifest / compose dual-write ───────────────────────────────────


def _backend_manifest() -> dict:
    return yaml.safe_load(
        (REPO_ROOT / "services" / "backend" / "service.yml").read_text(encoding="utf-8")
    )


def test_backend_manifest_declares_adapter_env():
    names = {e["name"] for e in _backend_manifest()["env"]}
    assert "LIGHTRAG_RERANK_ADAPTER_ENABLED" in names
    assert "LIGHTRAG_RERANK_ADAPTER_TOKEN" in names
    assert "LIGHTRAG_RERANK_ADAPTER_TIMEOUT_SECONDS" in names


def test_backend_adapter_token_is_marked_secret():
    token_decl = next(
        e for e in _backend_manifest()["env"] if e["name"] == "LIGHTRAG_RERANK_ADAPTER_TOKEN"
    )
    assert token_decl.get("secret") is True


def test_backend_compose_wires_adapter_env():
    text = (REPO_ROOT / "services" / "backend" / "compose.yml").read_text(encoding="utf-8")
    assert "TEI_RERANKER_ENDPOINT: ${TEI_RERANKER_ENDPOINT" in text
    assert "LIGHTRAG_RERANK_ADAPTER_ENABLED: ${LIGHTRAG_RERANK_ADAPTER_ENABLED" in text
    assert "LIGHTRAG_RERANK_ADAPTER_TOKEN: ${LIGHTRAG_RERANK_ADAPTER_TOKEN" in text


def test_lightrag_compose_sends_token_as_rerank_api_key():
    text = (REPO_ROOT / "services" / "lightrag" / "compose.yml").read_text(encoding="utf-8")
    # LightRAG forwards the shared token to the adapter as a bearer.
    assert "RERANK_BINDING_API_KEY: ${LIGHTRAG_RERANK_ADAPTER_TOKEN" in text


def test_env_example_lists_adapter_defaults():
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "LIGHTRAG_RERANK_ADAPTER_ENABLED=false" in text
    assert "LIGHTRAG_RERANK_ADAPTER_TOKEN=" in text
    assert "LIGHTRAG_RERANK_ADAPTER_TIMEOUT_SECONDS=30" in text
