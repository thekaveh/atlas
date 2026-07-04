from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "superset.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFEST = ROOT / "services" / "superset" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_superset_remains_watchlist_until_data_and_sso_are_ready() -> None:
    text = _candidate_text()

    assert "## Watchlist decision (2026-07-04)" in text
    assert "must not add `services/superset/service.yml` yet" in text
    assert "meaningful Trino/Iceberg or Postgres analytics datasets" in text
    assert "SSO" in text
    assert "current Trino integration is intentionally no-auth" in text
    assert "Superset complements Grafana and the Atlas root dashboard" in text


def test_superset_future_service_spec_covers_atlas_service_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`data-eng`",
        "`ml-eng`",
        "`all`",
        "`apps`",
        "`SUPERSET_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "`superset.localhost`",
        "`SUPERSET_SECRET_KEY`",
        "`superset -> supabase`",
        "`superset -> redis`",
        "`superset -> trino`",
        "Init companion",
        "Supabase/Postgres metadata database",
        "Dataset readiness gate",
    ]

    for term in expected_terms:
        assert term in text


def test_superset_service_manifest_is_not_added_by_watchlist_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_superset_candidate_is_indexed_from_minio_research_row() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    assert "| Apache Superset | apps | minio | [candidates/superset.md]" in matrix
