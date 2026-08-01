"""Header parity: README.md and docs/index.md must share the canonical header copy.

README.md is hand-authored. docs/index.md is hand-authored and is the Layer-B
source projected into both the .io landing and the GitHub wiki Home, so locking
these two files locks every surface against drift.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CANONICAL_TAGLINE = "A self-hosted, pre-integrated gen-AI, ML, and data platform — one Docker Compose stack"
CANONICAL_SUMMARY_ANCHOR = "Atlas is a self-hosted engineering platform that bundles 30+ services"

README = ROOT / "README.md"
INDEX = ROOT / "docs" / "index.md"


def test_readme_header_matches_canonical() -> None:
    text = README.read_text(encoding="utf-8")
    assert CANONICAL_TAGLINE in text, "README.md is missing the canonical tagline"
    assert CANONICAL_SUMMARY_ANCHOR in text, "README.md is missing the canonical summary anchor"


def test_index_header_matches_canonical() -> None:
    text = INDEX.read_text(encoding="utf-8")
    assert CANONICAL_TAGLINE in text, "docs/index.md is missing the canonical tagline"
    assert CANONICAL_SUMMARY_ANCHOR in text, "docs/index.md is missing the canonical summary anchor"
