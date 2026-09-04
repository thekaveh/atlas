"""Regression: the Textual launch pipeline swaps ``starter.banner`` for a
``_NullBanner`` to suppress stdout while inside the app. The previous
``__getattr__`` returned a bare ``lambda`` for undefined attributes, so
``starter.banner.console.print(...)`` raised
``'function' object has no attribute 'print'`` and crashed the
"Apply user model selections" step whenever a model pick triggered the
embedding dimension warning (start.py:311).

The fix returns a chain-swallowing ``_NullSink`` from ``__getattr__``.
"""
from __future__ import annotations

import pytest

from ui.textual.screens.wizard_screen import _NullBanner


def test_nullbanner_console_chain_is_noop():
    b = _NullBanner()
    # The exact crash path: attribute access (.console) then a method call.
    assert b.console.print("[yellow]warn[/yellow]") is None
    # Defined no-op methods still work.
    assert b.show_status_message("x", "info") is None
    assert b.log("y") is None
    # Arbitrary depth + call is safe.
    assert b.console.rule("z") is None
    assert b.anything.deeply.nested(1, 2, k=3) is None


def test_apply_user_model_selections_persists_catalog_dimension_contract():
    """A curated embedding selection carries its catalog dimension into env."""
    from start import AtlasStarter

    captured: dict = {}

    class _SOM:
        def update_env_file(self, d):
            captured.update(d)
            return True

    class _Stub:
        banner = _NullBanner()
        source_override_manager = _SOM()

    stub = _Stub()
    # qwen3-embedding:0.6b declares 1536 in services/ollama/models.yaml.
    selections = {
        "LITELLM_EMBEDDING_MODEL": "ollama/qwen3-embedding:0.6b",
        "OLLAMA_USER_MODELS": "qwen3.8:latest",
    }
    result = AtlasStarter.apply_user_model_selections(stub, selections)
    assert result is True
    assert captured.get("OLLAMA_USER_MODELS") == "qwen3.8:latest"
    assert captured.get("LANGMEM_EMBEDDING_DIM") == "1536"


def test_apply_user_model_selections_accepts_custom_model_with_selected_dimension():
    """A custom model carries the explicit dimension selected in the same flow."""
    from start import AtlasStarter

    captured: dict = {}

    class _SOM:
        def update_env_file(self, values):
            captured.update(values)
            return True

    class _Config:
        def parse_env_file(self):
            return {"LANGMEM_EMBEDDING_DIM": "768"}

    class _Stub:
        banner = _NullBanner()
        source_override_manager = _SOM()
        config_parser = _Config()

    selections = {
        "LITELLM_EMBEDDING_MODEL": "custom/acme-embedder",
        "LANGMEM_EMBEDDING_DIM": "1024",
    }

    assert AtlasStarter.apply_user_model_selections(_Stub(), selections) is True
    assert captured["LANGMEM_EMBEDDING_DIM"] == "1024"


def test_apply_user_model_selections_uses_existing_dimension_for_custom_model():
    """A custom-only model edit preserves an explicit existing dimension."""
    from start import AtlasStarter

    captured: dict = {}

    class _SOM:
        def update_env_file(self, values):
            captured.update(values)
            return True

    class _Config:
        def parse_env_file(self):
            return {
                "LITELLM_EMBEDDING_MODEL": "custom/acme-embedder",
                "LANGMEM_EMBEDDING_DIM": "2048",
            }

    class _Stub:
        banner = _NullBanner()
        source_override_manager = _SOM()
        config_parser = _Config()

    selections = {"LITELLM_EMBEDDING_MODEL": "custom/acme-embedder"}

    assert AtlasStarter.apply_user_model_selections(_Stub(), selections) is True
    assert captured["LANGMEM_EMBEDDING_DIM"] == "2048"


def test_apply_user_model_selections_does_not_inherit_unrelated_default_dimension():
    """A fresh generic 768 must not silently declare a new custom model."""
    from start import AtlasStarter

    class _SOM:
        def update_env_file(self, _values):
            raise AssertionError("invalid contract must not be persisted")

    class _Config:
        def parse_env_file(self):
            return {
                "LITELLM_EMBEDDING_MODEL": "ollama/nomic-embed-text",
                "LANGMEM_EMBEDDING_DIM": "768",
            }

    class _Stub:
        banner = _NullBanner()
        source_override_manager = _SOM()
        config_parser = _Config()

    with pytest.raises(ValueError, match="custom.*LANGMEM_EMBEDDING_DIM"):
        AtlasStarter.apply_user_model_selections(
            _Stub(), {"LITELLM_EMBEDDING_MODEL": "custom/new-embedder"}
        )


@pytest.mark.parametrize("dimension", ["", "clear", "not-a-number", "0", "4001"])
def test_apply_user_model_selections_rejects_invalid_custom_dimension(dimension):
    from start import AtlasStarter

    class _SOM:
        def update_env_file(self, _values):
            raise AssertionError("invalid contract must not be persisted")

    class _Config:
        def parse_env_file(self):
            return {}

    class _Stub:
        banner = _NullBanner()
        source_override_manager = _SOM()
        config_parser = _Config()

    with pytest.raises(ValueError, match="LANGMEM_EMBEDDING_DIM"):
        AtlasStarter.apply_user_model_selections(
            _Stub(),
            {
                "LITELLM_EMBEDDING_MODEL": "custom/new-embedder",
                "LANGMEM_EMBEDDING_DIM": dimension,
            },
        )


def test_apply_user_model_selections_rejects_unaligned_langmem_override():
    from start import AtlasStarter

    class _Config:
        def parse_env_file(self):
            return {
                "LANGMEM_EMBEDDING_MODEL": "custom/provider-a",
                "LITELLM_EMBEDDING_MODEL": "custom/provider-a",
                "LANGMEM_EMBEDDING_DIM": "2048",
            }

    class _Stub:
        banner = _NullBanner()
        source_override_manager = object()
        config_parser = _Config()

    with pytest.raises(ValueError, match="LANGMEM_EMBEDDING_MODEL.*override"):
        AtlasStarter.apply_user_model_selections(
            _Stub(),
            {
                "LITELLM_EMBEDDING_MODEL": "custom/provider-b",
                "LANGMEM_EMBEDDING_DIM": "1024",
            },
        )


def test_apply_user_model_selections_accepts_explicitly_aligned_langmem_override():
    from start import AtlasStarter

    captured = {}

    class _SOM:
        def update_env_file(self, values):
            captured.update(values)
            return True

    class _Config:
        def parse_env_file(self):
            return {"LANGMEM_EMBEDDING_MODEL": "custom/provider-a"}

    class _Stub:
        banner = _NullBanner()
        source_override_manager = _SOM()
        config_parser = _Config()

    assert AtlasStarter.apply_user_model_selections(
        _Stub(),
        {
            "LITELLM_EMBEDDING_MODEL": "custom/provider-b",
            "LANGMEM_EMBEDDING_MODEL": "custom/provider-b",
            "LANGMEM_EMBEDDING_DIM": "1024",
        },
    )
    assert captured["LANGMEM_EMBEDDING_MODEL"] == "custom/provider-b"
    assert captured["LANGMEM_EMBEDDING_DIM"] == "1024"
