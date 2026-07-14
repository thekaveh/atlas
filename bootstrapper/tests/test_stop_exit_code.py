"""stop.py must propagate a failed `docker compose down` via its exit code.

Regression guard: stop_services() used to return True on both branches,
so a failed stop was undetectable to scripts and CI.
"""
from __future__ import annotations

import click.testing
import sys

import stop as stop_module


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


def test_privileged_hosts_cleanup_uses_bytecode_free_python_child(monkeypatch):
    import utils.system

    calls = []

    class Result:
        returncode = 0

    monkeypatch.setattr(utils.system, "is_elevated", lambda: False)
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

    result = click.testing.CliRunner().invoke(stop_module.main, [])

    assert result.exit_code == 1


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
