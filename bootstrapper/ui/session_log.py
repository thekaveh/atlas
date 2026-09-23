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
import datetime
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SEGMENT_MAX_BYTES = 32 * 1024 * 1024
MAX_SEGMENTS = 3
RETAINED_SESSIONS = 5

#: Everything this module may ever delete matches this shape and nothing else.
_SESSION_FILE_RE = re.compile(r"^atlas-launch-.+\.log(\.\d+)?$")


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
        self._segment_index = 0
        self._rolling: list[Path] = []
        self._bytes = 0

        directory = config.directory or tempfile.gettempdir()
        self._prune_old_sessions(Path(directory), config.retained_sessions)

        fh = tempfile.NamedTemporaryFile(
            mode="w",
            buffering=1,
            encoding="utf-8",
            prefix=prefix,
            suffix=".log",
            dir=directory,
            delete=False,
        )
        self._fh = fh
        self.base_path = Path(fh.name)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        self._write_raw(f"# atlas session log — started {stamp}\n")

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
        if self._degraded:
            return
        try:
            if self._bytes >= self._segment_max_bytes:
                self._rotate()
            self._write_raw(text)
        except OSError as exc:
            self._enter_degraded(exc)

    def flush(self) -> None:
        if self._degraded:
            return
        with contextlib.suppress(OSError):
            self._fh.flush()

    # -- rotation ----------------------------------------------------------
    def _rotate(self) -> None:
        self._fh.close()
        self._segment_index += 1
        segment_path = self.base_path.with_name(
            f"{self.base_path.name}.{self._segment_index}"
        )
        self._fh = open(  # noqa: SIM115 - lifetime managed by close()
            segment_path, "w", buffering=1, encoding="utf-8"
        )
        with contextlib.suppress(OSError):
            segment_path.chmod(0o600)
        self._rolling.append(segment_path)
        while len(self._rolling) > self._max_segments - 1:
            oldest = self._rolling.pop(0)
            with contextlib.suppress(OSError):
                oldest.unlink()
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        self._bytes = 0
        self._write_raw(
            f"# atlas session log — rotated segment {self._segment_index} at {stamp}\n"
            f"# truncation point: earlier output beyond the retained rolling "
            f"segments was deleted; segment 0 (session start + first "
            f"diagnostics) is always kept at {self.base_path.name}\n"
        )

    # -- degradation -------------------------------------------------------
    def _enter_degraded(self, exc: OSError) -> None:
        self._degraded = True
        with contextlib.suppress(Exception):
            self._fh.close()
        if self._on_degraded is not None:
            with contextlib.suppress(Exception):
                self._on_degraded(
                    "session-file logging degraded and stopped "
                    f"({exc.__class__.__name__}: {exc}) — the launch itself "
                    "is unaffected; on-screen logs continue"
                )

    @property
    def degraded(self) -> bool:
        return self._degraded

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._fh.close()
