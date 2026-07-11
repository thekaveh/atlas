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
