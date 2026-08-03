"""Non-blocking, deadline-bounded model startup for Parakeet providers."""

import asyncio
import logging
import os
from typing import Any, Callable


FATAL_TIMEOUT_EXIT_CODE = 70
logger = logging.getLogger(__name__)


class ModelStartup:
    """Own one off-event-loop model load and its truthful readiness state."""

    def __init__(
        self,
        provider: str,
        loader: Callable[[], Any],
        *,
        timeout_seconds: float,
        terminate: Callable[[int], None] = os._exit,
    ):
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise ValueError("startup timeout must be greater than 0 and at most 3600")
        self._provider = provider
        self._loader = loader
        self._timeout_seconds = timeout_seconds
        self._terminate = terminate
        self._task = None
        self._model = None
        self._state = "loading"
        self._start_count = 0

    @property
    def state(self) -> str:
        return self._state

    @property
    def model(self):
        return self._model

    @property
    def start_count(self) -> int:
        return self._start_count

    def start(self) -> asyncio.Task:
        if self._task is None:
            self._start_count += 1
            self._task = asyncio.create_task(self._run())
        return self._task

    async def _run(self) -> None:
        native_task = asyncio.create_task(asyncio.to_thread(self._loader))
        try:
            self._model = await asyncio.wait_for(
                asyncio.shield(native_task),
                timeout=self._timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            self._state = "unhealthy"
            logger.error(
                "Provider startup deadline exceeded (provider=%s)",
                self._provider,
            )
            self._terminate(FATAL_TIMEOUT_EXIT_CODE)
        except Exception as exc:
            self._state = "unhealthy"
            logger.error(
                "Provider startup failed (provider=%s, error_type=%s)",
                self._provider,
                type(exc).__name__,
            )
        else:
            self._state = "healthy"
