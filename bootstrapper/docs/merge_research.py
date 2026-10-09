"""Merge research rows + candidates into the master integration matrix.

Reads:
  docs/research/rows/<service>.md
  docs/research/candidates/<slug>.md

Writes:
  docs/research/integration-matrix.md           — generated index
  docs/research/candidates/<slug>.md            — referenced-by frontmatter
                                                   updated in place

Deterministic and idempotent: re-running produces byte-identical output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROOT = REPO_ROOT / "docs" / "research"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_CAND_REF_RE = re.compile(r"\.\./candidates/([\w-]+)\.md")
# Lifecycles that end a candidacy (#1190): the record stays under
# candidates/ as history, but no derived index lists it as a candidate.
CLOSED_LIFECYCLES = frozenset({"shipped", "rejected"})


def _parse(text: str) -> tuple[dict, str] | None:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except (yaml.YAMLError, ValueError):  # ValueError: an impossible date
        return None
    if not isinstance(fm, dict):
        return None
    return fm, m.group(2)


def _scalar(value, *, in_flow: bool = False):
    """``value`` as written, quoted only when YAML would read it back as
    something else: an unquoted ` #` truncated it and `: ` made the file
    unparseable on the next regen (2026-10-08 run, cycle 43)."""
    if not isinstance(value, str):
        return value
    text = f"[{value}]" if in_flow else value
    try:
        loaded = yaml.safe_load(f"k: {text}")
    except yaml.YAMLError:
        loaded = None
    expected = [value] if in_flow else value
    return value if loaded == {"k": expected} else json.dumps(value, ensure_ascii=False)


def _emit_frontmatter(fm: dict) -> str:
    """Emit YAML frontmatter deterministically. Uses inline (`[a, b]`) form for
    short lists to match the existing example fixtures."""
    lines = ["---"]
    for k in sorted(fm.keys()):
        v = fm[k]
        if isinstance(v, list):
            if all(isinstance(x, str) for x in v) and len(v) <= 8:
                rendered = "[" + ", ".join(str(_scalar(x, in_flow=True)) for x in v) + "]"
                lines.append(f"{k}: {rendered}")
            else:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {_scalar(item)}")
        else:
            lines.append(f"{k}: {_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def closed_candidate_slugs(root: Path = DEFAULT_ROOT) -> frozenset[str]:
    """Slugs of candidates whose lifecycle is shipped or rejected."""
    closed = set()
    for path in sorted((root / "candidates").glob("*.md")):
        if path.name == "README.md":
            continue
        parsed = _parse(path.read_text(encoding="utf-8"))
        if parsed and parsed[0].get("lifecycle") in CLOSED_LIFECYCLES:
            closed.add(str(parsed[0].get("slug", path.stem)))
    return frozenset(closed)


def _write_or_check(path: Path, text: str, check: bool) -> bool:
    """True when ``path`` differs from ``text``; writes it unless checking."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    if not check:
        path.write_text(text, encoding="utf-8")
    return True


def run_merge(root: Path, *, check: bool = False) -> list[Path]:
    """Merge research artifacts under `root` (a Path pointing at docs/research/).

    Returns the files that changed (or, with ``check``, would change).
    """

    rows_dir = root / "rows"
    cands_dir = root / "candidates"

    rows: list[tuple[Path, dict, str]] = []
    if rows_dir.is_dir():
        for p in sorted(rows_dir.glob("*.md")):
            if p.name in ("README.md", ".gitkeep"):
                continue
            parsed = _parse(p.read_text(encoding="utf-8"))
            if parsed:
                rows.append((p, parsed[0], parsed[1]))

    cands: dict[str, tuple[Path, dict, str]] = {}
    if cands_dir.is_dir():
        for p in sorted(cands_dir.glob("*.md")):
            if p.name in ("README.md", ".gitkeep"):
                continue
            parsed = _parse(p.read_text(encoding="utf-8"))
            if parsed:
                cands[parsed[0].get("slug", p.stem)] = (p, parsed[0], parsed[1])

    referenced_by: dict[str, set[str]] = {slug: set() for slug in cands}
    for _, fm, body in rows:
        svc = fm.get("service", "")
        for slug in _CAND_REF_RE.findall(body):
            if slug in referenced_by:
                referenced_by[slug].add(svc)

    changed: list[Path] = []
    for slug, (path, fm, body) in cands.items():
        new_fm = dict(fm)
        new_fm["referenced-by"] = sorted(referenced_by.get(slug, set()))
        new_text = _emit_frontmatter(new_fm) + "\n" + body.lstrip("\n")
        if _write_or_check(path, new_text, check):
            changed.append(path)

    out_path = root / "integration-matrix.md"
    if _write_or_check(out_path, _build_matrix(rows, cands, referenced_by), check):
        changed.append(out_path)
    return changed


def _build_matrix(
    rows: list[tuple[Path, dict, str]],
    cands: dict[str, tuple[Path, dict, str]],
    referenced_by: dict[str, set[str]],
) -> str:
    """Build the integration-matrix.md content."""

    lines: list[str] = []
    lines.append("# Cross-service Integration Matrix")
    lines.append("")
    lines.append(
        "> **Generated** by `python -m bootstrapper.docs.merge_research`. "
        "Do not edit by hand — your changes will be overwritten on the next run."
    )
    lines.append("")
    lines.append("## 1. By service")
    lines.append("")
    lines.append("| Service | Category | Sources | Row file |")
    lines.append("|---|---|---|---|")
    for _, fm, _body in sorted(rows, key=lambda r: r[1].get("service", "")):
        svc = fm.get("service", "?")
        cat = fm.get("category", "?")
        src_count = len(fm.get("sources_consulted") or [])
        lines.append(f"| {svc} | {cat} | {src_count} | [rows/{svc}.md](./rows/{svc}.md) |")
    lines.append("")

    by_cat: dict[str, list[str]] = {}
    for _, fm, _body in rows:
        by_cat.setdefault(fm.get("category", "?"), []).append(fm.get("service", "?"))
    lines.append("## 2. By category")
    lines.append("")
    for number, cat in enumerate(sorted(by_cat), start=1):
        lines.append(f"### 2.{number}. {cat}")
        lines.append("")
        for svc in sorted(by_cat[cat]):
            lines.append(f"- [{svc}](./rows/{svc}.md)")
        lines.append("")

    lines.extend(_candidate_lines(cands, referenced_by))
    return "\n".join(lines).rstrip() + "\n"


def _candidate_lines(
    cands: dict[str, tuple[Path, dict, str]],
    referenced_by: dict[str, set[str]],
) -> list[str]:
    """The "Candidate new services" section: open candidates only (#1190)."""
    lines = ["## 3. Candidate new services", ""]
    active = sorted(
        slug for slug, (_p, fm, _b) in cands.items()
        if fm.get("lifecycle") not in CLOSED_LIFECYCLES
    )
    if len(active) < len(cands):
        lines.append(
            "Shipped and rejected candidates are not listed; their one-pagers stay "
            "under `candidates/` with the `lifecycle` that closed them."
        )
        lines.append("")
    if active:
        lines.append("| Candidate | Category fit | Referenced by | One-pager |")
        lines.append("|---|---|---|---|")
        for slug in active:
            _, fm, _body = cands[slug]
            name = fm.get("name", slug)
            cat_fit = fm.get("category-fit", "?")
            refs = sorted(referenced_by.get(slug, set()))
            refs_str = ", ".join(refs) if refs else "_(none)_"
            lines.append(f"| {name} | {cat_fit} | {refs_str} | [candidates/{slug}.md](./candidates/{slug}.md) |")
    else:
        lines.append("_No candidate new services proposed._")
    lines.append("")
    return lines


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="bootstrapper.docs.merge_research")
    ap.add_argument("--research-root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument(
        "--check", action="store_true",
        help="report files merge would rewrite and exit 2 instead of writing them",
    )
    args = ap.parse_args(argv)
    if not args.research_root.is_dir():
        print(f"error: research root not found: {args.research_root}", file=sys.stderr)
        return 1
    changed = run_merge(args.research_root, check=args.check)
    if args.check and changed:
        for path in changed:
            print(f"drift: {path}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
