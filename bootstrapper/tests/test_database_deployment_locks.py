"""Cross-checkout database exclusion through real CLI admission and publication."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import types

import pytest

from tests.test_database_backup_repair_contracts import _module
from tests.test_database_backup_admission import host_backup_cli

SOURCE = Path(__file__).resolve().parents[2] / "services/backup/database_orchestrator.py"
TOKEN = "a" * 32
WORKER = r"""
import importlib.util, json, os, pathlib, pwd, subprocess, sys, types
spec = importlib.util.spec_from_file_location("worker_orchestrator", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
cfg = json.loads(sys.argv[2])
repo = pathlib.Path(cfg["repo"])
m.__file__ = str(repo / "services/backup/database_orchestrator.py")
pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_dir=cfg["account"])
for key in tuple(os.environ):
    if key.startswith(("BACKUP_", "ATLAS_", "COMPOSE_", "NEO4J_", "WEAVIATE_", "DOCKER_")) or key in {"PROJECT_NAME", "GRAPH_DB_AUTH"}:
        del os.environ[key]
os.environ.update(BACKUP_SOURCE="container", PROJECT_NAME=cfg.get("project", "shared"),
    WEAVIATE_SOURCE="disabled", BACKUP_TIMESTAMP="20261021_010203",
    BACKUP_RESTORE_MAINTENANCE_MODE="confirmed", DOCKER_CONTEXT=cfg.get("context", "alias-a"),
    COMPOSE_PROJECT_NAME=cfg.get("compose", "label-a"), HOME=str(repo), TMPDIR=str(repo), XDG_STATE_HOME=str(repo))
if cfg.get("override"):
    os.environ["ATLAS_DATABASE_LOCK_DIR"] = cfg["override"]
if cfg.get("volume_overrides"):
    os.environ.update(ATLAS_DATABASE_BACKUP_LIVE_INTEGRATION="1", ATLAS_DATABASE_BACKUP_TEST_TOKEN="a" * 32)
    for variable, role in cfg["volume_overrides"].items():
        os.environ[variable] = "atlas-it-" + "a" * 32 + "-" + role
def run(self, command, **kwargs):
    if command[:2] == ["docker", "info"]:
        if cfg.get("probe_failure") == "timeout":
            raise subprocess.TimeoutExpired("secret-host", 15)
        if cfg.get("probe_failure") == "exit":
            return subprocess.CompletedProcess(command, 1, "", "secret-host")
        return subprocess.CompletedProcess(command, 0, cfg.get("daemon", "fake-daemon-a"), "")
    return subprocess.CompletedProcess(command, 0, "", "")
def protected(self, timestamp):
    pathlib.Path(cfg["marker"]).write_text("entered")
    print("ENTER", flush=True)
    if sys.stdin.readline().strip() != "release":
        raise RuntimeError("missing release handshake")
    if cfg.get("poison"):
        self.poison_reason = "controlled uncertain cleanup"
    return set()
m.CommandRunner.run = run
m.CommandRunner.cleanup = lambda *a, **k: None
m.CommandRunner.assert_owned_volume = lambda *a, **k: None
m.DatabaseCoordinator.backup_neo4j = protected
m.DatabaseCoordinator.restore = protected
m.DatabaseCoordinator.containers_disappeared_after_compose_run = lambda *a: None
m.DatabaseCoordinator.prune_completed_database_snapshots = lambda *a: None
try:
    code = m.main([cfg.get("operation", "backup")])
except (m.ContractError, subprocess.TimeoutExpired) as exc:
    print(str(exc), file=sys.stderr)
    code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 64
raise SystemExit(code)
"""


def _config(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    account = tmp_path / "account"
    account.mkdir(mode=0o700, exist_ok=True)
    return {"repo": str(repo), "account": str(account), "marker": str(repo / "protected")}


def _worker(config):
    return subprocess.Popen([sys.executable, "-c", WORKER, str(SOURCE), json.dumps(config)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _entered(process):
    ready, _, _ = select.select([process.stdout], [], [], 10)
    assert ready, "worker did not reach bounded handshake"
    line = process.stdout.readline()
    assert line == "ENTER\n", (line, process.stderr.read())


@pytest.mark.parametrize("operations", [("backup", "backup"), ("backup", "restore"), ("restore", "backup")])
@pytest.mark.parametrize("override", [False, True])
def test_cross_checkout_cli_contends_before_protected_work(tmp_path, operations, override):
    first = _config(tmp_path, "first")
    second = _config(tmp_path, "second")
    first["operation"], second["operation"] = operations
    second.update(context="another-alias", compose="different-compose-label")
    if override:
        lock_dir = tmp_path / "common"
        lock_dir.mkdir(mode=0o700)
        first["override"] = second["override"] = str(lock_dir)
    holder = _worker(first)
    try:
        _entered(holder)
        contender = _worker(second)
        out, err = contender.communicate("release\n", timeout=10)
        assert contender.returncode == 64, (out, err)
        assert not Path(second["marker"]).exists()
        assert holder.poll() is None
    finally:
        out, err = holder.communicate("release\n", timeout=10)
        assert holder.returncode == 0, (out, err)


@pytest.mark.parametrize("difference", ["daemon", "project"])
def test_disjoint_resources_progress_while_other_checkout_holds(tmp_path, difference):
    first, second = _config(tmp_path, "first"), _config(tmp_path, "second")
    second[difference] = "independent"
    holder = _worker(first)
    try:
        _entered(holder)
        other = _worker(second)
        out, err = other.communicate("release\n", timeout=10)
        assert other.returncode == 0, (out, err)
        assert Path(second["marker"]).exists()
        assert holder.poll() is None
    finally:
        holder.communicate("release\n", timeout=10)
        assert holder.returncode == 0


@pytest.fixture
def shared_module(tmp_path, monkeypatch):
    module = _module()
    monkeypatch.setenv("ATLAS_DATABASE_LOCK_DIR", str(tmp_path / "locks"))
    return module


@pytest.mark.parametrize("state", ["active", "dead", "unknown", "poisoned", "invalid"])
def test_surviving_shared_lock_requires_manual_recovery_unchanged(shared_module, tmp_path, state):
    module = shared_module
    path = tmp_path / "resource.lock"
    holder = module.SharedResourceLock(path, token=TOKEN)
    holder.acquire()
    if state == "poisoned":
        holder.poison("uncertain cleanup")
    elif state == "dead":
        path.write_text(holder.content.replace(f"pid={holder.pid}", "pid=99999999"))
    elif state == "unknown":
        path.write_text(holder.content.replace(holder.start, "unknown-fingerprint"))
    elif state == "invalid":
        path.write_text("incomplete metadata")
    before = (path.stat().st_ino, path.read_bytes())
    contender = module.SharedResourceLock(path, token="b" * 32)
    with pytest.raises(module.ContractError, match="manual recovery"):
        contender.acquire()
    assert (path.stat().st_ino, path.read_bytes()) == before


def test_shared_disappearance_retries_publication(shared_module, tmp_path, monkeypatch):
    module = shared_module
    path = tmp_path / "resource.lock"
    path.write_text("disappearing old boundary")
    original = module.SharedResourceLock._reclaim_stale
    def disappear(lock):
        path.unlink()
        original(lock)
    monkeypatch.setattr(module.SharedResourceLock, "_reclaim_stale", disappear)
    lock = module.SharedResourceLock(path, token=TOKEN)
    lock.acquire()
    assert path.read_text() == lock.content
    lock.release()
    assert not path.exists()


def test_one_volume_overlap_unwinds_only_owned_earlier_keys(shared_module, tmp_path):
    module = shared_module
    paths = [tmp_path / name for name in ("a.lock", "b.lock", "c.lock", "d.lock")]
    holder = module.BoundaryLocks(paths[1:3], token=TOKEN)
    holder.acquire()
    original = {path: path.read_bytes() for path in paths[1:3]}
    contender = module.BoundaryLocks([paths[3], paths[1], paths[0]], token="b" * 32)
    with pytest.raises(module.ContractError):
        contender.acquire()
    assert not paths[0].exists() and not paths[3].exists()
    assert {path: path.read_bytes() for path in paths[1:3]} == original
    holder.release()


def test_aggregate_poison_attempts_every_key_after_foreign_replacement(shared_module, tmp_path):
    module = shared_module
    paths = [tmp_path / f"{index}.lock" for index in range(4)]
    boundary = module.BoundaryLocks(paths, token=TOKEN)
    boundary.acquire()
    paths[0].unlink()
    replacement = module.SharedResourceLock(paths[0], token="b" * 32)
    replacement.acquire()
    before = paths[0].read_bytes()
    with pytest.raises(module.ContractError, match="ownership changed"):
        boundary.poison("uncertain cleanup")
    assert paths[0].read_bytes() == before
    assert all("state=poisoned" in path.read_text() for path in paths[1:])
    boundary.release()
    assert all(path.exists() for path in paths)


@pytest.mark.parametrize("identity", ["", " ", "line1\nline2", "secret@host", "x" * 257])
def test_invalid_daemon_identity_is_secret_safe_and_creates_no_lock(shared_module, identity):
    module = shared_module
    runner = types.SimpleNamespace(run=lambda *a, **k: subprocess.CompletedProcess([], 0, identity, "credential-secret"))
    with pytest.raises(module.ContractError, match="Docker daemon identity") as raised:
        module.docker_daemon_identity(runner)
    assert "credential-secret" not in str(raised.value)
    assert not Path(os.environ["ATLAS_DATABASE_LOCK_DIR"]).exists()


@pytest.mark.parametrize("failure", ["exit", "timeout", "oserror"])
def test_failed_daemon_identity_fails_closed(shared_module, failure):
    module = shared_module
    def run(*args, **kwargs):
        assert kwargs["timeout"] == 15
        if failure == "timeout":
            raise subprocess.TimeoutExpired("secret-command", 15)
        if failure == "oserror":
            raise OSError("secret-endpoint")
        return subprocess.CompletedProcess([], 1, "fake-id", "secret-endpoint")
    with pytest.raises(subprocess.TimeoutExpired if failure == "timeout" else module.ContractError, match="Docker daemon identity") as raised:
        module.docker_daemon_identity(types.SimpleNamespace(run=run))
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("kind", ["relative", "symlink", "mode", "owner", "file"])
def test_shared_root_rejects_unsafe_operator_directory(shared_module, tmp_path, monkeypatch, kind):
    module = shared_module
    path = tmp_path / "locks"
    if kind == "relative":
        monkeypatch.setenv("ATLAS_DATABASE_LOCK_DIR", "relative-locks")
    elif kind == "file":
        path.write_text("not a directory")
    elif kind == "symlink":
        path.symlink_to(tmp_path, target_is_directory=True)
    else:
        path.mkdir(mode=0o755 if kind == "mode" else 0o700)
    if kind == "owner":
        monkeypatch.setattr(module.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(module.ContractError, match="ATLAS_DATABASE_LOCK_DIR"):
        module.database_lock_directory()


@pytest.mark.parametrize("state", ["active", "poisoned", "dead"])
@pytest.mark.parametrize("location", ["checkout", "override"])
def test_legacy_files_block_unchanged(shared_module, tmp_path, state, location):
    module = shared_module
    repo = tmp_path / "checkout"
    root = repo / "volumes/locks" if location == "checkout" else tmp_path / "locks"
    root.mkdir(parents=True, mode=0o700)
    legacy = root / "atlas-database-boundary-old.lock"
    legacy.write_text(f"state={state}\npid=99999999\n")
    before = legacy.read_bytes()
    with pytest.raises(module.ContractError, match="legacy"):
        module.reject_legacy_locks(repo, tmp_path / "locks")
    assert legacy.read_bytes() == before


def test_captured_routing_and_project_override_credentials_overlay(shared_module, tmp_path, monkeypatch):
    module = shared_module
    runner = module.CommandRunner(token=TOKEN, timeout=5)
    runner.environment = {"DOCKER_CONTEXT": "original", "PROJECT_NAME": "Literal", "COMPOSE_FILE": "original.yml", "COMPOSE_PROJECT_NAME": "scope", "PATH": "/fake"}
    overlay = {"DOCKER_CONTEXT": "redirected", "DOCKER_HOST": "secret-host", "PROJECT_NAME": "Other", "NEO4J_PASSWORD": "password"}
    result = runner.command_environment(overlay)
    assert result["DOCKER_CONTEXT"] == "original" and "DOCKER_HOST" not in result
    assert result["PROJECT_NAME"] == "Literal" and result["COMPOSE_FILE"] == "original.yml"
    assert result["NEO4J_PASSWORD"] == "password"


def test_current_context_selection_is_pinned_once(shared_module):
    module = shared_module
    calls = []
    runner = types.SimpleNamespace(environment={}, run=lambda command, **kwargs: (calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0, "selected-context\n", "")))
    module.pin_docker_selection(runner)
    assert runner.environment["DOCKER_CONTEXT"] == "selected-context"
    module.pin_docker_selection(runner)
    assert len(calls) == 1 and calls[0][1]["timeout"] == 15


def test_database_resource_keys_are_sorted_deduplicated_per_volume(shared_module):
    module = shared_module
    first = module.database_lock_paths("daemon-a", ("volume-a", "volume-b", "volume-a"))
    other = module.database_lock_paths("daemon-a", ("volume-c", "volume-b"))
    assert len(first) == 2 and first == sorted(first)
    assert len(set(first) & set(other)) == 1
    assert not set(first) & set(module.database_lock_paths("daemon-b", ("volume-a", "volume-b")))


def test_runbook_explains_shared_scope_and_legacy_migration():
    text = (SOURCE.parent / "README.md").read_text()
    assert "repository-scoped host lock" not in text
    for phrase in ("same client host", "same OS account", "daemon ID", "physical volume", "ATLAS_DATABASE_LOCK_DIR", "every historical checkout", "Stop all schedules", "context definitions", "manual recovery", "different hosts"):
        assert phrase in text


VOLUME_ROLES = {"ATLAS_NEO4J_LIVE_VOLUME": "neo-live", "ATLAS_WEAVIATE_LIVE_VOLUME": "weaviate-live", "ATLAS_NEO4J_BACKUP_VOLUME": "neo-backups", "ATLAS_WEAVIATE_BACKUP_VOLUME": "weaviate-backups"}


def test_one_physical_volume_overlap_blocks_real_cli_process(tmp_path):
    first, second = _config(tmp_path, "first"), _config(tmp_path, "second")
    overlap = {"ATLAS_NEO4J_LIVE_VOLUME": "neo-live"}
    first["volume_overrides"] = second["volume_overrides"] = overlap
    second["project"] = "otherwise-disjoint"
    holder = _worker(first)
    try:
        _entered(holder)
        contender = _worker(second)
        out, err = contender.communicate("release\n", timeout=10)
        assert contender.returncode == 64, (out, err)
        assert not Path(second["marker"]).exists()
    finally:
        holder.communicate("release\n", timeout=10)
        assert holder.returncode == 0


@pytest.mark.parametrize("variable", VOLUME_ROLES)
def test_finalized_poison_blocks_new_process_with_each_single_overlap(tmp_path, variable):
    first, second = _config(tmp_path, "first"), _config(tmp_path, "second")
    first.update(volume_overrides=VOLUME_ROLES, poison=True)
    second.update(project="otherwise-disjoint", volume_overrides={variable: VOLUME_ROLES[variable]})
    holder = _worker(first)
    _entered(holder)
    out, err = holder.communicate("release\n", timeout=10)
    assert holder.returncode == 64, (out, err)
    locks = list((Path(first["account"]) / ".atlas-database-locks").glob("*.lock"))
    assert len(locks) == 4 and all("state=poisoned" in path.read_text() for path in locks)
    before = {path: path.read_bytes() for path in locks}
    contender = _worker(second)
    out, err = contender.communicate("release\n", timeout=10)
    assert contender.returncode == 64 and not Path(second["marker"]).exists(), (out, err)
    assert {path: path.read_bytes() for path in locks} == before


@pytest.mark.parametrize("failure", ["exit", "timeout", "malformed"])
def test_cli_identity_failure_has_no_locks_or_protected_work(tmp_path, failure):
    config = _config(tmp_path, "first")
    if failure == "malformed":
        config["daemon"] = "two\nlines"
    else:
        config["probe_failure"] = failure
    process = _worker(config)
    out, err = process.communicate("release\n", timeout=10)
    assert process.returncode == (124 if failure == "timeout" else 64), (out, err)
    assert "secret-host" not in err
    assert not Path(config["marker"]).exists()
    assert not (Path(config["account"]) / ".atlas-database-locks").exists()


def test_snapshot_preserves_targets_and_compose_identity_after_edits(shared_module, tmp_path, monkeypatch):
    module = shared_module
    monkeypatch.setenv("PROJECT_NAME", "LiteralProject")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "custom-label")
    monkeypatch.setenv("COMPOSE_FILE", "original.yml")
    config = module.DatabaseConfiguration(tmp_path, {"GRAPH_DB_AUTH": "user/original"}, TOKEN)
    expected = tuple(config.volumes.values())
    monkeypatch.setenv("PROJECT_NAME", "redirected")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "redirected")
    monkeypatch.setenv("COMPOSE_FILE", "redirected.yml")
    (tmp_path / ".env").write_text("PROJECT_NAME=another\nGRAPH_DB_AUTH=user/changed\n")
    coordinator = module.DatabaseCoordinator(config, token=TOKEN, timeout=5)
    assert (coordinator.neo_live, coordinator.weaviate_live, coordinator.neo_backups, coordinator.weaviate_backups) == expected
    child = coordinator.runner.command_environment({"NEO4J_PASSWORD": "overlay"})
    assert child["PROJECT_NAME"] == "LiteralProject" and child["COMPOSE_PROJECT_NAME"] == "custom-label"
    assert child["COMPOSE_FILE"] == "original.yml" and child["NEO4J_PASSWORD"] == "overlay"
    assert coordinator.neo_password == "original"


@pytest.mark.parametrize("variable", VOLUME_ROLES)
def test_restricted_volume_override_fails_before_context_or_identity_probe(host_backup_cli, variable):
    state = host_backup_cli
    state.monkeypatch.setenv("BACKUP_SOURCE", "container")
    state.monkeypatch.setenv(variable, "unsafe-production-override")
    with pytest.raises(state.module.ContractError, match="restricted"):
        state.module.main(["backup"])
    assert state.events == []


def test_acquisition_signal_unwinds_current_published_and_earlier_keys(shared_module, tmp_path, monkeypatch):
    module = shared_module
    paths = [tmp_path / f"{index}.lock" for index in range(4)]
    original = module.SharedResourceLock.acquire
    def interrupted(lock):
        original(lock)
        if lock.path == paths[1]:
            raise module.SignalInterruption("controlled publication interruption")
    monkeypatch.setattr(module.SharedResourceLock, "acquire", interrupted)
    boundary = module.BoundaryLocks(paths, token=TOKEN)
    with pytest.raises(module.SignalInterruption):
        boundary.acquire()
    assert not any(path.exists() for path in paths)


def test_poison_failure_preserves_primary_and_keeps_unpoisoned_owned_key(shared_module, tmp_path, monkeypatch, capsys):
    module = shared_module
    paths = [tmp_path / f"{index}.lock" for index in range(4)]
    boundary = module.BoundaryLocks(paths, token=TOKEN)
    boundary.acquire()
    original = module.SharedResourceLock.poison
    def failing(lock, reason):
        if lock.path == paths[1]:
            raise OSError("controlled poison failure")
        original(lock, reason)
    monkeypatch.setattr(module.SharedResourceLock, "poison", failing)
    primary = module.ContractError("original operation failure")
    with pytest.raises(module.ContractError, match="original operation failure"):
        try:
            raise primary
        finally:
            boundary.poison("uncertain cleanup")
    boundary.release()
    assert all(path.exists() for path in paths)
    assert "state=active" in paths[1].read_text()
    assert all("state=poisoned" in path.read_text() for path in (paths[0], paths[2], paths[3]))
    assert "poison failed" in capsys.readouterr().err
