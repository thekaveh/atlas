"""Session-log rotation, retention, and degradation contract (#1178)."""

from __future__ import annotations

import errno
import re
import time
from pathlib import Path

from ui.session_log import (
    MAX_SEGMENTS,
    RETAINED_SESSIONS,
    SEGMENT_MAX_BYTES,
    SessionLogConfig,
    SessionLogTee,
    retention_summary,
)


def test_default_budget_stays_inside_the_acceptance_envelope():
    # The AC names a 100 MB session budget for a 1 GB stream; the shipped
    # configuration is 3 × 32 MiB = 96 MiB. The synthetic-stream test below
    # exercises the identical code path at a proportional scale.
    assert MAX_SEGMENTS * SEGMENT_MAX_BYTES <= 100 * 1024 * 1024
    assert RETAINED_SESSIONS >= 3
    assert "32 MiB" in retention_summary()


def _session_files(directory: Path, base: Path) -> list[Path]:
    return sorted(
        p for p in directory.iterdir() if p.name.startswith(base.name)
    )


def test_a_huge_stream_stays_within_the_session_budget(tmp_path):
    segment_max = 4096
    tee = SessionLogTee(
        prefix="atlas-launch-20260101T000000-",
        config=SessionLogConfig(
            directory=str(tmp_path),
            segment_max_bytes=segment_max,
            max_segments=3,
            retained_sessions=5,
        ),
    )
    line = "x" * 100 + "\n"
    total_stream = segment_max * 40  # ~40x the per-segment budget
    written = 0
    while written < total_stream:
        tee.write(line)
        written += len(line)
    tee.close()

    files = _session_files(tmp_path, tee.base_path)
    # Pinned segment 0 plus at most (max_segments - 1) rolling segments.
    assert 1 <= len(files) <= 3
    assert tee.base_path in files, "segment 0 (first-failure context) must survive"
    on_disk = sum(p.stat().st_size for p in files)
    # Generous slack for headers; the point is the 40x stream collapsed to
    # the configured session envelope instead of growing unboundedly.
    assert on_disk <= 3 * (segment_max + 1024)

    # Segment 0 keeps the ORIGINAL session header (earliest context).
    assert "session log — started" in tee.base_path.read_text().splitlines()[0]


def _rotation_marker(path: Path, base: Path) -> tuple[int, str]:
    """Parse and structurally assert one rolling segment's marker header."""
    head = path.read_text().splitlines()[:2]
    match = re.search(r"rotated segment (\d+) at (\S+)", head[0])
    assert match, f"missing rotation marker in {path.name}: {head[0]!r}"
    assert "truncation point" in head[1]
    assert base.name in head[1]  # names where the earlier context lives
    return int(match.group(1)), match.group(2)


def test_rotation_writes_ordered_timestamps_and_truncation_markers(tmp_path):
    tee = SessionLogTee(
        prefix="atlas-launch-20260101T000000-",
        config=SessionLogConfig(
            directory=str(tmp_path),
            segment_max_bytes=512,
            max_segments=3,
            retained_sessions=5,
        ),
    )
    for _ in range(200):
        tee.write("payload line\n")
    tee.close()

    rolling = [
        p for p in _session_files(tmp_path, tee.base_path) if p != tee.base_path
    ]
    assert rolling, "the stream above must have rotated"
    markers = [_rotation_marker(path, tee.base_path) for path in rolling]
    indexes = [index for index, _stamp in markers]
    stamps = [stamp for _index, stamp in markers]
    assert indexes == sorted(indexes)
    assert stamps == sorted(stamps)  # ISO timestamps order lexicographically


def test_disk_exhaustion_degrades_loudly_without_raising(tmp_path):
    notices: list[str] = []
    tee = SessionLogTee(
        prefix="atlas-launch-20260101T000000-",
        config=SessionLogConfig(directory=str(tmp_path), segment_max_bytes=4096),
        on_degraded=notices.append,
    )

    class _FullDisk:
        def write(self, _text):
            raise OSError(errno.ENOSPC, "No space left on device")

        def flush(self):
            raise OSError(errno.ENOSPC, "No space left on device")

        def close(self):
            pass

    tee._fh = _FullDisk()
    tee.write("this write hits a full disk\n")
    assert tee.degraded
    assert len(notices) == 1
    assert "degraded" in notices[0]
    assert "launch itself is unaffected" in notices[0]
    # Every later write is a silent no-op — never an exception into the
    # launch pipeline, and never a second notice.
    tee.write("later write\n")
    tee.flush()
    assert len(notices) == 1
    tee.close()


def test_old_sessions_are_pruned_but_foreign_files_never_touched(tmp_path):
    for index in range(4):
        base = tmp_path / f"atlas-launch-2026010{index}T000000-old{index}.log"
        base.write_text("old session\n")
        (tmp_path / (base.name + ".1")).write_text("old rolling\n")
        stamp = time.time() - (400 - index)
        import os

        os.utime(base, (stamp, stamp))
        os.utime(tmp_path / (base.name + ".1"), (stamp, stamp))
    keeper = tmp_path / "support-bundle-notes.log"
    keeper.write_text("user-exported evidence — never delete\n")
    exported = tmp_path / "atlas-launch-export.txt"
    exported.write_text("user export with a different shape\n")

    tee = SessionLogTee(
        prefix="atlas-launch-20260201T000000-",
        config=SessionLogConfig(directory=str(tmp_path), retained_sessions=3),
    )
    tee.close()

    remaining_sessions = {
        p.name.split(".log")[0]
        for p in tmp_path.iterdir()
        if p.name.startswith("atlas-launch-2026010")
    }
    # newest two old sessions survive (this one is the third of three)
    assert len(remaining_sessions) == 2
    assert keeper.exists() and exported.exists(), "foreign files are untouchable"


def test_wizard_screen_uses_the_bounded_tee(monkeypatch, tmp_path):
    import tempfile as _tempfile

    monkeypatch.setattr(_tempfile, "gettempdir", lambda: str(tmp_path))
    from ui.session_log import SessionLogTee as Tee
    from ui.textual.screens import wizard_screen

    screen = wizard_screen.WizardScreen(steps=[], services=[])
    try:
        assert isinstance(screen._launch_log_fh, Tee)
        assert screen._launch_log_path == screen._launch_log_fh.base_path
        screen._tee_to_log("hello", source="pipeline", level="info")
        assert "hello" in screen._launch_log_path.read_text()
    finally:
        screen._close_launch_log_tee()
        for p in tmp_path.glob("atlas-launch-*"):
            p.unlink(missing_ok=True)
