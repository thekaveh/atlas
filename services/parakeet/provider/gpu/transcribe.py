"""NVIDIA NeMo transcription implementation for Parakeet-TDT."""

import asyncio
import logging
import os
import threading

import nemo.collections.asr as nemo_asr


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_model = None
_model_lock = threading.Lock()
_transcription_semaphore = threading.BoundedSemaphore(
    max(1, int(os.getenv("PARAKEET_CONCURRENCY", "1")))
)


def model_is_loaded() -> bool:
    return _model is not None


def load_model():
    """Load the configured Parakeet model once, off the event loop."""
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model
        model_name = os.getenv("PARAKEET_MODEL", "nvidia/parakeet-tdt-0.6b-v3")
        device = os.getenv("PARAKEET_DEVICE", "cuda")
        logger.info("Loading Parakeet model")
        try:
            model = nemo_asr.models.ASRModel.from_pretrained(model_name)
            model = model.cuda() if device == "cuda" else model.cpu()
            model.eval()
        except Exception as exc:
            logger.error("Failed to load model (error_type=%s)", type(exc).__name__)
            raise
        _model = model
        logger.info("Model loaded successfully")
        return _model


def transcribe_audio_sync(
    audio_path: str,
    language: str = None,
    temperature: float = 0.0,
    return_timestamps: bool = False,
    word_timestamps: bool = False,
):
    """Run one bounded synchronous NeMo transcription."""
    del temperature, word_timestamps
    try:
        with _transcription_semaphore:
            model = load_model()
            logger.info("Transcribing audio file")
            transcription = model.transcribe(
                [audio_path],
                timestamps=bool(return_timestamps),
            )

        first = transcription[0] if isinstance(transcription, list) else transcription
        if hasattr(first, "text"):
            text = first.text
        elif isinstance(first, str):
            text = first
        else:
            text = str(first)

        logger.info("Transcription complete (characters=%s)", len(text))
        result = {"text": text, "language": language or "auto"}
        if return_timestamps:
            try:
                timing = getattr(first, "timestamp", None) or {}
                if timing:
                    result["timestamps"] = timing
                    result["has_timestamps"] = True
                else:
                    result["has_timestamps"] = False
            except Exception as exc:
                logger.warning(
                    "Could not extract timestamps (error_type=%s)",
                    type(exc).__name__,
                )
                result["has_timestamps"] = False
        return result
    except Exception as exc:
        logger.error("Transcription failed (error_type=%s)", type(exc).__name__)
        raise


async def transcribe_audio(
    audio_path: str,
    language: str = None,
    temperature: float = 0.0,
    return_timestamps: bool = False,
    word_timestamps: bool = False,
):
    """Compatibility wrapper for existing async callers."""
    return await asyncio.to_thread(
        transcribe_audio_sync,
        audio_path,
        language,
        temperature,
        return_timestamps,
        word_timestamps,
    )
