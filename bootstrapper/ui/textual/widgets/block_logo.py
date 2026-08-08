"""
BlockLogo — 6-row block-art lockup with vertical gradient.

Each of the 6 art rows is painted in one solid color from the
``_GRADIENT`` (bright blue at the top, deep navy at the bottom). Since
every letter spans the same 6 rows, each letter naturally inherits the
same vertical bright→dark fade.

Two lockup variants are defined; ``render()`` picks at runtime based on
``shutil.get_terminal_size()``:
  * ``_LOGO_ROWS_FULL`` — "ATLAS-PLATFORM", 118 cells wide. Used when
    the terminal is wide enough.
  * ``_LOGO_ROWS_COMPACT`` — "ATLAS" only, 41 cells wide. Fallback for
    terminals narrower than ``_WIDTH_THRESHOLD`` columns so the
    lockup never clips on the right edge.

Total height: 7 cells (1 spacer + 6 art). No bottom spacer — the
panel below it (InfoPanel) provides the breathing room.
"""

from __future__ import annotations

import shutil

from rich.align import Align
from rich.console import Group, RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widget import Widget

from utils import brand_logo


# The block-art rows + width threshold live in ``utils.brand_logo`` so this
# Textual brand panel and the --no-tui banner share ONE source, and a fork can
# override the lockup via ``BRAND_LOGO_FILE`` (see brand_logo). These
# module-level names remain the built-in ATLAS default — re-exported for
# back-compat with ``scripts/generate_logo.py`` (the image splash, which stays
# ATLAS) and the parity tests. ``render()`` resolves the active, possibly
# brand-overridden, art at runtime.
_LOGO_ROWS_FULL: list[str] = brand_logo.ATLAS_FULL
_LOGO_ROWS_COMPACT: list[str] = brand_logo.ATLAS_COMPACT
_WIDTH_THRESHOLD = brand_logo.width_threshold(brand_logo.ATLAS_FULL)


# Width of one fully-rendered row of the full lockup. Used by tests
# that compare canvas dimensions.
_ROW_WIDTH = len(_LOGO_ROWS_FULL[0])


# Vertical-gradient color stops (top → bottom). Top is the brightest.
# Every letter inherits this same fade because each row is painted in
# one solid color and every letter spans the same 6 rows.
_GRADIENT = [
    "#74A6F4",
    "#4F8AED",
    "#316DDF",
    "#1F4FBE",
    "#14338B",
    "#0A1A55",
]


class BlockLogo(Widget):
    """Single-line block-art lockup with vertical gradient, horizontally centered.

    Renders ``_LOGO_ROWS_FULL`` (ATLAS-PLATFORM, 118 cells) when the
    terminal is at least ``_WIDTH_THRESHOLD`` columns wide; otherwise
    falls back to ``_LOGO_ROWS_COMPACT`` (ATLAS, 41 cells).
    """

    DEFAULT_CSS = """
    BlockLogo {
        height: 7;
        width: 100%;
        background: #0e0f18;
    }
    """

    can_focus = False

    _art: tuple[list[str], list[str], int] | None = None

    def render(self) -> RenderableType:
        # 1 row top spacer + 6 art rows = 7 cells. No bottom spacer.
        # The active lockup (built-in ATLAS or a BRAND_LOGO_FILE override) is
        # resolved once and cached so .env is read a single time, not on every
        # refresh/resize. shutil.get_terminal_size() queries the OS-level
        # terminal dimensions, which closely match the widget's available width
        # since the BrandPanel is sized at 100%.
        if self._art is None:
            self._art = brand_logo.resolve_from_env()
        full, compact, threshold = self._art
        rows = (
            full
            if shutil.get_terminal_size().columns >= threshold
            else compact
        )
        renderables: list[RenderableType] = [Text("")]
        for i, row_str in enumerate(rows):
            color = _GRADIENT[i] if i < len(_GRADIENT) else _GRADIENT[-1]
            renderables.append(Align.center(Text(row_str, style=color)))
        return Group(*renderables)


class BrandPanel(Container):
    """Bordered, titled box wrapping the BlockLogo with brand metadata
    on the top + bottom borders.

    Top-left title: tagline (e.g. "Self-hosted Engineering Platform").
    Bottom subtitle (left-aligned): Setup/Logs tabs and byline.
    """

    DEFAULT_CSS = """
    BrandPanel {
        height: 9;
        width: 100%;
        border: round #2b2f4a;
        background: #0e0f18;
        padding: 0;
        border-title-align: left;
        border-subtitle-align: left;
    }
    BrandPanel > BlockLogo {
        height: 7;
        background: transparent;
    }
    """

    can_focus = False

    TAB_SETUP = "setup"
    TAB_LOGS = "logs"
    _TAB_LABELS = ((TAB_SETUP, "Setup"), (TAB_LOGS, "Logs"))

    def __init__(
        self,
        *,
        tagline: str = "Self-hosted Engineering Platform",
        author: str = "",
        author_email: str = "",
        license: str = "",  # noqa: A002 - matches BrandInfo field name
        version: str = "",
        repo: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.tagline = tagline
        self.author = author
        self.author_email = author_email
        self.license = license
        self.version = version
        self.repo = repo
        self._active_tab = self.TAB_SETUP
        self._tabs_enabled = False
        self._tab_spans: dict[str, tuple[int, int]] = {}

    def compose(self) -> ComposeResult:
        yield BlockLogo()

    def _byline(self) -> str:
        parts: list[str] = []
        if self.author:
            who = f"by {self.author}"
            if self.author_email:
                who = f"{who} <{self.author_email}>"
            parts.append(who)
        if self.license:
            parts.append(self.license)
        if self.version:
            v = self.version if self.version.startswith("v") else f"v{self.version}"
            parts.append(v)
        if self.repo:
            parts.append(self.repo)
        return " " + "  ·  ".join(parts) + " " if parts else ""

    def _tab_segment(self) -> str:
        """Left-hand tab labels. Brackets are ESCAPED: Textual consumes a bare
        "[" in a border subtitle as console markup and the label vanishes.

        Returns empty string if tabs are not enabled, preserving the original
        byline-only mode when set_tabs() has not been called.
        """
        if not self._tabs_enabled:
            self._tab_spans = {}
            return ""

        out = " "
        self._tab_spans = {}
        # Track position in rendered output (after escapes are resolved to displayed chars).
        # Each \[ renders as [ (1 char), so rendered_pos grows by bracket_len + space.
        rendered_pos = len(out)

        for tab_id, label in self._TAB_LABELS:
            marker = "▸" if tab_id == self._active_tab else " "
            # Measure just the bracket content (without the trailing space).
            bracket_content_len = len(f"[{marker} {label} ]")

            start = rendered_pos
            end = rendered_pos + bracket_content_len
            self._tab_spans[tab_id] = (start, end)

            # Add the full tab string with trailing space to the raw output.
            out += rf"\[{marker} {label} ] "
            # Advance rendered position by bracket length + space.
            rendered_pos += bracket_content_len + 1

        return out

    def tab_spans(self) -> dict[str, tuple[int, int]]:
        """Half-open column ranges of each tab label, for click routing."""
        return dict(self._tab_spans)

    def set_tabs(self, active: str, *, enabled: bool = True) -> None:
        self._active_tab = active
        self._tabs_enabled = enabled
        self._render_border()

    def _render_border(self) -> None:
        if self.tagline:
            self.border_title = f" {self.tagline} "

        if not self._tabs_enabled:
            # Byline-only path (original behavior): use right-alignment and let Textual fill dashes.
            # This avoids the implicit-gap artifact that occurs when left-aligned padding starts
            # with dashes (╰─ gap ───... would appear). Instead: ╰────── byline.
            self.styles.border_subtitle_align = "right"
            self.border_subtitle = self._byline()
        else:
            # Tabs-enabled path: left-aligned with manual padding to position tabs+byline.
            # The implicit gap after the corner is unavoidable with left-alignment;
            # tabs and their separator account for it via border_offset.
            self.styles.border_subtitle_align = "left"
            left = self._tab_segment()
            right = self._byline()
            # Available width for border content, accounting for:
            # - 2 chars: left (╰) and right (╯) border corners
            # - ~2 chars: Textual's implicit gap-padding around the border_subtitle
            inner = max(0, self.size.width - 4)
            pad = max(1, inner - len(left) - len(right))
            self.border_subtitle = left + "─" * pad + right

            # Adjust tab spans to account for the border rendering offset.
            # Textual renders the border_subtitle on the bottom border as:
            #   ╰─ [implicit-gap] border_subtitle_content ─╯
            # The offset (3) = corner (╰) + line (─) + implicit-gap-left.
            # This constant depends on CSS: border: round; padding: 0; border-subtitle-align: left.
            # If CSS changes (border style, padding, alignment), this offset may need adjustment.
            if self._tab_spans:
                border_offset = 3
                adjusted_spans = {}
                for tab_id, (start, end) in self._tab_spans.items():
                    adjusted_spans[tab_id] = (start + border_offset, end + border_offset)
                self._tab_spans = adjusted_spans

    def on_mount(self) -> None:
        self._render_border()

    def on_resize(self) -> None:
        self._render_border()
