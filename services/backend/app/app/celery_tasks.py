from __future__ import annotations

import asyncio
from typing import Any, Optional
from uuid import uuid4

import httpx
from celery.utils.time import get_exponential_backoff_interval

from celery_app import celery_app
from memory_service import MemoryService


TRANSIENT_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.NetworkError,
)


def run_memory_consolidate(user_id: Optional[str]) -> dict[str, Any]:
    return asyncio.run(
        MemoryService().consolidate(user_id=user_id, retry_transient=True)
    )


@celery_app.task(
    name="memory_consolidate",
    autoretry_for=TRANSIENT_EXCEPTIONS,
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def memory_consolidate_task(user_id: Optional[str] = None) -> dict[str, Any]:
    return run_memory_consolidate(user_id)


@celery_app.task(
    bind=True,
    name="rag_ingestion",
    max_retries=None,
)
def rag_ingestion_task(
    self, ingestion_id: str, transient_attempt: int = 0
) -> dict[str, Any]:
    """Run a submitted RAG ingestion job to completion (#413). The record was
    already created + persisted by the submit endpoint; this drives the phases and
    updates the shared store so the status endpoint stays observable."""
    from rag_ingestion import (
        IngestionExecutionBusy,
        ingestion_execution_lease_seconds,
        run_rag_ingestion,
    )

    owner = f"{self.request.id or 'celery'}:{uuid4()}"
    try:
        return run_rag_ingestion(ingestion_id, execution_owner=owner)
    except IngestionExecutionBusy as exc:
        raise self.retry(
            exc=exc,
            countdown=ingestion_execution_lease_seconds(),
        )
    except TRANSIENT_EXCEPTIONS as exc:
        if transient_attempt >= 3:
            raise
        countdown = get_exponential_backoff_interval(
            factor=1,
            retries=transient_attempt,
            maximum=600,
            full_jitter=True,
        )
        raise self.retry(
            exc=exc,
            countdown=countdown,
            args=(),
            kwargs={
                "ingestion_id": ingestion_id,
                "transient_attempt": transient_attempt + 1,
            },
        )
