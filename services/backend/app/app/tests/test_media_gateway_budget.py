from __future__ import annotations

import asyncio

import pytest
import importlib
import os
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt


def _stub_required_env(monkeypatch):
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def _fresh_main(monkeypatch, *, budget_enabled, default_cap="", disabled_providers=""):
    _CapturingFalClient.cancel_result = True
    _stub_required_env(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", "enabled")
    monkeypatch.setenv("FAL_API_KEY", "fal-key")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux/dev")
    monkeypatch.setenv("FAL_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("MEDIA_BUDGET_ENABLED", "true" if budget_enabled else "false")
    monkeypatch.setenv("MEDIA_BUDGET_STORE", "memory")  # never touch Postgres in tests
    monkeypatch.setenv("MEDIA_BUDGET_DEFAULT_USD", str(default_cap))
    monkeypatch.setenv("MEDIA_DISABLED_PROVIDERS", disabled_providers)
    monkeypatch.setenv("MEDIA_BUDGET_CONSUMER_CAPS", "")
    monkeypatch.setenv("MEDIA_BUDGET_ALLOW_UNKNOWN_COST", "false")
    sys.modules.pop("main", None)
    return importlib.import_module("main")


class _CapturingFalClient:
    captured: dict = {}
    cancel_result = True

    def __init__(self, *args, **kwargs):
        _CapturingFalClient.captured["init"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def submit_media_operation(self, **kwargs):
        return {
            "operation_id": "fal-3d-9",
            "status": "submitted",
            "provider": "fal",
            "model": kwargs.get("model", "fal-ai/trellis"),
            "modality": "image_to_3d",
            "artifact_url": None,
            "artifacts": [],
            "cost_usd": 0.05,
            "license": "MIT",
            "provenance": {"provider_request_id": "fal-3d-9"},
            "raw": {},
        }

    async def get_media_operation(self, *, operation_id, modality):
        return {
            "operation_id": operation_id,
            "status": "succeeded",
            "provider": "fal",
            "model": "fal-ai/trellis",
            "modality": "image_to_3d",
            "artifact_url": "https://cdn.example/model.glb",
            "artifacts": [
                {"url": "https://cdn.example/model.glb", "role": "model_glb"}
            ],
            "cost_usd": 0.05,
            "license": "MIT",
            "provenance": {"provider_request_id": operation_id},
            "raw": {},
        }

    async def cancel_media_operation(self, *, operation_id, modality):
        _CapturingFalClient.captured["cancel"] = {
            "operation_id": operation_id,
            "modality": modality,
        }
        return self.cancel_result


class _ExplodingFalClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("provider must not be invoked when policy blocks the request")


def _submit(client, *, consumer="acme", model="trellis"):
    return client.post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
            "provider": "fal",
            "model": model,
            "input": {"image": "https://cdn.example/sprite.png"},
            "consumer": consumer,
        },
    )


def _direct_http_request():
    from starlette.requests import Request

    return Request(
        {
            "type": "http", "method": "POST", "path": "/media/generate",
            "headers": [], "query_string": b"", "server": ("backend", 80),
            "client": ("127.0.0.1", 1), "scheme": "http",
        }
    )


def _direct_media_request(main):
    return main.MediaGenerateRequest(
        modality="image_to_3d",
        provider="fal",
        model="trellis",
        input={"image": "https://cdn.example/sprite.png"},
        consumer="acme",
    )


def test_budget_disabled_preserves_gateway(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client).status_code == 202
    spend = client.get("/media/spend", params={"consumer": "acme"})
    assert spend.status_code == 200
    assert spend.json()["enabled"] is False
    assert spend.json()["records"] == []


def test_model_invalid_image_request_precedes_budget_denial(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap="10")
    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image",
            "provider": "fal",
            "input": {"prompt": "invalid mapped request", "negative_prompt": "blur"},
            "consumer": "acme",
        },
    )

    assert response.status_code == 400
    assert "negative_prompt" in response.json()["detail"]


def test_allowed_spend_records_ledger(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client, consumer="acme").status_code == 202

    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["enabled"] is True
    assert len(spend["records"]) == 1
    rec = spend["records"][0]
    assert rec["operation_id"] == "fal-3d-9"  # reservation re-keyed to provider id
    assert rec["status"] == "submitted"
    assert rec["provider"] == "fal"
    assert rec["estimated_cost_usd"] == 0.05
    assert spend["reserved_usd"] == 0.05


def test_over_budget_hard_stops_before_provider(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=0.01)
    monkeypatch.setattr(main, "FalClient", _ExplodingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    resp = _submit(client, consumer="acme")  # trellis est 0.05 > 0.01 cap
    assert resp.status_code == 402
    assert "budget exceeded" in resp.json()["detail"]

    # The denial was recorded, and no spend was reserved.
    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.0
    assert any(r["status"] == "denied" for r in spend["records"])


def test_kill_switch_blocks_provider_before_call(monkeypatch):
    main = _fresh_main(
        monkeypatch, budget_enabled=True, default_cap=10.0, disabled_providers="fal"
    )
    monkeypatch.setattr(main, "FalClient", _ExplodingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    resp = _submit(client, consumer="acme")
    assert resp.status_code == 403
    assert "kill-switch" in resp.json()["detail"]


def test_spend_read_is_scoped_to_consumer(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client, consumer="alpha").status_code == 202

    alpha = client.get("/media/spend", params={"consumer": "alpha"}).json()
    beta = client.get("/media/spend", params={"consumer": "beta"}).json()
    assert len(alpha["records"]) == 1
    assert beta["records"] == []  # no leakage across consumers


def test_attach_failure_does_not_500_and_retains_reservation(monkeypatch):
    # The provider call succeeded, so a ledger re-key hiccup must retain the
    # reservation and a durable attach intent rather than undercount paid work.
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    async def boom(*a, **k):
        raise RuntimeError("transient ledger DB error")

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", boom)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    resp = _submit(client, consumer="acme")
    assert resp.status_code == 202  # operation succeeded despite the ledger hiccup

    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.05
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation["budget_tracked"] is False
    assert operation["last_payload"]["provenance"]["ledger_attach_pending"] is True


def test_state_store_preflight_blocks_provider_and_budget_reservation(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _ExplodingFalClient, raising=False)

    async def unavailable():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(
        main.MEDIA_OPERATION_STORE, "ensure_available", unavailable, raising=False
    )

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = _submit(client, consumer="acme")
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "state_store_unavailable",
        "message": "Media operation state store is unavailable",
    }
    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["records"] == []


def test_state_persistence_failure_retains_spend_after_cancel_request(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    _CapturingFalClient.captured = {}
    _CapturingFalClient.cancel_result = True
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    attempts = 0

    async def fail_create(_operation):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("redis write failed")

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "create", fail_create)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = _submit(client, consumer="acme")
    assert response.status_code == 503
    assert attempts == 3
    assert response.json()["detail"] == {
        "message": "Provider accepted the operation but Atlas could not persist its state",
        "provider_operation_id": "fal-3d-9",
        "recovery_ledger_ids": ["fal-3d-9"],
        "provider_cancellation_requested": True,
        "manual_reconciliation_required": True,
    }
    assert _CapturingFalClient.captured["cancel"] == {
        "operation_id": "fal-3d-9",
        "modality": "image_to_3d",
    }
    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.05


def test_disabled_budget_persistence_failure_creates_reconcilable_row(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    async def fail_create(_operation):
        raise RuntimeError("redis write failed")

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "create", fail_create)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = _submit(client, consumer="acme")
    assert response.status_code == 503
    assert response.json()["detail"]["recovery_ledger_ids"] == ["fal-3d-9"]
    reconciled = client.post(
        "/media/operations/fal-3d-9/reconcile",
        json={"outcome": "release"},
    )
    assert reconciled.status_code == 200


def test_attach_failure_cleanup_retries_from_persisted_operation(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    async def attach_failed(*_args, **_kwargs):
        raise RuntimeError("ledger attach unavailable")

    original_attach = main.MEDIA_BUDGET_ENGINE.attach_operation
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", attach_failed)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    assert submitted.status_code == 202
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    provenance = operation["last_payload"]["provenance"]
    assert provenance["ledger_attach_pending"] is True
    assert len(provenance["ledger_attach_candidate_ids"]) == 2

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", original_attach)
    polled = client.get("/media/operations/fal-3d-9")
    assert polled.status_code == 200, polled.json()
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation["last_payload"]["provenance"]["ledger_attach_completed"] is True
    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.0
    assert spend["committed_usd"] == 0.05
    assert spend["records"][0]["reason"] is None


def test_background_drain_recovers_attach_without_client_poll(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    original_attach = main.MEDIA_BUDGET_ENGINE.attach_operation

    async def attach_failed(*_args, **_kwargs):
        raise RuntimeError("ledger attach unavailable")

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", attach_failed)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client, consumer="acme").status_code == 202
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", original_attach)
    sleeps = 0

    async def one_iteration(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(main.asyncio, "sleep", one_iteration)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._media_ledger_intent_loop())

    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation["last_payload"]["provenance"]["ledger_attach_completed"] is True
    assert asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get("fal-3d-9")) is not None


def test_background_drain_caps_recovery_pages_per_cycle(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False)
    calls = []

    class EndlessStore:
        async def pending_ledger_intent_page(self, *, cursor, limit):
            calls.append((cursor, limit))
            return SimpleNamespace(records=[], next_cursor=str(len(calls)))

    monkeypatch.setattr(main, "MEDIA_OPERATION_STORE", EndlessStore())
    monkeypatch.setattr(main, "media_ledger_recovery_batch_size", lambda: 7)
    monkeypatch.setattr(main, "media_ledger_recovery_max_cycles", lambda: 3)
    sleeps = 0

    async def one_poll_then_stop(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(main.asyncio, "sleep", one_poll_then_stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._media_ledger_intent_loop())

    assert calls == [(None, 7), ("1", 7), ("2", 7)]


def test_background_drain_defers_repeated_failure_while_new_work_progresses(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=False)
    from media_operation_store import InMemoryMediaOperationStore

    class ArrivingStore(InMemoryMediaOperationStore):
        def __init__(self):
            super().__init__()
            self.pages = 0

        async def pending_ledger_intent_page(self, **kwargs):
            if self.pages:
                operation_id = f"arrival-{self.pages}"
                await self.create(pending_operation(operation_id))
            self.pages += 1
            return await super().pending_ledger_intent_page(**kwargs)

    def pending_operation(operation_id):
        return {
            "operation_id": operation_id,
            "provider": "fal",
            "modality": "image",
            "model": "flux",
            "owner_scope": "service",
            "budget_tracked": True,
            "reconciled": False,
            "last_payload": {
                "operation_id": operation_id,
                "status": "queued",
                "provenance": {"ledger_attach_pending": True},
            },
        }

    store = ArrivingStore()
    asyncio.run(store.create(pending_operation("failed")))
    asyncio.run(store.create(pending_operation("existing")))
    monkeypatch.setattr(main, "MEDIA_OPERATION_STORE", store)
    monkeypatch.setattr(main, "media_ledger_recovery_batch_size", lambda: 1)
    monkeypatch.setattr(main, "media_ledger_recovery_max_cycles", lambda: 1)
    main._media_ledger_intent_cursor = None
    attempts = []

    async def recover(operation_id, operation):
        attempts.append(operation_id)
        if operation_id == "failed":
            raise RuntimeError("still unavailable")
        return operation

    async def reconcile(_operation_id, _operation):
        return None

    monkeypatch.setattr(main, "_maybe_recover_media_ledger_intent", recover)
    monkeypatch.setattr(main, "_maybe_reconcile_ledger", reconcile)
    polls = 0

    async def four_polls(_seconds):
        nonlocal polls
        polls += 1
        if polls > 4:
            raise asyncio.CancelledError()

    monkeypatch.setattr(main.asyncio, "sleep", four_polls)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._media_ledger_intent_loop())

    assert attempts[:4] == ["failed", "existing", "failed", "arrival-1"]


def test_background_drain_resets_cursor_when_failure_cannot_be_deferred(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False)

    class BrokenDeferralStore:
        async def pending_ledger_intent_page(self, **_kwargs):
            return SimpleNamespace(
                records=[{"operation_id": "failed"}], next_cursor="8"
            )

        async def defer_pending_ledger_intent(self, _operation_id):
            raise RuntimeError("redis unavailable")

    async def fail_recovery(_operation_id, _operation):
        raise RuntimeError("ledger unavailable")

    polls = 0

    async def one_poll(_seconds):
        nonlocal polls
        polls += 1
        if polls > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(main, "MEDIA_OPERATION_STORE", BrokenDeferralStore())
    monkeypatch.setattr(main, "_maybe_recover_media_ledger_intent", fail_recovery)
    monkeypatch.setattr(main, "media_ledger_recovery_max_cycles", lambda: 3)
    monkeypatch.setattr(main.asyncio, "sleep", one_poll)
    main._media_ledger_intent_cursor = "7"

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._media_ledger_intent_loop())

    assert main._media_ledger_intent_cursor is None


def test_duplicate_provider_operation_id_does_not_cross_tenant_state(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client, consumer="tenant-a").status_code == 202
    duplicate = _submit(client, consumer="tenant-b")
    assert duplicate.status_code == 502
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation["consumer"] == "tenant-a"
    tenant_b = client.get("/media/spend", params={"consumer": "tenant-b"}).json()
    assert tenant_b["reserved_usd"] == 0.0


def test_pre_provider_cleanup_failure_exposes_reconcilable_reservation(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def invalid_submission(*_args, **_kwargs):
        raise ValueError("provider input rejected")

    original_release = main.MEDIA_BUDGET_ENGINE.release

    async def release_failed(*_args, **_kwargs):
        raise RuntimeError("ledger cleanup unavailable")

    monkeypatch.setattr(main, "_submit_media_provider", invalid_submission)
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "release", release_failed)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = _submit(client, consumer="acme")
    assert response.status_code == 503
    local_id = response.json()["detail"]["local_submission_id"]
    assert response.json()["detail"]["recovery_ledger_ids"] == [local_id]

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "release", original_release)
    reconciled = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert reconciled.status_code == 200
    polled = client.get(f"/media/operations/{local_id}")
    assert polled.status_code == 200
    assert polled.json()["status"] == "failed"
    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.0


def test_state_persistence_retry_recovers_without_provider_cancel(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    _CapturingFalClient.captured = {}
    _CapturingFalClient.cancel_result = True
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    original_create = main.MEDIA_OPERATION_STORE.create
    attempts = 0

    async def transient_create(operation):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient redis write failure")
        return await original_create(operation)

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "create", transient_create)

    from fastapi.testclient import TestClient

    response = _submit(TestClient(main.app), consumer="acme")
    assert response.status_code == 202
    assert attempts == 3
    assert "cancel" not in _CapturingFalClient.captured
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation["budget_tracked"] is True


def test_lost_operation_state_ack_can_sync_ledger_fallback_winner(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    original_persist = main._persist_media_operation

    async def committed_without_ack(operation):
        await original_persist(operation)
        raise RuntimeError("operation-state acknowledgement lost")

    monkeypatch.setattr(main, "_persist_media_operation", committed_without_ack)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    assert submitted.status_code == 503

    original_get = main.MEDIA_OPERATION_STORE.get

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("operation store unavailable")

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "get", unavailable)
    first = client.post(
        "/media/operations/fal-3d-9/reconcile", json={"outcome": "release"}
    )
    assert first.status_code == 503

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "get", original_get)
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    provider_terminal = dict(operation["last_payload"])
    provider_terminal["status"] = "succeeded"
    asyncio.run(
        main.MEDIA_OPERATION_STORE.transition_payload(
            "fal-3d-9", provider_terminal, expected_status="queued"
        )
    )
    synced = client.post(
        "/media/operations/fal-3d-9/reconcile", json={"outcome": "release"}
    )
    assert synced.status_code == 200
    assert synced.json()["status"] == "failed"
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation["last_payload"]["provenance"][
        "manual_reconciliation_outcome"
    ] == "release"

def test_uncancelled_unpersisted_operation_retains_budget_reservation(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    _CapturingFalClient.captured = {}
    _CapturingFalClient.cancel_result = False
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    async def fail_create(_operation):
        raise RuntimeError("redis write failed")

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "create", fail_create)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = _submit(client, consumer="acme")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["provider_operation_id"] == "fal-3d-9"
    assert detail["provider_cancellation_requested"] is False
    assert detail["manual_reconciliation_required"] is True
    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.05
    assert spend["records"][0]["operation_id"] == "fal-3d-9"


def test_reconcile_commits_on_successful_poll(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client, consumer="acme").status_code == 202
    polled = client.get("/media/operations/fal-3d-9")
    assert polled.status_code == 200
    assert polled.json()["status"] == "succeeded"

    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["committed_usd"] == 0.05
    assert spend["reserved_usd"] == 0.0
    rec = spend["records"][0]
    assert rec["status"] == "committed"
    assert rec["artifact_refs"] == ["https://cdn.example/model.glb"]


def test_cancellation_mid_submit_persists_ambiguous_reservation(monkeypatch):
    # A request cancelled mid-submit (uvicorn graceful shutdown / client
    # disconnect) raises asyncio.CancelledError — a BaseException the
    # ``except Exception`` handlers cannot catch. The finally must still release
    # the reservation so a durably committed RESERVED row is not stranded
    # against the consumer's cap (which would eventually 402 legit requests).
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    async def cancelled(*a, **k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(main, "_submit_media_provider", cancelled)

    import concurrent.futures

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    # asyncio.CancelledError surfaces as concurrent.futures.CancelledError once
    # it crosses the TestClient portal boundary (3.8+ split the two classes).
    with pytest.raises((asyncio.CancelledError, concurrent.futures.CancelledError)):
        _submit(client, consumer="acme")

    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.05
    assert spend["records"][0]["reason"].startswith("ambiguous provider submission")
    operation_id = spend["records"][0]["operation_id"]
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get(operation_id))
    assert operation["last_payload"]["status"] == "submission_unknown"


def test_repeated_pre_submit_cancellation_finishes_reservation_release(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    preparation_started = asyncio.Event()
    release_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    original_release = main.MEDIA_BUDGET_ENGINE.release

    async def blocked_to_thread(*_args, **_kwargs):
        preparation_started.set()
        await asyncio.Event().wait()

    async def gated_release(*args, **kwargs):
        release_started.set()
        await release_cleanup.wait()
        return await original_release(*args, **kwargs)

    monkeypatch.setattr(main.asyncio, "to_thread", blocked_to_thread)
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "release", gated_release)

    async def scenario():
        principal = main.BackendPrincipal(kind="service", subject="test")
        submission = asyncio.create_task(main.submit_media_generation(
            _direct_media_request(main), _direct_http_request(), principal
        ))
        await asyncio.wait_for(preparation_started.wait(), timeout=1)
        submission.cancel()
        await asyncio.wait_for(release_started.wait(), timeout=1)
        submission.cancel()
        await asyncio.sleep(0)
        assert submission.done() is False
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())
    spend = asyncio.run(main.MEDIA_BUDGET_ENGINE.spend(consumer="acme"))
    assert spend["reserved_usd"] == 0.0
    assert spend["records"][0]["status"] == main.media_ledger.STATUS_RELEASED


def test_pre_submit_cancellation_survives_release_failure_after_durable_intent(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    preparation_started = asyncio.Event()

    async def blocked_to_thread(*_args, **_kwargs):
        preparation_started.set()
        await asyncio.Event().wait()

    async def fail_release(*_args, **_kwargs):
        raise RuntimeError("simulated ledger release failure")

    monkeypatch.setattr(main.asyncio, "to_thread", blocked_to_thread)
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "release", fail_release)

    async def scenario():
        principal = main.BackendPrincipal(kind="service", subject="test")
        submission = asyncio.create_task(main.submit_media_generation(
            _direct_media_request(main), _direct_http_request(), principal
        ))
        await asyncio.wait_for(preparation_started.wait(), timeout=1)
        submission.cancel()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())
    spend = asyncio.run(main.MEDIA_BUDGET_ENGINE.spend(consumer="acme"))
    reservation_id = spend["records"][0]["operation_id"]
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get(reservation_id))
    assert operation["last_payload"]["provenance"]["ledger_cleanup_pending"] is True


def test_cancellation_after_provider_acceptance_never_releases_reservation(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    async def cancelled_attach(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        main.MEDIA_BUDGET_ENGINE, "attach_operation", cancelled_attach
    )
    import concurrent.futures
    from fastapi.testclient import TestClient

    with pytest.raises((asyncio.CancelledError, concurrent.futures.CancelledError)):
        _submit(TestClient(main.app), consumer="acme")

    spend = asyncio.run(main.MEDIA_BUDGET_ENGINE.spend(consumer="acme"))
    assert spend["reserved_usd"] == 0.05
    assert spend["records"][0]["status"] == "reserved"
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation["last_payload"]["provenance"]["ledger_attach_pending"] is True


def test_post_accept_cancellation_persists_provider_id_when_budget_disabled(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=False, default_cap="")
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    async def cancelled_attach(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        main.MEDIA_BUDGET_ENGINE, "attach_operation", cancelled_attach
    )
    import concurrent.futures
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    with pytest.raises((asyncio.CancelledError, concurrent.futures.CancelledError)):
        _submit(client, consumer="acme")

    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation is not None
    assert operation["operation_id"] == "fal-3d-9"
    assert asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get("fal-3d-9")) is not None
    assert client.get("/media/operations/fal-3d-9").status_code == 200
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get("fal-3d-9"))
    assert record.reason is None


def test_repeated_post_accept_cancellation_cannot_cancel_operation_persistence(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    original_persist = main._persist_media_operation
    persist_started = asyncio.Event()
    release_persist = asyncio.Event()
    persist_cancelled = False

    async def gated_persist(operation):
        nonlocal persist_cancelled
        persist_started.set()
        try:
            await release_persist.wait()
        except asyncio.CancelledError:
            persist_cancelled = True
            raise
        await original_persist(operation)

    monkeypatch.setattr(main, "_persist_media_operation", gated_persist)

    async def scenario():
        principal = main.BackendPrincipal(kind="service", subject="test")
        submission = asyncio.create_task(
            main.submit_media_generation(
                _direct_media_request(main), _direct_http_request(), principal
            )
        )
        await asyncio.wait_for(persist_started.wait(), timeout=1)
        submission.cancel()
        await asyncio.sleep(0)
        submission.cancel()
        await asyncio.sleep(0)
        assert submission.done() is False
        release_persist.set()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())

    assert persist_cancelled is False
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation is not None
    assert operation["operation_id"] == "fal-3d-9"


def test_post_accept_cancellation_during_attach_protection_still_persists_operation(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    protection_started = asyncio.Event()
    release_protection = asyncio.Event()
    original_protect = main.MEDIA_BUDGET_ENGINE.protect_attach_ids

    async def fail_attach(*_args, **_kwargs):
        raise RuntimeError("simulated attach failure")

    async def gated_protect(operation_ids):
        protection_started.set()
        await release_protection.wait()
        return await original_protect(operation_ids)

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", fail_attach)
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "protect_attach_ids", gated_protect)

    async def scenario():
        principal = main.BackendPrincipal(kind="service", subject="test")
        submission = asyncio.create_task(main.submit_media_generation(
            _direct_media_request(main), _direct_http_request(), principal
        ))
        await asyncio.wait_for(protection_started.wait(), timeout=1)
        submission.cancel()
        await asyncio.sleep(0)
        submission.cancel()
        assert submission.done() is False
        release_protection.set()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation is not None
    assert operation["last_payload"]["provenance"]["ledger_attach_pending"] is True


def test_post_accept_cancellation_during_failure_compensation_finishes_ledger(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    protection_started = asyncio.Event()
    release_protection = asyncio.Event()
    original_protect = main.MEDIA_BUDGET_ENGINE.protect_recovery_ids

    async def fail_persist(*_args, **_kwargs):
        raise RuntimeError("simulated operation persistence failure")

    async def gated_protect(operation_ids):
        protection_started.set()
        await release_protection.wait()
        return await original_protect(operation_ids)

    monkeypatch.setattr(main, "_persist_media_operation", fail_persist)
    monkeypatch.setattr(
        main.MEDIA_BUDGET_ENGINE, "protect_recovery_ids", gated_protect
    )

    async def scenario():
        principal = main.BackendPrincipal(kind="service", subject="test")
        submission = asyncio.create_task(main.submit_media_generation(
            _direct_media_request(main), _direct_http_request(), principal
        ))
        await asyncio.wait_for(protection_started.wait(), timeout=1)
        submission.cancel()
        await asyncio.sleep(0)
        submission.cancel()
        assert submission.done() is False
        release_protection.set()
        with pytest.raises(asyncio.CancelledError):
            await submission

    asyncio.run(scenario())
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get("fal-3d-9"))
    assert record is not None
    assert record.reason == main.media_ledger.AMBIGUOUS_RECOVERY_REASON


def test_clear_protection_race_with_terminal_poll_still_settles(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client, consumer="acme").status_code == 202
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    payload = dict(operation["last_payload"])
    provenance = dict(payload.get("provenance") or {})
    provenance["ledger_attach_protection_clear_pending"] = True
    payload["provenance"] = provenance
    asyncio.run(
        main.MEDIA_OPERATION_STORE.transition_payload(
            "fal-3d-9", payload, expected_status="submitted"
        )
    )
    stale = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    terminal = dict(stale["last_payload"])
    terminal["status"] = "succeeded"
    asyncio.run(
        main.MEDIA_OPERATION_STORE.transition_payload(
            "fal-3d-9", terminal, expected_status="submitted"
        )
    )
    recovered = asyncio.run(
        main._maybe_recover_media_ledger_intent("fal-3d-9", stale)
    )
    assert recovered["last_payload"]["status"] == "succeeded"
    asyncio.run(main._maybe_reconcile_ledger("fal-3d-9", recovered))
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get("fal-3d-9"))
    assert record.status == main.media_ledger.STATUS_COMMITTED


def test_ambiguous_fal_submit_timeout_retains_reservation_and_local_record(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError(
            "FAL may have accepted the request before Atlas timed out"
        )

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = _submit(client, consumer="acme")

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert detail["submission_status"] == "unknown"
    assert detail["manual_reconciliation_required"] is True
    local_id = detail["local_submission_id"]
    assert local_id.startswith("resv-")

    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.05
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    assert operation["last_payload"]["status"] == "submission_unknown"
    assert operation["budget_tracked"] is True
    polled = client.get(f"/media/operations/{local_id}")
    assert polled.status_code == 200
    assert polled.json()["status"] == "submission_unknown"

    class UnexpectedFalClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("a local reconciliation id must never reach FAL")

    monkeypatch.setattr(main, "FalClient", UnexpectedFalClient)
    cancelled = client.post(f"/media/operations/{local_id}/cancel")
    assert cancelled.status_code == 409
    assert "manual reconciliation" in cancelled.json()["detail"].lower()
    unchanged = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    assert unchanged["last_payload"]["status"] == "submission_unknown"

    reconciled = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "failed"
    assert (
        reconciled.json()["provenance"]["manual_reconciliation_outcome"]
        == "release"
    )
    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.0
    assert spend["records"][0]["status"] == "released"


def test_ambiguous_fal_submit_can_be_manually_committed(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]

    reconciled = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit", "final_cost_usd": 0.04},
    )

    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "succeeded"
    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["reserved_usd"] == 0.0
    assert spend["committed_usd"] == 0.04
    conflicting_retry = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit", "final_cost_usd": 0.03},
    )
    assert conflicting_retry.status_code == 409


def test_ambiguous_commit_uses_known_estimate_when_final_cost_is_omitted(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]
    reconciled = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit"},
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["cost_usd"] == 0.05


def test_ambiguous_commit_requires_final_cost_when_estimate_is_unknown(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False, default_cap="")

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = client.post(
        "/media/generate",
        json={
            "modality": "image",
            "provider": "fal",
            "input": {"prompt": "an atlas"},
        },
    )
    local_id = submitted.json()["detail"]["local_submission_id"]
    rejected = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit"},
    )
    assert rejected.status_code == 422
    accepted = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit", "final_cost_usd": 0.04},
    )
    assert accepted.status_code == 200


def test_operation_reconciliation_rejects_opposite_ledger_winner(monkeypatch):
    from dataclasses import replace

    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]
    reserved = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get(local_id))

    async def opposite_winner(**_kwargs):
        return replace(reserved, status=main.media_ledger.STATUS_RELEASED)

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "reconcile", opposite_winner)
    response = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit", "final_cost_usd": 0.04},
    )
    assert response.status_code == 409
    repaired = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    assert repaired["reconciled"] is True
    assert repaired["last_payload"]["status"] == "failed"
    provenance = repaired["last_payload"]["provenance"]
    assert provenance["manual_reconciliation_outcome"] == "release"
    assert provenance["requested_manual_reconciliation_outcome"] == "commit"
    assert provenance["ledger_winner_adopted"] is True
    assert provenance["requested_manual_reconciliation_cost_usd"] == 0.04
    assert provenance["ledger_winner_cost_usd"] is None
    assert provenance["ledger_conflict_kind"] == "outcome"

    retry = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "failed"


def test_ambiguous_reconciliation_rejects_user_token(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]

    secret = "atlas-test-supabase-jwt-secret-32-bytes"
    subject = str(uuid4())
    token = jwt.encode(
        {
            "sub": subject,
            "role": "authenticated",
            "aud": "authenticated",
            "exp": int(time.time()) + 60,
        },
        secret,
        algorithm="HS256",
    )
    monkeypatch.setenv("BACKEND_IDENTITY_AUTH", "required")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    monkeypatch.setenv("BACKEND_INTERNAL_API_TOKEN", "operator-secret")

    rejected = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rejected.status_code == 403

    accepted = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
        headers={"Authorization": "Bearer operator-secret"},
    )
    assert accepted.status_code == 200


def test_ambiguous_reconciliation_retries_after_ledger_failure(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]
    original_reconcile = main.MEDIA_BUDGET_ENGINE.reconcile
    calls = 0

    async def fail_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary ledger outage")
        return await original_reconcile(**kwargs)

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "reconcile", fail_once)
    first = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert first.status_code == 503

    pending = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    assert pending["last_payload"]["status"] == "failed"
    assert pending["reconciled"] is False
    assert pending["last_payload"]["provenance"]["ledger_reconciliation_pending"] is True
    assert pending["last_payload"]["provenance"]["manual_reconciliation_required"] is True

    retried = client.get(f"/media/operations/{local_id}")
    assert retried.status_code == 200
    assert calls == 2
    settled = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    assert settled["reconciled"] is True
    assert settled["last_payload"]["provenance"]["ledger_reconciliation_pending"] is False
    assert settled["last_payload"]["provenance"]["manual_reconciliation_required"] is False


def test_ambiguous_reconciliation_retries_after_metadata_finalize_failure(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]
    original_replace = main.MEDIA_OPERATION_STORE.replace_terminal_payload
    calls = 0

    async def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary operation-store write failure")
        return await original_replace(*args, **kwargs)

    monkeypatch.setattr(
        main.MEDIA_OPERATION_STORE, "replace_terminal_payload", fail_once
    )
    first = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert first.status_code == 503
    pending = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    assert pending["reconciled"] is False

    retried = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert retried.status_code == 200
    settled = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    assert settled["reconciled"] is True
    assert settled["last_payload"]["provenance"]["ledger_reconciliation_pending"] is False


def test_same_outcome_transition_loser_returns_persisted_result(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]
    unknown = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    terminal_payload = dict(unknown["last_payload"])
    terminal_payload["status"] = "failed"
    terminal_payload["provenance"] = dict(terminal_payload["provenance"])
    terminal_payload["provenance"].update(
        {
            "manual_reconciliation_required": True,
            "manual_reconciliation_outcome": "release",
            "ledger_reconciliation_pending": True,
        }
    )
    asyncio.run(
        main.MEDIA_OPERATION_STORE.transition_payload(
            local_id,
            terminal_payload,
            expected_status="submission_unknown",
        )
    )

    original_get = main.MEDIA_OPERATION_STORE.get
    get_calls = 0

    async def concurrent_get(operation_id):
        nonlocal get_calls
        get_calls += 1
        if get_calls == 1:
            return unknown
        return await original_get(operation_id)

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "get", concurrent_get)
    response = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"


def test_ambiguous_reconciliation_falls_back_to_durable_ledger(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    async def unavailable_operation_store(*_args, **_kwargs):
        raise RuntimeError("operation store unavailable")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    monkeypatch.setattr(main, "_persist_media_operation", unavailable_operation_store)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    detail = submitted.json()["detail"]
    assert detail["local_record_persisted"] is False
    local_id = detail["local_submission_id"]

    async def continuing_operation_store_outage(*_args, **_kwargs):
        raise RuntimeError("operation store still unavailable")

    original_get = main.MEDIA_OPERATION_STORE.get
    monkeypatch.setattr(
        main.MEDIA_OPERATION_STORE, "get", continuing_operation_store_outage
    )

    reconciled = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert reconciled.status_code == 503
    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "get", original_get)
    reconciled = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["provenance"]["local_record_persisted"] is False
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get(local_id))
    assert record.status == main.media_ledger.STATUS_RELEASED
    assert record.reason == "manual reconciliation: release"

    retry = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert retry.status_code == 200


def test_ledger_fallback_rejects_retry_with_different_committed_cost(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    async def unavailable_operation_store(*_args, **_kwargs):
        raise RuntimeError("operation store unavailable")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    monkeypatch.setattr(main, "_persist_media_operation", unavailable_operation_store)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]
    first = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit", "final_cost_usd": 0.04},
    )
    assert first.status_code == 200
    assert first.json()["cost_usd"] == 0.04

    conflicting = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit", "final_cost_usd": 0.03},
    )
    assert conflicting.status_code == 409
    retry = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit"},
    )
    assert retry.status_code == 200
    assert retry.json()["cost_usd"] == 0.04


def test_ledger_fallback_accepts_explicit_retry_of_committed_estimate(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    async def unavailable_operation_store(*_args, **_kwargs):
        raise RuntimeError("operation store unavailable")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    monkeypatch.setattr(main, "_persist_media_operation", unavailable_operation_store)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]
    first = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit"},
    )
    assert first.status_code == 200
    retry = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "commit", "final_cost_usd": 0.05},
    )
    assert retry.status_code == 200
    assert retry.json()["cost_usd"] == 0.05


def test_ambiguous_recovery_row_exists_when_budget_enforcement_is_disabled(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=False, default_cap="")

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    async def unavailable_operation_store(*_args, **_kwargs):
        raise RuntimeError("operation store unavailable")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    monkeypatch.setattr(main, "_persist_media_operation", unavailable_operation_store)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    detail = submitted.json()["detail"]
    assert detail["local_record_persisted"] is False
    assert detail["ledger_record_persisted"] is True
    local_id = detail["local_submission_id"]

    reconciled = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert reconciled.status_code == 200
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get(local_id))
    assert record.status == main.media_ledger.STATUS_RELEASED


def test_local_ambiguous_record_can_be_resolved_when_recovery_ledger_write_fails(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=False, default_cap="")

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    async def unavailable_ledger(*_args, **_kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "record_ambiguous", unavailable_ledger)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    detail = submitted.json()["detail"]
    assert detail["local_record_persisted"] is True
    assert detail["ledger_record_persisted"] is False
    local_id = detail["local_submission_id"]

    reconciled = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert reconciled.status_code == 200
    provenance = reconciled.json()["provenance"]
    assert provenance["manual_reconciliation_required"] is False
    assert provenance["ledger_reconciliation_pending"] is False


def test_lost_recovery_insert_ack_is_discovered_before_local_resolution(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False, default_cap="")

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    original_record = main.MEDIA_BUDGET_ENGINE.record_ambiguous

    async def committed_without_ack(**kwargs):
        await original_record(**kwargs)
        raise RuntimeError("database acknowledgement lost")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    monkeypatch.setattr(
        main.MEDIA_BUDGET_ENGINE, "record_ambiguous", committed_without_ack
    )
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    detail = submitted.json()["detail"]
    assert detail["ledger_record_persisted"] is False
    local_id = detail["local_submission_id"]

    reconciled = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert reconciled.status_code == 200
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get(local_id))
    assert record.status == main.media_ledger.STATUS_RELEASED
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    provenance = operation["last_payload"]["provenance"]
    assert provenance["ledger_record_recovered_after_lost_ack"] is True
    assert provenance["ledger_record_persisted"] is True


def test_missing_tracked_recovery_record_returns_503(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]

    async def missing_record(**_kwargs):
        return None

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "reconcile", missing_record)
    response = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert response.status_code == 503
    pending = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    assert pending["reconciled"] is False


def test_old_missing_manual_recovery_record_is_never_treated_as_pruned(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]
    main.MEDIA_BUDGET_ENGINE.config.retention_days = 1
    main.MEDIA_OPERATION_STORE._records[local_id]["created_at_epoch"] -= 2 * 86400

    async def missing_record(**_kwargs):
        return None

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "reconcile", missing_record)
    response = client.post(
        f"/media/operations/{local_id}/reconcile", json={"outcome": "release"}
    )
    assert response.status_code == 503
    pending = asyncio.run(main.MEDIA_OPERATION_STORE.get(local_id))
    assert pending["reconciled"] is False


def test_late_terminal_poll_records_intentional_ledger_prune(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client, consumer="acme").status_code == 202
    main.MEDIA_BUDGET_ENGINE.config.retention_days = 1
    operation = main.MEDIA_OPERATION_STORE._records["fal-3d-9"]
    operation["created_at_epoch"] -= 2 * 86400
    main.MEDIA_BUDGET_ENGINE.store._records.pop("fal-3d-9")

    response = client.get("/media/operations/fal-3d-9")
    assert response.status_code == 200
    persisted = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert persisted["reconciled"] is True
    assert persisted["last_payload"]["provenance"]["ledger_retention_pruned"] is True


def test_operation_poll_returns_503_when_state_store_lookup_fails(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False)

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "get", unavailable)
    from fastapi.testclient import TestClient

    response = TestClient(main.app).get("/media/operations/fal-op")
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "state_store_unavailable",
        "message": "Media operation state store is unavailable",
    }


def test_submission_returns_503_when_budget_reservation_store_fails(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def unavailable(**_kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "reserve", unavailable)
    from fastapi.testclient import TestClient

    response = _submit(TestClient(main.app), consumer="acme")
    assert response.status_code == 503


def test_spend_returns_503_when_ledger_read_fails(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)

    async def unavailable(**_kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "spend", unavailable)
    from fastapi.testclient import TestClient

    response = TestClient(main.app).get(
        "/media/spend", params={"consumer": "acme"}
    )
    assert response.status_code == 503


def test_poll_returns_503_when_state_transition_fails(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client).status_code == 202

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "transition_payload", unavailable)
    assert client.get("/media/operations/fal-3d-9").status_code == 503


def test_cancel_returns_503_when_intent_or_enrichment_write_fails(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client).status_code == 202
    original_transition = main.MEDIA_OPERATION_STORE.transition_payload
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("redis unavailable")
        return await original_transition(*args, **kwargs)

    monkeypatch.setattr(main.MEDIA_OPERATION_STORE, "transition_payload", fail_second)
    assert client.post("/media/operations/fal-3d-9/cancel").status_code == 503


def test_local_only_reconciliation_returns_503_during_operation_store_outage(
    monkeypatch,
):
    main = _fresh_main(monkeypatch, budget_enabled=False, default_cap="")

    async def ambiguous(*_args, **_kwargs):
        raise main.FalSubmissionAmbiguousError("provider outcome unknown")

    async def unavailable_ledger(*_args, **_kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(main, "_submit_media_provider", ambiguous)
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "record_ambiguous", unavailable_ledger)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = _submit(client, consumer="acme")
    local_id = submitted.json()["detail"]["local_submission_id"]

    async def unavailable_operation_store(*_args, **_kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(
        main.MEDIA_OPERATION_STORE, "get", unavailable_operation_store
    )
    response = client.post(
        f"/media/operations/{local_id}/reconcile",
        json={"outcome": "release"},
    )
    assert response.status_code == 503


def test_manual_release_rejects_irrelevant_final_cost(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False)
    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/operations/missing/reconcile",
        json={"outcome": "release", "final_cost_usd": 1.0},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "final_cost", ["1e999", "1000000", "0.1234567", "true", '"0.04"']
)
def test_manual_reconciliation_rejects_unrepresentable_cost(monkeypatch, final_cost):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = client.post(
        "/media/operations/resv-missing/reconcile",
        content=(
            '{"outcome":"commit","final_cost_usd":' + final_cost + "}"
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_media_budget_prune_loop_invokes_prune_expired(monkeypatch):
    # The reservation-reclamation backstop advertised via MEDIA_BUDGET_RETENTION_DAYS
    # must actually run: the reaper loop calls prune_expired() each sweep. (Without
    # scheduling it, an abandoned SUBMITTED op leaks its ledger row against the cap.)
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    calls = []

    async def fake_prune():
        calls.append(True)
        return 0

    async def fake_sleep(_seconds):
        if calls:  # after the first sweep, stop the loop
            raise asyncio.CancelledError()

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "prune_expired", fake_prune)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._media_budget_prune_loop())
    assert calls == [True]


def test_partial_attach_failure_recovers_from_either_candidate_id(monkeypatch):
    # attach_operation re-keys reservation_id → operation_id before bumping
    # status; if the re-key landed but the bump failed, the row is under
    # operation_id. The retry intent carries BOTH ids and converges without
    # releasing paid work.
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    real_attach = main.MEDIA_BUDGET_ENGINE.attach_operation

    async def boom(*a, **k):
        raise RuntimeError("attach bump failed after re-key")

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", boom)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client, consumer="acme").status_code == 202
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    candidates = operation["last_payload"]["provenance"][
        "ledger_attach_candidate_ids"
    ]
    assert any(r.startswith("resv-") for r in candidates)
    assert "fal-3d-9" in candidates
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", real_attach)
    recovered = client.get("/media/operations/fal-3d-9")
    assert recovered.status_code == 200, recovered.json()


# Legacy /comfyui/generate FAL branch vs budgets (kept here: the FAL provider
# test module is at its size ceiling).
from tests.test_fal_media_provider import _fresh_main as _fresh_fal_main  # noqa: E402


@pytest.mark.parametrize("disabled_providers", ["", "fal"])
def test_fal_compatibility_path_is_refused_while_budgets_are_enabled(monkeypatch, disabled_providers):
    """It calls FAL with no reservation or kill-switch check, so with budgets
    on (including FAL kill-switched) it spent outside every cap."""
    monkeypatch.setenv("MEDIA_BUDGET_ENABLED", "true")
    monkeypatch.setenv("MEDIA_DISABLED_PROVIDERS", disabled_providers)
    main = _fresh_fal_main(monkeypatch, fal_source="enabled", fal_api_key="fal-key")

    class UnexpectedFalClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("the budget refusal must happen before FAL execution")

    monkeypatch.setattr(main, "FalClient", UnexpectedFalClient)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post("/comfyui/generate", json={"prompt": "a cat"})

    if disabled_providers:
        # The kill-switch is checked first (2026-10-08 run, cycle 23).
        assert response.status_code == 403
        assert "kill-switch" in response.json()["detail"]
    else:
        assert response.status_code == 409
        assert "/media/generate" in response.json()["detail"]


def test_fal_compatibility_timeout_is_a_504_not_a_retryable_200(monkeypatch):
    main = _fresh_fal_main(monkeypatch, fal_source="enabled", fal_api_key="fal-key")

    class SlowFalClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def generate_simple_image(self, **_kwargs):
            raise asyncio.TimeoutError

    monkeypatch.setattr(main, "FalClient", SlowFalClient)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post("/comfyui/generate", json={"prompt": "a cat"})

    assert response.status_code == 504


def test_timed_out_fal_job_keeps_its_reservation_until_settled(monkeypatch):
    """FAL keeps running (and billing) after the deadline: releasing the
    reservation at `timeout` let repeated short-timeout jobs spend past the
    cap. The op holds the reservation as `cancellation_requested`, and an
    operator can settle one FAL never resolves via /reconcile."""
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    async def _running(*, provider, operation_id, modality, model):
        return {"operation_id": operation_id, "provider": provider,
                "modality": modality, "model": model, "status": "running"}

    monkeypatch.setattr(main, "_poll_media_provider", _running)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client).status_code == 202
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    monkeypatch.setattr(
        main.time, "time",
        lambda: operation["created_at_epoch"] + operation["timeout_seconds"] + 1,
    )

    polled = client.get("/media/operations/fal-3d-9").json()
    assert (polled["status"], polled["provenance"]["timed_out"]) == ("cancellation_requested", True)
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get("fal-3d-9"))
    assert record.status != main.media_ledger.STATUS_RELEASED

    settled = client.post("/media/operations/fal-3d-9/reconcile", json={"outcome": "release"})
    assert settled.status_code == 200 and settled.json()["status"] == "failed"
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get("fal-3d-9"))
    assert record.status == main.media_ledger.STATUS_RELEASED


def test_operator_can_settle_a_user_cancelled_fal_job_fal_never_resolves(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client).status_code == 202
    cancelled = client.post("/media/operations/fal-3d-9/cancel")
    assert cancelled.json()["status"] == "cancellation_requested", cancelled.json()

    async def _unreachable(**_kwargs):
        raise main.HTTPException(status_code=503, detail="FAL is disabled")

    monkeypatch.setattr(main, "_poll_media_provider", _unreachable)
    settled = client.post("/media/operations/fal-3d-9/reconcile", json={"outcome": "release"})
    assert settled.status_code == 200 and settled.json()["status"] == "failed", settled.json()
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get("fal-3d-9"))
    assert record.status == main.media_ledger.STATUS_RELEASED


def test_timed_out_fal_job_keeps_its_reservation_after_a_recovered_attach(monkeypatch):
    """The reservation is tracked once a failed submit-time attach is
    recovered, although the operation's budget_tracked flag stays False."""
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    original_attach = main.MEDIA_BUDGET_ENGINE.attach_operation

    async def fail_attach(*_args, **_kwargs):
        raise RuntimeError("simulated attach failure")

    async def _running(*, provider, operation_id, modality, model):
        return {"operation_id": operation_id, "provider": provider,
                "modality": modality, "model": model, "status": "running"}

    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", fail_attach)
    monkeypatch.setattr(main, "_poll_media_provider", _running)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert _submit(client).status_code == 202
    monkeypatch.setattr(main.MEDIA_BUDGET_ENGINE, "attach_operation", original_attach)
    assert client.get("/media/operations/fal-3d-9").json()["status"] == "running"  # recovers the attach
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation["last_payload"]["provenance"].get("ledger_attach_completed") is True
    assert not operation.get("budget_tracked")
    monkeypatch.setattr(
        main.time, "time",
        lambda: operation["created_at_epoch"] + operation["timeout_seconds"] + 1,
    )

    polled = client.get("/media/operations/fal-3d-9").json()
    assert (polled["status"], polled["provenance"]["timed_out"]) == ("cancellation_requested", True)
    record = asyncio.run(main.MEDIA_BUDGET_ENGINE.store.get("fal-3d-9"))
    assert record.status != main.media_ledger.STATUS_RELEASED


# --- FAL request timeout, SDK client lifetime, unknown status, missing GLB (#1358)


def test_request_timeout_reaches_fal_submit(monkeypatch):
    main = _fresh_main(monkeypatch, budget_enabled=False)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    from fastapi.testclient import TestClient

    body = {"modality": "image_to_3d", "provider": "fal", "model": "trellis",
            "input": {"image": "https://cdn.example/sprite.png"}, "timeout_seconds": 900}
    assert TestClient(main.app).post("/media/generate", json=body).status_code == 202
    assert _CapturingFalClient.captured["init"]["start_timeout_seconds"] == 900


def _fal_sdk(monkeypatch, status_reply, result=None):
    """A fal_client stand-in whose AsyncClient records construction and close."""
    log = {"clients": 0, "closed": 0, "submit": None}

    class _Http:
        async def aclose(self):
            log["closed"] += 1
            if log.get("close_fails"):
                raise RuntimeError("transport already gone")

    class FakeAsyncClient:
        def __init__(self, *, key, default_timeout):
            log["clients"] += 1
            log["default_timeout"] = default_timeout

        @property
        async def _client(self):
            return _Http()

        async def submit(self, model, *, arguments, start_timeout):
            self.__dict__["_client"] = None  # what fal-client's cache leaves
            log["submit"] = start_timeout
            return SimpleNamespace(request_id="fal-1")

        async def status(self, model, request_id):
            return status_reply()

        async def result(self, model, request_id):
            return result

    monkeypatch.setitem(sys.modules, "fal_client", SimpleNamespace(AsyncClient=FakeAsyncClient))
    from fal_media_client import FalClient

    return FalClient, log


def test_one_sdk_client_per_fal_client_closed_on_exit_with_request_start_timeout(monkeypatch):
    class Completed:
        error = None

    fal, log = _fal_sdk(monkeypatch, Completed, {"model_mesh": {"url": "https://cdn/m.glb"}})

    async def run(start_timeout_seconds):
        async with fal(api_key="k", model="fal-ai/trellis", timeout_seconds=120,
                       start_timeout_seconds=start_timeout_seconds) as c:
            await c.submit_media_operation(modality="image_to_3d", input={"image": "https://x/a.png"})
            await c.get_media_operation(operation_id="fal-1", modality="image_to_3d")
            return c.start_timeout_seconds

    asyncio.run(run(900))
    # The queue-start limit follows the request; each HTTP call keeps 120 s.
    assert (log["submit"], log["default_timeout"]) == (900, 120.0)
    assert (log["clients"], log["closed"]) == (1, 1)
    # A short request never lowers it (the SDK rejects a start timeout <= 1 s),
    # and a failed close does not mask the submitted request.
    log["close_fails"] = True
    assert asyncio.run(run(1)) == 120.0 and log["submit"] == 120.0


def test_unknown_fal_queue_status_is_a_provider_error_not_a_caller_error(monkeypatch):
    def unknown():
        raise ValueError("Unknown status: PAUSED")

    _fal_sdk(monkeypatch, unknown)
    main = _fresh_main(monkeypatch, budget_enabled=False)
    operation = {"provider": "fal", "modality": "image_to_3d", "model": "fal-ai/trellis"}

    # The real FalClient over the SDK's ValueError: it used to answer 400.
    with pytest.raises(main.HTTPException) as raised:
        asyncio.run(main._poll_media_operation_or_raise(operation, "fal-1"))
    assert raised.value.status_code == 502


@pytest.mark.parametrize("result", [{"preview": {"url": "https://cdn/p.png"}}, {}])
def test_a_completed_3d_job_without_a_glb_is_flagged_and_stays_succeeded(monkeypatch, result):
    class Completed:
        error = None

    fal, _log = _fal_sdk(monkeypatch, Completed, result)
    polled = asyncio.run(fal(api_key="k", model="fal-ai/trellis").get_media_operation(
        operation_id="fal-1", modality="image_to_3d"))

    assert (polled["status"], polled["artifact_url"]) == ("succeeded", None)
    assert polled["provenance"]["glb_missing"] is True



def test_kill_switch_holds_with_budgets_off(monkeypatch):
    """reserve() returned before the kill-switch when budgets were off (the
    default), so a provider the operator disabled kept spending (2026-10-08
    run, cycle 23)."""
    main = _fresh_main(monkeypatch, budget_enabled=False, disabled_providers="fal")
    monkeypatch.setattr(main, "FalClient", _ExplodingFalClient, raising=False)
    from fastapi.testclient import TestClient

    resp = _submit(TestClient(main.app), consumer="acme")
    assert resp.status_code == 403
    assert "kill-switch" in resp.json()["detail"]


def test_an_over_long_attribution_header_is_rejected(monkeypatch):
    """The ledger columns are VARCHAR(255); an over-long header failed every
    ledger insert as a retryable 503 (cycle 23)."""
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _ExplodingFalClient, raising=False)
    from fastapi.testclient import TestClient

    resp = TestClient(main.app).post(
        "/media/generate",
        headers={"X-Atlas-Project": "p" * 256},
        json={"modality": "image_to_3d", "provider": "fal", "model": "trellis",
              "input": {"image": "https://cdn.example/sprite.png"}, "consumer": "acme"},
    )
    assert resp.status_code == 400



def test_a_nul_in_an_attribution_label_is_rejected_not_retried(monkeypatch):
    """Postgres rejects 0x00, so a NUL project failed every ledger write as a
    retryable 503, on submit and on /media/spend (2026-10-08 run, cycle 49)."""
    main = _fresh_main(monkeypatch, budget_enabled=True, default_cap=10.0)
    monkeypatch.setattr(main, "FalClient", _ExplodingFalClient, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    resp = client.post(
        "/media/generate",
        json={"modality": "image_to_3d", "provider": "fal", "model": "trellis",
              "input": {"image": "https://cdn.example/sprite.png"}, "consumer": "acme", "project": "a\u0000"},
    )
    assert resp.status_code == 400 and "control characters" in resp.json()["detail"]
    assert client.get("/media/spend", params={"consumer": "acme", "project": "a\x00b"}).status_code == 400
    resp = client.post(
        "/media/generate",
        json={"modality": "image", "provider": "fal", "model": "fal-ai/flux/dev\u0000",
              "input": {"prompt": "a cat", "provider_arguments": {"prompt": "a cat"}}, "consumer": "acme"},
    )
    assert resp.status_code == 400 and "model must not contain control characters" in resp.json()["detail"], resp.json()


def test_the_legacy_fal_route_honours_the_kill_switch(monkeypatch):
    """/comfyui/generate called FAL with no kill-switch check (cycle 23)."""
    monkeypatch.setenv("MEDIA_DISABLED_PROVIDERS", "fal")
    main = _fresh_fal_main(monkeypatch, fal_source="enabled", fal_api_key="fal-key")

    class UnexpectedFalClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("a disabled provider must not be called")

    class _NoComfyUIClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("ComfyUI must not be used")

    monkeypatch.setattr(main, "FalClient", UnexpectedFalClient)
    monkeypatch.setattr(main, "ComfyUIClient", _NoComfyUIClient)
    from fastapi.testclient import TestClient

    response = TestClient(main.app).post("/comfyui/generate", json={"prompt": "x", "wait_for_completion": True})
    assert response.status_code == 403


@pytest.mark.parametrize("path, body", [
    ("/comfyui/generate", {"prompt": "x", "wait_for_completion": False}),
    ("/comfyui/workflow", {"workflow": {"1": {"class_type": "KSampler", "inputs": {}}}}),
])
def test_the_legacy_comfyui_routes_honour_the_kill_switch(monkeypatch, path, body):
    """MEDIA_DISABLED_PROVIDERS=comfyui stopped /media/generate but not the
    legacy routes n8n and Open WebUI call (2026-10-08 run, cycle 29)."""
    monkeypatch.setenv("MEDIA_DISABLED_PROVIDERS", "comfyui")
    main = _fresh_fal_main(monkeypatch, fal_source="disabled")

    class NoComfyUI:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("a disabled provider must not be called")

    monkeypatch.setattr(main, "ComfyUIClient", NoComfyUI)
    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(path, json=body)
    assert response.status_code == 403, response.text


def test_a_streaming_plugin_route_authenticates_before_reading_the_body(monkeypatch):
    """The streaming exemption skipped the middleware, and FastAPI reads the
    whole body before the route's auth dependency: 40 MiB was read before
    a 401 (2026-10-08 run, cycle 46). The plugin's own mode now runs first."""
    import asyncio

    for var, default in (("KONG_URL", "http://kong-api-gateway:8000"), ("SUPABASE_SERVICE_KEY", "dummy-key"),
                         ("DATABASE_URL", "postgresql://x:x@localhost/x")):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)
    import main
    import plugin_seam
    from media_request_limit import RequestLimitMiddleware

    inventory = [
        {"route_prefix": "/inh", "status": "loaded", "auth": "inherit", "kong_route": {"request_buffering": False}},
        {"route_prefix": "/key", "status": "loaded", "auth": "key-auth", "kong_route": {"request_buffering": False}},
        {"route_prefix": "/open", "status": "loaded", "auth": "open", "kong_route": {"request_buffering": False}},
    ]
    assert plugin_seam.streaming_auth_modes(inventory) == {"/inh": "inherit", "/key": "key-auth", "/open": "open"}
    from fastapi import HTTPException

    async def deny(_scope):  # the suite's autouse fixture disables identity auth
        raise HTTPException(status_code=401, detail="no principal")

    monkeypatch.setitem(main._STREAMING_AUTHENTICATORS, "inherit", deny)
    policy = main._request_limit_policy(inventory)
    assert set(policy.streaming_auth) == {"/inh", "/key"}

    reached, read = [], []

    async def app(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def no_auth(_scope):
        return None

    middleware = RequestLimitMiddleware(app, policy=policy, authenticate=no_auth)

    async def receive():
        read.append(1)
        return {"type": "http.request", "body": b"x" * 1024, "more_body": False}

    async def call(path):
        sent = []

        async def send(message):
            sent.append(message)

        await middleware({"type": "http", "method": "POST", "path": path, "headers": [],
                          "query_string": b""}, receive, send)
        return sent[0]["status"]

    monkeypatch.setenv("BACKEND_KONG_API_KEY", "k")
    assert asyncio.run(call("/inh/upload")) == 401
    assert asyncio.run(call("/key/upload")) == 401
    assert read == [] and reached == []
    assert asyncio.run(call("/open/upload")) == 200 and reached == ["/open/upload"]


def test_a_delete_with_a_body_gets_the_default_envelope():
    """Only POST/PUT/PATCH were limited; FastAPI reads a Body parameter on
    DELETE too, before auth, so a multi-GB DELETE was buffered and then
    refused (2026-10-08 run, cycle 70)."""
    from media_request_limit import LimitPolicy, RequestLimitMiddleware
    from tests.test_media_request_limit import _scope

    reached, read, sent = [], [], []

    async def app(scope, receive, send):
        reached.append(scope["method"])

    async def receive():
        read.append(1)
        return {"type": "http.request", "body": b"x", "more_body": False}

    async def send(message):
        sent.append(message)

    async def no_auth(_scope):
        return None

    middleware = RequestLimitMiddleware(app, policy=LimitPolicy(rules=[]), authenticate=no_auth)
    scope = _scope(method="DELETE", path="/items/bulk", content_length=40 * 1024 * 1024)
    asyncio.run(middleware(scope, receive, send))
    assert sent[0]["status"] == 413 and read == [] and reached == []
    # A chunked DELETE declares its body without a Content-Length (cycle 74).
    chunked = _scope(method="DELETE", path="/items/bulk")
    chunked["headers"] = [*chunked["headers"], (b"transfer-encoding", b"chunked")]
    big = {"type": "http.request", "body": b"x" * (17 * 1024 * 1024), "more_body": False}

    async def receive_big():
        return big

    async def reading_app(scope, receive, send):
        while (await receive()).get("more_body"):
            pass

    sent.clear()
    asyncio.run(RequestLimitMiddleware(reading_app, policy=LimitPolicy(rules=[]), authenticate=no_auth)(chunked, receive_big, send))
    assert sent[0]["status"] == 413  # the counter trips mid-stream; the app then sees a disconnect
    # A DELETE without a body is not touched.
    asyncio.run(middleware(_scope(method="DELETE", path="/items/1"), receive, send))
    assert reached == ["DELETE"]
