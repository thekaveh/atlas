from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "timescaledb.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFEST = ROOT / "services" / "timescaledb" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_timescaledb_remains_watchlist_until_trading_data_slice_exists() -> None:
    text = _candidate_text()

    assert "Watchlist decision (2026-07-04)" in text
    assert "must not add `services/timescaledb/service.yml` yet" in text
    assert "later trading-data slice" in text
    assert "not standalone Atlas platform infrastructure" in text
    assert "read-only/paper" in text
    assert "no live exchange credentials" in text


def test_timescaledb_future_service_spec_covers_atlas_service_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`trading`",
        "`all`",
        "`data`",
        "`TIMESCALEDB_SOURCE=disabled|extension|container`",
        "disabled by default",
        "Wizard placement",
        "no public Kong route",
        "isolated `trading` database/schema",
        "`timescaledb -> supabase`",
        "`jupyterhub -> timescaledb`",
        "`redpanda -> timescaledb`",
        "hypertable chunk interval",
        "compression/columnstore policy",
        "retention policies",
        "custom `BASE_PORT`",
    ]

    for term in expected_terms:
        assert term in text


def test_timescaledb_service_manifest_is_not_added_by_watchlist_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_timescaledb_candidate_is_indexed_from_supabase_research_row() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    assert "| TimescaleDB | data | supabase | [candidates/timescaledb.md]" in matrix
