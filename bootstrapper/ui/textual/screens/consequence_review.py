"""Full-screen, scrollable review of a destructive step's consequences (#1168).

``PromptStep.wrap_subtitle`` promises that a destructive warning is fully
readable before the user confirms it, but the compact-height CSS caps every
subtitle at two rows with hidden overflow. At the supported minimum terminal
size (60x20) the cold-start warning does not fit in two rows, so part of what
the user is agreeing to was simply not on screen.

This screen is the way out that does not fight the compact layout: the prompt
stays as small as it is, and the complete text moves to an overlay that owns
the whole terminal and scrolls. It is read-only — dismissing it returns to the
prompt with the selection untouched, so opening the review can never be the
thing that confirms a destructive action.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from .. import palette as P


class ConsequenceReview(ModalScreen):
    """Read-only overlay listing everything a destructive step will do."""

    BINDINGS = [
        # Every exit is a cancel. There is deliberately no "confirm" here:
        # the user returns to the prompt and answers it there, with the
        # safe default still selected.
        Binding("escape", "close", "Back", priority=True),
        Binding("ctrl+r", "close", "Back", priority=True),
        Binding("q", "close", "Back", priority=True),
    ]

    DEFAULT_CSS = f"""
    ConsequenceReview {{
        align: center middle;
        background: $background 85%;
    }}
    ConsequenceReview > #review-box {{
        width: 100%;
        height: 100%;
        padding: 0 1;
        background: {P.BG};
        border: solid {P.WARN};
    }}
    ConsequenceReview .review-heading {{
        color: {P.WARN};
        text-style: bold;
        padding: 0 0 1 0;
    }}
    ConsequenceReview .review-body {{
        color: {P.TEXT};
        padding: 0 0 1 0;
    }}
    ConsequenceReview .review-choice {{
        color: {P.TEXT};
        padding: 0 0 1 0;
    }}
    ConsequenceReview .review-footer {{
        color: {P.TEXT_MUTED};
    }}
    """

    def __init__(self, *, heading: str, body: str, choices: list[tuple[str, str]]):
        super().__init__()
        self._heading = heading
        self._body = body
        self._choices = choices

    def compose(self) -> ComposeResult:
        scroll = VerticalScroll(id="review-box")
        scroll.can_focus = True
        with scroll:
            yield Static(f"⚠  {self._heading}", classes="review-heading")
            yield Static(self._body, classes="review-body")
            for label, hint in self._choices:
                text = f"  • {label}" + (f"\n      {hint}" if hint else "")
                yield Static(text, classes="review-choice")
            yield Static(
                "↑ ↓ PgUp PgDn scroll  ·  Esc returns to the prompt "
                "— nothing is confirmed here",
                classes="review-footer",
            )

    def on_mount(self) -> None:
        # Focus the scroller so the arrow keys scroll this overlay rather
        # than walking the option list on the screen underneath.
        self.query_one("#review-box", VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)
