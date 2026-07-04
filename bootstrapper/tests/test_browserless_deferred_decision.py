from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "browserless.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
SERVICE_MANIFEST = ROOT / "services" / "browserless" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_browserless_remains_deferred_behind_crawl4ai() -> None:
    text = _candidate_text()

    assert "## Deferred decision (2026-07-04)" in text
    assert "must not add `services/browserless/service.yml` yet" in text
    assert "Crawl4AI-first" in text
    assert "SSPL-1.0" in text
    assert "Chromium memory" in text
    assert "persistent browser sessions" in text


def test_browserless_future_service_spec_covers_atlas_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`gen-ai-rag`",
        "`all`",
        "`media`",
        "`BROWSERLESS_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "`browserless.localhost`",
        "no public route by default",
        "`BROWSERLESS_TOKEN`",
        "`KEY`",
        "`TOKEN`",
        "`CONCURRENT`",
        "`QUEUED`",
        "`TIMEOUT`",
        "WebSocket",
        "n8n",
        "SearXNG",
        "backend",
        "Hermes",
        "Crawl4AI",
        "doc-processor",
        "Weaviate",
        "custom `BASE_PORT`",
    ]

    for term in expected_terms:
        assert term in text


def test_browserless_service_manifest_is_not_added_by_deferred_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_browserless_decision_stays_indexed_and_reflected_in_strategy() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert "| Browserless | media | n8n, searxng | [candidates/browserless.md]" in matrix
    assert (
        "July 4, 2026 decision keeps Browserless deferred behind Crawl4AI"
        in strategy
    )
