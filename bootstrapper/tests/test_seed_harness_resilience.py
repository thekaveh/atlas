"""Network-resilience contracts for the Docker-backed seed harness."""

from __future__ import annotations

import subprocess

from tests import seed_harness


def test_docker_available_checks_reachable_daemon(monkeypatch) -> None:
    monkeypatch.setattr(seed_harness.shutil, "which", lambda _command: "/usr/bin/docker")
    monkeypatch.setattr(
        seed_harness.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )

    assert seed_harness.docker_available() is True


def test_seed_harness_retries_transient_image_pull(monkeypatch) -> None:
    calls: list[list[str]] = []
    results = iter(
        (
            subprocess.CompletedProcess([], 1, b"", b"missing"),
            subprocess.CompletedProcess([], 1, b"", b"registry timeout"),
            subprocess.CompletedProcess([], 0, b"pulled", b""),
        )
    )

    def run(command, **kwargs):
        calls.append(command)
        return next(results)

    monkeypatch.setattr(seed_harness.subprocess, "run", run)
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)

    seed_harness.ensure_database_image()

    assert calls == [
        ["docker", "image", "inspect", seed_harness.DB_IMAGE],
        ["docker", "pull", seed_harness.DB_IMAGE],
        ["docker", "pull", seed_harness.DB_IMAGE],
    ]


def test_seed_harness_surfaces_final_pull_failure(monkeypatch) -> None:
    results = iter(
        [subprocess.CompletedProcess([], 1, b"", b"missing")]
        + [subprocess.CompletedProcess([], 1, b"", b"registry timeout")] * 3
    )
    monkeypatch.setattr(seed_harness.subprocess, "run", lambda *_args, **_kwargs: next(results))
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)

    try:
        seed_harness.ensure_database_image()
    except subprocess.CalledProcessError as exc:
        assert exc.cmd == ["docker", "pull", seed_harness.DB_IMAGE]
        assert exc.stderr == b"registry timeout"
    else:  # pragma: no cover - makes a missing exception explicit
        raise AssertionError("final Docker pull failure was swallowed")
