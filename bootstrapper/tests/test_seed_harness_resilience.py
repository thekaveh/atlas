"""Local-image contracts for the Docker-backed seed harness."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from tests import seed_harness
from tests import test_database_role_boundaries as role_boundaries
from tests import test_memory_pgvector_dimension_migration as memory_migration
from tests import test_postgres_restore_safety as restore_safety


ROLE_DAEMON_PROBE_FAILURES = (
    pytest.param(
        subprocess.CompletedProcess(
            ("docker", "info"),
            1,
            "",
            "permission denied while connecting to Docker socket",
        ),
        id="nonzero",
    ),
    pytest.param(
        subprocess.TimeoutExpired(("docker", "info"), 10),
        id="timeout",
    ),
    pytest.param(PermissionError("docker socket denied"), id="launch-error"),
)
ROLE_IMAGE_PROBE_FAILURES = (
    pytest.param(
        subprocess.TimeoutExpired(("docker", "image", "inspect"), 10),
        id="timeout",
    ),
    pytest.param(PermissionError("Docker image metadata denied"), id="launch-error"),
)


def _run_failed_role_runtime_probe(
    monkeypatch: pytest.MonkeyPatch, probe_failure: object, *, in_ci: bool
) -> None:
    if in_ci:
        monkeypatch.setenv("CI", "true")
    else:
        monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(role_boundaries.shutil, "which", lambda _name: "/usr/bin/docker")

    def failed_probe(*_args, **_kwargs):
        if isinstance(probe_failure, BaseException):
            raise probe_failure
        return probe_failure

    monkeypatch.setattr(role_boundaries, "_run", failed_probe)
    role_boundaries._require_disposable_postgres_runtime()


@pytest.mark.parametrize("probe_failure", ROLE_DAEMON_PROBE_FAILURES)
def test_role_runtime_skips_any_failed_daemon_probe_outside_ci(
    monkeypatch, probe_failure
) -> None:
    with pytest.raises(pytest.skip.Exception, match="Docker daemon unavailable"):
        _run_failed_role_runtime_probe(monkeypatch, probe_failure, in_ci=False)


@pytest.mark.parametrize("probe_failure", ROLE_DAEMON_PROBE_FAILURES)
def test_role_runtime_failed_daemon_probe_remains_fatal_in_ci(
    monkeypatch, probe_failure
) -> None:
    with pytest.raises(pytest.fail.Exception, match="Docker daemon probe failed"):
        _run_failed_role_runtime_probe(monkeypatch, probe_failure, in_ci=True)


@pytest.mark.parametrize("probe_failure", ROLE_IMAGE_PROBE_FAILURES)
@pytest.mark.parametrize("image", (role_boundaries.POSTGRES_IMAGE, role_boundaries.INIT_IMAGE))
def test_role_runtime_normalizes_image_probe_launch_failures(
    monkeypatch, image, probe_failure
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(role_boundaries.shutil, "which", lambda _name: "/usr/bin/docker")

    def failed_image_probe(*args, **_kwargs):
        if args[1:3] == ("image", "inspect") and args[3] == image:
            raise probe_failure
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(role_boundaries, "_run", failed_image_probe)
    with pytest.raises(pytest.fail.Exception, match=f"Docker image probe failed for {image}"):
        role_boundaries._require_disposable_postgres_runtime()


@pytest.mark.parametrize("probe_failure", ROLE_IMAGE_PROBE_FAILURES)
def test_restore_safety_normalizes_image_probe_launch_failures(
    monkeypatch, probe_failure
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(restore_safety.shutil, "which", lambda _name: "/usr/bin/docker")

    def failed_image_probe(*args, **_kwargs):
        if args[1:3] == ("image", "inspect"):
            raise probe_failure
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(restore_safety, "_run", failed_image_probe)
    fixture = restore_safety.disposable_postgres.__wrapped__()
    with pytest.raises(pytest.fail.Exception, match="Docker image probe failed"):
        next(fixture)


def test_docker_available_checks_reachable_daemon(monkeypatch) -> None:
    monkeypatch.setattr(seed_harness.shutil, "which", lambda _command: "/usr/bin/docker")
    monkeypatch.setattr(
        seed_harness.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )

    assert seed_harness.docker_available() is True


@pytest.mark.parametrize(
    ("docker_path", "outcome"),
    [
        (None, None),
        ("/usr/bin/docker", subprocess.CompletedProcess([], 1)),
    ],
)
def test_docker_available_rejects_missing_cli_or_unreachable_daemon(
    monkeypatch, docker_path, outcome
) -> None:
    monkeypatch.setattr(seed_harness.shutil, "which", lambda _command: docker_path)

    def run(*_args, **_kwargs):
        assert outcome is not None, "docker info must not run without the CLI"
        return outcome

    monkeypatch.setattr(seed_harness.subprocess, "run", run)

    assert seed_harness.docker_available() is False


def test_docker_available_treats_a_daemon_timeout_as_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(seed_harness.shutil, "which", lambda _command: "/usr/bin/docker")

    def run(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(seed_harness.subprocess, "run", run)

    assert seed_harness.docker_available() is False


def test_seed_harness_uses_the_pinned_local_image_without_pulling(monkeypatch) -> None:
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess([], 0, b"present", b"")

    monkeypatch.setattr(seed_harness.subprocess, "run", run)

    seed_harness.ensure_database_image()

    assert calls == [
        ["docker", "image", "inspect", seed_harness.DB_IMAGE],
    ]


def test_seed_harness_surfaces_missing_local_image_without_pulling(monkeypatch) -> None:
    monkeypatch.setattr(
        seed_harness.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, b"", b"missing local image"
        ),
    )

    try:
        seed_harness.ensure_database_image()
    except subprocess.CalledProcessError as exc:
        assert exc.cmd == ["docker", "image", "inspect", seed_harness.DB_IMAGE]
        assert exc.stderr == b"missing local image"
    else:  # pragma: no cover - makes a missing exception explicit
        raise AssertionError("missing local image was swallowed")


@pytest.mark.parametrize("probe_failure", ROLE_IMAGE_PROBE_FAILURES)
def test_seed_harness_normalizes_image_probe_launch_failures(
    monkeypatch, probe_failure
) -> None:
    monkeypatch.setattr(
        seed_harness.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(probe_failure),
    )

    with pytest.raises(pytest.fail.Exception, match="Docker image probe failed for"):
        seed_harness.ensure_database_image()


def _completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _owned_seed_cleanup_runner(calls: list[list[str]], name: str, token: str):
    visible = True

    def run(command, **_kwargs):
        nonlocal visible
        calls.append(command)
        if command[:2] == ["docker", "inspect"]:
            if visible:
                record = {
                    "Name": f"/{name}",
                    "Config": {"Labels": {seed_harness.SEED_OWNER_LABEL: token}},
                }
                return _completed(command, stdout=json.dumps([record]))
            return _completed(command, returncode=1, stderr="not found")
        if command[:3] == ["docker", "ps", "-a"]:
            return _completed(command)
        if command[:3] == ["docker", "rm", "-f"]:
            visible = False
            return _completed(command)
        raise AssertionError(f"unexpected cleanup command: {command}")

    return run


@pytest.mark.parametrize(
    "launch_failure",
    [
        subprocess.TimeoutExpired(["docker", "run"], 30),
        KeyboardInterrupt(),
    ],
)
def test_seed_run_failure_or_interrupt_still_removes_preallocated_container(
    monkeypatch, tmp_path, launch_failure
) -> None:
    calls: list[list[str]] = []
    visible = True
    monkeypatch.setattr(seed_harness, "ensure_database_image", lambda: None)
    monkeypatch.setattr(
        seed_harness.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32)
    )
    ticks = iter((0.0, 0.4, 0.8, 31.2))
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)

    def run(command, **_kwargs):
        nonlocal visible
        calls.append(command)
        if command[:3] == ["docker", "run", "-d"]:
            raise launch_failure
        if command[:2] == ["docker", "inspect"]:
            if visible:
                record = {
                    "Name": "/atlas-seedtest-aaaaaaaaaaaa",
                    "Config": {
                        "Labels": {seed_harness.SEED_OWNER_LABEL: "a" * 32}
                    },
                }
                return _completed(command, stdout=json.dumps([record]))
            return _completed(command, returncode=1, stderr="not found")
        if command[:3] == ["docker", "ps", "-a"]:
            return _completed(command)
        if command[:3] == ["docker", "rm", "-f"]:
            visible = False
            return _completed(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(seed_harness.subprocess, "run", run)

    with pytest.raises(type(launch_failure)):
        seed_harness.run_scripts_and_dump(tmp_path)

    assert calls[0][:3] == ["docker", "run", "-d"]
    assert f"{seed_harness.SEED_OWNER_LABEL}={'a' * 32}" in calls[0]
    cleanup = [call for call in calls if call[:3] == ["docker", "rm", "-f"]]
    assert cleanup == [["docker", "rm", "-f", "atlas-seedtest-aaaaaaaaaaaa"]]


def test_seed_name_collision_never_removes_a_foreign_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    removals: list[list[str]] = []
    monkeypatch.setattr(seed_harness, "ensure_database_image", lambda: None)
    monkeypatch.setattr(
        seed_harness.uuid, "uuid4", lambda: SimpleNamespace(hex="b" * 32)
    )
    ticks = iter((0.0, 0.4, 0.8, 31.2))
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)

    def run(command, **_kwargs):
        if command[:3] == ["docker", "run", "-d"]:
            assert f"{seed_harness.SEED_OWNER_LABEL}={'b' * 32}" in command
            raise subprocess.CalledProcessError(125, command, stderr="name conflict")
        if command[:2] == ["docker", "inspect"]:
            foreign = {
                "Name": "/atlas-seedtest-bbbbbbbbbbbb",
                "Config": {"Labels": {seed_harness.SEED_OWNER_LABEL: "foreign"}},
            }
            return _completed(command, stdout=json.dumps([foreign]))
        if command[:3] == ["docker", "rm", "-f"]:
            removals.append(command)
        return _completed(command)

    monkeypatch.setattr(seed_harness.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        seed_harness.run_scripts_and_dump(tmp_path)
    assert removals == []


def test_cleanup_failure_does_not_mask_primary_error(monkeypatch, capsys) -> None:
    ticks = iter((0.0, 61.0))
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        seed_harness.subprocess,
        "run",
        lambda command, **_kwargs: _completed(
            command, returncode=1, stderr="daemon unavailable"
        ),
    )

    with pytest.raises(ValueError, match="primary"):
        with seed_harness.seed_container_cleanup("seed", "token"):
            raise ValueError("primary")

    assert "could not remove seed container" in capsys.readouterr().err


def test_cleanup_sleep_interruption_does_not_replace_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = ValueError("primary")
    ticks = iter((0.0, 1.0, 31.0))
    sleeps = iter((KeyboardInterrupt("second interrupt"), None))
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: next(ticks))

    def sleep(_seconds):
        outcome = next(sleeps)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(seed_harness.time, "sleep", sleep)
    monkeypatch.setattr(
        seed_harness, "_remove_owned_seed_once", lambda *_args: None
    )

    with pytest.raises(ValueError) as caught:
        with seed_harness.seed_container_cleanup("seed", "token"):
            raise primary

    assert caught.value is primary
    assert "sleep was interrupted" in "\n".join(primary.__notes__)


def test_cleanup_defers_first_sleep_interruption_until_reconciliation_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((0.0, 1.0, 31.0))
    sleeps = iter((KeyboardInterrupt("operator interrupt"), None))
    cleanup_passes: list[str] = []
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: next(ticks))

    def sleep(_seconds):
        outcome = next(sleeps)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(seed_harness.time, "sleep", sleep)
    monkeypatch.setattr(
        seed_harness,
        "_remove_owned_seed_once",
        lambda *_args: cleanup_passes.append("pass"),
    )

    with pytest.raises(KeyboardInterrupt, match="operator interrupt"):
        seed_harness.remove_seed_container("seed", "token", uncertain=True)

    assert cleanup_passes == ["pass", "pass"]


def test_cleanup_defers_certain_resource_interruption_until_reconciled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((0.0, 1.0, 31.0))
    outcomes = iter((KeyboardInterrupt("operation interrupt"), None))
    cleanup_passes: list[str] = []
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)

    def remove(*_args):
        cleanup_passes.append("pass")
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(seed_harness, "_remove_owned_seed_once", remove)

    with pytest.raises(KeyboardInterrupt, match="operation interrupt"):
        seed_harness.remove_seed_container("seed", "token", uncertain=False)

    assert cleanup_passes == ["pass", "pass"]


def test_cleanup_failure_after_success_is_not_silently_ignored(monkeypatch) -> None:
    monkeypatch.setattr(
        seed_harness.subprocess,
        "run",
        lambda command, **_kwargs: _completed(
            command, returncode=1, stderr="permission denied"
        ),
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        with seed_harness.seed_container_cleanup("seed", "token"):
            pass


def test_seed_cleanup_reconciles_owned_container_visible_after_one_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visible = False
    inspections = 0
    removals: list[list[str]] = []
    ticks = iter((0.0, 2.0, 31.0))
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)

    def run(command, **_kwargs):
        nonlocal inspections, visible
        if command[:2] == ["docker", "inspect"]:
            inspections += 1
            if inspections >= 2 and not removals:
                visible = True
            if visible:
                record = {
                    "Name": "/seed",
                    "Config": {
                        "Labels": {seed_harness.SEED_OWNER_LABEL: "token"}
                    },
                }
                return _completed(command, stdout=json.dumps([record]))
            return _completed(command, returncode=1, stderr="not found")
        if command[:3] == ["docker", "ps", "-a"]:
            return _completed(command)
        if command[:3] == ["docker", "rm", "-f"]:
            removals.append(command)
            visible = False
            return _completed(command)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(seed_harness.subprocess, "run", run)
    seed_harness.remove_seed_container("seed", "token", uncertain=True)
    assert removals == [["docker", "rm", "-f", "seed"]]


def test_postgres_wait_requires_both_durable_readiness_markers(monkeypatch) -> None:
    calls: list[list[str]] = []
    log_polls = 0
    sleeps: list[float] = []

    def run(command, **_kwargs):
        nonlocal log_polls
        calls.append(command)
        if command[:3] == ["docker", "exec", "seed"]:
            return _completed(command)
        if command[:2] == ["docker", "logs"]:
            log_polls += 1
            return _completed(
                command,
                stdout="database system is ready to accept connections\n"
                * log_polls,
            )
        if command[:2] == ["docker", "inspect"]:
            return _completed(
                command,
                stdout='{"Status":"running","ExitCode":0,"Error":""}\n',
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(seed_harness.subprocess, "run", run)
    monkeypatch.setattr(seed_harness.time, "sleep", sleeps.append)

    seed_harness.wait_for_postgres("seed", timeout_seconds=1, poll_interval=0)

    assert sum(command[:2] == ["docker", "exec"] for command in calls) == 2
    assert sum(command[:2] == ["docker", "inspect"] for command in calls) == 2
    assert sleeps == [0]


def test_postgres_wait_rejects_readiness_observed_during_terminal_race(
    monkeypatch,
) -> None:
    def run(command, **_kwargs):
        if command[:3] == ["docker", "exec", "seed"]:
            return _completed(command)
        if command[:2] == ["docker", "logs"]:
            return _completed(
                command,
                stdout=(
                    "database system is ready to accept connections\n"
                    "database system is ready to accept connections\n"
                    "fatal after readiness\n"
                ),
            )
        if command[:2] == ["docker", "inspect"]:
            return _completed(
                command,
                stdout='{"Status":"exited","ExitCode":2,"Error":""}\n',
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(seed_harness.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="terminal state 'exited'"):
        seed_harness.wait_for_postgres("seed", timeout_seconds=1)


def test_postgres_wait_stops_immediately_when_container_exits(monkeypatch) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[:3] == ["docker", "exec", "seed"]:
            return _completed(command, returncode=1)
        if command[:2] == ["docker", "logs"]:
            return _completed(command, stdout="booting\nfatal: bad config\n")
        if command[:2] == ["docker", "inspect"]:
            return _completed(
                command,
                stdout='{"Status":"exited","ExitCode":17,"Error":"boom"}\n',
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(seed_harness.subprocess, "run", run)
    monkeypatch.setattr(seed_harness.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError) as exc_info:
        seed_harness.wait_for_postgres("seed", timeout_seconds=180)

    message = str(exc_info.value)
    assert "exited" in message
    assert "exit code 17" in message
    assert "boom" in message
    assert "fatal: bad config" in message
    assert sleeps == []
    assert sum(command[:2] == ["docker", "inspect"] for command in calls) == 1


def test_postgres_wait_bounds_terminal_log_tail(monkeypatch) -> None:
    marker = "the-useful-final-diagnostic"
    noisy_logs = "x" * (seed_harness.CONTAINER_LOG_TAIL_CHARS * 2) + marker

    def run(command, **_kwargs):
        if command[:3] == ["docker", "exec", "seed"]:
            return _completed(command, returncode=1)
        if command[:2] == ["docker", "logs"]:
            return _completed(command, stdout=noisy_logs)
        if command[:2] == ["docker", "inspect"]:
            return _completed(
                command,
                stdout='{"Status":"dead","ExitCode":1,"Error":""}\n',
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(seed_harness.subprocess, "run", run)

    with pytest.raises(RuntimeError) as exc_info:
        seed_harness.wait_for_postgres("seed", timeout_seconds=180)

    message = str(exc_info.value)
    assert marker in message
    assert noisy_logs not in message
    assert len(message) < seed_harness.CONTAINER_LOG_TAIL_CHARS + 300


def test_postgres_wait_reports_timeout_with_last_logs(monkeypatch) -> None:
    now = 0.0
    observed_timeouts: list[float] = []
    sleeps: list[float] = []

    def run(command, **kwargs):
        nonlocal now
        observed_timeouts.append(kwargs["timeout"])
        now += min(0.2, kwargs["timeout"])
        if command[:3] == ["docker", "exec", "seed"]:
            return _completed(command, returncode=1)
        if command[:2] == ["docker", "logs"]:
            return _completed(command, stdout="still starting\n")
        if command[:2] == ["docker", "inspect"]:
            return _completed(
                command,
                stdout='{"Status":"running","ExitCode":0,"Error":""}\n',
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(seed_harness.subprocess, "run", run)
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: now)

    def sleep(duration):
        nonlocal now
        sleeps.append(duration)
        now += duration

    monkeypatch.setattr(seed_harness.time, "sleep", sleep)

    with pytest.raises(RuntimeError) as exc_info:
        seed_harness.wait_for_postgres("seed", timeout_seconds=0.5, poll_interval=1)

    assert "did not become ready in 0.5s" in str(exc_info.value)
    assert "still starting" in str(exc_info.value)
    assert now == pytest.approx(0.5)
    assert sleeps == []
    assert observed_timeouts == pytest.approx([0.5, 0.3, 0.1])


def test_postgres_wait_fails_fast_when_container_state_cannot_be_inspected(
    monkeypatch,
) -> None:
    def run(command, **_kwargs):
        if command[:3] == ["docker", "exec", "seed"]:
            return _completed(command, returncode=1)
        if command[:2] == ["docker", "logs"]:
            return _completed(command, stderr="daemon went away")
        if command[:2] == ["docker", "inspect"]:
            return _completed(command, returncode=1, stderr="cannot connect")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(seed_harness.subprocess, "run", run)

    with pytest.raises(RuntimeError) as exc_info:
        seed_harness.wait_for_postgres("seed", timeout_seconds=180)

    assert "could not inspect" in str(exc_info.value)
    assert "cannot connect" in str(exc_info.value)


@pytest.mark.parametrize(
    "launch_failure",
    [
        subprocess.TimeoutExpired(["docker", "run"], 30),
        KeyboardInterrupt(),
    ],
)
def test_pgvector_fixture_cleans_preallocated_name_when_launch_fails(
    monkeypatch, launch_failure
) -> None:
    cleanup_calls: list[list[str]] = []
    monkeypatch.setattr(memory_migration.seed_harness, "docker_available", lambda: True)
    monkeypatch.setattr(memory_migration.seed_harness, "ensure_database_image", lambda: None)
    monkeypatch.setattr(
        memory_migration.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="memoryfixture0000"),
    )
    ticks = iter((0.0, 0.4, 0.8, 31.2))
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)

    def launch(*args, **_kwargs):
        assert args[:2] == ("run", "-d")
        assert (
            "--label",
            f"{seed_harness.SEED_OWNER_LABEL}=memoryfixture0000",
        ) == args[5:7]
        raise launch_failure

    monkeypatch.setattr(memory_migration, "_docker", launch)
    monkeypatch.setattr(
        seed_harness.subprocess,
        "run",
        _owned_seed_cleanup_runner(
            cleanup_calls, "atlas-memory-dim-memoryfixt", "memoryfixture0000"
        ),
    )

    fixture = memory_migration.disposable_pgvector.__wrapped__()
    with pytest.raises(type(launch_failure)):
        next(fixture)

    assert [call for call in cleanup_calls if call[:3] == ["docker", "rm", "-f"]] == [
        ["docker", "rm", "-f", "atlas-memory-dim-memoryfixt"]
    ]


def test_pgvector_fixture_uses_shared_durable_hard_deadline_wait(monkeypatch) -> None:
    wait_calls: list[tuple[str, float, float]] = []
    cleanup_calls: list[list[str]] = []
    monkeypatch.setattr(memory_migration.seed_harness, "docker_available", lambda: True)
    monkeypatch.setattr(memory_migration.seed_harness, "ensure_database_image", lambda: None)
    monkeypatch.setattr(
        memory_migration.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="memoryfixture0000"),
    )
    ticks = iter((0.0, 0.4, 0.8, 31.2))
    monkeypatch.setattr(seed_harness.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        memory_migration,
        "_docker",
        lambda *args, **_kwargs: _completed(["docker", *args]),
    )

    def wait(name: str, *, timeout_seconds: float, poll_interval: float) -> None:
        wait_calls.append((name, timeout_seconds, poll_interval))
        raise RuntimeError("seed container entered terminal state 'exited'")

    monkeypatch.setattr(memory_migration.seed_harness, "wait_for_postgres", wait)
    monkeypatch.setattr(
        memory_migration.seed_harness.subprocess,
        "run",
        _owned_seed_cleanup_runner(
            cleanup_calls, "atlas-memory-dim-memoryfixt", "memoryfixture0000"
        ),
    )

    fixture = memory_migration.disposable_pgvector.__wrapped__()
    with pytest.raises(RuntimeError, match="terminal state 'exited'"):
        next(fixture)

    assert wait_calls == [("atlas-memory-dim-memoryfixt", 180, 1)]
    assert [call for call in cleanup_calls if call[:3] == ["docker", "rm", "-f"]] == [
        ["docker", "rm", "-f", "atlas-memory-dim-memoryfixt"]
    ]


def test_pgvector_fixture_surfaces_cleanup_failure_after_success(monkeypatch) -> None:
    monkeypatch.setattr(memory_migration.seed_harness, "docker_available", lambda: True)
    monkeypatch.setattr(memory_migration.seed_harness, "ensure_database_image", lambda: None)
    monkeypatch.setattr(
        memory_migration.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="memoryfixture0000"),
    )
    monkeypatch.setattr(
        memory_migration,
        "_docker",
        lambda *args, **_kwargs: _completed(["docker", *args]),
    )
    monkeypatch.setattr(
        memory_migration.seed_harness,
        "wait_for_postgres",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        memory_migration.seed_harness.subprocess,
        "run",
        lambda command, **_kwargs: _completed(
            command, returncode=1, stderr="cleanup denied"
        ),
    )

    fixture = memory_migration.disposable_pgvector.__wrapped__()
    assert next(fixture) == "atlas-memory-dim-memoryfixt"
    with pytest.raises(RuntimeError, match="cleanup denied"):
        next(fixture)


@pytest.mark.parametrize(
    "startup_failure",
    [
        subprocess.TimeoutExpired(["docker", "run"], 60),
        KeyboardInterrupt(),
        RuntimeError("seed container entered terminal state 'exited'"),
    ],
)
def test_role_fixture_cleans_container_and_network_on_ordinary_startup_failure(
    monkeypatch, startup_failure
) -> None:
    cleanup_calls: list[list[str]] = []
    visible = {"container": True, "network": True}
    monkeypatch.setattr(role_boundaries, "_require_disposable_postgres_runtime", lambda: None)
    monkeypatch.setattr(
        role_boundaries.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="rolefixture00000"),
    )
    ticks = iter((0.0, 61.0))
    monkeypatch.setattr(role_boundaries.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(role_boundaries.time, "sleep", lambda _seconds: None)
    def run(*args, **_kwargs):
        command = list(args)
        if command[1:3] == ["container", "inspect"]:
            if not visible["container"]:
                return _completed(command, returncode=1, stderr="not found")
            record = {
                "Name": "/atlas-db-roles-pg-rolefixture0",
                "Config": {
                    "Labels": {
                        role_boundaries.DATABASE_ROLE_OWNER_LABEL: "rolefixture00000"
                    }
                },
            }
            return _completed(command, stdout=json.dumps([record]))
        if command[1:3] == ["network", "inspect"]:
            if not visible["network"]:
                return _completed(command, returncode=1, stderr="not found")
            record = {
                "Name": "atlas-db-roles-rolefixture0",
                "Labels": {
                    role_boundaries.DATABASE_ROLE_OWNER_LABEL: "rolefixture00000"
                },
            }
            return _completed(command, stdout=json.dumps([record]))
        if command[:3] in (
            ["docker", "rm", "-f"],
            ["docker", "network", "rm"],
        ):
            cleanup_calls.append(command)
            visible["container" if command[1] == "rm" else "network"] = False
        return _completed(command)
    monkeypatch.setattr(role_boundaries, "_run", run)
    monkeypatch.setattr(
        role_boundaries,
        "_start_disposable_postgres",
        lambda **_kwargs: (_ for _ in ()).throw(startup_failure),
    )
    fixture = role_boundaries.disposable_postgres.__wrapped__()
    with pytest.raises(type(startup_failure)):
        next(fixture)

    assert cleanup_calls == [
        ["docker", "rm", "-f", "atlas-db-roles-pg-rolefixture0"],
        ["docker", "network", "rm", "atlas-db-roles-rolefixture0"],
    ]


def test_role_wait_uses_shared_durable_hard_deadline_wait(monkeypatch) -> None:
    calls: list[tuple[str, float, float]] = []

    def wait(name: str, *, timeout_seconds: float, poll_interval: float) -> None:
        calls.append((name, timeout_seconds, poll_interval))

    monkeypatch.setattr(seed_harness, "wait_for_postgres", wait)

    role_boundaries._wait_for_disposable_postgres("role-pg", "unused-password")

    assert calls == [("role-pg", 45, 0.25)]


def test_role_cleanup_failures_do_not_mask_primary_error(monkeypatch) -> None:
    cleanup_calls: list[list[str]] = []
    monkeypatch.setattr(role_boundaries, "_require_disposable_postgres_runtime", lambda: None)
    monkeypatch.setattr(
        role_boundaries.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="rolefixture00000"),
    )
    ticks = iter((0.0, 61.0))
    monkeypatch.setattr(role_boundaries.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(role_boundaries.time, "sleep", lambda _seconds: None)

    def run(*args, **_kwargs):
        command = list(args)
        if command[1:3] == ["container", "inspect"]:
            record = {
                "Name": "/atlas-db-roles-pg-rolefixture0",
                "Config": {
                    "Labels": {
                        role_boundaries.DATABASE_ROLE_OWNER_LABEL: "rolefixture00000"
                    }
                },
            }
            return _completed(command, stdout=json.dumps([record]))
        if command[1:3] == ["network", "inspect"]:
            record = {
                "Name": "atlas-db-roles-rolefixture0",
                "Labels": {
                    role_boundaries.DATABASE_ROLE_OWNER_LABEL: "rolefixture00000"
                },
            }
            return _completed(command, stdout=json.dumps([record]))
        if command[:3] in (
            ["docker", "rm", "-f"],
            ["docker", "network", "rm"],
        ):
            cleanup_calls.append(command)
            return _completed(command, returncode=1, stderr="cleanup denied")
        return _completed(command)

    monkeypatch.setattr(role_boundaries, "_run", run)
    monkeypatch.setattr(
        role_boundaries,
        "_start_disposable_postgres",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("primary failure")),
    )

    fixture = role_boundaries.disposable_postgres.__wrapped__()
    with pytest.raises(ValueError, match="primary failure") as raised:
        next(fixture)

    assert len(cleanup_calls) == 2
    assert "cleanup denied" in "\n".join(raised.value.__notes__)


def test_role_fixture_retains_only_storage_blocked_startup_for_diagnostics(
    monkeypatch,
) -> None:
    cleanup_calls: list[list[str]] = []
    monkeypatch.setattr(role_boundaries, "_require_disposable_postgres_runtime", lambda: None)
    monkeypatch.setattr(
        role_boundaries.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="rolefixture00000"),
    )

    def run(*args, **_kwargs):
        command = list(args)
        if command == ["docker", "network", "create", "atlas-db-roles-rolefixture0"]:
            return _completed(command)
        if command[:3] == ["docker", "run", "--detach"]:
            return _completed(command, returncode=1, stderr="no space left on device")
        if command[:3] in (
            ["docker", "rm", "-f"],
            ["docker", "network", "rm"],
        ):
            cleanup_calls.append(command)
        return _completed(command)

    monkeypatch.setattr(role_boundaries, "_run", run)

    fixture = role_boundaries.disposable_postgres.__wrapped__()
    with pytest.raises(pytest.fail.Exception, match="storage exhaustion"):
        next(fixture)

    assert cleanup_calls == []
