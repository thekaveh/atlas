"""Tests for lightrag_model_entry()."""
from __future__ import annotations

from pathlib import Path
from tests.shared_helpers import load_litellm_init_module


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_SOURCE", "disabled")
    mod = load_litellm_init_module()
    assert mod.lightrag_model_entry() is None


def test_returns_entry_when_enabled(monkeypatch):
    monkeypatch.setenv("LIGHTRAG_SOURCE", "container")
    monkeypatch.setenv("LIGHTRAG_ENDPOINT", "http://lightrag:9621")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "sk-lightrag-test")
    mod = load_litellm_init_module()
    entry = mod.lightrag_model_entry()
    assert entry is not None
    assert entry["model_name"] == "lightrag"
    # api_base is the bare endpoint; the ollama_chat adapter appends /api/chat
    # internally to reach LightRAG's Ollama-shim endpoint.
    assert entry["litellm_params"]["api_base"] == "http://lightrag:9621"
    # api_key is the `os.environ/X` directive, never the literal: the rendered
    # config.yaml sits world-readable on the host bind mount, and the litellm
    # server resolves the directive from its own environment.
    assert entry["litellm_params"]["api_key"] == "os.environ/LIGHTRAG_API_KEY"
    assert "sk-lightrag-test" not in repr(entry)


def test_litellm_server_receives_the_lightrag_key_the_row_references():
    import yaml

    compose = yaml.safe_load(
        (REPO_ROOT / "services/litellm/compose.yml").read_text(encoding="utf-8")
    )
    env = compose["services"]["litellm"]["environment"]
    assert env["LIGHTRAG_API_KEY"] == "${LIGHTRAG_API_KEY:-}"


def test_adapter_is_ollama_chat_not_openai(monkeypatch):
    """LightRAG exposes /api/chat (Ollama-shim path), not /chat/completions.
    The openai/ adapter targets /chat/completions and 404s; ollama_chat/
    targets /api/chat and resolves correctly."""
    monkeypatch.setenv("LIGHTRAG_SOURCE", "container")
    monkeypatch.setenv("LIGHTRAG_ENDPOINT", "http://lightrag:9621")
    monkeypatch.setenv("LIGHTRAG_API_KEY", "sk-lightrag-test")
    mod = load_litellm_init_module()
    entry = mod.lightrag_model_entry()
    assert entry["litellm_params"]["model"] == "ollama_chat/lightrag"
