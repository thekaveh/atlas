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
