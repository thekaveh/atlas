"""
brand_logo — the 6-row block-art lockup, overridable via ``BRAND_LOGO_FILE``.

The TUI brand panel (``ui/textual/widgets/block_logo.py``) and the
``--no-tui`` startup banner (``utils/banner.py``) both render a 6-row
ANSI-Shadow block-art lockup. By default it is the built-in "ATLAS" lockup
(``ATLAS_FULL`` wide / ``ATLAS_COMPACT`` narrow). A fork can override it —
the same ``BRAND_*`` rebranding contract that already covers the
name/tagline/credits — by pointing ``BRAND_LOGO_FILE`` at a text file with
its own art::

    <wide-lockup rows>
    ---
    <compact-lockup rows>

A line that is exactly ``---`` (alone on its line) separates the wide lockup
from the narrow fallback used on terminals too small for the wide one. With
no ``---`` separator the single block is used for both widths. Leading and
trailing blank lines are trimmed; every other line is taken verbatim
(internal spacing preserved). Keep the art **6 rows tall** to fit the brand
panel (ANSI-Shadow figlet output is 6 rows regardless of text length).

An unset / empty / missing / unreadable / non-UTF-8 / all-blank
``BRAND_LOGO_FILE`` falls back to the built-in ATLAS art, byte-for-byte.

``resolve(env)`` returns ``(full_rows, compact_rows, width_threshold)``; both
render surfaces consume it, so a custom lockup stays in parity across the TUI
and the linear banner. The width threshold is ``full_width + 1`` so the wide
lockup renders only when it fits with ≥1 column of margin.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


# Built-in "ATLAS-PLATFORM" wide lockup (118 cells, 6 rows) — ANSI Shadow.
ATLAS_FULL: list[str] = [
    " █████╗ ████████╗██╗      █████╗ ███████╗        ██████╗ ██╗      █████╗ ████████╗███████╗ ██████╗ ██████╗ ███╗   ███╗",
    "██╔══██╗╚══██╔══╝██║     ██╔══██╗██╔════╝        ██╔══██╗██║     ██╔══██╗╚══██╔══╝██╔════╝██╔═══██╗██╔══██╗████╗ ████║",
    "███████║   ██║   ██║     ███████║███████╗ █████╗ ██████╔╝██║     ███████║   ██║   █████╗  ██║   ██║██████╔╝██╔████╔██║",
    "██╔══██║   ██║   ██║     ██╔══██║╚════██║ ╚════╝ ██╔═══╝ ██║     ██╔══██║   ██║   ██╔══╝  ██║   ██║██╔══██╗██║╚██╔╝██║",
    "██║  ██║   ██║   ███████╗██║  ██║███████║        ██║     ███████╗██║  ██║   ██║   ██║     ╚██████╔╝██║  ██║██║ ╚═╝ ██║",
    "╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝        ╚═╝     ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝      ╚═════╝ ╚═╝  ╚═╝╚═╝     ╚═╝",
]

# Built-in "ATLAS" narrow fallback (41 cells, 6 rows).
ATLAS_COMPACT: list[str] = [
    " █████╗ ████████╗██╗      █████╗ ███████╗",
    "██╔══██╗╚══██╔══╝██║     ██╔══██╗██╔════╝",
    "███████║   ██║   ██║     ███████║███████╗",
    "██╔══██║   ██║   ██║     ██╔══██║╚════██║",
    "██║  ██║   ██║   ███████╗██║  ██║███████║",
    "╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝",
]

_SEPARATOR = "---"


def _row_width(rows: list[str]) -> int:
    return max((len(r) for r in rows), default=0)


def width_threshold(full_rows: list[str]) -> int:
    """Min terminal columns to render ``full_rows`` with ≥1 column of margin."""
    return _row_width(full_rows) + 1


def _trim_blank_edges(rows: list[str]) -> list[str]:
    start, end = 0, len(rows)
    while start < end and not rows[start].strip():
        start += 1
    while end > start and not rows[end - 1].strip():
        end -= 1
    return rows[start:end]


def _parse(text: str) -> tuple[list[str], list[str]]:
    """Split a ``BRAND_LOGO_FILE`` body into ``(full_rows, compact_rows)``.

    A line that is exactly ``---`` (after stripping) separates the wide lockup
    from the narrow fallback. Without it, ``compact_rows`` is empty and the
    caller reuses the full rows for both widths.
    """
    lines = text.splitlines()
    sep = next((i for i, ln in enumerate(lines) if ln.strip() == _SEPARATOR), None)
    if sep is None:
        return _trim_blank_edges(lines), []
    return _trim_blank_edges(lines[:sep]), _trim_blank_edges(lines[sep + 1:])


def resolve(env: Mapping[str, str]) -> tuple[list[str], list[str], int]:
    """Return ``(full_rows, compact_rows, width_threshold)``.

    Reads ``BRAND_LOGO_FILE`` from ``env``. A readable file with at least one
    non-blank wide-lockup row overrides the built-in ATLAS art (its compact
    section — or the wide rows when none is given — is the narrow fallback).
    Any problem (unset, empty, missing, unreadable, not UTF-8, all-blank)
    falls back to the built-in ATLAS lockup byte-for-byte.
    """
    path = (env.get("BRAND_LOGO_FILE", "") or "").strip()
    if path:
        try:
            p = Path(path)
            if p.is_file():
                full, compact = _parse(p.read_text(encoding="utf-8"))
                if full:
                    if not compact:
                        compact = full
                    return full, compact, width_threshold(full)
        except (OSError, ValueError):
            pass  # unreadable or not UTF-8 → built-in fallback
    return list(ATLAS_FULL), list(ATLAS_COMPACT), width_threshold(ATLAS_FULL)


def resolve_from_env() -> tuple[list[str], list[str], int]:
    """``resolve()`` against the project ``.env`` (best-effort).

    Mirrors banner.py's brand-field reading: never raises — a missing or
    unreadable ``.env`` yields the built-in ATLAS lockup.
    """
    try:
        from core.config_parser import ConfigParser
        env = ConfigParser().parse_env_file() or {}
    except Exception:  # noqa: BLE001 — branding must never break startup
        env = {}
    return resolve(env)
