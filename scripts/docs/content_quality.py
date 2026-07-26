"""Fence-aware content-quality lint rules for Atlas docs.

Companion to heading_quality.py. Each finder returns (line_number, message)
tuples for lines that violate a rule. Fenced code blocks are always skipped,
and any line containing the literal `<!-- lint-ok -->` marker is exempt.
"""
from __future__ import annotations

import re

_SUPPRESS = "<!-- lint-ok -->"

# Rule 1 — prose that narrates an adjacent diagram/image.
_DIAGRAM_NARRATION = re.compile(
    r"\b("
    r"the (?:diagram|figure|image|chart|graph)\s+(?:above|below)\s+(?:shows|depicts|illustrates)"
    r"|as (?:you can|we can) see (?:above|below|in the (?:diagram|figure))"
    r"|(?:this|the) (?:diagram|figure|image) (?:shows|depicts|illustrates)"
    r"|in the (?:diagram|figure) (?:above|below)"
    r")\b",
    re.IGNORECASE,
)

# Rule 2 — narration of how a doc/diagram was produced or styled.
_PRODUCTION_STYLE = re.compile(
    r"\b("
    r"(?:dark|light|slate-\d+|navy|gray|grey)\s+background"
    r"|same\s+(?:font|palette|typeface|colou?rs?)"
    r"|per the .{0,30}style (?:guide|guidelines)"
    r"|landscape-orient|portrait-orient"
    r"|JetBrains Mono|slate-950"
    r")\b",
    re.IGNORECASE,
)

# Rule 3 — unearned marketing adjectives (only enforced in service READMEs).
_MARKETING_WORDS = (
    "intelligent",
    "powerful",
    "seamless",
    "seamlessly",
    "cutting-edge",
    "state-of-the-art",
    "ai-powered",
    "blazing",
    "world-class",
    "next-generation",
    "revolutionary",
)
_MARKETING_RE = re.compile(
    r"\b(" + "|".join(re.escape(word) for word in _MARKETING_WORDS) + r")\b",
    re.IGNORECASE,
)


def _structural_lines(text: str):
    in_fence = False
    fence_marker = ""
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            yield line_number, line, True
            continue
        yield line_number, line, in_fence


def _scan(text: str, pattern: re.Pattern) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line, in_fence in _structural_lines(text):
        if in_fence or _SUPPRESS in line:
            continue
        for match in pattern.finditer(line):
            findings.append((line_number, match.group(0).strip()))
    return findings


def diagram_narration_findings(text: str) -> list[tuple[int, str]]:
    return _scan(text, _DIAGRAM_NARRATION)


def production_style_findings(text: str) -> list[tuple[int, str]]:
    return _scan(text, _PRODUCTION_STYLE)


def marketing_adjective_findings(
    text: str, *, is_service_readme: bool
) -> list[tuple[int, str]]:
    if not is_service_readme:
        return []
    return _scan(text, _MARKETING_RE)


def _content_lines(text: str) -> list[tuple[int, str]]:
    """Non-fence, non-blank lines as (line_number, stripped_text)."""
    out = []
    for line_number, line, in_fence in _structural_lines(text):
        if in_fence:
            continue
        stripped = line.strip()
        if stripped:
            out.append((line_number, stripped))
    return out


def duplicate_block_findings(
    docs: dict[str, str], *, min_lines: int = 4, min_pages: int = 4
) -> list[tuple[str, str]]:
    # Map each normalized window -> set of pages it appears on.
    window_pages: dict[tuple[str, ...], set[str]] = {}
    per_page_windows: dict[str, list[tuple[str, ...]]] = {}
    for path, text in docs.items():
        lines = [text for _, text in _content_lines(text)]
        windows = []
        for i in range(0, max(0, len(lines) - min_lines + 1)):
            window = tuple(lines[i : i + min_lines])
            windows.append(window)
            window_pages.setdefault(window, set()).add(path)
        per_page_windows[path] = windows
    findings: list[tuple[str, str]] = []
    for path in docs:
        seen: set[tuple[str, ...]] = set()
        for window in per_page_windows[path]:
            if window in seen:
                continue
            pages = window_pages[window]
            if len(pages) >= min_pages:
                seen.add(window)
                findings.append(
                    (
                        path,
                        f"block starting {window[0]!r} is duplicated across "
                        f"{len(pages)} pages",
                    )
                )
    # de-dup to one finding per page for the first offending block
    if any(p == path for p, _ in findings):
        first = next((f for f in findings if f[0] == path), None)
        findings = [f for f in findings if f[0] != path]
        if first:
            findings.append(first)
    return sorted(findings)
