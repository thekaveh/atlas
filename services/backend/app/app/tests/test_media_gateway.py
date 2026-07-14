from __future__ import annotations

import importlib
import json
import os
import sys
import types

import pytest


def _stub_required_env(monkeypatch):
    for var, default in (
        ("KONG_URL", "http://kong-api-gateway:8000"),
        ("SUPABASE_SERVICE_KEY", "dummy-key"),
        ("DATABASE_URL", "postgresql://x:x@localhost/x"),
    ):
        if not os.environ.get(var):
            monkeypatch.setenv(var, default)


def _fresh_main(monkeypatch, *, fal_source: str = "enabled", fal_api_key: str = "fal-key"):
    _stub_required_env(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", fal_source)
    monkeypatch.setenv("FAL_API_KEY", fal_api_key)
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux/dev")
    monkeypatch.setenv("FAL_TIMEOUT_SECONDS", "120")
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def test_media_generate_submits_fal_image_operation_without_exposing_key(monkeypatch):
    main = _fresh_main(monkeypatch)
    calls = {}

    class FakeFalClient:
        def __init__(self, *args, **kwargs):
            calls["init"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def submit_media_operation(self, **kwargs):
            calls["submit"] = kwargs
            return {
                "operation_id": "fal-req-1",
                "status": "submitted",
                "provider": "fal",
                "model": "fal-ai/flux/dev",
                "modality": "image",
                "artifact_url": None,
                "artifacts": [],
                "cost_usd": None,
                "license": "fal/provider-terms",
                "provenance": {"provider_request_id": "fal-req-1"},
                "raw": {"request_id": "fal-req-1"},
            }

    monkeypatch.setattr(main, "FalClient", FakeFalClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image",
            "provider": "fal",
            "model": "fal-ai/flux/dev",
            "input": {
                "prompt": "orbital blue glass library",
                "negative_prompt": "low detail",
                "width": 768,
                "height": 512,
                "steps": 28,
                "cfg": 3.5,
                "seed": 42,
            },
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == "fal-req-1"
    assert body["status"] == "submitted"
    assert body["provider"] == "fal"
    assert body["model"] == "fal-ai/flux/dev"
    assert body["modality"] == "image"
    assert body["cost_usd"] is None
    assert body["license"] == "fal/provider-terms"
    assert body["operation_url"] == "/media/operations/fal-req-1"
    assert "fal-key" not in json.dumps(body)
    assert calls["init"] == {"api_key": "fal-key", "model": "fal-ai/flux/dev"}
    assert calls["submit"]["modality"] == "image"
    assert calls["submit"]["input"]["prompt"] == "orbital blue glass library"


def test_media_operation_poll_normalizes_fal_result(monkeypatch):
    main = _fresh_main(monkeypatch)

    class FakeFalClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def submit_media_operation(self, **kwargs):
            return {
                "operation_id": "fal-req-2",
                "status": "submitted",
                "provider": "fal",
                "model": "fal-ai/flux/dev",
                "modality": "image",
                "artifact_url": None,
                "artifacts": [],
                "cost_usd": None,
                "license": "fal/provider-terms",
                "provenance": {"provider_request_id": "fal-req-2"},
                "raw": {"request_id": "fal-req-2"},
            }

        async def get_media_operation(self, *, operation_id, modality):
            assert operation_id == "fal-req-2"
            assert modality == "image"
            return {
                "operation_id": "fal-req-2",
                "status": "succeeded",
                "provider": "fal",
                "model": "fal-ai/flux/dev",
                "modality": "image",
                "artifact_url": "https://cdn.example/fal.png",
                "artifacts": [
                    {
                        "url": "https://cdn.example/fal.png",
                        "content_type": "image/png",
                    }
                ],
                "cost_usd": None,
                "license": "fal/provider-terms",
                "provenance": {"provider_request_id": "fal-req-2"},
                "raw": {"images": [{"url": "https://cdn.example/fal.png"}]},
            }

    monkeypatch.setattr(main, "FalClient", FakeFalClient, raising=False)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = client.post(
        "/media/generate",
        json={
            "modality": "image",
            "provider": "fal",
            "input": {"prompt": "blue orbital refinery"},
        },
    )
    assert submitted.status_code == 202

    response = client.get("/media/operations/fal-req-2")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["artifact_url"] == "https://cdn.example/fal.png"
    assert body["artifacts"][0]["content_type"] == "image/png"
    assert body["cost_usd"] is None
    assert body["model"] == "fal-ai/flux/dev"
    assert body["license"] == "fal/provider-terms"
    assert "fal-key" not in json.dumps(body)


def test_media_generate_rejects_unsupported_modality_before_provider_call(monkeypatch):
    main = _fresh_main(monkeypatch)

    class ExplodingFalClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unsupported modality must not initialize FalClient")

    monkeypatch.setattr(main, "FalClient", ExplodingFalClient, raising=False)

    from fastapi.testclient import TestClient

    # `video` remains unsupported (image + image_to_3d are the only routes).
    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "video",
            "provider": "fal",
            "input": {"prompt": "convert this image"},
        },
    )

    assert response.status_code == 400
    assert "Unsupported media route" in response.json()["detail"]


def test_media_generate_requires_enabled_fal_source_and_key(monkeypatch):
    main = _fresh_main(monkeypatch, fal_source="disabled", fal_api_key="")

    from fastapi.testclient import TestClient

    disabled = TestClient(main.app).post(
        "/media/generate",
        json={"modality": "image", "provider": "fal", "input": {"prompt": "x"}},
    )
    assert disabled.status_code == 503
    assert "FAL_SOURCE=enabled" in disabled.json()["detail"]

    main = _fresh_main(monkeypatch, fal_source="enabled", fal_api_key="")
    missing_key = TestClient(main.app).post(
        "/media/generate",
        json={"modality": "image", "provider": "fal", "input": {"prompt": "x"}},
    )
    assert missing_key.status_code == 503
    assert "FAL_API_KEY" in missing_key.json()["detail"]


def test_fal_client_submits_and_polls_queue_operations(monkeypatch):
    captured = {}

    def fake_submit(model, *, arguments):
        captured["submit"] = {"model": model, "arguments": arguments}
        return types.SimpleNamespace(request_id="fal-queue-1")

    def fake_status(model, request_id):
        captured["status"] = {"model": model, "request_id": request_id}
        return types.SimpleNamespace(status="COMPLETED")

    def fake_result(model, request_id):
        captured["result"] = {"model": model, "request_id": request_id}
        return {
            "images": [
                {
                    "url": "https://cdn.example/result.jpeg",
                    "content_type": "image/jpeg",
                }
            ]
        }

    monkeypatch.setitem(
        sys.modules,
        "fal_client",
        types.SimpleNamespace(
            submit=fake_submit,
            status=fake_status,
            result=fake_result,
        ),
    )

    from fal_media_client import FalClient
    import asyncio

    client = FalClient(api_key="fal-key", model="fal-ai/flux/dev")
    submitted = asyncio.run(
        client.submit_media_operation(
            modality="image",
            input={
                "prompt": "neon data center",
                "width": 1024,
                "height": 768,
                "steps": 28,
                "cfg": 3.5,
                "seed": 123,
            },
        )
    )
    polled = asyncio.run(client.get_media_operation(operation_id="fal-queue-1", modality="image"))

    assert submitted["operation_id"] == "fal-queue-1"
    assert submitted["status"] == "submitted"
    assert polled["status"] == "succeeded"
    assert polled["artifact_url"] == "https://cdn.example/result.jpeg"
    assert captured["submit"]["model"] == "fal-ai/flux/dev"
    assert captured["submit"]["arguments"]["prompt"] == "neon data center"
    assert captured["submit"]["arguments"]["image_size"] == {"width": 1024, "height": 768}
    assert captured["status"] == {"model": "fal-ai/flux/dev", "request_id": "fal-queue-1"}
    assert captured["result"] == {"model": "fal-ai/flux/dev", "request_id": "fal-queue-1"}


# --- image_to_3d route wiring (#340) ----------------------------------------


class _CapturingFalClient:
    """Shared fake that records submit/poll kwargs for image_to_3d route tests."""

    captured: dict = {}

    def __init__(self, *args, **kwargs):
        _CapturingFalClient.captured["init"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def submit_media_operation(self, **kwargs):
        _CapturingFalClient.captured["submit"] = kwargs
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
            "provenance": {"provider_request_id": "fal-3d-9", "modality": "image_to_3d"},
            "raw": {"request_id": "fal-3d-9"},
        }

    async def get_media_operation(self, *, operation_id, modality):
        assert modality == "image_to_3d"
        return {
            "operation_id": operation_id,
            "status": "succeeded",
            "provider": "fal",
            "model": "fal-ai/trellis",
            "modality": "image_to_3d",
            "artifact_url": "https://cdn.example/model.glb",
            "artifacts": [
                {
                    "url": "https://cdn.example/model.glb",
                    "content_type": "model/gltf-binary",
                    "role": "model_glb",
                    "source_key": "model_mesh",
                }
            ],
            "cost_usd": 0.05,
            "license": "MIT",
            "provenance": {"provider_request_id": operation_id, "modality": "image_to_3d"},
            "raw": {"model_mesh": {"url": "https://cdn.example/model.glb"}},
        }


def test_media_generate_image_to_3d_url_input_normalizes_model(monkeypatch):
    main = _fresh_main(monkeypatch)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
            "provider": "fal",
            "model": "trellis",  # alias must resolve to the canonical id
            "input": {"image": "https://cdn.example/sprite.png", "seed": 3},
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == "fal-3d-9"
    assert body["modality"] == "image_to_3d"
    assert body["license"] == "MIT"
    assert body["cost_usd"] == 0.05
    assert body["operation_url"] == "/media/operations/fal-3d-9"
    # Alias resolved to the canonical endpoint id before submit.
    assert _CapturingFalClient.captured["submit"]["model"] == "fal-ai/trellis"
    # URL input passes through untouched.
    assert (
        _CapturingFalClient.captured["submit"]["input"]["image"]
        == "https://cdn.example/sprite.png"
    )
    assert "fal-key" not in json.dumps(body)


def test_media_generate_image_to_3d_requires_image(monkeypatch):
    main = _fresh_main(monkeypatch)

    class ExplodingFalClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("missing image must not initialize FalClient")

    monkeypatch.setattr(main, "FalClient", ExplodingFalClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
            "provider": "fal",
            "input": {"prompt": "no image here"},
        },
    )

    assert response.status_code == 400
    assert "image" in response.json()["detail"]


def test_media_generate_image_to_3d_unknown_model_rejected(monkeypatch):
    main = _fresh_main(monkeypatch)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
            "provider": "fal",
            "model": "fal-ai/not-a-real-3d-model",
            "input": {"image": "https://cdn.example/x.png"},
        },
    )

    assert response.status_code == 400
    assert "Unknown image_to_3d model" in response.json()["detail"]


def test_media_generate_image_to_3d_hosts_datauri_for_tripo(monkeypatch):
    main = _fresh_main(monkeypatch)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    # Avoid Pillow: treat the input as opaque so only the hosting path runs.
    import media_input

    monkeypatch.setattr(media_input, "has_transparency", lambda data: False)
    monkeypatch.setattr(
        main,
        "_media_input_uploader",
        lambda data, content_type, key: "https://storage.example/media-inputs/x.png",
    )

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
            "provider": "fal",
            "model": "tripo",  # needs a hosted URL — rejects data URIs
            "input": {"image": "data:image/png;base64,aGVsbG8="},
        },
    )

    assert response.status_code == 202
    # The data URI was replaced with the hosted URL before submit.
    assert (
        _CapturingFalClient.captured["submit"]["input"]["image"]
        == "https://storage.example/media-inputs/x.png"
    )
    assert (
        _CapturingFalClient.captured["submit"]["model"]
        == "fal-ai/tripo3d/tripo/v2.5/image-to-3d"
    )


def test_media_generate_image_to_3d_storage_failure_returns_503(monkeypatch):
    main = _fresh_main(monkeypatch)
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)
    import media_input

    monkeypatch.setattr(media_input, "has_transparency", lambda data: False)

    def boom(data, content_type, key):
        raise RuntimeError("storage down")

    monkeypatch.setattr(main, "_media_input_uploader", boom)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
            "provider": "fal",
            "model": "tripo",
            "input": {"image": "data:image/png;base64,aGVsbG8="},
        },
    )

    assert response.status_code == 503


def test_media_generate_image_to_3d_missing_key_does_not_host(monkeypatch):
    # FAL enabled but no key: the request must 503 before any storage write.
    main = _fresh_main(monkeypatch, fal_source="enabled", fal_api_key="")
    monkeypatch.setattr(
        main,
        "_media_input_uploader",
        lambda *a, **k: pytest.fail("must not host input when the API key is missing"),
    )
    import media_input

    monkeypatch.setattr(
        media_input,
        "has_transparency",
        lambda data: pytest.fail("must not inspect input when the API key is missing"),
    )

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
            "provider": "fal",
            "model": "tripo",
            "input": {"image": "data:image/png;base64,aGVsbG8="},
        },
    )

    assert response.status_code == 503
    assert "FAL_API_KEY" in response.json()["detail"]


def test_media_operation_poll_returns_normalized_glb(monkeypatch):
    main = _fresh_main(monkeypatch)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = client.post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
            "provider": "fal",
            "input": {"image": "https://cdn.example/sprite.png"},
        },
    )
    assert submitted.status_code == 202

    polled = client.get("/media/operations/fal-3d-9")
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "succeeded"
    assert body["artifact_url"] == "https://cdn.example/model.glb"
    assert body["artifacts"][0]["role"] == "model_glb"
    assert body["license"] == "MIT"


def test_media_operation_times_out(monkeypatch):
    main = _fresh_main(monkeypatch)
    _CapturingFalClient.captured = {}
    monkeypatch.setattr(main, "FalClient", _CapturingFalClient, raising=False)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = client.post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
            "provider": "fal",
            "input": {"image": "https://cdn.example/sprite.png"},
        },
    )
    assert submitted.status_code == 202

    # Force the operation past its timeout budget.
    main.MEDIA_OPERATIONS["fal-3d-9"]["created_at"] = 0.0
    main.MEDIA_OPERATIONS["fal-3d-9"]["timeout_seconds"] = 1

    polled = client.get("/media/operations/fal-3d-9")
    assert polled.status_code == 200
    assert polled.json()["status"] == "timeout"


# ── #518: POST /media/operations/{id}/cancel ────────────────────────────────
def _seed_inflight_operation(main, operation_id="fal-req-cxl", budget_tracked=True):
    main.MEDIA_OPERATIONS[operation_id] = {
        "provider": "fal",
        "modality": "image",
        "model": "fal-ai/flux/dev",
        "created_at": __import__("time").monotonic(),
        "timeout_seconds": 120,
        "last_payload": {
            "operation_id": operation_id,
            "status": "in_progress",
            "provider": "fal",
            "model": "fal-ai/flux/dev",
            "modality": "image",
            "artifact_url": None,
            "artifacts": [],
            "cost_usd": None,
            "license": "fal/provider-terms",
            "provenance": {"provider_request_id": operation_id},
            "raw": {},
        },
        "consumer": "default",
        "project": None,
        "budget_tracked": budget_tracked,
        "reconciled": False,
    }
    return operation_id


class _ReconcileSpy:
    def __init__(self):
        self.calls = []

    async def reconcile(self, **kwargs):
        self.calls.append(kwargs)


class _CancelFalClient:
    """FalClient stub whose provider cancel outcome is configurable."""

    result = True
    constructions = 0

    def __init__(self, *args, **kwargs):
        type(self).constructions += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def cancel_media_operation(self, **kwargs):
        if isinstance(type(self).result, Exception):
            raise type(self).result
        return type(self).result


def test_media_cancel_unknown_operation_returns_404(monkeypatch):
    main = _fresh_main(monkeypatch)
    from fastapi.testclient import TestClient

    response = TestClient(main.app).post("/media/operations/nope/cancel")
    assert response.status_code == 404


def test_media_cancel_marks_terminal_releases_budget_and_propagates(monkeypatch):
    """#518 core: cancel → terminal `cancelled`, ledger reconciled with
    status=cancelled (reservation release, #342), provider cancel delivered."""
    main = _fresh_main(monkeypatch)
    op_id = _seed_inflight_operation(main)
    spy = _ReconcileSpy()
    monkeypatch.setattr(main, "MEDIA_BUDGET_ENGINE", spy, raising=False)
    _CancelFalClient.result = True
    monkeypatch.setattr(main, "FalClient", _CancelFalClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(f"/media/operations/{op_id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["provenance"]["provider_cancelled"] is True
    # Ledger settled exactly once with the spend-releasing status.
    assert len(spy.calls) == 1
    assert spy.calls[0]["status"] == "cancelled"
    assert spy.calls[0]["operation_id"] == op_id
    assert main.MEDIA_OPERATIONS[op_id]["reconciled"] is True


def test_media_cancel_is_idempotent_409_when_already_terminal(monkeypatch):
    main = _fresh_main(monkeypatch)
    op_id = _seed_inflight_operation(main)
    spy = _ReconcileSpy()
    monkeypatch.setattr(main, "MEDIA_BUDGET_ENGINE", spy, raising=False)
    _CancelFalClient.result = True
    monkeypatch.setattr(main, "FalClient", _CancelFalClient, raising=False)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert client.post(f"/media/operations/{op_id}/cancel").status_code == 200
    second = client.post(f"/media/operations/{op_id}/cancel")
    assert second.status_code == 409
    # Reconcile ran once, not twice.
    assert len(spy.calls) == 1


def test_media_poll_after_cancel_is_stable_and_skips_provider(monkeypatch):
    """AC: GET reports status=cancelled after cancel — and never re-polls the
    provider (the payload must not flip back to the provider's status)."""
    main = _fresh_main(monkeypatch)
    op_id = _seed_inflight_operation(main, budget_tracked=False)
    _CancelFalClient.result = True
    _CancelFalClient.constructions = 0
    monkeypatch.setattr(main, "FalClient", _CancelFalClient, raising=False)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert client.post(f"/media/operations/{op_id}/cancel").status_code == 200
    constructions_after_cancel = _CancelFalClient.constructions

    poll = client.get(f"/media/operations/{op_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] == "cancelled"
    # No new FalClient was constructed for the poll — terminal is stable.
    assert _CancelFalClient.constructions == constructions_after_cancel


def test_media_cancel_degrades_when_provider_cancel_fails(monkeypatch):
    """Best-effort contract: an undeliverable provider cancel still cancels
    server-side and releases the budget."""
    main = _fresh_main(monkeypatch)
    op_id = _seed_inflight_operation(main)
    spy = _ReconcileSpy()
    monkeypatch.setattr(main, "MEDIA_BUDGET_ENGINE", spy, raising=False)
    _CancelFalClient.result = RuntimeError("fal queue unreachable")
    monkeypatch.setattr(main, "FalClient", _CancelFalClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(f"/media/operations/{op_id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["provenance"]["provider_cancelled"] is False
    assert len(spy.calls) == 1


def test_media_cancel_works_server_side_when_fal_disabled(monkeypatch):
    """Even with the provider unavailable (FAL_SOURCE=disabled) the op is
    cancelled server-side and stops counting against spend."""
    main = _fresh_main(monkeypatch, fal_source="disabled", fal_api_key="")
    op_id = _seed_inflight_operation(main)
    spy = _ReconcileSpy()
    monkeypatch.setattr(main, "MEDIA_BUDGET_ENGINE", spy, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(f"/media/operations/{op_id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["provenance"]["provider_cancelled"] is False
    assert len(spy.calls) == 1


# ── #519: provider=comfyui route parity (submit/poll/cancel/img2img) ───────
def _fresh_main_comfyui(monkeypatch, *, comfyui_enabled: bool = True):
    """Same stubbing as _fresh_main, plus the source-aware ComfyUI gate
    (COMFYUI_SOURCE must be a non-disabled source, mirroring FAL_SOURCE)."""
    main = _fresh_main(monkeypatch)
    monkeypatch.setenv("COMFYUI_SOURCE", "container-cpu" if comfyui_enabled else "disabled")
    return main


class _FakeComfyUIMediaClient:
    """ComfyUIMediaClient stub capturing submit/poll/cancel calls.

    Constructed as ``ComfyUIMediaClient(model=...)`` (no api_key — local).
    """

    submit_kwargs = None
    poll_kwargs = None
    cancel_kwargs = None
    submit_payload = None
    poll_payload = None
    cancel_result = True

    def __init__(self, *args, **kwargs):
        self.model = kwargs.get("model")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def submit_media_operation(self, **kwargs):
        type(self).submit_kwargs = kwargs
        return type(self).submit_payload or {
            "operation_id": "comfyui-prompt-1",
            "status": "queued",
            "provider": "comfyui",
            "model": kwargs.get("model", "krea2-turbo-bf16"),
            "modality": kwargs.get("modality", "image"),
            "artifact_url": None,
            "artifacts": [],
            "cost_usd": 0.0,
            "license": "local/self-hosted",
            "provenance": {
                "provider_request_id": "comfyui-prompt-1",
                "modality": "image",
                "cost_basis": "local_zero",
            },
            "raw": {"prompt_id": "comfyui-prompt-1"},
        }

    async def get_media_operation(self, **kwargs):
        type(self).poll_kwargs = kwargs
        return type(self).poll_payload or {
            "operation_id": kwargs["operation_id"],
            "status": "succeeded",
            "provider": "comfyui",
            "model": "krea2-turbo-bf16",
            "modality": "image",
            "artifact_url": "/comfyui/image/out.png?folder_type=output",
            "artifacts": [
                {
                    "url": "/comfyui/image/out.png?folder_type=output",
                    "role": "image",
                    "content_type": "image/png",
                    "filename": "out.png",
                }
            ],
            "cost_usd": 0.0,
            "license": "local/self-hosted",
            "provenance": {"provider_request_id": kwargs["operation_id"], "cost_basis": "local_zero"},
            "raw": {"history": {}},
        }

    async def cancel_media_operation(self, **kwargs):
        type(self).cancel_kwargs = kwargs
        result = type(self).cancel_result
        if isinstance(result, Exception):
            raise result
        return result


def _reset_comfyui_fake():
    # Class-level capture must reset between tests.
    for attr in ("submit_kwargs", "poll_kwargs", "cancel_kwargs"):
        setattr(_FakeComfyUIMediaClient, attr, None)
    _FakeComfyUIMediaClient.submit_payload = None
    _FakeComfyUIMediaClient.poll_payload = None
    _FakeComfyUIMediaClient.cancel_result = True


def test_comfyui_submit_returns_202_with_local_zero_cost_envelope(monkeypatch):
    _reset_comfyui_fake()
    main = _fresh_main_comfyui(monkeypatch)
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image",
            "provider": "comfyui",
            "model": "krea2-turbo-bf16",
            "input": {"prompt": "blue glass observatory", "width": 1024, "height": 1024},
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == "comfyui-prompt-1"
    assert body["status"] == "queued"
    assert body["provider"] == "comfyui"
    assert body["model"] == "krea2-turbo-bf16"
    assert body["cost_usd"] == 0.0  # local generation is free, recorded
    assert body["license"] == "local/self-hosted"
    assert body["provenance"]["cost_basis"] == "local_zero"
    assert body["operation_url"] == "/media/operations/comfyui-prompt-1"
    # The client was constructed with the model only (no provider key).
    assert _FakeComfyUIMediaClient.submit_kwargs["modality"] == "image"
    assert _FakeComfyUIMediaClient.submit_kwargs["model"] == "krea2-turbo-bf16"
    assert _FakeComfyUIMediaClient.submit_kwargs["input_payload"]["prompt"] == "blue glass observatory"


def test_comfyui_submit_503_when_comfyui_not_enabled(monkeypatch):
    _reset_comfyui_fake()
    main = _fresh_main_comfyui(monkeypatch, comfyui_enabled=False)
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={"modality": "image", "provider": "comfyui", "model": "x", "input": {"prompt": "p"}},
    )
    assert response.status_code == 503
    assert "COMFYUI_SOURCE" in response.json()["detail"]


def test_comfyui_submit_503_when_source_disabled_despite_enabled_flag(monkeypatch):
    """The gate is source-aware: COMFYUI_SOURCE=disabled → 503 even though
    compose's COMFYUI_ENABLED stays true (the static flag alone is not trusted)."""
    _reset_comfyui_fake()
    main = _fresh_main(monkeypatch)
    monkeypatch.setenv("COMFYUI_ENABLED", "true")  # compose hardcodes this
    monkeypatch.setenv("COMFYUI_SOURCE", "disabled")
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={"modality": "image", "provider": "comfyui", "model": "x", "input": {"prompt": "p"}},
    )
    assert response.status_code == 503


def test_comfyui_submit_400_without_model(monkeypatch):
    _reset_comfyui_fake()
    main = _fresh_main_comfyui(monkeypatch)
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={"modality": "image", "provider": "comfyui", "input": {"prompt": "p"}},
    )
    assert response.status_code == 400
    assert "model" in response.json()["detail"].lower()


def test_comfyui_poll_normalizes_succeeded_with_proxy_artifact(monkeypatch):
    _reset_comfyui_fake()
    main = _fresh_main_comfyui(monkeypatch)
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = client.post(
        "/media/generate",
        json={"modality": "image", "provider": "comfyui", "model": "krea2-turbo-bf16", "input": {"prompt": "p"}},
    )
    assert submitted.status_code == 202
    op_id = submitted.json()["operation_id"]

    polled = client.get(f"/media/operations/{op_id}")
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "succeeded"
    assert body["artifact_url"] == "/comfyui/image/out.png?folder_type=output"
    assert body["artifacts"][0]["filename"] == "out.png"
    assert body["cost_usd"] == 0.0
    assert _FakeComfyUIMediaClient.poll_kwargs["operation_id"] == op_id


def test_comfyui_cancel_marks_terminal_and_propagates(monkeypatch):
    _reset_comfyui_fake()
    main = _fresh_main_comfyui(monkeypatch)
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = client.post(
        "/media/generate",
        json={"modality": "image", "provider": "comfyui", "model": "krea2-turbo-bf16", "input": {"prompt": "p"}},
    )
    op_id = submitted.json()["operation_id"]

    response = client.post(f"/media/operations/{op_id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    # provider cancel was delivered to ComfyUI (queue delete + interrupt).
    assert body["provenance"]["provider_cancelled"] is True
    assert _FakeComfyUIMediaClient.cancel_kwargs["operation_id"] == op_id


def test_comfyui_cancel_on_terminal_returns_409(monkeypatch):
    _reset_comfyui_fake()
    main = _fresh_main_comfyui(monkeypatch)
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    submitted = client.post(
        "/media/generate",
        json={"modality": "image", "provider": "comfyui", "model": "krea2-turbo-bf16", "input": {"prompt": "p"}},
    )
    op_id = submitted.json()["operation_id"]
    # Poll once to settle it as succeeded (terminal), then cancel must 409.
    client.get(f"/media/operations/{op_id}")
    response = client.post(f"/media/operations/{op_id}/cancel")
    assert response.status_code == 409


def test_comfyui_img2img_forwards_init_image_and_strength(monkeypatch):
    _reset_comfyui_fake()
    main = _fresh_main_comfyui(monkeypatch)
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image",
            "provider": "comfyui",
            "model": "sdxl_base.safetensors",
            "input": {
                "prompt": "remix",
                "image_url": "https://x/init.png",
                "strength": 0.5,
            },
        },
    )
    assert response.status_code == 202
    # img2img contract (#453 parity): init image + strength reach the client
    # under the same accepted keys.
    forwarded = _FakeComfyUIMediaClient.submit_kwargs["input_payload"]
    assert forwarded["image_url"] == "https://x/init.png"
    assert forwarded["strength"] == 0.5


def test_comfyui_with_image_to_3d_modality_returns_400(monkeypatch):
    """ComfyUI provider is image-only; image_to_3d stays on fal."""
    _reset_comfyui_fake()
    main = _fresh_main_comfyui(monkeypatch)
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={"modality": "image_to_3d", "provider": "comfyui", "model": "x", "input": {"image": "i"}},
    )
    assert response.status_code == 400


def test_unknown_media_provider_returns_400(monkeypatch):
    _reset_comfyui_fake()
    main = _fresh_main_comfyui(monkeypatch)
    monkeypatch.setattr(main, "ComfyUIMediaClient", _FakeComfyUIMediaClient, raising=False)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/media/generate",
        json={"modality": "image", "provider": "acme", "model": "x", "input": {"prompt": "p"}},
    )
    assert response.status_code == 400
    assert "provider" in response.json()["detail"].lower()
