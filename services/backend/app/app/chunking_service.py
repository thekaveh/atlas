from __future__ import annotations

import os
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


ChunkStrategy = Literal["semantic", "recursive", "token"]


class ChunkingError(RuntimeError):
    pass


class ChunkingDependencyError(ChunkingError):
    pass


class ChunkRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    strategy: ChunkStrategy = "recursive"
    chunk_size: int = Field(default=512, ge=1, le=8192)
    overlap: int = Field(default=64, ge=0, le=2048)
    tokenizer: str = Field(default="gpt2", min_length=1, max_length=64)
    semantic_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    min_characters_per_sentence: int = Field(default=24, ge=1, le=512)

    @model_validator(mode="after")
    def _validate_overlap(self):
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        return self


class TextChunk(BaseModel):
    index: int
    start_char: int
    end_char: int
    content: str
    token_count: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChunkResponse(BaseModel):
    strategy: ChunkStrategy
    chunk_size: int
    overlap: int
    chunk_count: int
    chunks: List[TextChunk]
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _import_chonkie():
    try:
        from chonkie import RecursiveChunker, SemanticChunker, TokenChunker
    except ImportError as exc:
        raise ChunkingDependencyError("Chonkie is not installed in this runtime") from exc
    return RecursiveChunker, SemanticChunker, TokenChunker


def _call_chunker(chunker: Any, text: str, *, strategy: ChunkStrategy):
    if strategy == "token":
        return chunker(text, show_progress_bar=False)
    if strategy == "recursive":
        return chunker(text, show_progress=False)
    return chunker(text)


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int, str]:
    raw = text[start:end]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw.rstrip())
    trimmed_start = start + leading
    trimmed_end = start + trailing
    return trimmed_start, trimmed_end, text[trimmed_start:trimmed_end]


def _fallback_span(source_text: str, content: str, cursor: int) -> tuple[int, int]:
    found = source_text.find(content, cursor)
    if found == -1:
        found = source_text.find(content)
    if found == -1:
        return cursor, cursor + len(content)
    return found, found + len(content)


def _normalize_chunks(raw_chunks: list[Any], source_text: str) -> List[TextChunk]:
    normalized: List[TextChunk] = []
    cursor = 0
    for raw_chunk in raw_chunks:
        raw_text = str(getattr(raw_chunk, "text", raw_chunk))
        start = getattr(raw_chunk, "start_index", None)
        end = getattr(raw_chunk, "end_index", None)
        if start is None or end is None:
            start, end = _fallback_span(source_text, raw_text, cursor)
        start = max(0, int(start))
        end = min(len(source_text), int(end))
        start, end, content = _trimmed_span(source_text, start, end)
        if not content:
            continue
        cursor = end
        normalized.append(
            TextChunk(
                index=len(normalized),
                start_char=start,
                end_char=end,
                content=content,
                token_count=getattr(raw_chunk, "token_count", None),
                metadata=dict(getattr(raw_chunk, "metadata", {}) or {}),
            )
        )
    return normalized


def _semantic_embedding_model_override(semantic_embedding_model: Any | None) -> Any:
    if semantic_embedding_model is not None:
        return semantic_embedding_model
    return os.getenv("CHONKIE_SEMANTIC_EMBEDDING_MODEL", "minishlab/potion-base-32M")


def chunk_text(
    request: ChunkRequest,
    *,
    semantic_embedding_model: Any | None = None,
) -> ChunkResponse:
    RecursiveChunker, SemanticChunker, TokenChunker = _import_chonkie()
    metadata: Dict[str, Any] = {
        "tokenizer": request.tokenizer,
        "overlap": request.overlap,
        "overlap_applied": request.strategy == "token",
    }

    try:
        if request.strategy == "token":
            chunker = TokenChunker(
                tokenizer=request.tokenizer,
                chunk_size=request.chunk_size,
                chunk_overlap=request.overlap,
            )
        elif request.strategy == "recursive":
            metadata["overlap_ignored_reason"] = (
                "Chonkie RecursiveChunker does not accept overlap in the current API"
            )
            chunker = RecursiveChunker(
                tokenizer=request.tokenizer,
                chunk_size=request.chunk_size,
                min_characters_per_chunk=min(
                    request.min_characters_per_sentence,
                    request.chunk_size,
                ),
            )
        else:
            metadata["semantic_threshold"] = request.semantic_threshold
            metadata["overlap_ignored_reason"] = (
                "Chonkie SemanticChunker does not accept overlap in the current API"
            )
            chunker = SemanticChunker(
                embedding_model=_semantic_embedding_model_override(semantic_embedding_model),
                threshold=request.semantic_threshold,
                chunk_size=request.chunk_size,
                min_characters_per_sentence=request.min_characters_per_sentence,
            )
        raw_chunks = _call_chunker(chunker, request.text, strategy=request.strategy)
    except ChunkingDependencyError:
        raise
    except Exception as exc:
        raise ChunkingError(f"Chunking failed for strategy {request.strategy!r}: {exc}") from exc

    chunks = _normalize_chunks(raw_chunks, request.text)
    return ChunkResponse(
        strategy=request.strategy,
        chunk_size=request.chunk_size,
        overlap=request.overlap,
        chunk_count=len(chunks),
        chunks=chunks,
        metadata=metadata,
    )
