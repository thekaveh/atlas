"""Shared, monotonic state store for hosted media operations."""

from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from typing import Any, Optional


TERMINAL_MEDIA_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "timeout"}
)
_KEY_PREFIX = "atlas:media:operations:"
_PENDING_LEDGER_KEY = "atlas:media:pending-ledger-intents"


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
        self._lock = asyncio.Lock()

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
                return deepcopy(persisted)
            persisted = candidate
            self._records[operation_id] = persisted
            return deepcopy(persisted)

    async def pending_ledger_intents(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                deepcopy(operation)
                for operation in self._records.values()
                if _has_pending_ledger_intent(operation)
            ]

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
            return deepcopy(operation), True

    async def mark_reconciled(self, operation_id: str) -> bool:
        async with self._lock:
            operation = self._records.get(operation_id)
            if operation is None:
                return False
            operation["reconciled"] = True
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
            return deepcopy(operation), True

    async def aclose(self) -> None:
        return None


class RedisMediaOperationStore:
    """Redis-backed operation state shared by Backend replicas and restarts."""

    _CREATE_SCRIPT = """
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
    if pending then redis.call('SADD', KEYS[2], existing.operation_id) end
    return {0, blob}
end
if ARGV[3] == '1' then
    redis.call('SET', KEYS[1], ARGV[1])
else
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
end
if ARGV[4] == '1' then redis.call('SADD', KEYS[2], ARGV[5]) end
return {1, ARGV[1]}
"""

    _REMOVE_STALE_PENDING_SCRIPT = """
for i = 1, #ARGV, 2 do
    if redis.call('EXISTS', ARGV[i]) == 0 then
        redis.call('SREM', KEYS[1], ARGV[i + 1])
    end
end
return 1
"""

    _TRANSITION_SCRIPT = """
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
if (terminal and (operation.budget_tracked == true or reconciliation_pending
        or attach_completed)
    or cleanup_pending or attach_pending or attach_clear_pending)
   and operation.reconciled ~= true then
    -- A terminal operation is still the durable retry intent until its
    -- ledger row is settled and mark_reconciled reapplies the normal TTL.
    redis.call('SET', KEYS[1], blob)
else
    redis.call('SET', KEYS[1], blob, 'EX', ARGV[2])
end
if cleanup_pending or attach_pending or attach_clear_pending
   or (terminal and operation.reconciled ~= true
       and (operation.budget_tracked == true or reconciliation_pending
           or attach_completed)) then
    redis.call('SADD', KEYS[2], operation.operation_id)
else
    redis.call('SREM', KEYS[2], operation.operation_id)
end
return {1, blob}
"""

    _REPLACE_TERMINAL_SCRIPT = """
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
if operation.reconciled ~= true
   and (operation.budget_tracked == true or reconciliation_pending
       or attach_completed
       or cleanup_pending or attach_pending or attach_clear_pending) then
    redis.call('SET', KEYS[1], blob)
    redis.call('SADD', KEYS[2], operation.operation_id)
else
    redis.call('SET', KEYS[1], blob, 'EX', ARGV[3])
    redis.call('SREM', KEYS[2], operation.operation_id)
end
return {1, blob}
"""

    _MARK_RECONCILED_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return 0 end
local operation = cjson.decode(blob)
operation.reconciled = true
redis.call('SET', KEYS[1], cjson.encode(operation), 'EX', ARGV[1])
redis.call('SREM', KEYS[2], operation.operation_id)
return 1
"""

    _COMPLETE_ATTACH_CLEAR_SCRIPT = """
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
if terminal and operation.reconciled ~= true
   and (operation.budget_tracked == true or reconciliation_pending
       or attach_completed) then
    redis.call('SET', KEYS[1], blob)
    redis.call('SADD', KEYS[2], operation.operation_id)
else
    redis.call('SET', KEYS[1], blob, 'EX', ARGV[1])
    redis.call('SREM', KEYS[2], operation.operation_id)
end
return {1, blob}
"""

    _ADOPT_LEDGER_RECONCILIATION_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return {0, false} end
local operation = cjson.decode(blob)
local provenance = operation.last_payload.provenance or {}
if operation.reconciled == true
   or tostring(provenance.manual_reconciliation_outcome or '') ~= ARGV[1]
   or provenance.ledger_reconciliation_pending ~= true then
    return {0, blob}
end
operation.last_payload = cjson.decode(ARGV[2])
operation.state_version = tonumber(operation.state_version or 0) + 1
blob = cjson.encode(operation)
-- Keep the repaired winner durable until mark_reconciled reapplies the TTL.
redis.call('SET', KEYS[1], blob)
return {1, blob}
"""

    _ADOPT_LEDGER_FALLBACK_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return {0, false} end
local operation = cjson.decode(blob)
if operation.reconciled == true
   or tonumber(operation.state_version or 0) ~= tonumber(ARGV[1]) then
    return {0, blob}
end
operation.last_payload = cjson.decode(ARGV[2])
operation.state_version = tonumber(operation.state_version or 0) + 1
blob = cjson.encode(operation)
redis.call('SET', KEYS[1], blob)
redis.call('SADD', KEYS[2], operation.operation_id)
return {1, blob}
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
            2,
            key,
            _PENDING_LEDGER_KEY,
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

    async def pending_ledger_intents(self) -> list[dict[str, Any]]:
        operation_ids = list(await self._redis.smembers(_PENDING_LEDGER_KEY))
        if not operation_ids:
            return []
        keys = [_KEY_PREFIX + str(operation_id) for operation_id in operation_ids]
        blobs = await self._redis.mget(keys)
        pending: list[dict[str, Any]] = []
        missing: list[str] = []
        for operation_id, blob in zip(operation_ids, blobs):
            if not blob:
                missing.extend(
                    [_KEY_PREFIX + str(operation_id), str(operation_id)]
                )
                continue
            operation = json.loads(blob)
            if _has_pending_ledger_intent(operation):
                pending.append(operation)
        if missing:
            await self._redis.eval(
                self._REMOVE_STALE_PENDING_SCRIPT,
                1,
                _PENDING_LEDGER_KEY,
                *missing,
            )
        return pending

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
            2,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
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
            2,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            expected_status,
            json.dumps(payload),
            self._ttl,
        )
        return (json.loads(blob) if blob else None), bool(changed)

    async def mark_reconciled(self, operation_id: str) -> bool:
        result = await self._redis.eval(
            self._MARK_RECONCILED_SCRIPT,
            2,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            self._ttl,
        )
        return bool(result)

    async def complete_attach_protection_clear(
        self, operation_id: str
    ) -> tuple[Optional[dict[str, Any]], bool]:
        changed, blob = await self._redis.eval(
            self._COMPLETE_ATTACH_CLEAR_SCRIPT,
            2,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
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
            1,
            _KEY_PREFIX + operation_id,
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
            2,
            _KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            expected_version,
            json.dumps(payload),
        )
        return (json.loads(blob) if blob else None), bool(changed)

    async def aclose(self) -> None:
        await self._redis.aclose()


def build_media_operation_store() -> (
    InMemoryMediaOperationStore | RedisMediaOperationStore
):
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if redis_url:
        return RedisMediaOperationStore(redis_url)
    return InMemoryMediaOperationStore()
