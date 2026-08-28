"""CLI argument-range validation for start.py worker-count flags.

``--spark-workers`` (1-8) and ``--ray-worker-count`` (0-64) mirror the
wizard's SecondaryNumberInput clamps. An out-of-range value must exit with
click's conventional usage-error code 2 — not the masked "unexpected error"
exit 1 the catch-all handler used to produce before main() learned to
re-raise click.ClickException ahead of the generic handler.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
import yaml

from start import main


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("args", "factory_name", "error_type"),
    [
        (("managed-host", "remove", "example", "--yes"), "_managed_host_manager", "generic"),
        (("blender-mcp", "remove"), "_blender_mcp_manager", "blender"),
    ],
)
def test_remove_cli_exits_nonzero_when_state_deletion_fails(
    monkeypatch, args, factory_name, error_type,
):
    from types import SimpleNamespace
    import start as start_module
    from services.blender_mcp_manager import BlenderMcpError
    from services.managed_host import ManagedHostError

    exception_type = ManagedHostError if error_type == "generic" else BlenderMcpError
    manager = SimpleNamespace(
        state_dir=Path("/tmp/atlas-test-state"),
        remove=lambda: (_ for _ in ()).throw(exception_type("could not remove state")),
    )
    monkeypatch.setattr(start_module, factory_name, lambda *_args: manager)

    result = CliRunner().invoke(main, list(args))

    assert result.exit_code == 1
    assert isinstance(result.exception, exception_type)
    assert "Removed" not in result.output and "removed" not in result.output


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_remove_cli_is_idempotent_for_absent_state(tmp_path, monkeypatch, manager_kind):
    import start as start_module
    from services.blender_mcp_manager import BlenderMcpManager
    from services.managed_host import HostProcessSpec, ManagedHostManager

    if manager_kind == "generic":
        manager = ManagedHostManager(
            HostProcessSpec(name="remove-test", command=("sleep", "300"), port=8399),
            tmp_path / manager_kind,
        )
        args, factory = ("managed-host", "remove", "example", "--yes"), "_managed_host_manager"
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind)
        args, factory = ("blender-mcp", "remove"), "_blender_mcp_manager"
    monkeypatch.setattr(start_module, factory, lambda *_args: manager)

    first = CliRunner().invoke(main, list(args))
    second = CliRunner().invoke(main, list(args))

    assert first.exit_code == second.exit_code == 0
    assert "removed" in first.output.lower() and "removed" in second.output.lower()


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_remove_cli_rejects_descendant_not_found_with_existing_root(
    tmp_path, monkeypatch, manager_kind,
):
    import services as services_package
    import start as start_module
    from services.blender_mcp_manager import BlenderMcpError, BlenderMcpManager
    from services.managed_host import HostProcessSpec, ManagedHostError, ManagedHostManager

    if manager_kind == "generic":
        manager = ManagedHostManager(
            HostProcessSpec(name="remove-test", command=("sleep", "300"), port=8399),
            tmp_path / manager_kind,
        )
        args, factory, error_type = (
            ("managed-host", "remove", "example", "--yes"),
            "_managed_host_manager", ManagedHostError,
        )
    else:
        manager = BlenderMcpManager(tmp_path / manager_kind)
        args, factory, error_type = (
            ("blender-mcp", "remove"), "_blender_mcp_manager", BlenderMcpError,
        )
    manager.state_dir.mkdir(parents=True)
    monkeypatch.setattr(start_module, factory, lambda *_args: manager)
    monkeypatch.setattr(
        services_package.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("vanished child")),
    )

    result = CliRunner().invoke(main, list(args))

    assert result.exit_code == 1
    assert isinstance(result.exception, error_type)
    assert "removed" not in result.output.lower()


@pytest.mark.parametrize("value", ["0", "9", "-1", "99"])
def test_spark_workers_out_of_range_exits_2(value):
    result = CliRunner().invoke(main, ["--spark-workers", value])
    assert result.exit_code == 2
    assert "spark-workers must be in 1-8" in result.output


@pytest.mark.parametrize("value", ["-1", "65", "99"])
def test_ray_worker_count_out_of_range_exits_2(value):
    result = CliRunner().invoke(main, ["--ray-worker-count", value])
    assert result.exit_code == 2
    assert "ray-worker-count must be in 0-64" in result.output


@pytest.mark.parametrize("value", ["0", "366", "-1", "999"])
def test_prometheus_retention_out_of_range_exits_2(value):
    result = CliRunner().invoke(main, ["--prometheus-retention-days", value])
    assert result.exit_code == 2
    assert "prometheus-retention-days must be in 1-365" in result.output


@pytest.mark.parametrize("boundary", ["below", "above"])
def test_numeric_base_port_out_of_range_fails_before_starter_construction(
    monkeypatch, boundary,
):
    import start as start_module
    from core.port_manager import PortManager

    manager = PortManager()
    offsets = manager.port_offsets()
    maximum = 65535 - (max(offsets.values()) if offsets else 0)
    value = 1023 if boundary == "below" else maximum + 1

    monkeypatch.setattr(
        start_module,
        "AtlasStarter",
        lambda *_args, **_kwargs: pytest.fail("starter must not be constructed"),
    )

    result = CliRunner().invoke(
        main,
        ["--no-tui", "--cold", "--base-port", value],
    )

    assert result.exit_code == 2
    assert "1024" in result.output
    assert str(maximum) in result.output


def test_numeric_base_port_accepts_canonical_dynamic_maximum(monkeypatch):
    import start as start_module
    from core.port_manager import PortManager

    manager = PortManager()
    offsets = manager.port_offsets()
    maximum = 65535 - (max(offsets.values()) if offsets else 0)

    # --list-tracks exits before starter construction but still runs the
    # option callback, which is the boundary under test.
    result = CliRunner().invoke(main, ["--base-port", str(maximum), "--list-tracks"])
    assert result.exit_code == 0


def test_no_port_migrate_help_lists_all_migrations():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "catalog v4" in result.output


def test_explicit_consumer_error_fails_before_environment_preparation(
    monkeypatch, tmp_path,
):
    import start as start_module

    missing = tmp_path / "missing.consumer.yml"

    class ConfigParser:
        def load_consumer_config(self):
            raise FileNotFoundError(missing)

    class Starter:
        config_parser = ConfigParser()

        def prepare_environment(self, *_args, **_kwargs):
            pytest.fail("invalid explicit consumer must fail before preparation")

    monkeypatch.setattr(start_module, "AtlasStarter", Starter)

    result = CliRunner().invoke(
        main,
        ["--consumer", str(missing), "--no-tui", "--cold"],
    )

    assert result.exit_code == 2
    assert "invalid --consumer manifest" in result.output
    assert str(missing) in result.output


def test_json_wraps_prelinear_consumer_failure(monkeypatch, tmp_path):
    import json
    import start as start_module

    missing = tmp_path / "missing.consumer.yml"

    class ConfigParser:
        def load_consumer_config(self):
            raise FileNotFoundError(missing)

    class Starter:
        config_parser = ConfigParser()

    monkeypatch.setattr(start_module, "AtlasStarter", Starter)

    result = CliRunner().invoke(
        main,
        ["--consumer", str(missing), "--json", "--no-tui"],
    )

    assert result.exit_code == 2
    assert json.loads(result.stdout) == {"ok": False, "exit_code": 2}
    assert "invalid --consumer manifest" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["--json", "--base-port", "70000"],
        ["--json", "--project", "env", "--base-port", "70000"],
        ["--json", "--not-an-atlas-option"],
    ],
)
def test_json_wraps_root_command_parse_failures(args):
    import json

    result = CliRunner().invoke(main, args)

    assert result.exit_code == 2
    assert json.loads(result.stdout) == {"ok": False, "exit_code": 2}
    assert "Error:" in result.stderr


def test_root_json_option_does_not_wrap_subcommand_output():
    help_result = CliRunner().invoke(main, ["--json", "env", "--help"])
    assert help_result.exit_code == 0
    assert '"exit_code"' not in help_result.stdout

    failing_result = CliRunner().invoke(main, ["--json", "env"])
    assert failing_result.exit_code == 2
    assert '"exit_code"' not in failing_result.stdout


def test_manifest_backed_source_flags_match_manifest_choices():
    manifest_choices = {}
    for path in (REPO_ROOT / "services").glob("*/service.yml"):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        sources = manifest.get("sources") or {}
        if sources.get("var"):
            manifest_choices[sources["var"]] = {
                option["id"] for option in sources.get("options", [])
            }

    for parameter in main.params:
        source_var = parameter.name.upper()
        if source_var not in manifest_choices or not hasattr(parameter.type, "choices"):
            continue
        assert set(parameter.type.choices) == manifest_choices[source_var], source_var


def test_comfyui_managed_mps_source_is_accepted_by_cli():
    result = CliRunner().invoke(
        main,
        ["--comfyui-source", "managed-localhost-mps", "--list-tracks"],
    )
    assert result.exit_code == 0


@pytest.mark.parametrize("flag", ["--backup-source", "--cloudflared-source"])
def test_optional_operations_source_flags_are_accepted(flag):
    result = CliRunner().invoke(main, [flag, "disabled", "--list-tracks"])
    assert result.exit_code == 0


def test_setup_hosts_does_not_suggest_sudo_start(monkeypatch):
    import start as start_module
    import utils.system

    monkeypatch.setattr(utils.system, "is_elevated", lambda: False)
    monkeypatch.setattr(start_module, "_run_privileged_hosts_setup", lambda: False)

    result = CliRunner().invoke(main, ["--setup-hosts"])

    assert result.exit_code == 1
    assert "--setup-hosts requires admin privileges" in result.output
    assert "sudo ./start.sh" not in result.output
    assert "./start.sh --setup-hosts" in result.output


def test_privileged_hosts_helper_uses_bytecode_free_python_child(monkeypatch):
    import start as start_module
    import utils.system

    calls = []

    class Result:
        returncode = 0

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setattr(utils.system, "is_elevated", lambda: False)
    monkeypatch.setattr(start_module.subprocess, "run", fake_run)

    assert start_module._run_privileged_hosts_setup() is True
    assert calls, "expected a privileged helper subprocess"
    args, kwargs = calls[0]
    assert args[:2] == ["sudo", "env"]
    assert sys.executable in args
    assert f"PYTHONPATH={kwargs['env']['PYTHONPATH']}" in args
    assert "PYTHONDONTWRITEBYTECODE=1" in args
    assert "start.sh" not in args
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "bootstrapper" in kwargs["env"]["PYTHONPATH"]
