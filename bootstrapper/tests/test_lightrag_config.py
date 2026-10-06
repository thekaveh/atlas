# bootstrapper/tests/test_lightrag_config.py
"""Tests for _generate_lightrag_config()."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.service_config import ServiceConfig


REPO_ROOT = Path(__file__).resolve().parents[2]


_BASE_ENV = {
    "PROJECT_NAME": "atlas",
    "LIGHTRAG_LOCALHOST_PORT": "63068",
}


def _make(source: str) -> ServiceConfig:
    sc = ServiceConfig(config_parser=MagicMock())
    sc.localhost_host = "localhost"
    sc.service_sources = {"LIGHTRAG_SOURCE": source}
    sc.config_parser.parse_env_file.return_value = dict(_BASE_ENV)
    return sc


def test_disabled_clears_endpoint_and_scales():
    sc = _make("disabled")
    env = sc._generate_lightrag_config()
    assert env["LIGHTRAG_ENDPOINT"] == ""
    assert env["LIGHTRAG_SCALE"] == "0"
    assert env["LIGHTRAG_INIT_SCALE"] == "0"


def test_container_endpoint_and_scales():
    sc = _make("container")
    env = sc._generate_lightrag_config()
    assert env["LIGHTRAG_ENDPOINT"] == "http://lightrag:9621"
    assert env["LIGHTRAG_SCALE"] == "1"
    assert env["LIGHTRAG_INIT_SCALE"] == "1"


def test_localhost_uses_LIGHTRAG_LOCALHOST_PORT():
    sc = _make("localhost")
    env = sc._generate_lightrag_config()
    assert env["LIGHTRAG_ENDPOINT"] == "http://localhost:63068"
    assert env["LIGHTRAG_SCALE"] == "0"
    assert env["LIGHTRAG_INIT_SCALE"] == "0"


def test_init_waits_for_postgres_before_pgvector_migration():
    """supabase-db is SOURCE-replaceable, so lightrag-init must NOT hard
    depend_on it in compose — it polls the Postgres endpoint for readiness in
    the script instead. Guard that the readiness poll precedes the migration so
    the pgvector step can't race supabase-db readiness (regression guard)."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    script = (repo / "services/lightrag/init/scripts/init-lightrag.sh").read_text(
        encoding="utf-8"
    )
    poll_at = script.find("SELECT 1")
    migrate_at = script.find("migrate-pgvector.sql")
    assert poll_at != -1, "lightrag-init must poll Postgres readiness (SELECT 1)"
    assert migrate_at != -1, "lightrag-init must run the pgvector migration"
    assert poll_at < migrate_at, (
        "the Postgres readiness poll must come BEFORE the pgvector migration"
    )


def test_resolve_models_fails_when_chat_model_cannot_be_resolved(monkeypatch, capsys):
    """lightrag-init must not write LLM_MODEL= and exit 0 on a bad model list."""
    script = REPO_ROOT / "services/lightrag/init/scripts/resolve-models.py"
    spec = importlib.util.spec_from_file_location("lightrag_resolve_models", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.delenv("LIGHTRAG_LLM_MODEL", raising=False)
    monkeypatch.delenv("LITELLM_DEFAULT_MODEL", raising=False)
    monkeypatch.setenv("LITELLM_EMBEDDING_MODEL", "ollama/nomic-embed-text")
    monkeypatch.setattr(module, "fetch_models", lambda: [])

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code == 1
    assert "could not resolve a chat model" in capsys.readouterr().err


def test_lightrag_knows_the_catalog_default_embedding_dimension(monkeypatch):
    """qwen3-embedding:0.6b fell through to a first-boot probe that ran before
    the model was pulled and silently created a 768-dim store."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "services/lightrag/init/scripts/resolve-models.py"
    spec = importlib.util.spec_from_file_location("lightrag_resolve_models_dims", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed")))
    assert module.resolve_dim("ollama/qwen3-embedding:0.6b") == 1024
    assert module.resolve_dim("ollama/nomic-embed-text") == 768
