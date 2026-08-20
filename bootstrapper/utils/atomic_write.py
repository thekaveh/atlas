"""Crash-safe, mode-preserving writes for secrets-bearing text files."""

from __future__ import annotations

import os
import errno
import glob as _glob
import re
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


#: How many snapshots to keep per (source file, version) prefix. Every base-port
#: change, key rotation and env migration drops another full copy of `.env`, so
#: without a cap the repo root accumulates them without bound — each one holding
#: the Supabase JWT signing keys and whatever cloud-provider keys are configured.
#: Keeping the most recent few preserves the rollback the backups exist for while
#: bounding how long a *rotated* secret stays readable on disk.
BACKUP_RETENTION = 5


def _prune_old_backups(
    source_path: Path, prefix: str, *, keep: int, protect: Path | None = None
) -> None:
    """Delete all but the *keep* newest snapshots sharing *prefix*.

    *protect* is the snapshot just written; it is never a prune candidate. The
    timestamp segment has one-second resolution, so a burst of rotations can
    leave several snapshots sharing an mtime — without this, the pruner could
    evict the very backup the caller is about to return.

    Best-effort by design: a backup that cannot be removed (permissions, a
    racing process) must never fail the write that just succeeded.
    """
    if keep < 0:
        return
    # The glob alone is not enough to scope a version. `.env.backup.*` also
    # matches `.env.backup.v1.<ts>.<rand>`, so an UNVERSIONED rotation would
    # treat the v1/v2/v3 migration snapshots as its own history and prune the
    # rollback points those exist to provide. Requiring a timestamp immediately
    # after the prefix keeps each version's history disjoint: after
    # `.env.backup.` a versioned name has `v1.`, not a digit.
    # The regex is escaped, so the glob must be too: ATLAS_ENV_FILE is
    # operator-supplied, and a basename containing a bracket makes
    # `glob(f"{prefix}*")` a character class that matches nothing — silently
    # disabling retention entirely, which is the one failure this must not have.
    scoped = re.compile(re.escape(prefix) + r"\d{8}T\d{6}\.")
    # Compare by NAME, not by path object. `tempfile.mkstemp` always returns an
    # absolute path while `glob` yields paths in whatever form the caller passed
    # in, so `p != protect` is True for every candidate when `source` is
    # relative (or contains `..`) — which puts the just-written snapshot back
    # into the prune set and silently retains one fewer than asked, or at
    # keep=1 deletes the file being returned. Both live in the same directory,
    # so the basename is an exact identity here.
    protect_name = protect.name if protect is not None else None
    try:
        existing = [
            p
            for p in source_path.parent.glob(_glob.escape(prefix) + "*")
            if p.is_file() and p.name != protect_name and scoped.match(p.name)
        ]
    except OSError:
        return
    # `protect` occupies one retention slot.
    keep = max(0, keep - 1)
    # mkstemp's suffix is random, so name order is not age order — stat instead.
    # Several snapshots can share an mtime (the timestamp segment has one-second
    # resolution and a rotation writes them in a burst), so the name breaks ties
    # to keep the choice deterministic rather than glob-order dependent. An entry
    # that vanishes mid-scan sorts oldest and is simply skipped by the unlink.
    def _age(path: Path) -> tuple[float, str]:
        try:
            return (path.stat().st_mtime, path.name)
        except OSError:
            return (0.0, path.name)

    for stale in sorted(existing, key=_age, reverse=True)[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def create_private_backup(
    source: str | Path,
    *,
    version: str | None = None,
    keep: int = BACKUP_RETENTION,
) -> Path:
    """Create a durable, collision-resistant 0600 snapshot of *source*.

    Retains the *keep* most recent snapshots for this source+version prefix and
    prunes older ones. Pass ``keep=-1`` to disable pruning.
    """
    source_path = Path(source)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    version_segment = f".{version}" if version else ""
    prefix = f"{source_path.name}.backup{version_segment}."
    fd, raw_backup = tempfile.mkstemp(
        dir=source_path.parent,
        prefix=f"{prefix}{timestamp}.",
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
        # Prune only after the new snapshot is durable, so a crash mid-write can
        # never leave the caller with fewer backups than it started with.
        _prune_old_backups(source_path, prefix, keep=keep, protect=backup)
        return backup
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    finally:
        if fd >= 0:
            os.close(fd)
