"""CLI argument-range validation for start.py worker-count flags.

``--spark-workers`` (1-8) and ``--ray-worker-count`` (0-64) mirror the
wizard's SecondaryNumberInput clamps. An out-of-range value must exit with
click's conventional usage-error code 2 — not the masked "unexpected error"
exit 1 the catch-all handler used to produce before main() learned to
re-raise click.ClickException ahead of the generic handler.
"""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner

from start import main


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
