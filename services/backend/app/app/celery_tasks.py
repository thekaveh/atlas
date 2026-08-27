from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any, Optional
from uuid import uuid4

import httpx
from celery.utils.time import get_exponential_backoff_interval
from redis.exceptions import RedisError

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
    RedisError,
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


@dataclass(frozen=True)
class _MemoryRetryState:
    user_id: Optional[str]
    idempotency_key: str
    attempt: int = 0
    result: Optional[dict[str, Any]] = None
    owner: Optional[str] = None

    def retry_kwargs(self) -> dict[str, Any]:
        state: dict[str, Any] = {"attempt": self.attempt}
        if self.owner is not None:
            state["owner"] = self.owner
        if self.result is not None:
            state["result"] = self.result
        return {
            "user_id": self.user_id,
            "idempotency_key": self.idempotency_key,
            "retry_state": state,
        }


def _memory_retry_state(
    user_id: Optional[str], idempotency_key: str, retry_state: Optional[dict[str, Any]]
) -> _MemoryRetryState:
    payload = retry_state or {}
    if not isinstance(payload, dict):
        raise ValueError("retry_state must be an object")
    attempt = payload.get("attempt", 0)
    result = payload.get("result")
    owner = payload.get("owner")
    if type(attempt) is not int or attempt < 0:
        raise ValueError("retry_state.attempt must be a non-negative integer")
    if result is not None and not isinstance(result, dict):
        raise ValueError("retry_state.result must be an object")
    if owner is not None and not isinstance(owner, str):
        raise ValueError("retry_state.owner must be a string")
    return _MemoryRetryState(user_id, idempotency_key, attempt, result, owner)


def _retry_memory_transient(
    task, exc: BaseException, state: _MemoryRetryState
) -> None:
    if state.attempt >= 3:
        raise exc
    countdown = get_exponential_backoff_interval(
        factor=1,
        retries=state.attempt,
        maximum=60,
        full_jitter=True,
    )
    retry_state = replace(state, attempt=state.attempt + 1)
    raise task.retry(exc=exc, countdown=countdown, kwargs=retry_state.retry_kwargs())


def _claim_memory_execution_or_retry(
    task, state: _MemoryRetryState, recovery_owner: Optional[str] = None
) -> tuple[bool, Optional[dict[str, Any]]]:
    if state.owner is None:
        raise ValueError("memory claim requires an owner")
    try:
        claim_state, completed = claim_memory_execution(
            state.idempotency_key, state.owner, recovery_owner
        )
    except RedisError as exc:
        _retry_memory_transient(task, exc, state)
    if claim_state == "done":
        assert completed is not None
        return False, completed
    if claim_state == "busy":
        raise task.retry(
            countdown=memory_execution_lease_seconds(), kwargs=state.retry_kwargs()
        )
    return True, None


def _resume_memory_completion(
    task, state: _MemoryRetryState
) -> dict[str, Any]:
    """Retry only the ambiguous Redis result commit, never consolidation."""
    if state.result is None or state.owner is None:
        raise ValueError("completion retry requires result and owner")
    try:
        if complete_memory_execution(
            state.idempotency_key, state.owner, state.result
        ):
            return state.result
        claim_state, completed = claim_memory_execution(
            state.idempotency_key, state.owner
        )
        if claim_state == "done":
            assert completed is not None
            return completed
        if claim_state == "busy":
            raise task.retry(
                countdown=memory_execution_lease_seconds(),
                kwargs=state.retry_kwargs(),
            )
        if not complete_memory_execution(
            state.idempotency_key, state.owner, state.result
        ):
            raise RuntimeError("Memory consolidation execution lease was lost")
    except RedisError as exc:
        _retry_memory_transient(task, exc, state)
    return state.result


def _run_claimed_memory(task, state: _MemoryRetryState) -> dict[str, Any]:
    if state.owner is None:
        raise ValueError("claimed memory execution requires an owner")
    try:
        result = run_memory_consolidate(state.user_id)
    except TRANSIENT_EXCEPTIONS as exc:
        _release_memory_execution_safely(state.idempotency_key, state.owner)
        _retry_memory_transient(task, exc, state)
    except Exception:
        _release_memory_execution_safely(state.idempotency_key, state.owner)
        raise
    completion_state = replace(state, result=result)
    try:
        completed = complete_memory_execution(
            state.idempotency_key, state.owner, result
        )
    except RedisError as exc:
        _retry_memory_transient(task, exc, completion_state)
    if not completed:
        raise RuntimeError("Memory consolidation execution lease was lost")
    return result


@celery_app.task(bind=True, name="memory_consolidate", max_retries=None)
def memory_consolidate_task(
    self,
    user_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    retry_state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if idempotency_key is None:
        return run_memory_consolidate(user_id)
    state = _memory_retry_state(user_id, idempotency_key, retry_state)
    if state.result is not None:
        return _resume_memory_completion(self, state)
    recovery_owner = state.owner
    owner = f"{self.request.id or 'celery'}:{uuid4()}"
    state = replace(state, owner=owner)
    should_run, completed = _claim_memory_execution_or_retry(
        self, state, recovery_owner
    )
    if not should_run:
        assert completed is not None
        return completed
    return _run_claimed_memory(self, state)


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
