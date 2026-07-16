"""
NVIDIA NeMo-based transcription implementation for Parakeet-TDT
Optimized for NVIDIA GPUs with CUDA acceleration
"""

import asyncio
import os
import logging
import nemo.collections.asr as nemo_asr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global model (loaded once on startup)
_model = None
_transcription_semaphore = asyncio.Semaphore(
    max(1, int(os.getenv("PARAKEET_CONCURRENCY", "1")))
)


def model_is_loaded() -> bool:
    return _model is not None


def load_model():
    """Load Parakeet model using NVIDIA NeMo (lazy loading)"""
    global _model

    if _model is None:
        model_name = os.getenv("PARAKEET_MODEL", "nvidia/parakeet-tdt-0.6b-v3")
        device = os.getenv("PARAKEET_DEVICE", "cuda")

        logger.info("Loading Parakeet model")

        try:
            # Load model using NeMo
            _model = nemo_asr.models.ASRModel.from_pretrained(model_name)

            # Move to specified device
            if device == "cuda":
                _model = _model.cuda()
                logger.info(f"Model loaded on CUDA device")
            else:
                _model = _model.cpu()
                logger.info(f"Model loaded on CPU device")

            # Set to evaluation mode
            _model.eval()

            logger.info("Model loaded successfully")

        except Exception as exc:
            logger.error("Failed to load model (error_type=%s)", type(exc).__name__)
            raise

    return _model

async def transcribe_audio(
    audio_path: str,
    language: str = None,
    temperature: float = 0.0,
    return_timestamps: bool = False,
    word_timestamps: bool = False
):
    """
    Transcribe audio file using Parakeet-TDT with NVIDIA NeMo

    Args:
        audio_path: Path to audio file (.wav, .flac, etc.)
        language: Optional language code (auto-detect if None)
        temperature: Sampling temperature (not used by NeMo)
        return_timestamps: Whether to return segment timestamps
        word_timestamps: Whether to return word-level timestamps

    Returns:
        dict: Transcription result with text and optional timestamps
    """
    try:
        async with _transcription_semaphore:
            model = await asyncio.to_thread(load_model)
            logger.info("Transcribing audio file")
            transcription = await asyncio.to_thread(
                model.transcribe,
                [audio_path],
                timestamps=bool(return_timestamps),
            )

        # Extract text result. For RNNT/TDT models NeMo returns
        # List[Hypothesis] EVEN without return_hypotheses — indexing
        # used to hand a Hypothesis dataclass to len() and 500 every
        # request. Mirror the mlx sibling's defensive extraction.
        first = transcription[0] if isinstance(transcription, list) else transcription
        if hasattr(first, 'text'):
            text = first.text
        elif isinstance(first, str):
            text = first
        else:
            text = str(first)

        logger.info(f"Transcription complete: {len(text)} characters")

        # Build result
        result = {
            "text": text,
            "language": language or "auto"
        }

        # Add timestamp information if requested and available
        if return_timestamps:
            try:
                # NeMo 2.x Hypothesis carries `.timestamp` (a dict with
                # 'word'/'segment'/... keys) — `.timestep` doesn't exist.
                timing = getattr(first, 'timestamp', None) or {}
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

# Pre-load model on module import (optional, for faster first request)
if os.getenv("PRELOAD_MODEL", "false").lower() == "true":
    logger.info("Pre-loading model on startup...")
    load_model()
