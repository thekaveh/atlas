from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HONCHO_CANDIDATE = ROOT / "docs" / "research" / "candidates" / "honcho.md"
GRAPHITI_CANDIDATE = ROOT / "docs" / "research" / "candidates" / "graphiti.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFEST = ROOT / "services" / "honcho" / "service.yml"


def test_honcho_candidate_records_july_deferred_decision() -> None:
    text = HONCHO_CANDIDATE.read_text(encoding="utf-8")

    expected = [
        "Deferred decision (2026-07-04)",
        "LangMem",
        "Graphiti",
        "AGPL-3.0",
        "MCP",
        "Hermes",
        "OpenClaw",
        "LiteLLM",
        "Postgres",
        "pgvector",
        "deriver",
    ]

    for phrase in expected:
        assert phrase in text


def test_honcho_future_contract_is_conservative() -> None:
    text = HONCHO_CANDIDATE.read_text(encoding="utf-8")

    expected_terms = [
        "`gen-ai-eng`",
        "`gen-ai-rag`",
        "`all`",
        "`agents`",
        "`HONCHO_SOURCE=disabled|container|localhost`",
        "`HONCHO_API_PORT`",
        "disabled by default",
        "Wizard placement",
        "no default Kong route",
        "custom `BASE_PORT`",
        "Supabase Postgres",
        "Redis",
        "backend",
        "Hermes",
        "OpenClaw",
        "Open WebUI",
        "LiteLLM",
        "data_flow.calls",
        "init companion",
        "schema migration",
        "memory ownership model",
        "tenant isolation",
        "route auth",
        "conversation consent",
    ]

    for term in expected_terms:
        assert term in text


def test_honcho_remains_out_of_the_service_graph_for_now() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    assert not SERVICE_MANIFEST.exists()
    assert "| Honcho | data | openclaw | [candidates/honcho.md]" in matrix


def test_strategy_report_names_honcho_deferral_gate() -> None:
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")
    graphiti = GRAPHITI_CANDIDATE.read_text(encoding="utf-8")

    assert (
        "July 4, 2026 decision keeps Honcho deferred until LangMem and Graphiti"
        in strategy
    )
    assert "backend-only" in graphiti
    assert "LangMem remains" in graphiti
