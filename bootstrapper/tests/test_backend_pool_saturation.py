"""CI contract for bounded PostgreSQL pool acquisition (#1171).

Runs the backend's real ``acquire_conn`` path in-process against a faked
``asyncpg.create_pool`` whose acquisition semantics mirror asyncpg's
(awaitable ``acquire(timeout=)`` raising TimeoutError, explicit ``release``).
asyncpg itself comes from the dev dependency group, so ``db_connection``
imports cleanly in the required CI unit-test job; no database server and no
Docker are involved. The backend container suite carries the same scenarios
plus the 503/Retry-After handler mapping.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND_APP = Path(__file__).resolve().parents[2] / "services" / "backend" / "app" / "app"


def _load_db_connection():
    spec = importlib.util.spec_from_file_location(
        "atlas_backend_db_connection", BACKEND_APP / "db_connection.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


db_connection = _load_db_connection()


class _FakePool:
    """asyncpg.Pool stand-in with real acquisition semantics."""

    def __init__(self, max_size: int):
        self.max_size = max_size
        self._sem = asyncio.Semaphore(max_size)
        self.in_use = 0

    async def acquire(self, timeout=None):
        if timeout is None:
            await self._sem.acquire()
        else:
            await asyncio.wait_for(self._sem.acquire(), timeout)
        self.in_use += 1
        return object()

    async def release(self, _conn):
        self.in_use -= 1
        self._sem.release()

    def get_size(self):
        return self.max_size

    def get_idle_size(self):
        return self.max_size - self.in_use

    def is_closing(self):
        return False


def _patched(created: list):
    async def _create_pool(url, **kwargs):
        pool = _FakePool(kwargs.get("max_size", 10))
        created.append(pool)
        return pool

    return patch.object(db_connection.asyncpg, "create_pool", _create_pool)


URL = "postgresql://u:p@db:5432/atlas"


def test_saturated_pool_fails_within_the_deadline_and_leaks_no_slot():
    db_connection._pools.clear()

    async def _drive(created):
        holder_entered = asyncio.Event()
        proceed = asyncio.Event()

        async def _holder():
            async with db_connection.acquire_conn(URL):
                holder_entered.set()
                await proceed.wait()

        holder = asyncio.create_task(_holder())
        await holder_entered.wait()
        loop = asyncio.get_running_loop()
        started = loop.time()
        caught = None
        try:
            async with db_connection.acquire_conn(URL):
                raise AssertionError("second acquisition must not succeed")
        except db_connection.PoolSaturatedError as exc:
            caught = exc
            elapsed = loop.time() - started
        proceed.set()
        await holder
        # The freed slot must be immediately acquirable — nothing leaked.
        async with db_connection.acquire_conn(URL):
            pass
        return created[0], caught, elapsed

    created: list = []
    with patch.object(db_connection, "_POOL_MAX", 1), patch.object(
        db_connection, "_POOL_MIN", 0
    ), patch.object(
        db_connection, "_POOL_ACQUIRE_TIMEOUT_SECONDS", 0.05
    ), _patched(created):
        pool, exc, elapsed = asyncio.run(_drive(created))

    assert pool.in_use == 0
    assert "saturated" in str(exc)
    assert "postgresql://" not in str(exc)
    assert elapsed < 1.0
    db_connection._pools.clear()


def test_cancelled_caller_returns_its_checked_out_connection():
    db_connection._pools.clear()

    async def _drive(created):
        entered = asyncio.Event()

        async def _victim():
            async with db_connection.acquire_conn(URL):
                entered.set()
                await asyncio.sleep(30)

        victim = asyncio.create_task(_victim())
        await entered.wait()
        victim.cancel()
        try:
            await victim
        except asyncio.CancelledError:
            pass
        async with db_connection.acquire_conn(URL):
            pass
        return created[0]

    created: list = []
    with patch.object(db_connection, "_POOL_MAX", 1), patch.object(
        db_connection, "_POOL_MIN", 0
    ), _patched(created):
        pool = asyncio.run(_drive(created))

    assert pool.in_use == 0
    db_connection._pools.clear()


def test_command_timeouts_inside_the_block_are_not_misclassified():
    """A TimeoutError raised by the caller's own query must propagate as-is,
    never as PoolSaturatedError — the deadline guards acquisition only."""
    db_connection._pools.clear()

    async def _drive():
        try:
            async with db_connection.acquire_conn(URL):
                raise TimeoutError("query exceeded command_timeout")
        except TimeoutError as exc:
            return exc

    with patch.object(db_connection, "_POOL_MAX", 1), patch.object(
        db_connection, "_POOL_MIN", 0
    ), _patched([]):
        exc = asyncio.run(_drive())

    assert not isinstance(exc, db_connection.PoolSaturatedError)
    assert "command_timeout" in str(exc)
    db_connection._pools.clear()
