"""#506: a normal (warm) start after an in-place Atlas source/submodule upgrade
must rebuild stale local-build images before recreating containers.

`docker compose up --force-recreate` recreates containers but reuses the
existing locally-named image even when its Dockerfile/context changed with a
pin bump — so e.g. a pre-Celery backend image runs old code and crash-loops
(`ModuleNotFoundError: No module named 'celery'`). The fix gates `--build` on
the selected Atlas source commit having changed since local images were last
built here, recorded in a per-deployment marker. Ordinary restarts (unchanged
commit) stay fast; buildkit's content cache means only contexts that actually
changed rebuild when a build does run.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _drift_manager(monkeypatch, tmp_path, commit):
    """A DockerManager whose marker lives under tmp_path and whose source
    commit is `commit` (None simulates a non-git checkout)."""
    from core.docker_manager import DockerManager

    manager = DockerManager(str(REPO_ROOT))
    monkeypatch.setattr(manager, "root_dir", Path(tmp_path))
    monkeypatch.setattr(manager, "_current_source_commit", lambda: commit)
    return manager


def test_no_forced_build_when_source_unchanged(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="abc123")
    manager._source_marker_path().write_text("abc123\n", encoding="utf-8")
    assert manager.pending_source_rebuild() is False
    assert manager.source_build_args() == []


def test_forced_build_when_commit_changed(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="new456")
    manager._source_marker_path().write_text("old123\n", encoding="utf-8")
    assert manager.pending_source_rebuild() is True
    assert manager.source_build_args() == ["--build"]


def test_forced_build_when_marker_absent(monkeypatch, tmp_path):
    # Fresh checkout with no prior build record → build.
    manager = _drift_manager(monkeypatch, tmp_path, commit="abc123")
    assert not manager._source_marker_path().exists()
    assert manager.pending_source_rebuild() is True
    assert manager.source_build_args() == ["--build"]


def test_no_forced_build_when_not_a_git_checkout(monkeypatch, tmp_path):
    # Indeterminate commit (not git) → behave as before, never force a rebuild
    # every start on non-git deployments.
    manager = _drift_manager(monkeypatch, tmp_path, commit=None)
    (tmp_path / manager.SOURCE_BUILD_MARKER).write_text("whatever\n", encoding="utf-8")
    assert manager.pending_source_rebuild() is False
    assert manager.source_build_args() == []


def test_mark_source_built_records_current_commit(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="deadbeef")
    manager.mark_source_built()
    assert manager._source_marker_path().read_text(encoding="utf-8").strip() == "deadbeef"
    # Recording clears the drift.
    assert manager.pending_source_rebuild() is False


def test_mark_source_built_noop_when_not_git(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit=None)
    manager.mark_source_built()  # must not raise or create a marker
    assert not manager._source_marker_path().exists()


def test_start_services_rebuilds_and_marks_on_drift(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="new456")
    manager._source_marker_path().write_text("old123\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(manager, "execute_compose_command",
                        lambda args: (calls.append(list(args)) or 0))
    monkeypatch.setattr(manager, "enabled_service_targets", lambda: ["backend"])

    rc = manager.start_services(detached=True)

    assert rc == 0
    up = calls[0]
    assert up[:3] == ["up", "-d", "--force-recreate"]
    assert "--build" in up
    assert up[-1] == "backend"
    # Marker advanced to the new commit → next start won't rebuild.
    assert manager._source_marker_path().read_text(encoding="utf-8").strip() == "new456"


def test_start_services_skips_build_when_unchanged(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="same789")
    manager._source_marker_path().write_text("same789\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(manager, "execute_compose_command",
                        lambda args: (calls.append(list(args)) or 0))
    monkeypatch.setattr(manager, "enabled_service_targets", lambda: ["backend"])

    manager.start_services(detached=True)

    assert "--build" not in calls[0]


def test_start_services_does_not_advance_marker_on_failure(monkeypatch, tmp_path):
    # A failed up (rc != 0) must leave the marker unchanged so the next start
    # retries the rebuild rather than silently running stale images.
    manager = _drift_manager(monkeypatch, tmp_path, commit="new456")
    manager._source_marker_path().write_text("old123\n", encoding="utf-8")
    monkeypatch.setattr(manager, "execute_compose_command", lambda args: 1)
    monkeypatch.setattr(manager, "enabled_service_targets", lambda: ["backend"])

    rc = manager.start_services(detached=True)

    assert rc == 1
    assert manager._source_marker_path().read_text(encoding="utf-8").strip() == "old123"


# --------------------------------------------------------------------------- #
# AC#6: docker-gated end-to-end proof. Builds an "old" image, changes the
# build context under a new source commit, and proves the drift gate flips
# (real git HEAD) and that the rebuild yields the new content. Opt-in:
#   ATLAS_DOCKER_LIVE=1 uv run pytest -q -m live tests/test_source_drift_rebuild.py
# --------------------------------------------------------------------------- #
import os
import shutil
import subprocess

import pytest


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.mark.live
def test_source_change_triggers_rebuild_with_new_content(tmp_path):
    if not os.environ.get("ATLAS_DOCKER_LIVE"):
        pytest.skip("set ATLAS_DOCKER_LIVE=1 (needs docker + git) for the #506 rebuild proof")
    if shutil.which("docker") is None or shutil.which("git") is None:
        pytest.skip("docker + git required")

    from core.docker_manager import DockerManager

    root = tmp_path
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    svc = root / "svc"
    svc.mkdir()
    (svc / "dep.txt").write_text("v1\n")
    (svc / "Dockerfile").write_text(
        "FROM busybox\nCOPY dep.txt /dep.txt\nCMD [\"cat\", \"/dep.txt\"]\n"
    )
    (root / "f").write_text("a")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "A")

    manager = DockerManager(str(root))
    tag = "atlas506probe:local"

    def build():
        subprocess.run(["docker", "build", "-t", tag, str(svc)], check=True,
                       capture_output=True, text=True)

    def run_output():
        return subprocess.run(["docker", "run", "--rm", tag],
                              check=True, capture_output=True, text=True).stdout.strip()

    try:
        # First build reflects the "old" dependency, and we record the marker.
        build()
        manager.mark_source_built()
        assert run_output() == "v1"
        assert manager.pending_source_rebuild() is False        # unchanged commit
        assert manager.source_build_args() == []

        # A source change (new commit) that alters the build context.
        (svc / "dep.txt").write_text("v2\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "B")

        # The drift gate flips against the REAL new git HEAD.
        assert manager.pending_source_rebuild() is True
        assert manager.source_build_args() == ["--build"]

        # The rebuild the warm start now performs yields the new dependency.
        build()
        assert run_output() == "v2"

        manager.mark_source_built()
        assert manager.pending_source_rebuild() is False        # settled again
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, text=True)
