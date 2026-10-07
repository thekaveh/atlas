"""Unit tests for the Atlas-managed Apple-Silicon/Metal ComfyUI host (#335).

Every host effect (platform detection, subprocess, socket, HTTP, process
signals, filesystem) is mocked, so the whole lifecycle — preflight, idempotent
install/update, start/stop/status/health, failure recovery — is exercised on
generic Linux CI. A separately-marked Darwin-arm64 ``live`` test (bottom of the
file) proves the real /system_stats-reports-MPS contract; it never runs in CI.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import platform
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import comfyui_mps_manager as mod
from services.comfyui_mps_manager import (
    ComfyUiMpsError,
    ComfyUiMpsManager,
    manager_from_env,
)
from tests.three_surface_test_utils import surface_text


def test_stack_commit_releases_managed_host_group_authority():
    import start as start_module

    starter = start_module.AtlasStarter.__new__(start_module.AtlasStarter)
    manager = SimpleNamespace(commit_started_process=Mock())
    starter._managed_hosts_started_this_run = [("probe", manager)]

    starter.commit_managed_host_processes()

    manager.commit_started_process.assert_called_once_with()
    assert starter._managed_hosts_started_this_run == []


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


def _darwin_arm64(monkeypatch, *, memsize_gb=64):
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, *a, **k):
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(memsize_gb * 1024 ** 3))
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "run_with_deadline", fake_run)
    return fake_run


def _mgr(tmp_path, **kw):
    kw.setdefault("port", 8188)
    kw.setdefault("ref", "v0.27.0")
    kw.setdefault("models_path", tmp_path / "hostmodels")
    kw.setdefault("min_memory_gb", 16)
    return ComfyUiMpsManager(tmp_path / "state", **kw)


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


def test_preflight_passes_on_darwin_arm64(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch, memsize_gb=64)
    mgr = _mgr(tmp_path)
    # A valid host models dir keeps the models_dir check green (#648).
    (mgr.models_path / "checkpoints").mkdir(parents=True)
    result = mgr.preflight()
    assert result.ok  # ok (mps skipped until venv exists)
    assert result.status in ("ok", "skipped")
    mps = next(c for c in result.checks if c["name"] == "mps")
    assert mps["status"] == "skipped"
    assert next(c for c in result.checks if c["name"] == "models_dir")["status"] == "ok"


def test_preflight_warns_on_low_memory(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch, memsize_gb=8)
    result = _mgr(tmp_path, min_memory_gb=16).preflight()
    assert result.status == "warn"
    mem = next(c for c in result.checks if c["name"] == "memory")
    assert mem["status"] == "warn"
    assert "below" in mem["detail"]


def test_preflight_missing_git_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: None if name == "git" else f"/usr/bin/{name}")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _FakeCompleted(0, str(64 * 1024 ** 3)))
    result = _mgr(tmp_path).preflight()
    assert result.status == "fail"
    git = next(c for c in result.checks if c["name"] == "git")
    assert git["status"] == "fail"


def test_preflight_flags_fp8_model_as_mps_unsafe(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    result = _mgr(tmp_path).preflight(
        models=[
            {"name": "krea-turbo", "precision": "fp8"},
            {"name": "flux-dev", "precision": "bf16"},
        ]
    )
    krea = next(c for c in result.checks if c["name"] == "model:krea-turbo")
    flux = next(c for c in result.checks if c["name"] == "model:flux-dev")
    assert krea["status"] == "warn" and "MPS" in krea["detail"]
    assert flux["status"] == "ok"


def test_preflight_probes_torch_mps_after_install(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    mgr = _mgr(tmp_path)
    mgr.venv_python.parent.mkdir(parents=True)
    mgr.venv_python.write_text("#!/bin/sh\n")

    def fake_run(cmd, *a, **k):
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(64 * 1024 ** 3))
        if str(mgr.venv_python) in cmd and "-c" in cmd:
            return _FakeCompleted(0, "1")  # torch.backends.mps.is_available() -> True
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "run_with_deadline", fake_run)
    result = mgr.preflight()
    mps = next(c for c in result.checks if c["name"] == "mps")
    assert mps["status"] == "ok"


# ─────────────────────────── install ───────────────────────────
def test_install_clones_and_builds_venv(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    calls = []

    def rec_run(cmd, *a, **k):
        calls.append(cmd)
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(64 * 1024 ** 3))
        if cmd[:2] == ["git", "clone"]:
            repo_dir = Path(cmd[-1])
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / "requirements.txt").write_text("torch\n")
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", rec_run)
    monkeypatch.setattr(mod, "run_with_deadline", rec_run)
    mgr = _mgr(tmp_path)  # fresh: repo_dir absent → clone path
    mgr.install()

    joined = [" ".join(c) for c in calls]
    assert any("git clone" in j for j in joined)
    assert any(f"checkout --force {mgr.ref}" in j for j in joined)
    assert any("venv" in j for j in joined)
    assert any("pip install torch" in j for j in joined)
    # state + model-paths written
    assert mgr.status_file.exists()
    assert mgr.model_paths_file.exists()
    assert "base_path" in mgr.model_paths_file.read_text()


def test_install_idempotent_skips_dependencies_when_fingerprint_matches(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    mgr.repo_dir.mkdir(parents=True)
    (mgr.repo_dir / "requirements.txt").write_text("torch\n")
    mgr.venv_python.parent.mkdir(parents=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    mgr._write_status(
        installed_ref=mgr.ref,
        requirements_sha256=mgr._requirements_sha256(),
        torch_pin=mgr.torch_pin,
    )

    calls = []

    def rec_run(cmd, *a, **k):
        calls.append(cmd)
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(64 * 1024 ** 3))
        if str(mgr.venv_python) in cmd and "-c" in cmd:
            return _FakeCompleted(0, "1")
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", rec_run)
    monkeypatch.setattr(mod, "run_with_deadline", rec_run)
    mgr.install()  # not update — repo + venv already present
    joined = [" ".join(c) for c in calls]
    assert not any("git clone" in j for j in joined)  # no re-clone
    assert not any("venv" in j and "-m" in j for j in joined)  # no venv rebuild
    assert any(f"checkout --force {mgr.ref}" in j for j in joined)  # ref still pinned
    assert not any("pip install" in j for j in joined)  # fingerprint matched


def _seed_installed(mgr, pin):
    mgr.repo_dir.mkdir(parents=True)
    (mgr.repo_dir / "requirements.txt").write_text("torch\n")
    mgr.venv_python.parent.mkdir(parents=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    mgr._write_status(
        installed_ref=mgr.ref,
        requirements_sha256=mgr._requirements_sha256(),
        torch_pin=pin,
    )


def test_a_changed_torch_pin_reconciles_on_a_plain_install(tmp_path, monkeypatch):
    """services/comfyui/README.md: the pin is reconciled automatically."""
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    _seed_installed(mgr, ["torch==0.0.1"])
    # stop() rewrites status; it must carry the installed pin, not the new one.
    mgr._write_status(installed_ref=mgr.ref, pid=None)
    calls = []

    def rec_run(cmd, *a, **k):
        calls.append(cmd)
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(64 * 1024 ** 3))
        if str(mgr.venv_python) in cmd and "-c" in cmd:
            return _FakeCompleted(0, "1")
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", rec_run)
    monkeypatch.setattr(mod, "run_with_deadline", rec_run)
    mgr.install()
    joined = [" ".join(c) for c in calls]
    assert any("pip install " + " ".join(mgr.torch_pin) in j for j in joined)


def test_a_failed_reconcile_leaves_no_installed_marker(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    _seed_installed(mgr, ["torch==0.0.1"])

    def run(cmd, *a, **k):
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(64 * 1024 ** 3))
        if str(mgr.venv_python) in cmd and "-c" in cmd:
            return _FakeCompleted(0, "1")
        if "pip" in cmd:
            return _FakeCompleted(1)
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", run)
    monkeypatch.setattr(mod, "run_with_deadline", run)
    with pytest.raises(mod.ComfyUiMpsError):
        mgr.install()
    assert not mgr._installed_environment_matches(mgr._requirements_sha256())


def test_install_reconciles_changed_requirements_without_update_flag(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    mgr.repo_dir.mkdir(parents=True)
    requirements = mgr.repo_dir / "requirements.txt"
    requirements.write_text("torch\n")
    mgr.venv_python.parent.mkdir(parents=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    mgr._write_status(installed_ref=mgr.ref, requirements_sha256="stale")
    calls = []

    def rec_run(cmd, *a, **k):
        calls.append(cmd)
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(64 * 1024 ** 3))
        if str(mgr.venv_python) in cmd and "-c" in cmd:
            return _FakeCompleted(0, "1")
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", rec_run)
    monkeypatch.setattr(mod, "run_with_deadline", rec_run)
    mgr.install()
    assert any(
        "pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0" in " ".join(c)
        for c in calls
    )


def test_install_fetches_when_ref_missing_locally(tmp_path, monkeypatch):
    """A COMFYUI_MPS_REF bump without --update must fetch+retry, not hard-fail."""
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path, ref="v9.9.9")
    mgr.repo_dir.mkdir(parents=True)
    (mgr.repo_dir / "requirements.txt").write_text("torch\n")
    mgr.venv_python.parent.mkdir(parents=True)
    mgr.venv_python.write_text("#!/bin/sh\n")

    calls = []
    state = {"fetched": False}

    def rec_run(cmd, *a, **k):
        calls.append(cmd)
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(64 * 1024 ** 3))
        if str(mgr.venv_python) in cmd and "-c" in cmd:
            return _FakeCompleted(0, "1")
        if cmd[2:4] == ["fetch", "--tags"] or (len(cmd) > 3 and cmd[3] == "fetch"):
            state["fetched"] = True
            return _FakeCompleted(0)
        if "fetch" in cmd:
            state["fetched"] = True
            return _FakeCompleted(0)
        if "checkout" in cmd:
            # First checkout (pre-fetch) fails: ref unknown locally.
            if not state["fetched"]:
                return _FakeCompleted(1, stderr="error: pathspec 'v9.9.9' did not match")
            return _FakeCompleted(0)
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", rec_run)
    monkeypatch.setattr(mod, "run_with_deadline", rec_run)
    mgr.install(update=False)  # must NOT raise
    joined = [" ".join(c) for c in calls]
    assert state["fetched"], "expected a fetch fallback when the ref was missing"
    assert sum("checkout --force v9.9.9" in j for j in joined) == 2  # failed + retried


def test_update_reinstalls_torch(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)
    mgr = _mgr(tmp_path)
    mgr.repo_dir.mkdir(parents=True)
    (mgr.repo_dir / "requirements.txt").write_text("torch\n")
    mgr.venv_python.parent.mkdir(parents=True)
    mgr.venv_python.write_text("#!/bin/sh\n")

    calls = []

    def rec_run(cmd, *a, **k):
        calls.append(cmd)
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(64 * 1024 ** 3))
        if str(mgr.venv_python) in cmd and "-c" in cmd:
            return _FakeCompleted(0, "1")
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", rec_run)
    monkeypatch.setattr(mod, "run_with_deadline", rec_run)
    mgr.install(update=True)
    joined = [" ".join(c) for c in calls]
    # #648: update re-applies the exact pinned Torch stack (no --upgrade drift).
    assert any(
        "pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0" in j
        for j in joined
    )
    assert any("fetch --tags" in j for j in joined)  # update fetches


def test_install_refuses_on_unsupported_host(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(ComfyUiMpsError, match="preflight failed"):
        _mgr(tmp_path).install()


def test_run_raises_on_command_failure(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch)

    def fail_run(cmd, *a, **k):
        if cmd[:1] == ["sysctl"]:
            return _FakeCompleted(0, str(64 * 1024 ** 3))
        if cmd[:2] == ["git", "clone"]:
            return _FakeCompleted(128, stderr="fatal: could not read from remote")
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    monkeypatch.setattr(mod, "run_with_deadline", fail_run)
    with pytest.raises(ComfyUiMpsError, match="command failed"):
        _mgr(tmp_path).install()


def test_run_translates_install_timeout(tmp_path, monkeypatch):
    def time_out(_cmd, **_kwargs):
        raise subprocess.TimeoutExpired(["slow"], 1)

    monkeypatch.setattr(mod, "run_with_deadline", time_out)
    with pytest.raises(ComfyUiMpsError, match="timed out"):
        _mgr(tmp_path)._run(["slow"])


def test_run_translates_output_overflow(tmp_path, monkeypatch):
    def overflow(_cmd, **_kwargs):
        raise mod.CommandOutputTooLarge

    monkeypatch.setattr(mod, "run_with_deadline", overflow)
    with pytest.raises(ComfyUiMpsError, match="output limit"):
        _mgr(tmp_path)._run(["noisy"])


# ─────────────────────────── start / stop / status ───────────────────────────
def _install_stub(mgr):
    """Pretend an install already happened (venv + repo + model paths)."""
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    mgr.repo_dir.mkdir(parents=True, exist_ok=True)
    (mgr.repo_dir / "main.py").write_text("# comfyui\n")
    (mgr.repo_dir / "requirements.txt").write_text("torch\n")
    mgr.model_paths_file.parent.mkdir(parents=True, exist_ok=True)
    mgr.model_paths_file.write_text("atlas_host:\n  base_path: /x\n")


def test_start_launches_and_records_pid(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    monkeypatch.setattr(mod.socket, "socket", lambda *a, **k: _FakeSocket(1))  # port free
    launched = {}

    def fake_popen(args, **kwargs):
        # The manager also shells out to `ps` to stamp the pid file with the
        # process start time — its identity, replacing the old argv-substring
        # guess. Record only the real launch.
        if args and args[0] == "ps":
            return SimpleNamespace(pid=0, returncode=0, stdout="", stderr="")
        launched["args"] = args
        launched["kwargs"] = kwargs
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: "Mon Jan  1 00:00:00 2024"),
    )
    status, created = mgr.start_with_ownership()
    assert status.running and status.pid == 4242 and status.port == 8188
    assert created is True
    assert mgr.pid_file.read_text().splitlines()[0] == "4242"
    # launched with the pinned port, loopback bind, and reused model paths
    assert "--port" in launched["args"] and "8188" in launched["args"]
    assert "--listen" in launched["args"] and "127.0.0.1" in launched["args"]
    assert "--extra-model-paths-config" in launched["args"]
    assert launched["kwargs"].get("start_new_session") is True


def test_start_is_idempotent_when_already_running(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("999")
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)  # pid 999 "alive"
    # pid 999 is OUR ComfyUI (not a stranger), so start() must no-op (#647).
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: False)

    def boom(*a, **k):
        raise AssertionError("Popen must not be called when already running")

    monkeypatch.setattr(mod.subprocess, "Popen", boom)
    status = mgr.start()
    assert status.running and status.pid == 999


def test_start_refuses_when_port_in_use(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    monkeypatch.setattr(mod.socket, "socket", lambda *a, **k: _FakeSocket(0))  # in use
    with pytest.raises(ComfyUiMpsError, match="already in use"):
        mgr.start()


def test_start_requires_install(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)  # no venv
    monkeypatch.setattr(mod.socket, "socket", lambda *a, **k: _FakeSocket(1))
    with pytest.raises(ComfyUiMpsError, match="venv is not installed"):
        mgr.start()


def test_stop_sigints_then_kills(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("777")
    signals = []
    # pid stays alive through SIGINT, so stop escalates to SIGKILL.
    alive = {"v": True}

    def fake_killpg(pid, sig):
        signals.append(sig)
        if sig == mod.signal.SIGKILL:
            alive["v"] = False
        # signal 0 = liveness probe; raise if dead
        if sig == 0 and not alive["v"]:
            raise ProcessLookupError

    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(mgr, "_managed_process_alive", lambda pid: alive["v"])
    monkeypatch.setattr(mod.os, "killpg", fake_killpg)
    monkeypatch.setattr(mod.os, "waitpid", lambda pid, flags: (0, 0))
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: False)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    # Advance the clock 5s per call so the 10s grace window elapses in ~2
    # iterations while the pid is still alive → stop escalates to SIGKILL.
    clock = {"v": 0.0}

    def fake_monotonic():
        clock["v"] += 5.0
        return clock["v"]

    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)
    assert mgr.stop() is True
    assert mod.signal.SIGINT in signals and mod.signal.SIGKILL in signals
    assert not mgr.pid_file.exists()


def test_stop_refuses_to_kill_a_recycled_stranger_pid(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("321")
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)  # pid 321 "alive"
    # ps proves pid 321 is some unrelated program → must not be signalled.
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: True)

    def boom(pid, sig):
        raise AssertionError("must never signal a stranger pid")

    # only the liveness probe (sig 0) is allowed; a real signal is a bug
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None if sig == 0 else boom(pid, sig))
    assert mgr.stop() is False
    assert mgr.pid_file.exists()  # preserve evidence for inspection/retry


def test_stop_reaps_exited_leader_during_grace_period(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("556")
    state = {"alive": True, "signals": []}

    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: True)
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


def test_stop_reports_failure_when_process_survives_sigkill(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("888")
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: False)
    monkeypatch.setattr(mgr, "_managed_process_alive", lambda pid: True)
    monkeypatch.setattr(mod.os, "killpg", lambda pid, sig: None)
    monkeypatch.setattr(mod.os, "waitpid", lambda pid, flags: (0, 0))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    clock = {"v": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock.__setitem__("v", clock["v"] + 5.0) or clock["v"])
    # Process never dies → stop must be honest and NOT clear the pidfile.
    assert mgr.stop() is False
    assert mgr.pid_file.exists()


def test_stop_waits_for_group_after_sigkill(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("889")
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
    monkeypatch.setattr(mgr, "_pid_alive", alive)
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


def test_stop_refuses_stale_leaderless_group_evidence(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("777")
    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(mgr, "_process_group_alive", lambda pgid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: True)
    monkeypatch.setattr(
        mgr,
        "_sweep_orphaned_group",
        lambda _pid: pytest.fail("stale PID evidence authorized a group signal"),
        raising=False,
    )


    assert mgr.stop() is False
    assert mgr.pid_file.exists()


def test_stop_sweeps_current_launch_group_after_leader_exits(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr._untracked_pid = 777
    alive = {"group": True}
    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(mgr, "_process_group_alive", lambda pgid: alive["group"])
    monkeypatch.setattr(
        mgr,
        "_pid_is_stranger",
        lambda _pid: pytest.fail("a dead leader has no probeable identity"),
    )
    swept = []

    def sweep(pgid):
        swept.append(pgid)
        alive["group"] = False
        return True

    monkeypatch.setattr(mgr, "_sweep_orphaned_group", sweep, raising=False)
    assert mgr.stop() is True
    assert swept == [777]
    assert mgr._untracked_pid is None


def test_stop_returns_false_without_process(tmp_path):
    assert _mgr(tmp_path).stop() is False


def test_status_reflects_liveness(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("555")
    mgr._write_status(installed_ref="v0.27.0", pid=555)

    # Alive AND ours: a live PID that is not a stranger reports running. (Drive
    # the ownership helpers directly so the test never depends on whether the
    # host actually has a process/process-group at PID 555 — the source of the
    # historical os.kill-vs-os.killpg flake, #647.)
    monkeypatch.setattr(ComfyUiMpsManager, "_managed_process_alive", lambda self, pid: True)
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: False)
    st = mgr.status()
    assert st.running and st.pid == 555 and st.installed_ref == "v0.27.0"

    # Dead: no live process at the PID.
    monkeypatch.setattr(ComfyUiMpsManager, "_managed_process_alive", lambda self, pid: False)
    st2 = mgr.status()
    assert not st2.running and st2.pid is None


def test_status_recycled_pid_reports_not_running(tmp_path, monkeypatch):
    """#647 AC#1: a live PID whose argv is NOT our ComfyUI (a recycled/foreign
    process) reports running=False even though kill-0 succeeds."""
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("4242")
    mgr._write_status(installed_ref="v0.27.0", pid=4242)
    # kill-0 says the PID is alive, but the process is a stranger.
    monkeypatch.setattr(ComfyUiMpsManager, "_managed_process_alive", lambda self, pid: True)
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: True)

    st = mgr.status()
    assert not st.running and st.pid is None


def test_permission_denied_pid_is_live_but_never_adopted(tmp_path, monkeypatch):
    """Permission denial proves existence, not ownership."""
    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(mod.os, "kill", denied)
    assert ComfyUiMpsManager._pid_alive(4242) is True

    monkeypatch.setattr(mod.os, "killpg", denied)
    assert ComfyUiMpsManager._process_group_alive(4242) is True

    # …and status() therefore reports not running for such a PID.
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("4242")
    before = mgr.pid_file.read_bytes()
    st = mgr.status()
    assert not st.running and st.pid is None
    assert mgr.stop() is False
    with pytest.raises(ComfyUiMpsError, match="ownership is mismatched or unknown"):
        mgr.start_with_ownership()
    assert mgr.pid_file.read_bytes() == before


def test_start_refuses_stale_stranger_pidfile_without_launching(tmp_path, monkeypatch):
    """A live untrusted PID is never replaced or silently orphaned."""
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("4242")
    # The PID is alive but a stranger (recycled), so status() is not running.
    monkeypatch.setattr(ComfyUiMpsManager, "_managed_process_alive", lambda self, pid: True)
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: True)
    before = mgr.pid_file.read_bytes()
    monkeypatch.setattr(
        mod.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("must not launch a replacement"),
    )

    with pytest.raises(ComfyUiMpsError, match="ownership is mismatched or unknown"):
        mgr.start()
    assert mgr.pid_file.read_bytes() == before


# ─────────────────────────── health ───────────────────────────
def test_health_reports_mps_device(tmp_path, monkeypatch):
    body = '{"devices": [{"name": "mps", "type": "mps", "index": 0}]}'
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(body))
    h = _mgr(tmp_path).health()
    assert h["reachable"] and h["device"] == "mps"


def test_health_reports_cpu_fallback(tmp_path, monkeypatch):
    body = '{"devices": [{"name": "cpu", "type": "cpu", "index": 0}]}'
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(body))
    h = _mgr(tmp_path).health()
    assert h["reachable"] and h["device"] == "cpu"


def test_health_unreachable(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    h = _mgr(tmp_path).health()
    assert h["reachable"] is False and h["device"] == "unknown"


def test_wait_healthy_polls_until_reachable(tmp_path, monkeypatch):
    seq = iter([{"reachable": False}, {"reachable": False}, {"reachable": True, "device": "mps"}])
    monkeypatch.setattr(ComfyUiMpsManager, "health", lambda self, **k: next(seq))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    h = _mgr(tmp_path).wait_healthy(timeout=30, interval=0)
    assert h["reachable"] and h["device"] == "mps"


# ─────────────────────────── ensure_running / remove ───────────────────────────
def test_ensure_running_refuses_unsupported_host(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(ComfyUiMpsError, match="unsupported host"):
        _mgr(tmp_path).ensure_running()


def test_ensure_running_installs_then_starts(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    order = []
    monkeypatch.setattr(ComfyUiMpsManager, "preflight",
                        lambda self, **k: mod.PreflightResult(status="ok"))
    monkeypatch.setattr(
        ComfyUiMpsManager,
        "_install_locked",
        lambda self, **k: order.append("install"),
    )
    monkeypatch.setattr(
        ComfyUiMpsManager,
        "_start_locked",
        lambda self: (
            order.append("start") or mod.ProcessStatus(True, 1, mgr.port),
            True,
        ),
    )
    mgr.ensure_running()
    assert order == ["install", "start"]


def test_start_with_ownership_distinguishes_existing_process(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(
        mgr, "status", lambda: mod.ProcessStatus(True, 4242, mgr.port)
    )

    status, created = mgr.start_with_ownership()

    assert status.pid == 4242
    assert created is False


def test_concurrent_starts_assign_ownership_to_one_launcher(tmp_path, monkeypatch):
    first = _mgr(tmp_path)
    second = _mgr(tmp_path)
    _install_stub(first)
    monkeypatch.setattr(
        ComfyUiMpsManager,
        "_pid_alive",
        staticmethod(lambda _pid: True),
    )
    # The winning launcher's process is ours, so the loser's status() sees it as
    # running (not a stranger) and no-ops (#647).
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: False)
    monkeypatch.setattr(ComfyUiMpsManager, "_port_in_use", lambda self: False)
    launches: list[int] = []

    def fake_popen(*_args, **_kwargs):
        # Ignore the `ps` identity probe that stamps the pid file.
        if _args and _args[0] and _args[0][0] == "ps":
            return SimpleNamespace(pid=0, returncode=0, stdout="", stderr="")
        launches.append(4242)
        time.sleep(0.05)
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: "Mon Jan  1 00:00:00 2024"),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda manager: manager.start_with_ownership(), [first, second]
            )
        )

    assert sorted(created for _status, created in results) == [False, True]
    assert launches == [4242]


def test_launch_metadata_failure_terminates_new_process(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    monkeypatch.setattr(mgr, "_port_in_use", lambda: False)
    monkeypatch.setattr(
        mod.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=5150)
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

    with pytest.raises(ComfyUiMpsError, match="child was terminated"):
        mgr.start_with_ownership()

    assert terminated == [5150]
    assert not mgr.pid_file.exists()


def test_missing_launch_identity_terminates_new_process(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    monkeypatch.setattr(mgr, "_port_in_use", lambda: False)
    monkeypatch.setattr(
        mod.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=5151)
    )
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: None),
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    terminated: list[int] = []
    monkeypatch.setattr(
        mgr, "_terminate_pid", lambda pid: terminated.append(pid) or True
    )

    with pytest.raises(ComfyUiMpsError, match="child was terminated"):
        mgr.start_with_ownership()

    assert terminated == [5151]
    assert not mgr.pid_file.exists()


def test_failed_launch_compensation_retains_untracked_pid(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    monkeypatch.setattr(mgr, "_port_in_use", lambda: False)
    monkeypatch.setattr(
        mod.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=5150)
    )
    monkeypatch.setattr(
        mgr,
        "_write_status",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("metadata failed")),
    )
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: "Mon Jan  1 00:00:00 2024"),
    )
    monkeypatch.setattr(mgr, "_terminate_pid", lambda _pid: False)

    with pytest.raises(ComfyUiMpsError, match="could not be terminated") as raised:
        mgr.start_with_ownership()

    assert raised.value.surviving_process is True
    assert mgr._untracked_pid == 5150
    assert mgr.pid_file.read_text(encoding="utf-8").splitlines() == [
        "5150", "start_utc=Mon Jan  1 00:00:00 2024",
    ]


def test_immediate_launch_exit_is_not_reported_running(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    monkeypatch.setattr(mgr, "_port_in_use", lambda: False)
    process = SimpleNamespace(pid=5154, poll=lambda: 7)
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: "Mon Jan  1 00:00:00 2024"),
    )
    terminated = []
    monkeypatch.setattr(mgr, "_terminate_pid", lambda pid: terminated.append(pid) or True)

    with pytest.raises(ComfyUiMpsError, match="exited during startup"):
        mgr.start_with_ownership()

    assert terminated == [5154]
    assert mgr._untracked_pid is None


def test_dead_launch_leader_keeps_group_sweep_authority(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr._untracked_pid = 5155
    mgr.pid_file.parent.mkdir(parents=True)
    mgr.pid_file.write_text("5155\nstart_utc=stamp\n", encoding="utf-8")
    monkeypatch.setattr(mgr, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(mgr, "_process_group_alive", lambda _pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda _pid: True)

    assert mgr.confirm_started_process(5155) is False
    assert mgr._untracked_pid == 5155


def test_verified_launch_retains_group_authority_until_commit(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr._untracked_pid = 5156
    monkeypatch.setattr(mgr, "_read_pid", lambda: 5156)
    monkeypatch.setattr(mgr, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda _pid: False)

    assert mgr.confirm_started_process(5156) is True
    assert mgr._untracked_pid == 5156
    mgr.commit_started_process()
    assert mgr._untracked_pid is None


def test_launch_identity_interrupt_compensates_and_propagates(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    monkeypatch.setattr(mgr, "_port_in_use", lambda: False)
    monkeypatch.setattr(
        mod.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=5152)
    )
    monkeypatch.setattr(
        "services.managed_host.require_process_start_time",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    terminated: list[int] = []
    monkeypatch.setattr(
        mgr, "_terminate_pid", lambda pid: terminated.append(pid) or True
    )

    with pytest.raises(KeyboardInterrupt):
        mgr.start_with_ownership()

    assert terminated == [5152]
    assert not mgr.pid_file.exists()


def test_launch_lock_timeout_is_bounded(tmp_path, monkeypatch):
    if mod.fcntl is None:
        pytest.skip("fcntl lock timeout is POSIX-only")
    mgr = _mgr(tmp_path)
    ticks = iter([0.0, 31.0])
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        mod.fcntl,
        "flock",
        lambda *_args: (_ for _ in ()).throw(BlockingIOError()),
    )

    with pytest.raises(ComfyUiMpsError, match="timed out waiting"):
        mgr.start_with_ownership()


def test_launch_guard_has_portable_no_fcntl_path(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(mod, "fcntl", None)
    monkeypatch.setattr(
        mgr, "status", lambda: mod.ProcessStatus(True, 4242, mgr.port)
    )

    _status, created = mgr.start_with_ownership()

    assert created is False


def test_remove_stops_and_deletes_state(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    (mgr.state_dir / "junk").write_text("x")
    monkeypatch.setattr(ComfyUiMpsManager, "_stop_locked", lambda self: False)
    mgr.remove()
    assert not mgr.state_dir.exists()


def test_stop_on_absent_state_does_not_create_service_directory(tmp_path):
    mgr = _mgr(tmp_path)

    assert mgr.stop() is False
    assert not mgr.state_dir.exists()


def test_stop_waiting_for_remove_does_not_recreate_deleted_state(
    tmp_path, monkeypatch
):
    remover = _mgr(tmp_path)
    stopper = _mgr(tmp_path)
    remover.state_dir.mkdir(parents=True)
    (remover.state_dir / "junk").write_text("x")
    monkeypatch.setattr(ComfyUiMpsManager, "_stop_locked", lambda self: False)
    removed = threading.Event()
    release_remove = threading.Event()
    real_rmtree = mod.shutil.rmtree

    def delayed_rmtree(path):
        real_rmtree(path)
        removed.set()
        assert release_remove.wait(timeout=2)

    monkeypatch.setattr(mod.shutil, "rmtree", delayed_rmtree)
    with ThreadPoolExecutor(max_workers=2) as pool:
        removal = pool.submit(remover.remove)
        assert removed.wait(timeout=2)
        stopping = pool.submit(stopper.stop)
        time.sleep(0.05)
        release_remove.set()
        removal.result(timeout=2)
        stopping.result(timeout=2)

    assert not remover.state_dir.exists()


def test_remove_preserves_state_when_process_survives(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    (mgr.state_dir / "junk").write_text("x")
    mgr.pid_file.write_text("5150\n", encoding="utf-8")
    monkeypatch.setattr(ComfyUiMpsManager, "_stop_locked", lambda self: False)
    monkeypatch.setattr(mgr, "_pid_alive", lambda _pid: True)

    with pytest.raises(ComfyUiMpsError, match="refusing to remove"):
        mgr.remove()

    assert (mgr.state_dir / "junk").exists()


# ─────────────────────────── env factory ───────────────────────────
def test_manager_from_env_reads_all_knobs(tmp_path):
    mgr = manager_from_env({
        "COMFYUI_MPS_STATE_DIR": str(tmp_path / "s"),
        "COMFYUI_MPS_LOCALHOST_PORT": "9000",
        "COMFYUI_MPS_REF": "v0.28.0",
        "COMFYUI_MPS_MODELS_PATH": str(tmp_path / "m"),
        "COMFYUI_MPS_MIN_MEMORY_GB": "24",
    })
    assert mgr.port == 9000 and mgr.ref == "v0.28.0" and mgr.min_memory_gb == 24
    assert mgr.state_dir == (tmp_path / "s")


def test_manager_from_env_uses_defaults_for_blank_port(tmp_path):
    mgr = manager_from_env({"COMFYUI_MPS_STATE_DIR": str(tmp_path), "COMFYUI_MPS_LOCALHOST_PORT": ""})
    assert mgr.port == 8188


# ─────────────────────────── three-surface docs ───────────────────────────
_ROOT = Path(__file__).resolve().parents[2]


def test_managed_mps_documented_on_all_three_surfaces():
    """The managed-MPS source + lifecycle must appear on repo README, MkDocs
    site, and GitHub wiki (mirrors the Krea three-surface contract)."""
    repo = (_ROOT / "services/comfyui/README.md").read_text(encoding="utf-8")
    site = surface_text("services/comfyui/README.md", "site")
    wiki = surface_text("services/comfyui/README.md", "wiki")
    for surface in (repo, site, wiki):
        assert "managed-localhost-mps" in surface
        assert "Managed Apple-Silicon" in surface
        assert "COMFYUI_MPS_REF" in surface
        # cold/warm health, unsupported-host, and removal are all required.
        assert "cold" in surface.lower() and "unsupported" in surface.lower()
        assert "BF16" in surface  # fp8-crashes-on-MPS precision constraint
    # Env knobs surface on the wiki + site reference tables too (manifest-derived).
    assert "COMFYUI_MPS_STATE_DIR" in surface_text("docs/reference/env-vars.md", "wiki")


# ─────────────────────────── optional live smoke ───────────────────────────
@pytest.mark.live
@pytest.mark.skipif(
    platform.system() != "Darwin" or platform.machine().lower() not in ("arm64", "aarch64"),
    reason="managed-localhost-mps runs only on Apple Silicon",
)
def test_live_managed_host_reports_mps():
    """OPTIONAL Darwin-arm64 smoke: prove /system_stats reports MPS.

    Never downloads weights itself. Runs preflight (must pass on this host), and
    if a managed process is already up, asserts the compute device is MPS. If it
    isn't running, skips with an operator hint — bring it up first with
    ``./start.sh comfyui-mps install && ./start.sh comfyui-mps start``.
    """
    mgr = manager_from_env({
        "COMFYUI_MPS_STATE_DIR": "~/.atlas/comfyui-mps",
        "COMFYUI_MPS_LOCALHOST_PORT": "8188",
    })
    pre = mgr.preflight()
    assert next(c for c in pre.checks if c["name"] == "os")["status"] == "ok"
    assert next(c for c in pre.checks if c["name"] == "arch")["status"] == "ok"
    if not mgr.status().running:
        pytest.skip("managed MPS host not running; run `comfyui-mps install && start` first")
    health = mgr.health()
    assert health["reachable"], health
    assert health["device"] == "mps", f"expected MPS, got {health['device']}"


# ── #648: pinned Torch, YAML-safe model paths, models-dir preflight ─────────
def test_torch_pin_default_and_env_override(monkeypatch):
    from services.comfyui_mps_manager import (
        ComfyUiMpsManager as _Mgr,
        _DEFAULT_TORCH_PIN,
        manager_from_env,
    )

    assert _Mgr("/tmp/x").torch_pin == _DEFAULT_TORCH_PIN.split()
    # An explicit env pin (bumped alongside COMFYUI_MPS_REF) wins.
    m = manager_from_env({"COMFYUI_MPS_TORCH_PIN": "torch==9.9.9 torchvision==9.9.9"})
    assert m.torch_pin == ["torch==9.9.9", "torchvision==9.9.9"]
    # A blank override falls back to the reproducible default (never unpinned).
    assert manager_from_env({"COMFYUI_MPS_TORCH_PIN": ""}).torch_pin == _DEFAULT_TORCH_PIN.split()


def test_write_model_paths_quotes_yaml_special_chars(tmp_path):
    # A models path with YAML-special characters must not corrupt the file.
    tricky = tmp_path / "models: with #special"
    mgr = _mgr(tmp_path, models_path=tricky)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)

    mgr._write_model_paths()
    text = mgr.model_paths_file.read_text(encoding="utf-8")
    assert text.startswith("# AUTO-GENERATED by Atlas")

    import yaml as _yaml

    parsed = _yaml.safe_load(text)
    assert parsed["atlas_host"]["base_path"] == str(tricky)
    assert parsed["atlas_host"]["checkpoints"] == f"{tricky}/checkpoints"
    assert parsed["atlas_host"]["vae"] == f"{tricky}/vae"


def test_preflight_warns_on_missing_models_dir(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch, memsize_gb=64)
    mgr = _mgr(tmp_path, models_path=tmp_path / "does-not-exist")
    result = mgr.preflight()
    md = next(c for c in result.checks if c["name"] == "models_dir")
    assert md["status"] == "warn"
    assert "does not exist" in md["detail"]


def test_preflight_warns_on_empty_models_dir(tmp_path, monkeypatch):
    _darwin_arm64(monkeypatch, memsize_gb=64)
    models = tmp_path / "empty-models"
    models.mkdir()
    mgr = _mgr(tmp_path, models_path=models)
    result = mgr.preflight()
    md = next(c for c in result.checks if c["name"] == "models_dir")
    assert md["status"] == "warn"
    assert "none of the expected" in md["detail"]


# ── #651: COMFYUI_MPS_LISTEN + loopback-reachable probes ────────────────────
def test_manager_from_env_threads_listen():
    assert manager_from_env({"COMFYUI_MPS_LISTEN": "0.0.0.0"}).listen == "0.0.0.0"
    # Default (and a blank override) preserve today's loopback behavior.
    assert manager_from_env({}).listen == "127.0.0.1"
    assert manager_from_env({"COMFYUI_MPS_LISTEN": ""}).listen == "127.0.0.1"


def test_probe_host_falls_back_to_loopback_for_wildcard(tmp_path):
    assert _mgr(tmp_path, listen="0.0.0.0")._probe_host == "127.0.0.1"
    assert _mgr(tmp_path, listen="::")._probe_host == "127.0.0.1"
    assert _mgr(tmp_path, listen="")._probe_host == "127.0.0.1"
    # A concrete bind address is probed as-is.
    assert _mgr(tmp_path, listen="192.168.1.5")._probe_host == "192.168.1.5"
    assert _mgr(tmp_path)._probe_host == "127.0.0.1"  # default


def test_port_probe_uses_loopback_when_listening_on_all_interfaces(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, listen="0.0.0.0")
    seen = {}

    class _RecSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, _t):
            pass

        def connect_ex(self, addr):
            seen["addr"] = addr
            return 1  # not in use

    monkeypatch.setattr(mod.socket, "socket", lambda *a, **k: _RecSock())
    assert mgr._port_in_use() is False
    assert seen["addr"] == ("127.0.0.1", mgr.port)  # not 0.0.0.0


def test_health_uses_loopback_probe_host(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, listen="0.0.0.0")
    seen = {}

    def fake_urlopen(url, *a, **k):
        seen["url"] = url
        raise OSError("cold")  # unreachable is fine; only the URL matters

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    mgr.health()
    assert seen["url"] == f"http://127.0.0.1:{mgr.port}/system_stats"


def test_listen_flows_to_comfyui_launch_argv(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, listen="0.0.0.0")
    _install_stub(mgr)
    monkeypatch.setattr(ComfyUiMpsManager, "_port_in_use", lambda self: False)
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: False)
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError))
    captured = {}

    def fake_popen(args, *a, **k):
        # Ignore the `ps` identity probe that stamps the pid file.
        if args and args[0] == "ps":
            return SimpleNamespace(pid=0, returncode=0, stdout="", stderr="")
        captured["args"] = list(args)
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: "Mon Jan  1 00:00:00 2024"),
    )
    mgr.start()

    args = captured["args"]
    assert "--listen" in args
    assert args[args.index("--listen") + 1] == "0.0.0.0"  # binds all interfaces


# ── pass 21: (pid, start time) identity, shared with managed_host ────


def test_pid_ownership_uses_start_time_not_argv(tmp_path, monkeypatch):
    """`markers = ("main.py", "ComfyUI", repo_dir, state_dir)` is not an identity.

    The first two are generic substrings. After a crash left the pid file
    behind and the OS recycled the pid onto ANY process whose argv contains
    them — a user's own ComfyUI install, any `python main.py` app — this
    returned False and `_terminate_pid` escalated to `os.killpg(pid, SIGKILL)`
    on the stranger's whole process group. Verified before the fix: an
    unrelated app and its worker child were both killed.

    `(pid, start time)` is unique on POSIX, which is what
    `managed_host.pid_is_stranger` compares. An argv is rewritten by a wrapper
    script, `exec`, `setproctitle` or a gunicorn/celery master.
    """
    from services.managed_host import write_pid_file_with_identity

    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)

    # A recycled pid: the file's stamp does not match the live process.
    write_pid_file_with_identity(mgr.pid_file, 4242, "Mon Jan  1 00:00:00 2024")
    monkeypatch.setattr(
        "services.legacy_process_start_identity",
        lambda pid: "Tue Feb  2 02:02:02 2027",
    )
    assert mgr._pid_is_stranger(4242) is True, "a recycled pid would be killed"

    # ...and the same pid when the stamp matches is ours.
    monkeypatch.setattr(
        "services.legacy_process_start_identity",
        lambda pid: "Mon Jan  1 00:00:00 2024",
    )
    assert mgr._pid_is_stranger(4242) is False


def test_a_legacy_unstamped_pid_file_refuses_unknown_ownership(tmp_path, monkeypatch):
    """An unknowable probe must never authorize a signal."""
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda pid: "Tue Feb  2 02:02:02 2027"),
    )
    assert mgr._pid_is_stranger(4242) is True


def test_unstamped_comfyui_marker_still_refuses_ownership(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("4242\n", encoding="utf-8")
    result = type("Result", (), {"returncode": 0, "stdout": "python main.py ComfyUI"})()
    monkeypatch.setattr(mod.subprocess, "run", lambda *_args, **_kwargs: result)
    assert mgr._pid_is_stranger(4242) is True


def test_start_refuses_live_unknown_pid_and_preserves_tracking(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("4242\n", encoding="utf-8")
    before = mgr.pid_file.read_bytes()
    monkeypatch.setattr(mgr, "_managed_process_alive", lambda _pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda _pid: True)

    with pytest.raises(ComfyUiMpsError, match="ownership is mismatched or unknown"):
        mgr.start_with_ownership()

    assert mgr.pid_file.read_bytes() == before


def test_the_pid_file_round_trips_through_its_own_reader(tmp_path):
    """The writer and the reader must agree — they did not.

    `write_pid_file_with_identity` emits `pid\nstart_utc=...`, but `_read_pid`
    parsed the WHOLE file with `int()`, so it raised ValueError and returned
    None on every launch where `ps` answers — i.e. every macOS host, the only
    platform MPS supports. The whole lifecycle inverted: `status` reported
    not-running while it ran, `stop` deleted the pid file and returned False
    with the process alive, `remove` rmtree'd the install out from under it,
    the next `start` failed with "port already in use", and rollback printed
    success.

    This replaces an `inspect.getsource` string grep, which could not see the
    defect because it never exercised the pair. Every existing test stubs
    `ps`, so the two-line file never appeared in any of them — and the
    pre-existing assertion had been WEAKENED to `.splitlines()[0]`, encoding
    the new format in the test while production still parsed the whole file.
    """
    from services.managed_host import write_pid_file_with_identity

    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)

    write_pid_file_with_identity(mgr.pid_file, 55163, "Fri Aug 21 22:53:12 2026")
    assert mgr.pid_file.read_text(encoding="utf-8").count("\n") == 2
    assert mgr._read_pid() == 55163, "the writer's own output is unreadable"

    # a single-line file from an earlier version still parses
    mgr.pid_file.write_text("4242\n", encoding="utf-8")
    assert mgr._read_pid() == 4242

    # and a non-positive pid is refused — `_terminate_pid` escalates to
    # os.killpg, where 0 is the CALLER's group and -1 is a broadcast
    for bad in ("0\n", "-1\n", "garbage\n", ""):
        mgr.pid_file.write_text(bad, encoding="utf-8")
        assert mgr._read_pid() is None, bad


def test_a_real_spawn_is_reported_running(tmp_path, monkeypatch):
    """A REAL child, a REAL `ps` probe, and NO stdlib patching.

    The previous version patched `mod.subprocess.Popen` — the stdlib module
    object — which intercepted `managed_host._process_start_time`'s own
    `subprocess.run(["ps", ...])`. That returned None, the pid file was written
    SINGLE-line, and the two-line format the test claimed to exercise never
    existed: re-installing the pre-fix reader left it passing.

    So this patches NOTHING in subprocess. It spawns a real process, runs the
    real identity probe against it, writes the real two-line pid file, and
    asserts the reader/ownership/status trio agrees — which is the pairing the
    regression broke. Mutation-checked: it fails with the pre-fix reader.
    """
    child = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)

    mgr = _mgr(tmp_path)
    monkeypatch.setattr(ComfyUiMpsManager, "_port_in_use", lambda self: False)
    try:
        from services.managed_host import ManagedHostManager, write_pid_file_with_identity

        stamp = ManagedHostManager._process_start_time(child.pid)
        assert stamp, "the real ps probe returned nothing — cannot exercise the pair"
        mgr.state_dir.mkdir(parents=True, exist_ok=True)
        write_pid_file_with_identity(mgr.pid_file, child.pid, stamp)

        assert mgr._read_pid() == child.pid, "the real two-line pid file is unreadable"
        assert mgr._pid_is_stranger(child.pid) is False, "our own process read as a stranger"
        assert mgr.status().running is True, "a live process reported not-running"
    finally:
        child.kill()
        child.wait()


# ── #990: a stamp-less pid file from before the managed-host framework ──────
#
# A pin older than #795 wrote the pid file as a bare pid, with no start_utc
# identity stamp. The shared guard in services.refuse_untrusted_tracked_pid
# still refuses to signal or replace the live process it names (it cannot be
# proven Atlas's), but that one refusal no longer aborts the bring-up. The
# guard is shared by all three managed hosts, so these cover all three.


class _BringUpManager:
    """Minimal manager for driving AtlasStarter.start_managed_host_processes."""

    def __init__(self, failure: Exception):
        self.failure = failure
        self.stop_calls = 0

    def ensure_running_with_ownership(self):
        raise self.failure

    def ensure_running(self):
        raise self.failure

    def stop(self) -> bool:
        self.stop_calls += 1
        return False


def _bring_up(monkeypatch, env: dict, manager) -> tuple[object, list]:
    import start as start_module
    from services import blender_mcp_manager, vllm_metal_manager

    for module in (mod, vllm_metal_manager, blender_mcp_manager):
        monkeypatch.setattr(module, "manager_from_env", lambda _env: manager)
    monkeypatch.setattr(start_module, "_resolved_comfyui_model_rows", lambda _env: [])
    monkeypatch.setattr(start_module, "_resolved_comfyui_custom_nodes", lambda _env: [])
    starter = start_module.AtlasStarter()
    monkeypatch.setattr(starter.config_parser, "parse_env_file", lambda: env)
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        starter.banner, "show_status_message",
        lambda message, level: messages.append((message, level)),
    )
    return starter, messages


def _refusal_for(pid_file: Path, pid: int, error_type, body: str | None = None):
    import services

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{pid}\n" if body is None else body, encoding="utf-8")
    with pytest.raises(error_type) as raised:
        services.refuse_untrusted_tracked_pid(
            (pid, pid_file), lambda _pid: True, lambda _pid: True,
            ("ComfyUI MPS", error_type),
        )
    return raised.value


def test_stampless_legacy_record_does_not_abort_the_bring_up(tmp_path, monkeypatch):
    """#990 AC1/AC2 through the real ComfyUI manager: the unproven process is
    left exactly as found -- not signalled, not replaced, its record intact,
    nothing added to rollback ownership -- and the bring-up continues."""
    live = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        mgr = _mgr(tmp_path)
        mgr.state_dir.mkdir(parents=True)
        mgr.pid_file.write_text(f"{live.pid}\n", encoding="utf-8")
        before = mgr.pid_file.read_bytes()
        monkeypatch.setattr(mgr, "preflight", lambda: SimpleNamespace(ok=True, checks=[]))
        monkeypatch.setattr(mgr, "_install_locked", lambda: None)
        monkeypatch.setattr(
            mod.subprocess, "Popen",
            lambda *_a, **_k: pytest.fail("a replacement must not be launched"),
        )
        starter, messages = _bring_up(
            monkeypatch,
            {"COMFYUI_SOURCE": "managed-localhost-mps", "VLLM_METAL_SOURCE": "disabled"},
            mgr,
        )

        assert starter.start_managed_host_processes() is True
        assert live.poll() is None, "the unproven process was signalled"
        assert mgr.pid_file.read_bytes() == before
        assert starter._managed_hosts_started_this_run == []
        assert not [m for m, level in messages if level == "error"]
        warnings = [m for m, level in messages if level == "warning"]
        assert any(str(mgr.pid_file) in m and "start_utc" in m for m in warnings)
        # The command ends the line, so copying it cannot drag trailing prose
        # or punctuation into the path.
        assert any(m.endswith(f"rm -f {mgr.pid_file}") for m in warnings), warnings
    finally:
        live.kill()
        live.wait()


@pytest.mark.parametrize("kind", ["comfyui", "vllm", "blender"])
def test_every_managed_host_survives_its_own_legacy_record(tmp_path, monkeypatch, kind):
    from services import blender_mcp_manager, vllm_metal_manager

    error_type = {
        "comfyui": ComfyUiMpsError,
        "vllm": vllm_metal_manager.VllmMetalError,
        "blender": blender_mcp_manager.BlenderMcpError,
    }[kind]
    manager = _BringUpManager(_refusal_for(tmp_path / "host.pid", 4242, error_type))
    starter, _messages = _bring_up(monkeypatch, {
        "COMFYUI_SOURCE": "managed-localhost-mps" if kind == "comfyui" else "disabled",
        "VLLM_METAL_SOURCE": "managed-localhost" if kind == "vllm" else "disabled",
        "VLLM_METAL_MODEL": "example/model",
        "BLENDER_MCP_SOURCE": "managed-localhost" if kind == "blender" else "disabled",
    }, manager)

    assert starter.start_managed_host_processes() is True
    assert manager.stop_calls == 0
    assert starter._managed_hosts_started_this_run == []


def test_a_stamped_record_that_fails_to_match_is_still_fatal(tmp_path, monkeypatch):
    """#990 AC4 at the bring-up level: only the stamp-less legacy record is
    tolerated. A stamped record whose identity does not match (a recycled
    pid) is a stranger and still stops the bring-up."""
    import services

    failure = _refusal_for(
        tmp_path / "host.pid", 4242, ComfyUiMpsError,
        body="4242\nstart_utc=linux-proc-start-v1:1\n",
    )
    assert services.legacy_pid_refusal_file(failure) is None
    starter, _messages = _bring_up(
        monkeypatch,
        {"COMFYUI_SOURCE": "managed-localhost-mps", "VLLM_METAL_SOURCE": "disabled"},
        _BringUpManager(failure),
    )

    assert starter.start_managed_host_processes() is False


def test_a_stampless_legacy_record_explains_itself_and_its_remedy(tmp_path):
    """#990 AC3: name the file, say the stamp is missing, give the exact
    commands -- and keep the wording the ownership tests match on."""
    import shlex

    import services

    pid_file = tmp_path / "state dir" / "comfyui-mps.pid"
    failure = _refusal_for(pid_file, 88428, ComfyUiMpsError)

    message = str(failure)
    assert "ownership is mismatched or unknown" in message
    assert str(pid_file) in message
    assert "no start_utc identity stamp" in message
    assert "kill -TERM 88428" in message
    assert message.endswith(f"rm -f {shlex.quote(str(pid_file))}")
    assert services.legacy_pid_refusal_file(failure) == pid_file


@pytest.mark.parametrize(
    "body",
    ["88428\nstart_utc=linux-proc-start-v1:1\n", "4242\n", ""],
    ids=["stamped", "names-another-pid", "empty"],
)
def test_only_a_stampless_record_naming_the_pid_is_legacy(tmp_path, body):
    import services

    failure = _refusal_for(tmp_path / "host.pid", 88428, ComfyUiMpsError, body=body)
    assert services.legacy_pid_refusal_file(failure) is None
    assert "inspect the pid file and process manually" in str(failure)


def test_extra_model_paths_cover_every_provisioned_category_dir():
    # provision_models writes into each category's target dir; a dir missing
    # from extra_model_paths.yaml is downloaded but never listed by ComfyUI.
    from services.comfyui_mps_manager import _MODEL_SUBDIRS
    from utils.comfyui_library import CATEGORY_TARGET_DIR

    assert set(CATEGORY_TARGET_DIR.values()) <= set(_MODEL_SUBDIRS)


def test_start_clears_a_recycled_pid_record_instead_of_refusing(tmp_path):
    """#1341: the tableau stack's pid file named a pid now held by Safari's
    SearchHelper, started after the recorded ComfyUI. That record is stale:
    start clears it (failing later only because no venv is installed here)
    and never touches the process holding the pid."""
    import subprocess as sp
    import sys as _sys

    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    stranger = sp.Popen([_sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        mgr.pid_file.write_text(
            f"{stranger.pid}\nstart_utc=Thu Jan  1 00:00:00 2020\n", encoding="utf-8"
        )
        with pytest.raises(ComfyUiMpsError, match="venv is not installed"):
            mgr.start()
        assert not mgr.pid_file.exists()
        assert stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait()

# --- shared recycled-pid handling across managed hosts (#1341) --------------
# Generic-framework cases live here, beside the ComfyUI case that motivated
# them, so the framework test module stays under the module-size signal.
import os  # noqa: E402
import sys  # noqa: E402

from services.managed_host import ManagedHostError  # noqa: E402
from tests.test_managed_host_framework import _manager, running_service  # noqa: E402,F401



def _stranger():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _spy_signals_to(monkeypatch, watched: int) -> list[int]:
    """Record real signals (not liveness probes) sent to ``watched``; pass the rest on."""
    signalled: list[int] = []
    real_kill, real_killpg = os.kill, os.killpg

    def kill(pid, sig):
        if pid == watched and sig:
            signalled.append(pid)
            return None
        return real_kill(pid, sig)

    def killpg(pid, sig):
        if pid == watched:
            signalled.append(pid)
            return None
        return real_killpg(pid, sig)

    monkeypatch.setattr("services.managed_host.os.kill", kill)
    monkeypatch.setattr("services.managed_host.os.killpg", killpg)
    return signalled


def test_start_replaces_a_recycled_pid_record_without_signalling_its_holder(
    running_service, monkeypatch
):
    """The pid file names a live process that started after the recorded one:
    the OS recycled the pid. Start must clear the record and launch, and must
    never signal the process now holding that pid."""
    manager = running_service
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    stranger = _stranger()
    signalled = _spy_signals_to(monkeypatch, stranger.pid)
    try:
        manager.pid_file.write_text(
            f"{stranger.pid}\nstart_utc=Thu Jan  1 00:00:00 2020\n", encoding="utf-8"
        )
        warnings = [c for c in manager.preflight().checks if c["name"] == "pid-file"]
        assert warnings and warnings[0]["status"] == "warn"
        assert str(stranger.pid) in warnings[0]["detail"]

        status = manager.start(wait_timeout=30.0)

        assert status.running is True
        assert status.pid != stranger.pid
        assert manager._read_pid() == status.pid
        assert stranger.poll() is None, "start() touched the process holding the recycled pid"
        assert signalled == []
    finally:
        stranger.kill()
        stranger.wait()


@pytest.mark.parametrize(
    "case",
    [
        ("linux-proc-start-v1:500", "linux-proc-start-v1:900", None, True),
        # Boot-relative ticks restart at boot, so a smaller value is still a
        # different process.
        ("linux-proc-start-v1:900000", "linux-proc-start-v1:500", None, True),
        ("linux-proc-start-v1:500", "linux-proc-start-v1:500", None, False),
        ("linux-proc-start-v1:500", None, None, False),
        ("Wed Oct  1 10:00:00 2026", None, "Fri Oct  3 08:00:00 2026", True),
        ("Fri Oct  3 08:00:00 2026", None, "Wed Oct  1 10:00:00 2026", False),
        ("Wed Oct  1 10:00:00 2026", None, "Wed Oct  1 10:00:00 2026", False),
        ("Wed Oct  1 10:00:00 2026", None, None, False),
        ("not a time", None, "Fri Oct  3 08:00:00 2026", False),
        (None, "linux-proc-start-v1:900", "Fri Oct  3 08:00:00 2026", False),
    ],
)
def test_only_a_proven_identity_mismatch_counts_as_recycled(tmp_path, monkeypatch, case):
    """``case`` is (recorded stamp, live v1 identity, live ps identity, expected)."""
    import services

    recorded, live_v1, live_ps, expected = case
    monkeypatch.setattr(services.sys, "platform", "darwin")
    pid_file = tmp_path / "svc.pid"
    pid_file.write_text("4242\n" + (f"start_utc={recorded}\n" if recorded else ""), encoding="utf-8")
    monkeypatch.setattr(services, "process_start_identity", lambda _pid: live_v1)
    monkeypatch.setattr(services, "legacy_process_start_identity", lambda _pid: live_ps)

    assert services.tracked_pid_was_recycled(4242, pid_file) is expected


def test_refusal_stands_for_unknown_ownership_but_not_for_a_recycled_pid(tmp_path, monkeypatch):
    import services

    pid_file = tmp_path / "svc.pid"
    pid_file.write_text("4242\nstart_utc=Wed Oct  1 10:00:00 2026\n", encoding="utf-8")
    args = ((4242, pid_file), lambda _pid: True, lambda _pid: True, ("svc", ManagedHostError))

    monkeypatch.setattr(services, "legacy_process_start_identity", lambda _pid: "Fri Oct  3 08:00:00 2026")
    services.refuse_untrusted_tracked_pid(*args)  # recycled: no refusal

    monkeypatch.setattr(services, "legacy_process_start_identity", lambda _pid: None)
    with pytest.raises(ManagedHostError, match="ownership is mismatched or unknown"):
        services.refuse_untrusted_tracked_pid(*args)


def test_preflight_stays_quiet_for_our_own_live_process(tmp_path):
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    ours = _stranger()
    try:
        manager._write_pid_file(ours.pid)
        assert not [c for c in manager.preflight().checks if c["name"] == "pid-file"]
    finally:
        ours.kill()
        ours.wait()


@pytest.mark.parametrize(("booted_after", "expected"), [(True, True), (False, False)])
def test_linux_lstart_records_need_a_reboot_since_the_record(
    tmp_path, monkeypatch, booted_after, expected
):
    """On Linux ``ps lstart`` moves with the realtime clock, so a later live
    time alone could be Atlas's own process after a clock step."""
    import services

    monkeypatch.setattr(services.sys, "platform", "linux")
    monkeypatch.setattr(services, "legacy_process_start_identity", lambda _pid: "Fri Oct  3 08:00:00 2026")
    monkeypatch.setattr(services, "_linux_booted_after", lambda _recorded: booted_after)
    pid_file = tmp_path / "svc.pid"
    pid_file.write_text("4242\nstart_utc=Wed Oct  1 10:00:00 2026\n", encoding="utf-8")

    assert services.tracked_pid_was_recycled(4242, pid_file) is expected


def test_a_record_naming_another_pid_is_never_called_recycled(tmp_path, monkeypatch):
    import services

    monkeypatch.setattr(services.sys, "platform", "darwin")
    monkeypatch.setattr(services, "legacy_process_start_identity", lambda _pid: "Fri Oct  3 08:00:00 2026")
    pid_file = tmp_path / "svc.pid"
    pid_file.write_text("0\nstart_utc=Wed Oct  1 10:00:00 2026\n", encoding="utf-8")

    assert services.tracked_pid_was_recycled(4242, pid_file) is False


def test_the_pid_file_warning_leads_the_preflight_checks(tmp_path, monkeypatch):
    import services
    from services.managed_host import PreflightResult

    result = PreflightResult()
    result.add("memory", "warn", "low memory")
    manager = type("M", (), {
        "pid_file": tmp_path / "svc.pid",
        "_read_pid": lambda self: 4242,
        "_managed_process_alive": lambda self, pid: True,
    })()
    monkeypatch.setattr(services, "tracked_pid_was_recycled", lambda pid, pid_file: True)

    services.add_recycled_pid_check(result, manager)

    assert [c["name"] for c in result.checks] == ["pid-file", "memory"]


# ─────────── a changed port or listen restarts the owned process (#1361) ───────────
@pytest.mark.parametrize("changed", [{"port": 8288}, {"listen": "0.0.0.0"}])
@pytest.mark.parametrize("entry", ["start_with_ownership", "ensure_running_with_ownership"])
def test_start_restarts_an_owned_process_launched_with_other_settings(tmp_path, monkeypatch, changed, entry):
    mgr = _mgr(tmp_path, **changed)
    _install_stub(mgr)
    mgr.pid_file.write_text("999")
    mgr.status_file.write_text(json.dumps({"pid": 999, "port": 8188, "listen": "127.0.0.1"}))
    alive = {999}
    monkeypatch.setattr(ComfyUiMpsManager, "_managed_process_alive", lambda self, pid: pid in alive)
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: False)
    stopped = []

    def stop(self):
        stopped.append(self._read_pid())
        alive.discard(999)
        self._clear_pid()
        return True

    monkeypatch.setattr(ComfyUiMpsManager, "_stop_locked", stop)
    monkeypatch.setattr(mod.socket, "socket", lambda *a, **k: _FakeSocket(1))  # port free
    launched = {}

    def fake_popen(args, **kwargs):
        if args and args[0] == "ps":
            return SimpleNamespace(pid=0, returncode=0, stdout="", stderr="")
        launched["args"] = args
        alive.add(4242)
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: "Mon Jan  1 00:00:00 2024"),
    )
    # ./start.sh goes through preflight and install, which rewrites the status
    # file before start compares it with the configuration.
    monkeypatch.setattr(ComfyUiMpsManager, "preflight", lambda self: SimpleNamespace(ok=True, checks=[]))
    monkeypatch.setattr(ComfyUiMpsManager, "_install_locked", lambda self: self._write_status(installed_ref=self.ref))
    assert mgr.status().port == 8188  # what the old process actually serves

    status, created = getattr(mgr, entry)()

    assert stopped == [999] and created is True and status.pid == 4242
    assert status.port == mgr.port and str(mgr.port) in launched["args"]
    assert mgr.listen in launched["args"]
    record = json.loads(mgr.status_file.read_text())
    assert (record["port"], record["listen"]) == (mgr.port, mgr.listen)
