"""LOC + complexity accounting for the #535 MVVM migration.

Committed so the before/after numbers are reproducible rather than
asserted. Run at every pass boundary:

    cd bootstrapper && uv run python scripts/loc_report.py

LOC is a weak proxy and is tracked because it was asked for. The
primary signal is complexity: a VMx refactor can legitimately increase
line count while removing branching.
"""

from __future__ import annotations

from pathlib import Path

from radon.complexity import cc_visit

LAYERS = [
    "wizard/model",
    "wizard/viewmodel",
    "wizard/view",
    "ui/textual",
    "wizard",
]


def count_layer(root: Path) -> dict[str, int]:
    """{'files', 'lines', 'max_complexity'} for one directory tree."""
    if not root.exists():
        return {"files": 0, "lines": 0, "max_complexity": 0}
    files = 0
    lines = 0
    worst = 0
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # #535 followups review, finding R8: read_text used to sit
            # outside this try, so a single non-UTF-8 .py file under a
            # scanned layer killed the whole report with an uncaught
            # UnicodeDecodeError. Skip the unreadable file entirely
            # (files/lines/complexity all uncounted for it) rather than
            # aborting — this report is a weak proxy already (see module
            # docstring), not a correctness gate.
            continue
        files += 1
        lines += len(source.splitlines())
        try:
            for block in cc_visit(source):
                worst = max(worst, block.complexity)
        except SyntaxError:
            continue
    return {"files": files, "lines": lines, "max_complexity": worst}


def format_report(bootstrapper_root: Path) -> str:
    rows = ["| layer | files | lines | worst CC |", "|---|---|---|---|"]
    for layer in LAYERS:
        stats = count_layer(bootstrapper_root / layer)
        rows.append(
            f"| {layer} | {stats['files']} | {stats['lines']} "
            f"| {stats['max_complexity']} |"
        )
    rows.append(
        "\nNote: rows overlap, they do not sum to a whole. Any row whose "
        "path is an ANCESTOR directory of another row's path double-counts "
        "that row's files — e.g. `wizard` already includes everything "
        "under `wizard/model` (and `wizard/viewmodel` once it exists). "
        "The same will apply to `ui/textual` once it starts moving under "
        "`wizard/` (`wizard/view`) in a later migration pass — check "
        "which LAYERS entries are prefixes of which others rather than "
        "assuming only the pair called out here overlaps."
    )
    return "\n".join(rows)


if __name__ == "__main__":
    print(format_report(Path(__file__).resolve().parents[1]))
