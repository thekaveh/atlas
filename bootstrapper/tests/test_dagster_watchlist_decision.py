from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "dagster.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFEST = ROOT / "services" / "dagster" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_dagster_remains_watchlist_until_airflow_boundary_exists() -> None:
    text = _candidate_text()

    assert "## Watchlist decision (2026-07-04)" in text
    assert "must not add `services/dagster/service.yml` yet" in text
    assert "Airflow remains Atlas' default scheduler" in text
    assert "concrete asset-lineage workflow" in text
    assert "Do not run duplicate schedules" in text
    assert "Airlift" in text


def test_dagster_future_service_spec_covers_atlas_service_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`data-eng`",
        "`ml-eng`",
        "`all`",
        "`agents`",
        "`DAGSTER_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "`dagster.localhost`",
        "`dagster -> supabase`",
        "`dagster -> airflow`",
        "`dagster -> minio`",
        "`dagster -> trino`",
        "`dagster -> spark`",
        "Init companion",
        "webserver, daemon, and one user-code/code-location container",
        "Asset-workflow readiness gate",
    ]

    for term in expected_terms:
        assert term in text


def test_dagster_service_manifest_is_not_added_by_watchlist_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_dagster_candidate_is_indexed_from_minio_research_row() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    assert "| Dagster | agents | minio | [candidates/dagster.md]" in matrix
