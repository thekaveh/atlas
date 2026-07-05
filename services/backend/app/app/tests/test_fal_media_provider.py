from __future__ import annotations

import importlib
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


def _fresh_main(monkeypatch, *, fal_source: str, fal_api_key: str = ""):
    _stub_required_env(monkeypatch)
    monkeypatch.setenv("FAL_SOURCE", fal_source)
    monkeypatch.setenv("FAL_API_KEY", fal_api_key)
    monkeypatch.setenv("FAL_MODEL", "fal-ai/flux/dev")
    monkeypatch.setenv("FAL_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("FAL_OUTPUT_FORMAT", "jpeg")
    monkeypatch.setenv("FAL_ENABLE_SAFETY_CHECKER", "true")
    sys.modules.pop("main", None)
    return importlib.import_module("main")


class _ExplodingComfyUIClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("ComfyUIClient should not be used for FAL-backed generation")


def test_fal_enabled_routes_simple_generation_to_fal_client(monkeypatch):
    main = _fresh_main(monkeypatch, fal_source="enabled", fal_api_key="fal-key")
    calls = {}

    class FakeFalClient:
        def __init__(self, *args, **kwargs):
            calls["init"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def generate_simple_image(self, **kwargs):
            calls["generate"] = kwargs
            return {
                "success": True,
                "prompt_id": "fal-request-1",
                "client_id": "fal",
                "outputs": {
                    "images": [
                        {
                            "url": "https://cdn.example/fal.jpg",
                            "content_type": "image/jpeg",
                        }
                    ]
                },
                "parameters": {
                    "provider": "fal",
                    **kwargs,
                },
            }

    monkeypatch.setattr(main, "FalClient", FakeFalClient, raising=False)
    monkeypatch.setattr(main, "ComfyUIClient", _ExplodingComfyUIClient)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/comfyui/generate",
        json={
            "prompt": "orbital blue glass library",
            "negative_prompt": "low detail",
            "width": 768,
            "height": 512,
            "steps": 28,
            "cfg": 3.5,
            "seed": 42,
            "wait_for_completion": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["prompt_id"] == "fal-request-1"
    assert body["client_id"] == "fal"
    assert body["data"]["provider"] == "fal"
    assert body["data"]["outputs"]["images"][0]["url"] == "https://cdn.example/fal.jpg"
    assert calls["generate"]["prompt"] == "orbital blue glass library"
    assert calls["generate"]["negative_prompt"] == "low detail"
    assert calls["generate"]["width"] == 768
    assert calls["generate"]["height"] == 512
    assert calls["generate"]["steps"] == 28
    assert calls["generate"]["cfg"] == 3.5
    assert calls["generate"]["seed"] == 42


def test_fal_enabled_without_key_returns_clear_503(monkeypatch):
    main = _fresh_main(monkeypatch, fal_source="enabled", fal_api_key="")
    monkeypatch.setattr(main, "ComfyUIClient", _ExplodingComfyUIClient)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/comfyui/generate",
        json={"prompt": "blueprint moonbase"},
    )

    assert response.status_code == 503
    assert "FAL_API_KEY" in response.json()["detail"]


def test_fal_disabled_preserves_comfyui_generation_without_key(monkeypatch):
    main = _fresh_main(monkeypatch, fal_source="disabled", fal_api_key="")

    class FakeComfyUIClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def generate_simple_image(self, **kwargs):
            return {
                "success": True,
                "prompt_id": "comfy-1",
                "client_id": "comfy-client",
                "parameters": kwargs,
            }

        async def wait_for_completion(self, prompt_id):
            return {
                "success": True,
                "outputs": {"7": {"images": [{"filename": "comfy.png"}]}},
            }

    monkeypatch.setattr(main, "ComfyUIClient", FakeComfyUIClient)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/comfyui/generate",
        json={"prompt": "local render", "wait_for_completion": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["prompt_id"] == "comfy-1"
    assert body["client_id"] == "comfy-client"


@pytest.mark.asyncio
async def test_fal_client_constructs_subscribe_request(monkeypatch):
    spec = importlib.util.find_spec("fal_media_client")
    assert spec is not None, "backend must provide fal_media_client.FalClient"

    captured = {}

    def fake_subscribe(model, *, arguments):
        captured["model"] = model
        captured["arguments"] = arguments
        return {
            "request_id": "req-fal-42",
            "images": [
                {
                    "url": "https://cdn.example/image.jpeg",
                    "width": 1024,
                    "height": 768,
                    "content_type": "image/jpeg",
                }
            ],
            "seed": 123,
            "prompt": "neon data center",
        }

    monkeypatch.setitem(
        sys.modules,
        "fal_client",
        types.SimpleNamespace(subscribe=fake_subscribe),
    )

    from fal_media_client import FalClient

    result = await FalClient(
        api_key="fal-key",
        model="fal-ai/flux/dev",
        output_format="jpeg",
        enable_safety_checker=True,
    ).generate_simple_image(
        prompt="neon data center",
        negative_prompt="",
        width=1024,
        height=768,
        steps=28,
        cfg=3.5,
        seed=123,
    )

    assert captured == {
        "model": "fal-ai/flux/dev",
        "arguments": {
            "prompt": "neon data center",
            "image_size": {"width": 1024, "height": 768},
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "seed": 123,
            "num_images": 1,
            "enable_safety_checker": True,
            "output_format": "jpeg",
        },
    }
    assert result["success"] is True
    assert result["prompt_id"] == "req-fal-42"
    assert result["outputs"]["images"][0]["url"] == "https://cdn.example/image.jpeg"
