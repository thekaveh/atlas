from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "openlit.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
SERVICE_MANIFEST = ROOT / "services" / "openlit" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_openlit_remains_deferred_behind_langfuse_and_otel_stack() -> None:
    text = _candidate_text()

    assert "## Deferred decision (2026-07-04)" in text
    assert "must not add `services/openlit/service.yml` yet" in text
    assert "Langfuse plus OTel/Tempo/Loki" in text
    assert "second observability UI" in text
    assert "OpenLIT-specific functionality" in text


def test_openlit_future_service_spec_covers_atlas_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`observability`",
        "`gen-ai-eng`",
        "`gen-ai-rag`",
        "`ml-eng`",
        "`all`",
        "`infra`",
        "`agents`",
        "`OPENLIT_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "`openlit.localhost`",
        "Do not expose OTLP ingestion publicly by default",
        "ClickHouse",
        "OTel Collector",
        "backend",
        "LiteLLM",
        "Ollama",
        "Hermes",
        "JupyterHub",
        "Weaviate",
        "custom `BASE_PORT`",
    ]

    for term in expected_terms:
        assert term in text


def test_openlit_service_manifest_is_not_added_by_deferred_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_openlit_decision_stays_indexed_and_reflected_in_strategy() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert "| OpenLIT | infra | ollama | [candidates/openlit.md]" in matrix
    assert "July 4, 2026 decision keeps OpenLIT deferred" in strategy
