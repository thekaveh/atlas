"""Tests for fal_model_entry() — the litellm-init fal text→image registration (#515)."""
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
    for var in ("FAL_SOURCE", "FAL_API_KEY", "FAL_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_returns_none_when_disabled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "disabled")
    monkeypatch.setenv("FAL_API_KEY", "fal-key-123")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux/dev")
    mod = _load_init_module()
    assert mod.fal_model_entry() is None


def test_returns_none_when_key_missing(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "enabled")
    monkeypatch.setenv("FAL_API_KEY", "")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux/dev")
    mod = _load_init_module()
    assert mod.fal_model_entry() is None


def test_returns_none_when_model_missing(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "enabled")
    monkeypatch.setenv("FAL_API_KEY", "fal-key-123")
    monkeypatch.setenv("FAL_MODEL", "")
    mod = _load_init_module()
    assert mod.fal_model_entry() is None


def test_returns_entry_when_enabled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "enabled")
    monkeypatch.setenv("FAL_API_KEY", "fal-key-123")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux/dev")
    mod = _load_init_module()
    entry = mod.fal_model_entry()
    assert entry is not None
    # Stable, friendly alias so image clients don't churn when FAL_MODEL changes.
    assert entry["model_name"] == "fal-image"
    # Native fal_ai provider: fal_ai/ prefix + the fal endpoint id.
    assert entry["litellm_params"]["model"] == "fal_ai/fal-ai/flux/dev"
    # Key is a directive the LiteLLM SERVER resolves at request time — never the
    # literal secret (which would leak into config.yaml).
    assert entry["litellm_params"]["api_key"] == "os.environ/FAL_AI_API_KEY"
    assert "fal-key-123" not in str(entry)
    assert entry["model_info"]["mode"] == "image_generation"


def test_custom_model_id_flows_through(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "enabled")
    monkeypatch.setenv("FAL_API_KEY", "fal-key-123")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux-pro/v1.1-ultra")
    mod = _load_init_module()
    entry = mod.fal_model_entry()
    assert entry["litellm_params"]["model"] == "fal_ai/fal-ai/flux-pro/v1.1-ultra"


def test_case_and_padding_normalized(monkeypatch):
    """A padded/cased source value must not sneak a row in when disabled."""
    _clear(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "  DISABLED  ")
    monkeypatch.setenv("FAL_API_KEY", "fal-key-123")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux/dev")
    mod = _load_init_module()
    assert mod.fal_model_entry() is None


def test_render_config_appends_fal_entry(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "enabled")
    monkeypatch.setenv("FAL_API_KEY", "fal-key-123")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux/dev")
    # Keep the other stitched providers out so fal-image is the only extra row.
    monkeypatch.setenv("HERMES_SOURCE", "disabled")
    monkeypatch.setenv("LIGHTRAG_SOURCE", "disabled")
    monkeypatch.setenv("VLLM_METAL_SOURCE", "disabled")
    # render_config loads shared settings from /catalog — point it at the utils dir.
    monkeypatch.setenv("ATLAS_CATALOG_DIR", str(REPO_ROOT / "bootstrapper" / "utils"))
    mod = _load_init_module()
    config = mod.render_config([])
    names = [r.get("model_name") for r in config["model_list"]]
    assert "fal-image" in names
