#!/usr/bin/env python3
"""Phase B research-schema validator.

Validates row files (docs/research/rows/<service>.md) and candidate files
(docs/research/candidates/<slug>.md) against the schemas defined in
docs/research/README.md.

Usage:
  python scripts/validate_research_schema.py <file>
  python scripts/validate_research_schema.py --all [--research-root PATH]

Exit codes:
  0 — all valid.
  1 — one or more validation errors (printed to stdout).
  2 — usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

_ROW_SECTIONS = (
    "Missing-pair integrations",
    "Candidate new services",
    "Per-service feature gaps",
)

_CAND_SECTIONS = (
    "Headline",
    "Problem it solves",
    "Stack wiring sketch",
    "Effort",
    "Risks & open questions",
    "Upstream evidence",
)

_ROW_FRONTMATTER_KEYS = {"service", "category", "generated", "generator", "sources_consulted"}
_CAND_FRONTMATTER_KEYS = {"slug", "name", "type", "category-fit", "generated", "upstream", "license", "referenced-by"}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL_RE = re.compile(r"https?://\S+")

_WORD_CAP = 800
_CANDIDATE_CAP = 5

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str] | None:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return fm, m.group(2)


def _section_match(body: str, title: str) -> re.Match[str] | None:
    return re.search(
        rf"^##\s+(?:\d+(?:\.\d+)*\.\s+)?{re.escape(title)}\s*$",
        body,
        re.MULTILINE,
    )


def _validate_row(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    parsed = _parse_frontmatter(text)
    if parsed is None:
        errors.append(f"{path}: missing or unparseable frontmatter")
        return errors
    fm, body = parsed

    missing = _ROW_FRONTMATTER_KEYS - set(fm)
    if missing:
        errors.append(f"{path}: frontmatter missing key(s): {sorted(missing)}")

    if "generated" in fm and not _DATE_RE.match(str(fm.get("generated", ""))):
        errors.append(f"{path}: frontmatter `generated` must be YYYY-MM-DD")

    src = fm.get("sources_consulted")
    if not isinstance(src, list) or len(src) == 0:
        errors.append(f"{path}: frontmatter `sources_consulted` must be a non-empty list")

    last_idx = -1
    # Row sections are canonically numbered (## 1. …, ## 2. …, ## 3. …), so the
    # missing/out-of-order diagnostics report the section number — matching the
    # schema's on-disk form and letting callers find the right heading.
    for number, sec in enumerate(_ROW_SECTIONS, start=1):
        match = _section_match(body, sec)
        if match is None:
            errors.append(f"{path}: missing required section: ## {number}. {sec}")
            continue
        idx = match.start()
        if idx <= last_idx:
            errors.append(f"{path}: section out of order: ## {number}. {sec}")
        last_idx = idx

    word_count = len(body.split())
    if word_count > _WORD_CAP:
        errors.append(f"{path}: body exceeds {_WORD_CAP}-word cap ({word_count} words)")

    sec2 = _section_match(body, "Candidate new services")
    sec3 = _section_match(body, "Per-service feature gaps")
    sec2_start = sec2.start() if sec2 else -1
    sec3_start = sec3.start() if sec3 else -1
    if sec2_start != -1 and sec3_start > sec2_start:
        sec2_body = body[sec2_start:sec3_start]
        cand_refs = re.findall(r"\.\./candidates/[\w-]+\.md", sec2_body)
        if len(cand_refs) > _CANDIDATE_CAP:
            errors.append(f"{path}: section 2 has {len(cand_refs)} candidate refs (cap: {_CANDIDATE_CAP})")

    return errors


def _validate_candidate(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    parsed = _parse_frontmatter(text)
    if parsed is None:
        errors.append(f"{path}: missing or unparseable frontmatter")
        return errors
    fm, body = parsed

    missing = _CAND_FRONTMATTER_KEYS - set(fm)
    if missing:
        errors.append(f"{path}: frontmatter missing key(s): {sorted(missing)}")

    if "generated" in fm and not _DATE_RE.match(str(fm.get("generated", ""))):
        errors.append(f"{path}: frontmatter `generated` must be YYYY-MM-DD")

    up = str(fm.get("upstream", ""))
    if not _URL_RE.match(up):
        errors.append(f"{path}: frontmatter `upstream` must be an http(s) URL")

    rb = fm.get("referenced-by")
    if not isinstance(rb, list):
        errors.append(f"{path}: frontmatter `referenced-by` must be a list (may be empty)")

    last_idx = -1
    for sec in _CAND_SECTIONS:
        match = _section_match(body, sec)
        if match is None:
            errors.append(f"{path}: missing required section: ## {sec}")
            continue
        idx = match.start()
        if idx <= last_idx:
            errors.append(f"{path}: section out of order: ## {sec}")
        last_idx = idx

    upstream_evidence = _section_match(body, "Upstream evidence")
    ue_start = upstream_evidence.start() if upstream_evidence else -1
    if ue_start != -1:
        # Skip past the heading line itself, then search for the next heading.
        header_end = body.find("\n", ue_start)
        search_from = header_end + 1 if header_end != -1 else ue_start + len("## Upstream evidence")
        ue_end = len(body)
        nxt = re.search(r"(?m)^#+ ", body[search_from:])
        if nxt:
            ue_end = search_from + nxt.start()
        ue_body = body[ue_start:ue_end]
        if not _URL_RE.search(ue_body):
            errors.append(f"{path}: Upstream evidence section must contain at least one URL")

    return errors


def _validate_one(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.parent.name == "rows":
        return _validate_row(path, text)
    if path.parent.name == "candidates":
        return _validate_candidate(path, text)
    parsed = _parse_frontmatter(text)
    if parsed and "slug" in parsed[0]:
        return _validate_candidate(path, text)
    return _validate_row(path, text)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("file", nargs="?", type=Path, help="One row or candidate file.")
    grp.add_argument("--all", action="store_true", help="Validate every row + candidate file.")
    ap.add_argument("--research-root", type=Path, default=REPO_ROOT / "docs" / "research")
    args = ap.parse_args(argv)

    targets: list[Path] = []
    if args.all:
        for sub in ("rows", "candidates"):
            d = args.research_root / sub
            if d.is_dir():
                targets.extend(sorted(p for p in d.glob("*.md") if p.name not in ("README.md", ".gitkeep")))
        if not targets:
            # Empty-glob guard: a missing/renamed research root must fail
            # loudly, not exit 0 having validated nothing.
            print(
                f"error: --all found no row/candidate files under {args.research_root}",
                file=sys.stderr,
            )
            return 2
    else:
        if not args.file.exists():
            print(f"error: file not found: {args.file}", file=sys.stderr)
            return 2
        targets = [args.file]

    all_errors: list[str] = []
    for p in targets:
        all_errors.extend(_validate_one(p))

    if all_errors:
        for e in all_errors:
            print(e)
        print(f"\n{len(all_errors)} error(s) across {len(targets)} file(s).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
