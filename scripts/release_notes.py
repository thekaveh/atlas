"""Dry-run release notes from Conventional Commits (#968 prototype).

Reads a commit range from Git, classifies every first-parent commit by its
Conventional Commits subject, expands develop-to-main promotion merges into
the develop commits they carry, de-duplicates by pull-request number so a
change counted on develop is not counted again by its promotion, and renders
concise notes whose headings satisfy the documentation numbering contract.

It never writes ``docs/CHANGELOG.md`` and needs no credentials: the input is
the local repository, the output is stdout or a file the caller names.

Usage::

    python -m scripts.release_notes --range v0.1.0..origin/main
    python -m scripts.release_notes --since-tag --format json
    python -m scripts.release_notes --range A..B --overrides notes-overrides.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    from scripts.bounded_subprocess import run_bounded
except ModuleNotFoundError:  # Direct ``python scripts/release_notes.py`` invocation.
    from bounded_subprocess import run_bounded  # type: ignore[no-redef]


_SUBJECT_RE = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s+(?P<subject>.+?)\s*$"
)
_PR_RE = re.compile(r"\(#(?P<pr>\d+)\)")
_MERGE_RE = re.compile(r"^Merge pull request #(?P<pr>\d+) from (?P<head>\S+)")
# Merges made outside a pull request (release branches refreshed from develop,
# feature branches synced from main) carry no PR number but only wrap changes
# that are counted on their own; expanding them lets de-duplication do its job.
_BRANCH_MERGE_RE = re.compile(r"^Merge (?:remote-tracking )?(?:branch )?'?[^' ]+'? into \S+")
# Squash promotions typed as fix/docs/chore still read "promote … to main".
_PROMOTE_SUBJECT_RE = re.compile(r"^(?:promote|reconcile) .*\b(?:to|into) main\b")
_EXPANSION_DEPTH = 4
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"
_CHANGELOG = Path("docs") / "CHANGELOG.md"
CHANGELOG_BEGIN = "<!-- BEGIN GENERATED RELEASE NOTES -->"
CHANGELOG_END = "<!-- END GENERATED RELEASE NOTES -->"
_RANGE_COMMENT_RE = re.compile(r"<!-- generated-range: (?P<range>\S+) -->")
# Buckets listed entry by entry in the changelog block; the rest are counted.
_DETAILED_BUCKETS = ("Breaking changes", "Security", "Features")
# Pull-request titles must be Conventional Commits so the squash commit is.
CONVENTIONAL_TITLE_RE = re.compile(
    r"^(?:build|chore|ci|deps|docs|feat|fix|maintenance|perf|refactor|release|security|style|test)"
    r"(?:\([^()\s][^()]*\))?!?: \S.*$"
)

BUCKETS = (
    "Breaking changes",
    "Security",
    "Features",
    "Fixes",
    "Documentation",
    "Build and CI",
    "Maintenance",
    "Promotions",
    "Unclassified",
)
_TYPE_BUCKETS = {
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Fixes",
    "security": "Security",
    "docs": "Documentation",
    "build": "Build and CI",
    "ci": "Build and CI",
    "chore": "Maintenance",
    "refactor": "Maintenance",
    "test": "Maintenance",
    "style": "Maintenance",
    "deps": "Maintenance",
    "maintenance": "Maintenance",
    "release": "Promotions",
}


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    subject: str
    body: str


@dataclass(frozen=True)
class Note:
    sha: str
    pr: int | None
    bucket: str
    scope: str | None
    subject: str


def _git(repo_root: Path, *args: str) -> str:
    result = run_bounded(["git", *args], cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed (exit {result.returncode})")
    return result.stdout


def read_commits(repo_root: Path, rev_range: str, *, first_parent: bool = True) -> list[Commit]:
    """Return the commits in ``rev_range`` newest first, optionally first-parent only."""
    fmt = f"--format=%H{_FIELD_SEP}%P{_FIELD_SEP}%s{_FIELD_SEP}%b{_RECORD_SEP}"
    args = ["log", fmt]
    if first_parent:
        args.append("--first-parent")
    output = _git(repo_root, *args, rev_range, "--")
    commits: list[Commit] = []
    for record in output.split(_RECORD_SEP):
        if not record.strip():
            continue
        sha, parents, subject, body = record.strip("\n").split(_FIELD_SEP, 3)
        commits.append(Commit(sha, tuple(parents.split()), subject.strip(), body.strip()))
    return commits


def _pr_numbers(text: str) -> list[int]:
    return [int(match.group("pr")) for match in _PR_RE.finditer(text)]


def _is_breaking(match: re.Match[str], body: str) -> bool:
    return bool(match.group("bang")) or "BREAKING CHANGE" in body


def _bucket_for(kind: str, scope: str | None, subject: str) -> str:
    if kind == "release" or scope == "release" or _PROMOTE_SUBJECT_RE.match(subject):
        return "Promotions"
    if scope == "security" and kind not in {"docs", "test"}:
        return "Security"
    return _TYPE_BUCKETS.get(kind, "Unclassified")


def classify(commit: Commit) -> Note:
    """Map one commit to a note; unknown shapes land in ``Unclassified`` verbatim."""
    merge = _MERGE_RE.match(commit.subject)
    if merge is not None:
        return Note(commit.sha, int(merge.group("pr")), "Promotions", None, commit.subject)
    if _BRANCH_MERGE_RE.match(commit.subject):
        return Note(commit.sha, None, "Promotions", None, commit.subject)
    match = _SUBJECT_RE.match(commit.subject)
    if match is None:
        return Note(commit.sha, None, "Unclassified", None, commit.subject)
    prs = _pr_numbers(commit.subject)  # GitHub appends the PR number last
    subject = _PR_RE.sub("", match.group("subject")).strip()
    scope = match.group("scope") or None
    kind = match.group("type").lower()
    if _is_breaking(match, commit.body):
        bucket = "Breaking changes"
    else:
        bucket = _bucket_for(kind, scope, subject)
    return Note(commit.sha, prs[-1] if prs else None, bucket, scope, subject)


def is_promotion(commit: Commit) -> bool:
    return classify(commit).bucket == "Promotions"


def expand_promotion(repo_root: Path, commit: Commit) -> list[Commit]:
    """Return the develop-side commits a promotion merge carries (empty for squashes)."""
    if len(commit.parents) < 2:
        return []
    return read_commits(repo_root, f"{commit.parents[0]}..{commit.parents[1]}")


def _dedupe_key(note: Note) -> str:
    return f"pr:{note.pr}" if note.pr is not None else f"sha:{note.sha}"


def _leaf_commits(repo_root: Path, commit: Commit, depth: int) -> list[Commit]:
    """Replace a promotion merge by what it carries, recursively, bounded by depth."""
    expanded = expand_promotion(repo_root, commit) if depth and is_promotion(commit) else []
    if not expanded:
        return [commit]
    leaves: list[Commit] = []
    for inner in expanded:
        leaves.extend(_leaf_commits(repo_root, inner, depth - 1))
    return leaves


def collect_notes(repo_root: Path, rev_range: str) -> list[Note]:
    """Classify a range, expanding promotions and de-duplicating by PR number."""
    notes: list[Note] = []
    seen: set[str] = set()
    for commit in read_commits(repo_root, rev_range):
        for leaf in _leaf_commits(repo_root, commit, _EXPANSION_DEPTH):
            note = classify(leaf)
            key = _dedupe_key(note)
            if key in seen:
                continue
            seen.add(key)
            notes.append(note)
    return notes


def load_overrides(path: Path | None) -> dict[int, dict[str, str]]:
    """Maintainer corrections keyed by PR number: ``{pr: {bucket?, subject?}}``."""
    if path is None:
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("overrides must be a mapping of PR number to corrections")
    overrides: dict[int, dict[str, str]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict) or not set(value) <= {"bucket", "subject"}:
            raise ValueError(f"override for {key!r} may only set bucket and subject")
        if value.get("bucket") not in (None, *BUCKETS):
            raise ValueError(f"override for {key!r} names an unknown bucket")
        overrides[int(key)] = {k: str(v) for k, v in value.items()}
    return overrides


def apply_overrides(notes: list[Note], overrides: dict[int, dict[str, str]]) -> list[Note]:
    corrected: list[Note] = []
    for note in notes:
        change = overrides.get(note.pr) if note.pr is not None else None
        if change is None:
            corrected.append(note)
            continue
        corrected.append(
            Note(
                note.sha,
                note.pr,
                change.get("bucket", note.bucket),
                note.scope,
                change.get("subject", note.subject),
            )
        )
    return corrected


def _line(note: Note) -> str:
    reference = f"#{note.pr}" if note.pr is not None else note.sha[:8]
    scope = f"**{note.scope}:** " if note.scope else ""
    return f"- {scope}{note.subject} ({reference})"


def render_markdown(notes: list[Note], *, rev_range: str) -> str:
    """Render numbered sections so the output satisfies the heading contract."""
    lines = [f"# Release notes — `{rev_range}`", ""]
    if not notes:
        lines.append("No changes in this range.")
        return "\n".join(lines) + "\n"
    number = 0
    for bucket in BUCKETS:
        entries = [note for note in notes if note.bucket == bucket]
        if not entries:
            continue
        number += 1
        lines.append(f"## {number}. {bucket}")
        lines.append("")
        lines.extend(_line(note) for note in entries)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_json(notes: list[Note], *, rev_range: str) -> str:
    return json.dumps({"range": rev_range, "notes": [asdict(note) for note in notes]}, indent=2) + "\n"


def latest_version_tag(repo_root: Path) -> str:
    return _git(repo_root, "describe", "--tags", "--abbrev=0", "--match", "v*").strip()


def resolve_range(repo_root: Path, rev_range: str) -> str:
    """Pin the range end to a full commit id so the block regenerates identically."""
    start, sep, end = rev_range.partition("..")
    if not sep:
        raise ValueError(f"expected <start>..<end>, got {rev_range!r}")
    return f"{start}..{_git(repo_root, 'rev-parse', '--verify', f'{end}^{{commit}}').strip()}"


def _counted_line(notes: list[Note]) -> str:
    counts = [
        f"{len([n for n in notes if n.bucket == bucket])} {bucket.lower()}"
        for bucket in BUCKETS
        if bucket not in _DETAILED_BUCKETS and any(n.bucket == bucket for n in notes)
    ]
    return "Also in this range: " + ", ".join(counts) + " — detailed in the curated entries below."


def render_changelog_block(notes: list[Note], *, rev_range: str) -> str:
    """Render the generated Unreleased summary that lives between the markers.

    Only breaking changes, security fixes and features are listed entry by
    entry; everything else is counted, so the block stays a concise summary
    while the curated entries below it keep the detail (#838 item 2).
    """
    head = rev_range.partition("..")[2]
    lines = [
        CHANGELOG_BEGIN,
        f"<!-- generated-range: {rev_range} -->",
        f"### 1.1. Generated summary — `{rev_range.partition('..')[0]}` through `{head[:8]}`",
        "",
        "Generated by `scripts/release_notes.py` from Conventional Commits; do not edit by hand. "
        "Regenerate with `uv run --project bootstrapper python -m scripts.release_notes --update-changelog`.",
        "",
    ]
    for bucket in _DETAILED_BUCKETS:
        entries = [note for note in notes if note.bucket == bucket]
        if entries:
            lines.append(f"**{bucket}**")
            lines.append("")
            lines.extend(_line(note) for note in entries)
            lines.append("")
    if any(note.bucket not in _DETAILED_BUCKETS for note in notes):
        lines.append(_counted_line(notes))
        lines.append("")
    lines.append(CHANGELOG_END)
    return "\n".join(lines) + "\n"


def replace_changelog_block(text: str, block: str) -> str:
    """Swap the generated block in the changelog text, keeping everything else."""
    if text.count(CHANGELOG_BEGIN) != 1 or text.count(CHANGELOG_END) != 1:
        raise ValueError("docs/CHANGELOG.md must contain exactly one generated release-notes block")
    start = text.index(CHANGELOG_BEGIN)
    end = text.index(CHANGELOG_END) + len(CHANGELOG_END) + 1
    return text[:start] + block + text[end:]


def committed_block_range(text: str) -> str:
    match = _RANGE_COMMENT_RE.search(text)
    if match is None:
        raise ValueError("the generated release-notes block records no range")
    return match.group("range")


def check_pull_request_title(title: str) -> bool:
    return CONVENTIONAL_TITLE_RE.match(title.strip()) is not None


def _changelog_notes(repo_root: Path, rev_range: str, overrides: Path | None) -> tuple[str, list[Note]]:
    pinned = resolve_range(repo_root, rev_range)
    notes = apply_overrides(collect_notes(repo_root, pinned), load_overrides(overrides))
    return pinned, notes


def update_changelog(repo_root: Path, rev_range: str, overrides: Path | None, *, check: bool) -> bool:
    """Regenerate (or verify) the block; returns True when the file is current."""
    path = repo_root / _CHANGELOG
    text = path.read_text(encoding="utf-8")
    pinned, notes = _changelog_notes(repo_root, rev_range, overrides)
    block = render_changelog_block(notes, rev_range=pinned)
    updated = replace_changelog_block(text, block)
    if updated == text:
        return True
    if not check:
        path.write_text(updated, encoding="utf-8")
    return False


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run release notes from Conventional Commits")
    parser.add_argument("--range", dest="rev_range", help="Git revision range, e.g. v0.1.0..origin/main")
    parser.add_argument("--since-tag", action="store_true", help="Use <latest v* tag>..HEAD")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--overrides", type=Path, help="YAML of {pr: {bucket, subject}} corrections")
    parser.add_argument("--output", type=Path, help="Write here instead of stdout")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--update-changelog", action="store_true",
        help="Regenerate the marked block in docs/CHANGELOG.md (range defaults to the committed one)",
    )
    parser.add_argument(
        "--check-changelog", action="store_true",
        help="Exit 1 when the marked block in docs/CHANGELOG.md is not what its recorded range renders",
    )
    parser.add_argument("--check-title", metavar="TITLE", help="Exit 1 unless TITLE is a Conventional Commits subject")
    args = parser.parse_args(argv)
    if args.check_title is not None:
        return args
    if args.check_changelog:
        args.rev_range = args.rev_range or committed_block_range(
            (args.repo / _CHANGELOG).read_text(encoding="utf-8")
        )
        return args
    if bool(args.rev_range) == args.since_tag:
        parser.error("pass exactly one of --range or --since-tag")
    return args


def _refuse_changelog(output: Path | None, repo_root: Path) -> None:
    if output is not None and output.resolve() == (repo_root / _CHANGELOG).resolve():
        raise SystemExit("refusing to overwrite docs/CHANGELOG.md; this is a dry run (#968)")


def _changelog_mode(args: argparse.Namespace) -> int:
    rev_range = args.rev_range or f"{latest_version_tag(args.repo)}..HEAD"
    current = update_changelog(args.repo, rev_range, args.overrides, check=args.check_changelog)
    if args.check_changelog:
        print("PASS generated release-notes block is current" if current else
              "FAIL docs/CHANGELOG.md generated block drifted; run --update-changelog")
        return 0 if current else 1
    print("generated release-notes block " + ("already current" if current else "updated"))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_title is not None:
        ok = check_pull_request_title(args.check_title)
        print(("PASS" if ok else "FAIL") + f" pull-request title {args.check_title!r}"
              + ("" if ok else " is not a Conventional Commits subject (type(scope)!: summary)"))
        return 0 if ok else 1
    if args.update_changelog or args.check_changelog:
        return _changelog_mode(args)
    repo_root: Path = args.repo
    _refuse_changelog(args.output, repo_root)
    rev_range = args.rev_range or f"{latest_version_tag(repo_root)}..HEAD"
    notes = apply_overrides(collect_notes(repo_root, rev_range), load_overrides(args.overrides))
    render: Any = render_json if args.format == "json" else render_markdown
    text = render(notes, rev_range=rev_range)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
