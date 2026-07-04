#!/usr/bin/env python3
"""Validate Atlas' generated MkDocs documentation site."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    run([sys.executable, "scripts/generate-docs-site.py", "--check"])
    # CI contract: mkdocs build --strict
    run(["mkdocs", "build", "--strict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
