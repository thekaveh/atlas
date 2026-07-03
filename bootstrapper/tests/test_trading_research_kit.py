from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tracks import is_in_track, load_tracks


ROOT = Path(__file__).resolve().parents[2]
JUPYTER_BUILD = ROOT / "services" / "jupyterhub" / "build"
FINANCE_HELPER = JUPYTER_BUILD / "atlas_finance" / "research.py"
FINANCE_NOTEBOOK = JUPYTER_BUILD / "notebooks" / "11_financial_research_kit.ipynb"


def _requirement_lines() -> set[str]:
    lines: set[str] = set()
    for raw_line in (JUPYTER_BUILD / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line and not line.startswith("--"):
            lines.add(line)
    return lines


def _load_helper():
    assert FINANCE_HELPER.exists(), "Atlas finance helper module must exist"
    spec = importlib.util.spec_from_file_location("atlas_finance.research", FINANCE_HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_jupyterhub_image_pins_openbb_and_ccxt_for_financial_research() -> None:
    requirements = _requirement_lines()

    assert "openbb==4.7.2" in requirements
    assert "openbb-yfinance==1.6.3" in requirements
    assert "ccxt==4.5.64" in requirements


def test_finance_helper_blocks_live_exchange_credentials_and_private_methods() -> None:
    helper = _load_helper()

    helper.assert_no_live_exchange_credentials({"OPENAI_API_KEY": "allowed-for-litellm"})

    with pytest.raises(RuntimeError, match="Live exchange credentials are blocked"):
        helper.assert_no_live_exchange_credentials({"BINANCE_API_KEY": "not-in-v1"})

    for method in ("fetch_ticker", "fetch_ohlcv", "fetch_order_book", "load_markets"):
        helper.assert_public_ccxt_method(method)

    for method in ("create_order", "fetch_balance", "withdraw", "transfer"):
        with pytest.raises(ValueError, match="private/trading CCXT method"):
            helper.assert_public_ccxt_method(method)


def test_finance_helper_computes_paper_portfolio_without_exchange_side_effects() -> None:
    helper = _load_helper()

    summary = helper.paper_portfolio_summary(
        positions=[
            {"symbol": "BTC/USDT", "quantity": 0.10, "cost_basis": 60000.0},
            {"symbol": "ETH/USDT", "quantity": 1.50, "cost_basis": 3000.0},
        ],
        marks={"BTC/USDT": 64000.0, "ETH/USDT": 2800.0},
    )

    assert summary["total_market_value"] == pytest.approx(10600.0)
    assert summary["total_cost_basis"] == pytest.approx(10500.0)
    assert summary["unrealized_pnl"] == pytest.approx(100.0)
    assert summary["positions"][0]["weight"] == pytest.approx(6400.0 / 10600.0)
    assert summary["positions"][1]["unrealized_pnl"] == pytest.approx(-300.0)


def test_financial_research_notebook_is_registered_and_guarded() -> None:
    notebook = json.loads(FINANCE_NOTEBOOK.read_text(encoding="utf-8"))
    cells = notebook.get("cells", [])
    text = "\n".join("".join(cell.get("source", [])) for cell in cells)

    assert notebook["nbformat"] == 4
    assert notebook["nbformat_minor"] >= 5
    assert all(cell.get("id") for cell in cells)
    for expected in [
        "OpenBB",
        "CCXT",
        "not financial advice",
        "no live trading",
        "assert_no_live_exchange_credentials",
        "paper_portfolio_summary",
        "AWS_ENDPOINT_URL_S3",
        "MLFLOW_TRACKING_URI",
        "LITELLM_BASE_URL",
    ]:
        assert expected in text

    for doc in (
        ROOT / "services" / "jupyterhub" / "README.md",
        ROOT / "services" / "jupyterhub" / "build" / "README.md",
    ):
        assert "`11_financial_research_kit.ipynb`" in doc.read_text(encoding="utf-8")


def test_trading_track_is_research_only_and_excludes_live_trading_services() -> None:
    registry = load_tracks()
    trading = registry.by_key["trading"]

    assert trading.display_name == "Trading / Financial Research"
    assert "paper portfolios" in trading.description.lower()

    for service in ("jupyterhub", "minio", "mlflow", "langfuse"):
        assert is_in_track(trading, service, always_on=registry.always_on)

    for service in ("redpanda", "trino", "airflow", "spark"):
        assert not is_in_track(trading, service, always_on=registry.always_on)
