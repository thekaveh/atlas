"""Tests for scripts/validate_research_schema.py."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VALIDATOR = REPO_ROOT / "scripts" / "validate_research_schema.py"
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(VALIDATOR), *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)


def test_validates_clean_row_fixture():
    """The committed example_row.md passes validation."""
    r = _run(str(FIXTURE_DIR / "example_row.md"))
    assert r.returncode == 0, r.stdout + r.stderr


def test_validates_clean_candidate_fixture():
    """The committed example_candidate.md passes validation."""
    r = _run(str(FIXTURE_DIR / "example_candidate.md"))
    assert r.returncode == 0, r.stdout + r.stderr


def test_validates_hierarchically_numbered_candidate(tmp_path):
    source = (FIXTURE_DIR / "example_candidate.md").read_text()
    counter = 0

    def number_heading(match):
        nonlocal counter
        counter += 1
        return f"## {counter}. {match.group(1)}"

    numbered = re.sub(r"^## (.+)$", number_heading, source, flags=re.MULTILINE)
    candidate = tmp_path / "numbered_candidate.md"
    candidate.write_text(numbered)

    result = _run(str(candidate))
    assert result.returncode == 0, result.stdout + result.stderr


def test_rejects_required_heading_with_extra_suffix(tmp_path):
    source = (FIXTURE_DIR / "example_candidate.md").read_text()
    candidate = tmp_path / "malformed_candidate.md"
    candidate.write_text(source.replace("## Headline", "## Headline extra words"))

    result = _run(str(candidate))

    assert result.returncode == 1
    assert "missing required section: ## Headline" in result.stdout


def test_rejects_row_missing_frontmatter(tmp_path):
    bad = tmp_path / "bad_row.md"
    bad.write_text("# bad — Integration Research\n\n## 1. Missing-pair integrations\n_None._")
    r = _run(str(bad))
    assert r.returncode == 1
    assert "frontmatter" in r.stdout.lower()


def test_rejects_row_missing_required_section(tmp_path):
    """A row file missing one of the three numbered sections is rejected."""
    bad = tmp_path / "bad_row.md"
    bad.write_text(
        "---\nservice: bad\ncategory: data\ngenerated: 2026-05-18\n"
        "generator: phase-b-subagent\nsources_consulted:\n  - https://example.com\n---\n\n"
        "# bad — Integration Research\n\n"
        "## 1. Missing-pair integrations\n_No high-confidence opportunities identified._\n\n"
        "## 2. Candidate new services\n_No high-confidence opportunities identified._\n"
    )
    r = _run(str(bad))
    assert r.returncode == 1
    assert "section" in r.stdout.lower()
    assert "3" in r.stdout


def test_rejects_row_exceeding_word_cap(tmp_path):
    """A row file with > 800 words is rejected."""
    body = "word " * 900
    bad = tmp_path / "fat_row.md"
    bad.write_text(
        "---\nservice: fat\ncategory: data\ngenerated: 2026-05-18\n"
        "generator: phase-b-subagent\nsources_consulted:\n  - https://example.com\n---\n\n"
        "# fat — Integration Research\n\n"
        "## 1. Missing-pair integrations\n" + body + "\n\n"
        "## 2. Candidate new services\n_No high-confidence opportunities identified._\n\n"
        "## 3. Per-service feature gaps\n_No high-confidence opportunities identified._\n"
    )
    r = _run(str(bad))
    assert r.returncode == 1
    assert "word" in r.stdout.lower() or "800" in r.stdout


def test_rejects_row_exceeding_candidate_cap(tmp_path):
    """A row file with > 5 candidate cross-references is rejected."""
    cands = "\n".join(
        f"- **Cand {i}** → `../candidates/cand-{i}.md`\n  - Headline: ...\n  - Other consumers in stack: ..."
        for i in range(7)
    )
    bad = tmp_path / "many_cands.md"
    bad.write_text(
        "---\nservice: many\ncategory: data\ngenerated: 2026-05-18\n"
        "generator: phase-b-subagent\nsources_consulted:\n  - https://example.com\n---\n\n"
        "# many — Integration Research\n\n"
        "## 1. Missing-pair integrations\n_None._\n\n"
        "## 2. Candidate new services\n" + cands + "\n\n"
        "## 3. Per-service feature gaps\n_None._\n"
    )
    r = _run(str(bad))
    assert r.returncode == 1
    assert "candidate" in r.stdout.lower() or "5" in r.stdout


def test_rejects_candidate_missing_required_section(tmp_path):
    bad = tmp_path / "bad_cand.md"
    bad.write_text(
        "---\nslug: bad\nname: Bad\ntype: external-service\ncategory-fit: data\n"
        "generated: 2026-05-18\nupstream: https://example.com\nlicense: MIT\n"
        "referenced-by: []\n---\n\n"
        "# Bad\n\n## Headline\nFoo.\n\n## Problem it solves\nBar.\n\n"
        "## Stack wiring sketch\n- a → b via http\n\n## Effort\nsmall — foo.\n\n"
        "## Risks & open questions\n- none\n"
    )
    r = _run(str(bad))
    assert r.returncode == 1
    assert "upstream evidence" in r.stdout.lower()


def test_all_mode_walks_research_tree(tmp_path):
    """--all validates every row and candidate under docs/research/."""
    rows = tmp_path / "docs" / "research" / "rows"
    cands = tmp_path / "docs" / "research" / "candidates"
    rows.mkdir(parents=True)
    cands.mkdir(parents=True)

    good_row = FIXTURE_DIR / "example_row.md"
    good_cand = FIXTURE_DIR / "example_candidate.md"
    (rows / "example.md").write_text(good_row.read_text())
    (cands / "example.md").write_text(good_cand.read_text())

    r = subprocess.run(
        [sys.executable, str(VALIDATOR), "--all", "--research-root", str(tmp_path / "docs" / "research")],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
