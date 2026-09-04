"""Configuration and public failure contract for shared Backend state."""

from __future__ import annotations

import os


class StateStoreUnavailable(RuntimeError):
    """Shared state cannot satisfy a durable/multi-process operation."""


def backend_state_store_mode() -> str:
    """Return the explicit state mode; production defaults to durable Redis."""
    mode = os.getenv("BACKEND_STATE_STORE_MODE", "redis")
    if mode not in {"redis", "memory"}:
        raise StateStoreUnavailable(
            "BACKEND_STATE_STORE_MODE must be exactly 'redis' or 'memory'"
        )
    return mode


def redis_url() -> str:
    value = (os.getenv("REDIS_URL") or "").strip()
    if not value:
        raise StateStoreUnavailable(
            "REDIS_URL is required when BACKEND_STATE_STORE_MODE=redis"
        )
    return value


def state_store_detail(message: str = "Shared state store is unavailable") -> dict[str, str]:
    return {"code": "state_store_unavailable", "message": message}
