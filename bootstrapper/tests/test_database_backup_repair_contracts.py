"""Adversarial contracts for consistency-safe database restore orchestration."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import types

import pytest


REPO = Path(__file__).resolve().parents[2]
ORCHESTRATOR = REPO / "services/backup/database_orchestrator.py"
RESTORE = REPO / "services/backup/run-database-restore.sh"
NEO_RESTORE = REPO / "services/neo4j/build/scripts/offline-restore.sh"
SNAPSHOTS = REPO / "services/backup/init/scripts/database-snapshots.sh"
RESTORE_SNAPSHOTS = REPO / "services/backup/init/scripts/restore-databases.sh"
BACKUP_ENTRYPOINT = REPO / "services/backup/init/scripts/entrypoint.sh"
BACKUP_DOCKERFILE = REPO / "services/backup/init/Dockerfile"
BACKUP_COMPOSE = REPO / "services/backup/compose.yml"
WORKFLOW = REPO / ".github/workflows/services-lint.yml"
BACKUP_MANIFEST = REPO / "services/backup/service.yml"
BACKUP_README = REPO / "services/backup/README.md"
NEO_README = REPO / "services/neo4j/README.md"
DAEMON_PROBE_FAILURES = (
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


def _failed_restore_safety_daemon_fixture(
    monkeypatch: pytest.MonkeyPatch, probe_failure: object, *, in_ci: bool
):
    from bootstrapper.tests import test_postgres_restore_safety as restore_safety

    if in_ci:
        monkeypatch.setenv("CI", "true")
    else:
        monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(restore_safety.shutil, "which", lambda _name: "/usr/bin/docker")
    def failed_probe(*_args, **_kwargs):
        if isinstance(probe_failure, BaseException):
            raise probe_failure
        return probe_failure

    monkeypatch.setattr(restore_safety, "_run", failed_probe)
    return restore_safety.disposable_postgres.__wrapped__()


@pytest.mark.parametrize("probe_failure", DAEMON_PROBE_FAILURES)
def test_restore_safety_skips_any_failed_daemon_probe_outside_ci(
    monkeypatch, probe_failure
) -> None:
    fixture = _failed_restore_safety_daemon_fixture(
        monkeypatch, probe_failure, in_ci=False
    )

    with pytest.raises(pytest.skip.Exception, match="docker daemon unavailable"):
        next(fixture)


@pytest.mark.parametrize("probe_failure", DAEMON_PROBE_FAILURES)
def test_restore_safety_failed_daemon_probe_remains_fatal_in_ci(
    monkeypatch, probe_failure
) -> None:
    fixture = _failed_restore_safety_daemon_fixture(
        monkeypatch, probe_failure, in_ci=True
    )

    with pytest.raises(pytest.fail.Exception, match="Docker daemon probe failure"):
        next(fixture)


def _shell_function(path: Path, name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"missing {name} in {path}"
    return match.group(0)


@pytest.mark.parametrize(
    "case",
    [
        (
            "backup-all.sh",
            "cleanup",
            'COMPLETE=/tmp/complete\nWORK=/tmp/work\n'
            'close_snapshot() { return 0; }\nrelease_backup_lock() { return 0; }\n',
        ),
        (
            "restore-postgres.sh",
            "cleanup",
            'CUTOVER_STARTED=0\nCUTOVER_COMPLETE=0\nTEMP_CREATED=0\nLOCK_PID=\n'
            'WORK=/tmp/work\nstop_download() { return 0; }\n'
            'cleanup_backup_s3_config() { return 7; }\n',
        ),
        (
            "restore-databases.sh",
            "cleanup_restore_stages",
            'work=/tmp/work\nstage_tmp=/tmp/stage\nstage_final=/tmp/final\npublished=0\n',
        ),
    ],
)
@pytest.mark.parametrize("ending_case", [("kill -INT $$", 130), (":", 7)])
def test_exit_cleanup_preserves_primary_and_surfaces_cleanup_only_failure(
    tmp_path: Path, case, ending_case,
) -> None:
    script_name, function_name, prelude = case
    ending, expected = ending_case
    source = REPO / "services/backup/init/scripts" / script_name
    trap_signal = "1 2 15" if script_name != "restore-databases.sh" else "HUP INT TERM"
    cleanup_trap = "0" if script_name != "restore-databases.sh" else "EXIT"
    harness = tmp_path / "cleanup-probe.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        + prelude
        + 'rm() { return 7; }\nrecover_cutover() { return 0; }\nrun_bounded() { return 0; }\n'
        + _shell_function(source, function_name)
        + f"trap {function_name} {cleanup_trap}\n"
        + f"trap 'exit 130' {trap_signal}\n"
        + ending
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["sh", str(harness)], text=True, capture_output=True, check=False, timeout=5
    )
    assert result.returncode == expected, result.stderr


def test_restore_download_cleanup_propagates_fifo_removal_failure(tmp_path: Path) -> None:
    restore = REPO / "services/backup/init/scripts/restore-postgres.sh"
    harness = tmp_path / "download-cleanup-probe.sh"
    harness.write_text(
        "#!/bin/sh\nset +e\nDOWNLOAD_PID=\nDOWNLOAD_FIFO=/tmp/fifo\n"
        "rm() { return 7; }\n"
        + _shell_function(restore, "stop_download")
        + "stop_download\nexit $?\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["sh", str(harness)], text=True, capture_output=True, check=False, timeout=5
    )
    assert result.returncode == 7


@pytest.mark.parametrize("ending_case", [("kill -INT $$", 130), (":", 9)])
def test_cutover_recovery_failure_is_recorded_without_masking_primary(
    tmp_path: Path, ending_case,
) -> None:
    restore = REPO / "services/backup/init/scripts/restore-postgres.sh"
    ending, expected = ending_case
    harness = tmp_path / "cutover-cleanup-probe.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        "SUPABASE_DB_PASSWORD=secret\nSUPABASE_DB_USER=admin\n"
        "SUPABASE_DB_NAME=primary\nTEMP_DB=stage\nROLLBACK_DB=rollback\n"
        "CUTOVER_STARTED=1\nCUTOVER_COMPLETE=0\nTEMP_CREATED=0\nLOCK_PID=\n"
        "WORK=/tmp/work\nstop_download() { return 0; }\n"
        "cleanup_backup_s3_config() { return 0; }\nrm() { return 0; }\n"
        "run_bounded() { return 9; }\n"
        + _shell_function(restore, "recover_cutover")
        + _shell_function(restore, "cleanup")
        + "trap cleanup 0\ntrap 'exit 130' 1 2 15\n"
        + ending
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["sh", str(harness)], text=True, capture_output=True, check=False, timeout=5
    )
    assert result.returncode == expected
    assert "automatic cutover-state inspection failed" in result.stderr


def test_runner_cleanup_reports_all_failures_before_rethrowing_signal(capsys) -> None:
    module = _module()
    runner = module.CommandRunner(token="5" * 32, timeout=5, scope="scope")
    runner.containers.update(("container-oserror", "container-signal"))
    runner.volumes.update(("volume-contract", "volume-ok"))
    calls: list[str] = []

    def remove_container(name):
        calls.append(name)
        if name.endswith("oserror"):
            raise OSError("container spawn failed")
        raise module.SignalInterruption("received signal 15")

    def remove_volume(name):
        calls.append(name)
        if name.endswith("contract"):
            raise module.ContractError("volume ownership unproven")

    runner.remove_container = remove_container
    runner.remove_volume = remove_volume
    with pytest.raises(module.SignalInterruption, match="signal 15"):
        runner.cleanup()

    assert len(calls) == 4
    stderr = capsys.readouterr().err
    assert "container spawn failed" in stderr
    assert "received signal 15" in stderr
    assert "volume ownership unproven" in stderr


def test_unverified_rollback_is_forced_stopped_and_never_restarted() -> None:
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5
    coordinator.initial_states = {
        "neo4j": module.DatabaseServiceState(True, True, True)
    }
    running = True
    calls: list[tuple] = []
    coordinator._service_state = lambda _service: module.DatabaseServiceState(
        True, running, running
    )

    def compose(*args, **_kwargs):
        nonlocal running
        calls.append(args)
        if args[0] == "stop":
            running = False

    coordinator.compose = compose
    coordinator._restore_initial_states(
        [("neo4j", "neo-live", "neo-stage")], restartable=set()
    )

    assert [call[0] for call in calls] == ["stop"]
    assert running is False


@pytest.mark.parametrize(
    "initial", [
        pytest.param((True, False, False), id="initially-stopped"),
        pytest.param((False, False, False), id="initially-absent"),
    ],
)
def test_unverified_rollback_race_is_forced_offline_for_every_initial_state(initial) -> None:
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5
    coordinator.initial_states = {"neo4j": module.DatabaseServiceState(*initial)}
    states = iter([
        module.DatabaseServiceState(True, True, True),
        module.DatabaseServiceState(True, False, False),
    ])
    coordinator._service_state = lambda _service: next(states)
    calls: list[tuple] = []
    coordinator.compose = lambda *args, **_kwargs: calls.append(args)

    expected = module.ContractError if not initial[0] else None
    if expected:
        with pytest.raises(expected, match="unverified rollback"):
            coordinator._restore_initial_states(
                [("neo4j", "neo-live", "neo-stage")], restartable=set()
            )
    else:
        coordinator._restore_initial_states(
            [("neo4j", "neo-live", "neo-stage")], restartable=set()
        )
    assert [call[0] for call in calls] == ["stop"]


def test_recovery_stop_sweep_continues_after_effective_oserror(capsys) -> None:
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.poison_reason = None
    running = {"neo4j-graph-db": True, "weaviate": True}
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )
    calls: list[str] = []; restored: list[bool] = []

    def compose(*args, **_kwargs):
        service = args[-1]; calls.append(service); running[service] = False
        if service == "neo4j-graph-db":
            raise OSError("stop transport failed after effect")

    coordinator.compose = compose
    coordinator.restore_rollback = lambda _enabled: restored.append(True)
    enabled = [
        ("neo4j", "neo-live", "neo-stage"),
        ("weaviate", "weaviate-live", "weaviate-stage"),
    ]
    coordinator._recover_after_live_mutation(enabled)

    assert calls == ["neo4j-graph-db", "weaviate"]
    assert running == {"neo4j-graph-db": False, "weaviate": False}
    assert restored == [True]
    assert "stop transport failed after effect" in capsys.readouterr().err


def test_recovery_effective_signal_propagates_after_rollback(capsys) -> None:
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.poison_reason = None
    running = {"neo4j-graph-db": True, "weaviate": True}
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )
    calls: list[str] = []; restored: list[bool] = []

    def compose(*args, **_kwargs):
        service = args[-1]; calls.append(service); running[service] = False
        signal = 15 if service == "neo4j-graph-db" else 2
        raise module.SignalInterruption(f"received signal {signal}")

    coordinator.compose = compose
    coordinator.restore_rollback = lambda _enabled: restored.append(True)
    enabled = [
        ("neo4j", "neo-live", "neo-stage"),
        ("weaviate", "weaviate-live", "weaviate-stage"),
    ]
    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator._recover_after_live_mutation(enabled)

    assert calls == ["neo4j-graph-db", "weaviate"]
    assert running == {"neo4j-graph-db": False, "weaviate": False}
    assert restored == [True]
    assert coordinator.poison_reason is None
    stderr = capsys.readouterr().err
    assert "signal 15" in stderr and "signal 2" in stderr


def test_async_signal_at_proof_to_rollback_seam_is_deferred() -> None:
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.poison_reason = None
    coordinator.boundary_state = "cutover-mutated"
    coordinator._service_state = lambda _service: module.DatabaseServiceState(
        True, False, False
    )
    coordinator.compose = lambda *_args, **_kwargs: None
    restored: list[bool] = []

    original_finish = coordinator._finish_recovery_quiesce

    def signal_at_seam(failures, proof_failures):
        result = original_finish(failures, proof_failures)
        os.kill(os.getpid(), signal.SIGTERM)
        return result

    coordinator._finish_recovery_quiesce = signal_at_seam

    def restore_rollback(_enabled):
        restored.append(True)
        coordinator.boundary_state = "recovery-proven"

    coordinator.restore_rollback = restore_rollback
    previous = signal.signal(signal.SIGTERM, module._signal_as_exception)
    try:
        with pytest.raises(module.SignalInterruption, match=f"signal {signal.SIGTERM}"):
            coordinator._recover_after_live_mutation(
                [("neo4j", "neo-live", "neo-stage")]
            )
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert restored == [True]
    assert coordinator.boundary_state == "recovery-proven"
    assert coordinator.poison_reason is None


def test_cutover_promotes_recovery_signal_after_compensating_rollback(capsys) -> None:
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.plan = module.SourcePlan(True, False)
    coordinator.neo_live = "neo-live"; coordinator.weaviate_live = "weaviate-live"
    coordinator.stage = {"neo4j": "neo-stage"}
    coordinator.was_running = {}; coordinator.initial_states = {}; coordinator.rollback = {}
    coordinator.cutover_started = False; coordinator.boundary_state = "pre-cutover"
    coordinator.poison_reason = None; coordinator.timeout = 5
    coordinator._bounded_count = lambda *_args: 1
    coordinator._validate_neo4j_data_volume = lambda *_args: None
    running = {"neo4j-graph-db": True}
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )
    copy_roles: list[str] = []
    coordinator._copy_volume = lambda _source, _target, role: copy_roles.append(role)
    coordinator._verify_volume_copy = lambda *_args: None
    coordinator.runner = types.SimpleNamespace(
        create_volume=lambda role: role,
        assert_no_owned_containers=lambda: None,
        remove_volume=lambda _name: None,
        prune_retained_rollbacks=lambda *_args, **_kwargs: None,
    )
    up_calls = 0

    def compose(*args, **_kwargs):
        nonlocal up_calls
        action = args[0]; service = args[-1]
        if action == "stop":
            running[service] = False
            if up_calls:
                raise module.SignalInterruption("received signal 15 during recovery")
        elif action == "up":
            up_calls += 1; running[service] = True
            if up_calls == 1:
                raise module.ContractError("primary cutover failure")

    coordinator.compose = compose
    with pytest.raises(module.SignalInterruption, match="signal 15 during recovery"):
        coordinator.cutover({})

    assert "neo4j-rollback-restore" in copy_roles
    assert running["neo4j-graph-db"] is True
    assert coordinator.boundary_state == "recovery-proven"
    assert coordinator.poison_reason is None
    stderr = capsys.readouterr().err
    assert "received signal 15 during recovery" in stderr
    assert "post-mutation recovery warning" not in stderr


def test_recovery_stop_sweep_retries_and_prioritizes_repeated_signal(capsys) -> None:
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.poison_reason = None
    running = {"neo4j-graph-db": True, "weaviate": True}
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )
    calls: list[str] = []; restored: list[bool] = []

    def compose(*args, **_kwargs):
        service = args[-1]; calls.append(service)
        if service == "weaviate":
            running[service] = False
            raise module.SignalInterruption("received signal 2")
        raise module.SignalInterruption("received signal 15")

    coordinator.compose = compose
    coordinator.restore_rollback = lambda _enabled: restored.append(True)
    enabled = [
        ("neo4j", "neo-live", "neo-stage"),
        ("weaviate", "weaviate-live", "weaviate-stage"),
    ]
    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator._recover_after_live_mutation(enabled)

    assert calls == ["neo4j-graph-db", "weaviate", "neo4j-graph-db"]
    assert running["weaviate"] is False
    assert restored == []
    assert coordinator.poison_reason and "stopped boundary" in coordinator.poison_reason
    stderr = capsys.readouterr().err
    assert "signal 15" in stderr and "signal 2" in stderr


def test_rollback_verification_failure_keeps_failed_database_offline() -> None:
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.poison_reason = None
    coordinator.rollback = {"neo4j": "neo-rollback", "weaviate": "weaviate-rollback"}
    coordinator.runner = types.SimpleNamespace(assert_no_owned_containers=lambda: None)
    coordinator._require_stopped = lambda _enabled: None
    coordinator.initial_states = {
        "neo4j": module.DatabaseServiceState(True, True, True),
        "weaviate": module.DatabaseServiceState(True, True, True),
    }
    running = {"neo4j-graph-db": False, "weaviate": False}
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )
    coordinator._copy_volume = lambda *_args: None
    coordinator._verify_volume_copy = lambda _source, _target, role: (
        (_ for _ in ()).throw(module.ContractError("rollback verification failed"))
        if role.startswith("neo4j") else None
    )
    compose_calls: list[tuple] = []

    def compose(*args, **_kwargs):
        compose_calls.append(args)
        if args[0] == "up":
            running[args[-1]] = True

    coordinator.compose = compose
    enabled = [
        ("neo4j", "neo-live", "neo-stage"),
        ("weaviate", "weaviate-live", "weaviate-stage"),
    ]
    with pytest.raises(module.ContractError, match="rollback verification failed"):
        coordinator._rollback_and_restore(enabled)

    assert [call[-1] for call in compose_calls if call[0] == "up"] == ["weaviate"]
    assert running == {"neo4j-graph-db": False, "weaviate": True}
    assert coordinator.poison_reason and "recovery" in coordinator.poison_reason


def test_backup_entrypoint_reports_missing_openssl_without_an_undefined_helper(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        ["/bin/sh", str(BACKUP_ENTRYPOINT), "/bin/true"],
        env={
            "PATH": str(tmp_path),
            "BACKUP_SOURCE": "container",
            "BACKUP_COMMAND_TIMEOUT_SECONDS": "1",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 69
    assert result.stderr == "backup: backup image is missing required OpenSSL\n"


def test_backup_readme_matches_the_built_image_and_baked_openssl_contract() -> None:
    readme = BACKUP_README.read_text(encoding="utf-8")
    compose = BACKUP_COMPOSE.read_text(encoding="utf-8")
    dockerfile = BACKUP_DOCKERFILE.read_text(encoding="utf-8")
    pinned_base = (
        "postgres:17.10-alpine@sha256:"
        "742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
    )

    assert f"BASE_IMAGE: ${{BACKUP_IMAGE:-{pinned_base}}}" in compose
    assert "image: ${PROJECT_NAME}-backup:local" in compose
    assert f"ARG BASE_IMAGE={pinned_base}" in dockerfile
    assert "RUN apk add --no-cache openssl=3.5.8-r0" in dockerfile
    expected_claims = (
        "`${PROJECT_NAME}-backup:local`",
        f"`{pinned_base}`",
        "`openssl=3.5.8-r0`",
    )
    for expected in expected_claims:
        assert expected in readme
    assert "BACKUP_IMAGE=postgres:17.10-alpine " not in readme
    stale_runtime_install = (
        "OpenSSL is still resolved from the Alpine repository at container startup"
    )
    assert stale_runtime_install not in readme


def _module():
    assert ORCHESTRATOR.is_file(), "host database orchestrator is missing"
    spec = importlib.util.spec_from_file_location("atlas_database_orchestrator", ORCHESTRATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("neo", "weaviate", "expected"),
    [
        ("container", "container", (True, True)),
        ("container", "disabled", (True, False)),
        ("disabled", "container", (False, True)),
        ("disabled", "disabled", (False, False)),
    ],
)
def test_source_plan_skips_disabled_databases(neo, weaviate, expected):
    module = _module()
    plan = module.source_plan(neo, weaviate)
    assert (plan.neo4j, plan.weaviate) == expected


@pytest.mark.parametrize("neo,weaviate", [("localhost", "container"), ("container", "localhost")])
def test_source_plan_rejects_localhost_before_work(neo, weaviate):
    module = _module()
    with pytest.raises(module.ContractError, match="localhost"):
        module.source_plan(neo, weaviate)


def test_restore_requires_explicit_maintenance_confirmation():
    env = {
        **os.environ,
        "NEO4J_GRAPH_DB_SOURCE": "disabled",
        "WEAVIATE_SOURCE": "disabled",
    }
    result = subprocess.run(
        [str(RESTORE)], cwd=REPO, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode == 64
    assert "BACKUP_RESTORE_MAINTENANCE_MODE=confirmed" in result.stderr


def test_timestamp_validation_is_calendar_strict():
    module = _module()
    assert module.validate_backup_timestamp("20240229_235959") == "20240229_235959"
    for value in ("20230229_120000", "20241301_000000", "20240132_000000", "20240101_240000"):
        with pytest.raises(module.ContractError):
            module.validate_backup_timestamp(value)


def test_atomic_lock_rejects_concurrency_and_pid_reuse(tmp_path):
    module = _module()
    lock_path = tmp_path / "restore.lock"
    first = module.OwnedFileLock(lock_path, token="a" * 32)
    first.acquire()
    with pytest.raises(module.ContractError, match="active"):
        module.OwnedFileLock(lock_path, token="b" * 32).acquire()

    # A live PID with the wrong process-start fingerprint is stale (PID reuse).
    first.detach_for_test()
    lock_path.write_text(
        f"state=active\npid={os.getpid()}\nstart=not-this-process\ntoken={'c' * 32}\n"
    )
    replacement = module.OwnedFileLock(lock_path, token="d" * 32)
    replacement.acquire()
    replacement.release()
    assert not lock_path.exists()


def test_old_lock_owner_cannot_unlink_replacement(tmp_path):
    module = _module()
    lock_path = tmp_path / "restore.lock"
    old = module.OwnedFileLock(lock_path, token="e" * 32)
    old.acquire()
    old.detach_for_test()
    lock_path.unlink()
    new = module.OwnedFileLock(lock_path, token="f" * 32)
    new.acquire()
    old.release()
    assert lock_path.exists()
    new.release()


def test_exact_weaviate_status_machine_accepts_v138_transients():
    module = _module()
    for status in ("STARTED", "TRANSFERRING", "TRANSFERRED", "FINALIZING", "CANCELLING"):
        assert module.weaviate_status_kind(status) == "pending"
    assert module.weaviate_status_kind("SUCCESS") == "success"
    for status in ("FAILED", "CANCELED"):
        assert module.weaviate_status_kind(status) == "failed"
    with pytest.raises(module.ContractError):
        module.weaviate_status_kind("success")


def test_neo_archives_are_fully_validated_before_either_load():
    text = NEO_RESTORE.read_text(encoding="utf-8")
    first_mutation = text.index("database load system")
    assert text.index("database load --info system") < first_mutation
    assert text.index("database load --info neo4j") < first_mutation
    assert 'metadata_value "${database}_bytes"' in text
    assert 'wc -c <"${archive}"' in text
    assert "database check system" in text and "database check neo4j" in text


def test_snapshot_restore_stages_are_private_unique_and_link_safe():
    text = RESTORE_SNAPSHOTS.read_text(encoding="utf-8")
    assert "BACKUP_RESTORE_TOKEN" in text
    assert "umask 077" in text
    assert "unsafe archive link type" in text
    assert "restore-set.complete" in text
    assert "cleanup_restore_stages" in text


def test_weaviate_timeout_cancels_exact_owned_backup():
    text = SNAPSHOTS.read_text(encoding="utf-8")
    assert "FINALIZING" in text and "CANCELLING" in text and "CANCELED" in text
    assert "DELETE" in text
    assert "/v1/backups/filesystem/${database_weaviate_snapshot_id}" in text


def test_ci_live_database_drill_is_explicit_and_pulls_exact_images():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "docker pull neo4j:5.26.27" in text
    assert "docker pull cr.weaviate.io/semitechnologies/weaviate:1.38.13" in text
    assert "ATLAS_DATABASE_BACKUP_LIVE_INTEGRATION" in text


def test_host_orchestrator_owns_jobs_and_rollback_cutovers():
    text = ORCHESTRATOR.read_text(encoding="utf-8") if ORCHESTRATOR.exists() else ""
    for fragment in (
        "start_new_session=True",
        "os.killpg",
        "com.atlas.database-restore-token",
        "--pull=never",
        "rollback",
        "validate_neo4j_stage",
        "validate_weaviate_stage",
        "restore_rollback",
    ):
        assert fragment in text


def test_second_cutover_copy_failure_restores_both_live_generations():
    module = _module()
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.plan = module.SourcePlan(True, True)
    coordinator.neo_live = "neo-live"
    coordinator.weaviate_live = "weaviate-live"
    coordinator.stage = {"neo4j": "neo-stage", "weaviate": "weaviate-stage"}
    coordinator.was_running = {}
    coordinator.initial_states = {}
    coordinator.rollback = {}
    coordinator.cutover_started = False
    coordinator.boundary_state = "pre-cutover"
    coordinator.poison_reason = None
    coordinator.timeout = 5
    contents = {
        "neo-live": "neo-old", "weaviate-live": "weaviate-old",
        "neo-stage": "neo-new", "weaviate-stage": "weaviate-new",
    }
    running = {"neo4j-graph-db": True, "weaviate": True}

    class Runner:
        def create_volume(self, role):
            contents[role] = ""
            return role

        def prune_retained_rollbacks(self, retained, *, keep):
            raise AssertionError("failed cutover must not prune rollback volumes")

        def remove_volume(self, name):
            contents.pop(name, None)

        def assert_no_owned_containers(self):
            return None

    coordinator.runner = Runner()
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )
    coordinator._bounded_count = lambda *_args: 1
    coordinator._validate_neo4j_data_volume = lambda *_args: None
    coordinator._validate_weaviate_data_volume = lambda *_args: None
    coordinator._verify_volume_copy = lambda source, target, role: None

    def compose(self, *args, check=True, timeout=None):
        del check, timeout
        service = args[-1]
        if args[0] == "stop":
            running[service] = False
        elif args[0] == "up":
            running[service] = True
        return subprocess.CompletedProcess(args, 0, "", "")

    def copy(self, source, target, role):
        if role == "weaviate-cutover":
            raise module.ContractError("injected second cutover copy failure")
        contents[target] = contents[source]

    coordinator.compose = types.MethodType(compose, coordinator)
    coordinator._copy_volume = types.MethodType(copy, coordinator)

    with pytest.raises(module.ContractError, match="second cutover"):
        coordinator.cutover({})
    assert contents["neo-live"] == "neo-old"
    assert contents["weaviate-live"] == "weaviate-old"
    assert running == {"neo4j-graph-db": True, "weaviate": True}


def test_second_service_stop_failure_restarts_the_first_without_mutation():
    module = _module()
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.plan = module.SourcePlan(True, True)
    coordinator.neo_live = "neo-live"
    coordinator.weaviate_live = "weaviate-live"
    coordinator.stage = {"neo4j": "neo-stage", "weaviate": "weaviate-stage"}
    coordinator.was_running = {}
    coordinator.initial_states = {}
    coordinator.rollback = {}
    coordinator.cutover_started = False
    coordinator.boundary_state = "pre-cutover"
    coordinator.poison_reason = None
    coordinator.timeout = 5
    running = {"neo4j-graph-db": True, "weaviate": True}

    class Runner:
        def create_volume(self, _role):
            raise AssertionError("rollback creation must not start after failed quiesce")

    coordinator.runner = Runner()
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )

    def compose(self, *args, check=True, timeout=None):
        del timeout
        service = args[-1]
        if args[0] == "stop" and service == "weaviate" and check:
            raise module.ContractError("injected stop failure")
        if args[0] == "stop":
            running[service] = False
        elif args[0] == "up":
            running[service] = True
        return subprocess.CompletedProcess(args, 0, "", "")

    coordinator.compose = types.MethodType(compose, coordinator)
    coordinator._copy_volume = lambda *_args: None
    with pytest.raises(module.ContractError, match="stop failure"):
        coordinator.cutover({})
    assert running == {"neo4j-graph-db": True, "weaviate": True}


def test_local_snapshot_and_rollback_retention_are_bounded():
    manifest = BACKUP_MANIFEST.read_text(encoding="utf-8")
    snapshots = SNAPSHOTS.read_text(encoding="utf-8")
    orchestrator = ORCHESTRATOR.read_text(encoding="utf-8")
    assert "BACKUP_LOCAL_SNAPSHOT_RETENTION_COUNT" in manifest
    assert "BACKUP_LOCAL_ROLLBACK_RETENTION_COUNT" in manifest
    assert "prune_completed_database_snapshots" in snapshots
    assert "prune_retained_rollbacks" in orchestrator


def test_docs_do_not_claim_atomic_cross_component_recovery():
    text = BACKUP_README.read_text(encoding="utf-8")
    assert "independent authenticated completion markers" in text
    assert "not an atomic cross-database recovery point" in text


def test_legacy_neo4j_bind_snapshots_remain_operator_accessible():
    text = NEO_README.read_text(encoding="utf-8")
    assert "build/snapshot" in text
    assert "legacy" in text.lower()
