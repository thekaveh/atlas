from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from uuid import uuid4

import httpx
from celery.utils.time import get_exponential_backoff_interval

from celery_app import (
    celery_app,
    claim_memory_execution,
    complete_memory_execution,
    memory_execution_lease_seconds,
    release_memory_execution,
)
from db_connection import close_pg_pools
from memory_service import MemoryService


logger = logging.getLogger(__name__)

TRANSIENT_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.NetworkError,
)


async def _run_memory_consolidate(user_id: Optional[str]) -> dict[str, Any]:
    try:
        return await MemoryService().consolidate(
            user_id=user_id, retry_transient=True
        )
    finally:
        # ``asyncio.run`` owns a fresh loop for each Celery task invocation.
        # asyncpg pools are loop-bound, so no cached pool may outlive it.
        try:
            await close_pg_pools()
        except Exception:
            # Cleanup must not replace the transient task error that Celery's
            # autoretry policy needs to observe.
            logger.exception("Postgres pool cleanup failed after consolidation")


def run_memory_consolidate(user_id: Optional[str]) -> dict[str, Any]:
    return asyncio.run(_run_memory_consolidate(user_id))


def _release_memory_execution_safely(task_id: str, owner: str) -> None:
    try:
        release_memory_execution(task_id, owner)
    except Exception:
        logger.exception("Memory consolidation execution lease release failed")


@celery_app.task(bind=True, name="memory_consolidate", max_retries=None)
def memory_consolidate_task(
    self,
    user_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    transient_attempt: int = 0,
) -> dict[str, Any]:
    if idempotency_key is None:
        return run_memory_consolidate(user_id)
    owner = f"{self.request.id or 'celery'}:{uuid4()}"
    state, completed = claim_memory_execution(idempotency_key, owner)
    if state == "done":
        assert completed is not None
        return completed
    if state == "busy":
        raise self.retry(countdown=memory_execution_lease_seconds())
    try:
        result = run_memory_consolidate(user_id)
    except TRANSIENT_EXCEPTIONS as exc:
        _release_memory_execution_safely(idempotency_key, owner)
        if transient_attempt >= 3:
            raise
        countdown = get_exponential_backoff_interval(
            factor=1,
            retries=transient_attempt,
            maximum=60,
            full_jitter=True,
        )
        raise self.retry(
            exc=exc,
            countdown=countdown,
            kwargs={
                "user_id": user_id,
                "idempotency_key": idempotency_key,
                "transient_attempt": transient_attempt + 1,
            },
        )
    except Exception:
        _release_memory_execution_safely(idempotency_key, owner)
        raise
    if not complete_memory_execution(idempotency_key, owner, result):
        raise RuntimeError("Memory consolidation execution lease was lost")
    return result


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
        IngestionExecutionLeaseLost,
        ingestion_execution_lease_seconds,
        run_rag_ingestion,
    )

    owner = f"{self.request.id or 'celery'}:{uuid4()}"
    try:
        return run_rag_ingestion(
            ingestion_id,
            execution_owner=owner,
            retry_transient=transient_attempt < 3,
        )
    except IngestionExecutionBusy as exc:
        raise self.retry(
            exc=exc,
            countdown=ingestion_execution_lease_seconds(),
        )
    except IngestionExecutionLeaseLost as exc:
        raise self.retry(
            exc=exc,
            countdown=ingestion_execution_lease_seconds(),
            args=(),
            kwargs={
                "ingestion_id": ingestion_id,
                "transient_attempt": transient_attempt,
            },
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
