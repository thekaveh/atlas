from __future__ import annotations

import os
from pathlib import Path

import pytest

from utils import atomic_write


def test_atomic_write_replace_failure_preserves_original_and_cleans_secret_tmp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / ".env"
    destination.write_text("SECRET=old\n", encoding="utf-8")
    os.chmod(destination, 0o600)

    monkeypatch.setattr(
        atomic_write.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        atomic_write.atomic_write_text(destination, "SECRET=new\n")

    assert destination.read_text(encoding="utf-8") == "SECRET=old\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [destination]


def test_atomic_write_preserves_mode_and_replaces_complete_content(
    tmp_path: Path,
) -> None:
    destination = tmp_path / ".env"
    destination.write_text("SECRET=old\n", encoding="utf-8")
    os.chmod(destination, 0o600)

    atomic_write.atomic_write_text(destination, "SECRET=new\n")

    assert destination.read_text(encoding="utf-8") == "SECRET=new\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600


def test_atomic_write_can_enforce_a_private_mode(tmp_path: Path) -> None:
    destination = tmp_path / "consumer.env"
    destination.write_text("TOKEN=old\n", encoding="utf-8")
    os.chmod(destination, 0o644)

    atomic_write.atomic_write_text(destination, "TOKEN=new\n", mode=0o600)

    assert destination.read_text(encoding="utf-8") == "TOKEN=new\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600


def test_atomic_write_falls_back_when_fchmod_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / ".env"
    monkeypatch.delattr(atomic_write.os, "fchmod")

    atomic_write.atomic_write_text(destination, "SECRET=new\n", mode=0o600)

    assert destination.read_text(encoding="utf-8") == "SECRET=new\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is POSIX-only")
def test_atomic_write_fsyncs_file_and_parent_directory(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[int] = []
    real_fsync = atomic_write.os.fsync

    def recording_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(atomic_write.os, "fsync", recording_fsync)

    atomic_write.atomic_write_text(tmp_path / ".env", "SECRET=new\n")

    assert len(calls) == 2


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is POSIX-only")
def test_atomic_write_tolerates_unsupported_directory_fsync(
    tmp_path: Path, monkeypatch
) -> None:
    import errno

    calls = 0
    real_fsync = atomic_write.os.fsync

    def unsupported_directory_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EINVAL, "directory fsync unsupported")
        real_fsync(fd)

    monkeypatch.setattr(atomic_write.os, "fsync", unsupported_directory_fsync)
    destination = tmp_path / ".env"

    atomic_write.atomic_write_text(destination, "SECRET=new\n")

    assert destination.read_text(encoding="utf-8") == "SECRET=new\n"
    assert calls == 2


def test_private_backups_are_exclusive_unique_and_mode_clamped(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".env"
    source.write_bytes(b"SECRET=value\r\nSECOND=line\r\n")
    os.chmod(source, 0o644)

    first = atomic_write.create_private_backup(source, version="v2")
    second = atomic_write.create_private_backup(source, version="v2")

    assert first != second
    assert first.name.startswith(".env.backup.v2.")
    assert second.name.startswith(".env.backup.v2.")
    assert first.read_bytes() == source.read_bytes()
    assert second.read_bytes() == source.read_bytes()
    assert os.stat(first).st_mode & 0o777 == 0o600
    assert os.stat(second).st_mode & 0o777 == 0o600


def test_private_backups_are_pruned_to_the_retention_cap(tmp_path: Path) -> None:
    """Every base-port change, key rotation and env migration snapshots `.env`.

    Without a cap those accumulate without bound, each holding the Supabase JWT
    signing keys, so a rotated secret stays readable on disk indefinitely.
    """
    source = tmp_path / ".env"
    source.write_text("SECRET=value\n", encoding="utf-8")

    made = [
        atomic_write.create_private_backup(source, keep=3)
        for _ in range(7)
    ]

    surviving = sorted(tmp_path.glob(".env.backup.*"))
    assert len(surviving) == 3, surviving
    # Every survivor is one of the snapshots actually created, and the newest
    # one is always retained. (Snapshots written inside the same second share an
    # mtime, so which of *those* survives is deliberately unspecified — only the
    # count and the newest are contractual.)
    assert set(surviving) <= set(made)
    assert made[-1] in surviving
    # Pruning never damages the source or the snapshot contents.
    assert source.read_text(encoding="utf-8") == "SECRET=value\n"
    for path in surviving:
        assert path.read_text(encoding="utf-8") == "SECRET=value\n"
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_private_backup_pruning_is_scoped_to_its_version_prefix(tmp_path: Path) -> None:
    """A v1 migration snapshot must not evict the unversioned rotation history."""
    source = tmp_path / ".env"
    source.write_text("SECRET=value\n", encoding="utf-8")

    plain = [atomic_write.create_private_backup(source, keep=2) for _ in range(2)]
    versioned = [
        atomic_write.create_private_backup(source, version="v1", keep=2)
        for _ in range(3)
    ]

    assert all(p.exists() for p in plain)
    assert len(list(tmp_path.glob(".env.backup.v1.*"))) == 2
    assert len(versioned) == 3


def test_private_backup_retention_can_be_disabled(tmp_path: Path) -> None:
    source = tmp_path / ".env"
    source.write_text("SECRET=value\n", encoding="utf-8")

    for _ in range(4):
        atomic_write.create_private_backup(source, keep=-1)

    assert len(list(tmp_path.glob(".env.backup.*"))) == 4
