"""Durable job store for RAG ingestion records (#413).

The store is the shared state that makes a job observable across the API/worker
boundary: the Celery worker updates it as the job advances, the status endpoint
reads it. Two implementations:

- ``InMemoryIngestionStore`` — process-local dict; used by tests and as a
  no-broker fallback.
- ``RedisIngestionStore`` — JSON blobs under ``atlas:rag:ingestions:<id>`` with a
  key→id idempotency index; used when ``REDIS_URL`` is available. ``redis`` is
  imported lazily so ``main.py``'s import closure never requires it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from .models import IngestionRecord

_TTL_SECONDS = int(os.getenv("RAG_INGESTION_TTL_SECONDS", str(7 * 24 * 3600)))
_KEY_PREFIX = "atlas:rag:ingestions:"
_IDX_PREFIX = "atlas:rag:idempotency:"
_INDEX_SET = "atlas:rag:ingestion-ids"
_LEASE_PREFIX = "atlas:rag:execution:"


@dataclass(frozen=True)
class ExecutionClaim:
    owner: str
    lease_seconds: int
    recovery_owner: Optional[str] = None


class IngestionStore:
    """Abstract store interface."""

    def save(self, record: IngestionRecord) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def create_if_absent(
        self, record: IngestionRecord
    ) -> tuple[IngestionRecord, bool]:  # pragma: no cover - interface
        raise NotImplementedError

    def get(self, ingestion_id: str) -> Optional[IngestionRecord]:  # pragma: no cover
        raise NotImplementedError

    def list(self) -> List[IngestionRecord]:  # pragma: no cover - interface
        raise NotImplementedError

    def find_by_idempotency_key(self, key: str) -> Optional[IngestionRecord]:  # pragma: no cover
        raise NotImplementedError

    def request_cancel(self, ingestion_id: str, updated_at: str) -> bool:
        raise NotImplementedError

    def fail_pending_dispatch(
        self,
        ingestion_id: str,
        error: Dict[str, object],
        update: tuple[str, Optional[str]],
    ) -> Optional[IngestionRecord]:
        raise NotImplementedError

    def claim_dispatch(
        self, ingestion_id: str, claim: tuple[str, str, str]
    ) -> bool:
        raise NotImplementedError

    def mark_dispatched(
        self, ingestion_id: str, dispatch: tuple[Optional[str], str], updated_at: str
    ) -> Optional[IngestionRecord]:
        raise NotImplementedError

    def claim_execution(self, ingestion_id: str, claim: ExecutionClaim) -> bool:
        raise NotImplementedError

    def renew_execution(
        self, ingestion_id: str, owner: str, lease_seconds: int
    ) -> bool:
        raise NotImplementedError

    def save_claimed(self, record: IngestionRecord, owner: str) -> bool:
        raise NotImplementedError

    def release_execution(self, ingestion_id: str, owner: str) -> bool:
        raise NotImplementedError


class InMemoryIngestionStore(IngestionStore):
    def __init__(self) -> None:
        self._records: Dict[str, str] = {}
        self._index: Dict[str, str] = {}
        self._leases: Dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _save_locked(self, record: IngestionRecord) -> None:
        self._records[record.id] = json.dumps(record.to_dict())
        if record.is_dedup_candidate:
            self._index[record.idempotency_key] = record.id
        elif self._index.get(record.idempotency_key) == record.id:
            del self._index[record.idempotency_key]

    def save(self, record: IngestionRecord) -> None:
        with self._lock:
            current_blob = self._records.get(record.id)
            if current_blob:
                current = IngestionRecord.from_dict(json.loads(current_blob))
                if current.is_terminal:
                    return
                if current.cancel_requested:
                    record.cancel_requested = True
            self._save_locked(record)

    def create_if_absent(self, record: IngestionRecord) -> tuple[IngestionRecord, bool]:
        with self._lock:
            current_id = self._index.get(record.idempotency_key)
            current_blob = self._records.get(current_id) if current_id else None
            if current_blob:
                current = IngestionRecord.from_dict(json.loads(current_blob))
                if current.is_dedup_candidate:
                    return current, False
            if current_id:
                self._index.pop(record.idempotency_key, None)
            self._save_locked(record)
            return IngestionRecord.from_dict(json.loads(self._records[record.id])), True

    def get(self, ingestion_id: str) -> Optional[IngestionRecord]:
        with self._lock:
            blob = self._records.get(ingestion_id)
        return IngestionRecord.from_dict(json.loads(blob)) if blob else None

    def list(self) -> List[IngestionRecord]:
        with self._lock:
            blobs = list(self._records.values())
        return [IngestionRecord.from_dict(json.loads(b)) for b in blobs]

    def find_by_idempotency_key(self, key: str) -> Optional[IngestionRecord]:
        with self._lock:
            ingestion_id = self._index.get(key)
        return self.get(ingestion_id) if ingestion_id else None

    def request_cancel(self, ingestion_id: str, updated_at: str = "") -> bool:
        with self._lock:
            blob = self._records.get(ingestion_id)
            if blob is None:
                return False
            record = IngestionRecord.from_dict(json.loads(blob))
            if not record.is_terminal:
                record.cancel_requested = True
                if updated_at:
                    record.updated_at = updated_at
                self._save_locked(record)
            return True

    def fail_pending_dispatch(
        self,
        ingestion_id: str,
        error: Dict[str, object],
        update: tuple[str, Optional[str]],
    ) -> Optional[IngestionRecord]:
        updated_at, owner = update
        with self._lock:
            blob = self._records.get(ingestion_id)
            if blob is None:
                return None
            record = IngestionRecord.from_dict(json.loads(blob))
            claim_matches = (
                record.dispatch_state == "prepared" and owner is None
            ) or (
                record.dispatch_state == "dispatching"
                and record.dispatch_owner == owner
            )
            if record.status == "pending" and claim_matches:
                record.status = "failed"
                record.updated_at = updated_at
                record.errors.append(dict(error))
                self._save_locked(record)
            return record

    def claim_dispatch(
        self, ingestion_id: str, claim: tuple[str, str, str]
    ) -> bool:
        owner, updated_at, stale_before = claim
        with self._lock:
            blob = self._records.get(ingestion_id)
            if blob is None:
                return False
            record = IngestionRecord.from_dict(json.loads(blob))
            reclaimable = (
                record.dispatch_state == "dispatching"
                and (
                    record.dispatch_claimed_at is None
                    or record.dispatch_claimed_at <= stale_before
                )
            )
            if record.status != "pending" or not (
                record.dispatch_state == "prepared" or reclaimable
            ):
                return False
            record.dispatch_state = "dispatching"
            record.dispatch_owner = owner
            record.dispatch_claimed_at = updated_at
            record.updated_at = updated_at
            self._save_locked(record)
            return True

    def mark_dispatched(
        self, ingestion_id: str, dispatch: tuple[Optional[str], str], updated_at: str
    ) -> Optional[IngestionRecord]:
        job_id, owner = dispatch
        with self._lock:
            blob = self._records.get(ingestion_id)
            if blob is None:
                return None
            record = IngestionRecord.from_dict(json.loads(blob))
            if (
                record.status not in {"failed", "cancelled"}
                and record.dispatch_state == "dispatching"
                and record.dispatch_owner == owner
            ):
                record.dispatch_state = "accepted"
                record.dispatch_job_id = job_id
                record.dispatch_owner = None
                record.dispatch_claimed_at = None
                record.updated_at = updated_at
                self._save_locked(record)
            return record

    def claim_execution(self, ingestion_id: str, claim: ExecutionClaim) -> bool:
        with self._lock:
            blob = self._records.get(ingestion_id)
            if blob is None:
                return False
            record = IngestionRecord.from_dict(json.loads(blob))
            if record.is_terminal:
                return False
            now = time.monotonic()
            current = self._leases.get(ingestion_id)
            if current is not None and current[1] > now:
                current_owner = current[0]
                if (
                    claim.recovery_owner != current_owner
                    or claim.owner == current_owner
                ):
                    return False
            self._leases[ingestion_id] = (
                claim.owner,
                now + claim.lease_seconds,
            )
            return True

    def renew_execution(
        self, ingestion_id: str, owner: str, lease_seconds: int
    ) -> bool:
        with self._lock:
            current = self._leases.get(ingestion_id)
            now = time.monotonic()
            if current is None or current[0] != owner or current[1] <= now:
                return False
            self._leases[ingestion_id] = (owner, now + lease_seconds)
            return True

    def save_claimed(self, record: IngestionRecord, owner: str) -> bool:
        with self._lock:
            current_lease = self._leases.get(record.id)
            now = time.monotonic()
            if (
                current_lease is None
                or current_lease[0] != owner
                or current_lease[1] <= now
            ):
                return False
            current_blob = self._records.get(record.id)
            if current_blob:
                current = IngestionRecord.from_dict(json.loads(current_blob))
                if current.is_terminal:
                    return False
                if current.cancel_requested:
                    record.cancel_requested = True
            self._save_locked(record)
            return True

    def release_execution(self, ingestion_id: str, owner: str) -> bool:
        with self._lock:
            current = self._leases.get(ingestion_id)
            if current is None or current[0] != owner:
                return False
            del self._leases[ingestion_id]
            return True


class RedisIngestionStore(IngestionStore):
    def __init__(self, url: str) -> None:
        import redis  # lazy — keeps main.py import closure redis-free

        self._redis = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )

    _CREATE_SCRIPT = """
local current_id = redis.call('GET', KEYS[1])
if current_id then
    local current_blob = redis.call('GET', ARGV[1] .. current_id)
    if current_blob then
        local current = cjson.decode(current_blob)
        if current.status == 'pending' or current.status == 'running'
           or current.status == 'completed' then
            return {0, current_blob}
        end
    end
    redis.call('DEL', KEYS[1])
end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('SADD', KEYS[3], ARGV[4])
redis.call('SET', KEYS[1], ARGV[4], 'EX', ARGV[3])
return {1, ARGV[2]}
"""

    _SAVE_SCRIPT = """
local incoming = cjson.decode(ARGV[1])
local current_blob = redis.call('GET', KEYS[1])
if current_blob then
    local current = cjson.decode(current_blob)
    if current.status == 'completed' or current.status == 'failed'
       or current.status == 'cancelled' then
        return current_blob
    end
    if current.cancel_requested == true then
        incoming.cancel_requested = true
    end
end
local blob = cjson.encode(incoming)
redis.call('SET', KEYS[1], blob, 'EX', ARGV[2])
redis.call('SADD', KEYS[3], incoming.id)
if incoming.status == 'pending' or incoming.status == 'running'
   or incoming.status == 'completed' then
    redis.call('SET', KEYS[2], incoming.id, 'EX', ARGV[2])
elseif redis.call('GET', KEYS[2]) == incoming.id then
    redis.call('DEL', KEYS[2])
end
return blob
"""

    _CANCEL_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return false end
local record = cjson.decode(blob)
if record.status ~= 'completed' and record.status ~= 'failed'
   and record.status ~= 'cancelled' then
    record.cancel_requested = true
    record.updated_at = ARGV[1]
    blob = cjson.encode(record)
    redis.call('SET', KEYS[1], blob, 'EX', ARGV[2])
end
return blob
"""

    _DISPATCH_FAILED_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return false end
local record = cjson.decode(blob)
if record.dispatch_state == nil then record.dispatch_state = 'prepared' end
local owner = ARGV[4]
local claim_matches = (record.dispatch_state == 'prepared' and owner == '')
    or (record.dispatch_state == 'dispatching' and record.dispatch_owner == owner)
if record.status == 'pending' and claim_matches then
    record.status = 'failed'
    record.updated_at = ARGV[2]
    table.insert(record.errors, cjson.decode(ARGV[1]))
    blob = cjson.encode(record)
    redis.call('SET', KEYS[1], blob, 'EX', ARGV[3])
    if redis.call('GET', KEYS[2]) == record.id then
        redis.call('DEL', KEYS[2])
    end
end
return blob
"""

    _CLAIM_DISPATCH_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return 0 end
local record = cjson.decode(blob)
if record.dispatch_state == nil then record.dispatch_state = 'prepared' end
local reclaimable = record.dispatch_state == 'dispatching'
    and (record.dispatch_claimed_at == nil or record.dispatch_claimed_at <= ARGV[3])
if record.status ~= 'pending'
   or (record.dispatch_state ~= 'prepared' and not reclaimable) then
    return 0
end
record.dispatch_state = 'dispatching'
record.dispatch_owner = ARGV[1]
record.updated_at = ARGV[2]
record.dispatch_claimed_at = ARGV[2]
redis.call('SET', KEYS[1], cjson.encode(record), 'EX', ARGV[4])
return 1
"""

    _DISPATCHED_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return false end
local record = cjson.decode(blob)
if record.status ~= 'failed' and record.status ~= 'cancelled'
   and record.dispatch_state == 'dispatching'
   and record.dispatch_owner == ARGV[4] then
    record.dispatch_state = 'accepted'
    if ARGV[1] ~= '' then
        record.dispatch_job_id = ARGV[1]
    else
        record.dispatch_job_id = cjson.null
    end
    record.dispatch_owner = cjson.null
    record.dispatch_claimed_at = cjson.null
    record.updated_at = ARGV[2]
    blob = cjson.encode(record)
    redis.call('SET', KEYS[1], blob, 'EX', ARGV[3])
end
return blob
"""

    _CLAIM_EXECUTION_SCRIPT = """
local blob = redis.call('GET', KEYS[1])
if not blob then return 0 end
local record = cjson.decode(blob)
if record.status == 'completed' or record.status == 'failed'
   or record.status == 'cancelled' then
    return 0
end
local current = redis.call('GET', KEYS[2])
if not current then
    redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
    return 1
end
if ARGV[3] ~= '' and current == ARGV[3] and ARGV[1] ~= ARGV[3] then
    redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
    return 1
end
return 0
"""

    _RENEW_EXECUTION_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""

    _SAVE_CLAIMED_SCRIPT = """
if redis.call('GET', KEYS[4]) ~= ARGV[3] then return 0 end
local incoming = cjson.decode(ARGV[1])
local current_blob = redis.call('GET', KEYS[1])
if not current_blob then return 0 end
local current = cjson.decode(current_blob)
if current.status == 'completed' or current.status == 'failed'
   or current.status == 'cancelled' then
    return 0
end
if current.cancel_requested == true then
    incoming.cancel_requested = true
end
local blob = cjson.encode(incoming)
redis.call('SET', KEYS[1], blob, 'EX', ARGV[2])
redis.call('SADD', KEYS[3], incoming.id)
if incoming.status == 'pending' or incoming.status == 'running'
   or incoming.status == 'completed' then
    redis.call('SET', KEYS[2], incoming.id, 'EX', ARGV[2])
elseif redis.call('GET', KEYS[2]) == incoming.id then
    redis.call('DEL', KEYS[2])
end
return 1
"""

    _RELEASE_EXECUTION_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""

    def save(self, record: IngestionRecord) -> None:
        blob = json.dumps(record.to_dict())
        self._redis.eval(
            self._SAVE_SCRIPT,
            3,
            _KEY_PREFIX + record.id,
            _IDX_PREFIX + record.idempotency_key,
            _INDEX_SET,
            blob,
            _TTL_SECONDS,
        )

    def create_if_absent(self, record: IngestionRecord) -> tuple[IngestionRecord, bool]:
        blob = json.dumps(record.to_dict())
        created, persisted = self._redis.eval(
            self._CREATE_SCRIPT,
            3,
            _IDX_PREFIX + record.idempotency_key,
            _KEY_PREFIX + record.id,
            _INDEX_SET,
            _KEY_PREFIX,
            blob,
            _TTL_SECONDS,
            record.id,
        )
        return IngestionRecord.from_dict(json.loads(persisted)), bool(created)

    def get(self, ingestion_id: str) -> Optional[IngestionRecord]:
        blob = self._redis.get(_KEY_PREFIX + ingestion_id)
        return IngestionRecord.from_dict(json.loads(blob)) if blob else None

    def list(self) -> List[IngestionRecord]:
        ids = self._redis.smembers(_INDEX_SET) or set()
        out: List[IngestionRecord] = []
        stale: List[str] = []
        for ingestion_id in ids:
            record = self.get(ingestion_id)
            if record is not None:
                out.append(record)
            else:
                stale.append(ingestion_id)
        if stale:
            self._redis.srem(_INDEX_SET, *stale)
        return out

    def find_by_idempotency_key(self, key: str) -> Optional[IngestionRecord]:
        ingestion_id = self._redis.get(_IDX_PREFIX + key)
        return self.get(ingestion_id) if ingestion_id else None

    def request_cancel(self, ingestion_id: str, updated_at: str = "") -> bool:
        result = self._redis.eval(
            self._CANCEL_SCRIPT,
            1,
            _KEY_PREFIX + ingestion_id,
            updated_at,
            _TTL_SECONDS,
        )
        return bool(result)

    def fail_pending_dispatch(
        self,
        ingestion_id: str,
        error: Dict[str, object],
        update: tuple[str, Optional[str]],
    ) -> Optional[IngestionRecord]:
        updated_at, owner = update
        record = self.get(ingestion_id)
        if record is None:
            return None
        result = self._redis.eval(
            self._DISPATCH_FAILED_SCRIPT,
            2,
            _KEY_PREFIX + ingestion_id,
            _IDX_PREFIX + record.idempotency_key,
            json.dumps(error),
            updated_at,
            _TTL_SECONDS,
            owner or "",
        )
        return IngestionRecord.from_dict(json.loads(result)) if result else None

    def claim_dispatch(
        self, ingestion_id: str, claim: tuple[str, str, str]
    ) -> bool:
        owner, updated_at, stale_before = claim
        return bool(
            self._redis.eval(
                self._CLAIM_DISPATCH_SCRIPT,
                1,
                _KEY_PREFIX + ingestion_id,
                owner,
                updated_at,
                stale_before,
                _TTL_SECONDS,
            )
        )

    def mark_dispatched(
        self, ingestion_id: str, dispatch: tuple[Optional[str], str], updated_at: str
    ) -> Optional[IngestionRecord]:
        job_id, owner = dispatch
        result = self._redis.eval(
            self._DISPATCHED_SCRIPT,
            1,
            _KEY_PREFIX + ingestion_id,
            job_id or "",
            updated_at,
            _TTL_SECONDS,
            owner,
        )
        return IngestionRecord.from_dict(json.loads(result)) if result else None

    def claim_execution(self, ingestion_id: str, claim: ExecutionClaim) -> bool:
        return bool(
            self._redis.eval(
                self._CLAIM_EXECUTION_SCRIPT,
                2,
                _KEY_PREFIX + ingestion_id,
                _LEASE_PREFIX + ingestion_id,
                claim.owner,
                claim.lease_seconds,
                claim.recovery_owner or "",
            )
        )

    def renew_execution(
        self, ingestion_id: str, owner: str, lease_seconds: int
    ) -> bool:
        return bool(
            self._redis.eval(
                self._RENEW_EXECUTION_SCRIPT,
                1,
                _LEASE_PREFIX + ingestion_id,
                owner,
                lease_seconds,
            )
        )

    def save_claimed(self, record: IngestionRecord, owner: str) -> bool:
        return bool(
            self._redis.eval(
                self._SAVE_CLAIMED_SCRIPT,
                4,
                _KEY_PREFIX + record.id,
                _IDX_PREFIX + record.idempotency_key,
                _INDEX_SET,
                _LEASE_PREFIX + record.id,
                json.dumps(record.to_dict()),
                _TTL_SECONDS,
                owner,
            )
        )

    def release_execution(self, ingestion_id: str, owner: str) -> bool:
        return bool(
            self._redis.eval(
                self._RELEASE_EXECUTION_SCRIPT,
                1,
                _LEASE_PREFIX + ingestion_id,
                owner,
            )
        )


def default_store() -> IngestionStore:
    """Build the store the running backend uses: Redis when a URL is configured,
    else an in-memory store (single-process fallback)."""
    url = os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL")
    if url:
        try:
            return RedisIngestionStore(url)
        except Exception:  # noqa: BLE001 - redis missing/unreachable → degrade
            return InMemoryIngestionStore()
    return InMemoryIngestionStore()
