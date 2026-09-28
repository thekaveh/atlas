"""Keyboard-reachable review of the command and every service's details (#1179).

The live command summary caps itself at four rows so a long command cannot
starve the prompt, and the service table shows each row's source options,
dependencies and URLs only in a hover tooltip. Neither widget takes focus, so
a keyboard-only user could read neither the rest of a long command nor any
service's details.

This overlay is the keyboard route, following the consequence review (#1168):
it owns the whole terminal while open and is read-only, so dismissing it
returns to the prompt with the step, cursor and selections untouched. Nothing
here docks into the wizard layout, so the 60x20 prompt is unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, OptionList, Static
from textual.widgets.option_list import Option

from .. import palette as P
from ..widgets.command_summary import SECRET_MASK, masked_flags
from ..widgets.service_table import ServiceRow, service_card


def command_lines(program: str, flags: Iterable[tuple[str, str]]) -> list[str]:
    """The command as copyable shell lines: one flag per line, continued.

    Secret-named flags carry the summary's mask, quoted so a pasted command
    passes the literal placeholder instead of the shell reading ``<set>`` as
    two redirections.
    """
    parts = [program] + [
        f"{flag} {repr(value) if value == SECRET_MASK else value}".rstrip()
        for flag, value in masked_flags(flags)
    ]
    return [
        ("  " if i else "") + part + (" \\" if i < len(parts) - 1 else "")
        for i, part in enumerate(parts)
    ]


class ReviewDetails(ModalScreen):
    """Read-only overlay: the whole command, then any service's details."""

    BINDINGS = [
        Binding("escape", "close", "Back", priority=True),
        Binding("ctrl+o", "close", "Back", priority=True),
        Binding("q", "close", "Back", priority=True),
        Binding("tab", "switch_page", "Command/services", priority=True),
        Binding("shift+tab", "switch_page", "Command/services", priority=True),
        Binding("y", "copy_command", "Copy command", priority=True),
    ]

    DEFAULT_CSS = f"""
    ReviewDetails {{
        align: center middle;
        background: $background 85%;
    }}
    ReviewDetails > #details-box {{
        width: 100%;
        height: 100%;
        padding: 0 1;
        background: {P.BG};
        border: solid {P.ACCENT};
    }}
    ReviewDetails .details-heading {{
        color: {P.ACCENT};
        text-style: bold;
    }}
    ReviewDetails #details-pages {{ height: 1fr; }}
    ReviewDetails #page-command {{ height: 1fr; }}
    ReviewDetails #page-services {{ height: 1fr; }}
    ReviewDetails #service-list {{
        height: 1fr;
        min-height: 3;
        border: none;
        background: {P.BG};
    }}
    ReviewDetails #service-card {{
        height: auto;
        padding: 0 0 0 1;
        border-left: solid {P.TEXT_FAINT};
    }}
    ReviewDetails .details-footer {{
        color: {P.TEXT_MUTED};
        height: auto;
    }}
    """

    def __init__(self, *, program: str, flags: Sequence[tuple[str, str]],
                 rows: Sequence[ServiceRow]) -> None:
        super().__init__()
        self._lines = command_lines(program, flags)
        self._rows = list(rows)

    def compose(self) -> ComposeResult:
        with Vertical(id="details-box"):
            yield Static("", id="details-heading", classes="details-heading")
            with ContentSwitcher(initial="page-command", id="details-pages"):
                with VerticalScroll(id="page-command"):
                    yield Static(Text("\n".join(self._lines), style=P.TEXT), id="command-text")
                with Vertical(id="page-services"):
                    yield OptionList(
                        *(Option(row.name, id=str(i)) for i, row in enumerate(self._rows)),
                        id="service-list",
                    )
                    yield Static("", id="service-card")
            yield Static(
                "Tab command/services · ↑↓ PgUp PgDn move · y copy · Esc back",
                classes="details-footer",
            )

    def on_mount(self) -> None:
        self._show("page-command")

    def _show(self, page: str) -> None:
        self.query_one(ContentSwitcher).current = page
        heading = (
            f"Command summary · {len(self._lines)} lines"
            if page == "page-command"
            else f"Services · {len(self._rows)} · ↑↓ select"
        )
        self.query_one("#details-heading", Static).update(heading)
        if page == "page-command":
            self.query_one("#page-command", VerticalScroll).focus()
        else:
            services = self.query_one("#service-list", OptionList)
            services.focus()
            if services.highlighted is None and self._rows:
                services.highlighted = 0
            self._show_card(services.highlighted)

    def _show_card(self, index: int | None) -> None:
        card = self.query_one("#service-card", Static)
        if index is None or not self._rows:
            card.update("")
            return
        # The hover card is built for a floating tooltip and never wraps; here
        # it wraps, so no value is clipped at the 60-column floor.
        text = service_card(self._rows[index]).copy()
        text.no_wrap = False
        text.overflow = "fold"
        card.update(text)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_card(event.option_index)

    def action_switch_page(self) -> None:
        current = self.query_one(ContentSwitcher).current
        self._show("page-services" if current == "page-command" else "page-command")

    def action_copy_command(self) -> None:
        self.app.copy_to_clipboard("\n".join(self._lines))
        # OSC 52: a terminal that ignores it leaves the clipboard unchanged,
        # so the notice says what was done rather than claiming success.
        self.notify("Sent to the terminal clipboard (OSC 52), secrets masked", timeout=4)

    def action_close(self) -> None:
        self.dismiss(None)
