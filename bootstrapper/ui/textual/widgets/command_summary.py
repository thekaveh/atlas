"""
CommandSummary — bordered, soft-wrapping live command preview.

Layout:

    ╭─ Command summary ──────────────────────────────────────────────╮
    │ ./start.sh --llm-provider-source ollama-localhost              │
    │ --comfyui-source container-gpu --base-port 63000               │
    ╰────────────────────────────────────────────────────────────────╯

The command renders as ONE flowing line that soft-wraps to the panel width,
rather than one line per flag. With the number of source-configurable services
Atlas now ships, a line-per-flag summary outgrew its slot by the end of the
wizard and overflowed the pane it shares with the prompt. Flat wrapping makes
the height track the command's total width instead of the selection count, and
``max-height`` + ``overflow-y: auto`` bound the remainder — a very long command
scrolls inside the panel instead of squeezing the prompt/service area.

Updates as the user picks options. Uses ``Static.update()`` (not
``refresh()``) so the new content actually replaces the old.
"""

from __future__ import annotations

from typing import Iterable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

from .. import palette as P


def _build_text(program: str, flags: list[tuple[str, str]]) -> Text:
    """Single flowing line: ``./start.sh --a x --b y``.

    Deliberately NOT one line per flag. Atlas supports enough source-configurable
    services that a line-per-flag summary grew past the panel's slot by the end
    of the wizard and overflowed (it shares ``#lower-pane`` with the prompt). A
    flat line soft-wraps to the panel width instead, so the height grows with
    the *width* of the command rather than the *count* of selections, and the
    panel's ``max-height`` bounds whatever is left.
    """
    out = Text(no_wrap=False)
    out.append(program, style=P.TEXT_BRIGHT)
    if not flags:
        out.append("    (using .env defaults)", style=P.TEXT_FAINT)
        return out
    for flag, value in flags:
        out.append(" ")
        out.append(flag, style=P.ACCENT)
        if value:
            out.append(" ")
            out.append(value, style=P.TEXT)
    return out


class CommandSummary(Container):
    """Bordered + titled live command summary."""

    #: Rows of command text shown before the panel starts scrolling. The panel
    #: shares ``#lower-pane`` with the prompt, so this cap is what guarantees a
    #: long command can never starve the prompt/service area (#906 follow-up).
    MAX_BODY_ROWS = 4

    DEFAULT_CSS = """
    CommandSummary {
        height: auto;
        max-height: 6;          /* MAX_BODY_ROWS + border (2) */
        min-height: 3;          /* border + 1 row: yields on short terminals
                                   rather than colliding with the footer */
        overflow-y: auto;
        overflow-x: hidden;
        border: round #2b2f4a;
        background: #0e0f18;
        padding: 0 1;
        margin-top: 1;   /* gutter between the prompt panel and this one */
        scrollbar-size-vertical: 1;
    }
    CommandSummary > Static {
        height: auto;
        background: transparent;
    }
    """

    can_focus = False

    def __init__(
        self,
        *,
        program: str = "./start.sh",
        flags: Iterable[tuple[str, str]] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.program = program
        self.flags: list[tuple[str, str]] = list(flags or [])
        self._body = Static(_build_text(self.program, self.flags))

    def on_mount(self) -> None:
        self.border_title = " Command summary "

    def compose(self) -> ComposeResult:
        yield self._body

    def set_flags(self, flags: Iterable[tuple[str, str]]) -> None:
        self.flags = list(flags)
        self._body.update(_build_text(self.program, self.flags))
