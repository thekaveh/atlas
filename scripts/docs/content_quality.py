"""Fence-aware content-quality lint rules for Atlas docs.

Companion to heading_quality.py. Each finder returns (line_number, message)
tuples for lines that violate a rule. Fenced code blocks are always skipped,
and any line containing the literal `<!-- lint-ok -->` marker is exempt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .heading_quality import _structural_lines

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
        lines = [t for _, t in _content_lines(text)]
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
        page_findings = [f for f in findings if f[0] == path]
        if page_findings:
            first = page_findings[0]
            findings = [f for f in findings if f[0] != path]
            findings.append(first)
    return sorted(findings)


# Rule 5 — STE length limits (ASD-STE100, ste-software profile). A sentence
# carries at most 25 words, a numbered procedural step at most 20, and a
# paragraph at most 75 words and 6 sentences. Existing pages are held to a
# ratchet baseline (see prose_ratchet_findings) instead of failing at once.
@dataclass(frozen=True)
class ProseLimits:
    sentence: int = 25
    step: int = 20
    para_words: int = 75
    para_sentences: int = 6


PROSE_RULES = ("sentence", "step", "para_words", "para_sentences")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\[(])")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|(\d+)[.)])\s+")
_GENERATED_BEGIN = ("<!-- BEGIN GENERATED", "<!-- TOPOLOGY:BEGIN")
_GENERATED_END = ("<!-- END GENERATED", "<!-- TOPOLOGY:END")


def prose_words(text: str) -> list[str]:
    """Words a reader reads: link targets and HTML tags do not count."""
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return [word for word in text.split() if re.search(r"[A-Za-z0-9]", word)]


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT.split(text) if part.strip()]


def _is_block_break(stripped: str) -> bool:
    return not stripped or stripped.startswith(("#", "<", "---", "***", "|"))


def _table_cells(stripped: str) -> list[str]:
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell):
        return []
    return [cell for cell in cells if cell]


def _generated_state(stripped: str, in_generated: bool) -> bool:
    if stripped.startswith(_GENERATED_BEGIN):
        return True
    if stripped.startswith(_GENERATED_END):
        return False
    return in_generated


def _body(line: str) -> str:
    stripped = line.strip()
    return stripped[1:].strip() if stripped.startswith(">") else stripped


def _line_kind(body: str, hidden: bool) -> str:
    """Classify one line: hidden, table, break, step, item or text."""
    if hidden:
        return "hidden"
    if body.startswith("|"):
        return "table"
    if _is_block_break(body):
        return "break"
    item = _LIST_ITEM.match(body)
    if item:
        return "step" if item.group(1) else "item"
    return "text"


def prose_blocks(text: str):
    """Yield (line, prose, is_step) blocks outside fences and generated ranges.

    A blank line, heading, HTML line, rule or table row ends a block, and each
    list item starts one. Every table cell is its own block. A block that
    contains ``<!-- lint-ok -->`` on one of its lines is skipped.
    """
    block: list[str] = []
    start, is_step, in_generated = 0, False, False
    for line_number, line, in_fence in _structural_lines(text):
        body = _body(line)
        in_generated = _generated_state(body, in_generated)
        kind = _line_kind(body, in_fence or in_generated)
        if kind != "text" and block:
            yield start, " ".join(block), is_step
            block = []
        if kind == "table":
            yield from ((line_number, cell, False) for cell in _table_cells(body))
        if kind in {"hidden", "table", "break"}:
            continue
        if not block:
            start, is_step = line_number, kind == "step"
        block.append(_LIST_ITEM.sub("", body, count=1))
    if block:
        yield start, " ".join(block), is_step


def _block_findings(block: str, is_step: bool, limits: ProseLimits):
    if _SUPPRESS in block:
        return
    sentences = _sentences(block)
    word_count = len(prose_words(block))
    if word_count > limits.para_words:
        yield "para_words", f"paragraph {word_count}w > {limits.para_words}"
    if len(sentences) > limits.para_sentences:
        yield "para_sentences", (
            f"paragraph {len(sentences)} sentences > {limits.para_sentences}"
        )
    rule, limit = ("step", limits.step) if is_step else ("sentence", limits.sentence)
    for sentence in sentences:
        count = len(prose_words(sentence))
        if count > limit:
            yield rule, f"{rule} {count}w > {limit}: {sentence[:60]!r}"


def long_prose_findings(
    text: str, limits: ProseLimits = ProseLimits()
) -> list[tuple[int, str, str]]:
    """Return (line, rule, message) for every STE length violation."""
    findings: list[tuple[int, str, str]] = []
    for line_number, block, is_step in prose_blocks(text):
        for rule, message in _block_findings(block, is_step, limits):
            findings.append((line_number, rule, message))
    return findings


def prose_counts(findings) -> dict[str, int]:
    """Collapse (line, rule, message) findings into non-zero per-rule counts."""
    counts: dict[str, int] = {}
    for _line, rule, _message in findings:
        counts[rule] = counts.get(rule, 0) + 1
    return counts


# Manifest prose feeds .env.example, docs/reference/env-vars.md and the
# generated capability tables, so its limits are checked at the source.
MANIFEST_RULES = ("env_description", "env_issue_ref", "capability_note")
_ISSUE_REF = re.compile(r"(?<![\w&/])#\d{2,5}\b")


def _env_prose_findings(entry: dict) -> list[tuple[str, str, str]]:
    name, description = entry.get("name", "?"), str(entry.get("description") or "")
    findings = []
    count = len(prose_words(description))
    if count > 40:
        findings.append((name, "env_description", f"description {count}w > 40"))
    if _ISSUE_REF.search(description):
        findings.append((name, "env_issue_ref", "description cites an issue number"))
    return findings


def _capability_prose_findings(
    capability: dict, limits: ProseLimits
) -> list[tuple[str, str, str]]:
    name = str(capability.get("name", "?"))
    return [
        (name, "capability_note", f"note sentence {count}w > {limits.sentence}")
        for count in (
            len(prose_words(sentence))
            for sentence in _sentences(str(capability.get("note") or ""))
        )
        if count > limits.sentence
    ]


def manifest_prose_findings(
    manifest: dict, limits: ProseLimits = ProseLimits()
) -> list[tuple[str, str, str]]:
    """Return (key, rule, message) for long or issue-citing manifest prose.

    An env ``description`` may hold at most 40 words and no ``#NNN`` issue
    reference. Each sentence of a capability ``note`` obeys the sentence limit.
    """
    findings: list[tuple[str, str, str]] = []
    for entry in manifest.get("env") or []:
        findings.extend(_env_prose_findings(entry))
    for capability in manifest.get("capabilities") or []:
        findings.extend(_capability_prose_findings(capability, limits))
    return findings


def prose_ratchet_findings(
    current: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]
) -> list[str]:
    """Compare per-file rule counts with the committed ratchet baseline.

    A count above its baseline is a regression. A count below it means the
    baseline must be lowered in the same change, so a gain cannot be lost.
    A file absent from the baseline must be clean.
    """
    messages: list[str] = []
    for path in sorted(set(current) | set(baseline)):
        have, allowed = current.get(path, {}), baseline.get(path, {})
        for rule in sorted(set(have) | set(allowed)):
            count, ceiling = have.get(rule, 0), allowed.get(rule, 0)
            if count > ceiling:
                messages.append(f"{path}: {rule} {count} > baseline {ceiling}")
            elif count < ceiling:
                messages.append(
                    f"{path}: {rule} {count} < baseline {ceiling}; "
                    "lower the baseline (--write-prose-baseline)"
                )
    return messages


def prose_baseline_increases(
    current: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]
) -> list[str]:
    """List the counts a baseline rewrite would raise."""
    return [
        f"{path}: {rule} {count} > {baseline.get(path, {}).get(rule, 0)}"
        for path, counts in sorted(current.items())
        for rule, count in sorted(counts.items())
        if count > baseline.get(path, {}).get(rule, 0)
    ]
