"""Tests for tei_rerank_model_entry() — the litellm-init /v1/rerank registration (#516)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_PY = REPO_ROOT / "services/litellm/init/scripts/init.py"


def _load_init_module():
    sys.modules.setdefault("psycopg2", MagicMock())
    sys.modules.setdefault("psycopg2.extras", MagicMock())
    spec = importlib.util.spec_from_file_location("litellm_init", INIT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["litellm_init"] = mod
    spec.loader.exec_module(mod)
    return mod


def _clear(monkeypatch):
    for var in ("TEI_RERANKER_SOURCE", "TEI_RERANKER_ENDPOINT", "TEI_RERANKER_MODEL_ID"):
        monkeypatch.delenv(var, raising=False)


def test_returns_none_when_disabled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEI_RERANKER_SOURCE", "disabled")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    monkeypatch.setenv("TEI_RERANKER_MODEL_ID", "mixedbread-ai/mxbai-rerank-base-v1")
    mod = _load_init_module()
    assert mod.tei_rerank_model_entry() is None


def test_returns_none_when_endpoint_blank(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEI_RERANKER_SOURCE", "container-cpu")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "")
    monkeypatch.setenv("TEI_RERANKER_MODEL_ID", "mixedbread-ai/mxbai-rerank-base-v1")
    mod = _load_init_module()
    assert mod.tei_rerank_model_entry() is None


def test_returns_none_when_model_blank(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEI_RERANKER_SOURCE", "container-cpu")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    monkeypatch.setenv("TEI_RERANKER_MODEL_ID", "")
    mod = _load_init_module()
    assert mod.tei_rerank_model_entry() is None


def test_returns_entry_with_huggingface_prefix(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEI_RERANKER_SOURCE", "container-cpu")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    monkeypatch.setenv("TEI_RERANKER_MODEL_ID", "mixedbread-ai/mxbai-rerank-base-v1")
    mod = _load_init_module()
    entry = mod.tei_rerank_model_entry()
    assert entry is not None
    assert entry["model_name"] == "tei-rerank"
    # CRITICAL: huggingface/ prefix (speaks TEI's {query, texts}); NOT
    # infinity/jina/cohere (which send {query, documents} and break TEI).
    assert entry["litellm_params"]["model"] == "huggingface/mixedbread-ai/mxbai-rerank-base-v1"
    assert not entry["litellm_params"]["model"].startswith(("infinity/", "jina/", "cohere/"))
    assert entry["litellm_params"]["api_base"] == "http://tei-reranker:80"
    # rerank is not an OpenAI modality → no api_key (TEI is unauthenticated).
    assert "api_key" not in entry["litellm_params"]
    assert entry["model_info"]["mode"] == "rerank"


def test_localhost_endpoint_trailing_slash_trimmed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEI_RERANKER_SOURCE", "localhost")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://host.docker.internal:63049/")
    monkeypatch.setenv("TEI_RERANKER_MODEL_ID", "mixedbread-ai/mxbai-rerank-base-v1")
    mod = _load_init_module()
    entry = mod.tei_rerank_model_entry()
    assert entry["litellm_params"]["api_base"] == "http://host.docker.internal:63049"


def test_render_config_appends_tei_rerank_entry(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TEI_RERANKER_SOURCE", "container-cpu")
    monkeypatch.setenv("TEI_RERANKER_ENDPOINT", "http://tei-reranker:80")
    monkeypatch.setenv("TEI_RERANKER_MODEL_ID", "mixedbread-ai/mxbai-rerank-base-v1")
    # Keep the other stitched providers out.
    for v in ("HERMES_SOURCE", "LIGHTRAG_SOURCE", "VLLM_METAL_SOURCE", "FAL_SOURCE"):
        monkeypatch.setenv(v, "disabled")
    monkeypatch.setenv("ATLAS_CATALOG_DIR", str(REPO_ROOT / "bootstrapper" / "utils"))
    mod = _load_init_module()
    config = mod.render_config([])
    rows = {r.get("model_name"): r for r in config["model_list"]}
    assert "tei-rerank" in rows
    assert rows["tei-rerank"]["model_info"]["mode"] == "rerank"
