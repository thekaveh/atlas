"""Unit tests for the Atlas-managed Apple-Silicon/Metal ComfyUI host (#335).

Every host effect (platform detection, subprocess, socket, HTTP, process
signals, filesystem) is mocked, so the whole lifecycle — preflight, idempotent
install/update, start/stop/status/health, failure recovery — is exercised on
generic Linux CI. A separately-marked Darwin-arm64 ``live`` test (bottom of the
file) proves the real /system_stats-reports-MPS contract; it never runs in CI.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import platform
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import comfyui_mps_manager as mod
from services.comfyui_mps_manager import (
    ComfyUiMpsError,
    ComfyUiMpsManager,
    manager_from_env,
)
from tests.three_surface_test_utils import surface_text


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
    result = _mgr(tmp_path).preflight()
    assert result.ok  # ok (mps skipped until venv exists)
    assert result.status in ("ok", "skipped")
    mps = next(c for c in result.checks if c["name"] == "mps")
    assert mps["status"] == "skipped"


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
        return _FakeCompleted(0)

    monkeypatch.setattr(mod.subprocess, "run", rec_run)
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


def test_install_idempotent_skips_clone_when_present(tmp_path, monkeypatch):
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
    mgr.install()  # not update — repo + venv already present
    joined = [" ".join(c) for c in calls]
    assert not any("git clone" in j for j in joined)  # no re-clone
    assert not any("venv" in j and "-m" in j for j in joined)  # no venv rebuild
    assert any(f"checkout --force {mgr.ref}" in j for j in joined)  # ref still pinned


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
    mgr.install(update=True)
    joined = [" ".join(c) for c in calls]
    assert any("pip install --upgrade torch" in j for j in joined)
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
    with pytest.raises(ComfyUiMpsError, match="command failed"):
        _mgr(tmp_path).install()


# ─────────────────────────── start / stop / status ───────────────────────────
def _install_stub(mgr):
    """Pretend an install already happened (venv + repo + model paths)."""
    mgr.venv_python.parent.mkdir(parents=True, exist_ok=True)
    mgr.venv_python.write_text("#!/bin/sh\n")
    mgr.repo_dir.mkdir(parents=True, exist_ok=True)
    (mgr.repo_dir / "main.py").write_text("# comfyui\n")
    mgr.model_paths_file.parent.mkdir(parents=True, exist_ok=True)
    mgr.model_paths_file.write_text("atlas_host:\n  base_path: /x\n")


def test_start_launches_and_records_pid(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    _install_stub(mgr)
    monkeypatch.setattr(mod.socket, "socket", lambda *a, **k: _FakeSocket(1))  # port free
    launched = {}

    def fake_popen(args, **kwargs):
        launched["args"] = args
        launched["kwargs"] = kwargs
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    status, created = mgr.start_with_ownership()
    assert status.running and status.pid == 4242 and status.port == 8188
    assert created is True
    assert mgr.pid_file.read_text().strip() == "4242"
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

    def fake_kill(pid, sig):
        signals.append(sig)
        if sig == mod.signal.SIGKILL:
            alive["v"] = False
        # signal 0 = liveness probe; raise if dead
        if sig == 0 and not alive["v"]:
            raise ProcessLookupError

    monkeypatch.setattr(mod.os, "kill", fake_kill)
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
    assert not mgr.pid_file.exists()  # stale pidfile cleared


def test_stop_reports_failure_when_process_survives_sigkill(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("888")
    monkeypatch.setattr(ComfyUiMpsManager, "_pid_is_stranger", lambda self, pid: False)
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)  # always "alive"
    monkeypatch.setattr(mod.os, "waitpid", lambda pid, flags: (0, 0))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    clock = {"v": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock.__setitem__("v", clock["v"] + 5.0) or clock["v"])
    # Process never dies → stop must be honest and NOT clear the pidfile.
    assert mgr.stop() is False
    assert mgr.pid_file.exists()


def test_stop_returns_false_without_process(tmp_path):
    assert _mgr(tmp_path).stop() is False


def test_status_reflects_liveness(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    mgr.pid_file.write_text("555")
    mgr._write_status(installed_ref="v0.27.0", pid=555)
    monkeypatch.setattr(mod.os, "kill", lambda pid, sig: None)
    st = mgr.status()
    assert st.running and st.pid == 555 and st.installed_ref == "v0.27.0"

    def dead(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(mod.os, "kill", dead)
    st2 = mgr.status()
    assert not st2.running and st2.pid is None


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
    monkeypatch.setattr(ComfyUiMpsManager, "_port_in_use", lambda self: False)
    launches: list[int] = []

    def fake_popen(*_args, **_kwargs):
        launches.append(4242)
        time.sleep(0.05)
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)

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
    monkeypatch.setattr(mgr, "_terminate_pid", lambda _pid: False)

    with pytest.raises(ComfyUiMpsError, match="could not be terminated") as raised:
        mgr.start_with_ownership()

    assert raised.value.surviving_process is True
    assert mgr._untracked_pid == 5150


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


def test_remove_preserves_state_when_process_survives(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True)
    (mgr.state_dir / "junk").write_text("x")
    monkeypatch.setattr(ComfyUiMpsManager, "_stop_locked", lambda self: False)
    monkeypatch.setattr(
        mgr, "status", lambda: mod.ProcessStatus(True, 5150, mgr.port)
    )

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
