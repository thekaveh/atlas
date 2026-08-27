"""Project-scoped `./stop.sh` must not kill host-global managed runtimes (#655).

Apple-Silicon/Metal ComfyUI-MPS and vLLM-Metal run as native HOST-GLOBAL
processes on fixed loopback ports, shared by every Atlas consumer on the
machine — not Compose-project resources. Stopping one project must therefore
leave them alone unless the operator passes the explicit `--stop-managed-hosts`
opt-in. These tests pin both directions for both runtimes.
"""
from __future__ import annotations

import click.testing
import pytest
import select
import signal
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS

import stop as stop_module
import services as services_module
from services import blender_mcp_manager, comfyui_mps_manager, managed_host, vllm_metal_manager
from services.blender_mcp_manager import BlenderMcpError, BlenderMcpManager, ProcessStatus
from services.managed_host import (
    HostProcessSpec,
    HostProcessStatus,
    ManagedHostError,
    ManagedHostManager,
)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Never touch the developer's real .env / host PIDs from a CLI test."""
    env = tmp_path / ".env"
    env.write_text("PROJECT_NAME=atlas\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))


class _HostManager:
    """Stub for a host-global managed runtime. Records stop() invocations and
    keeps its running/PID state so a test can assert it was left untouched."""

    def __init__(self, *, running: bool = True, pid: int = 4242):
        self.running = running
        self.pid = pid
        self.stop_calls = 0

    def status(self):
        return type(
            "Status", (), {"running": self.running, "pid": self.pid if self.running else None}
        )()

    def stop(self) -> bool:
        self.stop_calls += 1
        was_running = self.running
        self.running = False
        self.pid = None
        return was_running


def _patch_main_preamble(monkeypatch):
    """Drive main() straight to the managed-host step without touching Docker,
    the real .env, or project-name persistence."""
    monkeypatch.setattr(
        stop_module.AtlasStopper, "validate_persisted_project_name",
        lambda self, project_name: True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "persist_project_name", lambda self, name: None,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: project_name_override or "atlas",
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "ensure_dependencies_available", lambda self: True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "stop_services", lambda self, cold, project_name: True,
    )


def _install(monkeypatch, comfy, vllm, blender):
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: comfy)
    monkeypatch.setattr(vllm_metal_manager, "manager_from_env", lambda _env: vllm)
    monkeypatch.setattr(blender_mcp_manager, "manager_from_env", lambda _env: blender)


def _generic_lifecycle_manager(tmp_path):
    return ManagedHostManager(
        HostProcessSpec(name="guardrail-test", command=("sleep", "300"), port=8399),
        tmp_path,
    )


class _OwnedProcess:
    pid = 4242

    def __init__(self):
        self.wait_calls = 0

    def poll(self):
        return None

    def wait(self, **_kwargs):
        self.wait_calls += 1
        return 0


def test_stop_uses_live_owned_child_when_pid_file_is_missing(tmp_path, monkeypatch):
    manager = _generic_lifecycle_manager(tmp_path)
    signals = []
    owned = _OwnedProcess()
    manager._owned_process = owned
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        manager,
        "_signal",
        lambda pid, sig: signals.append((pid, sig)) or True,
    )
    monkeypatch.setattr(manager, "_managed_process_alive", lambda _pid: False)

    assert manager.stop() is True
    assert signals == [(4242, signal.SIGTERM)]
    assert owned.wait_calls == 1
    assert manager._owned_process is None
    assert not manager.pid_file.exists()


def test_leaderless_owned_child_authorizes_group_sweep(tmp_path, monkeypatch):
    manager = _generic_lifecycle_manager(tmp_path)
    swept = []
    owned = _OwnedProcess()
    manager._owned_process = owned
    manager._owned_group_pid = owned.pid
    monkeypatch.setattr(manager, "_group_survives", lambda _pid: True)
    monkeypatch.setattr(
        manager,
        "_sweep_orphaned_group",
        lambda pid: swept.append(pid) or True,
    )

    assert manager._finish_leaderless_stop(4242) is True
    assert swept == [4242]
    assert owned.wait_calls == 1
    assert manager._owned_process is None


def test_exited_owned_leader_preserves_group_sweep_authorization(
    tmp_path, monkeypatch
):
    manager = _generic_lifecycle_manager(tmp_path)
    swept = []

    class ExitedProcess:
        pid = 4242

        def poll(self):
            return 0

    manager._owned_process = ExitedProcess()
    manager._owned_group_pid = 4242
    monkeypatch.setattr(manager, "_group_survives", lambda _pid: True)
    monkeypatch.setattr(
        manager,
        "_sweep_orphaned_group",
        lambda pid: swept.append(pid) or True,
    )

    assert manager._finish_leaderless_stop(4242) is True
    assert swept == [4242]
    assert manager._owned_group_pid is None


def test_live_owned_child_wins_over_stale_dead_pidfile(tmp_path, monkeypatch):
    manager = _generic_lifecycle_manager(tmp_path)
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("999999\n", encoding="utf-8")
    owned = _OwnedProcess()
    manager._owned_process = owned
    signals = []
    monkeypatch.setattr(
        manager,
        "_signal",
        lambda pid, sig: signals.append((pid, sig)) or True,
    )
    monkeypatch.setattr(manager, "_managed_process_alive", lambda _pid: False)

    assert manager.stop() is True
    assert signals == [(owned.pid, signal.SIGTERM)]
    assert manager._owned_process is None
    assert not manager.pid_file.exists()


def test_exited_owned_child_cannot_bypass_reused_pid_guard(tmp_path, monkeypatch):
    manager = _generic_lifecycle_manager(tmp_path)
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\nstart_utc=old\n", encoding="utf-8")

    class ExitedProcess:
        pid = 4242

        def poll(self):
            return 0

    manager._owned_process = ExitedProcess()
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(manager, "_pid_is_stranger", lambda _pid: True)
    monkeypatch.setattr(
        manager,
        "_signal",
        lambda *_args: pytest.fail("reused PID must not be signalled"),
    )

    assert manager.stop() is False
    assert manager._owned_process is None
    assert manager.pid_file.exists()


@contextmanager
def _managed_child(*args, **kwargs):
    child = subprocess.Popen(*args, **kwargs)
    try:
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=5)


def test_start_is_serialized_across_processes(tmp_path):
    if managed_host.fcntl is None:
        pytest.skip("cross-process lifecycle locking is POSIX-only")
    manager = _generic_lifecycle_manager(tmp_path)
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    script = textwrap.dedent(f"""
        import pathlib, sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
        import services.managed_host as managed_host
        from services.managed_host import ManagedHostManager, HostProcessSpec
        spec = HostProcessSpec(name="guardrail-test", command=("sleep","300"), port=8399)
        m = ManagedHostManager(spec, pathlib.Path({str(tmp_path)!r}))
        real_flock = managed_host.fcntl.flock
        def observed_flock(fd, operation):
            print("ATTEMPT", flush=True)
            return real_flock(fd, operation)
        managed_host.fcntl.flock = observed_flock
        with m._lifecycle_lock():
            print("ACQUIRED", flush=True)
    """)
    with _managed_child(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    ) as child:
        with manager._lifecycle_lock():
            assert child.stdout is not None
            ready, _, _ = select.select([child.stdout], [], [], 5)
            assert ready, "child never attempted to acquire the lifecycle lock"
            assert child.stdout.readline().strip() == "ATTEMPT"
            assert child.poll() is None
        remaining = child.communicate(timeout=30)[0].strip().splitlines()
        assert remaining[-1] == "ACQUIRED"
        assert set(remaining[:-1]) <= {"ATTEMPT"}


def test_generic_start_rejects_foreign_port_before_spawn(tmp_path, monkeypatch):
    manager = _generic_lifecycle_manager(tmp_path)
    monkeypatch.setattr(manager, "status", lambda: NS(running=False, port_open=False))
    monkeypatch.setattr(manager, "_port_in_use", lambda: True)
    monkeypatch.setattr(
        manager, "_spawn", lambda: pytest.fail("occupied port reached spawn")
    )

    with pytest.raises(ManagedHostError, match="port 8399 is already in use"):
        manager.start(wait_timeout=0)


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_start_waits_for_existing_owned_process_endpoint(
    tmp_path, monkeypatch, manager_kind,
):
    if manager_kind == "generic":
        manager = _generic_lifecycle_manager(tmp_path / manager_kind)
        status = HostProcessStatus(running=True, pid=4242, port_open=False)
        error_type = ManagedHostError
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind, port=19876)
        status = ProcessStatus(running=True, pid=4242, port_open=False)
        error_type = BlenderMcpError
    monkeypatch.setattr(manager, "status", lambda: status)
    monkeypatch.setattr(manager, "health", lambda **_kwargs: {"reachable": False})

    with pytest.raises(error_type, match="did not become ready"):
        manager.start(wait_timeout=0)


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_existing_readiness_mismatch_never_grants_rollback_ownership(
    tmp_path, monkeypatch, manager_kind,
):
    if manager_kind == "generic":
        manager = _generic_lifecycle_manager(tmp_path / manager_kind)
        status = HostProcessStatus(running=True, pid=4242, port_open=True)
        error_type = ManagedHostError
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind, port=19876)
        status = ProcessStatus(running=True, pid=4242, port_open=True)
        error_type = BlenderMcpError
    monkeypatch.setattr(manager, "status", lambda: status)
    monkeypatch.setattr(
        manager,
        "health",
        lambda **_kwargs: {"reachable": True, "matched": False},
    )

    with pytest.raises(error_type) as raised:
        manager.start(wait_timeout=0)
    assert raised.value.surviving_process is False


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_post_admission_competing_listener_is_not_readiness(
    tmp_path, monkeypatch, manager_kind,
):
    polls = iter([None, 7])
    process = NS(pid=4242, poll=lambda: next(polls))
    if manager_kind == "generic":
        manager = _generic_lifecycle_manager(tmp_path / manager_kind)
        monkeypatch.setattr(manager, "_port_in_use", lambda **_kwargs: True)
        await_readiness = lambda: manager._await_port(process, 1)
        error_type = ManagedHostError
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind, port=19876)
        await_readiness = lambda: manager._await_spawned_readiness(process, 1)
        error_type = BlenderMcpError
    monkeypatch.setattr(manager, "health", lambda **_kwargs: {"reachable": True})

    if manager_kind == "generic":
        monkeypatch.setattr(manager, "_stop_locked", lambda: True)
        monkeypatch.setattr(manager, "_log_tail", lambda: "bind failed")
        with pytest.raises(error_type, match="did not become healthy"):
            await_readiness()
    else:
        assert await_readiness() is None


def test_spawned_readiness_requires_declared_semantic_match(tmp_path, monkeypatch):
    manager = _generic_lifecycle_manager(tmp_path)
    process = NS(pid=4242, poll=lambda: None)
    ticks = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(managed_host.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(managed_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        manager,
        "health",
        lambda **_kwargs: {"reachable": True, "matched": False},
    )
    monkeypatch.setattr(manager, "_stop_locked", lambda: True)
    monkeypatch.setattr(manager, "_log_tail", lambda: "wrong endpoint")

    with pytest.raises(ManagedHostError, match="did not become healthy"):
        manager._await_port(process, 1)


def test_live_child_cannot_adopt_foreign_tcp_listener(tmp_path, monkeypatch):
    manager = _generic_lifecycle_manager(tmp_path)
    process = NS(pid=4242, poll=lambda: None)
    ticks = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(managed_host.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(managed_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(manager, "health", lambda **_kwargs: {"reachable": True})
    monkeypatch.setattr(manager, "_spawned_endpoint_owned", lambda _pid: False)
    monkeypatch.setattr(manager, "_stop_locked", lambda: True)
    monkeypatch.setattr(manager, "_log_tail", lambda: "foreign listener")

    with pytest.raises(ManagedHostError, match="did not become healthy"):
        manager._await_port(process, 1)


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
@pytest.mark.parametrize(
    "case",
    [
        (phase, control_flow_type)
        for phase in ("poll", "health", "ownership", "sleep")
        for control_flow_type in (KeyboardInterrupt, SystemExit)
    ],
)
def test_readiness_control_flow_always_compensates(
    tmp_path, monkeypatch, manager_kind, case,
):
    phase, control_flow_type = case
    control_flow = control_flow_type(130)
    process = NS(pid=4242, poll=lambda: None)
    if manager_kind == "generic":
        manager = _generic_lifecycle_manager(tmp_path / manager_kind)
        start_readiness = lambda: manager._await_port(process, 1)
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind, port=19876)
        start_readiness = lambda: manager._await_spawned_readiness(process, 1)
    monkeypatch.setattr(manager, "health", lambda **_kwargs: {"reachable": False})
    if phase == "poll":
        process.poll = lambda: (_ for _ in ()).throw(control_flow)
    elif phase == "health":
        manager.health = lambda **_kwargs: (_ for _ in ()).throw(control_flow)
    elif phase == "ownership":
        manager.health = lambda **_kwargs: {"reachable": True}
        manager._spawned_endpoint_owned = (
            lambda _pid: (_ for _ in ()).throw(control_flow)
        )
    else:
        monkeypatch.setattr(
            managed_host.time,
            "sleep",
            lambda _seconds: (_ for _ in ()).throw(control_flow),
        )
    cleanup = []
    monkeypatch.setattr(manager, "_stop_locked", lambda: cleanup.append(True) or True)

    with pytest.raises(type(control_flow)) as raised:
        start_readiness()
    assert raised.value is control_flow
    assert cleanup == [True]


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_interrupted_readiness_warns_when_managed_process_survives(
    tmp_path, monkeypatch, capsys, manager_kind,
):
    process = NS(pid=4242, poll=lambda: None)
    if manager_kind == "generic":
        manager = _generic_lifecycle_manager(tmp_path / manager_kind)
        start_readiness = lambda: manager._await_port(process, 1)
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind, port=19876)
        start_readiness = lambda: manager._await_spawned_readiness(process, 1)
    interrupted = KeyboardInterrupt()
    monkeypatch.setattr(
        manager,
        "health",
        lambda **_kwargs: (_ for _ in ()).throw(interrupted),
    )
    monkeypatch.setattr(manager, "_stop_locked", lambda: False)
    monkeypatch.setattr(
        services_module,
        "tracked_process_may_survive",
        lambda _manager: (4242, True),
    )

    with pytest.raises(KeyboardInterrupt) as raised:
        start_readiness()

    assert raised.value is interrupted
    assert any("terminate the process manually" in note for note in raised.value.__notes__)
    assert "terminate the process manually" in capsys.readouterr().err


def test_blender_readiness_probe_exception_runs_launch_cleanup(tmp_path, monkeypatch):
    manager = BlenderMcpManager(tmp_path / "blender-probe", port=19876)
    manager.state_dir.mkdir(parents=True)
    manager.addon_path.write_bytes(b"addon")
    manager.launcher_path.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr(manager, "blender_binary", lambda: "/fake/blender")
    monkeypatch.setattr(manager, "_port_in_use", lambda: False)
    process = NS(pid=4242, poll=lambda: None)
    monkeypatch.setattr(blender_mcp_manager.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: "Mon Jan  1 00:00:00 2024"),
    )
    monkeypatch.setattr(
        manager,
        "health",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad response")),
    )
    cleanup = []
    monkeypatch.setattr(manager, "_stop_locked", lambda: cleanup.append(True) or True)

    with pytest.raises(
        BlenderMcpError, match="readiness probe failed: bad response"
    ) as raised:
        manager.start(wait_timeout=1)
    assert cleanup == [True]
    assert isinstance(raised.value.__cause__, ValueError)
    assert "within" not in str(raised.value)


def test_generic_readiness_probe_exception_preserves_cause_and_cleanup(
    tmp_path, monkeypatch,
):
    manager = _generic_lifecycle_manager(tmp_path / "generic-probe")
    process = NS(pid=4242, poll=lambda: None)
    monkeypatch.setattr(
        manager,
        "health",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad response")),
    )
    cleanup = []
    monkeypatch.setattr(manager, "_stop_locked", lambda: cleanup.append(True) or True)

    with pytest.raises(
        ManagedHostError, match="readiness probe failed: bad response"
    ) as raised:
        manager._await_port(process, 1)
    assert cleanup == [True]
    assert isinstance(raised.value.__cause__, ValueError)
    assert "within" not in str(raised.value)


def test_existing_blender_readiness_failure_is_never_rollback_owned(monkeypatch):
    import start as start_module

    class ExistingManager:
        bind = "127.0.0.1"
        port = 9876
        stop_calls = 0

        def ensure_running(self):
            raise BlenderMcpError(
                "existing endpoint unavailable", surviving_process=False
            )

        def stop(self):
            self.stop_calls += 1

    starter = start_module.AtlasStarter()
    manager = ExistingManager()
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "disabled",
            "VLLM_METAL_SOURCE": "disabled",
            "BLENDER_MCP_SOURCE": "managed-localhost",
        },
    )
    monkeypatch.setattr(blender_mcp_manager, "manager_from_env", lambda _env: manager)

    assert starter.start_managed_host_processes() is False
    assert manager.stop_calls == 0
    assert starter._managed_hosts_started_this_run == []


def test_blender_start_spawns_and_waits_for_semantic_health(tmp_path, monkeypatch):
    manager = BlenderMcpManager(tmp_path / "blender-start", port=19876)
    manager.state_dir.mkdir(parents=True)
    manager.addon_path.write_bytes(b"addon")
    manager.launcher_path.write_text("launcher", encoding="utf-8")
    monkeypatch.setattr(manager, "blender_binary", lambda: "/fake/blender")
    spawned = {}

    class _FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(argv, **_kwargs):
        spawned["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: "Mon Jan  1 00:00:00 2024"),
    )
    monkeypatch.setattr(blender_mcp_manager.subprocess, "Popen", fake_popen)
    ports = iter([False, False])
    monkeypatch.setattr(manager, "_port_in_use", lambda: next(ports))
    health = iter([{"reachable": False}, {"reachable": True}])
    monkeypatch.setattr(manager, "health", lambda **_kwargs: next(health))
    monkeypatch.setattr(manager, "_spawned_endpoint_owned", lambda _pid: True)

    status = manager.start()

    assert status.running and status.pid == 4242
    assert manager._read_pid() == 4242
    assert spawned["argv"][:2] == ["/fake/blender", "--background"]
    assert str(manager.launcher_path) in spawned["argv"]


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
@pytest.mark.parametrize("sweep_succeeds", [True, False])
def test_readiness_failure_retains_launch_ownership_for_group_cleanup(
    tmp_path, monkeypatch, manager_kind, sweep_succeeds,
):
    if manager_kind == "generic":
        manager = _generic_lifecycle_manager(tmp_path / manager_kind)
        error_type = ManagedHostError
        process = NS(pid=4242)
        monkeypatch.setattr(manager, "status", lambda: NS(running=False, port_open=False))
        monkeypatch.setattr(manager, "_spawn", lambda: process)
        monkeypatch.setattr(
            manager,
            "_write_pid_file",
            lambda pid: manager.pid_file.write_text(
                f"{pid}\nstart_utc=STAMP\n", encoding="utf-8"
            ),
        )
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind)
        error_type = BlenderMcpError
        manager.state_dir.mkdir(parents=True)
        manager.addon_path.write_text("addon", encoding="utf-8")
        manager.launcher_path.write_text("launcher", encoding="utf-8")
        polls = iter([None, 1])
        process = NS(pid=4242, poll=lambda: next(polls))
        monkeypatch.setattr(manager, "status", lambda: ProcessStatus(False))
        monkeypatch.setattr(manager, "blender_binary", lambda: "/fake/blender")
        monkeypatch.setattr(
            blender_mcp_manager.subprocess, "Popen", lambda *_args, **_kwargs: process
        )
        monkeypatch.setattr(
            ManagedHostManager, "_process_start_time", staticmethod(lambda _pid: "STAMP")
        )
    monkeypatch.setattr(manager, "_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(manager, "_managed_process_alive", lambda _pid: True)
    if manager_kind == "generic":
        monkeypatch.setattr(manager, "_group_survives", lambda _pid: True)
    swept: list[int] = []
    monkeypatch.setattr(
        manager, "_sweep_orphaned_group",
        lambda pid: swept.append(pid) or sweep_succeeds,
    )

    with pytest.raises(error_type) as excinfo:
        manager.start(wait_timeout=0)

    assert swept == [4242]
    assert excinfo.value.surviving_process is (not sweep_succeeds)
    assert manager._untracked_pid is (None if sweep_succeeds else 4242)


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
@pytest.mark.parametrize("missing", ["fcntl", "killpg", "sigterm", "sigkill"])
def test_unsupported_lifecycle_primitives_fail_preflight_and_never_spawn(
    tmp_path, monkeypatch, manager_kind, missing,
):
    if manager_kind == "generic":
        manager, module, error_type = (
            _generic_lifecycle_manager(tmp_path / manager_kind), managed_host,
            ManagedHostError,
        )
        monkeypatch.setattr(manager, "_spawn", lambda: pytest.fail("unsupported host spawned"))
    else:
        manager, module, error_type = (
            BlenderMcpManager(tmp_path / manager_kind), blender_mcp_manager,
            BlenderMcpError,
        )
        manager.state_dir.mkdir(parents=True)
        manager.addon_path.write_text("addon", encoding="utf-8")
        manager.launcher_path.write_text("launcher", encoding="utf-8")
        monkeypatch.setattr(manager, "blender_binary", lambda: "/fake/blender")
        monkeypatch.setattr(
            module.subprocess, "Popen",
            lambda *_args, **_kwargs: pytest.fail("unsupported host spawned"),
        )
    monkeypatch.setattr(manager, "_port_in_use", lambda: False)
    if missing == "fcntl":
        monkeypatch.setattr(module, "fcntl", None)
    elif missing == "killpg":
        monkeypatch.delattr(module.os, "killpg")
    else:
        monkeypatch.delattr(module.signal, missing.upper())

    preflight = manager.preflight()
    assert preflight.ok is False
    assert any(check["name"] == "lifecycle" for check in preflight.checks)
    with pytest.raises(error_type, match="lifecycle primitives"):
        manager.start(wait_timeout=0)


def test_blender_launch_lock_timeout_is_bounded(tmp_path, monkeypatch):
    if blender_mcp_manager.fcntl is None:
        pytest.skip("fcntl lock timeout is POSIX-only")
    manager = BlenderMcpManager(tmp_path)
    ticks = iter([0.0, 31.0])
    monkeypatch.setattr(blender_mcp_manager.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(blender_mcp_manager.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        blender_mcp_manager.fcntl, "flock",
        lambda *_args: (_ for _ in ()).throw(BlockingIOError()),
    )

    with pytest.raises(BlenderMcpError, match="timed out waiting"):
        manager.start()


# ── Default (opt-out) — never touch a host-global runtime ────────────────────

@pytest.mark.parametrize("args", [[], ["--cold"], ["--project", "consumer-b"]])
def test_default_stop_leaves_host_global_runtimes_running(monkeypatch, args):
    """AC#1/#2/#4/#6: a default project-scoped stop — standard, `--cold`, or a
    different `--project` (consumer B while consumer A owns/shares the runtime)
    — never stops the host-global ComfyUI-MPS / vLLM-Metal processes. PID and
    running state are unchanged, so A stays reachable."""
    _patch_main_preamble(monkeypatch)
    comfy = _HostManager(running=True, pid=111)
    vllm = _HostManager(running=True, pid=222)
    blender = _HostManager(running=True, pid=333)
    _install(monkeypatch, comfy, vllm, blender)

    result = click.testing.CliRunner().invoke(stop_module.main, args)

    assert result.exit_code == 0, result.output
    assert comfy.stop_calls == 0 and comfy.running and comfy.pid == 111
    assert vllm.stop_calls == 0 and vllm.running and vllm.pid == 222
    assert blender.stop_calls == 0 and blender.running and blender.pid == 333
    # An advisory points at the explicit opt-in.
    assert "stop-managed-hosts" in result.output


def test_default_stop_is_silent_when_no_managed_runtime_running(monkeypatch):
    """No advisory noise (and no teardown) when nothing host-global runs — e.g.
    the ComfyUI/vLLM sources are disabled for every consumer."""
    _patch_main_preamble(monkeypatch)
    comfy = _HostManager(running=False)
    vllm = _HostManager(running=False)
    blender = _HostManager(running=False)
    _install(monkeypatch, comfy, vllm, blender)

    result = click.testing.CliRunner().invoke(stop_module.main, [])

    assert result.exit_code == 0, result.output
    assert comfy.stop_calls == 0 and vllm.stop_calls == 0 and blender.stop_calls == 0
    assert "left running" not in result.output


# ── Explicit opt-in — deliberately stop, with a host-global warning ──────────

@pytest.mark.parametrize("args", [["--stop-managed-hosts"], ["--cold", "--stop-managed-hosts"]])
def test_explicit_flag_stops_both_managed_runtimes(monkeypatch, args):
    """AC#3/#4: `--stop-managed-hosts` (standard or `--cold`) deliberately stops
    both host-global runtimes and warns about the host-global impact."""
    _patch_main_preamble(monkeypatch)
    comfy = _HostManager(running=True)
    vllm = _HostManager(running=True)
    blender = _HostManager(running=True)
    _install(monkeypatch, comfy, vllm, blender)

    result = click.testing.CliRunner().invoke(stop_module.main, args)

    assert result.exit_code == 0, result.output
    assert comfy.stop_calls == 1 and not comfy.running
    assert vllm.stop_calls == 1 and not vllm.running
    assert blender.stop_calls == 1 and not blender.running
    # AC#3: clearly reports the host-global impact.
    assert "HOST-GLOBAL" in result.output


def test_explicit_flag_reports_failure_when_a_managed_host_survives(monkeypatch):
    """A host-global runtime that won't die under `--stop-managed-hosts` keeps
    the exit code nonzero (the real teardown result is surfaced)."""
    _patch_main_preamble(monkeypatch)

    class Stubborn(_HostManager):
        def stop(self) -> bool:  # never clears; status stays running
            self.stop_calls += 1
            return False

    comfy = Stubborn(running=True)
    vllm = _HostManager(running=True)
    blender = _HostManager(running=True)
    _install(monkeypatch, comfy, vllm, blender)

    result = click.testing.CliRunner().invoke(stop_module.main, ["--stop-managed-hosts"])

    assert result.exit_code == 1
    assert comfy.stop_calls == 1


def test_explicit_flag_reports_unknown_ownership_as_failure(
    monkeypatch, tmp_path,
):
    _patch_main_preamble(monkeypatch)

    class UnknownOwnership(_HostManager):
        def __init__(self):
            super().__init__(running=False)
            self.pid_file = tmp_path / "unknown.pid"
            self.pid_file.write_text("4242\n", encoding="utf-8")

        def stop(self) -> bool:
            self.stop_calls += 1
            return False

    unknown = UnknownOwnership()
    _install(monkeypatch, unknown, _HostManager(running=False), _HostManager(running=False))

    result = click.testing.CliRunner().invoke(stop_module.main, ["--stop-managed-hosts"])

    assert result.exit_code == 1
    assert unknown.pid_file.exists()
    assert "completed with errors" in result.output
