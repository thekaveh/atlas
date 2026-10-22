"""Private shared session reservations and lifetime OS leases (#1760).

Only the stable coordinator serializes inventory/admission. A lease stays held
without that coordinator throughout writing, rotation and quiet periods.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
import uuid

MAX_GROUPS = 5
MAX_PAYLOAD_BYTES = 5 * 3 * 32 * 1024 * 1024
RECORD_BYTES = 1024
LOCK_OFFSET = 4096
COORDINATOR = '.coordinator'
BASE_RE = re.compile(r'^atlas-launch-[A-Za-z0-9_-]+\.log$')


_FORK_DESCRIPTORS = {}
_FORK_LOCK = threading.RLock()


def track_descriptor(fd):
    info = os.fstat(fd)
    _FORK_DESCRIPTORS[fd] = (info.st_dev, info.st_ino)


def forget_descriptor(fd):
    _FORK_DESCRIPTORS.pop(fd, None)


def _close_fd(fd):
    with _FORK_LOCK:
        os.close(fd)
        forget_descriptor(fd)


def _before_fork():
    _FORK_LOCK.acquire()


def _after_fork_parent():
    _FORK_LOCK.release()


def _after_fork():
    global _FORK_LOCK
    # Include admissions in progress, before a tee enters its weak registry.
    # Close only matching inherited descriptions, and never explicitly unlock.
    for fd, expected in list(_FORK_DESCRIPTORS.items()):
        with contextlib.suppress(OSError):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == expected:
                os.close(fd)
    _FORK_DESCRIPTORS.clear()
    # The before hook owns the inherited mutex; later child operations use a
    # fresh one, without retaining another thread's synchronization state.
    _FORK_LOCK = threading.RLock()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(before=_before_fork, after_in_parent=_after_fork_parent,
                        after_in_child=_after_fork)


def _windows_lock(fd, unlock):
    import msvcrt
    position = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        mode = msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def _lock(fd, unlock=False):
    try:
        if os.name == 'nt':
            _windows_lock(fd, unlock)
        else:
            import fcntl
            mode = fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(fd, mode)
    except ImportError as exc:
        raise OSError('session-log locking unavailable') from exc


def _try_lock(fd):
    try:
        _lock(fd)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


def _acquire_coordinator(fd):
    deadline = time.monotonic() + 1.0
    while not _try_lock(fd):
        if time.monotonic() >= deadline:
            raise OSError('session-log coordinator timeout')
        time.sleep(0.01)


def _close_lease(fd):
    try:
        _lock(fd, unlock=True)
    finally:
        _close_fd(fd)


def identity(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or _redirected(info):
        raise OSError('session-log path is not a regular file')
    _private(info)
    return info.st_dev, info.st_ino


def _redirected(info):
    return bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _private(info):
    if os.name != 'nt':
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise OSError('session-log store is not owner-private')


def _windows_apis():
    import ctypes as c
    from ctypes import wintypes as w
    adv = c.WinDLL('advapi32', use_last_error=True)
    kernel = c.WinDLL('kernel32', use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, w.DWORD, w.DWORD,
        c.POINTER(c.c_void_p), c.POINTER(c.c_void_p), c.POINTER(c.c_void_p),
        c.POINTER(c.c_void_p), c.POINTER(c.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p,
                                       w.DWORD, c.POINTER(w.DWORD)]
    adv.GetTokenInformation.restype = w.BOOL
    adv.ConvertSidToStringSidW.argtypes = [c.c_void_p, c.POINTER(w.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    adv.GetAce.argtypes = [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)]
    adv.GetAce.restype = w.BOOL
    adv.IsValidSid.argtypes = [c.c_void_p]
    adv.IsValidSid.restype = w.BOOL
    adv.GetLengthSid.argtypes = [c.c_void_p]
    adv.GetLengthSid.restype = w.DWORD
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.LocalFree.restype = c.c_void_p
    return adv, kernel


def _sid_text(pointer, apis):
    import ctypes as c
    from ctypes import wintypes as w
    adv, kernel = apis
    text = w.LPWSTR()
    if not adv.IsValidSid(pointer):
        raise OSError('invalid Windows session-log SID')
    if not adv.ConvertSidToStringSidW(pointer, c.byref(text)):
        raise c.WinError(c.get_last_error())
    try:
        return text.value
    finally:
        kernel.LocalFree(c.cast(text, c.c_void_p))


def _current_sid(apis):
    import ctypes as c
    from ctypes import wintypes as w
    adv, kernel = apis
    token = w.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, c.byref(token)):
        raise c.WinError(c.get_last_error())
    try:
        required = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, c.byref(required))
        if not 0 < required.value <= 65536:
            raise OSError('unusable Windows session-log token')
        data = c.create_string_buffer(required.value)
        if not adv.GetTokenInformation(token, 1, data, len(data), c.byref(required)):
            raise c.WinError(c.get_last_error())
        return _sid_text(c.cast(data, c.POINTER(c.c_void_p))[0], apis)
    finally:
        kernel.CloseHandle(token)


def _acl_grants(dacl, apis):
    import ctypes as c
    adv, _ = apis
    header = c.string_at(dacl, 8)
    size = int.from_bytes(header[2:4], 'little')
    count = int.from_bytes(header[4:6], 'little')
    if not 8 <= size <= 65535 or count > 128:
        raise OSError('unusable Windows session-log DACL')
    grants = []
    for index in range(count):
        pointer = c.c_void_p()
        if not adv.GetAce(dacl, index, c.byref(pointer)):
            raise c.WinError(c.get_last_error())
        grant = _ace_grant(pointer, (dacl, size), apis)
        if grant is not None:
            grants.append(grant)
    return grants


def _ace_grant(pointer, extent, apis):
    import ctypes as c
    adv, _ = apis
    dacl, size = extent
    ace = c.string_at(pointer, 8)
    ace_size = int.from_bytes(ace[2:4], 'little')
    offset = pointer.value - dacl
    if offset < 8 or offset + ace_size > size or ace_size < 16:
        raise OSError('invalid Windows session-log ACE')
    if ace[0] not in (0, 1):
        raise OSError('unsupported Windows session-log ACE')
    sid = pointer.value + 8
    if not adv.IsValidSid(sid) or adv.GetLengthSid(sid) > ace_size - 8:
        raise OSError('invalid Windows session-log ACE SID')
    if ace[0] == 0:
        # Include INHERIT_ONLY grants: these can expose child logs.
        return _sid_text(sid, apis)
    return None


def _windows_acl(path):
    import ctypes as c
    adv, kernel = apis = _windows_apis()
    owner, dacl, descriptor = c.c_void_p(), c.c_void_p(), c.c_void_p()
    result = adv.GetNamedSecurityInfoW(str(path), 1, 0x00000001 | 0x00000004,
        c.byref(owner), None, c.byref(dacl), None, c.byref(descriptor))
    if result:
        raise c.WinError(result)
    try:
        if not owner.value or not dacl.value:
            raise OSError('Windows session-log owner or DACL missing')
        return _sid_text(owner, apis), _current_sid(apis), _acl_grants(dacl.value, apis)
    finally:
        kernel.LocalFree(descriptor)


def _windows_private(path):
    owner, user, grants = _windows_acl(path)
    if owner != user:
        raise OSError('Windows session-log owner differs from current user')
    if set(grants) - {user, 'S-1-5-18', 'S-1-5-32-544'}:
        raise OSError('Windows session-log store permits other accounts')


def resolve_store(parent):
    parent = Path(parent or tempfile.gettempdir()).resolve(strict=True)
    if os.name == 'nt':
        name = 'atlas-session-logs-v1'
    else:
        name = f'atlas-session-logs-v1-{os.geteuid()}'
    store = parent / name
    with contextlib.suppress(FileExistsError):
        store.mkdir(mode=0o700)
    info = store.lstat()
    if not stat.S_ISDIR(info.st_mode) or _redirected(info):
        raise OSError('session-log store is redirected or unusable')
    _private(info)
    if os.name == 'nt':
        _windows_private(store)
    return store


def _open_private(path, create=False):
    flags = os.O_RDWR | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    # Fork must see either the fully enrolled descriptor or no descriptor.
    with _FORK_LOCK:
        fd = os.open(path, flags, 0o600)
        try:
            os.set_inheritable(fd, False)
            info = os.fstat(fd)
            _private(info)
            if identity(path) != (info.st_dev, info.st_ino):
                raise OSError('session-log file changed while opening')
            track_descriptor(fd)
            return fd
        except BaseException:
            _close_fd(fd)
            raise


def _coordinator(store):
    path = store / COORDINATOR
    try:
        fd = _open_private(path, create=True)
    except FileExistsError:
        fd = _open_private(path)
    if os.fstat(fd).st_size:
        _close_fd(fd)
        raise OSError('unexpected session-log coordinator contents')
    return fd


def _check_coordinator(store, fd):
    info = os.fstat(fd)
    if identity(store / COORDINATOR) != (info.st_dev, info.st_ino):
        raise OSError('session-log coordinator changed during admission')


def _check_descriptor(path, fd):
    info = os.fstat(fd)
    if identity(path) != (info.st_dev, info.st_ino):
        raise OSError('session-log lease changed during inspection')


def _read_record(fd):
    if os.fstat(fd).st_size > RECORD_BYTES:
        raise OSError('oversized session-log lease record')
    os.lseek(fd, 0, os.SEEK_SET)
    raw = os.read(fd, RECORD_BYTES)
    try:
        record = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(record, dict):
        return None
    if set(record) != {'version', 'segment_bytes', 'segments'}:
        return None
    if type(record['version']) is not int or record['version'] != 1:
        return None
    return record


def _reservation(record):
    if record is None:
        raise OSError('ambiguous session-log reservation')
    size, count = record['segment_bytes'], record['segments']
    if type(size) is not int or type(count) is not int:
        raise OSError('invalid session-log reservation')
    if size <= 0 or count < 2 or size * count > MAX_PAYLOAD_BYTES:
        raise OSError('invalid session-log reservation')
    return size * count


def _group_paths(store, base):
    paths = []
    for path in store.iterdir():
        if path.name == base or re.fullmatch(re.escape(base) + r'\.\d+', path.name):
            identity(path)
            paths.append(path)
    return paths


def _inventory(store):
    leases, payload = {}, set()
    for path in store.iterdir():
        if path.name == COORDINATOR:
            continue
        identity(path)
        base = path.name.removesuffix('.lease')
        if path.name.endswith('.lease') and BASE_RE.fullmatch(base):
            leases[base] = path
        elif re.fullmatch(r'atlas-launch-[A-Za-z0-9_-]+\.log(?:\.\d+)?', path.name):
            payload.add(path.name.split('.log')[0] + '.log')
        else:
            raise OSError('unknown file in session-log store')
    if payload - leases.keys():
        raise OSError('unleased session-log payload')
    if len(leases) > MAX_GROUPS:
        raise OSError('session-log inventory exceeds capacity')
    return leases


def _validate_payload(paths, record):
    if len(paths) > record['segments']:
        raise OSError('session-log group exceeds reservation')
    for path in paths:
        if path.stat().st_size > record['segment_bytes']:
            raise OSError('session-log segment exceeds reservation')


def _inspect_candidate(store, base):
    path = store / (base + '.lease')
    fd = _open_private(path)
    try:
        available = _try_lock(fd)
        _check_descriptor(path, fd)
        record = _read_record(fd)
        paths = _group_paths(store, base)
        if available and record is None and not paths:
            _close_lease(fd)
            fd = None
            path.unlink()
            return None
        budget = _reservation(record)
        _validate_payload(paths, record)
        result = [base, budget, path.stat().st_mtime_ns, fd if available else None]
        if available:
            fd = None  # inventory owns this independently opened contender
        return result
    finally:
        if fd is not None:
            _close_fd(fd)


def _candidates(store):
    result = []
    try:
        for base in _inventory(store):
            candidate = _inspect_candidate(store, base)
            if candidate is not None:
                result.append(candidate)
        return result
    except BaseException:
        _release_candidates(result)
        raise


def _release_candidates(candidates):
    for _, _, _, fd in candidates:
        if fd is not None:
            with contextlib.suppress(OSError):
                _close_lease(fd)


def _delete_candidate(store, candidate):
    base, _, _, fd = candidate
    _check_descriptor(store / (base + '.lease'), fd)
    checked = {path: identity(path) for path in _group_paths(store, base)}
    for path, expected in checked.items():
        if identity(path) != expected:
            raise OSError('session-log cleanup path changed')
        path.unlink()
    if _group_paths(store, base):
        raise OSError('session-log group cleanup incomplete')
    # The coordinator remains held, so no contender can reopen this name.
    # Windows needs the descriptor closed before deleting its sidecar.
    try:
        _close_lease(fd)
    finally:
        candidate[3] = None
    (store / (base + '.lease')).unlink()


def _make_room(store, budget, target):
    candidates = _candidates(store)
    count = len(candidates)
    reserved = sum(item[1] for item in candidates)
    try:
        for candidate in sorted(candidates, key=lambda item: item[2]):
            if count < target and reserved + budget <= MAX_PAYLOAD_BYTES:
                break
            if candidate[3] is None:
                continue
            try:
                _delete_candidate(store, candidate)
            except OSError:
                # A partial deletion is still charged; never reuse its bytes.
                continue
            candidates.remove(candidate)
            count -= 1
            reserved -= candidate[1]
        if count >= target or reserved + budget > MAX_PAYLOAD_BYTES:
            raise OSError('session-log capacity unavailable; on-screen logs continue')
    finally:
        _release_candidates(candidates)


class Reservation:
    """One admitted group; close releases ownership but does not erase its charge."""

    def __init__(self, store, base):
        self.store = store
        self.base_path = store / base
        self.path = store / (base + '.lease')
        self.fd = None
        self.store_identity = None
        self.lock_identity = None
        self.lease_identity = None

    def publish(self, config):
        self.fd = _open_private(self.path, create=True)
        _lock(self.fd)
        record = {'version': 1, 'segment_bytes': config.segment_max_bytes,
                  'segments': config.max_segments}
        raw = json.dumps(record, separators=(',', ':')).encode('ascii')
        if os.write(self.fd, raw) != len(raw):
            raise OSError('incomplete session-log reservation')
        self.store_identity = self._directory_identity()
        self.lock_identity = identity(self.store / COORDINATOR)
        self.lease_identity = identity(self.path)

    def _directory_identity(self):
        info = self.store.lstat()
        if not stat.S_ISDIR(info.st_mode) or _redirected(info):
            raise OSError('session-log store disappeared or changed')
        _private(info)
        return info.st_dev, info.st_ino

    def check(self):
        if self._directory_identity() != self.store_identity:
            raise OSError('session-log store changed')
        if identity(self.store / COORDINATOR) != self.lock_identity:
            raise OSError('session-log coordinator changed')
        if identity(self.path) != self.lease_identity:
            raise OSError('session-log lease changed')

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            _close_lease(fd)

    def after_fork(self):
        # Never LOCK_UN an inherited open description: that unlocks the parent.
        self.fd = None  # module hook already closed all inherited lease fds

    def rollback(self):
        self.close()
        for path in _group_paths(self.store, self.base_path.name):
            path.unlink()
        self.path.unlink(missing_ok=True)


@contextlib.contextmanager
def admission(prefix, config):
    budget = config.segment_max_bytes * config.max_segments
    if budget > MAX_PAYLOAD_BYTES or config.retained_sessions < 1:
        raise OSError('session-log request exceeds capacity')
    if not re.fullmatch(r'atlas-launch-[A-Za-z0-9_-]*', prefix):
        raise OSError('invalid session-log prefix')
    store = resolve_store(config.directory)
    coordinator = _coordinator(store)
    reservation = None
    try:
        _acquire_coordinator(coordinator)
        _check_coordinator(store, coordinator)
        _make_room(store, budget, min(config.retained_sessions, MAX_GROUPS))
        reservation = Reservation(store, prefix + uuid.uuid4().hex + '.log')
        try:
            reservation.publish(config)
            yield reservation
        except BaseException:
            reservation.rollback()
            raise
    finally:
        _close_fd(coordinator)
