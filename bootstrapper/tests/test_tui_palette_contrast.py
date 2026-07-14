"""Readable Textual palette contracts."""

from pathlib import Path

from ui.textual import palette as P


ROOT = Path(__file__).resolve().parents[2]


def _relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92
        if value <= 0.03928
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_muted_and_faint_text_remain_readable_on_primary_background():
    assert _contrast(P.TEXT_MUTED, P.BG) >= 4.5
    assert _contrast(P.TEXT_FAINT, P.BG) >= 4.5


def test_interactive_hint_and_chip_text_do_not_use_decorative_dim_colors():
    for relative in (
        "bootstrapper/ui/textual/widgets/prompt_panel.py",
        "bootstrapper/ui/textual/widgets/multiselect_filter_chips.py",
        "bootstrapper/ui/textual/widgets/log_filter_chips.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "color: #565f89" not in source, relative
        assert "color: #3d4261" not in source, relative
    assert _contrast(P.TEXT_MUTED, P.BG_INSET) >= 4.5
