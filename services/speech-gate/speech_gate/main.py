"""Voice-activity gate in front of Speaches' OpenAI-compatible transcription route.

POST /v1/audio/transcriptions runs the Silero rule on the upload. Speech is
forwarded unchanged to Speaches. Non-speech returns an empty transcript in the
shape Speaches uses for the requested response_format, and Speaches is not called.
Every other path is not proxied.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from anyio import to_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.datastructures import UploadFile

from speech_gate.audio import UndecodableAudio, iter_mono_16k
from speech_gate.vad import SAMPLE_RATE, SileroVad

log = logging.getLogger("speech_gate")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
RESPONSE_FORMATS = frozenset({"json", "text", "srt", "verbose_json", "vtt"})
TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
DEFAULT_UPSTREAM_URL = "http://speaches:8000"


class _BodyTooLarge(Exception):
    pass


class BodySizeLimit:
    """Reject request bodies over the limit while they stream in.

    Starlette caps only non-file multipart parts, and it spools file parts to
    disk without a limit, so the byte count is checked at the ASGI layer.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            response = JSONResponse({"detail": "upload exceeds the size limit"}, status_code=413)
            await response(scope, receive, send)


def create_app(
    *,
    vad: SileroVad | None = None,
    upstream_url: str | None = None,
    upstream_transport: httpx2.AsyncBaseTransport | None = None,
    upstream_timeout_seconds: float | None = None,
) -> FastAPI:
    """Build the gate. Tests inject ``vad`` and ``upstream_transport``."""
    base_url = (upstream_url or os.environ.get("SPEECH_GATE_UPSTREAM_URL") or DEFAULT_UPSTREAM_URL).rstrip("/")
    timeout = upstream_timeout_seconds or float(
        os.environ.get("SPEECH_GATE_UPSTREAM_TIMEOUT_SECONDS", "600")
    )

    # Built here, not in the lifespan, so test transports that skip lifespan still
    # get a model and a client. A bad Silero hash raises before the server binds.
    upstream = httpx2.AsyncClient(timeout=timeout, transport=upstream_transport)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await upstream.aclose()

    app = FastAPI(
        title="Speech gate",
        description="Voice-activity check in front of Speaches transcription.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.vad = vad if vad is not None else SileroVad()
    app.state.upstream = upstream
    app.state.upstream_url = base_url
    app.add_middleware(BodySizeLimit, max_bytes=MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(TRANSCRIPTIONS_PATH)
    async def transcribe(request: Request) -> Response:
        form = await request.form(max_files=1, max_fields=64, max_part_size=64 * 1024)
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            return JSONResponse({"detail": "the file field is required"}, status_code=422)
        fields = [(key, value) for key, value in form.multi_items() if key != "file" and isinstance(value, str)]
        response_format = dict(fields).get("response_format", "json")
        if response_format not in RESPONSE_FORMATS:
            return JSONResponse(
                {"detail": f"response_format must be one of {sorted(RESPONSE_FORMATS)}"},
                status_code=422,
            )

        data = await upload.read()
        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse({"detail": "upload exceeds the size limit"}, status_code=413)
        duration = 0.0
        try:
            speech = await to_thread.run_sync(
                lambda: app.state.vad.is_speech(lambda: iter_mono_16k(data))
            )
            if not speech and response_format == "verbose_json":
                duration = await to_thread.run_sync(lambda: _duration_seconds(data))
        except UndecodableAudio as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

        if not speech:
            log.info("speech gate: non-speech upload of %d bytes not forwarded", len(data))
            return _empty_transcript(response_format, duration)

        files = {"file": (upload.filename or "audio", data, upload.content_type or "application/octet-stream")}
        return await _forward(request, files, fields)

    return app


def _empty_transcript(response_format: str, duration: float) -> Response:
    """Empty result with the keys Speaches returns for the same response_format.

    Speaches 0.9.0-rc.3 returns {"text", "logprobs", "usage"} for json and
    {"duration", "language", "text", "segments", "usage", "words"} for
    verbose_json. The language is unknown for silence, so it stays empty.
    """
    if response_format in ("text", "srt", "vtt"):
        return PlainTextResponse("")
    if response_format == "verbose_json":
        return JSONResponse(
            {
                "duration": round(duration, 3),
                "language": "",
                "text": "",
                "segments": [],
                "usage": None,
                "words": None,
            }
        )
    return JSONResponse({"text": "", "logprobs": None, "usage": None})


def _duration_seconds(data: bytes) -> float:
    samples = sum(len(chunk) for chunk in iter_mono_16k(data))
    return samples / SAMPLE_RATE


async def _forward(
    request: Request,
    files: dict[str, tuple[str, bytes, str]],
    fields: list[tuple[str, str]],
) -> Response:
    headers = {}
    authorization = request.headers.get("authorization")
    if authorization:
        headers["Authorization"] = authorization
    try:
        upstream = await request.app.state.upstream.post(
            f"{request.app.state.upstream_url}{TRANSCRIPTIONS_PATH}",
            data=_form_data(fields),
            files=files,
            headers=headers,
        )
    except httpx2.TimeoutException:
        return JSONResponse({"detail": "speaches timed out"}, status_code=504)
    except httpx2.HTTPError:
        return JSONResponse({"detail": "speaches is unreachable"}, status_code=502)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


def _form_data(fields: list[tuple[str, str]]) -> dict[str, list[str] | str]:
    grouped: dict[str, list[str] | str] = {}
    for key, value in fields:
        if key in grouped:
            existing = grouped[key]
            grouped[key] = [*existing, value] if isinstance(existing, list) else [existing, value]
        else:
            grouped[key] = value
    return grouped


app = create_app()
