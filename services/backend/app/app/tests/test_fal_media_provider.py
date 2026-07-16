from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
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
            "negative_prompt": "",
            "width": 768,
            "height": 512,
            "steps": 28,
            "cfg": 3.5,
            "seed": 42,
            "wait_for_completion": True,
            "timeout_seconds": 42,
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
    assert calls["generate"]["negative_prompt"] == ""
    assert calls["generate"]["width"] == 768
    assert calls["generate"]["height"] == 512
    assert calls["generate"]["steps"] == 28
    assert calls["generate"]["cfg"] == 3.5
    assert calls["generate"]["seed"] == 42
    assert 0 < calls["init"]["timeout_seconds"] <= 42


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


def test_fal_queue_only_compatibility_request_is_rejected(monkeypatch):
    main = _fresh_main(monkeypatch, fal_source="enabled", fal_api_key="fal-key")

    class UnexpectedFalClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("queue-only rejection must happen before FAL execution")

    monkeypatch.setattr(main, "FalClient", UnexpectedFalClient)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/comfyui/generate",
        json={"prompt": "queued render", "wait_for_completion": False},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "FAL does not support queue-only compatibility requests"
    )


def test_fal_compatibility_validation_error_is_a_client_error(monkeypatch):
    main = _fresh_main(monkeypatch, fal_source="enabled", fal_api_key="fal-key")

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/comfyui/generate",
        json={"prompt": "mapped request", "negative_prompt": "low detail"},
    )

    assert response.status_code == 400
    assert "negative_prompt" in response.json()["detail"]


def test_fal_compatibility_rejects_custom_model_before_client(monkeypatch):
    main = _fresh_main(monkeypatch, fal_source="enabled", fal_api_key="fal-key")
    monkeypatch.setenv("FAL_MODEL", "fal-ai/custom-endpoint")

    class UnexpectedFalClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("custom compatibility model must fail before client init")

    monkeypatch.setattr(main, "FalClient", UnexpectedFalClient)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/comfyui/generate", json={"prompt": "custom model"}
    )

    assert response.status_code == 400
    assert "fal-ai/flux/dev" in response.json()["detail"]


def test_fal_disabled_preserves_comfyui_generation_without_key(monkeypatch):
    main = _fresh_main(monkeypatch, fal_source="disabled", fal_api_key="")
    calls = {}

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

        async def wait_for_completion(self, prompt_id, timeout):
            calls["completion"] = {"prompt_id": prompt_id, "timeout": timeout}
            return {
                "success": True,
                "outputs": {"7": {"images": [{"filename": "comfy.png"}]}},
            }

    monkeypatch.setattr(main, "ComfyUIClient", FakeComfyUIClient)

    from fastapi.testclient import TestClient

    response = TestClient(main.app).post(
        "/comfyui/generate",
        json={
            "prompt": "local render",
            "wait_for_completion": True,
            "timeout_seconds": 42,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["prompt_id"] == "comfy-1"
    assert body["client_id"] == "comfy-client"
    assert calls["completion"]["prompt_id"] == "comfy-1"
    assert 0 < calls["completion"]["timeout"] < 42


def test_comfyui_submission_consumes_the_same_request_deadline(monkeypatch):
    main = _fresh_main(monkeypatch, fal_source="disabled", fal_api_key="")

    class SlowComfyUIClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def generate_simple_image(self, **_kwargs):
            await asyncio.sleep(2)
            return {"success": True, "prompt_id": "too-late"}

        async def wait_for_completion(self, *_args, **_kwargs):
            raise AssertionError("polling must not begin after submission times out")

    monkeypatch.setattr(main, "ComfyUIClient", SlowComfyUIClient)

    from fastapi.testclient import TestClient

    started = time.monotonic()
    response = TestClient(main.app).post(
        "/comfyui/generate",
        json={"prompt": "slow submission", "timeout_seconds": 1},
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "prompt_id": None,
        "client_id": None,
        "message": None,
        "data": None,
        "error": "Image generation timed out",
    }
    assert elapsed < 1.5


def test_fal_client_accepts_request_scoped_timeout():
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", timeout_seconds=0.25)

    assert client.timeout_seconds == 0.25


@pytest.mark.parametrize(
    ("name", "value", "message"),
    (
        ("FAL_ENABLE_SAFETY_CHECKER", "treu", "boolean"),
        ("FAL_TIMEOUT_SECONDS", "not-a-number", "finite"),
        ("FAL_TIMEOUT_SECONDS", "nan", "finite"),
        ("FAL_TIMEOUT_SECONDS", "inf", "finite"),
        ("FAL_TIMEOUT_SECONDS", "0", "greater than 0"),
        ("FAL_TIMEOUT_SECONDS", "3601", "at most 3600"),
        ("FAL_OUTPUT_FORMAT", "webp", "jpeg or png"),
    ),
)
def test_fal_client_rejects_malformed_provider_configuration(
    monkeypatch, name, value, message
):
    monkeypatch.setenv(name, value)

    from fal_media_client import FalClient

    with pytest.raises(ValueError, match=message):
        FalClient(api_key="fal-key")


def test_fal_client_constructs_subscribe_request(monkeypatch):
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

    result = asyncio.run(
        FalClient(
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


def test_fal_client_rejects_unsupported_negative_prompt(monkeypatch):
    from fal_media_client import FalClient

    with pytest.raises(ValueError, match="negative_prompt.*not supported"):
        asyncio.run(
            FalClient(api_key="fal-key").generate_simple_image(
                prompt="orbital blue glass library",
                negative_prompt="low detail",
                width=768,
                height=512,
            )
        )


# --- image_to_3d modality (#340) --------------------------------------------

# These mimic the REAL fal-client (>=0.8.0) queue `status()` return types:
# type-discriminated dataclasses whose *class name* signals state, carrying no
# `.status`/`.state` attribute. A failed job is a `Completed` with a truthy
# `error`. The class NAMES must stay exactly Completed/InProgress/Queued —
# `_normalize_status` reads `type(payload).__name__`.


class Completed:  # noqa: D401 - mirrors fal_client.client.Completed
    def __init__(self, error=None, error_type=None):
        self.logs = None
        self.metrics = {}
        self.error = error
        self.error_type = error_type


class InProgress:  # mirrors fal_client.client.InProgress
    def __init__(self):
        self.logs = []


class Queued:  # mirrors fal_client.client.Queued
    def __init__(self, position=0):
        self.position = position


def test_fal_queue_submit_applies_configured_timeout(monkeypatch):
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/flux/dev")
    client.timeout_seconds = 0.01

    def slow_submit(*args):
        time.sleep(0.1)
        return {"request_id": "late"}

    monkeypatch.setattr(client, "_submit", slow_submit)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            client.submit_media_operation(
                modality="image", input={"prompt": "atlas"}
            )
        )


def test_fal_queue_status_and_result_apply_configured_timeout(monkeypatch):
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/flux/dev")
    client.timeout_seconds = 0.01

    def slow_status(*args):
        time.sleep(0.1)
        return Completed()

    monkeypatch.setattr(client, "_status", slow_status)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            client.get_media_operation(operation_id="fal-1", modality="image")
        )

    monkeypatch.setattr(client, "_status", lambda *args: Completed())
    monkeypatch.setattr(client, "_result", slow_status)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            client.get_media_operation(operation_id="fal-1", modality="image")
        )


def test_fal_queue_cancel_timeout_degrades_to_false(monkeypatch):
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/flux/dev")
    client.timeout_seconds = 0.01

    def slow_cancel(*args):
        time.sleep(0.1)

    monkeypatch.setattr(client, "_cancel", slow_cancel)

    cancelled = asyncio.run(
        client.cancel_media_operation(operation_id="fal-1", modality="image")
    )
    assert cancelled is False


def _stub_fal_queue(monkeypatch, *, result_payload, status_obj=None):
    captured: dict = {}

    def fake_submit(model, *, arguments):
        captured["submit"] = {"model": model, "arguments": arguments}
        return types.SimpleNamespace(request_id="fal-3d-1")

    def fake_status(model, request_id):
        captured["status"] = {"model": model, "request_id": request_id}
        # Default to the real "job finished" shape, not a fabricated .status.
        return status_obj if status_obj is not None else Completed()

    def fake_result(model, request_id):
        captured["result"] = {"model": model, "request_id": request_id}
        return result_payload

    monkeypatch.setitem(
        sys.modules,
        "fal_client",
        types.SimpleNamespace(
            submit=fake_submit, status=fake_status, result=fake_result
        ),
    )
    return captured


def test_fal_client_submits_and_polls_image_to_3d_glb(monkeypatch):
    captured = _stub_fal_queue(
        monkeypatch,
        result_payload={
            "model_mesh": {"url": "https://cdn.example/mesh.glb"},
            "seed": 7,
            "some_vendor_field": {"quality": "high"},
        },
    )

    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/trellis")
    submitted = asyncio.run(
        client.submit_media_operation(
            modality="image_to_3d",
            input={"image": "https://cdn.example/sprite.png", "seed": 7},
        )
    )
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )

    # Input image goes under fal's image_url key; the gateway already hosted it.
    assert captured["submit"]["arguments"]["image_url"] == "https://cdn.example/sprite.png"
    assert captured["submit"]["arguments"]["seed"] == 7
    assert submitted["operation_id"] == "fal-3d-1"
    assert submitted["status"] == "submitted"
    assert submitted["modality"] == "image_to_3d"
    # Registry-driven license + estimated cost.
    assert submitted["license"] == "MIT"
    assert submitted["cost_usd"] == 0.05
    assert submitted["provenance"]["cost_basis"] == "estimated"

    assert polled["status"] == "succeeded"
    assert polled["artifact_url"] == "https://cdn.example/mesh.glb"
    glb = polled["artifacts"][0]
    assert glb["role"] == "model_glb"
    assert glb["content_type"] == "model/gltf-binary"
    assert glb["source_key"] == "model_mesh"
    # Unknown provider fields preserved under a namespaced provenance bag; the
    # consumed seed/model_mesh keys are not duplicated there.
    provider_fields = polled["provenance"]["provider_fields"]
    assert provider_fields["some_vendor_field"] == {"quality": "high"}
    assert provider_fields["seed"] == 7
    assert "model_mesh" not in provider_fields


@pytest.mark.parametrize(
    "model,expected_key,expected_value",
    (
        ("fal-ai/trellis", "image_url", "https://cdn.example/input.png"),
        ("fal-ai/hunyuan3d/v2", "input_image_url", "https://cdn.example/input.png"),
        (
            "tripo3d/tripo/v2.5/image-to-3d",
            "image_url",
            "https://cdn.example/input.png",
        ),
        (
            "fal-ai/hyper3d/rodin",
            "input_image_urls",
            ["https://cdn.example/input.png"],
        ),
    ),
)
def test_image_to_3d_uses_model_specific_image_field(
    monkeypatch, model, expected_key, expected_value
):
    captured = _stub_fal_queue(monkeypatch, result_payload={})
    from fal_media_client import FalClient

    asyncio.run(
        FalClient(api_key="fal-key", model=model).submit_media_operation(
            modality="image_to_3d",
            input={"image": "https://cdn.example/input.png", "seed": 7},
        )
    )

    arguments = captured["submit"]["arguments"]
    assert arguments[expected_key] == expected_value
    assert set(arguments) == {expected_key, "seed"}


@pytest.mark.parametrize("seed", (True, 1.5, "7", float("nan")))
def test_image_to_3d_rejects_non_integer_seed(seed):
    from fal_media_client import FalClient

    with pytest.raises(ValueError, match="seed"):
        asyncio.run(
            FalClient(api_key="fal-key", model="fal-ai/trellis").submit_media_operation(
                modality="image_to_3d",
                input={"image": "https://cdn.example/input.png", "seed": seed},
            )
        )


def test_image_to_3d_rejects_unowned_extra_payload():
    from fal_media_client import FalClient

    with pytest.raises(ValueError, match="unsupported fields"):
        asyncio.run(
            FalClient(api_key="fal-key", model="fal-ai/trellis").submit_media_operation(
                modality="image_to_3d",
                input={
                    "image": "https://cdn.example/input.png",
                    "extra": {"image_url": "https://attacker.example/override.png"},
                },
            )
        )


def test_fal_client_image_to_3d_response_key_variants(monkeypatch):
    from fal_media_client import FalClient

    variants = {
        "model_glb": "https://cdn.example/a.glb",
        "model": "https://cdn.example/b.glb",
        "mesh": {"url": "https://cdn.example/c.glb"},
        "pbr_model": {"url": "https://cdn.example/d.glb"},
        "base_model": "https://cdn.example/e.glb",
    }
    for key, value in variants.items():
        _stub_fal_queue(monkeypatch, result_payload={key: value})
        client = FalClient(api_key="fal-key", model="fal-ai/hunyuan3d/v2")
        polled = asyncio.run(
            client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
        )
        assert polled["status"] == "succeeded"
        expected = value["url"] if isinstance(value, dict) else value
        assert polled["artifact_url"] == expected, f"key={key}"
        assert polled["artifacts"][0]["source_key"] == key


def test_fal_client_image_to_3d_requires_image(monkeypatch):
    _stub_fal_queue(monkeypatch, result_payload={})
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/trellis")
    try:
        asyncio.run(
            client.submit_media_operation(modality="image_to_3d", input={})
        )
    except ValueError as e:
        assert "image" in str(e)
    else:  # pragma: no cover
        raise AssertionError("missing image must raise ValueError")


def test_fal_client_image_to_3d_real_status_shapes(monkeypatch):
    """Lock the production contract: fal's queue status() returns Queued /
    InProgress / Completed dataclasses (state = class name, no .status attr).
    A genuinely completed job MUST normalize to 'succeeded' and extract the GLB
    (regression guard for the _normalize_status attribute-only bug)."""
    from fal_media_client import FalClient

    # Completed with no error -> succeeded + GLB extracted.
    _stub_fal_queue(
        monkeypatch,
        result_payload={"model_glb": "https://cdn.example/real.glb"},
        status_obj=Completed(),
    )
    client = FalClient(api_key="fal-key", model="fal-ai/trellis")
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    assert polled["status"] == "succeeded"
    assert polled["artifact_url"] == "https://cdn.example/real.glb"

    # InProgress -> running.
    _stub_fal_queue(monkeypatch, result_payload={}, status_obj=InProgress())
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    assert polled["status"] == "running"
    assert polled["artifact_url"] is None

    # Queued -> submitted.
    _stub_fal_queue(monkeypatch, result_payload={}, status_obj=Queued(position=2))
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    assert polled["status"] == "submitted"


def test_fal_client_image_to_3d_failed_status_has_no_artifact(monkeypatch):
    # Real fal shape: a failed job is a Completed carrying a truthy error; the
    # result must NOT be fetched and no artifact is produced.
    def fake_status(model, request_id):
        return Completed(error="mesh generation failed", error_type="InternalError")

    def fake_result(model, request_id):  # pragma: no cover - must not be called
        raise AssertionError("result must not be fetched for a failed operation")

    monkeypatch.setitem(
        sys.modules,
        "fal_client",
        types.SimpleNamespace(status=fake_status, result=fake_result),
    )

    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/trellis")
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    assert polled["status"] == "failed"
    assert polled["artifact_url"] is None
    assert polled["artifacts"] == []


def test_fal_client_image_to_3d_cancelled_status_string_shape(monkeypatch):
    # Forward-compat: a dict/string-shaped status reporting cancellation still
    # normalizes to 'cancelled' (the real SDK has no distinct cancelled class).
    monkeypatch.setitem(
        sys.modules,
        "fal_client",
        types.SimpleNamespace(
            status=lambda model, request_id: {"status": "CANCELED"}
        ),
    )
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/trellis")
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    assert polled["status"] == "cancelled"


def test_fal_client_image_to_3d_preview_without_glb_yields_no_artifact_url(monkeypatch):
    # GLB key absent but a preview present: artifact_url must be None (the GLB),
    # never the shadowing preview PNG. The preview still appears in artifacts[].
    _stub_fal_queue(
        monkeypatch,
        result_payload={"preview_image": {"url": "https://cdn.example/preview.png"}},
    )
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/trellis")
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    assert polled["status"] == "succeeded"
    assert polled["artifact_url"] is None
    assert [a["role"] for a in polled["artifacts"]] == ["preview"]


def test_fal_client_image_to_3d_preserves_non_url_value_under_known_key(monkeypatch):
    # A recognized GLB key holding a non-URL vendor object must be preserved
    # under provenance.provider_fields, not silently dropped.
    _stub_fal_queue(
        monkeypatch,
        result_payload={
            "model_mesh": {"url": "https://cdn.example/mesh.glb"},
            "model": {"job": "abc", "meta": 1},  # known key, no url
        },
    )
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/trellis")
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    assert polled["artifact_url"] == "https://cdn.example/mesh.glb"
    assert polled["provenance"]["provider_fields"]["model"] == {"job": "abc", "meta": 1}


def test_fal_client_image_to_3d_malformed_result_is_empty(monkeypatch):
    # Succeeded status but a result missing every known GLB key must degrade to
    # no artifacts rather than raising.
    _stub_fal_queue(
        monkeypatch, result_payload={"unexpected": "no-mesh-here"}
    )
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/trellis")
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    assert polled["status"] == "succeeded"
    assert polled["artifact_url"] is None
    assert polled["artifacts"] == []
    assert polled["provenance"]["provider_fields"] == {"unexpected": "no-mesh-here"}


def test_fal_client_image_to_3d_tripo_license_and_cost(monkeypatch):
    _stub_fal_queue(
        monkeypatch,
        result_payload={"pbr_model": "https://cdn.example/tripo.glb"},
    )
    from fal_media_client import FalClient

    client = FalClient(
        api_key="fal-key", model="tripo3d/tripo/v2.5/image-to-3d"
    )
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    assert polled["license"] == "tripo-commercial-gated"
    assert polled["cost_usd"] == 0.20
    assert polled["provenance"]["provider_request_id"] == "fal-3d-1"


def test_fal_client_image_to_3d_extracts_preview_and_textures(monkeypatch):
    _stub_fal_queue(
        monkeypatch,
        result_payload={
            "model_glb": "https://cdn.example/model.glb",
            "preview_image": {"url": "https://cdn.example/preview.png"},
            "textures": [
                {"url": "https://cdn.example/albedo.png"},
                "https://cdn.example/normal.png",
            ],
        },
    )
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/trellis")
    polled = asyncio.run(
        client.get_media_operation(operation_id="fal-3d-1", modality="image_to_3d")
    )
    roles = [a["role"] for a in polled["artifacts"]]
    assert roles == ["model_glb", "preview", "texture", "texture"]
    assert polled["artifact_url"] == "https://cdn.example/model.glb"


# ── #453: img2img pass-through + nested image_size fallback ─────────────────
def _submit_image_operation(monkeypatch, input_payload, *, model="fal-ai/flux/dev"):
    """Drive submit_media_operation(modality='image') with a stubbed fal_client
    and return the exact arguments dict handed to fal_client.submit."""
    captured = {}

    class _Handle:
        request_id = "fal-img-1"

    def fake_submit(model, *, arguments):
        captured["model"] = model
        captured["arguments"] = arguments
        return _Handle()

    monkeypatch.setitem(
        sys.modules, "fal_client", types.SimpleNamespace(submit=fake_submit)
    )
    from fal_media_client import FalClient

    client = FalClient(
        api_key="fal-key",
        model=model,
        output_format="jpeg",
        enable_safety_checker=True,
    )
    submitted = asyncio.run(
        client.submit_media_operation(modality="image", input=input_payload)
    )
    return captured, submitted


def test_fal_image_submit_forwards_img2img_init_image_and_strength(monkeypatch):
    """#453: image_url + strength reach fal_client.submit instead of being
    silently dropped to text2img."""
    captured, submitted = _submit_image_operation(
        monkeypatch,
        {
            "prompt": "expand this sprite",
            "image_url": "data:image/webp;base64,AAAA",
            "strength": 0.4,
        },
    )
    args = captured["arguments"]
    assert captured["model"] == "fal-ai/flux/dev/image-to-image"
    assert args == {
        "prompt": "expand this sprite",
        "num_inference_steps": 20,
        "guidance_scale": 7.0,
        "num_images": 1,
        "enable_safety_checker": True,
        "output_format": "jpeg",
        "image_url": "data:image/webp;base64,AAAA",
        "strength": 0.4,
    }
    assert submitted["status"] == "submitted"
    assert submitted["model"] == "fal-ai/flux/dev/image-to-image"


def test_fal_image_submit_accepts_image_and_init_image_aliases(monkeypatch):
    """#453: `image` and `init_image` are accepted aliases for the init image."""
    for alias in ("image", "init_image"):
        captured, _ = _submit_image_operation(
            monkeypatch,
            {"prompt": "variation", alias: "https://cdn.example/sprite.png"},
        )
        assert captured["arguments"]["image_url"] == "https://cdn.example/sprite.png"
        # strength omitted → not injected
        assert "strength" not in captured["arguments"]


def test_fal_image_submit_text2img_contract_unchanged(monkeypatch):
    """#453 no-regression lock: without an init image the arguments dict is
    exactly the historical text2img shape (no image_url / strength keys)."""
    captured, _ = _submit_image_operation(
        monkeypatch,
        {"prompt": "neon data center", "width": 640, "height": 480, "steps": 28,
         "cfg": 3.5, "seed": 123},
    )
    assert captured["arguments"] == {
        "prompt": "neon data center",
        "image_size": {"width": 640, "height": 480},
        "num_inference_steps": 28,
        "guidance_scale": 3.5,
        "seed": 123,
        "num_images": 1,
        "enable_safety_checker": True,
        "output_format": "jpeg",
    }


def test_fal_image_submit_preserves_explicit_zero_cfg(monkeypatch):
    captured, _ = _submit_image_operation(
        monkeypatch,
        {
            "prompt": "zero-value contract",
            "provider_arguments": {
                "prompt": "zero-value contract",
                "guidance_scale": 0,
            },
        },
        model="fal-ai/custom-zero-guidance",
    )
    assert captured["arguments"] == {
        "prompt": "zero-value contract",
        "guidance_scale": 0,
    }


def test_custom_fal_image_endpoint_requires_provider_native_arguments(monkeypatch):
    with pytest.raises(ValueError, match="provider_arguments"):
        _submit_image_operation(
            monkeypatch,
            {"prompt": "custom endpoint"},
            model="fal-ai/custom-endpoint",
        )


def test_custom_fal_image_endpoint_requires_matching_prompt(monkeypatch):
    with pytest.raises(ValueError, match="matching prompt"):
        _submit_image_operation(
            monkeypatch,
            {
                "prompt": "top-level prompt",
                "provider_arguments": {"prompt": "different prompt"},
            },
            model="fal-ai/custom-endpoint",
        )


@pytest.mark.parametrize("field", ("width", "height", "image_size", "negative_prompt"))
def test_default_fal_img2img_rejects_unsupported_controls(monkeypatch, field):
    value = {"width": 512, "height": 512} if field == "image_size" else 512
    if field == "negative_prompt":
        value = "low detail"
    with pytest.raises(ValueError, match=field):
        _submit_image_operation(
            monkeypatch,
            {
                "prompt": "image variation",
                "image_url": "https://cdn.example/in.png",
                field: value,
            },
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ({"steps": 51}, "steps"),
        ({"cfg": 0}, "cfg"),
        ({"image_url": "https://cdn.example/in.png", "steps": 9}, "steps"),
        ({"image_url": "https://cdn.example/in.png", "strength": 0}, "strength"),
        ({"image_url": "https://cdn.example/in.png", "strength": 1.1}, "strength"),
        ({"image_url": "https://cdn.example/in.png", "strength": float("nan")}, "strength"),
    ),
)
def test_default_fal_endpoints_reject_provider_invalid_controls(
    monkeypatch, payload, message
):
    with pytest.raises(ValueError, match=message):
        _submit_image_operation(monkeypatch, {"prompt": "bounded", **payload})


@pytest.mark.parametrize("prompt", (None, "", "   ", {}, "x" * 4001))
def test_fal_image_submit_rejects_invalid_prompt(monkeypatch, prompt):
    with pytest.raises(ValueError, match="FAL image prompt"):
        _submit_image_operation(monkeypatch, {"prompt": prompt})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("width", 63),
        ("width", 4097),
        ("width", 512.5),
        ("height", 63),
        ("height", 4097),
        ("steps", 0),
        ("steps", 151),
        ("steps", 1.5),
        ("num_images", 0),
        ("num_images", 5),
        ("cfg", -1),
        ("cfg", 31),
        ("cfg", True),
        ("cfg", float("nan")),
        ("cfg", float("inf")),
    ),
)
def test_fal_image_submit_rejects_invalid_numeric_values(
    monkeypatch, field, value
):
    with pytest.raises(ValueError, match="FAL image"):
        _submit_image_operation(
            monkeypatch,
            {"prompt": "invalid numeric contract", field: value},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("image_size", "square"),
        ("image_size", [512, 512]),
        ("seed", True),
        ("seed", 42.0),
        ("seed", "42"),
        ("seed", float("nan")),
    ),
)
def test_fal_image_submit_rejects_malformed_schema_fields(monkeypatch, field, value):
    with pytest.raises(ValueError, match=field):
        _submit_image_operation(
            monkeypatch,
            {"prompt": "strict schema", field: value},
        )


def test_fal_image_submit_accepts_nested_image_size_fallback(monkeypatch):
    """#453 (secondary): a nested image_size object no longer silently
    defaults to 512x512; flat width/height keys still win when both present."""
    captured, _ = _submit_image_operation(
        monkeypatch,
        {"prompt": "p", "image_size": {"width": 1280, "height": 720}},
    )
    assert captured["arguments"]["image_size"] == {"width": 1280, "height": 720}

    captured, _ = _submit_image_operation(
        monkeypatch,
        {"prompt": "p", "width": 800, "image_size": {"width": 1280, "height": 720}},
    )
    assert captured["arguments"]["image_size"] == {"width": 800, "height": 720}


# ── #518: FalClient.cancel_media_operation (provider-side cancel hook) ──────
def test_fal_client_cancel_delivers_via_sdk(monkeypatch):
    captured = {}

    def fake_cancel(model, request_id):
        captured["model"] = model
        captured["request_id"] = request_id

    monkeypatch.setitem(
        sys.modules, "fal_client", types.SimpleNamespace(cancel=fake_cancel)
    )
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/flux/dev")
    ok = asyncio.run(
        client.cancel_media_operation(operation_id="fal-req-9", modality="image")
    )
    assert ok is True
    assert captured == {"model": "fal-ai/flux/dev", "request_id": "fal-req-9"}


def test_fal_client_cancel_safe_noop_without_sdk_support(monkeypatch):
    """Older fal-client releases without `cancel` → False (server-side cancel
    proceeds), never an exception."""
    monkeypatch.setitem(sys.modules, "fal_client", types.SimpleNamespace())
    from fal_media_client import FalClient

    client = FalClient(api_key="fal-key", model="fal-ai/flux/dev")
    ok = asyncio.run(
        client.cancel_media_operation(operation_id="fal-req-9", modality="image")
    )
    assert ok is False
