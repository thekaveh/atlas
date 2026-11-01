"""
Banner display utilities with gradient colors and responsive sizing.
"""

import shutil
from typing import List, Optional
from rich.console import Console
from rich.text import Text
from rich.align import Align

from utils import brand_logo


class BannerDisplay:
    """Handles banner display with gradient colors and terminal responsiveness."""

    def __init__(self):
        self.console = Console()



    def _brand_value(self, key: str) -> str:
        """A BRAND_* value from .env, or "" when unset or unreadable."""
        try:
            from core.config_parser import ConfigParser
            return (ConfigParser().parse_env_file().get(key, "") or "").strip()
        except Exception:  # noqa: BLE001 — banner must never break startup
            return ""

    def _brand_tagline(self) -> str:
        """BRAND_TAGLINE from .env, falling back to the manifest default that
        the wizard also uses — same rebranding contract as _brand_credits."""
        from wizard.model.state import AppState

        return self._brand_value("BRAND_TAGLINE") or AppState.tagline

    def _brand_name(self) -> str:
        """BRAND_NAME from .env, falling back to the manifest default."""
        from wizard.model.state import AppState

        return self._brand_value("BRAND_NAME") or AppState.brand_name

    def _brand_credits(self) -> tuple[str, str, str]:
        """(author, repo_url, license) for the credit lines.

        Honors the same BRAND_* rebranding knobs the Textual wizard reads
        (forks rebrand via .env) — the linear --no-tui banner used to
        hardcode the upstream credits. Best-effort: falls back to the
        upstream defaults when .env is absent/unreadable.
        """
        defaults = (
            "Developed by Kaveh Razavi",
            "https://github.com/thekaveh/atlas",
            "Apache License 2.0",
        )
        try:
            from core.config_parser import ConfigParser
            env = ConfigParser().parse_env_file()
        except Exception:  # noqa: BLE001 — banner must never break startup
            return defaults
        author = (env.get("BRAND_AUTHOR", "") or "").strip()
        return (
            f"Developed by {author}" if author else defaults[0],
            (env.get("BRAND_REPO_URL", "") or "").strip() or defaults[1],
            (env.get("BRAND_LICENSE", "") or "").strip() or defaults[2],
        )

    def get_terminal_width(self) -> int:
        """
        Get the current terminal width.

        Returns:
            int: Terminal width in characters
        """
        return shutil.get_terminal_size().columns

    def apply_enhanced_gradient(
        self, text: str, gradient_colors: Optional[List[str]] = None
    ) -> Text:
        """
        Apply rich multi-color gradient to text.
        Uses a 256-color palette.

        Args:
            text: Text to apply gradient to
            gradient_colors: List of color codes for the gradient

        Returns:
            rich.Text: Text with gradient applied
        """
        if gradient_colors is None:
            # Rich blue hue palette based on the original Bash script
            # 256-color codes matching the Bash implementation
            gradient_colors = [
                "color(17)",  # Dark Navy Blue
                "color(18)",  # Dark Blue
                "color(19)",  # Medium Dark Blue
                "color(20)",  # Royal Blue
                "color(21)",  # Bright Blue
                "color(26)",  # Blue-Cyan
                "color(27)",  # Cyan-Blue
                "color(33)",  # Bright Cyan-Blue
                "color(39)",  # Light Cyan-Blue
                "color(45)",  # Cyan
                "color(51)",  # Bright Cyan
                "color(87)",  # Light Blue-Cyan
                "color(123)",  # Light Blue
                "color(159)",  # Very Light Blue
                "color(195)",  # Pale Blue
            ]

        rich_text = Text()
        text_length = len(text)

        if text_length == 0:
            return rich_text

        # Enhanced gradient algorithm matching the original Bash implementation
        for i, char in enumerate(text):
            # Calculate color index with smoother gradient distribution
            # This matches the original algorithm: $((i * palette_size / length))
            color_index = int((i * len(gradient_colors)) // text_length)
            if color_index >= len(gradient_colors):
                color_index = len(gradient_colors) - 1

            color = gradient_colors[color_index]
            rich_text.append(char, style=f"bold {color}")

        return rich_text

    def get_ascii_art_full(self) -> List[str]:
        """
        Get the full ASCII-art banner (built-in ATLAS, or a BRAND_LOGO_FILE
        override — same source as the Textual brand panel, via
        ``utils.brand_logo``).

        Returns the wide 6-row lockup when the terminal is at least its width
        threshold; otherwise falls back to the narrow (compact) lockup. Both
        variants are 6 rows tall; the 15-color horizontal gradient
        (color(17) → color(195)) is applied per-character at render time, so
        each row reads as bright-left → pale-right.

        Returns:
            list: List of ASCII-art lines (6 entries)
        """
        full, compact, threshold = brand_logo.resolve_from_env()
        if self.get_terminal_width() >= threshold:
            return full
        return compact

    def get_ascii_art_compact(self) -> List[str]:
        """
        Get the compact ASCII art banner for narrow terminals. The box shows
        BRAND_NAME, truncated to fit; the default renders "Atlas" as before.

        Returns:
            list: List of compact ASCII art lines
        """
        name = self._brand_name()[:33]
        left = max(1, (35 - len(name)) // 2 - 1)
        return [
            "╔═══════════════════════════════════╗",
            "║" + " " * left + name + " " * (35 - left - len(name)) + "║",
            "║       Engineering Platform        ║",
            "╚═══════════════════════════════════╝",
        ]

    def show_hero(self, no_splash: bool = False) -> bool:
        """Print the pre-rendered Atlas hero (linear/--no-tui path). Returns
        whether anything was printed."""
        from feature_flags import splash_enabled
        if not splash_enabled():
            return False
        if no_splash:
            return False
        width = self.get_terminal_width()
        if width < 80:
            return False
        from ui.textual.widgets.atlas_hero import load_hero, hero_rows
        data = load_hero(width)
        if not data:
            return False
        self.console.print()
        for t in hero_rows(data):
            self.console.print(Align.center(t))
        self.console.print()
        return True

    def show_banner(self, force_compact: bool = False) -> None:
        """
        Display the appropriate banner based on terminal width.

        Args:
            force_compact: Force compact banner regardless of terminal width
        """
        terminal_width = self.get_terminal_width()

        # Use compact banner for narrow terminals (< 70 chars) or when forced (matches original Bash script)
        if terminal_width < 70 or force_compact:
            self.show_compact_banner()
        else:
            self.show_full_banner()

    def show_full_banner(self) -> None:
        """
        Display the full branded ASCII banner with gradient colors and centering.
        """
        ascii_art = self.get_ascii_art_full()

        self.console.print()  # Empty line

        for line in ascii_art:
            # Apply gradient and center the line
            gradient_text = self.apply_enhanced_gradient(line)
            centered_line = Align.center(gradient_text)
            self.console.print(centered_line)

        # Add tagline — honors BRAND_TAGLINE like the credits below.
        tagline = self._brand_tagline()
        tagline_text = Text(tagline, style="bold cyan")
        self.console.print(Align.center(tagline_text))

        # Add credit information matching original Bash script
        self.console.print()  # Empty line

        # Credit information with colors matching the original (\e[1;94m = bright blue, \e[1;96m = bright cyan, \e[1;93m = bright yellow)
        author, repo_url, license_name = self._brand_credits()
        credit_text = Text(author, style="bold bright_blue")
        self.console.print(Align.center(credit_text))

        url_text = Text(repo_url, style="bold bright_cyan")
        self.console.print(Align.center(url_text))

        license_text = Text(license_name, style="bold bright_yellow")
        self.console.print(Align.center(license_text))

        self.console.print()  # Empty line

    def show_compact_banner(self) -> None:
        """
        Display a compact banner for narrow terminals.
        """
        ascii_art = self.get_ascii_art_compact()

        self.console.print()  # Empty line

        for line in ascii_art:
            # Apply gradient and center the line - using 256-color compact palette
            compact_gradient = [
                "color(51)",
                "color(27)",
                "color(21)",
                "color(19)",
                "color(129)",
            ]  # cyan to blue to magenta
            gradient_text = self.apply_enhanced_gradient(line, compact_gradient)
            centered_line = Align.center(gradient_text)
            self.console.print(centered_line)

        # Add compact tagline (brand-aware; truncated for narrow terms)
        tagline = self._brand_tagline()[:40]
        tagline_text = Text(tagline, style="bold cyan")
        self.console.print(Align.center(tagline_text))

        # Add credit information for compact banner (same as full banner)
        self.console.print()  # Empty line

        author, repo_url, license_name = self._brand_credits()
        credit_text = Text(author, style="bold bright_blue")
        self.console.print(Align.center(credit_text))

        url_text = Text(repo_url, style="bold bright_cyan")
        self.console.print(Align.center(url_text))

        license_text = Text(license_name, style="bold bright_yellow")
        self.console.print(Align.center(license_text))

        self.console.print()  # Empty line

    def show_section_header(self, title: str, icon: str = "") -> None:
        """
        Display a section header with consistent formatting.

        Args:
            title: Section title
            icon: Optional prefix text shown before the title (empty by default)
        """
        prefix = f"{icon} " if icon else ""
        header_text = Text(f"{prefix}{title}", style="bold bright_white")
        self.console.print(header_text)

    def show_status_message(self, message: str, status: str = "info") -> None:
        """
        Display a status message with appropriate styling.

        Args:
            message: Message to display
            status: Status type ("info", "success", "warning", "error")
        """
        labels = {"info": "[INFO]", "success": "[OK]", "warning": "[WARN]", "error": "[ERROR]"}

        colors = {
            "info": "bright_white",
            "success": "bright_green",
            "warning": "bright_yellow",
            "error": "bright_red",
        }

        label = labels.get(status, "[INFO]")
        color = colors.get(status, "bright_white")

        status_text = Text(f"{label} {message}", style=color)
        self.console.print(status_text)
