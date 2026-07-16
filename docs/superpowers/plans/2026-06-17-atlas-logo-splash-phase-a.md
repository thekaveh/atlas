# Atlas Logo + Wizard Splash — Phase A (App) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the Atlas-and-globe source image to colored terminal block-art shipped as committed data, and play it as a skippable opening splash in the setup wizard that pixel-dissolves into the live UI (with a linear/`--no-tui` fallback).

**Architecture:** A maintainer-only generator (Pillow + `chafa`) crops/enhances the source and pre-renders it to per-width JSON cell-grids committed in-repo. At runtime the app reads the nearest-width grid and paints it via Rich `Text` (no `chafa`/Pillow dependency for end users). A `AtlasSplash` overlay covers the wizard's content region, holds ~3s, then pixel-dissolves to reveal the wizard beneath; any key/mouse skips. The linear path prints the same grid once.

**Tech Stack:** Python ≥3.10, Textual (TUI), Rich (`Text`/`Style`), `chafa` (generator only), Pillow (generator only), pytest.

## 1. Global Constraints

Every task's requirements implicitly include these (verbatim from the spec and project rules):

- **No emojis anywhere** — code, comments, docs, commit messages, CLI output.
- **No runtime `chafa` or Pillow dependency** for end users. The generator (Pillow + `chafa`) is maintainer-only and is not imported by the app; do not add Pillow/chafa to `bootstrapper/pyproject.toml`.
- **No CI drift gate for the art.** Committed JSON cell-grids are the source of truth; regeneration is a manual maintainer step. Do not add `chafa` to CI.
- **No third-party project credited as the inspiration** for Atlas's name or logo anywhere (docs, code comments, commits). (The `hermes` service that ships in the stack is unaffected — this concerns inspiration credit only.)
- **Explicit `encoding="utf-8"`** on every text-IO call (`open`, `read_text`, `write_text`). Project convention.
- **Commit messages:** terse, third-person, conventional prefix (e.g. `feat(tui):`). **No `Co-Authored-By` trailer.**
- **`main` is protected.** All work lands via PR with the four `services-lint` checks green; never `git push origin main`. Work on a branch.
- The committed source PNG is ~3MB (>1MB). Pushing it needs a one-shot buffer bump: `git -c http.postBuffer=524288000 push`. Optionally shrink first with `pngquant --quality=70-85` (~4× reduction, no visible loss).
- Locked render params: w84 crop `x ∈ [0.101, 0.941]`, full height; enhance gamma `1.12` / brightness `1.04` / saturation `1.22` / contrast `1.16`; `chafa -f symbols -c full --symbols block+space --fill block --dither none`.
- Width breakpoints: `160, 120, 100, 80`. Below 80 columns: no hero (caller falls back to the compact title).
- Tests run via `cd bootstrapper && uv run pytest`; test imports are rooted at `bootstrapper/` (e.g. `from ui.textual.widgets.atlas_hero import ...`).

---

### 1.1. Task 1: Source asset + generator + committed cell-grids

**Files:**
- Create: `assets/atlas-source.png` (copy of the master source image)
- Create: `bootstrapper/scripts/generate_logo.py` (maintainer generator)
- Create: `bootstrapper/ui/textual/assets/atlas_hero_80.json`, `_100.json`, `_120.json`, `_160.json` (generated, committed)
- Test: `bootstrapper/tests/test_atlas_hero_data.py`

**Interfaces:**
- Produces: JSON files each shaped `{"cols": int, "rows": int, "cells": [[[glyph, fg_hex, bg_hex], ...per column...], ...per row...]}`. `glyph` is a single str; `fg_hex`/`bg_hex` are `"#rrggbb"`.
- Produces: `generate_logo.main()` (maintainer entry; not imported by the app).

- [ ] **Step 1: Copy the source image into the repo**

```bash
mkdir -p assets bootstrapper/ui/textual/assets
cp "/Users/kaveh/.claude/uploads/1b8eef11-cd75-485c-9d0b-c4da70fb0b87/76fc5a47-848569C749154FA88E1CDF9112C3D02C.png" assets/atlas-source.png
# optional: pngquant --quality=70-85 --force --output assets/atlas-source.png assets/atlas-source.png
```

- [ ] **Step 2: Write the generator**

Create `bootstrapper/scripts/generate_logo.py`:

```python
#!/usr/bin/env python3
"""Maintainer-only generator: render assets/atlas-source.png to per-width
terminal cell-grids committed under bootstrapper/ui/textual/assets/.

Requires Pillow and chafa (NOT runtime deps):
    pip install pillow   &&   brew install chafa   # or apt-get install chafa

Run from the repo root:
    python bootstrapper/scripts/generate_logo.py
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageEnhance

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "assets" / "atlas-source.png"
OUT_DIR = REPO / "bootstrapper" / "ui" / "textual" / "assets"
BREAKPOINTS = (160, 120, 100, 80)

# Locked params.
CROP = (0.101, 0.0, 0.941, 1.0)
GAMMA, BRIGHT, SAT, CONTRAST = 1.12, 1.04, 1.22, 1.16

_SGR = re.compile(r"\x1b\[([0-9;?]*)m")
_OTHER = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_DEFAULT_FG = "#c0caf5"
_DEFAULT_BG = "#0e0f18"


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _enhanced_source() -> Image.Image:
    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    box = (int(CROP[0] * w), int(CROP[1] * h), int(CROP[2] * w), int(CROP[3] * h))
    im = im.crop(box)
    lut = [min(255, int(((i / 255) ** GAMMA) * 255 * BRIGHT)) for i in range(256)]
    im = im.point(lut * 3)
    im = ImageEnhance.Color(im).enhance(SAT)
    im = ImageEnhance.Contrast(im).enhance(CONTRAST)
    return im


def _chafa(tmp_png: Path, cols: int, rows: int) -> str:
    cmd = [
        "chafa", "-f", "symbols", "-c", "full", "--symbols", "block+space",
        "--fill", "block", "--dither", "none", "-s", f"{cols}x{rows}", str(tmp_png),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def _parse(ansi: str) -> list[list[list[str]]]:
    fg, bg = _DEFAULT_FG, _DEFAULT_BG
    ansi = _OTHER.sub(lambda m: m.group(0) if m.group(0).endswith("m") else "", ansi)
    rows: list[list[list[str]]] = []
    cur: list[list[str]] = []
    i = 0
    while i < len(ansi):
        m = _SGR.match(ansi, i)
        if m:
            parts = [p for p in m.group(1).split(";") if p != ""]
            j = 0
            while j < len(parts):
                c = parts[j]
                if c == "0":
                    fg, bg = _DEFAULT_FG, _DEFAULT_BG
                elif c == "38" and parts[j + 1 : j + 2] == ["2"]:
                    fg = _hex((int(parts[j + 2]), int(parts[j + 3]), int(parts[j + 4]))); j += 4
                elif c == "48" and parts[j + 1 : j + 2] == ["2"]:
                    bg = _hex((int(parts[j + 2]), int(parts[j + 3]), int(parts[j + 4]))); j += 4
                j += 1
            i = m.end(); continue
        ch = ansi[i]
        if ch == "\n":
            rows.append(cur); cur = []; i += 1; continue
        if ch in "\r\x1b":
            i += 1; continue
        cur.append([ch, fg, bg]); i += 1
    if cur:
        rows.append(cur)
    rows = [r for r in rows if r]
    width = max(len(r) for r in rows)
    for r in rows:
        while len(r) < width:
            r.append([" ", _DEFAULT_FG, _DEFAULT_BG])
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    im = _enhanced_source()
    aspect = im.height / im.width
    tmp = OUT_DIR / "_tmp_source.png"
    im.save(tmp)
    try:
        for cols in BREAKPOINTS:
            rows = max(1, int(cols * aspect * 0.5) + 1)
            grid = _parse(_chafa(tmp, cols, rows))
            data = {"cols": len(grid[0]), "rows": len(grid), "cells": grid}
            out = OUT_DIR / f"atlas_hero_{cols}.json"
            out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            print(f"wrote {out.relative_to(REPO)}  {data['cols']}x{data['rows']}")
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the generator to emit the committed JSON**

Run: `python bootstrapper/scripts/generate_logo.py`
Expected: four `wrote bootstrapper/ui/textual/assets/atlas_hero_*.json ...` lines; no `_tmp_source.png` left behind.

- [ ] **Step 4: Write the data-validation test**

Create `bootstrapper/tests/test_atlas_hero_data.py`:

```python
import json
import re
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parent.parent / "ui" / "textual" / "assets"
BREAKPOINTS = (80, 100, 120, 160)
_HEX = re.compile(r"^#[0-9a-f]{6}$")


@pytest.mark.parametrize("cols", BREAKPOINTS)
def test_grid_is_wellformed(cols):
    data = json.loads((ASSETS / f"atlas_hero_{cols}.json").read_text(encoding="utf-8"))
    assert data["cols"] == cols
    assert data["rows"] == len(data["cells"]) > 0
    for row in data["cells"]:
        assert len(row) == cols
        for glyph, fg, bg in row:
            assert isinstance(glyph, str) and len(glyph) == 1
            assert _HEX.match(fg) and _HEX.match(bg)
```

- [ ] **Step 5: Run the test**

Run: `cd bootstrapper && uv run pytest tests/test_atlas_hero_data.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add assets/atlas-source.png bootstrapper/scripts/generate_logo.py \
        bootstrapper/ui/textual/assets/atlas_hero_*.json \
        bootstrapper/tests/test_atlas_hero_data.py
git commit -m "feat(tui): add Atlas hero generator and committed cell-grids"
```

---

### 1.2. Task 2: Hero loader + `AtlasHero` widget

**Files:**
- Create: `bootstrapper/ui/textual/widgets/atlas_hero.py`
- Test: `bootstrapper/tests/test_atlas_hero.py`

**Interfaces:**
- Consumes: the JSON cell-grids from Task 1.
- Produces:
  - `load_hero(width: int) -> dict | None` — returns the parsed grid dict for the largest breakpoint `<= width`, or `None` when `width < 80`.
  - `hero_rows(data: dict) -> list[rich.text.Text]` — one centered-ready `Text` per grid row.
  - `class AtlasHero(textual.widget.Widget)` — renders the nearest-width hero, vertically as `data["rows"]` cells tall.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_atlas_hero.py`:

```python
import pytest
from rich.text import Text

from ui.textual.widgets.atlas_hero import load_hero, hero_rows


@pytest.mark.parametrize("width,expected_cols", [
    (200, 160), (160, 160), (159, 120), (120, 120),
    (110, 100), (100, 100), (90, 80), (80, 80),
])
def test_load_hero_picks_largest_fitting_breakpoint(width, expected_cols):
    data = load_hero(width)
    assert data is not None and data["cols"] == expected_cols


def test_load_hero_returns_none_below_min():
    assert load_hero(79) is None


def test_hero_rows_render_one_text_per_row_full_width():
    data = load_hero(100)
    rows = hero_rows(data)
    assert len(rows) == data["rows"]
    assert all(isinstance(t, Text) for t in rows)
    assert len(rows[0].plain) == data["cols"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_atlas_hero.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.textual.widgets.atlas_hero'`.

- [ ] **Step 3: Write the implementation**

Create `bootstrapper/ui/textual/widgets/atlas_hero.py`:

```python
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
_BREAKPOINTS = (160, 120, 100, 80)  # descending


def load_hero(width: int) -> dict | None:
    """Largest breakpoint <= width, or None when width < smallest (80)."""
    for cols in _BREAKPOINTS:
        if width >= cols:
            path = _ASSETS / f"atlas_hero_{cols}.json"
            return json.loads(path.read_text(encoding="utf-8"))
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

    def __init__(self, width: int, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._data = load_hero(width)

    @property
    def grid_rows(self) -> int:
        return self._data["rows"] if self._data else 0

    def render(self) -> RenderableType:
        if not self._data:
            return Text("")
        return Group(*[Align.center(t) for t in hero_rows(self._data)])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_atlas_hero.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/ui/textual/widgets/atlas_hero.py bootstrapper/tests/test_atlas_hero.py
git commit -m "feat(tui): add Atlas hero loader and widget"
```

---

### 1.3. Task 3: Splash pure logic (dissolve order + show-decision + dissolved set)

**Files:**
- Create: `bootstrapper/ui/textual/widgets/atlas_splash.py` (logic only this task)
- Test: `bootstrapper/tests/test_atlas_splash_logic.py`

**Interfaces:**
- Produces:
  - `should_show_splash(no_splash: bool) -> bool` — `False` if `no_splash` or env `ATLAS_NO_SPLASH` is set (truthy); else `True`. (Non-TTY/CI is already excluded upstream by `is_tui_capable`.)
  - `dissolve_order(n_cells: int, seed: int = 1337) -> list[int]` — a deterministic permutation of `range(n_cells)`.
  - `dissolved_count(n_cells: int, progress: float) -> int` — `round(clamp(progress, 0, 1) * n_cells)`.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_atlas_splash_logic.py`:

```python
from ui.textual.widgets.atlas_splash import (
    should_show_splash, dissolve_order, dissolved_count,
)


def test_show_decision(monkeypatch):
    monkeypatch.delenv("ATLAS_NO_SPLASH", raising=False)
    assert should_show_splash(no_splash=False) is True
    assert should_show_splash(no_splash=True) is False
    monkeypatch.setenv("ATLAS_NO_SPLASH", "1")
    assert should_show_splash(no_splash=False) is False


def test_dissolve_order_is_deterministic_permutation():
    a = dissolve_order(500)
    b = dissolve_order(500)
    assert a == b
    assert sorted(a) == list(range(500))


def test_dissolved_count_bounds():
    assert dissolved_count(100, 0.0) == 0
    assert dissolved_count(100, 1.0) == 100
    assert dissolved_count(100, 0.5) == 50
    assert dissolved_count(100, 2.0) == 100
    assert dissolved_count(100, -1.0) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_atlas_splash_logic.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

Create `bootstrapper/ui/textual/widgets/atlas_splash.py`:

```python
"""Atlas opening-splash: pure logic (Task 3) + Textual widget (Task 4)."""
from __future__ import annotations

import os
import random


def should_show_splash(no_splash: bool) -> bool:
    if no_splash:
        return False
    if (os.environ.get("ATLAS_NO_SPLASH", "") or "").strip():
        return False
    return True


def dissolve_order(n_cells: int, seed: int = 1337) -> list[int]:
    idx = list(range(n_cells))
    random.Random(seed).shuffle(idx)
    return idx


def dissolved_count(n_cells: int, progress: float) -> int:
    p = max(0.0, min(1.0, progress))
    return round(p * n_cells)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_atlas_splash_logic.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/ui/textual/widgets/atlas_splash.py bootstrapper/tests/test_atlas_splash_logic.py
git commit -m "feat(tui): add Atlas splash dissolve/show logic"
```

---

### 1.4. Task 4: `AtlasSplash` overlay widget (hold, dissolve, skip)

**Files:**
- Modify: `bootstrapper/ui/textual/widgets/atlas_splash.py` (append the widget)
- Test: `bootstrapper/tests/test_atlas_splash_widget.py`

**Interfaces:**
- Consumes: `load_hero`, `hero_rows` (Task 2); `dissolve_order`, `dissolved_count` (Task 3).
- Produces: `class AtlasSplash(Widget)` with:
  - `__init__(self, width: int, *, hold: float = 3.0, frames: int = 14, on_done: Callable[[], None])`
  - `render() -> RenderableType` — hero rows with the first `dissolved_count(...)` cells (in `dissolve_order`) blanked to a transparent space, so the wizard beneath shows through.
  - `skip(self) -> None` — completes immediately and calls `on_done` once.
  - Reacts to `Key` and `MouseDown` by calling `skip()`.

**Note on testing:** the existing suite has no Textual Pilot harness. Test `render()` output and the idempotent `on_done` via direct instantiation (no running app); verify interactive timing manually in Step 6.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_atlas_splash_widget.py`:

```python
from ui.textual.widgets.atlas_splash import AtlasSplash


def test_full_progress_blanks_every_cell():
    calls = []
    s = AtlasSplash(100, on_done=lambda: calls.append(1))
    s._progress = 1.0  # fully dissolved
    rendered = s.render()
    # Every painted glyph is a space once fully dissolved.
    from rich.console import Console
    txt = Console(width=400).render_str  # noqa: F841 (sanity import)
    assert s._blank_cell_count() == s._n_cells


def test_skip_is_idempotent():
    calls = []
    s = AtlasSplash(100, on_done=lambda: calls.append(1))
    s.skip()
    s.skip()
    assert calls == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_atlas_splash_widget.py -v`
Expected: FAIL — `AtlasSplash` not defined / attributes missing.

- [ ] **Step 3: Append the widget implementation**

Append to `bootstrapper/ui/textual/widgets/atlas_splash.py`:

```python
from typing import Callable

from rich.align import Align
from rich.console import Group, RenderableType
from rich.style import Style
from rich.text import Text
from textual import events
from textual.widget import Widget

from ui.textual.widgets.atlas_hero import load_hero


class AtlasSplash(Widget):
    """Overlay that holds the hero, then pixel-dissolves to reveal the
    wizard beneath, removing itself when done. Any key/mouse skips."""

    DEFAULT_CSS = """
    AtlasSplash { width: 100%; height: 100%; background: transparent; }
    """

    can_focus = True

    def __init__(self, width: int, *, hold: float = 3.0, frames: int = 14,
                 on_done: Callable[[], None]) -> None:
        super().__init__()
        self._data = load_hero(width)
        self._hold = hold
        self._frames = max(1, frames)
        self._on_done = on_done
        self._done = False
        self._progress = 0.0
        cols = self._data["cols"] if self._data else 0
        rows = self._data["rows"] if self._data else 0
        self._n_cells = cols * rows
        from ui.textual.widgets.atlas_splash import dissolve_order  # self-module
        self._order = dissolve_order(self._n_cells)

    # --- rendering -----------------------------------------------------
    def _blank_indices(self) -> set[int]:
        from ui.textual.widgets.atlas_splash import dissolved_count
        return set(self._order[: dissolved_count(self._n_cells, self._progress)])

    def _blank_cell_count(self) -> int:
        return len(self._blank_indices())

    def render(self) -> RenderableType:
        if not self._data:
            return Text("")
        blanks = self._blank_indices()
        cols = self._data["cols"]
        out: list[RenderableType] = []
        for y, row in enumerate(self._data["cells"]):
            t = Text()
            for x, (glyph, fg, bg) in enumerate(row):
                if (y * cols + x) in blanks:
                    t.append(" ")  # transparent -> wizard shows through
                else:
                    t.append(glyph, Style(color=fg, bgcolor=bg))
            out.append(Align.center(t))
        return Group(*out)

    # --- lifecycle -----------------------------------------------------
    def on_mount(self) -> None:
        self.focus()
        self.set_timer(self._hold, self._start_dissolve)

    def _start_dissolve(self) -> None:
        if self._done:
            return
        self._step = 0
        self._interval = self.set_interval(0.05, self._tick)

    def _tick(self) -> None:
        self._step += 1
        self._progress = self._step / self._frames
        self.refresh()
        if self._step >= self._frames:
            self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            self.remove()
        finally:
            self._on_done()

    def skip(self) -> None:
        if self._done:
            return
        self._progress = 1.0
        self._finish()

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.skip()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        event.stop()
        self.skip()
```

- [ ] **Step 4: Simplify the test to match the public surface**

Replace `bootstrapper/tests/test_atlas_splash_widget.py` with:

```python
from ui.textual.widgets.atlas_splash import AtlasSplash


def test_full_progress_blanks_every_cell():
    s = AtlasSplash(100, on_done=lambda: None)
    s._progress = 1.0
    assert s._blank_cell_count() == s._n_cells


def test_zero_progress_blanks_nothing():
    s = AtlasSplash(100, on_done=lambda: None)
    s._progress = 0.0
    assert s._blank_cell_count() == 0


def test_skip_is_idempotent():
    calls = []
    s = AtlasSplash(100, on_done=lambda: calls.append(1))
    s.skip()
    s.skip()
    assert calls == [1]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_atlas_splash_widget.py -v`
Expected: 3 PASS. (`remove()` on an unmounted widget is a no-op outside a running app; if it raises, wrap the `self.remove()` call in `try/except Exception` — the `on_done` callback must still fire.)

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/ui/textual/widgets/atlas_splash.py bootstrapper/tests/test_atlas_splash_widget.py
git commit -m "feat(tui): add Atlas splash overlay widget"
```

---

### 1.5. Task 5: Mount the splash in `WizardScreen`

**Files:**
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py` (constructor + `on_mount`)
- Test: manual (Step 4) — the existing suite does not drive `WizardScreen` interactively.

**Interfaces:**
- Consumes: `AtlasSplash` (Task 4), `should_show_splash` (Task 3).
- Produces: `WizardScreen.__init__(..., no_splash: bool = False)` accepted and stored as `self._no_splash`.

- [ ] **Step 1: Accept `no_splash` in the constructor**

In `bootstrapper/ui/textual/screens/wizard_screen.py`, add a keyword parameter `no_splash: bool = False` to `WizardScreen.__init__` and store `self._no_splash = no_splash` alongside the other stored attributes (near where `self._phase` / panels are initialized, ~line 168).

- [ ] **Step 2: Mount the splash overlay on screen mount**

Add (or extend) `on_mount` on `WizardScreen` so that, after the body is composed, the splash is mounted as an overlay covering the content region and dismisses itself:

```python
def on_mount(self) -> None:
    # ...any existing on_mount body stays first...
    from ui.textual.widgets.atlas_splash import AtlasSplash, should_show_splash
    if not should_show_splash(self._no_splash):
        return
    width = self.app.size.width or 100
    if width < 80:
        return  # too narrow for the hero; skip straight to the wizard
    splash = AtlasSplash(width, on_done=lambda: None)
    # cover the area below BrandPanel and above FooterBar
    self.mount(splash, after=self.query_one("#info-section"))
```

CSS: add a rule so the splash sits on a top layer over `#info-section` + `#lower-pane`. In the `WizardScreen` `DEFAULT_CSS` (near the existing `#lower-pane` rule, ~line 152), add:

```css
WizardScreen { layers: base overlay; }
WizardScreen AtlasSplash { layer: overlay; dock: top; height: 1fr; }
```

(Exact dock/height to be confirmed in Step 4 so the overlay spans `#info-section` through `#lower-pane` without covering `BrandPanel` or `FooterBar`. If `dock: top` proves imprecise, mount the splash inside a wrapper that already spans those two regions.)

- [ ] **Step 3: Keep the rest of the suite green**

Run: `cd bootstrapper && uv run pytest -q`
Expected: PASS (no behavioral change to existing tests; `no_splash` defaults preserve current construction).

- [ ] **Step 4: Manual verification in a real terminal**

Run: `./start.sh --no-tui=false` in a ≥100-col terminal (or just `./start.sh`). Confirm:
- The Atlas hero fills the content region under the title and above the footer.
- It holds ~3s, then pixel-dissolves to reveal the wizard.
- A key press or mouse click during the hold/dissolve skips immediately.
- `./start.sh --no-splash` (added in Task 6) shows the wizard with no splash.
Capture a screenshot for the change record.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/ui/textual/screens/wizard_screen.py
git commit -m "feat(tui): mount Atlas splash over the wizard content region"
```

---

### 1.6. Task 6: `--no-splash` flag plumbing

**Files:**
- Modify: `bootstrapper/start.py` (Click option + `main` signature + flow calls)
- Modify: `bootstrapper/ui/textual/integration.py` (`run_setup_flow`, `run_launch_flow`, `_SetupApp`/`_LaunchApp` → `WizardScreen`)
- Test: `bootstrapper/tests/test_no_splash_flag.py`

**Interfaces:**
- Produces: `run_setup_flow(..., no_splash: bool = False)` and `run_launch_flow(..., no_splash: bool = False)`, each forwarding `no_splash` into `WizardScreen(...)`.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_no_splash_flag.py`:

```python
import inspect

from ui.textual import integration
import start as start_mod


def test_flows_accept_no_splash():
    assert "no_splash" in inspect.signature(integration.run_setup_flow).parameters
    assert "no_splash" in inspect.signature(integration.run_launch_flow).parameters


def test_cli_declares_no_splash():
    names = {p.name for p in start_mod.main.params}  # Click params
    assert "no_splash" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_no_splash_flag.py -v`
Expected: FAIL — parameter / option absent.

- [ ] **Step 3: Add the Click option and thread it through**

In `bootstrapper/start.py`, after the `--no-tui` option (~line 1870) add:

```python
@click.option('--no-splash', is_flag=True, default=False,
              help='Disable the opening splash animation in the wizard.')
```

Add `no_splash` to the `main(...)` signature, and pass `no_splash=no_splash` into both the `run_setup_flow(...)` and `run_launch_flow(...)` call sites.

In `bootstrapper/ui/textual/integration.py`:
- Add `no_splash: bool = False` to `run_setup_flow` (line 840) and `run_launch_flow` (line 954) signatures.
- In `_SetupApp.on_mount` (~line 928) and `_LaunchApp.on_mount` (~line 1087), pass `no_splash=no_splash` into the `WizardScreen(...)` construction (capture `no_splash` via closure as the other flow args already are).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_no_splash_flag.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/start.py bootstrapper/ui/textual/integration.py bootstrapper/tests/test_no_splash_flag.py
git commit -m "feat(cli): add --no-splash flag threaded to the wizard"
```

---

### 1.7. Task 7: Linear `--no-tui` hero print

**Files:**
- Modify: `bootstrapper/utils/banner.py` (`BannerDisplay`)
- Modify: `bootstrapper/start.py` (call site, ~line 2433)
- Test: `bootstrapper/tests/test_banner_hero.py`

**Interfaces:**
- Consumes: `load_hero`, `hero_rows` (Task 2).
- Produces: `BannerDisplay.show_hero(self, no_splash: bool = False) -> bool` — prints the nearest-width hero centered; returns `True` if printed, `False` when `no_splash`, terminal `<80` cols, or no grid available.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_banner_hero.py`:

```python
from utils.banner import BannerDisplay


def test_hero_suppressed_when_no_splash(monkeypatch):
    monkeypatch.setattr(BannerDisplay, "get_terminal_width", lambda self: 120)
    assert BannerDisplay().show_hero(no_splash=True) is False


def test_hero_suppressed_when_too_narrow(monkeypatch):
    monkeypatch.setattr(BannerDisplay, "get_terminal_width", lambda self: 70)
    assert BannerDisplay().show_hero(no_splash=False) is False


def test_hero_prints_when_wide(monkeypatch):
    monkeypatch.setattr(BannerDisplay, "get_terminal_width", lambda self: 120)
    assert BannerDisplay().show_hero(no_splash=False) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_banner_hero.py -v`
Expected: FAIL — `show_hero` missing.

- [ ] **Step 3: Implement `show_hero`**

In `bootstrapper/utils/banner.py`, add to `BannerDisplay`:

```python
def show_hero(self, no_splash: bool = False) -> bool:
    """Print the pre-rendered Atlas hero (linear/--no-tui path). Returns
    whether anything was printed."""
    if no_splash:
        return False
    width = self.get_terminal_width()
    if width < 80:
        return False
    from ui.textual.widgets.atlas_hero import load_hero, hero_rows
    from rich.align import Align
    data = load_hero(width)
    if not data:
        return False
    self.console.print()
    for t in hero_rows(data):
        self.console.print(Align.center(t))
    self.console.print()
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_banner_hero.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Wire it into the linear flow**

In `bootstrapper/start.py`, just before the existing `self.banner.show_banner()` call (~line 2433 inside `show_banner`), call the hero first when not suppressed. Add a `no_splash` attribute on the starter (set from the CLI flag in Task 6) and:

```python
def show_banner(self):
    """Display the startup banner (hero + wordmark/credits)."""
    self.banner.show_hero(no_splash=getattr(self, "no_splash", False))
    self.banner.show_banner()
```

- [ ] **Step 6: Run the full suite**

Run: `cd bootstrapper && uv run pytest -q`
Expected: PASS, including `test_banner_block_logo_parity.py` (the wordmark/title is untouched).

- [ ] **Step 7: Commit**

```bash
git add bootstrapper/utils/banner.py bootstrapper/start.py bootstrapper/tests/test_banner_hero.py
git commit -m "feat(cli): print Atlas hero in the linear --no-tui flow"
```

---

### 1.8. Task 8: Document the flag and the regeneration step

**Files:**
- Modify: `README.md` (or the start-options doc that lists CLI flags) — add `--no-splash` and `ATLAS_NO_SPLASH`.
- Modify: `CLAUDE.md` is gitignored — do NOT edit it for shared docs. Instead document the generator in `bootstrapper/scripts/generate_logo.py`'s module docstring (already present) and add a short note to the repo's contributor docs if one lists generated artifacts.

- [ ] **Step 1: Add flag docs**

In `README.md` near the other `./start.sh` flags, add:

```markdown
- `./start.sh --no-splash` — skip the opening splash animation (also: set `ATLAS_NO_SPLASH=1`).
```

- [ ] **Step 2: Add a maintainer regeneration note**

In the contributor/services doc that enumerates generated artifacts (e.g. `docs/CONTRIBUTING-services.md` or the docs that mention `docs.regen`), add one line:

```markdown
- Atlas hero art: regenerate with `python bootstrapper/scripts/generate_logo.py` (needs `pip install pillow` + `chafa`); commit the refreshed `bootstrapper/ui/textual/assets/atlas_hero_*.json`. Not gated in CI.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/CONTRIBUTING-services.md
git commit -m "docs: document --no-splash and Atlas hero regeneration"
```

---

## 2. Self-Review

**Spec coverage (Phase A scope of the spec):**
- §2 locked params → Task 1 generator constants. [complete]
- §4 rendering pipeline (generator, committed cell-grids, breakpoints, Textual rendering) → Tasks 1, 2. [complete]
- §5.1–5.3 splash layout, pixel dissolve, ~3s hold, skip, every-launch, reduced-motion/`--no-splash` → Tasks 3, 4, 5, 6. [complete]
- §5.4 `--no-tui` linear hero → Task 7. [complete]
- §7 no CI chafa gate; non-chafa loader/renderer/splash tests → Tasks 1–4, 6, 7 tests; constraint stated in Global Constraints. [complete]
- App hero has no wordmark → Tasks 2/4 render only the image grid (no wordmark anywhere). [complete]
- Phase B (GitHub banner/social/avatar/README About) → intentionally out of scope; separate plan.

**Placeholder scan:** No "TBD/TODO/handle edge cases" left. Two items are flagged as "confirm in Step 4 / wrap if it raises" — these are real verification instructions with a concrete fallback, not placeholders.

**Type consistency:** `load_hero(width) -> dict|None` and `hero_rows(data) -> list[Text]` used identically in Tasks 2, 4, 7. `dissolve_order`/`dissolved_count`/`should_show_splash` signatures defined in Task 3 and consumed unchanged in Task 4. `no_splash` is a `bool` throughout (CLI → flows → `WizardScreen` → logic; and `BannerDisplay.show_hero`).

**Known follow-ups (resolved during execution, not blockers):** exact splash overlay dock/height CSS (Task 5 Step 4 manual tune); animation frame count/interval constants (Task 4, tune in terminal); whether `remove()` needs a guard outside a running app (Task 4 Step 5 note).
