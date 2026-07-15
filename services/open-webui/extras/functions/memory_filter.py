"""
title: Memory Auto-Extraction
author: Atlas
author_url: https://github.com/thekaveh/atlas
description: Automatically extracts and stores memories from conversations
required_open_webui_version: 0.4.4
requirements: requests
version: 1.0.0
license: MIT
type: filter
"""

import sys
import queue
import threading
import os
import requests
from pydantic import BaseModel, Field
from typing import Optional


class _BoundedDaemonExecutor:
    """Small fire-and-forget pool with a bounded waiting queue."""

    def __init__(self, max_workers: int = 2, max_queue: int = 4):
        self._jobs: queue.Queue = queue.Queue(maxsize=max_queue)
        self._max_workers = max_workers
        self._started = False
        self._start_lock = threading.Lock()

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._started:
                return
            for index in range(self._max_workers):
                threading.Thread(
                    target=self._run,
                    name=f"atlas-memory-extract-{index}",
                    daemon=True,
                ).start()
            self._started = True

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception as exc:
                print(
                    f"memory_filter: worker failed (error_type={type(exc).__name__})",
                    file=sys.stderr,
                )
            finally:
                self._jobs.task_done()

    def submit(self, job) -> bool:
        self._ensure_started()
        try:
            self._jobs.put_nowait(job)
        except queue.Full:
            return False
        return True


_EXECUTOR = _BoundedDaemonExecutor()


class Filter:
    class Valves(BaseModel):
        backend_url: str = Field(
            default="http://backend:8000", description="Backend API URL"
        )
        enabled: bool = Field(
            default=True, description="Enable automatic memory extraction"
        )
        min_messages: int = Field(
            default=4,
            description="Minimum number of messages before extraction triggers",
        )
        timeout: int = Field(default=120, description="Request timeout in seconds")

    def __init__(self):
        self.valves = self.Valves()

    @staticmethod
    def _backend_headers() -> dict[str, str]:
        token = (os.getenv("BACKEND_OPEN_WEBUI_API_TOKEN") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Pre-request hook: pass through without modification."""
        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Post-response hook: extract memories from conversation asynchronously."""
        if not self.valves.enabled:
            return body

        messages = body.get("messages", [])

        # Only trigger extraction after enough conversation has accumulated
        if len(messages) < self.valves.min_messages:
            return body

        user_id = ""
        if __user__:
            user_id = __user__.get("id", "")
        if not user_id:
            return body  # No valid user ID, skip extraction

        if not _EXECUTOR.submit(lambda: self._extract(user_id, messages)):
            print("memory_filter: extraction queue full; request skipped", file=sys.stderr)

        return body

    def _extract(self, user_id: str, messages: list[dict]) -> None:
        try:
            recent_messages = messages[-self.valves.min_messages :]
            formatted = [
                {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                for msg in recent_messages
                if msg.get("content")
            ]
            if not formatted:
                return
            response = requests.post(
                f"{self.valves.backend_url}/memory/extract",
                headers=self._backend_headers(),
                json={
                    "user_id": user_id,
                    "messages": formatted,
                    "namespace": "default",
                },
                timeout=self.valves.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            print(
                f"memory_filter: extraction failed (error_type={type(exc).__name__})",
                file=sys.stderr,
            )
