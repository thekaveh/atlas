#!/usr/bin/env python3
"""Compatibility entry point for the manifest-driven documentation build."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.docs.build_docs import build  # noqa: E402
from scripts.docs.canonical_references import sync_canonical_references  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = sync_canonical_references(ROOT, check=args.check)
    if changed and args.check:
        for path in changed:
            print(path.relative_to(ROOT).as_posix())
        return 1
    build(
        ROOT / "docs" / "manifest.yaml",
        ROOT,
        site=True,
        wiki=True,
        check=args.check,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
