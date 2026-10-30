"""Owned scratch regression cases for active session retention (#1760)."""
import contextlib
import os
from pathlib import Path
import threading

import pytest

from tests.test_session_log_locking import _reap_fork
from ui.session_log import SessionLogConfig, SessionLogTee


def make_tee(parent, **kwargs):
    return SessionLogTee(prefix="atlas-launch-owned-", config=SessionLogConfig(
        directory=str(parent), segment_max_bytes=1024, **kwargs))


def payloads(tee):
    return [p for p in tee.base_path.parent.glob(tee.base_path.name + "*")
            if ".lease" not in p.name]


def test_quiet_live_group_survives_more_than_five_later_attempts(tmp_path):
    old = make_tee(tmp_path)
    old.write("x" * 1800)
    paths = payloads(old)
    assert len(paths) >= 2
    try:
        for _ in range(8):
            later = make_tee(tmp_path)
            later.close()
            assert all(p.exists() for p in paths)
        old.write("still live\n")
        assert not old.degraded
    finally:
        old.close()


def test_closed_group_is_reclaimed_whole_and_legacy_is_untouched(tmp_path):
    legacy = tmp_path / "atlas-launch-legacy.log"
    legacy.write_text("legacy")
    old = make_tee(tmp_path)
    old.write("x" * 1800)
    paths = payloads(old)
    old.close()
    for _ in range(5):
        later = make_tee(tmp_path)
        later.close()
    assert not any(p.exists() for p in paths)
    assert legacy.read_text() == "legacy"


def test_five_live_reservations_refuse_sixth_without_eviction(tmp_path):
    tees = [make_tee(tmp_path) for _ in range(5)]
    try:
        with pytest.raises(OSError, match="capacity"):
            make_tee(tmp_path)
        assert all(t.base_path.exists() for t in tees)
        assert tees[0].base_path.parent != tmp_path
    finally:
        for tee in tees:
            tee.close()


@pytest.mark.parametrize("which", ["base", "current", "earlier"])
@pytest.mark.parametrize("replace", [False, True])
def test_active_path_loss_degrades_once_before_writing(tmp_path, which, replace):
    notices = []
    tee = SessionLogTee(prefix="atlas-launch-owned-", config=SessionLogConfig(
        directory=str(tmp_path), segment_max_bytes=1024), on_degraded=notices.append)
    tee.write("x" * 2200)
    path = {"base": tee.base_path, "current": tee._rolling[-1],
            "earlier": tee._rolling[0]}[which]
    fh = tee._fh
    path.unlink()
    if replace:
        path.write_text("replacement")
    tee.write("must not disappear into unlinked descriptor")
    tee.flush()
    assert tee.degraded and fh.closed and len(notices) == 1
    assert "on-screen logs continue" in notices[0]


def test_concurrent_admissions_are_serialized(tmp_path):
    barrier = threading.Barrier(8)
    admitted, refused = [], []

    def open_one():
        barrier.wait(timeout=5)
        try:
            admitted.append(make_tee(tmp_path))
        except OSError as exc:
            refused.append(exc)

    threads = [threading.Thread(target=open_one) for _ in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(8)
        assert len(admitted) == 5 and len(refused) == 3
        assert all(t.base_path.exists() for t in admitted)
    finally:
        for tee in admitted:
            tee.close()


def test_heterogeneous_reservations_use_actual_capacity(tmp_path, monkeypatch):
    from ui import session_log_retention as retention
    monkeypatch.setattr(retention, 'MAX_PAYLOAD_BYTES', 7 * 1024)
    first = make_tee(tmp_path)
    second = SessionLogTee(prefix='atlas-launch-other-', config=SessionLogConfig(
        directory=str(tmp_path), segment_max_bytes=512, max_segments=3))
    try:
        with pytest.raises(OSError, match='capacity'):
            make_tee(tmp_path)
        second.close()
        third = make_tee(tmp_path)
        assert first.base_path.exists() and not second.base_path.exists()
        third.close()
    finally:
        first.close()
        second.close()


def test_over_global_request_fails_before_publication(tmp_path):
    with pytest.raises(OSError, match='capacity'):
        SessionLogTee(prefix='atlas-launch-owned-', config=SessionLogConfig(
            directory=str(tmp_path), segment_max_bytes=481 * 1024 * 1024))
    assert not list(tmp_path.iterdir())


def test_lower_retention_cannot_evict_live_group(tmp_path):
    first = make_tee(tmp_path, retained_sessions=1)
    try:
        with pytest.raises(OSError, match='capacity'):
            make_tee(tmp_path, retained_sessions=1)
        first.close()
        second = make_tee(tmp_path, retained_sessions=1)
        second.close()
        assert not first.base_path.exists()
    finally:
        first.close()


@pytest.mark.parametrize('which', ['lease', 'coordinator', 'store'])
def test_coordination_identity_loss_degrades_on_flush(tmp_path, which):
    notices = []
    tee = SessionLogTee(prefix='atlas-launch-owned-', config=SessionLogConfig(
        directory=str(tmp_path)), on_degraded=notices.append)
    if which == 'store':
        tee.base_path.parent.rename(tee.base_path.parent.with_name('moved-store'))
    else:
        path = tee._reservation.path if which == 'lease' else tee.base_path.parent / '.coordinator'
        path.unlink()
        path.write_text('replacement')
    tee.flush()
    tee.flush()
    assert tee.degraded and len(notices) == 1 and tee._fh.closed


@pytest.mark.parametrize('kind', ['unleased', 'malformed', 'symlink'])
def test_unknown_inventory_refuses_without_deleting_payload(tmp_path, kind):
    from ui.session_log_retention import resolve_store
    store = resolve_store(tmp_path)
    base = store / 'atlas-launch-unknown.log'
    base.write_text('unknown evidence')
    base.chmod(0o600)
    if kind == 'malformed':
        lease = store / (base.name + '.lease')
        lease.write_text('bad record')
        lease.chmod(0o600)
    elif kind == 'symlink':
        (store / (base.name + '.lease')).symlink_to(base)
    with pytest.raises(OSError):
        make_tee(tmp_path)
    assert base.read_text() == 'unknown evidence'


@pytest.mark.parametrize('mode', [0o600, 0o644])
def test_foreign_store_files_are_ignored_without_disabling_logging(tmp_path, mode):
    first = make_tee(tmp_path)
    first.close()
    foreign = first.base_path.parent / '.DS_Store'
    foreign.write_bytes(b'finder metadata')
    foreign.chmod(mode)
    later = make_tee(tmp_path)
    try:
        later.write('file logging continues\n')
        assert not later.degraded
    finally:
        later.close()
    assert foreign.read_bytes() == b'finder metadata'


def test_incomplete_admission_without_payload_is_recovered(tmp_path):
    from ui.session_log_retention import resolve_store
    store = resolve_store(tmp_path)
    partial = store / 'atlas-launch-partial.log.lease'
    partial.touch(mode=0o600)
    tee = make_tee(tmp_path)
    tee.close()
    assert not partial.exists()


def test_failed_group_delete_stays_charged(tmp_path, monkeypatch):
    old = make_tee(tmp_path, retained_sessions=1)
    old.close()
    actual = Path.unlink

    def fail(path, *args, **kwargs):
        if path == old.base_path:
            raise PermissionError('owned deletion failure')
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    with pytest.raises(OSError, match='capacity'):
        make_tee(tmp_path, retained_sessions=1)
    assert old.base_path.exists() and old._reservation.path.exists()


def test_legacy_pruner_does_not_recurse_private_store(tmp_path):
    import re
    from collections import defaultdict
    old = make_tee(tmp_path)
    old.write('x' * 1800)
    protected = payloads(old)
    for index in range(6):
        (tmp_path / f'atlas-launch-legacy-{index}.log').write_text('old')
    # Original deployed algorithm: inspect only root-level regular log files.
    sessions = defaultdict(list)
    for path in tmp_path.iterdir():
        if path.is_file() and re.match(r'^atlas-launch-.+\.log(\.\d+)?$', path.name):
            sessions[path.name.split('.log')[0]].append(path)
    ordered = sorted(sessions.values(), key=lambda group: max(p.stat().st_mtime for p in group), reverse=True)
    for group in ordered[4:]:
        for path in group:
            path.unlink()
    try:
        assert len(list(tmp_path.glob('atlas-launch-*.log'))) == 4
        assert all(path.exists() for path in protected)
    finally:
        old.close()


def test_actual_missing_log_bundle_read_is_contained(tmp_path):
    from core import support_bundle as bundle
    tee = make_tee(tmp_path)
    tee.base_path.unlink()
    tee.flush()
    entry = bundle.log_entry(bundle.LogSource('session.log', path=tee.base_path),
                             bundle.BundleLimits(), bundle.Redactor())
    assert tee.degraded and entry['available'] is False


@pytest.mark.parametrize('stage', ['before_retire', 'before_open'])
def test_rotation_and_admission_interleave_without_orphans(tmp_path, monkeypatch, stage):
    old = make_tee(tmp_path)
    old.write('x' * 3000)
    old.write('x' * (old._segment_max_bytes - old._bytes))
    entered, release = threading.Event(), threading.Event()
    original_unlink = Path.unlink
    original_open = old._open_segment
    errors = []

    def pause():
        entered.set()
        assert release.wait(5)

    def unlink(path, *args, **kwargs):
        if path == old._rolling[0] and stage == 'before_retire':
            pause()
        return original_unlink(path, *args, **kwargs)

    def open_segment(path):
        if stage == 'before_open':
            pause()
        return original_open(path)

    def rotate():
        try:
            old.write('next segment')
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(Path, 'unlink', unlink)
    monkeypatch.setattr(old, '_open_segment', open_segment)
    thread = threading.Thread(target=rotate)
    thread.start()
    try:
        assert entered.wait(5)
        for _ in range(8):
            other = make_tee(tmp_path)
            other.close()
            assert old.base_path.exists()
            assert len(list(old.base_path.parent.glob('*.lease'))) <= 5
    finally:
        release.set()
        thread.join(5)
        old.close()
    assert not errors and not old.degraded
    assert all(path.exists() for path in payloads(old))
    assert len(payloads(old)) <= 3


def test_saturation_warning_keeps_wizard_and_onscreen_logs_available(tmp_path, monkeypatch):
    import tempfile
    from ui.textual.screens.wizard_screen import WizardScreen
    tees = [make_tee(tmp_path) for _ in range(5)]
    monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(tmp_path))
    try:
        screen = WizardScreen(steps=[], services=[])
        assert screen._launch_log_fh is None and screen._launch_log_path is None
        assert 'could not open launch log file' in screen._log_pane.visible_text()
        screen._log_pane.write_log('on-screen output continues')
        assert 'on-screen output continues' in screen._log_pane.visible_text()
        screen._close_launch_log_tee()
    finally:
        for tee in tees:
            tee.close()


def test_partial_unlink_failure_retains_reservation_charge(tmp_path, monkeypatch):
    old = make_tee(tmp_path, retained_sessions=1)
    old.write('x' * 2200)
    paths = payloads(old)
    old.close()
    actual = Path.unlink
    removed = []

    def fail_second(path, *args, **kwargs):
        if path in paths:
            if removed:
                raise PermissionError('owned second unlink failure')
            removed.append(path)
        return actual(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_second)
    with pytest.raises(OSError, match='capacity'):
        make_tee(tmp_path, retained_sessions=1)
    assert len(removed) == 1 and old._reservation.path.exists()
    assert len(list(old.base_path.parent.glob('*.lease'))) == 1


def test_unreachable_tees_release_ownership_without_erasing_evidence(tmp_path):
    import gc
    import weakref
    live = make_tee(tmp_path)
    abandoned = [make_tee(tmp_path) for _ in range(4)]
    paths = [tee.base_path for tee in abandoned]
    references = [weakref.ref(tee) for tee in abandoned]
    del abandoned
    gc.collect()
    assert all(reference() is None for reference in references)
    assert all(path.exists() for path in paths)
    try:
        newcomer = make_tee(tmp_path)
        newcomer.close()
        assert live.base_path.exists() and not live.degraded
    finally:
        live.close()


@contextlib.contextmanager
def _pause_descriptor_transition(monkeypatch, phase, kind):
    entered, release = threading.Event(), threading.Event()
    handles = []
    actual_open, actual_close = os.open, os.close
    parent = os.getpid()
    suffix = {'coordinator': '.coordinator', 'lease': '.lease', 'writer': '.log'}[kind]

    def pause():
        entered.set()
        assert release.wait(5), 'descriptor transition was not released'

    def open_descriptor(path, *args, **kwargs):
        fd = actual_open(path, *args, **kwargs)
        if not handles and str(path).endswith(suffix):
            handles.append(fd)
            if phase == 'enroll':
                pause()
        return fd

    def close_descriptor(fd):
        if phase == 'close' and handles == [fd] and os.getpid() == parent:
            pause()
        return actual_close(fd)

    monkeypatch.setattr(os, 'open', open_descriptor)
    monkeypatch.setattr(os, 'close', close_descriptor)
    try:
        yield entered, release, handles
    finally:
        release.set()


def _report_transition_child(handle, report, finish, parent):
    import select
    try:
        os.fstat(handle)
        inherited = b'open'
    except OSError:
        inherited = b'closed'
    child_parent = parent / 'child-owned'
    child_parent.mkdir()
    fresh = make_tee(child_parent)
    fresh.write('fresh child logger works\n')
    fresh.close()
    os.write(report, inherited)
    select.select([finish], [], [], 5)
    os._exit(0)


@contextlib.contextmanager
def _fork_during_transition(operation, paused, parent):
    import select
    entered, release, handles = paused
    errors = []

    def run():
        try:
            operation()
        except BaseException as exc:
            errors.append(exc)

    reader, writer = os.pipe()
    finish, finished = os.pipe()
    attempted = threading.Event()

    def release_transition():
        assert attempted.wait(5)
        # A correct before-fork hook waits for the transition. A separate
        # thread releases it even when no child can report until it finishes.
        select.select([reader], [], [], 1)
        release.set()

    worker = threading.Thread(target=run)
    releaser = threading.Thread(target=release_transition)
    pid = None
    worker.start()
    try:
        assert entered.wait(5)
        releaser.start()
        attempted.set()
        pid = os.fork()
        if pid == 0:
            _report_transition_child(handles[0], writer, finish, parent)
        assert select.select([reader], [], [], 3)[0], 'fork child did not report'
        report = os.read(reader, 16)
        worker.join(5)
        assert not worker.is_alive() and not errors, errors
        yield report
    finally:
        release.set()
        attempted.set()
        worker.join(5)
        if releaser.ident is not None:
            releaser.join(5)
        os.write(finished, b'done')
        os.close(finished)
        if pid:
            _reap_fork(pid, require_success=True)
        for fd in (reader, writer, finish):
            os.close(fd)


def _assert_transition_parent_control(control, targets):
    from ui import session_log_retention as retention
    control.write('parent control remains live\n')
    probe = retention._open_private(control._reservation.path)
    try:
        assert not retention._try_lock(probe), 'child unlocked the live parent'
    finally:
        retention._close_fd(probe)
    for target in targets:
        target.close()
    coordinator = retention._coordinator(control.base_path.parent)
    try:
        assert retention._try_lock(coordinator), 'child retained the coordinator'
    finally:
        retention._close_fd(coordinator)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork only')
@pytest.mark.filterwarnings('ignore:This process.*multi-threaded:DeprecationWarning')
@pytest.mark.parametrize('phase', ['enroll', 'close'])
@pytest.mark.parametrize('kind', ['coordinator', 'lease', 'writer'])
def test_fork_serializes_complete_descriptor_transitions(tmp_path, monkeypatch, phase, kind):
    control = make_tee(tmp_path)
    targets = []
    if phase == 'close' and kind != 'coordinator':
        targets.append(make_tee(tmp_path))
    try:
        with _pause_descriptor_transition(monkeypatch, phase, kind) as paused:
            if targets:
                tee = targets[0]
                paused[2].append(tee._reservation.fd if kind == 'lease' else tee._fh.fileno())
                operation = tee.close
            else:
                operation = lambda: targets.append(make_tee(tmp_path))
            with _fork_during_transition(operation, paused, tmp_path) as report:
                _assert_transition_parent_control(control, targets)
                assert report == b'closed', 'child inherited a descriptor outside the registry'
                assert not control.degraded and control.base_path.exists()
    finally:
        for tee in targets:
            tee.close()
        control.close()
