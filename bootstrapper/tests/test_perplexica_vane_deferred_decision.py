from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VANE_CANDIDATE = ROOT / "docs" / "research" / "candidates" / "perplexica.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFESTS = [
    ROOT / "services" / "vane" / "service.yml",
    ROOT / "services" / "perplexica" / "service.yml",
]


def test_vane_candidate_records_july_deferred_decision() -> None:
    text = VANE_CANDIDATE.read_text(encoding="utf-8")

    expected = [
        "## Deferred decision (2026-07-04)",
        "Vane v1.12.2",
        "MIT",
        "ItzCrazyKns/Vane",
        "slim-latest",
        "bundled SearXNG",
        "`/api/search`",
        "`/api/providers`",
        "Server-Sent Events",
        "cited-answer",
    ]

    for phrase in expected:
        assert phrase in text


def test_vane_future_contract_is_conservative() -> None:
    text = VANE_CANDIDATE.read_text(encoding="utf-8")

    expected_terms = [
        "`gen-ai-rag`",
        "`gen-ai-eng`",
        "`all`",
        "`apps`",
        "`VANE_SOURCE=disabled|container|localhost`",
        "`VANE_PORT`",
        "disabled by default",
        "Wizard placement",
        "protected Kong route",
        "custom `BASE_PORT`",
        "SearXNG",
        "LiteLLM",
        "Ollama",
        "Open WebUI",
        "Local Deep Researcher",
        "backend",
        "n8n",
        "Crawl4AI",
        "MinIO",
        "data_flow.calls",
        "init companion",
        "provenance",
        "route auth",
        "upload storage",
        "duplicate UX",
    ]

    for term in expected_terms:
        assert term in text


def test_vane_remains_out_of_the_service_graph_for_now() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    for manifest in SERVICE_MANIFESTS:
        assert not manifest.exists()

    assert "| Perplexica (Vane) | apps | searxng | [candidates/perplexica.md]" in matrix


def test_strategy_report_names_vane_deferral_gate() -> None:
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert (
        "July 4, 2026 decision keeps Perplexica/Vane deferred until Atlas"
        in strategy
    )
