"""
OpenAI-compatible Document Processing API Server
Supports GPU backend for Docling (used by container)
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Response, status
from fastapi.middleware.cors import CORSMiddleware
import os
import logging

from bounded_upload import EmptyUploadError, UploadTooLargeError, spool_upload

# Import backend-specific processor
try:
    from processor import process_document, processor_ready
except ImportError as e:
    logging.error(f"Failed to import processor module: {e}")
    raise

# Import models
from models import ConversionResponse, HealthResponse

app = FastAPI(
    title="Docling Document Processor API",
    version="1.0.0",
    description="AI-powered document processing using IBM Docling"
)

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
        "name": "Docling Document Processor API",
        "version": "1.0.0",
        "backend": os.getenv("DOCLING_DEVICE", "cpu"),
        "supported_formats": ["pdf", "docx", "pptx", "html", "png", "jpg", "tiff"]
    }

@app.get("/health", response_model=HealthResponse)
async def health_check(response: Response):
    ready = processor_ready()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="healthy" if ready else "unavailable",
        backend=os.getenv("DOCLING_DEVICE", "cpu"),
        device=os.getenv("DOCLING_DEVICE", "cpu"),
        models_loaded=["DocLayNet", "TableFormer"] if ready else []
    )

@app.post("/v1/document/convert", response_model=ConversionResponse)
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
    tmp_path = None
    try:
        logger.info(f"Processing: {file.filename}")
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
    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

@app.get("/v1/models")
async def list_models():
    return {
        "models": [
            {"id": "doclaynet", "name": "DocLayNet Layout Analyzer", "description": "AI layout analysis"},
            {"id": "tableformer", "name": "TableFormer Table Extractor", "description": "Table structure recognition"}
        ]
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
