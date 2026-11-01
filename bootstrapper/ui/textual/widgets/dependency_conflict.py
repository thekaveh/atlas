"""
DependencyConflict — red-bordered inline panel for unresolvable selections.

Mockup 003 inline insert (after a wizard option list):

    ┌─────────────────────────────────────────────────────────┐
    │ ⚠ Dependency conflict detected                          │
    │                                                          │
    │ Open WebUI requires an LLM provider, but none is        │
    │ enabled. Pick one or disable Open WebUI.                │
    │                                                          │
    │   ▸ Enable ollama-localhost (recommended)               │
    │   ▸ Disable Open WebUI                                  │
    └─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.widget import Widget

from .. import palette as P


@dataclass
class ConflictAction:
    label: str
    primary: bool = False


class DependencyConflict(Widget):
    """Red-bordered inline panel."""

    DEFAULT_CSS = """
    DependencyConflict {
        height: auto;
        min-height: 4;
        padding: 0 2;
        border: solid #80393a;
        background: #12131e;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        *,
        title: str = "Dependency conflict detected",
        body: str = "",
        actions: list[ConflictAction] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.title = title
        self.body = body
        self.actions = actions or []

    def render(self) -> Text:
        out = Text()
        out.append("⚠ ", style=P.ERR_SOFT)
        out.append(self.title, style=P.ERR_SOFT)
        if self.body:
            out.append("\n\n")
            out.append(self.body, style=P.TEXT)
        if self.actions:
            out.append("\n\n")
            for i, action in enumerate(self.actions):
                if i > 0:
                    out.append("   ", style=P.TEXT_FAINT)
                    out.append("or", style=P.TEXT_FAINT)
                    out.append("   ", style=P.TEXT_FAINT)
                color = P.ACCENT if action.primary else P.TEXT_MUTED
                style = f"bold {color}" if action.primary else color
                out.append("▸ ", style=color)
                out.append(action.label, style=style)
        return out
