"""
Docling GPU Processor
Handles document processing using Docling library with GPU acceleration
"""

import asyncio
import os
import time
from typing import Optional
from models import ConversionResponse, DocumentMetadata, DocumentChunk, ChunkMetadata
from utils import get_file_size, detect_format, chunk_text

# Import Docling
try:
    from docling.document_converter import DocumentConverter
except ImportError:
    # Fallback for development
    DocumentConverter = None


_conversion_semaphore = asyncio.Semaphore(
    max(1, int(os.getenv("DOCLING_CONCURRENCY", "1")))
)


def _convert_document(file_path: str):
    return DocumentConverter().convert(file_path)


async def process_document(
    file_path: str,
    output_format: str = "markdown",
    use_ocr: str = "auto",
    table_mode: str = "accurate",
    enable_chunking: bool = False,
    chunk_size: int = 512,
    chunk_overlap: int = 50
) -> ConversionResponse:
    """
    Process document using Docling

    Args:
        file_path: Path to document file
        output_format: Output format (markdown, html, json, doctags)
        use_ocr: OCR mode (auto, always, never)
        table_mode: Table extraction mode (accurate, fast)
        enable_chunking: Whether to chunk output for RAG
        chunk_size: Size of chunks
        chunk_overlap: Overlap between chunks

    Returns:
        ConversionResponse with processed content and metadata
    """
    start_time = time.time()

    # Get file metadata
    file_size = get_file_size(file_path)
    source_format = detect_format(file_path)

    # Process document with Docling
    if DocumentConverter is None:
        raise ImportError("Docling library not installed. Install with: pip install docling")

    try:
        async with _conversion_semaphore:
            result = await asyncio.to_thread(_convert_document, file_path)
        doc = result.document

        # Export to requested format
        if output_format == "markdown":
            content = doc.export_to_markdown()
        elif output_format == "html":
            content = doc.export_to_html()
        elif output_format == "json":
            import json
            content = json.dumps(doc.export_to_dict(), indent=2)
        elif output_format == "doctags":
            content = doc.export_to_document_tokens()
        else:
            # Default to markdown
            content = doc.export_to_markdown()

        # Extract metadata
        pages = len(doc.pages) if hasattr(doc, 'pages') and doc.pages else 1

        # Count tables, images, formulas by iterating through document elements
        tables = 0
        images = 0
        formulas = 0

        if hasattr(doc, 'tables') and doc.tables:
            tables = len(doc.tables)
        if hasattr(doc, 'pictures') and doc.pictures:
            images = len(doc.pictures)
        if hasattr(doc, 'equations') and doc.equations:
            formulas = len(doc.equations)

    except Exception as e:
        raise RuntimeError(f"Docling processing failed: {e}") from e

    processing_time = time.time() - start_time

    metadata = DocumentMetadata(
        pages=pages,
        tables=tables,
        images=images,
        formulas=formulas,
        processing_time=processing_time,
        source_format=source_format,
        file_size=file_size
    )

    chunks = None
    if enable_chunking:
        raw_chunks = chunk_text(content, chunk_size, chunk_overlap)
        chunks = [
            DocumentChunk(
                text=c['text'],
                metadata=ChunkMetadata(
                    chunk_index=c['metadata']['chunk_index'],
                    chunk_type=c['metadata']['chunk_type']
                )
            )
            for c in raw_chunks
        ]

    return ConversionResponse(
        content=content,
        format=output_format,
        metadata=metadata,
        chunks=chunks
    )
