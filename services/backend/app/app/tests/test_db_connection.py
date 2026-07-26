import asyncio
from unittest.mock import AsyncMock, patch


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

    def acquire(self):
        pool = self

        class _Acq:
            async def __aenter__(self):
                await pool._sem.acquire()
                pool.in_use += 1
                pool.peak = max(pool.peak, pool.in_use)
                return object()

            async def __aexit__(self, *_exc):
                pool.in_use -= 1
                pool._sem.release()
                return False

        return _Acq()

    async def close(self):
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
