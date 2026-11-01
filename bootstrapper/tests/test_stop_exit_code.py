"""stop.py must propagate a failed `docker compose down` via its exit code.

Regression guard: stop_services() used to return True on both branches,
so a failed stop was undetectable to scripts and CI.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

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
    calls: list[dict] = []
    monkeypatch.setattr(
        stopper.docker_manager, "stop_services",
        lambda **kwargs: calls.append(kwargs) or rc,
    )
    return stopper, calls


def test_stop_services_returns_false_on_compose_failure(monkeypatch):
    stopper, _calls = _stopper_with_stop_result(monkeypatch, rc=1)
    assert stopper.stop_services(cold_stop=False, project_name="atlas") is False


def test_stop_services_returns_true_on_success(monkeypatch):
    stopper, calls = _stopper_with_stop_result(monkeypatch, rc=0)
    assert stopper.stop_services(cold_stop=False, project_name="atlas") is True
    # A plain stop must never reach `down --volumes`: that deletes project data.
    assert calls == [{"remove_volumes": False, "remove_orphans": True}]


def test_cold_stop_returns_false_when_volume_cleanup_fails(monkeypatch):
    stopper, calls = _stopper_with_stop_result(monkeypatch, rc=0)
    monkeypatch.setattr(stopper.docker_manager, "perform_cold_stop_cleanup", lambda: False)
    assert stopper.stop_services(cold_stop=True, project_name="atlas") is False
    assert calls == []


def test_main_exits_nonzero_when_stop_fails(monkeypatch):
    monkeypatch.setattr(
        stop_module.AtlasStopper, "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: "atlas",
    )
    calls = []
    monkeypatch.setattr(
        stop_module.AtlasStopper, "stop_services",
        lambda self, cold, project_name: calls.append((cold, project_name)) or False,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "ensure_dependencies_available", lambda self: True,
    )
    result = click.testing.CliRunner().invoke(stop_module.main, [])
    # A bare ./stop.sh is a plain stop that keeps volumes.
    assert calls == [(False, "atlas")], "stop_services never ran"
    assert result.exit_code == 1
    # The generic crash handler also exits 1; the failure must be the
    # reported stop result, not a crash before stop ran.
    assert "Unexpected error" not in result.output, result.output


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


_MANAGED_HOST_STOPS = {
    "comfyui": "stop_managed_comfyui_mps",
    "vllm": "stop_managed_vllm_metal",
    "blender": "stop_managed_blender_mcp",
}


@pytest.mark.parametrize("failing_host", sorted(_MANAGED_HOST_STOPS))
def test_main_exits_nonzero_when_managed_host_remains_running(monkeypatch, failing_host):
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
    # Exactly one host runtime fails its teardown; each must reach the exit code.
    for host, method in _MANAGED_HOST_STOPS.items():
        host_ok = host != failing_host
        monkeypatch.setattr(
            stop_module.AtlasStopper, method, lambda self, ok=host_ok: ok,
        )

    # Managed-host teardown is opt-in since #655 — the failure only reaches the
    # exit code when the operator explicitly requests it.
    result = click.testing.CliRunner().invoke(stop_module.main, ["--stop-managed-hosts"])

    assert result.exit_code == 1
    # The generic crash handler also exits 1; the failure must be the
    # reported stop result, not a crash before stop ran.
    assert "Unexpected error" not in result.output, result.output


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
    # The generic crash handler also exits 1; the failure must be the
    # reported stop result, not a crash before stop ran.
    assert "Unexpected error" not in result.output, result.output
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


def test_privileged_hosts_cleanup_reports_a_failed_sudo_child(monkeypatch):
    import importlib

    system_utils = importlib.import_module("utils.system")
    monkeypatch.setattr(system_utils, "is_elevated", lambda: False)
    monkeypatch.setattr(
        stop_module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )

    assert stop_module._run_privileged_hosts_cleanup() is False


@pytest.mark.parametrize("cleaned", [True, False])
def test_elevated_hosts_cleanup_reports_the_hosts_file_result(monkeypatch, cleaned):
    import importlib

    system_utils = importlib.import_module("utils.system")
    monkeypatch.setattr(system_utils, "is_elevated", lambda: True)
    monkeypatch.setattr(
        stop_module, "HostsManager",
        lambda: SimpleNamespace(cleanup_hosts_entries=lambda: cleaned),
    )
    monkeypatch.setattr(
        stop_module.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no sudo when elevated")),
    )

    assert stop_module._run_privileged_hosts_cleanup() is cleaned


class _UnprovenStopManager:
    """A managed host whose stop() fails with its pid file left, or raises."""

    pool_index = 0

    def __init__(self, pid_file, raises):
        self.pid_file, self._raises = pid_file, raises

    def status(self):
        return SimpleNamespace(running=False)

    def stop(self):
        if self._raises:
            raise RuntimeError("signal denied")
        return False


@pytest.mark.parametrize("raises", [False, True], ids=["pid-file-remains", "stop-raises"])
@pytest.mark.parametrize("host", sorted(_MANAGED_HOST_STOPS))
def test_managed_host_stop_fails_when_teardown_is_unproven(tmp_path, monkeypatch, host, raises):
    from services import blender_mcp_manager, comfyui_mps_manager, vllm_metal_manager

    pid_file = tmp_path / "host.pid"
    pid_file.write_text("4242\n", encoding="utf-8")
    manager = _UnprovenStopManager(pid_file, raises)
    stopper = stop_module.AtlasStopper()
    monkeypatch.setattr(stopper.config_parser, "env_file_exists", lambda: False)
    if host == "blender":
        monkeypatch.setattr(blender_mcp_manager, "pool_from_env", lambda _env, **_kw: [manager])
    else:
        module = {"comfyui": comfyui_mps_manager, "vllm": vllm_metal_manager}[host]
        monkeypatch.setattr(module, "manager_from_env", lambda _env: manager)

    assert getattr(stopper, _MANAGED_HOST_STOPS[host])() is False


def test_blender_pool_stop_tries_every_instance_and_reports_any_failure(monkeypatch):
    from services import blender_mcp_manager

    stopped: list[int] = []
    strays: list[bool] = []

    def member(index, survives):
        return SimpleNamespace(
            pool_index=index, pid_file=None,
            status=lambda: SimpleNamespace(running=survives),
            stop=lambda: stopped.append(index) or not survives,
        )

    pool = [member(0, True), member(1, False)]
    monkeypatch.setattr(
        blender_mcp_manager, "pool_from_env",
        lambda _env, *, include_strays=False: strays.append(include_strays) or pool,
    )
    stopper = stop_module.AtlasStopper()
    monkeypatch.setattr(stopper.config_parser, "env_file_exists", lambda: False)

    assert stopper.stop_managed_blender_mcp() is False
    assert stopped == [0, 1]
    # Instances above a shrunken pool size still hold pid files and must stop.
    assert strays == [True]


def test_default_stop_advises_for_every_blender_pool_instance(monkeypatch):
    from services import blender_mcp_manager

    strays: list[bool] = []
    messages: list[tuple[str, str]] = []
    pool = [SimpleNamespace(status=lambda: SimpleNamespace(running=True)) for _ in range(2)]
    monkeypatch.setattr(
        blender_mcp_manager, "pool_from_env",
        lambda _env, *, include_strays=False: strays.append(include_strays) or pool,
    )
    stopper = stop_module.AtlasStopper()
    monkeypatch.setattr(stopper.config_parser, "env_file_exists", lambda: False)
    monkeypatch.setattr(
        stopper.banner, "show_status_message",
        lambda message, level: messages.append((message, level)),
    )

    stopper.report_managed_hosts_left_running()

    assert strays == [True]
    assert sum(
        "Blender MCP bridge left running" in message and level == "info"
        for message, level in messages
    ) == 2


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
    # The generic crash handler also exits 1; the failure must be the
    # reported stop result, not a crash before stop ran.
    assert "Unexpected error" not in result.output, result.output
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


def test_consumer_manifest_reaches_the_teardown_compose_seam(monkeypatch):
    """--cold must load the consumer's overlays to remove their volumes."""
    import os

    seen = []
    # setenv first so monkeypatch restores the variable main() writes.
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", "")
    monkeypatch.setattr(
        stop_module.AtlasStopper, "show_configuration_info",
        lambda self, cold, clean, project_name_override=None: "atlas",
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "stop_services",
        lambda self, cold, project_name: seen.append(
            os.environ.get("ATLAS_CONSUMER_MANIFEST")
        ) or True,
    )
    monkeypatch.setattr(
        stop_module.AtlasStopper, "ensure_dependencies_available", lambda self: True,
    )
    result = click.testing.CliRunner().invoke(
        stop_module.main, ["--consumer", "a.yml", "--consumer", "b.yml"]
    )
    assert result.exit_code == 0, result.output
    assert seen == [os.pathsep.join(["a.yml", "b.yml"])]


def test_stop_wrapper_records_the_invoking_directory_like_start():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for wrapper in ("start.sh", "stop.sh"):
        text = (root / wrapper).read_text(encoding="utf-8")
        assert 'ATLAS_INVOKER_CWD="${PWD}"' in text, wrapper
        assert text.index("ATLAS_INVOKER_CWD") < text.index("CDPATH='' cd -- \"$(dirname -- \"$0\")\"")


def test_cold_stop_that_dropped_consumer_overlays_is_not_reported_as_a_full_wipe(
    tmp_path, monkeypatch
):
    """Volumes declared only by a broken overlay survive a base-stack
    `down --volumes`; the cold stop must not claim all data was removed."""
    from core.consumer_manifest import ConsumerManifestError
    from core.docker_manager import DockerManager

    manager = DockerManager(str(tmp_path))
    manager._compose_cmd = "docker compose"
    monkeypatch.setattr(manager.config_parser, "get_project_name", lambda: "atlas")
    monkeypatch.setattr(manager.config_parser, "env_file_exists", lambda: False)
    monkeypatch.setattr(
        manager.config_parser,
        "load_consumer_config",
        lambda: (_ for _ in ()).throw(ConsumerManifestError("invalid yaml")),
    )
    monkeypatch.setattr(
        "core.docker_manager.subprocess.run",
        lambda command, **_kwargs: type("Result", (), {"returncode": 0})(),
    )

    assert manager.perform_cold_stop_cleanup() is False
    assert manager.teardown_overlays_dropped is True


@pytest.mark.parametrize("directories", [("ordinary",), ("Research, Inc",), ("Research: Lab",), ("second: lab", "first, lab")])
@pytest.mark.parametrize("previous", [None, "previous private value"])
def test_stop_cli_consumer_paths_reach_real_teardown_overlay_selection(tmp_path, monkeypatch, directories, previous):
    import stop
    from core.docker_manager import DockerManager

    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    overlays = []
    args = []
    for index, directory in enumerate(directories):
        folder = tmp_path / directory
        folder.mkdir()
        overlay = folder / "overlay.yml"
        overlay.write_text("services: {}\n", encoding="utf-8")
        manifest = folder / "atlas.consumer.yml"
        manifest.write_text(f"name: selected-{index}\ncompose_overlays:\n  - ./overlay.yml\n", encoding="utf-8")
        overlays.append(str(overlay))
        args.extend(["--consumer", str(manifest)])
    manager = DockerManager(str(tmp_path))
    monkeypatch.setattr(manager, "detect_docker_compose_command", lambda: "docker compose")
    calls = []
    monkeypatch.setattr("core.docker_manager.subprocess.run", lambda argv, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr("core.docker_manager.run_compose_child", lambda argv, *rest: calls.append(argv) or 0)
    stopper = SimpleNamespace(
        root_dir=tmp_path,
        banner=SimpleNamespace(show_status_message=lambda *args: None),
        validate_persisted_project_name=lambda project: True,
        show_configuration_info=lambda *args, **kwargs: "atlas",
        ensure_dependencies_available=lambda: True,
        stop_services=lambda cold, project: manager.execute_compose_command(["down", "-v"], project_name=project) == 0,
        report_managed_hosts_left_running=lambda: None,
        show_final_status=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(stop, "AtlasStopper", lambda: stopper)
    monkeypatch.setattr(stop, "warn_if_submodule_pin_drifted", lambda root: None)
    monkeypatch.setenv("ATLAS_CONSUMER_MANIFEST", "previous.yml")
    if previous is None:
        monkeypatch.delenv("_ATLAS_CONSUMER_MANIFEST_CLI", raising=False)
    else:
        monkeypatch.setenv("_ATLAS_CONSUMER_MANIFEST_CLI", previous)

    result = click.testing.CliRunner().invoke(stop.main, [*args, "--cold"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0] == ["docker", "compose", "-p", "atlas", "-f", "docker-compose.yml", *[arg for overlay in overlays for arg in ("-f", overlay)], "down", "-v"]
    assert os.environ["ATLAS_CONSUMER_MANIFEST"] == os.pathsep.join(args[1::2])
    assert os.environ.get("_ATLAS_CONSUMER_MANIFEST_CLI") == previous
