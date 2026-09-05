"""An interrupted bring-up must say the build may outlive it (#1001).

`docker compose up --build` drives BuildKit inside the Docker daemon, not in the
start.sh process tree. A build that completes after the abort creates containers
for a run the operator already abandoned — including services a later run
disabled — which is indistinguishable from Atlas ignoring a source toggle. The
reporter filed exactly that bug before the timestamps showed otherwise.
"""
from __future__ import annotations

import subprocess

import pytest

from core.docker_manager import DockerManager


def _manager(messages: list[str]) -> DockerManager:
    manager = DockerManager.__new__(DockerManager)
    manager._on_command = messages.append  # type: ignore[attr-defined]
    return manager


def test_interrupt_report_names_the_daemon_and_the_project() -> None:
    messages: list[str] = []
    _manager(messages)._report_interrupted_compose("emporion")

    joined = "\n".join(messages)
    assert "Docker daemon" in joined
    assert "may still be running" in joined
    # The operator needs the exact filter, not a suggestion to "check docker".
    assert "com.docker.compose.project=emporion" in joined


def test_execute_compose_command_reports_then_propagates_the_interrupt(
    monkeypatch, tmp_path
) -> None:
    """The interrupt must still reach the caller; reporting is not swallowing."""
    messages: list[str] = []
    manager = _manager(messages)
    manager.root_dir = tmp_path  # type: ignore[attr-defined]
    manager.project_name_override = "atlas-test"  # type: ignore[attr-defined]
    monkeypatch.setattr(manager, "detect_docker_compose_command", lambda: "docker compose")
    monkeypatch.setattr(
        manager, "_validated_compose_file_args", lambda *a, **k: ([], False)
    )
    manager.config_parser = type(  # type: ignore[attr-defined]
        "_CP", (), {"env_file_exists": staticmethod(lambda: False)}
    )()

    def _interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("core.docker_manager.subprocess.run", _interrupted)

    with pytest.raises(KeyboardInterrupt):
        manager.execute_compose_command(["up", "-d"])

    assert any("may still be running" in message for message in messages)


def test_normal_completion_emits_no_interrupt_warning(monkeypatch, tmp_path) -> None:
    """The warning must not fire on an ordinary run, or it stops meaning anything."""
    messages: list[str] = []
    manager = _manager(messages)
    manager.root_dir = tmp_path  # type: ignore[attr-defined]
    manager.project_name_override = "atlas-test"  # type: ignore[attr-defined]
    monkeypatch.setattr(manager, "detect_docker_compose_command", lambda: "docker compose")
    monkeypatch.setattr(
        manager, "_validated_compose_file_args", lambda *a, **k: ([], False)
    )
    manager.config_parser = type(  # type: ignore[attr-defined]
        "_CP", (), {"env_file_exists": staticmethod(lambda: False)}
    )()
    monkeypatch.setattr(
        "core.docker_manager.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0),
    )

    assert manager.execute_compose_command(["up", "-d"]) == 0
    assert not any("may still be running" in message for message in messages)
