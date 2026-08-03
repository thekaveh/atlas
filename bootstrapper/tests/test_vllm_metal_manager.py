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

import hashlib
import io
import json
import platform
import subprocess
import tarfile
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
def test_pip_spec_default_uses_checksum_pinned_release_wheel(tmp_path):
    mgr = _mgr(tmp_path)
    spec = mgr._pip_spec()
    assert len(spec) == 1
    assert "vllm_metal-0.3.0.dev20260713103604" in spec[0]
    assert spec[0].endswith(
        "#sha256=7423302ad116656d712f4b52811ebca13a48d246bcde2b8c093b2a2a01d7c03f"
    )
    assert mod._DEFAULT_CORE_SHA256 == (
        "0862453adc1f3339f1a0c9dca1179c34d6ed6e118f87b6e5bddd120af614ac66"
    )


def test_pip_spec_rejects_unverified_release_override(tmp_path):
    mgr = _mgr(tmp_path, plugin_version="0.3.0", core_version="0.6.3")
    with pytest.raises(VllmMetalError, match="verified only"):
        mgr._pip_spec()


def test_installed_versions_probe_imports_plugin_core_and_api(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(0, f"{mgr.plugin_version}\n{mgr.core_version}+cpu\n")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    assert mgr._installed_versions() == (mgr.plugin_version, f"{mgr.core_version}+cpu")
    probe = captured["cmd"][-1]
    assert "import vllm_metal" in probe
    assert "vllm.entrypoints.openai.api_server" in probe
    assert "m.version('vllm-metal')" in probe


def test_install_core_verifies_archive_and_build_contract(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    archive = tmp_path / "vllm.tar.gz"
    prefix = f"vllm-{mgr.core_version}"
    with tarfile.open(archive, "w:gz") as bundle:
        payload = b"setuptools\n"
        info = tarfile.TarInfo(f"{prefix}/requirements/cpu.txt")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
        project = b"[build-system]\nrequires=[]\n"
        info = tarfile.TarInfo(f"{prefix}/pyproject.toml")
        info.size = len(project)
        bundle.addfile(info, io.BytesIO(project))

    expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    downloads = []

    def fake_open(url, *, timeout):
        downloads.append(url)
        assert timeout == 60
        return io.BytesIO(archive.read_bytes())

    calls = []
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(
        mod.urllib.request,
        "urlretrieve",
        lambda *_args, **_kwargs: pytest.fail("urlretrieve has no timeout"),
    )
    monkeypatch.setattr(mod, "_DEFAULT_CORE_SHA256", expected_digest)
    monkeypatch.setattr(mgr, "_run", lambda cmd, **kwargs: calls.append((cmd, kwargs)))

    mgr._install_core()

    assert downloads == [
        f"https://github.com/vllm-project/vllm/releases/download/v{mgr.core_version}/"
        f"vllm-{mgr.core_version}.tar.gz"
    ]
    assert calls[0][0][-1].endswith("requirements/cpu.txt")
    assert calls[1][1]["env"]["VLLM_TARGET_DEVICE"] == "cpu"
    assert calls[1][1]["env"]["CXXFLAGS"] == "-Wno-parentheses"


def test_install_core_rejects_checksum_mismatch(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)

    def fake_open(_url, *, timeout):
        assert timeout == 60
        return io.BytesIO(b"not-the-pinned-archive")

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(
        mod.urllib.request,
        "urlretrieve",
        lambda *_args, **_kwargs: pytest.fail("urlretrieve has no timeout"),
    )

    with pytest.raises(VllmMetalError, match="checksum mismatch"):
        mgr._install_core()


def test_safe_archive_extraction_rejects_traversal(tmp_path):
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as bundle:
        payload = b"owned"
        info = tarfile.TarInfo("../outside")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))

    with tarfile.open(archive) as bundle:
        with pytest.raises(VllmMetalError, match="unsafe path"):
            VllmMetalManager._extract_archive_safely(bundle, tmp_path / "extract")


# ─────────────────────────── install ───────────────────────────
def test_install_happy_path_creates_venv_and_pip_installs(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    calls = []
    monkeypatch.setattr(mgr, "_vllm_importable", lambda: True)
    monkeypatch.setattr(mgr, "_run", lambda cmd, **kwargs: calls.append(cmd))
    monkeypatch.setattr(
        mgr,
        "_install_core",
        lambda: calls.append([str(mgr.venv_python), "-m", "pip", "install", "requirements/cpu.txt"]),
    )
    # venv_python.exists() is False initially → full install path.
    mgr.install()
    joined = [" ".join(c) for c in calls]
    assert any("-m venv" in j for j in joined)
    assert any("pip install --upgrade pip" in j for j in joined)
    assert any("requirements/cpu.txt" in j for j in joined)
    assert any("vllm_metal-0.3.0.dev20260713103604" in j for j in joined)
    assert mgr.status_file.exists()


def test_install_idempotent_when_venv_exists(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr, "_vllm_importable", lambda: True)
    monkeypatch.setattr(
        mgr, "_installed_versions", lambda: (mgr.plugin_version, f"{mgr.core_version}+cpu")
    )
    mgr._write_status(
        installed_version=mgr.plugin_version,
        installed_core_version=mgr.core_version,
    )
    calls = []
    monkeypatch.setattr(mgr, "_run", lambda cmd, **kwargs: calls.append(cmd))
    monkeypatch.setattr(
        mgr,
        "_install_core",
        lambda: calls.append([str(mgr.venv_python), "-m", "pip", "install", "requirements/cpu.txt"]),
    )
    mgr.install()  # no update → nothing reinstalled
    assert calls == []


def test_install_update_reinstalls(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr, "_vllm_importable", lambda: True)
    calls = []
    monkeypatch.setattr(mgr, "_run", lambda cmd, **kwargs: calls.append(cmd))
    monkeypatch.setattr(
        mgr,
        "_install_core",
        lambda: calls.append([str(mgr.venv_python), "-m", "pip", "install", "requirements/cpu.txt"]),
    )
    mgr.install(update=True)
    joined = [" ".join(c) for c in calls]
    assert any("requirements/cpu.txt" in j for j in joined)
    assert any("vllm_metal-0.3.0.dev20260713103604" in j for j in joined)


def test_install_reconciles_stale_recorded_versions(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    mgr.status_file.write_text(json.dumps({
        "installed_version": "0.1.0",
        "installed_core_version": "0.13.0",
    }))
    monkeypatch.setattr(mgr, "_vllm_importable", lambda: True)
    monkeypatch.setattr(
        mgr, "_installed_versions", lambda: (mgr.plugin_version, f"{mgr.core_version}+cpu")
    )
    calls = []
    monkeypatch.setattr(mgr, "_run", lambda cmd, **kwargs: calls.append(cmd))
    monkeypatch.setattr(mgr, "_install_core", lambda: calls.append(["install-core"]))

    mgr.install()

    assert ["install-core"] in calls
    assert any("vllm_metal-0.3.0.dev20260713103604" in " ".join(c) for c in calls)


def test_install_raises_on_failed_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    mgr = _mgr(tmp_path)
    with pytest.raises(VllmMetalError, match="preflight failed"):
        mgr.install()


def test_run_translates_install_timeout(tmp_path, monkeypatch):
    def time_out(_cmd, **_kwargs):
        raise subprocess.TimeoutExpired(["slow"], 1)

    monkeypatch.setattr(mod, "run_with_deadline", time_out)
    with pytest.raises(VllmMetalError, match="timed out"):
        _mgr(tmp_path)._run(["slow"])


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

    def fake_killpg(pid, sig):
        if sig == mod.signal.SIGINT:
            alive["v"] = False
        if sig == 0 and not alive["v"]:
            raise ProcessLookupError

    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: False)
    monkeypatch.setattr(mgr, "_managed_process_alive", lambda pid: alive["v"])
    monkeypatch.setattr(mod.os, "killpg", fake_killpg)
    monkeypatch.setattr(mod.os, "waitpid", lambda pid, flags: (pid, 0))
    assert mgr.stop() is True
    assert not mgr.pid_file.exists()


def test_stop_reaps_exited_leader_during_grace_period(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("556")
    state = {"alive": True, "signals": []}

    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: False)
    monkeypatch.setattr(mgr, "_managed_process_alive", lambda pid: state["alive"])
    monkeypatch.setattr(
        mod.os, "killpg", lambda pid, sig: state["signals"].append(sig)
    )

    def reap(pid, flags):
        state["alive"] = False
        return pid, 0

    monkeypatch.setattr(mod.os, "waitpid", reap)

    assert mgr.stop() is True
    assert state["signals"] == [mod.signal.SIGINT]


def test_stop_signals_process_group_when_leader_has_exited(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("777")
    alive = {"group": True}
    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(mgr, "_process_group_alive", lambda pgid: alive["group"])
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: False)
    signals = []

    def fake_killpg(pgid, sig):
        signals.append((pgid, sig))
        if sig == mod.signal.SIGINT:
            alive["group"] = False

    monkeypatch.setattr(mod.os, "killpg", fake_killpg)
    monkeypatch.setattr(mod.os, "waitpid", lambda pid, flags: (pid, 0))

    assert mgr.stop() is True
    assert signals == [(777, mod.signal.SIGINT)]


def test_stop_waits_for_group_after_sigkill(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("888")
    state = {"killed": False, "post_kill_probes": 0}

    def alive(_pid):
        if not state["killed"]:
            return True
        state["post_kill_probes"] += 1
        return state["post_kill_probes"] < 3

    def killpg(_pid, sig):
        if sig == mod.signal.SIGKILL:
            state["killed"] = True

    clock = {"value": 0.0}
    monkeypatch.setattr(mgr, "_managed_process_alive", alive)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: False)
    monkeypatch.setattr(mod.os, "killpg", killpg)
    monkeypatch.setattr(mod.os, "waitpid", lambda pid, flags: (pid, 0))
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        mod.time,
        "monotonic",
        lambda: clock.__setitem__("value", clock["value"] + 2.0) or clock["value"],
    )

    assert mgr.stop() is True
    assert state["post_kill_probes"] >= 3


# ─────────────────────────── status ───────────────────────────
def test_status_reflects_liveness(tmp_path, monkeypatch):
    # status() gates liveness on _managed_process_alive AND not _pid_is_stranger
    # (NOT _pid_alive alone). Patch the exact pair status() consults so the
    # verdict never touches the real OS process table: pid 321 colliding with a
    # live CI daemon made _pid_is_stranger's `ps` probe return True ("stranger")
    # intermittently, flipping running=False under the full suite (#828). The
    # recycled-pid product behavior itself stays covered by
    # test_status_recycled_pid_reports_not_running.
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("321")
    mgr.status_file.write_text(json.dumps(
        {"installed_version": mgr.plugin_version, "installed_core_version": mgr.core_version,
         "port": 8000, "model": "Qwen/Qwen2.5-7B-Instruct", "pid": 321}
    ))
    monkeypatch.setattr(mgr, "_managed_process_alive", lambda pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: False)
    status = mgr.status()
    assert status.running and status.pid == 321
    assert status.installed_version == mgr.plugin_version
    assert status.model == "Qwen/Qwen2.5-7B-Instruct"


def test_status_not_running_when_pid_dead(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("321")
    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: False)
    status = mgr.status()
    assert not status.running
    assert status.pid is None


def test_status_recycled_pid_reports_not_running(tmp_path, monkeypatch):
    """#647: a live PID whose argv is NOT our vLLM Metal (a recycled/foreign
    process) reports running=False even though kill-0 succeeds — status() must
    apply the same _pid_is_stranger cross-check the comfyui-mps manager does."""
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("4242")
    monkeypatch.setattr(VllmMetalManager, "_managed_process_alive", lambda self, pid: True)
    monkeypatch.setattr(VllmMetalManager, "_pid_is_stranger", lambda self, pid: True)
    st = mgr.status()
    assert not st.running and st.pid is None


def test_pid_alive_treats_permission_denied_as_not_ours(tmp_path, monkeypatch):
    """#647: a PID we cannot signal (PermissionError — a foreign, likely
    root-owned, process recycled the number) is NOT our user-owned process."""
    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(mod.os, "kill", denied)
    assert VllmMetalManager._pid_alive(4242) is False

    monkeypatch.setattr(mod.os, "killpg", denied)
    assert VllmMetalManager._process_group_alive(4242) is False

    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("4242")
    st = mgr.status()
    assert not st.running and st.pid is None


def test_start_clears_stale_pidfile_before_launch(tmp_path, monkeypatch):
    """#647 arm 2: the not-running path clears a lingering stale/stranger pidfile
    before the launch preconditions, so a failed relaunch never leaves a stale
    pointer for a later probe (mirrors comfyui-mps)."""
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("4242")
    # Alive-but-stranger PID → status() reports not running.
    monkeypatch.setattr(VllmMetalManager, "_managed_process_alive", lambda self, pid: True)
    monkeypatch.setattr(VllmMetalManager, "_pid_is_stranger", lambda self, pid: True)
    # venv missing → _start_locked raises *after* clearing the stale pidfile.
    with pytest.raises(VllmMetalError):
        mgr.start_with_ownership()
    assert not mgr.pid_file.exists()


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
    assert mgr.plugin_version == "0.3.0.dev20260713103604"
    assert mgr.core_version == "0.24.0"
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
