"""Tests for bootstrapper.docs.merge_research."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _build_research_tree(tmp_path: Path, *, rows: dict[str, str], candidates: dict[str, str]) -> Path:
    """Build a synthetic docs/research/ tree."""
    root = tmp_path / "research"
    (root / "rows").mkdir(parents=True)
    (root / "candidates").mkdir(parents=True)
    for name, body in rows.items():
        (root / "rows" / f"{name}.md").write_text(body)
    for slug, body in candidates.items():
        (root / "candidates" / f"{slug}.md").write_text(body)
    return root


def test_merge_emits_integration_matrix(tmp_path):
    """The merge step writes docs/research/integration-matrix.md."""
    from docs.merge_research import run_merge
    row = (FIXTURE_DIR / "example_row.md").read_text()
    cand = (FIXTURE_DIR / "example_candidate.md").read_text()
    root = _build_research_tree(tmp_path, rows={"example-service": row}, candidates={"example-candidate": cand})

    run_merge(root)

    matrix = root / "integration-matrix.md"
    assert matrix.is_file()
    text = matrix.read_text()
    assert "example-service" in text
    assert "Example Candidate" in text


def test_merge_reconciles_referenced_by(tmp_path):
    """If row references a candidate, the candidate's referenced-by must list that service."""
    from docs.merge_research import run_merge
    row = (FIXTURE_DIR / "example_row.md").read_text()
    raw_cand = (FIXTURE_DIR / "example_candidate.md").read_text()
    cand_stripped = raw_cand.replace("referenced-by: [example-service]", "referenced-by: []")
    root = _build_research_tree(tmp_path, rows={"example-service": row}, candidates={"example-candidate": cand_stripped})

    run_merge(root)

    updated = (root / "candidates" / "example-candidate.md").read_text()
    assert "referenced-by: [example-service]" in updated or "referenced-by:\n- example-service" in updated


def test_merge_is_idempotent(tmp_path):
    """Re-running merge against an already-merged tree leaves files byte-identical."""
    from docs.merge_research import run_merge
    row = (FIXTURE_DIR / "example_row.md").read_text()
    cand = (FIXTURE_DIR / "example_candidate.md").read_text()
    root = _build_research_tree(tmp_path, rows={"example-service": row}, candidates={"example-candidate": cand})

    run_merge(root)
    matrix1 = (root / "integration-matrix.md").read_text()
    cand1 = (root / "candidates" / "example-candidate.md").read_text()

    run_merge(root)
    matrix2 = (root / "integration-matrix.md").read_text()
    cand2 = (root / "candidates" / "example-candidate.md").read_text()

    assert matrix1 == matrix2
    assert cand1 == cand2


def test_merge_groups_by_category(tmp_path):
    """integration-matrix.md groups rows by category."""
    from docs.merge_research import run_merge
    base_row = (FIXTURE_DIR / "example_row.md").read_text()

    row_a = base_row.replace("service: example-service", "service: alpha").replace("category: data", "category: agents")
    row_b = base_row.replace("service: example-service", "service: beta").replace("category: data", "category: media")
    root = _build_research_tree(tmp_path, rows={"alpha": row_a, "beta": row_b}, candidates={})

    run_merge(root)
    text = (root / "integration-matrix.md").read_text()
    assert "## 2. By category" in text
    assert "### 2.1. agents" in text
    assert "### 2.2. media" in text


def test_cli_entry(tmp_path):
    """python -m bootstrapper.docs.merge_research --research-root <path> writes the matrix."""
    import subprocess
    row = (FIXTURE_DIR / "example_row.md").read_text()
    cand = (FIXTURE_DIR / "example_candidate.md").read_text()
    root = _build_research_tree(tmp_path, rows={"example-service": row}, candidates={"example-candidate": cand})

    env = {"PYTHONPATH": str(REPO_ROOT / "bootstrapper")}
    import os
    r = subprocess.run(
        [sys.executable, "-m", "docs.merge_research", "--research-root", str(root)],
        capture_output=True, text=True, cwd=REPO_ROOT,
        env={**os.environ, **env},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "integration-matrix.md").is_file()


# --- #1190: lifecycle-aware index -----------------------------------------


def _candidate_tree(tmp_path: Path, lifecycle: str) -> Path:
    row = (FIXTURE_DIR / "example_row.md").read_text()
    cand = (FIXTURE_DIR / "example_candidate.md").read_text().replace(
        "lifecycle: proposed\n", f"lifecycle: {lifecycle}\ndecided: 2026-07-03\n"
    )
    return _build_research_tree(
        tmp_path, rows={"example-service": row}, candidates={"example-candidate": cand}
    )


@pytest.mark.parametrize(
    ("lifecycle", "listed"),
    [("proposed", True), ("planned", True), ("deferred", True),
     ("rejected", False), ("shipped", False)],
)
def test_matrix_lists_only_open_candidates(tmp_path, lifecycle, listed):
    from docs.merge_research import closed_candidate_slugs, run_merge

    root = _candidate_tree(tmp_path, lifecycle)
    run_merge(root)
    text = (root / "integration-matrix.md").read_text()

    row = "| Example Candidate | agents | example-service | [candidates/example-candidate.md]"
    assert (row in text) is listed
    assert (root / "candidates" / "example-candidate.md").is_file()
    assert ("example-candidate" in closed_candidate_slugs(root)) is not listed
    note = "Shipped and rejected candidates are not listed"
    assert (note in text) is not listed


def test_matrix_headings_match_the_committed_numbering(tmp_path):
    from docs.merge_research import run_merge

    root = _candidate_tree(tmp_path, "proposed")
    run_merge(root)
    text = (root / "integration-matrix.md").read_text()

    for heading in ("## 1. By service", "## 2. By category", "### 2.1. data",
                    "## 3. Candidate new services"):
        assert f"\n{heading}\n" in text


def test_check_mode_reports_drift_without_writing(tmp_path):
    from docs.merge_research import run_merge

    root = _candidate_tree(tmp_path, "proposed")
    run_merge(root)
    candidate = root / "candidates" / "example-candidate.md"
    matrix = root / "integration-matrix.md"
    assert run_merge(root, check=True) == []

    candidate.write_text(candidate.read_text().replace(
        "lifecycle: proposed", "lifecycle: rejected"
    ))
    before = matrix.read_text()

    assert run_merge(root, check=True) == [matrix]
    assert matrix.read_text() == before
    assert run_merge(root) == [matrix]
    assert "Example Candidate |" not in matrix.read_text()


def test_cli_check_exits_2_on_drift(tmp_path):
    import os
    import subprocess

    root = _candidate_tree(tmp_path, "proposed")
    cmd = [sys.executable, "-m", "docs.merge_research", "--research-root", str(root), "--check"]
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "bootstrapper")}

    drift = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, env=env)
    assert drift.returncode == 2
    assert "integration-matrix.md" in drift.stderr
    assert not (root / "integration-matrix.md").exists()


def test_emitted_frontmatter_round_trips_values_yaml_would_misread():
    """Unquoted, `Dagster #2` lost `#2` on the next parse and `a: b` made the
    file unparseable; plain values keep their unquoted spelling (2026-10-08
    run, cycle 43)."""
    import yaml

    from docs.merge_research import _emit_frontmatter

    fm = {"name": "Dagster #2", "summary": "Dagster: data orchestrator", "slug": "dagster",
          "tags": ["a #b", "c, d", "plain"], "long": ["x"] * 9 + ["y: z"]}
    text = _emit_frontmatter(fm)
    assert yaml.safe_load(text.strip().strip("-")) == fm
    assert "slug: dagster\n" in text
