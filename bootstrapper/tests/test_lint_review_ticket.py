"""The minimum bar for machine-generated review tickets (#1246).

``scripts/lint_review_ticket.py`` is one implementation for every enforcement
point; ``.github/workflows/review-ticket-lint.yml`` is the filing-time one.
Canary bodies are built here, so no real ticket text is copied in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import lint_review_ticket as linter

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/review-ticket-lint.yml"
BLOB = "https://github.com/thekaveh/atlas/blob/develop"


def _ticket(evidence: str = "", *, extra: str = "", review_id: str = "F23",
            criteria: str = "- [ ] `pytest tests/test_x.py` passes, checked in CI.") -> str:
    """A canonical ticket that meets the bar; parts are swapped per test."""
    evidence = evidence or (
        f"| [`bootstrapper/start.py:10`]({BLOB}/bootstrapper/start.py#L10) | the flag parser |"
    )
    return "\n".join([
        "## Summary", "", "The flag is ignored, so the wizard runs twice.", "",
        "## Context", "", "`start.py` reads the flag once (see below).", "",
        "## Acceptance criteria", "", criteria, "",
        "## Evidence", "", "| Location | What it shows |", "|---|---|", evidence, "",
        extra,
        f"<!-- atlas-review:2026-09-21:{review_id} -->",
    ])


def _rules(body: str, **kwargs) -> list[tuple[str, bool]]:
    return [(v.rule, v.blocking) for v in linter.lint(body, **kwargs)]


def test_a_canonical_ticket_meets_the_bar() -> None:
    assert _rules(_ticket()) == []


@pytest.mark.parametrize(("evidence", "expected"), [
    # A whole-file link with no reason is unworkable.
    (f"| [`services/x/README.md`]({BLOB}/services/x/README.md) | the adapter seam |", [("R1", True)]),
    # ...unless the caption says why a line anchor is not meaningful.
    (f"| [`services/x/README.md`]({BLOB}/services/x/README.md) | whole-file: the count is the point |", []),
    # Plain paths with and without a line.
    ("| `services/backend/app/Dockerfile:35-36` | the pinned base |", []),
    ("| `services/backend/app/Dockerfile` | the pinned base |", [("R1", True)]),
    # A command and its output is evidence too, not an unanchored path.
    ("| `grep -c image: services/*/compose.yml` → 86 | the denominator |", []),
    # Versions and hostnames in backticks are not paths.
    ("| `bootstrapper/start.py:10` pins `3.12` and `v0.1` | the floor |", []),
    # Bare paths, and links however the owner is cased, count too.
    ("| services/backend/app/Dockerfile | the pinned base |", [("R1", True)]),
    ("| services/backend/app/Dockerfile:35 | the pinned base |", []),
    ("| [Dockerfile](https://github.com/TheKaveh/Atlas/blob/develop/services/backend/app/Dockerfile) | base |",
     [("R1", True)]),
    # The prefix alone is not a reason.
    (f"| [`services/x/README.md`]({BLOB}/services/x/README.md) | whole-file: |", [("R1", True)]),
    # Older tickets list evidence as bullets.
    (f"- [README.md]({BLOB}/README.md) — Generic contribution instruction", [("R1", True)]),
    (f"- [README.md:155]({BLOB}/README.md#L155) — Generic contribution instruction", []),
])
def test_r1_evidence_must_be_line_anchored_or_say_why_not(evidence: str, expected: list) -> None:
    assert _rules(_ticket(evidence)) == expected


def test_r1_reads_evidence_under_a_sub_heading() -> None:
    body = _ticket(extra="### Detail\n\n| Location | What it shows |\n|---|---|\n"
                         f"| [`README.md`]({BLOB}/README.md) | the intro |\n")
    assert _rules(body) == [("R1", True)]


def test_r1_reads_the_older_h1_ticket_shape() -> None:
    """Older tickets open with an H1 and use ### sections; their evidence
    must still be read as evidence (a regression the backlog run caught)."""
    body = ("# Review FIX-43 — docs(x): y\n\n### Context and impact\n\nText.\n\n"
            "### Evidence and existing foundations\n\n"
            f"- [README.md]({BLOB}/README.md) — Existing foundation or integration seam; this proposal extends it\n"
            "\n<!-- atlas-review:2026-09-21:FIX-43 -->")
    assert _rules(body) == [("R1", True), ("R2", False)]


def test_r2_flags_the_generic_caption() -> None:
    evidence = (f"| [`start.py:10`]({BLOB}/bootstrapper/start.py#L10) | "
                "Existing foundation or integration seam; this proposal extends it |")
    assert _rules(_ticket(evidence)) == [("R2", False)]


@pytest.mark.parametrize(("extra", "expected"), [
    ("Any shared F13 export remains optional.", [("R3", True)]),
    ("Any shared F13 export (#1204) remains optional.", []),
    ("Tracks FIX-31 via https://github.com/thekaveh/atlas/issues/1176.", []),
    ("This is F23, the ticket's own finding.", []),
    ("The numbered H1 and step M8 are not review IDs.", []),
    # Each ID needs its own link, and "step #2" is not an issue.
    ("F13 (#1204) and, much later in the same long sentence about other things, F14 are related.",
     [("R3", True)]),
    ("See F13 in step #2.", [("R3", True)]),
    ("Superseded by #1204 (F27).", []),
    # Prose that merely looks like an ID.
    ("Raise the classifier F1 score above 0.9 and bind F5.", []),
    # The ID is the text of a link to its issue, however long the title.
    ("Needs [F27 — feat(agents): declare and review tool permissions before enabling an agent]"
     "(https://github.com/thekaveh/atlas/issues/1224) first.", []),
    # Code is not prose: a linter's F401 is not a review ID.
    ("Ruff reports `F401` here.\n\n```\nF841 local variable is assigned\n```", []),
])
def test_r3_other_review_ids_must_resolve_to_an_issue(extra: str, expected: list) -> None:
    assert _rules(_ticket(extra=extra)) == expected


def test_r4_to_r6_flag_boilerplate_restated_sections_and_mistitled_proposals() -> None:
    body = _ticket(extra="\n".join([
        "## Dependencies", "", "No additional hard prerequisite identified.", "",
        "## Cascading effects and regression boundaries", "", "The same spec, reordered.", "",
        "**Basis:** proposal.", "",
    ]))
    assert sorted(set(_rules(body, title="fix(x): y"))) == [("R4", False), ("R5", False), ("R6", False)]
    assert ("R6", False) not in _rules(body, title="feat(x): y")


@pytest.mark.parametrize(("criterion", "rule"), [
    ("- [ ] Deletes finish under the documented consistency bound.", "R7"),
    ("- [ ] The experience feels good.", "R8"),
])
def test_r7_r8_flag_criteria_that_cannot_be_checked(criterion: str, rule: str) -> None:
    assert (rule, False) in _rules(_ticket(criteria=criterion))


def test_r9_enforces_plain_words_no_filler_and_the_budgets() -> None:
    padded = "## Scope\n\n" + "We utilize a robust approach in order to help. " * 70
    messages = [v.message for v in linter.lint(_ticket(extra=padded)) if v.rule == "R9"]

    assert {m.split(":")[0] for m in messages} == {"W2", "W3", "W4"}
    assert any("words outside evidence and code, over 400" in m for m in messages)


def test_e1_re_resolves_evidence_against_a_checkout(tmp_path: Path) -> None:
    (tmp_path / "bootstrapper").mkdir()
    (tmp_path / "bootstrapper/start.py").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Atlas\n", encoding="utf-8")

    def messages(location: str) -> list[str]:
        return [v.message for v in linter.lint(_ticket(f"| {location} | x |"), repo_root=tmp_path)]

    assert messages(f"[`start.py:10`]({BLOB}/bootstrapper/start.py#L10)") == [
        "bootstrapper/start.py:10 is past the end of the file"]
    assert messages(f"[`gone.py:1`]({BLOB}/gone.py#L1)") == ["gone.py no longer exists"]
    # A range is checked at its end, not only its start.
    assert messages(f"[`start.py`]({BLOB}/bootstrapper/start.py#L1-L500)") == [
        "bootstrapper/start.py:500 is past the end of the file"]
    # A deleted nested file never resolves to a root file of the same name.
    assert messages(f"[`services/foo/README.md:1`]({BLOB}/services/foo/README.md#L1)") == [
        "services/foo/README.md no longer exists"]
    # GitHub's plain-view query and a branch name with a slash still resolve.
    assert messages("[`README.md:1`](https://github.com/thekaveh/atlas/blob/develop/README.md?plain=1#L1)") == []
    assert messages("[`start.py:2`](https://github.com/thekaveh/atlas/blob/batch/next10/bootstrapper/start.py#L2)") == []


def test_the_sentence_average_is_not_rounded_down() -> None:
    # Two sentences of 25 and 26 words: a mean of 25.5 is over the budget.
    prose = "## Scope\n\n" + " ".join(["word " * 24 + "end."] + ["word " * 25 + "end."])
    assert "W4: sentences average 25.5 words, over 25" in [v.message for v in linter.lint(prose)]


def test_a_checklist_is_not_one_long_sentence() -> None:
    criteria = "\n".join(f"- [ ] `pytest t{i}` passes in CI" for i in range(12))
    assert [v.message for v in linter.lint(_ticket(criteria=criteria)) if "sentence" in v.message] == []


def _cli(body: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/lint_review_ticket.py"), *args],
        input=body, capture_output=True, text=True, check=False, timeout=30,
    )


def test_only_the_unworkable_class_fails_the_cli() -> None:
    """Blocking is R1 and R3 only; warnings alone exit 0."""
    warned = _cli(_ticket(extra="**Basis:** proposal."), "--title=fix(x): y")
    dashed = _cli(_ticket(), "--title=-wip")
    blocked = _cli(_ticket(extra="See F13."), "--format", "json")

    assert (warned.returncode, warned.stdout.startswith("WARN  R6"), dashed.returncode) == (0, True, 0)
    assert (blocked.returncode, json.loads(blocked.stdout)[0]["rule"]) == (1, "R3")
    assert linter.BLOCKING == {"R1", "R3"}


def test_a_linter_crash_is_not_mistaken_for_a_bad_ticket(monkeypatch, capsys) -> None:
    """Review finding: an exception would exit 1, the violation status, and
    the workflow would label a valid ticket; it exits 2 instead."""
    def broken(*_args, **_kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(linter, "lint", broken)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(_ticket()))
    assert (linter.main([]), "ValueError: boom" in capsys.readouterr().err) == (2, True)


# ── the filing-time workflow ────────────────────────────────────────────


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_runs_on_marked_issues_with_the_body_kept_out_of_the_script() -> None:
    workflow = _workflow()
    job = workflow["jobs"]["lint"]
    step = job["steps"][-1]

    assert (workflow[True]["issues"]["types"], workflow["permissions"]) == (
        ["opened", "edited"], {"contents": "read", "issues": "write"})
    assert job["if"] == (
        "contains(github.event.issue.body, 'atlas-review:') "
        "|| contains(github.event.issue.labels.*.name, 'review-quality:needs-revision')")
    assert step["env"]["ISSUE_BODY"] == "${{ github.event.issue.body }}"
    assert "${{" not in step["run"], "untrusted issue text must reach the script only through env"
    assert "scripts/lint_review_ticket.py" in step["run"]


_FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
log = Path(os.environ["GH_LOG"])
args = sys.argv[1:]
with log.open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[:2] == ["api", "--paginate"]:
    print(os.environ.get("GH_COMMENTS", "[]"))
"""


_BOT = '{"login": "github-actions[bot]"}'
_FLAGGED_BEFORE = f'[{{"id": 7, "user": {_BOT}, "body": "<!-- atlas-review-ticket-lint -->\\nold"}}]'
# Someone else's comment that happens to start with the marker is not ours.
_IMPERSONATED = '[{"id": 9, "user": {"login": "someone"}, "body": "<!-- atlas-review-ticket-lint -->"}]'


@pytest.mark.parametrize("case", [
    # Flagged for the first time: label it and comment once.
    ("See F13.", "[]", "false", [{"label", "create"}, {"--add-label"}, {"issue", "comment"}]),
    # Flagged again: update the one comment instead of adding another.
    ("See F13.", _FLAGGED_BEFORE, "true", [{"label", "create"}, {"--add-label"}, {"PATCH", "comments/7"}]),
    # Fixed: drop the label and say so in the same comment.
    ("", _FLAGGED_BEFORE, "true", [{"--remove-label"}, {"PATCH", "comments/7"}]),
    # Fixed before any comment was posted, or labelled by hand: still drop it.
    ("", "[]", "true", [{"--remove-label"}]),
    # Never flagged: say nothing.
    ("", "[]", "false", []),
    # A look-alike comment by someone else is never edited.
    ("See F13.", _IMPERSONATED, "false", [{"label", "create"}, {"--add-label"}, {"issue", "comment"}]),
    # The marker was removed from a flagged ticket: clear the label.
    (None, _FLAGGED_BEFORE, "true", [{"--remove-label"}, {"PATCH", "comments/7"}]),
])
def test_workflow_reconciles_one_label_and_one_comment(tmp_path: Path, case: tuple) -> None:
    """(extra body or None for no marker, existing comments, label present, expected writes)."""
    body, comments, has_label, expected = case
    fake_bin, log = tmp_path / "bin", tmp_path / "gh.log"
    fake_bin.mkdir()
    (fake_bin / "gh").write_text(_FAKE_GH, encoding="utf-8")
    (fake_bin / "gh").chmod(0o755)
    run = _workflow()["jobs"]["lint"]["steps"][-1]["run"]
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}", "GH_LOG": str(log),
           "GH_COMMENTS": comments, "RUNNER_TEMP": str(tmp_path), "GH_REPO": "o/r",
           "ISSUE_BODY": _ticket(extra=body) if body is not None else "A rewritten body with no marker.",
           "ISSUE_TITLE": "feat(x): y", "ISSUE_NUMBER": "5", "HAS_LABEL": has_label}

    completed = subprocess.run(["bash", "-c", run], cwd=ROOT, env=env, capture_output=True,
                               text=True, check=False, timeout=60)
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    writes = [" ".join(call) for call in calls if call[:2] != ["api", "--paginate"]]

    assert completed.returncode == 0, completed.stderr
    assert len(writes) == len(expected) and all(
        all(token in write for token in tokens) for write, tokens in zip(writes, expected)), writes
