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

During setup it also carries the Decisions page (#1198): every answered step,
searchable by step title or service. Enter on one closes the overlay with the
list's state, which names that step, so the wizard can jump straight to it and
reopen the list exactly where it was once the edit is done.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Input, OptionList, Static
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


@dataclass(frozen=True)
class Decision:
    """One answered step on the Decisions page. ``value`` is display-safe:
    the wizard masks secret answers before building it."""

    step_index: int
    title: str
    service: str
    value: str


@dataclass(frozen=True)
class ReviewState:
    """Where the Decisions page was, so a finished edit can put it back.

    ``step_index`` names the highlighted decision, so the cursor returns to
    it even when the edit added or pruned rows above it; ``row`` is the
    fallback position when that decision is no longer listed.
    """

    search: str = ""
    step_index: int | None = None
    row: int = 0
    scroll_y: float = 0.0


@dataclass(frozen=True)
class DecisionsPage:
    """The Decisions page's rows, and where a finished edit left it."""

    decisions: Sequence[Decision]
    state: ReviewState | None = None


_PAGES = ("page-command", "page-services", "page-decisions")


def _words(text: str) -> str:
    """``text`` lowercased with the ``·`` separators and runs of spaces
    collapsed, so "ollama models" finds "Ollama  ·  models"."""
    return " ".join(text.replace("·", " ").lower().split())


def _footer(page: str) -> str:
    """The keys that work on ``page``, in one row at the 60-column floor
    (56 inside the box). On the Decisions page the search box takes ``y``
    and ``q`` as text, so it offers Enter instead of copy."""
    action = "↵ edit" if page == "page-decisions" else "y copy"
    return f"Tab pages · ↑↓ PgUp/Dn · {action} · Esc back"


class ReviewDetails(ModalScreen):
    """Read-only overlay: the whole command, any service's details, and
    (during setup) every answered decision."""

    BINDINGS = [
        Binding("escape", "close", "Back", priority=True),
        Binding("ctrl+o", "close", "Back", priority=True),
        Binding("tab", "switch_page(1)", "Next page", priority=True),
        Binding("shift+tab", "switch_page(-1)", "Previous page", priority=True),
        # Routed per page, so the arrows move the list even while the
        # Decisions search box holds focus.
        Binding("up", "move(-1)", "Up", priority=True),
        Binding("down", "move(1)", "Down", priority=True),
        Binding("pageup", "page(-1)", "Page up", priority=True),
        Binding("pagedown", "page(1)", "Page down", priority=True),
        # Not priority: while the search box has focus these are typed.
        Binding("q", "close", "Back"),
        Binding("y", "copy_command", "Copy command"),
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
    ReviewDetails #page-decisions {{ height: 1fr; }}
    ReviewDetails OptionList {{
        height: 1fr;
        min-height: 3;
        border: none;
        background: {P.BG};
    }}
    ReviewDetails #decision-search {{
        height: 1;
        border: none;
        padding: 0;
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

    def __init__(self, *, lines: Sequence[str], rows: Sequence[ServiceRow],
                 decisions: DecisionsPage | None = None) -> None:
        """``lines`` is the command as :func:`command_lines` renders it;
        ``decisions`` adds the Decisions page (setup only)."""
        super().__init__()
        self._lines = list(lines)
        self._rows = list(rows)
        self._decisions = list(decisions.decisions) if decisions is not None else None
        self._shown: list[Decision] = list(self._decisions or [])
        self._fragment: str | None = None
        self._state = decisions.state if decisions is not None else None
        self._pages = _PAGES if self._decisions is not None else _PAGES[:2]

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
                if self._decisions is not None:
                    with Vertical(id="page-decisions"):
                        # Not select-on-focus: a restored search is extended
                        # by the next key, not replaced by it.
                        yield Input(placeholder="type to filter steps or services",
                                    id="decision-search", select_on_focus=False)
                        yield OptionList(id="decision-list")
            yield Static("", id="details-footer", classes="details-footer")

    def on_mount(self) -> None:
        if self._decisions is not None:
            self._filter(self._state.search if self._state else "")
        if self._state is None:
            self._show("page-command")
            return
        # Returning from an edit (#1198): put the Decisions page back.
        self.query_one("#decision-search", Input).value = self._state.search
        self._show("page-decisions")
        decisions = self.query_one("#decision-list", OptionList)
        if self._shown:
            rows = [d.step_index for d in self._shown]
            decisions.highlighted = (
                rows.index(self._state.step_index)
                if self._state.step_index in rows
                else min(self._state.row, len(rows) - 1)
            )
        self.call_after_refresh(self._restore_scroll, decisions, self._state.scroll_y)

    @staticmethod
    def _restore_scroll(decisions: OptionList, scroll_y: float) -> None:
        # Back to the saved offset, then only as far as the cursor needs if
        # rows above it came or went.
        decisions.scroll_to(y=scroll_y, animate=False, immediate=True)
        decisions.scroll_to_highlight()

    # ── pages ─────────────────────────────────────────────────────────

    def _page(self) -> str:
        return self.query_one(ContentSwitcher).current or "page-command"

    def _show(self, page: str) -> None:
        self.query_one(ContentSwitcher).current = page
        headings = {
            "page-command": f"Command summary · {len(self._lines)} lines",
            "page-services": f"Services · {len(self._rows)} · ↑↓ select",
            "page-decisions": self._decisions_heading(),
        }
        self.query_one("#details-heading", Static).update(headings[page])
        self.query_one("#details-footer", Static).update(_footer(page))
        if page == "page-command":
            self.query_one("#page-command", VerticalScroll).focus()
        elif page == "page-services":
            services = self.query_one("#service-list", OptionList)
            services.focus()
            if services.highlighted is None and self._rows:
                services.highlighted = 0
            self._show_card(services.highlighted)
        else:
            self.query_one("#decision-search", Input).focus()

    def action_switch_page(self, delta: int) -> None:
        index = self._pages.index(self._page())
        self._show(self._pages[(index + delta) % len(self._pages)])

    def _list(self) -> OptionList | None:
        page = self._page()
        if page == "page-services":
            return self.query_one("#service-list", OptionList)
        if page == "page-decisions":
            return self.query_one("#decision-list", OptionList)
        return None

    def action_move(self, delta: int) -> None:
        options = self._list()
        if options is None:
            scroll = self.query_one("#page-command", VerticalScroll)
            (scroll.scroll_down if delta > 0 else scroll.scroll_up)(animate=False)
        elif delta > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()

    def action_page(self, delta: int) -> None:
        options = self._list()
        if options is None:
            scroll = self.query_one("#page-command", VerticalScroll)
            (scroll.scroll_page_down if delta > 0 else scroll.scroll_page_up)(animate=False)
        elif delta > 0:
            options.action_page_down()
        else:
            options.action_page_up()

    # ── services ──────────────────────────────────────────────────────

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
        if event.option_list.id == "service-list":
            self._show_card(event.option_index)

    # ── decisions (#1198) ─────────────────────────────────────────────

    def _filter(self, fragment: str) -> None:
        self._fragment = fragment
        needle = _words(fragment)
        self._shown = [
            d for d in self._decisions or []
            if needle in _words(d.title) or needle in _words(d.service)
        ]
        decisions = self.query_one("#decision-list", OptionList)
        decisions.clear_options()
        decisions.add_options([
            Option(Text.assemble((d.title, P.TEXT_BRIGHT), ("  " + d.value, P.TEXT_MUTED)))
            for d in self._shown
        ])
        if self._shown:
            decisions.highlighted = 0
        if self._page() == "page-decisions":
            self.query_one("#details-heading", Static).update(self._decisions_heading())

    def _decisions_heading(self) -> str:
        return f"Decisions · {len(self._shown)} · type to search · ↵ edit"

    def on_input_changed(self, event: Input.Changed) -> None:
        # Restoring the search on return already filtered; re-filtering on
        # its echoed Changed would reset the restored cursor to row 0.
        if event.input.id == "decision-search" and event.value != self._fragment:
            self._filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "decision-search":
            self._edit_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "decision-list":
            self._edit_highlighted()

    def _edit_highlighted(self) -> None:
        decisions = self.query_one("#decision-list", OptionList)
        index = decisions.highlighted
        if index is None or index >= len(self._shown):
            return
        state = ReviewState(
            search=self.query_one("#decision-search", Input).value,
            step_index=self._shown[index].step_index,
            row=index,
            scroll_y=decisions.scroll_y,
        )
        self.dismiss(state)

    # ── actions ───────────────────────────────────────────────────────

    def action_copy_command(self) -> None:
        self.app.copy_to_clipboard("\n".join(self._lines))
        # OSC 52: a terminal that ignores it leaves the clipboard unchanged,
        # so the notice says what was done rather than claiming success.
        self.notify("Sent to the terminal clipboard (OSC 52), secrets masked", timeout=4)

    def action_close(self) -> None:
        self.dismiss(None)
