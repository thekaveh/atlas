"""LOC + complexity accounting for the #535 MVVM migration.

Committed so the before/after numbers are reproducible rather than
asserted. Run at every pass boundary:

    cd bootstrapper && uv run python scripts/loc_report.py

LOC is a weak proxy and is tracked because it was asked for. The
primary signal is complexity: a VMx refactor can legitimately increase
line count while removing branching.
"""

from __future__ import annotations

import sys
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
        source = path.read_text(encoding="utf-8")
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
    return "\n".join(rows)


if __name__ == "__main__":
    print(format_report(Path(__file__).resolve().parents[1]))
    sys.exit(0)
