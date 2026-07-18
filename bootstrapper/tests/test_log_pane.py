"""LogPane styling-preservation regression (write_styled across a re-render)."""
from __future__ import annotations

from rich.text import Text

from ui.textual.widgets.log_pane import LogPane


def _capturing_pane():
    """A LogPane whose render calls are captured instead of touching the (un-
    mounted) RichLog buffer, so the record/filter logic can be exercised."""
    pane = LogPane()
    writes: list = []
    pane.write = lambda item: writes.append(item)  # type: ignore[method-assign]
    pane.clear = lambda: None  # type: ignore[method-assign]
    return pane, writes


def test_write_styled_preserves_styling_across_rerender():
    pane, writes = _capturing_pane()
    styled = Text("phase ok", style="bold yellow")
    pane.write_styled(styled, level="warn", source="pipeline")

    # The initial render emits the styled Text verbatim.
    assert writes and writes[-1] is styled

    writes.clear()
    # A filter re-render (e.g. clicking a chip) must re-emit the SAME styled Text
    # — not a plain fallback that drops the bold-yellow level coloring.
    pane.set_filter("all", set())
    assert writes, "re-render should re-emit the record"
    reemitted = writes[-1]
    assert isinstance(reemitted, Text)
    assert reemitted is styled  # exact styled Text re-emitted, not a plain fallback
    assert reemitted.style == "bold yellow"  # level coloring intact


def test_write_log_still_renders_after_rerender():
    # A plain (unstyled) log line still round-trips through a re-render.
    pane, writes = _capturing_pane()
    pane.write_log("plain line", level="info", source="docker")
    writes.clear()
    pane.set_filter("all", set())
    assert writes, "plain record should re-emit on re-render"
    assert isinstance(writes[-1], Text)
