"""The Conventional Commits release-notes dry run (#968 prototype)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import release_notes
from scripts.docs.heading_quality import heading_number_findings


ROOT = Path(__file__).resolve().parents[2]
_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "HOME": "/nonexistent",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=_ENV
    ).stdout.strip()


def _commit(repo: Path, subject: str, body: str = "") -> str:
    (repo / "f").open("a").write(subject + "\n")
    _git(repo, "add", "f")
    message = subject if not body else f"{subject}\n\n{body}"
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path: Path) -> Path:
    """main: base -> squash promotion -> merge promotion carrying develop commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "chore: initial")
    _git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    _commit(repo, "release: promote the wizard fixes to main (#20)")
    _git(repo, "checkout", "-q", "-b", "develop")
    _commit(repo, "fix(wizard): keep prompts usable (#21)")
    _commit(repo, "feat(catalog)!: expose support tiers (#22)", "BREAKING CHANGE: tiers are required")
    _commit(repo, "docs(security): publish the policy (#23)")
    _commit(repo, "chore(security): renew the exception set (#24)")
    _commit(repo, "Overnight maintenance run: 28 passes")
    _commit(repo, "fix(wizard): keep prompts usable (#21)")  # same PR twice on purpose
    _commit(repo, "fix(wizard): replace unsafe recovery advice (#25) (#26)")  # issue, then PR
    # A release branch off main that merges develop (no PR number), then the
    # PR that merges that release branch: two promotion layers to unwrap.
    _git(repo, "checkout", "-q", "-b", "release/x-to-main", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge remote-tracking branch 'origin/develop' into release/x-to-main", "develop")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #30 from thekaveh/release/x-to-main", "release/x-to-main")
    _commit(repo, "fix(wizard): promote minimum-terminal usability to main (#31)")
    return repo


def test_merge_promotions_expand_into_their_develop_commits(history: Path) -> None:
    notes = release_notes.collect_notes(history, "v0.1.0..main")

    # The PR merge (#30) and the release branch's develop merge (no PR) are
    # replaced by what they carry; the squash promotion (#20) and the typed
    # "fix(...): promote ... to main" squash (#31) stay as single entries; #26
    # takes the last (#N) suffix rather than the issue number that precedes it.
    assert {(note.bucket, note.pr) for note in notes} == {
        ("Fixes", 21),
        ("Breaking changes", 22),
        ("Documentation", 23),
        ("Security", 24),
        ("Unclassified", None),
        ("Fixes", 26),
        ("Promotions", 20),
        ("Promotions", 31),
    }


@pytest.mark.parametrize(
    "subject",
    [
        "Merge pull request #30 from thekaveh/release/x-to-main",
        "Merge remote-tracking branch 'origin/develop' into release/x-to-main",
        "Merge branch 'develop' into release/x-to-main",
        "Merge origin/main into reconcile/env-isolation-817-to-main",
        "release: promote develop to main (#984)",
        "chore(release): promote plugin Kong timeouts to main (#982)",
        "fix(wizard): promote minimum-terminal usability to main (#1080)",
        "chore(release): reconcile develop into main — wizard fixes (#965)",
    ],
)
def test_every_promotion_shape_is_recognised(subject: str) -> None:
    commit = release_notes.Commit("0" * 40, ("a", "b"), subject, "")

    assert release_notes.classify(commit).bucket == "Promotions"


@pytest.mark.parametrize(
    "subject, bucket",
    [
        ("maintenance: harden recovery boundaries (#983)", "Maintenance"),
        ("fix(security): take the cryptography fixes (#1025)", "Security"),
        ("docs(security): publish the policy (#1047)", "Documentation"),
        ("perf(backend): pool asyncpg connections (#804)", "Fixes"),
        ("Overnight maintenance run (2026-08-20): 28 passes (#959)", "Unclassified"),
    ],
)
def test_type_and_scope_mapping(subject: str, bucket: str) -> None:
    commit = release_notes.Commit("0" * 40, ("a",), subject, "")

    assert release_notes.classify(commit).bucket == bucket


def test_changes_are_deduplicated_by_pull_request(history: Path) -> None:
    notes = release_notes.collect_notes(history, "v0.1.0..main")

    assert [note.pr for note in notes].count(21) == 1


def test_unclassified_subjects_are_kept_verbatim(history: Path) -> None:
    notes = release_notes.collect_notes(history, "v0.1.0..main")
    unclassified = [note for note in notes if note.bucket == "Unclassified"]

    assert [note.subject for note in unclassified] == ["Overnight maintenance run: 28 passes"]
    assert unclassified[0].pr is None


def test_markdown_output_satisfies_the_heading_numbering_contract(history: Path) -> None:
    notes = release_notes.collect_notes(history, "v0.1.0..main")
    text = release_notes.render_markdown(notes, rev_range="v0.1.0..main")

    assert heading_number_findings(text) == []
    assert "## 1. Breaking changes" in text
    assert "- **catalog:** expose support tiers (#22)" in text
    assert "- **wizard:** keep prompts usable (#21)" in text
    assert "(#20)" in text and "Merge pull request" not in text


def test_empty_range_renders_a_no_changes_note(history: Path) -> None:
    text = release_notes.render_markdown([], rev_range="main..main")

    assert "No changes in this range." in text
    assert heading_number_findings(text) == []


def test_overrides_reclassify_without_touching_history(history: Path, tmp_path: Path) -> None:
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("24:\n  bucket: Fixes\n  subject: renew the exception set (reviewed)\n", encoding="utf-8")
    notes = release_notes.apply_overrides(
        release_notes.collect_notes(history, "v0.1.0..main"),
        release_notes.load_overrides(overrides),
    )
    corrected = next(note for note in notes if note.pr == 24)

    assert corrected.bucket == "Fixes"
    assert corrected.subject == "renew the exception set (reviewed)"
    assert _git(history, "log", "--format=%s", "-1", corrected.sha) == "chore(security): renew the exception set (#24)"


def test_overrides_reject_unknown_buckets_and_fields(tmp_path: Path) -> None:
    bad_bucket = tmp_path / "a.yaml"
    bad_bucket.write_text("24:\n  bucket: Nope\n", encoding="utf-8")
    bad_field = tmp_path / "b.yaml"
    bad_field.write_text("24:\n  sha: abc\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unknown bucket"):
        release_notes.load_overrides(bad_bucket)
    with pytest.raises(ValueError, match="only set bucket and subject"):
        release_notes.load_overrides(bad_field)


def test_cli_since_tag_json_and_changelog_refusal(history: Path, tmp_path: Path, capsys) -> None:
    assert release_notes.main(["--since-tag", "--format", "json", "--repo", str(history)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["range"] == "v0.1.0..HEAD"
    assert {note["pr"] for note in payload["notes"]} >= {20, 21, 22, 23, 24}

    out = tmp_path / "notes.md"
    assert release_notes.main(["--range", "v0.1.0..main", "--repo", str(history), "--output", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Release notes")

    changelog = history / "docs" / "CHANGELOG.md"
    changelog.parent.mkdir()
    changelog.write_text("# 9.5. Changelog\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="refusing to overwrite docs/CHANGELOG.md"):
        release_notes.main(["--range", "v0.1.0..main", "--repo", str(history), "--output", str(changelog)])
    assert changelog.read_text(encoding="utf-8") == "# 9.5. Changelog\n"


def test_cli_requires_exactly_one_range_selector(history: Path) -> None:
    with pytest.raises(SystemExit):
        release_notes.main(["--repo", str(history)])
    with pytest.raises(SystemExit):
        release_notes.main(["--repo", str(history), "--range", "a..b", "--since-tag"])


def test_dry_run_over_real_history_is_numbering_compliant() -> None:
    """The prototype must at least run against Atlas's own recent history."""
    notes = release_notes.collect_notes(ROOT, "HEAD~8..HEAD")
    text = release_notes.render_markdown(notes, rev_range="HEAD~8..HEAD")

    assert heading_number_findings(text) == []
    assert notes, "recent history should produce at least one note"


def test_changelog_block_lists_detailed_buckets_and_counts_the_rest(history: Path) -> None:
    pinned = release_notes.resolve_range(history, "v0.1.0..main")
    notes = release_notes.collect_notes(history, pinned)
    block = release_notes.render_changelog_block(notes, rev_range=pinned)

    assert block.startswith(release_notes.CHANGELOG_BEGIN + "\n<!-- generated-range: v0.1.0..")
    assert block.rstrip().endswith(release_notes.CHANGELOG_END)
    assert "### 1.1. Generated summary — `v0.1.0` through `" in block
    assert "**Breaking changes**" in block and "expose support tiers (#22)" in block
    assert "**Security**" in block and "renew the exception set (#24)" in block
    assert "**Features**" not in block  # the fixture has no feat commits
    assert "Also in this range: 2 fixes, 1 documentation, 2 promotions, 1 unclassified" in block
    assert "keep prompts usable" not in block  # fixes are counted, not listed
    assert heading_number_findings("# 9.5. Changelog\n\n## 1. [Unreleased]\n\n" + block) == []


def test_replace_changelog_block_keeps_everything_else(history: Path) -> None:
    text = (
        "# 9.5. Changelog\n\n## 1. [Unreleased]\n\n"
        f"{release_notes.CHANGELOG_BEGIN}\n<!-- generated-range: pending -->\n{release_notes.CHANGELOG_END}\n\n"
        "### 1.2. Fixed — curated entry\n\n- kept\n"
    )
    pinned = release_notes.resolve_range(history, "v0.1.0..main")
    block = release_notes.render_changelog_block(release_notes.collect_notes(history, pinned), rev_range=pinned)

    updated = release_notes.replace_changelog_block(text, block)

    assert updated.endswith("### 1.2. Fixed — curated entry\n\n- kept\n")
    assert updated.count(release_notes.CHANGELOG_BEGIN) == 1
    assert release_notes.committed_block_range(updated) == pinned
    with pytest.raises(ValueError, match="exactly one"):
        release_notes.replace_changelog_block(text + text, block)


def test_update_and_check_changelog_detect_hand_edits(history: Path) -> None:
    changelog = history / "docs" / "CHANGELOG.md"
    changelog.parent.mkdir()
    changelog.write_text(
        "# 9.5. Changelog\n\n## 1. [Unreleased]\n\n"
        f"{release_notes.CHANGELOG_BEGIN}\n<!-- generated-range: pending -->\n{release_notes.CHANGELOG_END}\n",
        encoding="utf-8",
    )

    assert release_notes.main(["--update-changelog", "--range", "v0.1.0..main", "--repo", str(history)]) == 0
    assert release_notes.main(["--check-changelog", "--repo", str(history)]) == 0
    tampered = changelog.read_text(encoding="utf-8").replace("expose support tiers", "expose tiers")
    changelog.write_text(tampered, encoding="utf-8")
    assert release_notes.main(["--check-changelog", "--repo", str(history)]) == 1


@pytest.mark.parametrize(
    "title, ok",
    [
        ("fix(wizard): keep prompts usable at the minimum", True),
        ("feat!: drop the legacy SOURCE names", True),
        ("release: promote the batch to main", True),
        ("deps(deps): bump the minors group across 2 directories", True),
        ("Overnight maintenance run (2026-08-20)", False),
        ("fix: ", False),
        ("Fix(wizard): capitalised type", False),
        ("fix(): empty scope", False),
    ],
)
def test_pull_request_title_gate(title: str, ok: bool) -> None:
    assert release_notes.check_pull_request_title(title) is ok
    assert release_notes.main(["--check-title", title]) == (0 if ok else 1)


def test_committed_changelog_block_is_current() -> None:
    """The block in docs/CHANGELOG.md must equal what its recorded range renders."""
    assert release_notes.main(["--check-changelog", "--repo", str(ROOT)]) == 0


def test_required_lint_job_gates_titles_and_the_changelog_block() -> None:
    workflow = (ROOT / ".github" / "workflows" / "services-lint.yml").read_text(encoding="utf-8")
    # The two gates must sit inside the required lint job, i.e. after its name
    # and before the next job's name.
    lint_job = workflow.split("name: Manifest lint + unit tests", 1)[1].split(
        "name: Compose merge + byte-equivalence + source-permutation matrix", 1
    )[0]

    assert "--check-title \"$PR_TITLE\"" in lint_job
    assert "if: github.event_name == 'pull_request'" in lint_job
    assert "python -m scripts.release_notes --check-changelog" in lint_job
