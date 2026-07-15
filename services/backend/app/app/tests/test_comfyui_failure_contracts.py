from __future__ import annotations

import logging

import pytest


@pytest.mark.asyncio
async def test_queue_prompt_redacts_upstream_failure(monkeypatch, caplog):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def fail(*_args, **_kwargs):
        raise RuntimeError("SENTINEL_COMFY_SECRET")

    monkeypatch.setattr(client.client, "post", fail)
    with caplog.at_level(logging.ERROR):
        result = await client.queue_prompt({})
    await client.client.aclose()

    assert result == {"success": False, "error": "ComfyUI prompt submission failed"}
    assert "SENTINEL_COMFY_SECRET" not in caplog.text


def test_comfyui_tool_does_not_embed_image_data_or_backend_url():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[4]
        / "open-webui/extras/tools/comfyui_image_generation_tool.py"
    ).read_text(encoding="utf-8")

    assert "base64.b64encode" not in source
    assert "Backend URL:" not in source
    assert "result.get('error'" not in source


class _StreamResponse:
    def __init__(self, chunks, content_length=None):
        self._chunks = chunks
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        _StreamResponse([], content_length=5),
        _StreamResponse([b"123", b"45"]),
    ],
)
async def test_image_download_rejects_declared_and_streamed_oversize(response):
    import comfyui_client

    class Client:
        def stream(self, *_args, **_kwargs):
            return response

    client = comfyui_client.ComfyUIClient()
    await client.client.aclose()
    client.client = Client()
    client.max_image_bytes = 4

    with pytest.raises(ValueError, match="byte limit"):
        await client.get_image_data("large.png")
