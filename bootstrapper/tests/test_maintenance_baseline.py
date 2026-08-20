from __future__ import annotations

import ast
import json
from pathlib import Path

from radon.complexity import cc_visit


ROOT = Path(__file__).resolve().parents[2]


def _baseline_python_files() -> list[Path]:
    files = [
        *ROOT.joinpath("bootstrapper").rglob("*.py"),
        *ROOT.joinpath("services/backend/app/app").rglob("*.py"),
    ]
    return [path for path in files if not any(part.startswith(".") for part in path.parts)]


def _current_complexity_counts() -> tuple[int, int, int]:
    radon_c_or_worse = 0
    over_60 = 0
    over_100 = 0
    for path in _baseline_python_files():
        source = path.read_text(encoding="utf-8")
        # Radon class blocks aggregate their methods and would double-count
        # the symbol-level baseline; functions and methods are the owned units.
        radon_c_or_worse += sum(
            block.letter != "C" and block.complexity >= 11
            for block in cc_visit(source)
        )
        tree = ast.parse(source, filename=str(path))
        lengths = [
            node.end_lineno - node.lineno + 1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.end_lineno is not None
        ]
        over_60 += sum(length > 60 for length in lengths)
        over_100 += sum(length > 100 for length in lengths)
    return radon_c_or_worse, over_60, over_100


def _complexity_ledger() -> dict:
    baseline = json.loads((ROOT / ".maintenance.json").read_text(encoding="utf-8"))
    return baseline["complexity"]


def test_complexity_baseline_is_owned_and_reviewed() -> None:
    complexity = _complexity_ledger()

    assert complexity["owner"] == "Atlas maintainers"
    assert complexity["review_by"] >= "2026-09-01"
    assert "Do not increase" in complexity["regression_policy"]
    assert complexity["baseline_snapshot"]["radon_grade_e_or_worse"] == 20
    assert complexity["baseline_snapshot"]["radon_grade_c_or_worse"] == 357
    assert complexity["baseline_snapshot"]["functions_over_60_physical_lines"] == 188
    assert complexity["baseline_snapshot"]["functions_over_100_physical_lines"] == 69


def test_complexity_baseline_is_recomputed_and_regression_bounded() -> None:
    complexity = _complexity_ledger()
    current = _current_complexity_counts()
    allowed = complexity["baseline_snapshot"]
    assert current[0] <= allowed["radon_grade_c_or_worse"]
    assert current[1] <= allowed["functions_over_60_physical_lines"]
    assert current[2] <= allowed["functions_over_100_physical_lines"]


def test_complexity_dispositions_are_grounded() -> None:
    complexity = _complexity_ledger()
    assert len(complexity["accepted_signal_groups"]) >= 3
    for item in complexity["accepted_signals"]:
        assert item["rationale"].strip()
        assert (ROOT / item["path"]).is_file()


def test_confirmed_dead_private_helpers_remain_removed() -> None:
    wizard = (
        ROOT
        / "bootstrapper"
        / "ui"
        / "textual"
        / "screens"
        / "wizard_screen.py"
    ).read_text(encoding="utf-8")
    validator = (
        ROOT / "bootstrapper" / "services" / "source_validator.py"
    ).read_text(encoding="utf-8")

    assert "def _run_command(" not in wizard
    assert "def suggest_valid_source(" not in validator
    assert "def prune_system(" not in (
        ROOT / "bootstrapper" / "core" / "docker_manager.py"
    ).read_text(encoding="utf-8")
