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

from rich.console import Console

from ui.textual.widgets.option_row import OptionRow


def _rendered_lines(row: OptionRow, width: int) -> list[str]:
    """Return the row's VISUAL lines after wrapping at ``width``.

    ``render()`` hands back an unwrapped ``Text``; Rich wraps it when the
    widget is painted, which is where the indent is lost. Wrapping here
    through a real Rich console reproduces what the terminal shows —
    asserting on ``render().plain`` alone would pass against the bug.
    """
    class _Size:
        def __init__(self, w: int) -> None:
            self.width = w
            self.height = 2

    row._size = _Size(width)  # type: ignore[attr-defined]
    console = Console(width=width)
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
