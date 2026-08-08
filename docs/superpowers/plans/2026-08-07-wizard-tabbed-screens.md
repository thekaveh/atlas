# Tabbed Wizard/Launch Screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the Atlas wizard into two tabs — Setup and Logs — sharing one screen, so the log pane gets a usable share of the terminal instead of being crushed by the 61-service stack overview.

**Architecture:** One `WizardScreen` keeps all state. `compose()` wraps today's children in two sibling containers (`#tab-setup`, `#tab-logs`); switching toggles their `display` so both stay mounted and keep updating while hidden. Tabs render on the `BrandPanel`'s bottom border alongside the existing byline, costing zero rows.

**Tech Stack:** Python 3.10+, Textual 6.2.1, pytest (headless `App.run_test()`), `uv` for running.

## Global Constraints

- Textual is pinned `textual>=0.85`; installed version is **6.2.1**. Do not add or bump dependencies.
- Layout only. Do not change the pipeline, compose streaming, service resolution, or any wizard-step logic.
- Bare `[` in a `border_subtitle` is consumed as console markup. Tab labels **must** escape it as `\[`.
- `Widget.allow_select` is `ALLOW_SELECT and not is_container`. `LogPane` subclasses `RichLog` (a scrolling container), so it can never drag-select. Do not attempt to make it selectable by subclassing.
- Never launch Atlas, run `./start.sh`, or touch the GPU. All verification is `pytest` + headless `run_test()`.
- Run tests from `bootstrapper/`: `cd bootstrapper && uv run pytest ...`
- Existing screen bindings are all `priority=True`; new bare-letter bindings must be added to the `check_action` whitelist logic so they do not hijack the search input.
- Preserve all existing output, colors, borders, and panel titles. This is the repo's editing rule.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `bootstrapper/ui/textual/widgets/block_logo.py` | `BrandPanel` — owns the logo box and its border content, now including tabs | Modify: add `set_tabs()` + subtitle composition |
| `bootstrapper/ui/textual/screens/wizard_screen.py` | Screen — owns tab state, layout, bindings, footer swap | Modify: `compose()`, `_transition_to_launch()`, BINDINGS, actions |
| `bootstrapper/ui/textual/widgets/log_pane.py` | `LogPane` — add copy helpers over its existing `_records` | Modify: add `visible_text()` |
| `bootstrapper/tests/test_brand_panel_tabs.py` | Border/tab rendering contract | Create |
| `bootstrapper/tests/test_wizard_tabs.py` | Tab switching, gating, footer, streaming-while-hidden, budget | Create |
| `bootstrapper/tests/test_log_copy.py` | Copy shortcuts | Create |
| `docs/CHANGELOG.md` | User-visible record | Modify |
| `services/comfyui/README.md` | *(not touched — layout change only)* | — |

---

### Task 1: BrandPanel renders tabs on its bottom border

**Files:**
- Modify: `bootstrapper/ui/textual/widgets/block_logo.py:107-173`
- Test: `bootstrapper/tests/test_brand_panel_tabs.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `BrandPanel.set_tabs(active: str, enabled: bool) -> None` where `active` is `"setup"` or `"logs"`; `BrandPanel.TAB_SETUP = "setup"`; `BrandPanel.TAB_LOGS = "logs"`; `BrandPanel.tab_spans() -> dict[str, tuple[int, int]]` returning `{tab_id: (start_col, end_col)}` half-open column ranges for click mapping.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_brand_panel_tabs.py`:

```python
"""BrandPanel renders Setup/Logs tabs on its bottom border beside the byline.

The border is the only chrome that can carry tabs without spending a row, which
matters because the 61-service stack overview already over-subscribes a 44-row
terminal. Bare "[" is console markup in Textual, so labels must be escaped.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from textual.app import App, ComposeResult  # noqa: E402

from ui.textual.widgets.block_logo import BrandPanel  # noqa: E402


def _panel(**kw) -> BrandPanel:
    return BrandPanel(
        tagline="A self-hosted platform",
        author="Kaveh Razavi",
        license="Apache License 2.0",
        version="0.1.0",
        repo="github.com/thekaveh/atlas",
        **kw,
    )


class _App(App):
    def __init__(self, panel: BrandPanel) -> None:
        super().__init__()
        self._panel = panel

    def compose(self) -> ComposeResult:
        yield self._panel


def _bottom_border(app: App) -> str:
    strips = list(app.screen._compositor.render_strips(app.screen.size))
    panel = app.query_one(BrandPanel)
    return strips[panel.region.y + panel.region.height - 1].text


def test_tabs_and_byline_share_the_bottom_border():
    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            return _bottom_border(pilot.app)

    row = asyncio.run(scenario())

    assert "Setup" in row, "tab label missing (bare '[' eaten as markup?)"
    assert "Logs" in row
    assert "Kaveh Razavi" in row, "byline must stay on the same border"
    assert row.index("Setup") < row.index("Kaveh Razavi"), "tabs left, byline right"


def test_tab_labels_survive_a_narrow_terminal():
    """The byline elides under pressure; tab labels never truncate."""
    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(70, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_LOGS, enabled=True)
            await pilot.pause()
            return _bottom_border(pilot.app)

    row = asyncio.run(scenario())

    assert "Setup" in row
    assert "Logs" in row


def test_tab_spans_map_columns_for_click_routing():
    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            return panel.tab_spans(), _bottom_border(pilot.app)

    spans, row = asyncio.run(scenario())

    assert set(spans) == {BrandPanel.TAB_SETUP, BrandPanel.TAB_LOGS}
    # Boundary-EXACT for BOTH tabs. A containment check like
    # `"Setup" in row[start:end + 2]` passes even for a span that is off by a
    # column or two, which is exactly the escape-drift bug this guards.
    for tab_id, label in ((BrandPanel.TAB_SETUP, "Setup"), (BrandPanel.TAB_LOGS, "Logs")):
        start, end = spans[tab_id]
        assert row[start] == "[", f"{tab_id} span must start at the rendered '['"
        assert row[end - 1] == "]", f"{tab_id} span must end at the rendered ']'"
        assert label in row[start:end]


def test_byline_renders_alone_before_set_tabs_called():
    """Until set_tabs() is called the panel must look EXACTLY as it did before
    tabs existed — the live wizard hits this path on every run. Asserting only
    "labels absent" is not enough: a left-aligned padded string renders a stray
    space after the corner (``╰─ ───``) while still containing no labels."""
    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()          # no set_tabs() call
            return _bottom_border(pilot.app)

    row = asyncio.run(scenario())

    assert "Setup" not in row and "Logs" not in row
    assert row[2] != " ", f"stray gap artifact after the corner: {row[:10]!r}"
    assert "…" not in row, "byline must not be ellipsized at 140 cols"
    assert "github.com/thekaveh/atlas" in row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_brand_panel_tabs.py -v`
Expected: FAIL with `AttributeError: 'BrandPanel' object has no attribute 'set_tabs'`

- [ ] **Step 3: Write minimal implementation**

In `block_logo.py`, set the `BrandPanel` CSS `border-subtitle-align` to `left` (the tabs path needs it; `_render_border` flips it back to `right` at runtime for the byline-only path), then add the tab machinery. Replace the class body's `__init__`/`on_mount` region with:

```python
    TAB_SETUP = "setup"
    TAB_LOGS = "logs"
    _TAB_LABELS = ((TAB_SETUP, "Setup"), (TAB_LOGS, "Logs"))

    def __init__(
        self,
        *,
        tagline: str = "Self-hosted Engineering Platform",
        author: str = "",
        author_email: str = "",
        license: str = "",  # noqa: A002 - matches BrandInfo field name
        version: str = "",
        repo: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.tagline = tagline
        self.author = author
        self.author_email = author_email
        self.license = license
        self.version = version
        self.repo = repo
        self._active_tab = self.TAB_SETUP
        self._tabs_enabled = False
        self._tab_spans: dict[str, tuple[int, int]] = {}

    def compose(self) -> ComposeResult:
        yield BlockLogo()

    def _byline(self) -> str:
        parts: list[str] = []
        if self.author:
            who = f"by {self.author}"
            if self.author_email:
                who = f"{who} <{self.author_email}>"
            parts.append(who)
        if self.license:
            parts.append(self.license)
        if self.version:
            v = self.version if self.version.startswith("v") else f"v{self.version}"
            parts.append(v)
        if self.repo:
            parts.append(self.repo)
        return " " + "  ·  ".join(parts) + " " if parts else ""

    def _tab_segment(self) -> str:
        """Left-hand tab labels. Brackets are ESCAPED: Textual consumes a bare
        "[" in a border subtitle as console markup and the label vanishes.

        Returns "" when tabs are not enabled, so the panel keeps its original
        byline-only appearance until ``set_tabs()`` is called.

        SPAN MATH: measure positions in RENDERED characters, never in the raw
        escaped string. Each ``\\[`` occupies 2 source chars but renders as 1,
        so indexing the source drifts by one column per preceding tab.
        """
        if not self._tabs_enabled:
            self._tab_spans = {}
            return ""

        out = " "
        self._tab_spans = {}
        rendered_pos = len(out)
        for tab_id, label in self._TAB_LABELS:
            marker = "▸" if tab_id == self._active_tab else " "
            bracket_content_len = len(f"[{marker} {label} ]")
            self._tab_spans[tab_id] = (
                rendered_pos, rendered_pos + bracket_content_len,
            )
            out += rf"\[{marker} {label} ] "
            rendered_pos += bracket_content_len + 1
        return out

    def tab_spans(self) -> dict[str, tuple[int, int]]:
        """Half-open column ranges of each tab label, for click routing."""
        return dict(self._tab_spans)

    def set_tabs(self, active: str, *, enabled: bool = True) -> None:
        self._active_tab = active
        self._tabs_enabled = enabled
        self._render_border()

    def _render_border(self) -> None:
        """Two branches — do NOT collapse them into one padded string.

        Textual inserts an implicit gap between the corner and the subtitle, so
        a left-aligned string that STARTS with dashes renders as ``╰─ ───`` (a
        stray space). The byline-only path therefore restores the original
        right-aligned plain byline and lets Textual draw its own dash fill,
        which is byte-identical to the pre-tabs rendering.
        """
        if self.tagline:
            self.border_title = f" {self.tagline} "

        if not self._tabs_enabled:
            self.styles.border_subtitle_align = "right"
            self.border_subtitle = self._byline()
            return

        self.styles.border_subtitle_align = "left"
        left = self._tab_segment()
        right = self._byline()
        # width - 4 = two corners + Textual's implicit gap either side of the
        # subtitle. Using width - 2 overruns the budget and Textual ellipsizes.
        inner = max(0, self.size.width - 4)
        pad = max(1, inner - len(left) - len(right))
        self.border_subtitle = left + "─" * pad + right

        # Shift spans into panel-region columns. 3 = corner (╰) + line (─) +
        # implicit gap. Assumes `border: round`, `padding: 0`,
        # `border-subtitle-align: left`; the boundary-exact span tests fail
        # loudly if that CSS changes.
        if self._tab_spans:
            self._tab_spans = {
                tab_id: (start + 3, end + 3)
                for tab_id, (start, end) in self._tab_spans.items()
            }

    def on_mount(self) -> None:
        self._render_border()

    def on_resize(self) -> None:
        self._render_border()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_brand_panel_tabs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Verify nothing else regressed**

Run: `cd bootstrapper && uv run pytest -k "brand or logo or wizard or textual" -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/ui/textual/widgets/block_logo.py bootstrapper/tests/test_brand_panel_tabs.py
git commit -m "feat(tui): render Setup/Logs tabs on the brand panel border"
```

---

### Task 2: Split the screen body into two tab containers

**Files:**
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py` — `DEFAULT_CSS` (~line 429), `compose()` (~line 630), `_transition_to_launch()` (~line 1421)
- Test: `bootstrapper/tests/test_wizard_tabs.py`

**Interfaces:**
- Consumes: `BrandPanel.set_tabs`, `BrandPanel.TAB_SETUP`, `BrandPanel.TAB_LOGS` from Task 1.
- Produces: `WizardScreen.show_tab(tab_id: str) -> None`; `WizardScreen.active_tab -> str`; container ids `#tab-setup` and `#tab-logs`.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_wizard_tabs.py`:

```python
"""Setup and Logs live in sibling containers; switching toggles display only.

Both bodies stay mounted so the stack overview keeps updating while the user
reads logs, and the log stream keeps appending while the user is on Setup.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from textual.app import App, ComposeResult  # noqa: E402

from ui.textual.screens.wizard_screen import WizardScreen  # noqa: E402
from ui.textual.widgets.block_logo import BrandPanel  # noqa: E402


class _App(App):
    def __init__(self, screen: WizardScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _screen() -> WizardScreen:
    return WizardScreen(steps=[], services=[], no_splash=True)


def test_both_tab_bodies_are_mounted_and_only_one_is_visible():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            setup = scr.query_one("#tab-setup")
            logs = scr.query_one("#tab-logs")
            return setup.display, logs.display, scr.active_tab

    setup_visible, logs_visible, active = asyncio.run(scenario())

    assert setup_visible is True
    assert logs_visible is False, "Logs body stays mounted but hidden"
    assert active == BrandPanel.TAB_SETUP


def test_show_tab_swaps_visibility_without_unmounting():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            setup = scr.query_one("#tab-setup")
            logs = scr.query_one("#tab-logs")
            return setup.display, logs.display, scr.active_tab, setup.is_mounted

    setup_visible, logs_visible, active, still_mounted = asyncio.run(scenario())

    assert setup_visible is False
    assert logs_visible is True
    assert active == BrandPanel.TAB_LOGS
    assert still_mounted is True, "hidden body must NOT be unmounted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_wizard_tabs.py -v`
Expected: FAIL — `NoMatches` for `#tab-setup` (the container does not exist yet)

- [ ] **Step 3: Write minimal implementation**

3a. In `wizard_screen.py` `DEFAULT_CSS`, replace the `#lower-pane` rules with tab-container rules:

```css
    WizardScreen #tab-setup { width: 100%; height: 1fr; }
    WizardScreen #tab-logs  { width: 100%; height: 1fr; }
    WizardScreen #info-section { width: 100%; height: auto; margin-top: 1; }
    WizardScreen #lower-pane {
        width: 100%;
        height: 1fr;
        margin-top: 1;
        overflow: hidden;
    }
    WizardScreen #lower-pane > PromptPanel { height: 1fr; }
    WizardScreen > #wizard-body > FooterBar { margin-top: 1; }
```

3b. Rewrite `compose()` to nest the existing children under the two tab bodies. The Logs body is created up front (hidden) instead of at transition time:

```python
    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-body"):
            yield self._brand_panel
            with Vertical(id="tab-setup"):
                with Vertical(id="info-section"):
                    yield self._info_panel
                with Vertical(id="lower-pane"):
                    yield self._prompt
                    yield self._command_summary
            with Vertical(id="tab-logs"):
                yield self._log_chips
                yield self._log_pane
            yield self._footer
```

3c. In `__init__`, build the brand panel and the log widgets eagerly (they were previously constructed inline in `compose()` / at transition):

```python
        self._brand_panel = BrandPanel(
            tagline=self._brand.tagline or "Self-hosted Engineering Platform",
            author=self._brand.creator,
            author_email=self._brand.creator_email,
            license=self._brand.license,
            version=self._brand.version,
            repo=self._brand.repo,
        )
        self._log_chips = LogFilterChips(on_change=self._on_log_filter_change)
        self._log_pane = LogPane(
            title=" Stack startup · pipeline ",
            subtitle=" ctrl+c to cancel ",
        )
        self._log_pane.set_on_new_source(self._log_chips.add_source)
        self._active_tab = BrandPanel.TAB_SETUP
        self._logs_enabled = False
```

3d. Add the tab API to `WizardScreen`:

```python
    @property
    def active_tab(self) -> str:
        return self._active_tab

    def show_tab(self, tab_id: str) -> None:
        """Toggle body visibility. Both bodies stay mounted so the overview and
        the log stream keep updating while hidden."""
        if tab_id == BrandPanel.TAB_LOGS and not self._logs_enabled:
            return
        self._active_tab = tab_id
        self.query_one("#tab-setup").display = tab_id == BrandPanel.TAB_SETUP
        self.query_one("#tab-logs").display = tab_id == BrandPanel.TAB_LOGS
        self._brand_panel.set_tabs(tab_id, enabled=self._logs_enabled)
        self._footer.update_hints(
            _STARTUP_HINTS if tab_id == BrandPanel.TAB_LOGS else _SETUP_HINTS
        )
```

3e. In `on_mount`, set the initial state:

```python
        self.query_one("#tab-logs").display = False
        self._brand_panel.set_tabs(BrandPanel.TAB_SETUP, enabled=False)
```

3f. In `_transition_to_launch()`, delete the destructive teardown and reveal the tab instead. Replace:

```python
        lower = self.query_one("#lower-pane", Vertical)
        await lower.remove_children()

        self._log_chips = LogFilterChips(on_change=self._on_log_filter_change)
        self._log_pane = LogPane(
            title=" Stack startup · pipeline ",
            subtitle=" ctrl+c to cancel ",
        )
        self._log_pane.set_on_new_source(self._log_chips.add_source)
        await lower.mount(self._log_chips)
        await lower.mount(self._log_pane)

        self._footer.update_hints(_STARTUP_HINTS)
```

with:

```python
        # Log widgets already exist (built in __init__, hidden). Reveal the tab
        # instead of tearing the setup body down — the overview must stay live.
        self._logs_enabled = True
        self.show_tab(BrandPanel.TAB_LOGS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_wizard_tabs.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the whole TUI suite for regressions**

Run: `cd bootstrapper && uv run pytest -k "wizard or textual or tui or launch" -q`
Expected: PASS. If `test_tui_launch_exit_code.py` fails on a missing `#lower-pane`, it is asserting the old layout — update that assertion to query `#tab-logs`.

- [ ] **Step 6: Commit**

```bash
git add bootstrapper/ui/textual/screens/wizard_screen.py bootstrapper/tests/test_wizard_tabs.py
git commit -m "feat(tui): split wizard body into Setup/Logs tab containers"
```

---

### Task 3: Tab switching by keyboard and mouse

**Files:**
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py` — `BINDINGS` (~line 408), `check_action()` (~line 908), plus new actions
- Test: `bootstrapper/tests/test_wizard_tabs.py` (extend)

**Interfaces:**
- Consumes: `WizardScreen.show_tab`, `WizardScreen.active_tab` (Task 2); `BrandPanel.tab_spans()` (Task 1).
- Produces: `action_show_setup()`, `action_show_logs()`, `action_cycle_tab(delta: int)`.

- [ ] **Step 1: Write the failing test**

Append to `bootstrapper/tests/test_wizard_tabs.py`:

```python
def test_number_keys_switch_tabs_once_logs_are_enabled():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            # Logs are gated before launch: "2" must be a no-op.
            await pilot.press("2")
            await pilot.pause()
            before = scr.active_tab
            scr._logs_enabled = True
            await pilot.press("2")
            await pilot.pause()
            after = scr.active_tab
            await pilot.press("1")
            await pilot.pause()
            return before, after, scr.active_tab

    before, after, back = asyncio.run(scenario())

    assert before == BrandPanel.TAB_SETUP, "Logs must be gated before launch"
    assert after == BrandPanel.TAB_LOGS
    assert back == BrandPanel.TAB_SETUP, "Setup stays reachable after launch"


def test_clicking_a_tab_label_on_the_border_switches_tabs():
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._brand_panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            spans = scr._brand_panel.tab_spans()
            start, end = spans[BrandPanel.TAB_LOGS]
            panel = scr._brand_panel
            y = panel.region.height - 1          # bottom border row
            await pilot.click(BrandPanel, offset=((start + end) // 2, y))
            await pilot.pause()
            return scr.active_tab

    assert asyncio.run(scenario()) == BrandPanel.TAB_LOGS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_wizard_tabs.py -v -k "number_keys or clicking"`
Expected: FAIL — pressing `2` does not change `active_tab` (no binding yet)

- [ ] **Step 3: Write minimal implementation**

3a. Add to `BINDINGS` (keep `priority=True` to match the surrounding style):

```python
        Binding("1", "show_setup", "Setup tab", show=False, priority=True),
        Binding("2", "show_logs", "Logs tab", show=False, priority=True),
        Binding("shift+tab", "cycle_tab(-1)", "Prev tab", show=False, priority=True),
```

3b. Add the actions to `WizardScreen`:

```python
    def action_show_setup(self) -> None:
        self.show_tab(BrandPanel.TAB_SETUP)

    def action_show_logs(self) -> None:
        self.show_tab(BrandPanel.TAB_LOGS)

    def action_cycle_tab(self, delta: int) -> None:
        if not self._logs_enabled:
            return
        order = [BrandPanel.TAB_SETUP, BrandPanel.TAB_LOGS]
        idx = (order.index(self._active_tab) + delta) % len(order)
        self.show_tab(order[idx])
```

3c. Route border clicks. Add to `WizardScreen`:

```python
    def on_click(self, event) -> None:
        """A click on the brand panel's bottom border row selects a tab."""
        panel = self._brand_panel
        if event.widget is not panel:
            return
        if event.y != panel.region.height - 1:
            return
        for tab_id, (start, end) in panel.tab_spans().items():
            if start <= event.x <= end:
                self.show_tab(tab_id)
                return
```

3d. Keep the new bare-digit keys out of the search box. In `check_action`, add `"show_setup"`, `"show_logs"`, and `"cycle_tab"` to the whitelist of actions that stay enabled while the search input has focus — digits are not text the model-name search needs. If the existing whitelist is a tuple/set literal, extend it in place and add a one-line comment noting tabs stay reachable while searching.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_wizard_tabs.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/ui/textual/screens/wizard_screen.py bootstrapper/tests/test_wizard_tabs.py
git commit -m "feat(tui): switch tabs with 1/2, shift+tab, and border clicks"
```

---

### Task 4: Hidden bodies keep streaming, and the budget is reclaimed

**Files:**
- Test: `bootstrapper/tests/test_wizard_tabs.py` (extend)

**Interfaces:**
- Consumes: `WizardScreen.show_tab` (Task 2); `LogPane.write_log(text, *, level, source)` (existing).
- Produces: nothing — this task is the regression guard for the bug that motivated the work.

- [ ] **Step 1: Write the failing test**

Append to `bootstrapper/tests/test_wizard_tabs.py`:

```python
def test_logs_written_while_setup_is_active_are_not_lost():
    """The Logs body stays mounted, so a failure during launch is still in the
    pane when the user switches over."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr._log_pane.write_log(
                "supabase-db ready", level="info", source="supabase-db"
            )
            scr._log_pane.write_log(
                "comfyui failed", level="error", source="comfyui"
            )
            await pilot.pause()
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            return [r.raw for r in scr._log_pane._records]

    lines = asyncio.run(scenario())

    assert any("supabase-db ready" in t for t in lines)
    assert any("comfyui failed" in t for t in lines)


def test_logs_tab_reclaims_vertical_space():
    """The bug: 61 services + fixed chrome over-subscribed a 44-row terminal by
    6 rows and crushed the log pane. On the Logs tab the overview is hidden, so
    the pane must get a usable share back."""
    scr = _screen()

    async def scenario():
        async with _App(scr).run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            scr._logs_enabled = True
            scr.show_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            return scr._log_pane.region.height

    assert asyncio.run(scenario()) >= 20
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `cd bootstrapper && uv run pytest tests/test_wizard_tabs.py -v -k "not_lost or reclaims"`
Expected: PASS if Tasks 2-3 are correct. If `reclaims` FAILS, the fixed chrome still over-subscribes — check that `#tab-setup` is actually `display: none` on the Logs tab (a visible-but-empty container still claims `1fr`).

- [ ] **Step 3: Commit**

```bash
git add bootstrapper/tests/test_wizard_tabs.py
git commit -m "test(tui): guard log streaming while hidden and the log-pane budget"
```

---

### Task 5: Copy shortcuts for the log pane

**Files:**
- Modify: `bootstrapper/ui/textual/widgets/log_pane.py` (add `visible_text()`)
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py` (bindings + actions)
- Test: `bootstrapper/tests/test_log_copy.py`

**Interfaces:**
- Consumes: `WizardScreen._log_pane` (Task 2); `WizardScreen._launch_log_path` (existing, may be `None`); `LogPane._records` — a list of `_LogRecord` whose plain-text field is **`raw`** (not `.text`).
- Produces: `LogPane.visible_text() -> str`; `WizardScreen.action_copy_logs()`; `WizardScreen.action_copy_session_log()`.

- [ ] **Step 1: Write the failing test**

Create `bootstrapper/tests/test_log_copy.py`:

```python
"""Copy affordances for the log pane.

LogPane subclasses RichLog, a scrolling container, and Textual's rule is
`allow_select = ALLOW_SELECT and not is_container` — so it can never
drag-select. Users get terminal-native Shift-drag plus these explicit copy
actions; the command summary and stack overview are already selectable via
their inner Static widgets.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from textual.app import App, ComposeResult  # noqa: E402

from ui.textual.widgets.log_pane import LogPane  # noqa: E402


class _App(App):
    def compose(self) -> ComposeResult:
        yield LogPane(title=" logs ")


def test_visible_text_joins_the_buffered_records():
    async def scenario():
        async with _App().run_test(size=(100, 20)) as pilot:
            pane = pilot.app.query_one(LogPane)
            pane.write_log("supabase-db ready", level="info", source="supabase-db")
            pane.write_log("kong started", level="info", source="kong")
            await pilot.pause()
            return pane.visible_text()

    text = asyncio.run(scenario())

    assert "supabase-db ready" in text
    assert "kong started" in text
    assert text.count("\n") >= 1, "records must be newline-joined"


def test_log_pane_is_not_drag_selectable_by_design():
    """Documents the constraint so nobody 'fixes' it by subclassing."""
    async def scenario():
        async with _App().run_test(size=(100, 20)) as pilot:
            return pilot.app.query_one(LogPane).allow_select

    assert asyncio.run(scenario()) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bootstrapper && uv run pytest tests/test_log_copy.py -v`
Expected: FAIL with `AttributeError: 'LogPane' object has no attribute 'visible_text'`

- [ ] **Step 3: Write minimal implementation**

3a. Add to `LogPane` in `log_pane.py`:

```python
    def visible_text(self) -> str:
        """Plain text of the buffered records, for clipboard copy.

        RichLog is a scrolling container so Textual refuses to drag-select it;
        this is the explicit copy path instead."""
        return "\n".join(rec.raw for rec in self._records)
```

3b. Add bindings to `WizardScreen.BINDINGS`:

```python
        Binding("y", "copy_logs", "Copy logs", show=False, priority=True),
        Binding("Y", "copy_session_log", "Copy session log", show=False, priority=True),
```

3c. Add the actions to `WizardScreen`:

```python
    def action_copy_logs(self) -> None:
        if self._log_pane is None:
            return
        text = self._log_pane.visible_text()
        if not text:
            return
        self.app.copy_to_clipboard(text)
        self.notify("Log buffer copied to clipboard.", timeout=3)

    def action_copy_session_log(self) -> None:
        path = self._launch_log_path
        if path is None:
            self.notify("No session log yet.", severity="warning", timeout=3)
            return
        try:
            self.app.copy_to_clipboard(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            self.notify(
                f"Could not read the session log: {type(exc).__name__}",
                severity="error", timeout=5,
            )
            return
        self.notify(f"Session log copied ({path}).", timeout=3)
```

3d. Add `"copy_logs"` and `"copy_session_log"` to the `check_action` NON-whitelist — that is, leave them OUT of the whitelist so a literal `y` typed into the model-search box stays text. Add a one-line comment saying so.

3e. Surface the affordance: add `(("y",), "copy logs")` to `_STARTUP_HINTS` and `_LAUNCH_HINTS`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bootstrapper && uv run pytest tests/test_log_copy.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add bootstrapper/ui/textual/widgets/log_pane.py bootstrapper/ui/textual/screens/wizard_screen.py bootstrapper/tests/test_log_copy.py
git commit -m "feat(tui): add y/Y copy shortcuts for the log pane"
```

---

### Task 6: Full validation and CHANGELOG

**Files:**
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: nothing.

- [ ] **Step 1: Run the full bootstrapper suite**

Run: `cd bootstrapper && uv run pytest -q`
Expected: PASS. Baseline before this work was 3418 passed / 10 skipped; expect roughly +11 from the new tests. Any failure here is a real regression — fix it before continuing.

- [ ] **Step 2: Run the docs and audit gates**

```bash
cd /Users/kaveh/repos/atlas
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
make docs-check
```

Expected: both exit 0. These are required CI checks.

- [ ] **Step 3: Add the CHANGELOG entry**

Append a new numbered subsection at the end of the `## 1. [Unreleased]` block in `docs/CHANGELOG.md`, immediately before `## 2. [3.0.0]`, continuing the existing numbering:

```markdown
### 1.154. Changed — 2026-08-07 — Wizard splits into Setup and Logs tabs

- **The launch logs get their own tab** — Atlas now ships 61 source-configurable services, and the stack overview grew with them: on a 44-row terminal the fixed chrome over-subscribed the screen by 6 rows, squeezing the log pane to nothing exactly when it matters. The wizard now has two tabs. Setup keeps the stack overview, the step prompt, and the command summary; Logs shows the filter chips and the log pane. The logo pane and the shortcuts bar stay on both, and the shortcuts contents swap per tab. On a 44-row terminal the log pane goes from effectively zero rows to 20+.
- **Tabs cost no vertical space** — they render on the logo pane's bottom border, to the left of the existing author/license/version/repo byline, which keeps its place and elides on narrow terminals. Switch with `1`/`2`, `shift+tab`, or by clicking a tab label. Launch switches to Logs automatically; Setup stays reachable, and because both bodies stay mounted the stack overview keeps updating live while you read logs and the log stream keeps appending while you are on Setup.
- **Copying** — the command summary and stack overview are selectable with a normal drag. The log pane is a scrolling `RichLog`, which Textual will not drag-select, so it gains `y` (copy the log buffer) and `Y` (copy the full session log); terminal-native Shift-drag also works.
```

- [ ] **Step 4: Re-run the docs gate after editing the CHANGELOG**

Run: `cd /Users/kaveh/repos/atlas && make docs-check`
Expected: exit 0

- [ ] **Step 5: Commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs(changelog): record the wizard Setup/Logs tab split"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task |
|---|---|
| §4 Architecture (two bodies, both mounted) | Task 2 |
| §4.2 Tab affordance on the border | Task 1 |
| §5 Tab mechanics (keys, click, gating, footer swap) | Tasks 2, 3 |
| §6 State/data flow (streaming while hidden) | Task 4 |
| §7 Selectable/copyable content | Task 5 |
| §8 Error handling (gating, safe switching) | Task 3 (gating), Task 2 (`show_tab` touches no workers) |
| §9 Testing (all 7 items) | Tasks 1, 3, 4, 5 |
| §10 Migration path (5 steps) | Tasks 1-6 in the same order |

Two deliberate deviations from the spec, both recorded here:
- §5's "activity marker on the Logs label when errors arrive while on Setup" is **not** implemented. It needs a hook into the error path and would widen this layout-only change; the `y`/`Y` affordances and auto-switch-on-launch already cover the primary risk. Track separately.
- §8's "fix the pre-existing ≤30-row overlap" is not a discrete task because Task 2 replaces the `#lower-pane` sizing rules wholesale; Task 4's budget test is the guard. If the overlap survives at h≤30 after Task 2, file it separately rather than expanding scope here.

**2. Placeholder scan:** No TBD/TODO. Every code step carries real code. No "similar to Task N" references.

**3. Type consistency:** `set_tabs(active, *, enabled)`, `tab_spans() -> dict[str, tuple[int, int]]`, `show_tab(tab_id)`, `active_tab`, `visible_text() -> str`, and the `TAB_SETUP`/`TAB_LOGS` constants are used identically in every task that references them. Container ids `#tab-setup` / `#tab-logs` match between Task 2's implementation and Tasks 3-4's tests.
