"""Durable job store for RAG ingestion records (#413).

The store is the shared state that makes a job observable across the API/worker
boundary: the Celery worker updates it as the job advances, the status endpoint
reads it. Two implementations:

- ``InMemoryIngestionStore`` — process-local dict; used only when the explicit
  single-process ``BACKEND_STATE_STORE_MODE=memory`` mode is selected.
- ``RedisIngestionStore`` — JSON blobs under ``atlas:rag:ingestions:<id>`` with a
  key→id idempotency index; the durable production default. ``redis`` is
  imported lazily so importing this module does not construct a connection.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from shared_state import StateStoreUnavailable, backend_state_store_mode, redis_url

from .models import IngestionRecord

_DEFAULT_TTL_SECONDS = 7 * 24 * 3600
_MIN_TTL_SECONDS = 60
_MAX_TTL_SECONDS = 365 * 24 * 3600
_KEY_PREFIX = "atlas:rag:ingestions:"
_IDX_PREFIX = "atlas:rag:idempotency:"
_INDEX_SET = "atlas:rag:ingestion-ids"
_INDEX_ZSET = "atlas:rag:ingestion-ids:v2"
_INDEX_SEQUENCE = "atlas:rag:ingestion-ids:v2:sequence"
_INDEX_MIGRATION_CURSOR = "atlas:rag:ingestion-ids:v2:migration-cursor"
_LEASE_PREFIX = "atlas:rag:execution:"
_DEFAULT_LIST_LIMIT = 100
_MAX_LIST_LIMIT = 200
_MAX_CURSOR = 2**53 - 1
_CANONICAL_CURSOR_TOKEN = re.compile(r"(?:0|[1-9][0-9]*)")


def _ttl_seconds() -> int:
    """Resolve a Redis retention TTL without making module import fallible."""
    try:
        value = int(os.getenv("RAG_INGESTION_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS)))
    except ValueError:
        return _DEFAULT_TTL_SECONDS
    if _MIN_TTL_SECONDS <= value <= _MAX_TTL_SECONDS:
        return value
    return _DEFAULT_TTL_SECONDS


@dataclass(frozen=True)
class ExecutionClaim:
    owner: str
    lease_seconds: int
    recovery_owner: Optional[str] = None


@dataclass(frozen=True)
class IngestionPage:
    records: List[IngestionRecord]
    next_cursor: Optional[str]


def _validated_page_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LIST_LIMIT:
        raise ValueError(f"limit must be an integer from 1 through {_MAX_LIST_LIMIT}")
    return limit


def validate_page_cursor(cursor: Optional[str | int]) -> int:
    """Return a safe cursor value after enforcing its canonical wire form."""
    if cursor is None:
        return 0
    if isinstance(cursor, bool):
        raise ValueError("cursor must be a non-negative integer")
    if isinstance(cursor, int):
        value = cursor
    elif isinstance(cursor, str) and _CANONICAL_CURSOR_TOKEN.fullmatch(cursor):
        value = int(cursor)
    else:
        raise ValueError("cursor must be a canonical non-negative decimal integer")
    if not 0 <= value <= _MAX_CURSOR:
        raise ValueError(f"cursor must be an integer from 0 through {_MAX_CURSOR}")
    return value


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

    def list_page(
        self, cursor: Optional[str | int] = None, limit: int = _DEFAULT_LIST_LIMIT
    ) -> IngestionPage:  # pragma: no cover - interface
        raise NotImplementedError

    def list(self) -> List[IngestionRecord]:
        """Compatibility shim: the first bounded page, never all records."""
        return self.list_page().records

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
        self._record_sequence: Dict[str, int] = {}
        self._next_sequence = 0
        self._lock = threading.Lock()

    def _save_locked(self, record: IngestionRecord) -> None:
        if record.id not in self._record_sequence:
            self._next_sequence += 1
            self._record_sequence[record.id] = self._next_sequence
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

    def list_page(
        self, cursor: Optional[str | int] = None, limit: int = _DEFAULT_LIST_LIMIT
    ) -> IngestionPage:
        limit = _validated_page_limit(limit)
        cursor_value = validate_page_cursor(cursor)
        with self._lock:
            ordered = sorted(
                (sequence, record_id)
                for record_id, sequence in self._record_sequence.items()
                if sequence > cursor_value and record_id in self._records
            )
            selected_pairs = ordered[:limit]
            selected = [record_id for _sequence, record_id in selected_pairs]
            blobs = [self._records[record_id] for record_id in selected]
            next_cursor = (
                str(selected_pairs[-1][0]) if len(ordered) > limit else None
            )
        return IngestionPage(
            [IngestionRecord.from_dict(json.loads(blob)) for blob in blobs],
            next_cursor,
        )

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
        self._ttl_seconds = _ttl_seconds()

    _SCORE_RESERVATION_SCRIPT = """
local function reserve_score(legacy_key, index_key, sequence_key, member)
    local legacy_type = redis.call('TYPE', legacy_key).ok
    local index_type = redis.call('TYPE', index_key).ok
    local sequence_type = redis.call('TYPE', sequence_key).ok
    if legacy_type ~= 'none' and legacy_type ~= 'set' then
        return nil, 'legacy ingestion index must be a set'
    end
    if index_type ~= 'none' and index_type ~= 'zset' then
        return nil, 'v2 ingestion index must be a sorted set'
    end
    if sequence_type ~= 'none' and sequence_type ~= 'string' then
        return nil, 'ingestion index sequence must be a string'
    end
    local sequence_value = redis.call('GET', sequence_key)
    local sequence_number = tonumber(sequence_value)
    if sequence_value and (not sequence_number or sequence_number < 0
       or sequence_number ~= math.floor(sequence_number)
       or sequence_number > 9007199254740991) then
        return nil, 'ingestion index sequence must be an integer'
    end
    if redis.call('ZSCORE', index_key, member) then return false, nil end
    local sequence = tonumber(sequence_value or '0')
    local highest = redis.call('ZREVRANGE', index_key, 0, 0, 'WITHSCORES')
    if #highest > 0 and sequence < tonumber(highest[2]) then
        sequence = math.floor(tonumber(highest[2]))
    end
    if sequence >= 9007199254740991 then
        return nil, 'ingestion index sequence exhausted'
    end
    local score = sequence + 1
    redis.call('SET', sequence_key, string.format('%.0f', score))
    return score, nil
end
"""

    _CREATE_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
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
end
local score, score_error = reserve_score(KEYS[3], KEYS[4], KEYS[5], ARGV[4])
if score_error then return redis.error_reply(score_error) end
if current_id then redis.call('DEL', KEYS[1]) end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('SADD', KEYS[3], ARGV[4])
if score then redis.call('ZADD', KEYS[4], 'NX', score, ARGV[4]) end
redis.call('SET', KEYS[1], ARGV[4], 'EX', ARGV[3])
return {1, ARGV[2]}
"""

    _SAVE_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
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
local score, score_error = reserve_score(KEYS[3], KEYS[4], KEYS[5], incoming.id)
if score_error then return redis.error_reply(score_error) end
redis.call('SET', KEYS[1], blob, 'EX', ARGV[2])
redis.call('SADD', KEYS[3], incoming.id)
if score then redis.call('ZADD', KEYS[4], 'NX', score, incoming.id) end
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

    _SAVE_CLAIMED_SCRIPT = _SCORE_RESERVATION_SCRIPT + """
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
local score, score_error = reserve_score(KEYS[3], KEYS[5], KEYS[6], incoming.id)
if score_error then return redis.error_reply(score_error) end
redis.call('SET', KEYS[1], blob, 'EX', ARGV[2])
redis.call('SADD', KEYS[3], incoming.id)
if score then redis.call('ZADD', KEYS[5], 'NX', score, incoming.id) end
if incoming.status == 'pending' or incoming.status == 'running'
   or incoming.status == 'completed' then
    redis.call('SET', KEYS[2], incoming.id, 'EX', ARGV[2])
elseif redis.call('GET', KEYS[2]) == incoming.id then
    redis.call('DEL', KEYS[2])
end
return 1
"""

    _MIGRATE_LEGACY_INDEX_SCRIPT = """
local legacy_type = redis.call('TYPE', KEYS[1]).ok
local index_type = redis.call('TYPE', KEYS[2]).ok
local sequence_type = redis.call('TYPE', KEYS[3]).ok
local cursor_type = redis.call('TYPE', KEYS[4]).ok
if legacy_type ~= 'none' and legacy_type ~= 'set' then
    return redis.error_reply('legacy ingestion index must be a set')
end
if index_type ~= 'none' and index_type ~= 'zset' then
    return redis.error_reply('v2 ingestion index must be a sorted set')
end
if sequence_type ~= 'none' and sequence_type ~= 'string' then
    return redis.error_reply('ingestion index sequence must be a string')
end
if cursor_type ~= 'none' and cursor_type ~= 'string' then
    return redis.error_reply('ingestion migration cursor must be a string')
end
local sequence_value = redis.call('GET', KEYS[3])
local sequence_number = tonumber(sequence_value)
if sequence_value and (not sequence_number or sequence_number < 0
   or sequence_number ~= math.floor(sequence_number)
   or sequence_number > 9007199254740991) then
    return redis.error_reply('ingestion index sequence must be an integer')
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
    return redis.error_reply('ingestion index sequence exhausted')
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

    _REMOVE_STALE_INDEX_SCRIPT = """
local legacy_type = redis.call('TYPE', KEYS[1]).ok
local index_type = redis.call('TYPE', KEYS[2]).ok
if legacy_type ~= 'none' and legacy_type ~= 'set' then
    return redis.error_reply('legacy ingestion index must be a set')
end
if index_type ~= 'none' and index_type ~= 'zset' then
    return redis.error_reply('v2 ingestion index must be a sorted set')
end
for i = 2, #ARGV do
    if redis.call('EXISTS', ARGV[1] .. ARGV[i]) == 0 then
        redis.call('SREM', KEYS[1], ARGV[i])
        redis.call('ZREM', KEYS[2], ARGV[i])
    end
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
            5,
            _KEY_PREFIX + record.id,
            _IDX_PREFIX + record.idempotency_key,
            _INDEX_SET,
            _INDEX_ZSET,
            _INDEX_SEQUENCE,
            blob,
            self._ttl_seconds,
        )

    def create_if_absent(self, record: IngestionRecord) -> tuple[IngestionRecord, bool]:
        blob = json.dumps(record.to_dict())
        created, persisted = self._redis.eval(
            self._CREATE_SCRIPT,
            5,
            _IDX_PREFIX + record.idempotency_key,
            _KEY_PREFIX + record.id,
            _INDEX_SET,
            _INDEX_ZSET,
            _INDEX_SEQUENCE,
            _KEY_PREFIX,
            blob,
            self._ttl_seconds,
            record.id,
        )
        return IngestionRecord.from_dict(json.loads(persisted)), bool(created)

    def get(self, ingestion_id: str) -> Optional[IngestionRecord]:
        blob = self._redis.get(_KEY_PREFIX + ingestion_id)
        return IngestionRecord.from_dict(json.loads(blob)) if blob else None

    def _migrate_legacy_index(self, limit: int) -> tuple[int, str]:
        """Non-destructively scan legacy members and add missing v2 scores.

        Redis treats SSCAN ``COUNT`` as a work hint, not a strict result cap;
        the API record page and MGET remain hard-capped by ``limit``.
        """
        migrated, scan_cursor, _scanned = self._redis.eval(
            self._MIGRATE_LEGACY_INDEX_SCRIPT,
            4,
            _INDEX_SET,
            _INDEX_ZSET,
            _INDEX_SEQUENCE,
            _INDEX_MIGRATION_CURSOR,
            limit,
        )
        return int(migrated), str(scan_cursor)

    def list_page(
        self, cursor: Optional[str | int] = None, limit: int = _DEFAULT_LIST_LIMIT
    ) -> IngestionPage:
        limit = _validated_page_limit(limit)
        cursor_value = validate_page_cursor(cursor)
        _migrated, migration_cursor = self._migrate_legacy_index(limit)
        minimum = f"({cursor_value}" if cursor_value else "-inf"
        probed_with_scores = list(
            self._redis.zrangebyscore(
                _INDEX_ZSET,
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
            # A full migration batch can consist entirely of legacy-set
            # duplicates that already sit at or before this cursor. Keep the
            # same exclusive cursor so another bounded call can migrate the
            # remaining set-only members instead of ending traversal early.
            continuation = str(cursor_value) if migration_cursor != "0" else None
            return IngestionPage([], continuation)
        blobs = self._redis.mget([_KEY_PREFIX + ingestion_id for ingestion_id in selected])
        records: List[IngestionRecord] = []
        stale: List[str] = []
        for ingestion_id, blob in zip(selected, blobs):
            if blob:
                records.append(IngestionRecord.from_dict(json.loads(blob)))
            else:
                stale.append(ingestion_id)
        if stale:
            self._redis.eval(
                self._REMOVE_STALE_INDEX_SCRIPT,
                2,
                _INDEX_SET,
                _INDEX_ZSET,
                _KEY_PREFIX,
                *stale,
            )
        has_more = len(probed_with_scores) > limit or migration_cursor != "0"
        next_cursor = (
            str(int(selected_with_scores[-1][1])) if has_more else None
        )
        return IngestionPage(records, next_cursor)

    def find_by_idempotency_key(self, key: str) -> Optional[IngestionRecord]:
        ingestion_id = self._redis.get(_IDX_PREFIX + key)
        return self.get(ingestion_id) if ingestion_id else None

    def request_cancel(self, ingestion_id: str, updated_at: str = "") -> bool:
        result = self._redis.eval(
            self._CANCEL_SCRIPT,
            1,
            _KEY_PREFIX + ingestion_id,
            updated_at,
            self._ttl_seconds,
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
            self._ttl_seconds,
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
                self._ttl_seconds,
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
            self._ttl_seconds,
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
                6,
                _KEY_PREFIX + record.id,
                _IDX_PREFIX + record.idempotency_key,
                _INDEX_SET,
                _LEASE_PREFIX + record.id,
                _INDEX_ZSET,
                _INDEX_SEQUENCE,
                json.dumps(record.to_dict()),
                self._ttl_seconds,
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
    """Build the explicitly selected shared-state implementation."""
    if backend_state_store_mode() == "memory":
        return InMemoryIngestionStore()
    return RedisIngestionStore(redis_url())
