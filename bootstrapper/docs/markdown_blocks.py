"""Shared Markdown block scanning used by README section writers."""

from __future__ import annotations

from markdown_it import MarkdownIt


_MARKDOWN = MarkdownIt("commonmark")


def fenced_code_spans(text: str) -> list[tuple[int, int]]:
    """Return character spans for CommonMark fenced code blocks.

    Markdown-it supplies the container-aware block parsing rules: root fences
    permit zero to three leading spaces, four-space markers remain indented
    code, and fences nested in list items are recognized relative to the list
    content indentation.
    """
    line_offsets = [0]
    for line in text.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    spans: list[tuple[int, int]] = []
    for token in _MARKDOWN.parse(text):
        if token.type != "fence" or token.map is None:
            continue
        start_line, end_line = token.map
        spans.append((line_offsets[start_line], line_offsets[end_line]))
    return spans
