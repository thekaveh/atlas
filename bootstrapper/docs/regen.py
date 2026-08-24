"""Per-service docs + diagram regenerator.

Usage:
  python -m bootstrapper.docs.regen <service> [--out-root PATH] [--dry-run]
                                              [--section-only] [--check]
  python -m bootstrapper.docs.regen --all     [same flags]

Each `services/<name>/` folder hosts its own `README.md`, `architecture.svg`,
and `architecture.html`. This script regenerates the auto-generated
"Dependencies & Integrations" and "Capabilities & limitations" blocks in the
README plus the two diagram files, preserving any user-authored content in the
README (including the three `Future — ...` subsections under "Dependencies &
Integrations").

Exit codes:
  0 — success.
  1 — manifest error.
  2 — drift detected (--check mode only).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .deps_resolver import build_doc_graph

from services.manifests import load_manifests  # noqa: E402

from .capabilities_resolver import (
    capability_section_enabled,
    is_aggregate_capability_doc,
    resolve_capability_rows,
)
from .capabilities_section_writer import upsert_capabilities_section
from .deps_section_writer import render_section
from .diagram_renderer import render_html, render_svg

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICES_DIR = REPO_ROOT / "services"

DEPS_HEADER_RE = re.compile(r"^##\s+(?:(\d+)\.\s+)?Dependencies\s*&\s*Integrations\b", re.MULTILINE)
NEXT_TOP_HEADER_RE = re.compile(r"^##\s+", re.MULTILINE)
NEXT_SUBSEC_HEADER_RE = re.compile(r"^###\s+", re.MULTILINE)
FUTURE_HEADER_RE = re.compile(
    r"^###\s+(?:\d+\.\d+\.?\s+)?Future\s*[—-]\s*(Missing pair integrations|Candidate new services|Unused features in this service)\b",
    re.MULTILINE,
)
PLACEHOLDER_LINE = "_No high-confidence opportunities identified._"

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _fenced_spans(text: str) -> list[tuple[int, int]]:
    """Char-offset spans covering fenced code blocks (``` or ~~~).

    Header detection must ignore ``## ``/``### `` lines that live inside a code
    fence — user-authored ``Future — …`` subsections routinely embed snippets
    (a YAML ``## note``, a diff, a heading example). Without this the slicer
    treats a fenced heading as a real section boundary and corrupts the README
    on the next regen write (splice-inside-fence / orphaned tail / truncation).
    """
    spans: list[tuple[int, int]] = []
    open_off: int | None = None
    open_char = ""
    open_len = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        m = _FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if open_off is None:
                open_off, open_char, open_len = offset, marker[0], len(marker)
            elif marker[0] == open_char and len(marker) >= open_len:
                # A closing fence is the same char, at least as long, with no
                # trailing info string.
                if line.strip()[len(marker):].strip() == "":
                    spans.append((open_off, offset + len(line)))
                    open_off = None
        offset += len(line)
    if open_off is not None:  # unterminated fence → to end of text
        spans.append((open_off, len(text)))
    return spans


def _in_fence(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def _search_outside_fences(pattern: re.Pattern, text: str, start: int, spans: list[tuple[int, int]]):
    """First match of ``pattern`` at/after ``start`` that is not inside a fence."""
    for m in pattern.finditer(text, start):
        if not _in_fence(m.start(), spans):
            return m
    return None


def _enumerate_doc_folders() -> list[str]:
    return sorted(
        p.name for p in SERVICES_DIR.iterdir()
        if p.is_dir()
        and not p.name.startswith(("_", "."))
        and (p / "README.md").exists()
    )


def _slice_deps_section(readme_text: str) -> tuple[int, int] | None:
    """Locate the `## Dependencies & Integrations` block.

    Returns (start, end) char offsets, or None if the section is absent.
    The slice runs from the `##` header to (exclusive of) the next `##`
    header or end-of-file.
    """
    # The START search must be fence-aware too. Every END search already was,
    # but this one was not — so a README that DOCUMENTS the deps header inside
    # a ```markdown fence had the generated tables spliced INSIDE that fence,
    # leaving a duplicate header; the next regen pass then treated the fenced
    # copy as the real section and deleted everything from it to the following
    # `##` — taking the genuine Dependencies section, Troubleshooting and
    # References with it. Regen stopped being idempotent at that point.
    spans = _fenced_spans(readme_text)
    m = _search_outside_fences(DEPS_HEADER_RE, readme_text, 0, spans)
    if not m:
        return None
    start = m.start()
    nxt = _search_outside_fences(NEXT_TOP_HEADER_RE, readme_text, m.end(), spans)
    end = nxt.start() if nxt else len(readme_text)
    return (start, end)


def _extract_future_blocks(deps_text: str) -> dict[str, str]:
    """Pull the three `### Future — ...` subsections out of the Dependencies block.

    Returns a dict keyed by the canonical heading suffix (e.g. "Missing pair
    integrations") → block body (everything from the `### Future — …` line
    up to the next `### ` or `## ` header). Missing or placeholder-only
    subsections are returned as empty strings.
    """
    out: dict[str, str] = {
        "Missing pair integrations": "",
        "Candidate new services": "",
        "Unused features in this service": "",
    }
    spans = _fenced_spans(deps_text)
    for m in FUTURE_HEADER_RE.finditer(deps_text):
        if _in_fence(m.start(), spans):
            # A `### Future — …` line inside a code fence is example text, not a
            # real subsection heading.
            continue
        key = m.group(1)
        body_start = m.end()
        # Block extends to the next ### or ## header that is NOT inside a fence.
        boundaries = [
            b.start()
            for b in (
                _search_outside_fences(NEXT_SUBSEC_HEADER_RE, deps_text, body_start, spans),
                _search_outside_fences(NEXT_TOP_HEADER_RE, deps_text, body_start, spans),
            )
            if b is not None
        ]
        end = min(boundaries) if boundaries else len(deps_text)
        body = deps_text[body_start:end].strip()
        # Strip placeholder
        if body == PLACEHOLDER_LINE or not body:
            out[key] = ""
        else:
            out[key] = body
    return out


def _detect_position(readme_text: str) -> int:
    """Detect the section number of the existing Dependencies & Integrations
    heading. Defaults to 5 (canonical slot) if absent or unnumbered."""
    # Fence-aware, matching `_slice_deps_section`. A fenced EXAMPLE of the
    # header would otherwise dictate the subsection numbering of the real one.
    m = _search_outside_fences(DEPS_HEADER_RE, readme_text, 0, _fenced_spans(readme_text))
    if m and m.group(1):
        return int(m.group(1))
    return 5


def _render_section_with_future(graph, existing_readme: str) -> str:
    """Generate the auto-block, splicing in any user-authored Future content
    found in the existing README."""

    position = _detect_position(existing_readme)
    auto_section = render_section(graph, position=position)
    sl = _slice_deps_section(existing_readme)
    if sl is None:
        return auto_section
    future = _extract_future_blocks(existing_readme[sl[0]: sl[1]])
    # Replace each `### Future — X\n\n_No high-confidence opportunities identified._`
    # in auto_section with the preserved body.
    for heading_suffix, body in future.items():
        if not body:
            continue
        placeholder_pattern = re.compile(
            r"(^###\s+(?:\d+\.\d+\.?\s+)?Future\s*[—-]\s*"
            + re.escape(heading_suffix)
            + r"\b.*?$\n\n)"
            + re.escape(PLACEHOLDER_LINE),
            re.MULTILINE,
        )
        # Use a function replacement so `body` (user-authored Future content)
        # is spliced LITERALLY. A plain `r"\1" + body` template makes re.sub
        # interpret escapes in body — a `\d` regex example or a Windows path
        # like `C:\Users` raises re.error and aborts the whole --all/CI run,
        # and a `\1` would splice the captured heading mid-body.
        auto_section = placeholder_pattern.sub(
            lambda m: m.group(1) + body, auto_section, count=1
        )
    return auto_section


def _upsert_section(readme_text: str, section: str) -> str:
    """Replace an existing Dependencies section, or append it if missing."""
    sl = _slice_deps_section(readme_text)
    if sl is not None:
        return (readme_text[: sl[0]] + section.rstrip() + "\n\n" + readme_text[sl[1]:]).rstrip() + "\n"
    return readme_text.rstrip() + "\n\n" + section


def _process(name: str, out_root: Path, dry_run: bool, section_only: bool, check: bool) -> int:
    graph = build_doc_graph(name, SERVICES_DIR)
    target_dir = out_root / name
    readme_path = target_dir / "README.md"
    existing_readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

    section = _render_section_with_future(graph, existing_readme)
    new_readme = _upsert_section(existing_readme, section)
    if capability_section_enabled(name):
        rows = resolve_capability_rows(name, load_manifests(SERVICES_DIR))
        new_readme = upsert_capabilities_section(
            new_readme,
            rows,
            aggregate=is_aggregate_capability_doc(name),
        )

    artifacts: list[tuple[Path, str]] = [(readme_path, new_readme)]
    if not section_only:
        artifacts.append((target_dir / "architecture.svg", render_svg(graph)))
        artifacts.append((target_dir / "architecture.html", render_html(graph)))

    drift = 0
    for path, content in artifacts:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if existing != content:
            if check:
                drift += 1
                print(f"DRIFT: {path}")
            elif dry_run:
                print(f"would write {path}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    return drift


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="bootstrapper.docs.regen")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("service", nargs="?", help="Single doc folder name (e.g. hermes).")
    grp.add_argument("--all", action="store_true", help="Process every doc folder under services/.")
    ap.add_argument("--out-root", type=Path, default=SERVICES_DIR)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--section-only",
        action="store_true",
        help="Only write generated README sections; skip HTML+SVG.",
    )
    ap.add_argument("--check", action="store_true", help="Exit 2 if any artifact would change. Implies --dry-run.")
    args = ap.parse_args(argv)

    targets = _enumerate_doc_folders() if args.all else [args.service]

    total_drift = 0
    for name in targets:
        try:
            total_drift += _process(name, args.out_root, args.dry_run, args.section_only, args.check)
        except KeyError as e:
            print(f"manifest error for {name}: {e}", file=sys.stderr)
            return 1

    if args.check and total_drift:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
