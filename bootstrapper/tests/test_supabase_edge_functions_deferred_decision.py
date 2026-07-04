from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "supabase-edge-functions.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
SERVICE_MANIFEST = ROOT / "services" / "supabase-edge-functions" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_supabase_edge_functions_remain_deferred_behind_existing_execution_surfaces() -> None:
    text = _candidate_text()

    assert "## Deferred decision (2026-07-04)" in text
    assert "must not add `services/supabase-edge-functions/service.yml` yet" in text
    assert "backend, n8n, Celery/Flower, and Airflow" in text
    assert "self-hosting beta" in text
    assert "Deno function surface" in text
    assert "edge-specific use case" in text


def test_supabase_edge_functions_future_service_spec_covers_atlas_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`async-jobs`",
        "`all`",
        "`agents`",
        "`apps`",
        "`SUPABASE_EDGE_FUNCTIONS_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "`/functions/v1/*`",
        "no public unauthenticated route by default",
        "Supabase Auth",
        "JWT secret",
        "service-role",
        "backend",
        "n8n",
        "Celery",
        "Airflow",
        "LiteLLM",
        "custom `BASE_PORT`",
    ]

    for term in expected_terms:
        assert term in text


def test_supabase_edge_functions_service_manifest_is_not_added_by_deferred_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_supabase_edge_functions_decision_stays_indexed_and_reflected_in_strategy() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert (
        "| Supabase Edge Functions (Deno runtime) | apps | supabase | "
        "[candidates/supabase-edge-functions.md]"
    ) in matrix
    assert (
        "July 4, 2026 decision keeps Supabase Edge Functions deferred"
        in strategy
    )
