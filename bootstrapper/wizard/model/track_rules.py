"""Track force-disable rule (#535 Pass 1).

Extracted from ui/textual/integration.py::_selections_to_args. This is
domain truth about track semantics, placed in wizard/model so the
`--no-tui` path can adopt it in a later pass without importing the
wizard's Textual layer. Today `integration.py` (the Textual path) is
its only caller — `--no-tui` still runs its own, separate
`tracks.synthesize_track_source_args`.

Returns additions instead of mutating a caller dict — that is what
makes the rule unit-testable.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from tracks import is_in_track, load_tracks


def track_force_disabled_sources(
    *,
    track_key: str | None,
    services_info: Sequence[object],
    already_set: Mapping[str, str],
) -> dict[str, str]:
    """CLI-key -> "disabled" for every out-of-track service not already set.

    ``services_info`` items need only a ``.key`` attribute.

    An ``all``-style track (``track.services is None``) force-disables
    nothing. A track-registry load failure degrades to ``{}``: it must
    never block the wizard.
    """
    if not track_key:
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
