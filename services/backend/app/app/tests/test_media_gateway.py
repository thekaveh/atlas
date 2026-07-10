from __future__ import annotations

import importlib
import json
import os
import sys
import types


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

    response = TestClient(main.app).post(
        "/media/generate",
        json={
            "modality": "image_to_3d",
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
