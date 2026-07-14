"""
MultiselectFilterChips — single-select filter row mounted above a
multiselect step's option list.

Used by the wizard's ``Ollama  ·  models`` step to expose capability
filters: ``[ALL] embedding thinking vision tools audio``. Exactly one
chip is active; clicking another chip emits a custom
:class:`FilterChanged` message that the :class:`PromptPanel` handles by
re-mounting only the matching ``OptionRow`` widgets.

Visual / interaction model is a deliberate copy of ``log_filter_chips._Chip``:
same 1-cell height, same colour matrix, same click-to-activate.
Refactoring the inner chip class out of ``log_filter_chips.py`` into a
shared module is a worthwhile follow-up but kept out of this diff so it
stays focused.
"""

from __future__ import annotations

from typing import Iterable

from textual.app import ComposeResult
from textual.containers import Container
from textual.message import Message
from textual.widgets import Static


# Sentinel chip key for "no filter — show every option".
ALL_KEY = "all"


class _FilterChip(Static):
    """Pill-shaped clickable filter chip.

    Visually matches ``log_filter_chips._Chip`` (same hex values, same
    active-state styling) so the two chip rows feel consistent across
    the wizard and the launch-log pane.
    """

    DEFAULT_CSS = """
    _FilterChip {
        height: 1;
        width: auto;
        padding: 0 2;
        margin-right: 1;
        background: #1a1b2c;
        color: #8992b5;
    }
    _FilterChip.-active {
        background: #2c3e54;
        color: #e0e6f2;
        text-style: bold;
    }
    /* ALL gets the same accent treatment as the log pane's "All". */
    _FilterChip.-key-all.-active {
        background: #2c3e54;
        color: #a8d4e6;
    }
    """

    def __init__(self, label: str, *, chip_key: str, active: bool = False) -> None:
        super().__init__(label)
        self._key = chip_key
        self._active = active
        self._sync_classes()

    def _sync_classes(self) -> None:
        self.set_class(self._active, "-active")
        self.set_class(True, f"-key-{self._key}")

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._sync_classes()

    def on_click(self) -> None:
        # Bubble a message up through the DOM so the parent panel sees it
        # via on_filter_changed. Cleaner than a direct callback ref —
        # lets the chip stay parent-agnostic.
        self.post_message(FilterChanged(self._key))


class FilterChanged(Message):
    """Emitted when the user picks a different filter chip.

    Handled by :meth:`PromptPanel.on_filter_changed`, which recomputes
    the visible option set and re-mounts ``OptionRow`` widgets.
    """

    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag


class MultiselectFilterChips(Container):
    """Single-select filter chip row (inline, 1 cell tall, no border).

    Constructor takes the explicit tag list (already lowercased,
    already in display order). The widget always prepends an ``ALL``
    chip that resets the filter.
    """

    DEFAULT_CSS = """
    MultiselectFilterChips {
        height: 1;
        layout: horizontal;
        padding: 0;
        background: #0e0f18;
    }
    MultiselectFilterChips Static.mfc-label {
        color: #8992b5; padding-right: 2;
        height: 1; width: 8; text-style: bold;
    }
    """

    can_focus = False

    def __init__(
        self,
        tags: Iterable[str],
        *,
        active: str = ALL_KEY,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        # Preserve insertion order; dedup defensively in case a caller
        # passes the same tag twice.
        self._tags: list[str] = []
        seen: set[str] = set()
        for t in tags:
            t = t.strip().lower()
            if t and t != ALL_KEY and t not in seen:
                seen.add(t)
                self._tags.append(t)
        self._active = active if (active == ALL_KEY or active in seen) else ALL_KEY
        self._chips: dict[str, _FilterChip] = {}

    def compose(self) -> ComposeResult:
        yield Static("Filter", classes="mfc-label")
        all_chip = _FilterChip(
            "ALL", chip_key=ALL_KEY, active=(self._active == ALL_KEY),
        )
        self._chips[ALL_KEY] = all_chip
        yield all_chip
        for tag in self._tags:
            chip = _FilterChip(
                tag, chip_key=tag, active=(self._active == tag),
            )
            self._chips[tag] = chip
            yield chip

    @property
    def active(self) -> str:
        return self._active

    def set_active(self, tag: str) -> None:
        """Programmatic activation (no Message bubble) — used by the
        panel when restoring state across step revisits."""
        if tag != ALL_KEY and tag not in self._chips:
            tag = ALL_KEY
        if tag == self._active:
            return
        self._active = tag
        for key, chip in self._chips.items():
            chip.set_active(key == tag)
