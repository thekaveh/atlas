#!/usr/bin/env python3
"""Compatibility entry point for the strict generated-site check."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.docs.check_site import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
