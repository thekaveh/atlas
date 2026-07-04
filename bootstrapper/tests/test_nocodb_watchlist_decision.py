from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "nocodb.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFEST = ROOT / "services" / "nocodb" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_nocodb_remains_watchlist_until_review_workflow_and_auth_exist() -> None:
    text = _candidate_text()

    assert "## Watchlist decision (2026-07-04)" in text
    assert "must not add `services/nocodb/service.yml` yet" in text
    assert "concrete human-review queue" in text
    assert "SSO and route-auth posture" in text
    assert "not a Supabase Studio replacement" in text
    assert "not a Label Studio replacement" in text


def test_nocodb_future_service_spec_covers_atlas_service_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`platform`",
        "`agents`",
        "`rag`",
        "`apps`",
        "`NOCODB_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "`nocodb.localhost`",
        "`nocodb -> supabase`",
        "`nocodb -> redis`",
        "`n8n -> nocodb`",
        "`backend -> nocodb`",
        "worker-mode",
        "Init companion",
        "custom `BASE_PORT`",
    ]

    for term in expected_terms:
        assert term in text


def test_nocodb_service_manifest_is_not_added_by_watchlist_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_nocodb_candidate_remains_indexed_from_n8n_research_row() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    assert "| NocoDB | apps | n8n | [candidates/nocodb.md]" in matrix
