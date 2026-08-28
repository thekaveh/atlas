"""Data model for the generic RAG ingestion job contract (#413).

The engine records a durable, machine-readable job with per-phase status, counts,
timing, and actionable per-file errors. These are plain dataclasses (serialized to
JSON for the store + the status endpoint) plus the Pydantic request/response
models the FastAPI surface uses. Nothing here does I/O.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# Ordered ingestion lifecycle. Every phase is recorded even when skipped, so a
# status reader can see exactly where a job is (or why a target was skipped).
PHASES = (
    "discover",
    "parse",
    "chunk",
    "embed",
    "vector_write",
    "lightrag_upload",
    "drain",
    "finalize",
)

# Terminal + active job statuses.
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
DISPATCH_PREPARED = "prepared"
DISPATCH_DISPATCHING = "dispatching"
DISPATCH_ACCEPTED = "accepted"
_TERMINAL = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})
# A prior job with one of these statuses satisfies an idempotent re-submit (we do
# NOT re-run it). A failed job is allowed to be retried under the same key.
_DEDUP_STATUSES = frozenset({STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED})


@dataclass
class PhaseRecord:
    name: str
    status: str = STATUS_PENDING  # pending | running | completed | failed | skipped
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_ms: Optional[int] = None
    counts: Dict[str, int] = field(default_factory=dict)
    note: Optional[str] = None
    error: Optional[Dict[str, Any]] = None


@dataclass
class IngestionError:
    """An actionable failure record: which file, phase, upstream service, and the
    HTTP status/body when the failure came from an upstream call."""

    phase: str
    message: str
    file: Optional[str] = None
    service: Optional[str] = None
    http_status: Optional[int] = None
    body: Optional[str] = None


@dataclass
class IngestionRecord:
    id: str
    consumer: str
    profile: str
    revision: str
    idempotency_key: str
    status: str = STATUS_PENDING
    phases: List[PhaseRecord] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    content_digest: Optional[str] = None
    profile_snapshot: Optional[Dict[str, Any]] = None
    corpus: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    cancel_requested: bool = False
    dispatch_state: str = DISPATCH_PREPARED
    dispatch_job_id: Optional[str] = None
    dispatch_owner: Optional[str] = None
    dispatch_claimed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IngestionRecord":
        def list_field(name: str) -> List[Any]:
            value = data.get(name, [])
            # Redis Lua cjson represents an empty table as {}, losing whether
            # Python originally supplied [] or {}. Both persisted list fields
            # are unambiguous here, so repair only absent/empty legacy shapes.
            if value is None or value == {}:
                return []
            if not isinstance(value, list):
                raise ValueError(f"{name} must be a list")
            return value

        phases = [PhaseRecord(**p) for p in list_field("phases")]
        payload = {k: v for k, v in data.items() if k != "phases"}
        payload["errors"] = list_field("errors")
        return cls(phases=phases, **payload)

    def phase(self, name: str) -> PhaseRecord:
        for p in self.phases:
            if p.name == name:
                return p
        record = PhaseRecord(name=name)
        self.phases.append(record)
        return record

    def add_error(self, error: IngestionError) -> None:
        self.errors.append(asdict(error))

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def is_dedup_candidate(self) -> bool:
        return self.status in _DEDUP_STATUSES


# ─── FastAPI request/response models ─────────────────────────────────

class RagIngestionRequest(BaseModel):
    profile: str = Field(..., min_length=1, max_length=128, description="Declared profile name.")
    # Optional corpus override — still resolved through the same path-safety
    # boundary as the profile's declared corpus (never an arbitrary host path).
    corpus_path: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Override the profile's mount corpus path (relative, no '..').",
    )


class RagIngestionQueuedResponse(BaseModel):
    ingestion_id: str
    job_id: Optional[str] = None
    status: str
    message: str
    task: str = "rag_ingestion"


class RagIngestionRecordResponse(BaseModel):
    id: str
    consumer: str
    profile: str
    revision: str
    idempotency_key: str
    status: str
    phases: List[Dict[str, Any]]
    counts: Dict[str, int]
    errors: List[Dict[str, Any]]
    content_digest: Optional[str] = None
    profile_snapshot: Optional[Dict[str, Any]] = None
    corpus: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    cancel_requested: bool = False
    dispatch_state: str = DISPATCH_PREPARED
    dispatch_job_id: Optional[str] = None
