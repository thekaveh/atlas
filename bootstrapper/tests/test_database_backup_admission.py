"""Host backup admission and the independent one-shot container guard."""

from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest

from tests.test_database_backup_repair_contracts import BACKUP_ENTRYPOINT, _module


@pytest.fixture
def host_backup_cli(tmp_path, monkeypatch, capsys):
    """Run the real CLI against a synthetic checkout and controlled boundaries."""
    module = _module()
    repo = tmp_path / "checkout"
    repo.mkdir()
    monkeypatch.setattr(module, "__file__", str(repo / "services/backup/database_orchestrator.py"))
    for key in tuple(os.environ):
        if key.startswith(("BACKUP_", "ATLAS_", "COMPOSE_", "NEO4J_", "WEAVIATE_")) or key in {"PROJECT_NAME", "GRAPH_DB_AUTH"}:
            monkeypatch.delenv(key)
    monkeypatch.setattr(sys, "argv", ["database_orchestrator.py", "backup"])
    state = types.SimpleNamespace(
        module=module, repo=repo, events=[], running=True, fail_backup=False,
        monkeypatch=monkeypatch, capsys=capsys,
    )
    _control_host_backup_boundaries(state, monkeypatch)
    return state


def _control_host_backup_boundaries(state, monkeypatch):
    module = state.module
    original_init = module.DatabaseCoordinator.__init__

    def lock(config, token, timeout):
        config.command_runner(token, timeout)
        module.pin_docker_selection(config.runner)
        module.docker_daemon_identity(config.runner)
        state.events.append(("lock",))
        return types.SimpleNamespace(
            acquire=lambda: state.events.append(("acquire",)),
            release=lambda: state.events.append(("release",)),
            poison=lambda reason: state.events.append(("poison", reason)),
        )

    def coordinator_init(coordinator, *args, **kwargs):
        state.events.append(("coordinator",))
        original_init(coordinator, *args, **kwargs)

    def unexpected_process(*args, **kwargs):
        raise AssertionError(f"unexpected real process: {args}")

    monkeypatch.setattr(module, "deployment_boundary", lock)
    monkeypatch.setattr(module.DatabaseCoordinator, "__init__", coordinator_init)
    monkeypatch.setattr(module.signal, "signal", lambda *_args: state.events.append(("signal",)))
    monkeypatch.setattr(module.subprocess, "run", unexpected_process)
    monkeypatch.setattr(module.subprocess, "Popen", unexpected_process)
    _control_host_backup_commands(state, monkeypatch)


def _control_host_backup_commands(state, monkeypatch):
    module = state.module

    def run(_runner, command, **kwargs):
        state.events.append(("command", command, kwargs))
        if command[:3] == ["docker", "compose", "stop"]:
            state.running = False
        elif command[:3] == ["docker", "compose", "up"]:
            state.running = True
        elif command[-1] == "/scripts/offline-backup.sh":
            (state.repo / "snapshot.dump").write_text("controlled snapshot", encoding="utf-8")
        elif command[-1] == "/scripts/backup-all.sh" and state.fail_backup:
            raise module.ContractError("controlled backup failure")
        output = "fake-daemon" if command[:2] == ["docker", "info"] else "default" if command[:3] == ["docker", "context", "show"] else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(module.CommandRunner, "run", run)
    monkeypatch.setattr(module.CommandRunner, "_inspect_json", lambda *_args: None)
    monkeypatch.setattr(module.CommandRunner, "cleanup", lambda *_args, **_kw: state.events.append(("cleanup",)))
    monkeypatch.setattr(module.DatabaseCoordinator, "_service_state", lambda *_args: module.DatabaseServiceState(True, state.running, state.running))
    monkeypatch.setattr(module.DatabaseCoordinator, "prune_completed_database_snapshots", lambda *_args: state.events.append(("prune",)))


@pytest.mark.parametrize(("file_source", "exported_source"), [
    ("disabled", None), ("container", "disabled"), (None, None),
    ("", None), ("", ""), (None, "invalid"), ("localhost", None),
    ("CONTAINER", None), ("disabled", ""), ("container", ""),
])
def test_host_backup_rejects_unadmitted_source_before_any_boundary(
    host_backup_cli, file_source, exported_source,
):
    state = host_backup_cli
    if file_source is not None:
        (state.repo / ".env").write_text(f"BACKUP_SOURCE={file_source}\n", encoding="utf-8")
    if exported_source is not None:
        state.monkeypatch.setenv("BACKUP_SOURCE", exported_source)
    with pytest.raises(SystemExit) as raised:
        state.module._cli()
    assert raised.value.code == 64
    assert state.capsys.readouterr().err == (
        "database orchestrator: backup disabled; set BACKUP_SOURCE=container before running backup\n"
    )
    assert state.events == []
    assert state.running is True
    assert sorted(path.name for path in state.repo.iterdir()) == ([] if file_source is None else [".env"])
    assert "COMPOSE_PROJECT_NAME" not in os.environ
    assert "COMPOSE_FILE" not in os.environ


@pytest.mark.parametrize(("name", "value"), [
    ("PROJECT_NAME", "invalid project"),
    ("BACKUP_DATABASE_QUIESCE_TIMEOUT_SECONDS", "invalid"),
    ("BACKUP_COMMAND_TIMEOUT_SECONDS", "invalid"),
    ("NEO4J_GRAPH_DB_SOURCE", "invalid"),
])
def test_disabled_host_backup_precedes_irrelevant_configuration_errors(
    host_backup_cli, name, value,
):
    state = host_backup_cli
    (state.repo / ".env").write_text(f"BACKUP_SOURCE=disabled\n{name}={value}\n", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        state.module._cli()
    assert raised.value.code == 64
    assert "backup disabled; set BACKUP_SOURCE=container" in state.capsys.readouterr().err
    assert state.events == []
    assert sorted(path.name for path in state.repo.iterdir()) == [".env"]
    assert "COMPOSE_PROJECT_NAME" not in os.environ
    assert "COMPOSE_FILE" not in os.environ


@pytest.mark.parametrize(("file_source", "exported_source"), [
    ("disabled", "container"), ("container", None),
])
@pytest.mark.parametrize("sources", [
    ("container", "container"), ("container", "disabled"),
    ("disabled", "container"), ("disabled", "disabled"),
])
def test_enabled_host_backup_preserves_source_plan_and_cleanup(
    host_backup_cli, file_source, exported_source, sources,
):
    state = host_backup_cli
    neo, weaviate = sources
    (state.repo / ".env").write_text(
        f"BACKUP_SOURCE={file_source}\nNEO4J_GRAPH_DB_SOURCE={neo}\nWEAVIATE_SOURCE={weaviate}\n",
        encoding="utf-8",
    )
    if exported_source is not None:
        state.monkeypatch.setenv("BACKUP_SOURCE", exported_source)
    with pytest.raises(SystemExit) as raised:
        state.module._cli()
    assert raised.value.code == 0
    commands = [event[1] for event in state.events if event[0] == "command"]
    _assert_enabled_backup_job_sources(commands, sources)
    _assert_enabled_neo4j_snapshot_lifecycle(state, commands, neo)
    assert state.events[-3:] == [("prune",), ("cleanup",), ("release",)]


def _assert_enabled_backup_job_sources(commands, sources):
    neo, weaviate = sources
    job = next(command for command in commands if command[-1] == "/scripts/backup-all.sh")
    assert f"BACKUP_NEO4J_SOURCE={neo}" in job
    assert f"BACKUP_WEAVIATE_SOURCE={weaviate}" in job
    assert "BACKUP_DATABASES=true" in job


def _assert_enabled_neo4j_snapshot_lifecycle(state, commands, neo):
    assert any(command[-1] == "/scripts/offline-backup.sh" for command in commands) == (neo == "container")
    assert sum(command[:3] == ["docker", "compose", "stop"] for command in commands) == (neo == "container")
    assert sum(command[:3] == ["docker", "compose", "up"] for command in commands) == (neo == "container")
    assert (state.repo / "snapshot.dump").exists() == (neo == "container")
    assert state.running is True


def test_enabled_host_backup_preserves_failure_cleanup(host_backup_cli):
    state = host_backup_cli
    state.monkeypatch.setenv("BACKUP_SOURCE", "container")
    state.fail_backup = True
    with pytest.raises(SystemExit) as raised:
        state.module._cli()
    assert raised.value.code == 64
    assert "controlled backup failure" in state.capsys.readouterr().err
    assert state.running is True
    assert state.events[-2:] == [("cleanup",), ("release",)]
    assert ("prune",) not in state.events


def test_host_restore_retains_its_existing_admission_with_disabled_backup_source(
    host_backup_cli, monkeypatch,
):
    state = host_backup_cli
    monkeypatch.setattr(sys, "argv", ["database_orchestrator.py", "restore"])
    monkeypatch.setenv("BACKUP_SOURCE", "disabled")
    monkeypatch.setenv("NEO4J_GRAPH_DB_SOURCE", "disabled")
    monkeypatch.setenv("WEAVIATE_SOURCE", "disabled")
    monkeypatch.setenv("BACKUP_RESTORE_MAINTENANCE_MODE", "confirmed")
    monkeypatch.setenv("BACKUP_TIMESTAMP", "20261021_010203")
    with pytest.raises(SystemExit) as raised:
        state.module._cli()
    assert raised.value.code == 0
    assert ("coordinator",) in state.events
    assert state.events[-2:] == [("cleanup",), ("release",)]


def test_restore_test_token_validation_still_precedes_env_file_read(
    host_backup_cli, monkeypatch, capsys,
):
    state = host_backup_cli
    monkeypatch.setattr(sys, "argv", ["database_orchestrator.py", "restore"])
    monkeypatch.setenv("ATLAS_DATABASE_BACKUP_TEST_TOKEN", "invalid")

    def unexpected_env_read(_repo):
        raise AssertionError("restore token must be checked before reading .env")

    monkeypatch.setattr(state.module, "_env_file_values", unexpected_env_read)
    with pytest.raises(SystemExit) as raised:
        state.module._cli()
    assert raised.value.code == 64
    assert "test token requires explicit live integration mode" in state.capsys.readouterr().err
    assert state.events == []


@pytest.mark.parametrize("source", [None, "", "disabled", "invalid", "CONTAINER"])
def test_container_entrypoint_independently_rejects_unadmitted_sources(tmp_path, source):
    trace = tmp_path / "tools.trace"
    snapshot = tmp_path / "snapshot.dump"
    for name in ("openssl", "setsid", "uname", "sha256sum", "mc", "timeout", "sh", "docker", "pg_dump", "pg_restore"):
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >>"$TRACE"\nexit 97\n', encoding="utf-8")
        tool.chmod(0o755)
    target = tmp_path / "backup.sh"
    target.write_text('printf snapshot >"$SNAPSHOT"\n', encoding="utf-8")
    env = {"PATH": str(tmp_path), "TRACE": str(trace), "SNAPSHOT": str(snapshot), "BACKUP_COMMAND_TIMEOUT_SECONDS": "invalid"}
    if source is not None:
        env["BACKUP_SOURCE"] = source
    result = subprocess.run(["/bin/sh", str(BACKUP_ENTRYPOINT), str(target)], env=env, text=True, capture_output=True, check=False, timeout=5)
    assert result.returncode == 64
    assert result.stderr == "backup: disabled; set BACKUP_SOURCE=container before running backup or restore\n"
    assert not trace.exists()
    assert not snapshot.exists()
