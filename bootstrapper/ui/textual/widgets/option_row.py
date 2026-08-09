"""
OptionRow — single selectable option in the wizard prompt panel.

Two render shapes:

  • **Simple row** (single-select / non-Ollama steps): 1 cell when
    ``hint`` is empty, 2 when present.

        Line 1:  ▸ ◉ ollama-localhost                  [rec.][GPU]
        Line 2:       Use the Ollama already running on this host

  • **Ollama multiselect row** (filter_tags step): always 2 cells.
    Adds an expand glyph, a tree-leaf rendering mode, capability tags
    in fixed canonical columns (left-aligned, not right-aligned), a
    per-tag-coloured size variants line, and a right-aligned pull
    count.

        Line 1:  ▸ ▼ [✓] qwen3.6  [thinking][vision][tools][mlx]  [library]   28.6M
        Line 2:       latest · 8b (4.8GB) · 14b (8.4GB) · 27b (17GB) · …

        Leaf:  ○ └─ [✓] qwen3.6:8b   (4.8GB · 256K ctx)   [vision]

Capability tags render in a canonical column order
(``embedding · thinking · vision · tools · audio · mlx``) with
reserved per-slot widths so the same tag lands at the same horizontal
column across visible rows. Absent capabilities still reserve their
slot. Status tags (``pulled`` / ``library`` / ``legacy`` / ``default``)
follow with variable width. Below
``_PARENT_ALIGNED_MIN_WIDTH``/``_LEAF_ALIGNED_MIN_WIDTH`` cells of
terminal width the row falls back to inline variable-width tag
rendering so the right-aligned pull count doesn't get pushed off.

Optional fields driving the multiselect shape:
* ``sizes``        — tuple of variant tags (``"8b"``, ``"70b"``, …).
* ``pulls``        — int; rendered ``114.2M`` / ``85K`` / ``1.2B``.
* ``checked_variants`` — frozenset of size tags that should colour
                         green on line 2 (per-tag selection state).
* ``expand_state``       — ``"collapsed"`` / ``"expanded"`` / ``"none"``
                           drives the ▶ / ▼ glyph.
* ``is_leaf`` + ``leaf_size_label`` — branch into ``_render_leaf``.
* ``size_labels``        — per-tag size annotations from the detail-
                           page fetch (overrides the Q4 approximation).
* ``tag_start_col_override`` — caller-computed column at which the
                               tag block must start (set by
                               PromptPanel for dynamic alignment).

Default empty values render the row identical to its pre-feature
shape so simple non-Ollama steps see zero change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Static

from .. import palette as P

if TYPE_CHECKING:
    from textual.widgets import Input


_BADGE_STYLES = {
    "rec.": P.OK,
    "rec":  P.OK,
    "default": P.OK,
    "GPU": P.RESOURCE,
    "CPU": P.TEXT_MUTED,
    "cloud": P.INFO,
    "external": P.INFO,
    "disabled": P.TEXT_FAINT,
    "container": P.OK,
    # Ollama library capability tags — colours pulled from existing
    # palette accents so the row stays harmonious with the rest of the
    # TUI (no new hex values introduced).
    "embedding": P.ACCENT,        # cyan-ish, sub-family of supabase blue
    "thinking":  P.TAG_INFRA,     # warm purple, sub-family of kong
    "vision":    P.OK,            # green, sub-family of comfyui
    "tools":     P.RESOURCE,      # amber
    "audio":     P.ERR_SOFT,      # soft pink
    # MLX = Apple-Silicon-optimised quantized weights. Muted teal so it
    # reads as a distinct hardware-target tag, not yet another
    # capability category.
    "mlx":       P.TAG_DATA,      # muted teal
    # Status badges already in use by the merge helper:
    "pulled":    P.OK,
    "library":   P.INFO,
    # Recency bucket marker — applied to models updated > 365 days ago
    # so they're visually distinct from the recent-popular surface.
    # Muted (TEXT_FAINT) so it reads as "deprioritised" not "warning".
    "legacy":    P.TEXT_FAINT,
    # ── ComfyUI display-group badges (lowercased to match the chip
    #    widget's normalisation). Each group gets a distinct hue so
    #    the row's leading badge reads as the category at a glance.
    "image":       P.ACCENT,      # cyan
    "image-edit":  P.TAG_INFRA,   # purple
    "video":       P.WARN,        # amber
    # "audio" already mapped above (P.ERR_SOFT) for the Ollama
    # capability tag; ComfyUI's group of the same name reuses it
    # intentionally — pink reads as the same family across pickers.
    "3d":          P.OK,          # green
    "custom":      P.RESOURCE,    # orange — sidecar entries stand out
    # ── ComfyUI category badges (per-model). Muted compared to the
    #    group above so the group dominates visually; categories add
    #    secondary information without competing.
    "checkpoint":   P.OK,
    "lora":         P.ACCENT,
    "vae":          P.TAG_DATA,
    "controlnet":   P.TAG_INFRA,
    "ipadapter":    P.TAG_INFRA,
    "instantid":    P.TAG_INFRA,
    "upscaler":     P.RESOURCE,
    # "embedding" already mapped above (Ollama capability tag).
    "clip":         P.TAG_DATA,
    "animatediff":  P.WARN,
    "motion_lora":  P.WARN,
    "video_model":  P.WARN,
    "voice_model":  P.ERR_SOFT,
    "audio_model":  P.ERR_SOFT,
    "mesh_model":   P.OK,
}


# ─── capability tag column alignment ────────────────────────────────────
#
# Capability tags render in a FIXED canonical column order so the same
# tag appears at the same horizontal position across rows. Each tag
# gets a reserved slot of width ``len("[name]")``; rows missing that
# tag emit equal-width whitespace so subsequent slots stay aligned.
#
# Status tags (library / legacy / default / pulled) are rendered AFTER
# the capability block with variable width — they're metadata about
# the row's state, not a capability dimension, so vertical alignment
# matters less than seeing them at all.
_CAPABILITY_COLUMN_ORDER = (
    "embedding", "thinking", "vision", "tools", "audio", "mlx",
)
_CAPABILITY_COLUMN_WIDTHS = {
    name: len(f"[{name}]") for name in _CAPABILITY_COLUMN_ORDER
}
_CAPABILITY_COLUMN_GAP = 2  # cells between adjacent capability slots
_CAPABILITY_TAGS = frozenset(_CAPABILITY_COLUMN_ORDER)

# Default target start columns for the capability block. The caller
# (PromptPanel._mount_visible_rows) supersedes these with a dynamic
# value computed from the longest content across visible rows — so
# every row pads to the same column even when one outlier label
# (qwen3.6:35b-a3b-coding-mxfp8 …) is much longer than its siblings.
# Defaults below remain as a sane fallback for callers that don't
# pre-compute (smoke tests, non-multiselect use of OptionRow).
_PARENT_TAG_COL = 30
_LEAF_TAG_COL = 64

# Fixed prefix widths BEFORE the label. Used by the caller to compute
# the dynamic tag-start column (max prefix + max label = where the
# tag block must begin to be flush-right of every row).
_PARENT_PREFIX_WIDTH = 11   # cursor(3) + 2-space gap + expand(2) + checkbox(4)
_LEAF_PREFIX_WIDTH = 14     # indent(3) + cursor(3) + space(1) + connector(3) + checkbox(4)

# Below these widths we drop alignment and fall back to inline,
# variable-width tag rendering so a narrow terminal doesn't push the
# pull count off-screen or wrap the row.
_PARENT_ALIGNED_MIN_WIDTH = 100
_LEAF_ALIGNED_MIN_WIDTH = 130


# Quantization rule of thumb for converting Ollama parameter counts to
# approximate on-disk size. ``0.6`` matches Ollama's default Q4_K_M
# quantization (~4.7 bits/param → 0.59 bytes/param). Real downloads
# can be 10-20% off either direction depending on model architecture
# and chosen tag, but it's a useful order-of-magnitude indicator.
_QUANT_BYTES_PER_PARAM = 0.6


def _approx_size(param_str: str) -> str:
    """Convert an Ollama parameter notation to an approximate disk size.

    The listing page only publishes parameter counts ("8b", "270m"),
    not byte sizes — those live on per-variant detail pages. We
    convert in place so the user sees a familiar GB/MB number instead
    of having to translate ``b`` (billions of parameters) into disk
    footprint mentally.

    "8b"   → "4.8GB"    (8e9 × 0.6 ≈ 4.8e9 bytes)
    "70b"  → "42GB"
    "405b" → "243GB"
    "1.5b" → "900MB"    (sub-GB values render in MB for legibility)
    "1b"   → "600MB"
    "0.6b" → "360MB"
    "270m" → "162MB"

    Unparseable input is returned verbatim (defence against unexpected
    upstream notation; the caller falls through unchanged).
    """
    s = param_str.strip().lower()
    if not s:
        return ""
    suffix = s[-1]
    multipliers = {"b": 1_000_000_000, "m": 1_000_000}
    if suffix not in multipliers:
        return param_str
    try:
        params = float(s[:-1]) * multipliers[suffix]
    except ValueError:
        return param_str
    bytes_ = params * _QUANT_BYTES_PER_PARAM
    gb = bytes_ / 1_000_000_000
    if gb < 1:
        mb = bytes_ / 1_000_000
        return f"{mb:.0f}MB"
    # Drop trailing ".0" for whole-number multiples; keep one decimal
    # otherwise. Mirrors _format_pulls.
    return f"{gb:.1f}".rstrip("0").rstrip(".") + "GB"


def _format_pulls(n: int) -> str:
    """Reverse of :func:`utils.ollama_library._parse_pull_count`.

    ``114_200_000`` → ``"114.2M"`` (one decimal, dropped when .0).
    ``< 1000`` returns the bare integer. Negative or zero → empty
    string (caller suppresses the column).
    """
    if n <= 0:
        return ""
    if n < 1_000:
        return str(n)
    for suffix, scale in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if n >= scale:
            v = n / scale
            # Drop trailing ".0" for whole-number multiples; keep one
            # decimal otherwise. Mirrors ollama.com's own formatting.
            return f"{v:.1f}".rstrip("0").rstrip(".") + suffix
    return str(n)


# Separator between variant entries on line 2 (e.g. ``8b·14b·32b``).
_SIZE_SEP = "·"


def _render_capability_block(badges: list[str], *, aligned: bool) -> Text:
    """Capability tags in canonical column order.

    ``aligned=True`` reserves each tag's slot at its bracketed width
    even when the row doesn't carry that capability — absent slots
    render as whitespace so the next present tag still lands at its
    canonical column. ``aligned=False`` collapses absent slots and
    just emits present tags with a 2-space separator, used at narrow
    terminal widths where the full reserved block won't fit.

    Tags outside ``_CAPABILITY_TAGS`` (status / legacy / default /
    pulled / library / unknown) are ignored — the caller renders
    those separately via ``_render_status_badges``.
    """
    cap_set = {b for b in badges if b in _CAPABILITY_TAGS}
    out = Text()
    if aligned:
        for i, name in enumerate(_CAPABILITY_COLUMN_ORDER):
            if i > 0:
                out.append(" " * _CAPABILITY_COLUMN_GAP)
            width = _CAPABILITY_COLUMN_WIDTHS[name]
            if name in cap_set:
                color = _BADGE_STYLES.get(name, P.TEXT_FAINT)
                out.append(f"[{name}]", style=color)
            else:
                out.append(" " * width)
    else:
        first = True
        for name in _CAPABILITY_COLUMN_ORDER:
            if name not in cap_set:
                continue
            color = _BADGE_STYLES.get(name, P.TEXT_FAINT)
            if not first:
                out.append("  ")
            first = False
            out.append(f"[{name}]", style=color)
    return out


def _render_status_badges(badges: list[str]) -> Text:
    """Variable-width tags rendered AFTER the capability block.

    These are state badges (library / legacy / default / pulled) plus
    any future or unrecognised tag — anything not in the canonical
    capability column set. Each is prefixed with ``"  "`` so it reads
    visually grouped with the preceding capability columns.

    Warning badges (any text starting with the ⚠ glyph — emitted by
    the ComfyUI picker for missing custom-node + GPU/VRAM mismatches)
    get P.WARN regardless of the rest of the string, so the user
    doesn't have to register a hardware-mismatch badge in
    _BADGE_STYLES with full match text.
    """
    out = Text()
    for b in badges:
        if b in _CAPABILITY_TAGS:
            continue
        if b.startswith("⚠"):
            color = P.WARN
        else:
            color = _BADGE_STYLES.get(b, P.TEXT_FAINT)
        out.append("  ")
        out.append(f"[{b}]", style=color)
    return out


def _pad_to_column(line: Text, target_col: int, *, min_gap: int = 2) -> None:
    """Pad ``line`` with spaces so the next append starts at
    ``target_col``. When the line already extends past ``target_col``,
    we still emit at least ``min_gap`` spaces so the following column
    doesn't bleed straight into the previous content.
    """
    needed = target_col - line.cell_len
    if needed < min_gap:
        needed = min_gap
    line.append(" " * needed)


# Rich needs a console to resolve wrap options. Nothing about the wrap
# depends on this console's own width (it is passed explicitly), so one
# module-level instance is reused rather than built on every repaint.
_WRAP_CONSOLE = Console()


def _hanging_indent(body: Text, indent: int, width: int) -> Text:
    """Wrap ``body`` and indent EVERY resulting visual line by ``indent``.

    A single ``Text`` that merely *starts* with spaces is one logical
    line to Rich: it indents the first visual row and lets every wrapped
    continuation restart at column 0, so a long hint reads as though it
    escaped its row. Pre-wrapping here and prefixing each line keeps the
    whole block nested under the label. Because the returned lines
    already fit, Rich does not re-wrap them downstream.

    ``OptionRowWithInput`` gets this for free by giving its hint a real
    ``Static`` with ``padding-left``; plain ``OptionRow`` renders to a
    single ``Text``, so it has to do the wrapping itself.
    """
    available = max(1, width - indent)
    pad = " " * indent
    out = Text()
    for i, line in enumerate(body.wrap(_WRAP_CONSOLE, available)):
        if i:
            out.append("\n")
        out.append(pad)
        out.append(line)
    return out


class OptionRow(Widget):
    """1- or 2-cell option row."""

    DEFAULT_CSS = """
    OptionRow { height: auto; padding: 0 1; }
    OptionRow.option-selected { background: #1c2034; }
    """

    can_focus = False

    def __init__(
        self,
        label: str,
        *,
        hint: str = "",
        badges: list[str] | None = None,
        selected: bool = False,
        # Multi-select extras: when ``multi`` is True, the row shows a
        # ``[✓]`` / ``[ ]`` checkbox prefix and the ``checked`` flag
        # tracks user-toggled state independently of the cursor focus
        # (``selected``).
        multi: bool = False,
        checked: bool = False,
        # Optional Ollama enrichments. Empty defaults preserve the
        # pre-feature layout for every non-Ollama caller.
        sizes: tuple[str, ...] | None = None,
        pulls: int = 0,
        # Tags within ``sizes`` that the user has explicitly checked
        # via the variant picker (or pre-checked from default-active
        # in .env). When non-empty, the line-2 size column renders
        # those variants in ``P.OK`` instead of the default muted
        # colour, so a row checked with specific variants reads as
        # "qwen3 with 8b + 14b highlighted" at a glance. Empty set
        # means the row is either unchecked or checked-bare (default
        # :latest pull) — line 2 stays uniformly muted.
        checked_variants: frozenset[str] | None = None,
        # Expand-tree state for the Ollama multiselect: ``"collapsed"``
        # / ``"expanded"`` (the row is a multi-variant parent and the
        # leftmost column gets a ▶ / ▼ glyph), or ``"none"`` (regular
        # row — leftmost column is blank for alignment). Empty default
        # string is treated the same as ``"none"``.
        expand_state: str = "",
        # When True, this row is a tree leaf (one variant of a parent
        # above). Renders indented with a tree connector; capability
        # badges and pull count are suppressed because they're
        # inherited from the parent right above.
        is_leaf: bool = False,
        # For leaves: a single-variant size description rendered on
        # the right (e.g. ``"4.8GB"`` or ``"model-maker default"``
        # for the synthetic :latest leaf).
        leaf_size_label: str = "",
        # Optional override for per-size annotations on the parent's
        # line-2 size column. Maps each tag in ``sizes`` to its
        # rendered annotation (e.g. ``{"27b": "17GB", "27b-coding-mxfp8":
        # "31GB"}``). When a tag isn't in this dict, the renderer falls
        # back to ``_approx_size`` for param-count notation. Used by
        # the Ollama multiselect after a detail-page fetch surfaces
        # real per-variant disk sizes that the Q4 approximation
        # can't derive (quantization variants like ``-mxfp8`` have
        # tag names with no recognisable param-count suffix).
        size_labels: dict[str, str] | None = None,
        # Caller-computed column at which the capability tag block
        # must start. Used by PromptPanel to align tags across the
        # entire visible row set: it pre-passes the rows, finds the
        # one with the longest content (prefix + label [+ size_label
        # for leaves]), and passes that column to every row so the
        # tag block lands at the same horizontal position even when
        # one outlier row has a much longer label than its siblings.
        # ``None`` falls back to the default ``_PARENT_TAG_COL`` /
        # ``_LEAF_TAG_COL`` constants — sane for smoke tests and
        # non-multiselect callers that don't aggregate row metrics.
        tag_start_col_override: int | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.label = label
        self.hint = hint or ""
        self.badges = badges or []
        self.selected = selected
        self.multi = multi
        self.checked = checked
        self.sizes = tuple(sizes or ())
        self.pulls = pulls
        self.checked_variants: frozenset[str] = (
            checked_variants if checked_variants is not None else frozenset()
        )
        self.expand_state = expand_state or "none"
        self.is_leaf = is_leaf
        self.leaf_size_label = leaf_size_label or ""
        self.size_labels: dict[str, str] = dict(size_labels or {})
        self.tag_start_col_override = tag_start_col_override
        if selected:
            self.add_class("option-selected")

    def set_selected(self, value: bool) -> None:
        if value == self.selected:
            return
        self.selected = value
        self.set_class(value, "option-selected")
        self.refresh()

    def set_checked(self, value: bool) -> None:
        if value == self.checked:
            return
        self.checked = value
        self.refresh()

    def set_checked_variants(self, value: frozenset[str]) -> None:
        """Update which size variants render highlighted on line 2.

        Called after the variant-picker popup confirms — the row stays
        in place but its line-2 colouring updates to reflect the new
        per-tag selection.
        """
        if value == self.checked_variants:
            return
        self.checked_variants = value
        self.refresh()

    # ─── render ─────────────────────────────────────────────────────────

    def _render_size_variants(self) -> Text:
        """Format sizes for line 2 as ``latest · 8b (4.8GB) · 70b (42GB) · …``.

        First entry is always the synthetic ``latest`` token — Ollama
        resolves a bare ``ollama pull <model>`` to ``:latest``, and
        the variant tree exposes ``latest`` as a real leaf, so we
        surface it here too. The remaining entries are the scraped
        sizes paired with their approximate Q4 disk footprint.

        Every entry is coloured individually: variants present in
        ``checked_variants`` render in the OK accent colour so the
        user can see at a glance which tags they've picked — that
        includes the synthetic ``latest``, so selecting the :latest
        leaf is now reflected on the parent the same way picking
        ``8b`` or ``14b`` is.

        Returns an empty Text when no sizes are known (catalog-
        fallback path, embedding-only models, pulled-but-not-in-
        library rows) — those rows don't have a variant tree to
        annotate, so prepending ``latest`` alone would be misleading.
        """
        out = Text()
        if not self.sizes:
            return out
        # ``latest`` first — the synthetic always-available tag.
        latest_style = P.OK if "latest" in self.checked_variants else P.TEXT_FAINT
        out.append("latest", style=latest_style)
        out.append(_SIZE_SEP, style=P.TEXT_FAINT)
        for i, raw in enumerate(self.sizes):
            # Prefer the real label from the detail-page fetch when we
            # have it; fall back to the Q4 approximation for plain
            # param-count tags. Tags like ``27b-coding-mxfp8`` can't be
            # approximated (no recognisable suffix), but the detail
            # page gives us the real ``31GB`` to show.
            annot = self.size_labels.get(raw) if self.size_labels else None
            if annot is None:
                a = _approx_size(raw)
                annot = a if a and a != raw else ""
            piece = f"{raw} ({annot})" if annot else raw
            style = P.OK if raw in self.checked_variants else P.TEXT_FAINT
            if i > 0:
                out.append(_SIZE_SEP, style=P.TEXT_FAINT)
            out.append(piece, style=style)
        return out

    def _render_leaf(self, width: int) -> Text:
        """Render a tree-leaf row: indented under the parent, tree-
        connector prefix, name + inline size info in parentheses.

        Layout: ``<indent><cursor><dot> └─ [✓] qwen3:8b (8b · 4.8GB)``
        — the cursor + dot indicator sits AFTER the 3-cell indent so
        the focus glyph reads as visually nested under the parent
        rather than at the same column.

        Capability badges (vision / audio / mlx + family-inherited
        thinking / tools / embedding) render in fixed canonical
        columns starting at ``_LEAF_TAG_COL`` so the same capability
        appears at the same horizontal position across every leaf
        under the parent (when the terminal is wide enough). Status
        badges follow with variable width.
        """
        line = Text()
        # 3-cell indent BEFORE the focus glyph — visually nests the
        # leaf under the parent's checkbox column.
        line.append("   ", style=P.TEXT_FAINT)
        # Cursor + dot indicator (shifted right by the indent above).
        if self.selected:
            line.append(P.ARROW_RIGHT, style=f"bold {P.ACCENT}")
            line.append(" ")
            line.append(P.DOT_FILLED, style=P.ACCENT)
        else:
            line.append(" ")
            line.append(" ")
            line.append(P.DOT_HOLLOW, style=P.TEXT_FAINT)
        line.append(" ", style=P.TEXT_FAINT)
        line.append("└─ ", style=P.TEXT_FAINT)
        # Leaf checkbox.
        if self.multi:
            box = "[✓]" if self.checked else "[ ]"
            box_color = P.OK if self.checked else P.TEXT_FAINT
            line.append(box, style=box_color)
            line.append(" ")
        # Full identifier label (e.g. ``qwen3:8b``).
        label_color = P.ACCENT if self.selected else P.TEXT
        line.append(
            self.label,
            style=f"bold {label_color}" if self.selected else label_color,
        )
        # Inline size info in parentheses, muted. ``leaf_size_label`` is
        # pre-formatted by the caller (e.g. ``"(8b · 4.8GB)"`` or
        # ``"(model-maker default)"``).
        if self.leaf_size_label:
            line.append("  ")
            line.append(self.leaf_size_label, style=P.TEXT_FAINT)
        # Per-variant capability badges in canonical column order.
        # Padded to the caller's pre-computed tag-start column (max of
        # every visible row's content width) so the same tag appears
        # under the same column across leaves of a parent expansion
        # even when one outlier label is much longer than the rest.
        # Explicit ``is None`` check (not ``or``) — a literal ``0`` is
        # never passed today (callers feed ``max(default, ...)``) but
        # ``or`` would mis-fall-back to the default if it ever was.
        if self.badges:
            target_col = (
                _LEAF_TAG_COL
                if self.tag_start_col_override is None
                else self.tag_start_col_override
            )
            aligned = width >= _LEAF_ALIGNED_MIN_WIDTH
            if aligned:
                _pad_to_column(line, target_col)
            else:
                line.append("  ")
            line.append(_render_capability_block(self.badges, aligned=aligned))
            line.append(_render_status_badges(self.badges))
        return line

    def render(self) -> Text:
        width = self.size.width or 60
        if self.is_leaf:
            return self._render_leaf(width)
        # ── line 1 — expand-indicator + label + capability badges + pulls ──
        # Sizes were on line 1 in the first iteration of this widget,
        # but with up to 8 variants (qwen3) and the GB conversion, the
        # row got too dense. They live on line 2 now in muted style;
        # see _render_size_variants below.
        line1 = Text()
        if self.selected:
            line1.append(P.ARROW_RIGHT, style=f"bold {P.ACCENT}")
            line1.append(" ")
            line1.append(P.DOT_FILLED, style=P.ACCENT)
        else:
            line1.append(" ")
            line1.append(" ")
            line1.append(P.DOT_HOLLOW, style=P.TEXT_FAINT)
        line1.append("  ")
        # Expand indicator (2 cells: glyph + space, OR 2 blank cells
        # for non-expandable rows so the checkbox column stays flush).
        if self.expand_state == "collapsed":
            line1.append("▶ ", style=P.TEXT_FAINT)
        elif self.expand_state == "expanded":
            line1.append("▼ ", style=P.ACCENT)
        else:
            line1.append("  ")
        if self.multi:
            # Render checkbox prefix in addition to the dot/arrow indicator.
            box = "[✓]" if self.checked else "[ ]"
            box_color = P.OK if self.checked else P.TEXT_FAINT
            line1.append(box, style=box_color)
            line1.append(" ")
        # Column at which the label begins — cursor(3) + 2-space gap +
        # expand-indicator(2) [+ checkbox(4) when multi]. Captured here
        # so line 2 (size variants / hint) can indent to exactly this
        # column and read as nested *under* the label rather than
        # starting to its left.
        label_col = line1.cell_len
        label_color = P.ACCENT if self.selected else P.TEXT
        line1.append(self.label, style=f"bold {label_color}" if self.selected else label_color)

        # Capability badges in canonical column order — left-aligned
        # in fixed slots so the same tag lands at the same column
        # across rows (when the terminal is wide enough). Status
        # tags (library / legacy / default / pulled) follow with
        # variable width. Pull count stays in the right-aligned block.
        # ``tag_start_col_override`` (set by PromptPanel) keeps every
        # parent's tag block at the same column even when one parent
        # has a much longer label than its siblings. Explicit
        # ``is None`` check — see the matching note in ``_render_leaf``.
        if self.badges:
            target_col = (
                _PARENT_TAG_COL
                if self.tag_start_col_override is None
                else self.tag_start_col_override
            )
            aligned = width >= _PARENT_ALIGNED_MIN_WIDTH
            if aligned:
                _pad_to_column(line1, target_col)
            else:
                line1.append("  ")
            line1.append(_render_capability_block(self.badges, aligned=aligned))
            line1.append(_render_status_badges(self.badges))
        right = Text()
        pull_text = _format_pulls(self.pulls)
        if pull_text:
            right.append(pull_text, style=P.TEXT_MUTED)
        gap = max(1, width - line1.cell_len - right.cell_len - 2)
        line1.append(" " * gap)
        line1.append(right)

        # ── line 2 — size variants (per-tag styled) + hint (muted) ──
        # Up to two fragments joined with " · ":
        #   • Size variants in "8b (4.8GB)" form — each variant
        #     coloured individually (accent if in ``checked_variants``,
        #     faint otherwise) so the row's per-tag selection state is
        #     visible at a glance.
        #   • The hint string (curated description + "updated X ago"
        #     annotation, assembled in llm_steps._compose_hint) —
        #     uniformly muted.
        # Either fragment is optional; line 2 is suppressed entirely
        # when both are empty so simple steps (Cold start, base port,
        # service-source pickers) stay 1-cell tall as before.
        sizes_text = self._render_size_variants()
        has_sizes = sizes_text.cell_len > 0
        has_hint = bool(self.hint)
        if not has_sizes and not has_hint:
            return line1

        body = Text()
        if has_sizes:
            body.append(sizes_text)
        if has_sizes and has_hint:
            body.append("  ·  ", style=P.TEXT_FAINT)
        if has_hint:
            body.append(self.hint, style=P.TEXT_FAINT)
        line2 = _hanging_indent(body, label_col, width)

        out = Text()
        out.append(line1)
        out.append("\n")
        out.append(line2)
        return out


# ────────────────────────────────────────────────────────────────────────────
# OptionRowWithInput — composite row for prompts with a SecondaryNumberInput
# ────────────────────────────────────────────────────────────────────────────


class _LabelOnlyRender(Widget):
    """Renders only the cursor + dot indicator + label (one line). No
    hint, no badges, no trailing padding.

    Used inside ``OptionRowWithInput`` so the horizontal top row can
    have a fixed-width label cell that aligns the textbox + badges
    columns across rows. The hint (when present) is rendered as a
    separate Static below the horizontal row so it can flow at full
    container width without being clipped by the label cell's
    constrained width — which was the bug in the previous
    ``_LabelHintRender`` design.
    """

    DEFAULT_CSS = """
    _LabelOnlyRender { height: 1; }
    """

    can_focus = False

    def __init__(self, label: str, selected: bool) -> None:
        super().__init__()
        self._label = label
        self.selected = selected

    def set_selected(self, value: bool) -> None:
        if value == self.selected:
            return
        self.selected = value
        self.refresh()

    def render(self) -> Text:
        line = Text()
        if self.selected:
            line.append(P.ARROW_RIGHT, style=f"bold {P.ACCENT}")
            line.append(" ")
            line.append(P.DOT_FILLED, style=P.ACCENT)
        else:
            line.append("  ")
            line.append(P.DOT_HOLLOW, style=P.TEXT_FAINT)
        # 4-cell inter-column gap after the dot indicator, matching
        # OptionRow's line 1 layout so eligible / ineligible rows stay
        # column-aligned.
        line.append("    ")
        label_color = P.ACCENT if self.selected else P.TEXT
        line.append(
            self._label,
            style=f"bold {label_color}" if self.selected else label_color,
        )
        return line


SECONDARY_INPUT_SLOT_WIDTH = 12  # cells reserved for the textbox column
# 7 prefix cells: 3 for cursor + dot indicator, 4 for the inter-column gap.
# Used so PromptPanel can compute ``label_width`` = 7 + max(option label
# widths) and pass it to every row in the step — eligible AND ineligible
# — so the textbox column starts at the same horizontal position on
# every row.
SECONDARY_LABEL_PREFIX_WIDTH = 7


class OptionRowWithInput(Container):
    """Composite row: ``[label + hint] [textbox-slot] [badges]`` in
    horizontal layout, used by every row in a prompt step whose
    ``secondary_number`` is set — eligible AND ineligible alike.

    The textbox slot has a FIXED width (``SECONDARY_INPUT_SLOT_WIDTH``)
    on every row so the textbox column lines up across the screen; only
    eligible rows mount a real ``Input``, the rest mount an invisible
    spacer Static of the same width. The badges Static therefore also
    starts at the same horizontal column on every row.

    All eligible-row inputs in a single prompt step share one logical
    value — ``PromptPanel`` mirrors keystrokes across siblings via its
    ``on_input_changed`` handler.
    """

    DEFAULT_CSS = """
    OptionRowWithInput {
        height: auto;
        padding: 0 1;
        layout: vertical;
    }
    OptionRowWithInput.option-selected { background: #1c2034; }

    OptionRowWithInput > Container.row-top {
        height: 1;
        layout: horizontal;
    }
    OptionRowWithInput > Container.row-top > Input.secondary-inline {
        height: 1;
        border: none;
        padding: 0 1;
        margin: 0 2 0 2;
        background: #11141f;
    }
    OptionRowWithInput > Container.row-top > Static.secondary-inline-placeholder {
        height: 1;
        margin: 0 2 0 2;
        padding: 0 1;
    }
    OptionRowWithInput > Container.row-top > Static.secondary-inline-suffix {
        width: auto;
        height: 1;
        margin: 0 2 0 0;
    }
    OptionRowWithInput > Container.row-top > Static.row-badges {
        width: auto;
        height: 1;
    }
    OptionRowWithInput > Static.row-hint {
        height: auto;
        width: 1fr;
        padding-left: 7;
    }
    """

    can_focus = False

    def __init__(
        self,
        label: str,
        *,
        hint: str = "",
        badges: list[str] | None = None,
        selected: bool = False,
        input_widget: "Input | None" = None,
        label_width: int = 0,
        unit_suffix: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.label_text = label
        self.hint = hint or ""
        self.badges = list(badges or [])
        self.selected = selected
        # ``input_widget`` is None for ineligible rows — they still
        # participate in the layout (so the badges column stays aligned)
        # but the textbox slot renders as an invisible Static spacer.
        self._input_widget = input_widget
        self._label_width = label_width
        # Short suffix rendered right after the textbox on eligible rows
        # ("workers", "port", "MB", …). Empty string skips the suffix
        # entirely. Ineligible rows never render it — they have no
        # textbox, so a dangling unit label would be confusing.
        self._unit_suffix = unit_suffix
        if input_widget is not None:
            # Tag the Input so the composite's CSS rule applies. Safe to
            # re-add — Textual de-dupes classes.
            input_widget.add_class("secondary-inline")
        if selected:
            self.add_class("option-selected")
        self._label_widget: _LabelOnlyRender | None = None

    def compose(self) -> ComposeResult:
        # Top row — fixed-width label + fixed-width textbox slot + badges.
        # Horizontal layout means widths are independent of any sibling
        # below (the hint), so each column's start position is stable
        # across rows. Class-based selector (NOT id) because each row
        # mounts its own ``row-top`` — Textual requires widget ids to
        # be globally unique within the app.
        top = Container(classes="row-top")
        with top:
            self._label_widget = _LabelOnlyRender(
                self.label_text, self.selected,
            )
            # Fixed label-cell width passed in by PromptPanel: max label
            # width across the step + cursor+dot+gap prefix. Hint length
            # is intentionally ignored here — the hint lives in its own
            # full-width row below and isn't clipped by this width.
            if self._label_width > 0:
                self._label_widget.styles.width = self._label_width
            yield self._label_widget
            if self._input_widget is not None:
                # Inline width keeps the eligible-row Input the same
                # cell width as the ineligible-row placeholder so the
                # badges column stays column-aligned across rows.
                self._input_widget.styles.width = SECONDARY_INPUT_SLOT_WIDTH
                yield self._input_widget
            else:
                placeholder = Static("", classes="secondary-inline-placeholder")
                placeholder.styles.width = SECONDARY_INPUT_SLOT_WIDTH
                yield placeholder
            # Unit suffix ("workers", "port", …) right after the textbox.
            # Rendered on eligible rows; replaced by an equal-length run
            # of spaces on ineligible rows so the badges column starts
            # at the same horizontal cell on every row in the step. A
            # visible dangling label on a row without a textbox would
            # be confusing, but the spacer keeps alignment intact.
            suffix_raw = self._unit_suffix.strip()
            if suffix_raw:
                if self._input_widget is not None:
                    suffix_widget_text = Text(suffix_raw, style=P.TEXT_FAINT)
                else:
                    suffix_widget_text = Text(" " * len(suffix_raw))
                yield Static(
                    suffix_widget_text,
                    classes="secondary-inline-suffix",
                )
            badges_text = Text()
            badges_text.append(_render_capability_block(self.badges, aligned=False))
            badges_text.append(_render_status_badges(self.badges))
            yield Static(badges_text, classes="row-badges")
        yield top
        # Second row — hint, full container width. Padding-left of 7
        # matches the label column: _LabelOnlyRender's cursor(3) + 4-cell
        # gap puts the label at column 7, and OptionRow.render indents its
        # own line-2 hint to the same computed label column.
        if self.hint:
            yield Static(
                Text(self.hint, style=P.TEXT_FAINT),
                classes="row-hint",
            )

    def set_selected(self, value: bool) -> None:
        if value == self.selected:
            return
        self.selected = value
        self.set_class(value, "option-selected")
        if self._label_widget is not None:
            self._label_widget.set_selected(value)
