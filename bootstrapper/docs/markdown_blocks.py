"""Shared Markdown block scanning used by README section writers."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt


_MARKDOWN = MarkdownIt("commonmark")
_COMMONMARK_LINE_ENDING_RE = re.compile(r"\r\n|\r|\n")


def _commonmark_line_offsets(text: str) -> list[int]:
    """Translate Markdown-it line maps to character offsets.

    CommonMark recognizes only CRLF, CR, and LF as line endings. Python's
    ``str.splitlines()`` recognizes additional Unicode separators, so using it
    here can shift Markdown-it's token line numbers away from the source text.
    """
    offsets = [0]
    offsets.extend(
        match.end() for match in _COMMONMARK_LINE_ENDING_RE.finditer(text)
    )
    if offsets[-1] != len(text):
        offsets.append(len(text))
    return offsets


def fenced_code_spans(text: str) -> list[tuple[int, int]]:
    """Return character spans for CommonMark fenced code blocks.

    Markdown-it supplies the container-aware block parsing rules: root fences
    permit zero to three leading spaces, four-space markers remain indented
    code, and fences nested in list items are recognized relative to the list
    content indentation.
    """
    line_offsets = _commonmark_line_offsets(text)

    spans: list[tuple[int, int]] = []
    for token in _MARKDOWN.parse(text):
        if token.type != "fence" or token.map is None:
            continue
        start_line, end_line = token.map
        spans.append((line_offsets[start_line], line_offsets[end_line]))
    return spans


def top_level_list_items(text: str) -> list[tuple[int, int]]:
    """Return ``(first_line, end_line)`` for each item of a root-level list.

    Line numbers are 0-based and ``end_line`` is exclusive. A CommonMark item
    includes its lazy and indented continuation lines, the paragraphs of a
    loose item, and any code fence nested in it; a fence or paragraph outside
    a list is never part of an item.
    """
    return [
        (token.map[0], token.map[1])
        for token in _MARKDOWN.parse(text)
        if token.type == "list_item_open" and token.level == 1 and token.map
    ]
