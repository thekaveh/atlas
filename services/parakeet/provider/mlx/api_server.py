"""Authenticated OpenAI-compatible API for native Parakeet MLX."""

import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, status

from bounded_upload import (
    MAX_BODY_TIMEOUT_SECONDS,
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
    parse_positive_int,
    parse_timeout_seconds,
    run_with_deadline,
)
from startup import ModelStartup, model_lifespan

if __package__:
    from .alignment import advanced_payload, alignment_payload, result_text
else:
    from alignment import advanced_payload, alignment_payload, result_text

try:
    from parakeet_mlx import from_pretrained
except ImportError as exc:
    logging.error(
        "Failed to import parakeet_mlx (error_type=%s)",
        type(exc).__name__,
    )
    raise


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_MAX_UPLOAD_BYTES = parse_positive_int(
    "PARAKEET_MAX_UPLOAD_BYTES", default=104_857_600
)
_UPLOAD_TIMEOUT_SECONDS = parse_positive_int(
    "PARAKEET_UPLOAD_TIMEOUT_SECONDS",
    default=120,
    maximum=MAX_BODY_TIMEOUT_SECONDS,
)


def _load_model():
    model_name = os.getenv("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")
    logger.info("Loading Parakeet model")
    model = from_pretrained(model_name)
    logger.info("Model loaded successfully")
    return model


_model_startup = ModelStartup(
    "PARAKEET",
    _load_model,
    timeout_seconds=parse_timeout_seconds("PARAKEET"),
)
_transcription_semaphore = threading.BoundedSemaphore(
    max(1, int(os.getenv("PARAKEET_CONCURRENCY", "1")))
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with model_lifespan(app, _model_startup):
        yield


app = FastAPI(
    title="Parakeet MLX STT API",
    version="1.0.0",
    description="OpenAI-compatible Speech-to-Text API using Parakeet MLX",
    lifespan=lifespan,
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=multipart_body_limit(_MAX_UPLOAD_BYTES),
    body_timeout_seconds=_UPLOAD_TIMEOUT_SECONDS,
    paths={"/v1/audio/transcriptions", "/v1/audio/transcriptions/advanced"},
)
_BOUNDARY_SETTINGS = load_boundary_settings(
    "PARAKEET",
    {"/v1/audio/transcriptions", "/v1/audio/transcriptions/advanced"},
)
install_provider_boundary(app, _BOUNDARY_SETTINGS)


def _require_ready() -> None:
    if _model_startup.state != "healthy" or _model_startup.model is None:
        detail = (
            "Service is loading"
            if _model_startup.state == "loading"
            else "Service is unhealthy"
        )
        raise HTTPException(status_code=503, detail=detail)


def _transcribe(file_path: str):
    model = _model_startup.model
    if model is None:
        raise RuntimeError("Parakeet model is unavailable")
    with _transcription_semaphore:
        logger.info("Transcribing uploaded file")
        return model.transcribe(file_path)


def _transcribe_standard(file_path: str, response_format: str, language: Optional[str]):
    result = _transcribe(file_path)
    transcribed_text = result_text(result)
    logger.info("Transcription complete (characters=%s)", len(transcribed_text))
    if response_format == "text":
        return transcribed_text
    if response_format == "verbose_json":
        payload = alignment_payload(result)
        payload.update({"task": "transcribe", "language": language or "unknown"})
        payload.pop("words")
        return payload
    return {"text": transcribed_text}


def _transcribe_advanced(
    file_path: str,
    return_timestamps: bool,
    word_timestamps: bool,
):
    result = _transcribe(file_path)
    return advanced_payload(result, return_timestamps, word_timestamps)


@app.get("/")
async def root():
    return {
        "name": "Parakeet MLX STT API",
        "version": "1.0.0",
        "description": "OpenAI-compatible Speech-to-Text API",
        "backend": "mlx",
        "device": "mps",
        "model": os.getenv("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3"),
    }


@app.get("/health")
async def health(response: Response):
    ready = _model_startup.state == "healthy" and _model_startup.model is not None
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "healthy" if ready else _model_startup.state,
        "backend": "mlx",
        "device": "mps",
        "model": os.getenv("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3"),
        "model_loaded": ready,
    }


@app.post("/v1/audio/transcriptions")
async def transcribe_audio(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: Literal["json", "verbose_json", "text"] = Form("json"),
    temperature: Optional[float] = Form(0.0),
):
    del model, prompt, temperature
    _require_ready()
    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = await spool_upload(file, max_bytes=_MAX_UPLOAD_BYTES, suffix=suffix)
        return await run_with_deadline(
            "PARAKEET",
            lambda: _transcribe_standard(
                str(tmp_path),
                response_format,
                language,
            ),
        )
    except ProviderDeadlineExceeded:
        return fatal_timeout_response("PARAKEET")
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except EmptyUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Transcription failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Transcription failed") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@app.post("/v1/audio/transcriptions/advanced")
async def transcribe_advanced(
    file: UploadFile = File(...),
    return_timestamps: bool = Form(default=False),
    word_timestamps: bool = Form(False),
):
    _require_ready()
    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = await spool_upload(file, max_bytes=_MAX_UPLOAD_BYTES, suffix=suffix)
        return await run_with_deadline(
            "PARAKEET",
            lambda: _transcribe_advanced(
                str(tmp_path),
                return_timestamps,
                word_timestamps,
            ),
        )
    except ProviderDeadlineExceeded:
        return fatal_timeout_response("PARAKEET")
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except EmptyUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Advanced transcription failed (error_type=%s)",
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Transcription failed") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PARAKEET_LOCALHOST_PORT") or 63042)
    bind_host = os.getenv("PARAKEET_LOCALHOST_BIND_HOST", "127.0.0.1")
    uvicorn.run(app, host=bind_host, port=port)
