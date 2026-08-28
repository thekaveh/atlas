from __future__ import annotations

import os
import json
import logging
from typing import Any

from celery import Celery
from celery.signals import worker_process_init

from observability import configure_celery_otel


logger = logging.getLogger(__name__)


def _redis_url() -> str:
    return os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL", "")


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _load_worker_limits() -> dict[str, int]:
    limits = {
        "worker_concurrency": _positive_int_env("CELERY_WORKER_CONCURRENCY", 2),
        "worker_prefetch_multiplier": _positive_int_env(
            "CELERY_WORKER_PREFETCH_MULTIPLIER", 1
        ),
        "task_soft_time_limit": _positive_int_env(
            "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", 840
        ),
        "task_time_limit": _positive_int_env(
            "CELERY_TASK_TIME_LIMIT_SECONDS", 900
        ),
        "visibility_timeout": _positive_int_env(
            "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS", 3600
        ),
    }
    if limits["task_soft_time_limit"] >= limits["task_time_limit"]:
        raise ValueError(
            "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS must be less than "
            "CELERY_TASK_TIME_LIMIT_SECONDS"
        )
    if limits["visibility_timeout"] <= limits["task_time_limit"]:
        raise ValueError(
            "CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS must be greater than "
            "CELERY_TASK_TIME_LIMIT_SECONDS"
        )
    return limits


celery_app = Celery(
    "atlas_backend",
    broker=_redis_url(),
    backend=os.getenv("CELERY_RESULT_BACKEND") or _redis_url(),
    include=["celery_tasks"],
)

_worker_limits = _load_worker_limits()
_visibility_timeout = _worker_limits["visibility_timeout"]
celery_app.conf.update(
    task_default_queue=os.getenv("CELERY_QUEUE", "atlas"),
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_concurrency=_worker_limits["worker_concurrency"],
    worker_prefetch_multiplier=_worker_limits["worker_prefetch_multiplier"],
    task_soft_time_limit=_worker_limits["task_soft_time_limit"],
    task_time_limit=_worker_limits["task_time_limit"],
    broker_transport_options={"visibility_timeout": _visibility_timeout},
    result_backend_transport_options={
        "visibility_timeout": _visibility_timeout,
        "global_keyprefix": "atlas:celery:",
        "retry_policy": {"timeout": 5.0},
    },
    visibility_timeout=_visibility_timeout,
)


@worker_process_init.connect(weak=False)
def _configure_worker_otel(*_args, **_kwargs) -> None:
    configure_celery_otel(service_name="backend-celery-worker")


def celery_is_enabled() -> bool:
    return (
        os.getenv("CELERY_SOURCE", "disabled") == "container"
        and bool(os.getenv("CELERY_BROKER_URL"))
        and bool(os.getenv("CELERY_RESULT_BACKEND"))
    )


def _memory_execution_key(task_id: str) -> str:
    return f"atlas:celery:memory-execution:{task_id}"


def memory_execution_lease_seconds() -> int:
    return _worker_limits["task_time_limit"] + 60


def claim_memory_execution(
    task_id: str, owner: str, recovery_owner: str | None = None
) -> tuple[str, dict[str, Any] | None]:
    """Claim consolidation execution or return its completed result."""

    from redis import Redis

    client = Redis.from_url(_redis_url(), decode_responses=True)
    try:
        key = _memory_execution_key(task_id)
        if client.set(
            key,
            f"running:{owner}",
            nx=True,
            ex=memory_execution_lease_seconds(),
        ):
            return "claimed", None
        current = client.get(key) or ""
        if current.startswith("done:"):
            return "done", json.loads(current.removeprefix("done:"))
        if recovery_owner and current == f"running:{recovery_owner}":
            refreshed = client.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                "return redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3]) "
                "else return 0 end",
                1,
                key,
                f"running:{recovery_owner}",
                f"running:{owner}",
                memory_execution_lease_seconds(),
            )
            return ("claimed", None) if refreshed else ("busy", None)
        return "busy", None
    finally:
        client.close()


def release_memory_execution(task_id: str, owner: str) -> bool:
    """Release only the caller's live consolidation execution lease."""

    from redis import Redis

    client = Redis.from_url(_redis_url(), decode_responses=True)
    try:
        return bool(
            client.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                "return redis.call('DEL', KEYS[1]) else return 0 end",
                1,
                _memory_execution_key(task_id),
                f"running:{owner}",
            )
        )
    finally:
        client.close()


def complete_memory_execution(
    task_id: str, owner: str, result: dict[str, Any]
) -> bool:
    """Atomically replace the caller's lease with a bounded result marker."""

    from redis import Redis

    client = Redis.from_url(_redis_url(), decode_responses=True)
    try:
        return bool(
            client.eval(
                "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end "
                "redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3]); return 1",
                1,
                _memory_execution_key(task_id),
                f"running:{owner}",
                "done:" + json.dumps(result, separators=(",", ":"), sort_keys=True),
                _visibility_timeout,
            )
        )
    finally:
        client.close()


def _normalize_status(status: str) -> str:
    mapping = {
        "PENDING": "pending",
        "RECEIVED": "pending",
        "STARTED": "running",
        "RETRY": "retry",
        "SUCCESS": "success",
        "FAILURE": "failure",
        "REVOKED": "revoked",
    }
    return mapping.get((status or "").upper(), (status or "unknown").lower())


def get_celery_job_status(job_id: str) -> dict[str, Any]:
    result = celery_app.AsyncResult(job_id)
    status = _normalize_status(result.status)
    ready = bool(result.ready())
    successful = bool(result.successful()) if ready else False
    failed = bool(result.failed()) if ready else False

    payload: dict[str, Any] = {
        "job_id": job_id,
        "status": status,
        "ready": ready,
        "successful": successful,
        "failed": failed,
        "result": None,
        "error": None,
        "traceback": None,
    }
    if successful:
        payload["result"] = result.result
    elif failed:
        logger.error(
            "Celery job %s failed (error_type=%s)",
            job_id,
            type(result.result).__name__,
        )
        payload["error"] = "Background job failed"
    return payload
