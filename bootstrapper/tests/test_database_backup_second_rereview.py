"""Behavioral regressions from the second database-backup adversarial review."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import types

import pytest

from tests import test_database_backup_live_integration as live_integration


REPO = Path(__file__).resolve().parents[2]
ORCHESTRATOR = REPO / "services/backup/database_orchestrator.py"
RESTORE_SCRIPT = REPO / "services/backup/init/scripts/restore-databases.sh"
NEO_BACKUP = REPO / "services/neo4j/build/scripts/offline-backup.sh"
NEO_RESTORE = REPO / "services/neo4j/build/scripts/offline-restore.sh"


LIVE_PROBE_FAILURES = (
    pytest.param(
        subprocess.TimeoutExpired(("docker", "info"), 20),
        id="timeout",
    ),
    pytest.param(PermissionError("docker socket denied"), id="launch-error"),
)
@pytest.mark.parametrize("probe_failure", LIVE_PROBE_FAILURES)
@pytest.mark.parametrize("probe_target", ("daemon", *live_integration.IMAGES))
def test_exact_image_fixture_normalizes_probe_launch_failures(
    monkeypatch: pytest.MonkeyPatch,
    probe_target: str,
    probe_failure: BaseException,
) -> None:
    monkeypatch.setenv("ATLAS_DATABASE_BACKUP_LIVE_INTEGRATION", "1")
    monkeypatch.setattr(live_integration.shutil, "which", lambda _name: "/usr/bin/docker")

    def failed_probe(*args, **_kwargs):
        target = "daemon" if args[1] == "info" else args[-1]
        if target == probe_target:
            raise probe_failure
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(live_integration, "_run", failed_probe)
    diagnostic = (
        "Docker daemon probe failed"
        if probe_target == "daemon"
        else f"Docker image probe failed for {probe_target}"
    )
    with pytest.raises(pytest.fail.Exception, match=diagnostic):
        live_integration.exact_docker.__wrapped__()


def _module():
    spec = importlib.util.spec_from_file_location("atlas_database_orchestrator_rereview", ORCHESTRATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_lock_fingerprint_is_identical_from_an_independent_process(tmp_path: Path):
    lock = tmp_path / "database.lock"
    ready = tmp_path / "ready"
    code = textwrap.dedent(
        f"""
        import importlib.util, pathlib, sys, time
        spec = importlib.util.spec_from_file_location('child_orchestrator', {str(ORCHESTRATOR)!r})
        module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
        held = module.OwnedFileLock(pathlib.Path({str(lock)!r}), token={'a' * 32!r})
        held.acquire(); pathlib.Path({str(ready)!r}).write_text('ready'); time.sleep(30)
        """
    )
    child = subprocess.Popen([sys.executable, "-c", code])
    try:
        for _ in range(100):
            if ready.exists():
                break
            child.poll()
            if child.returncode is not None:
                pytest.fail(f"lock child exited early: {child.returncode}")
            import time
            time.sleep(0.02)
        module = _module()
        with pytest.raises(module.ContractError, match="active"):
            module.OwnedFileLock(lock, token="b" * 32).acquire()
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_poisoned_lock_is_never_automatically_reclaimed(tmp_path: Path):
    module = _module()
    lock = module.OwnedFileLock(tmp_path / "database.lock", token="c" * 32)
    lock.acquire()
    lock.poison("owned cleanup could not be proven")
    with pytest.raises(module.ContractError, match="poison"):
        module.OwnedFileLock(lock.path, token="d" * 32).acquire()


def test_unique_names_use_full_128_bit_owner_token():
    module = _module()
    token = "0123456789abcdef0123456789abcdef"
    name = module.CommandRunner(token=token, timeout=5, scope="scope").unique_name("validator")
    assert token in name
    assert len(name) <= 128


def test_live_volume_overrides_require_opt_in_full_token_and_owned_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _module()
    token = "1" * 32
    monkeypatch.setenv("ATLAS_NEO4J_LIVE_VOLUME", f"atlas-it-{token}-neo-live")
    monkeypatch.setenv("NEO4J_GRAPH_DB_SOURCE", "container")
    monkeypatch.setenv("WEAVIATE_SOURCE", "disabled")
    monkeypatch.setenv("WEAVIATE_ENABLE_MODULES", "backup-filesystem")
    with pytest.raises(module.ContractError, match="live integration"):
        module.DatabaseCoordinator(tmp_path, token=token, timeout=5)
    monkeypatch.setenv("ATLAS_DATABASE_BACKUP_LIVE_INTEGRATION", "1")
    monkeypatch.setenv("ATLAS_DATABASE_BACKUP_TEST_TOKEN", "short")
    with pytest.raises(module.ContractError, match="128-bit"):
        module.DatabaseCoordinator(tmp_path, token=token, timeout=5)


def test_create_volume_rejects_preexisting_or_wrong_labels():
    module = _module()
    token = "2" * 32
    runner = module.CommandRunner(token=token, timeout=5, scope="scope")
    name = runner.unique_name("stage")
    calls: list[list[str]] = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[:3] == ["docker", "volume", "inspect"]:
            return subprocess.CompletedProcess(
                command, 0,
                json.dumps([{"Name": name, "Labels": {module.OWNER_LABEL: "wrong"}}]), "",
            )
        return subprocess.CompletedProcess(command, 0, name + "\n", "")

    runner.run = run
    with pytest.raises(module.ContractError, match="ownership"):
        runner.create_volume("stage")


@pytest.mark.parametrize("kind", ["container", "volume"])
def test_cleanup_refuses_to_treat_an_unproven_inspect_failure_as_absence(kind: str):
    module = _module()
    runner = module.CommandRunner(token="3" * 32, timeout=5, scope="scope")
    name = runner.unique_name("cleanup")
    getattr(runner, kind + "s").add(name)

    def run(command, **_kwargs):
        if command[:3] == ["docker", kind, "inspect"]:
            return subprocess.CompletedProcess(command, 125, "", "daemon unavailable")
        if command[:3] in (["docker", "ps", "-a"], ["docker", "volume", "ls"]):
            return subprocess.CompletedProcess(command, 125, "", "daemon unavailable")
        raise AssertionError(command)

    runner.run = run
    with pytest.raises(module.ContractError, match="prove.*absent"):
        runner.cleanup()
    assert name in getattr(runner, kind + "s")


@pytest.mark.parametrize(
    ("initially_running", "stop_effect", "stop_error", "expected_restarts"),
    [
        (False, "stopped", None, 0),
        (True, "stopped", None, 1),
        (True, "stopped", "error", 1),
        (True, "running", "error", 0),
        (True, "stopped", "signal", 1),
    ],
)
def test_neo_backup_stop_compensation_uses_observed_state(
    initially_running: bool, stop_effect: str, stop_error: str | None, expected_restarts: int
):
    module = _module()
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.token = "4" * 32; coordinator.poison_reason = None
    state = {"running": initially_running}; restarts: list[str] = []
    coordinator._service_state = lambda _service: module.DatabaseServiceState(
        True, state["running"], state["running"]
    )
    coordinator._service_running = lambda _service: state["running"]

    def compose(*args, **_kwargs):
        if args[0] == "stop":
            state["running"] = stop_effect == "running"
            if stop_error == "signal":
                raise InterruptedError("signal during stop")
            if stop_error:
                raise module.ContractError("stop failed after taking effect")
        elif args[0] == "up":
            state["running"] = True; restarts.append(args[-1])
        return subprocess.CompletedProcess(args, 0, "", "")

    coordinator.compose = compose
    coordinator.runner = types.SimpleNamespace(
        scope="scope",
        unique_name=lambda _role: "job",
        register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    coordinator.containers_disappeared_after_compose_run = lambda _name: None
    expected = (InterruptedError, module.ContractError) if stop_error else ()
    if expected:
        with pytest.raises(expected):
            coordinator.backup_neo4j("20260830_010203")
    else:
        coordinator.backup_neo4j("20260830_010203")
    assert len(restarts) == expected_restarts
    assert state["running"] is initially_running


def _cutover_coordinator(module, fail_role: str):
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.plan = module.SourcePlan(True, True)
    coordinator.neo_live = "neo-live"; coordinator.weaviate_live = "weaviate-live"
    coordinator.stage = {"neo4j": "neo-stage", "weaviate": "weaviate-stage"}
    coordinator.was_running = {}; coordinator.rollback = {}; coordinator.timeout = 5
    coordinator.initial_states = {}; coordinator.cutover_started = False
    coordinator.boundary_state = "pre-cutover"; coordinator.poison_reason = None
    contents = {
        "neo-live": "neo-old", "weaviate-live": "weaviate-old",
        "neo-stage": "neo-new", "weaviate-stage": "weaviate-new",
    }
    running = {"neo4j-graph-db": True, "weaviate": True}

    class Runner:
        containers: set[str] = set()
        def create_volume(self, role):
            contents[role] = "partial"
            return role
        def remove_volume(self, name):
            contents.pop(name, None)
        def assert_no_owned_containers(self):
            return None
        def prune_retained_rollbacks(self, *_args, **_kwargs):
            return None
    coordinator.runner = Runner()
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )
    coordinator._validate_neo4j_data_volume = lambda *_args: None
    coordinator._validate_weaviate_data_volume = lambda *_args: None
    coordinator._bounded_count = lambda *_args: 1
    coordinator._verify_volume_copy = lambda source, target, role: None

    def compose(self, *args, check=True, timeout=None):
        del check, timeout
        service = args[-1]
        if args[0] == "stop": running[service] = False
        if args[0] == "up": running[service] = True
        return subprocess.CompletedProcess(args, 0, "", "")

    def copy(self, source, target, role):
        if role == fail_role:
            contents[target] = "partial-copy"
            raise module.ContractError("injected partial rollback preparation")
        contents[target] = contents[source]

    coordinator.compose = types.MethodType(compose, coordinator)
    coordinator._copy_volume = types.MethodType(copy, coordinator)
    return coordinator, contents, running


@pytest.mark.parametrize("role", ["neo4j-rollback-copy", "weaviate-rollback-copy"])
def test_partial_rollback_preparation_never_overwrites_untouched_live(role: str):
    module = _module()
    coordinator, contents, running = _cutover_coordinator(module, role)
    with pytest.raises(module.ContractError, match="partial rollback"):
        coordinator.cutover({})
    assert contents["neo-live"] == "neo-old"
    assert contents["weaviate-live"] == "weaviate-old"
    assert coordinator.rollback == {}
    assert running == {"neo4j-graph-db": True, "weaviate": True}


def test_validators_remove_owned_container_even_when_validation_raises():
    module = _module()
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.neo_user = "neo4j"; coordinator.neo_password = "secret"
    removed: list[str] = []
    coordinator._start_owned = lambda *_args, **_kwargs: "validator"
    coordinator._wait_exec = lambda *_args, **_kwargs: (_ for _ in ()).throw(module.ContractError("bad data"))
    coordinator.runner = types.SimpleNamespace(remove_container=removed.append)
    with pytest.raises(module.ContractError, match="bad data"):
        coordinator._validate_neo4j_data_volume("stage", "validator")
    assert removed == ["validator"]


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        ("STARTED", "pending"), ("TRANSFERRING", "pending"),
        ("TRANSFERRED", "pending"), ("FINALIZING", "pending"),
        ("CANCELLING", "pending"), ("SUCCESS", "success"),
        ("FAILED", "failed"), ("CANCELED", "failed"),
    ],
)
def test_exact_weaviate_138_status_enum(status: str, kind: str):
    assert _module().weaviate_status_kind(status) == kind


def test_restore_rejects_unbounded_limits_and_unsafe_test_root(tmp_path: Path):
    env = {
        **os.environ,
        "BACKUP_TIMESTAMP": "20260830_010203", "BACKUP_RESTORE_TOKEN": "e" * 32,
        "BACKUP_MANIFEST_HMAC_KEY": "f" * 64, "BACKUP_DEPLOYMENT_ID": "test",
        "BACKUP_COMMAND_TIMEOUT_SECONDS": "86401", "BACKUP_MAX_DATABASE_ARCHIVE_BYTES": "1099511627777",
        "DATABASE_RESTORE_ROOT": "/tmp/atlas-database-restore-test-" + "e" * 32 + "/../escape",
    }
    result = subprocess.run(["sh", str(RESTORE_SCRIPT), "prepare"], env=env, text=True, capture_output=True)
    assert result.returncode == 64
    assert "timeout" in result.stderr or "archive" in result.stderr or "restore root" in result.stderr


def test_prepared_plan_parser_requires_exact_correlated_values():
    module = _module()
    token = "a" * 32
    timestamp = "20260830_010203"
    valid = (
        "ATLAS_DATABASE_RESTORE_PLAN backup_timestamp=20260830_010203 "
        f"restore_token={token} backup_id={'b' * 32} neo4j_state=complete "
        "weaviate_state=disabled artifact_stage=restore-" + token + " "
        "weaviate_snapshot_id=disabled\n"
    )
    parsed = module.parse_prepared_plan(valid, token=token, timestamp=timestamp)
    assert parsed["artifact_stage"] == f"restore-{token}"
    with pytest.raises(module.ContractError):
        module.parse_prepared_plan(valid.replace(token, "../escape", 1), token=token, timestamp=timestamp)


def test_host_docker_commands_never_contain_neo4j_password(tmp_path: Path, monkeypatch):
    module = _module()
    monkeypatch.setenv("NEO4J_GRAPH_DB_SOURCE", "container")
    monkeypatch.setenv("WEAVIATE_SOURCE", "disabled")
    monkeypatch.setenv("WEAVIATE_ENABLE_MODULES", "backup-filesystem")
    monkeypatch.setenv("GRAPH_DB_AUTH", "neo4j/super-secret-value")
    coordinator = module.DatabaseCoordinator(tmp_path, token="b" * 32, timeout=5)
    commands: list[list[str]] = []
    coordinator._start_owned = lambda _role, command, **_kwargs: commands.append(command) or "validator"
    coordinator._wait_exec = lambda _container, command, _label, **_kwargs: commands.append(command) or '"neo4j"\n"system"\n'
    coordinator.runner.remove_container = lambda _name: None
    coordinator._validate_neo4j_data_volume("volume", "role")
    assert all("super-secret-value" not in " ".join(command) for command in commands)


def test_docker_exec_environment_options_precede_the_container_name():
    module = _module()
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 1
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ready", "")

    coordinator.runner = types.SimpleNamespace(run=run)
    assert coordinator._wait_exec(
        "validator", ["sh", "-c", "true"], "validator",
        env={**os.environ, "NEO4J_PASSWORD": "secret"},
        exec_env=("NEO4J_PASSWORD",),
    ) == "ready"
    assert commands == [[
        "docker", "exec", "-e", "NEO4J_PASSWORD", "validator", "sh", "-c", "true"
    ]]


def test_weaviate_live_validator_has_a_private_backup_backend():
    module = _module()
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.weaviate_modules = "backup-filesystem"
    commands: list[list[str]] = []
    requests: list[str] = []
    coordinator._start_owned = lambda _role, command: commands.append(command) or "validator"
    coordinator._validate_weaviate_runtime = lambda _container: None
    coordinator._weaviate_json = lambda _container, path: requests.append(path) or (
        {"classes": []} if path == "/v1/schema" else {"objects": [], "totalResults": 0}
    )
    coordinator.runner = types.SimpleNamespace(remove_container=lambda _name: None)
    coordinator._validate_weaviate_data_volume("stage", "validator")
    joined = " ".join(commands[0])
    assert "BACKUP_FILESYSTEM_PATH=/backups" in joined
    assert "--tmpfs /backups:" in joined
    assert requests == ["/v1/schema", "/v1/objects?limit=1"]


def test_exact_neo4j_version_match_is_not_substring():
    for script in (NEO_BACKUP, NEO_RESTORE):
        text = script.read_text(encoding="utf-8")
        assert '*"${EXPECTED_NEO4J_VERSION}"*' not in text


def test_successful_cutover_is_not_rolled_back_for_retention_failure():
    module = _module()
    coordinator, contents, running = _cutover_coordinator(module, "never")
    coordinator.runner.prune_retained_rollbacks = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        module.ContractError("injected retention failure")
    )
    coordinator.cutover({})
    assert contents["neo-live"] == "neo-new"
    assert contents["weaviate-live"] == "weaviate-new"
    assert running == {"neo4j-graph-db": True, "weaviate": True}


def test_migration_v5_handles_spacing_quotes_duplicates_and_inline_comments(tmp_path: Path):
    from services.migrations import migration_v5

    env = tmp_path / ".env"
    env.write_text(
        'BOOTSTRAPPER_PORT_LAYOUT_VERSION = 4\n'
        'WEAVIATE_ENABLE_MODULES = "text2vec-openai, backup-filesystem" # keep\n'
        'WEAVIATE_ENABLE_MODULES=duplicate\n',
        encoding="utf-8",
    )
    with pytest.raises(migration_v5.MigrationV5Error, match="duplicate"):
        migration_v5.apply(env)


def _fourth_prune_coordinator(module, *, both=False, run_error=None):
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.plan = module.SourcePlan(True, both)
    coordinator.poison_reason = None
    coordinator.token = "1" * 32
    coordinator.timeout = 5
    coordinator._bounded_count = lambda *_args: 2
    running = {"neo4j-graph-db": True, "weaviate": True}
    unhealthy: set[str] = set()
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service] and service not in unhealthy
    )
    coordinator.runner = types.SimpleNamespace(
        scope="scope", unique_name=lambda _role: "prune-job",
        register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(run_error) if run_error else None
        ),
    )
    coordinator.containers_disappeared_after_compose_run = lambda _name: None

    def compose(*args, **_kwargs):
        running[args[-1]] = args[0] == "up"

    coordinator.compose = compose
    coordinator._prune_compose = compose
    coordinator._prune_unhealthy = unhealthy
    return coordinator


def test_successful_owned_command_cleanup_failure_poisons_and_raises():
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.poison_reason = None; coordinator.token = "1" * 32
    coordinator.runner = types.SimpleNamespace(
        scope="scope", unique_name=lambda _role: "owned-job",
        register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
        remove_container=lambda _name: (_ for _ in ()).throw(
            module.ContractError("removal unproven")
        ),
    )

    with pytest.raises(module.ContractError, match="removal unproven"):
        coordinator._owned_run("fixture", ["ignored"])

    assert coordinator.poison_reason and "container cleanup" in coordinator.poison_reason


def test_backup_job_cleanup_timeout_does_not_replace_body_signal(capsys):
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.token = "1" * 32; coordinator.poison_reason = None
    coordinator._service_state = lambda _service: module.DatabaseServiceState(True, True, True)
    coordinator._service_running = lambda _service: True
    coordinator.runner = types.SimpleNamespace(
        scope="scope", unique_name=lambda _role: "job", register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.SignalInterruption("received signal 15")
        ),
        remove_container=lambda _name: (
            (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["docker", "inspect"], 5)
            )
        ),
    )
    coordinator.compose = lambda *_args, **_kwargs: None

    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator.backup_neo4j("20260830_010203")

    assert coordinator.poison_reason and "container cleanup" in coordinator.poison_reason
    assert "timed out" in capsys.readouterr().err


def test_snapshot_prune_rejects_initial_unhealthy_service_before_mutation():
    module = _module(); coordinator = _fourth_prune_coordinator(module)
    coordinator._prune_unhealthy.add("neo4j-graph-db")
    compose_calls: list[tuple] = []
    coordinator.compose = lambda *args, **_kwargs: compose_calls.append(args)

    with pytest.raises(module.ContractError, match="without proven health"):
        coordinator.prune_completed_database_snapshots()

    assert compose_calls == []
    assert coordinator.poison_reason is None


def test_snapshot_prune_refuses_body_when_stop_state_remains_running():
    module = _module(); coordinator = _fourth_prune_coordinator(module)
    coordinator.runner.run = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(AssertionError("prune body must not run"))
    )

    def compose(*args, **_kwargs):
        if args[0] == "up":
            coordinator._prune_compose(*args)

    coordinator.compose = compose
    with pytest.raises(module.ContractError, match="not proven stopped"):
        coordinator.prune_completed_database_snapshots()


def test_snapshot_prune_later_restart_signal_wins_after_all_attempts(capsys):
    module = _module(); coordinator = _fourth_prune_coordinator(module, both=True)
    calls: list[tuple] = []

    def compose(*args, **_kwargs):
        calls.append(args)
        if args[0] == "up" and args[-1] == "neo4j-graph-db":
            raise module.ContractError("neo restart failed")
        if args[0] == "up" and args[-1] == "weaviate":
            raise module.SignalInterruption("received signal 15")
        coordinator._prune_compose(*args)

    coordinator.compose = compose
    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator.prune_completed_database_snapshots()

    assert [call[-1] for call in calls if call[0] == "up"] == [
        "neo4j-graph-db", "weaviate"
    ]
    assert coordinator.poison_reason and "snapshot" in coordinator.poison_reason
    assert "neo restart failed" in capsys.readouterr().err


def test_snapshot_prune_unhealthy_restart_poisons_and_continues(capsys):
    module = _module(); coordinator = _fourth_prune_coordinator(module, both=True)
    calls: list[tuple] = []

    def compose(*args, **_kwargs):
        calls.append(args)
        coordinator._prune_compose(*args)
        if args[0] == "up" and args[-1] == "neo4j-graph-db":
            coordinator._prune_unhealthy.add("neo4j-graph-db")

    coordinator.compose = compose
    with pytest.raises(module.ContractError, match="snapshot retention restart"):
        coordinator.prune_completed_database_snapshots()

    assert [call[-1] for call in calls if call[0] == "up"] == [
        "neo4j-graph-db", "weaviate"
    ]
    assert coordinator.poison_reason and "snapshot" in coordinator.poison_reason
    assert "health was not proven" in capsys.readouterr().err


def test_snapshot_prune_body_signal_survives_unhealthy_restart(capsys):
    module = _module(); coordinator = _fourth_prune_coordinator(
        module, run_error=module.SignalInterruption("received signal 15")
    )

    def compose(*args, **_kwargs):
        coordinator._prune_compose(*args)
        if args[0] == "up":
            coordinator._prune_unhealthy.add(args[-1])

    coordinator.compose = compose
    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator.prune_completed_database_snapshots()

    assert coordinator.poison_reason and "snapshot" in coordinator.poison_reason
    assert "health was not proven" in capsys.readouterr().err


def test_backup_unhealthy_restart_poisons_and_fails():
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.token = "1" * 32; coordinator.poison_reason = None
    state_calls = 0

    def state(_service):
        nonlocal state_calls
        state_calls += 1
        return module.DatabaseServiceState(True, True, state_calls == 1)

    coordinator._service_state = state
    coordinator._service_running = lambda _service: False
    coordinator.runner = types.SimpleNamespace(
        scope="scope", unique_name=lambda _role: "job", register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    coordinator.containers_disappeared_after_compose_run = lambda _name: None
    coordinator.compose = lambda *_args, **_kwargs: None

    with pytest.raises(module.ContractError, match="restart health"):
        coordinator.backup_neo4j("20260830_010203")

    assert coordinator.poison_reason and "restart compensation" in coordinator.poison_reason


def _fourth_rollback_coordinator(module):
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.rollback = {"neo4j": "neo-rollback", "weaviate": "weaviate-rollback"}
    coordinator.poison_reason = None
    coordinator.runner = types.SimpleNamespace(assert_no_owned_containers=lambda: None)
    coordinator._require_stopped = lambda _enabled: None
    return coordinator


def test_rollback_oserror_attempts_every_database_and_state_restore():
    module = _module(); coordinator = _fourth_rollback_coordinator(module)
    copied: list[str] = []; verified: list[str] = []; compose_calls: list[tuple] = []
    coordinator.timeout = 5
    coordinator.initial_states = {
        "neo4j": module.DatabaseServiceState(True, True, True),
        "weaviate": module.DatabaseServiceState(True, True, True),
    }
    running = {"neo4j-graph-db": False, "weaviate": False}
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
    )

    def compose(*args, **_kwargs):
        compose_calls.append(args)
        if args[0] == "up":
            running[args[-1]] = True

    coordinator.compose = compose

    def copy(_source, _target, role):
        copied.append(role)
        if role == "neo4j-rollback-restore":
            raise OSError("transient Docker spawn failure")

    coordinator._copy_volume = copy
    coordinator._verify_volume_copy = lambda _source, _target, role: verified.append(role)
    enabled = [
        ("neo4j", "neo-live", "neo-stage"),
        ("weaviate", "weaviate-live", "weaviate-stage"),
    ]

    with pytest.raises(module.ContractError, match="transient Docker spawn failure"):
        coordinator._rollback_and_restore(enabled)

    assert copied == ["neo4j-rollback-restore", "weaviate-rollback-restore"]
    assert verified == ["weaviate-rollback-verify"]
    assert [call[-1] for call in compose_calls if call[0] == "up"] == ["weaviate"]
    assert running == {"neo4j-graph-db": False, "weaviate": True}
    assert coordinator.poison_reason and "recovery" in coordinator.poison_reason


def test_rollback_repeated_signals_attempt_all_work_and_preserve_first_signal():
    module = _module(); coordinator = _fourth_rollback_coordinator(module)
    copied: list[str] = []; restored: list[bool] = []

    def copy(_source, _target, role):
        copied.append(role)
        signum = 15 if role.startswith("neo4j") else 2
        raise module.SignalInterruption(f"received signal {signum}")

    coordinator._copy_volume = copy
    coordinator._verify_volume_copy = lambda *_args: None
    coordinator._restore_initial_states = (
        lambda _enabled, *, restartable: restored.append(restartable)
    )
    enabled = [
        ("neo4j", "neo-live", "neo-stage"),
        ("weaviate", "weaviate-live", "weaviate-stage"),
    ]

    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator._rollback_and_restore(enabled)

    assert copied == ["neo4j-rollback-restore", "weaviate-rollback-restore"]
    assert restored == [set()]
    assert coordinator.poison_reason and "recovery" in coordinator.poison_reason


@pytest.mark.parametrize(
    ("first_failure", "expected"),
    [
        (OSError("Docker state probe failed"), "exact initial service state"),
        (KeyboardInterrupt(), "KeyboardInterrupt"),
    ],
)
def test_exact_state_restore_attempts_later_service_after_base_exception(
    first_failure, expected
):
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5
    coordinator.initial_states = {
        "neo4j": module.DatabaseServiceState(True, True, True),
        "weaviate": module.DatabaseServiceState(True, True, True),
    }
    calls: list[str] = []

    def state(service):
        calls.append(service)
        if service == "neo4j-graph-db":
            raise first_failure
        return module.DatabaseServiceState(True, True, True)

    coordinator._service_state = state
    coordinator.compose = lambda *_args, **_kwargs: None
    enabled = [
        ("neo4j", "neo-live", "neo-stage"),
        ("weaviate", "weaviate-live", "weaviate-stage"),
    ]

    if isinstance(first_failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            coordinator._restore_exact_initial_states(enabled)
    else:
        with pytest.raises(module.ContractError, match=expected):
            coordinator._restore_exact_initial_states(enabled)

    assert calls == ["neo4j-graph-db", "weaviate", "weaviate"]


@pytest.mark.parametrize("failure_kind", ["oserror", "signal"])
def test_runner_cleanup_sweeps_all_resources_before_propagating(failure_kind):
    module = _module()
    runner = module.CommandRunner(token="4" * 32, timeout=5, scope="scope")
    runner.containers.update(("container-a", "container-b"))
    runner.volumes.update(("volume-a", "volume-b"))
    calls: list[str] = []

    def remove_container(name):
        calls.append(name)
        if len(calls) == 1:
            if failure_kind == "signal":
                raise module.SignalInterruption("received signal 15")
            raise OSError("Docker cleanup spawn failed")

    runner.remove_container = remove_container
    runner.remove_volume = lambda name: calls.append(name)

    expected = module.SignalInterruption if failure_kind == "signal" else module.ContractError
    with pytest.raises(expected):
        runner.cleanup()

    assert set(calls) == {"container-a", "container-b", "volume-a", "volume-b"}
    assert len(calls) == 4
