"""Chunked, size-bounded UploadFile spooling for provider APIs."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from starlette.responses import JSONResponse


_CHUNK_BYTES = 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 1024 * 1024


class UploadTooLargeError(ValueError):
    pass


class EmptyUploadError(ValueError):
    pass


class _RequestBodyTooLarge(Exception):
    pass


def multipart_body_limit(max_upload_bytes: int) -> int:
    if max_upload_bytes <= 0:
        raise ValueError("max_upload_bytes must be positive")
    return max_upload_bytes + MULTIPART_OVERHEAD_BYTES


class RequestBodyLimitMiddleware:
    """Reject oversized request bodies before multipart file spooling."""

    def __init__(self, app, *, max_body_bytes: int, paths: Iterable[str]) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.paths = frozenset(paths)

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method", "").upper() != "POST"
            or scope.get("path") not in self.paths
        ):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                declared_length = int(value)
            except ValueError:
                break
            if declared_length > self.max_body_bytes:
                await self._reject(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send) -> None:
        response = JSONResponse(
            {"detail": "Upload is too large"},
            status_code=413,
        )
        await response(scope, receive, send)


async def spool_upload(
    upload: Any,
    *,
    max_bytes: int,
    suffix: str,
    directory: Path | None = None,
) -> Path:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    fd, raw_path = tempfile.mkstemp(
        suffix=suffix,
        dir=str(directory) if directory is not None else None,
    )
    path = Path(raw_path)
    total = 0
    try:
        with os.fdopen(fd, "wb") as stream:
            while True:
                chunk = await upload.read(_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLargeError(
                        f"upload exceeds {max_bytes} byte limit"
                    )
                stream.write(chunk)
        if total == 0:
            raise EmptyUploadError("upload is empty")
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
