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

    async def create(self, operation: dict[str, Any]) -> dict[str, Any]:
        operation_id = str(operation["operation_id"])
        async with self._lock:
            persisted = self._records.setdefault(operation_id, deepcopy(operation))
            return deepcopy(persisted)

    async def get(self, operation_id: str) -> Optional[dict[str, Any]]:
        async with self._lock:
            operation = self._records.get(operation_id)
            return deepcopy(operation) if operation is not None else None

    async def transition_payload(
        self, operation_id: str, payload: dict[str, Any]
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
            operation["last_payload"] = deepcopy(payload)
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
            return deepcopy(operation), True

    async def mark_reconciled(self, operation_id: str) -> bool:
        async with self._lock:
            operation = self._records.get(operation_id)
            if operation is None:
                return False
            operation["reconciled"] = True
            return True

    async def aclose(self) -> None:
        return None


class RedisMediaOperationStore:
    """Redis-backed operation state shared by Backend replicas and restarts."""

    _TRANSITION_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return {0, false} end
local operation = cjson.decode(blob)
local current = tostring(operation.last_payload.status or '')
if current == 'succeeded' or current == 'failed'
   or current == 'cancelled' or current == 'timeout' then
    return {0, blob}
end
operation.last_payload = cjson.decode(ARGV[1])
blob = cjson.encode(operation)
redis.call('SET', KEYS[1], blob, 'EX', ARGV[2])
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
blob = cjson.encode(operation)
redis.call('SET', KEYS[1], blob, 'EX', ARGV[3])
return {1, blob}
"""

    _MARK_RECONCILED_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return 0 end
local operation = cjson.decode(blob)
operation.reconciled = true
redis.call('SET', KEYS[1], cjson.encode(operation), 'EX', ARGV[1])
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

    async def create(self, operation: dict[str, Any]) -> dict[str, Any]:
        operation_id = str(operation["operation_id"])
        key = _KEY_PREFIX + operation_id
        blob = json.dumps(operation)
        for _ in range(2):
            created = await self._redis.set(key, blob, ex=self._ttl, nx=True)
            if created:
                return deepcopy(operation)
            existing = await self._redis.get(key)
            if existing:
                return json.loads(existing)
        raise RuntimeError("media operation state expired during creation")

    async def get(self, operation_id: str) -> Optional[dict[str, Any]]:
        blob = await self._redis.get(_KEY_PREFIX + operation_id)
        return json.loads(blob) if blob else None

    async def transition_payload(
        self, operation_id: str, payload: dict[str, Any]
    ) -> tuple[Optional[dict[str, Any]], bool]:
        changed, blob = await self._redis.eval(
            self._TRANSITION_SCRIPT,
            1,
            _KEY_PREFIX + operation_id,
            json.dumps(payload),
            self._ttl,
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
            1,
            _KEY_PREFIX + operation_id,
            expected_status,
            json.dumps(payload),
            self._ttl,
        )
        return (json.loads(blob) if blob else None), bool(changed)

    async def mark_reconciled(self, operation_id: str) -> bool:
        result = await self._redis.eval(
            self._MARK_RECONCILED_SCRIPT,
            1,
            _KEY_PREFIX + operation_id,
            self._ttl,
        )
        return bool(result)

    async def aclose(self) -> None:
        await self._redis.aclose()


def build_media_operation_store() -> (
    InMemoryMediaOperationStore | RedisMediaOperationStore
):
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if redis_url:
        return RedisMediaOperationStore(redis_url)
    return InMemoryMediaOperationStore()
