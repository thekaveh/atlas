"""Render and fence-safely upsert README capability-contract sections."""

from __future__ import annotations

import re

from .capabilities_resolver import CapabilityRow
from .markdown_blocks import fenced_code_spans as _fenced_spans


_CAPABILITIES_HEADER_RE = re.compile(
    r"^##[ \t]+(\d+)\.[ \t]+Capabilities[ \t]+&[ \t]+limitations"
    r"(?:[ \t]+#+)?[ \t]*$",
    re.MULTILINE,
)
_NUMBERED_TOP_HEADER_RE = re.compile(r"^##[ \t]+(\d+)\.[ \t]+", re.MULTILINE)
_NEXT_TOP_HEADER_RE = re.compile(r"^##(?:[ \t]+|$)", re.MULTILINE)


class CapabilitySectionError(ValueError):
    """Raised when a README has an ambiguous generated capability section."""


def _outside_fences(position: int, spans: list[tuple[int, int]]) -> bool:
    return not any(start <= position < end for start, end in spans)


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


def render_capabilities_section(
    rows: tuple[CapabilityRow, ...],
    *,
    position: int,
    aggregate: bool,
) -> str:
    """Render a byte-deterministic capability section."""
    lines = [f"## {position}. Capabilities & limitations", ""]
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
) -> str:
    """Replace the real capability section, or append a dynamically numbered one."""
    existing = _slice_capabilities_section(readme_text)
    if existing is None:
        position = _next_section_position(readme_text)
        section = render_capabilities_section(
            rows,
            position=position,
            aggregate=aggregate,
        )
        prefix = readme_text.rstrip()
        return (prefix + "\n\n" if prefix else "") + section

    start, end, existing_position = existing
    position = existing_position or _next_section_position(readme_text)
    section = render_capabilities_section(
        rows,
        position=position,
        aggregate=aggregate,
    )
    suffix = readme_text[end:]
    if suffix:
        return readme_text[:start] + section.rstrip() + "\n\n" + suffix.lstrip("\n")
    return readme_text[:start] + section
