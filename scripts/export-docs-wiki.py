#!/usr/bin/env python3
"""Export or verify GitHub Wiki-compatible Atlas docs pages."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI_HOME = ROOT / "docs" / "wiki" / "Home.md"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Verify wiki/Home.md and siblings are current.")
    args = parser.parse_args()

    cmd = [sys.executable, "scripts/generate-docs-site.py"]
    if args.check:
        cmd.append("--check")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode
    print(f"wiki export ready: {WIKI_HOME.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
