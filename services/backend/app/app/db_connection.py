from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import asyncpg


logger = logging.getLogger(__name__)


def _uses_transaction_pooler(database_url: str) -> bool:
    """Return true for Atlas/Supabase transaction-pooler URLs."""
    parsed = urlparse(database_url)
    if parsed.hostname == "supavisor":
        return True
    return parsed.port == 6543


async def connect_postgres(
    database_url: str,
    *,
    timeout: int = 10,
    command_timeout: int = 30,
):
    """Create an asyncpg connection compatible with direct and pooled URLs."""
    kwargs = {
        "timeout": timeout,
        "command_timeout": command_timeout,
    }
    if _uses_transaction_pooler(database_url):
        kwargs["statement_cache_size"] = 0
    return await asyncpg.connect(database_url, **kwargs)


# ---------------------------------------------------------------------------
# Shared connection pool (#804)
#
# Every SHORT-LIVED DB op should acquire from a shared pool instead of paying a
# fresh TCP + auth handshake per operation. Pools are cached per database_url
# (all backend services read the same DATABASE_URL, so they share one pool) and
# created lazily under a lock; the FastAPI lifespan pre-warms + disposes them.
#
# INVARIANT: never hold a *pooled* connection across a long/non-DB await (an LLM
# completion, an embedding call, a Weaviate/HTTP round-trip, a poll). Doing so
# pins a bounded pool slot across slow I/O and risks a reaped connection. Code
# paths that legitimately hold a connection across such I/O (e.g. the memory
# vector-reconcile loop) must keep using `connect_postgres` — a dedicated
# ephemeral connection — NOT the pool.
# ---------------------------------------------------------------------------

_POOL_MIN = int(os.getenv("BACKEND_PG_POOL_MIN", "1"))
_POOL_MAX = int(os.getenv("BACKEND_PG_POOL_MAX", "10"))
_POOL_CLOSE_TIMEOUT_SECONDS = 10.0


class PoolConfigurationError(ValueError):
    """Raised when the configured asyncpg pool bounds are impossible."""


def validate_pool_config() -> None:
    if _POOL_MIN < 0:
        raise PoolConfigurationError("BACKEND_PG_POOL_MIN must be non-negative")
    if _POOL_MAX <= 0:
        raise PoolConfigurationError("BACKEND_PG_POOL_MAX must be positive")
    if _POOL_MIN > _POOL_MAX:
        raise PoolConfigurationError(
            "BACKEND_PG_POOL_MIN must not exceed BACKEND_PG_POOL_MAX"
        )

_pools: dict[str, asyncpg.Pool] = {}
_pools_lock = asyncio.Lock()


def _terminate_pool(pool: asyncpg.Pool) -> None:
    try:
        pool.terminate()
    except Exception:
        logger.exception("Postgres pool termination failed")


async def get_pg_pool(
    database_url: str,
    *,
    timeout: int = 10,
    command_timeout: int = 30,
) -> asyncpg.Pool:
    """Return the shared asyncpg pool for ``database_url``, creating it lazily.

    Sizing is env-tunable (``BACKEND_PG_POOL_MIN`` / ``BACKEND_PG_POOL_MAX``).
    The transaction-pooler ``statement_cache_size=0`` nuance and the existing
    connect/command timeouts are preserved."""
    validate_pool_config()
    pool = _pools.get(database_url)
    if pool is not None and not pool.is_closing():
        return pool
    async with _pools_lock:
        pool = _pools.get(database_url)
        if pool is not None and not pool.is_closing():
            return pool
        kwargs: dict = {"timeout": timeout, "command_timeout": command_timeout}
        if _uses_transaction_pooler(database_url):
            kwargs["statement_cache_size"] = 0
        pool = await asyncpg.create_pool(
            database_url,
            min_size=_POOL_MIN,
            max_size=_POOL_MAX,
            **kwargs,
        )
        _pools[database_url] = pool
        return pool


@asynccontextmanager
async def acquire_conn(database_url: str, **pool_kwargs):
    """Acquire a pooled connection for a SHORT-LIVED DB op.

    Drop-in for the ``conn = await connect_postgres(...); try: ... finally:
    await conn.close()`` pattern — used as ``async with acquire_conn(url) as
    conn:``. See the pool invariant above: do NOT use this while holding the
    connection across non-DB I/O."""
    pool = await get_pg_pool(database_url, **pool_kwargs)
    async with pool.acquire() as conn:
        yield conn


async def close_pg_pools() -> None:
    """Dispose all cached pools (FastAPI lifespan shutdown)."""
    async with _pools_lock:
        pools = tuple(_pools.values())
        # Evict before awaiting any loop-bound close. A broken pool must never
        # survive cleanup and get reused by the next Celery ``asyncio.run``.
        _pools.clear()
    for pool in pools:
        try:
            await asyncio.wait_for(
                pool.close(), timeout=_POOL_CLOSE_TIMEOUT_SECONDS
            )
        except TimeoutError:
            logger.warning(
                "Postgres pool close exceeded %.1fs; terminating it",
                _POOL_CLOSE_TIMEOUT_SECONDS,
            )
            _terminate_pool(pool)
        except Exception:
            logger.exception("Postgres pool close failed; terminating it")
            _terminate_pool(pool)
