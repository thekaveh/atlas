"""Shared, monotonic state store for hosted media operations."""

from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional

from shared_state import StateStoreUnavailable, backend_state_store_mode, redis_url


TERMINAL_MEDIA_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "timeout"}
)
_KEY_PREFIX = "atlas:media:operations:"
_PENDING_LEDGER_KEY = "atlas:media:pending-ledger-intents"
_PENDING_LEDGER_ZSET = "atlas:media:pending-ledger-intents:v2"
_PENDING_LEDGER_SEQUENCE = "atlas:media:pending-ledger-intents:v2:sequence"
_PENDING_LEDGER_MIGRATION_CURSOR = (
    "atlas:media:pending-ledger-intents:v2:migration-cursor"
)


@dataclass(frozen=True)
class PendingLedgerPage:
    records: list[dict[str, Any]]
    next_cursor: Optional[str]


def _bounded_env(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer from 1 through {maximum}") from exc
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 through {maximum}")
    return value


def media_ledger_recovery_batch_size() -> int:
    return _bounded_env("MEDIA_LEDGER_RECOVERY_BATCH_SIZE", 100, 500)


def media_ledger_recovery_max_cycles() -> int:
    return _bounded_env("MEDIA_LEDGER_RECOVERY_MAX_CYCLES", 4, 50)


class MediaOperationCollisionError(RuntimeError):
    """A provider reused an operation id for a different immutable request."""


def _operation_identity(operation: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(operation.get(field) or "")
        for field in (
            "provider",
            "modality",
            "model",
            "owner_scope",
            "consumer",
            "project",
            "submission_id",
        )
    )


def _has_pending_ledger_intent(operation: dict[str, Any]) -> bool:
    provenance = dict((operation.get("last_payload") or {}).get("provenance") or {})
    status = str((operation.get("last_payload") or {}).get("status") or "")
    return bool(
        provenance.get("ledger_cleanup_pending")
        or provenance.get("ledger_attach_pending")
        or provenance.get("ledger_attach_protection_clear_pending")
        or (
            status in TERMINAL_MEDIA_STATUSES
            and not operation.get("reconciled")
            and (
                operation.get("budget_tracked")
                or provenance.get("ledger_reconciliation_pending")
                or provenance.get("ledger_attach_completed")
            )
        )
    )


def _ttl_seconds() -> int:
    try:
        value = int(os.getenv("MEDIA_OPERATION_TTL_SECONDS", str(7 * 24 * 3600)))
    except ValueError:
        return 7 * 24 * 3600
    return max(60, value)


class InMemoryMediaOperationStore:
    """Process-local fallback with the same transition contract as Redis."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._pending_scores: dict[str, int] = {}
        self._next_pending_score = 0
        self._lock = asyncio.Lock()

    def _sync_pending_index_locked(self, operation: dict[str, Any]) -> None:
        operation_id = str(operation["operation_id"])
        if _has_pending_ledger_intent(operation):
            if operation_id not in self._pending_scores:
                self._next_pending_score += 1
                self._pending_scores[operation_id] = self._next_pending_score
        else:
            self._pending_scores.pop(operation_id, None)

    async def ensure_available(self) -> None:
        return None

    async def create(self, operation: dict[str, Any]) -> dict[str, Any]:
        operation_id = str(operation["operation_id"])
        candidate = deepcopy(operation)
        candidate.setdefault("state_version", 0)
        async with self._lock:
            persisted = self._records.get(operation_id)
            if persisted is not None:
                if _operation_identity(persisted) != _operation_identity(candidate):
                    raise MediaOperationCollisionError(
                        f"media operation id collision: {operation_id}"
                    )
                self._sync_pending_index_locked(persisted)
                return deepcopy(persisted)
            persisted = candidate
            self._records[operation_id] = persisted
            self._sync_pending_index_locked(persisted)
            return deepcopy(persisted)

    async def pending_ledger_intent_page(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> PendingLedgerPage:
        limit = media_ledger_recovery_batch_size() if limit is None else limit
        if not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 through 500")
        cursor_value = int(cursor or 0)
        async with self._lock:
            ordered = sorted(
                (score, operation_id)
                for operation_id, score in self._pending_scores.items()
                if score > cursor_value and operation_id in self._records
            )
            selected_pairs = ordered[:limit]
            selected = [operation_id for _score, operation_id in selected_pairs]
            records = [deepcopy(self._records[operation_id]) for operation_id in selected]
            next_cursor = (
                str(selected_pairs[-1][0]) if len(ordered) > limit else None
            )
        return PendingLedgerPage(records, next_cursor)

    async def defer_pending_ledger_intent(self, operation_id: str) -> bool:
        """Move a still-pending failed intent behind work already queued."""
        async with self._lock:
            operation = self._records.get(operation_id)
            if operation is None or not _has_pending_ledger_intent(operation):
                self._pending_scores.pop(operation_id, None)
                return False
            self._next_pending_score += 1
            self._pending_scores[operation_id] = self._next_pending_score
            return True

    async def pending_ledger_intents(self) -> list[dict[str, Any]]:
        """Compatibility shim returning only the first bounded page."""
        return (await self.pending_ledger_intent_page()).records

    async def get(self, operation_id: str) -> Optional[dict[str, Any]]:
        async with self._lock:
            operation = self._records.get(operation_id)
            return deepcopy(operation) if operation is not None else None

    async def transition_payload(
        self,
        operation_id: str,
        payload: dict[str, Any],
        *,
        expected_status: Optional[str] = None,
    ) -> tuple[Optional[dict[str, Any]], bool]:
        async with self._lock:
            operation = self._records.get(operation_id)
            if operation is None:
                return None, False
            current_status = str(
                (operation.get("last_payload") or {}).get("status", "")
            )
            if current_status in TERMINAL_MEDIA_STATUSES:
                return deepcopy(operation), False
            if expected_status is not None and current_status != expected_status:
                return deepcopy(operation), False
            operation["last_payload"] = deepcopy(payload)
            operation["state_version"] = int(operation.get("state_version", 0)) + 1
            self._sync_pending_index_locked(operation)
            return deepcopy(operation), True

    async def replace_terminal_payload(
        self,
        operation_id: str,
        expected_status: str,
        payload: dict[str, Any],
    ) -> tuple[Optional[dict[str, Any]], bool]:
        async with self._lock:
            operation = self._records.get(operation_id)
            if operation is None:
                return None, False
            current_status = str(
                (operation.get("last_payload") or {}).get("status", "")
            )
            if (
                current_status != expected_status
                or str(payload.get("status", "")) != expected_status
            ):
                return deepcopy(operation), False
            operation["last_payload"] = deepcopy(payload)
            operation["state_version"] = int(operation.get("state_version", 0)) + 1
            self._sync_pending_index_locked(operation)
            return deepcopy(operation), True

    async def mark_reconciled(self, operation_id: str) -> bool:
        async with self._lock:
            operation = self._records.get(operation_id)
            if operation is None:
                return False
            operation["reconciled"] = True
            self._sync_pending_index_locked(operation)
            return True

    async def complete_attach_protection_clear(
        self, operation_id: str
    ) -> tuple[Optional[dict[str, Any]], bool]:
        async with self._lock:
            operation = self._records.get(operation_id)
            if operation is None:
                return None, False
            payload = dict(operation.get("last_payload") or {})
            provenance = dict(payload.get("provenance") or {})
            if not provenance.get("ledger_attach_protection_clear_pending"):
                return deepcopy(operation), False
            provenance["ledger_attach_protection_clear_pending"] = False
            provenance["ledger_attach_protection_clear_completed"] = True
            payload["provenance"] = provenance
            operation["last_payload"] = payload
            operation["state_version"] = int(operation.get("state_version", 0)) + 1
            self._sync_pending_index_locked(operation)
            return deepcopy(operation), True

    async def adopt_ledger_reconciliation(
        self,
        operation_id: str,
        expected_outcome: str,
        payload: dict[str, Any],
    ) -> tuple[Optional[dict[str, Any]], bool]:
        async with self._lock:
            operation = self._records.get(operation_id)
            if operation is None:
                return None, False
            provenance = dict((operation.get("last_payload") or {}).get("provenance") or {})
            if (
                operation.get("reconciled")
                or provenance.get("manual_reconciliation_outcome") != expected_outcome
                or not provenance.get("ledger_reconciliation_pending")
            ):
                return deepcopy(operation), False
            operation["last_payload"] = deepcopy(payload)
            operation["state_version"] = int(operation.get("state_version", 0)) + 1
            self._sync_pending_index_locked(operation)
            return deepcopy(operation), True

    async def adopt_ledger_fallback(
        self,
        operation_id: str,
        expected_version: int,
        payload: dict[str, Any],
    ) -> tuple[Optional[dict[str, Any]], bool]:
        """Align an unreconciled operation to a ledger-only manual winner."""
        async with self._lock:
            operation = self._records.get(operation_id)
            if operation is None:
                return None, False
            if operation.get("reconciled") or int(
                operation.get("state_version", 0)
            ) != expected_version:
                return deepcopy(operation), False
            operation["last_payload"] = deepcopy(payload)
            operation["state_version"] = int(operation.get("state_version", 0)) + 1
            self._sync_pending_index_locked(operation)
            return deepcopy(operation), True

    async def aclose(self) -> None:
        return None


class RedisMediaOperationStore:
    """Redis-backed operation state shared by Backend replicas and restarts."""

    _SCORE_RESERVATION_SCRIPT = """
local function reserve_score(legacy_key, index_key, sequence_key, member, allocate)
    local legacy_type = redis.call('TYPE', legacy_key).ok
    local index_type = redis.call('TYPE', index_key).ok
    local sequence_type = redis.call('TYPE', sequence_key).ok
    if legacy_type ~= 'none' and legacy_type ~= 'set' then
        return nil, 'legacy media pending index must be a set'
    end
    if index_type ~= 'none' and index_type ~= 'zset' then
        return nil, 'v2 media pending index must be a sorted set'
    end
    if sequence_type ~= 'none' and sequence_type ~= 'string' then
        return nil, 'media pending sequence must be a string'
    end
    local sequence_value = redis.call('GET', sequence_key)
    local sequence_number = tonumber(sequence_value)
    if sequence_value and (not sequence_number or sequence_number < 0
       or sequence_number ~= math.floor(sequence_number)
       or sequence_number > 9007199254740991) then
        return nil, 'media pending sequence must be an integer'
    end
    if not allocate or redis.call('ZSCORE', index_key, member) then
        return false, nil
    end
    local sequence = tonumber(sequence_value or '0')
    local highest = redis.call('ZREVRANGE', index_key, 0, 0, 'WITHSCORES')
    if #highest > 0 and sequence < tonumber(highest[2]) then
        sequence = math.floor(tonumber(highest[2]))
    end
    if sequence >= 9007199254740991 then
        return nil, 'media pending sequence exhausted'
    end
    local score = sequence + 1
    redis.call('SET', sequence_key, string.format('%.0f', score))
    return score, nil
end
"""

    _CREATE_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
local blob = redis.call('GET', KEYS[1])
if blob then
    local existing = cjson.decode(blob)
    local payload = existing.last_payload or {}
    local provenance = payload.provenance or {}
    local current = tostring(payload.status or '')
    local terminal = current == 'succeeded' or current == 'failed'
        or current == 'cancelled' or current == 'timeout'
    local pending = provenance.ledger_cleanup_pending == true
        or provenance.ledger_attach_pending == true
        or provenance.ledger_attach_protection_clear_pending == true
        or (terminal and existing.reconciled ~= true
            and (existing.budget_tracked == true
                or provenance.ledger_reconciliation_pending == true
                or provenance.ledger_attach_completed == true))
    if pending then
        local score, score_error = reserve_score(
            KEYS[2], KEYS[3], KEYS[4], existing.operation_id, true)
        if score_error then return redis.error_reply(score_error) end
        redis.call('SADD', KEYS[2], existing.operation_id)
        if score then redis.call('ZADD', KEYS[3], 'NX', score, existing.operation_id) end
    end
    return {0, blob}
end
local score, score_error = reserve_score(
    KEYS[2], KEYS[3], KEYS[4], ARGV[5], ARGV[4] == '1')
if score_error then return redis.error_reply(score_error) end
if ARGV[3] == '1' then
    redis.call('SET', KEYS[1], ARGV[1])
else
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
end
if ARGV[4] == '1' then
    redis.call('SADD', KEYS[2], ARGV[5])
    if score then redis.call('ZADD', KEYS[3], 'NX', score, ARGV[5]) end
end
return {1, ARGV[1]}
"""

    _REMOVE_STALE_PENDING_SCRIPT = """
local legacy_type = redis.call('TYPE', KEYS[1]).ok
local index_type = redis.call('TYPE', KEYS[2]).ok
if legacy_type ~= 'none' and legacy_type ~= 'set' then
    return redis.error_reply('legacy media pending index must be a set')
end
if index_type ~= 'none' and index_type ~= 'zset' then
    return redis.error_reply('v2 media pending index must be a sorted set')
end
for i = 2, #ARGV do
    local remove = false
    local blob = redis.call('GET', ARGV[1] .. ARGV[i])
    if not blob then
        remove = true
    else
        local operation = cjson.decode(blob)
        local payload = operation.last_payload or {}
        local provenance = payload.provenance or {}
        local current = tostring(payload.status or '')
        local terminal = current == 'succeeded' or current == 'failed'
            or current == 'cancelled' or current == 'timeout'
        local pending = provenance.ledger_cleanup_pending == true
            or provenance.ledger_attach_pending == true
            or provenance.ledger_attach_protection_clear_pending == true
            or (terminal and operation.reconciled ~= true
                and (operation.budget_tracked == true
                    or provenance.ledger_reconciliation_pending == true
                    or provenance.ledger_attach_completed == true))
        remove = not pending
    end
    if remove then
        redis.call('SREM', KEYS[1], ARGV[i])
        redis.call('ZREM', KEYS[2], ARGV[i])
    end
end
return 1
"""

    _MIGRATE_LEGACY_PENDING_SCRIPT = """
local legacy_type = redis.call('TYPE', KEYS[1]).ok
local index_type = redis.call('TYPE', KEYS[2]).ok
local sequence_type = redis.call('TYPE', KEYS[3]).ok
local cursor_type = redis.call('TYPE', KEYS[4]).ok
if legacy_type ~= 'none' and legacy_type ~= 'set' then
    return redis.error_reply('legacy media pending index must be a set')
end
if index_type ~= 'none' and index_type ~= 'zset' then
    return redis.error_reply('v2 media pending index must be a sorted set')
end
if sequence_type ~= 'none' and sequence_type ~= 'string' then
    return redis.error_reply('media pending sequence must be a string')
end
if cursor_type ~= 'none' and cursor_type ~= 'string' then
    return redis.error_reply('media pending migration cursor must be a string')
end
local sequence_value = redis.call('GET', KEYS[3])
local sequence_number = tonumber(sequence_value)
if sequence_value and (not sequence_number or sequence_number < 0
   or sequence_number ~= math.floor(sequence_number)
   or sequence_number > 9007199254740991) then
    return redis.error_reply('media pending sequence must be an integer')
end
local scan = redis.call(
    'SSCAN', KEYS[1], redis.call('GET', KEYS[4]) or '0', 'COUNT', ARGV[1])
local next_cursor = scan[1]
local missing = {}
for _, member in ipairs(scan[2]) do
    if not redis.call('ZSCORE', KEYS[2], member) then
        table.insert(missing, member)
    end
end
local actual = #missing
local highest = redis.call('ZREVRANGE', KEYS[2], 0, 0, 'WITHSCORES')
local sequence = tonumber(sequence_value or '0')
if #highest > 0 and sequence < tonumber(highest[2]) then
    sequence = math.floor(tonumber(highest[2]))
end
if sequence + actual > 9007199254740991 then
    return redis.error_reply('media pending sequence exhausted')
end
if actual > 0 then
    redis.call('SET', KEYS[3], string.format('%.0f', sequence + actual))
    for index, member in ipairs(missing) do
        redis.call('ZADD', KEYS[2], 'NX', sequence + index, member)
    end
end
if next_cursor == '0' then
    redis.call('DEL', KEYS[4])
else
    redis.call('SET', KEYS[4], next_cursor)
end
return {actual, next_cursor, #scan[2]}
"""

    _TRANSITION_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
local blob = redis.call('GET', KEYS[1])
if not blob then return {0, false} end
local operation = cjson.decode(blob)
local current = tostring(operation.last_payload.status or '')
if current == 'succeeded' or current == 'failed'
   or current == 'cancelled' or current == 'timeout' then
    return {0, blob}
end
if ARGV[3] ~= '' and current ~= ARGV[3] then
    return {0, blob}
end
operation.last_payload = cjson.decode(ARGV[1])
operation.state_version = tonumber(operation.state_version or 0) + 1
blob = cjson.encode(operation)
local next_status = tostring(operation.last_payload.status or '')
local terminal = next_status == 'succeeded' or next_status == 'failed'
    or next_status == 'cancelled' or next_status == 'timeout'
local provenance = operation.last_payload.provenance or {}
local reconciliation_pending = provenance.ledger_reconciliation_pending == true
local attach_completed = provenance.ledger_attach_completed == true
local cleanup_pending = provenance.ledger_cleanup_pending == true
local attach_pending = provenance.ledger_attach_pending == true
local attach_clear_pending = provenance.ledger_attach_protection_clear_pending == true
local pending = (terminal and (operation.budget_tracked == true or reconciliation_pending
        or attach_completed)
    or cleanup_pending or attach_pending or attach_clear_pending)
   and operation.reconciled ~= true
local score, score_error = reserve_score(
    KEYS[2], KEYS[3], KEYS[4], operation.operation_id, pending)
if score_error then return redis.error_reply(score_error) end
if pending then
    -- A terminal operation is still the durable retry intent until its
    -- ledger row is settled and mark_reconciled reapplies the normal TTL.
    redis.call('SET', KEYS[1], blob)
else
    redis.call('SET', KEYS[1], blob, 'EX', ARGV[2])
end
if pending then
    redis.call('SADD', KEYS[2], operation.operation_id)
    if score then redis.call('ZADD', KEYS[3], 'NX', score, operation.operation_id) end
else
    redis.call('SREM', KEYS[2], operation.operation_id)
    redis.call('ZREM', KEYS[3], operation.operation_id)
end
return {1, blob}
"""

    _REPLACE_TERMINAL_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
local blob = redis.call('GET', KEYS[1])
if not blob then return {0, false} end
local operation = cjson.decode(blob)
local payload = cjson.decode(ARGV[2])
if tostring(operation.last_payload.status or '') ~= ARGV[1]
   or tostring(payload.status or '') ~= ARGV[1] then
    return {0, blob}
end
operation.last_payload = payload
operation.state_version = tonumber(operation.state_version or 0) + 1
blob = cjson.encode(operation)
local provenance = operation.last_payload.provenance or {}
local reconciliation_pending = provenance.ledger_reconciliation_pending == true
local attach_completed = provenance.ledger_attach_completed == true
local cleanup_pending = provenance.ledger_cleanup_pending == true
local attach_pending = provenance.ledger_attach_pending == true
local attach_clear_pending = provenance.ledger_attach_protection_clear_pending == true
local pending = operation.reconciled ~= true
   and (operation.budget_tracked == true or reconciliation_pending
       or attach_completed
       or cleanup_pending or attach_pending or attach_clear_pending)
local score, score_error = reserve_score(
    KEYS[2], KEYS[3], KEYS[4], operation.operation_id, pending)
if score_error then return redis.error_reply(score_error) end
if pending then
    redis.call('SET', KEYS[1], blob)
    redis.call('SADD', KEYS[2], operation.operation_id)
    if score then redis.call('ZADD', KEYS[3], 'NX', score, operation.operation_id) end
else
    redis.call('SET', KEYS[1], blob, 'EX', ARGV[3])
    redis.call('SREM', KEYS[2], operation.operation_id)
    redis.call('ZREM', KEYS[3], operation.operation_id)
end
return {1, blob}
"""

    _MARK_RECONCILED_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
local blob = redis.call('GET', KEYS[1])
if not blob then return 0 end
local operation = cjson.decode(blob)
local _score, score_error = reserve_score(
    KEYS[2], KEYS[3], KEYS[4], operation.operation_id, false)
if score_error then return redis.error_reply(score_error) end
operation.reconciled = true
redis.call('SET', KEYS[1], cjson.encode(operation), 'EX', ARGV[1])
redis.call('SREM', KEYS[2], operation.operation_id)
redis.call('ZREM', KEYS[3], operation.operation_id)
return 1
"""

    _COMPLETE_ATTACH_CLEAR_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
local blob = redis.call('GET', KEYS[1])
if not blob then return {0, false} end
local operation = cjson.decode(blob)
local payload = operation.last_payload or {}
local provenance = payload.provenance or {}
if provenance.ledger_attach_protection_clear_pending ~= true then
    return {0, blob}
end
provenance.ledger_attach_protection_clear_pending = false
provenance.ledger_attach_protection_clear_completed = true
payload.provenance = provenance
operation.last_payload = payload
operation.state_version = tonumber(operation.state_version or 0) + 1
blob = cjson.encode(operation)
local current = tostring(payload.status or '')
local terminal = current == 'succeeded' or current == 'failed'
    or current == 'cancelled' or current == 'timeout'
local reconciliation_pending = provenance.ledger_reconciliation_pending == true
local attach_completed = provenance.ledger_attach_completed == true
local pending = terminal and operation.reconciled ~= true
   and (operation.budget_tracked == true or reconciliation_pending or attach_completed)
local score, score_error = reserve_score(
    KEYS[2], KEYS[3], KEYS[4], operation.operation_id, pending)
if score_error then return redis.error_reply(score_error) end
if pending then
    redis.call('SET', KEYS[1], blob)
    redis.call('SADD', KEYS[2], operation.operation_id)
    if score then redis.call('ZADD', KEYS[3], 'NX', score, operation.operation_id) end
else
    redis.call('SET', KEYS[1], blob, 'EX', ARGV[1])
    redis.call('SREM', KEYS[2], operation.operation_id)
    redis.call('ZREM', KEYS[3], operation.operation_id)
end
return {1, blob}
"""

    _ADOPT_LEDGER_RECONCILIATION_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
local blob = redis.call('GET', KEYS[1])
if not blob then return {0, false} end
local operation = cjson.decode(blob)
local provenance = operation.last_payload.provenance or {}
if operation.reconciled == true
   or tostring(provenance.manual_reconciliation_outcome or '') ~= ARGV[1]
   or provenance.ledger_reconciliation_pending ~= true then
    return {0, blob}
end
local next_payload = cjson.decode(ARGV[2])
local score, score_error = reserve_score(
    KEYS[2], KEYS[3], KEYS[4], operation.operation_id, true)
if score_error then return redis.error_reply(score_error) end
operation.last_payload = next_payload
operation.state_version = tonumber(operation.state_version or 0) + 1
blob = cjson.encode(operation)
-- Keep the repaired winner durable until mark_reconciled reapplies the TTL.
redis.call('SET', KEYS[1], blob)
redis.call('SADD', KEYS[2], operation.operation_id)
if score then redis.call('ZADD', KEYS[3], 'NX', score, operation.operation_id) end
return {1, blob}
"""

    _ADOPT_LEDGER_FALLBACK_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
local blob = redis.call('GET', KEYS[1])
if not blob then return {0, false} end
local operation = cjson.decode(blob)
if operation.reconciled == true
   or tonumber(operation.state_version or 0) ~= tonumber(ARGV[1]) then
    return {0, blob}
end
local next_payload = cjson.decode(ARGV[2])
local score, score_error = reserve_score(
    KEYS[2], KEYS[3], KEYS[4], operation.operation_id, true)
if score_error then return redis.error_reply(score_error) end
operation.last_payload = next_payload
operation.state_version = tonumber(operation.state_version or 0) + 1
blob = cjson.encode(operation)
redis.call('SET', KEYS[1], blob)
redis.call('SADD', KEYS[2], operation.operation_id)
if score then redis.call('ZADD', KEYS[3], 'NX', score, operation.operation_id) end
return {1, blob}
"""

    _DEFER_PENDING_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
local blob = redis.call('GET', KEYS[1])
if not blob then return 0 end
local operation = cjson.decode(blob)
local payload = operation.last_payload or {}
local provenance = payload.provenance or {}
local current = tostring(payload.status or '')
local terminal = current == 'succeeded' or current == 'failed'
    or current == 'cancelled' or current == 'timeout'
local pending = provenance.ledger_cleanup_pending == true
    or provenance.ledger_attach_pending == true
    or provenance.ledger_attach_protection_clear_pending == true
    or (terminal and operation.reconciled ~= true
        and (operation.budget_tracked == true
            or provenance.ledger_reconciliation_pending == true
            or provenance.ledger_attach_completed == true))
local _score, score_error = reserve_score(
    KEYS[2], KEYS[3], KEYS[4], operation.operation_id, false)
if score_error then return redis.error_reply(score_error) end
if not pending then
    redis.call('SREM', KEYS[2], operation.operation_id)
    redis.call('ZREM', KEYS[3], operation.operation_id)
    return 0
end
local sequence_value = redis.call('GET', KEYS[4])
local sequence = tonumber(sequence_value or '0')
local highest = redis.call('ZREVRANGE', KEYS[3], 0, 0, 'WITHSCORES')
if #highest > 0 and sequence < tonumber(highest[2]) then
    sequence = math.floor(tonumber(highest[2]))
end
if sequence >= 9007199254740991 then
    return redis.error_reply('media pending sequence exhausted')
end
local next_score = sequence + 1
redis.call('SET', KEYS[4], string.format('%.0f', next_score))
redis.call('SADD', KEYS[2], operation.operation_id)
redis.call('ZADD', KEYS[3], next_score, operation.operation_id)
return 1
"""

    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        self._ttl = _ttl_seconds()

    async def ensure_available(self) -> None:
        await self._redis.ping()

    async def create(self, operation: dict[str, Any]) -> dict[str, Any]:
        operation = deepcopy(operation)
        operation.setdefault("state_version", 0)
        operation_id = str(operation["operation_id"])
        key = _KEY_PREFIX + operation_id
        blob = json.dumps(operation)
        status = str((operation.get("last_payload") or {}).get("status", ""))
        provenance = dict((operation.get("last_payload") or {}).get("provenance") or {})
        no_expiry = bool(
            status == "submission_unknown"
            or provenance.get("ledger_cleanup_pending")
            or provenance.get("ledger_attach_pending")
            or provenance.get("ledger_attach_protection_clear_pending")
            or _has_pending_ledger_intent(operation)
        )
        created, persisted_blob = await self._redis.eval(
            self._CREATE_SCRIPT,
            4,
            key,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            blob,
            self._ttl,
            "1" if no_expiry else "0",
            "1" if _has_pending_ledger_intent(operation) else "0",
            operation_id,
        )
        persisted = json.loads(persisted_blob)
        if not created and _operation_identity(persisted) != _operation_identity(operation):
            raise MediaOperationCollisionError(
                f"media operation id collision: {operation_id}"
            )
        return persisted

    async def pending_ledger_intent_page(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> PendingLedgerPage:
        limit = media_ledger_recovery_batch_size() if limit is None else limit
        if not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 through 500")
        _migrated, migration_cursor = await self._migrate_legacy_pending(limit)
        minimum = f"({cursor}" if cursor else "-inf"
        probed_with_scores = list(
            await self._redis.zrangebyscore(
                _PENDING_LEDGER_ZSET,
                minimum,
                "+inf",
                start=0,
                num=limit + 1,
                withscores=True,
            )
        )
        selected_with_scores = probed_with_scores[:limit]
        selected = [member for member, _score in selected_with_scores]
        if not selected:
            continuation = str(cursor or 0) if migration_cursor != "0" else None
            return PendingLedgerPage([], continuation)
        blobs = await self._redis.mget(
            [_KEY_PREFIX + str(operation_id) for operation_id in selected]
        )
        pending: list[dict[str, Any]] = []
        stale: list[str] = []
        for operation_id, blob in zip(selected, blobs):
            if not blob:
                stale.append(str(operation_id))
                continue
            operation = json.loads(blob)
            if _has_pending_ledger_intent(operation):
                pending.append(operation)
            else:
                stale.append(str(operation_id))
        if stale:
            await self._redis.eval(
                self._REMOVE_STALE_PENDING_SCRIPT,
                2,
                _PENDING_LEDGER_KEY,
                _PENDING_LEDGER_ZSET,
                _KEY_PREFIX,
                *stale,
            )
        has_more = len(probed_with_scores) > limit or migration_cursor != "0"
        next_cursor = (
            str(int(selected_with_scores[-1][1])) if has_more else None
        )
        return PendingLedgerPage(pending, next_cursor)

    async def _migrate_legacy_pending(self, limit: int) -> tuple[int, str]:
        """Non-destructively scan legacy members and add missing v2 scores.

        Redis treats SSCAN ``COUNT`` as a work hint, not a strict result cap;
        the recovery record page and MGET remain hard-capped by ``limit``.
        """
        migrated, scan_cursor, _scanned = await self._redis.eval(
            self._MIGRATE_LEGACY_PENDING_SCRIPT,
            4,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            _PENDING_LEDGER_MIGRATION_CURSOR,
            limit,
        )
        return int(migrated), str(scan_cursor)

    async def pending_ledger_intents(self) -> list[dict[str, Any]]:
        """Compatibility shim returning only the first bounded page."""
        return (await self.pending_ledger_intent_page()).records

    async def defer_pending_ledger_intent(self, operation_id: str) -> bool:
        return bool(
            await self._redis.eval(
                self._DEFER_PENDING_SCRIPT,
                4,
                _KEY_PREFIX + operation_id,
                _PENDING_LEDGER_KEY,
                _PENDING_LEDGER_ZSET,
                _PENDING_LEDGER_SEQUENCE,
            )
        )

    async def get(self, operation_id: str) -> Optional[dict[str, Any]]:
        blob = await self._redis.get(_KEY_PREFIX + operation_id)
        return json.loads(blob) if blob else None

    async def transition_payload(
        self,
        operation_id: str,
        payload: dict[str, Any],
        *,
        expected_status: Optional[str] = None,
    ) -> tuple[Optional[dict[str, Any]], bool]:
        changed, blob = await self._redis.eval(
            self._TRANSITION_SCRIPT,
            4,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            json.dumps(payload),
            self._ttl,
            expected_status or "",
        )
        return (json.loads(blob) if blob else None), bool(changed)

    async def replace_terminal_payload(
        self,
        operation_id: str,
        expected_status: str,
        payload: dict[str, Any],
    ) -> tuple[Optional[dict[str, Any]], bool]:
        changed, blob = await self._redis.eval(
            self._REPLACE_TERMINAL_SCRIPT,
            4,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            expected_status,
            json.dumps(payload),
            self._ttl,
        )
        return (json.loads(blob) if blob else None), bool(changed)

    async def mark_reconciled(self, operation_id: str) -> bool:
        result = await self._redis.eval(
            self._MARK_RECONCILED_SCRIPT,
            4,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            self._ttl,
        )
        return bool(result)

    async def complete_attach_protection_clear(
        self, operation_id: str
    ) -> tuple[Optional[dict[str, Any]], bool]:
        changed, blob = await self._redis.eval(
            self._COMPLETE_ATTACH_CLEAR_SCRIPT,
            4,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            self._ttl,
        )
        return (json.loads(blob) if blob else None), bool(changed)

    async def adopt_ledger_reconciliation(
        self,
        operation_id: str,
        expected_outcome: str,
        payload: dict[str, Any],
    ) -> tuple[Optional[dict[str, Any]], bool]:
        changed, blob = await self._redis.eval(
            self._ADOPT_LEDGER_RECONCILIATION_SCRIPT,
            4,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            expected_outcome,
            json.dumps(payload),
        )
        return (json.loads(blob) if blob else None), bool(changed)

    async def adopt_ledger_fallback(
        self,
        operation_id: str,
        expected_version: int,
        payload: dict[str, Any],
    ) -> tuple[Optional[dict[str, Any]], bool]:
        changed, blob = await self._redis.eval(
            self._ADOPT_LEDGER_FALLBACK_SCRIPT,
            4,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            expected_version,
            json.dumps(payload),
        )
        return (json.loads(blob) if blob else None), bool(changed)

    async def aclose(self) -> None:
        await self._redis.aclose()


class UnavailableMediaOperationStore:
    """Non-fallback sentinel used when durable state is misconfigured."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def ensure_available(self) -> None:
        raise StateStoreUnavailable(self.reason)

    async def aclose(self) -> None:
        return None

    def __getattr__(self, _name: str):
        async def unavailable(*_args, **_kwargs):
            raise StateStoreUnavailable(self.reason)

        return unavailable


def build_media_operation_store() -> (
    InMemoryMediaOperationStore | RedisMediaOperationStore | UnavailableMediaOperationStore
):
    try:
        if backend_state_store_mode() == "memory":
            return InMemoryMediaOperationStore()
        return RedisMediaOperationStore(redis_url())
    except StateStoreUnavailable as exc:
        return UnavailableMediaOperationStore(str(exc))
