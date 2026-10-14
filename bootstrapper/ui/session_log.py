"""Bounded, rotating session-log tee for the launch TUI (#1178).

The wizard tees every log line to a ``delete=False`` file under the system
temp directory so a failed launch can be diagnosed after the TUI is gone.
Before this module the tee had no size bound and old sessions accumulated
until the OS rotated ``/tmp``. This bounds both dimensions while keeping
the failure-diagnosis contract:

- **Per-session budget**: an active segment caps at ``SEGMENT_MAX_BYTES``;
  on overflow the tee rotates to a numbered rolling segment. Segment 0 —
  the session header, wizard configuration, and the earliest diagnostics —
  is **always retained** (first-failure context), and at most
  ``MAX_SEGMENTS - 1`` rolling segments are kept, so a session can never
  exceed ``MAX_SEGMENTS × SEGMENT_MAX_BYTES`` (96 MiB, inside the 100 MB
  acceptance budget). Every rolling segment opens with an ISO-timestamped
  truncation marker naming the segment number and what was dropped.
- **Retained sessions**: opening a new session prunes the oldest
  ``atlas-launch-*`` session files beyond ``RETAINED_SESSIONS``. Only this
  module's own naming pattern is ever deleted — user-exported reports and
  support bundles live under different names and are never touched.
- **Disk exhaustion**: a failed write flips the tee into degraded mode —
  one explicit notice through ``on_degraded``, every later write a no-op,
  and nothing ever raises into the launch pipeline, so logging failure can
  never masquerade as launch failure (or success).

File-like surface (``write``/``flush``/``close``) so existing call sites —
including the post-failure ``docker compose logs`` capture — use it as a
drop-in for the raw file handle.
"""

from __future__ import annotations

import contextlib
import os
import datetime
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SEGMENT_MAX_BYTES = 32 * 1024 * 1024
MAX_SEGMENTS = 3
RETAINED_SESSIONS = 5

#: Everything this module may ever delete matches this shape and nothing else.
_SESSION_FILE_RE = re.compile(r"^atlas-launch-.+\.log(\.\d+)?$")


def _utf8_window_end(data: bytes, start: int, capacity: int) -> int:
    """End a bounded window before a partial UTF-8 code point."""
    end = min(len(data), start + capacity)
    while end > start and end < len(data) and data[end] & 0xC0 == 0x80:
        end -= 1
    return end


def retention_summary() -> str:
    return (
        f"{MAX_SEGMENTS} × {SEGMENT_MAX_BYTES // (1024 * 1024)} MiB per session "
        f"(first segment always kept), {RETAINED_SESSIONS} sessions retained"
    )


@dataclass(frozen=True)
class SessionLogConfig:
    """Size/retention knobs, bundled so callers pass one object."""

    directory: Optional[str] = None
    segment_max_bytes: int = SEGMENT_MAX_BYTES
    max_segments: int = MAX_SEGMENTS
    retained_sessions: int = RETAINED_SESSIONS


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

        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        header = f"# atlas session log — started {stamp}\n"
        if self._max_segments < 2 or len(header.encode("utf-8")) > self._segment_max_bytes:
            raise OSError("session-log budget cannot retain the initial header and rolling files")
        directory = config.directory or tempfile.gettempdir()
        self._prune_old_sessions(Path(directory), config.retained_sessions)

        fh = tempfile.NamedTemporaryFile(
            mode="w",
            buffering=1,
            encoding="utf-8",
            newline="",  # LF stays literal, so UTF-8 accounting matches disk bytes.
            prefix=prefix,
            suffix=".log",
            dir=directory,
            delete=False,
        )
        self._fh = fh
        self.base_path = Path(fh.name)
        try:
            self._write_raw(header)
        except OSError:
            self.close()
            raise

    # -- retention across sessions -----------------------------------------
    @staticmethod
    def _prune_old_sessions(directory: Path, retained_sessions: int) -> None:
        """Keep the newest ``retained_sessions - 1`` prior sessions (this
        one becomes the Nth). Best-effort; only our own file shape."""
        try:
            candidates = [
                p
                for p in directory.iterdir()
                if p.is_file() and _SESSION_FILE_RE.match(p.name)
            ]
        except OSError:
            return
        sessions: dict[str, list[Path]] = {}
        for path in candidates:
            stem = path.name.split(".log")[0]
            sessions.setdefault(stem, []).append(path)

        def newest_mtime(paths: list[Path]) -> float:
            times = []
            for p in paths:
                with contextlib.suppress(OSError):
                    times.append(p.stat().st_mtime)
            return max(times, default=0.0)

        ordered = sorted(sessions.values(), key=newest_mtime, reverse=True)
        for group in ordered[max(retained_sessions - 1, 0):]:
            for path in group:
                with contextlib.suppress(OSError):
                    path.unlink()

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
                self._write_bounded(text.encode("utf-8"))
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
            self._write_raw(data[offset:end].decode("utf-8"))
            offset = end

    def flush(self) -> None:
        with self._lock:
            if self._degraded:
                return
            with contextlib.suppress(OSError):
                self._fh.flush()

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
        self._fh.close()
        # Retire a full rolling slot before creating another file. Failed
        # deletion keeps its bookkeeping and reaches write's degraded path.
        while len(self._rolling) >= self._max_segments - 1:
            self._rolling[0].unlink(missing_ok=True)
            self._rolling.pop(0)
        self._segment_index = index
        segment_path = self.base_path.with_name(
            f"{self.base_path.name}.{self._segment_index}"
        )
        # Exclusive, no-follow, owner-only from the first byte: the segment
        # name is predictable, and on a shared /tmp another user could have
        # pre-created it (or a symlink) to capture compose logs.
        # A file we cannot remove makes O_EXCL fail; write() then degrades.
        with contextlib.suppress(OSError):
            segment_path.unlink()
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)  # no CRT \r\r\n
        )
        self._fh = os.fdopen(  # noqa: SIM115 - lifetime managed by close()
            os.open(segment_path, flags, 0o600), "w", buffering=1, encoding="utf-8", newline=""
        )
        self._rolling.append(segment_path)
        self._bytes = 0
        self._write_raw(header)

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
            with contextlib.suppress(Exception):
                self._fh.close()
