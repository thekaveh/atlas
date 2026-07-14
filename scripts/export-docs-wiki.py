#!/usr/bin/env python3
"""Compatibility entry point for the generated GitHub wiki."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.docs.build_docs import build  # noqa: E402
from scripts.docs.push_wiki import DEFAULT_REMOTE, push_wiki  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--push", action="store_true")
    parser.add_argument("--remote", default=os.environ.get("WIKI_REMOTE", DEFAULT_REMOTE))
    args = parser.parse_args()
    build(
        ROOT / "docs" / "manifest.yaml",
        ROOT,
        site=False,
        wiki=True,
        check=args.check,
    )
    key_value = os.environ.get("WIKI_DEPLOY_KEY")
    push_wiki(
        ROOT / "generated" / "wiki",
        args.remote,
        Path(key_value) if key_value else None,
        push=args.push,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
