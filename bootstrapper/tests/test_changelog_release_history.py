"""Offline contracts for Atlas's public release and pre-tag milestone history."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import markdown as python_markdown
import pytest
import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token


ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "docs/CHANGELOG.md"
RELEASING = ROOT / "docs/deployment/releasing.md"

SEMVER = (
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)\."
    r"(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
DATE = r"\d{4}-\d{2}-\d{2}"
NUMBERED_PREFIX = r"(?:\d+\.\s+)?"
VERSION_TOKEN = re.compile(rf"(?<![0-9A-Za-z.])v?(?P<version>{SEMVER})(?![0-9A-Za-z.])")
RECORD_START = "<!-- atlas-release-record:start -->"
RECORD_END = "<!-- atlas-release-record:end -->"
RECORD_HEADER = ("Tag", "Tagged", "Tag object", "Target commit", "Target changelog")
RECORD_HEADER_MARKDOWN = "| " + " | ".join(RECORD_HEADER) + " |"
RECORD_DIVIDER = "| --- | --- | --- | --- | --- |"
TARGET_CHANGELOG_PRESENT = "contains-release-heading"
TARGET_CHANGELOG_LEGACY = "legacy-unreleased-exception"
LEGACY_TARGET_EXCEPTIONS = {"v0.1.0"}
WORKFLOW_LABELS = (
    "Finalize release notes",
    "Promote through Gitflow",
    "Create the tag",
    "Record immutable object IDs",
)
MARKDOWN = MarkdownIt("commonmark").enable("table")
VOID_HTML_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "basefont",
        "br",
        "col",
        "embed",
        "frame",
        "hr",
        "img",
        "input",
        "keygen",
        "link",
        "menuitem",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


@dataclass(frozen=True)
class HistoryEntry:
    kind: str
    version: str
    tagged: str


@dataclass(frozen=True)
class ReleaseRecord:
    tagged: str
    tag_object: str
    target_commit: str
    target_changelog: str


@dataclass(frozen=True)
class ClassifiedToken:
    token: Token
    raw_containers: tuple[str, ...]

    @property
    def is_top_level(self) -> bool:
        return self.token.level == 0 and not self.raw_containers


class _RawBlockContainerTracker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        tag = tag.lower()
        if tag not in VOID_HTML_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag, attrs

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_HTML_ELEMENTS:
            return
        assert self.stack, f"unmatched raw HTML container close: </{tag}>"
        expected = self.stack[-1]
        assert expected == tag, (
            "mismatched raw HTML container close: "
            f"expected </{expected}>, found </{tag}>"
        )
        self.stack.pop()


def _classify_markdown_tokens(markdown: str) -> list[ClassifiedToken]:
    tracker = _RawBlockContainerTracker()
    classified: list[ClassifiedToken] = []
    for token in MARKDOWN.parse(markdown):
        classified.append(ClassifiedToken(token, tuple(tracker.stack)))
        if token.type == "html_block":
            tracker.feed(token.content)
    tracker.close()
    assert not tracker.stack, f"unclosed raw HTML containers: {tracker.stack}"
    return classified


def _workflow_labels(markdown: str) -> tuple[str, ...]:
    classified = _classify_markdown_tokens(markdown)
    in_release_section = False
    in_ordered_list = False
    in_list_item = False
    labels: list[str] = []
    for index, item in enumerate(classified):
        token = item.token
        if item.is_top_level and token.type == "heading_open" and token.tag == "h2":
            inline = classified[index + 1].token
            if in_release_section:
                break
            in_release_section = inline.content.endswith("Cutting a release (maintainer)")
            continue
        if not in_release_section:
            continue
        if item.is_top_level and token.type == "ordered_list_open":
            in_ordered_list = True
            continue
        if token.type == "ordered_list_close" and token.level == 0:
            in_ordered_list = False
            continue
        if token.type == "list_item_open" and in_ordered_list:
            in_list_item = True
            continue
        if token.type == "list_item_close" and in_ordered_list:
            in_list_item = False
            continue
        if (
            token.type != "inline"
            or item.raw_containers
            or not in_ordered_list
            or not in_list_item
        ):
            continue
        children = [
            child
            for child in token.children or []
            if child.type != "text" or child.content
        ]
        if len(children) >= 3 and children[0].type == "strong_open":
            labels.append(children[1].content)
    return tuple(labels)


def _release_workflow_ordered_list_count(markdown: str) -> int:
    classified = _classify_markdown_tokens(markdown)
    in_release_section = False
    count = 0
    for index, item in enumerate(classified):
        token = item.token
        if item.is_top_level and token.type == "heading_open" and token.tag == "h2":
            inline = classified[index + 1].token
            if in_release_section:
                break
            in_release_section = inline.content.endswith("Cutting a release (maintainer)")
            continue
        if in_release_section and item.is_top_level and token.type == "ordered_list_open":
            count += 1
    return count


def _release_tag_inventory(raw_tags: list[str]) -> set[str]:
    invalid = sorted(tag for tag in raw_tags if not re.fullmatch(rf"v{SEMVER}", tag))
    assert not invalid, f"invalid release-style tags: {invalid}"
    return set(raw_tags)


def _checked_out_v_tags() -> list[str]:
    return subprocess.run(
        ["git", "tag", "--list", "v*"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


def _visible_inline_text(token: Token) -> str:
    return "".join(
        child.content
        for child in token.children or []
        if child.type in {"text", "code_inline"}
    )


def _history_entries(markdown: str) -> tuple[list[HistoryEntry], list[str]]:
    classified = _classify_markdown_tokens(markdown)
    entries: list[HistoryEntry] = []
    invalid: list[str] = []
    for index, item in enumerate(classified):
        token = item.token
        if token.type != "heading_open" or token.tag != "h2" or not item.is_top_level:
            continue
        inline = classified[index + 1].token
        children = inline.children or []
        entry: HistoryEntry | None = None

        release_children = children
        if (
            release_children
            and release_children[0].type == "text"
            and re.fullmatch(r"\d+\.\s+", release_children[0].content)
        ):
            release_children = release_children[1:]
        if (
            len(release_children) == 4
            and release_children[0].type == "link_open"
            and release_children[1].type == "text"
            and release_children[2].type == "link_close"
            and release_children[3].type == "text"
            and re.fullmatch(SEMVER, release_children[1].content)
        ):
            version = release_children[1].content
            tail = re.fullmatch(
                rf"\s+-\s+(?P<date>{DATE})(?:\s+.*)?",
                release_children[3].content,
            )
            if (
                tail
                and release_children[0].attrGet("href")
                == f"https://github.com/thekaveh/atlas/tree/v{version}"
            ):
                entry = HistoryEntry("release", version, tail.group("date"))

        if (
            entry is None
            and len(children) == 3
            and children[0].type == "text"
            and re.fullmatch(
                rf"{NUMBERED_PREFIX}Historical milestone:\s+",
                children[0].content,
            )
            and children[1].type == "code_inline"
            and re.fullmatch(SEMVER, children[1].content)
            and children[2].type == "text"
        ):
            tail = re.fullmatch(rf"\s+-\s+(?P<date>{DATE})(?:\s+.*)?", children[2].content)
            if tail:
                entry = HistoryEntry("milestone", children[1].content, tail.group("date"))

        if entry is not None:
            entries.append(entry)
        elif VERSION_TOKEN.search(_visible_inline_text(inline)):
            invalid.append(inline.content)
    return entries, invalid


def _has_top_level_unreleased_heading(markdown: str) -> bool:
    classified = _classify_markdown_tokens(markdown)
    return any(
        item.token.type == "heading_open"
        and item.token.tag == "h2"
        and item.is_top_level
        and re.fullmatch(
            rf"{NUMBERED_PREFIX}\[Unreleased\]",
            classified[index + 1].token.content,
        )
        for index, item in enumerate(classified)
    )


def _table_rows(tokens: list[Token]) -> list[list[tuple[str, Token]]]:
    assert tokens and tokens[0].type == "table_open" and tokens[-1].type == "table_close", (
        "release-record markers must contain one rendered Markdown table"
    )
    rows: list[list[tuple[str, Token]]] = []
    row: list[tuple[str, Token]] | None = None
    cell_kind: str | None = None
    for token in tokens:
        if token.type == "tr_open":
            assert row is None, "nested release-record table row"
            row = []
        elif token.type in {"th_open", "td_open"}:
            cell_kind = token.type[:2]
        elif token.type == "inline" and row is not None and cell_kind is not None:
            row.append((cell_kind, token))
            cell_kind = None
        elif token.type == "tr_close":
            assert row is not None, "release-record table row closes without opening"
            rows.append(row)
            row = None
    return rows


def _release_record(markdown: str) -> dict[str, ReleaseRecord]:
    classified = _classify_markdown_tokens(markdown)
    tokens = [item.token for item in classified]
    starts = [
        index
        for index, item in enumerate(classified)
        if item.is_top_level
        and item.token.type == "html_block"
        and item.token.content.strip() == RECORD_START
    ]
    ends = [
        index
        for index, item in enumerate(classified)
        if item.is_top_level
        and item.token.type == "html_block"
        and item.token.content.strip() == RECORD_END
    ]
    assert len(starts) == 1, "missing or duplicate rendered release-record start marker"
    assert len(ends) == 1, "missing or duplicate rendered release-record end marker"
    assert starts[0] < ends[0], "release-record end marker precedes start marker"

    table_rows = _table_rows(tokens[starts[0] + 1 : ends[0]])
    assert table_rows, "release-record table is empty"
    header = tuple(_visible_inline_text(cell) for kind, cell in table_rows[0] if kind == "th")
    assert header == RECORD_HEADER, "malformed release-record table header"

    records: dict[str, ReleaseRecord] = {}
    dates: list[str] = []
    for table_row in table_rows[1:]:
        assert len(table_row) == len(RECORD_HEADER), "malformed release-record row width"
        values: list[str] = []
        for kind, cell in table_row:
            children = cell.children or []
            assert kind == "td" and len(children) == 1 and children[0].type == "code_inline", (
                "malformed release-record row: data cells must contain one code value"
            )
            values.append(children[0].content)
        tag, tagged, tag_object, target_commit, target_changelog = values
        assert re.fullmatch(rf"v{SEMVER}", tag), f"malformed release-record tag: {tag}"
        assert tag not in records, f"duplicate release-record tag: {tag}"
        date.fromisoformat(tagged)
        assert re.fullmatch(r"[0-9a-f]{40}", tag_object), f"malformed tag object: {tag_object}"
        assert re.fullmatch(r"[0-9a-f]{40}", target_commit), (
            f"malformed target commit: {target_commit}"
        )
        assert target_changelog in {TARGET_CHANGELOG_PRESENT, TARGET_CHANGELOG_LEGACY}, (
            f"invalid target changelog policy: {target_changelog}"
        )
        if target_changelog == TARGET_CHANGELOG_LEGACY:
            assert tag in LEGACY_TARGET_EXCEPTIONS, f"unapproved target changelog exception: {tag}"
        dates.append(tagged)
        records[tag] = ReleaseRecord(tagged, tag_object, target_commit, target_changelog)
    assert records, "release record must contain at least one immutable tag"
    assert dates == sorted(dates, reverse=True), "release record must be newest first"
    return records


def _history_errors(
    changelog: str,
    release_dates: dict[str, str],
    checked_out_tags: set[str],
) -> list[str]:
    errors: list[str] = []
    classified: list[tuple[str, str, str]] = []
    release_versions: set[str] = set()
    milestone_versions: set[str] = set()
    unrecorded_release_versions: list[str] = []

    entries, invalid_headings = _history_entries(changelog)
    for entry in entries:
        if entry.kind == "release":
            version = entry.version
            tag = f"v{version}"
            classified.append(("release", version, entry.tagged))
            if version in release_versions:
                errors.append(f"duplicate release heading: {version}")
            release_versions.add(version)
            try:
                date.fromisoformat(entry.tagged)
            except ValueError:
                errors.append(f"release heading {version} has an invalid date")
            if tag not in release_dates:
                unrecorded_release_versions.append(version)
            elif entry.tagged != release_dates[tag]:
                errors.append(f"release heading {version} date differs from its tag record")
        else:
            version = entry.version
            classified.append(("milestone", version, entry.tagged))
            if version in milestone_versions:
                errors.append(f"duplicate historical milestone heading: {version}")
            milestone_versions.add(version)
            try:
                date.fromisoformat(entry.tagged)
            except ValueError:
                errors.append(f"historical milestone {version} has an invalid date")
            if f"v{version}" in release_dates:
                errors.append(f"tagged release {version} is mislabeled as a historical milestone")
    errors.extend(
        f"version-like H2 does not use the release-history schema: {heading}"
        for heading in invalid_headings
    )

    overlap = release_versions & milestone_versions
    if overlap:
        errors.append(f"versions classified as both release and milestone: {sorted(overlap)}")

    tags_without_records = checked_out_tags - set(release_dates)
    if tags_without_records:
        errors.append(
            "checked-out release tag lacks an immutable release record: "
            f"{sorted(tags_without_records)}"
        )

    prepared_version = (
        unrecorded_release_versions[0]
        if len(unrecorded_release_versions) == 1
        else None
    )
    prepared_is_newest = bool(
        prepared_version
        and classified
        and classified[0][:2] == ("release", prepared_version)
    )
    prepared_tag_absent = bool(
        prepared_version and f"v{prepared_version}" not in checked_out_tags
    )
    if not (prepared_is_newest and prepared_tag_absent):
        errors.extend(
            f"release heading {version} has no immutable tag record"
            for version in unrecorded_release_versions
        )

    missing_headings = {tag.removeprefix("v") for tag in release_dates} - release_versions
    if missing_headings:
        errors.append(f"immutable tags lack release headings: {sorted(missing_headings)}")

    dates = [date for _kind, _version, date in classified]
    if dates != sorted(dates, reverse=True):
        errors.append("release and milestone headings must be newest first")

    return errors


def _validate_target_changelog(tag: str, record: ReleaseRecord, changelog: str) -> None:
    target_releases = {
        f"v{entry.version}": entry.tagged
        for entry in _history_entries(changelog)[0]
        if entry.kind == "release"
    }
    if record.target_changelog == TARGET_CHANGELOG_PRESENT:
        assert tag in target_releases, f"tag target lacks its release heading: {tag}"
        assert target_releases[tag] == record.tagged, (
            f"tag target release date differs from the record: {tag}"
        )
    else:
        assert tag in LEGACY_TARGET_EXCEPTIONS
        assert tag not in target_releases
        assert _has_top_level_unreleased_heading(changelog), (
            f"legacy tag target lacks a top-level Unreleased heading: {tag}"
        )


def test_changelog_versions_are_tagged_releases_or_explicit_historical_milestones() -> None:
    record = _release_record(RELEASING.read_text(encoding="utf-8"))
    release_dates = {tag: values.tagged for tag, values in record.items()}
    checked_out_tags = _release_tag_inventory(_checked_out_v_tags())
    errors = _history_errors(
        CHANGELOG.read_text(encoding="utf-8"),
        release_dates,
        checked_out_tags,
    )
    assert not errors, "\n".join(errors)
    assert {
        tag
        for tag, values in record.items()
        if values.target_changelog == TARGET_CHANGELOG_LEGACY
    } == LEGACY_TARGET_EXCEPTIONS


def test_release_record_matches_tags_and_targets_in_a_complete_checkout() -> None:
    record = _release_record(RELEASING.read_text(encoding="utf-8"))
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if shallow == "true":
        return

    release_tags = _release_tag_inventory(_checked_out_v_tags())
    assert release_tags == set(record), "committed release record differs from checked-out tags"

    for tag, expected in record.items():
        actual_type = subprocess.run(
            ["git", "cat-file", "-t", f"refs/tags/{tag}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert actual_type == "tag", f"release tag {tag} must be annotated"
        actual_object = subprocess.run(
            ["git", "rev-parse", f"refs/tags/{tag}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert actual_object == expected.tag_object, f"immutable tag object {tag} moved"
        actual_target = subprocess.run(
            ["git", "rev-list", "-n", "1", tag],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert actual_target == expected.target_commit, f"immutable tag {tag} moved"
        actual_date = subprocess.run(
            [
                "git",
                "for-each-ref",
                "--format=%(creatordate:short)",
                f"refs/tags/{tag}",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert actual_date == expected.tagged, f"recorded tag date differs for {tag}"

        target_changelog = subprocess.run(
            ["git", "show", f"{expected.target_commit}:docs/CHANGELOG.md"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        _validate_target_changelog(tag, expected, target_changelog)


def test_history_parser_accepts_semver_prereleases_builds_and_tag_links() -> None:
    changelog = "\n".join(
        [
            "## [Unreleased]",
            "## [1.2.3-rc.1](https://github.com/thekaveh/atlas/tree/v1.2.3-rc.1) - 2026-08-30",
            "## [1.2.3+build.5](https://github.com/thekaveh/atlas/tree/v1.2.3+build.5) - 2026-08-29",
            "## Historical milestone: `1.0.0` - 2026-08-01",
        ]
    )
    release_dates = {
        "v1.2.3-rc.1": "2026-08-30",
        "v1.2.3+build.5": "2026-08-29",
    }
    assert _history_errors(changelog, release_dates, set(release_dates)) == []


def test_history_parser_rejects_unrecorded_duplicate_and_out_of_order_versions() -> None:
    changelog = "\n".join(
        [
            "## Historical milestone: `1.0.0` - 2025-01-01",
            "## [2.0.0](https://github.com/thekaveh/atlas/tree/v2.0.0) - 2026-01-01",
            "## [2.0.0](https://github.com/thekaveh/atlas/tree/v2.0.0) - 2025-12-01",
        ]
    )
    errors = _history_errors(changelog, {}, set())
    assert any("newest first" in error for error in errors)
    assert any("duplicate release heading" in error for error in errors)
    assert sum("has no immutable tag record" in error for error in errors) == 2


def test_history_parser_rejects_ambiguous_or_inexact_release_links() -> None:
    changelog = "\n".join(
        [
            "## Version 3.0.0 - 2026-01-01",
            "## [4.0.0](https://github.com/thekaveh/atlas/tree/v4.0.1) - 2026-01-01",
        ]
    )
    errors = _history_errors(changelog, {}, set())
    assert sum("does not use the release-history schema" in error for error in errors) == 2


def test_history_parser_rejects_missing_heading_date_and_wrong_classification() -> None:
    changelog = "\n".join(
        [
            "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-29",
            "## Historical milestone: `2.0.0` - 2026-08-01",
        ]
    )
    errors = _history_errors(
        changelog,
        {"v1.2.3": "2026-08-30", "v2.0.0": "2026-08-01", "v3.0.0": "2026-07-01"},
        {"v1.2.3", "v2.0.0", "v3.0.0"},
    )
    assert any("date differs from its tag record" in error for error in errors)
    assert any("mislabeled as a historical milestone" in error for error in errors)
    assert any("immutable tags lack release headings" in error for error in errors)


def test_release_record_rejects_duplicate_out_of_order_and_invalid_rows() -> None:
    sha_a = "a" * 40
    sha_b = "b" * 40

    duplicate = "\n".join(
        [
            RECORD_START,
            RECORD_HEADER_MARKDOWN,
            RECORD_DIVIDER,
            f"| `v1.0.0` | `2026-01-01` | `{sha_a}` | `{sha_b}` | `{TARGET_CHANGELOG_PRESENT}` |",
            f"| `v1.0.0` | `2026-01-01` | `{sha_a}` | `{sha_b}` | `{TARGET_CHANGELOG_PRESENT}` |",
            RECORD_END,
        ]
    )
    with pytest.raises(AssertionError, match="duplicate release-record tag"):
        _release_record(duplicate)

    out_of_order = "\n".join(
        [
            RECORD_START,
            RECORD_HEADER_MARKDOWN,
            RECORD_DIVIDER,
            f"| `v1.0.0` | `2025-01-01` | `{sha_a}` | `{sha_b}` | `{TARGET_CHANGELOG_PRESENT}` |",
            f"| `v1.1.0` | `2026-01-01` | `{sha_b}` | `{sha_a}` | `{TARGET_CHANGELOG_PRESENT}` |",
            RECORD_END,
        ]
    )
    with pytest.raises(AssertionError, match="release record must be newest first"):
        _release_record(out_of_order)

    malformed = "\n".join(
        [
            RECORD_START,
            RECORD_HEADER_MARKDOWN,
            RECORD_DIVIDER,
            f"| v1.0.0 | {sha_a} |",
            RECORD_END,
        ]
    )
    with pytest.raises(AssertionError, match="malformed release-record row"):
        _release_record(malformed)


def test_fenced_release_heading_does_not_satisfy_required_release_history() -> None:
    changelog = "\n".join(
        [
            "```md",
            "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
            "```",
        ]
    )
    errors = _history_errors(
        changelog,
        {"v1.2.3": "2026-08-30"},
        {"v1.2.3"},
    )
    assert any("immutable tags lack release headings" in error for error in errors)


def test_fenced_release_record_table_is_not_authoritative() -> None:
    sha = "a" * 40
    releasing = "\n".join(
        [
            "```md",
            RECORD_START,
            RECORD_HEADER_MARKDOWN,
            RECORD_DIVIDER,
            f"| `v1.0.0` | `2026-01-01` | `{sha}` | `{sha}` | `{TARGET_CHANGELOG_PRESENT}` |",
            RECORD_END,
            "```",
        ]
    )
    with pytest.raises(AssertionError, match="release-record"):
        _release_record(releasing)


def test_marker_text_inside_fence_is_ignored_when_real_record_exists() -> None:
    sha = "a" * 40
    releasing = "\n".join(
        [
            "```md",
            RECORD_START,
            RECORD_END,
            "```",
            RECORD_START,
            RECORD_HEADER_MARKDOWN,
            RECORD_DIVIDER,
            f"| `v1.0.0` | `2026-01-01` | `{sha}` | `{sha}` | `{TARGET_CHANGELOG_PRESENT}` |",
            RECORD_END,
        ]
    )
    assert set(_release_record(releasing)) == {"v1.0.0"}


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        ('<details markdown="1" class="audit">', "</details>"),
        ('<div markdown data-surface="release">', "</div>"),
    ],
)
def test_raw_html_block_container_hides_release_heading(
    opening: str,
    closing: str,
) -> None:
    changelog = "\n\n".join(
        [
            opening,
            "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
            closing,
        ]
    )
    errors = _history_errors(changelog, {"v1.2.3": "2026-08-30"}, {"v1.2.3"})
    assert any("immutable tags lack release headings" in error for error in errors)


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        ('<details markdown="1" class="audit">', "</details>"),
        ('<div markdown data-surface="record">', "</div>"),
    ],
)
def test_raw_html_block_container_hides_release_record(
    opening: str,
    closing: str,
) -> None:
    sha = "a" * 40
    releasing = "\n\n".join(
        [
            opening,
            "\n".join(
                [
                    RECORD_START,
                    RECORD_HEADER_MARKDOWN,
                    RECORD_DIVIDER,
                    f"| `v1.0.0` | `2026-01-01` | `{sha}` | `{sha}` | `{TARGET_CHANGELOG_PRESENT}` |",
                    RECORD_END,
                ]
            ),
            closing,
        ]
    )
    with pytest.raises(AssertionError, match="release-record"):
        _release_record(releasing)


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        ('<details markdown="1" class="audit">', "</details>"),
        ('<div markdown data-surface="workflow">', "</div>"),
    ],
)
def test_raw_html_block_container_hides_complete_release_workflow(
    opening: str,
    closing: str,
) -> None:
    workflow = "\n\n".join(
        [
            "## 3. Cutting a release (maintainer)",
            opening,
            "\n".join(
                f"{index}. **{label}.**"
                for index, label in enumerate(WORKFLOW_LABELS, start=1)
            ),
            closing,
        ]
    )
    assert _workflow_labels(workflow) == ()


def test_nested_raw_html_block_containers_hide_policy_content() -> None:
    hidden = "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30"
    visible = "## [1.2.2](https://github.com/thekaveh/atlas/tree/v1.2.2) - 2026-08-29"
    changelog = "\n\n".join(
        [
            '<details markdown="1">',
            '<div markdown class="nested">',
            hidden,
            "</div>",
            "</details>",
            visible,
        ]
    )
    entries, invalid = _history_entries(changelog)
    assert invalid == []
    assert entries == [HistoryEntry("release", "1.2.2", "2026-08-29")]


@pytest.mark.parametrize(
    "tag",
    [
        "article",
        "aside",
        "figure",
        "footer",
        "header",
        "main",
        "nav",
        "section",
        "template",
        "x-policy-container",
    ],
)
def test_every_raw_html_block_element_is_treated_as_a_container(tag: str) -> None:
    hidden = "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30"
    changelog = f'<{tag} markdown="1">\n\n{hidden}\n\n</{tag}>'
    entries, invalid = _history_entries(changelog)
    assert entries == []
    assert invalid == []


def _closed_raw_html_prefix() -> str:
    return "\n\n".join(
        [
            '<div class="closed"><section data-value=">ok"></section></div>',
            "<hr>",
            '<aside data-mode="self-closing" />',
        ]
    )


def test_closed_same_token_void_and_self_closing_html_preserve_later_release() -> None:
    changelog = "\n\n".join(
        [
            _closed_raw_html_prefix(),
            "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
        ]
    )
    assert _history_errors(
        changelog,
        {"v1.2.3": "2026-08-30"},
        {"v1.2.3"},
    ) == []


def test_closed_same_token_void_and_self_closing_html_preserve_later_record() -> None:
    sha = "a" * 40
    releasing = "\n\n".join(
        [
            _closed_raw_html_prefix(),
            "\n".join(
                [
                    RECORD_START,
                    RECORD_HEADER_MARKDOWN,
                    RECORD_DIVIDER,
                    f"| `v1.0.0` | `2026-01-01` | `{sha}` | `{sha}` | `{TARGET_CHANGELOG_PRESENT}` |",
                    RECORD_END,
                ]
            ),
        ]
    )
    assert set(_release_record(releasing)) == {"v1.0.0"}


def test_closed_same_token_void_and_self_closing_html_preserve_later_workflow() -> None:
    workflow = "\n\n".join(
        [
            _closed_raw_html_prefix(),
            "## 3. Cutting a release (maintainer)",
            "\n".join(
                f"{index}. **{label}** — required"
                for index, label in enumerate(WORKFLOW_LABELS, start=1)
            ),
        ]
    )
    assert _workflow_labels(workflow) == WORKFLOW_LABELS


def test_mismatched_raw_html_container_close_fails_closed() -> None:
    changelog = "\n\n".join(
        [
            '<div markdown="1">',
            "## hidden",
            "</section>",
            "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
        ]
    )
    with pytest.raises(AssertionError, match="mismatched raw HTML container close"):
        _history_entries(changelog)


def test_unclosed_raw_html_container_fails_closed() -> None:
    changelog = "\n\n".join(
        [
            '<details markdown="1">',
            "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
        ]
    )
    with pytest.raises(AssertionError, match="unclosed raw HTML containers"):
        _history_entries(changelog)


def test_release_workflow_requires_pre_tag_gitflow_then_post_tag_recording() -> None:
    releasing = RELEASING.read_text(encoding="utf-8")
    assert _workflow_labels(releasing) == WORKFLOW_LABELS
    assert _release_workflow_ordered_list_count(releasing) == 1
    guidance = " ".join(releasing.split())
    assert "permits exactly one brief automatic transition state" in guidance
    assert "newest classified history entry" in guidance
    assert "matching `vX.Y.Z` tag does not yet exist" in guidance


def test_release_workflow_renders_as_one_four_step_list_on_docs_surfaces() -> None:
    releasing = RELEASING.read_text(encoding="utf-8")
    section = releasing.split("## 3. Cutting a release (maintainer)", 1)[1].split(
        "## 4. Immutable release record",
        1,
    )[0]
    rendered = python_markdown.markdown(section, extensions=["pymdownx.superfences"])
    assert rendered.count("<ol>") == 1
    assert rendered.count("<li>") == 4
    assert rendered.count("<pre") == 2


def test_release_workflow_contract_rejects_tagging_before_gitflow() -> None:
    markdown = "\n".join(
        [
            "## 3. Cutting a release (maintainer)",
            "1. **Finalize release notes.**",
            "2. **Create the tag.**",
            "3. **Promote through Gitflow.**",
            "4. **Record immutable object IDs.**",
        ]
    )
    assert _workflow_labels(markdown) != WORKFLOW_LABELS


def test_services_lint_runs_release_guard_with_full_tag_history() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/services-lint.yml").read_text())
    steps = workflow["jobs"]["lint"]["steps"]
    checkout = next(step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@"))
    assert checkout.get("with", {}).get("fetch-depth") == 0
    assert checkout.get("with", {}).get("fetch-tags", True) is not False

    unit_test = next(step for step in steps if step.get("name", "").startswith("Run unit tests"))
    command = unit_test["run"]
    assert unit_test.get("working-directory") == "bootstrapper"
    assert "pytest tests/" in command
    assert "--ignore=tests/test_changelog_release_history.py" not in command
    assert (ROOT / unit_test["working-directory"] / "tests/test_changelog_release_history.py").is_file()


@pytest.mark.parametrize("malformed", ["vnext", "v0.2", "v1.2.3.4", "v01.2.3"])
def test_raw_v_prefixed_tag_inventory_rejects_non_semver(malformed: str) -> None:
    with pytest.raises(AssertionError, match="invalid release-style tags"):
        _release_tag_inventory(["v0.1.0", malformed])


@pytest.mark.parametrize(
    "nested",
    [
        "> ## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
        "- item\n\n  ## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
        "<h2><a href='https://github.com/thekaveh/atlas/tree/v1.2.3'>1.2.3</a> - 2026-08-30</h2>",
    ],
)
def test_nested_or_html_release_heading_is_not_a_top_level_policy_heading(nested: str) -> None:
    errors = _history_errors(nested, {"v1.2.3": "2026-08-30"}, {"v1.2.3"})
    assert any("immutable tags lack release headings" in error for error in errors)


@pytest.mark.parametrize("container", ["blockquote", "list"])
def test_nested_release_record_table_is_not_authoritative(container: str) -> None:
    sha = "a" * 40
    rows = [
        RECORD_START,
        "",
        RECORD_HEADER_MARKDOWN,
        RECORD_DIVIDER,
        f"| `v1.0.0` | `2026-01-01` | `{sha}` | `{sha}` | `{TARGET_CHANGELOG_PRESENT}` |",
        "",
        RECORD_END,
    ]
    if container == "blockquote":
        releasing = "\n".join(f"> {row}" if row else ">" for row in rows)
    else:
        releasing = "- record\n\n" + "\n".join(f"  {row}" if row else "" for row in rows)
    with pytest.raises(AssertionError, match="release-record"):
        _release_record(releasing)


def test_duplicate_rendered_record_markers_are_rejected() -> None:
    with pytest.raises(AssertionError, match="duplicate rendered release-record start"):
        _release_record(f"{RECORD_START}\n{RECORD_START}\n{RECORD_END}")


def test_release_record_rejects_an_unapproved_legacy_target_exception() -> None:
    sha = "a" * 40
    releasing = "\n".join(
        [
            RECORD_START,
            RECORD_HEADER_MARKDOWN,
            RECORD_DIVIDER,
            f"| `v1.0.0` | `2026-01-01` | `{sha}` | `{sha}` | `{TARGET_CHANGELOG_LEGACY}` |",
            RECORD_END,
        ]
    )
    with pytest.raises(AssertionError, match="unapproved target changelog exception"):
        _release_record(releasing)


def test_release_record_rejects_extra_cells_and_multiple_tables() -> None:
    sha = "a" * 40
    extra_cell = "\n".join(
        [
            RECORD_START,
            "| Tag | Tagged | Tag object | Target commit | Target changelog | Extra |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| `v1.0.0` | `2026-01-01` | `{sha}` | `{sha}` | `{TARGET_CHANGELOG_PRESENT}` | `extra` |",
            RECORD_END,
        ]
    )
    with pytest.raises(AssertionError, match="table header"):
        _release_record(extra_cell)

    table = "\n".join(
        [
            RECORD_HEADER_MARKDOWN,
            RECORD_DIVIDER,
            f"| `v1.0.0` | `2026-01-01` | `{sha}` | `{sha}` | `{TARGET_CHANGELOG_PRESENT}` |",
        ]
    )
    with pytest.raises(AssertionError, match="release-record"):
        _release_record(f"{RECORD_START}\n{table}\n\n{table}\n{RECORD_END}")


def test_future_tag_target_requires_real_matching_release_heading() -> None:
    record = ReleaseRecord(
        tagged="2026-08-30",
        tag_object="a" * 40,
        target_commit="b" * 40,
        target_changelog=TARGET_CHANGELOG_PRESENT,
    )
    valid = "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30"
    _validate_target_changelog("v1.2.3", record, valid)

    fenced = f"```md\n{valid}\n```"
    with pytest.raises(AssertionError, match="target lacks its release heading"):
        _validate_target_changelog("v1.2.3", record, fenced)

    wrong_date = "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-29"
    with pytest.raises(AssertionError, match="date differs"):
        _validate_target_changelog("v1.2.3", record, wrong_date)


@pytest.mark.parametrize(
    "changelog",
    [
        "```md\n## [Unreleased]\n```",
        "> ## [Unreleased]",
        "- history\n\n  ## [Unreleased]",
    ],
)
def test_legacy_target_exception_requires_real_top_level_unreleased_heading(
    changelog: str,
) -> None:
    record = ReleaseRecord(
        tagged="2026-06-21",
        tag_object="a" * 40,
        target_commit="b" * 40,
        target_changelog=TARGET_CHANGELOG_LEGACY,
    )
    with pytest.raises(AssertionError, match="top-level Unreleased heading"):
        _validate_target_changelog("v0.1.0", record, changelog)


def test_one_newest_unrecorded_heading_is_allowed_before_its_tag_exists() -> None:
    changelog = "\n".join(
        [
            "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
            "## [1.2.2](https://github.com/thekaveh/atlas/tree/v1.2.2) - 2026-08-29",
        ]
    )
    assert _history_errors(changelog, {"v1.2.2": "2026-08-29"}, {"v1.2.2"}) == []


def test_two_unrecorded_headings_are_rejected() -> None:
    changelog = "\n".join(
        [
            "## [1.2.4](https://github.com/thekaveh/atlas/tree/v1.2.4) - 2026-08-31",
            "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
        ]
    )
    errors = _history_errors(changelog, {}, set())
    assert sum("has no immutable tag record" in error for error in errors) == 2


def test_unrecorded_heading_is_rejected_when_it_is_not_newest() -> None:
    changelog = "\n".join(
        [
            "## Historical milestone: `2.0.0` - 2026-08-31",
            "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30",
        ]
    )
    errors = _history_errors(changelog, {}, set())
    assert any("has no immutable tag record" in error for error in errors)


@pytest.mark.parametrize("checked_out_tag", ["v1.2.3", "v9.9.9"])
def test_unrecorded_heading_is_rejected_when_any_unrecorded_tag_exists(
    checked_out_tag: str,
) -> None:
    changelog = (
        "## [1.2.3](https://github.com/thekaveh/atlas/tree/v1.2.3) - 2026-08-30"
    )
    errors = _history_errors(changelog, {}, {checked_out_tag})
    assert any("lacks an immutable release record" in error for error in errors)
