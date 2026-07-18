from __future__ import annotations

from collections.abc import Iterable


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
        cells = [" ".join(cell.split()).replace("|", "/") for cell in row]
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
