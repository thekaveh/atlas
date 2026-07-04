from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "docs" / "research" / "candidates" / "live-trading-services.md"
MATRIX = ROOT / "docs" / "research" / "integration-matrix.md"
STRATEGY_REPORT = ROOT / "docs" / "strategy" / "atlas-vnext-strategy-report.md"
TRACKS = ROOT / "bootstrapper" / "tracks.yml"
SERVICE_MANIFESTS = [
    ROOT / "services" / "hummingbot" / "service.yml",
    ROOT / "services" / "freqtrade" / "service.yml",
    ROOT / "services" / "nautilustrader" / "service.yml",
]


def _candidate_text() -> str:
    return CANDIDATE.read_text(encoding="utf-8")


def test_live_trading_services_have_rejected_for_now_decision() -> None:
    text = _candidate_text()

    assert "## Rejected-for-now decision (2026-07-04)" in text
    assert "must not add `services/hummingbot/service.yml`" in text
    assert "must not add `services/freqtrade/service.yml`" in text
    assert "must not add `services/nautilustrader/service.yml`" in text
    assert "no live exchange trading" in text
    assert "not financial advice" in text
    assert "read-only financial research and paper portfolios" in text


def test_live_trading_future_service_contract_is_guarded_and_complete() -> None:
    text = _candidate_text()

    expected_terms = [
        "`trading`",
        "`all`",
        "`agents`",
        "`apps`",
        "`HUMMINGBOT_SOURCE=disabled|container|localhost`",
        "`FREQTRADE_SOURCE=disabled|container|localhost`",
        "`NAUTILUSTRADER_SOURCE=disabled|container|localhost`",
        "disabled by default",
        "Wizard placement",
        "`hummingbot.localhost`",
        "`freqtrade.localhost`",
        "`nautilustrader.localhost`",
        "no Kong route for order-execution APIs",
        "Infisical",
        "OpenBao",
        "audit logs",
        "operator risk controls",
        "paper mode",
        "sandbox",
        "read-only keys",
        "JupyterHub",
        "MinIO",
        "MLflow",
        "Langfuse",
        "TimescaleDB",
        "Redpanda",
        "Grafana",
        "n8n",
        "custom `BASE_PORT`",
        "init companion",
        "data_flow.calls",
    ]

    for term in expected_terms:
        assert term in text


def test_live_trading_service_manifests_are_not_added_by_rejected_decision() -> None:
    for manifest in SERVICE_MANIFESTS:
        assert not manifest.exists()


def test_trading_track_and_strategy_keep_live_trading_rejected() -> None:
    tracks = TRACKS.read_text(encoding="utf-8")
    matrix = MATRIX.read_text(encoding="utf-8")
    strategy = STRATEGY_REPORT.read_text(encoding="utf-8")

    assert "Read-only financial research and paper portfolios in notebooks; no live trading." in tracks
    assert "| Live Trading Services | agents | _(none)_ | [candidates/live-trading-services.md]" in matrix
    assert "July 4, 2026 decision keeps live trading services rejected for now" in strategy
