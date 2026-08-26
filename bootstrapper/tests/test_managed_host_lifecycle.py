from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
import click
from click.testing import CliRunner
import start as start_module
import services as services_package
from services.managed_host import (
    HostProcessSpec,
    ManagedHostError,
    ManagedHostManager,
    compensate_failed_launch,
    raise_launch_recording_failure,
)
from services import LaunchCompensation, tracked_process_may_survive


REPO_ROOT = Path(__file__).resolve().parents[2]


def _generic_manager(tmp_path: Path) -> ManagedHostManager:
    return ManagedHostManager(
        HostProcessSpec(
            name="compensation-test",
            command=("sleep", "300"),
            port=8399,
        ),
        tmp_path,
    )


def test_failed_identity_compensation_preserves_pid_evidence(tmp_path, monkeypatch):
    manager = _generic_manager(tmp_path)
    fake = type("FakeProcess", (), {"pid": 999_997})()
    monkeypatch.setattr(ManagedHostManager, "_spawn", lambda self: fake)
    monkeypatch.setattr(
        ManagedHostManager,
        "_write_pid_file",
        lambda self, _pid: (_ for _ in ()).throw(ManagedHostError("no identity")),
    )
    monkeypatch.setattr(
        ManagedHostManager, "_terminate_untracked", staticmethod(lambda _process: False)
    )

    with pytest.raises(ManagedHostError, match="could not be terminated"):
        manager.start(wait_timeout=1.0)

    assert manager.pid_file.read_text(encoding="utf-8").splitlines() == [str(fake.pid)]


def test_failed_identity_and_evidence_write_retains_pid_in_memory(
    tmp_path, monkeypatch,
):
    manager = _generic_manager(tmp_path)
    fake = type("FakeProcess", (), {"pid": 999_990})()
    monkeypatch.setattr(ManagedHostManager, "_spawn", lambda self: fake)
    monkeypatch.setattr(
        ManagedHostManager,
        "_write_pid_file",
        lambda self, _pid: (_ for _ in ()).throw(ManagedHostError("no identity")),
    )
    monkeypatch.setattr(
        ManagedHostManager, "_terminate_untracked", staticmethod(lambda _process: False)
    )
    monkeypatch.setattr(
        services_package,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: True)

    with pytest.raises(ManagedHostError, match="could not be terminated"):
        manager.start(wait_timeout=1.0)

    assert manager._untracked_pid == fake.pid
    assert tracked_process_may_survive(manager) == (fake.pid, True)


def test_blender_failed_identity_and_evidence_write_retains_pid_in_memory(
    tmp_path, monkeypatch,
):
    from services import blender_mcp_manager, managed_host
    from services.blender_mcp_manager import (
        BlenderMcpError,
        BlenderMcpManager,
        ProcessStatus,
    )

    manager = BlenderMcpManager(tmp_path)
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.addon_path.write_text("addon", encoding="utf-8")
    manager.launcher_path.write_text("launcher", encoding="utf-8")
    fake = type("FakeProcess", (), {"pid": 999_989, "poll": lambda self: None})()
    monkeypatch.setattr(manager, "status", lambda: ProcessStatus(False))
    monkeypatch.setattr(manager, "_port_in_use", lambda: False)
    monkeypatch.setattr(manager, "blender_binary", lambda: "/bin/blender")
    monkeypatch.setattr(blender_mcp_manager.subprocess, "Popen", lambda *_a, **_k: fake)
    monkeypatch.setattr(
        ManagedHostManager, "_process_start_time", staticmethod(lambda _pid: None)
    )
    monkeypatch.setattr(
        ManagedHostManager, "_terminate_untracked", staticmethod(lambda _process: False)
    )
    monkeypatch.setattr(
        services_package,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(managed_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: True)

    with pytest.raises(BlenderMcpError, match="could not be terminated"):
        manager.start(wait_timeout=1.0)

    assert manager._untracked_pid == fake.pid
    assert tracked_process_may_survive(manager) == (fake.pid, True)


def test_identity_interrupt_compensates_and_propagates(tmp_path, monkeypatch):
    manager = _generic_manager(tmp_path)
    fake = type("FakeProcess", (), {"pid": 999_996})()
    terminated: list[int] = []
    monkeypatch.setattr(ManagedHostManager, "_spawn", lambda self: fake)
    monkeypatch.setattr(
        ManagedHostManager,
        "_write_pid_file",
        lambda self, _pid: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    monkeypatch.setattr(
        ManagedHostManager,
        "_terminate_untracked",
        staticmethod(lambda process: terminated.append(process.pid) or True),
    )

    with pytest.raises(KeyboardInterrupt):
        manager.start(wait_timeout=1.0)

    assert terminated == [fake.pid]


def test_terminate_untracked_reports_failed_kill_send_and_wait(monkeypatch):
    class StubbornProcess:
        pid = 999_995

        def poll(self):
            return None

        def send_signal(self, _sig):
            raise OSError("denied")

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("stubborn", timeout)

    monkeypatch.setattr(
        os, "killpg", lambda *_args: (_ for _ in ()).throw(OSError("denied"))
    )

    assert ManagedHostManager._terminate_untracked(StubbornProcess()) is False


def test_terminate_untracked_rejects_leader_exit_with_surviving_group(monkeypatch):
    class LeaderExits:
        pid = 999_988

        def poll(self):
            return None

        def send_signal(self, _sig):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(os, "killpg", lambda *_args: None)
    monkeypatch.setattr(
        ManagedHostManager,
        "_sweep_orphaned_group",
        staticmethod(lambda _pid: False),
    )

    assert ManagedHostManager._terminate_untracked(LeaderExits()) is False


@pytest.mark.parametrize("control_flow", [KeyboardInterrupt(), SystemExit(130)])
def test_failed_interrupt_compensation_retains_evidence_and_warning(
    tmp_path, monkeypatch, control_flow, capsys,
):
    manager = _generic_manager(tmp_path)
    fake = type("FakeProcess", (), {"pid": 999_994})()
    monkeypatch.setattr(ManagedHostManager, "_spawn", lambda self: fake)
    monkeypatch.setattr(
        ManagedHostManager,
        "_write_pid_file",
        lambda self, _pid: (_ for _ in ()).throw(control_flow),
    )
    monkeypatch.setattr(
        ManagedHostManager, "_terminate_untracked", staticmethod(lambda _process: False)
    )

    with pytest.raises(type(control_flow)) as raised:
        manager.start(wait_timeout=1.0)

    assert manager.pid_file.read_text(encoding="utf-8").splitlines() == [str(fake.pid)]
    assert any(
        f"terminate pid {fake.pid} manually" in note.lower()
        for note in getattr(raised.value, "__notes__", [])
    )
    assert f"terminate pid {fake.pid} manually" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    ("control_flow", "expected_exit"),
    [(KeyboardInterrupt(), 1), (SystemExit(130), 130)],
)
def test_cli_keeps_failed_interrupt_compensation_warning_visible(
    tmp_path, monkeypatch, control_flow, expected_exit,
):
    manager = _generic_manager(tmp_path)
    fake = type("FakeProcess", (), {"pid": 999_991})()
    monkeypatch.setattr(ManagedHostManager, "_spawn", lambda self: fake)
    monkeypatch.setattr(
        ManagedHostManager,
        "_write_pid_file",
        lambda self, _pid: (_ for _ in ()).throw(control_flow),
    )
    monkeypatch.setattr(
        ManagedHostManager, "_terminate_untracked", staticmethod(lambda _process: False)
    )

    @click.command()
    def command():
        manager.start(wait_timeout=1.0)

    result = CliRunner().invoke(command)

    assert result.exit_code == expected_exit
    assert f"terminate pid {fake.pid} manually" in result.output.lower()


def test_compensation_cleanup_exceptions_are_reported_not_raised(tmp_path):
    pid_file = tmp_path / "managed.pid"

    outcome = compensate_failed_launch(
        999_993,
        pid_file,
        lambda: (_ for _ in ()).throw(RuntimeError("kill failed")),
    )

    assert outcome.terminated is False
    assert outcome.evidence == "pid"
    assert any("kill failed" in error for error in outcome.cleanup_errors)


def test_compensation_unlink_exception_is_reported_not_raised():
    class UnlinkFailure:
        def unlink(self, *, missing_ok=False):
            raise OSError("unlink failed")

    outcome = compensate_failed_launch(999_992, UnlinkFailure(), lambda: True)

    assert outcome.terminated is True
    assert any("unlink failed" in error for error in outcome.cleanup_errors)


@pytest.mark.parametrize("control_flow", [KeyboardInterrupt(), SystemExit(130)])
def test_control_flow_reports_stale_evidence_after_successful_termination(
    control_flow, capsys,
):
    outcome = LaunchCompensation(
        terminated=True,
        cleanup_errors=("OSError: could not unlink stale pid file",),
    )

    with pytest.raises(type(control_flow)) as raised:
        raise_launch_recording_failure(
            control_flow,
            4242,
            outcome,
            ("test identity", ManagedHostError),
        )

    assert raised.value is control_flow
    assert "could not unlink stale pid file" in capsys.readouterr().err


@dataclass
class _Status:
    running: bool
    pid: int | None = None
    port: int = 8000
    log_file: str = "/tmp/managed-host.log"


class _Manager:
    def __init__(
        self, *, running: bool = False, fail_start: Exception | None = None,
        confirm: bool = True,
    ):
        self.running = running
        self.fail_start = fail_start
        self.confirm = confirm
        self.stop_calls = 0

    def status(self) -> _Status:
        return _Status(self.running, 42 if self.running else None)

    def ensure_running_with_ownership(self) -> tuple[_Status, bool]:
        if self.fail_start is not None:
            raise self.fail_start
        created = not self.running
        self.running = True
        return self.status(), created

    def wait_healthy(self, **_kwargs) -> dict[str, object]:
        return {"reachable": True, "device": "mps"}

    def confirm_started_process(self, pid: int) -> bool:
        return self.confirm and self.running and pid == 42

    def stop(self) -> bool:
        self.stop_calls += 1
        was_running = self.running
        self.running = False
        return was_running


def test_generate_service_configuration_does_not_launch_native_hosts(monkeypatch):
    starter = start_module.AtlasStarter()
    monkeypatch.setattr(starter.service_config, "generate_and_update_env", lambda: True)
    for name in (
        "_finalize_consumer_storage",
        "_finalize_consumer_litellm_models",
        "_finalize_consumer_n8n_workflows",
        "_finalize_consumer_rag_ingestion_profiles",
        "_finalize_consumer_lightrag_query_profiles",
    ):
        monkeypatch.setattr(starter, name, lambda: True)
    monkeypatch.setattr(
        starter,
        "_finalize_managed_comfyui_mps",
        lambda: (_ for _ in ()).throw(AssertionError("configuration launched ComfyUI")),
    )
    monkeypatch.setattr(
        starter,
        "_finalize_managed_vllm_metal",
        lambda: (_ for _ in ()).throw(AssertionError("configuration launched vLLM")),
    )

    assert starter.generate_service_configuration() is True


def test_second_managed_host_failure_rolls_back_only_newly_started_host(monkeypatch):
    from services import comfyui_mps_manager, vllm_metal_manager

    starter = start_module.AtlasStarter()
    comfy = _Manager()
    vllm = _Manager(fail_start=vllm_metal_manager.VllmMetalError("boom"))
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "managed-localhost-mps",
            "VLLM_METAL_SOURCE": "managed-localhost",
            "VLLM_METAL_MODEL": "example/model",
        },
    )
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: comfy)
    monkeypatch.setattr(vllm_metal_manager, "manager_from_env", lambda _env: vllm)

    assert starter.start_managed_host_processes() is False
    assert comfy.stop_calls == 1
    assert comfy.running is False


@pytest.mark.parametrize("manager_kind", ["comfyui", "vllm"])
def test_managed_engine_exit_after_health_rolls_back_current_launch(
    monkeypatch, manager_kind,
):
    from services import comfyui_mps_manager, vllm_metal_manager

    starter = start_module.AtlasStarter()
    manager = _Manager(confirm=False)
    env = {
        "COMFYUI_SOURCE": (
            "managed-localhost-mps" if manager_kind == "comfyui" else "disabled"
        ),
        "VLLM_METAL_SOURCE": (
            "managed-localhost" if manager_kind == "vllm" else "disabled"
        ),
        "VLLM_METAL_MODEL": "example/model",
    }
    monkeypatch.setattr(starter.config_parser, "parse_env_file", lambda: env)
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: manager)
    monkeypatch.setattr(vllm_metal_manager, "manager_from_env", lambda _env: manager)

    assert starter.start_managed_host_processes() is False
    assert manager.stop_calls == 1 and manager.running is False


def test_unexpected_second_host_error_still_rolls_back_first(monkeypatch):
    from services import comfyui_mps_manager, vllm_metal_manager

    starter = start_module.AtlasStarter()
    comfy = _Manager()
    vllm = _Manager(fail_start=RuntimeError("unexpected"))
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "managed-localhost-mps",
            "VLLM_METAL_SOURCE": "managed-localhost",
        },
    )
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: comfy)
    monkeypatch.setattr(vllm_metal_manager, "manager_from_env", lambda _env: vllm)

    with pytest.raises(RuntimeError, match="unexpected"):
        starter.start_managed_host_processes()

    assert comfy.stop_calls == 1
    assert comfy.running is False


def test_surviving_untracked_child_is_added_to_rollback_ownership(monkeypatch):
    from services import comfyui_mps_manager

    starter = start_module.AtlasStarter()
    failure = comfyui_mps_manager.ComfyUiMpsError(
        "metadata and compensation failed", surviving_process=True
    )
    comfy = _Manager(running=True, fail_start=failure)
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "managed-localhost-mps",
            "VLLM_METAL_SOURCE": "disabled",
        },
    )
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: comfy)

    assert starter.start_managed_host_processes() is False
    assert comfy.stop_calls == 1
    assert comfy.running is False


def test_blender_surviving_child_is_added_to_rollback_ownership(monkeypatch):
    from services import blender_mcp_manager

    starter = start_module.AtlasStarter()
    failure = blender_mcp_manager.BlenderMcpError(
        "metadata and compensation failed", surviving_process=True
    )

    class _BlenderManager(_Manager):
        def ensure_running(self):
            raise failure

    blender = _BlenderManager(running=True)
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "disabled",
            "VLLM_METAL_SOURCE": "disabled",
            "BLENDER_MCP_SOURCE": "managed-localhost",
        },
    )
    monkeypatch.setattr(blender_mcp_manager, "manager_from_env", lambda _env: blender)

    assert starter.start_managed_host_processes() is False
    assert blender.stop_calls == 1
    assert blender.running is False


def test_rollback_preserves_unknown_live_pid_evidence(monkeypatch):
    starter = start_module.AtlasStarter()

    class _RefusingManager(_Manager):
        def stop(self) -> bool:
            self.stop_calls += 1
            return True

        def status(self) -> _Status:
            return _Status(False)

        def _read_pid(self) -> int:
            return 4242

        @staticmethod
        def _pid_alive(_pid: int) -> bool:
            return False

        @staticmethod
        def _managed_process_alive(_pid: int) -> bool:
            return True

    manager = _RefusingManager(running=True)
    starter._managed_hosts_started_this_run = [("test", manager)]
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        starter.banner, "show_status_message",
        lambda message, level: messages.append((message, level)),
    )

    assert starter.rollback_managed_host_processes() is False
    assert starter._managed_hosts_started_this_run == [("test", manager)]
    assert any("still" in message.lower() and level == "warning"
               for message, level in messages)
    assert not any("rolled back" in message.lower() for message, _level in messages)


@pytest.mark.parametrize("manager_kind", ["generic", "comfyui", "vllm", "blender"])
def test_remove_preserves_state_for_unknown_live_pid(tmp_path, monkeypatch, manager_kind):
    from services.blender_mcp_manager import BlenderMcpError, BlenderMcpManager
    from services.comfyui_mps_manager import ComfyUiMpsError, ComfyUiMpsManager
    from services.vllm_metal_manager import VllmMetalError, VllmMetalManager

    if manager_kind == "generic":
        manager = _generic_manager(tmp_path / manager_kind)
        error_type = ManagedHostError
        monkeypatch.setattr(manager, "stop", lambda: False)
    elif manager_kind == "comfyui":
        manager = ComfyUiMpsManager(tmp_path / manager_kind)
        error_type = ComfyUiMpsError
        monkeypatch.setattr(manager, "_stop_locked", lambda: False)
    elif manager_kind == "vllm":
        manager = VllmMetalManager(tmp_path / manager_kind)
        error_type = VllmMetalError
        monkeypatch.setattr(manager, "_stop_locked", lambda: False)
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind)
        error_type = BlenderMcpError
        monkeypatch.setattr(manager, "stop", lambda: False)

    manager.state_dir.mkdir(parents=True)
    marker = manager.state_dir / "installed.marker"
    marker.write_text("keep", encoding="utf-8")
    manager.pid_file.write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr(manager, "_read_pid", lambda: 4242)
    if manager_kind in {"comfyui", "vllm"}:
        monkeypatch.setattr(manager, "_pid_alive", lambda _pid: False)
        monkeypatch.setattr(manager, "_managed_process_alive", lambda _pid: True)
    else:
        monkeypatch.setattr(manager, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        manager, "status",
        lambda: SimpleNamespace(running=False, pid=None),
    )

    with pytest.raises(error_type, match="refus|still|alive"):
        manager.remove()

    assert marker.read_text(encoding="utf-8") == "keep"
    assert manager.pid_file.exists()


@pytest.mark.parametrize(
    ("args", "factory_name"),
    [
        (("comfyui-mps", "stop"), "_comfyui_mps_manager"),
        (("vllm-metal", "stop"), "_vllm_metal_manager"),
        (("blender-mcp", "stop"), "_blender_mcp_manager"),
        (("managed-host", "stop", "example"), "_managed_host_manager"),
    ],
)
def test_manual_stop_exits_nonzero_when_tracked_pid_survives(
    monkeypatch, args, factory_name,
):
    class _RefusingManager:
        def stop(self) -> bool:
            return True

        def _read_pid(self) -> int:
            return 4242

        @staticmethod
        def _pid_alive(_pid: int) -> bool:
            return False

        @staticmethod
        def _managed_process_alive(_pid: int) -> bool:
            return True

    manager = _RefusingManager()
    if factory_name == "_managed_host_manager":
        monkeypatch.setattr(start_module, factory_name, lambda _name: manager)
    else:
        monkeypatch.setattr(start_module, factory_name, lambda: manager)

    result = CliRunner().invoke(start_module.main, list(args))

    assert result.exit_code == 1
    assert "4242" in result.output
    assert "alive" in result.output.lower() or "could not stop" in result.output.lower()


def test_blender_status_rejects_unknown_live_pid(tmp_path, monkeypatch):
    from services.blender_mcp_manager import BlenderMcpManager

    manager = BlenderMcpManager(tmp_path)
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(manager, "_pid_is_stranger", lambda _pid: True)
    monkeypatch.setattr(manager, "_port_in_use", lambda: True)

    status = manager.status()

    assert status.running is False
    assert status.pid is None


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_stop_preserves_evidence_when_leader_dies_but_group_survives(
    tmp_path, monkeypatch, manager_kind,
):
    from services import blender_mcp_manager, managed_host
    from services.blender_mcp_manager import BlenderMcpManager

    if manager_kind == "generic":
        manager = _generic_manager(tmp_path / manager_kind)
        monkeypatch.setattr(manager, "_signal", lambda _pid, _sig: True)
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind)
        monkeypatch.setattr(blender_mcp_manager.os, "killpg", lambda *_args: None)

    manager.state_dir.mkdir(parents=True)
    manager.pid_file.write_text(
        "4242\nstart_utc=Mon Jan  1 00:00:00 2024\n", encoding="utf-8"
    )
    alive_calls = 0

    def leader_alive(_pid):
        nonlocal alive_calls
        alive_calls += 1
        return alive_calls == 1

    monkeypatch.setattr(manager, "_pid_alive", leader_alive)
    monkeypatch.setattr(manager, "_pid_is_stranger", lambda _pid: False)
    monkeypatch.setattr(
        ManagedHostManager, "_group_survives", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(managed_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(blender_mcp_manager.time, "sleep", lambda _seconds: None)

    assert manager.stop() is False
    assert manager.pid_file.exists()


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
@pytest.mark.parametrize(
    "record",
    ["4242\n", "4242\nstart_utc=Mon Jan  1 00:00:00 2024\n"],
)
def test_stale_pid_never_authorizes_signalling_a_leaderless_foreign_group(
    tmp_path, monkeypatch, manager_kind, record,
):
    from services.blender_mcp_manager import BlenderMcpManager

    manager = (
        _generic_manager(tmp_path / manager_kind)
        if manager_kind == "generic"
        else BlenderMcpManager(tmp_path / manager_kind)
    )
    manager.state_dir.mkdir(parents=True)
    manager.pid_file.write_text(record, encoding="utf-8")
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(
        ManagedHostManager, "_group_survives", staticmethod(lambda _pid: True)
    )
    monkeypatch.setattr(
        manager,
        "_sweep_orphaned_group",
        lambda _pid: pytest.fail("stale PID evidence authorized a group signal"),
    )

    assert manager.stop() is False
    assert manager.pid_file.exists()


def test_unsignalable_leaderless_group_is_treated_as_surviving(monkeypatch):
    from services import managed_host

    monkeypatch.setattr(
        managed_host.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr(
        managed_host.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(PermissionError()),
    )

    assert ManagedHostManager._group_survives(4242) is True


@pytest.mark.parametrize("manager_kind", ["generic", "comfyui", "vllm", "blender"])
@pytest.mark.parametrize(
    "evidence_case",
    [("", False), ("garbled\n", False), ("4242\n", True)],
)
def test_corrupt_pid_evidence_blocks_start_stop_and_remove(
    tmp_path, monkeypatch, manager_kind, evidence_case,
):
    from services.blender_mcp_manager import BlenderMcpError, BlenderMcpManager
    from services.comfyui_mps_manager import ComfyUiMpsError, ComfyUiMpsManager
    from services.vllm_metal_manager import VllmMetalError, VllmMetalManager

    if manager_kind == "generic":
        manager = _generic_manager(tmp_path / manager_kind)
        error_type = ManagedHostError
        start = lambda: manager.start(wait_timeout=0)
    elif manager_kind == "comfyui":
        manager = ComfyUiMpsManager(tmp_path / manager_kind)
        error_type = ComfyUiMpsError
        start = manager.start
    elif manager_kind == "vllm":
        manager = VllmMetalManager(tmp_path / manager_kind)
        error_type = VllmMetalError
        start = manager.start
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind)
        error_type = BlenderMcpError
        start = lambda: manager.start(wait_timeout=0)

    record, force_unreadable = evidence_case
    manager.state_dir.mkdir(parents=True)
    manager.pid_file.write_text(record, encoding="utf-8")
    if force_unreadable:
        monkeypatch.setattr(manager, "_read_pid", lambda: None)

    with pytest.raises(error_type, match="PID|pid|evidence|tracked"):
        start()
    assert manager.pid_file.exists()
    assert manager.stop() is False
    assert manager.pid_file.exists()
    with pytest.raises(error_type, match="refus|alive|evidence"):
        manager.remove()
    assert manager.pid_file.exists()


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
@pytest.mark.parametrize("evidence_kind", ["leaderless-group", "in-memory-pid"])
def test_start_refuses_group_or_memory_only_survivor(
    tmp_path, monkeypatch, manager_kind, evidence_kind,
):
    from services import blender_mcp_manager
    from services.blender_mcp_manager import BlenderMcpError, BlenderMcpManager

    if manager_kind == "generic":
        manager = _generic_manager(tmp_path / manager_kind)
        error_type = ManagedHostError
        monkeypatch.setattr(
            manager, "_spawn", lambda: pytest.fail("start reached spawn")
        )
        start = lambda: manager.start(wait_timeout=0)
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind)
        error_type = BlenderMcpError
        monkeypatch.setattr(
            blender_mcp_manager.subprocess,
            "Popen",
            lambda *_a, **_k: pytest.fail("start reached spawn"),
        )
        start = lambda: manager.start(wait_timeout=0)

    manager.state_dir.mkdir(parents=True)
    if evidence_kind == "leaderless-group":
        manager.pid_file.write_text(
            "4242\nstart_utc=Mon Jan  1 00:00:00 2024\n", encoding="utf-8"
        )
    else:
        manager._untracked_pid = 4242
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(manager, "_managed_process_alive", lambda _pid: True)
    monkeypatch.setattr(manager, "_pid_is_stranger", lambda _pid: True)

    with pytest.raises(error_type, match="refus|ownership|tracked"):
        start()


def test_rollback_does_not_stop_preexisting_managed_host(monkeypatch):
    from services import comfyui_mps_manager

    starter = start_module.AtlasStarter()
    comfy = _Manager(running=True)
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "COMFYUI_SOURCE": "managed-localhost-mps",
            "VLLM_METAL_SOURCE": "disabled",
        },
    )
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: comfy)

    assert starter.start_managed_host_processes() is True
    assert starter.rollback_managed_host_processes() is True
    assert comfy.stop_calls == 0
    assert comfy.running is True


def test_docker_start_failure_rolls_back_managed_hosts(monkeypatch):
    starter = start_module.AtlasStarter()
    calls: list[str] = []
    monkeypatch.setattr(
        starter.docker_manager,
        "start_services",
        lambda **_kwargs: 1,
    )
    monkeypatch.setattr(
        starter,
        "rollback_managed_host_processes",
        lambda: calls.append("rollback") or True,
    )

    assert starter.start_docker_services() is False
    assert calls == ["rollback"]


def test_docker_start_success_commits_managed_hosts(monkeypatch):
    starter = start_module.AtlasStarter()
    calls: list[str] = []
    monkeypatch.setattr(
        starter.docker_manager,
        "start_services",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(starter, "verify_one_shot_init_containers", lambda: True)
    monkeypatch.setattr(
        starter,
        "commit_managed_host_processes",
        lambda: calls.append("commit"),
    )

    assert starter.start_docker_services() is True
    assert calls == ["commit"]


def test_tui_starts_managed_hosts_after_setup_and_rolls_back_failures():
    source = (
        REPO_ROOT / "bootstrapper/ui/textual/screens/wizard_screen.py"
    ).read_text(encoding="utf-8")
    setup = source.index("starter.generate_service_configuration),")
    managed = source.index("starter.start_managed_host_processes", setup)
    compose_up = source.index('self._run_compose(["up"', managed)

    assert setup < managed < compose_up
    assert "asyncio.shield(managed_host_start_task)" in source[managed:compose_up]
    assert source.count("starter.rollback_managed_host_processes", managed) == 1
    assert source.index("starter.commit_managed_host_processes", managed) > compose_up


def test_uncancellable_cleanup_wait_survives_repeated_cancellation():
    from ui.textual.screens.wizard_screen import _await_uncancellable

    async def scenario():
        release = asyncio.Event()

        async def work():
            await release.wait()
            return "settled"

        worker = asyncio.create_task(work())
        waiter = asyncio.create_task(_await_uncancellable(worker))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.sleep(0)
        waiter.cancel()
        release.set()
        return await waiter

    assert asyncio.run(scenario()) == "settled"


def _method_source(path: Path, method_name: str) -> str:
    import ast

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == method_name
    )
    return ast.get_source_segment(source, node) or ""


def test_linear_post_up_steps_are_ordered_before_commit():
    import inspect

    linear = inspect.getsource(start_module.AtlasStarter.start_docker_services)
    assert linear.index("verify_one_shot_init_containers") < linear.index(
        "_reactivate_n8n_if_needed"
    ) < linear.index("commit_managed_host_processes")


def test_tui_post_up_steps_are_ordered_before_commit():
    """The TUI owns its own compose path and must enforce the same gates."""

    path = (
        Path(start_module.__file__).parent / "ui" / "textual" / "screens" / "wizard_screen.py"
    )
    pipeline = _method_source(path, "_run_pipeline_and_stream")
    helper = _method_source(path, "_reactivate_n8n_after_up")
    assert pipeline.index("verify_one_shot_init_containers") < pipeline.index(
        "_reactivate_n8n_after_up"
    ) < pipeline.index("commit_managed_host_processes")
    assert "_reactivate_n8n_if_needed" in helper
