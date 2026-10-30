"""Host backup admission and the independent one-shot container guard."""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path
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


def _launch_pid_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _reap_launch_children(state):
    for pid in state.pids:
        try:
            reaped, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            continue
        if reaped:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
    assert not any(_launch_pid_exists(pid) for pid in state.pids)


@pytest.fixture
def launch_state(tmp_path, monkeypatch):
    module = _module()
    handled = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous = {sig: signal.signal(sig, module._signal_as_exception) for sig in handled}
    state = types.SimpleNamespace(
        module=module, path=tmp_path, pids=[], sent=signal.SIGTERM, phase="none", fired=False, signals=None,
        mask=signal.pthread_sigmask(signal.SIG_BLOCK, []), handled=handled,
        runner=module.CommandRunner(token="e" * 32, timeout=2, scope="launch-test"),
    )
    original_fork = subprocess._fork_exec
    original_clock = module.time.monotonic

    def fork(*args, **kwargs):
        pid = original_fork(*args, **kwargs)
        state.pids.append(pid)
        if state.phase == "fork":
            for sent in state.signals or (state.sent,):
                os.kill(os.getpid(), sent)
        return pid

    def clock():
        if state.phase == "runtime" and not state.fired:
            state.fired = True
            os.kill(os.getpid(), state.sent)
        return original_clock()

    monkeypatch.setattr(subprocess, "_fork_exec", fork)
    monkeypatch.setattr(module.time, "monotonic", clock)
    try:
        yield state
    finally:
        state.phase = "none"
        try:
            _reap_launch_children(state)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def _assert_launch_environment_restored(state):
    assert all(signal.getsignal(sig) == state.module._signal_as_exception for sig in state.handled)
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == state.mask


def _finalize_launch_boundary(state, lock):
    coordinator = types.SimpleNamespace(poison_reason=None, runner=state.runner, boundary_state="pre-cutover")
    state.module.finalize_boundary_lock(lock, coordinator, retained=set())


@pytest.mark.parametrize("sent", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
@pytest.mark.parametrize("input_text", (None, "payload"))
def test_command_launch_interruption_cleans_before_unlock(launch_state, sent, input_text):
    state = launch_state
    lock = state.module.OwnedFileLock(state.path / "boundary.lock", token=state.runner.token)
    lock.acquire()
    state.sent, state.phase = sent, "fork"
    with pytest.raises(state.module.SignalInterruption, match=f"received signal {sent}"):
        state.runner.run([sys.executable, "-c", "import time; time.sleep(30)"], input_text=input_text)
    assert not any(_launch_pid_exists(pid) for pid in state.pids)
    _assert_launch_environment_restored(state)
    _finalize_launch_boundary(state, lock)
    assert not lock.path.exists()
    assert state.runner.process_group_cleanup_failed is False


@pytest.mark.parametrize("input_text", (None, "payload"))
def test_runtime_interruption_keeps_existing_cleanup(launch_state, input_text):
    state = launch_state
    state.phase = "runtime"
    with pytest.raises(state.module.SignalInterruption, match="received signal 15"):
        state.runner.run([sys.executable, "-c", "import time; time.sleep(30)"], input_text=input_text)
    assert not any(_launch_pid_exists(pid) for pid in state.pids)
    _assert_launch_environment_restored(state)


def test_command_launch_failure_restores_handlers(launch_state):
    state = launch_state
    with pytest.raises(FileNotFoundError):
        state.runner.run([str(state.path / "missing-command")])
    assert not any(_launch_pid_exists(pid) for pid in state.pids)
    _assert_launch_environment_restored(state)
    assert state.runner.process_group_cleanup_failed is False


def test_command_child_inherits_original_signal_mask(launch_state):
    state = launch_state
    command = [sys.executable, "-c", "import json,signal; print(json.dumps(sorted(map(int,signal.pthread_sigmask(signal.SIG_BLOCK,[])))))"]
    result = state.runner.run(command)
    assert json.loads(result.stdout) == sorted(map(int, state.mask))
    assert result.args == command
    assert result.returncode == 0 and result.stderr == ""
    _assert_launch_environment_restored(state)


@pytest.mark.parametrize("input_text", (None, "payload"))
def test_command_timeout_keeps_command_identity(launch_state, input_text):
    state = launch_state
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(subprocess.TimeoutExpired) as captured:
        state.runner.run(command, timeout=1, input_text=input_text)
    assert captured.value.cmd == command
    assert not any(_launch_pid_exists(pid) for pid in state.pids)
    _assert_launch_environment_restored(state)


def test_command_input_and_output_remain_literal(launch_state):
    state = launch_state
    result = state.runner.run([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"], input_text="literal input\n")
    assert result.stdout == "literal input\n"
    assert result.stderr == "" and result.returncode == 0
    _assert_launch_environment_restored(state)


def test_command_launch_unproven_cleanup_preserves_lock(launch_state, monkeypatch):
    state = launch_state
    original_cleanup = state.module._terminate_owned_process_group

    def unproven(process):
        assert original_cleanup(process)
        return False

    monkeypatch.setattr(state.module, "_terminate_owned_process_group", unproven)
    lock = state.module.OwnedFileLock(state.path / "boundary.lock", token=state.runner.token)
    lock.acquire()
    state.phase = "fork"
    with pytest.raises(state.module.SignalInterruption, match="received signal 15"):
        state.runner.run([sys.executable, "-c", "import time; time.sleep(30)"])
    assert state.runner.process_group_cleanup_failed is True
    _finalize_launch_boundary(state, lock)
    assert "state=poisoned\n" in lock.path.read_text()
    _assert_launch_environment_restored(state)


def test_deferred_signal_precedes_later_popen_error(launch_state):
    state = launch_state
    state.phase = "fork"
    with pytest.raises(state.module.SignalInterruption, match="received signal 15"):
        state.runner.run([str(state.path / "missing-command")])
    assert not any(_launch_pid_exists(pid) for pid in state.pids)
    _assert_launch_environment_restored(state)


@pytest.mark.parametrize("earlier", ("body", "deferred"))
def test_launch_deferral_preserves_interruption_order(launch_state, monkeypatch, earlier):
    state = launch_state
    launch = state.module._RecoverySignalDeferral()
    original_mask = signal.pthread_sigmask
    fired = False

    def mask(how, values):
        nonlocal fired
        restored = signal.getsignal(signal.SIGTERM) == state.module._signal_as_exception
        if earlier == "body" and how == signal.SIG_SETMASK and restored and not fired:
            fired = True
            os.kill(os.getpid(), signal.SIGTERM)
        return original_mask(how, values)

    monkeypatch.setattr(signal, "pthread_sigmask", mask)
    with pytest.raises(KeyboardInterrupt):
        with launch:
            if earlier == "deferred":
                os.kill(os.getpid(), signal.SIGTERM)
            raise KeyboardInterrupt("body interruption")
    assert (launch.interruption is None) == (earlier == "body")
    assert launch.body_interruption_precedes_deferred == (earlier == "body")
    _assert_launch_environment_restored(state)


@pytest.mark.parametrize("signals", [(signal.SIGTERM,), (signal.SIGTERM, signal.SIGINT),
                                    (signal.SIGINT, signal.SIGTERM), (signal.SIGTERM, signal.SIGTERM)])
@pytest.mark.parametrize("input_text", (None, "payload"))
def test_command_launch_preserves_outer_recovery_owner(launch_state, signals, input_text):
    state = launch_state
    outer = state.module._RecoverySignalDeferral()
    state.phase, state.signals = "fork", signals
    with outer:
        result = state.runner.run([sys.executable, "-c", "print('compensation completed')"], input_text=input_text)
        assert result.stdout == "compensation completed\n"
    assert str(outer.interruption) == f"received signal {signals[0]}"
    assert result.returncode == 0 and result.stderr == ""
    assert not any(_launch_pid_exists(pid) for pid in state.pids)
    _assert_launch_environment_restored(state)


def test_command_launch_preserves_already_recorded_outer_signal(launch_state):
    state = launch_state
    outer = state.module._RecoverySignalDeferral()
    state.phase, state.signals = "fork", (signal.SIGINT,)
    with outer:
        os.kill(os.getpid(), signal.SIGTERM)
        result = state.runner.run([sys.executable, "-c", "print('compensation completed')"])
    assert result.stdout == "compensation completed\n"
    assert str(outer.interruption) == "received signal 15"
    _assert_launch_environment_restored(state)
