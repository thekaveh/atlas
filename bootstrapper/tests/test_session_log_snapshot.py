"""Owned-file, deterministic snapshot regressions for #1759."""
from pathlib import Path
import threading

import pytest

from ui.session_log import SessionLogConfig, SessionLogTee


@pytest.fixture
def tee(tmp_path):
    value = SessionLogTee(prefix="atlas-launch-snapshot-", config=SessionLogConfig(
        directory=str(tmp_path), segment_max_bytes=1024, max_segments=16))
    try:
        yield value
    finally:
        value.close()


def test_snapshot_reconstructs_numeric_segments_without_generated_headers(tee):
    payload = "".join(f"row-{index:04d}: café🙂 " + "x" * 50 + "\n" for index in range(120))
    tee.write(payload)
    assert tee._segment_index >= 10
    snapshot = tee.snapshot()
    assert snapshot.data.decode() == payload
    assert snapshot.segment_indexes == tuple(range(tee._segment_index + 1))
    assert not snapshot.head_cut and not snapshot.history_omitted
    assert snapshot.original_bytes == sum(p.stat().st_size for p in tee._identities)
    assert tee.base_path.exists()


def test_only_exact_generated_header_offsets_are_removed(tee):
    text = "# atlas session log — rotated segment 50 at user-text\n"
    tee.write(text * 30)
    assert tee.snapshot().data.decode() == text * 30


def test_snapshot_selects_recent_contiguous_run_and_reports_gap(tmp_path):
    tee = SessionLogTee(prefix="atlas-launch-gap-", config=SessionLogConfig(
        directory=str(tmp_path), segment_max_bytes=1024))
    try:
        tee.write("initial-context\n" + "numbered-line\n" * 1000 + "TERMINAL_FAILURE_CANARY\n")
        snapshot = tee.snapshot()
        assert snapshot.data.endswith(b"TERMINAL_FAILURE_CANARY\n")
        assert b"initial-context" not in snapshot.data
        assert snapshot.history_omitted and snapshot.head_cut
        assert snapshot.segment_indexes == (tee._segment_index - 1, tee._segment_index)
        assert b"initial-context" in tee.base_path.read_bytes()
    finally:
        tee.close()


def test_snapshot_read_budget_is_total_and_explicit(tee, monkeypatch):
    tee.write("café🙂 line\n" * 600)
    from ui import session_log
    original = session_log.os.read
    requests, returned = [], []

    def observe(fd, count):
        requests.append(count)
        data = original(fd, count)
        returned.append(len(data))
        return data

    monkeypatch.setattr(session_log.os, "read", observe)
    snapshot = tee.snapshot(max_bytes=317)
    assert requests and all(0 < count <= 317 for count in requests)
    assert sum(returned) <= 317 and len(snapshot.data) <= 317
    assert snapshot.head_cut


@pytest.mark.parametrize("which", ["base", "current", "older"])
@pytest.mark.parametrize("replace", [False, True])
def test_snapshot_path_loss_degrades_once_without_exporting_replacement(tee, which, replace):
    tee.write("x" * 2200)
    path = {"base": tee.base_path, "current": tee._rolling[-1], "older": tee._rolling[0]}[which]
    notices = []
    tee._on_degraded = notices.append
    path.unlink()
    if replace:
        path.write_text("UNTRUSTED_REPLACEMENT")
    snapshot = tee.snapshot()
    assert snapshot.error and not snapshot.data
    assert tee.degraded and len(notices) == 1
    assert tee.snapshot().error and len(notices) == 1


def test_snapshot_after_close_does_not_reopen_paths(tee, monkeypatch):
    tee.write("closed evidence\n")
    tee.close()
    monkeypatch.setattr(Path, "open", lambda *_a, **_k: pytest.fail("closed path reopened"))
    assert tee.snapshot().error and not tee.snapshot().data


def test_snapshot_holds_lock_until_read_and_blocks_close_and_rotation(tee, monkeypatch):
    tee.write("before snapshot\n")
    entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
    original = tee._read_snapshot_member
    results = []

    def paused(path, start, count):
        entered.set()
        assert release.wait(5)
        return original(path, start, count)

    def close_and_write():
        attempted.set()
        tee.write("after snapshot\n" + "x" * 1500)
        tee.close()

    monkeypatch.setattr(tee, "_read_snapshot_member", paused)
    reader = threading.Thread(target=lambda: results.append(tee.snapshot()))
    writer = threading.Thread(target=close_and_write)
    reader.start()
    try:
        assert entered.wait(5)
        writer.start()
        assert attempted.wait(5)
        assert not tee._closed
    finally:
        release.set()
        reader.join(6)
        writer.join(6)
    assert not reader.is_alive() and not writer.is_alive()
    assert results[0].data == b"before snapshot\n"


def test_snapshot_health_failure_callback_runs_outside_lock(tee):
    completed = threading.Event()

    def notice(_message):
        worker = threading.Thread(target=lambda: (tee.flush(), completed.set()))
        worker.start()
        worker.join(3)
        assert not worker.is_alive()

    tee._on_degraded = notice
    tee.base_path.unlink()
    assert tee.snapshot().error
    assert completed.is_set()


def test_raw_read_lifecycle_stays_inside_existing_fork_mutex(tee, monkeypatch):
    from ui import session_log
    tee.write("bounded reader lifecycle\n")
    events = []
    descriptor = []
    native_open, native_read, native_close = session_log.os.open, session_log.os.read, session_log.os.close

    def opened(path, flags, *args):
        fd = native_open(path, flags, *args)
        if Path(path) == tee.base_path and flags & session_log.os.O_ACCMODE == session_log.os.O_RDONLY:
            assert session_log._retention._FORK_LOCK._is_owned()
            descriptor.append(fd)
            events.append("open")
        return fd

    def read(fd, count):
        if fd in descriptor:
            assert session_log._retention._FORK_LOCK._is_owned()
            events.append("read")
        return native_read(fd, count)

    def closed(fd):
        if fd in descriptor:
            assert session_log._retention._FORK_LOCK._is_owned()
            events.append("close")
        return native_close(fd)

    monkeypatch.setattr(session_log.os, "open", opened)
    monkeypatch.setattr(session_log.os, "read", read)
    monkeypatch.setattr(session_log.os, "close", closed)
    assert tee.snapshot().data == b"bounded reader lifecycle\n"
    assert events == ["open", "read", "close"]
