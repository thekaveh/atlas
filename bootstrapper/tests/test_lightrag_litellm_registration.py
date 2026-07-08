"""Tests for lightrag_model_entry()."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_PY = REPO_ROOT / "services/litellm/init/scripts/init.py"


def _load_init_module():
    # The init.py module-level `import psycopg2` runs only inside the
    # litellm-init container image (psycopg2-binary is pinned in the
    # init Dockerfile, not the bootstrapper venv). Stub it in sys.modules
    # before exec_module so pytest can exercise the pure-Python helpers
    # without provisioning the container's deps.
    sys.modules.setdefault("psycopg2", MagicMock())
    sys.modules.setdefault("psycopg2.extras", MagicMock())
    spec = importlib.util.spec_from_file_location("litellm_init", INIT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["litellm_init"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_SOURCE", "disabled")
    mod = _load_init_module()
    assert mod.lightrag_model_entry() is None


def test_returns_entry_when_enabled(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_SOURCE", "container")
    monkeypatch.setenv("LIGHTRAG_ENDPOINT", "http://lightrag:9621")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "sk-lightrag-test")
    mod = _load_init_module()
    entry = mod.lightrag_model_entry()
    assert entry is not None
    assert entry["model_name"] == "lightrag"
    # api_base is the bare endpoint; the ollama_chat adapter appends /api/chat
    # internally to reach LightRAG's Ollama-shim endpoint.
    assert entry["litellm_params"]["api_base"] == "http://lightrag:9621"
    # api_key is literal-embedded (not the `os.environ/X` directive
    # pattern). LIGHTRAG_API_KEY is auto-generated once and never
    # force-rotated; rotation would need a litellm-init re-seed
    # regardless, so a runtime env-directive buys nothing here.
    assert entry["litellm_params"]["api_key"] == "sk-lightrag-test"


def test_adapter_is_ollama_chat_not_openai(monkeypatch):
    """LightRAG exposes /api/chat (Ollama-shim path), not /chat/completions.
    The openai/ adapter targets /chat/completions and 404s; ollama_chat/
    targets /api/chat and resolves correctly."""
    monkeypatch.setenv("LIGHTRAG_SOURCE", "container")
    monkeypatch.setenv("LIGHTRAG_ENDPOINT", "http://lightrag:9621")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "sk-lightrag-test")
    mod = _load_init_module()
    entry = mod.lightrag_model_entry()
    assert entry["litellm_params"]["model"] == "ollama_chat/lightrag"
