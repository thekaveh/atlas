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

    # Border structure should start with ╰─ (left corner + border line).
    assert row.startswith("╰─"), f"border should start with '╰─', got: {row[:4]}"
