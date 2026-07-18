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
            self.write(text)

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
        self.clear()
        for rec in self._records:
            if self._passes_filter(rec):
                self._write_record(rec)

    def known_sources(self) -> list[str]:
        seen: list[str] = []
        for rec in self._records:
            if rec.source and rec.source not in seen:
                seen.append(rec.source)
        return seen
