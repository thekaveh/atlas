"""Native owned-process leases and narrow platform adapter contracts (#1760)."""
from __future__ import annotations

import contextlib
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

if __name__ == '__main__':
    # Only the --worker subprocess needs this; pytest's pythonpath covers tests.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui.session_log import SessionLogConfig, SessionLogTee
from ui import session_log_retention as retention


def worker(parent, prefix):
    parent = Path(parent).resolve()

    def guard(event, args):
        if event == 'open':
            path, _, flags = args
            if isinstance(path, int):
                return
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                if not Path(path).absolute().is_relative_to(parent):
                    raise PermissionError('worker write outside owned parent')
        if event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime'):
            if not Path(args[0]).absolute().is_relative_to(parent):
                raise PermissionError('worker mutation outside owned parent')
        if event in ('os.rename', 'os.link', 'os.symlink'):
            if not Path(args[1]).absolute().is_relative_to(parent):
                raise PermissionError('worker destination outside owned parent')
        if event in ('subprocess.Popen', 'os.system', 'socket.connect', 'socket.bind',
                     'socket.getaddrinfo', 'socket.gethostbyname', 'os.posix_spawn', 'os.kill'):
            raise PermissionError('worker external action denied')
    sys.addaudithook(guard)
    tee = SessionLogTee(prefix=prefix, config=SessionLogConfig(
        directory=str(parent), segment_max_bytes=1024))
    tee.write('owned child\n' + 'x' * 1800)
    print(json.dumps({'base': str(tee.base_path)}), flush=True)
    for command in sys.stdin:
        if command.strip() == 'write':
            tee.write('quiet child still alive\n')
            print(json.dumps({'degraded': tee.degraded}), flush=True)
        elif command.strip() == 'close':
            tee.close()
            print('closed', flush=True)
            return
    tee.close()


@contextlib.contextmanager
def owned_child(parent, prefix):
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), '--worker', str(parent), prefix],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    try:
        ready = json.loads(process.stdout.readline())
        yield process, Path(ready['base'])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        error = process.stderr.read()
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
        assert not error, error


def test_independent_native_processes_crash_recovery_and_namespaces(tmp_path):
    with owned_child(tmp_path, 'atlas-launch-project-a-') as (live, base):
        paths = list(base.parent.glob(base.name + '*'))
        with owned_child(tmp_path, 'atlas-launch-project-b-') as (dead, dead_base):
            dead_paths = list(dead_base.parent.glob(dead_base.name + '*'))
            dead.kill()
            dead.wait(timeout=5)
            for _ in range(8):
                tee = SessionLogTee(prefix='atlas-launch-project-c-', config=SessionLogConfig(
                    directory=str(tmp_path), segment_max_bytes=1024))
                tee.write('x' * 2500)
                tee.close()
                assert all(path.exists() for path in paths)
                leases = list(base.parent.glob('*.lease'))
                assert len(leases) <= 5
                payload = list(base.parent.glob('*.log*'))
                assert sum(p.stat().st_size for p in payload) <= 5 * 3 * 1024 + 5 * 1024
            assert not any(path.exists() for path in dead_paths)
            live.stdin.write('write\n')
            live.stdin.flush()
            assert json.loads(live.stdout.readline()) == {'degraded': False}


def test_native_independent_handles_and_release(tmp_path):
    path = tmp_path / 'owned.lease'
    first = retention._open_private(path, create=True)
    second = retention._open_private(path)
    try:
        assert retention._try_lock(first)
        assert not retention._try_lock(second)
        retention._close_lease(first)
        first = None
        assert retention._try_lock(second)
    finally:
        if first is not None:
            os.close(first)
        os.close(second)


@pytest.mark.parametrize('failure', [False, True])
def test_windows_adapter_uses_one_byte_and_restores_position(tmp_path, monkeypatch, failure):
    fd = os.open(tmp_path / 'owned', os.O_CREAT | os.O_RDWR, 0o600)
    calls = []

    def locking(handle, mode, length):
        calls.append((handle, mode, length, os.lseek(handle, 0, os.SEEK_CUR)))
        if failure:
            raise OSError(errno.EBADF, 'owned invalid handle')

    monkeypatch.setitem(sys.modules, 'msvcrt', types.SimpleNamespace(
        LK_NBLCK=2, LK_UNLCK=0, locking=locking))
    try:
        os.lseek(fd, 7, os.SEEK_SET)
        if failure:
            with pytest.raises(OSError):
                retention._windows_lock(fd, False)
        else:
            retention._windows_lock(fd, False)
        assert os.lseek(fd, 0, os.SEEK_CUR) == 7
        assert calls == [(fd, 2, 1, 4096)]
        assert os.fstat(fd).st_size == 0
    finally:
        os.close(fd)


def test_unlock_failure_still_closes_descriptor(tmp_path, monkeypatch):
    fd = retention._open_private(tmp_path / 'owned', create=True)

    def fail(*args, **kwargs):
        raise OSError(errno.EBADF, 'owned unlock failure')

    monkeypatch.setattr(retention, '_lock', fail)
    with pytest.raises(OSError):
        retention._close_lease(fd)
    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork only')
def test_fork_child_cannot_write_or_retain_parent_lease(tmp_path):
    tee = SessionLogTee(prefix='atlas-launch-fork-', config=SessionLogConfig(directory=str(tmp_path)))
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        tee.write('inherited child must not write')
        os.write(writer, str(tee._reservation.fd is None and tee.degraded).encode())
        os.close(writer)
        os._exit(0)
    os.close(writer)
    try:
        assert os.read(reader, 100) == b'True'
        assert os.waitpid(pid, 0)[1] == 0
        probe = retention._open_private(tee._reservation.path)
        try:
            assert not retention._try_lock(probe)
            tee.close()
            assert retention._try_lock(probe)
        finally:
            os.close(probe)
    finally:
        os.close(reader)
        tee.close()


@pytest.mark.skipif(os.name != 'nt', reason='native Windows runtime required')
def test_native_windows_process_and_readable_lease(tmp_path):
    with owned_child(tmp_path, 'atlas-launch-windows-') as (child, base):
        fd = retention._open_private(base.with_name(base.name + '.lease'))
        try:
            assert not retention._try_lock(fd)
            assert retention._read_record(fd)['version'] == 1
            child.stdin.write('close\n')
            child.stdin.flush()
            assert child.stdout.readline().strip() == 'closed'
            child.wait(timeout=5)
            assert retention._try_lock(fd)
        finally:
            os.close(fd)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork only')
def test_fork_closes_inherited_admission_coordinator(tmp_path):
    store = retention.resolve_store(tmp_path)
    fd = retention._coordinator(store)
    retention._acquire_coordinator(fd)
    reader, writer = os.pipe()
    ready_read, ready_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(writer)
        os.close(ready_read)
        os.write(ready_write, b'ready')
        os.read(reader, 1)
        os._exit(0)
    os.close(reader)
    os.close(ready_write)
    try:
        assert os.read(ready_read, 5) == b'ready'
        os.close(fd)
        contender = retention._coordinator(store)
        try:
            assert retention._try_lock(contender)
        finally:
            os.close(contender)
    finally:
        os.close(writer)
        os.close(ready_read)
        os.waitpid(pid, 0)


@pytest.mark.parametrize('owner,grants,accepted', [
    ('user', ['user', 'S-1-5-18', 'S-1-5-32-544'], True),
    ('other', ['user'], False),
    ('user', ['S-1-1-0'], False),
])
def test_windows_store_acl_policy_preserves_private_custom_roots(monkeypatch, owner, grants, accepted):
    monkeypatch.setattr(retention, '_windows_acl', lambda path: (owner, 'user', grants))
    if accepted:
        retention._windows_private(Path('private-custom-store'))
    else:
        with pytest.raises(OSError):
            retention._windows_private(Path('permissive-custom-store'))


def test_invalid_lock_error_is_not_inactive(monkeypatch):
    def invalid(fd, unlock=False):
        raise OSError(errno.EBADF, 'owned invalid descriptor')
    monkeypatch.setattr(retention, '_lock', invalid)
    with pytest.raises(OSError):
        retention._try_lock(-1)


def test_coordinator_deadline_is_bounded(monkeypatch):
    times = iter([0.0, 1.1])
    monkeypatch.setattr(retention.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(retention, '_try_lock', lambda fd: False)
    with pytest.raises(OSError, match='timeout'):
        retention._acquire_coordinator(-1)


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork only')
@pytest.mark.filterwarnings('ignore:This process.*multi-threaded:DeprecationWarning')
def test_fork_during_buffered_write_never_flushes_child_copy(tmp_path, monkeypatch):
    import select
    import threading
    tee = SessionLogTee(prefix='atlas-launch-buffered-', config=SessionLogConfig(directory=str(tmp_path)))
    entered, release = threading.Event(), threading.Event()
    original_write = tee._fh.write
    parent_pid = os.getpid()
    errors = []

    def held_write(text):
        written = original_write(text)
        if os.getpid() == parent_pid:
            entered.set()
            assert release.wait(5)
        return written

    def write():
        try:
            tee.write('pending without newline')
        except BaseException as exc:
            errors.append(exc)

    before = tee.base_path.read_bytes()
    monkeypatch.setattr(tee._fh, 'write', held_write)
    thread = threading.Thread(target=write)
    thread.start()
    reader, writer = os.pipe()
    pid = None
    try:
        assert entered.wait(5)
        before_fork = tee.base_path.read_bytes()
        pid = os.fork()
        if pid == 0:
            os.close(reader)
            tee.write('child must not write')
            os.write(writer, b'ready')
            os._exit(0)
        os.close(writer)
        ready, _, _ = select.select([reader], [], [], 2)
        assert ready, 'fork child blocked on inherited buffered I/O'
        assert os.read(reader, 5) == b'ready'
        assert tee.base_path.read_bytes() == before_fork
    finally:
        release.set()
        thread.join(5)
        os.close(reader)
        if pid:
            _reap_fork(pid)
        tee.close()
    assert not errors
    assert tee.base_path.read_bytes() == before + b'pending without newline'


def test_cleanup_closes_lease_before_sidecar_unlink(tmp_path, monkeypatch):
    tee = SessionLogTee(prefix='atlas-launch-cleanup-', config=SessionLogConfig(
        directory=str(tmp_path), retained_sessions=1))
    tee.close()
    observed = []
    original_close = retention._close_lease
    original_unlink = Path.unlink

    def close(fd):
        observed.append('close')
        return original_close(fd)

    def unlink(path, *args, **kwargs):
        if path == tee._reservation.path:
            observed.append('unlink-lease')
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(retention, '_close_lease', close)
    monkeypatch.setattr(Path, 'unlink', unlink)
    replacement = SessionLogTee(prefix='atlas-launch-cleanup-', config=SessionLogConfig(
        directory=str(tmp_path), retained_sessions=1))
    try:
        assert observed[:2] == ['close', 'unlink-lease']
    finally:
        replacement.close()


@pytest.mark.parametrize('invalid', ['none', 'owner', 'count', 'inherit-only', 'unknown-ace'])
def test_windows_security_descriptor_cleanup_and_ace_inspection(monkeypatch, invalid):
    import ctypes as c
    # A minimal ACL containing one allowed ACE and its eight-byte SID.
    acl = c.create_string_buffer(bytes([2, 0, 24, 0, 1, 0, 0, 0,
        0, 8, 16, 0, 0xFF, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 5]))
    if invalid == 'count':
        acl[4] = bytes([129])
    if invalid == 'unknown-ace':
        acl[8] = bytes([9])
    freed = []

    def get_security(*args):
        assert len(args) == 8
        path, kind, flags, owner, group, dacl, sacl, descriptor = args
        assert path.startswith("owned-") and kind == 1 and flags == 5
        assert group is None and sacl is None
        c.cast(owner, c.POINTER(c.c_void_p))[0] = 0 if invalid == 'owner' else 123
        c.cast(dacl, c.POINTER(c.c_void_p))[0] = c.addressof(acl)
        c.cast(descriptor, c.POINTER(c.c_void_p))[0] = 456
        return 0

    def get_ace(dacl, index, pointer):
        c.cast(pointer, c.POINTER(c.c_void_p))[0] = c.addressof(acl) + 8
        return 1

    adv = types.SimpleNamespace(GetNamedSecurityInfoW=get_security, GetAce=get_ace,
        IsValidSid=lambda pointer: True, GetLengthSid=lambda pointer: 8)
    kernel = types.SimpleNamespace(LocalFree=lambda pointer: freed.append(pointer.value))
    monkeypatch.setattr(retention, '_windows_apis', lambda: (adv, kernel))
    monkeypatch.setattr(retention, '_current_sid', lambda apis: 'user')
    monkeypatch.setattr(retention, '_sid_text', lambda pointer, apis:
                        'everyone' if invalid == 'inherit-only' and isinstance(pointer, int) else 'user')
    if invalid in ('owner', 'count', 'unknown-ace', 'inherit-only'):
        with pytest.raises(OSError):
            retention._windows_private(Path('owned-custom-store'))
    else:
        retention._windows_private(Path('owned-default-store'))
    assert freed == [456]


def test_windows_token_handle_cleanup_on_inspection_failure(monkeypatch):
    import ctypes as c
    from ctypes import wintypes as w
    closed = []

    def open_token(process, access, output):
        c.cast(output, c.POINTER(w.HANDLE))[0] = 123
        return 1

    def get_token(*args):
        assert len(args) == 5
        token, kind, data, size, required = args
        assert token.value == 123 and kind == 1
        assert (data is None) == (size == 0)
        c.cast(required, c.POINTER(w.DWORD))[0] = 8
        return 0

    adv = types.SimpleNamespace(OpenProcessToken=open_token, GetTokenInformation=get_token)
    kernel = types.SimpleNamespace(GetCurrentProcess=lambda: 1,
                                   CloseHandle=lambda handle: closed.append(handle.value))
    monkeypatch.setattr(c, 'get_last_error', lambda: 5, raising=False)
    monkeypatch.setattr(c, 'WinError', lambda code: OSError(code, 'owned API failure'), raising=False)
    with pytest.raises(OSError):
        retention._current_sid((adv, kernel))
    assert closed == [123]


@pytest.mark.skipif(os.name == 'nt', reason='POSIX missing-primitive path')
def test_missing_lock_primitive_is_contained_as_oserror(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'fcntl', None)
    with pytest.raises(OSError, match='locking unavailable'):
        SessionLogTee(prefix='atlas-launch-missing-', config=SessionLogConfig(directory=str(tmp_path)))


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork only')
def test_inherited_writer_close_cannot_touch_reused_child_descriptor(tmp_path):
    tee = SessionLogTee(prefix='atlas-launch-reuse-', config=SessionLogConfig(directory=str(tmp_path)))
    tee._fh.write('pending parent canary')
    original_fd = tee._fh.fileno()
    reader, writer = os.pipe()
    target = tmp_path / 'child-owned.txt'
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        fd = os.open(target, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600)
        if fd != original_fd:
            os.dup2(fd, original_fd)
            os.close(fd)
        os.write(original_fd, b'child only')
        tee.close()
        try:
            os.fstat(original_fd)
            usable = True
        except OSError:
            usable = False
        report = {'usable': usable, 'text': target.read_text()}
        os.write(writer, json.dumps(report).encode())
        os._exit(0)
    os.close(writer)
    try:
        report = json.loads(os.read(reader, 4096))
        assert os.waitpid(pid, 0)[1] == 0
        assert report == {'usable': True, 'text': 'child only'}
    finally:
        os.close(reader)
        tee.close()


@pytest.mark.parametrize('zero_progress', [False, True])
def test_raw_writer_handles_short_writes_and_zero_progress(tmp_path, monkeypatch, zero_progress):
    notices = []
    tee = SessionLogTee(prefix='atlas-launch-short-', config=SessionLogConfig(
        directory=str(tmp_path)), on_degraded=notices.append)
    original_write = os.write
    fd = tee._fh.fileno()

    def short_write(handle, data):
        if handle == fd:
            return 0 if zero_progress else original_write(handle, data[:2])
        return original_write(handle, data)

    monkeypatch.setattr(os, 'write', short_write)
    try:
        tee.write('界😀\r\n')
        if zero_progress:
            assert tee.degraded and tee._fh.closed and len(notices) == 1
        else:
            assert not tee.degraded and tee.base_path.read_text().endswith('界😀\n')
            assert tee.base_path.read_bytes().endswith('界😀\r\n'.encode())
            assert tee._bytes == tee.base_path.stat().st_size
    finally:
        tee.close()


def test_failed_writer_close_retains_ownership_until_writer_stops(tmp_path, monkeypatch):
    config = SessionLogConfig(directory=str(tmp_path), retained_sessions=1)
    tee = SessionLogTee(prefix='atlas-launch-close-', config=config)
    actual_close = tee._fh.close

    def fail():
        raise OSError(errno.EIO, 'owned close failure with usable writer')

    monkeypatch.setattr(tee._fh, 'close', fail)
    try:
        tee.close()
        assert tee._reservation.fd is not None and not tee._fh.closed
        with pytest.raises(OSError, match='capacity'):
            SessionLogTee(prefix='atlas-launch-close-', config=config)
        monkeypatch.setattr(tee._fh, 'close', actual_close)
        tee.close()
        replacement = SessionLogTee(prefix='atlas-launch-close-', config=config)
        replacement.close()
        assert not tee.base_path.exists()
    finally:
        monkeypatch.setattr(tee._fh, 'close', actual_close)
        tee.close()


def _reap_fork(pid, *, require_success=False):
    import signal
    import time
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            assert not require_success or status == 0, 'fork child failed'
            return
        time.sleep(0.01)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    if require_success:
        pytest.fail('fork child did not exit after release')


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork only')
@pytest.mark.filterwarnings('ignore:This process.*multi-threaded:DeprecationWarning')
def test_admission_in_progress_writer_is_abandoned_before_fd_reuse(tmp_path, monkeypatch):
    import select
    import threading
    entered, release = threading.Event(), threading.Event()
    holders, tees, errors = [], [], []
    actual_open, actual_write = SessionLogTee._open_segment, SessionLogTee._write_raw

    def open_segment(self, path):
        result = actual_open(self, path)
        holders.append(result)
        return result

    def held_write(self, text):
        entered.set()
        assert release.wait(5)
        actual_write(self, text)

    def construct():
        try:
            tees.append(SessionLogTee(prefix='atlas-launch-admitting-', config=SessionLogConfig(directory=str(tmp_path))))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(SessionLogTee, '_open_segment', open_segment)
    monkeypatch.setattr(SessionLogTee, '_write_raw', held_write)
    thread = threading.Thread(target=construct)
    thread.start()
    reader, writer = os.pipe()
    pid = None
    try:
        assert entered.wait(5)
        original_fd = holders[0].fileno()
        pid = os.fork()
        if pid == 0:
            os.close(reader)
            fd = os.open(tmp_path / 'child-owned', os.O_CREAT | os.O_RDWR, 0o600)
            os.dup2(fd, original_fd)
            holders[0].close()
            os.fstat(original_fd)
            os.write(writer, str(holders[0].closed).encode())
            os._exit(0)
        os.close(writer)
        assert select.select([reader], [], [], 2)[0]
        assert os.read(reader, 16) == b'True'
    finally:
        release.set()
        thread.join(5)
        os.close(reader)
        if pid:
            _reap_fork(pid)
        for tee in tees:
            tee.close()
    assert not errors


if __name__ == '__main__' and sys.argv[1:2] == ['--worker']:
    worker(sys.argv[2], sys.argv[3])
