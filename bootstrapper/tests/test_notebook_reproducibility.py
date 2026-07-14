import json
from pathlib import Path

from scripts.notebook_reproducibility import audit_notebooks


def _write_notebook(path: Path, *, output: bool = False, duplicate_id: bool = False) -> None:
    cells = [
        {
            "cell_type": "markdown",
            "id": "intro",
            "metadata": {},
            "source": ["# Example\n"],
        },
        {
            "cell_type": "code",
            "execution_count": 1 if output else None,
            "id": "intro" if duplicate_id else "code-cell",
            "metadata": {},
            "outputs": [{"output_type": "stream", "name": "stdout", "text": ["x\n"]}]
            if output
            else [],
            "source": ["print('x')\n"],
        },
    ]
    path.write_text(
        json.dumps(
            {
                "cells": cells,
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        ),
        encoding="utf-8",
    )


def test_notebook_audit_accepts_clean_unexecuted_notebooks(tmp_path: Path) -> None:
    _write_notebook(tmp_path / "clean.ipynb")

    assert audit_notebooks(tmp_path) == []


def test_notebook_audit_reports_outputs_counts_and_duplicate_ids(tmp_path: Path) -> None:
    _write_notebook(tmp_path / "dirty.ipynb", output=True, duplicate_id=True)

    findings = audit_notebooks(tmp_path)

    assert any("duplicate cell id" in finding for finding in findings)
    assert any("execution_count must be null" in finding for finding in findings)
    assert any("outputs must be empty" in finding for finding in findings)
