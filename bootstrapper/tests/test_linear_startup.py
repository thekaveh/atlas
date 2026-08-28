from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

from core import linear_startup


def _options(**overrides):
    values = {
        "cold": False,
        "base_port": 63000,
        "project_name": "atlas",
        "source_args": {"grafana_source": "disabled"},
        "profile": "default",
        "explicit_prometheus": None,
        "explicit_grafana": "disabled",
        "cloud_api_keys": {},
        "user_model_selections": {},
        "no_port_migrate": False,
        "setup_hosts": False,
        "skip_hosts": True,
        "track": "gen-ai-rag",
        "detach": True,
        "json_output": True,
        "no_splash": True,
    }
    values.update(overrides)
    return linear_startup.LinearStartupOptions(**values)


class _FakeStarter:
    def __init__(self, fail_at: str | None = None, log_return_code: int = 0):
        self.calls: list[str] = []
        self.fail_at = fail_at
        self.log_return_code = log_return_code
        self.config_parser = SimpleNamespace(root_dir="/repo")
        self.source_validator = SimpleNamespace(validation_errors=[])
        self.key_generator = SimpleNamespace(
            assert_no_placeholders_remaining=lambda: None
        )
        self.banner = SimpleNamespace(
            console=SimpleNamespace(print=lambda *_args, **_kwargs: None)
        )

    def __getattr__(self, name: str):
        def call(*_args, **_kwargs):
            self.calls.append(name)
            print(f"progress:{name}")
            return name != self.fail_at

        return call

    def show_detached_status_summary(self, *, json_output: bool = False) -> bool:
        self.calls.append("show_detached_status_summary")
        if json_output:
            print(json.dumps({"ok": True, "services": []}))
        return True

    def show_container_logs(self) -> int:
        self.calls.append("show_container_logs")
        return self.log_return_code


def test_linear_startup_runs_the_headless_pipeline_to_detached_summary(
    monkeypatch,
) -> None:
    starter = _FakeStarter()
    monkeypatch.setattr(
        linear_startup,
        "warn_if_submodule_pin_drifted",
        lambda *_args: starter.calls.append("submodule_pin_guard"),
    )

    assert linear_startup.run_linear_startup(starter, _options()) == 0
    assert starter.calls.index("generate_encryption_keys") < starter.calls.index(
        "generate_service_configuration"
    )
    assert starter.calls[-4:] == [
        "submodule_pin_guard",
        "show_container_status_and_verify_ports",
        "check_comfyui_models",
        "show_detached_status_summary",
    ]


def test_linear_startup_stops_at_the_first_failed_stage(monkeypatch) -> None:
    starter = _FakeStarter(fail_at="generate_kong_configuration")
    monkeypatch.setattr(
        linear_startup, "warn_if_submodule_pin_drifted", lambda *_args: None
    )

    assert linear_startup.run_linear_startup(starter, _options()) == 1
    assert "generate_kong_configuration" in starter.calls
    assert "generate_litellm_configuration" not in starter.calls
    assert "start_docker_services" not in starter.calls


def test_json_mode_emits_only_one_json_document_to_stdout(monkeypatch, capsys) -> None:
    starter = _FakeStarter()
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)

    assert linear_startup.run_linear_startup(starter, _options()) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"ok": True, "services": []}
    assert captured.out.count("\n") == 1
    assert "progress:prepare_environment" in captured.err


def test_json_mode_emits_terminal_failure_json(monkeypatch, capsys) -> None:
    starter = _FakeStarter(fail_at="generate_kong_configuration")
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)

    assert linear_startup.run_linear_startup(starter, _options()) == 1

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"ok": False, "exit_code": 1}
    assert captured.out.count("\n") == 1
    assert "progress:generate_kong_configuration" in captured.err


def test_interactive_log_failure_is_returned(monkeypatch) -> None:
    starter = _FakeStarter(log_return_code=17)
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)

    result = linear_startup.run_linear_startup(
        starter,
        _options(detach=False, json_output=False),
    )

    assert result == 17
    assert starter.calls[-1] == "show_container_logs"


def test_json_mode_routes_child_process_stdout_to_stderr(monkeypatch, capfd) -> None:
    starter = _FakeStarter()
    original = starter.prepare_environment

    def prepare(*args, **kwargs):
        subprocess.run(
            [sys.executable, "-c", "print('child-progress')"],
            check=True,
        )
        return original(*args, **kwargs)

    starter.prepare_environment = prepare
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_: None)

    assert linear_startup.run_linear_startup(starter, _options()) == 0

    captured = capfd.readouterr()
    assert json.loads(captured.out) == {"ok": True, "services": []}
    assert "child-progress" in captured.err
