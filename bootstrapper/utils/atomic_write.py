"""Crash-safe, mode-preserving writes for secrets-bearing text files."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def atomic_write_text(
    destination: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Replace destination only after a complete, flushed temporary write."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    target_mode = (
        mode
        if mode is not None
        else stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    )
    fd, raw_temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(raw_temporary)
    try:
        os.fchmod(fd, target_mode)
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            fd = -1
            written = handle.write(content)
            if written != len(content):
                raise OSError(
                    f"short atomic write for {path}: {written}/{len(content)}"
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)
