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
from typing import Dict, List, Optional

from .models import IngestionRecord

_TTL_SECONDS = int(os.getenv("RAG_INGESTION_TTL_SECONDS", str(7 * 24 * 3600)))
_KEY_PREFIX = "atlas:rag:ingestions:"
_IDX_PREFIX = "atlas:rag:idempotency:"
_INDEX_SET = "atlas:rag:ingestion-ids"


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
        updated_at: str,
    ) -> Optional[IngestionRecord]:
        raise NotImplementedError


class InMemoryIngestionStore(IngestionStore):
    def __init__(self) -> None:
        self._records: Dict[str, str] = {}
        self._index: Dict[str, str] = {}
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
        updated_at: str,
    ) -> Optional[IngestionRecord]:
        with self._lock:
            blob = self._records.get(ingestion_id)
            if blob is None:
                return None
            record = IngestionRecord.from_dict(json.loads(blob))
            if record.status == "pending":
                record.status = "failed"
                record.updated_at = updated_at
                record.errors.append(dict(error))
                self._save_locked(record)
            return record


class RedisIngestionStore(IngestionStore):
    def __init__(self, url: str) -> None:
        import redis  # lazy — keeps main.py import closure redis-free

        self._redis = redis.Redis.from_url(url, decode_responses=True)

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
if record.status == 'pending' then
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
        updated_at: str,
    ) -> Optional[IngestionRecord]:
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
        )
        return IngestionRecord.from_dict(json.loads(result)) if result else None


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
