from __future__ import annotations

import asyncio
import importlib
import os
import sys


def _stub_required_env(monkeypatch):
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def _fresh_main(monkeypatch, *, budget_enabled, default_cap="", disabled_providers=""):
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


def test_attach_failure_does_not_500_and_releases_reservation(monkeypatch):
    # The provider call succeeded; a ledger re-key hiccup must not 500 the
    # request, and it must free the temp-id reservation (not orphan the budget).
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
    # Reservation was released best-effort; budget is not permanently held.
    assert spend["reserved_usd"] == 0.0
    operation = asyncio.run(main.MEDIA_OPERATION_STORE.get("fal-3d-9"))
    assert operation["budget_tracked"] is False


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
    assert response.json()["detail"] == "Media operation state store is unavailable"
    spend = client.get("/media/spend", params={"consumer": "acme"}).json()
    assert spend["records"] == []


def test_state_persistence_failure_retries_then_cancels_provider(monkeypatch):
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
        "provider_cancelled": True,
        "manual_reconciliation_required": False,
    }
    assert _CapturingFalClient.captured["cancel"] == {
        "operation_id": "fal-3d-9",
        "modality": "image_to_3d",
    }
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
    assert detail["provider_cancelled"] is False
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
