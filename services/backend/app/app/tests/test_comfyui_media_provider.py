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


def test_extension_and_content_type_sniffing():
    assert cmc._guess_extension("https://x/y.png", b"") == "png"
    assert cmc._guess_extension("https://x/y.JPG", b"") == "jpg"
    assert cmc._guess_extension("https://x/y", b"\x89PNG\r\n\x1a\n") == "png"
    assert cmc._guess_extension("data:x", b"\xff\xd8\xff") == "jpg"
    assert cmc._content_type_for("a.png") == "image/png"
    assert cmc._content_type_for("a.jpeg") == "image/jpeg"
    assert cmc._content_type_for("a.webp") == "image/webp"


def test_first_present_and_coerce():
    assert cmc._first_present({"b": "x"}, ("a", "b")) == "x"
    assert cmc._first_present({}, ("a", "b")) is None
    assert cmc._coerce_float("0.4", default=0.75) == 0.4
    assert cmc._coerce_float(None, default=0.75) == 0.75
    assert cmc._coerce_float("nope", default=0.75) == 0.75


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
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _client_with_transport(handler: Callable[[httpx.Request], httpx.Response]) -> ComfyUIMediaClient:
    """Build a ComfyUIMediaClient whose httpx calls are routed by `handler`."""
    client = ComfyUIMediaClient(base_url="http://comfyui.test")
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def _run(handler, body: Callable[[ComfyUIMediaClient], Any]) -> Any:
    """Drive an async test body against a mocked client inside one event loop
    (the client + its httpx connection share the loop)."""

    async def _driver():
        client = _client_with_transport(handler)
        try:
            return await body(client)
        finally:
            await client.client.aclose()

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


def test_cancel_deletes_queue_and_interrupts_when_running():
    calls = []

    def handler(request):
        url = str(request.url)
        calls.append((request.method, url))
        if url.endswith("/queue") and request.method == "GET":
            return httpx.Response(200, json={"queue_running": [[0, "pid-r"]], "queue_pending": []})
        if url.endswith("/interrupt"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    async def body(client):
        ok = await client.cancel_media_operation(operation_id="pid-r", modality="image")
        assert ok is True

    _run(handler, body)
    # queue GET, queue POST (delete), interrupt POST — interrupt fires because
    # the prompt was in queue_running.
    assert any(m == "POST" and u.endswith("/interrupt") for (m, u) in calls)
    assert any(m == "POST" and u.endswith("/queue") for (m, u) in calls)


def test_cancel_pending_only_does_not_interrupt():
    def handler(request):
        if request.url.path.endswith("/queue") and request.method == "GET":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": [[1, "pid-p"]]})
        if request.url.path.endswith("/interrupt"):
            pytest.fail("interrupt must not fire for a pending-only prompt")
        return httpx.Response(200, json={})

    async def body(client):
        ok = await client.cancel_media_operation(operation_id="pid-p", modality="image")
        assert ok is True  # queue-delete only; no interrupt needed

    _run(handler, body)


def test_cancel_returns_false_on_transport_failure():
    def handler(request):
        return httpx.Response(500)

    async def body(client):
        ok = await client.cancel_media_operation(operation_id="pid", modality="image")
        assert ok is False

    _run(handler, body)


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
            return httpx.Response(200, json={"name": "stored.png", "subfolder": "", "type": "input"})
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
    assert graph["5"]["inputs"]["image"] == "stored.png"
    # strength drives KSampler denoise, clamped to [0,1]
    assert graph["4"]["inputs"]["denoise"] == 0.4


def test_img2img_fetches_url_init_image():
    captured: Dict[str, Any] = {}

    def handler(request):
        url = str(request.url)
        if "remote-img.test" in url:
            return httpx.Response(200, content=PNG_MAGIC, headers={"content-type": "image/png"})
        if url.endswith("/upload/image"):
            return httpx.Response(200, json={"name": "u.png", "subfolder": "s", "type": "input"})
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
    assert graph["5"]["inputs"]["image"] == "s/u.png"


def test_img2img_rejects_non_url_non_data_source():
    async def body(client):
        with pytest.raises(ValueError):
            await client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "x", "image": "/local/path.png"},
                model="sdxl_base.safetensors",
            )

    _run(lambda r: httpx.Response(200, json={}), body)


def test_img2img_url_init_image_rejects_oversized_declared_length():
    """A caller-supplied init-image URL whose Content-Length exceeds the byte
    cap is rejected before the body is buffered → ValueError → gateway 400, so
    a huge remote file can't OOM the worker."""

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


def test_img2img_url_init_image_caps_streamed_body_without_content_length():
    """When no Content-Length is declared (chunked transfer — the case a
    malicious upstream would use to slip past the header precheck), the cap is
    still enforced incrementally while streaming, not after buffering."""

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
