from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "finrl-fingpt.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
TRACKS = ROOT / "bootstrapper" / "tracks.yml"
SERVICE_MANIFESTS = [
    ROOT / "services" / "finrl" / "service.yml",
    ROOT / "services" / "fingpt" / "service.yml",
]


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_finrl_fingpt_remain_deferred_to_notebooks() -> None:
    text = _candidate_text()

    assert "## Deferred-to-notebooks decision (2026-07-04)" in text
    assert "must not add `services/finrl/service.yml`" in text
    assert "must not add `services/fingpt/service.yml`" in text
    assert "research notebook assets" in text
    assert "not production trading intelligence" in text
    assert "not financial advice" in text
    assert "push-button trading AI" in text


def test_finrl_fingpt_future_notebook_contract_is_conservative() -> None:
    text = _candidate_text()

    expected_terms = [
        "`trading`",
        "`ml-eng`",
        "`all`",
        "`apps`",
        "no standalone SOURCE values",
        "`JUPYTERHUB_SOURCE=container`",
        "disabled by default",
        "Wizard placement",
        "no Kong aliases",
        "no direct URLs",
        "JupyterHub",
        "MinIO",
        "MLflow",
        "LiteLLM",
        "Langfuse",
        "OpenBB",
        "CCXT",
        "TimescaleDB",
        "no live exchange credentials",
        "curated datasets",
        "eval criteria",
        "paper-trading guardrails",
        "data_flow.calls",
        "custom `BASE_PORT`",
    ]

    for term in expected_terms:
        assert term in text


def test_finrl_fingpt_service_manifests_are_not_added() -> None:
    for manifest in SERVICE_MANIFESTS:
        assert not manifest.exists()


def test_finrl_fingpt_decision_is_indexed_and_strategy_reflected() -> None:
    tracks = TRACKS.read_text(encoding="utf-8")
    matrix = MATRIX.read_text(encoding="utf-8")
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert "Read-only financial research and paper portfolios in notebooks; no live trading." in tracks
    assert "| FinRL And FinGPT | apps | _(none)_ | [candidates/finrl-fingpt.md]" in matrix
    assert "July 4, 2026 decision keeps FinRL and FinGPT deferred to notebooks" in strategy
