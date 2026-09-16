"""The advertised support route must exist and stay available (#1049).

GitHub Discussions is disabled on thekaveh/atlas (repository metadata
``has_discussions: false``, re-read 2026-09-16), so no reader-facing page may
send people there. When a maintainer explicitly enables and moderates
Discussions, delete ``test_no_reader_facing_page_links_the_disabled_discussions``
in the same change that reintroduces the link.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DISCUSSIONS_URL = "https://github.com/thekaveh/atlas/discussions"
ISSUES_URL = "https://github.com/thekaveh/atlas/issues"
# History and archived planning notes may mention the old route; every page a
# reader is sent to may not.
_HISTORICAL_PREFIXES = ("docs/superpowers/", "docs/CHANGELOG.md")


def _reader_facing_markdown() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode().split("\0")
    return [
        ROOT / relative
        for relative in tracked
        if relative and not relative.startswith(_HISTORICAL_PREFIXES)
    ]


def test_no_reader_facing_page_links_the_disabled_discussions() -> None:
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in _reader_facing_markdown()
        if DISCUSSIONS_URL in path.read_text(encoding="utf-8", errors="replace")
    ]

    assert offenders == [], offenders


def test_readme_support_section_routes_bugs_questions_and_security_separately() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^## \d+\. Support\n(?P<body>.*?)(?=^## |\Z)", readme, re.M | re.S)
    assert match is not None, "README must keep a numbered Support section"
    body = match.group("body")

    assert ISSUES_URL in body
    assert f"{ISSUES_URL}/new?labels=question" in body
    assert "[security policy](SECURITY.md)" in body
    assert "public issue" in body.lower()


def test_documentation_map_getting_help_separates_security_reports() -> None:
    text = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^## \d+\. Getting help\n(?P<body>.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match is not None
    body = match.group("body")

    assert "issue" in body.lower()
    assert "../SECURITY.md" in body


def test_troubleshooting_community_resources_point_at_live_destinations() -> None:
    text = (ROOT / "docs" / "quick-start" / "troubleshooting.md").read_text(encoding="utf-8")
    match = re.search(r"^### \d+\.\d+\. Community Resources\n(?P<body>.*?)(?=^#|\Z)", text, re.M | re.S)
    assert match is not None
    body = match.group("body")

    assert ISSUES_URL in body
    assert f"{ISSUES_URL}/new?labels=question" in body
    assert DISCUSSIONS_URL not in body
