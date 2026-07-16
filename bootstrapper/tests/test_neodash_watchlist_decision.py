from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "neodash.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
SERVICE_MANIFEST = ROOT / "services" / "neodash" / "service.yml"


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_neodash_remains_watchlist_until_graph_data_and_boundary_exist() -> None:
    text = _candidate_text()

    assert "Watchlist decision (2026-07-04)" in text
    assert "must not add `services/neodash/service.yml` yet" in text
    assert "richer graph-native application data" in text
    assert "no longer maintained" in text
    assert "Root dashboard remains" in text
    assert "data dashboard for Neo4j content" in text


def test_neodash_future_service_spec_covers_atlas_service_contract() -> None:
    text = _candidate_text()

    expected_terms = [
        "`rag`",
        "`agents`",
        "`apps`",
        "`NEODASH_SOURCE=disabled|container`",
        "disabled by default",
        "Wizard placement",
        "`neodash.localhost`",
        "`neodash -> neo4j`",
        "`backend -> neo4j`",
        "`llm-graph-builder -> neo4j`",
        "read-only Neo4j",
        "Standalone/read-only mode",
        "custom `BASE_PORT`",
        "namespaced/read-only Cypher",
    ]

    for term in expected_terms:
        assert term in text


def test_neodash_service_manifest_is_not_added_by_watchlist_decision() -> None:
    assert not SERVICE_MANIFEST.exists()


def test_neodash_candidate_remains_indexed_from_neo4j_research_row() -> None:
    matrix = MATRIX.read_text(encoding="utf-8")

    assert "| NeoDash | apps | neo4j | [candidates/neodash.md]" in matrix
