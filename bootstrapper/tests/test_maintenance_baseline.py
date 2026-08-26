from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from radon.complexity import cc_rank, cc_visit
from radon.raw import analyze


ROOT = Path(__file__).resolve().parents[2]


def _baseline_python_files() -> list[Path]:
    files = [
        *ROOT.joinpath("bootstrapper").rglob("*.py"),
        *ROOT.joinpath("services/backend/app/app").rglob("*.py"),
    ]
    return [path for path in files if not any(part.startswith(".") for part in path.parts)]


def _function_nodes(
    source: str, path: Path
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(source, filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return (
        len(node.args.posonlyargs)
        + len(node.args.args)
        + len(node.args.kwonlyargs)
        + (node.args.vararg is not None)
        + (node.args.kwarg is not None)
    )


def _file_complexity_counts(source: str, path: Path) -> tuple[int, int, int, int, int, int]:
    """Return all symbol and module counts owned by the maintenance ledger."""
    # Radon class blocks aggregate their methods and would double-count the
    # symbol-level baseline; functions and methods are the owned units.
    blocks = [block for block in cc_visit(source) if block.letter != "C"]
    c_or_worse = sum(block.complexity >= 11 for block in blocks)
    # E-grade was asserted only against the ledger, never recomputed, so a
    # regression into the E band could not be detected at all.
    e_or_worse = sum(cc_rank(block.complexity) in ("E", "F") for block in blocks)
    function_nodes = _function_nodes(source, path)
    lengths = [
        node.end_lineno - node.lineno + 1
        for node in function_nodes
        if node.end_lineno is not None
    ]
    return (
        c_or_worse,
        sum(length > 60 for length in lengths),
        sum(length > 100 for length in lengths),
        e_or_worse,
        sum(_parameter_count(node) > 4 for node in function_nodes),
        int(analyze(source).lloc > 600),
    )


def _current_complexity_counts() -> tuple[int, int, int, int, int, int, int]:
    totals = [0, 0, 0, 0, 0, 0]
    for path in _baseline_python_files():
        counts = _file_complexity_counts(path.read_text(encoding="utf-8"), path)
        totals = [a + b for a, b in zip(totals, counts)]
    tracked_files = int(
        subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout.count(b"\0")
    )
    return (*totals, tracked_files)


def _complexity_ledger() -> dict:
    baseline = json.loads((ROOT / ".maintenance.json").read_text(encoding="utf-8"))
    return baseline["complexity"]


def _assert_complexity_signal(item: dict, source: str, path: Path) -> None:
    matches = [
        block
        for block in cc_visit(source)
        if block.letter != "C" and block.fullname == item["symbol"]
    ]
    assert len(matches) == 1, f"cannot uniquely resolve {item['symbol']} in {path}"
    assert matches[0].complexity == item["cyclomatic_complexity"]


def _assert_length_signal(item: dict, source: str, path: Path) -> None:
    matches = [
        node
        for node in _function_nodes(source, path)
        if node.name == item["symbol"]
    ]
    assert len(matches) == 1, f"cannot uniquely resolve {item['symbol']} in {path}"
    assert matches[0].end_lineno is not None
    assert (
        matches[0].end_lineno - matches[0].lineno + 1
        == item["function_effective_lines"]
    )


def _assert_accepted_signal_grounded(item: dict) -> None:
    path = ROOT / item["path"]
    assert item["rationale"].strip()
    assert path.is_file()
    source = path.read_text(encoding="utf-8")
    if "cyclomatic_complexity" in item:
        _assert_complexity_signal(item, source, path)
    else:
        assert "function_effective_lines" in item, (
            f"accepted signal has no supported metric: {item}"
        )
        _assert_length_signal(item, source, path)


def test_complexity_baseline_is_owned_and_reviewed() -> None:
    complexity = _complexity_ledger()

    assert complexity["owner"] == "Atlas maintainers"
    assert complexity["review_by"] >= "2026-09-01"
    assert "Do not increase" in complexity["regression_policy"]
    assert complexity["baseline_snapshot"]["radon_grade_e_or_worse"] == 20
    assert complexity["baseline_snapshot"]["radon_grade_c_or_worse"] == 359
    assert complexity["baseline_snapshot"]["functions_over_60_physical_lines"] == 189
    assert complexity["baseline_snapshot"]["functions_over_100_physical_lines"] == 70


def test_complexity_baseline_is_recomputed_and_regression_bounded() -> None:
    complexity = _complexity_ledger()
    current = _current_complexity_counts()
    allowed = complexity["baseline_snapshot"]
    assert current[0] <= allowed["radon_grade_c_or_worse"]
    assert current[1] <= allowed["functions_over_60_physical_lines"]
    assert current[2] <= allowed["functions_over_100_physical_lines"]
    assert current[3] <= allowed["radon_grade_e_or_worse"]
    assert current[4] <= allowed["functions_over_4_parameters"]
    assert current[5] <= allowed["modules_over_600_logical_lines"]
    assert current[6] <= allowed["tracked_files"]


def test_complexity_dispositions_are_grounded() -> None:
    complexity = _complexity_ledger()
    assert len(complexity["accepted_signal_groups"]) >= 3
    for item in complexity["accepted_signals"]:
        _assert_accepted_signal_grounded(item)


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
