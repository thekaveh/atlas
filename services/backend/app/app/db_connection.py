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
#: Acquisition deadline (#1171). command_timeout bounds a query, but a
#: saturated pool queued acquisitions indefinitely before this. 5 s keeps a
#: stalled request well inside typical client budgets while riding out brief
#: bursts; a constant (not an env knob) — tests patch it directly.
_POOL_ACQUIRE_TIMEOUT_SECONDS = 5.0

# Saturation observability (#1171). prometheus_client ships with the backend
# runtime (via the FastAPI instrumentator); the guard keeps this module
# importable from environments that only stub the pool in tests.
try:  # pragma: no cover - exercised implicitly by both import paths
    from prometheus_client import Counter, Histogram

    _POOL_SATURATED_TOTAL = Counter(
        "backend_pg_pool_saturated_total",
        "Pool acquisitions that timed out because every slot stayed busy",
    )
    _POOL_ACQUIRE_WAIT_SECONDS = Histogram(
        "backend_pg_pool_acquire_wait_seconds",
        "Time spent waiting to acquire a pooled Postgres connection",
        buckets=(0.001, 0.01, 0.05, 0.25, 1.0, 2.5, 5.0),
    )
except ImportError:  # pragma: no cover
    _POOL_SATURATED_TOTAL = None
    _POOL_ACQUIRE_WAIT_SECONDS = None


class PoolSaturatedError(RuntimeError):
    """Every pool slot stayed busy past the acquisition deadline.

    Temporary overload, NOT a database connectivity failure: readiness stays
    green and the API layer maps this to 503 with a Retry-After. The message
    carries pool occupancy only — never the DSN.
    """

    def __init__(self, *, deadline: float, size: int, free: int):
        super().__init__(
            "PostgreSQL pool saturated: no connection became free within "
            f"{deadline:g}s (size={size}, free={free})"
        )
        self.deadline = deadline


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
    connection across non-DB I/O.

    Acquisition is bounded (#1171): when every slot stays busy past
    ``_POOL_ACQUIRE_TIMEOUT_SECONDS`` this raises :class:`PoolSaturatedError`
    instead of queuing indefinitely. asyncpg owns the timeout/cancellation
    path, so a timed-out or cancelled acquisition never leaks a slot.
    """
    pool = await get_pg_pool(database_url, **pool_kwargs)
    started = asyncio.get_running_loop().time()
    try:
        # The timeout guards ONLY the acquisition wait: a TimeoutError raised
        # later by the caller's own queries (command_timeout) must never be
        # misclassified as saturation.
        conn = await pool.acquire(timeout=_POOL_ACQUIRE_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        if _POOL_SATURATED_TOTAL is not None:
            _POOL_SATURATED_TOTAL.inc()
        size = getattr(pool, "get_size", lambda: -1)()
        idle = getattr(pool, "get_idle_size", lambda: -1)()
        raise PoolSaturatedError(
            deadline=_POOL_ACQUIRE_TIMEOUT_SECONDS, size=size, free=idle
        ) from exc
    if _POOL_ACQUIRE_WAIT_SECONDS is not None:
        _POOL_ACQUIRE_WAIT_SECONDS.observe(
            asyncio.get_running_loop().time() - started
        )
    try:
        yield conn
    finally:
        # Runs on success, caller exceptions, AND caller cancellation — the
        # checked-out slot always returns to the pool.
        await pool.release(conn)


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
