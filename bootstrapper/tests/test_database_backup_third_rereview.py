"""Behavioral regressions from the third database-backup adversarial review."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import types

import pytest

from tests import seed_harness


REPO = Path(__file__).resolve().parents[2]
ORCHESTRATOR = REPO / "services/backup/database_orchestrator.py"
LIVE_TEST = REPO / "bootstrapper/tests/test_database_backup_live_integration.py"
VOLUME_TEST = REPO / "bootstrapper/tests/test_database_volume_backup_contracts.py"


@pytest.mark.parametrize(
    "ticks",
    (
        (KeyboardInterrupt("deadline initialization"), 0.0, 1.0, 31.0),
        (0.0, KeyboardInterrupt("deadline check"), 1.0, 31.0),
    ),
    ids=("initialization", "post-pass-check"),
)
def test_seed_cleanup_defers_deadline_interruptions_until_reconciled(
    monkeypatch: pytest.MonkeyPatch,
    ticks: tuple[BaseException | float, ...],
) -> None:
    outcomes = iter(ticks)
    cleanup_passes: list[str] = []

    def monotonic() -> float:
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(seed_harness.time, "monotonic", monotonic)
    monkeypatch.setattr(seed_harness.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        seed_harness,
        "_remove_owned_seed_once",
        lambda *_args: cleanup_passes.append("pass"),
    )

    with pytest.raises(KeyboardInterrupt, match="deadline"):
        seed_harness.remove_seed_container("seed", "token", uncertain=True)

    assert cleanup_passes == ["pass", "pass"]


def _module():
    spec = importlib.util.spec_from_file_location("atlas_database_orchestrator_third", ORCHESTRATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _coordinator(module, *, both: bool = False):
    coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.plan = module.SourcePlan(True, both)
    coordinator.neo_live = "neo-live"; coordinator.weaviate_live = "weav-live"
    coordinator.stage = {"neo4j": "neo-stage", "weaviate": "weav-stage"}
    coordinator.was_running = {}; coordinator.initial_states = {}; coordinator.rollback = {}
    coordinator.timeout = 5; coordinator.boundary_state = "pre-cutover"
    coordinator.poison_reason = None; coordinator.cutover_started = False
    coordinator._bounded_count = lambda *_args: 2
    coordinator._validate_neo4j_data_volume = lambda *_args: None
    coordinator._validate_weaviate_data_volume = lambda *_args: None
    return coordinator


@pytest.mark.parametrize("post_stop", ["stopped", "running", "probe-error"])
def test_nonzero_stop_never_begins_rollback_copy_and_requires_manual_recovery(post_stop: str):
    module = _module(); coordinator = _coordinator(module)
    created: list[str] = []
    coordinator.runner = types.SimpleNamespace(
        create_volume=lambda role: created.append(role) or role,
        assert_no_owned_containers=lambda: None,
        remove_volume=lambda _name: None,
        prune_retained_rollbacks=lambda *_args, **_kwargs: None,
    )
    calls = 0

    def state(_service):
        nonlocal calls
        calls += 1
        if calls == 1:
            return module.DatabaseServiceState(True, True, True)
        if post_stop == "probe-error":
            raise module.ContractError("daemon unavailable")
        return module.DatabaseServiceState(True, post_stop == "running", False)

    coordinator._service_state = state
    coordinator.compose = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        module.ContractError("stop returned nonzero")
    )
    with pytest.raises(module.ContractError, match="stop returned nonzero"):
        coordinator.cutover({})
    assert created == []
    assert coordinator.boundary_state == "pre-cutover"
    assert coordinator.poison_reason and "quiesce" in coordinator.poison_reason


def test_all_enabled_services_are_reproved_stopped_before_any_rollback_write():
    module = _module(); coordinator = _coordinator(module, both=True)
    states = {
        "neo4j-graph-db": iter([
            module.DatabaseServiceState(True, True, True),
            module.DatabaseServiceState(True, False, False),
            module.DatabaseServiceState(True, True, False),
        ]),
        "weaviate": iter([
            module.DatabaseServiceState(True, True, True),
            module.DatabaseServiceState(True, False, False),
            module.DatabaseServiceState(True, False, False),
        ]),
    }
    coordinator._service_state = lambda service: next(states[service])
    coordinator.compose = lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", "")
    created: list[str] = []
    coordinator.runner = types.SimpleNamespace(
        create_volume=lambda role: created.append(role) or role,
        assert_no_owned_containers=lambda: None,
        remove_volume=lambda _name: None,
        prune_retained_rollbacks=lambda *_args, **_kwargs: None,
    )
    with pytest.raises(module.ContractError, match="not proven stopped"):
        coordinator.cutover({})
    assert created == []
    assert coordinator.poison_reason


@pytest.mark.parametrize("recovery_failure", [None, "copy", "verify", "restart", "health"])
def test_post_mutation_exit_is_clean_only_after_fully_verified_recovery(recovery_failure: str | None):
    module = _module(); coordinator = _coordinator(module)
    running = True
    restart_attempted = False
    coordinator._service_state = lambda _service: module.DatabaseServiceState(
        True, running, running and not (recovery_failure == "health" and restart_attempted)
    )

    class Runner:
        def create_volume(self, role): return role
        def remove_volume(self, _name): return None
        def assert_no_owned_containers(self): return None
        def prune_retained_rollbacks(self, *_args, **_kwargs): return None
    coordinator.runner = Runner()

    def compose(*args, **_kwargs):
        nonlocal running, restart_attempted
        if args[0] == "stop": running = False
        elif args[0] == "up":
            restart_attempted = True
            if recovery_failure == "restart":
                raise module.ContractError("restart failed")
            running = True
        return subprocess.CompletedProcess(args, 0, "", "")

    def copy(_source, _target, role):
        if role == "neo4j-cutover":
            raise module.ContractError("injected cutover failure")
        if role == "neo4j-rollback-restore" and recovery_failure == "copy":
            raise module.ContractError("rollback copy failed")

    rollback_verifications = 0

    def verify(_source, _target, role):
        nonlocal rollback_verifications
        if role == "neo4j-rollback-verify":
            rollback_verifications += 1
        if role == "neo4j-rollback-verify" and rollback_verifications == 2 and recovery_failure == "verify":
            raise module.ContractError("rollback verify failed")

    coordinator.compose = compose; coordinator._copy_volume = copy; coordinator._verify_volume_copy = verify
    with pytest.raises(module.ContractError):
        coordinator.cutover({})
    if recovery_failure is None:
        assert coordinator.boundary_state == "recovery-proven"
        assert coordinator.poison_reason is None
    else:
        assert coordinator.boundary_state == "cutover-mutated"
        assert coordinator.poison_reason and "recovery" in coordinator.poison_reason


@pytest.mark.parametrize(("initial_exists", "current_exists"), [(True, False), (False, True)])
def test_exact_state_recovery_rejects_compose_container_existence_changes(
    initial_exists: bool, current_exists: bool
):
    module = _module(); coordinator = _coordinator(module)
    coordinator.initial_states = {
        "neo4j": module.DatabaseServiceState(initial_exists, False, False)
    }
    coordinator._service_state = lambda _service: module.DatabaseServiceState(
        current_exists, False, False
    )
    coordinator.compose = lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", "")
    with pytest.raises(module.ContractError, match="exact initial service state"):
        coordinator._restore_exact_initial_states([("neo4j", "neo-live", "neo-stage")])


def test_poisoned_transaction_lock_rejects_a_second_process(tmp_path: Path):
    module = _module(); lock = module.OwnedFileLock(tmp_path / "boundary.lock", token="a" * 32)
    lock.acquire()
    coordinator = types.SimpleNamespace(
        poison_reason="recovery could not be proven",
        runner=types.SimpleNamespace(cleanup=lambda **_kwargs: None),
    )
    module.finalize_boundary_lock(lock, coordinator, retained=set())
    with pytest.raises(module.ContractError, match="poison"):
        module.OwnedFileLock(lock.path, token="b" * 32).acquire()


@pytest.mark.parametrize("kind", ["container", "volume"])
def test_post_remove_daemon_error_is_not_treated_as_absence(kind: str):
    module = _module(); runner = module.CommandRunner(token="c" * 32, timeout=5, scope="scope")
    name = runner.unique_name("owned")
    getattr(runner, kind + "s").add(name)
    inspect_calls = 0

    def run(command, **_kwargs):
        nonlocal inspect_calls
        if command[:3] == ["docker", kind, "inspect"]:
            inspect_calls += 1
            if inspect_calls == 1:
                labels = {
                    module.OWNER_LABEL: runner.token,
                    module.SCOPE_LABEL: runner.scope,
                    module.ROLE_LABEL: "owned",
                }
                record = {"Name": name, "Labels": labels}
                if kind == "container": record = {"Name": "/" + name, "Config": {"Labels": labels}}
                return subprocess.CompletedProcess(command, 0, json.dumps([record]), "")
            return subprocess.CompletedProcess(command, 125, "", "daemon unavailable")
        if command[:3] == ["docker", "rm", "-f"] or command[:3] == ["docker", "volume", "rm"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] in (["docker", "ps", "-a"], ["docker", "volume", "ls"]):
            return subprocess.CompletedProcess(command, 125, "", "daemon unavailable")
        raise AssertionError(command)

    runner.run = run
    with pytest.raises(module.ContractError, match="prove.*absent"):
        getattr(runner, "remove_" + kind)(name)
    assert name in getattr(runner, kind + "s")


def test_rollback_pruning_requires_owner_and_name_token_correlation():
    module = _module(); runner = module.CommandRunner(token="d" * 32, timeout=5, scope="scope")
    new_owner = "e" * 32; old_owner = "a" * 32
    newest = f"atlas-db-neo4j-rollback-{new_owner}"
    oldest = f"atlas-db-neo4j-rollback-{old_owner}"
    decoy = "atlas-db-neo4j-rollback-decoy"
    removed: list[str] = []

    def run(command, **_kwargs):
        if command[:4] == ["docker", "volume", "ls", "-q"]:
            role = command[-1].split("=", 2)[-1]
            if command[-1].startswith("name="):
                return subprocess.CompletedProcess(command, 0, "", "")
            names = f"{newest}\n{oldest}\n{decoy}\n" if role == "neo4j-rollback" else ""
            return subprocess.CompletedProcess(command, 0, names, "")
        if command[:3] == ["docker", "volume", "inspect"]:
            name = command[-1]
            if name in removed:
                return subprocess.CompletedProcess(command, 1, "", "not found")
            owner = new_owner if name == newest else old_owner if name == oldest else "f" * 32
            return subprocess.CompletedProcess(command, 0, json.dumps([{
                "Name": name,
                "CreatedAt": "2026-08-30T02:00:00Z" if name == newest else "2026-08-30T01:00:00Z",
                "Labels": {
                    module.OWNER_LABEL: owner, module.SCOPE_LABEL: "scope",
                    module.ROLE_LABEL: "neo4j-rollback",
                },
            }]), "")
        if command[:3] == ["docker", "volume", "rm"]:
            removed.append(command[-1]); return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    runner.run = run
    runner.prune_retained_rollbacks(set(), keep=1)
    assert removed == [oldest]
    assert newest not in removed and decoy not in removed


def test_backup_restart_compensation_failure_marks_boundary_for_poisoning():
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.token = "1" * 32; coordinator.poison_reason = None
    coordinator._service_state = lambda _service: module.DatabaseServiceState(True, True, True)
    coordinator._service_running = lambda _service: False
    coordinator.runner = types.SimpleNamespace(
        scope="scope", unique_name=lambda _role: "job", register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    coordinator.containers_disappeared_after_compose_run = lambda _name: None

    def compose(*args, **_kwargs):
        if args[0] == "up": raise module.ContractError("restart health failed")
        return subprocess.CompletedProcess(args, 0, "", "")

    coordinator.compose = compose
    with pytest.raises(module.ContractError, match="restart health"):
        coordinator.backup_neo4j("20260830_010203")
    assert coordinator.poison_reason and "backup restart" in coordinator.poison_reason


def test_backup_probe_signal_restarts_neo4j_and_propagates_cancellation():
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.token = "1" * 32; coordinator.poison_reason = None
    coordinator._service_state = lambda _service: module.DatabaseServiceState(True, True, True)
    coordinator._service_running = lambda _service: (_ for _ in ()).throw(
        module.SignalInterruption("received signal 15")
    )
    coordinator.runner = types.SimpleNamespace(
        scope="scope", unique_name=lambda _role: "job", register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    coordinator.containers_disappeared_after_compose_run = lambda _name: None
    compose_calls: list[tuple] = []
    coordinator.compose = lambda *args, **_kwargs: compose_calls.append(args)

    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator.backup_neo4j("20260830_010203")

    assert [call[0] for call in compose_calls] == ["stop", "up"]


def test_owned_container_cleanup_failure_preserves_signal_and_poisons(capsys):
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.poison_reason = None; coordinator.token = "1" * 32
    coordinator.runner = types.SimpleNamespace(
        scope="scope",
        unique_name=lambda _role: "owned-job",
        register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.SignalInterruption("received signal 15")
        ),
        remove_container=lambda _name: (_ for _ in ()).throw(
            module.ContractError("removal unproven")
        ),
    )

    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator._owned_run("fixture", ["ignored"])

    assert coordinator.poison_reason and "container cleanup" in coordinator.poison_reason
    assert "removal unproven" in capsys.readouterr().err


def test_rollback_preparation_cleanup_failure_preserves_signal(capsys):
    module = _module(); coordinator = _coordinator(module)
    coordinator._service_state = lambda _service: module.DatabaseServiceState(True, False, False)
    coordinator.compose = lambda *_args, **_kwargs: None
    coordinator._copy_volume = lambda *_args: (_ for _ in ()).throw(
        module.SignalInterruption("received signal 15")
    )
    coordinator._restore_exact_initial_states = lambda _enabled: None
    coordinator.runner = types.SimpleNamespace(
        create_volume=lambda _role: "rollback-volume",
        remove_volume=lambda _name: (_ for _ in ()).throw(
            module.ContractError("volume removal unproven")
        ),
    )

    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator.cutover({})

    assert coordinator.poison_reason and "rollback preparation" in coordinator.poison_reason
    assert "volume removal unproven" in capsys.readouterr().err


def test_live_recovery_failure_preserves_signal_and_poisons(capsys):
    module = _module(); coordinator = _coordinator(module)
    coordinator._service_state = lambda _service: module.DatabaseServiceState(True, False, False)
    coordinator.compose = lambda *_args, **_kwargs: None
    coordinator.runner = types.SimpleNamespace(
        create_volume=lambda role: role,
        remove_volume=lambda _name: None,
        assert_no_owned_containers=lambda: None,
    )

    def copy(_source, _target, role):
        if role == "neo4j-cutover":
            raise module.SignalInterruption("received signal 15")

    coordinator._copy_volume = copy
    coordinator._verify_volume_copy = lambda *_args: None
    coordinator.restore_rollback = lambda _enabled: (_ for _ in ()).throw(
        module.ContractError("rollback recovery unproven")
    )

    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator.cutover({})

    assert coordinator.poison_reason and "recovery" in coordinator.poison_reason
    assert "rollback recovery unproven" in capsys.readouterr().err


def test_backup_body_signal_survives_restart_failure(capsys):
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.token = "1" * 32; coordinator.poison_reason = None
    coordinator._service_state = lambda _service: module.DatabaseServiceState(True, True, True)
    coordinator._service_running = lambda _service: False
    coordinator.runner = types.SimpleNamespace(
        scope="scope", unique_name=lambda _role: "job", register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.SignalInterruption("received signal 15")
        ),
        remove_container=lambda _name: None,
    )
    coordinator.containers_disappeared_after_compose_run = lambda _name: None
    coordinator.compose = lambda *args, **_kwargs: (
        (_ for _ in ()).throw(module.ContractError("restart failed"))
        if args[0] == "up" else None
    )

    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator.backup_neo4j("20260830_010203")

    assert coordinator.poison_reason and "restart compensation" in coordinator.poison_reason
    assert "restart failed" in capsys.readouterr().err


def test_backup_probe_oserror_still_restarts_neo4j():
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.timeout = 5; coordinator.token = "1" * 32; coordinator.poison_reason = None
    coordinator._service_state = lambda _service: module.DatabaseServiceState(True, True, True)
    coordinator._service_running = lambda _service: (_ for _ in ()).throw(
        OSError("transient Docker probe failure")
    )
    coordinator.runner = types.SimpleNamespace(
        scope="scope", unique_name=lambda _role: "job", register_container=lambda _name: None,
        run=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    coordinator.containers_disappeared_after_compose_run = lambda _name: None
    compose_calls: list[tuple] = []
    coordinator.compose = lambda *args, **_kwargs: compose_calls.append(args)

    coordinator.backup_neo4j("20260830_010203")

    assert [call[0] for call in compose_calls] == ["stop", "up"]
    assert coordinator.poison_reason is None


def _prune_coordinator(module, *, both=False, run_error=None):
    coordinator = _coordinator(module, both=both)
    coordinator.token = "1" * 32
    running = {"neo4j-graph-db": True, "weaviate": True}
    coordinator._service_state = lambda service: module.DatabaseServiceState(
        True, running[service], running[service]
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
        if args[0] == "stop":
            running[args[-1]] = False
        elif args[0] == "up":
            running[args[-1]] = True
    coordinator.compose = compose
    coordinator._prune_compose = compose
    return coordinator


def test_snapshot_prune_body_signal_survives_restart_failure(capsys):
    module = _module()
    coordinator = _prune_coordinator(
        module, run_error=module.SignalInterruption("received signal 15")
    )
    def compose(*args, **_kwargs):
        if args[0] == "up":
            raise module.ContractError("restart failed")
        coordinator._prune_compose(*args)
    coordinator.compose = compose

    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator.prune_completed_database_snapshots()

    assert coordinator.poison_reason and "snapshot" in coordinator.poison_reason
    assert "restart failed" in capsys.readouterr().err


def test_snapshot_prune_cleanup_only_failure_poisons_and_raises():
    module = _module(); coordinator = _prune_coordinator(module)
    def compose(*args, **_kwargs):
        if args[0] == "up":
            raise module.ContractError("restart failed")
        coordinator._prune_compose(*args)
    coordinator.compose = compose

    with pytest.raises(module.ContractError, match="snapshot retention restart"):
        coordinator.prune_completed_database_snapshots()

    assert coordinator.poison_reason and "snapshot" in coordinator.poison_reason


def test_snapshot_prune_registers_compensation_before_effective_stop():
    module = _module(); coordinator = _prune_coordinator(module, both=True)
    calls: list[tuple] = []

    def compose(*args, **_kwargs):
        calls.append(args)
        if args[0] == "stop" and args[-1] == "neo4j-graph-db":
            coordinator._prune_compose(*args)
            raise module.SignalInterruption("received signal 2")
        coordinator._prune_compose(*args)

    coordinator.compose = compose
    with pytest.raises(module.SignalInterruption, match="signal 2"):
        coordinator.prune_completed_database_snapshots()

    assert [call[-1] for call in calls if call[0] == "up"] == ["neo4j-graph-db"]


def test_snapshot_prune_attempts_every_restart_after_body_signal(capsys):
    module = _module(); coordinator = _prune_coordinator(
        module, both=True, run_error=module.SignalInterruption("received signal 15")
    )
    calls: list[tuple] = []

    def compose(*args, **_kwargs):
        calls.append(args)
        if args[0] == "up" and args[-1] == "neo4j-graph-db":
            raise module.ContractError("neo restart failed")
        coordinator._prune_compose(*args)

    coordinator.compose = compose
    with pytest.raises(module.SignalInterruption, match="signal 15"):
        coordinator.prune_completed_database_snapshots()

    assert [call[-1] for call in calls if call[0] == "up"] == [
        "neo4j-graph-db", "weaviate"
    ]
    assert coordinator.poison_reason and "snapshot" in coordinator.poison_reason
    assert "neo restart failed" in capsys.readouterr().err


@pytest.mark.parametrize("validator", ["neo4j", "weaviate-volume", "weaviate-stage"])
def test_stage_container_cleanup_failure_preserves_signal(validator, capsys):
    module = _module(); coordinator = object.__new__(module.DatabaseCoordinator)
    coordinator.poison_reason = None; coordinator.timeout = 5
    coordinator.neo_user = "neo4j"; coordinator.neo_password = "secret"
    coordinator.weaviate_modules = ""; coordinator.stage = {}
    coordinator.runner = types.SimpleNamespace(
        create_volume=lambda _role: "stage-volume",
        remove_container=lambda _name: (_ for _ in ()).throw(
            module.ContractError("container removal unproven")
        ),
    )
    coordinator._start_owned = lambda *_args, **_kwargs: "stage-container"
    interruption = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        module.SignalInterruption("received signal 15")
    )
    coordinator._wait_exec = interruption
    coordinator._validate_weaviate_runtime = interruption

    with pytest.raises(module.SignalInterruption, match="signal 15"):
        if validator == "neo4j":
            coordinator._validate_neo4j_data_volume("volume", "role")
        elif validator == "weaviate-volume":
            coordinator._validate_weaviate_data_volume("volume", "role")
        else:
            coordinator.validate_weaviate_stage("artifacts", "stage", "snapshot")

    assert coordinator.poison_reason and "container cleanup" in coordinator.poison_reason
    assert "container removal unproven" in capsys.readouterr().err


def test_live_volume_creation_rejects_wrong_immediate_labels(monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location("atlas_database_live_test", LIVE_TEST)
    assert spec and spec.loader
    live = importlib.util.module_from_spec(spec); sys.modules[spec.name] = live; spec.loader.exec_module(live)
    calls = 0

    def run(*args, **_kwargs):
        nonlocal calls
        calls += 1
        if args[:3] == ("docker", "volume", "inspect"):
            return subprocess.CompletedProcess(args, 0, json.dumps([{
                "Name": args[-1], "Labels": {live.OWNER_LABEL: "wrong"},
            }]), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(live, "_run", run)
    owned = live.OwnedDocker("labels")
    with pytest.raises(AssertionError) as raised:
        owned.volume("source")
    assert calls == 3
    assert "cleanup could not be proven" in "\n".join(raised.value.__notes__)


def test_live_cleanup_preserves_primary_and_attempts_every_resource(
    monkeypatch: pytest.MonkeyPatch,
):
    spec = importlib.util.spec_from_file_location("atlas_database_live_cleanup", LIVE_TEST)
    assert spec and spec.loader
    live = importlib.util.module_from_spec(spec); sys.modules[spec.name] = live; spec.loader.exec_module(live)
    owned = live.OwnedDocker("cleanup-all")
    owned.networks.append(f"{owned.prefix}-network")
    owned.volumes.append(f"{owned.prefix}-volume")
    attempts = []

    def remove(kind, name):
        attempts.append((kind, name))
        if kind == "network":
            raise PermissionError("network cleanup denied")

    monkeypatch.setattr(owned, "_remove_owned", remove)
    primary = subprocess.TimeoutExpired(("docker", "volume", "create"), 20)
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        try:
            raise primary
        finally:
            owned.cleanup()
    assert [kind for kind, _name in attempts] == ["network", "volume"]
    assert "cleanup could not be proven" in "\n".join(raised.value.__notes__)


def test_restore_live_fixture_uses_owned_sentinel_not_blind_rmtree():
    text = VOLUME_TEST.read_text(encoding="utf-8")
    assert "restore-owner" in text
    assert "ignore_errors=True" not in text


@pytest.mark.parametrize(
    ("phase", "sent_signal"),
    [
        ("stop", signal.SIGHUP),
        ("copy", signal.SIGINT),
        ("validator", signal.SIGTERM),
        ("restart", signal.SIGTERM),
    ],
)
def test_real_signals_kill_ignoring_process_group_before_boundary_unlock(
    tmp_path: Path, phase: str, sent_signal: signal.Signals
):
    """Drive the real coordinator state machine around a real CommandRunner child."""
    harness = tmp_path / "signal_harness.py"
    ready = tmp_path / f"{phase}.ready"
    pids = tmp_path / f"{phase}.pids"
    cleaned = tmp_path / f"{phase}.cleaned"
    lock_path = tmp_path / f"{phase}.lock"
    harness.write_text(
        r'''
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

module_path, phase, ready_path, pids_path, cleaned_path, lock_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location("atlas_signal_database_orchestrator", module_path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
for handled in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(handled, module._signal_as_exception)

token = "7" * 32
lock = module.OwnedFileLock(Path(lock_path), token=token)
lock.acquire()
coordinator = object.__new__(module.DatabaseCoordinator)
coordinator.plan = module.SourcePlan(True, False)
coordinator.neo_live = "neo-live"
coordinator.weaviate_live = "weav-live"
coordinator.stage = {"neo4j": "neo-stage", "weaviate": "weav-stage"}
coordinator.was_running = {}
coordinator.initial_states = {}
coordinator.rollback = {}
coordinator.timeout = 10
coordinator.boundary_state = "pre-cutover"
coordinator.poison_reason = None
coordinator.cutover_started = False
coordinator._bounded_count = lambda *_args: 1
runner = module.CommandRunner(token=token, timeout=20, scope="signal-test")
runner.create_volume = lambda role: role
runner.remove_volume = lambda _name: None
runner.prune_retained_rollbacks = lambda *_args, **_kwargs: None
runner.assert_no_owned_containers = lambda: None
coordinator.runner = runner
running = True
injected = False

grandchild = (
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(60)"
)
blocking_child = (
    "import os,signal,subprocess,sys,time; from pathlib import Path; "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "signal.signal(signal.SIGINT, signal.SIG_IGN); "
    f"g=subprocess.Popen([sys.executable,'-c',{grandchild!r}]); "
    f"Path({pids_path!r}).write_text(str(os.getpid())+'\\n'+str(g.pid)+'\\n'); "
    f"Path({ready_path!r}).write_text('ready'); time.sleep(60)"
)

def block_once(target):
    global injected
    if phase == target and not injected:
        injected = True
        runner.run([sys.executable, "-c", blocking_child], timeout=20)

def service_state(_service):
    return module.DatabaseServiceState(True, running, running)

def compose(*args, **_kwargs):
    global running
    if args[0] == "stop":
        running = False
        block_once("stop")
    elif args[0] == "up":
        block_once("restart")
        running = True
    return subprocess.CompletedProcess(args, 0, "", "")

def copy(_source, _target, role):
    if role == "neo4j-cutover":
        block_once("copy")

def validate(_volume, _role):
    block_once("validator")

coordinator._service_state = service_state
coordinator.compose = compose
coordinator._copy_volume = copy
coordinator._verify_volume_copy = lambda *_args: None
coordinator._validate_neo4j_data_volume = validate
coordinator._validate_weaviate_data_volume = lambda *_args: None

exit_code = 0
try:
    coordinator.cutover({})
except BaseException:
    exit_code = 130
finally:
    deadline = time.monotonic() + 5
    owned_pids = [int(value) for value in Path(pids_path).read_text().splitlines()]
    while time.monotonic() < deadline:
        alive = []
        for pid in owned_pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            alive.append(pid)
        if not alive:
            break
        time.sleep(0.05)
    else:
        coordinator._mark_poison("owned signal child still exists")
        exit_code = 70
    Path(cleaned_path).write_text("children-absent-before-finalize")
    module.finalize_boundary_lock(lock, coordinator, retained=set())
raise SystemExit(exit_code)
''',
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(harness),
            str(ORCHESTRATOR),
            phase,
            str(ready),
            str(pids),
            str(cleaned),
            str(lock_path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not ready.exists() and process.poll() is None:
        time.sleep(0.05)
    assert ready.exists(), process.communicate(timeout=5)
    os.kill(process.pid, sent_signal)
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 130, (stdout, stderr)
    assert cleaned.read_text(encoding="utf-8") == "children-absent-before-finalize"
    for pid in [int(value) for value in pids.read_text().splitlines()]:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    if phase == "stop":
        assert "state=poisoned\n" in lock_path.read_text(encoding="utf-8")
    else:
        assert not lock_path.exists()


def test_timeout_sweeps_descendant_when_process_group_leader_exits_on_term(
    tmp_path: Path,
):
    """A TERM-responsive leader must not hide its TERM-ignoring descendant."""
    module = _module()
    runner = module.CommandRunner(token="8" * 32, timeout=1, scope="mixed-term")
    child_pid_path = tmp_path / "child.pid"
    descendant = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    leader = (
        "import signal,subprocess,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"child=subprocess.Popen([sys.executable,'-c',{descendant!r}]); "
        "signal.signal(signal.SIGTERM, signal.SIG_DFL); "
        f"Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    child_pid: int | None = None
    try:
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            runner.run([sys.executable, "-c", leader])
        elapsed = time.monotonic() - started
        assert 3.5 <= elapsed < 10
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("TERM-ignoring descendant survived CommandRunner cleanup")
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_cleanup_probe_failure_does_not_mask_primary_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    runner = module.CommandRunner(token="9" * 32, timeout=1, scope="probe-error")
    monkeypatch.setattr(
        module,
        "_wait_for_process_group_exit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("cleanup probe failed")
        ),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        runner.run([sys.executable, "-c", "import time; time.sleep(60)"])

    assert "cleanup probe failed" in capsys.readouterr().err


def test_unproven_group_cleanup_preserves_primary_error_and_poisons_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    runner = module.CommandRunner(token="a" * 32, timeout=1, scope="unproven")

    def unproven_cleanup(process):
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
        return False

    monkeypatch.setattr(module, "_terminate_owned_process_group", unproven_cleanup)
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run([sys.executable, "-c", "import time; time.sleep(60)"])

    lock = module.OwnedFileLock(tmp_path / "boundary.lock", token=runner.token)
    lock.acquire()
    coordinator = types.SimpleNamespace(poison_reason=None, runner=runner)
    module.finalize_boundary_lock(lock, coordinator, retained=set())
    assert "state=poisoned\n" in lock.path.read_text(encoding="utf-8")
    assert "process-group cleanup" in capsys.readouterr().err
