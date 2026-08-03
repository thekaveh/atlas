from __future__ import annotations

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
    def __init__(self, fail_at: str | None = None):
        self.calls: list[str] = []
        self.fail_at = fail_at
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
            return name != self.fail_at

        return call


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
