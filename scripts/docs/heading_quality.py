"""Fence-aware heading numbering and professional-tone checks."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess


_HEADING_RE = re.compile(r"^(#{2,6})[ \t]+(.+?)\s*$")
_NUMBER_PREFIX_RE = re.compile(
    r"^(?:\d+\.(?:\d+\.)*|\d+(?:\.\d+)+)[ \t]+"
)
_DECORATIVE_SYMBOLS = (
    "✅",
    "❌",
    "⚠️",
    "⚠",
    "✓",
    "✗",
    "✔",
    "✘",
    "📝",
    "⏳",
    "▶",
    "★",
    "└",
)
_EXCLUDED_PREFIXES = (".agents/", "bootstrapper/tests/fixtures/")
_EXCLUDED_FILES = {"AGENTS.md"}


def documentation_paths(repo_root: Path) -> list[Path]:
    relative_paths = subprocess.check_output(
        ["git", "ls-files", "*.md"],
        cwd=repo_root,
        text=True,
    ).splitlines()
    candidates = [
        repo_root / relative
        for relative in relative_paths
        if relative not in _EXCLUDED_FILES
        and not relative.startswith(_EXCLUDED_PREFIXES)
    ]
    return [path for path in candidates if path.is_file()]


def _structural_lines(text: str):
    in_fence = False
    fence_marker = ""
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            yield line_number, line, True
            continue
        yield line_number, line, in_fence


def _renumbered_heading(
    level: int,
    title: str,
    counters: dict[int, int],
) -> str:
    missing_parents = [
        parent for parent in range(2, level) if counters[parent] == 0
    ]
    if missing_parents:
        raise ValueError(
            f"heading level H{level} skips required parent H{missing_parents[-1]}"
        )
    counters[level] += 1
    for deeper in range(level + 1, 7):
        counters[deeper] = 0
    number = ".".join(str(counters[item]) for item in range(2, level + 1))
    clean_title = title
    while True:
        normalized = _NUMBER_PREFIX_RE.sub("", clean_title, count=1)
        if normalized == clean_title:
            break
        clean_title = normalized
    return f"{'#' * level} {number}. {clean_title}"


def renumber_markdown(text: str) -> str:
    counters = {level: 0 for level in range(2, 7)}
    output: list[str] = []
    for _line_number, line, in_fence in _structural_lines(text):
        match = None if in_fence else _HEADING_RE.match(line.rstrip("\r\n"))
        if match is None:
            output.append(line)
            continue
        ending = "\n" if line.endswith("\n") else ""
        level = len(match.group(1))
        output.append(
            _renumbered_heading(level, match.group(2), counters) + ending
        )
    return "".join(output)


def heading_number_findings(text: str) -> list[tuple[int, str]]:
    counters = {level: 0 for level in range(2, 7)}
    findings: list[tuple[int, str]] = []
    for line_number, line, in_fence in _structural_lines(text):
        match = None if in_fence else _HEADING_RE.match(line.rstrip("\r\n"))
        if match is None:
            continue
        level = len(match.group(1))
        try:
            expected = _renumbered_heading(level, match.group(2), counters)
        except ValueError as exc:
            findings.append((line_number, str(exc)))
            continue
        actual = line.rstrip("\r\n")
        if actual != expected:
            findings.append((line_number, f"expected heading {expected!r}"))
    return findings


def decorative_symbol_findings(text: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line, in_fence in _structural_lines(text):
        if in_fence:
            continue
        for symbol in _DECORATIVE_SYMBOLS:
            if symbol in line:
                findings.append((line_number, symbol))
                break
    return findings
