"""Authenticated OpenAI-compatible API for native Parakeet MLX."""

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.responses import PlainTextResponse

from provider_boundary import parse_positive_int, parse_timeout_seconds
from bounded_upload import MAX_BODY_TIMEOUT_SECONDS
from startup import ModelStartup
from transcription_api import ProviderHooks, create_api

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
_DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"


def _load_model():
    model_name = os.getenv("PARAKEET_MODEL", _DEFAULT_MODEL)
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


def _is_loaded() -> bool:
    return _model_startup.model is not None


def _describe() -> dict[str, str]:
    # Fixed for this provider: PARAKEET_BACKEND and PARAKEET_DEVICE are not read here.
    return {
        "backend": "mlx",
        "device": "mps",
        "model": os.getenv("PARAKEET_MODEL", _DEFAULT_MODEL),
    }


def _transcribe(file_path: str):
    model = _model_startup.model
    if model is None:
        raise RuntimeError("Parakeet model is unavailable")
    with _transcription_semaphore:
        logger.info("Transcribing uploaded file")
        return model.transcribe(file_path)


def _transcribe_standard(
    file_path: str,
    *,
    response_format: str,
    language: Optional[str],
    temperature: float,
):
    # MLX has no sampling temperature; the form field is accepted and ignored.
    del temperature
    result = _transcribe(file_path)
    transcribed_text = result_text(result)
    logger.info("Transcription complete (characters=%s)", len(transcribed_text))
    if response_format == "text":
        return PlainTextResponse(transcribed_text)  # OpenAI's text format is raw text
    if response_format == "verbose_json":
        payload = alignment_payload(result)
        payload.update({"task": "transcribe", "language": language or "unknown"})
        payload.pop("words")
        return payload
    return {"text": transcribed_text}


def _transcribe_advanced(
    file_path: str,
    *,
    return_timestamps: bool,
    word_timestamps: bool,
):
    result = _transcribe(file_path)
    return advanced_payload(result, return_timestamps, word_timestamps)


app = create_api(
    ProviderHooks(
        provider="PARAKEET",
        title="Parakeet MLX STT API",
        description="OpenAI-compatible Speech-to-Text API using Parakeet MLX",
        logger_name=__name__,
        model_startup=_model_startup,
        is_loaded=_is_loaded,
        describe=_describe,
        transcribe_standard=_transcribe_standard,
        transcribe_advanced=_transcribe_advanced,
        max_upload_bytes=_MAX_UPLOAD_BYTES,
        upload_timeout_seconds=_UPLOAD_TIMEOUT_SECONDS,
    )
)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PARAKEET_LOCALHOST_PORT") or 63042)
    bind_host = os.getenv("PARAKEET_LOCALHOST_BIND_HOST", "127.0.0.1")
    uvicorn.run(app, host=bind_host, port=port)
