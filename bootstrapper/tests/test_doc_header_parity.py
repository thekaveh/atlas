"""Header parity: README.md and docs/index.md must share the canonical header copy.

README.md is hand-authored. docs/index.md is hand-authored and is the Layer-B
source projected into both the .io landing and the GitHub wiki Home, so locking
these two files locks every surface against drift.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CANONICAL_TAGLINE = "A self-hosted, pre-integrated gen-AI, ML, and data platform — one Docker Compose stack"
CANONICAL_SUBTITLE = (
    "Chat, RAG, agents, distributed compute, and a full data platform — "
    "source-configurable services wired together out of the box and selectable "
    "among the deployment modes each supports."
)
CANONICAL_SUMMARY = (
    "Atlas is a self-hosted engineering platform that bundles 30+ services — "
    "LLM inference and a gateway, vector and graph databases, workflow and DAG "
    "automation, distributed compute, object storage, notebooks, and observability "
    "— behind a Kong gateway and an adaptive FastAPI backend."
)

README = ROOT / "README.md"
INDEX = ROOT / "docs" / "index.md"


def test_readme_header_matches_canonical() -> None:
    text = README.read_text(encoding="utf-8")
    tagline = re.search(r"<strong>([^<]+)</strong>", text)
    subtitle = re.search(r"<p align=\"center\">\s*([^<]+?)\s*</p>", text)
    summary = re.search(
        r"^(Atlas is a self-hosted engineering platform[^\n]+)$",
        text,
        re.MULTILINE,
    )
    assert tagline and tagline.group(1) == CANONICAL_TAGLINE
    assert subtitle and subtitle.group(1).strip() == CANONICAL_SUBTITLE
    assert summary and summary.group(1) == CANONICAL_SUMMARY


def test_index_header_matches_canonical() -> None:
    text = INDEX.read_text(encoding="utf-8")
    tagline = re.search(r'<p class="atlas-kicker">([^<]+)</p>', text)
    subtitle = re.search(
        r'<p>(Chat, RAG, agents, distributed compute[^<]+)</p>', text,
    )
    summary = re.search(
        r"<p>(Atlas is a self-hosted engineering platform[^<]+)</p>",
        text,
    )
    assert tagline and tagline.group(1) == CANONICAL_TAGLINE
    assert subtitle and subtitle.group(1) == CANONICAL_SUBTITLE
    assert summary and summary.group(1) == CANONICAL_SUMMARY
