import asyncio
from unittest.mock import AsyncMock, patch

import pytest


def _run(coro):
    return asyncio.run(coro)


def test_connect_postgres_keeps_asyncpg_defaults_for_direct_database_url():
    from db_connection import connect_postgres

    with patch("asyncpg.connect", new_callable=AsyncMock) as mock_connect:
        _run(connect_postgres("postgresql://user:pw@supabase-db:5432/postgres"))

    mock_connect.assert_awaited_once_with(
        "postgresql://user:pw@supabase-db:5432/postgres",
        timeout=10,
        command_timeout=30,
    )


def test_connect_postgres_disables_statement_cache_for_supavisor_transaction_url():
    from db_connection import connect_postgres

    with patch("asyncpg.connect", new_callable=AsyncMock) as mock_connect:
        _run(connect_postgres("postgresql://user.atlas:pw@supavisor:6543/postgres"))

    mock_connect.assert_awaited_once_with(
        "postgresql://user.atlas:pw@supavisor:6543/postgres",
        timeout=10,
        command_timeout=30,
        statement_cache_size=0,
    )


def test_connect_postgres_disables_statement_cache_for_any_6543_pooler_url():
    from db_connection import connect_postgres

    with patch("asyncpg.connect", new_callable=AsyncMock) as mock_connect:
        _run(connect_postgres("postgresql://user:pw@pooler.example:6543/postgres"))

    assert mock_connect.await_args.kwargs["statement_cache_size"] == 0


# ---------------------------------------------------------------------------
# Shared connection pool (#804)
# ---------------------------------------------------------------------------

class _FakePool:
    """Minimal asyncpg.Pool stand-in: bounds concurrent acquisitions to
    ``max_size`` and records the peak so the concurrency test can prove the
    pool is never over-subscribed."""

    def __init__(self, max_size):
        import asyncio as _asyncio

        self.max_size = max_size
        self._sem = _asyncio.Semaphore(max_size)
        self.in_use = 0
        self.peak = 0
        self._closed = False
        self.terminated = False

    async def acquire(self, timeout=None):
        """Mirror asyncpg: awaitable acquisition honoring ``timeout`` by
        raising TimeoutError, releasing nothing on expiry."""
        import asyncio as _asyncio

        if timeout is None:
            await self._sem.acquire()
        else:
            await _asyncio.wait_for(self._sem.acquire(), timeout)
        self.in_use += 1
        self.peak = max(self.peak, self.in_use)
        return object()

    async def release(self, _conn):
        self.in_use -= 1
        self._sem.release()

    def get_size(self):
        return self.max_size

    def get_idle_size(self):
        return self.max_size - self.in_use

    async def close(self):
        self._closed = True

    def is_closing(self):
        return self._closed

    def terminate(self):
        self.terminated = True
        self._closed = True


def _reset_pools():
    import db_connection

    db_connection._pools.clear()


def _patch_create_pool(created):
    async def _create_pool(url, **kwargs):
        pool = _FakePool(kwargs.get("max_size", 10))
        created.append((url, kwargs, pool))
        return pool

    return patch("asyncpg.create_pool", _create_pool)


def test_get_pg_pool_caches_one_pool_per_url():
    _reset_pools()
    from db_connection import get_pg_pool

    created = []
    with _patch_create_pool(created):
        p1 = _run(get_pg_pool("postgresql://u:p@db:5432/atlas"))
        p2 = _run(get_pg_pool("postgresql://u:p@db:5432/atlas"))

    assert p1 is p2
    assert len(created) == 1  # second call served from cache
    _reset_pools()


def test_get_pg_pool_disables_statement_cache_for_transaction_pooler():
    _reset_pools()
    from db_connection import get_pg_pool

    created = []
    with _patch_create_pool(created):
        _run(get_pg_pool("postgresql://u.atlas:p@supavisor:6543/postgres"))

    _url, kwargs, _pool = created[0]
    assert kwargs["statement_cache_size"] == 0
    _reset_pools()


def test_get_pg_pool_keeps_defaults_for_direct_url():
    _reset_pools()
    from db_connection import get_pg_pool

    created = []
    with _patch_create_pool(created):
        _run(get_pg_pool("postgresql://u:p@supabase-db:5432/postgres"))

    _url, kwargs, _pool = created[0]
    assert "statement_cache_size" not in kwargs
    assert kwargs["timeout"] == 10 and kwargs["command_timeout"] == 30
    _reset_pools()


def test_get_pg_pool_honours_env_sizing(monkeypatch):
    _reset_pools()
    import db_connection

    monkeypatch.setattr(db_connection, "_POOL_MIN", 2)
    monkeypatch.setattr(db_connection, "_POOL_MAX", 7)
    created = []
    with _patch_create_pool(created):
        _run(db_connection.get_pg_pool("postgresql://u:p@db:5432/atlas"))

    _url, kwargs, _pool = created[0]
    assert kwargs["min_size"] == 2 and kwargs["max_size"] == 7
    _reset_pools()


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(-1, 10), (1, 0), (11, 10)],
)
def test_get_pg_pool_rejects_invalid_sizing(monkeypatch, minimum, maximum):
    _reset_pools()
    import db_connection

    monkeypatch.setattr(db_connection, "_POOL_MIN", minimum)
    monkeypatch.setattr(db_connection, "_POOL_MAX", maximum)
    with pytest.raises(db_connection.PoolConfigurationError):
        _run(db_connection.get_pg_pool("postgresql://u:p@db:5432/atlas"))
    _reset_pools()


def test_acquire_conn_yields_a_pooled_connection():
    _reset_pools()
    from db_connection import acquire_conn

    async def _use():
        async with acquire_conn("postgresql://u:p@db:5432/atlas") as conn:
            return conn

    with _patch_create_pool([]):
        conn = _run(_use())

    assert conn is not None
    _reset_pools()


def test_close_pg_pools_disposes_and_clears_cache():
    _reset_pools()
    import db_connection

    created = []
    with _patch_create_pool(created):
        _run(db_connection.get_pg_pool("postgresql://u:p@db:5432/atlas"))
        pool = created[0][2]
        _run(db_connection.close_pg_pools())

    assert pool._closed is True
    assert db_connection._pools == {}
    _reset_pools()


def test_close_pg_pools_terminates_a_pool_that_misses_the_deadline(monkeypatch):
    _reset_pools()
    import db_connection

    class StuckPool(_FakePool):
        async def close(self):
            await asyncio.Event().wait()

    pool = StuckPool(1)
    db_connection._pools["postgresql://u:p@db:5432/atlas"] = pool
    monkeypatch.setattr(db_connection, "_POOL_CLOSE_TIMEOUT_SECONDS", 0.01)

    _run(db_connection.close_pg_pools())

    assert pool.terminated is True
    assert db_connection._pools == {}
    _reset_pools()


def test_close_pg_pools_evicts_and_terminates_a_pool_whose_close_raises():
    _reset_pools()
    import db_connection

    class BrokenClosePool(_FakePool):
        async def close(self):
            raise RuntimeError("close failed")

    pool = BrokenClosePool(1)
    db_connection._pools["postgresql://u:p@db:5432/atlas"] = pool

    _run(db_connection.close_pg_pools())

    assert db_connection._pools == {}
    assert pool.terminated is True
    _reset_pools()


def test_pool_never_exceeds_max_size_under_concurrency():
    _reset_pools()
    import asyncio as _asyncio

    from db_connection import acquire_conn

    async def _worker(hold):
        async with acquire_conn("postgresql://u:p@db:5432/atlas"):
            await _asyncio.sleep(hold)

    async def _drive(created):
        # 20 concurrent acquisitions against a max_size=3 pool.
        await _asyncio.gather(*(_worker(0.01) for _ in range(20)))
        return created[0][2]

    created = []
    with patch("db_connection._POOL_MAX", 3), _patch_create_pool(created):
        pool = _run(_drive(created))

    assert pool.peak <= 3, f"pool over-subscribed: peak={pool.peak}"
    assert pool.in_use == 0  # all released
    _reset_pools()


# ---------------------------------------------------------------------------
# Bounded pool acquisition (#1171)
# ---------------------------------------------------------------------------

def test_saturated_pool_fails_within_the_acquisition_deadline():
    _reset_pools()
    import asyncio as _asyncio
    import db_connection
    from db_connection import PoolSaturatedError, acquire_conn

    async def _drive():
        holder_entered = _asyncio.Event()
        proceed = _asyncio.Event()

        async def _holder():
            async with acquire_conn("postgresql://u:p@db:5432/atlas"):
                holder_entered.set()
                await proceed.wait()

        holder = _asyncio.create_task(_holder())
        await holder_entered.wait()
        loop = _asyncio.get_running_loop()
        started = loop.time()
        try:
            async with acquire_conn("postgresql://u:p@db:5432/atlas"):
                raise AssertionError("second acquisition must not succeed")
        except PoolSaturatedError as exc:
            elapsed = loop.time() - started
            proceed.set()
            await holder
            return exc, elapsed

    with patch("db_connection._POOL_MAX", 1), patch(
        "db_connection._POOL_MIN", 0
    ), patch.object(
        db_connection, "_POOL_ACQUIRE_TIMEOUT_SECONDS", 0.05
    ), _patch_create_pool([]):
        exc, elapsed = _run(_drive())

    assert "saturated" in str(exc)
    assert "postgresql://" not in str(exc)  # never the DSN
    assert elapsed < 1.0
    _reset_pools()


def test_timed_out_acquisition_leaks_no_slot():
    _reset_pools()
    import asyncio as _asyncio
    import db_connection
    from db_connection import PoolSaturatedError, acquire_conn

    async def _drive(created):
        holder_entered = _asyncio.Event()
        proceed = _asyncio.Event()

        async def _holder():
            async with acquire_conn("postgresql://u:p@db:5432/atlas"):
                holder_entered.set()
                await proceed.wait()

        holder = _asyncio.create_task(_holder())
        await holder_entered.wait()
        try:
            async with acquire_conn("postgresql://u:p@db:5432/atlas"):
                pass
        except PoolSaturatedError:
            pass
        proceed.set()
        await holder
        # The slot freed by the holder must be immediately acquirable.
        async with acquire_conn("postgresql://u:p@db:5432/atlas"):
            pass
        return created[0][2]

    created = []
    with patch("db_connection._POOL_MAX", 1), patch(
        "db_connection._POOL_MIN", 0
    ), patch.object(
        db_connection, "_POOL_ACQUIRE_TIMEOUT_SECONDS", 0.05
    ), _patch_create_pool(created):
        pool = _run(_drive(created))

    assert pool.in_use == 0
    _reset_pools()


def test_cancelled_caller_returns_its_checked_out_connection():
    _reset_pools()
    import asyncio as _asyncio
    from db_connection import acquire_conn

    async def _drive(created):
        entered = _asyncio.Event()

        async def _victim():
            async with acquire_conn("postgresql://u:p@db:5432/atlas"):
                entered.set()
                await _asyncio.sleep(30)

        victim = _asyncio.create_task(_victim())
        await entered.wait()
        victim.cancel()
        try:
            await victim
        except _asyncio.CancelledError:
            pass
        # Cancellation released the slot: the next acquisition succeeds.
        async with acquire_conn("postgresql://u:p@db:5432/atlas"):
            pass
        return created[0][2]

    created = []
    with patch("db_connection._POOL_MAX", 1), patch(
        "db_connection._POOL_MIN", 0
    ), _patch_create_pool(created):
        pool = _run(_drive(created))

    assert pool.in_use == 0
    _reset_pools()


def test_saturation_maps_to_503_with_retry_after(monkeypatch):
    import os

    # main.py needs these at import time; stub only when absent (#817 style).
    for _var, _default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(_var):
            monkeypatch.setenv(_var, _default)

    from db_connection import PoolSaturatedError
    import main

    async def _call():
        return await main._pg_pool_saturated(
            None, PoolSaturatedError(deadline=5.0, size=10, free=0)
        )

    response = _run(_call())
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"

