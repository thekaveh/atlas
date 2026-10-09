"""The Conventional Commits release-notes dry run (#968 prototype)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

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
        "merge: bring develop (x) into main",
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


@pytest.fixture
def promoted(tmp_path: Path) -> Path:
    """develop squashes, main squash promotions of them, then main synced back into develop."""
    repo = tmp_path / "promoted"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "chore: initial")
    _git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    _git(repo, "checkout", "-q", "-b", "develop")
    _commit(repo, "feat(llm): add managed vLLM Metal Apple-silicon source (#379) (#507)")
    _commit(repo, "feat(cache): bound the response cache (#12) (#40)")
    _commit(repo, "feat(cache): bound the response cache (#12) (#41)")
    _commit(repo, "fix(stack): take mc from the pgsty fork (#1333)")
    _commit(repo, "fix(stack): scope multi-image exception rows (#1334)")
    _git(repo, "checkout", "-q", "main")
    # A pre-convention promotion repeats the develop subject under its own
    # number; a release title names the develop pull requests it carries.
    _commit(
        repo,
        "feat(llm): add managed vLLM Metal Apple-silicon source (#379) (#509)",
        "Promotes the managed vLLM Metal source from develop to main.",
    )
    _commit(repo, "chore(release): merge develop into main for #1333 and #1334 (#1335)")
    _commit(repo, "chore(release): merge develop into main for #77 (#78)")  # #77 is not in range
    _git(repo, "checkout", "-q", "develop")
    _git(repo, "merge", "-q", "-s", "ours", "--no-ff", "-m", "Merge pull request #510 from thekaveh/main", "main")
    return repo


def test_distinct_features_with_near_identical_subjects_both_survive(promoted: Path) -> None:
    """#40 and #41 differ only in their pull-request number, exactly as a
    develop/main pair does, but nothing records one as the other's promotion,
    so both stay: a subject is never an identity (#968)."""
    notes = release_notes.collect_notes(promoted, "v0.1.0..develop")
    cache = [note for note in notes if note.scope == "cache"]

    assert [(note.pr, note.promoted_in) for note in cache] == [(41, ()), (40, ())]
    assert {note.subject for note in cache} == {"bound the response cache"}
    text = release_notes.render_markdown(notes, rev_range="v0.1.0..develop")
    assert "- **cache:** bound the response cache (#41)" in text
    assert "- **cache:** bound the response cache (#40)" in text


def test_develop_squash_and_its_main_promotion_collapse_into_one_entry(promoted: Path) -> None:
    notes = release_notes.collect_notes(promoted, "v0.1.0..develop")
    folded = {note.pr: note.promoted_in for note in notes}

    # A reviewed entry folds main's #509 into develop's #507; the release title
    # names its sources, so #1335 folds into #1333 and #1334; #78 names a source
    # outside the range and stays its own Promotions entry.
    assert release_notes.REVIEWED_PROMOTIONS[509] == (507,)
    assert {pr: folded[pr] for pr in (507, 1333, 1334, 78)} == {507: (509,), 1333: (1335,), 1334: (1335,), 78: ()}
    assert not {509, 1335} & set(folded)
    text = release_notes.render_markdown(notes, rev_range="v0.1.0..develop")
    assert "- **llm:** add managed vLLM Metal Apple-silicon source (#507, #509)" in text
    assert text.count("add managed vLLM Metal Apple-silicon source") == 1
    assert "- **stack:** take mc from the pgsty fork (#1333, #1335)" in text
    assert heading_number_findings(text) == []


@pytest.mark.parametrize(
    "subject, sources",
    [
        ("merge develop into main for #1333 and #1334", (1333, 1334)),
        ("merge develop into main for #1323, #1324, #1325 and #1327", (1323, 1324, 1325, 1327)),
        ("promote the wizard fixes to main", ()),
    ],
)
def test_release_titles_name_their_develop_sources(subject: str, sources: tuple[int, ...]) -> None:
    release = release_notes.Note("0" * 40, 1326, "Promotions", "release", subject)

    assert release_notes.promotion_sources(release) == sources


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
    # The documented bare form re-renders the recorded range (no --range).
    assert release_notes.main(["--update-changelog", "--repo", str(history)]) == 0
    assert release_notes.main(["--check-changelog", "--repo", str(history)]) == 0


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
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "services-lint.yml").read_text(encoding="utf-8")
    )
    # The two gates sit in the `lint` job, whose result the required
    # "Manifest lint + unit tests" gate requires (#1176).
    steps = {step.get("name"): step for step in workflow["jobs"]["lint"]["steps"]}
    title = steps["Pull-request title is a Conventional Commits subject"]
    changelog = steps["Generated changelog summary is current"]

    assert '--check-title "$PR_TITLE"' in title["run"]
    assert title["if"] == "github.event_name == 'pull_request'"
    assert "python -m scripts.release_notes --check-changelog" in changelog["run"]
    assert "lint" in workflow["jobs"]["required-lint"]["needs"]


# --- release merges and squash promotions whose sources are off-range (#1351)


@pytest.fixture
def released(tmp_path: Path) -> Path:
    """Both gitflow release shapes: a "merge: bring develop" release merge,
    and a squash promotion naming a develop pull request main never had."""
    repo = tmp_path / "released"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "chore: initial")
    _git(repo, "tag", "-a", "v0.1.0", "-m", "v0.1.0")
    _git(repo, "checkout", "-q", "-b", "develop")
    _commit(repo, "fix(stack): keep the cache bounded (#5)")
    _git(repo, "checkout", "-q", "-b", "release/5-to-main", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge: bring develop (#5) into main", "develop")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #6 from thekaveh/release/5-to-main", "release/5-to-main")
    _git(repo, "tag", "-a", "v0.2.0", "-m", "v0.2.0")
    _git(repo, "checkout", "-q", "develop")
    _commit(repo, "feat(rag): rank by recency (#7)")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "chore(release): merge develop into main for #7 (#8)")
    return repo


def test_a_release_merge_expands_into_its_develop_commits(released: Path) -> None:
    notes = release_notes.collect_notes(released, "v0.1.0..v0.2.0")

    assert [(note.pr, note.bucket, note.subject) for note in notes] == [
        (5, "Fixes", "keep the cache bounded")
    ]


def test_a_squash_promotion_takes_its_off_range_source_bucket(released: Path) -> None:
    notes = release_notes.collect_notes(released, "v0.2.0..main")

    assert [(note.pr, note.bucket, note.subject, note.promoted_in) for note in notes] == [
        (7, "Features", "rank by recency", (8,))
    ]
    # A commit naming develop and main is a promotion only as a merge.
    single = release_notes.Commit("0" * 40, ("a",), "fix: keep develop and main in sync (#9)", "")
    assert release_notes.classify(single).bucket == "Fixes"
    # A range that starts after #7 was released does not list it again: a
    # main->develop sync merge brings in its promotion, which stays counted.
    _git(released, "checkout", "-q", "develop")
    _git(released, "tag", "-a", "d1", "-m", "d1")
    _git(released, "merge", "-q", "-s", "ours", "--no-ff", "-m", "Merge pull request #9 from thekaveh/main", "main")
    synced = {note.pr: note.bucket for note in release_notes.collect_notes(released, "d1..develop")}
    assert synced[8] == "Promotions" and 7 not in synced
    # Without a develop branch to resolve #7, today's single entry stays.
    _git(released, "checkout", "-q", "main")
    _git(released, "branch", "-D", "develop")
    kept = release_notes.collect_notes(released, "v0.2.0..main")
    assert [(note.pr, note.bucket) for note in kept] == [(8, "Promotions")]



def test_the_counted_line_does_not_claim_the_counts_are_itemized():
    """The line said every counted commit was "detailed in the curated entries
    below"; those cite 13 of 478 (2026-10-08 run, cycle 43)."""
    line = release_notes._counted_line([])
    assert "detailed in the curated entries below" not in line
    assert "not itemized" in line
