"""Non-blocking, deadline-bounded model startup for Parakeet providers."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
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
        shutdown_timeout_seconds: float = 10.0,
        terminate: Callable[[int], None] = os._exit,
    ):
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise ValueError("startup timeout must be greater than 0 and at most 3600")
        if shutdown_timeout_seconds <= 0 or shutdown_timeout_seconds > 60:
            raise ValueError("shutdown timeout must be greater than 0 and at most 60")
        self._provider = provider
        self._loader = loader
        self._timeout_seconds = timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._terminate = terminate
        self._task = None
        self._native_task = None
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
        self._native_task = native_task
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
            # Mirror the deadline branch: a failed load (transient HuggingFace
            # rate-limit / CUDA OOM, or a deterministic misconfig) must take the
            # process down for supervised restart. Docker restarts on exit, not on
            # a failed healthcheck, so staying alive here leaves the provider
            # returning 503 indefinitely. A deterministic misconfig crash-loops
            # under Docker's restart backoff — visible and bounded, unlike a
            # silently wedged container.
            self._terminate(FATAL_TIMEOUT_EXIT_CODE)
        else:
            self._state = "healthy"

    async def shutdown(self) -> None:
        """Bound shutdown even though Python cannot cancel native model loading."""
        pending = self._native_task
        if pending is None or pending.done():
            pending = self._task
        if pending is None or pending.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(pending), timeout=self._shutdown_timeout_seconds
            )
        except asyncio.CancelledError:
            self._terminate_stuck_shutdown("was cancelled during shutdown")
            await self._cancel_wrapper_task()
            raise
        except (asyncio.TimeoutError, TimeoutError):
            self._terminate_stuck_shutdown("did not stop before shutdown deadline")
            await self._cancel_wrapper_task()

    def _terminate_stuck_shutdown(self, reason: str) -> None:
        self._state = "unhealthy"
        logger.error(
            "Provider model load %s (provider=%s)", reason, self._provider
        )
        self._terminate(FATAL_TIMEOUT_EXIT_CODE)

    async def _cancel_wrapper_task(self) -> None:
        """Clean up when an injected terminator returns instead of exiting."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task


@asynccontextmanager
async def model_lifespan(app: Any, startup: ModelStartup):
    """Start model loading on mount and enforce bounded cleanup on unmount."""
    del app
    startup.start()
    try:
        yield
    finally:
        await startup.shutdown()
