"""Authenticated OpenAI-compatible Parakeet API for the GPU container."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, status

from bounded_upload import EmptyUploadError, UploadTooLargeError, spool_upload
from provider_boundary import (
    ProviderDeadlineExceeded,
    fatal_timeout_response,
    install_provider_boundary,
    load_boundary_settings,
    parse_positive_int,
    parse_timeout_seconds,
    run_with_deadline,
)
from startup import ModelStartup

try:
    from transcribe import load_model, model_is_loaded, transcribe_audio_sync
except ImportError as exc:
    logging.error(
        "Failed to import transcribe module (error_type=%s)",
        type(exc).__name__,
    )
    raise


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_MAX_UPLOAD_BYTES = parse_positive_int(
    "PARAKEET_MAX_UPLOAD_BYTES", default=104_857_600
)
_model_startup = ModelStartup(
    "PARAKEET",
    load_model,
    timeout_seconds=parse_timeout_seconds("PARAKEET"),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    _model_startup.start()
    yield


app = FastAPI(
    title="Parakeet STT API",
    version="1.0.0",
    description="OpenAI-compatible Speech-to-Text API using NVIDIA Parakeet-TDT",
    lifespan=lifespan,
)
_BOUNDARY_SETTINGS = load_boundary_settings(
    "PARAKEET",
    {"/v1/audio/transcriptions", "/v1/audio/transcriptions/advanced"},
)
install_provider_boundary(app, _BOUNDARY_SETTINGS)


def _require_ready() -> None:
    if _model_startup.state != "healthy" or not model_is_loaded():
        detail = (
            "Service is loading"
            if _model_startup.state == "loading"
            else "Service is unhealthy"
        )
        raise HTTPException(status_code=503, detail=detail)


@app.get("/")
async def root():
    return {
        "name": "Parakeet STT API",
        "version": "1.0.0",
        "description": "OpenAI-compatible Speech-to-Text API",
        "backend": os.getenv("PARAKEET_BACKEND", "cuda"),
        "device": os.getenv("PARAKEET_DEVICE", "unknown"),
        "model": os.getenv("PARAKEET_MODEL", "nvidia/parakeet-tdt-0.6b-v3"),
    }


@app.get("/health")
async def health_check(response: Response):
    ready = _model_startup.state == "healthy" and model_is_loaded()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "healthy" if ready else _model_startup.state,
        "model_loaded": ready,
        "backend": os.getenv("PARAKEET_BACKEND", "cuda"),
        "device": os.getenv("PARAKEET_DEVICE", "unknown"),
        "model": os.getenv("PARAKEET_MODEL", "unknown"),
    }


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
    _require_ready()
    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = await spool_upload(file, max_bytes=_MAX_UPLOAD_BYTES, suffix=suffix)
        result = await run_with_deadline(
            "PARAKEET",
            lambda: transcribe_audio_sync(
                audio_path=str(tmp_path),
                language=language,
                temperature=temperature,
            ),
        )
        if response_format == "json":
            return {"text": result["text"]}
        if response_format == "verbose_json":
            return result
        return result["text"]
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
    word_timestamps: bool = Form(default=False),
):
    _require_ready()
    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = await spool_upload(file, max_bytes=_MAX_UPLOAD_BYTES, suffix=suffix)
        return await run_with_deadline(
            "PARAKEET",
            lambda: transcribe_audio_sync(
                audio_path=str(tmp_path),
                return_timestamps=return_timestamps,
                word_timestamps=word_timestamps,
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

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
