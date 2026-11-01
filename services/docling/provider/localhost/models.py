"""
Pydantic models for Docling Document Processor API
"""

from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class DocumentMetadata(BaseModel):
    """Metadata about processed document"""
    pages: int
    tables: int
    images: int
    formulas: int
    processing_time: float
    source_format: str
    file_size: int


class ChunkMetadata(BaseModel):
    """Metadata for a document chunk"""
    chunk_index: int
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    chunk_type: str  # text, table, image, formula, code


class DocumentChunk(BaseModel):
    """A single chunk of processed document"""
    text: str
    metadata: ChunkMetadata


class ConversionResponse(BaseModel):
    """Response from document conversion endpoint"""
    content: str
    format: str
    metadata: DocumentMetadata
    chunks: Optional[List[DocumentChunk]] = None


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    backend: str
    device: str
    models_loaded: List[str] = []
