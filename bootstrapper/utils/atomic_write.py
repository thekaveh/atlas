"""Crash-safe, mode-preserving writes for secrets-bearing text files."""

from __future__ import annotations

import os
import errno
import stat
import tempfile
from datetime import datetime
from pathlib import Path


def _fsync_parent_directory(path: Path) -> None:
    """Persist a replaced directory entry on platforms that support it."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    unsupported = {
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    try:
        directory_fd = os.open(path.parent, flags)
    except OSError as exc:
        if exc.errno in unsupported:
            return
        raise
    try:
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            if exc.errno not in unsupported:
                raise
    finally:
        os.close(directory_fd)


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
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(fd, target_mode)
        else:
            os.chmod(temporary, target_mode)
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
        _fsync_parent_directory(path)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def create_private_backup(
    source: str | Path,
    *,
    version: str | None = None,
) -> Path:
    """Create a durable, collision-resistant 0600 snapshot of *source*."""
    source_path = Path(source)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    version_segment = f".{version}" if version else ""
    fd, raw_backup = tempfile.mkstemp(
        dir=source_path.parent,
        prefix=f"{source_path.name}.backup{version_segment}.{timestamp}.",
    )
    backup = Path(raw_backup)
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(fd, 0o600)
        else:
            os.chmod(backup, 0o600)
        content = source_path.read_bytes()
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            written = handle.write(content)
            if written != len(content):
                raise OSError(
                    f"short backup write for {source_path}: "
                    f"{written}/{len(content)}"
                )
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent_directory(backup)
        return backup
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    finally:
        if fd >= 0:
            os.close(fd)
