from __future__ import annotations

import os
from typing import Any

from celery import Celery


def _redis_url() -> str:
    return os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL", "")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


celery_app = Celery(
    "atlas_backend",
    broker=_redis_url(),
    backend=os.getenv("CELERY_RESULT_BACKEND") or _redis_url(),
    include=["celery_tasks"],
)

_visibility_timeout = _int_env("CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS", 3600)
celery_app.conf.update(
    task_default_queue=os.getenv("CELERY_QUEUE", "atlas"),
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=_int_env("CELERY_WORKER_PREFETCH_MULTIPLIER", 1),
    task_soft_time_limit=_int_env("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", 840),
    task_time_limit=_int_env("CELERY_TASK_TIME_LIMIT_SECONDS", 900),
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
