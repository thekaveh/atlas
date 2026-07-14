#!/usr/bin/env python3
"""Internal-markdown-link validator.

Scans markdown files for relative `[label](./path.md)` and `[label](path.md)`
links and asserts every target resolves to an existing file. External links
(http://, https://, mailto:) are skipped. Anchor fragments (`#section`,
including pure same-page `#section` links) are validated against the
target file's GitHub-computed heading slugs and explicit
`<a name=…>`/`<a id=…>` anchors.

Default scan set when invoked with no args:
  - README.md
  - docs/ (recursive, *.md only)
  - services/ (recursive, *.md only)

Exit codes:
  0 — every link resolves.
  1 — one or more broken links; details printed to stdout.
  2 — usage error (e.g. nonexistent input path).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Match `[label](target)` where target does NOT start with http://, https://,
# mailto:, or `#`. Greedy on label; non-greedy avoidance not needed because
# the link is opaque to label content (we only consume up to the matching `)`).
_LINK_RE = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<target>(?!https?://|mailto:)[^)]+)\)")

# Strip fenced code blocks (```...```) — non-greedy, multiline.
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)


def _strip_fenced_code(text: str) -> str:
    """Remove fenced code blocks before link extraction.

    Only fenced (```...```) code blocks are removed. Inline code (`...`)
    is NOT removed because inline-code-formatted link labels are part of
    real links and must be validated.
    """
    return _FENCED_CODE_RE.sub("", text)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_EXPLICIT_ANCHOR_RE = re.compile(r"<a\s+(?:name|id)=[\"']([^\"']+)[\"']")
# Strip backticks/asterisks (md formatting) and [text](url) wrappers.
# Underscores are NOT stripped — GitHub keeps them in slugs
# (`depends_on` → depends_on, not dependson).
_INLINE_FORMAT_RE = re.compile(r"[`*]|\[(?P<txt>[^\]]*)\]\([^)]*\)")


def _slugify(title: str) -> str:
    """GitHub heading slug: drop md formatting, lowercase, strip
    punctuation (keep word chars/hyphens/underscores), spaces → hyphens.
    Consecutive hyphens are preserved (GitHub does not collapse them)."""
    title = _INLINE_FORMAT_RE.sub(lambda m: m.group("txt") or "", title)
    out = []
    for ch in title.strip().lower():
        if ch.isalnum() or ch == "_" or ch == "-":
            out.append(ch)
        elif ch in " \t":
            out.append("-")
        # everything else (periods, em-dashes, emoji, …) is dropped
    return "".join(out)


_ANCHOR_CACHE: dict[Path, set[str]] = {}


def _heading_anchors(md: Path) -> set[str]:
    """All valid fragment ids for a markdown file: GitHub slugs of every
    heading (with -1/-2 dedupe suffixes) plus explicit <a name=/id=>
    anchors. Lenient variants with leading/trailing hyphens stripped are
    included to absorb slugger edge cases (emoji-prefixed headings)."""
    if md in _ANCHOR_CACHE:
        return _ANCHOR_CACHE[md]
    anchors: set[str] = set()
    seen_counts: dict[str, int] = {}
    in_fence = False
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _ANCHOR_CACHE[md] = anchors
        return anchors
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            slug = _slugify(m.group(2))
            n = seen_counts.get(slug, 0)
            seen_counts[slug] = n + 1
            anchors.add(slug if n == 0 else f"{slug}-{n}")
        for a in _EXPLICIT_ANCHOR_RE.finditer(line):
            anchors.add(a.group(1))
    anchors |= {a.strip("-") for a in anchors}
    _ANCHOR_CACHE[md] = anchors
    return anchors


def _collect_md_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for r in roots:
        if r.is_file() and r.suffix == ".md":
            files.append(r)
        elif r.is_dir():
            files.extend(sorted(r.rglob("*.md")))
    return files


def _default_roots() -> list[Path]:
    """Repo-default scan set.

    Covers every authoritative docs location:
      * main README.md (entry point)
      * docs/ (CHANGELOG, ROADMAP, CONTRIBUTING-services, deployment/, quick-start/, research/)
      * services/ (per-service READMEs — primary doc location since the
        2026-05-22 retirement of docs/services/)
    """
    roots = []
    for candidate in (REPO_ROOT / "README.md", REPO_ROOT / "docs", REPO_ROOT / "services"):
        if candidate.exists():
            roots.append(candidate)
    return roots


def _resolve_link_target(md: Path, file_part: str) -> Path:
    resolved = (md.parent / file_part).resolve()
    if resolved.exists():
        return resolved

    wiki_roots = (
        (REPO_ROOT / "docs" / "wiki").resolve(),
        (REPO_ROOT / "generated" / "wiki").resolve(),
    )
    is_wiki_page = any(md.resolve().is_relative_to(root) for root in wiki_roots)
    if is_wiki_page and not file_part.lower().endswith(".md"):
        wiki_page = Path(f"{resolved}.md")
        if wiki_page.exists():
            return wiki_page

    return resolved


def _check_file(md: Path) -> list[str]:
    """Return a list of broken-link error strings for this markdown file.

    Fenced code blocks (```...```) are stripped before link extraction so
    that code examples do not produce false positives. Inline-code-formatted
    link labels are NOT stripped because they are part of real links.
    """
    errors: list[str] = []
    text = _strip_fenced_code(md.read_text(encoding="utf-8", errors="replace"))
    for m in _LINK_RE.finditer(text):
        target = m.group("target").strip()
        file_part, _, fragment = target.partition("#")
        if file_part:
            resolved = _resolve_link_target(md, file_part)
            if not resolved.exists():
                errors.append(f"{md}: broken link [{m.group('label')}]({target}) → {resolved}")
                continue
        else:
            resolved = md  # pure `#section` — same-page anchor
        if fragment and resolved.suffix == ".md" and resolved.is_file():
            frag = fragment.strip().lower()
            anchors = _heading_anchors(resolved)
            if frag not in anchors and frag.strip("-") not in anchors:
                errors.append(
                    f"{md}: dead anchor [{m.group('label')}]({target}) — "
                    f"no heading slug `#{frag}` in {resolved.name}"
                )
    return errors


def main(argv: list[str]) -> int:
    if argv:
        raw_paths = [Path(p) for p in argv]
        for p in raw_paths:
            if not p.exists():
                print(f"error: path does not exist: {p}", file=sys.stderr)
                return 2
        roots = raw_paths
    else:
        roots = _default_roots()
        if not roots:
            print("error: no default paths exist (README.md / docs/ / services/)", file=sys.stderr)
            return 2

    all_errors: list[str] = []
    for md in _collect_md_files(roots):
        all_errors.extend(_check_file(md))

    if all_errors:
        for e in all_errors:
            print(e)
        print(f"\n{len(all_errors)} broken link(s).")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
