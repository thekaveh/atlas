"""Tests for the ComfyUI-backed media provider (#519).

Two layers:
  * graph-building + normalization helpers — pure unit tests (no network).
  * submit/poll/cancel + img2img upload — exercised through an httpx
    MockTransport so the full client flow runs without a live ComfyUI.

Async client calls are wrapped in ``asyncio.run`` (the backend suite is
deliberately asyncio-marker-free and pytest-asyncio is not installed in
the CI venv — same pattern as ``test_fal_media_provider.py``). The live
end-to-end smoke lives in bootstrapper/tests/ (registered ``live`` marker
+ ATLAS_COMFYUI_LIVE_ENDPOINT).
"""
import asyncio
import base64
import json
from typing import Any, Callable, Dict

import httpx
import pytest

import comfyui_media_client as cmc
from comfyui_media_client import ComfyUIMediaClient


# ────────────────────────────────────────────────────────────────────
# Graph building — text2img
# ────────────────────────────────────────────────────────────────────
def _t2i_checkpoint():
    return cmc._build_checkpoint_graph(
        model="sdxl_base.safetensors",
        prompt="a castle",
        negative_prompt="blurry",
        width=1024,
        height=768,
        steps=25,
        cfg=7.0,
        seed=42,
        sampler_name="euler",
        scheduler="normal",
        denoise=1.0,
        init_image_name=None,
    )


def test_checkpoint_graph_text2img_structure():
    g = _t2i_checkpoint()
    assert g["1"]["class_type"] == "CheckpointLoaderSimple"
    assert g["1"]["inputs"]["ckpt_name"] == "sdxl_base.safetensors"
    # positive/negative conditioning off the checkpoint's CLIP output (idx 1)
    assert g["2"]["inputs"]["clip"] == ["1", 1]
    assert g["3"]["inputs"]["text"] == "blurry"
    # text2img uses EmptyLatentImage at the requested resolution
    assert g["5"]["class_type"] == "EmptyLatentImage"
    assert g["5"]["inputs"]["width"] == 1024
    assert g["5"]["inputs"]["height"] == 768
    # KSampler denoise=1.0 for text2img, latent from the empty image
    assert g["4"]["inputs"]["denoise"] == 1.0
    assert g["4"]["inputs"]["latent_image"] == ["5", 0]
    # VAEDecode off the checkpoint's VAE output (idx 2); SaveImage present
    assert g["7"]["inputs"]["vae"] == ["1", 2]
    assert g["8"]["class_type"] == "SaveImage"


def test_krea2_split_graph_matches_fixture_shape():
    g = cmc._build_split_graph(
        model="krea2_turbo_bf16.safetensors",
        profile=dict(cmc._KREA2_PROFILE),
        prompt="observatory",
        width=1024,
        height=1024,
        steps=8,
        cfg=1.0,
        seed=337,
        sampler_name="euler",
        scheduler="simple",
        denoise=1.0,
        init_image_name=None,
    )
    # Mirrors services/comfyui/workflows/krea2-turbo-api.json node-for-node.
    assert g["1"]["class_type"] == "UNETLoader"
    assert g["1"]["inputs"]["unet_name"] == "krea2_turbo_bf16.safetensors"
    assert g["2"]["class_type"] == "CLIPLoader"
    assert g["2"]["inputs"]["type"] == "krea2"  # Krea-2-specific CLIP type
    assert g["2"]["inputs"]["clip_name"] == "qwen3vl_4b_bf16.safetensors"
    assert g["3"]["class_type"] == "VAELoader"
    assert g["3"]["inputs"]["vae_name"] == "qwen_image_vae.safetensors"
    # cfg=1.0 family → ConditioningZeroOut negative (not a real CLIPTextEncode)
    assert g["5"]["class_type"] == "ConditioningZeroOut"
    assert g["7"]["inputs"]["cfg"] == 1.0
    assert g["7"]["inputs"]["steps"] == 8
    assert g["7"]["inputs"]["scheduler"] == "simple"
    # SaveImage node id 9 — the live smoke keys outputs["9"], keep it stable.
    assert g["9"]["class_type"] == "SaveImage"


# ────────────────────────────────────────────────────────────────────
# Graph building — img2img
# ────────────────────────────────────────────────────────────────────
def test_checkpoint_img2img_swaps_latent_source_and_sets_denoise():
    g = cmc._build_checkpoint_graph(
        model="sdxl_base.safetensors",
        prompt="a castle",
        negative_prompt="",
        width=1024,
        height=1024,
        steps=25,
        cfg=7.0,
        seed=1,
        sampler_name="euler",
        scheduler="normal",
        denoise=0.6,
        init_image_name="atlas-init-abc.png",
    )
    # img2img: LoadImage + VAEEncode replace EmptyLatentImage.
    assert g["5"]["class_type"] == "LoadImage"
    assert g["5"]["inputs"]["image"] == "atlas-init-abc.png"
    assert g["6"]["class_type"] == "VAEEncode"
    # KSampler denoise comes from strength; latent from VAEEncode.
    assert g["4"]["inputs"]["denoise"] == 0.6
    assert g["4"]["inputs"]["latent_image"] == ["6", 0]


def test_krea2_img2img_uses_standalone_vae_for_encode():
    g = cmc._build_split_graph(
        model="krea2_turbo_bf16.safetensors",
        profile=dict(cmc._KREA2_PROFILE),
        prompt="x",
        width=1024,
        height=1024,
        steps=8,
        cfg=1.0,
        seed=1,
        sampler_name="euler",
        scheduler="simple",
        denoise=0.75,
        init_image_name="init.png",
    )
    assert g["6"]["class_type"] == "LoadImage"
    assert g["10"]["class_type"] == "VAEEncode"
    # Split-family VAEEncode uses the standalone VAELoader output, not a ckpt.
    assert g["10"]["inputs"]["vae"] == ["3", 0]
    assert g["7"]["inputs"]["latent_image"] == ["10", 0]


# ────────────────────────────────────────────────────────────────────
# Profiles + helpers
# ────────────────────────────────────────────────────────────────────
def test_profile_selection_by_keyword():
    assert cmc._profile_for("krea2-turbo-bf16")["graph_kind"] == "split"
    assert cmc._profile_for("KREA2_RAW")["graph_kind"] == "split"
    assert cmc._profile_for("sdxl_base.safetensors")["graph_kind"] == "checkpoint"
    assert cmc._profile_for("")["graph_kind"] == "checkpoint"


def test_role_file_parsing():
    roles = cmc._extract_role_files(
        "diffusion=krea2_turbo_bf16.safetensors,vae=qwen_image_vae.safetensors"
    )
    assert roles == {
        "diffusion": "krea2_turbo_bf16.safetensors",
        "vae": "qwen_image_vae.safetensors",
    }
    assert cmc._extract_role_files("plain-name.safetensors") == {}


def test_artifact_content_type_from_filename():
    assert cmc._content_type_for("a.png") == "image/png"
    assert cmc._content_type_for("a.jpeg") == "image/jpeg"
    assert cmc._content_type_for("a.webp") == "image/webp"


def test_coerce_float():
    assert cmc._coerce_float("0.4", default=0.75) == 0.4
    assert cmc._coerce_float(None, default=0.75) == 0.75
    assert cmc._coerce_float("nope", default=0.75) == 0.75


def test_select_init_image_distinguishes_absent_from_invalid():
    assert cmc._select_init_image({}) is None
    assert cmc._select_init_image({"image": " data:image/png;base64,AA== "}) == (
        "data:image/png;base64,AA=="
    )

    for payload in ({"image": ""}, {"image_url": 42}, {"init_image": None}):
        with pytest.raises(ValueError, match="non-empty string"):
            cmc._select_init_image(payload)


def test_select_init_image_rejects_multiple_aliases():
    with pytest.raises(ValueError, match="only one"):
        cmc._select_init_image(
            {"image": "data:image/png;base64,AA==", "image_url": "https://x.test/a.png"}
        )


def test_trusted_image_origins_are_exact_and_canonical():
    assert cmc._trusted_image_origins(
        "https://Images.Example.com, https://images.example.com:8443"
    ) == frozenset(
        {"https://images.example.com:443", "https://images.example.com:8443"}
    )


@pytest.mark.parametrize(
    "origin",
    (
        "http://images.example.com",
        "https://user@images.example.com",
        "https://images.example.com/path",
        "https://images.example.com?query=1",
        "https://*.example.com",
    ),
)
def test_trusted_image_origins_reject_malformed_policy(origin):
    with pytest.raises(ValueError, match="COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS"):
        cmc._trusted_image_origins(origin)


# ────────────────────────────────────────────────────────────────────
# Status + artifact normalization
# ────────────────────────────────────────────────────────────────────
def test_history_status_success_and_error():
    ok = {"outputs": {"9": {"images": [{"filename": "a.png"}]}}, "status": {"status_str": "success"}}
    assert cmc.ComfyUIMediaClient._history_status(ok) == ("success", None)

    err = {
        "outputs": {},
        "status": {
            "status_str": "error",
            "messages": [["execution_error", {"exception_message": "boom"}]],
        },
    }
    status_str, msg = cmc.ComfyUIMediaClient._history_status(err)
    assert status_str == "error"
    assert msg == "boom"


def test_queue_status_running_queued_lost():
    running = {"queue_running": [[0, "pid-1", {}, {}, []]], "queue_pending": []}
    assert cmc.ComfyUIMediaClient._queue_status("pid-1", running) == "running"
    pending = {"queue_running": [], "queue_pending": [[1, "pid-2", {}, {}, []]]}
    assert cmc.ComfyUIMediaClient._queue_status("pid-2", pending) == "queued"
    assert cmc.ComfyUIMediaClient._queue_status("pid-x", running) == "failed"


def test_extract_artifacts_builds_proxy_url():
    entry = {"outputs": {"9": {"images": [
        {"filename": "out.png", "subfolder": "sub", "type": "output"},
    ]}}}
    arts = cmc.ComfyUIMediaClient._extract_artifacts(entry)
    assert len(arts) == 1
    a = arts[0]
    assert a["url"] == "/comfyui/image/out.png?subfolder=sub&folder_type=output"
    assert a["role"] == "image"
    assert a["content_type"] == "image/png"
    assert a["filename"] == "out.png"


def test_artifact_url_is_gateway_relative_contract():
    """#678: the ComfyUI `artifact_url` is a GATEWAY-RELATIVE proxy path (no
    scheme) — a consumer must resolve it against its own backend/gateway base,
    unlike FAL's absolute hosted URL (pinned in test_media_gateway.py). This
    guards the documented shape difference so the two can't silently diverge."""
    entry = {"outputs": {"9": {"images": [{"filename": "out.png", "type": "output"}]}}}
    url = cmc.ComfyUIMediaClient._extract_artifacts(entry)[0]["url"]
    assert url.startswith("/comfyui/image/")  # gateway-relative
    assert "://" not in url  # never an absolute URL
    assert not url.startswith(("http://", "https://"))


def test_operation_payload_local_zero_cost_envelope():
    payload = ComfyUIMediaClient(base_url="http://x")._operation_payload(
        operation_id="pid",
        status="queued",
        model="krea2-turbo-bf16",
        modality="image",
        raw={"prompt_id": "pid"},
    )
    assert payload["provider"] == "comfyui"
    assert payload["cost_usd"] == 0.0  # local is free, never None
    assert payload["license"] == "local/self-hosted"
    assert payload["provenance"]["cost_basis"] == "local_zero"
    assert payload["provenance"]["provider_request_id"] == "pid"
    assert payload["artifact_url"] is None
    assert payload["artifacts"] == []


# ────────────────────────────────────────────────────────────────────
# Full submit/poll/cancel flow through a mocked ComfyUI transport
# ────────────────────────────────────────────────────────────────────
PNG_MAGIC = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
)


async def _client_with_transport(
    handler: Callable[[httpx.Request], httpx.Response]
) -> ComfyUIMediaClient:
    """Build a ComfyUIMediaClient whose httpx calls are routed by `handler`."""
    client = ComfyUIMediaClient(base_url="http://comfyui.test")
    await client.client.aclose()
    await client.image_client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.image_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    return client


def _run(handler, body: Callable[[ComfyUIMediaClient], Any]) -> Any:
    """Drive an async test body against a mocked client inside one event loop
    (the client + its httpx connection share the loop)."""

    async def _driver():
        client = await _client_with_transport(handler)
        try:
            return await body(client)
        finally:
            await client.client.aclose()
            await client.image_client.aclose()

    return asyncio.run(_driver())


def _submit_handler(prompt_id="prompt-abc", captured=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["last_prompt_body"] = json.loads(request.content) if request.content else None
            captured["last_url"] = str(request.url)
        return httpx.Response(200, json={"prompt_id": prompt_id, "number": 1, "node_errors": {}})
    return handler


def test_submit_returns_queued_envelope_with_prompt_id():
    def body(client):
        return client.submit_media_operation(
            modality="image",
            input_payload={"prompt": "a castle", "width": 768, "height": 512, "steps": 10},
            model="sdxl_base.safetensors",
        )

    payload = _run(_submit_handler(), body)
    assert payload["status"] == "queued"
    assert payload["operation_id"] == "prompt-abc"
    assert payload["provider"] == "comfyui"
    assert payload["cost_usd"] == 0.0
    assert payload["raw"]["prompt_id"] == "prompt-abc"
    assert payload["parameters"]["width"] == 768


def test_submit_builds_krea2_graph_for_krea_model():
    captured: Dict[str, Any] = {}

    async def body(client):
        return await client.submit_media_operation(
            modality="image",
            input_payload={"prompt": "obs", "seed": 5},
            model="krea2-turbo-bf16",
        )

    _run(_submit_handler(captured=captured), body)
    graph = captured["last_prompt_body"]["prompt"]
    assert graph["1"]["class_type"] == "UNETLoader"
    assert graph["2"]["inputs"]["type"] == "krea2"
    assert graph["5"]["class_type"] == "ConditioningZeroOut"
    assert graph["7"]["inputs"]["seed"] == 5


def test_submit_rejects_unsupported_modality_and_missing_model():
    async def body(client):
        with pytest.raises(ValueError):
            await client.submit_media_operation(
                modality="image_to_3d", input_payload={"prompt": "x"}, model="m"
            )
        with pytest.raises(ValueError):
            await client.submit_media_operation(
                modality="image", input_payload={"prompt": "x"}, model=""
            )
        with pytest.raises(ValueError):
            await client.submit_media_operation(
                modality="image", input_payload={}, model="m"
            )

    _run(lambda r: httpx.Response(200, json={}), body)


def test_submit_maps_comfyui_4xx_to_valueerror_for_gateway_400():
    """A ComfyUI 4xx (bad graph / unknown model) must surface as ValueError so
    the gateway maps it to a 400 client error — NOT a 502 (host-down)."""
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {"message": "Bad graph"},
                "node_errors": {"4": {"errors": ["no such ckpt"]}},
            },
        )

    async def body(client):
        with pytest.raises(ValueError) as excinfo:
            await client.submit_media_operation(
                modality="image", input_payload={"prompt": "x"}, model="missing.safetensors"
            )
        assert "node_errors" in str(excinfo.value)

    _run(handler, body)


def test_submit_maps_comfyui_5xx_to_http_error_for_gateway_502():
    """A ComfyUI 5xx is a host-side failure → HTTPStatusError → gateway 502."""
    def handler(request):
        return httpx.Response(503, json={"error": {"message": "ComfyUI overloaded"}})

    async def body(client):
        with pytest.raises(httpx.HTTPStatusError):
            await client.submit_media_operation(
                modality="image", input_payload={"prompt": "x"}, model="m.safetensors"
            )

    _run(handler, body)


def test_poll_running_then_succeeded():
    state = {"call": 0}

    def handler(request):
        url = str(request.url)
        if url.endswith("/history/prompt-abc"):
            state["call"] += 1
            if state["call"] == 1:
                return httpx.Response(200, json={})  # not finished
            return httpx.Response(
                200,
                json={
                    "prompt-abc": {
                        "outputs": {"8": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
                        "status": {"status_str": "success", "completed": True, "messages": []},
                    }
                },
            )
        if url.endswith("/queue"):
            return httpx.Response(200, json={"queue_running": [[0, "prompt-abc"]], "queue_pending": []})
        return httpx.Response(404)

    async def body(client):
        running = await client.get_media_operation(operation_id="prompt-abc", modality="image")
        assert running["status"] == "running"
        assert running["artifact_url"] is None
        done = await client.get_media_operation(operation_id="prompt-abc", modality="image")
        assert done["status"] == "succeeded"
        assert done["artifact_url"] == "/comfyui/image/out.png?folder_type=output"
        assert done["artifacts"][0]["filename"] == "out.png"
        assert done["cost_usd"] == 0.0

    _run(handler, body)


def test_poll_failed_on_error_status():
    def handler(request):
        if request.url.path.endswith("/history/pid-err"):
            return httpx.Response(200, json={"pid-err": {
                "outputs": {},
                "status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "OOM"}]]},
            }})
        return httpx.Response(200, json={})

    async def body(client):
        payload = await client.get_media_operation(operation_id="pid-err", modality="image")
        assert payload["status"] == "failed"
        assert payload["raw"]["error"] == "OOM"

    _run(handler, body)


def test_poll_partial_history_entry_stays_running_not_succeeded():
    """A history entry present but incomplete (empty status_str + no outputs)
    must stay ``running`` — never be normalized to a succeeded-but-artifactless
    terminal that the gateway would cache permanently (#519 review fix)."""
    def handler(request):
        if request.url.path.endswith("/history/pid-partial"):
            return httpx.Response(200, json={"pid-partial": {
                "outputs": {},
                "status": {},  # present but empty → _history_status returns "running"
            }})
        return httpx.Response(200, json={})

    async def body(client):
        payload = await client.get_media_operation(operation_id="pid-partial", modality="image")
        assert payload["status"] == "running"
        assert payload["artifact_url"] is None
        assert payload["artifacts"] == []

    _run(handler, body)


def test_poll_lost_prompt_when_absent_everywhere():
    def handler(request):
        return httpx.Response(200, json={"queue_running": [], "queue_pending": []})

    async def body(client):
        payload = await client.get_media_operation(operation_id="ghost", modality="image")
        assert payload["status"] == "failed"  # evicted / lost

    _run(handler, body)


@pytest.mark.parametrize("cancelled", [True, False])
def test_cancel_uses_only_targeted_job_endpoint(cancelled):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.raw_path))
        return httpx.Response(200, json={"cancelled": cancelled})

    async def body(client):
        result = await client.cancel_media_operation(
            operation_id="pid/running", modality="image"
        )
        assert result is cancelled

    _run(handler, body)
    assert calls == [("POST", b"/api/jobs/pid%2Frunning/cancel")]


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (404, {"cancelled": False}),
        (405, {"cancelled": False}),
        (500, {"cancelled": False}),
        (200, None),
        (200, {}),
        (200, {"cancelled": 1}),
        (200, {"cancelled": "true"}),
    ],
)
def test_cancel_rejects_unsupported_or_malformed_responses(status, payload):
    def handler(request):
        if payload is None:
            return httpx.Response(status, content=b"not-json")
        return httpx.Response(status, json=payload)

    async def body(client):
        result = await client.cancel_media_operation(
            operation_id="pid", modality="image"
        )
        assert result is False

    _run(handler, body)


def test_interrupted_history_is_terminal_cancelled():
    entry = {
        "status": {
            "status_str": "error",
            "messages": [["execution_interrupted", {}]],
        }
    }
    assert cmc.ComfyUIMediaClient._history_status(entry) == ("cancelled", None)

    def handler(request):
        return httpx.Response(200, json={"pid": entry})

    async def body(client):
        payload = await client.get_media_operation(
            operation_id="pid", modality="image"
        )
        assert payload["status"] == "cancelled"
        assert payload["artifacts"] == []
        assert payload["artifact_url"] is None

    _run(handler, body)


def test_interruption_message_does_not_override_success_history():
    entry = {
        "status": {
            "status_str": "success",
            "messages": [["execution_interrupted", {}]],
        },
        "outputs": {"1": {"images": [{"filename": "kept.png"}]}},
    }

    assert cmc.ComfyUIMediaClient._history_status(entry) == ("success", None)


# ────────────────────────────────────────────────────────────────────
# img2img upload (data URI + URL)
# ────────────────────────────────────────────────────────────────────
def test_img2img_data_uri_is_uploaded_and_strength_sets_denoise():
    captured: Dict[str, Any] = {}

    def handler(request):
        url = str(request.url)
        if url.endswith("/prompt"):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"prompt_id": "pid-i2i"})
        if url.endswith("/upload/image"):
            captured["upload"] = request.content
            return httpx.Response(200, json={"name": "stored.png", "subfolder": "", "type": "temp"})
        return httpx.Response(404)

    async def body(client):
        b64 = base64.b64encode(PNG_MAGIC).decode()
        payload = await client.submit_media_operation(
            modality="image",
            input_payload={
                "prompt": "remix",
                "image_url": f"data:image/png;base64,{b64}",
                "strength": 0.4,
            },
            model="sdxl_base.safetensors",
        )
        assert payload["operation_id"] == "pid-i2i"
        assert payload["parameters"]["strength"] == 0.4

    _run(handler, body)
    graph = captured["body"]["prompt"]
    # init image uploaded then referenced by LoadImage
    assert graph["5"]["class_type"] == "LoadImage"
    assert graph["5"]["inputs"]["image"] == "stored.png [temp]"
    assert b'\r\n\r\ntemp\r\n' in captured["upload"]
    # strength drives KSampler denoise, clamped to [0,1]
    assert graph["4"]["inputs"]["denoise"] == 0.4


def test_img2img_fetches_url_init_image_from_exact_trusted_origin(monkeypatch):
    monkeypatch.setenv(
        "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "https://remote-img.test"
    )
    captured: Dict[str, Any] = {}

    def handler(request):
        url = str(request.url)
        if "remote-img.test" in url:
            return httpx.Response(200, content=PNG_MAGIC, headers={"content-type": "image/png"})
        if url.endswith("/upload/image"):
            return httpx.Response(200, json={"name": "u.png", "subfolder": "s", "type": "temp"})
        if url.endswith("/prompt"):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"prompt_id": "pid-u"})
        return httpx.Response(404)

    async def body(client):
        await client.submit_media_operation(
            modality="image",
            input_payload={"prompt": "x", "image": "https://remote-img.test/img.png"},
            model="sdxl_base.safetensors",
        )

    _run(handler, body)
    graph = captured["body"]["prompt"]
    # subfolder-prefixed name feeds LoadImage
    assert graph["5"]["inputs"]["image"] == "s/u.png [temp]"


def test_img2img_rejects_remote_url_when_no_origin_is_trusted(monkeypatch):
    monkeypatch.delenv("COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", raising=False)

    def handler(request):
        pytest.fail(f"untrusted URL must not be fetched: {request.url}")

    async def body(client):
        with pytest.raises(ValueError, match="trusted origin"):
            await client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "x", "image": "https://remote-img.test/a.png"},
                model="sdxl_base.safetensors",
            )

    _run(handler, body)


@pytest.mark.parametrize(
    "url",
    (
        "http://remote-img.test/a.png",
        "https://user@remote-img.test/a.png",
        "https://remote-img.test.evil/a.png",
        "https://remote-img.test:444/a.png",
        "https://remote-img.test/a.png#fragment",
    ),
)
def test_img2img_rejects_url_outside_exact_trusted_origin(monkeypatch, url):
    monkeypatch.setenv(
        "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "https://remote-img.test"
    )

    def handler(request):
        pytest.fail(f"untrusted URL must not be fetched: {request.url}")

    async def body(client):
        with pytest.raises(ValueError, match="trusted origin"):
            await client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "x", "image": url},
                model="sdxl_base.safetensors",
            )

    _run(handler, body)


def test_comfyui_client_rejects_invalid_trusted_origin_config(monkeypatch):
    monkeypatch.setenv(
        "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "https://images.example.com/path"
    )

    with pytest.raises(ValueError, match="COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS"):
        ComfyUIMediaClient(base_url="http://comfyui.test")


def test_comfyui_download_client_alone_disables_proxy_environment(monkeypatch):
    monkeypatch.delenv("COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", raising=False)
    client = ComfyUIMediaClient(base_url="http://comfyui.test")
    try:
        assert client.client._trust_env is True
        assert client.client.follow_redirects is False
        assert client.image_client._trust_env is False
        assert client.image_client.follow_redirects is False
    finally:
        async def close_clients():
            await client.client.aclose()
            await client.image_client.aclose()

        asyncio.run(close_clients())


def test_img2img_rejects_non_url_non_data_source():
    async def body(client):
        with pytest.raises(ValueError):
            await client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "x", "image": "/local/path.png"},
                model="sdxl_base.safetensors",
            )

    _run(lambda r: httpx.Response(200, json={}), body)


def test_img2img_url_init_image_rejects_oversized_declared_length(monkeypatch):
    """A caller-supplied init-image URL whose Content-Length exceeds the byte
    cap is rejected before the body is buffered → ValueError → gateway 400, so
    a huge remote file can't OOM the worker."""

    monkeypatch.setenv(
        "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "https://big-img.test"
    )

    def handler(request):
        if "big-img.test" in str(request.url):
            # plain bytes → httpx sets an honest Content-Length > the cap.
            return httpx.Response(200, content=b"\x00" * 4096, headers={"content-type": "image/png"})
        return httpx.Response(404)

    async def body(client):
        client.max_image_bytes = 8
        with pytest.raises(ValueError, match="byte limit"):
            await client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "x", "image": "https://big-img.test/huge.png"},
                model="sdxl_base.safetensors",
            )

    _run(handler, body)


def test_img2img_url_init_image_caps_streamed_body_without_content_length(monkeypatch):
    """When no Content-Length is declared (chunked transfer — the case a
    malicious upstream would use to slip past the header precheck), the cap is
    still enforced incrementally while streaming, not after buffering."""

    monkeypatch.setenv(
        "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "https://chunked-img.test"
    )

    async def _chunks():
        for _ in range(64):
            yield b"\x00" * 64  # 4096 bytes total, streamed with no Content-Length

    def handler(request):
        if "chunked-img.test" in str(request.url):
            return httpx.Response(200, content=_chunks(), headers={"content-type": "image/png"})
        return httpx.Response(404)

    async def body(client):
        client.max_image_bytes = 8
        with pytest.raises(ValueError, match="byte limit"):
            await client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "x", "image": "https://chunked-img.test/huge.png"},
                model="sdxl_base.safetensors",
            )

    _run(handler, body)


def test_img2img_data_uri_enforces_comfyui_byte_limit():
    async def body(client):
        client.max_image_bytes = 8
        b64 = base64.b64encode(PNG_MAGIC).decode("ascii")
        with pytest.raises(ValueError, match="byte limit"):
            await client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "x", "image": f"data:image/png;base64,{b64}"},
                model="sdxl_base.safetensors",
            )

    _run(lambda _request: httpx.Response(500), body)


def test_img2img_remote_response_mime_must_match_decoded_image(monkeypatch):
    monkeypatch.setenv(
        "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "https://remote-img.test"
    )

    def handler(request):
        if request.url.host == "remote-img.test":
            return httpx.Response(
                200, content=PNG_MAGIC, headers={"content-type": "image/jpeg"}
            )
        return httpx.Response(404)

    async def body(client):
        with pytest.raises(ValueError, match="does not match"):
            await client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "x", "image": "https://remote-img.test/a.jpg"},
                model="sdxl_base.safetensors",
            )

    _run(handler, body)


def test_img2img_upload_extension_comes_from_verified_content(monkeypatch):
    monkeypatch.setenv(
        "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "https://remote-img.test"
    )
    captured: Dict[str, Any] = {}

    def handler(request):
        if request.url.host == "remote-img.test":
            return httpx.Response(
                200, content=PNG_MAGIC, headers={"content-type": "image/png"}
            )
        if request.url.path.endswith("/upload/image"):
            captured["upload"] = request.content
            return httpx.Response(200, json={"name": "verified.png", "type": "temp"})
        if request.url.path.endswith("/prompt"):
            captured["prompt"] = json.loads(request.content)
            return httpx.Response(200, json={"prompt_id": "pid"})
        return httpx.Response(404)

    async def body(client):
        await client.submit_media_operation(
            modality="image",
            input_payload={"prompt": "x", "image": "https://remote-img.test/spoof.jpg"},
            model="sdxl_base.safetensors",
        )

    _run(handler, body)
    assert b"atlas-init-" in captured["upload"]
    assert b".png" in captured["upload"]
    assert b".jpg" not in captured["upload"]
    assert captured["prompt"]["prompt"]["5"]["inputs"]["image"] == (
        "verified.png [temp]"
    )


def test_img2img_redirect_error_does_not_disclose_full_url(monkeypatch):
    monkeypatch.setenv(
        "COMFYUI_INIT_IMAGE_TRUSTED_ORIGINS", "https://remote-img.test"
    )
    secret_url = "https://remote-img.test/a.png?token=secret-value"

    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.test/a.png"})

    async def body(client):
        with pytest.raises(ValueError) as exc:
            await client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "x", "image": secret_url},
                model="sdxl_base.safetensors",
            )
        assert "secret-value" not in str(exc.value)

    _run(handler, body)


def test_strength_clamped_to_unit_range():
    captured: Dict[str, Any] = {}

    def handler(request):
        url = str(request.url)
        if url.endswith("/upload/image"):
            return httpx.Response(200, json={"name": "i.png"})
        if url.endswith("/prompt"):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"prompt_id": "pid"})
        return httpx.Response(404)

    async def body(client):
        b64 = base64.b64encode(PNG_MAGIC).decode()
        await client.submit_media_operation(
            modality="image",
            input_payload={"prompt": "x", "init_image": f"data:image/png;base64,{b64}", "strength": 5.0},
            model="sdxl_base.safetensors",
        )

    _run(handler, body)
    graph = captured["body"]["prompt"]
    assert graph["4"]["inputs"]["denoise"] == 1.0  # clamped


# ────────────────────────────────────────────────────────────────────
# #675: catalog entry name → bundle filename resolution
# ────────────────────────────────────────────────────────────────────
_CATALOG_MANIFEST_MODELS = [
    {"name": "krea2-turbo-bf16", "bundle_id": "krea2-turbo-bf16",
     "bundle_file_role": "diffusion", "filename": "krea2_turbo_bf16.safetensors",
     "type": "diffusion_models"},
    {"name": "krea2-turbo-bf16", "bundle_id": "krea2-turbo-bf16",
     "bundle_file_role": "text_encoder", "filename": "qwen3vl_4b_bf16.safetensors",
     "type": "text_encoders"},
    {"name": "krea2-turbo-bf16", "bundle_id": "krea2-turbo-bf16",
     "bundle_file_role": "vae", "filename": "qwen_image_vae.safetensors",
     "type": "vae"},
    {"name": "sd_xl_base_1.0", "filename": "sd_xl_base_1.0.safetensors",
     "type": "checkpoint"},
]


def _write_catalog_manifest(tmp_path, monkeypatch, models=None):
    import yaml

    manifest = tmp_path / "selected-models.yaml"
    manifest.write_text(
        yaml.safe_dump({"models": models if models is not None else _CATALOG_MANIFEST_MODELS}),
        encoding="utf-8",
    )
    monkeypatch.setenv("COMFYUI_MANIFEST_PATH", str(manifest))
    return manifest


def test_resolve_catalog_model_bundle_single_file_and_unknown():
    # Catalog name → per-role bundle filenames.
    bundle = cmc._resolve_catalog_model("krea2-turbo-bf16", _CATALOG_MANIFEST_MODELS)
    assert bundle == {
        "kind": "bundle",
        "roles": {
            "diffusion": "krea2_turbo_bf16.safetensors",
            "text_encoder": "qwen3vl_4b_bf16.safetensors",
            "vae": "qwen_image_vae.safetensors",
        },
    }
    # Single-file catalog entry name → its physical filename.
    assert cmc._resolve_catalog_model("sd_xl_base_1.0", _CATALOG_MANIFEST_MODELS) == {
        "kind": "file", "filename": "sd_xl_base_1.0.safetensors",
    }
    # A direct physical filename matches verbatim (both forms accepted).
    assert cmc._resolve_catalog_model("krea2_turbo_bf16.safetensors", _CATALOG_MANIFEST_MODELS) == {
        "kind": "file", "filename": "krea2_turbo_bf16.safetensors",
    }
    # An unknown token resolves to nothing.
    assert cmc._resolve_catalog_model("nope", _CATALOG_MANIFEST_MODELS) is None


def test_submit_resolves_catalog_name_to_bundle_filenames(tmp_path, monkeypatch):
    """AC#1: model=<catalog entry name> resolves to the bundle's per-role
    filenames in the built graph."""
    _write_catalog_manifest(tmp_path, monkeypatch)
    captured: Dict[str, Any] = {}

    async def body(client):
        return await client.submit_media_operation(
            modality="image", input_payload={"prompt": "obs", "seed": 1},
            model="krea2-turbo-bf16",
        )

    _run(_submit_handler(captured=captured), body)
    graph = captured["last_prompt_body"]["prompt"]
    assert graph["1"]["inputs"]["unet_name"] == "krea2_turbo_bf16.safetensors"
    assert graph["2"]["inputs"]["clip_name"] == "qwen3vl_4b_bf16.safetensors"
    assert graph["3"]["inputs"]["vae_name"] == "qwen_image_vae.safetensors"


def test_submit_accepts_checkpoint_filename_form(tmp_path, monkeypatch):
    """AC#2: model=<checkpoint filename> keeps working (verbatim) even with the
    manifest present."""
    _write_catalog_manifest(tmp_path, monkeypatch)
    captured: Dict[str, Any] = {}

    async def body(client):
        return await client.submit_media_operation(
            modality="image", input_payload={"prompt": "obs", "seed": 1},
            model="krea2_turbo_bf16.safetensors",
        )

    _run(_submit_handler(captured=captured), body)
    graph = captured["last_prompt_body"]["prompt"]
    assert graph["1"]["inputs"]["unet_name"] == "krea2_turbo_bf16.safetensors"


def test_submit_rejects_unknown_model_with_both_forms(tmp_path, monkeypatch):
    """AC#3: an unknown token 400s (ValueError) with a message naming both
    accepted forms."""
    _write_catalog_manifest(tmp_path, monkeypatch)

    async def body(client):
        with pytest.raises(ValueError) as excinfo:
            await client.submit_media_operation(
                modality="image", input_payload={"prompt": "obs"},
                model="totally-unknown-model",
            )
        message = str(excinfo.value)
        assert "catalog" in message and "filename" in message
        assert "krea2-turbo-bf16" in message  # lists a known catalog name

    _run(lambda r: httpx.Response(200, json={}), body)


def test_submit_without_manifest_falls_back_to_verbatim(tmp_path, monkeypatch):
    """No manifest (ComfyUI disabled / not started) → verbatim passthrough, so
    an unknown token is NOT pre-rejected (ComfyUI does the final validation)."""
    monkeypatch.setenv("COMFYUI_MANIFEST_PATH", str(tmp_path / "does-not-exist.yaml"))
    captured: Dict[str, Any] = {}

    async def body(client):
        return await client.submit_media_operation(
            modality="image", input_payload={"prompt": "obs", "seed": 1},
            model="sdxl_base.safetensors",
        )

    _run(_submit_handler(captured=captured), body)
    graph = captured["last_prompt_body"]["prompt"]
    assert graph["1"]["inputs"]["ckpt_name"] == "sdxl_base.safetensors"
