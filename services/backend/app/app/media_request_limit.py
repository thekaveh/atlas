"""Bound request bodies for the hosted media submission endpoint."""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable, Dict

DEFAULT_MEDIA_REQUEST_MAX_BYTES = 40 * 1024 * 1024


def media_request_max_bytes_from_env() -> int:
    raw = (
        os.getenv("MEDIA_REQUEST_MAX_BYTES")
        or str(DEFAULT_MEDIA_REQUEST_MAX_BYTES)
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("MEDIA_REQUEST_MAX_BYTES must be a positive integer") from exc
    if value <= 0:
        raise ValueError("MEDIA_REQUEST_MAX_BYTES must be a positive integer")
    return value


class MediaRequestLimitMiddleware:
    """Reject oversized ``POST /media/generate`` bodies before route parsing."""

    def __init__(self, app: Callable[..., Awaitable[None]], max_bytes: int | None = None):
        self.app = app
        self.max_bytes = (
            media_request_max_bytes_from_env() if max_bytes is None else max_bytes
        )
        if self.max_bytes <= 0:
            raise ValueError("MEDIA_REQUEST_MAX_BYTES must be a positive integer")

    async def __call__(self, scope: Dict[str, Any], receive, send) -> None:
        if not (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/media/generate"
        ):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError:
                await self._reject(send, 400, "Content-Length must be an integer")
                return
            if declared_size < 0:
                await self._reject(send, 400, "Content-Length must not be negative")
                return
            if declared_size > self.max_bytes:
                await self._too_large(send)
                return

        chunks = []
        total = 0
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                await self._too_large(send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> Dict[str, Any]:
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        await self.app(scope, replay, send)

    async def _too_large(self, send) -> None:
        await self._reject(
            send,
            413,
            "Media request body exceeds MEDIA_REQUEST_MAX_BYTES "
            f"({self.max_bytes} bytes)",
        )

    @staticmethod
    async def _reject(send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
