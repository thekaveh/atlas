"""Follow-ups from the whole-branch review of #535 Pass 0 + Pass 1.

Each test here pins a defect the review proved but that the original
Pass 0/Pass 1 landing didn't close, or closes a gap where an existing
test would pass against a broken implementation. See the review
findings C1-C5; this file covers C1 and C2 specifically (C3 lives in
``test_wizard_layer_boundaries.py``, C4/C5 in
``test_wizard_model_cloud_rules.py``).
"""
from __future__ import annotations

import importlib
import sys

import pytest


# ── C1: _selections_to_args.env_vars must be required, not defaulted ──
#
# The original default (``env_vars: dict | None = None``) normalized a
# missing ``env_vars`` to ``{}`` inside the function body. That makes
# ``existing_key_set`` False for EVERY cloud provider on EVERY call
# that omits the parameter, which -- via
# ``wizard/model/cloud_rules.resolve_cloud_provider``'s SECRET_KEEP
# branch -- silently force-disables every already-keyed cloud provider
# in .env. The one production call site (``run_setup_flow``'s
# ``_resolve`` closure) always passes the real snapshot, so this was
# latent, not live -- but a default that omits it "for free" is a trap
# for the next caller (the Pass 2/3 ViewModel, or any `--no-tui`
# adopter), not a safety net. Omitting it must raise ``TypeError``.


def test_selections_to_args_requires_env_vars():
    from ui.textual.integration import _selections_to_args

    with pytest.raises(TypeError):
        _selections_to_args({}, [], 63000)  # env_vars omitted


# ── C2: a broken `tracks` import must degrade, not crash the wizard ──
#
# ``track_rules.py`` moved its ``from tracks import is_in_track,
# load_tracks`` to module scope so a test could monkeypatch
# ``load_tracks`` directly. But ``ui/textual/integration.py``
# module-imports ``track_rules``, which module-imports ``tracks`` --
# so an ImportError in ``tracks`` (broken yaml/jsonschema, a partial
# venv, a syntax error in tracks.py) now raises at wizard IMPORT time
# and kills the whole wizard before a step renders, even though
# ``track_force_disabled_sources``'s own docstring still promises "a
# track-registry load failure degrades to `{}`: it must never block
# the wizard." The guarded, lazy-resolved import in ``track_rules.py``
# must make BOTH hold: the ImportError degrades, AND the monkeypatch
# seam used by ``test_registry_failure_never_blocks_the_wizard``
# survives.


def test_track_rules_import_error_of_tracks_does_not_propagate():
    """Poison ``sys.modules['tracks']`` with ``None`` -- the standard
    way to force ``from tracks import ...`` to raise ``ImportError``
    -- then reload ``track_rules`` under that poisoned state and prove
    neither the reload nor a subsequent call raises."""
    import wizard.model.track_rules as mod

    saved_tracks = sys.modules.get("tracks")
    sys.modules["tracks"] = None  # a None entry forces ImportError, not a real import
    try:
        importlib.reload(mod)
        assert mod.load_tracks is None, (
            "a guarded import failure must degrade the module-level name "
            "to None, not leave the stale real function bound"
        )

        from dataclasses import dataclass

        @dataclass
        class _Svc:
            key: str

        result = mod.track_force_disabled_sources(
            track_key="gen-ai-rag",
            services_info=[_Svc("comfyui")],
            already_set={},
        )
        assert result == {}
    finally:
        # Restore the real `tracks` module and reload track_rules again
        # so every OTHER test in the suite (including ones sharing this
        # interpreter) sees the genuine, working import -- not the
        # poisoned one this test intentionally created.
        if saved_tracks is not None:
            sys.modules["tracks"] = saved_tracks
        else:
            sys.modules.pop("tracks", None)
        importlib.reload(mod)
        assert mod.load_tracks is not None
