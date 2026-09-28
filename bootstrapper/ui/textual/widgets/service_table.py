"""
ServiceTable — dense, color-coded service grid for InfoPanel.

Two slots (sets of columns) side-by-side, separated by a wide gutter
so the visual break between left and right halves is unambiguous.
Each slot has these sub-columns, in order:

    [lock]  [port]  [name]  [source]  [full alias URL]

Where:
- ``port``: the assigned port (``:63000``) — ``—`` when disabled
- ``lock``: 🔒 for always-on infrastructure whose source the user
  cannot change; blank for services the wizard will ask about
- ``name``: the service display name
- ``source``: the chosen variant (e.g. ``ollama-container-gpu``)
- ``full alias URL``: ``http://host:kong_port`` — clickable; ``—``
  when no alias exists for this service. All Kong-routed aliases
  share the Kong listener port (virtual-host routing).

Each column is sized exactly one cell wider than the longest value
present in the actual data — never inflated past content. If the
data is too wide for two slots side-by-side, the layout falls back
to one column so every value still renders without truncation.

Spacing is governed by ``COL_SEP`` (gap between adjacent sub-columns)
and ``GUTTER`` (gap between the two slot halves). ``GUTTER`` is held
at >= 3 * COL_SEP so the visual break between halves is clearly
larger than the gaps between columns inside a half.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text
from textual.widget import Widget

from .. import palette as P


@dataclass
class ServiceRow:
    name: str
    source: str = "container"
    alias: str = ""
    port: str = ""              # the service's own assigned port
    # Port to use when building the clickable alias URL — for Kong
    # virtual-host-routed services this is the Kong listener port
    # (KONG_HTTP_PORT), NOT the service's internal port. Falls back
    # to ``port`` when not explicitly set.
    alias_port: str = ""
    default_source: str = ""
    # True when the user can pick the service's source via the wizard.
    # False marks always-on infrastructure (e.g. Supabase DB) where the
    # source is fixed; rendered with a lock icon in the table.
    configurable: bool = True
    category: str = ""        # drives leading bar color (Task 5.4 uses this)
    pending: bool = False     # drives pending-state rendering
    off_track: bool = False   # True when a track is active and this service is excluded
    # Extra (label, value) rows appended to the hover tooltip card — e.g.
    # MinIO's S3 endpoints, which aren't the row's own console URL. Empty
    # for most services.
    tooltip_extra: list[tuple[str, str]] = field(default_factory=list)
    # Hover-card metadata (from the manifest): all selectable source ids and
    # the services this one depends on. Shown as their own card rows.
    source_options: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)

    @property
    def is_changed(self) -> bool:
        return bool(self.default_source) and self.source != self.default_source


def _fit(text: str, width: int) -> str:
    """Pad with spaces, or truncate with an ellipsis when too long."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text + " " * (width - len(text))
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _port_label(r: ServiceRow) -> str:
    p = (r.port or "").strip().lstrip(":")
    return f":{p}" if p else ""


def _category_aware_split(
    rows: list[ServiceRow], cols: int,
) -> list[list[ServiceRow]]:
    """Partition `rows` into `cols` columns aligned to category boundaries.

    The naive `rows[i*half:(i+1)*half]` split breaks the visual rule
    "same category = adjacent rows" — with 27 rows and 2 columns, LLM
    Core's two members (LiteLLM, LLM Engine) straddle the column gutter.
    Instead we find the row indices where the category changes and pick
    the split point(s) closest to evenly dividing the rows. When no
    internal category boundary exists (single category, or only one row),
    we fall back to an even split so the layout still works.

    Determinism: ties prefer the smaller-left-column choice so the right
    column is at least as tall as the left.
    """
    if cols <= 1 or len(rows) <= 1:
        return [list(rows)]

    # Indices where the category changes (these are the legal split points).
    boundaries = [
        i for i in range(1, len(rows))
        if (rows[i].category or "") != (rows[i - 1].category or "")
    ]
    if not boundaries:
        # No category info or only one category — fall back to even split.
        half = (len(rows) + cols - 1) // cols
        return [rows[i * half:(i + 1) * half] for i in range(cols)]

    targets = [(c + 1) * len(rows) / cols for c in range(cols - 1)]
    splits: list[int] = []
    for target in targets:
        # Pick the unused boundary closest to the target; ties → smaller idx.
        candidates = [b for b in boundaries if b not in splits]
        if not candidates:
            splits.append(int(round(target)))
            continue
        splits.append(min(candidates, key=lambda b: (abs(b - target), b)))
    splits.sort()
    splits.append(len(rows))
    groups: list[list[ServiceRow]] = []
    prev = 0
    for s in splits:
        groups.append(rows[prev:s])
        prev = s
    return groups


def _alias_url(r: ServiceRow) -> str:
    """Full clickable URL for the alias, when one exists.

    Kong-routed aliases (the only kind we render) all share Kong's
    listener port — that's how virtual-host routing works. We pull
    that from ``r.alias_port`` when set, falling back to ``r.port``
    only if a caller didn't provide a Kong port.
    """
    alias = (r.alias or "").strip()
    if not alias:
        return ""
    p = (r.alias_port or r.port or "").strip().lstrip(":")
    if p:
        return f"http://{alias}:{p}"
    return f"http://{alias}"


class ServiceTable(Widget):
    """Service grid rendered as a single styled Text block."""

    DEFAULT_CSS = """
    ServiceTable { height: auto; min-height: 6; }
    """

    can_focus = False

    # Category marker — small filled rectangle in the row's category
    # color, sitting between the service name and its source column.
    # Uses U+25B0 BLACK RECTANGLE so the marker reads at the same visual
    # weight as the source-state ● dot (similar size, doesn't fill the
    # cell), not as a heavy vertical bar.
    BAR_W = 1
    BAR_GLYPH = "▰" * BAR_W

    # Fixed-width portions of each slot.
    ARROW_W = 1
    DOT_W = 1
    # Lock-status column. The 🔒 emoji is double-width on most terminals
    # so the column is allocated 2 cells; configurable rows render blank.
    LOCK_W = 2
    LOCK_ICON = "🔒"
    # Spacing rules:
    #   COL_SEP: gap between adjacent columns within one slot.
    #   GUTTER:  gap between the two slot halves — kept at >= 3 * COL_SEP
    #            so the visual division between halves is clearly larger
    #            than the gaps between sub-columns inside a half.
    COL_SEP = 2
    GUTTER = 6
    # Absolute floors — only apply when the actual data is empty.
    MIN_PORT = 4
    MIN_NAME = 6
    MIN_SOURCE = 6
    MIN_ALIAS_URL = 6

    def __init__(
        self,
        rows: list[ServiceRow] | None = None,
        *,
        max_visible: int | None = None,
        columns: int = 2,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._rows: list[ServiceRow] = list(rows or [])
        self.max_visible = max_visible if max_visible is not None else 999
        # Number of slot columns the user requested. The actual number
        # rendered may drop to 1 when the data is too wide to fit two
        # tight-packed slots side-by-side.
        self.columns = max(1, columns)
        self._cursor: int | None = 0
        # Set by `render()` so neighbour widgets can align to the actual
        # 2nd-slot start. 0 until the first render (or for 1-col mode).
        self._col2_start: int = 0

    def set_cursor(self, index: int | None) -> None:
        self._cursor = index
        self.refresh()

    def set_rows(self, rows: list[ServiceRow]) -> None:
        self._rows = list(rows)
        self._cursor = 0
        self.refresh()

    @property
    def visible_rows(self) -> list[ServiceRow]:
        return self._rows

    # ─── width math ─────────────────────────────────────────────────

    @property
    def _slot_fixed(self) -> int:
        # Per-slot fixed-width overhead. Column order:
        #   arrow(1) sp(1) dot(1) sp(2) lock(LOCK_W) sep
        #     port(var) sep name(var) sep cat-mark(BAR_W) sp
        #     source(var) sep alias(var)
        # The category marker sits between the service name and its
        # source — the eye that read the name immediately picks up the
        # category before moving on to source/alias.
        return (
            self.ARROW_W + 1 + self.DOT_W + 2
            + self.LOCK_W + 4 * self.COL_SEP
            + self.BAR_W + 1
        )

    def _raw_widths(
        self, rows: list[ServiceRow],
    ) -> tuple[int, int, int, int]:
        """Tightest possible widths for the given data — longest content
        in each of the four columns plus exactly one cell of breathing
        room. Returned in render order: (port, name, source, alias_url).
        No scaling, no truncation."""
        def _maxlen(values) -> int:
            return max((len(v) for v in values), default=0)

        port_max = max(self.MIN_PORT, _maxlen((_port_label(r) or "—") for r in rows))
        name_max = max(self.MIN_NAME, _maxlen(r.name for r in rows))
        src_max = max(self.MIN_SOURCE, _maxlen((r.source or "—") for r in rows))
        alias_max = max(
            self.MIN_ALIAS_URL,
            _maxlen((_alias_url(r) or "—") for r in rows),
        )
        return (
            port_max + 1, name_max + 1, src_max + 1, alias_max + 1,
        )

    def _scaled_widths(
        self, raw: tuple[int, int, int, int], slot_width: int,
    ) -> tuple[int, int, int, int]:
        """Fall-back when even the raw widths don't fit a slot — scale
        each down proportionally so we still render something."""
        available = max(10, slot_width - self._slot_fixed)
        total = sum(raw)
        if total <= available:
            return raw
        scale = available / total
        p = max(self.MIN_PORT, int(raw[0] * scale))
        n = max(self.MIN_NAME, int(raw[1] * scale))
        s = max(self.MIN_SOURCE, int(raw[2] * scale))
        a = max(self.MIN_ALIAS_URL, available - p - n - s)
        return p, n, s, a

    def _slot_text(
        self,
        r: ServiceRow | None,
        *,
        is_cursor: bool,
        widths: tuple[int, int, int, int],
    ) -> Text:
        port_w, name_w, source_w, alias_w = widths
        slot = Text()
        sep = " " * self.COL_SEP
        if r is None:
            # All padding in one shot — placeholder row has no marker to color.
            total = (
                self.ARROW_W + 1 + self.DOT_W + 2
                + self.LOCK_W + self.COL_SEP
                + port_w + self.COL_SEP
                + name_w + self.COL_SEP
                + self.BAR_W + 1
                + source_w + self.COL_SEP
                + alias_w
            )
            slot.append(" " * total)
            return slot
        bar_color = P.style_for_category(r.category)
        # off-track rows get the category bar dimmed and are otherwise
        # rendered with muted/faint styling throughout this method.
        if r.off_track:
            bar_color = f"dim {bar_color}"
        slot.append(P.ARROW_RIGHT if is_cursor else " ",
                    style=f"bold {P.ACCENT}" if is_cursor else P.TEXT_FAINT)
        slot.append(" ")
        is_disabled = (r.source or "").lower() == "disabled"
        # Pending-state branch: row decided by the user has not been confirmed yet.
        # All "decided" columns collapse to em-dashes / placeholders; the dot
        # switches to a hollow yellow ◌. Locked rows can never be pending so we
        # don't need to coordinate with the lock branch.
        if r.pending:
            slot.append("◌", style=P.WARN)
            slot.append("  ")
            if r.configurable:
                slot.append(" " * self.LOCK_W)
            else:
                slot.append(self.LOCK_ICON)
            slot.append(sep)
            slot.append(_fit("—", port_w), style=P.TEXT_FAINT)
            slot.append(sep)
            name_color = P.ACCENT if is_cursor else P.TEXT
            slot.append(_fit(r.name, name_w),
                        style=f"bold {name_color}" if is_cursor else name_color)
            slot.append(sep)
            # Category marker — sits between name and source, same ▰ as on answered rows.
            slot.append(self.BAR_GLYPH, style=bar_color)
            slot.append(" ")
            slot.append(_fit("pending…", source_w), style=f"italic {P.WARN}")
            slot.append(sep)
            slot.append(_fit("—", alias_w), style=P.TEXT_FAINT)
            return slot
        dot_style = P.style_for_source_choice(r.source)
        if r.off_track:
            dot_style = f"dim {dot_style}"
        slot.append(P.DOT_RUNNING, style=dot_style)
        slot.append("  ")
        # 1) Lock status — 🔒 for always-on services whose source can't
        # be picked in the wizard; blank for configurable services so
        # the user's eye is drawn to the rows the wizard will ask about.
        if r.configurable:
            slot.append(" " * self.LOCK_W)
        else:
            slot.append(self.LOCK_ICON)
        slot.append(sep)
        # 2) Port — the at-a-glance answer to "what port is this on?"
        port = _port_label(r) if not is_disabled else ""
        port_text = port or "—"
        port_color = P.ACCENT if port else P.TEXT_FAINT
        if r.off_track:
            port_color = f"dim {port_color}"
        slot.append(_fit(port_text, port_w), style=port_color)
        slot.append(sep)
        # 3) Name
        name_color = P.TEXT_MUTED if (is_disabled or r.off_track) else (
            P.ACCENT if is_cursor else P.TEXT
        )
        slot.append(_fit(r.name, name_w),
                    style=f"bold {name_color}" if is_cursor else name_color)
        slot.append(sep)
        # 4) Category marker — small filled rectangle between name and source.
        slot.append(self.BAR_GLYPH, style=bar_color)
        slot.append(" ")
        # 5) Source — full variant name. Color tracks the source-state
        # ● dot via `style_for_source_choice` so localhost variants
        # render in the same light blue as the dot itself. (Previously
        # this branch applied WARN/yellow when `r.is_changed`, which
        # made a user-picked localhost variant look orange instead of
        # blue. "Changed from default" is a transient state that the
        # wizard's command preview already surfaces; the source column
        # is reserved for steady-state semantics.)
        # off-track rows render as "disabled (off-track)" per spec §5.2 #5:
        # their effective fate is disabled regardless of the actual source
        # value stored in .env. The actual source is preserved in r.source;
        # only the visual label changes.
        source_style = P.style_for_source_choice(r.source)
        source_label = r.source or "—"
        if r.off_track:
            source_label = "disabled (off-track)"
            source_style = f"dim {source_style}"
        slot.append(_fit(source_label, source_w), style=source_style)
        slot.append(sep)
        # 6) Full clickable alias URL — uses Kong listener port for
        # virtual-host-routed aliases, NOT the service's own port.
        url = _alias_url(r) if not is_disabled else ""
        url_text = url or "—"
        url_color = P.INFO if url else P.TEXT_FAINT
        if r.off_track:
            url_color = f"dim {url_color}"
        slot.append(_fit(url_text, alias_w), style=url_color)
        return slot

    def render(self) -> Text:
        rows = self.visible_rows[: self.max_visible]
        out = Text()
        if not rows:
            out.append("  no services match the filter", style=P.TEXT_MUTED)
            return out

        avail = self.size.width or 130

        # Each column gets exactly (longest content + 1) cells. If two
        # tight slots don't fit side-by-side at that width, fall back to
        # one column so every value still renders without truncation.
        raw = self._raw_widths(rows)
        needed = sum(raw) + self._slot_fixed
        cols = self.columns
        while cols > 1:
            slot_width = (avail - self.GUTTER * (cols - 1)) // cols
            if needed <= slot_width:
                break
            cols -= 1
        slot_width = (avail - self.GUTTER * (cols - 1)) // cols
        widths = self._scaled_widths(raw, slot_width)

        # Cache the actual second-slot start column so neighbour widgets
        # (CloudApisRow) can align to it. Each rendered slot occupies
        # exactly ``self._slot_fixed + sum(widths)`` cells (the tight
        # raw widths, NOT the full ``slot_width`` budget), so the visual
        # 2nd-slot start lands at that total plus GUTTER. Lives on the
        # instance, refreshed each render, default 0 when single-column.
        slot_render_width = self._slot_fixed + sum(widths)
        self._col2_start = (
            slot_render_width + self.GUTTER if cols >= 2 else 0
        )

        groups = _category_aware_split(rows, cols)
        # group_offsets[c_idx] is the starting absolute index for that column.
        group_offsets: list[int] = [0]
        for g in groups[:-1]:
            group_offsets.append(group_offsets[-1] + len(g))
        # The render loop walks visually-top-to-bottom across all columns;
        # row count = the tallest group.
        max_height = max((len(g) for g in groups), default=0)

        # Stash the layout so on_mouse_move() can map a cursor (x, y) back to
        # a row for the hover tooltip. Each column block is
        # slot_render_width + GUTTER wide; row index within a column == y line.
        self._hit_flat = [r for g in groups for r in g]
        self._hit_offsets = list(group_offsets)
        self._hit_lens = [len(g) for g in groups]
        self._hit_block_w = slot_render_width + self.GUTTER
        for r_idx in range(max_height):
            for c_idx, group in enumerate(groups):
                if c_idx > 0:
                    out.append(" " * self.GUTTER)
                if r_idx < len(group):
                    abs_idx = group_offsets[c_idx] + r_idx
                    is_cursor = (self._cursor is not None and abs_idx == self._cursor)
                    out.append(self._slot_text(
                        group[r_idx], is_cursor=is_cursor, widths=widths,
                    ))
                else:
                    out.append(self._slot_text(
                        None, is_cursor=False, widths=widths,
                    ))
            if r_idx + 1 < max_height:
                out.append("\n")
        return out

    def on_resize(self) -> None:
        self.refresh()

    def _row_at(self, x: int, y: int) -> ServiceRow | None:
        """Map a widget-relative (x, y) to the rendered row, or None.

        Mirrors render()'s grid: column = x // block_width, row = y line.
        Relies on the layout stashed by the last render().
        """
        block_w = getattr(self, "_hit_block_w", 0)
        offsets = getattr(self, "_hit_offsets", [])
        if block_w <= 0 or not offsets:
            return None
        col = x // block_w
        if col < 0 or col >= len(offsets):
            return None
        if y < 0 or y >= self._hit_lens[col]:
            return None
        idx = offsets[col] + y
        flat = getattr(self, "_hit_flat", [])
        return flat[idx] if 0 <= idx < len(flat) else None

    @staticmethod
    def _tooltip_pairs(row: ServiceRow) -> list[tuple[str, str]]:
        """The (label, value) rows shown in a service's hover card."""
        pairs: list[tuple[str, str]] = [("Source", row.source or "—")]
        if row.source_options:
            pairs.append(("Source options", ", ".join(row.source_options)))
        if row.depends_on:
            pairs.append(("Depends on", ", ".join(row.depends_on)))
        if row.alias and row.alias_port:
            pairs.append(("URL", f"http://{row.alias}:{row.alias_port}"))
        # ``row.port`` is pre-colon'd by resolve_port (e.g. ":64094").
        port = (row.port or "").lstrip(":").strip()
        if port and port != "—":
            pairs.append(("Local", f"localhost:{port}"))
        pairs.extend(row.tooltip_extra)
        return pairs

    @staticmethod
    def _build_tooltip(row: ServiceRow) -> Text:
        """The row's hover tooltip: see ``service_card``."""
        return service_card(row)

    def on_mouse_move(self, event) -> None:
        """Update the hover tooltip to the service under the cursor."""
        row = self._row_at(event.x, event.y)
        self.tooltip = self._build_tooltip(row) if row is not None else None


def service_card(row: ServiceRow) -> Text:
    """A row's details card, shared by the hover tooltip and the keyboard
    review (#1179) so the two can never show different details.

    Title line (the service name) over one ``label  value`` row per
    field — labels left-aligned in a fixed column, URLs accented, and
    the active source highlighted within the 'Source options' list.
    """
    pairs = ServiceTable._tooltip_pairs(row)
    label_w = max((len(k) for k, _ in pairs), default=0)
    card = Text(no_wrap=True)
    card.append(row.name, style=f"bold {P.TEXT_BRIGHT}")
    for label, value in pairs:
        card.append("\n")
        card.append(label.ljust(label_w), style=P.TEXT_MUTED)
        card.append("  ")
        if label == "Source options" and row.source_options:
            # Accent the selected/active source; dim the rest.
            for i, opt in enumerate(row.source_options):
                if i:
                    card.append(", ", style=P.TEXT_MUTED)
                if opt == row.source:
                    card.append(opt, style=f"bold {P.ACCENT}")
                else:
                    card.append(opt, style=P.TEXT_MUTED)
        else:
            is_url = value.startswith(("http://", "https://", "sc://"))
            card.append(value, style=P.ACCENT if is_url else P.TEXT_BRIGHT)
    return card

