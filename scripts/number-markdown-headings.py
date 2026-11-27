#!/usr/bin/env python3
"""Normalize hierarchical numbering in tracked Atlas documentation."""

import sys
from pathlib import Path

try:
    from scripts.docs.heading_quality import documentation_paths, renumber_markdown
except ModuleNotFoundError:  # Direct ``python scripts/...`` invocation.
    from docs.heading_quality import documentation_paths, renumber_markdown


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    changed = 0
    failures: list[str] = []
    for path in documentation_paths(root):
        source = path.read_text(encoding="utf-8")
        # One file with an unrenumberable heading must not stop the batch.
        try:
            rendered = renumber_markdown(source)
        except ValueError as exc:
            failures.append(f"{path.relative_to(root).as_posix()}: {exc}")
            continue
        if rendered != source:
            path.write_text(rendered, encoding="utf-8")
            changed += 1
    print(f"Numbered headings in {changed} Markdown files")
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
