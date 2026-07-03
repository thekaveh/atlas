"""Safe helpers for Atlas' financial research notebooks.

This module intentionally supports public market-data research and paper
portfolio accounting only. It does not create authenticated exchange clients,
place orders, or read live exchange credentials.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


BLOCKED_EXCHANGE_CREDENTIAL_ENV: frozenset[str] = frozenset(
    {
        "CCXT_API_KEY",
        "CCXT_SECRET",
        "CCXT_PASSWORD",
        "BINANCE_API_KEY",
        "BINANCE_SECRET",
        "BINANCE_SECRET_KEY",
        "COINBASE_API_KEY",
        "COINBASE_SECRET",
        "COINBASE_SECRET_KEY",
        "KRAKEN_API_KEY",
        "KRAKEN_SECRET",
        "KRAKEN_SECRET_KEY",
        "OKX_API_KEY",
        "OKX_SECRET",
        "OKX_SECRET_KEY",
        "BYBIT_API_KEY",
        "BYBIT_SECRET",
        "BYBIT_SECRET_KEY",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
    }
)

PRIVATE_CCXT_METHOD_PREFIXES: tuple[str, ...] = (
    "create_",
    "cancel_",
    "edit_",
    "withdraw",
    "transfer",
    "borrow",
    "repay",
    "set_",
)

PRIVATE_CCXT_METHODS: frozenset[str] = frozenset(
    {
        "fetch_balance",
        "fetch_accounts",
        "fetch_deposit",
        "fetch_deposits",
        "fetch_ledger",
        "fetch_my_trades",
        "fetch_open_orders",
        "fetch_closed_orders",
        "fetch_order",
        "fetch_orders",
        "fetch_positions",
        "fetch_transaction_fee",
        "fetch_transactions",
    }
)


def assert_no_live_exchange_credentials(env: Mapping[str, str | None]) -> None:
    """Fail fast when notebook env contains live exchange credential names."""

    blocked = sorted(
        key
        for key in env
        if key.upper() in BLOCKED_EXCHANGE_CREDENTIAL_ENV and env.get(key)
    )
    if blocked:
        names = ", ".join(blocked)
        raise RuntimeError(
            "Live exchange credentials are blocked in the Atlas financial "
            f"research kit: {names}. Use public/read-only data or a later "
            "secrets-managed paper/live trading workflow."
        )


def assert_public_ccxt_method(method_name: str) -> None:
    """Allow only public/read-only CCXT method names in notebook examples."""

    normalized = method_name.strip()
    if normalized in PRIVATE_CCXT_METHODS or normalized.startswith(PRIVATE_CCXT_METHOD_PREFIXES):
        raise ValueError(
            f"{method_name!r} is a private/trading CCXT method and is blocked "
            "in this first Atlas financial research slice."
        )


def make_public_exchange_config() -> dict[str, Any]:
    """Return the CCXT config Atlas examples use for unauthenticated data."""

    return {
        "enableRateLimit": True,
        "timeout": 15_000,
    }


def paper_portfolio_summary(
    *,
    positions: Sequence[Mapping[str, float | str]],
    marks: Mapping[str, float],
) -> dict[str, Any]:
    """Summarize paper positions against mark prices without side effects."""

    rows: list[dict[str, Any]] = []
    total_cost = 0.0
    total_value = 0.0

    for position in positions:
        symbol = str(position["symbol"])
        quantity = float(position["quantity"])
        cost_basis = float(position["cost_basis"])
        mark = float(marks[symbol])
        cost_value = quantity * cost_basis
        market_value = quantity * mark
        pnl = market_value - cost_value
        total_cost += cost_value
        total_value += market_value
        rows.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "cost_basis": cost_basis,
                "mark": mark,
                "cost_value": cost_value,
                "market_value": market_value,
                "unrealized_pnl": pnl,
                "unrealized_pnl_pct": (pnl / cost_value) if cost_value else 0.0,
            }
        )

    for row in rows:
        row["weight"] = (row["market_value"] / total_value) if total_value else 0.0

    total_pnl = total_value - total_cost
    return {
        "positions": rows,
        "total_cost_basis": total_cost,
        "total_market_value": total_value,
        "unrealized_pnl": total_pnl,
        "unrealized_pnl_pct": (total_pnl / total_cost) if total_cost else 0.0,
    }
