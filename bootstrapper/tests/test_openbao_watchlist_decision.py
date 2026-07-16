from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "openbao.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFEST = ROOT / "services" / "openbao" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_openbao_remains_watchlist_until_operator_story_exists() -> None:
    text = _candidate_text()

    assert "Watchlist decision (2026-07-04)" in text
    assert "must not add `services/openbao/service.yml` yet" in text
    assert "Infisical-first" in text
    assert "concrete secrets lifecycle and operator story" in text
    assert "storage, unseal, backup, and bootstrap" in text
    assert "Vault-lineage compatibility" in text


def test_openbao_future_service_spec_covers_atlas_service_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`identity-security`",
        "`all`",
        "`infra`",
        "`OPENBAO_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "`openbao.localhost`",
        "no public route by default",
        "integrated storage",
        "Shamir unseal",
        "auto-unseal",
        "bootstrap token",
        "audit device",
        "custom `BASE_PORT`",
    ]

    for term in expected_terms:
        assert term in text


def test_openbao_service_manifest_is_not_added_by_watchlist_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_openbao_candidate_is_indexed_from_kong_research_row() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    assert "| OpenBao | infra | kong | [candidates/openbao.md]" in matrix
