from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable

import httpx
import redis.asyncio as redis

from db_connection import connect_postgres


async def _postgres_ready() -> None:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is unset")
    conn = await connect_postgres(database_url, timeout=3, command_timeout=3)
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()


async def _redis_ready() -> None:
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        raise RuntimeError("REDIS_URL is unset")
    client = redis.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _litellm_ready() -> None:
    base_url = (os.getenv("LITELLM_BASE_URL") or "").rstrip("/")
    if not base_url:
        raise RuntimeError("LITELLM_BASE_URL is unset")
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.get(f"{base_url}/health/liveliness")
        response.raise_for_status()


async def check_backend_readiness() -> dict[str, str]:
    """Probe the Backend's required state, broker, and inference dependencies."""
    probes: dict[str, Callable[[], Awaitable[None]]] = {
        "postgres": _postgres_ready,
        "redis": _redis_ready,
        "litellm": _litellm_ready,
    }
    outcomes = await asyncio.gather(
        *(probe() for probe in probes.values()), return_exceptions=True
    )
    return {
        name: "ready" if not isinstance(outcome, BaseException) else "unavailable"
        for name, outcome in zip(probes, outcomes, strict=True)
    }
