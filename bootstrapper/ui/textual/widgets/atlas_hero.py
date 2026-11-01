"""Load and render the pre-generated Atlas hero cell-grid.

The grids are produced by bootstrapper/scripts/generate_logo.py and
committed under ../assets/. This module has no chafa/Pillow dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

from rich.align import Align
from rich.console import Group, RenderableType
from rich.style import Style
from rich.text import Text
from textual.widget import Widget

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
HERO_BACKGROUND = Style(bgcolor="#000412")


def _available(prefix: str) -> list[int]:
    """Column breakpoints with a committed cell-grid for ``prefix``, desc."""
    cols: list[int] = []
    for p in _ASSETS.glob(f"{prefix}_*.json"):
        try:
            cols.append(int(p.stem.rsplit("_", 1)[1]))
        except ValueError:
            continue
    return sorted(cols, reverse=True)


def load_hero(width: int, height: int | None = None,
              prefix: str = "atlas_hero") -> dict | None:
    """Largest committed cell-grid that fits: columns <= ``width`` and (when
    given) rows <= ``height``. Returns None when nothing fits.

    ``prefix`` selects the grid family (``atlas_hero`` = landscape source,
    ``atlas_poster`` = landscape poster with wordmark)."""
    for cols in _available(prefix):
        if width < cols:
            continue
        data = json.loads((_ASSETS / f"{prefix}_{cols}.json").read_text(encoding="utf-8"))
        if height is None or data["rows"] <= height:
            return data
    return None


def hero_rows(data: dict) -> list[Text]:
    rows: list[Text] = []
    for row in data["cells"]:
        t = Text()
        for glyph, fg, bg in row:
            t.append(glyph, Style(color=fg, bgcolor=bg))
        rows.append(t)
    return rows


class AtlasHero(Widget):
    """Static block-art hero, horizontally centered, sized to the grid."""

    can_focus = False

    def __init__(self, width: int, *, height: int | None = None,
                 prefix: str = "atlas_hero", name: str | None = None,
                 id: str | None = None, classes: str | None = None,
                 disabled: bool = False) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._data = load_hero(width, height, prefix)

    @property
    def grid_rows(self) -> int:
        return self._data["rows"] if self._data else 0

    def render(self) -> RenderableType:
        if not self._data:
            return Text("")
        return Group(*[Align.center(t, style=HERO_BACKGROUND) for t in hero_rows(self._data)])
