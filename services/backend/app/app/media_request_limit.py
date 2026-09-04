"""Bound request bodies for the hosted media submission endpoint."""

from __future__ import annotations

import json
import os
from tempfile import SpooledTemporaryFile
from typing import Any, Awaitable, Callable, Dict, Mapping

from starlette.exceptions import HTTPException

DEFAULT_MEDIA_REQUEST_MAX_BYTES = 40 * 1024 * 1024
_MEDIA_REQUEST_SPOOL_MEMORY_BYTES = 1024 * 1024
_MEDIA_REQUEST_REPLAY_CHUNK_BYTES = 64 * 1024


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
    """Authenticate and bound ``POST /media/generate`` before route parsing."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        max_bytes: int | None = None,
        *,
        authenticate: Callable[[Dict[str, Any]], Awaitable[Any]],
    ):
        self.app = app
        self.authenticate = authenticate
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

        try:
            await self.authenticate(scope)
        except HTTPException as exc:
            await self._reject(send, exc.status_code, exc.detail, exc.headers)
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

        spool_memory_bytes = min(
            self.max_bytes,
            _MEDIA_REQUEST_SPOOL_MEMORY_BYTES,
        )
        with SpooledTemporaryFile(
            max_size=spool_memory_bytes,
            mode="w+b",
        ) as body_file:
            total = 0
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    return
                body = message.get("body", b"")
                previous_total = total
                total += len(body)
                if total > self.max_bytes:
                    await self._too_large(send)
                    return
                if previous_total <= spool_memory_bytes < total:
                    body_file.rollover()
                body_file.write(body)
                if not message.get("more_body", False):
                    break

            body_file.seek(0)
            replay_complete = False

            async def replay() -> Dict[str, Any]:
                nonlocal replay_complete
                if replay_complete:
                    return await receive()
                body = body_file.read(_MEDIA_REQUEST_REPLAY_CHUNK_BYTES)
                more_body = body_file.tell() < total
                replay_complete = not more_body
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": more_body,
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
    async def _reject(
        send,
        status: int,
        detail: Any,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        response_headers.extend(
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in (headers or {}).items()
        )
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": response_headers,
            }
        )
        await send({"type": "http.response.body", "body": body})
