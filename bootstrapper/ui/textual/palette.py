"""
Canonical color tokens for the Textual TUI.

Widgets read these tokens as attributes (``P.TEXT``, ``P.ACCENT``, …).
``theme.css`` cannot import Python, so it hard-codes four of the values:
``$bg`` (BG), ``$text`` (TEXT), ``$border_panel`` (BORDER_PANEL) and
``$accent`` (ACCENT). Change a token and its ``theme.css`` twin together.
"""

from __future__ import annotations

import hashlib
from typing import Dict


# ─── Background layers ──────────────────────────────────────────────────
BG_INSET       = "#0e0f18"  # command preview, code blocks
BG             = "#12131e"  # terminal window

# ─── Borders ────────────────────────────────────────────────────────────
BORDER_PANEL    = "#2b2f4a"  # outer panel; theme.css $border_panel

# ─── Text ───────────────────────────────────────────────────────────────
TEXT_BRIGHT = "#e0e6f2"  # extra-emphasised
TEXT        = "#c0caf5"  # primary
TEXT_MUTED  = "#8992b5"  # secondary (timestamps, hints, "off")
TEXT_FAINT  = "#747d9d"  # tertiary (footer, very muted)

# ─── Semantic accents ───────────────────────────────────────────────────
ACCENT       = "#7dcfff"  # selection, active, primary CTA, "local"
ACCENT_HOVER = "#a8d4e6"  # hover intensification

OK    = "#98c379"  # success, healthy, "container", recommended
WARN  = "#ffbd2e"  # warnings, "changed"
ERR   = "#ff5f57"  # errors, danger button
ERR_SOFT = "#ff8a80"  # error TEXT (toast titles)
INFO  = "#89aad4"  # info, openwebui
RESOURCE = "#f0a050"  # GPU

# ─── Service category tags ──────────────────────────────────────────────
TAG_INFRA = "#9a8cc6"
TAG_LLM   = "#7dcfff"
TAG_ML    = "#b8a35a"
TAG_DATA  = "#6a9aaa"
TAG_TOOL  = "#89aad4"

# ─── Per-service hues (consistent across launch / health / chip / log src)
SVC_SUPABASE  = "#7dcfff"
SVC_REDIS     = "#98c379"
SVC_KONG      = "#9a8cc6"
SVC_OLLAMA    = "#b8a35a"
SVC_LITELLM   = "#d4a574"   # warmer hue than Ollama, signals the gateway role
SVC_WEAVIATE  = "#6a9aaa"
SVC_OPENWEBUI = "#89aad4"
SVC_BACKEND   = "#b8a35a"
SVC_COMFYUI   = "#98c379"
SVC_NEO4J     = "#6a9aaa"
SVC_N8N       = "#89aad4"
SVC_SEARXNG   = "#7dcfff"
SVC_MINIO     = "#c97b9a"   # warm magenta — distinct from data-tier blues
SVC_DISABLED  = "#565f89"

SOURCE_COLORS: Dict[str, str] = {
    "supabase": SVC_SUPABASE,
    "redis": SVC_REDIS,
    "kong": SVC_KONG,
    "litellm": SVC_LITELLM,
    "litellm-init": SVC_LITELLM,
    "ollama": SVC_OLLAMA,
    "weaviate": SVC_WEAVIATE,
    "openwebui": SVC_OPENWEBUI,
    "open-webui": SVC_OPENWEBUI,
    "backend": SVC_BACKEND,
    "comfyui": SVC_COMFYUI,
    "neo4j": SVC_NEO4J,
    "n8n": SVC_N8N,
    "searxng": SVC_SEARXNG,
    "minio": SVC_MINIO,
    "minio-init": SVC_MINIO,
    "disabled": SVC_DISABLED,
}

# ─── Glyphs used by the widgets ────────────────────────────────────────
DOT_RUNNING   = "●"
DOT_HOLLOW    = "○"
DOT_FILLED    = "◉"
ARROW_RIGHT   = "▸"
PROGRESS_FILLED = "⣿"
PROGRESS_EMPTY = "⣀"


# ─── Helpers ────────────────────────────────────────────────────────────
def color_for_source(name: str) -> str:
    """Map a service name → stable hue. Curated SOURCE_COLORS first;
    deterministic hash fallback for everything else (jupyterhub,
    openclaw, local-deep-researcher, …) so every service in the log
    pane gets a distinguishable color, matching the per-service
    coloring docker compose used to provide via ``--ansi=always``.
    Falls back to TEXT only for empty input.
    """
    if not name:
        return TEXT
    norm = name.lower().strip()
    for key, color in SOURCE_COLORS.items():
        if key in norm:
            return color
    return _hash_color_for(norm)


# Stable hue palette for the hash-based fallback. Hues chosen to be
# visually distinct from the curated SOURCE_COLORS above so a known
# service and a hash-fallback service rarely collide visually.
_FALLBACK_HUES = (
    "#c89cc4",  # purple
    "#d49a8a",  # warm pink
    "#a8b89e",  # sage
    "#7dcfa0",  # light teal
    "#d4b574",  # amber
    "#9aa6d4",  # periwinkle
    "#d49ac4",  # rose
    "#9ad4c0",  # mint
)


def _hash_color_for(norm: str) -> str:
    """Pick a deterministic hue for an arbitrary service name. Uses
    md5 (NOT Python's built-in ``hash``, which is randomized per process)
    so the same service gets the same color across runs.
    """
    h = hashlib.md5(norm.encode("utf-8")).digest()
    return _FALLBACK_HUES[h[0] % len(_FALLBACK_HUES)]


def style_for_source_choice(source: str) -> str:
    """Dot color driven by user CHOICE (not docker state)."""
    if not source or source == "disabled":
        return TEXT_MUTED
    if "container" in source or source.endswith("-cpu") or source.endswith("-gpu"):
        return OK
    if "localhost" in source or "external" in source or source == "api":
        return ACCENT
    return TEXT_MUTED


# ─── Category color tokens — drives the leading bar on every service row ───
#
# Canonical mapping lives in ``services.topology.CATEGORY_COLORS`` so the
# architecture-diagram generator and any future SVG/PDF emitter can reuse
# the exact same hues without depending on the TUI package. The ``CAT_*``
# names below are a named view of that dict for ``style=P.CAT_INFRA``
# syntax. Widgets currently resolve a slug through ``style_for_category()``.
#
# Note on the ``TAG_*`` / ``CAT_*`` distinction:
#   * ``CAT_*`` — canonical category colors driven by topology; six
#     categories, the hues of the leading service-row bar.
#   * ``TAG_*`` — legacy aliases for option-row badges; only TAG_INFRA and
#     TAG_DATA are referenced today (widgets/option_row.py), the rest are
#     kept as palette reserves. The two sets serve different taxonomies —
#     TAG_* labels badge content (CPU/GPU/MLX/embedding/...), CAT_* labels
#     topological category.
from services.topology import CATEGORY_COLORS as _CATEGORY_COLOR

CAT_INFRA  = _CATEGORY_COLOR["infra"]
CAT_DATA   = _CATEGORY_COLOR["data"]
CAT_LLM    = _CATEGORY_COLOR["llm"]
CAT_MEDIA  = _CATEGORY_COLOR["media"]
CAT_AGENTS = _CATEGORY_COLOR["agents"]
CAT_APPS   = _CATEGORY_COLOR["apps"]


def style_for_category(name: str) -> str:
    """Return the hex color for a category slug, falling back to TEXT_MUTED."""
    return _CATEGORY_COLOR.get((name or "").lower(), TEXT_MUTED)
