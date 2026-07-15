"""
OpenAI-compatible Speech-to-Text API Server
Supports both MLX (Mac) and NVIDIA GPU (CUDA) backends
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Response, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import logging
from typing import Optional

from bounded_upload import EmptyUploadError, UploadTooLargeError, spool_upload

# Import backend-specific transcriber
try:
    from transcribe import model_is_loaded, transcribe_audio
except ImportError as e:
    logging.error(f"Failed to import transcribe module: {e}")
    raise

app = FastAPI(
    title="Parakeet STT API",
    version="1.0.0",
    description="OpenAI-compatible Speech-to-Text API using NVIDIA Parakeet-TDT"
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

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Parakeet STT API",
        "version": "1.0.0",
        "description": "OpenAI-compatible Speech-to-Text API",
        "backend": os.getenv("PARAKEET_BACKEND", "cuda"),
        "device": os.getenv("PARAKEET_DEVICE", "unknown"),
        "model": os.getenv("PARAKEET_MODEL", "nvidia/parakeet-tdt-0.6b-v3")
    }

@app.get("/health")
async def health_check(response: Response):
    """Health check endpoint"""
    ready = model_is_loaded()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "healthy" if ready else "loading",
        "model_loaded": ready,
        "backend": os.getenv("PARAKEET_BACKEND", "cuda"),
        "device": os.getenv("PARAKEET_DEVICE", "unknown"),
        "model": os.getenv("PARAKEET_MODEL", "unknown")
    }

@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="parakeet-tdt-0.6b-v3"),
    language: Optional[str] = Form(default=None),
    prompt: Optional[str] = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float = Form(default=0.0)
):
    """
    OpenAI-compatible transcription endpoint

    POST /v1/audio/transcriptions

    Accepts audio files (.wav, .flac, .mp3, etc.) and returns transcription text
    Compatible with OpenAI Whisper API format

    Args:
        file: Audio file to transcribe
        model: Model identifier (informational, actual model from PARAKEET_MODEL env)
        language: Optional language code for transcription
        prompt: Optional context prompt (not used by Parakeet)
        response_format: Format of response (json, verbose_json, text)
        temperature: Sampling temperature (0.0 = greedy decoding)

    Returns:
        Transcription result in requested format
    """
    tmp_path = None
    try:
        logger.info("Received transcription request")

        max_bytes = int(os.getenv("PARAKEET_MAX_UPLOAD_BYTES", "104857600"))
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = await spool_upload(file, max_bytes=max_bytes, suffix=suffix)

        logger.info("Saved temporary audio file")

        # Transcribe using backend-specific function
        result = await transcribe_audio(
            audio_path=str(tmp_path),
            language=language,
            temperature=temperature
        )

        # Format response based on response_format
        if response_format == "json":
            return {"text": result["text"]}
        elif response_format == "verbose_json":
            return result
        elif response_format == "text":
            return result["text"]
        else:
            return {"text": result["text"]}

    except UploadTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except EmptyUploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
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
    return_timestamps: bool = Form(default=True),
    word_timestamps: bool = Form(default=False)
):
    """
    Advanced transcription endpoint with Parakeet-specific features

    POST /v1/audio/transcriptions/advanced

    Returns word-level timestamps and additional metadata

    Args:
        file: Audio file to transcribe
        return_timestamps: Whether to return segment timestamps
        word_timestamps: Whether to return word-level timestamps

    Returns:
        Detailed transcription result with timestamps
    """
    tmp_path = None
    try:
        logger.info("Received advanced transcription request")

        max_bytes = int(os.getenv("PARAKEET_MAX_UPLOAD_BYTES", "104857600"))
        suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
        tmp_path = await spool_upload(file, max_bytes=max_bytes, suffix=suffix)

        result = await transcribe_audio(
            audio_path=str(tmp_path),
            return_timestamps=return_timestamps,
            word_timestamps=word_timestamps
        )

        return result

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
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
