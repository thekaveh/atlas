"""Generic RAG ingestion job contract (#413).

Atlas owns the repeatable ingestion lifecycle (discover → parse → chunk → embed →
vector write → LightRAG upload → drain → finalize) across documents, vector
stores, and LightRAG; a downstream consumer declares a versioned
``rag_ingestion_profiles`` block and submits jobs headlessly. The engine records a
durable, machine-readable job with per-phase status/counts/timing and actionable
errors, capability-gates targets by enabled SOURCE, and is idempotent by
consumer + profile revision + corpus digest.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from .models import (
    RagIngestionQueuedResponse,
    RagIngestionRecordResponse,
    RagIngestionRequest,
)
from .profiles import ProfileNotFoundError, get_profile, load_profiles
from .service import (
    Deps,
    IngestionExecutionBusy,
    IngestionExecutionLeaseLost,
    RagIngestionService,
    ingestion_execution_lease_seconds,
)

__all__ = [
    "RagIngestionService",
    "Deps",
    "RagIngestionRequest",
    "RagIngestionQueuedResponse",
    "RagIngestionRecordResponse",
    "ProfileNotFoundError",
    "get_profile",
    "load_profiles",
    "run_rag_ingestion",
    "IngestionExecutionBusy",
    "IngestionExecutionLeaseLost",
    "ingestion_execution_lease_seconds",
]


def run_rag_ingestion(
    ingestion_id: str, *, execution_owner: Optional[str] = None
) -> Dict[str, Any]:
    """Synchronous entrypoint for the Celery worker: run an already-submitted
    ingestion to completion and return its final record dict."""
    service = RagIngestionService()
    record = asyncio.run(
        service.run(
            ingestion_id,
            retry_transient=True,
            execution_owner=execution_owner,
        )
    )
    return record.to_dict()
