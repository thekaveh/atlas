"""
Docling Document Processor - Localhost Server
Standalone FastAPI server for native execution
"""

import os
import sys
from pathlib import Path

# Load .env from the REPO root (for port configuration with --base-port).
# This file lives at services/docling/provider/localhost/, so the root is
# five parents up — three only reached services/docling/ and the load
# silently no-op'd.
try:
    from dotenv import load_dotenv
    env_file = Path(__file__).resolve().parents[4] / '.env'
    if env_file.exists():
        load_dotenv(env_file)
except ImportError:
    # python-dotenv not installed, will use os.environ directly
    pass

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Response, status
from fastapi.middleware.cors import CORSMiddleware
import logging
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bounded_upload import EmptyUploadError, UploadTooLargeError, spool_upload

# Import processor
from processor import process_document, processor_status

app = FastAPI(title="Docling Document Processor", version="1.0.0")

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
    return {
        "name": "Docling Document Processor API (Localhost)",
        "version": "1.0.0",
        "backend": os.getenv("DOCLING_DEVICE", "cpu")
    }

@app.get("/health")
async def health_check(response: Response):
    readiness = await processor_status()
    ready = readiness == "healthy"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": readiness,
        "backend": os.getenv("DOCLING_DEVICE", "cpu"),
        "device": os.getenv("DOCLING_DEVICE", "cpu")
    }

@app.post("/v1/document/convert")
async def convert_document(
    file: UploadFile = File(...),
    output_format: Literal["markdown", "html", "json", "doctags"] = Form(
        default=os.getenv("DOCLING_OUTPUT_FORMAT", "markdown")
    ),
    use_ocr: Literal["auto", "always", "never"] = Form(
        default=os.getenv("DOCLING_USE_OCR", "auto")
    ),
    table_mode: Literal["accurate", "fast"] = Form(
        default=os.getenv("DOCLING_TABLE_MODE", "accurate")
    ),
    enable_chunking: bool = Form(default=False),
    chunk_size: int = Form(default=512),
    chunk_overlap: int = Form(default=50)
):
    """Convert documents to structured format"""
    tmp_path = None
    try:
        logger.info("Processing uploaded document")
        max_bytes = int(os.getenv("DOCLING_MAX_FILE_SIZE", "52428800"))
        suffix = os.path.splitext(file.filename or "document.pdf")[1] or ".pdf"
        tmp_path = await spool_upload(
            file, max_bytes=max_bytes, suffix=suffix
        )

        result = await process_document(
            file_path=str(tmp_path),
            output_format=output_format,
            use_ocr=use_ocr,
            table_mode=table_mode,
            enable_chunking=enable_chunking,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
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
            "Document conversion failed (error_type=%s)",
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Document conversion failed") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

if __name__ == "__main__":
    import uvicorn
    # DOCLING_LOCALHOST_PORT is the stack's localhost-mode contract (the
    # var Kong / runtime_sc / localhost_validator all probe). The old
    # DOC_PROCESSOR_PORT read is the CONTAINER-mode host-bind var — it
    # only worked because the fallback happened to match the default.
    port = int(os.getenv("DOCLING_LOCALHOST_PORT") or 18159)
    print(f"🚀 Starting Docling server on port {port}")
    print(f"📄 Device: {os.getenv('DOCLING_DEVICE', 'cpu')}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
