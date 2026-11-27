"""Authenticated OpenAI-compatible Parakeet API for the GPU container."""

import logging
import os

from fastapi.responses import PlainTextResponse

from bounded_upload import MAX_BODY_TIMEOUT_SECONDS
from provider_boundary import parse_positive_int, parse_timeout_seconds
from startup import ModelStartup
from transcription_api import ProviderHooks, create_api

try:
    from transcribe import load_model, model_is_loaded, transcribe_audio_sync
except ImportError as exc:
    logging.error(
        "Failed to import transcribe module (error_type=%s)",
        type(exc).__name__,
    )
    raise


logging.basicConfig(level=logging.INFO)
_DEFAULT_MODEL = "nvidia/parakeet-tdt-0.6b-v3"
_MAX_UPLOAD_BYTES = parse_positive_int(
    "PARAKEET_MAX_UPLOAD_BYTES", default=104_857_600
)
_UPLOAD_TIMEOUT_SECONDS = parse_positive_int(
    "PARAKEET_UPLOAD_TIMEOUT_SECONDS",
    default=120,
    maximum=MAX_BODY_TIMEOUT_SECONDS,
)
_model_startup = ModelStartup(
    "PARAKEET",
    load_model,
    timeout_seconds=parse_timeout_seconds("PARAKEET"),
)


def _describe() -> dict[str, str]:
    return {
        "backend": os.getenv("PARAKEET_BACKEND", "cuda"),
        "device": os.getenv("PARAKEET_DEVICE", "unknown"),
        "model": os.getenv("PARAKEET_MODEL", _DEFAULT_MODEL),
    }


def _transcribe_standard(
    file_path: str,
    *,
    response_format: str,
    language: str | None,
    temperature: float,
):
    result = transcribe_audio_sync(
        audio_path=file_path,
        language=language,
        temperature=temperature,
    )
    if response_format == "json":
        return {"text": result["text"]}
    if response_format == "verbose_json":
        return result
    return PlainTextResponse(result["text"])  # OpenAI's text format is raw text


def _transcribe_advanced(
    file_path: str,
    *,
    return_timestamps: bool,
    word_timestamps: bool,
):
    return transcribe_audio_sync(
        audio_path=file_path,
        return_timestamps=return_timestamps,
        word_timestamps=word_timestamps,
    )


app = create_api(
    ProviderHooks(
        provider="PARAKEET",
        title="Parakeet STT API",
        description="OpenAI-compatible Speech-to-Text API using NVIDIA Parakeet-TDT",
        logger_name=__name__,
        model_startup=_model_startup,
        is_loaded=model_is_loaded,
        describe=_describe,
        transcribe_standard=_transcribe_standard,
        transcribe_advanced=_transcribe_advanced,
        max_upload_bytes=_MAX_UPLOAD_BYTES,
        upload_timeout_seconds=_UPLOAD_TIMEOUT_SECONDS,
    )
)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
