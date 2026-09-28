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


# --- #1190: candidate lifecycle -------------------------------------------

PIPELINES_RECORD = REPO_ROOT / "docs" / "research" / "candidates" / "open-webui-pipelines.md"


def _candidate(lifecycle_lines: str) -> str:
    source = (FIXTURE_DIR / "example_candidate.md").read_text()
    return source.replace("lifecycle: proposed\n", lifecycle_lines)


def _all_mode(tmp_path: Path, candidate_text: str) -> subprocess.CompletedProcess[str]:
    research = tmp_path / "docs" / "research"
    (research / "rows").mkdir(parents=True)
    (research / "candidates").mkdir(parents=True)
    (research / "candidates" / "example.md").write_text(candidate_text)
    return _run("--all", "--research-root", str(research))


def test_all_mode_fails_without_lifecycle_and_passes_once_present(tmp_path):
    missing = _all_mode(tmp_path / "missing", _candidate(""))
    present = _all_mode(tmp_path / "present", _candidate("lifecycle: proposed\n"))

    assert missing.returncode == 1
    assert "frontmatter missing key(s): ['lifecycle']" in missing.stdout
    assert present.returncode == 0, present.stdout + present.stderr


def test_rejects_a_lifecycle_outside_the_closed_set(tmp_path):
    result = _all_mode(tmp_path, _candidate("lifecycle: maybe\n"))

    assert result.returncode == 1
    assert (
        "`lifecycle` must be one of "
        "['proposed', 'planned', 'deferred', 'rejected', 'shipped']" in result.stdout
    )


@pytest.mark.parametrize("lifecycle", ["planned", "deferred", "rejected", "shipped"])
def test_a_decided_lifecycle_needs_its_decision_date(tmp_path, lifecycle):
    undated = _all_mode(tmp_path / "undated", _candidate(f"lifecycle: {lifecycle}\n"))
    dated = _all_mode(
        tmp_path / "dated", _candidate(f"lifecycle: {lifecycle}\ndecided: 2026-07-03\n")
    )

    assert undated.returncode == 1
    assert f"a {lifecycle} candidate needs `decided: YYYY-MM-DD`" in undated.stdout
    assert dated.returncode == 0, dated.stdout + dated.stderr


@pytest.mark.parametrize(
    ("superseded_by", "ok"),
    [
        ("https://github.com/thekaveh/atlas/issues/207", True),
        ("services/open-webui/README.md", True),
        ("services/no-such-service/README.md", False),
        ("issue 207", False),
        ('""', False),
        ("/etc/hosts", False),
        ("..", False),
        (".", False),
    ],
)
def test_superseded_by_names_a_url_or_an_existing_repo_path(tmp_path, superseded_by, ok):
    result = _all_mode(
        tmp_path,
        _candidate(
            f"lifecycle: rejected\ndecided: 2026-07-03\nsuperseded-by: {superseded_by}\n"
        ),
    )

    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    if not ok:
        assert "`superseded-by` must be an http(s) URL or an existing repo path" in result.stdout


def _historical(text: str) -> str:
    """Demote Effort under a historical heading, as a closed record does."""
    return text.replace("## Effort", "## Historical proposal\n\n### Effort")


@pytest.mark.parametrize(
    ("lifecycle", "ok"),
    [("rejected", True), ("shipped", True), ("deferred", False), ("proposed", False)],
)
def test_only_closed_candidates_may_nest_sections_under_a_historical_heading(
    tmp_path, lifecycle, ok
):
    dated = "" if lifecycle == "proposed" else "decided: 2026-07-03\n"
    result = _all_mode(tmp_path, _historical(_candidate(f"lifecycle: {lifecycle}\n{dated}")))

    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    if not ok:
        assert "missing required section: ## Effort" in result.stdout


def test_open_webui_pipelines_record_is_rejected_with_its_decision():
    head = PIPELINES_RECORD.read_text(encoding="utf-8").split("\n---\n", 1)[0]

    assert "\nlifecycle: rejected\n" in head
    assert "\ndecided: 2026-07-03\n" in head
    assert "\nsuperseded-by: https://github.com/thekaveh/atlas/issues/207\n" in head


def test_open_webui_pipelines_proposal_is_kept_verbatim_under_a_historical_heading():
    text = PIPELINES_RECORD.read_text(encoding="utf-8")
    historical = text.index("## 4. Historical proposal (rejected 2026-07-03)")
    evidence = text.index("## 5. Upstream evidence")
    original_sentences = [
        "- open-webui → pipelines via `OPENAI_API_BASE_URLS=http://pipelines:9099` "
        "(added alongside the existing LiteLLM URL, or fronted by litellm)",
        "- kong → pipelines via a `pipelines.localhost` alias for admin UI",
        "medium — new compose fragment, new SOURCE variants (container, disabled), "
        "Kong alias, and an init step to drop curated pipeline scripts into the "
        "pipelines volume; minimal env wiring beyond that.",
        "- Pipelines is single-tenant: scaling needs sticky sessions or a queue.",
        "The stack already has the natural consumers (Open WebUI, LiteLLM, Hermes) "
        "and an obvious tracing target (Langfuse, also proposed).",
    ]

    for heading in ("### 4.1. Stack wiring sketch", "### 4.2. Effort", "### 4.4. Why now"):
        assert historical < text.index(heading) < evidence
    for sentence in original_sentences:
        assert historical < text.index(sentence) < evidence


@pytest.mark.parametrize(
    ("dates", "message"),
    [
        ("lifecycle: shipped\ndecided: 2026-02-30\n", "missing or unparseable frontmatter"),
        ("lifecycle: proposed\ndecided: soon\n", "frontmatter `decided` must be YYYY-MM-DD"),
    ],
)
def test_malformed_decision_dates_are_errors_not_crashes(tmp_path, dates, message):
    result = _all_mode(tmp_path, _candidate(dates))

    assert result.returncode == 1
    assert message in result.stdout
    assert "Traceback" not in result.stderr


def test_a_nested_upstream_evidence_section_still_needs_a_url(tmp_path):
    text = _candidate("lifecycle: shipped\ndecided: 2026-07-03\n")
    nested = re.sub(r"(?s)## Upstream evidence\n.*", "### Upstream evidence\nNone recorded.\n", text)

    result = _all_mode(tmp_path, nested)

    assert result.returncode == 1
    assert "Upstream evidence section must contain at least one URL" in result.stdout
