"""Bounded rotating tee with shared active-session retention (#1178, #1760).

The pinned first segment and rolling tail share one reserved payload budget.
A lifetime lease protects quiet writers; logging failures never fail launches.
"""

from __future__ import annotations

import contextlib
import os
import datetime
import weakref
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import session_log_retention as _retention
from .session_log_retention import admission, identity, track_descriptor, _close_fd

SEGMENT_MAX_BYTES = 32 * 1024 * 1024
MAX_SEGMENTS = 3
RETAINED_SESSIONS = 5

_LIVE_TEES = weakref.WeakSet()


def _after_fork():
    for tee in list(_LIVE_TEES):
        tee._after_fork()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _utf8_window_end(data: bytes, start: int, capacity: int) -> int:
    """End a bounded window before a partial UTF-8 code point."""
    end = min(len(data), start + capacity)
    while end > start and end < len(data) and data[end] & 0xC0 == 0x80:
        end -= 1
    return end


def retention_summary() -> str:
    return (
        f"{MAX_SEGMENTS} × {SEGMENT_MAX_BYTES // (1024 * 1024)} MiB per session "
        f"(first segment always kept), up to {RETAINED_SESSIONS} managed sessions / "
        "480 MiB reserved; active sessions protected"
    )


@dataclass(frozen=True)
class SessionLogConfig:
    """Size/retention knobs, bundled so callers pass one object."""

    directory: Optional[str] = None
    segment_max_bytes: int = SEGMENT_MAX_BYTES
    max_segments: int = MAX_SEGMENTS
    retained_sessions: int = RETAINED_SESSIONS


class _SessionWriter:
    """Unbuffered UTF-8 descriptor owner; no inherited buffered-I/O locks."""

    def __init__(self, fd, path):
        self._fd = fd
        self.name = str(path)

    @property
    def closed(self):
        return self._fd is None

    def fileno(self):
        if self.closed:
            raise ValueError("I/O operation on closed session log")
        return self._fd

    def write(self, text):
        fd = self.fileno()
        data = text.encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(fd, memoryview(data)[offset:])
            if written <= 0:
                raise OSError("session-log write made no progress")
            offset += written
        return len(text)

    def flush(self):
        self.fileno()  # Raw writes have no userspace buffer to flush.

    def close(self):
        with _retention._FORK_LOCK:
            if not self.closed:
                _close_fd(self._fd)
                self._fd = None

    def abandon(self):
        # The retention fork hook already closed the raw descriptor. Clearing
        # this owner prevents later close/finalization from touching a reused fd.
        self._fd = None


class SessionLogTee:
    def __init__(
        self,
        *,
        prefix: str,
        config: Optional[SessionLogConfig] = None,
        on_degraded: Optional[Callable[[str], None]] = None,
    ):
        config = config or SessionLogConfig()
        self._segment_max_bytes = config.segment_max_bytes
        self._max_segments = config.max_segments
        self._on_degraded = on_degraded
        self._degraded = False
        self._closed = False
        self._lock = threading.RLock()
        self._segment_index = 0
        self._rolling: list[Path] = []
        self._bytes = 0
        self._identities = {}
        self._reservation = None

        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        header = f"# atlas session log — started {stamp}\n"
        if self._max_segments < 2 or len(header.encode("utf-8")) > self._segment_max_bytes:
            raise OSError("session-log budget cannot retain the initial header and rolling files")
        _LIVE_TEES.add(self)  # Include constructors suspended during admission.
        with admission(prefix, config) as reservation:
            self._reservation = reservation
            self.base_path = reservation.base_path
            self._fh = self._open_segment(self.base_path)
            try:
                self._write_raw(header)
                self._check_paths()
            except BaseException:
                self.close()
                raise

    def _open_segment(self, path):
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with _retention._FORK_LOCK:
            fd = os.open(path, flags, 0o600)
            try:
                os.set_inheritable(fd, False)
                track_descriptor(fd)
                self._identities[path] = identity(path)
                self._current_path = path
                return _SessionWriter(fd, path)
            except BaseException:
                _close_fd(fd)
                raise

    def _check_paths(self):
        self._reservation.check()
        for path, expected in self._identities.items():
            if identity(path) != expected:
                raise OSError(f"session-log path changed: {path.name}")
        if not self._fh.closed:
            current = os.fstat(self._fh.fileno())
            expected = self._identities[self._current_path]
            if (current.st_dev, current.st_ino) != expected:
                raise OSError("session-log writer changed")

    def _after_fork(self):
        self._lock = threading.RLock()
        self._closed = True
        self._degraded = True
        fh = getattr(self, "_fh", None)
        if fh is not None:
            fh.abandon()
        if self._reservation is not None:
            self._reservation.after_fork()

    # -- writing -----------------------------------------------------------
    def _write_raw(self, text: str) -> None:
        self._fh.write(text)
        self._fh.flush()
        self._bytes += len(text.encode("utf-8", errors="replace"))

    def write(self, text: str) -> None:
        failure = None
        with self._lock:
            if self._degraded:
                return
            if self._closed:
                raise ValueError("I/O operation on closed session log")
            if not text:
                return
            try:
                self._check_paths()
                self._write_bounded(text.encode("utf-8"))
                self._check_paths()
            except OSError as exc:
                self._enter_degraded()
                failure = exc
        if failure is not None:
            # External callbacks may reenter or wait for another thread.
            self._notify_degraded(failure)

    def _write_bounded(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            end = _utf8_window_end(data, offset, self._segment_max_bytes - self._bytes)
            if end == offset:
                self._rotate()
                continue
            self._check_paths()
            self._write_raw(data[offset:end].decode("utf-8"))
            self._check_paths()
            offset = end

    def flush(self) -> None:
        failure = None
        with self._lock:
            if self._degraded or self._closed:
                return
            try:
                self._check_paths()
                self._fh.flush()
                self._check_paths()
            except OSError as exc:
                self._enter_degraded()
                failure = exc
        if failure is not None:
            self._notify_degraded(failure)

    # -- rotation ----------------------------------------------------------
    def _rotation_header(self, index: int) -> str:
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        return (
            f"# atlas session log — rotated segment {index} at {stamp}\n"
            f"# truncation point: earlier output beyond the retained rolling "
            f"segments was deleted; segment 0 (session start + first "
            f"diagnostics) is always kept at {self.base_path.name}\n"
        )

    def _rotate(self) -> None:
        index = self._segment_index + 1
        header = self._rotation_header(index)
        if len(header.encode("utf-8")) + 4 > self._segment_max_bytes:
            raise OSError("session-log budget cannot retain a complete rolling marker and UTF-8 text")
        self._check_paths()
        self._fh.close()
        # Retire a full rolling slot before creating another file. Failed
        # deletion keeps its bookkeeping and reaches write's degraded path.
        while len(self._rolling) >= self._max_segments - 1:
            retired = self._rolling[0]
            retired.unlink()
            self._rolling.pop(0)
            del self._identities[retired]
        self._segment_index = index
        segment_path = self.base_path.with_name(
            f"{self.base_path.name}.{self._segment_index}"
        )
        self._fh = self._open_segment(segment_path)
        self._rolling.append(segment_path)
        self._bytes = 0
        self._write_raw(header)
        self._check_paths()

    # -- degradation -------------------------------------------------------
    def _enter_degraded(self) -> None:
        self._degraded = True
        self.close()

    def _notify_degraded(self, exc: OSError) -> None:
        if self._on_degraded is not None:
            with contextlib.suppress(Exception):
                self._on_degraded(
                    "session-file logging degraded and stopped "
                    f"({exc.__class__.__name__}: {exc}) — the launch itself "
                    "is unaffected; on-screen logs continue"
                )

    @property
    def degraded(self) -> bool:
        with self._lock:
            return self._degraded

    def close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self._fh.close()
            except Exception:
                # A usable writer must keep its lifetime ownership.
                if not self._fh.closed:
                    return
            with contextlib.suppress(OSError):
                self._reservation.close()

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()
