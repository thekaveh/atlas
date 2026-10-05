from __future__ import annotations

import re
from collections.abc import Iterable

_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")


def _escape_angle_brackets(cell: str) -> str:
    """Escape ``<`` / ``>`` outside code spans.

    A bare ``<value>`` placeholder in a manifest description is otherwise an
    unknown HTML tag, which the browser swallows ("Authorization: Bearer .").
    """
    parts: list[str] = []
    last = 0
    for match in _CODE_SPAN_RE.finditer(cell):
        parts.append(cell[last:match.start()].replace("<", "&lt;").replace(">", "&gt;"))
        parts.append(match.group(0))
        last = match.end()
    parts.append(cell[last:].replace("<", "&lt;").replace(">", "&gt;"))
    return "".join(parts)


def csv_or_dash(values: Iterable[str]) -> str:
    clean = [str(value) for value in values if str(value)]
    return ", ".join(clean) if clean else "-"


def table(headers: list[str], rows: Iterable[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        # Collapse embedded newlines so a multi-line manifest description cannot
        # split one logical row across physical lines (invalid GFM/MkDocs), and
        # escape pipes so cell content cannot introduce spurious columns.
        cells = [
            _escape_angle_brackets(" ".join(cell.split()).replace("|", "/"))
            for cell in row
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def numbered_nav(items: list[dict], prefix: str = "") -> list[dict]:
    numbered: list[dict] = []
    for index, item in enumerate(items, start=1):
        for label, value in item.items():
            numbered_label = f"{prefix}{index}. {label}"
            if isinstance(value, list):
                numbered.append({numbered_label: numbered_nav(value, f"{prefix}{index}.")})
            else:
                numbered.append({numbered_label: value})
    return numbered
