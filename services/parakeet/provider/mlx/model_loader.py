"""Single-flight, off-event-loop model initialization."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class AsyncSingleFlightModel:
    def __init__(self, loader: Callable[[], Any]):
        self._loader = loader
        self._task: asyncio.Task | None = None
        self._model: Any = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def done(self) -> bool:
        return self._task is not None and self._task.done()

    def start(self) -> asyncio.Task:
        if self._task is None:
            self._task = asyncio.create_task(asyncio.to_thread(self._loader))
        return self._task

    async def get(self) -> Any:
        if self._model is None:
            task = self.start()
            try:
                self._model = await task
            except Exception:
                if self._task is task:
                    self._task = None
                raise
        return self._model
