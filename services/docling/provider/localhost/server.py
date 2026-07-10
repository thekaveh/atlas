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

from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
import tempfile
import logging

# Import processor
from processor import process_document

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
async def health_check():
    return {
        "status": "healthy",
        "backend": os.getenv("DOCLING_DEVICE", "cpu"),
        "device": os.getenv("DOCLING_DEVICE", "cpu")
    }

@app.post("/v1/document/convert")
async def convert_document(
    file: UploadFile = File(...),
    output_format: str = Form(default="markdown"),
    use_ocr: str = Form(default="auto"),
    table_mode: str = Form(default="accurate"),
    enable_chunking: bool = Form(default=False),
    chunk_size: int = Form(default=512),
    chunk_overlap: int = Form(default=50)
):
    """Convert documents to structured format"""
    try:
        logger.info(f"Processing: {file.filename}")

        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename or "document.pdf")[1]) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        result = await process_document(
            file_path=tmp_path,
            output_format=output_format,
            use_ocr=use_ocr,
            table_mode=table_mode,
            enable_chunking=enable_chunking,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )

        os.unlink(tmp_path)
        return result

    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        if 'tmp_path' in locals():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail=str(e))

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
