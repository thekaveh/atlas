from __future__ import annotations

import os
from typing import Any

from celery import Celery


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


def celery_is_enabled() -> bool:
    return (
        os.getenv("CELERY_SOURCE", "disabled") == "container"
        and bool(os.getenv("CELERY_BROKER_URL"))
        and bool(os.getenv("CELERY_RESULT_BACKEND"))
    )


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
        payload["error"] = str(result.result)
    return payload
