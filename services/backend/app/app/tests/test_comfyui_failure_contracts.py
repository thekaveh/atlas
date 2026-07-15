from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path

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


@pytest.mark.asyncio
async def test_history_outage_is_distinct_from_empty_history(monkeypatch, caplog):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def fail(*_args, **_kwargs):
        raise RuntimeError("SENTINEL_HISTORY_SECRET")

    monkeypatch.setattr(client.client, "get", fail)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(
            comfyui_client.ComfyUIHistoryUnavailableError,
            match="ComfyUI history is unavailable",
        ):
            await client.get_history("prompt-1")
    await client.client.aclose()

    assert "SENTINEL_HISTORY_SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_completion_history_outage_fails_without_polling(monkeypatch):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def fail(_prompt_id):
        raise comfyui_client.ComfyUIHistoryUnavailableError(
            "ComfyUI history is unavailable"
        )

    monkeypatch.setattr(client, "get_history", fail)
    result = await client.wait_for_completion("prompt-1", timeout=300)
    await client.client.aclose()

    assert result == {
        "success": False,
        "error": "ComfyUI history is unavailable",
        "prompt_id": "prompt-1",
    }


@pytest.mark.asyncio
async def test_completion_deadline_bounds_slow_history(monkeypatch):
    import comfyui_client

    client = comfyui_client.ComfyUIClient()

    async def slow(_prompt_id):
        await asyncio.sleep(10)
        return {}

    monkeypatch.setattr(client, "get_history", slow)
    result = await client.wait_for_completion("prompt-1", timeout=0.01)
    await client.client.aclose()

    assert result == {
        "success": False,
        "error": "Timeout waiting for completion",
    }


def test_open_webui_tool_propagates_deadline_and_returns_artifact(monkeypatch):
    tool_path = (
        Path(__file__).resolve().parents[4]
        / "open-webui/extras/tools/comfyui_image_generation_tool.py"
    )
    spec = importlib.util.spec_from_file_location("comfyui_image_tool_test", tool_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = {}

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *_args, **_kwargs: Response({"status": "healthy"}),
    )

    def post(*_args, **kwargs):
        captured.update(kwargs)
        return Response(
            {
                "success": True,
                "prompt_id": "prompt-1",
                "data": {
                    "outputs": {
                        "7": {"images": [{"filename": "atlas-output.png"}]}
                    },
                    "parameters": {},
                },
            }
        )

    monkeypatch.setattr(module.requests, "post", post)
    tool = module.Tools()
    tool.valves.timeout = 321

    result = tool.generate_image("blue orbital archive")

    assert captured["json"]["timeout_seconds"] == 321
    assert captured["timeout"] == 326
    assert "atlas-output.png" in result
    assert "base64" not in result.lower()


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
