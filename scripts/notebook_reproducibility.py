"""Fast source-hygiene checks for committed Jupyter notebooks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _finding(path: Path, root: Path, message: str) -> str:
    return f"{path.relative_to(root).as_posix()}: {message}"


def audit_notebooks(root: Path) -> list[str]:
    findings: list[str] = []
    notebooks = sorted(root.rglob("*.ipynb"))
    if not notebooks:
        return [f"{root}: no notebooks found"]
    for path in notebooks:
        try:
            notebook: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(_finding(path, root, f"invalid notebook JSON: {exc}"))
            continue
        if not isinstance(notebook, dict):
            findings.append(_finding(path, root, "notebook root must be an object"))
            continue
        if notebook.get("nbformat") != 4 or notebook.get("nbformat_minor", 0) < 5:
            findings.append(_finding(path, root, "nbformat must be 4.5 or newer"))
        cells = notebook.get("cells")
        if not isinstance(cells, list):
            findings.append(_finding(path, root, "cells must be a list"))
            continue
        seen_ids: set[str] = set()
        for index, cell in enumerate(cells):
            if not isinstance(cell, dict):
                findings.append(_finding(path, root, f"cell {index} must be an object"))
                continue
            cell_id = cell.get("id")
            if not isinstance(cell_id, str) or not cell_id.strip():
                findings.append(_finding(path, root, f"cell {index} has no stable id"))
            elif cell_id in seen_ids:
                findings.append(_finding(path, root, f"cell {index} has duplicate cell id {cell_id!r}"))
            else:
                seen_ids.add(cell_id)
            if cell.get("cell_type") != "code":
                continue
            if cell.get("execution_count") is not None:
                findings.append(_finding(path, root, f"cell {index} execution_count must be null"))
            if cell.get("outputs", []) != []:
                findings.append(_finding(path, root, f"cell {index} outputs must be empty"))
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit committed notebook source hygiene")
    parser.add_argument("--root", default="services/jupyterhub/build/notebooks")
    args = parser.parse_args()
    root = Path(args.root)
    findings = audit_notebooks(root)
    if findings:
        print("\n".join(findings))
        raise SystemExit(1)
    count = len(list(root.rglob("*.ipynb")))
    print(f"PASS {count} notebook sources have stable metadata and no committed outputs")


if __name__ == "__main__":
    main()
