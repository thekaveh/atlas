"""Decide which manifest-owned images a diff actually affects.

Scanning is driven by the image references a change moves, not by the files it
lands in. A manifest can change while every pinned image stays put — a new env
var, a note, a dependency hint — and those edits deploy exactly what they did
before, so scanning their images reports findings the diff neither introduced
nor can fix. Keeping that judgement here keeps `container_security` focused on
policy and scanning.
"""

from __future__ import annotations

import re
from typing import Sequence

CHANGED_SERVICE_FILE_RE = re.compile(
    r" b/services/(?P<service>[^/]+)/"
    r"(?P<file>service\.yml|compose\.yml|(?:.*/)?Dockerfile)$"
)


def changed_service_files(changed_diff: Sequence[str]) -> set[tuple[str, str]]:
    """Return the (service, filename) pairs a diff touches."""
    changed: set[tuple[str, str]] = set()
    for line in changed_diff:
        match = CHANGED_SERVICE_FILE_RE.search(line)
        if match:
            changed.add((match.group("service"), match.group("file")))
    return changed


def touched_lines_by_file(
    changed_diff: Sequence[str],
) -> dict[tuple[str, str], list[str]]:
    """Group added and removed content lines by the service file they belong to.

    Grouping per file keeps the judgement local, so one manifest's edit cannot
    make another manifest's images look changed.
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    current: tuple[str, str] | None = None
    for line in changed_diff:
        match = CHANGED_SERVICE_FILE_RE.search(line)
        if match:
            current = (match.group("service"), match.group("file"))
            grouped.setdefault(current, [])
        elif current and line[:1] in {"+", "-"} and line[:3] not in {"+++", "---"}:
            grouped[current].append(line[1:])
    return grouped


def select_touched(images: set[str], touched_lines: list[str] | None) -> set[str]:
    """Keep images whose reference appears in this file's changed lines.

    An empty or missing list means the diff carried no content for the file — a
    rename or header-only entry — where nothing identifies which images moved,
    so the full owned set is returned instead.
    """
    if not touched_lines:
        return images
    return {i for i in images if any(i in line for line in touched_lines)}
