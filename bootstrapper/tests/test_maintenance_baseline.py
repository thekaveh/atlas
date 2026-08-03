from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_complexity_baseline_is_owned_reviewed_and_regression_bounded() -> None:
    baseline = json.loads((ROOT / ".maintenance.json").read_text(encoding="utf-8"))
    complexity = baseline["complexity"]

    assert complexity["owner"] == "Atlas maintainers"
    assert complexity["review_by"] >= "2026-09-01"
    assert "Do not increase" in complexity["regression_policy"]
    assert complexity["baseline_snapshot"]["radon_grade_e_or_worse"] == 20
    assert complexity["baseline_snapshot"]["functions_over_60_physical_lines"] == 188
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
