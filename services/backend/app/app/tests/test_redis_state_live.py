"""Disposable-Redis contracts for bounded shared state (Task 13)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
from redis.exceptions import ResponseError

from media_operation_store import (
    RedisMediaOperationStore,
    _KEY_PREFIX as MEDIA_KEY_PREFIX,
    _PENDING_LEDGER_KEY,
    _PENDING_LEDGER_SEQUENCE,
    _PENDING_LEDGER_ZSET,
)
from rag_ingestion.models import IngestionRecord
from rag_ingestion.store import (
    RedisIngestionStore,
    _INDEX_SEQUENCE,
    _INDEX_SET,
    _INDEX_ZSET,
    _KEY_PREFIX as RAG_KEY_PREFIX,
)


_REDIS_URL = os.getenv("ATLAS_TEST_REDIS_URL")
_INDEX_MIGRATION_CURSOR = "atlas:rag:ingestion-ids:v2:migration-cursor"
_PENDING_LEDGER_MIGRATION_CURSOR = (
    "atlas:media:pending-ledger-intents:v2:migration-cursor"
)
pytestmark = pytest.mark.skipif(
    not _REDIS_URL,
    reason="set ATLAS_TEST_REDIS_URL to a disposable Redis instance",
)


def _rag_record(record_id: str) -> IngestionRecord:
    return IngestionRecord(
        id=record_id,
        consumer="acme",
        profile="default",
        revision="1",
        idempotency_key=f"key-{record_id}",
    )


def _pending_media(operation_id: str) -> dict:
    return {
        "operation_id": operation_id,
        "provider": "fal",
        "modality": "image",
        "model": "fal-ai/flux/dev",
        "owner_scope": "service",
        "budget_tracked": True,
        "reconciled": False,
        "last_payload": {
            "operation_id": operation_id,
            "status": "queued",
            "provider": "fal",
            "modality": "image",
            "model": "fal-ai/flux/dev",
            "provenance": {"ledger_attach_pending": True},
        },
    }


def test_rag_set_only_upgrade_resume_and_mixed_writer_have_no_skips() -> None:
    store = RedisIngestionStore(_REDIS_URL)
    redis = store._redis
    redis.delete(_INDEX_SET, _INDEX_ZSET, _INDEX_SEQUENCE, _INDEX_MIGRATION_CURSOR)
    prefix = f"upgrade-{uuid.uuid4().hex}-"
    legacy_ids = [prefix + suffix for suffix in ("z", "a", "m", "b", "y")]
    for record_id in legacy_ids:
        redis.set(RAG_KEY_PREFIX + record_id, json.dumps(_rag_record(record_id).to_dict()))
        redis.sadd(_INDEX_SET, record_id)
    assert redis.smembers(_INDEX_SET) == set(legacy_ids)

    first = store.list_page(limit=2)
    assert len(first.records) == 2
    assert first.next_cursor is not None  # exactly-limit migration still continues
    assert redis.smembers(_INDEX_SET) == set(legacy_ids)

    late_id = prefix + "aa"  # sorts behind the prior page lexically
    redis.set(RAG_KEY_PREFIX + late_id, json.dumps(_rag_record(late_id).to_dict()))
    redis.sadd(_INDEX_SET, late_id)  # simulate an old replica after paging began
    assert redis.smembers(_INDEX_SET) == set(legacy_ids + [late_id])

    restarted = RedisIngestionStore(_REDIS_URL)
    seen = [record.id for record in first.records]
    cursor = first.next_cursor
    for _ in range(10):
        page = restarted.list_page(cursor=cursor, limit=2)
        assert redis.smembers(_INDEX_SET) == set(legacy_ids + [late_id])
        seen.extend(record.id for record in page.records)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert set(seen) == set(legacy_ids + [late_id])
    assert len(seen) == len(set(seen))

    new_record = _rag_record(prefix + "new-version")
    restarted.create_if_absent(new_record)
    assert redis.sismember(_INDEX_SET, new_record.id)
    assert redis.zscore(_INDEX_ZSET, new_record.id) is not None

    redis.delete(*[RAG_KEY_PREFIX + record_id for record_id in legacy_ids + [late_id, new_record.id]])
    redis.delete(_INDEX_SET, _INDEX_ZSET, _INDEX_SEQUENCE, _INDEX_MIGRATION_CURSOR)


def test_rag_migration_reserves_only_actual_new_members() -> None:
    store = RedisIngestionStore(_REDIS_URL)
    redis = store._redis
    redis.delete(_INDEX_SET, _INDEX_ZSET, _INDEX_SEQUENCE, _INDEX_MIGRATION_CURSOR)
    redis.set(_INDEX_SEQUENCE, 0)

    assert store._migrate_legacy_index(100)[0] == 0
    assert redis.get(_INDEX_SEQUENCE) == "0"

    record_id = f"rag-one-{uuid.uuid4().hex}"
    redis.set(RAG_KEY_PREFIX + record_id, json.dumps(_rag_record(record_id).to_dict()))
    redis.sadd(_INDEX_SET, record_id)
    redis.set(_INDEX_SEQUENCE, 2**53 - 2)

    migrated, _scan_cursor = store._migrate_legacy_index(100)
    assert migrated == 1
    assert redis.get(_INDEX_SEQUENCE) == str(2**53 - 1)
    assert redis.zscore(_INDEX_ZSET, record_id) == 2**53 - 1
    assert redis.sismember(_INDEX_SET, record_id)

    redis.delete(RAG_KEY_PREFIX + record_id)
    redis.delete(_INDEX_SET, _INDEX_ZSET, _INDEX_SEQUENCE, _INDEX_MIGRATION_CURSOR)


def test_rag_stale_cleanup_rechecks_concurrent_recreate() -> None:
    store = RedisIngestionStore(_REDIS_URL)
    redis = store._redis
    record_id = f"recreated-{uuid.uuid4().hex}"
    redis.sadd(_INDEX_SET, record_id)
    redis.zadd(_INDEX_ZSET, {record_id: 1})
    redis.set(RAG_KEY_PREFIX + record_id, json.dumps(_rag_record(record_id).to_dict()))

    redis.eval(
        store._REMOVE_STALE_INDEX_SCRIPT,
        2,
        _INDEX_SET,
        _INDEX_ZSET,
        RAG_KEY_PREFIX,
        record_id,
    )

    assert redis.sismember(_INDEX_SET, record_id)
    assert redis.zscore(_INDEX_ZSET, record_id) is not None
    redis.delete(RAG_KEY_PREFIX + record_id)
    redis.srem(_INDEX_SET, record_id)
    redis.zrem(_INDEX_ZSET, record_id)


def test_rag_sequence_recovers_from_missing_or_behind_counter() -> None:
    store = RedisIngestionStore(_REDIS_URL)
    redis = store._redis
    redis.delete(_INDEX_SET, _INDEX_ZSET, _INDEX_SEQUENCE, _INDEX_MIGRATION_CURSOR)
    prefix = f"rag-sequence-{uuid.uuid4().hex}-"
    existing_ids = [prefix + "existing-a", prefix + "existing-b"]
    for score, record_id in enumerate(existing_ids, start=10):
        redis.set(RAG_KEY_PREFIX + record_id, json.dumps(_rag_record(record_id).to_dict()))
        redis.zadd(_INDEX_ZSET, {record_id: score})

    assert redis.get(_INDEX_SEQUENCE) is None
    created_id = prefix + "new"
    store.create_if_absent(_rag_record(created_id))
    assert redis.zscore(_INDEX_ZSET, created_id) == 12
    assert redis.get(_INDEX_SEQUENCE) == "12"

    migrated_id = prefix + "legacy"
    redis.delete(_INDEX_SET, _INDEX_MIGRATION_CURSOR)
    redis.set(RAG_KEY_PREFIX + migrated_id, json.dumps(_rag_record(migrated_id).to_dict()))
    redis.sadd(_INDEX_SET, migrated_id)
    redis.set(_INDEX_SEQUENCE, 1)
    migrated, _scan_cursor = store._migrate_legacy_index(1)
    assert migrated == 1
    assert redis.zscore(_INDEX_ZSET, migrated_id) == 13
    assert redis.get(_INDEX_SEQUENCE) == "13"

    seen = []
    cursors = []
    cursor = None
    while True:
        page = store.list_page(cursor=cursor, limit=1)
        seen.extend(record.id for record in page.records)
        cursor = page.next_cursor
        cursors.append(cursor)
        if cursor is None:
            break
    assert seen == existing_ids + [created_id, migrated_id]
    assert cursors == ["10", "11", "12", None]

    redis.delete(*[RAG_KEY_PREFIX + record_id for record_id in seen])
    redis.delete(_INDEX_SET, _INDEX_ZSET, _INDEX_SEQUENCE, _INDEX_MIGRATION_CURSOR)


def test_rag_create_rejects_corrupt_or_exhausted_sequence_without_partial_write() -> None:
    store = RedisIngestionStore(_REDIS_URL)
    redis = store._redis
    redis.delete(_INDEX_SET, _INDEX_ZSET, _INDEX_SEQUENCE)
    record = _rag_record(f"rag-atomic-{uuid.uuid4().hex}")

    for invalid_sequence in ("not-an-integer", "1.5", "-1", str(2**53 - 1)):
        redis.set(_INDEX_SEQUENCE, invalid_sequence)
        with pytest.raises(ResponseError):
            store.create_if_absent(record)
        assert redis.get(RAG_KEY_PREFIX + record.id) is None
        assert redis.get(f"atlas:rag:idempotency:{record.idempotency_key}") is None
        assert not redis.sismember(_INDEX_SET, record.id)
        assert redis.zscore(_INDEX_ZSET, record.id) is None
        assert redis.get(_INDEX_SEQUENCE) == invalid_sequence

    redis.delete(_INDEX_SET, _INDEX_ZSET, _INDEX_SEQUENCE, _INDEX_MIGRATION_CURSOR)


def test_media_set_only_upgrade_is_atomic_resumable_and_race_safe() -> None:
    async def scenario() -> None:
        store = RedisMediaOperationStore(_REDIS_URL)
        redis = store._redis
        await redis.delete(
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            _PENDING_LEDGER_MIGRATION_CURSOR,
        )
        prefix = f"media-upgrade-{uuid.uuid4().hex}-"
        legacy_ids = [prefix + suffix for suffix in ("z", "a", "m", "b", "y")]
        for operation_id in legacy_ids:
            await redis.set(
                MEDIA_KEY_PREFIX + operation_id,
                json.dumps(_pending_media(operation_id)),
            )
            await redis.sadd(_PENDING_LEDGER_KEY, operation_id)
        assert await redis.smembers(_PENDING_LEDGER_KEY) == set(legacy_ids)

        first = await store.pending_ledger_intent_page(limit=2)
        assert len(first.records) == 2
        assert first.next_cursor is not None
        assert await redis.smembers(_PENDING_LEDGER_KEY) == set(legacy_ids)
        late_id = prefix + "aa"
        await redis.set(MEDIA_KEY_PREFIX + late_id, json.dumps(_pending_media(late_id)))
        await redis.sadd(_PENDING_LEDGER_KEY, late_id)
        assert await redis.smembers(_PENDING_LEDGER_KEY) == set(legacy_ids + [late_id])

        restarted = RedisMediaOperationStore(_REDIS_URL)
        seen = [record["operation_id"] for record in first.records]
        cursor = first.next_cursor
        for _ in range(10):
            page = await restarted.pending_ledger_intent_page(
                cursor=cursor, limit=2
            )
            assert await redis.smembers(_PENDING_LEDGER_KEY) == set(
                legacy_ids + [late_id]
            )
            seen.extend(record["operation_id"] for record in page.records)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert set(seen) == set(legacy_ids + [late_id])
        assert len(seen) == len(set(seen))

        new_id = prefix + "new-version"
        await restarted.create(_pending_media(new_id))
        assert await redis.sismember(_PENDING_LEDGER_KEY, new_id)
        assert await redis.zscore(_PENDING_LEDGER_ZSET, new_id) is not None

        # Migration validates the sequence before SPOP, so a corrupt counter
        # cannot remove an intent from the only index that still contains it.
        rollback_id = prefix + "rollback"
        await redis.delete(_PENDING_LEDGER_KEY)
        await redis.sadd(_PENDING_LEDGER_KEY, rollback_id)
        await redis.delete(_PENDING_LEDGER_SEQUENCE)
        await redis.rpush(_PENDING_LEDGER_SEQUENCE, "wrong-type")
        with pytest.raises(ResponseError):
            await restarted.pending_ledger_intent_page(limit=1)
        assert await redis.sismember(_PENDING_LEDGER_KEY, rollback_id)
        await redis.delete(_PENDING_LEDGER_SEQUENCE)

        # A candidate observed missing/nonpending is re-read by Lua; a pending
        # concurrent recreation must retain membership in both indexes.
        recreated_id = prefix + "recreated"
        await redis.set(
            MEDIA_KEY_PREFIX + recreated_id,
            json.dumps(_pending_media(recreated_id)),
        )
        await redis.sadd(_PENDING_LEDGER_KEY, recreated_id)
        await redis.zadd(_PENDING_LEDGER_ZSET, {recreated_id: 9999})
        await redis.eval(
            restarted._REMOVE_STALE_PENDING_SCRIPT,
            2,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            MEDIA_KEY_PREFIX,
            recreated_id,
        )
        assert await redis.sismember(_PENDING_LEDGER_KEY, recreated_id)
        assert await redis.zscore(_PENDING_LEDGER_ZSET, recreated_id) is not None

        nonpending_id = prefix + "nonpending"
        nonpending = _pending_media(nonpending_id)
        nonpending["last_payload"].pop("provenance")
        await redis.set(MEDIA_KEY_PREFIX + nonpending_id, json.dumps(nonpending))
        await redis.sadd(_PENDING_LEDGER_KEY, nonpending_id)
        await redis.zadd(_PENDING_LEDGER_ZSET, {nonpending_id: 10000})
        await redis.eval(
            restarted._REMOVE_STALE_PENDING_SCRIPT,
            2,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            MEDIA_KEY_PREFIX,
            nonpending_id,
        )
        assert not await redis.sismember(_PENDING_LEDGER_KEY, nonpending_id)
        assert await redis.zscore(_PENDING_LEDGER_ZSET, nonpending_id) is None

        cleanup_ids = legacy_ids + [
            late_id,
            new_id,
            rollback_id,
            recreated_id,
            nonpending_id,
        ]
        await redis.delete(*[MEDIA_KEY_PREFIX + operation_id for operation_id in cleanup_ids])
        await redis.delete(
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            _PENDING_LEDGER_MIGRATION_CURSOR,
        )
        await store.aclose()
        await restarted.aclose()

    asyncio.run(scenario())


def test_media_migration_reserves_only_actual_new_members() -> None:
    async def scenario() -> None:
        store = RedisMediaOperationStore(_REDIS_URL)
        redis = store._redis
        await redis.delete(
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            _PENDING_LEDGER_MIGRATION_CURSOR,
        )
        await redis.set(_PENDING_LEDGER_SEQUENCE, 0)

        migrated, _scan_cursor = await store._migrate_legacy_pending(100)
        assert migrated == 0
        assert await redis.get(_PENDING_LEDGER_SEQUENCE) == "0"

        operation_id = f"media-one-{uuid.uuid4().hex}"
        await redis.set(
            MEDIA_KEY_PREFIX + operation_id,
            json.dumps(_pending_media(operation_id)),
        )
        await redis.sadd(_PENDING_LEDGER_KEY, operation_id)
        await redis.set(_PENDING_LEDGER_SEQUENCE, 2**53 - 2)

        migrated, _scan_cursor = await store._migrate_legacy_pending(100)
        assert migrated == 1
        assert await redis.get(_PENDING_LEDGER_SEQUENCE) == str(2**53 - 1)
        assert await redis.zscore(_PENDING_LEDGER_ZSET, operation_id) == 2**53 - 1
        assert await redis.sismember(_PENDING_LEDGER_KEY, operation_id)

        await redis.delete(MEDIA_KEY_PREFIX + operation_id)
        await redis.delete(
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            _PENDING_LEDGER_MIGRATION_CURSOR,
        )
        await store.aclose()

    asyncio.run(scenario())


def test_media_sequence_recovers_from_missing_or_behind_counter() -> None:
    async def scenario() -> None:
        store = RedisMediaOperationStore(_REDIS_URL)
        redis = store._redis
        await redis.delete(
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            _PENDING_LEDGER_MIGRATION_CURSOR,
        )
        prefix = f"media-sequence-{uuid.uuid4().hex}-"
        existing_ids = [prefix + "existing-a", prefix + "existing-b"]
        for score, operation_id in enumerate(existing_ids, start=10):
            await redis.set(
                MEDIA_KEY_PREFIX + operation_id,
                json.dumps(_pending_media(operation_id)),
            )
            await redis.zadd(_PENDING_LEDGER_ZSET, {operation_id: score})

        assert await redis.get(_PENDING_LEDGER_SEQUENCE) is None
        created_id = prefix + "new"
        await store.create(_pending_media(created_id))
        assert await redis.zscore(_PENDING_LEDGER_ZSET, created_id) == 12
        assert await redis.get(_PENDING_LEDGER_SEQUENCE) == "12"

        migrated_id = prefix + "legacy"
        await redis.delete(_PENDING_LEDGER_KEY, _PENDING_LEDGER_MIGRATION_CURSOR)
        await redis.set(
            MEDIA_KEY_PREFIX + migrated_id,
            json.dumps(_pending_media(migrated_id)),
        )
        await redis.sadd(_PENDING_LEDGER_KEY, migrated_id)
        await redis.set(_PENDING_LEDGER_SEQUENCE, 1)
        migrated, _scan_cursor = await store._migrate_legacy_pending(1)
        assert migrated == 1
        assert await redis.zscore(_PENDING_LEDGER_ZSET, migrated_id) == 13
        assert await redis.get(_PENDING_LEDGER_SEQUENCE) == "13"

        seen = []
        cursors = []
        cursor = None
        while True:
            page = await store.pending_ledger_intent_page(cursor=cursor, limit=1)
            seen.extend(record["operation_id"] for record in page.records)
            cursor = page.next_cursor
            cursors.append(cursor)
            if cursor is None:
                break
        assert seen == existing_ids + [created_id, migrated_id]
        assert cursors == ["10", "11", "12", None]

        await redis.delete(*[MEDIA_KEY_PREFIX + operation_id for operation_id in seen])
        await redis.delete(
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            _PENDING_LEDGER_MIGRATION_CURSOR,
        )
        await store.aclose()

    asyncio.run(scenario())


def test_media_transition_wrongtype_is_atomic() -> None:
    async def scenario() -> None:
        store = RedisMediaOperationStore(_REDIS_URL)
        redis = store._redis
        await redis.delete(
            _PENDING_LEDGER_KEY, _PENDING_LEDGER_ZSET, _PENDING_LEDGER_SEQUENCE
        )
        operation_id = f"media-atomic-{uuid.uuid4().hex}"
        operation = _pending_media(operation_id)
        operation["last_payload"].pop("provenance")
        original_blob = json.dumps(operation)
        await redis.set(MEDIA_KEY_PREFIX + operation_id, original_blob)
        await redis.set(_PENDING_LEDGER_ZSET, "wrong-type")

        next_payload = dict(operation["last_payload"])
        next_payload["provenance"] = {"ledger_attach_pending": True}
        with pytest.raises(ResponseError):
            await store.transition_payload(operation_id, next_payload)

        assert await redis.get(MEDIA_KEY_PREFIX + operation_id) == original_blob
        assert not await redis.sismember(_PENDING_LEDGER_KEY, operation_id)
        assert await redis.get(_PENDING_LEDGER_ZSET) == "wrong-type"
        assert await redis.get(_PENDING_LEDGER_SEQUENCE) is None

        await redis.delete(
            MEDIA_KEY_PREFIX + operation_id,
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
        )
        await store.aclose()

    asyncio.run(scenario())


def test_media_failed_intent_defers_to_tail_before_later_writer() -> None:
    async def scenario() -> None:
        store = RedisMediaOperationStore(_REDIS_URL)
        redis = store._redis
        await redis.delete(
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            _PENDING_LEDGER_MIGRATION_CURSOR,
        )
        prefix = f"media-defer-{uuid.uuid4().hex}-"
        failed_id = prefix + "failed"
        existing_id = prefix + "existing"
        later_id = prefix + "later"
        await store.create(_pending_media(failed_id))
        await store.create(_pending_media(existing_id))

        first = await store.pending_ledger_intent_page(limit=1)
        assert [item["operation_id"] for item in first.records] == [failed_id]
        assert await store.defer_pending_ledger_intent(failed_id) is True
        await store.create(_pending_media(later_id))

        seen = []
        cursor = first.next_cursor
        while True:
            page = await store.pending_ledger_intent_page(cursor=cursor, limit=1)
            seen.extend(item["operation_id"] for item in page.records)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert seen == [existing_id, failed_id, later_id]

        await redis.delete(
            *[MEDIA_KEY_PREFIX + operation_id for operation_id in (failed_id, existing_id, later_id)]
        )
        await redis.delete(
            _PENDING_LEDGER_KEY,
            _PENDING_LEDGER_ZSET,
            _PENDING_LEDGER_SEQUENCE,
            _PENDING_LEDGER_MIGRATION_CURSOR,
        )
        await store.aclose()

    asyncio.run(scenario())
