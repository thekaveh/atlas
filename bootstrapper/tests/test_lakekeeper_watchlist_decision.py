from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "lakekeeper.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFEST = ROOT / "services" / "lakekeeper" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_lakekeeper_remains_watchlist_until_catalog_pressure_exists() -> None:
    text = _candidate_text()

    assert "Watchlist decision (2026-07-04)" in text
    assert "must not add `services/lakekeeper/service.yml` yet" in text
    assert "data-eng-lab" in text
    assert "current Apache Iceberg REST fixture" in text
    assert "write/concurrency pressure" in text
    assert "vended credentials" in text
    assert "OIDC" in text
    assert "OpenFGA" in text


def test_lakekeeper_future_service_spec_covers_atlas_service_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`data-eng`",
        "`all`",
        "`data`",
        "`LAKEKEEPER_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "topology",
        "no public unauthenticated management route",
        "`lakekeeper -> minio`",
        "`lakekeeper -> supabase`",
        "`spark -> lakekeeper`",
        "`trino -> lakekeeper`",
        "Init companion",
        "Supabase/Postgres",
        "MinIO",
        "migration",
    ]

    for term in expected_terms:
        assert term in text


def test_lakekeeper_service_manifest_is_not_added_by_watchlist_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_lakekeeper_candidate_is_indexed_from_minio_research_row() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    assert "| Lakekeeper | data | minio | [candidates/lakekeeper.md]" in matrix
