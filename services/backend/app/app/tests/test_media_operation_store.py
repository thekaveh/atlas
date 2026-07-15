from __future__ import annotations

import asyncio

from media_operation_store import InMemoryMediaOperationStore, RedisMediaOperationStore


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
