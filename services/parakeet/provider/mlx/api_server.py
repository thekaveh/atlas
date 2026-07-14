"""
OpenAI-compatible Speech-to-Text API Server for Parakeet MLX
Uses the official parakeet-mlx package as the transcription backend
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import asyncio
import os
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bounded_upload import EmptyUploadError, UploadTooLargeError, spool_upload

# Import parakeet-mlx library
try:
    from parakeet_mlx import from_pretrained
except ImportError as e:
    logging.error(f"Failed to import parakeet_mlx: {e}")
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

# Global model (lazy loaded)
_model = None
_transcription_semaphore = asyncio.Semaphore(
    max(1, int(os.getenv("PARAKEET_CONCURRENCY", "1")))
)

def get_model():
    """Load model once and reuse"""
    global _model
    if _model is None:
        model_name = os.getenv("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")
        logger.info(f"Loading Parakeet model: {model_name}")
        _model = from_pretrained(model_name)
        logger.info("Model loaded successfully")
    return _model


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
        # Check if model can be loaded
        model = get_model()
        return {
            "status": "healthy",
            "backend": "mlx",
            "device": "mps",
            "model": os.getenv("PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v3"),
            "model_loaded": model is not None
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")


@app.post("/v1/audio/transcriptions")
async def transcribe_audio(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
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
                model_instance = await asyncio.to_thread(get_model)
                logger.info(f"Transcribing file: {file.filename}")
                result = await asyncio.to_thread(
                    model_instance.transcribe, str(tmp_path)
                )

            # Extract text from result
            if hasattr(result, 'text'):
                transcribed_text = result.text
            elif isinstance(result, dict) and 'text' in result:
                transcribed_text = result['text']
            elif isinstance(result, str):
                transcribed_text = result
            else:
                transcribed_text = str(result)

            logger.info(f"Transcription complete: {len(transcribed_text)} characters")

            # Format response based on response_format
            if response_format == "text":
                return transcribed_text
            elif response_format == "verbose_json":
                return {
                    "text": transcribed_text,
                    "task": "transcribe",
                    "language": language or "unknown",
                    "duration": getattr(result, 'duration', None),
                    "segments": getattr(result, 'segments', [])
                }
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
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


@app.post("/v1/audio/transcriptions/advanced")
async def transcribe_advanced(
    file: UploadFile = File(...),
    return_timestamps: bool = Form(False),
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
                model_instance = await asyncio.to_thread(get_model)
                logger.info(f"Advanced transcription: {file.filename}")
                result = await asyncio.to_thread(
                    model_instance.transcribe, str(tmp_path)
                )

            # Build response
            response = {
                "text": result.text if hasattr(result, 'text') else str(result),
                "has_timestamps": return_timestamps or word_timestamps
            }

            # Add timestamps if available and requested
            if return_timestamps and hasattr(result, 'segments'):
                response["segments"] = result.segments

            if word_timestamps and hasattr(result, 'words'):
                response["words"] = result.words

            return response

        finally:
            tmp_path.unlink(missing_ok=True)

    except UploadTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except EmptyUploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Advanced transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("STT_PROVIDER_PORT", 63042))
    uvicorn.run(app, host="0.0.0.0", port=port)
