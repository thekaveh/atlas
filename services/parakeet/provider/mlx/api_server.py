"""
OpenAI-compatible Speech-to-Text API Server for Parakeet MLX
Uses the official parakeet-mlx package as the transcription backend
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal, Optional
import asyncio
import os
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bounded_upload import EmptyUploadError, UploadTooLargeError, spool_upload
if __package__:
    from .alignment import advanced_payload, alignment_payload, result_text
    from .model_loader import AsyncSingleFlightModel
else:  # Direct execution from the mlx provider directory.
    from alignment import advanced_payload, alignment_payload, result_text
    from model_loader import AsyncSingleFlightModel

# Import parakeet-mlx library
try:
    from parakeet_mlx import from_pretrained
except ImportError as exc:
    logging.error(
        "Failed to import parakeet_mlx (error_type=%s)", type(exc).__name__
    )
    logging.error("Please install: pip install parakeet-mlx")
    raise

app = FastAPI(
    title="Parakeet MLX STT API",
    version="1.0.0",
    description="OpenAI-compatible Speech-to-Text API using Parakeet MLX"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _load_model():
    model_name = os.getenv("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")
    logger.info("Loading Parakeet model")
    model = from_pretrained(model_name)
    logger.info("Model loaded successfully")
    return model


_model_loader = AsyncSingleFlightModel(_load_model)
_transcription_semaphore = asyncio.Semaphore(
    max(1, int(os.getenv("PARAKEET_CONCURRENCY", "1")))
)

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Parakeet MLX STT API",
        "version": "1.0.0",
        "description": "OpenAI-compatible Speech-to-Text API",
        "backend": "mlx",
        "device": "mps",
        "model": os.getenv("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    try:
        if not _model_loader.loaded:
            task = _model_loader.start()
            if not task.done():
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "starting",
                        "backend": "mlx",
                        "device": "mps",
                        "model_loaded": False,
                    },
                )
        model = await _model_loader.get()
        return {
            "status": "healthy",
            "backend": "mlx",
            "device": "mps",
            "model": os.getenv("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3"),
            "model_loaded": model is not None
        }
    except Exception as exc:
        logger.error("Health check failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Service unhealthy") from exc


@app.post("/v1/audio/transcriptions")
async def transcribe_audio(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: Literal["json", "verbose_json", "text"] = Form("json"),
    temperature: Optional[float] = Form(0.0)
):
    """
    OpenAI-compatible audio transcription endpoint

    Parameters:
    - file: Audio file to transcribe (required)
    - model: Model to use (informational only, uses PARAKEET_MODEL env var)
    - language: Language code (not used by Parakeet)
    - prompt: Context prompt (not used by Parakeet)
    - response_format: Response format (json, verbose_json, text)
    - temperature: Sampling temperature (not used by Parakeet)
    """
    tmp_path = None
    try:
        max_bytes = int(os.getenv("PARAKEET_MAX_UPLOAD_BYTES", "104857600"))
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = await spool_upload(file, max_bytes=max_bytes, suffix=suffix)

        try:
            async with _transcription_semaphore:
                model_instance = await _model_loader.get()
                logger.info("Transcribing uploaded file")
                result = await asyncio.to_thread(
                    model_instance.transcribe, str(tmp_path)
                )

            # Extract text from result
            transcribed_text = result_text(result)

            logger.info(f"Transcription complete: {len(transcribed_text)} characters")

            # Format response based on response_format
            if response_format == "text":
                return transcribed_text
            elif response_format == "verbose_json":
                payload = alignment_payload(result)
                payload.update({"task": "transcribe", "language": language or "unknown"})
                payload.pop("words")
                return payload
            else:  # json (default)
                return {"text": transcribed_text}

        finally:
            # Clean up temp file
            tmp_path.unlink(missing_ok=True)

    except UploadTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except EmptyUploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Transcription failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Transcription failed") from exc


@app.post("/v1/audio/transcriptions/advanced")
async def transcribe_advanced(
    file: UploadFile = File(...),
    return_timestamps: bool = Form(default=False),
    word_timestamps: bool = Form(False)
):
    """
    Advanced transcription endpoint with Parakeet-specific features

    Parameters:
    - file: Audio file to transcribe (required)
    - return_timestamps: Include segment timestamps
    - word_timestamps: Include word-level timestamps
    """
    tmp_path = None
    try:
        max_bytes = int(os.getenv("PARAKEET_MAX_UPLOAD_BYTES", "104857600"))
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = await spool_upload(file, max_bytes=max_bytes, suffix=suffix)

        try:
            async with _transcription_semaphore:
                model_instance = await _model_loader.get()
                logger.info("Running advanced transcription")
                result = await asyncio.to_thread(
                    model_instance.transcribe, str(tmp_path)
                )

            return advanced_payload(result, return_timestamps, word_timestamps)

        finally:
            tmp_path.unlink(missing_ok=True)

    except UploadTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except EmptyUploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Advanced transcription failed (error_type=%s)",
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Transcription failed") from exc


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("STT_PROVIDER_PORT", 63042))
    uvicorn.run(app, host="0.0.0.0", port=port)
