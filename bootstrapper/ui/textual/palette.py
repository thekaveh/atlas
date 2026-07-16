"""
Canonical color tokens for the Textual TUI.

Widgets reference these tokens by name; ``theme.css`` exposes them as
``$``-prefixed Textual variables. This module is the source of truth
for the bootstrapper UI's color palette.
"""

from __future__ import annotations

import hashlib
from typing import Dict


# ─── Background layers (darkest → lightest) ─────────────────────────────
BG_DEEP        = "#080910"  # page / outermost (also: inverted button text)
BG_INSET       = "#0e0f18"  # command preview, code blocks
BG_PANE        = "#10111a"  # log pane (distinct from terminal bg)
BG             = "#12131e"  # terminal window
BG_RIBBON      = "#141525"  # status ribbon, top-panel, card header
BG_CHROME      = "#161728"  # window title bar, standard panel
BG_ELEVATED    = "#1c1d30"  # overlay titlebar, keycap background
BG_KEYCAP      = "#1e1f33"  # keycap inset

# Backwards-compat aliases used by existing widgets
COLOR_BG               = BG
COLOR_BG_DARKEST       = BG_DEEP
COLOR_BG_RIBBON        = BG_RIBBON
COLOR_BG_CHROME        = BG_CHROME
COLOR_SURFACE          = BG_CHROME
COLOR_SURFACE_DARKER   = BG_RIBBON
COLOR_SURFACE_LIGHTER  = BG_ELEVATED
COLOR_SURFACE_DEEP     = BG_INSET

# ─── Borders ────────────────────────────────────────────────────────────
BORDER_HAIRLINE = "#1e2038"  # section dividers (most common)
BORDER          = "#252840"  # window chrome bottom, command preview
BORDER_PANEL    = "#2b2f4a"  # outer panel, filter chip inactive, key badge
BORDER_KEYCAP   = "#3d4261"  # keycap edge

# Backwards-compat
COLOR_BORDER       = BORDER
COLOR_BORDER_LIGHT = BORDER_PANEL
COLOR_BORDER_DIM   = BORDER_HAIRLINE

# ─── Text ───────────────────────────────────────────────────────────────
TEXT_BRIGHT = "#e0e6f2"  # extra-emphasised
TEXT        = "#c0caf5"  # primary
TEXT_MUTED  = "#8992b5"  # secondary (timestamps, hints, "off")
TEXT_FAINT  = "#747d9d"  # tertiary (footer, very muted)

COLOR_TEXT        = TEXT
COLOR_TEXT_BRIGHT = TEXT_BRIGHT
COLOR_TEXT_DIM    = TEXT_MUTED
COLOR_TEXT_MUTED  = TEXT_FAINT

# ─── Semantic accents ───────────────────────────────────────────────────
ACCENT       = "#7dcfff"  # selection, active, primary CTA, "local"
ACCENT_HOVER = "#a8d4e6"  # hover intensification
ACCENT_BRIGHT = "#a5d6ff"

OK    = "#98c379"  # success, healthy, "container", recommended
WARN  = "#ffbd2e"  # warnings, "changed"
ERR   = "#ff5f57"  # errors, danger button
ERR_SOFT = "#ff8a80"  # error TEXT (toast titles)
INFO  = "#89aad4"  # info, openwebui
RESOURCE = "#f0a050"  # GPU
CONTAINER = OK

COLOR_ACCENT       = ACCENT
COLOR_ACCENT_DIM   = "#5c8db5"
COLOR_ACCENT_BRIGHT = ACCENT_BRIGHT
COLOR_OK   = OK
COLOR_WARN = WARN
COLOR_ERR  = ERR
COLOR_ERR_SOFT = ERR_SOFT
COLOR_INFO = INFO
COLOR_RESOURCE = RESOURCE
COLOR_CONTAINER = OK

# ─── Service category tags ──────────────────────────────────────────────
TAG_INFRA = "#9a8cc6"
TAG_LLM   = "#7dcfff"
TAG_ML    = "#b8a35a"
TAG_DATA  = "#6a9aaa"
TAG_TOOL  = "#89aad4"

COLOR_TAG_INFRA = TAG_INFRA
COLOR_TAG_LLM   = TAG_LLM
COLOR_TAG_ML    = TAG_ML
COLOR_TAG_DATA  = TAG_DATA
COLOR_TAG_TOOL  = TAG_TOOL

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

# 1-cell-wide service glyphs.
# Backwards-compat alias names (kept for any external code still using them)
COLOR_SRC_SUPABASE  = SVC_SUPABASE
COLOR_SRC_REDIS     = SVC_REDIS
COLOR_SRC_KONG      = SVC_KONG
COLOR_SRC_OLLAMA    = SVC_OLLAMA
COLOR_SRC_WEAVIATE  = SVC_WEAVIATE
COLOR_SRC_OPENWEBUI = SVC_OPENWEBUI
COLOR_SRC_BACKEND   = SVC_BACKEND
COLOR_SRC_COMFYUI   = SVC_COMFYUI
COLOR_SRC_NEO4J     = SVC_NEO4J
COLOR_SRC_N8N       = SVC_N8N
COLOR_SRC_SEARXNG   = SVC_SEARXNG
COLOR_SRC_MINIO     = SVC_MINIO

# ─── Gradient stops (approximated via multi-color spans in TUI) ─────────
LOGO_GRADIENT = ["#4a9eff", "#7dcfff", "#a8d4e6", "#7dcfff"]   # 4-stop
ACCENT_BAR_STOPS = ["#4a9eff", "#7dcfff", "#98c379", "#7dcfff", "#565f89"]  # 5-stop vertical

# ─── Pre-computed opaque equivalents for CSS rgba effects ───────────────
# Formula: opaque = bg + (fg − bg) × alpha, computed against BG_CHROME.
# Used wherever the mockup says rgba(...) — Textual cannot do alpha.
SEL_SVC_HOVER     = "#181a2c"  # rgba(125,207,255,0.04) over BG_CHROME
SEL_SVC_ACTIVE    = "#1b1f31"  # rgba(125,207,255,0.07)
SEL_OPT_HOVER     = "#181a2c"  # rgba(125,207,255,0.04)
SEL_OPT_ACTIVE    = "#1c2034"  # rgba(125,207,255,0.08)
SEL_LOG_HOVER     = "#161827"  # rgba(125,207,255,0.02)
SECTION_HEADER_BG = "#171829"  # rgba(125,207,255,0.04)

TOAST_WARN_BG     = "#1c1d27"  # rgba(255,189,46,0.05)
TOAST_WARN_BORDER = "#7a6429"  # rgba(255,189,46,0.4)
TOAST_ERR_BG      = "#1d1820"  # rgba(255,95,87,0.05)
TOAST_ERR_BORDER  = "#80393a"  # rgba(255,95,87,0.4)

CONFLICT_BORDER   = "#80393a"  # rgba(255,95,87,0.3) — close enough
HEALTHY_BORDER    = "#3a4d2e"  # rgba(152,195,121,0.3)
STARTING_BORDER   = "#2c3e54"  # rgba(125,207,255,0.3)

# ─── Glyphs (every Unicode char used in the mockups) ────────────────────
DOT_DIAMOND   = "◆"
DOT_RUNNING   = "●"
DOT_HOLLOW    = "○"
DOT_FILLED    = "◉"
DOT_SQUARE    = "▪"
ARROW_RIGHT   = "▸"
SPINNER_CYCLE = ("◐", "◓", "◑", "◒")
GLYPH_HOURGLASS = "⏳"
GLYPH_SEARCH  = "⌕"
GLYPH_COPY    = "⧉"
GLYPH_DOT     = "·"
GLYPH_DASH    = "—"
COLLAPSE_DOWN = "▾"
COLLAPSE_UP   = "▴"
PROGRESS_FILLED = "⣿"
PROGRESS_PARTIAL = ("⣷", "⣽")
PROGRESS_EMPTY = "⣀"
PROG_THIN_FULL  = "▰"
PROG_THIN_EMPTY = "▱"


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


def style_for_level(level: str) -> str:
    """Hex color for a log/status level."""
    return {
        "info": TEXT,
        "ok": OK,
        "success": OK,
        "warn": WARN,
        "warning": WARN,
        "err": ERR,
        "error": ERR,
        "dim": TEXT_MUTED,
        "title": ACCENT,
        "accent": ACCENT,
    }.get((level or "").lower(), TEXT)


def spaced_caps(s: str) -> str:
    """Approximate CSS letter-spacing on uppercase labels — used by section
    headers like ``CORE SERVICES`` / ``ACCESS URLS`` / ``OPTIONS``."""
    return " ".join(s.upper())


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
# named tokens below are aliases of that dict, kept so widget code can
# write ``style=P.CAT_INFRA`` without rebuilding the dict each time.
#
# Note on the ``TAG_*`` / ``CAT_*`` distinction:
#   * ``CAT_*`` — canonical category colors driven by topology. Source of
#     truth for new code; six categories, drives the leading service-row bar.
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
