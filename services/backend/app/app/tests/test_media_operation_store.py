from __future__ import annotations

import asyncio

import pytest

from media_operation_store import (
    InMemoryMediaOperationStore,
    MediaOperationCollisionError,
    RedisMediaOperationStore,
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
            return [1, args[2]]

    monkeypatch.setattr(redis.Redis, "from_url", lambda *_a, **_k: FakeRedis())
    store = RedisMediaOperationStore("redis://redis:6379/0")
    operation = _operation()
    operation["last_payload"]["status"] = "submission_unknown"

    asyncio.run(store.create(operation))

    assert captured["key_count"] == 2
    assert captured["args"][4] == "1"
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
    assert "redis.call('EXISTS', ARGV[i]) == 0" in script
    assert "redis.call('SREM', KEYS[1], ARGV[i + 1])" in script
