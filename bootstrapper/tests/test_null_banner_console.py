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


def test_apply_user_model_selections_warns_without_crashing_under_nullbanner():
    """The reported crash: a non-768-dim embedding default triggers the
    dimension warning, which the step prints via ``self.banner.console.print``.
    Under the NullBanner that must be a no-op, not an AttributeError."""
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
    # qwen3-embedding:0.6b is 1536-dim → embedding_dim_warning fires → the
    # line that used to crash runs.
    selections = {
        "LITELLM_EMBEDDING_MODEL": "ollama/qwen3-embedding:0.6b",
        "OLLAMA_USER_MODELS": "qwen3.8:latest",
    }
    result = AtlasStarter.apply_user_model_selections(stub, selections)
    assert result is True
    # The selections were still persisted (warning is non-blocking).
    assert captured.get("OLLAMA_USER_MODELS") == "qwen3.8:latest"
