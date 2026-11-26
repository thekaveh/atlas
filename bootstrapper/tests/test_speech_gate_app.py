"""Speech gate HTTP behaviour: forwarding, empty transcripts, and error mapping.

Speaches is replaced by an httpx MockTransport. The Silero model is the real
pinned file, so the speech and silence decisions are real.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import wave
from pathlib import Path

import httpx2
import numpy as np
import pytest
from fastapi import FastAPI, Request

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_ROOT = REPO_ROOT / "services" / "speech-gate"
TESTDATA = GATE_ROOT / "testdata"
if str(GATE_ROOT) not in sys.path:
    sys.path.insert(0, str(GATE_ROOT))

from speech_gate import main  # noqa: E402
from speech_gate.main import BodySizeLimit, create_app  # noqa: E402
from speech_gate.vad import SileroVad  # noqa: E402

UPSTREAM = "http://speaches.test:8000"
SPEECH = (TESTDATA / "kokoro-speech-16k.wav").read_bytes()


def _silence_wav(seconds: float = 3.0) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


SILENCE = _silence_wav()


@pytest.fixture(scope="module")
def vad() -> SileroVad:
    return SileroVad()


def _run(coroutine):
    return asyncio.run(coroutine)


def _gate(vad: SileroVad, handler) -> tuple[httpx2.AsyncClient, list[httpx2.Request]]:
    calls: list[httpx2.Request] = []

    async def recording(request: httpx2.Request) -> httpx2.Response:
        await request.aread()
        calls.append(request)
        return await handler(request)

    app = create_app(
        vad=vad,
        upstream_url=UPSTREAM,
        upstream_transport=httpx2.MockTransport(recording),
    )
    client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://gate")
    return client, calls


async def _ok_upstream(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json={"text": "The quick brown fox."})


async def _post(client, data=None, file=SPEECH, headers=None):
    files = {"file": ("clip.wav", file, "audio/wav")} if file is not None else None
    return await client.post(
        "/v1/audio/transcriptions", data=data or {"model": "whisper-1"}, files=files, headers=headers
    )


def test_speech_is_forwarded_unchanged_with_fields_and_auth(vad: SileroVad) -> None:
    async def scenario():
        client, calls = _gate(vad, _ok_upstream)
        async with client:
            response = await _post(
                client,
                data={"model": "whisper-1", "timestamp_granularities[]": ["word", "segment"]},
                headers={"Authorization": "Bearer speaches-key"},
            )
        return response, calls

    response, calls = _run(scenario())
    assert response.status_code == 200
    assert response.json() == {"text": "The quick brown fox."}
    assert len(calls) == 1
    forwarded = calls[0]
    assert str(forwarded.url) == f"{UPSTREAM}/v1/audio/transcriptions"
    assert forwarded.headers["authorization"] == "Bearer speaches-key"
    body = forwarded.content
    assert SPEECH in body
    assert b'name="model"' in body and b"whisper-1" in body
    assert body.count(b'name="timestamp_granularities[]"') == 2


def test_upstream_status_and_body_pass_through(vad: SileroVad) -> None:
    async def not_installed(request):
        return httpx2.Response(404, json={"detail": "Model is not installed locally"})

    async def scenario():
        client, _ = _gate(vad, not_installed)
        async with client:
            return await _post(client)

    response = _run(scenario())
    assert response.status_code == 404
    assert response.json() == {"detail": "Model is not installed locally"}


def test_silence_returns_empty_json_and_skips_speaches(vad: SileroVad) -> None:
    async def scenario():
        client, calls = _gate(vad, _ok_upstream)
        async with client:
            response = await _post(client, file=SILENCE)
        return response, calls

    response, calls = _run(scenario())
    assert response.status_code == 200
    assert response.json() == {"text": "", "logprobs": None, "usage": None}
    assert calls == []


def test_silence_text_format_returns_empty_plain_text(vad: SileroVad) -> None:
    async def scenario():
        client, calls = _gate(vad, _ok_upstream)
        async with client:
            response = await _post(client, data={"model": "whisper-1", "response_format": "text"}, file=SILENCE)
        return response, calls

    response, calls = _run(scenario())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == ""
    assert calls == []


def test_silence_verbose_json_has_speaches_keys(vad: SileroVad) -> None:
    async def scenario():
        client, calls = _gate(vad, _ok_upstream)
        async with client:
            response = await _post(client, data={"model": "whisper-1", "response_format": "verbose_json"}, file=SILENCE)
        return response, calls

    response, calls = _run(scenario())
    body = response.json()
    assert set(body) == {"duration", "language", "text", "segments", "usage", "words"}
    assert body["text"] == "" and body["segments"] == []
    assert body["duration"] == pytest.approx(3.0, abs=0.01)
    assert calls == []


def test_unknown_response_format_is_rejected_before_vad(vad: SileroVad) -> None:
    async def scenario():
        client, calls = _gate(vad, _ok_upstream)
        async with client:
            response = await _post(client, data={"model": "whisper-1", "response_format": "mp3"})
        return response, calls

    response, calls = _run(scenario())
    assert response.status_code == 422
    assert calls == []


def test_missing_file_is_422(vad: SileroVad) -> None:
    async def scenario():
        client, calls = _gate(vad, _ok_upstream)
        async with client:
            response = await _post(client, file=None)
        return response, calls

    response, calls = _run(scenario())
    assert response.status_code == 422
    assert calls == []


def test_undecodable_upload_is_400_and_not_forwarded(vad: SileroVad) -> None:
    async def scenario():
        client, calls = _gate(vad, _ok_upstream)
        async with client:
            response = await _post(client, file=b"definitely not audio")
        return response, calls

    response, calls = _run(scenario())
    assert response.status_code == 400
    assert calls == []


def test_unreachable_speaches_is_502(vad: SileroVad) -> None:
    async def refused(request):
        raise httpx2.ConnectError("connection refused")

    async def scenario():
        client, _ = _gate(vad, refused)
        async with client:
            return await _post(client)

    assert _run(scenario()).status_code == 502


def test_speaches_timeout_is_504(vad: SileroVad) -> None:
    async def slow(request):
        raise httpx2.ReadTimeout("too slow")

    async def scenario():
        client, _ = _gate(vad, slow)
        async with client:
            return await _post(client)

    assert _run(scenario()).status_code == 504


def test_oversize_upload_is_413(vad: SileroVad, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1000)

    async def scenario():
        client, calls = _gate(vad, _ok_upstream)
        async with client:
            response = await _post(client, file=SPEECH)
        return response, calls

    response, calls = _run(scenario())
    assert response.status_code == 413
    assert calls == []


def test_body_size_limit_rejects_streamed_bodies_over_the_limit() -> None:
    probe = FastAPI()

    @probe.post("/probe")
    async def probe_route(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    probe.add_middleware(BodySizeLimit, max_bytes=100)

    async def scenario():
        transport = httpx2.ASGITransport(app=probe)
        async with httpx2.AsyncClient(transport=transport, base_url="http://gate") as client:
            small = await client.post("/probe", content=b"a" * 50)
            large = await client.post("/probe", content=b"a" * 200)
        return small, large

    small, large = _run(scenario())
    assert small.status_code == 200 and small.json() == {"bytes": 50}
    assert large.status_code == 413


def test_only_transcriptions_are_proxied(vad: SileroVad) -> None:
    async def scenario():
        client, calls = _gate(vad, _ok_upstream)
        async with client:
            speech_route = await client.post("/v1/audio/speech", json={"input": "hi"})
            translations = await client.post(
                "/v1/audio/translations", files={"file": ("a.wav", SPEECH, "audio/wav")}
            )
        return speech_route, translations, calls

    speech_route, translations, calls = _run(scenario())
    assert speech_route.status_code == 404
    assert translations.status_code == 404
    assert calls == []


def test_health_reports_ok(vad: SileroVad) -> None:
    async def scenario():
        client, _ = _gate(vad, _ok_upstream)
        async with client:
            return await client.get("/health")

    response = _run(scenario())
    assert response.status_code == 200
    assert json.loads(response.text) == {"status": "ok"}
