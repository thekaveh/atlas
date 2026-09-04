from __future__ import annotations

import ast
from copy import deepcopy
from datetime import date, timedelta
import json
import subprocess
from pathlib import Path

import pytest
from radon.complexity import cc_rank, cc_visit
from radon.raw import analyze


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_BASELINE_SNAPSHOT = {
    "radon_grade_c_or_worse": 428,
    "radon_grade_e_or_worse": 23,
    "functions_over_60_physical_lines": 252,
    "functions_over_100_physical_lines": 85,
    "functions_over_4_parameters": 156,
    "modules_over_600_logical_lines": 21,
    "tracked_files": 1545,
    "v0.1.0_tracked_files": 667,
}
EXPECTED_EXTENDED_PYTHON_SNAPSHOT = {
    "radon_grade_c_or_worse": 62,
    "radon_grade_e_or_worse": 3,
    "functions_over_60_physical_lines": 30,
    "functions_over_100_physical_lines": 9,
    "functions_over_4_parameters": 40,
    "modules_over_600_logical_lines": 1,
}
_COMPLEXITY_METRICS = (
    "radon_grade_c_or_worse",
    "functions_over_60_physical_lines",
    "functions_over_100_physical_lines",
    "radon_grade_e_or_worse",
    "functions_over_4_parameters",
    "modules_over_600_logical_lines",
)


def _baseline_python_files() -> list[Path]:
    files = [
        *ROOT.joinpath("bootstrapper").rglob("*.py"),
        *ROOT.joinpath("services/backend/app/app").rglob("*.py"),
    ]
    return [path for path in files if not any(part.startswith(".") for part in path.parts)]


def _extended_python_files() -> list[Path]:
    """Cover every other tracked Python surface without raising core ceilings."""
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    core = {path.resolve() for path in _baseline_python_files()}
    return [
        ROOT / relative
        for relative in tracked
        if (ROOT / relative).resolve() not in core
        and not any(part.startswith(".") for part in Path(relative).parts)
    ]


def _all_maintained_python_files() -> list[Path]:
    return [*_baseline_python_files(), *_extended_python_files()]


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
    # Include untracked, non-ignored additions so a dirty maintenance worktree
    # cannot appear below the ceiling only to exceed it after the next commit.
    worktree_files = int(
        subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout.count(b"\0")
    )
    return (*totals, worktree_files)


def _complexity_counts(paths: list[Path]) -> tuple[int, int, int, int, int, int]:
    totals = [0, 0, 0, 0, 0, 0]
    for path in paths:
        counts = _file_complexity_counts(path.read_text(encoding="utf-8"), path)
        totals = [current + count for current, count in zip(totals, counts)]
    return tuple(totals)


def _complexity_ledger() -> dict:
    baseline = json.loads((ROOT / ".maintenance.json").read_text(encoding="utf-8"))
    return baseline["complexity"]


def _e_or_worse_symbols(paths: list[Path]) -> set[tuple[str, str, int]]:
    symbols: set[tuple[str, str, int]] = set()
    for path in paths:
        for block in cc_visit(path.read_text(encoding="utf-8")):
            if block.letter != "C" and cc_rank(block.complexity) in ("E", "F"):
                symbols.add(
                    (path.relative_to(ROOT).as_posix(), block.fullname, block.complexity)
                )
    return symbols


def _reviewed_e_or_worse_symbols(complexity: dict) -> set[tuple[str, str, int]]:
    reviewed: set[tuple[str, str, int]] = set()
    for item in complexity["accepted_e_or_worse_symbols"]:
        assert item["rationale"].strip()
        path = ROOT / item["path"]
        assert path.is_file()
        _assert_complexity_signal(item, path.read_text(encoding="utf-8"), path)
        identity = (item["path"], item["symbol"], item["cyclomatic_complexity"])
        assert identity not in reviewed, f"duplicate E/F disposition: {identity}"
        reviewed.add(identity)
    return reviewed


def _assert_e_or_worse_symbols_reviewed(complexity: dict) -> None:
    current = _e_or_worse_symbols(_all_maintained_python_files())
    assert current == _reviewed_e_or_worse_symbols(complexity)


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


def _assert_complexity_baseline_owned(
    complexity: dict, *, today: date | None = None
) -> None:
    # A today-valued default binds once at import, so a suite running across UTC
    # midnight compares a stale "today" with fixtures built from the current one
    # and the expiry boundary stops rejecting.
    if today is None:
        today = date.today()
    assert complexity["owner"] == "Atlas maintainers"
    assert date.fromisoformat(complexity["review_by"]) >= today
    regression_policy = complexity["regression_policy"]
    assert "Do not increase" in regression_policy
    assert "reviewed commit-range attribution" in regression_policy
    assert "every new Radon E/F symbol" in regression_policy
    assert complexity["baseline_snapshot"] == EXPECTED_BASELINE_SNAPSHOT
    assert (
        complexity["extended_python_baseline_snapshot"]
        == EXPECTED_EXTENDED_PYTHON_SNAPSHOT
    )


def _assert_counts_bounded(counts: tuple[int, ...], allowed: dict) -> None:
    for count, metric in zip(counts, _COMPLEXITY_METRICS):
        assert count <= allowed[metric], f"{metric}: {count} > {allowed[metric]}"


def test_complexity_baseline_is_owned_and_reviewed() -> None:
    _assert_complexity_baseline_owned(_complexity_ledger())


def test_every_maintenance_ceiling_is_immutable_without_review() -> None:
    original = _complexity_ledger()
    for key in EXPECTED_BASELINE_SNAPSHOT:
        mutated = deepcopy(original)
        mutated["baseline_snapshot"][key] += 1
        with pytest.raises(AssertionError):
            _assert_complexity_baseline_owned(mutated)
    for key in EXPECTED_EXTENDED_PYTHON_SNAPSHOT:
        mutated = deepcopy(original)
        mutated["extended_python_baseline_snapshot"][key] += 1
        with pytest.raises(AssertionError):
            _assert_complexity_baseline_owned(mutated)


def test_expired_maintenance_review_deadline_fails() -> None:
    mutated = deepcopy(_complexity_ledger())
    today = date.today()
    mutated["review_by"] = (today - timedelta(days=1)).isoformat()
    with pytest.raises(AssertionError):
        _assert_complexity_baseline_owned(mutated, today=today)


def test_complexity_baseline_is_recomputed_and_regression_bounded() -> None:
    complexity = _complexity_ledger()
    current = _current_complexity_counts()
    allowed = complexity["baseline_snapshot"]
    _assert_counts_bounded(current[:6], allowed)
    assert current[6] <= allowed["tracked_files"]

    extended = _complexity_counts(_extended_python_files())
    extended_allowed = complexity["extended_python_baseline_snapshot"]
    _assert_counts_bounded(extended, extended_allowed)


def test_extended_python_scope_covers_non_core_repository_code() -> None:
    relative = {path.relative_to(ROOT).as_posix() for path in _extended_python_files()}
    assert "scripts/container_security.py" in relative
    assert "services/asset-baker/app/asset_baker/api.py" in relative
    assert "services/parakeet/provider/gpu/transcribe.py" in relative
    assert not {path.resolve() for path in _baseline_python_files()} & {
        path.resolve() for path in _extended_python_files()
    }


def test_complexity_dispositions_are_grounded() -> None:
    complexity = _complexity_ledger()
    assert len(complexity["accepted_signal_groups"]) >= 3
    for item in complexity["accepted_signals"]:
        _assert_accepted_signal_grounded(item)


def test_every_e_or_worse_symbol_is_individually_reviewed() -> None:
    _assert_e_or_worse_symbols_reviewed(_complexity_ledger())


def test_equal_size_e_or_worse_identity_swap_is_rejected() -> None:
    mutated = deepcopy(_complexity_ledger())
    removed = mutated["accepted_e_or_worse_symbols"].pop()
    mutated["accepted_e_or_worse_symbols"].append(
        {
            **removed,
            "symbol": "unreviewed_replacement_with_same_aggregate_count",
        }
    )
    with pytest.raises(AssertionError):
        _assert_e_or_worse_symbols_reviewed(mutated)


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
