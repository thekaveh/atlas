"""Render and fence-safely upsert README capability-contract sections."""

from __future__ import annotations

import re

from .capabilities_resolver import CapabilityRow, SupportLine
from .markdown_blocks import fenced_code_spans as _fenced_spans


_CAPABILITIES_HEADER_RE = re.compile(
    r"^##[ \t]+(\d+)\.[ \t]+Capabilities[ \t]+&[ \t]+limitations"
    r"(?:[ \t]+#+)?[ \t]*$",
    re.MULTILINE,
)
_NUMBERED_TOP_HEADER_RE = re.compile(r"^##[ \t]+(\d+)\.[ \t]+", re.MULTILINE)
_NEXT_TOP_HEADER_RE = re.compile(r"^##(?:[ \t]+|$)", re.MULTILINE)
_ALIAS_HEADER_RE = re.compile(r"^#{2,3}[ \t]+[^\r\n]+", re.MULTILINE)
_HEADING_ALIAS_RE = re.compile(
    r""" {0,3}<a[ \t]+(?:id|name)=(?:"[^"]+"|'[^']+')[ \t]*></a>[ \t]*"""
)


class CapabilitySectionError(ValueError):
    """Raised when a README has an ambiguous generated capability section."""


def _outside_fences(position: int, spans: list[tuple[int, int]]) -> bool:
    return not any(start <= position < end for start, end in spans)


def _heading_identity(heading: str) -> str:
    """Ignore ATX presentation spacing and optional closing hashes."""
    title = re.sub(r"[ \t]+#+[ \t]*$", "", heading).rstrip(" \t")
    title = re.sub(r"[ \t]+", " ", title)
    numbered = _NUMBERED_TOP_HEADER_RE.match(title)
    if numbered:
        title = f"## {int(numbered.group(1))}. {title[numbered.end():]}"
    return re.sub(r"\bDependencies *& *Integrations$", "Dependencies & Integrations", title)


def _heading_aliases(section: str) -> dict[str, tuple[str, ...]]:
    """Read explicit alias lines immediately following real section headings."""
    aliases: dict[str, tuple[str, ...]] = {}
    spans = _fenced_spans(section)
    for heading in _ALIAS_HEADER_RE.finditer(section):
        if not _outside_fences(heading.start(), spans):
            continue
        lines: list[str] = []
        for line in re.split(r"\r\n|\r|\n", section[heading.end():]):
            if not line.strip():
                continue
            if not _HEADING_ALIAS_RE.fullmatch(line):
                break
            lines.append(line)
        if lines:
            aliases[_heading_identity(heading.group())] = tuple(lines)
    return aliases


def _preserve_heading_aliases(section: str, previous: str) -> str:
    """Keep each legacy fragment beside its own regenerated heading."""
    retained = _heading_aliases(previous)
    current = _heading_aliases(section)
    spans = _fenced_spans(section)
    for heading in reversed(list(_ALIAS_HEADER_RE.finditer(section))):
        if not _outside_fences(heading.start(), spans):
            continue
        identity = _heading_identity(heading.group())
        present = {line.strip(" \t") for line in current.get(identity, ())}
        aliases = [line for line in retained.get(identity, ())
                   if line.strip(" \t") not in present]
        suffix = section[heading.end():]
        if aliases:
            suffix = "\n\n" + "\n".join(aliases) + suffix
        section = section[:heading.end()] + suffix
    return section


def _first_outside_fences(
    pattern: re.Pattern[str],
    text: str,
    start: int,
    spans: list[tuple[int, int]],
) -> re.Match[str] | None:
    return next(
        (
            match
            for match in pattern.finditer(text, start)
            if _outside_fences(match.start(), spans)
        ),
        None,
    )


def _slice_capabilities_section(readme_text: str) -> tuple[int, int, int | None] | None:
    spans = _fenced_spans(readme_text)
    matches = [
        match
        for match in _CAPABILITIES_HEADER_RE.finditer(readme_text)
        if _outside_fences(match.start(), spans)
    ]
    if len(matches) > 1:
        raise CapabilitySectionError(
            f"multiple canonical capability sections found ({len(matches)})"
        )
    if not matches:
        return None
    match = matches[0]
    next_header = _first_outside_fences(
        _NEXT_TOP_HEADER_RE,
        readme_text,
        match.end(),
        spans,
    )
    end = next_header.start() if next_header is not None else len(readme_text)
    position = int(match.group(1))
    return (match.start(), end, position)


def _next_section_position(readme_text: str) -> int:
    spans = _fenced_spans(readme_text)
    positions = [
        int(match.group(1))
        for match in _NUMBERED_TOP_HEADER_RE.finditer(readme_text)
        if _outside_fences(match.start(), spans)
    ]
    return max(positions, default=0) + 1


def _escape_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _support_lines(support: tuple[SupportLine, ...], aggregate: bool) -> list[str]:
    """Render the support-tier statement(s) that precede the capability table."""
    rendered: list[str] = []
    for line in support:
        prefix = f"`{line.service}` — " if aggregate else ""
        revision = f" (evidence at `{line.evidence_revision}`)" if line.evidence_revision else ""
        rendered.append(
            f"{prefix}Support tier: **{line.tier}** — {_escape_cell(line.evidence)}{revision}."
        )
        rendered.extend(f"  - Limitation: {_escape_cell(item)}" for item in line.limitations)
    if rendered:
        rendered.append("")
    return rendered


def render_capabilities_section(
    rows: tuple[CapabilityRow, ...],
    *,
    position: int,
    aggregate: bool,
    support: tuple[SupportLine, ...] = (),
) -> str:
    """Render a byte-deterministic capability section."""
    lines = [f"## {position}. Capabilities & limitations", ""]
    lines.extend(_support_lines(support, aggregate))
    if not rows:
        lines.append("_No capability contract declared._")
        return "\n".join(lines) + "\n"

    if aggregate:
        lines.extend(
            [
                "| Service | Capability | Status | Verification | Notes |",
                "|---|---|---|---|---|",
            ]
        )
    else:
        lines.extend(
            [
                "| Capability | Status | Verification | Notes |",
                "|---|---|---|---|",
            ]
        )

    for row in rows:
        values = [row.capability, row.status, row.verification, row.notes]
        if aggregate:
            values.insert(0, row.service)
        lines.append("| " + " | ".join(_escape_cell(value) for value in values) + " |")
    return "\n".join(lines) + "\n"


def upsert_capabilities_section(
    readme_text: str,
    rows: tuple[CapabilityRow, ...],
    *,
    aggregate: bool,
    support: tuple[SupportLine, ...] = (),
) -> str:
    """Replace the real capability section, or append a dynamically numbered one."""
    existing = _slice_capabilities_section(readme_text)
    if existing is None:
        position = _next_section_position(readme_text)
        section = render_capabilities_section(
            rows,
            position=position,
            aggregate=aggregate,
            support=support,
        )
        prefix = readme_text.rstrip()
        return (prefix + "\n\n" if prefix else "") + section

    start, end, existing_position = existing
    position = existing_position or _next_section_position(readme_text)
    section = render_capabilities_section(
        rows,
        position=position,
        aggregate=aggregate,
        support=support,
    )
    section = _preserve_heading_aliases(section, readme_text[start:end])
    suffix = readme_text[end:]
    if suffix:
        return readme_text[:start] + section.rstrip() + "\n\n" + suffix.lstrip("\n")
    return readme_text[:start] + section
