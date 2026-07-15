"""Chunked, size-bounded UploadFile spooling for provider APIs."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


_CHUNK_BYTES = 1024 * 1024


class UploadTooLargeError(ValueError):
    pass


class EmptyUploadError(ValueError):
    pass


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
