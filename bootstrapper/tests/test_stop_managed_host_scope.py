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

import stop as stop_module
from services import blender_mcp_manager, comfyui_mps_manager, vllm_metal_manager


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
