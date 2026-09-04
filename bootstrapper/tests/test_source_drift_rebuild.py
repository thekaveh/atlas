"""#506: a normal (warm) start after an in-place Atlas source/submodule upgrade
must rebuild stale local-build images before recreating containers.

`docker compose up --force-recreate` recreates containers but reuses the
existing locally-named image even when its Dockerfile/context changed with a
pin bump — so e.g. a pre-Celery backend image runs old code and crash-loops
(`ModuleNotFoundError: No module named 'celery'`). The fix gates `--build` on
the Atlas source commit, Compose-resolved local build configuration, and actual
target set recorded in a per-deployment marker. Ordinary unchanged restarts
stay fast; buildkit's content cache means only contexts that actually changed
rebuild when a build does run.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _drift_manager(monkeypatch, tmp_path, commit, build_digest="build-v1"):
    """A DockerManager whose marker lives under tmp_path and whose source
    commit is `commit` (None simulates a non-git checkout)."""
    from core.docker_manager import DockerManager

    manager = DockerManager(str(REPO_ROOT))
    monkeypatch.setattr(manager, "root_dir", Path(tmp_path))
    monkeypatch.setattr(manager, "_current_source_commit", lambda: commit)
    monkeypatch.setattr(manager, "_current_build_config_digest", lambda: build_digest)
    return manager


def _write_marker(manager, commit, build_digest="build-v1", targets=None):
    manager._source_marker_path().write_text(
        json.dumps(
            {
                "version": 2,
                "source_commit": commit,
                "build_config_sha256": build_digest,
                "targets": sorted(set(targets)) if targets else None,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_marker(manager):
    return json.loads(manager._source_marker_path().read_text(encoding="utf-8"))


def test_no_forced_build_when_source_unchanged(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="abc123")
    _write_marker(manager, "abc123")
    assert manager.pending_source_rebuild() is False
    assert manager.source_build_args() == []


def test_forced_build_when_commit_changed(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="new456")
    _write_marker(manager, "old123")
    assert manager.pending_source_rebuild() is True
    assert manager.source_build_args() == ["--build"]


def test_forced_build_when_marker_absent(monkeypatch, tmp_path):
    # Fresh checkout with no prior build record → build.
    manager = _drift_manager(monkeypatch, tmp_path, commit="abc123")
    assert not manager._source_marker_path().exists()
    assert manager.pending_source_rebuild() is True
    assert manager.source_build_args() == ["--build"]


def test_build_digest_protects_non_git_release_checkout(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit=None)

    assert manager.pending_source_rebuild() is True
    manager.mark_source_built()
    assert manager.pending_source_rebuild() is False
    assert manager.source_build_args() == []

    monkeypatch.setattr(
        manager, "_current_build_config_digest", lambda: "release-build-v2"
    )
    assert manager.source_build_args() == ["--build"]


def test_mark_source_built_records_current_commit(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="deadbeef")
    manager.mark_source_built()
    assert _read_marker(manager) == {
        "build_config_sha256": "build-v1",
        "source_commit": "deadbeef",
        "targets": None,
        "version": 2,
    }
    # Recording clears the drift.
    assert manager.pending_source_rebuild() is False


def test_mark_source_built_records_digest_when_not_git(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit=None)
    manager.mark_source_built()
    assert _read_marker(manager) == {
        "build_config_sha256": "build-v1",
        "source_commit": None,
        "targets": None,
        "version": 2,
    }


def test_start_services_rebuilds_and_marks_on_drift(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="new456")
    _write_marker(manager, "old123", targets=["backend"])
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
    assert _read_marker(manager)["source_commit"] == "new456"


def test_start_services_skips_build_when_unchanged(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="same789")
    _write_marker(manager, "same789", targets=["backend"])
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
    _write_marker(manager, "old123", targets=["backend"])
    monkeypatch.setattr(manager, "execute_compose_command", lambda args: 1)
    monkeypatch.setattr(manager, "enabled_service_targets", lambda: ["backend"])

    rc = manager.start_services(detached=True)

    assert rc == 1
    assert _read_marker(manager)["source_commit"] == "old123"


def test_forced_build_when_resolved_build_config_changes(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="same789")
    _write_marker(manager, "same789", build_digest="mlflow-3.15.1")

    monkeypatch.setattr(
        manager, "_current_build_config_digest", lambda: "mlflow-3.15.2"
    )

    assert manager.pending_source_rebuild() is True
    assert manager.source_build_args() == ["--build"]


def test_legacy_commit_only_marker_forces_one_upgrade_build(monkeypatch, tmp_path):
    manager = _drift_manager(monkeypatch, tmp_path, commit="same789")
    manager._source_marker_path().write_text("same789\n", encoding="utf-8")

    assert manager.pending_source_rebuild() is True


def test_unavailable_build_config_fails_safe_to_rebuild(monkeypatch, tmp_path):
    manager = _drift_manager(
        monkeypatch, tmp_path, commit="same789", build_digest=None
    )
    _write_marker(manager, "same789")

    assert manager.pending_source_rebuild() is True
    manager.mark_source_built()
    assert _read_marker(manager)["build_config_sha256"] == "build-v1"


def test_build_config_command_failure_is_indeterminate(monkeypatch) -> None:
    from core.docker_manager import DockerManager

    manager = DockerManager(str(REPO_ROOT))
    monkeypatch.setattr(
        manager,
        "_build_compose_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no compose")),
    )

    assert manager._current_build_config_digest() is None


def test_build_config_digest_tracks_resolved_local_build_args(monkeypatch):
    from core import docker_manager as docker_manager_module
    from core.docker_manager import DockerManager

    manager = DockerManager(str(REPO_ROOT))
    rendered = {
        "services": {
            "mlflow": {
                "build": {
                    "context": "/atlas/services/mlflow",
                    "args": {"BASE_IMAGE": "$MLFLOW_IMAGE"},
                },
                "image": "atlas-mlflow:local",
                "environment": {"MLFLOW_TRACKING_TOKEN": "must-not-affect-digest"},
            }
        }
    }

    commands: list[list[str]] = []

    def compose_result(cmd, **_kwargs):
        from subprocess import CompletedProcess

        commands.append(list(cmd))
        resolved = json.loads(json.dumps(rendered))
        resolved["services"]["mlflow"]["build"]["args"]["BASE_IMAGE"] = (
            os.environ["MLFLOW_IMAGE"]
        )
        return CompletedProcess([], 0, stdout=json.dumps(resolved), stderr="")

    monkeypatch.setattr(docker_manager_module, "run_with_deadline", compose_result)
    monkeypatch.setenv("MLFLOW_IMAGE", "ghcr.io/mlflow/mlflow:v3.15.1")
    first = manager._current_build_config_digest()
    monkeypatch.setenv("MLFLOW_IMAGE", "ghcr.io/mlflow/mlflow:v3.15.2")
    second = manager._current_build_config_digest()

    assert first is not None
    assert second is not None
    assert first != second
    assert all("--no-interpolate" not in command for command in commands)


def test_build_config_digest_tracks_target_dependency_closure(monkeypatch) -> None:
    from core import docker_manager as docker_manager_module
    from core.docker_manager import DockerManager

    manager = DockerManager(str(REPO_ROOT))
    rendered = {
        "services": {
            "backend": {"build": {"context": "backend"}},
            "mlflow": {"build": {"context": "mlflow"}},
        }
    }

    def compose_result(_cmd, **_kwargs):
        from subprocess import CompletedProcess

        return CompletedProcess([], 0, stdout=json.dumps(rendered), stderr="")

    monkeypatch.setattr(docker_manager_module, "run_with_deadline", compose_result)
    before = manager._current_build_config_digest()
    rendered["services"]["backend"]["depends_on"] = {"mlflow": {}}
    after = manager._current_build_config_digest()

    assert before is not None
    assert after is not None
    assert before != after


def test_warm_start_rebuilds_after_mlflow_image_change(monkeypatch, tmp_path):
    from core import docker_manager as docker_manager_module
    from core.docker_manager import DockerManager

    manager = DockerManager(str(REPO_ROOT))
    monkeypatch.setattr(manager, "root_dir", Path(tmp_path))
    monkeypatch.setattr(manager, "_current_source_commit", lambda: "same789")
    rendered = {
        "services": {
            "mlflow": {
                "build": {
                    "context": "/atlas/services/mlflow",
                    "args": {
                        "BASE_IMAGE": "${MLFLOW_IMAGE:-ghcr.io/mlflow/mlflow:v3.15.1}"
                    },
                },
                "image": "atlas-mlflow:local",
            }
        }
    }
    renders: list[None] = []

    def compose_result(_cmd, **_kwargs):
        from subprocess import CompletedProcess

        renders.append(None)
        resolved = json.loads(json.dumps(rendered))
        resolved["services"]["mlflow"]["build"]["args"]["BASE_IMAGE"] = (
            os.environ["MLFLOW_IMAGE"]
        )
        return CompletedProcess([], 0, stdout=json.dumps(resolved), stderr="")

    monkeypatch.setattr(docker_manager_module, "run_with_deadline", compose_result)
    monkeypatch.setenv("MLFLOW_IMAGE", "ghcr.io/mlflow/mlflow:v3.15.1")
    manager.mark_source_built(["mlflow"])
    monkeypatch.setenv("MLFLOW_IMAGE", "ghcr.io/mlflow/mlflow:v3.15.2")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        manager, "execute_compose_command", lambda args: (calls.append(list(args)) or 0)
    )
    monkeypatch.setattr(manager, "enabled_service_targets", lambda: ["mlflow"])

    assert manager.start_services() == 0
    assert "--build" in calls[0]
    assert len(renders) == 2  # initial marker + one pre-build freshness render
    assert manager.pending_source_rebuild(["mlflow"]) is False


def test_disabled_build_change_cannot_mark_future_mlflow_target_fresh(
    monkeypatch, tmp_path
) -> None:
    manager = _drift_manager(
        monkeypatch, tmp_path, commit="same789", build_digest="mlflow-3.15.1"
    )
    _write_marker(
        manager, "same789", build_digest="mlflow-3.15.1", targets=["backend"]
    )
    monkeypatch.setattr(
        manager, "_current_build_config_digest", lambda: "mlflow-3.15.2"
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        manager, "execute_compose_command", lambda args: (calls.append(list(args)) or 0)
    )

    assert manager.start_services(services=["backend"]) == 0
    assert "--build" in calls[-1]
    assert _read_marker(manager)["targets"] == ["backend"]

    assert manager.source_build_args(["backend", "mlflow"]) == ["--build"]


def test_cold_build_marks_only_its_pre_build_state(monkeypatch, tmp_path) -> None:
    manager = _drift_manager(
        monkeypatch, tmp_path, commit="same789", build_digest="mlflow-3.15.1"
    )
    manager.capture_build_state(["mlflow"])
    monkeypatch.setattr(
        manager, "_current_build_config_digest", lambda: "mlflow-3.15.2"
    )

    manager.mark_source_built(["mlflow"])

    assert _read_marker(manager)["build_config_sha256"] == "mlflow-3.15.1"
    assert manager.pending_source_rebuild(["mlflow"]) is True


def test_captured_build_state_cannot_be_marked_for_different_targets(
    monkeypatch, tmp_path
) -> None:
    manager = _drift_manager(monkeypatch, tmp_path, commit="same789")
    manager.capture_build_state(["mlflow"])

    manager.mark_source_built(["backend"])

    assert not manager._source_marker_path().exists()


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
    manager._current_build_config_digest = lambda: "live-build-config"  # type: ignore[method-assign]
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
