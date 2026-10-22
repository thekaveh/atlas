"""Session-log rotation, retention, and degradation contract (#1178)."""

from __future__ import annotations

import errno
import re
import threading
import time
from pathlib import Path

import pytest

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
        p for p in base.parent.iterdir()
        if p.name == base.name or re.fullmatch(re.escape(base.name) + r"\.\d+", p.name)
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
    assert all(p.stat().st_size <= segment_max for p in files)
    assert on_disk <= 3 * segment_max, "headers also consume the byte budget"

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
    markers = sorted(_rotation_marker(path, tee.base_path) for path in rolling)
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

    original = tee._fh
    _FullDisk.fileno = lambda self: original.fileno()
    _FullDisk.closed = property(lambda self: original.closed)
    _FullDisk.close = lambda self: original.close()
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
    # Legacy unleased root files are outside the cooperating store.
    assert len(remaining_sessions) == 4
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



def test_rotated_segments_never_follow_a_planted_symlink(tmp_path):
    # The segment name is predictable; on a shared /tmp another user could
    # pre-create it as a symlink (or a world-readable file) to capture logs.
    tee = SessionLogTee(
        prefix="atlas-launch-20260101T000000-",
        config=SessionLogConfig(
            directory=str(tmp_path), segment_max_bytes=512, max_segments=3, retained_sessions=5,
        ),
    )
    trap = tmp_path / "captured.txt"
    trap.write_text("", encoding="utf-8")
    planted = tee.base_path.with_name(f"{tee.base_path.name}.1")
    planted.symlink_to(trap)
    for _ in range(50):
        tee.write("payload line\n")
    tee.close()
    assert trap.read_text(encoding="utf-8") == ""
    assert planted.is_symlink() and tee.degraded
    # Refuse pre-existing paths rather than unlinking unknown ownership.


@pytest.fixture
def bounded_tee(tmp_path):
    tee = SessionLogTee(
        prefix="atlas-launch-owned-",
        config=SessionLogConfig(directory=str(tmp_path), segment_max_bytes=512),
    )
    try:
        yield tee
    finally:
        tee.close()


def _assert_raw_session_budget(directory, tee):
    files = _session_files(directory, tee.base_path)
    sizes = [p.stat().st_size for p in files]
    assert tee.base_path in files
    assert len(files) <= tee._max_segments
    assert all(size <= tee._segment_max_bytes for size in sizes)
    assert sum(sizes) <= tee._max_segments * tee._segment_max_bytes


def _session_payloads(directory, base):
    files = sorted(
        _session_files(directory, base),
        key=lambda p: 0 if p == base else int(p.name.rsplit(".", 1)[1]),
    )
    payloads = []
    for path in files:
        if path != base:
            _rotation_marker(path, base)
        header_lines = 1 if path == base else 2
        payloads.append(path.read_bytes().split(b"\n", header_lines)[-1].decode("utf-8"))
    return payloads


@pytest.mark.parametrize("chunks", [
    ["small line\n"] * 60,
    ["x" * 600 + "\r\ntrailing fragment"],
])
def test_crossing_writes_preserve_text_inside_the_raw_budget(tmp_path, bounded_tee, chunks):
    for chunk in chunks:
        bounded_tee.write(chunk)

    _assert_raw_session_budget(tmp_path, bounded_tee)
    assert "".join(_session_payloads(tmp_path, bounded_tee.base_path)) == "".join(chunks)
    assert not bounded_tee.degraded


def test_one_long_write_keeps_the_pinned_prefix_and_latest_rolling_tail(tmp_path, bounded_tee):
    payload = "".join(f"{i:05d} owned line\n" for i in range(200))
    bounded_tee.write(payload)

    _assert_raw_session_budget(tmp_path, bounded_tee)
    parts = _session_payloads(tmp_path, bounded_tee.base_path)
    assert len(parts) == 3
    assert payload.startswith(parts[0]) and parts[0]
    assert payload.endswith("".join(parts[1:])) and parts[-1]
    assert len("".join(parts)) < len(payload)


@pytest.mark.parametrize("spare", [1, 2, 3])
@pytest.mark.parametrize("character", ["界", "😀"])
def test_unicode_codepoints_cross_segments_without_loss(tmp_path, bounded_tee, spare, character):
    prefix = "x" * (512 - bounded_tee.base_path.stat().st_size - spare)
    payload = prefix + character + "\n尾\r\nlast"
    bounded_tee.write(prefix)
    bounded_tee.write(character + "\n尾\r\nlast")

    _assert_raw_session_budget(tmp_path, bounded_tee)
    assert "".join(_session_payloads(tmp_path, bounded_tee.base_path)) == payload
    assert not bounded_tee.degraded


@pytest.mark.parametrize("fill", [False, True])
def test_empty_write_never_rotates_or_changes_content(tmp_path, bounded_tee, fill):
    if fill:
        bounded_tee.write("x" * (512 - bounded_tee.base_path.stat().st_size))
    before = {p.name: p.read_bytes() for p in _session_files(tmp_path, bounded_tee.base_path)}

    bounded_tee.write("")

    assert {p.name: p.read_bytes() for p in _session_files(tmp_path, bounded_tee.base_path)} == before


@pytest.mark.parametrize("limits", [(0, 3), (-1, 3), (1, 3), (512, 0), (512, 1)])
def test_impossible_initial_budget_fails_before_creating_files(tmp_path, limits):
    segment_bytes, segments = limits
    with pytest.raises(OSError):
        SessionLogTee(
            prefix="atlas-launch-owned-",
            config=SessionLogConfig(
                directory=str(tmp_path), segment_max_bytes=segment_bytes, max_segments=segments,
            ),
        )
    assert list(tmp_path.iterdir()) == []


def test_impossible_rolling_marker_degrades_once_without_creating_a_segment(tmp_path):
    notices = []
    tee = SessionLogTee(
        prefix="atlas-launch-owned-",
        config=SessionLogConfig(directory=str(tmp_path), segment_max_bytes=128),
        on_degraded=notices.append,
    )
    try:
        tee.write("x" * (128 - tee.base_path.stat().st_size))
        before = tee.base_path.read_bytes()
        tee.write("😀")
        tee.write("later ignored")
        assert tee.degraded and len(notices) == 1
        assert _session_files(tmp_path, tee.base_path) == [tee.base_path]
        assert tee.base_path.read_bytes() == before
    finally:
        tee.close()


def _fill_and_roll(tee):
    tee.write("x" * (tee._segment_max_bytes - tee._bytes))
    tee.write("next")


def test_failed_oldest_eviction_stops_before_an_extra_segment(tmp_path, monkeypatch):
    notices = []
    tee = SessionLogTee(
        prefix="atlas-launch-owned-",
        config=SessionLogConfig(directory=str(tmp_path), segment_max_bytes=512),
        on_degraded=notices.append,
    )
    try:
        _fill_and_roll(tee)
        _fill_and_roll(tee)
        oldest = tee._rolling[0]
        next_path = tee.base_path.with_name(tee.base_path.name + ".3")
        original_unlink = Path.unlink

        def fail_oldest(path, *args, **kwargs):
            if path == oldest:
                raise PermissionError(errno.EACCES, "owned eviction failure")
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_oldest)
        _fill_and_roll(tee)
        tee.write("later ignored")
        assert tee.degraded and len(notices) == 1
        assert oldest in tee._rolling and oldest.exists()
        assert not next_path.exists()
        _assert_raw_session_budget(tmp_path, tee)
    finally:
        tee.close()


@pytest.mark.parametrize("missing", [False, True])
def test_two_segment_eviction_is_bounded_and_handles_already_missing_files(
    tmp_path, monkeypatch, missing,
):
    tee = SessionLogTee(
        prefix="atlas-launch-owned-",
        config=SessionLogConfig(directory=str(tmp_path), segment_max_bytes=512, max_segments=2),
    )
    try:
        _fill_and_roll(tee)
        oldest = tee._rolling[0]
        next_path = tee.base_path.with_name(tee.base_path.name + ".2")
        if missing:
            oldest.unlink()
        original_unlink = Path.unlink
        observed = []

        def observe_eviction(path, *args, **kwargs):
            if path == oldest:
                observed.append(next_path.exists())
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", observe_eviction)
        _fill_and_roll(tee)
        if missing:
            assert tee.degraded and not next_path.exists()
            assert observed == []
        else:
            assert observed == [False], "eviction must precede creating another retained file"
            assert not tee.degraded and not oldest.exists() and next_path.exists()
            _assert_raw_session_budget(tmp_path, tee)
    finally:
        tee.close()


def test_binary_writer_keeps_literal_newlines_in_raw_utf8_budget(tmp_path, monkeypatch):
    """Raw descriptor writes preserve LF on every CRT; native Windows is separate."""
    from ui import session_log

    original_open = session_log.os.open
    flags_seen = []

    def observe_open(path, flags, *args):
        flags_seen.append(flags)
        return original_open(path, flags, *args)

    monkeypatch.setattr(session_log.os, "open", observe_open)
    tee = SessionLogTee(
        prefix="atlas-launch-owned-",
        config=SessionLogConfig(directory=str(tmp_path), segment_max_bytes=512),
    )
    payload = "α\nβ\r\n" * 70
    try:
        tee.write(payload)
        _assert_raw_session_budget(tmp_path, tee)
        assert "".join(_session_payloads(tmp_path, tee.base_path)) == payload
        binary_flag = getattr(session_log.os, "O_BINARY", 0)
        assert all(flags & binary_flag == binary_flag for flags in flags_seen)
    finally:
        tee.close()


def test_failure_after_one_chunk_keeps_its_prefix_and_degrades_once(tmp_path, monkeypatch):
    notices = []
    tee = SessionLogTee(
        prefix="atlas-launch-owned-",
        config=SessionLogConfig(directory=str(tmp_path), segment_max_bytes=512),
        on_degraded=notices.append,
    )
    actual_write = tee._write_raw
    calls = []

    def fail_second_write(text):
        calls.append(text)
        if len(calls) > 1:
            raise OSError(errno.ENOSPC, "owned later write failure")
        actual_write(text)

    monkeypatch.setattr(tee, "_write_raw", fail_second_write)
    try:
        tee.write("x" * 1024)
        tee.write("later ignored")
        assert tee.degraded and len(notices) == 1
        assert tee._fh.closed
        assert tee.base_path.read_bytes().split(b"\n", 1)[1] == calls[0].encode("utf-8")
        _assert_raw_session_budget(tmp_path, tee)
    finally:
        tee.close()


def test_rolling_open_failure_is_contained_and_keeps_pinned_content(tmp_path, monkeypatch):
    from ui import session_log

    notices = []
    tee = SessionLogTee(
        prefix="atlas-launch-owned-",
        config=SessionLogConfig(directory=str(tmp_path), segment_max_bytes=512),
        on_degraded=notices.append,
    )
    tee.write("x" * (512 - tee._bytes))
    before = tee.base_path.read_bytes()
    next_path = tee.base_path.with_name(tee.base_path.name + ".1")
    original_open = session_log.os.open

    def fail_next_open(path, *args, **kwargs):
        if Path(path) == next_path:
            raise PermissionError(errno.EACCES, "owned rolling open failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(session_log.os, "open", fail_next_open)
    try:
        tee.write("trigger")
        tee.write("later ignored")
        assert tee.degraded and len(notices) == 1
        assert tee.base_path.read_bytes() == before
        _assert_raw_session_budget(tmp_path, tee)
    finally:
        tee.close()


def test_initial_header_write_failure_closes_the_opened_handle(tmp_path, monkeypatch):
    from ui import session_log

    opened = []

    def fail_write(_text):
        raise OSError(errno.ENOSPC, "owned header write failure")

    actual_open = SessionLogTee._open_segment

    def failing_open(self, path):
        fh = actual_open(self, path)
        opened.append(fh)
        monkeypatch.setattr(fh, "write", fail_write)
        return fh

    monkeypatch.setattr(SessionLogTee, "_open_segment", failing_open)
    try:
        with pytest.raises(OSError):
            SessionLogTee(
                prefix="atlas-launch-owned-", config=SessionLogConfig(directory=str(tmp_path)),
            )
        assert opened and all(fh.closed for fh in opened)
    finally:
        for fh in opened:
            fh.close()


def test_invalid_unicode_keeps_strict_encoding_and_does_not_degrade(tmp_path, bounded_tee):
    before = bounded_tee.base_path.read_bytes()
    with pytest.raises(UnicodeEncodeError):
        bounded_tee.write("invalid \ud800")
    assert bounded_tee.base_path.read_bytes() == before
    assert not bounded_tee.degraded
    bounded_tee.write("valid afterwards\n")
    assert _session_payloads(tmp_path, bounded_tee.base_path) == ["valid afterwards\n"]


def _run_owned_threads(actions):
    errors = []

    def run(action):
        try:
            action()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(action,)) for action in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(8)
    assert all(not thread.is_alive() for thread in threads)
    return errors


def _coordinate_first_io(tee, *, fail=False):
    actual_write = tee._write_raw
    second_entered = threading.Event()
    observer_lock = threading.Lock()
    calls = []

    def write(text):
        with observer_lock:
            index = len(calls)
            calls.append(text)
        if index == 0:
            # Without serialization both writes select capacity before I/O.
            # With serialization, the first write proceeds after this finite
            # timeout; requiring a second I/O entrant would deadlock the fix.
            second_entered.wait(2)
        else:
            second_entered.set()
        if fail:
            raise OSError(errno.ENOSPC, "owned concurrent write failure")
        actual_write(text)

    tee._write_raw = write


def test_actual_safe_log_concurrent_writers_preserve_budgets_and_complete_records(
    tmp_path, bounded_tee,
):
    from types import SimpleNamespace
    from ui.textual.screens.wizard_screen import WizardScreen

    class Screen:
        _launch_log_fh = bounded_tee
        _log_pane = SimpleNamespace(write_log=lambda *args, **kwargs: None)
        app = SimpleNamespace(call_from_thread=lambda callback, *args, **kw: callback(*args, **kw))
        _tee_to_log = WizardScreen._tee_to_log

    screen = Screen()
    _coordinate_first_io(bounded_tee)
    actions = [
        lambda letter=letter: WizardScreen._safe_log(
            screen, letter * 300, source="owned", level="info",
        ) for letter in ("A", "B")
    ]
    assert _run_owned_threads(actions) == []
    _assert_raw_session_budget(tmp_path, bounded_tee)
    text = "".join(_session_payloads(tmp_path, bounded_tee.base_path))
    a, b = (f"[info/owned] {letter * 300}\n" for letter in ("A", "B"))
    assert text in (a + b, b + a)


def test_concurrent_write_failures_degrade_and_notify_only_once(tmp_path):
    notices = []
    tee = SessionLogTee(
        prefix="atlas-launch-owned-",
        config=SessionLogConfig(directory=str(tmp_path), segment_max_bytes=512),
        on_degraded=notices.append,
    )
    _coordinate_first_io(tee, fail=True)
    try:
        assert _run_owned_threads([lambda: tee.write("a"), lambda: tee.write("b")]) == []
        assert tee.degraded and len(notices) == 1
        _assert_raw_session_budget(tmp_path, tee)
    finally:
        tee.close()


def test_degraded_callback_can_use_the_tee_from_another_thread(tmp_path, monkeypatch):
    completed = threading.Event()
    callback_results, threads = [], []

    def callback(_notice):
        def use_tee():
            tee.flush()
            tee.close()
            tee.write("ignored after degradation")
            completed.set()

        thread = threading.Thread(target=use_tee)
        threads.append(thread)
        thread.start()
        thread.join(3)
        callback_results.append(completed.is_set())

    tee = SessionLogTee(
        prefix="atlas-launch-owned-", config=SessionLogConfig(directory=str(tmp_path)),
        on_degraded=callback,
    )

    def fail_write(_text):
        raise OSError(errno.ENOSPC, "owned callback boundary failure")

    monkeypatch.setattr(tee, "_write_raw", fail_write)
    try:
        tee.write("trigger")
        for thread in threads:
            thread.join(3)
        assert callback_results == [True]
        assert all(not thread.is_alive() for thread in threads)
    finally:
        tee.close()


@pytest.mark.parametrize("full", [False, True])
def test_close_is_terminal_even_when_a_stale_reference_holds_a_full_segment(
    tmp_path, bounded_tee, full,
):
    if full:
        bounded_tee.write("x" * (512 - bounded_tee._bytes))
    before = {p.name: p.read_bytes() for p in _session_files(tmp_path, bounded_tee.base_path)}
    bounded_tee.close()

    for text in ("late write", ""):
        with pytest.raises(ValueError):
            bounded_tee.write(text)

    assert {p.name: p.read_bytes() for p in _session_files(tmp_path, bounded_tee.base_path)} == before


def test_close_waits_for_an_inflight_write_before_closing_the_handle(tmp_path, bounded_tee):
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    close_started = threading.Event()
    actual_write = bounded_tee._write_raw
    errors = []

    def held_write(text):
        started.set()
        assert release.wait(5), "owned writer was not released"
        actual_write(text)

    def writer():
        try:
            bounded_tee.write("owned in-flight line\n")
        except BaseException as exc:
            errors.append(exc)

    def closer():
        close_started.set()
        bounded_tee.close()
        closed.set()

    bounded_tee._write_raw = held_write
    writing, closing = threading.Thread(target=writer), threading.Thread(target=closer)
    writing.start()
    try:
        assert started.wait(5)
        closing.start()
        assert close_started.wait(5)
        closed.wait(0.2)
    finally:
        release.set()
        writing.join(5)
        if closing.ident is not None:
            closing.join(5)
    assert errors == []
    assert closed.is_set()
    assert _session_payloads(tmp_path, bounded_tee.base_path) == ["owned in-flight line\n"]


def test_mixed_concurrent_writes_keep_valid_unicode_prefix_and_retained_tail(tmp_path, bounded_tee):
    actual_write = bounded_tee._write_raw
    observed = []

    def record_write(text):
        actual_write(text)
        if not text.startswith("# atlas session log"):
            observed.append(text)

    def writer(index):
        for item in range(30):
            bounded_tee.write(f"worker {index} item {item}: 界😀\r\n")

    bounded_tee._write_raw = record_write
    actions = [lambda index=index: writer(index) for index in range(3)]
    assert _run_owned_threads(actions) == []
    _assert_raw_session_budget(tmp_path, bounded_tee)
    parts = _session_payloads(tmp_path, bounded_tee.base_path)
    complete = "".join(observed)
    assert len(parts) == 3
    assert complete.startswith(parts[0]) and complete.endswith("".join(parts[1:]))
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in _session_files(tmp_path, bounded_tee.base_path))
