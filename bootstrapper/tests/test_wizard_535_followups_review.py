"""Follow-ups from the whole-branch review of #535 Pass 0 + Pass 1.

Each test here pins a defect the review proved but that the original
Pass 0/Pass 1 landing didn't close, or closes a gap where an existing
test would pass against a broken implementation. See the review
findings C1-C5; this file covers C1 (C2 originally lived here too, but
was retired -- see the note below; C3 lives in
``test_wizard_layer_boundaries.py``, C4/C5 in
``test_wizard_model_cloud_rules.py``).

C2 note (retired by the #535 followups review, finding R1): C2 pinned
that a broken ``tracks`` import degraded rather than crashed the
wizard, specifically for ``wizard/model/track_rules.py``'s
module-scope ``from tracks import ...``. Finding R1 deleted
``track_rules.py`` entirely -- it was a second, already-diverged copy
of ``tracks.synthesize_track_source_args``, the function ``start.py``'s
``--no-tui`` path actually uses. ``_selections_to_args`` now calls
``tracks.synthesize_track_source_args`` directly via a local,
function-scoped, try/except-guarded import (see
``ui/textual/integration.py``), which structurally can't raise at
wizard IMPORT time the way the old module-scope import could -- so
C2's specific failure mode no longer has a code path to occur through.
"""
from __future__ import annotations

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
