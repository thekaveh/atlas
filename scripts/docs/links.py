from __future__ import annotations

import re
from dataclasses import dataclass


REPO_URL = "https://github.com/thekaveh/atlas"
WIKI_URL = "https://github.com/thekaveh/atlas/wiki"
SITE_URL = "https://thekaveh.github.io/atlas"

_LINK_RE = re.compile(r"(?P<image>!)?\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)")
_HTML_LINK_RE = re.compile(
    r"<(?P<tag>a|img)\b[^>]*?\b(?:href|src)\s*=\s*"
    r"(?P<quote>[\"'])(?P<target>.*?)(?P=quote)[^>]*>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Link:
    target: str
    is_image: bool


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
