"""
Utility functions for Docling document processing
"""

import os
import hashlib
from typing import Dict, Any, List
from pathlib import Path


MAX_CHUNKS = 10_000


class ChunkLimitError(ValueError):
    """Raised when one conversion would emit an unsafe number of chunks."""


def get_file_hash(file_path: str) -> str:
    """Calculate SHA256 hash of file"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def get_file_size(file_path: str) -> int:
    """Get file size in bytes"""
    return os.path.getsize(file_path)


def detect_format(file_path: str) -> str:
    """Detect document format from file extension"""
    suffix = Path(file_path).suffix.lower()
    format_map = {
        '.pdf': 'pdf',
        '.docx': 'docx',
        '.doc': 'doc',
        '.pptx': 'pptx',
        '.ppt': 'ppt',
        '.xlsx': 'xlsx',
        '.html': 'html',
        '.htm': 'html',
        '.png': 'image',
        '.jpg': 'image',
        '.jpeg': 'image',
        '.tiff': 'image',
        '.tif': 'image',
    }
    return format_map.get(suffix, 'unknown')


def validate_file_size(file_path: str, max_size: int) -> bool:
    """Validate file size against maximum"""
    return get_file_size(file_path) <= max_size


def chunk_text(text: str, chunk_size: int = 512, chunk_overlap: int = 50) -> List[Dict[str, Any]]:
    """
    Chunk text into smaller pieces for RAG

    Args:
        text: Text to chunk
        chunk_size: Size of each chunk in characters
        chunk_overlap: Overlap between chunks

    Returns:
        List of chunk dictionaries with text and metadata
    """
    chunks = []
    # Clamp caller-supplied values: these arrive as raw Form() ints, and
    # chunk_overlap >= chunk_size (or chunk_size <= 0) made
    # `start = end - chunk_overlap` never advance — an infinite loop
    # appending chunks until OOM.
    if chunk_size <= 0:
        chunk_size = 512  # matches the Form()/processor default
    if chunk_overlap < 0 or chunk_overlap * 2 > chunk_size:
        chunk_overlap = 0
    start = 0
    chunk_index = 0

    while start < len(text):
        if chunk_index >= MAX_CHUNKS:
            raise ChunkLimitError(
                f"Document would exceed the {MAX_CHUNKS} chunk response limit"
            )
        end = start + chunk_size
        chunk_text = text[start:end]

        chunks.append({
            'text': chunk_text,
            'metadata': {
                'chunk_index': chunk_index,
                'start_char': start,
                'end_char': end,
                'chunk_type': 'text'
            }
        })

        start = end - chunk_overlap
        chunk_index += 1

    return chunks
