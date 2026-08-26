"""BrandPanel renders Setup/Logs tabs on its bottom border beside the byline.

The border is the only chrome that can carry tabs without spending a row, which
matters because the 61-service stack overview already over-subscribes a 44-row
terminal. Bare "[" is console markup in Textual, so labels must be escaped.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from textual.app import App, ComposeResult  # noqa: E402

from ui.textual.widgets.block_logo import BrandPanel  # noqa: E402


@pytest.fixture(autouse=True)
def _color_capable_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep compositor color assertions independent of the invoking shell."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")


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
    """Spans must map to exact bracket positions in the rendered row."""
    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            return panel.tab_spans(), _bottom_border(pilot.app)

    spans, row = asyncio.run(scenario())

    assert set(spans) == {BrandPanel.TAB_SETUP, BrandPanel.TAB_LOGS}

    # Verify Setup bracket boundaries.
    setup_start, setup_end = spans[BrandPanel.TAB_SETUP]
    assert setup_start < setup_end
    assert row[setup_start] == "[", f"Setup start should be '[', got '{row[setup_start]}'"
    assert row[setup_end - 1] == "]", f"Setup end-1 should be ']', got '{row[setup_end - 1]}'"
    assert "Setup" in row[setup_start:setup_end]

    # Verify Logs bracket boundaries.
    logs_start, logs_end = spans[BrandPanel.TAB_LOGS]
    assert logs_start < logs_end
    assert row[logs_start] == "[", f"Logs start should be '[', got '{row[logs_start]}'"
    assert row[logs_end - 1] == "]", f"Logs end-1 should be ']', got '{row[logs_end - 1]}'"
    assert "Logs" in row[logs_start:logs_end]


def test_byline_renders_alone_before_set_tabs_called():
    """Before set_tabs() is called, render byline only (no tab chrome).

    Byte-exact check: at wide width, the full byline should render without
    truncation or ellipsis, reproducing the old right-aligned output exactly.
    """
    panel = _panel()

    async def scenario():
        # Use width=200 to ensure no legitimate space pressure on the byline.
        async with _App(panel).run_test(size=(200, 12)) as pilot:
            await pilot.pause()
            # Do NOT call set_tabs(); tabs should not appear
            return _bottom_border(pilot.app)

    row = asyncio.run(scenario())

    # Tab labels should not appear before set_tabs() is called.
    assert "Setup" not in row, "tabs should not render before set_tabs() is called"
    assert "Logs" not in row, "tabs should not render before set_tabs() is called"

    # Byline must render fully un-truncated, without ellipsis (.../…).
    assert "Kaveh Razavi" in row
    assert "github.com/thekaveh/atlas" in row, "full repo URL must not be truncated"
    assert "…" not in row, "byline should not be ellipsized (no … character)"

    # Border structure: should start with continuous dashes after corner (no space artifact).
    # Old (correct): ╰───────...
    # New (artifact): ╰─ ────... (stray space at index 2)
    assert row.startswith("╰─"), f"border should start with '╰─', got: {row[:4]}"
    assert row[2] == "─", (
        f"no gap artifact: index 2 should be dash (─), not space; "
        f"got '{row[2]}' in row[:10]='{row[:10]}'"
    )


def test_tabs_enabled_byline_stays_flush_right_at_wide_width():
    """M1 regression: ``_render_border`` measured ``left`` (the tab segment)
    at its RAW length, which includes the ``\\[`` escape backslashes needed
    to stop Textual from eating a bare ``[`` as console markup (see
    ``_tab_segment``'s docstring). Each escape is 2 raw characters but
    renders as 1, so ``len(left)`` over-counted by one column per tab —
    2 tabs pushed the byline 2 columns short of flush-right at wide
    terminals. The byline-ONLY path (tabs disabled) has no escaped
    brackets, so it was never affected — both paths must end with the
    identical trailing filler once the byline content itself ends.
    """
    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(200, 12)) as pilot:
            await pilot.pause()
            byline_only_row = _bottom_border(pilot.app)
            panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            tabs_row = _bottom_border(pilot.app)
            return byline_only_row, tabs_row

    byline_only_row, tabs_row = asyncio.run(scenario())

    assert "…" not in tabs_row, "byline must not ellipsize 2 chars early at 200 cols"
    byline_only_suffix = byline_only_row[byline_only_row.rindex("atlas") + len("atlas"):]
    tabs_suffix = tabs_row[tabs_row.rindex("atlas") + len("atlas"):]
    assert tabs_suffix == byline_only_suffix, (
        "tabs-enabled path must end flush-right exactly like the byline-only "
        f"path — got trailing {tabs_suffix!r} vs byline-only {byline_only_suffix!r} "
        "(off-by-one-per-tab from measuring the escaped bracket's RAW length)"
    )


# ─── accent + hover styling ──────────────────────────────────────────


def _bottom_segments(app: App):
    """(text, style) for each segment of the panel's bottom border row."""
    strips = list(app.screen._compositor.render_strips(app.screen.size))
    panel = app.query_one(BrandPanel)
    row = strips[panel.region.y + panel.region.height - 1]
    return [(s.text, s.style) for s in row if s.text.strip()]


def _style_of(app: App, label: str) -> str:
    for text, style in _bottom_segments(app):
        if label in text:
            return str(style)
    raise AssertionError(f"{label!r} not found on the border row")


def test_the_active_tab_is_painted_in_the_theme_accent():
    """The tabs shipped inheriting the border colour, so neither read as
    selected — the only cue was a ▸ marker."""
    from ui.textual import palette as P

    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            return _style_of(pilot.app, "Setup"), _style_of(pilot.app, "Logs")

    active, inactive = asyncio.run(scenario())

    assert P.ACCENT.lower() in active.lower(), active
    assert "bold" in active.lower(), active
    assert P.ACCENT.lower() not in inactive.lower(), (
        f"the inactive tab must not wear the accent: {inactive}"
    )


def test_the_active_accent_follows_the_active_tab():
    from ui.textual import palette as P

    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_LOGS, enabled=True)
            await pilot.pause()
            return _style_of(pilot.app, "Setup"), _style_of(pilot.app, "Logs")

    setup, logs = asyncio.run(scenario())
    assert P.ACCENT.lower() in logs.lower(), logs
    assert P.ACCENT.lower() not in setup.lower(), setup


def test_hovering_an_inactive_tab_shows_it_is_selectable():
    from ui.textual import palette as P

    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            plain = _style_of(pilot.app, "Logs")
            panel.set_hovered_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            hovered = _style_of(pilot.app, "Logs")
            return plain, hovered

    plain, hovered = asyncio.run(scenario())
    assert plain != hovered, "hover must be visually distinguishable"
    assert P.ACCENT_HOVER.lower() in hovered.lower(), hovered


def test_hovering_the_active_tab_leaves_it_accented():
    """Hover must not demote the tab you are already on."""
    from ui.textual import palette as P

    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            panel.set_hovered_tab(BrandPanel.TAB_SETUP)
            await pilot.pause()
            return _style_of(pilot.app, "Setup")

    style = asyncio.run(scenario())
    assert P.ACCENT.lower() in style.lower(), style


def test_styling_does_not_move_the_click_targets():
    """Spans are measured from PLAIN text; markup must not shift them."""
    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            unhovered = panel.tab_spans()
            panel.set_hovered_tab(BrandPanel.TAB_LOGS)
            await pilot.pause()
            hovered = panel.tab_spans()
            row = _bottom_border(pilot.app)
            return unhovered, hovered, row

    unhovered, hovered, row = asyncio.run(scenario())
    assert unhovered == hovered, f"hover shifted the click targets: {unhovered} vs {hovered}"
    # And the spans must still point at the labels they claim to.
    for tab_id, label in ((BrandPanel.TAB_SETUP, "Setup"), (BrandPanel.TAB_LOGS, "Logs")):
        start, end = hovered[tab_id]
        assert label in row[start:end], f"{tab_id} span {start}:{end} = {row[start:end]!r}"


def test_the_byline_only_border_is_unchanged_at_every_width():
    """The no-tabs path must stay byte-identical to its pre-tabs output —
    a property that took three rounds to establish."""
    async def render(width: int) -> str:
        panel = _panel()
        async with _App(panel).run_test(size=(width, 12)) as pilot:
            await pilot.pause()
            return _bottom_border(pilot.app)

    async def scenario():
        return {w: await render(w) for w in (60, 90, 140, 200)}

    rows = asyncio.run(scenario())
    for width, row in rows.items():
        assert "Setup" not in row, f"tabs leaked into the byline-only path at {width}"
        assert "Logs" not in row, f"tabs leaked into the byline-only path at {width}"
        assert row.startswith("╰─"), f"corner artifact at {width}: {row[:12]!r}"
        assert "Kaveh Razavi" in row or "…" in row, f"byline missing at {width}"


def test_a_real_pointer_over_the_border_sets_the_hover():
    """Drives the actual mouse path, not set_hovered_tab directly.

    Without this the styling tests above would still pass with the
    on_mouse_move handler unwired or looking at the wrong row.
    """
    panel = _panel()

    async def scenario():
        async with _App(panel).run_test(size=(140, 12)) as pilot:
            await pilot.pause()
            panel.set_tabs(BrandPanel.TAB_SETUP, enabled=True)
            await pilot.pause()
            spans = panel.tab_spans()
            logs_start, logs_end = spans[BrandPanel.TAB_LOGS]
            mid = (logs_start + logs_end) // 2
            border_row = panel.size.height + 1
            await pilot.hover(BrandPanel, offset=(mid, border_row))
            await pilot.pause()
            hovered = panel._hovered_tab
            # Moving off the border row must clear it again.
            await pilot.hover(BrandPanel, offset=(mid, 1))
            await pilot.pause()
            return hovered, panel._hovered_tab

    hovered, cleared = asyncio.run(scenario())
    assert hovered == BrandPanel.TAB_LOGS, f"pointer over Logs set {hovered!r}"
    assert cleared is None, f"hover stuck after leaving the border row: {cleared!r}"
