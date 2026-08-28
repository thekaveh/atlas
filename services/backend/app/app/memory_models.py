"""
Pydantic models for the LangMem memory service API.
"""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List
import json


def _validate_uuid(value: str) -> str:
    """Validate that a string is a valid UUID."""
    UUID(value)  # Raises ValueError if invalid
    return value


# --- Request Models ---

class MemoryMessage(BaseModel):
    """Bounded chat message accepted by memory extraction."""
    role: str = Field(default="user", min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=20000)


class MemoryExtractRequest(BaseModel):
    """Request to extract facts from conversation messages."""
    user_id: str
    messages: List[MemoryMessage] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Conversation messages in [{role, content}] format"
    )
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=128)
    conversation_id: Optional[str] = None

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        return _validate_uuid(v)

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_uuid(v)
        return v


class MemoryRecallRequest(BaseModel):
    """Request to recall relevant memories for a query."""
    user_id: str
    query: str = Field(min_length=1, max_length=4000)
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=10, ge=1, le=100)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        return _validate_uuid(v)


class MemoryConsolidateRequest(BaseModel):
    """Request to consolidate/deduplicate a user's memories."""
    user_id: Optional[str] = Field(
        default=None,
        description="User ID to consolidate. If None, consolidates all users."
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Stable retry key for asynchronous consolidation dispatch.",
    )

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _validate_uuid(v)
        return v


class MemorySummarizeRequest(BaseModel):
    """Request to generate a user memory profile summary."""
    user_id: str
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        return _validate_uuid(v)


VALID_FACT_TYPES = ("observation", "preference", "instruction", "relationship", "event")


class MemoryUpdateRequest(BaseModel):
    """Request to update a specific memory fact."""
    content: Optional[str] = Field(default=None, max_length=20000)
    fact_type: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    is_active: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("fact_type")
    @classmethod
    def validate_fact_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_FACT_TYPES:
            raise ValueError(f"fact_type must be one of: {', '.join(VALID_FACT_TYPES)}")
        return v

    @field_validator("metadata")
    @classmethod
    def validate_metadata_size(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is not None:
            encoded = json.dumps(v, separators=(",", ":"), sort_keys=True)
            if len(encoded.encode("utf-8")) > 20000:
                raise ValueError("metadata must serialize to 20000 bytes or fewer")
        return v


# --- Response Models ---

class MemoryFact(BaseModel):
    """A single memory fact."""
    id: str
    content: str
    fact_type: str
    confidence: float
    namespace: str
    is_active: bool
    created_at: str
    updated_at: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryExtractResponse(BaseModel):
    """Response from memory extraction."""
    session_id: str
    status: str
    facts_extracted: int
    facts: List[MemoryFact]


class MemoryRecallResponse(BaseModel):
    """Response from memory recall."""
    memories: List[MemoryFact]
    context_summary: Optional[str] = None


class MemoryConsolidateResponse(BaseModel):
    """Response from memory consolidation."""
    user_id: Optional[str] = None
    facts_reviewed: int
    facts_merged: int
    facts_superseded: int
    facts_expired: int


class MemorySummarizeResponse(BaseModel):
    """Response from memory summarization."""
    user_id: str
    summary: str
    total_facts: int


class MemoryListResponse(BaseModel):
    """Response from listing memories."""
    user_id: str
    memories: List[MemoryFact]
    total: int


class MemoryHealthResponse(BaseModel):
    """Response from memory health check."""
    status: str
    vector_backend: str
    facts_count: int
    enabled: bool
    error: Optional[str] = None
