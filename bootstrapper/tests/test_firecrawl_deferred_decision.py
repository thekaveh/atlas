from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "firecrawl.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
SERVICE_MANIFEST = ROOT / "services" / "firecrawl" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_firecrawl_remains_deferred_behind_crawl4ai() -> None:
    text = _candidate_text()

    assert "## Deferred decision (2026-07-04)" in text
    assert "must not add `services/firecrawl/service.yml` yet" in text
    assert "Crawl4AI-first" in text
    assert "AGPL-3.0" in text
    assert "larger worker/Playwright footprint" in text
    assert "Firecrawl-specific functionality" in text


def test_firecrawl_future_service_spec_covers_atlas_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`gen-ai-rag`",
        "`all`",
        "`media`",
        "`FIRECRAWL_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "`firecrawl.localhost`",
        "no public route by default",
        "Playwright",
        "queue",
        "Redis",
        "SearXNG",
        "Local Deep Researcher",
        "n8n",
        "backend",
        "Hermes",
        "MCP",
        "Weaviate",
        "custom `BASE_PORT`",
    ]

    for term in expected_terms:
        assert term in text


def test_firecrawl_service_manifest_is_not_added_by_deferred_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_firecrawl_decision_stays_indexed_and_reflected_in_strategy() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert "| Firecrawl | media | local-deep-researcher | [candidates/firecrawl.md]" in matrix
    assert (
        "July 4, 2026 decision keeps Firecrawl deferred behind Crawl4AI"
        in strategy
    )
