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
