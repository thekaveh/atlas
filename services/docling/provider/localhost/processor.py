"""Docling localhost processor."""

import asyncio
import os
from pathlib import Path
import sys
import threading
import time

from models import ChunkMetadata, ConversionResponse, DocumentChunk, DocumentMetadata
from utils import chunk_text, detect_format, get_file_size

try:
    from shared.pipeline_config import build_converter, converter_status, resolve_pipeline_settings
except ModuleNotFoundError:
    shared_dir = Path(__file__).resolve().parents[1] / "shared"
    sys.path.insert(0, str(shared_dir))
    from pipeline_config import build_converter, converter_status, resolve_pipeline_settings

try:
    from docling.document_converter import DocumentConverter
except ImportError:
    DocumentConverter = None


DEVICE_DEFAULT = "cpu"
SUPPORTED_OUTPUT_FORMATS = {"markdown", "html", "json", "doctags"}
_conversion_semaphore = threading.BoundedSemaphore(
    max(1, int(os.getenv("DOCLING_CONCURRENCY", "1")))
)


async def processor_status() -> str:
    if DocumentConverter is None:
        return "unavailable"
    try:
        settings = resolve_pipeline_settings(
            use_ocr=os.getenv("DOCLING_USE_OCR", "auto"),
            table_mode=os.getenv("DOCLING_TABLE_MODE", "accurate"),
            device=os.getenv("DOCLING_DEVICE", DEVICE_DEFAULT),
            enable_formulas=os.getenv("DOCLING_ENABLE_FORMULAS", "true"),
            enable_code_blocks=os.getenv("DOCLING_ENABLE_CODE_BLOCKS", "true"),
        )
    except (AttributeError, TypeError, ValueError):
        return "unavailable"
    return await converter_status(settings)


def _convert_document(file_path: str, use_ocr: str, table_mode: str):
    settings = resolve_pipeline_settings(
        use_ocr=use_ocr,
        table_mode=table_mode,
        device=os.getenv("DOCLING_DEVICE", DEVICE_DEFAULT),
        enable_formulas=os.getenv("DOCLING_ENABLE_FORMULAS", "true"),
        enable_code_blocks=os.getenv("DOCLING_ENABLE_CODE_BLOCKS", "true"),
    )
    return build_converter(settings).convert(file_path)


def convert_document_once(file_path: str, *, use_ocr: str, table_mode: str):
    """Perform exactly one model-backed Docling conversion."""
    if DocumentConverter is None:
        raise ImportError("Docling library not installed. Install with: pip install docling")
    try:
        with _conversion_semaphore:
            return _convert_document(file_path, use_ocr, table_mode)
    except Exception as exc:
        raise RuntimeError("Docling processing failed") from exc


def _render_content(document, output_format: str) -> str:
    if output_format == "markdown":
        return document.export_to_markdown()
    if output_format == "html":
        return document.export_to_html()
    if output_format == "json":
        import json

        return json.dumps(document.export_to_dict(), indent=2)
    return document.export_to_document_tokens()


def render_conversion(
    result,
    *,
    file_path: str,
    output_format: str,
    enable_chunking: bool,
    chunk_size: int,
    chunk_overlap: int,
    started_at: float,
) -> ConversionResponse:
    """Render an existing conversion result without invoking Docling again."""
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(
            f"output_format must be one of: {', '.join(sorted(SUPPORTED_OUTPUT_FORMATS))}"
        )
    try:
        document = result.document
        content = _render_content(document, output_format)
        pages = len(document.pages) if getattr(document, "pages", None) else 1
        tables = len(document.tables) if getattr(document, "tables", None) else 0
        images = len(document.pictures) if getattr(document, "pictures", None) else 0
        formulas = len(document.equations) if getattr(document, "equations", None) else 0
    except Exception as exc:
        raise RuntimeError("Docling processing failed") from exc

    metadata = DocumentMetadata(
        pages=pages,
        tables=tables,
        images=images,
        formulas=formulas,
        processing_time=time.time() - started_at,
        source_format=detect_format(file_path),
        file_size=get_file_size(file_path),
    )
    chunks = None
    if enable_chunking:
        raw_chunks = chunk_text(content, chunk_size, chunk_overlap)
        chunks = [
            DocumentChunk(
                text=chunk["text"],
                metadata=ChunkMetadata(
                    chunk_index=chunk["metadata"]["chunk_index"],
                    chunk_type=chunk["metadata"]["chunk_type"],
                ),
            )
            for chunk in raw_chunks
        ]

    return ConversionResponse(
        content=content,
        format=output_format,
        metadata=metadata,
        chunks=chunks,
    )


async def process_document(
    file_path: str,
    output_format: str = "markdown",
    use_ocr: str = "auto",
    table_mode: str = "accurate",
    enable_chunking: bool = False,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> ConversionResponse:
    """Compatibility wrapper for callers that use the original processor API."""
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(
            f"output_format must be one of: {', '.join(sorted(SUPPORTED_OUTPUT_FORMATS))}"
        )
    started_at = time.time()
    result = await asyncio.to_thread(
        convert_document_once,
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
