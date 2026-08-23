"""
LogPane — bordered, filterable log surface that subclasses RichLog
directly so its native bounded-scroll behavior handles containment.

Wrapping RichLog in a Container with overflow:hidden was unreliable in
production (long compose lines pushed past the parent bounds). RichLog
itself manages an internal viewport — when it owns the border, lines
cannot escape it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from rich.text import Text
from textual.widgets import RichLog

from .. import palette as P


# Compose v2 with ``--ansi=never`` emits lines like
#   ``atlas-litellm                | <body>``
# A trailing run of spaces pads the prefix so the ``|`` aligns across
# services. We split on the first ``|`` after a leading whitespace so
# we can color the service-name prefix per-source without disturbing
# the body. ``Container/Network/Volume X Created`` lines (no ``|``)
# fall through to the plain renderer.
_COMPOSE_PREFIX_RE = re.compile(r"^([^|]*?\S)(\s*)(\| )(.*)$", re.DOTALL)


@dataclass
class _LogRecord:
    level: str         # "info" | "warn" | "error" | "ok" | "dim" | …
    source: str        # docker service name or pipeline phase
    raw: str           # ANSI line as received
    styled: "Text | None" = None  # caller-applied Rich styling, preserved across re-renders


class LogPane(RichLog):
    """Bordered, filterable log surface."""

    DEFAULT_CSS = """
    LogPane {
        height: 1fr;
        min-height: 5;
        border: round #2b2f4a;
        background: #0e0f18;
        padding: 0 1;
        margin: 1 0 0 0;
        scrollbar-size-vertical: 1;
    }
    LogPane:focus { border: round #7dcfff; }
    """

    DEFAULT_BUFFER = 10_000

    def __init__(
        self,
        *,
        title: str = " Logs ",
        subtitle: str = "",
        buffer: int = DEFAULT_BUFFER,
        id: str | None = None,
    ) -> None:
        super().__init__(
            id=id,
            highlight=False, markup=False, wrap=True,
            auto_scroll=True, max_lines=buffer,
        )
        self._title = title
        self._subtitle = subtitle
        self._buffer_cap = buffer
        self._records: list[_LogRecord] = []
        # Sources already announced via _on_new_source. A set lookup keeps
        # the hot per-line path O(1) — scanning _records was O(buffer)
        # per streamed line (10k records × `compose logs -f` of 30+
        # services, all on the UI event loop).
        self._seen_sources: set[str] = set()
        self._level_filter: str = "all"
        self._disabled_sources: set[str] = set()
        self._on_new_source = None
        # See _mark_dirty_if_hidden / reflow: set when a line is written
        # while this pane's region is collapsed (e.g. its container has
        # ``display: none``), so the caller can correct the resulting
        # mis-wrapped strips once the pane is visible again.
        self._wrap_dirty = False

    def on_mount(self) -> None:
        if self._title:
            self.border_title = self._title
        if self._subtitle:
            self.border_subtitle = self._subtitle

    def set_title(self, title: str, *, subtitle: str | None = None) -> None:
        self._title = title
        self.border_title = title
        if subtitle is not None:
            self._subtitle = subtitle
            self.border_subtitle = subtitle

    def set_on_new_source(self, callback) -> None:
        """Notified when a previously-unseen source appears (so the chip
        bar can add a chip for it)."""
        self._on_new_source = callback

    def write_log(
        self, line: str, *, level: str = "info", source: str = "",
    ) -> None:
        rec = _LogRecord(level=(level or "info").lower(), source=source, raw=line)
        # Notify on new source
        if (
            source
            and source not in self._disabled_sources
            and self._on_new_source is not None
            and source not in self._seen_sources
        ):
            self._seen_sources.add(source)
            self._on_new_source(source)
        self._records.append(rec)
        if len(self._records) > self._buffer_cap:
            del self._records[: len(self._records) - self._buffer_cap]
        if self._passes_filter(rec):
            self._mark_dirty_if_hidden()
            self._write_record(rec)

    def write_styled(
        self, text: Text, *, level: str = "info", source: str = "",
    ) -> None:
        rec = _LogRecord(
            level=(level or "info").lower(), source=source, raw=text.plain, styled=text,
        )
        if (
            source
            and source not in self._disabled_sources
            and self._on_new_source is not None
            and source not in self._seen_sources
        ):
            self._seen_sources.add(source)
            self._on_new_source(source)
        self._records.append(rec)
        if len(self._records) > self._buffer_cap:
            del self._records[: len(self._records) - self._buffer_cap]
        if self._passes_filter(rec):
            self._mark_dirty_if_hidden()
            self.write(text)

    def _mark_dirty_if_hidden(self) -> None:
        """Flag that the NEXT write will bake its wrap width wrong.

        ``RichLog.write()`` (when no explicit ``width=`` is given) renders
        at ``self.scrollable_content_region.width`` — which collapses to 0
        while this pane is hidden (e.g. its container has ``display:
        none``, as when the Setup tab is active). The write still renders
        IMMEDIATELY rather than deferring: RichLog only defers writes
        before its first-ever resize (tracked by ``self._size_known``,
        inherited from RichLog); once that has happened once, every later
        write renders now, baked at ``max(0, self.min_width)`` — 78 by
        default, regardless of the real terminal width — and the strip
        never re-flows on its own once revealed. ``reflow()`` uses this
        flag to correct it, without paying for a rerender (and the
        scroll-to-bottom jump that comes with one) on every reveal when
        nothing was actually written while hidden.
        """
        if self._size_known and self.scrollable_content_region.width <= 0:
            self._wrap_dirty = True

    def reflow(self) -> None:
        """Re-render buffered records if any were written while hidden.

        Call this once this pane's region is known-good again (e.g. right
        after un-hiding its container) — see ``_mark_dirty_if_hidden``.
        No-op when nothing was written while hidden, so switching tabs
        doesn't jump the scroll position to the bottom on every reveal.

        Retry-safe: the flag is only cleared once the region is confirmed
        non-zero AND the re-render has actually happened at that width.
        If this runs before layout has caught up (region still 0 — e.g.
        called too early relative to the reveal), the flag stays set so a
        later call can retry, instead of consuming the correction and
        silently re-baking the lines wrong again.
        """
        if not self._wrap_dirty:
            return
        if self.scrollable_content_region.width <= 0:
            return
        self._wrap_dirty = False
        self._rerender()

    def _passes_filter(self, rec: _LogRecord) -> bool:
        if self._level_filter != "all" and rec.level != self._level_filter:
            return False
        if rec.source and rec.source in self._disabled_sources:
            return False
        return True

    def _write_record(self, rec: _LogRecord) -> None:
        # A record written via write_styled carries caller-applied Rich styling
        # (pipeline status lines: green ✓ ticks, bold-yellow warnings, bold-red
        # error/recovery hints). Re-emit that Text verbatim so a filter re-render
        # doesn't strip the level coloring — the whole point of those lines.
        if rec.styled is not None:
            self.write(rec.styled)
            return
        # If this looks like a docker compose service line —
        #   ``<container-name>   | <body>``
        # — color the container-name prefix using the per-service
        # palette so each service is visually distinguishable in the
        # stream. The body still gets ANSI parsing in case the service
        # itself emits color codes.
        m = _COMPOSE_PREFIX_RE.match(rec.raw)
        if m and rec.source:
            head, pad, sep, body = m.groups()
            color = P.color_for_source(rec.source)
            text = Text()
            text.append(head, style=color)
            text.append(pad)
            text.append(sep, style=P.TEXT_FAINT)
            # Body may carry ANSI from the service (e.g. LiteLLM uses
            # ANSI bold/colors in its own output). Preserve those.
            text.append_text(Text.from_ansi(body))
            self.write(text)
            return
        # Fallback: plain ANSI-aware render (pipeline status lines,
        # ``Container X Created`` lines without ``|``, etc.).
        self.write(Text.from_ansi(rec.raw))

    def set_filter(self, level: str, disabled_sources: Iterable[str]) -> None:
        self._level_filter = (level or "all").lower()
        self._disabled_sources = set(disabled_sources)
        self._rerender()

    def _rerender(self) -> None:
        # set_filter() reaches this directly (not through write_log/
        # write_styled), so it needs its own dirty check: a level/source
        # filter change made while this pane is hidden re-bakes the WHOLE
        # buffer at the wrong width via _write_record's self.write() calls
        # below, same as a single hidden write does — without this, the
        # flag stays False and the next reveal's reflow() no-ops, leaving
        # the mis-wrapped lines uncorrected. Safe to call unconditionally:
        # when this is reached from reflow() itself (region already
        # confirmed non-zero there), the width check below is False and
        # nothing is (re-)marked.
        self._mark_dirty_if_hidden()
        self.clear()
        for rec in self._records:
            if self._passes_filter(rec):
                self._write_record(rec)

    def visible_text(self) -> str:
        """Plain text of the records passing the ACTIVE level/source
        filter, for clipboard copy.

        Textual 8.x makes RichLog drag-selectable like any other widget,
        but this explicit copy path still earns its place: the buffer can
        run longer than the viewport, and OSC-52 clipboard writes have a
        size cap a raw drag-select doesn't respect. Filtered through
        ``_passes_filter`` so the copy matches what's actually on screen —
        not the full unfiltered buffer."""
        return "\n".join(
            rec.raw for rec in self._records if self._passes_filter(rec)
        )

    def known_sources(self) -> list[str]:
        seen: list[str] = []
        for rec in self._records:
            if rec.source and rec.source not in seen:
                seen.append(rec.source)
        return seen
