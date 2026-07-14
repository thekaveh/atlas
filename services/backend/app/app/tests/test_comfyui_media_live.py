"""Live end-to-end smoke for the ComfyUI media provider (#519).

Validates the real ``ComfyUIMediaClient`` (graph building, submit, poll,
artifact extraction, img2img upload) against a running ComfyUI host.
Self-skips unless ``ATLAS_COMFYUI_LIVE_ENDPOINT`` is set, so it never runs
in CI — it is the hardware/paid gate for AC #4/#5 of issue #519 (text2img +
img2img produce a correct artifact end-to-end against a managed host).

Run locally:
    ATLAS_COMFYUI_LIVE_ENDPOINT=http://localhost:8188 \\
        ATLAS_COMFYUI_LIVE_MODEL=krea2-turbo-bf16 \\
        .venv/bin/python -m pytest tests/test_comfyui_media_live.py -q
"""
import asyncio
import base64
import os
import struct
import time

import pytest

import comfyui_media_client
from comfyui_media_client import ComfyUIMediaClient


def _live_endpoint():
    return (os.environ.get("ATLAS_COMFYUI_LIVE_ENDPOINT") or "").strip().rstrip("/")


pytestmark = pytest.mark.skipif(
    not _live_endpoint(),
    reason="set ATLAS_COMFYUI_LIVE_ENDPOINT to run the ComfyUI media live smoke",
)


def _default_model():
    # The managed Apple-Silicon/MPS host ships the Krea 2 catalog; a caller
    # can override with any installed checkpoint via the env var.
    return (os.environ.get("ATLAS_COMFYUI_LIVE_MODEL") or "krea2-turbo-bf16").strip()


def _poll_until_terminal(client, operation_id, timeout=1800):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = asyncio.run(
            client.get_media_operation(operation_id=operation_id, modality="image")
        )
        status = payload["status"]
        if status in ("succeeded", "failed", "cancelled", "timeout"):
            return payload
        time.sleep(2)
    raise AssertionError(f"ComfyUI operation {operation_id} did not finish before timeout")


def test_live_text2img_produces_png_artifact():
    endpoint = _live_endpoint()
    model = _default_model()
    client = ComfyUIMediaClient(base_url=endpoint, model=model)
    try:
        submitted = asyncio.run(
            client.submit_media_operation(
                modality="image",
                input_payload={"prompt": "a calm blue glass observatory at night", "seed": 337},
                model=model,
            )
        )
        assert submitted["status"] == "queued"
        assert submitted["cost_usd"] == 0.0
        operation_id = submitted["operation_id"]

        settled = _poll_until_terminal(client, operation_id)
        assert settled["status"] == "succeeded", settled
        assert settled["artifact_url"], "no artifact_url returned"
        # artifact_url is the backend proxy path; the filename must be present.
        assert "/comfyui/image/" in settled["artifact_url"]
        assert settled["artifacts"], "no artifacts extracted"
    finally:
        asyncio.run(client.client.aclose())


def test_live_img2img_remixes_init_image():
    endpoint = _live_endpoint()
    model = _default_model()
    client = ComfyUIMediaClient(base_url=endpoint, model=model)
    # A tiny 2x2 PNG init image (data URI).
    png_bytes = _minimal_png(2, 2)
    init_data_uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    try:
        submitted = asyncio.run(
            client.submit_media_operation(
                modality="image",
                input_payload={
                    "prompt": "concept variation",
                    "image_url": init_data_uri,
                    "strength": 0.8,
                },
                model=model,
            )
        )
        operation_id = submitted["operation_id"]
        settled = _poll_until_terminal(client, operation_id)
        assert settled["status"] == "succeeded", settled
        assert settled["artifact_url"]
    finally:
        asyncio.run(client.client.aclose())


def _minimal_png(width: int, height: int) -> bytes:
    """Hand-build a minimal valid PNG (no Pillow dependency in this suite)."""
    import zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b""
    for _ in range(height):
        raw += b"\x00" + b"\xff\x00\x00" * width  # filter byte + red pixels
    idat = zlib.compress(raw)
    return signature + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
