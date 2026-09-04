from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.token import Token


REPO_URL = "https://github.com/thekaveh/atlas"
WIKI_URL = "https://github.com/thekaveh/atlas/wiki"
SITE_URL = "https://thekaveh.github.io/atlas"

_LINK_RE = re.compile(r"(?P<image>!)?\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)")
_HTML_LINK_RE = re.compile(
    r"<(?P<tag>a|img)\b[^>]*?\b(?:href|src)\s*=\s*"
    r"(?P<quote>[\"'])(?P<target>.*?)(?P=quote)[^>]*>",
    re.IGNORECASE,
)
_MARKDOWN = MarkdownIt("commonmark")
_INERT_HTML_CONTAINERS = {"script", "style", "template"}


@dataclass(frozen=True)
class Link:
    target: str
    is_image: bool


class _RenderedAnchorParser(HTMLParser):
    """Collect navigable raw-HTML anchors while ignoring inert containers."""

    def __init__(self, targets: list[str]) -> None:
        super().__init__(convert_charrefs=True)
        self._targets = targets
        self._inert_stack: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized = tag.lower()
        if normalized in _INERT_HTML_CONTAINERS:
            self._inert_stack.append(normalized)
            return
        if self._inert_stack or normalized != "a":
            return
        href = next((value for name, value in attrs if name.lower() == "href"), None)
        if href is not None:
            self._targets.append(href)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if self._inert_stack and normalized == self._inert_stack[-1]:
            self._inert_stack.pop()


def _without_fenced_code(markdown: str) -> str:
    output: list[str] = []
    in_fence = False
    for line in markdown.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            output.append("\n" if line.endswith("\n") else "")
        elif in_fence:
            output.append("\n" if line.endswith("\n") else "")
        else:
            output.append(line)
    return "".join(output)


def find_links(markdown: str) -> list[Link]:
    text = _without_fenced_code(markdown)
    links: list[tuple[int, Link]] = []
    for match in _LINK_RE.finditer(text):
        target = match.group("target").strip("<>")
        links.append(
            (match.start(), Link(target=target, is_image=bool(match.group("image"))))
        )
    for match in _HTML_LINK_RE.finditer(text):
        links.append(
            (
                match.start(),
                Link(
                    target=match.group("target"),
                    is_image=match.group("tag").lower() == "img",
                ),
            )
        )
    return [link for _, link in sorted(links, key=lambda item: item[0])]


def navigable_link_targets(markdown: str) -> list[str]:
    """Return links that CommonMark renders as navigation, in document order.

    Markdown links include inline, reference, and URI autolinks. Raw HTML
    anchors are included because every Atlas surface renders them; comments,
    code, fences, images, and inert HTML containers never create graph edges.
    """
    targets: list[str] = []
    html_parser = _RenderedAnchorParser(targets)

    def visit(tokens: list[Token]) -> None:
        for token in tokens:
            if token.type == "link_open":
                href = token.attrGet("href")
                if href is not None:
                    targets.append(href)
            elif token.type in {"html_block", "html_inline"}:
                html_parser.feed(token.content)
            if token.children:
                visit(token.children)

    visit(_MARKDOWN.parse(markdown))
    html_parser.close()
    return targets


def is_forbidden(target: str, surface: str) -> bool:
    normalized = target.rstrip("/")
    is_wiki = normalized == WIKI_URL or normalized.startswith(f"{WIKI_URL}/")
    is_repo = (
        normalized == REPO_URL
        or normalized.startswith(f"{REPO_URL}/")
    ) and not is_wiki
    is_site = normalized == SITE_URL or normalized.startswith(f"{SITE_URL}/")
    if surface == "repo":
        return is_site or is_wiki
    if surface == "site":
        return is_repo or is_wiki
    if surface == "wiki":
        return is_repo or is_site
    raise ValueError(f"Unknown documentation surface: {surface}")
