"""Durable asyncpg-backed media spend ledger store (#342).

Implements the same interface as `media_ledger.InMemoryLedgerStore` against
`public.media_spend_ledger` (schema: `services/supabase/db/scripts/17-backend-media-ledger.sql`).
`reserve_within_cap` is the cross-worker-safe reservation: it runs the
totals-check + insert inside one transaction guarded by a per-scope advisory
lock, so two simultaneous submissions at the remaining-budget boundary cannot
both pass.

asyncpg is imported lazily (via `db_connection`) so this module is only loaded
when `MEDIA_BUDGET_STORE=postgres` is actually selected — the in-memory /
disabled paths never touch a database, keeping the backend CI unit-test venv
satisfied.
"""

from __future__ import annotations

import json
import zlib
from datetime import datetime
from typing import Any, List, Optional, Tuple

from media_ledger import (
    STATUS_COMMITTED,
    LedgerRecord,
    _RESERVED_STATES,
)

_TABLE = "public.media_spend_ledger"

_COLUMNS = (
    "operation_id",
    "consumer",
    "project",
    "provider",
    "model",
    "model_version",
    "modality",
    "status",
    "currency",
    "estimated_cost_usd",
    "final_cost_usd",
    "pricing_source_ts",
    "artifact_refs",
    "reason",
    "created_at",
    "updated_at",
)


def _record_to_params(record: LedgerRecord) -> list:
    return [
        record.operation_id,
        record.consumer,
        record.project,
        record.provider,
        record.model,
        record.model_version,
        record.modality,
        record.status,
        record.currency,
        record.estimated_cost_usd,
        record.final_cost_usd,
        record.pricing_source_ts,
        json.dumps(list(record.artifact_refs)),
        record.reason,
        record.created_at,
        record.updated_at,
    ]


def _row_to_record(row: Any) -> LedgerRecord:
    refs = row["artifact_refs"]
    if isinstance(refs, str):
        try:
            refs = json.loads(refs)
        except (ValueError, TypeError):
            refs = []
    return LedgerRecord(
        operation_id=row["operation_id"],
        consumer=row["consumer"],
        project=row["project"],
        provider=row["provider"],
        model=row["model"],
        model_version=row["model_version"],
        modality=row["modality"],
        status=row["status"],
        currency=row["currency"],
        estimated_cost_usd=_as_float(row["estimated_cost_usd"]),
        final_cost_usd=_as_float(row["final_cost_usd"]),
        pricing_source_ts=row["pricing_source_ts"],
        artifact_refs=tuple(refs or ()),
        reason=row["reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _as_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _scope_lock_key(consumer: str, project: str) -> int:
    # Stable 63-bit advisory-lock key from the consumer/project scope.
    digest = zlib.crc32(f"{consumer}:{project}".encode("utf-8"))
    return int(digest)


class PostgresLedgerStore:
    """Durable ledger store. Connects on demand (no shared pool)."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def _connect(self):
        # Lazy: asyncpg is only imported when the durable store is used.
        from db_connection import connect_postgres

        return await connect_postgres(self._database_url)

    async def append(self, record: LedgerRecord) -> None:
        conn = await self._connect()
        try:
            await conn.execute(self._insert_sql(), *_record_to_params(record))
        finally:
            await conn.close()

    async def get(self, operation_id: str) -> Optional[LedgerRecord]:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} "
                "WHERE operation_id = $1 ORDER BY created_at DESC LIMIT 1",
                operation_id,
            )
            return _row_to_record(row) if row else None
        finally:
            await conn.close()

    async def update(self, record: LedgerRecord) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                f"UPDATE {_TABLE} SET status = $2, final_cost_usd = $3, "
                "artifact_refs = $4, reason = $5, updated_at = $6 "
                "WHERE operation_id = $1",
                record.operation_id,
                record.status,
                record.final_cost_usd,
                json.dumps(list(record.artifact_refs)),
                record.reason,
                record.updated_at,
            )
        finally:
            await conn.close()

    async def rekey(self, old_id: str, new_id: str) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                f"UPDATE {_TABLE} SET operation_id = $2, updated_at = now() "
                "WHERE operation_id = $1",
                old_id,
                new_id,
            )
        finally:
            await conn.close()

    async def totals(self, consumer: str, project: str) -> Tuple[float, float]:
        conn = await self._connect()
        try:
            return await self._totals(conn, consumer, project)
        finally:
            await conn.close()

    async def _totals(self, conn, consumer: str, project: str) -> Tuple[float, float]:
        rows = await conn.fetch(
            f"SELECT status, "
            "COALESCE(final_cost_usd, estimated_cost_usd, 0) AS cost "
            f"FROM {_TABLE} WHERE consumer = $1 AND project = $2",
            consumer,
            project,
        )
        reserved = 0.0
        committed = 0.0
        for row in rows:
            cost = float(row["cost"] or 0)
            if row["status"] in _RESERVED_STATES:
                reserved += cost
            elif row["status"] == STATUS_COMMITTED:
                committed += cost
        return reserved, committed

    async def reserve_within_cap(
        self, record: LedgerRecord, cap: Optional[float]
    ) -> bool:
        """Atomic totals-check + insert under a per-scope advisory lock."""
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)",
                    _scope_lock_key(record.consumer, record.project),
                )
                if cap is not None and record.estimated_cost_usd is not None:
                    reserved, committed = await self._totals(
                        conn, record.consumer, record.project
                    )
                    if reserved + committed + record.estimated_cost_usd > cap + 1e-9:
                        return False
                await conn.execute(self._insert_sql(), *_record_to_params(record))
                return True
        finally:
            await conn.close()

    async def list(
        self,
        *,
        consumer: str,
        project: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[LedgerRecord]:
        clauses = ["consumer = $1"]
        params: list = [consumer]
        if project is not None:
            params.append(project)
            clauses.append(f"project = ${len(params)}")
        if since is not None:
            params.append(since)
            clauses.append(f"created_at >= ${len(params)}")
        if until is not None:
            params.append(until)
            clauses.append(f"created_at <= ${len(params)}")
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} "
                f"WHERE {' AND '.join(clauses)} ORDER BY created_at ASC",
                *params,
            )
            return [_row_to_record(row) for row in rows]
        finally:
            await conn.close()

    async def prune(self, older_than: datetime) -> int:
        conn = await self._connect()
        try:
            result = await conn.execute(
                f"DELETE FROM {_TABLE} WHERE created_at < $1", older_than
            )
            # asyncpg returns e.g. "DELETE 3".
            try:
                return int(str(result).split()[-1])
            except (ValueError, IndexError):
                return 0
        finally:
            await conn.close()

    @staticmethod
    def _insert_sql() -> str:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(_COLUMNS)))
        return (
            f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) "
            f"VALUES ({placeholders})"
        )
