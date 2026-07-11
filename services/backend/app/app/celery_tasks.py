from __future__ import annotations

import asyncio
from typing import Any, Optional

from celery_app import celery_app
from memory_service import MemoryService


def run_memory_consolidate(user_id: Optional[str]) -> dict[str, Any]:
    return asyncio.run(MemoryService().consolidate(user_id=user_id))


@celery_app.task(
    name="memory_consolidate",
    autoretry_for=(TimeoutError, ConnectionError),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def memory_consolidate_task(user_id: Optional[str] = None) -> dict[str, Any]:
    return run_memory_consolidate(user_id)


@celery_app.task(
    name="rag_ingestion",
    autoretry_for=(TimeoutError, ConnectionError),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def rag_ingestion_task(ingestion_id: str) -> dict[str, Any]:
    """Run a submitted RAG ingestion job to completion (#413). The record was
    already created + persisted by the submit endpoint; this drives the phases and
    updates the shared store so the status endpoint stays observable."""
    from rag_ingestion import run_rag_ingestion

    return run_rag_ingestion(ingestion_id)
