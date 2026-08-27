from __future__ import annotations

import json
import os
import uuid

import pytest

from rag_ingestion.models import (
    IngestionError,
    IngestionRecord,
    RagIngestionRecordResponse,
)
from rag_ingestion.store import (
    RedisIngestionStore,
    _IDX_PREFIX,
    _INDEX_SET,
    _KEY_PREFIX,
)


def test_redis_dispatch_scripts_normalize_legacy_records() -> None:
    prepared_default = (
        "if record.dispatch_state == nil then record.dispatch_state = 'prepared' end"
    )
    assert prepared_default in RedisIngestionStore._CLAIM_DISPATCH_SCRIPT
    assert prepared_default in RedisIngestionStore._DISPATCH_FAILED_SCRIPT
    assert "record.dispatch_owner == ARGV[4]" in RedisIngestionStore._DISPATCHED_SCRIPT
    assert "record.dispatch_owner == owner" in RedisIngestionStore._DISPATCH_FAILED_SCRIPT
    assert "record.dispatch_claimed_at <= ARGV[3]" in RedisIngestionStore._CLAIM_DISPATCH_SCRIPT


_TEST_REDIS_URL = os.getenv("ATLAS_TEST_REDIS_URL")


def _record_payload() -> dict:
    return IngestionRecord(
        id="ing-1",
        consumer="consumer",
        profile="profile",
        revision="rev-1",
        idempotency_key="key-1",
    ).to_dict()


def test_legacy_redis_empty_errors_object_normalizes_to_list() -> None:
    payload = _record_payload()
    payload["errors"] = {}
    payload["phases"] = {}

    record = IngestionRecord.from_dict(payload)
    response = RagIngestionRecordResponse(**record.to_dict())

    assert record.errors == []
    assert record.phases == []
    assert response.errors == []
    assert response.phases == []

    record.add_error(IngestionError(phase="dispatch", message="queue unavailable"))

    assert record.errors == [
        {
            "phase": "dispatch",
            "message": "queue unavailable",
            "file": None,
            "service": None,
            "http_status": None,
            "body": None,
        }
    ]
    assert record.phases == []
    assert isinstance(record.to_dict()["errors"], list)


@pytest.mark.parametrize("field", ["errors", "phases"])
def test_nonempty_list_field_mapping_remains_invalid(field: str) -> None:
    payload = _record_payload()
    payload[field] = {"unexpected": "shape"}

    with pytest.raises(ValueError, match=rf"{field} must be a list"):
        IngestionRecord.from_dict(payload)


@pytest.mark.parametrize("value", [None, {}])
def test_absent_list_shapes_normalize_to_empty_lists(value: object) -> None:
    payload = _record_payload()
    payload["errors"] = value
    payload["phases"] = value

    record = IngestionRecord.from_dict(payload)

    assert record.errors == []
    assert record.phases == []


@pytest.mark.skipif(
    not _TEST_REDIS_URL,
    reason="set ATLAS_TEST_REDIS_URL to exercise Redis Lua persistence",
)
def test_redis_lua_roundtrip_preserves_public_list_contract() -> None:
    store = RedisIngestionStore(_TEST_REDIS_URL)
    record_id = f"regression-{uuid.uuid4()}"
    record = IngestionRecord(
        id=record_id,
        consumer="rag-showcase",
        profile="redis-regression",
        revision="rev-1",
        idempotency_key=f"key-{record_id}",
    )

    try:
        created, was_created = store.create_if_absent(record)
        assert was_created is True
        assert created.errors == []

        # Redis 7.2's Lua cjson turns [] into {} during save. Reads must repair
        # that persistence shape before it reaches the status API contract.
        store.save(record)
        raw = json.loads(store._redis.get(_KEY_PREFIX + record_id))
        assert raw["errors"] == {}
        assert store.get(record_id).errors == []
        assert next(r for r in store.list() if r.id == record_id).errors == []

        failed = store.fail_pending_dispatch(
            record_id,
            {"phase": "dispatch", "message": "queue unavailable"},
            ("2026-07-16T00:00:00Z", None),
        )
        assert failed is not None
        assert len(failed.errors) == 1
        assert isinstance(
            json.loads(store._redis.get(_KEY_PREFIX + record_id))["errors"], list
        )
    finally:
        store._redis.delete(_KEY_PREFIX + record_id)
        store._redis.delete(_IDX_PREFIX + record.idempotency_key)
        store._redis.srem(_INDEX_SET, record_id)
