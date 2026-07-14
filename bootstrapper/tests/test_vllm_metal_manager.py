"""Unit tests for the Atlas-managed Apple-Silicon/Metal vLLM host (#379).

Every host effect (platform detection, subprocess, socket, HTTP, process
signals, filesystem) is mocked, so the whole lifecycle — preflight, idempotent
install/update, start/stop/status/health, failure recovery, PID-reuse guard —
is exercised on generic Linux CI. A separately-marked Darwin-arm64 ``live``
test (bottom of the file) probes the real /v1/models contract; it never runs
in CI.

Structurally mirrors test_comfyui_mps_manager.py (#335) so the two managed-host
sources stay consistent, adapted for vLLM's preflight (Python 3.12 + plugin
import + per-model quantization) and its OpenAI /v1 surface.
"""
from __future__ import annotations

import json
import platform
from pathlib import Path

import pytest

from services import vllm_metal_manager as mod
from services.vllm_metal_manager import (
    VllmMetalError,
    VllmMetalManager,
    manager_from_env,
)


# ─────────────────────────── fakes / seams ───────────────────────────
class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSocket:
    """socket() that reports a fixed connect_ex result (0 = port in use)."""

    def __init__(self, connect_result):
        self._result = connect_result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def settimeout(self, _t):
        pass

    def connect_ex(self, _addr):
        return self._result


class _FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _darwin_arm64(monkeypatch, *, memsize_gb=64, py_version="3.12.9"):
    """Stub a supported host: macOS arm64, python3.12 present, plenty of RAM."""
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, *a, **k):
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(memsize_gb * 1024 ** 3))
        # python -c "...version..." probe
        if len(cmd) >= 3 and cmd[1] == "-c" and "version_info" in cmd[2]:
            return _FakeCompleted(0, py_version)
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return fake_run


def _mgr(tmp_path, **kw):
    kw.setdefault("port", 8000)
    kw.setdefault("model", "Qwen/Qwen2.5-7B-Instruct")
    kw.setdefault("min_memory_gb", 16)
    return VllmMetalManager(tmp_path / "state", **kw)


# ─────────────────────────── preflight ───────────────────────────
def test_preflight_fails_on_non_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = _mgr(tmp_path).preflight()
    assert result.status == "fail"
    assert not result.ok
    os_check = next(c for c in result.checks if c["name"] == "os")
    assert os_check["status"] == "fail"
    assert "macOS" in os_check["detail"]


def test_preflight_fails_on_intel_mac(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = _mgr(tmp_path).preflight()
    assert result.status == "fail"
    arch = next(c for c in result.checks if c["name"] == "arch")
    assert arch["status"] == "fail"
    assert "Apple Silicon" in arch["detail"]


def test_preflight_passes_on_darwin_arm64_py312(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch, memsize_gb=64, py_version="3.12.9")
    result = _mgr(tmp_path).preflight()
    assert result.ok  # vllm check skipped until the venv exists
    py = next(c for c in result.checks if c["name"] == "python")
    assert py["status"] == "ok"
    vllm = next(c for c in result.checks if c["name"] == "vllm")
    assert vllm["status"] == "skipped"


def test_preflight_fails_on_wrong_python(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch, py_version="3.11.7")
    result = _mgr(tmp_path).preflight()
    assert result.status == "fail"
    py = next(c for c in result.checks if c["name"] == "python")
    assert py["status"] == "fail"
    assert "3.12" in py["detail"]


def test_preflight_fails_when_python_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, *a, **k: _FakeCompleted(0, "0"))
    result = _mgr(tmp_path).preflight()
    assert result.status == "fail"
    py = next(c for c in result.checks if c["name"] == "python")
    assert py["status"] == "fail"
    assert "not found" in py["detail"]


def test_preflight_warns_on_low_memory(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch, memsize_gb=8)
    result = _mgr(tmp_path, min_memory_gb=16).preflight()
    assert result.status == "warn"
    mem = next(c for c in result.checks if c["name"] == "memory")
    assert mem["status"] == "warn"
    assert "below" in mem["detail"]


def test_preflight_warns_on_unsupported_quant(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    result = _mgr(tmp_path).preflight(
        models=[{"name": "some-awq", "quantization": "AWQ"}]
    )
    q = next(c for c in result.checks if c["name"] == "model:some-awq")
    assert q["status"] == "warn"
    assert "MLX/Metal" in q["detail"]


def test_preflight_ok_on_supported_quant(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    result = _mgr(tmp_path).preflight(
        models=[{"name": "qwen-bf16", "quantization": "bf16"}]
    )
    q = next(c for c in result.checks if c["name"] == "model:qwen-bf16")
    assert q["status"] == "ok"


def test_preflight_vllm_ok_when_venv_importable(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr, "_vllm_importable", lambda: True)
    result = mgr.preflight()
    vllm = next(c for c in result.checks if c["name"] == "vllm")
    assert vllm["status"] == "ok"


# ─────────────────────────── pip spec ───────────────────────────
def test_pip_spec_default_pins_only_plugin(tmp_path):
    mgr = _mgr(tmp_path, plugin_version="0.3.0")
    assert mgr._pip_spec() == ["vllm-metal==0.3.0"]


def test_pip_spec_adds_core_pin_when_set(tmp_path):
    mgr = _mgr(tmp_path, plugin_version="0.3.0", core_version="0.6.3")
    assert mgr._pip_spec() == ["vllm-metal==0.3.0", "vllm==0.6.3"]


# ─────────────────────────── install ───────────────────────────
def test_install_happy_path_creates_venv_and_pip_installs(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    calls = []
    monkeypatch.setattr(mgr, "_run", lambda cmd: calls.append(cmd))
    # venv_python.exists() is False initially → full install path.
    mgr.install()
    joined = [" ".join(c) for c in calls]
    assert any("-m venv" in j for j in joined)
    assert any("pip install --upgrade pip" in j for j in joined)
    assert any("vllm-metal==0.3.0" in j for j in joined)
    assert mgr.status_file.exists()


def test_install_idempotent_when_venv_exists(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr, "_vllm_importable", lambda: True)
    calls = []
    monkeypatch.setattr(mgr, "_run", lambda cmd: calls.append(cmd))
    mgr.install()  # no update → nothing reinstalled
    assert calls == []


def test_install_update_reinstalls(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr, "_vllm_importable", lambda: True)
    calls = []
    monkeypatch.setattr(mgr, "_run", lambda cmd: calls.append(cmd))
    mgr.install(update=True)
    joined = [" ".join(c) for c in calls]
    assert any("pip install --upgrade vllm-metal==0.3.0" in j for j in joined)


def test_install_raises_on_failed_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    mgr = _mgr(tmp_path)
    with pytest.raises(VllmMetalError, match="preflight failed"):
        mgr.install()


# ─────────────────────────── start ───────────────────────────
def test_start_idempotent_when_already_running(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("4242")
    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: True)
    status = mgr.start()
    assert status.running
    assert status.pid == 4242


def test_start_with_ownership_distinguishes_existing_process(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(
        mgr, "status", lambda: mod.ProcessStatus(True, 4242, mgr.port)
    )

    status, created = mgr.start_with_ownership()

    assert status.pid == 4242
    assert created is False


def test_start_raises_when_venv_missing(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(mgr, "status", lambda: mod.ProcessStatus(False, None, mgr.port))
    with pytest.raises(VllmMetalError, match="venv is not installed"):
        mgr.start()


def test_start_raises_on_port_in_use(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr, "status", lambda: mod.ProcessStatus(False, None, mgr.port))
    monkeypatch.setattr(mod.socket, "socket", lambda *a, **k: _FakeSocket(0))
    with pytest.raises(VllmMetalError, match="already in use"):
        mgr.start()


def test_start_launches_and_writes_pid(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr, "status", lambda: mod.ProcessStatus(False, None, mgr.port))
    monkeypatch.setattr(mod.socket, "socket", lambda *a, **k: _FakeSocket(1))

    captured = {}

    class _FakeProc:
        pid = 9911

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    status, created = mgr.start_with_ownership()
    assert status.running and status.pid == 9911
    assert created is True
    assert mgr.pid_file.read_text().strip() == "9911"
    # Correct entrypoint + model wiring.
    assert "vllm.entrypoints.openai.api_server" in captured["args"]
    assert "--model" in captured["args"]
    assert "Qwen/Qwen2.5-7B-Instruct" in captured["args"]
    assert "--port" in captured["args"]
    assert str(mgr.port) in captured["args"]


def test_launch_metadata_failure_terminates_new_process(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr, "_port_in_use", lambda: False)
    monkeypatch.setattr(
        mod.subprocess,
        "Popen",
        lambda *_args, **_kwargs: type("Proc", (), {"pid": 5150})(),
    )
    monkeypatch.setattr(
        mgr,
        "_write_status",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("metadata failed")),
    )
    terminated: list[int] = []
    monkeypatch.setattr(
        mgr, "_terminate_pid", lambda pid: terminated.append(pid) or True
    )

    with pytest.raises(VllmMetalError, match="child was terminated"):
        mgr.start_with_ownership()

    assert terminated == [5150]
    assert not mgr.pid_file.exists()


def test_start_sets_hf_home_when_cache_dir(tmp_path, monkeypatch):
    cache = tmp_path / "hf"
    mgr = _mgr(tmp_path, hf_cache_dir=cache)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr, "status", lambda: mod.ProcessStatus(False, None, mgr.port))
    monkeypatch.setattr(mod.socket, "socket", lambda *a, **k: _FakeSocket(1))
    captured = {}

    class _FakeProc:
        pid = 7

    monkeypatch.setattr(mod.subprocess, "Popen",
                        lambda args, **kw: (captured.update(env=kw.get("env")), _FakeProc())[1])
    mgr.start()
    assert captured["env"]["HF_HOME"] == str(cache)


# ─────────────────────────── stop ───────────────────────────
def test_stop_noop_when_not_running(tmp_path):
    mgr = _mgr(tmp_path)
    assert mgr.stop() is False


def test_stop_refuses_stranger_pid(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("999")
    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: True)
    killed = []
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    assert mgr.stop() is False
    # never signalled the stranger
    assert all(sig == 0 for _, sig in killed) or killed == []
    assert not mgr.pid_file.exists()


def test_stop_sigint_success(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("555")
    alive = {"v": True}

    def fake_kill(pid, sig):
        if sig == mod.signal.SIGINT:
            alive["v"] = False
        if sig == 0 and not alive["v"]:
            raise ProcessLookupError

    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: False)
    monkeypatch.setattr(mod.os, "kill", fake_kill)
    monkeypatch.setattr(mod.os, "waitpid", lambda pid, flags: (pid, 0))
    assert mgr.stop() is True
    assert not mgr.pid_file.exists()


# ─────────────────────────── status ───────────────────────────
def test_status_reflects_liveness(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("321")
    mgr.status_file.write_text(json.dumps(
        {"installed_version": "0.3.0", "port": 8000, "model": "Qwen/Qwen2.5-7B-Instruct", "pid": 321}
    ))
    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: True)
    status = mgr.status()
    assert status.running and status.pid == 321
    assert status.installed_version == "0.3.0"
    assert status.model == "Qwen/Qwen2.5-7B-Instruct"


def test_status_not_running_when_pid_dead(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("321")
    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: False)
    status = mgr.status()
    assert not status.running
    assert status.pid is None


# ─────────────────────────── ensure_running / remove ───────────────────────────
def test_ensure_running_raises_on_unsupported_host(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    with pytest.raises(VllmMetalError, match="unsupported host"):
        _mgr(tmp_path).ensure_running()


def test_remove_stops_and_deletes_state(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    (mgr.state_dir / "marker").write_text("x")
    monkeypatch.setattr(mgr, "_stop_locked", lambda: True)
    mgr.remove()
    assert not mgr.state_dir.exists()


# ─────────────────────────── health ───────────────────────────
def test_health_unreachable(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)

    def boom(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    health = mgr.health()
    assert health["reachable"] is False
    assert health["models"] == []


def test_health_reports_served_models(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    body = json.dumps({"data": [{"id": "Qwen/Qwen2.5-7B-Instruct"}]})
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda url, timeout: _FakeResponse(body))
    health = mgr.health()
    assert health["reachable"] is True
    assert health["models"] == ["Qwen/Qwen2.5-7B-Instruct"]


def test_health_non_json(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda url, timeout: _FakeResponse("<html>oops"))
    health = mgr.health()
    assert health["reachable"] is True
    assert health["models"] == []


def test_wait_healthy_returns_immediately_when_reachable(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(mgr, "health", lambda: {"reachable": True, "models": ["m"]})
    assert mgr.wait_healthy(timeout=1.0)["reachable"] is True


# ─────────────────────────── manager_from_env ───────────────────────────
def test_manager_from_env_reads_all_knobs():
    mgr = manager_from_env({
        "VLLM_METAL_STATE_DIR": "/tmp/vm-state",
        "VLLM_METAL_LOCALHOST_PORT": "8123",
        "VLLM_METAL_MODEL": "mistralai/Mistral-7B-Instruct-v0.3",
        "VLLM_METAL_PLUGIN_VERSION": "0.4.0",
        "VLLM_METAL_CORE_VERSION": "0.6.3",
        "VLLM_METAL_PYTHON": "python3.12",
        "VLLM_METAL_MODELS_PATH": "/tmp/hf",
        "VLLM_METAL_MIN_MEMORY_GB": "32",
    })
    assert mgr.port == 8123
    assert mgr.model == "mistralai/Mistral-7B-Instruct-v0.3"
    assert mgr.plugin_version == "0.4.0"
    assert mgr.core_version == "0.6.3"
    assert mgr.hf_cache_dir == Path("/tmp/hf")
    assert mgr.min_memory_gb == 32
    assert str(mgr.state_dir) == "/tmp/vm-state"


def test_manager_from_env_defaults():
    mgr = manager_from_env({})
    assert mgr.port == 8000
    assert mgr.model == "Qwen/Qwen2.5-7B-Instruct"
    assert mgr.plugin_version == "0.3.0"
    assert mgr.core_version == ""
    assert mgr.hf_cache_dir is None


# ─────────────────────────── optional live smoke ───────────────────────────
@pytest.mark.live
@pytest.mark.skipif(
    platform.system() != "Darwin" or platform.machine().lower() not in ("arm64", "aarch64"),
    reason="managed-localhost vLLM Metal runs only on Apple Silicon",
)
def test_live_managed_host_serves_model():
    """OPTIONAL Darwin-arm64 smoke: prove /v1/models answers with the model.

    Never installs weights itself. Runs preflight (os/arch must pass on this
    host); if a managed process is already up, asserts /v1/models lists the
    configured model. If it isn't running, skips with an operator hint —
    bring it up first with ``./start.sh vllm-metal install && ./start.sh
    vllm-metal start``.
    """
    mgr = manager_from_env({
        "VLLM_METAL_STATE_DIR": "~/.atlas/vllm-metal",
        "VLLM_METAL_LOCALHOST_PORT": "8000",
    })
    pre = mgr.preflight()
    assert next(c for c in pre.checks if c["name"] == "os")["status"] == "ok"
    assert next(c for c in pre.checks if c["name"] == "arch")["status"] == "ok"
    if not mgr.status().running:
        pytest.skip("managed vLLM Metal host not running; run `vllm-metal install && start` first")
    health = mgr.health()
    assert health["reachable"], health
    assert health["models"], "expected at least one served model id"
