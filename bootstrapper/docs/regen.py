"""Per-service docs + diagram regenerator.

Usage:
  python -m bootstrapper.docs.regen <service> [--out-root PATH] [--dry-run]
                                              [--section-only] [--check]
  python -m bootstrapper.docs.regen --all     [same flags]

Each `services/<name>/` folder hosts its own `README.md`, `architecture.svg`,
and `architecture.html`. This script regenerates the auto-generated
"Dependencies & Integrations" block in the README plus the two diagram files,
preserving any user-authored content in the README (including the three
`Future — ...` subsections under "Dependencies & Integrations").

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
from .deps_section_writer import render_section
from .diagram_renderer import render_html, render_svg

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICES_DIR = REPO_ROOT / "services"

DEPS_HEADER_RE = re.compile(r"^##\s+(?:(\d+)\.\s+)?Dependencies\s*&\s*Integrations\b", re.MULTILINE)
NEXT_TOP_HEADER_RE = re.compile(r"^##\s+", re.MULTILINE)
FUTURE_HEADER_RE = re.compile(
    r"^###\s+(?:\d+\.\d+\.?\s+)?Future\s*[—-]\s*(Missing pair integrations|Candidate new services|Unused features in this service)\b",
    re.MULTILINE,
)
PLACEHOLDER_LINE = "_No high-confidence opportunities identified._"


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
    m = DEPS_HEADER_RE.search(readme_text)
    if not m:
        return None
    start = m.start()
    nxt = NEXT_TOP_HEADER_RE.search(readme_text, m.end())
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
    matches = list(FUTURE_HEADER_RE.finditer(deps_text))
    for m in matches:
        key = m.group(1)
        body_start = m.end()
        # Block extends to the next ### or ## header
        nxt_subsec = re.search(r"^###\s+", deps_text[body_start:], re.MULTILINE)
        nxt_topsec = re.search(r"^##\s+", deps_text[body_start:], re.MULTILINE)
        candidates = [c.start() for c in (nxt_subsec, nxt_topsec) if c is not None]
        end_rel = min(candidates) if candidates else len(deps_text) - body_start
        body = deps_text[body_start: body_start + end_rel].strip()
        # Strip placeholder
        if body == PLACEHOLDER_LINE or not body:
            out[key] = ""
        else:
            out[key] = body
    return out


def _detect_position(readme_text: str) -> int:
    """Detect the section number of the existing Dependencies & Integrations
    heading. Defaults to 5 (canonical slot) if absent or unnumbered."""
    m = DEPS_HEADER_RE.search(readme_text)
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
    ap.add_argument("--section-only", action="store_true", help="Only write README's deps section; skip HTML+SVG.")
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
