from __future__ import annotations

import asyncio
import json

import pytest

from media_operation_store import (
    InMemoryMediaOperationStore,
    MediaOperationCollisionError,
    RedisMediaOperationStore,
    build_media_operation_store,
)


def _operation() -> dict:
    return {
        "operation_id": "media-op-1",
        "provider": "fal",
        "modality": "image",
        "model": "fal-ai/flux/dev",
        "created_at_epoch": 1_700_000_000.0,
        "timeout_seconds": 120,
        "owner_scope": "service",
        "budget_tracked": True,
        "reconciled": False,
        "last_payload": {
            "operation_id": "media-op-1",
            "status": "queued",
            "provider": "fal",
            "modality": "image",
            "model": "fal-ai/flux/dev",
        },
    }


def test_first_terminal_media_transition_wins_and_remains_stable() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        await store.create(_operation())
        succeeded = {
            **_operation()["last_payload"],
            "status": "succeeded",
            "artifact_url": "https://example.test/output.png",
        }
        cancelled = {**_operation()["last_payload"], "status": "cancelled"}

        results = await asyncio.gather(
            store.transition_payload("media-op-1", succeeded),
            store.transition_payload("media-op-1", cancelled),
        )
        final = await store.get("media-op-1")
        assert final is not None
        assert final["last_payload"]["status"] in {"succeeded", "cancelled"}
        assert sum(changed for _, changed in results) == 1

        opposite = cancelled if final["last_payload"]["status"] == "succeeded" else succeeded
        stable, changed = await store.transition_payload("media-op-1", opposite)
        assert changed is False
        assert stable["last_payload"] == final["last_payload"]

    asyncio.run(scenario())


def test_expected_status_rejects_stale_nonterminal_poll() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        await store.create(_operation())
        cancelling = {
            **_operation()["last_payload"],
            "status": "cancellation_requested",
        }
        _, changed = await store.transition_payload(
            "media-op-1", cancelling, expected_status="queued"
        )
        assert changed is True

        stale_poll = {**_operation()["last_payload"], "status": "running"}
        persisted, changed = await store.transition_payload(
            "media-op-1", stale_poll, expected_status="queued"
        )

        assert changed is False
        assert persisted["last_payload"]["status"] == "cancellation_requested"

    asyncio.run(scenario())


def test_reconciliation_marker_is_shared_store_state() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        await store.create(_operation())

        assert await store.mark_reconciled("media-op-1") is True
        persisted = await store.get("media-op-1")
        assert persisted is not None
        assert persisted["reconciled"] is True
        assert await store.mark_reconciled("missing") is False

    asyncio.run(scenario())


def test_in_memory_media_store_is_always_available() -> None:
    store = InMemoryMediaOperationStore()
    assert asyncio.run(store.ensure_available()) is None


@pytest.mark.parametrize("mode", ["", "disk", "MEMORY"])
def test_invalid_state_store_mode_is_unavailable_without_import_crash(
    monkeypatch, mode
) -> None:
    monkeypatch.setenv("BACKEND_STATE_STORE_MODE", mode)
    store = build_media_operation_store()
    assert type(store).__name__ == "UnavailableMediaOperationStore"
    with pytest.raises(RuntimeError, match="BACKEND_STATE_STORE_MODE"):
        asyncio.run(store.ensure_available())


def test_redis_mode_without_url_is_unavailable_not_memory(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_STATE_STORE_MODE", "redis")
    monkeypatch.delenv("REDIS_URL", raising=False)
    store = build_media_operation_store()
    assert type(store).__name__ == "UnavailableMediaOperationStore"
    assert not isinstance(store, InMemoryMediaOperationStore)


def test_in_memory_create_rejects_cross_owner_operation_id_collision() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        await store.create(_operation())
        collision = _operation()
        collision["owner_scope"] = "another-tenant"
        with pytest.raises(MediaOperationCollisionError):
            await store.create(collision)
        retry = await store.create(_operation())
        assert retry["state_version"] == 0

    asyncio.run(scenario())


def test_create_rejects_same_owner_reuse_with_different_submission_id() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        first = _operation()
        first["submission_id"] = "reservation-1"
        await store.create(first)
        second = _operation()
        second["submission_id"] = "reservation-2"
        with pytest.raises(MediaOperationCollisionError):
            await store.create(second)

    asyncio.run(scenario())


def test_in_memory_pending_ledger_intents_are_indexed_by_state() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        operation = _operation()
        operation["last_payload"]["provenance"] = {"ledger_attach_pending": True}
        await store.create(operation)
        assert [item["operation_id"] for item in await store.pending_ledger_intents()] == [
            "media-op-1"
        ]

    asyncio.run(scenario())


def test_terminal_unreconciled_budget_operation_is_pending_work() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        await store.create(_operation())
        terminal = {**_operation()["last_payload"], "status": "succeeded"}
        await store.transition_payload("media-op-1", terminal)
        assert len(await store.pending_ledger_intents()) == 1
        await store.mark_reconciled("media-op-1")
        assert await store.pending_ledger_intents() == []

    asyncio.run(scenario())


def test_recovered_attach_terminal_is_pending_even_when_top_level_untracked() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        operation = _operation()
        operation["budget_tracked"] = False
        operation["last_payload"]["provenance"] = {
            "ledger_attach_completed": True
        }
        await store.create(operation)
        terminal = {**operation["last_payload"], "status": "succeeded"}
        await store.transition_payload("media-op-1", terminal)
        assert len(await store.pending_ledger_intents()) == 1

    asyncio.run(scenario())


def test_terminal_payload_can_only_be_enriched_without_changing_status() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        await store.create(_operation())
        cancelled = {**_operation()["last_payload"], "status": "cancelled"}
        terminal, changed = await store.transition_payload("media-op-1", cancelled)
        assert changed is True

        enriched_payload = {
            **terminal["last_payload"],
            "provenance": {"provider_cancellation_requested": True},
        }
        enriched, patched = await store.replace_terminal_payload(
            "media-op-1", "cancelled", enriched_payload
        )
        assert patched is True
        assert enriched["last_payload"]["status"] == "cancelled"
        assert (
            enriched["last_payload"]["provenance"][
                "provider_cancellation_requested"
            ]
            is True
        )

        _, patched_wrong_status = await store.replace_terminal_payload(
            "media-op-1",
            "succeeded",
            {**enriched_payload, "status": "succeeded"},
        )
        assert patched_wrong_status is False

    asyncio.run(scenario())


def test_ledger_fallback_can_replace_unreconciled_terminal_winner() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        await store.create(_operation())
        succeeded = {**_operation()["last_payload"], "status": "succeeded"}
        await store.transition_payload("media-op-1", succeeded)
        failed = {
            **succeeded,
            "status": "failed",
            "provenance": {"manual_reconciliation_outcome": "release"},
        }
        persisted, changed = await store.adopt_ledger_fallback(
            "media-op-1", 1, failed
        )
        assert changed is True
        assert persisted["last_payload"]["status"] == "failed"

    asyncio.run(scenario())


def test_ledger_fallback_rejects_stale_observed_version() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        await store.create(_operation())
        succeeded = {**_operation()["last_payload"], "status": "succeeded"}
        terminal, _ = await store.transition_payload("media-op-1", succeeded)
        observed_version = terminal["state_version"]
        enriched = {**succeeded, "artifact_url": "https://cdn.example/new.png"}
        await store.replace_terminal_payload("media-op-1", "succeeded", enriched)
        failed = {**succeeded, "status": "failed"}
        persisted, changed = await store.adopt_ledger_fallback(
            "media-op-1", observed_version, failed
        )
        assert changed is False
        assert persisted["last_payload"]["artifact_url"].endswith("new.png")

    asyncio.run(scenario())


def test_redis_media_store_configures_bounded_socket_deadlines(monkeypatch) -> None:
    import redis.asyncio as redis

    captured = {}
    sentinel = object()

    def fake_from_url(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return sentinel

    monkeypatch.setattr(redis.Redis, "from_url", fake_from_url)

    store = RedisMediaOperationStore("redis://redis:6379/0")

    assert store._redis is sentinel
    assert captured["socket_connect_timeout"] == 3
    assert captured["socket_timeout"] == 3


def test_redis_media_store_readiness_pings_redis(monkeypatch) -> None:
    import redis.asyncio as redis

    class FakeRedis:
        def __init__(self):
            self.calls = 0

        async def ping(self):
            self.calls += 1
            return True

    fake = FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: fake)
    store = RedisMediaOperationStore("redis://redis:6379/0")

    asyncio.run(store.ensure_available())

    assert fake.calls == 1


def test_redis_submission_unknown_record_has_no_expiry(monkeypatch) -> None:
    import redis.asyncio as redis

    captured = {}

    class FakeRedis:
        async def eval(self, script, key_count, *args):
            captured.update(
                {"script": script, "key_count": key_count, "args": args}
            )
            return [1, args[4]]

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: FakeRedis())
    store = RedisMediaOperationStore("redis://redis:6379/0")
    operation = _operation()
    operation["last_payload"]["status"] = "submission_unknown"

    asyncio.run(store.create(operation))

    assert captured["key_count"] == 4
    assert captured["args"][6] == "1"
    assert "redis.call('SET', KEYS[1], ARGV[1])" in captured["script"]
    assert "redis.call('SADD', KEYS[2]" in captured["script"]


def test_redis_terminal_budget_record_stays_durable_until_reconciled() -> None:
    script = RedisMediaOperationStore._TRANSITION_SCRIPT
    marker_script = RedisMediaOperationStore._MARK_RECONCILED_SCRIPT

    assert "operation.budget_tracked == true or reconciliation_pending" in script
    assert "provenance.ledger_reconciliation_pending == true" in script
    assert "operation.reconciled ~= true" in script
    assert "redis.call('SET', KEYS[1], blob)" in script
    assert "redis.call('SET', KEYS[1], cjson.encode(operation), 'EX'" in marker_script
    replace_script = RedisMediaOperationStore._REPLACE_TERMINAL_SCRIPT
    assert "ledger_attach_protection_clear_pending" in replace_script
    assert "cleanup_pending or attach_pending or attach_clear_pending" in replace_script
    assert "attach_completed" in replace_script
    assert "attach_completed" in RedisMediaOperationStore._TRANSITION_SCRIPT
    assert "attach_completed" in RedisMediaOperationStore._COMPLETE_ATTACH_CLEAR_SCRIPT
    for lua in (
        RedisMediaOperationStore._TRANSITION_SCRIPT,
        RedisMediaOperationStore._REPLACE_TERMINAL_SCRIPT,
        RedisMediaOperationStore._ADOPT_LEDGER_FALLBACK_SCRIPT,
    ):
        assert "state_version" in lua


def test_redis_ledger_winner_adoption_is_guarded_and_durable() -> None:
    script = RedisMediaOperationStore._ADOPT_LEDGER_RECONCILIATION_SCRIPT

    assert "provenance.manual_reconciliation_outcome" in script
    assert "provenance.ledger_reconciliation_pending ~= true" in script
    assert "operation.reconciled == true" in script
    assert "redis.call('SET', KEYS[1], blob)" in script


def test_redis_stale_pending_cleanup_rechecks_key_atomically() -> None:
    script = RedisMediaOperationStore._REMOVE_STALE_PENDING_SCRIPT
    assert "redis.call('GET', ARGV[1] .. ARGV[i])" in script
    assert "remove = not pending" in script
    assert "redis.call('SREM', KEYS[1], ARGV[i])" in script
    assert "redis.call('ZREM', KEYS[2], ARGV[i])" in script


def test_pending_ledger_pages_are_bounded_without_smembers_or_unbounded_mget(
    monkeypatch,
) -> None:
    import redis.asyncio as redis

    class FakeRedis:
        def __init__(self):
            self.mget_sizes = []
            self.removed = []

        async def eval(self, script, _key_count, *args):
            if "SSCAN" in script:
                return [0, "0", 0]
            self.removed.extend(args[3:])
            return 1

        async def zrangebyscore(
            self, _key, _minimum, _maximum, *, start, num, withscores
        ):
            assert start == 0
            assert num == 101
            assert withscores is True
            return [(f"op-{index:04d}", index + 1.0) for index in range(101)]

        async def mget(self, keys):
            self.mget_sizes.append(len(keys))
            pending = _operation()
            pending["last_payload"]["provenance"] = {"ledger_attach_pending": True}
            return [None if key.endswith("0003") else json.dumps(pending) for key in keys]

        async def smembers(self, *_args, **_kwargs):
            raise AssertionError("SMEMBERS must not be used in production recovery")

    fake = FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: fake)
    store = RedisMediaOperationStore("redis://redis:6379/0")

    page = asyncio.run(store.pending_ledger_intent_page(limit=100))

    assert len(page.records) == 99
    assert page.next_cursor == "100"
    assert fake.mget_sizes == [100]
    assert fake.removed == ["op-0003"]


def test_redis_pending_page_keeps_cursor_when_full_migration_yields_no_new_score(
    monkeypatch,
) -> None:
    """Duplicate legacy members must not terminate a still-bounded migration."""
    import redis.asyncio as redis

    class FakeRedis:
        async def eval(self, script, *_args):
            assert "SSCAN" in script
            return [0, "9", 2]

        async def zrangebyscore(self, *_args, **_kwargs):
            return []

        async def mget(self, _keys):
            raise AssertionError("an empty score page must not issue MGET")

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_args, **_kwargs: FakeRedis())
    store = RedisMediaOperationStore("redis://redis:6379/0")

    page = asyncio.run(store.pending_ledger_intent_page(cursor="7", limit=2))

    assert page.records == []
    assert page.next_cursor == "7"


def test_in_memory_pending_ledger_pages_do_not_lose_intents() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        for index in range(205):
            operation = _operation()
            operation["operation_id"] = f"op-{index:04d}"
            operation["last_payload"]["operation_id"] = operation["operation_id"]
            operation["last_payload"]["provenance"] = {
                "ledger_attach_pending": True
            }
            await store.create(operation)

        seen = set()
        cursor = None
        while True:
            page = await store.pending_ledger_intent_page(cursor=cursor, limit=50)
            assert len(page.records) <= 50
            seen.update(item["operation_id"] for item in page.records)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert len(seen) == 205

    asyncio.run(scenario())


def test_in_memory_failed_intent_defers_behind_current_work_before_later_arrivals() -> None:
    async def scenario():
        store = InMemoryMediaOperationStore()
        for operation_id in ("failed", "existing"):
            operation = _operation()
            operation["operation_id"] = operation_id
            operation["last_payload"]["operation_id"] = operation_id
            operation["last_payload"]["provenance"] = {
                "ledger_attach_pending": True
            }
            await store.create(operation)

        first = await store.pending_ledger_intent_page(limit=1)
        assert [item["operation_id"] for item in first.records] == ["failed"]
        assert await store.defer_pending_ledger_intent("failed") is True

        later = _operation()
        later["operation_id"] = "later"
        later["last_payload"]["operation_id"] = "later"
        later["last_payload"]["provenance"] = {"ledger_attach_pending": True}
        await store.create(later)

        seen = []
        cursor = first.next_cursor
        while True:
            page = await store.pending_ledger_intent_page(cursor=cursor, limit=1)
            seen.extend(item["operation_id"] for item in page.records)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert seen == ["existing", "failed", "later"]

    asyncio.run(scenario())
