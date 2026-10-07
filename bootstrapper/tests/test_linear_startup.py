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
        # AtlasStarter state the result block reads (#989): no image left out.
        self.skipped_builds: list[str] = []
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


# --- managed-host preflight runs before the warm-start teardown (#1342) ------


def test_managed_host_preflight_runs_before_port_configuration(monkeypatch) -> None:
    starter = _FakeStarter()
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_a: None)

    assert linear_startup.run_linear_startup(starter, _options()) == 0
    assert starter.calls.index("preflight_managed_host_processes") < starter.calls.index(
        "handle_port_configuration"
    )


def test_failed_managed_host_preflight_never_reaches_the_teardown(monkeypatch) -> None:
    """handle_port_configuration stops a running stack on a warm start; a host
    that would refuse to start must exit before it."""
    starter = _FakeStarter(fail_at="preflight_managed_host_processes")
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_a: None)

    assert linear_startup.run_linear_startup(starter, _options()) == 1
    assert "handle_port_configuration" not in starter.calls
    assert "start_managed_host_processes" not in starter.calls
    assert "start_docker_services" not in starter.calls


def test_tui_pipeline_preflights_managed_hosts_before_configuring_ports() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "ui" / "textual" / "screens" / "wizard_screen.py"
    ).read_text(encoding="utf-8")
    assert source.index('("Preflight managed hosts"') < source.index('("Configure ports"')

import pytest  # noqa: E402


# --- _managed_host_launch_blocker (#1342) ------------------------------------


class _Pre:
    def __init__(self, ok=True, checks=()):
        self.ok, self.checks = ok, list(checks)


class _FakeHost:
    port = 8188
    pid_file = None

    def __init__(self, tmp_path, **state):
        """``state``: pre, pid, alive, stranger, running, port_busy, record."""
        get = state.get
        self.pid_file = tmp_path / f"host-{id(self)}.pid"
        if get("pid") is not None:
            self.pid_file.write_text(get("record", "4242\nstart_utc=x\n"), encoding="utf-8")
        self._pre, self._pid = get("pre") or _Pre(), get("pid")
        self._alive, self._stranger = get("alive", False), get("stranger", False)
        self._running, self._busy = get("running", False), get("port_busy", False)

    def preflight(self):
        return self._pre

    def _read_pid(self):
        return self._pid

    def _managed_process_alive(self, pid):
        return self._alive

    def _pid_is_stranger(self, pid):
        return self._stranger

    def status(self):
        return type("S", (), {"running": self._running})()

    def _port_in_use(self):
        return self._busy


def _blocker(host):
    import start

    return start._managed_host_launch_blocker(lambda _env: host, {}, "ComfyUI (MPS)")


def test_launch_blocker_passes_a_startable_or_running_host(tmp_path) -> None:
    assert _blocker(_FakeHost(tmp_path)) is None
    assert _blocker(_FakeHost(tmp_path, running=True, port_busy=True)) is None


def test_launch_blocker_checks_the_new_port_of_a_host_it_will_restart(tmp_path) -> None:
    """A running host recorded on another port is restarted on 8188, so a
    busy 8188 must stop the warm start before the stack is torn down (#1361);
    a bind-only change keeps the port, which the host itself holds."""
    def host(recorded_port):
        moved = _FakeHost(tmp_path, running=True, port_busy=True)
        moved.status = lambda: type("S", (), {"running": True, "pid": 77})()
        moved._launch_record = lambda: {"pid": 77, "port": recorded_port}
        return moved

    assert "port 8188 is already in use" in _blocker(host(8187))
    assert _blocker(host(8188)) is None


def test_launch_blocker_reports_each_fatal_start_refusal(tmp_path) -> None:
    pre = _Pre(ok=False, checks=[{"name": "arch", "status": "fail", "detail": "needs arm64"}])
    assert "preflight failed: arch: needs arm64" in _blocker(_FakeHost(tmp_path, pre=pre))
    untrusted = _FakeHost(tmp_path, pid=4242, alive=True, stranger=True)
    assert "ownership is mismatched or unknown" in _blocker(untrusted)
    assert "port 8188 is already in use" in _blocker(_FakeHost(tmp_path, port_busy=True))


def test_launch_blocker_lets_the_stampless_legacy_record_through(tmp_path) -> None:
    """The start only warns on a pre-framework pid record (#990)."""
    legacy = _FakeHost(tmp_path, pid=4242, alive=True, stranger=True, record="4242\n")
    assert _blocker(legacy) is None


def test_launch_blocker_never_signals_or_starts(tmp_path, monkeypatch) -> None:
    import os

    monkeypatch.setattr(os, "kill", lambda *_a: pytest.fail("preflight must not signal"))
    monkeypatch.setattr(os, "killpg", lambda *_a: pytest.fail("preflight must not signal"))
    host = _FakeHost(tmp_path, pid=4242, alive=True, stranger=True)
    host.start = host.ensure_running_with_ownership = lambda *_a: pytest.fail("must not start")
    assert _blocker(host)


def test_preflight_managed_hosts_only_checks_selected_sources(monkeypatch) -> None:
    import importlib

    import start

    starter = start.AtlasStarter.__new__(start.AtlasStarter)
    messages: list[tuple[str, str]] = []
    starter.banner = type("B", (), {"show_status_message": lambda _s, m, k: messages.append((m, k))})()
    env = {"COMFYUI_SOURCE": "managed-localhost-mps", "VLLM_METAL_SOURCE": "disabled"}
    starter.config_parser = type("P", (), {"parse_env_file": lambda _s: dict(env)})()
    seen: list[str] = []

    def blocker(factory, _env, label):
        seen.append(label)
        return "port 8188 is already in use by an unmanaged process"

    monkeypatch.setattr(start, "_managed_host_launch_blocker", blocker)
    monkeypatch.setattr(importlib, "import_module", lambda name: type("M", (), {"manager_from_env": None}))

    assert starter.preflight_managed_host_processes() is False
    assert seen == ["ComfyUI (MPS)"]
    assert messages and messages[0][1] == "error"
    assert "before a warm start would stop the running containers" in messages[0][0]

    env["COMFYUI_SOURCE"] = "container-cpu"
    seen.clear()
    assert starter.preflight_managed_host_processes() is True
    assert seen == []



@pytest.mark.parametrize("module", [m for *_rest, m in __import__("start")._MANAGED_HOST_SOURCES])
def test_launch_blocker_runs_against_each_real_manager(tmp_path, module) -> None:
    """The blocker reads private manager members; a rename must fail here, not
    refuse every warm start. A fresh state dir is startable or fails preflight
    (on a host without the platform), never an attribute error."""
    import importlib
    import socket

    import start

    mod = importlib.import_module(module)
    cls = next(v for k, v in vars(mod).items() if k.endswith("Manager") and isinstance(v, type))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    manager = cls(tmp_path / "state")
    manager.port = free_port
    problem = start._managed_host_launch_blocker(lambda _env: manager, {}, "host")
    assert problem is None or problem.startswith("preflight failed"), problem


def test_both_flows_migrate_ports_before_applying_this_runs_overrides(monkeypatch) -> None:
    """The linear flow migrated after the overrides; the TUI migrates before
    its wizard reads .env (#1391)."""
    from pathlib import Path

    starter = _FakeStarter()
    monkeypatch.setattr(linear_startup, "warn_if_submodule_pin_drifted", lambda *_a: None)
    assert linear_startup.run_linear_startup(starter, _options()) == 0
    calls = starter.calls
    assert calls.index("backfill_missing_env_vars") < calls.index("run_port_migration")
    assert calls.index("run_port_migration") < calls.index("apply_source_overrides")
    source = (Path(__file__).resolve().parents[1] / "ui" / "textual" / "integration.py").read_text()
    setup = source[source.index("def run_setup_flow("):]
    assert setup.index("run_port_migration(") < setup.index("WizardScreen(")
