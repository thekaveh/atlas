from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from media_ledger import STATUS_RESERVED, LedgerRecord, _utcnow


def _run(coro):
    return asyncio.run(coro)


def _acquire_patch(conn):
    """Patch the shared pool's ``acquire_conn`` (#804) to yield ``conn``.

    The durable ledger draws short-lived connections from the pool via
    ``db_connection.acquire_conn``; tests inject their FakeConn there instead
    of at ``asyncpg.connect``."""

    @asynccontextmanager
    async def _acq(_url, **_kw):
        yield conn

    return patch("db_connection.acquire_conn", _acq)


class _FakeTxn:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


class FakeConn:
    """Records SQL + args; returns canned rows. Mirrors asyncpg's async API."""

    def __init__(self, *, fetch_rows=None, fetchrow_result=None, execute_result="INSERT 0 1"):
        self.calls = []
        self._fetch_rows = fetch_rows or []
        self._fetchrow = fetchrow_result
        self._execute_result = execute_result
        self.closed = False

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return self._execute_result

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self._fetch_rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self._fetchrow

    async def close(self):
        self.closed = True

    def transaction(self):
        return _FakeTxn()

    def executed_sql(self):
        return [c[1] for c in self.calls if c[0] == "execute"]

    def inserted(self):
        return [c for c in self.calls if c[0] == "execute" and "INSERT INTO" in c[1]]


def _store(conn):
    from media_ledger_postgres import PostgresLedgerStore

    store = PostgresLedgerStore("postgresql://x:x@db:5432/atlas")
    # Patch asyncpg.connect so connect_postgres returns our fake connection.
    return store


def _record(op_id="op-1", cost=0.05, consumer="acme"):
    now = _utcnow()
    return LedgerRecord(
        operation_id=op_id,
        consumer=consumer,
        project="default",
        provider="fal",
        model="fal-ai/trellis",
        modality="image_to_3d",
        status=STATUS_RESERVED,
        estimated_cost_usd=cost,
        pricing_source_ts=now,
        created_at=now,
        updated_at=now,
    )


def test_append_inserts_all_columns():
    conn = FakeConn()
    with _acquire_patch(conn):
        _run(_store(conn).append(_record()))

    inserts = conn.inserted()
    assert len(inserts) == 1
    sql, args = inserts[0][1], inserts[0][2]
    assert "INSERT INTO public.media_spend_ledger" in sql
    # 16 columns / 16 bind params.
    assert args[0] == "op-1"  # operation_id
    assert args[1] == "acme"  # consumer
    assert len(args) == 16
    # #804: the connection is drawn from the shared pool (released on context
    # exit), not closed by the caller — so no `conn.closed` assertion here.


def test_reserve_within_cap_admits_under_cap():
    conn = FakeConn(fetch_rows=[])  # no prior spend
    with _acquire_patch(conn):
        admitted = _run(_store(conn).reserve_within_cap(_record(cost=0.05), cap=10.0))

    assert admitted is True
    # Advisory lock taken + the row inserted inside the transaction.
    assert any("pg_advisory_xact_lock" in s for s in conn.executed_sql())
    assert len(conn.inserted()) == 1


def test_reserve_within_cap_denies_over_cap_without_insert():
    # Prior committed spend of 9.99 leaves < 0.05 under a 10.0 cap.
    conn = FakeConn(fetch_rows=[{"status": "committed", "cost": 9.99}])
    with _acquire_patch(conn):
        admitted = _run(_store(conn).reserve_within_cap(_record(cost=0.05), cap=10.0))

    assert admitted is False
    # Locked + checked totals, but did NOT insert an over-budget row.
    assert any("pg_advisory_xact_lock" in s for s in conn.executed_sql())
    assert conn.inserted() == []


def test_totals_splits_reserved_and_committed():
    conn = FakeConn(
        fetch_rows=[
            {"status": "reserved", "cost": 0.05},
            {"status": "submitted", "cost": 0.03},
            {"status": "committed", "cost": 0.10},
            {"status": "released", "cost": 0.99},  # ignored
            {"status": "denied", "cost": 0.99},  # ignored
        ]
    )
    with _acquire_patch(conn):
        reserved, committed = _run(_store(conn).totals("acme", "default"))

    assert round(reserved, 6) == 0.08
    assert round(committed, 6) == 0.10


def test_get_maps_row_to_record():
    now = _utcnow()
    row = {
        "operation_id": "op-9",
        "consumer": "acme",
        "project": "default",
        "provider": "fal",
        "model": "fal-ai/trellis",
        "model_version": None,
        "modality": "image_to_3d",
        "status": "committed",
        "currency": "USD",
        "estimated_cost_usd": 0.05,
        "final_cost_usd": 0.05,
        "pricing_source_ts": now,
        "artifact_refs": '["https://cdn/x.glb"]',
        "reason": None,
        "created_at": now,
        "updated_at": now,
    }
    conn = FakeConn(fetchrow_result=row)
    with _acquire_patch(conn):
        rec = _run(_store(conn).get("op-9"))

    assert rec.operation_id == "op-9"
    assert rec.final_cost_usd == 0.05
    assert rec.artifact_refs == ("https://cdn/x.glb",)


def test_rekey_updates_operation_id():
    conn = FakeConn()
    with _acquire_patch(conn):
        _run(_store(conn).rekey("resv-1", "prov-1"))

    updates = [s for s in conn.executed_sql() if "UPDATE public.media_spend_ledger" in s]
    assert updates and "operation_id = $2" in updates[0]
    assert conn.calls[0][2] == ("resv-1", "prov-1")


def test_prune_returns_deleted_count():
    conn = FakeConn(execute_result="DELETE 3")
    with _acquire_patch(conn):
        pruned = _run(_store(conn).prune(_utcnow()))

    assert pruned == 3
