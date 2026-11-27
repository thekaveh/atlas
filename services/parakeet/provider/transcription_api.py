"""OpenAI-compatible Parakeet HTTP surface shared by the GPU and MLX providers.

Each provider loads its own model, then passes a ProviderHooks record to
create_api(). The hooks cover the parts that differ: how a transcription is run
and shaped, what the root and health endpoints report, and when the model counts
as loaded. Upload spooling, the request-body cap, the provider boundary, the
native deadline, readiness gating, and generic error mapping are identical for
both providers and live here.
"""

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Optional

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, status

from bounded_upload import (
    EmptyUploadError,
    RequestBodyLimitMiddleware,
    UploadTooLargeError,
    multipart_body_limit,
    spool_upload,
)
from provider_boundary import (
    ProviderDeadlineExceeded,
    fatal_timeout_response,
    install_provider_boundary,
    load_boundary_settings,
    run_with_deadline,
)
from startup import ModelStartup, model_lifespan


API_VERSION = "1.0.0"
API_DESCRIPTION = "OpenAI-compatible Speech-to-Text API"
TRANSCRIPTION_PATHS = frozenset(
    {"/v1/audio/transcriptions", "/v1/audio/transcriptions/advanced"}
)


@dataclass(frozen=True)
class ProviderHooks:
    """Everything a provider supplies to create_api().

    describe() returns the backend, device, and model keys shown by root and health.
    transcribe_standard(path, *, response_format, language, temperature) returns a
    dict or a Response; it runs inside the native deadline.
    transcribe_advanced(path, *, return_timestamps, word_timestamps) returns a dict.
    """

    provider: str
    title: str
    description: str
    logger_name: str
    model_startup: ModelStartup
    is_loaded: Callable[[], bool]
    describe: Callable[[], Mapping[str, str]]
    transcribe_standard: Callable[..., Any]
    transcribe_advanced: Callable[..., Any]
    max_upload_bytes: int
    upload_timeout_seconds: int


def create_api(hooks: ProviderHooks) -> FastAPI:
    provider = hooks.provider
    model_startup = hooks.model_startup
    max_upload_bytes = hooks.max_upload_bytes
    upload_timeout_seconds = hooks.upload_timeout_seconds

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with model_lifespan(app, model_startup):
            yield

    app = FastAPI(
        title=hooks.title,
        version=API_VERSION,
        description=hooks.description,
        lifespan=lifespan,
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=multipart_body_limit(max_upload_bytes),
        body_timeout_seconds=upload_timeout_seconds,
        paths=TRANSCRIPTION_PATHS,
    )
    install_provider_boundary(app, load_boundary_settings(provider, TRANSCRIPTION_PATHS))
    _register_status_routes(app, hooks)
    _register_transcription_routes(app, hooks)
    return app


def _require_ready(hooks: ProviderHooks) -> None:
    state = hooks.model_startup.state
    if state != "healthy" or not hooks.is_loaded():
        detail = "Service is loading" if state == "loading" else "Service is unhealthy"
        raise HTTPException(status_code=503, detail=detail)


def _register_status_routes(app: FastAPI, hooks: ProviderHooks) -> None:
    model_startup = hooks.model_startup

    @app.get("/")
    async def root():
        return {
            "name": hooks.title,
            "version": API_VERSION,
            "description": API_DESCRIPTION,
            **hooks.describe(),
        }

    @app.get("/health")
    async def health(response: Response):
        ready = model_startup.state == "healthy" and hooks.is_loaded()
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "healthy" if ready else model_startup.state,
            "model_loaded": ready,
            **hooks.describe(),
        }


async def _transcribe_upload(
    hooks: ProviderHooks,
    file: UploadFile,
    operation: Callable[[str], Any],
    failure_log: str,
):
    logger = logging.getLogger(hooks.logger_name)
    provider = hooks.provider
    max_upload_bytes = hooks.max_upload_bytes
    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = await spool_upload(file, max_bytes=max_upload_bytes, suffix=suffix)
        return await run_with_deadline(
            provider,
            lambda: operation(str(tmp_path)),
        )
    except ProviderDeadlineExceeded:
        return fatal_timeout_response(provider)
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except EmptyUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(failure_log, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Transcription failed") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _register_transcription_routes(app: FastAPI, hooks: ProviderHooks) -> None:
    @app.post("/v1/audio/transcriptions")
    async def transcribe(
        file: UploadFile = File(...),
        model: str = Form(default="parakeet-tdt-0.6b-v3"),
        language: Optional[str] = Form(default=None),
        prompt: Optional[str] = Form(default=None),
        response_format: Literal["json", "verbose_json", "text"] = Form(default="json"),
        temperature: float = Form(default=0.0),
    ):
        del model, prompt
        _require_ready(hooks)
        return await _transcribe_upload(
            hooks,
            file,
            lambda path: hooks.transcribe_standard(
                path,
                response_format=response_format,
                language=language,
                temperature=temperature,
            ),
            "Transcription failed (error_type=%s)",
        )

    @app.post("/v1/audio/transcriptions/advanced")
    async def transcribe_advanced_route(
        file: UploadFile = File(...),
        return_timestamps: bool = Form(default=False),
        word_timestamps: bool = Form(default=False),
    ):
        _require_ready(hooks)
        return await _transcribe_upload(
            hooks,
            file,
            lambda path: hooks.transcribe_advanced(
                path,
                return_timestamps=return_timestamps,
                word_timestamps=word_timestamps,
            ),
            "Advanced transcription failed (error_type=%s)",
        )
