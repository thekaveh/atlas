"""stop.py must propagate a failed `docker compose down` via its exit code.

Regression guard: stop_services() used to return True on both branches,
so a failed stop was undetectable to scripts and CI.
"""
from __future__ import annotations

import click.testing
import pytest
import sys

import stop as stop_module


@pytest.fixture(autouse=True)
def _isolate_native_host_state(monkeypatch):
    """CLI tests must never inspect or signal the developer's real host PIDs."""
    from services import blender_mcp_manager, comfyui_mps_manager, vllm_metal_manager

    class IdleManager:
        def status(self):
            return type("Status", (), {"running": False})()

        def stop(self):
            return False

    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: IdleManager())
    monkeypatch.setattr(vllm_metal_manager, "manager_from_env", lambda _env: IdleManager())
    monkeypatch.setattr(blender_mcp_manager, "manager_from_env", lambda _env: IdleManager())


def _stopper_with_stop_result(monkeypatch, rc: int):
    stopper = stop_module.AtlasStopper()
    monkeypatch.setattr(
        stopper.docker_manager, "stop_services",
        lambda remove_volumes, remove_orphans: rc,
    )
    return stopper


def test_stop_services_returns_false_on_compose_failure(monkeypatch):
    stopper = _stopper_with_stop_result(monkeypatch, rc=1)
    assert stopper.stop_services(cold_stop=False, project_name="atlas") is False


def test_stop_services_returns_true_on_success(monkeypatch):
    stopper = _stopper_with_stop_result(monkeypatch, rc=0)
    assert stopper.stop_services(cold_stop=False, project_name="atlas") is True


def test_main_exits_nonzero_when_stop_fails(monkeypatch):
    monkeypatch.setattr(
        stop_module.AtlasStopper, "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: "atlas",
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "stop_services",
        lambda self, cold, project_name: False,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "ensure_dependencies_available", lambda self: True,
    )
    result = click.testing.CliRunner().invoke(stop_module.main, [])
    assert result.exit_code == 1


def test_main_exits_zero_when_stop_succeeds(monkeypatch):
    monkeypatch.setattr(
        stop_module.AtlasStopper, "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: "atlas",
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "stop_services",
        lambda self, cold, project_name: True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "ensure_dependencies_available", lambda self: True,
    )
    result = click.testing.CliRunner().invoke(stop_module.main, [])
    assert result.exit_code == 0


def test_managed_host_stop_ignores_current_source_selection(monkeypatch):
    from services import comfyui_mps_manager

    stopper = stop_module.AtlasStopper()

    class Manager:
        running = True

        def status(self):
            return type("Status", (), {"running": self.running})()

        def stop(self):
            self.running = False
            return True

    manager = Manager()
    monkeypatch.setattr(stopper.config_parser, "env_file_exists", lambda: True)
    monkeypatch.setattr(
        stopper.config_parser,
        "parse_env_file",
        lambda: {"COMFYUI_SOURCE": "disabled"},
    )
    monkeypatch.setattr(comfyui_mps_manager, "manager_from_env", lambda _env: manager)

    assert stopper.stop_managed_comfyui_mps() is True


def test_managed_host_stop_checks_default_state_without_env(monkeypatch):
    from services import vllm_metal_manager

    stopper = stop_module.AtlasStopper()
    seen: list[dict[str, str]] = []
    stop_calls: list[bool] = []
    manager = type(
        "Manager",
        (),
        {
            "status": lambda self: type("Status", (), {"running": False})(),
            "stop": lambda self: stop_calls.append(True) or False,
        },
    )()
    monkeypatch.setattr(stopper.config_parser, "env_file_exists", lambda: False)
    monkeypatch.setattr(
        vllm_metal_manager,
        "manager_from_env",
        lambda env: seen.append(env) or manager,
    )

    assert stopper.stop_managed_vllm_metal() is True
    assert seen == [{}]
    assert stop_calls == [True]


def test_main_exits_nonzero_when_managed_host_remains_running(monkeypatch):
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: "atlas",
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "ensure_dependencies_available", lambda self: True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "stop_services",
        lambda self, cold, project_name: True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "stop_managed_comfyui_mps",
        lambda self: False,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "stop_managed_vllm_metal",
        lambda self: True,
    )

    # Managed-host teardown is opt-in since #655 — the failure only reaches the
    # exit code when the operator explicitly requests it.
    result = click.testing.CliRunner().invoke(stop_module.main, ["--stop-managed-hosts"])

    assert result.exit_code == 1


def test_main_exits_nonzero_when_requested_hosts_cleanup_fails(monkeypatch):
    monkeypatch.setattr(
        stop_module.AtlasStopper, "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: "atlas",
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "stop_services",
        lambda self, cold, project_name: True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "ensure_dependencies_available", lambda self: True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "cleanup_hosts_entries", lambda self: False,
    )

    result = click.testing.CliRunner().invoke(stop_module.main, ["--clean-hosts"])

    assert result.exit_code == 1
    assert "stopped successfully" not in result.output
    assert "completed with errors" in result.output


def test_project_name_persistence_failure_does_not_abort_teardown(monkeypatch):
    calls = []
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "persist_project_name",
        lambda self, name: False,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: project_name_override,
    )
    monkeypatch.setattr(stop_module.AtlasStopper, "ensure_dependencies_available", lambda self: True)
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "stop_services",
        lambda self, cold, project_name: calls.append(project_name) or True,
    )

    result = click.testing.CliRunner().invoke(stop_module.main, ["--project", "demo"])

    assert result.exit_code == 0
    assert calls == ["demo"]
    assert "could not persist PROJECT_NAME" in result.output


def test_missing_sudo_is_reported_as_hosts_cleanup_failure(monkeypatch):
    import importlib

    system_utils = importlib.import_module("utils.system")
    monkeypatch.setattr(system_utils, "is_elevated", lambda: False)
    monkeypatch.setattr(
        stop_module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("sudo")),
    )

    assert stop_module._run_privileged_hosts_cleanup() is False


def test_stop_managed_hosts_help_names_blender_mcp():
    result = click.testing.CliRunner().invoke(stop_module.main, ["--help"])
    assert result.exit_code == 0
    assert "Blender MCP" in result.output


@pytest.mark.parametrize(
    "evidence",
    ["corrupt", "unreadable", "unstamped-live", "mismatched-live"],
)
def test_default_stop_advises_when_blender_evidence_may_still_be_live(
    tmp_path, monkeypatch, evidence,
):
    from services import blender_mcp_manager

    pid_file = tmp_path / "blender.pid"
    pid_file.write_text("garbled\n" if evidence == "corrupt" else "4242\n")

    class Manager:
        _untracked_pid = None

        def status(self):
            return type("Status", (), {"running": False})()

        def _read_pid(self):
            if evidence == "unreadable":
                raise PermissionError("denied")
            return None if evidence == "corrupt" else 4242

        def _managed_process_alive(self, _pid):
            return True

    manager = Manager()
    manager.pid_file = pid_file
    stopper = stop_module.AtlasStopper()
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(stopper.config_parser, "env_file_exists", lambda: False)
    monkeypatch.setattr(
        stopper.banner,
        "show_status_message",
        lambda message, level: messages.append((message, level)),
    )
    monkeypatch.setattr(blender_mcp_manager, "manager_from_env", lambda _env: manager)

    stopper.report_managed_hosts_left_running()

    assert any(
        "ownership" in message.lower() and level == "warning"
        for message, level in messages
    )


def test_privileged_hosts_cleanup_uses_bytecode_free_python_child(monkeypatch):
    import importlib

    system_utils = importlib.import_module("utils.system")

    calls = []

    class Result:
        returncode = 0

    monkeypatch.setattr(system_utils, "is_elevated", lambda: False)
    monkeypatch.setattr(
        stop_module.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)) or Result(),
    )

    assert stop_module._run_privileged_hosts_cleanup() is True
    args, kwargs = calls[0]
    assert args[:2] == ["sudo", "env"]
    assert sys.executable in args
    assert f"PYTHONPATH={kwargs['env']['PYTHONPATH']}" in args
    assert "PYTHONDONTWRITEBYTECODE=1" in args
    assert "stop.sh" not in args
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "bootstrapper" in kwargs["env"]["PYTHONPATH"]


def test_main_exits_nonzero_when_compose_version_preflight_fails(monkeypatch):
    native_stops: list[str] = []
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: "atlas",
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "ensure_dependencies_available",
        lambda self: False,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "stop_services",
        lambda self, cold, project_name: (_ for _ in ()).throw(
            AssertionError("stop_services should not run")
        ),
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "stop_managed_comfyui_mps",
        lambda self: native_stops.append("comfyui") or True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "stop_managed_vllm_metal",
        lambda self: native_stops.append("vllm") or True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "stop_managed_blender_mcp",
        lambda self: native_stops.append("blender") or True,
    )

    # With --stop-managed-hosts (opt-in since #655), a Docker preflight failure
    # must still not strand the requested managed-host teardown — it runs after
    # stop_services is skipped, and the preflight failure keeps the exit nonzero.
    result = click.testing.CliRunner().invoke(stop_module.main, ["--stop-managed-hosts"])

    assert result.exit_code == 1
    assert native_stops == ["comfyui", "vllm", "blender"]


def test_main_exits_2_for_invalid_persisted_project_before_preflights(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("PROJECT_NAME=bad.name\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(env))

    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "ensure_dependencies_available",
        lambda self: (_ for _ in ()).throw(
            AssertionError("Docker preflight should not run")
        ),
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper,
        "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: (_ for _ in ()).throw(
            AssertionError("configuration display should not read invalid project")
        ),
    )

    result = click.testing.CliRunner().invoke(stop_module.main, [])

    assert result.exit_code == 2
    assert "invalid PROJECT_NAME" in result.output
