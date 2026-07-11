"""Tests for vllm_metal_model_entry() — the litellm-init registration (#379)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_PY = REPO_ROOT / "services/litellm/init/scripts/init.py"


def _load_init_module():
    # init.py's module-level `import psycopg2` runs only inside the
    # litellm-init container image; stub it so pytest can exercise the
    # pure-Python helpers without the container's deps.
    sys.modules.setdefault("psycopg2", MagicMock())
    sys.modules.setdefault("psycopg2.extras", MagicMock())
    spec = importlib.util.spec_from_file_location("litellm_init", INIT_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["litellm_init"] = mod
    spec.loader.exec_module(mod)
    return mod


def _clear(monkeypatch):
    for var in ("VLLM_METAL_SOURCE", "VLLM_METAL_ENDPOINT", "VLLM_METAL_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_returns_none_when_disabled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VLLM_METAL_SOURCE", "disabled")
    monkeypatch.setenv("VLLM_METAL_ENDPOINT", "http://host.docker.internal:8000")
    monkeypatch.setenv("VLLM_METAL_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    mod = _load_init_module()
    assert mod.vllm_metal_model_entry() is None


def test_returns_none_when_endpoint_blank(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VLLM_METAL_SOURCE", "managed-localhost")
    monkeypatch.setenv("VLLM_METAL_ENDPOINT", "")
    monkeypatch.setenv("VLLM_METAL_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    mod = _load_init_module()
    assert mod.vllm_metal_model_entry() is None


def test_returns_none_when_model_blank(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VLLM_METAL_SOURCE", "managed-localhost")
    monkeypatch.setenv("VLLM_METAL_ENDPOINT", "http://host.docker.internal:8000")
    monkeypatch.setenv("VLLM_METAL_MODEL", "")
    mod = _load_init_module()
    assert mod.vllm_metal_model_entry() is None


def test_returns_entry_when_managed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VLLM_METAL_SOURCE", "managed-localhost")
    monkeypatch.setenv("VLLM_METAL_ENDPOINT", "http://host.docker.internal:8000")
    monkeypatch.setenv("VLLM_METAL_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    mod = _load_init_module()
    entry = mod.vllm_metal_model_entry()
    assert entry is not None
    # Alias == HF model id, so /v1/models is honest about what is served.
    assert entry["model_name"] == "Qwen/Qwen2.5-7B-Instruct"
    # LiteLLM openai passthrough → upstream model name matches the alias.
    assert entry["litellm_params"]["model"] == "openai/Qwen/Qwen2.5-7B-Instruct"
    # vLLM's OpenAI server lives at <endpoint>/v1.
    assert entry["litellm_params"]["api_base"] == "http://host.docker.internal:8000/v1"
    assert entry["litellm_params"]["api_key"] == "sk-noauth"
    assert entry["model_info"]["mode"] == "chat"


def test_endpoint_already_v1_not_doubled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VLLM_METAL_SOURCE", "managed-localhost")
    monkeypatch.setenv("VLLM_METAL_ENDPOINT", "http://host.docker.internal:8000/v1/")
    monkeypatch.setenv("VLLM_METAL_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
    mod = _load_init_module()
    entry = mod.vllm_metal_model_entry()
    assert entry["litellm_params"]["api_base"] == "http://host.docker.internal:8000/v1"


def test_case_and_padding_normalized(monkeypatch):
    """A padded/cased source value must NOT sneak a row in when disabled."""
    _clear(monkeypatch)
    monkeypatch.setenv("VLLM_METAL_SOURCE", "  DISABLED  ")
    monkeypatch.setenv("VLLM_METAL_ENDPOINT", "http://host.docker.internal:8000")
    monkeypatch.setenv("VLLM_METAL_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    mod = _load_init_module()
    assert mod.vllm_metal_model_entry() is None


def test_render_config_appends_vllm_metal_entry(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VLLM_METAL_SOURCE", "managed-localhost")
    monkeypatch.setenv("VLLM_METAL_ENDPOINT", "http://host.docker.internal:8000")
    monkeypatch.setenv("VLLM_METAL_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    # Keep hermes/lightrag out so the only stitched row is vllm-metal.
    monkeypatch.setenv("HERMES_SOURCE", "disabled")
    monkeypatch.setenv("LIGHTRAG_SOURCE", "disabled")
    # render_config also loads shared LiteLLM settings from /catalog — point it
    # at the bootstrapper utils dir so the load resolves outside a container.
    monkeypatch.setenv("ATLAS_CATALOG_DIR", str(REPO_ROOT / "bootstrapper" / "utils"))
    mod = _load_init_module()
    config = mod.render_config([])
    names = [r.get("model_name") for r in config["model_list"]]
    assert "Qwen/Qwen2.5-7B-Instruct" in names
