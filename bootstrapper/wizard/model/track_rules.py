"""Track force-disable rule (#535 Pass 1).

Extracted from ui/textual/integration.py::_selections_to_args. This is
domain truth about track semantics, placed in wizard/model so the
`--no-tui` path can adopt it in a later pass without importing the
wizard's Textual layer. Today `integration.py` (the Textual path) is
its only caller — `--no-tui` still runs its own, separate
`tracks.synthesize_track_source_args`.

Returns additions instead of mutating a caller dict — that is what
makes the rule unit-testable.

Fix-round note (#535 Pass 1 followups review, finding C2): the
``from tracks import is_in_track, load_tracks`` import lives at MODULE
scope, not inside ``track_force_disabled_sources``'s own try/except.
The ORIGINAL code (before this extraction) imported ``tracks`` inside
that try/except, so a broken ``tracks`` import (missing/broken
``yaml``/``jsonschema``, a partial venv, a syntax error in
``tracks.py``) degraded silently, exactly like a runtime registry
failure. Moving the import to module scope broke that: ``integration.py``
module-imports this module, which module-imports ``tracks`` -- so the
same ImportError now raises at wizard IMPORT time and kills the whole
wizard before a single step renders, even though this function's own
docstring still promises the opposite.

The import is guarded here (``try/except Exception`` around the
``from tracks import ...`` at module scope, falling back to ``None``
names) so an import failure degrades instead of propagating -- BUT the
names still live as ordinary module attributes, not captured into a
closure, so ``monkeypatch.setattr(track_rules, "load_tracks", ...)``
(used by ``test_registry_failure_never_blocks_the_wizard`` in
``tests/test_wizard_model_track_rules.py``) still works exactly as
before: the function looks the name up through the module's global
namespace at call time, and monkeypatch mutates that same namespace.
"""

from __future__ import annotations

from typing import Mapping, Sequence

try:
    from tracks import is_in_track, load_tracks
except Exception:  # noqa: BLE001 - see the fix-round note above; a
    # broken `tracks` import must degrade the wizard, not crash it.
    is_in_track = None  # type: ignore[assignment]
    load_tracks = None  # type: ignore[assignment]


def track_force_disabled_sources(
    *,
    track_key: str | None,
    services_info: Sequence[object],
    already_set: Mapping[str, str],
) -> dict[str, str]:
    """CLI-key -> "disabled" for every out-of-track service not already set.

    ``services_info`` items need only a ``.key`` attribute.

    An ``all``-style track (``track.services is None``) force-disables
    nothing. A track-registry load failure -- including a broken
    ``tracks`` import at module load, see the module docstring's
    fix-round note -- degrades to ``{}``: it must never block the
    wizard.
    """
    if not track_key:
        return {}

    if load_tracks is None or is_in_track is None:
        # `tracks` failed to import when this module was loaded.
        # Degrade exactly like a runtime registry failure below.
        return {}

    synthesized: dict[str, str] = {}
    try:
        registry = load_tracks()
        track = registry.by_key.get(track_key)
        if track is not None and track.services is not None:
            for svc in services_info:
                if is_in_track(track, svc.key, always_on=registry.always_on):
                    continue
                cli_key = svc.key.replace("-", "_") + "_source"
                if cli_key not in already_set:
                    synthesized[cli_key] = "disabled"
    except Exception:  # noqa: BLE001
        # Track-registry load failure must not block the wizard.
        return {}

    return synthesized
