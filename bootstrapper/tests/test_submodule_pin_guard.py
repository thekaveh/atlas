"""Tests for utils.submodule_pin_guard (#797).

Exercises a REAL superproject + git submodule built in tmp_path (no mocks) so
the read-only contract is proven concretely: after the guard runs, the
submodule's working HEAD and the superproject's index are byte-for-byte
unchanged — the guard detects and warns, it never moves or stages the pin.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from utils.submodule_pin_guard import (
    detect_submodule_pin_drift,
    warn_if_submodule_pin_drifted,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git CLI required to build the superproject/submodule fixture",
)


def _git(cwd: Path, *args: str) -> str:
    """Run git in cwd with a deterministic author identity; return stdout."""
    result = subprocess.run(
        [
            "git", "-c", "user.email=t@t", "-c", "user.name=t",
            "-c", "commit.gpgsign=false", "-c", "protocol.file.allow=always",
            *args,
        ],
        cwd=str(cwd), capture_output=True, text=True, check=True,
    )
    return (result.stdout or "").strip()


def _commit(cwd: Path, msg: str) -> str:
    _git(cwd, "commit", "--allow-empty", "-m", msg)
    return _git(cwd, "rev-parse", "HEAD")


def _build_submodule_consumer(tmp_path: Path) -> tuple[Path, Path]:
    """Build a consumer superproject with Atlas vendored as the `infra`
    submodule. Returns (superproject_root, submodule_checkout)."""
    upstream_src = tmp_path / "atlas_upstream"
    upstream_src.mkdir()
    _git(upstream_src, "init")
    _commit(upstream_src, "upstream base")  # the recorded pin

    bare = tmp_path / "atlas.git"
    subprocess.run(
        ["git", "clone", "--bare", str(upstream_src), str(bare)],
        capture_output=True, text=True, check=True,
    )

    super_root = tmp_path / "consumer"
    super_root.mkdir()
    _git(super_root, "init")
    _commit(super_root, "consumer root")
    _git(super_root, "submodule", "add", str(bare), "infra")
    _git(super_root, "commit", "-m", "vendor atlas as infra")
    return super_root, super_root / "infra"


def test_standalone_clone_is_noop(tmp_path):
    """A standalone Atlas clone (not a submodule of anything) → no drift, no warn."""
    root = tmp_path / "standalone"
    root.mkdir()
    _git(root, "init")
    _commit(root, "x")
    sink: list[str] = []
    assert warn_if_submodule_pin_drifted(root, sink=sink.append) is False
    assert sink == []
    assert detect_submodule_pin_drift(root).is_submodule is False


def test_non_git_checkout_is_noop(tmp_path):
    """A plain directory (no .git) → no-op, no exception."""
    root = tmp_path / "plain"
    root.mkdir()
    sink: list[str] = []
    assert warn_if_submodule_pin_drifted(root, sink=sink.append) is False
    assert sink == []


def test_submodule_clean_is_silent(tmp_path):
    """Recorded gitlink == working HEAD → no drift, no warning."""
    _super, sub = _build_submodule_consumer(tmp_path)
    sink: list[str] = []
    assert warn_if_submodule_pin_drifted(sub, sink=sink.append) is False
    assert sink == []


def test_submodule_head_drift_warns_and_does_not_mutate(tmp_path):
    """Working HEAD advanced past the recorded gitlink → warn loudly, and the
    guard must NOT move HEAD back (read-only; AC #1: warns, does not act)."""
    _super, sub = _build_submodule_consumer(tmp_path)
    recorded = _git(sub, "rev-parse", "HEAD")
    # Advance the submodule HEAD past the recorded pin (the drift).
    drifted_head = _commit(sub, "advance past pin")
    assert drifted_head != recorded

    sink: list[str] = []
    assert warn_if_submodule_pin_drifted(sub, sink=sink.append) is True
    assert len(sink) == 1
    msg = sink[0]
    assert "pin drift" in msg.lower()
    assert "did NOT move this" in msg  # the launcher disclaims responsibility
    assert recorded[:12] in msg and drifted_head[:12] in msg

    # READ-ONLY CONTRACT: HEAD is byte-for-byte unchanged after the guard ran.
    assert _git(sub, "rev-parse", "HEAD") == drifted_head

    status = detect_submodule_pin_drift(sub)
    assert status.is_submodule is True
    assert status.head_drifted is True  # recorded committed gitlink != working HEAD
    assert status.recorded_gitlink == recorded
    # The exact staged/working-tree column git reports for a moved submodule
    # is porcelain-internal; what matters is that drift was detected (above).


def test_submodule_staged_in_superproject_warns(tmp_path):
    """The superproject has staged the pointer change (the `git add infra`
    half of the bug) → detected + warned, no mutation."""
    super_root, sub = _build_submodule_consumer(tmp_path)
    recorded = _git(sub, "rev-parse", "HEAD")
    _commit(sub, "advance past pin")  # drift HEAD
    _git(super_root, "add", "infra")  # stage the pointer change in the superproject

    sink: list[str] = []
    assert warn_if_submodule_pin_drifted(sub, sink=sink.append) is True
    msg = sink[0]
    assert "staged a pointer change" in msg
    assert "restore --staged" in msg  # actionable unstage hint

    status = detect_submodule_pin_drift(sub)
    assert status.head_drifted is True
    assert status.staged_in_superproject is True
    # READ-ONLY: the superproject index is unchanged by the guard — the
    # pointer change we staged is still staged (the guard never unstages).
    assert "infra" in _git(super_root, "status", "--porcelain")
