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
