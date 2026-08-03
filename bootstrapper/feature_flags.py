"""Central feature flags — single source of truth for optional UI features.

Kept deliberately dependency-free so any layer (utils, ui, core) can import it
without risking an import cycle.
"""
from __future__ import annotations

import os

_FALSEY = {"", "0", "false", "no", "off"}


def _env_truthy(name: str) -> bool | None:
    """Tri-state read of an env var: ``None`` when unset/blank, else the parsed
    boolean (anything not in ``_FALSEY`` is truthy)."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    v = raw.strip().lower()
    if v == "":
        return None
    return v not in _FALSEY


# --- Atlas startup artwork (opening splash + linear hero) --------------------
# Master switch for ALL Atlas startup block-art: the TUI opening splash overlay
# (``AtlasSplash``) AND the linear / ``--no-tui`` printed hero banner
# (``BannerDisplay.show_hero``). Turned OFF for now — the block-art reproduction
# was judged too coarse at real terminal sizes.
#
# To re-enable permanently, flip ``_SPLASH_DEFAULT`` to ``True``. To toggle at
# runtime without editing code, set ``ATLAS_SPLASH=1`` (truthy enables, falsey
# disables). When enabled, the existing per-feature suppressors still apply on
# top: ``--no-splash`` / ``ATLAS_NO_SPLASH``, terminal-width, artwork-present.
SPLASH_FLAG_LIFECYCLE = {
    "owner": "Atlas maintainers",
    "introduced": "2026-06-17",
    "review_by": "2026-09-01",
    "decision": "Remove the flag or formalize it as a supported permanent setting.",
}
_SPLASH_DEFAULT = False


def splash_enabled() -> bool:
    """Whether Atlas startup artwork (splash overlay + linear hero) is enabled
    at all. ``ATLAS_SPLASH`` overrides the compiled-in default when set."""
    override = _env_truthy("ATLAS_SPLASH")
    return _SPLASH_DEFAULT if override is None else override
