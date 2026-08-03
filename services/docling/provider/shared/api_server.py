"""Authenticated Docling document-processing API for the GPU container."""

import logging
import os
import time
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, status

from bounded_upload import (
    EmptyUploadError,
    RequestBodyLimitMiddleware,
    UploadTooLargeError,
    multipart_body_limit,
    spool_upload,
)
from lightrag_bundle import build_lightrag_bundle
from models import ConversionResponse, HealthResponse
from pipeline_config import resolve_chunk_defaults, validate_chunk_settings
from processor import convert_document_once, processor_status, render_conversion
from provider_boundary import (
    ProviderDeadlineExceeded,
    fatal_timeout_response,
    install_provider_boundary,
    load_boundary_settings,
    parse_positive_int,
    run_with_deadline,
)
from utils import ChunkLimitError


_CHUNK_DEFAULTS = resolve_chunk_defaults()
_MAX_UPLOAD_BYTES = parse_positive_int("DOCLING_MAX_FILE_SIZE", default=52_428_800)
_UPLOAD_TIMEOUT_SECONDS = parse_positive_int(
    "DOCLING_UPLOAD_TIMEOUT_SECONDS", default=120
)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Docling Document Processor API",
    version="1.0.0",
    description="AI-powered document processing using IBM Docling",
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=multipart_body_limit(_MAX_UPLOAD_BYTES),
    body_timeout_seconds=_UPLOAD_TIMEOUT_SECONDS,
    paths={"/v1/document/convert", "/internal/lightrag/bundle"},
)
_BOUNDARY_SETTINGS = load_boundary_settings(
    "DOCLING",
    {"/v1/document/convert", "/internal/lightrag/bundle"},
)
install_provider_boundary(app, _BOUNDARY_SETTINGS)


def _convert_and_render(
    file_path: str,
    *,
    output_format: str,
    use_ocr: str,
    table_mode: str,
    enable_chunking: bool,
    chunk_size: int,
    chunk_overlap: int,
):
    started_at = time.time()
    result = convert_document_once(
        file_path,
        use_ocr=use_ocr,
        table_mode=table_mode,
    )
    return render_conversion(
        result,
        file_path=file_path,
        output_format=output_format,
        enable_chunking=enable_chunking,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        started_at=started_at,
    )


def _convert_and_bundle(
    file_path: str,
    *,
    upload_name: str,
    use_ocr: str,
    table_mode: str,
) -> bytes:
    result = convert_document_once(
        file_path,
        use_ocr=use_ocr,
        table_mode=table_mode,
    )
    return build_lightrag_bundle(result, upload_name=upload_name)


@app.get("/")
async def root():
    return {
        "name": "Docling Document Processor API",
        "version": "1.0.0",
        "backend": os.getenv("DOCLING_DEVICE", "cpu"),
        "supported_formats": ["pdf", "docx", "pptx", "html", "png", "jpg", "tiff"],
    }


@app.get("/health", response_model=HealthResponse)
async def health_check(response: Response):
    readiness = await processor_status()
    ready = readiness == "healthy"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status=readiness,
        backend=os.getenv("DOCLING_DEVICE", "cpu"),
        device=os.getenv("DOCLING_DEVICE", "cpu"),
        models_loaded=["DocumentConverter"] if ready else [],
    )


@app.post("/v1/document/convert", response_model=ConversionResponse)
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
    chunk_size: int = Form(default=_CHUNK_DEFAULTS.size, gt=0),
    chunk_overlap: int = Form(default=_CHUNK_DEFAULTS.overlap, ge=0),
):
    try:
        validate_chunk_settings(chunk_size, chunk_overlap)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename or "document.pdf")[1] or ".pdf"
        tmp_path = await spool_upload(file, max_bytes=_MAX_UPLOAD_BYTES, suffix=suffix)
        return await run_with_deadline(
            "DOCLING",
            lambda: _convert_and_render(
                str(tmp_path),
                output_format=output_format,
                use_ocr=use_ocr,
                table_mode=table_mode,
                enable_chunking=enable_chunking,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            ),
        )
    except ProviderDeadlineExceeded:
        return fatal_timeout_response("DOCLING")
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except EmptyUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ChunkLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Document conversion failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Document conversion failed") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@app.post("/internal/lightrag/bundle")
async def lightrag_bundle(
    file: UploadFile = File(...),
    use_ocr: Literal["auto", "always", "never"] = Form(
        default=os.getenv("DOCLING_USE_OCR", "auto")
    ),
    table_mode: Literal["accurate", "fast"] = Form(
        default=os.getenv("DOCLING_TABLE_MODE", "accurate")
    ),
):
    tmp_path = None
    try:
        upload_name = file.filename or "document.pdf"
        suffix = os.path.splitext(upload_name)[1] or ".pdf"
        tmp_path = await spool_upload(file, max_bytes=_MAX_UPLOAD_BYTES, suffix=suffix)
        payload = await run_with_deadline(
            "DOCLING",
            lambda: _convert_and_bundle(
                str(tmp_path),
                upload_name=upload_name,
                use_ocr=use_ocr,
                table_mode=table_mode,
            ),
        )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="docling-result.zip"'},
        )
    except ProviderDeadlineExceeded:
        return fatal_timeout_response("DOCLING")
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except EmptyUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Document bundle failed (error_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Document bundle failed") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@app.get("/v1/models")
async def list_models():
    return {
        "models": [
            {
                "id": "doclaynet",
                "name": "DocLayNet Layout Analyzer",
                "description": "AI layout analysis",
            },
            {
                "id": "tableformer",
                "name": "TableFormer Table Extractor",
                "description": "Table structure recognition",
            },
        ]
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
