#!/usr/bin/env python3
"""Normalize hierarchical numbering in tracked Atlas documentation."""

from pathlib import Path

try:
    from scripts.docs.heading_quality import documentation_paths, renumber_markdown
except ModuleNotFoundError:  # Direct ``python scripts/...`` invocation.
    from docs.heading_quality import documentation_paths, renumber_markdown


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    changed = 0
    for path in documentation_paths(root):
        source = path.read_text(encoding="utf-8")
        rendered = renumber_markdown(source)
        if rendered != source:
            path.write_text(rendered, encoding="utf-8")
            changed += 1
    print(f"Numbered headings in {changed} Markdown files")


if __name__ == "__main__":
    main()
