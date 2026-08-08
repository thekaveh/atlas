"""
LogFilterChips — pill-shaped level chips + a multi-select source dropdown.

    ╭─ Filters ───────────────────────────────────────────────────────╮
    │  Level    [All]  [Errors]  [Warns]  [Info]                       │
    │  Sources  [14 of 14 visible ▾]                                   │
    ╰──────────────────────────────────────────────────────────────────╯

Click the Sources trigger to open an in-place popup overlay (mounted on
the screen's ``popup`` layer at the trigger's offset). The popup
contains a ``SelectionList`` for multi-toggling sources. Escape or
click outside dismisses; the trigger label updates to reflect the new
visible count.
"""

from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import SelectionList, Static
from textual.widgets.selection_list import Selection


LEVEL_KEYS = ("all", "error", "warn", "info")
LEVEL_LABELS = {"all": "All", "error": "Errors", "warn": "Warns", "info": "Info"}


class _Chip(Static):
    """A single pill-shaped clickable chip."""

    DEFAULT_CSS = """
    _Chip {
        height: 1;
        width: auto;
        padding: 0 2;
        margin-right: 1;
        background: #1a1b2c;
        color: #8992b5;
    }
    _Chip.-active {
        background: #2c3e54;
        color: #e0e6f2;
        text-style: bold;
    }
    _Chip.-level-error.-active { background: #4a2628; color: #ff8a80; }
    _Chip.-level-warn.-active  { background: #4a3e1a; color: #ffd86b; }
    _Chip.-level-info.-active  { background: #1f3144; color: #b6cde6; }
    _Chip.-level-all.-active   { background: #2c3e54; color: #a8d4e6; }
    """

    def __init__(
        self,
        renderable,
        *,
        chip_kind: str,
        chip_key: str,
        active: bool = False,
        on_click: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(renderable)
        self._kind = chip_kind        # "level" or "trigger"
        self._key = chip_key
        self._active = active
        self._on_click_cb = on_click
        self._set_classes()

    def _set_classes(self) -> None:
        self.set_class(self._active, "-active")
        if self._kind == "level":
            for lvl in LEVEL_KEYS:
                self.set_class(lvl == self._key, f"-level-{lvl}")

    def update_state(
        self, *, renderable=None, active: bool | None = None,
    ) -> None:
        if renderable is not None:
            self.update(renderable)
        if active is not None:
            self._active = active
            self._set_classes()

    def on_click(self) -> None:
        if self._on_click_cb is not None:
            self._on_click_cb(self._kind, self._key)


class _SourcePopup(Container):
    """In-place dropdown popup. Mounted on the screen's ``popup`` layer
    at the trigger's screen offset; dismisses on escape."""

    BINDINGS = [
        Binding("escape", "dismiss", "Done", priority=True),
    ]

    DEFAULT_CSS = """
    _SourcePopup {
        layer: popup;
        width: 50;
        height: auto;
        max-height: 22;
        border: round #2b2f4a;
        background: #0e0f18;
        padding: 0 1;
    }
    _SourcePopup #popup-hint {
        color: #8992b5;
        height: 1;
        padding: 0;
    }
    _SourcePopup SelectionList {
        height: auto;
        max-height: 18;
        background: transparent;
        border: none;
        color: #c0caf5;
    }
    _SourcePopup SelectionList:focus {
        border: none;
    }
    _SourcePopup SelectionList > .selection-list--option-highlighted {
        background: #1c2034;
    }
    _SourcePopup SelectionList > .selection-list--option {
        padding: 0 1;
    }
    """

    can_focus = True

    def __init__(
        self,
        *,
        sources: list[str],
        disabled: set[str],
        on_dismiss: Callable[[set[str]], None],
    ) -> None:
        super().__init__()
        self._sources = list(sources)
        self._initially_disabled = set(disabled)
        self._on_dismiss_cb = on_dismiss
        self._dismissed = False

    def compose(self) -> ComposeResult:
        yield Static(" esc to close ", id="popup-hint")
        selections = [
            Selection(s, s, initial_state=(s not in self._initially_disabled))
            for s in self._sources
        ]
        yield SelectionList(*selections, id="popup-list")

    def on_mount(self) -> None:
        self.border_title = " Source filter "
        try:
            self.query_one(SelectionList).focus()
        except Exception:  # noqa: BLE001
            pass

    def action_dismiss(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        try:
            sl = self.query_one(SelectionList)
            selected = set(sl.selected)
        except Exception:  # noqa: BLE001
            selected = set(self._sources) - self._initially_disabled
        disabled = {s for s in self._sources if s not in selected}
        # Detach self from the screen, then notify caller.
        self.remove()
        self._on_dismiss_cb(disabled)

    def on_blur(self) -> None:
        # If focus leaves the popup, treat as dismiss.
        # Defer slightly so the SelectionList can take focus first on mount.
        self.set_timer(0.1, self._maybe_dismiss_on_blur)

    def _maybe_dismiss_on_blur(self) -> None:
        if self._dismissed:
            return
        # If focus moved to a child of this popup, stay open.
        focused = self.app.focused
        if focused is not None and focused in self.walk_children():
            return
        self.action_dismiss()


class LogFilterChips(Container):
    """Bordered chip bar — level chips + multi-select source dropdown."""

    DEFAULT_CSS = """
    LogFilterChips {
        height: auto;
        min-height: 4;
        padding: 0 1;
        background: #0e0f18;
        border: round #2b2f4a;
    }
    LogFilterChips > #lfc-level-row {
        height: 1; width: 100%;
    }
    LogFilterChips > #lfc-source-row {
        height: 1; width: 100%;
    }
    LogFilterChips Static.lfc-label {
        color: #8992b5; padding-right: 2;
        height: 1; width: 8; text-style: bold;
    }
    """

    can_focus = False

    def __init__(
        self,
        *,
        on_change: Callable[[str, set[str]], None] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._on_change = on_change
        self._level: str = "all"
        self._known_sources: list[str] = []
        self._disabled_svcs: set[str] = set()
        self._level_chips: dict[str, _Chip] = {}
        self._source_trigger: _Chip | None = None
        self._open_popup: _SourcePopup | None = None

    def on_mount(self) -> None:
        self.border_title = " Filters "

    def compose(self) -> ComposeResult:
        with Horizontal(id="lfc-level-row"):
            yield Static("Level", classes="lfc-label")
            for key in LEVEL_KEYS:
                chip = _Chip(
                    LEVEL_LABELS[key],
                    chip_kind="level", chip_key=key,
                    active=(key == self._level),
                    on_click=self._handle_chip_click,
                )
                self._level_chips[key] = chip
                yield chip
        with Horizontal(id="lfc-source-row"):
            yield Static("Sources", classes="lfc-label")
            self._source_trigger = _Chip(
                self._trigger_label(),
                chip_kind="trigger", chip_key="__sources__",
                active=True,
                on_click=self._handle_chip_click,
            )
            yield self._source_trigger

    # ─── chip click ──────────────────────────────────────────────────

    def _handle_chip_click(self, kind: str, key: str) -> None:
        if kind == "level":
            self._level = key
            for k, chip in self._level_chips.items():
                chip.update_state(active=(k == key))
            self._notify()
        elif kind == "trigger":
            if self._open_popup is not None:
                # Toggle close on second click.
                self._open_popup.action_dismiss()
            else:
                self._open_source_picker()

    def _open_source_picker(self) -> None:
        if not self._known_sources or self._source_trigger is None:
            return

        def _on_dismiss(disabled: set[str]) -> None:
            self._open_popup = None
            self._disabled_svcs = set(disabled)
            self._refresh_trigger()
            self._notify()

        popup = _SourcePopup(
            sources=list(self._known_sources),
            disabled=set(self._disabled_svcs),
            on_dismiss=_on_dismiss,
        )
        self._open_popup = popup
        # Mount on the screen and position relative to the trigger.
        screen = self.screen
        screen.mount(popup)

        # Position the popup just below the trigger chip in screen space.
        trigger_region = self._source_trigger.region
        screen_w, screen_h = screen.size
        popup_w = 50
        # Target position: aligned to trigger left, just below trigger row.
        x = trigger_region.x
        y = trigger_region.bottom
        # Clamp so the popup stays on screen.
        if x + popup_w > screen_w:
            x = max(0, screen_w - popup_w)
        # If there isn't enough room below, flip above the trigger.
        max_height_below = max(4, screen_h - y - 1)
        if max_height_below < 8 and trigger_region.y > max_height_below:
            # Flip up — popup grows upward from trigger top.
            popup.styles.offset = (x, max(0, trigger_region.y - 16))
        else:
            popup.styles.offset = (x, y)

    def toggle_source_picker(self) -> None:
        if self._open_popup is not None:
            self._open_popup.action_dismiss()
            return
        self._open_source_picker()

    def close_source_picker_if_open(self) -> None:
        """Dismiss the source-filter popup, if open, without touching the
        level/source filter state.

        The popup mounts on the SCREEN's ``popup`` layer (see
        ``_open_source_picker``), not inside this pane's own tab
        container, so a caller that hides the Logs tab (``display:
        none``) can't rely on that to also hide an open popup — it would
        otherwise keep rendering on top of whatever tab is now showing.
        ``action_dismiss()``'s own callback already clears
        ``self._open_popup``, so there's nothing else to reset here.
        """
        if self._open_popup is not None:
            self._open_popup.action_dismiss()

    def _trigger_label(self) -> str:
        total = len(self._known_sources)
        visible = total - len(self._disabled_svcs)
        if total == 0:
            return "(none yet) ▾"
        return f"{visible} of {total} visible ▾"

    def _refresh_trigger(self) -> None:
        if self._source_trigger is None:
            return
        self._source_trigger.update_state(renderable=self._trigger_label())

    def _notify(self) -> None:
        if self._on_change:
            self._on_change(self._level, set(self._disabled_svcs))

    # ─── public API ──────────────────────────────────────────────────

    def set_level(self, level: str) -> None:
        if level not in LEVEL_KEYS:
            return
        self._level = level
        for k, chip in self._level_chips.items():
            chip.update_state(active=(k == level))
        self._notify()

    def clear_filters(self) -> None:
        # Close an open source popup first: its dismiss handler writes
        # back the popup's open-time disabled-set snapshot, which would
        # silently revert this clear.
        if self._open_popup is not None:
            self._open_popup.action_dismiss()
            self._open_popup = None
        self._level = "all"
        self._disabled_svcs.clear()
        for k, chip in self._level_chips.items():
            chip.update_state(active=(k == "all"))
        self._refresh_trigger()
        self._notify()

    _META_SOURCES = {"pipeline", "docker", "hosts", "verify", "stdout", "stderr"}

    @staticmethod
    def _normalize(source: str) -> str:
        """Defensive re-strip: drop the active project prefix and any
        trailing ``-<digit>+`` replica suffix so a source that slipped
        through unstripped (timing race during launch transition)
        still collapses to the same bare form as a properly-stripped
        one. Mirrors ``wizard_screen._strip_project``."""
        try:
            from ..screens.wizard_screen import _strip_project
        except Exception:  # noqa: BLE001
            return source
        return _strip_project(source)

    def add_source(self, source: str) -> None:
        source = self._normalize(source)
        if (
            not source
            or source in self._known_sources
            or source in self._META_SOURCES
        ):
            return
        self._known_sources.append(source)
        self._refresh_trigger()
