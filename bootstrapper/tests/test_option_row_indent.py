"""Wrapped option hints must stay inside their hanging indent.

``OptionRow.render`` builds line 2 as a single ``Text`` that STARTS with
``" " * label_col``. Rich treats that as one logical line, so only the
first visual row carries the indent — every wrapped continuation restarts
at column 0 and reads as if it had spilled out of the row.

It is worst exactly where a user meets it first: the track picker, whose
hints enumerate the entire service list of a track (~300 characters), so
the very first prompt of the wizard is the one that looks broken.

``OptionRowWithInput`` in the same module already solves this by putting
the hint in its own ``Static`` with a ``padding-left``, so the codebase
disagreed with itself. These tests pin the plain ``OptionRow`` to the
same contract.
"""
from __future__ import annotations

from unittest.mock import PropertyMock, patch

from rich.console import Console
from textual.geometry import Size

from ui.textual.widgets.option_row import OptionRow


def _rendered_lines(row: OptionRow, width: int) -> list[str]:
    """Return the row's VISUAL lines after wrapping at ``width``.

    ``render()`` hands back an unwrapped ``Text``; Rich wraps it when the
    widget is painted, which is where the indent is lost. Wrapping here
    through a real Rich console reproduces what the terminal shows —
    asserting on ``render().plain`` alone would pass against the bug.

    ``Widget.size`` reads the content region, not ``_size``, so the width
    is patched on the property itself. Without that, ``render()`` falls
    back to 60 cells and never reaches the wide aligned layout.
    """
    size = PropertyMock(return_value=Size(width, 2))
    console = Console(width=width)
    with patch.object(OptionRow, "size", new_callable=lambda: size):
        return [line.plain for line in row.render().wrap(console, width)]


def test_a_long_hint_keeps_every_continuation_line_indented() -> None:
    row = OptionRow("Generative AI · RAG", hint="alpha bravo charlie delta " * 12)
    lines = _rendered_lines(row, 60)

    assert len(lines) >= 3, (
        "the hint must actually wrap for this test to mean anything; "
        f"got {len(lines)} line(s)"
    )
    indent = len(lines[1]) - len(lines[1].lstrip())
    assert indent > 0, "line 2 should be indented under the label"
    for extra in lines[2:]:
        assert extra.startswith(" " * indent), (
            f"continuation line escaped the hanging indent: {extra[:24]!r}"
        )


def test_a_short_hint_still_renders_exactly_two_lines() -> None:
    """The wrap fix must not add lines to hints that already fit."""
    row = OptionRow("Short", hint="fits easily")
    lines = _rendered_lines(row, 80)

    assert len(lines) == 2, f"expected label + hint, got {len(lines)}: {lines!r}"
    assert "fits easily" in lines[1]


def test_a_row_without_a_hint_stays_one_line() -> None:
    """Simple steps (cold start, base port, source pickers) stay 1 cell tall."""
    row = OptionRow("No hint here")
    lines = _rendered_lines(row, 80)

    assert len(lines) == 1, f"expected a single line, got {lines!r}"


def test_the_hint_text_survives_wrapping_intact() -> None:
    """Wrapping must not drop or duplicate words."""
    words = [f"w{i}" for i in range(40)]
    row = OptionRow("Label", hint=" ".join(words))
    lines = _rendered_lines(row, 50)

    joined = " ".join(line.strip() for line in lines[1:])
    assert joined.split() == words


def test_a_wide_single_select_row_keeps_its_badges_on_line_one() -> None:
    """A long source label must not push a status badge onto its own line.

    At 100+ cells the row used to reserve the full capability-column block
    even with no capability tag, so ``[disabled]`` wrapped to column 0.
    """
    row = OptionRow(
        "No backup service (default).",
        hint="Skip this service entirely",
        badges=["rec.", "disabled"],
    )
    for width in (100, 108, 128):
        lines = _rendered_lines(row, width)
        assert len(lines) == 2, f"width {width}: expected label + hint, got {lines!r}"
        assert "[rec.]" in lines[0] and "[disabled]" in lines[0], lines


def test_a_parent_that_cannot_fit_the_columns_keeps_its_pull_count() -> None:
    """The inline fallback must engage when the aligned line would overflow."""
    row = OptionRow(
        "gemma3",
        badges=["library", "vision"],
        multi=True,
        sizes=("1b", "4b"),
        pulls=28_600_000,
        tag_start_col_override=38,
    )
    lines = _rendered_lines(row, 107)

    assert len(lines) == 2, f"expected parent + sizes, got {lines!r}"
    assert "28.6M" in lines[0], f"pull count wrapped off line 1: {lines!r}"


def test_a_parent_that_fits_keeps_its_canonical_tag_column() -> None:
    """Rows wide enough for the aligned layout render exactly as before."""
    row = OptionRow(
        "gemma3",
        badges=["library", "vision"],
        multi=True,
        sizes=("1b", "4b"),
        pulls=28_600_000,
        tag_start_col_override=38,
    )
    lines = _rendered_lines(row, 127)

    assert len(lines) == 2, lines
    assert lines[0].index("[vision]") == 63, lines[0]
    assert "28.6M" in lines[0], lines[0]


def test_a_fitting_single_select_row_keeps_its_badge_column() -> None:
    row = OptionRow("Container (GPU)", hint="h", badges=["rec.", "GPU"])
    lines = _rendered_lines(row, 108)

    assert len(lines) == 2, lines
    assert lines[0].index("[rec.]") == 90, lines[0]


def test_a_leaf_that_cannot_fit_the_columns_stays_one_line() -> None:
    row = OptionRow(
        "qwen3.6:8b",
        badges=["thinking", "tools", "pulled"],
        multi=True,
        is_leaf=True,
        leaf_size_label="(8b · 4.8GB)",
    )
    for width in (130, 131):
        lines = _rendered_lines(row, width)
        assert len(lines) == 1, f"width {width}: leaf wrapped: {lines!r}"
        assert "[pulled]" in lines[0], lines


def test_a_leaf_that_fits_keeps_its_canonical_tag_column() -> None:
    row = OptionRow(
        "qwen3.6:8b",
        badges=["thinking", "tools", "pulled"],
        multi=True,
        is_leaf=True,
        leaf_size_label="(8b · 4.8GB)",
    )
    lines = _rendered_lines(row, 140)

    assert len(lines) == 1, lines
    assert lines[0].index("[thinking]") == 77, lines[0]
