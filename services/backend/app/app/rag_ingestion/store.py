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

    def get(self, ingestion_id: str) -> Optional[IngestionRecord]:  # pragma: no cover
        raise NotImplementedError

    def list(self) -> List[IngestionRecord]:  # pragma: no cover - interface
        raise NotImplementedError

    def find_by_idempotency_key(self, key: str) -> Optional[IngestionRecord]:  # pragma: no cover
        raise NotImplementedError

    def request_cancel(self, ingestion_id: str) -> bool:
        record = self.get(ingestion_id)
        if record is None:
            return False
        record.cancel_requested = True
        self.save(record)
        return True


class InMemoryIngestionStore(IngestionStore):
    def __init__(self) -> None:
        self._records: Dict[str, str] = {}
        self._index: Dict[str, str] = {}
        self._lock = threading.Lock()

    def save(self, record: IngestionRecord) -> None:
        blob = json.dumps(record.to_dict())
        with self._lock:
            self._records[record.id] = blob
            # Only a dedup-eligible record owns the idempotency slot, so a failed
            # job never blocks a retry under the same key.
            if record.is_dedup_candidate:
                self._index[record.idempotency_key] = record.id
            elif self._index.get(record.idempotency_key) == record.id:
                del self._index[record.idempotency_key]

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


class RedisIngestionStore(IngestionStore):
    def __init__(self, url: str) -> None:
        import redis  # lazy — keeps main.py import closure redis-free

        self._redis = redis.Redis.from_url(url, decode_responses=True)

    def save(self, record: IngestionRecord) -> None:
        blob = json.dumps(record.to_dict())
        pipe = self._redis.pipeline()
        pipe.set(_KEY_PREFIX + record.id, blob, ex=_TTL_SECONDS)
        pipe.sadd(_INDEX_SET, record.id)
        if record.is_dedup_candidate:
            pipe.set(_IDX_PREFIX + record.idempotency_key, record.id, ex=_TTL_SECONDS)
        pipe.execute()

    def get(self, ingestion_id: str) -> Optional[IngestionRecord]:
        blob = self._redis.get(_KEY_PREFIX + ingestion_id)
        return IngestionRecord.from_dict(json.loads(blob)) if blob else None

    def list(self) -> List[IngestionRecord]:
        ids = self._redis.smembers(_INDEX_SET) or set()
        out: List[IngestionRecord] = []
        for ingestion_id in ids:
            record = self.get(ingestion_id)
            if record is not None:
                out.append(record)
        return out

    def find_by_idempotency_key(self, key: str) -> Optional[IngestionRecord]:
        ingestion_id = self._redis.get(_IDX_PREFIX + key)
        return self.get(ingestion_id) if ingestion_id else None


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
