"""Lifecycle cases shared by the ComfyUI-MPS and vLLM-Metal managers.

These were near-copies in test_comfyui_mps_manager.py and
test_vllm_metal_manager.py. Each case now runs once per host through the
``host`` fixture, and the assertions are unchanged. What differs per host lives
in ``HOSTS``: the module, the manager class, the error type, the per-file
constructor and platform stub, and vLLM's extra Apple-Silicon detail check.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import ModuleType
from types import SimpleNamespace
from typing import Callable, Optional

import pytest

from services import blender_mcp_manager as blender_mod
from services import comfyui_mps_manager as comfy_mod
from services import vllm_metal_manager as vllm_mod
from services.managed_host import ManagedHostManager
from tests.test_blender_mcp_manager import _manager as _blender_manager
from tests.test_comfyui_mps_manager import _darwin_arm64 as _comfy_darwin
from tests.test_comfyui_mps_manager import _mgr as _comfy_mgr
from tests.test_vllm_metal_manager import _darwin_arm64 as _vllm_darwin
from tests.test_vllm_metal_manager import _mgr as _vllm_mgr


@dataclass(frozen=True)
class _Host:
    mod: ModuleType
    manager_cls: type
    error_cls: type
    mgr: Callable
    darwin: Callable
    # vLLM additionally asserts its Apple-Silicon wording; ComfyUI does not.
    intel_arch_detail: Optional[str]


HOSTS = {
    "comfyui": _Host(
        mod=comfy_mod,
        manager_cls=comfy_mod.ComfyUiMpsManager,
        error_cls=comfy_mod.ComfyUiMpsError,
        mgr=_comfy_mgr,
        darwin=_comfy_darwin,
        intel_arch_detail=None,
    ),
    "vllm": _Host(
        mod=vllm_mod,
        manager_cls=vllm_mod.VllmMetalManager,
        error_cls=vllm_mod.VllmMetalError,
        mgr=_vllm_mgr,
        darwin=_vllm_darwin,
        intel_arch_detail="Apple Silicon",
    ),
}


@pytest.fixture(params=["comfyui", "vllm"])
def host(request) -> _Host:
    return HOSTS[request.param]


# ─────────────────────────── preflight ───────────────────────────
def test_preflight_fails_on_non_macos(host, tmp_path, monkeypatch):
    monkeypatch.setattr(host.mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(host.mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(host.mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = host.mgr(tmp_path).preflight()
    assert result.status == "fail"
    assert not result.ok
    os_check = next(c for c in result.checks if c["name"] == "os")
    assert os_check["status"] == "fail"
    assert "macOS" in os_check["detail"]


def test_preflight_warns_on_low_memory(host, tmp_path, monkeypatch):
    host.darwin(monkeypatch, memsize_gb=8)
    result = host.mgr(tmp_path, min_memory_gb=16).preflight()
    assert result.status == "warn"
    mem = next(c for c in result.checks if c["name"] == "memory")
    assert mem["status"] == "warn"
    assert "below" in mem["detail"]


def test_preflight_fails_on_intel_mac(host, tmp_path, monkeypatch):
    monkeypatch.setattr(host.mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(host.mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(host.mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = host.mgr(tmp_path).preflight()
    assert result.status == "fail"
    arch = next(c for c in result.checks if c["name"] == "arch")
    assert arch["status"] == "fail"
    if host.intel_arch_detail is not None:
        assert host.intel_arch_detail in arch["detail"]


# ─────────────────────────── run / status ───────────────────────────
def test_run_translates_install_timeout(host, tmp_path, monkeypatch):
    def time_out(_cmd, **_kwargs):
        raise subprocess.TimeoutExpired(["slow"], 1)

    monkeypatch.setattr(host.mod, "run_with_deadline", time_out)
    with pytest.raises(host.error_cls, match="timed out"):
        host.mgr(tmp_path)._run(["slow"])


def test_run_translates_output_overflow(host, tmp_path, monkeypatch):
    def overflow(_cmd, **_kwargs):
        raise host.mod.CommandOutputTooLarge

    monkeypatch.setattr(host.mod, "run_with_deadline", overflow)
    with pytest.raises(host.error_cls, match="output limit"):
        host.mgr(tmp_path)._run(["noisy"])


def test_start_with_ownership_distinguishes_existing_process(host, tmp_path, monkeypatch):
    mgr = host.mgr(tmp_path)
    monkeypatch.setattr(
        mgr, "status", lambda: host.mod.ProcessStatus(True, 4242, mgr.port)
    )

    status, created = mgr.start_with_ownership()

    assert status.pid == 4242
    assert created is False


def test_start_refuses_live_unknown_pid_and_preserves_tracking(host, tmp_path, monkeypatch):
    mgr = host.mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("4242\n", encoding="utf-8")
    before = mgr.pid_file.read_bytes()
    monkeypatch.setattr(mgr, "_managed_process_alive", lambda _pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda _pid: True)

    with pytest.raises(host.error_cls, match="ownership is mismatched or unknown"):
        mgr.start_with_ownership()

    assert mgr.pid_file.read_bytes() == before


# ─────────────────────────── stop / group authority ───────────────────────────
def test_stop_on_absent_state_does_not_create_service_directory(host, tmp_path):
    mgr = host.mgr(tmp_path)

    assert mgr.stop() is False
    assert not mgr.state_dir.exists()


def test_stop_reaps_exited_leader_during_grace_period(host, tmp_path, monkeypatch):
    mgr = host.mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("556")
    state = {"alive": True, "signals": []}

    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: False)
    monkeypatch.setattr(mgr, "_managed_process_alive", lambda pid: state["alive"])
    monkeypatch.setattr(
        host.mod.os, "killpg", lambda pid, sig: state["signals"].append(sig)
    )

    def reap(pid, flags):
        state["alive"] = False
        return pid, 0

    monkeypatch.setattr(host.mod.os, "waitpid", reap)

    assert mgr.stop() is True
    assert state["signals"] == [host.mod.signal.SIGINT]


def test_stop_refuses_stale_leaderless_group_evidence(host, tmp_path, monkeypatch):
    mgr = host.mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
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


def test_stop_waits_for_group_after_sigkill(host, tmp_path, monkeypatch):
    mgr = host.mgr(tmp_path)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("889")
    state = {"killed": False, "post_kill_probes": 0}

    def alive(_pid):
        if not state["killed"]:
            return True
        state["post_kill_probes"] += 1
        return state["post_kill_probes"] < 3

    def killpg(_pid, sig):
        if sig == host.mod.signal.SIGKILL:
            state["killed"] = True

    clock = {"value": 0.0}
    monkeypatch.setattr(mgr, "_pid_alive", alive)
    monkeypatch.setattr(mgr, "_managed_process_alive", alive)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: False)
    monkeypatch.setattr(host.mod.os, "killpg", killpg)
    monkeypatch.setattr(host.mod.os, "waitpid", lambda pid, flags: (pid, 0))
    monkeypatch.setattr(host.mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        host.mod.time,
        "monotonic",
        lambda: clock.__setitem__("value", clock["value"] + 2.0) or clock["value"],
    )

    assert mgr.stop() is True
    assert state["post_kill_probes"] >= 3


def test_stop_sweeps_current_launch_group_after_leader_exits(host, tmp_path, monkeypatch):
    mgr = host.mgr(tmp_path)
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


def test_stop_waiting_for_remove_does_not_recreate_deleted_state(host, tmp_path, monkeypatch):
    remover = host.mgr(tmp_path)
    stopper = host.mgr(tmp_path)
    remover.state_dir.mkdir(parents=True)
    (remover.state_dir / "junk").write_text("x")
    monkeypatch.setattr(host.manager_cls, "_stop_locked", lambda self: False)
    removed = threading.Event()
    release_remove = threading.Event()
    real_rmtree = host.mod.shutil.rmtree

    def delayed_rmtree(path):
        real_rmtree(path)
        removed.set()
        assert release_remove.wait(timeout=2)

    monkeypatch.setattr(host.mod.shutil, "rmtree", delayed_rmtree)
    with ThreadPoolExecutor(max_workers=2) as pool:
        removal = pool.submit(remover.remove)
        assert removed.wait(timeout=2)
        stopping = pool.submit(stopper.stop)
        time.sleep(0.05)
        release_remove.set()
        removal.result(timeout=2)
        stopping.result(timeout=2)

    assert not remover.state_dir.exists()


def test_verified_launch_retains_group_authority_until_commit(host, tmp_path, monkeypatch):
    mgr = host.mgr(tmp_path)
    mgr._untracked_pid = 5156
    monkeypatch.setattr(mgr, "_read_pid", lambda: 5156)
    monkeypatch.setattr(mgr, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda _pid: False)

    assert mgr.confirm_started_process(5156) is True
    assert mgr._untracked_pid == 5156
    mgr.commit_started_process()
    assert mgr._untracked_pid is None


def test_dead_launch_leader_keeps_group_sweep_authority(host, tmp_path, monkeypatch):
    mgr = host.mgr(tmp_path)
    mgr._untracked_pid = 5155
    mgr.pid_file.parent.mkdir(parents=True)
    mgr.pid_file.write_text("5155\nstart_utc=stamp\n", encoding="utf-8")
    monkeypatch.setattr(mgr, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(mgr, "_process_group_alive", lambda _pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda _pid: True)

    assert mgr.confirm_started_process(5155) is False
    assert mgr._untracked_pid == 5155


# ─────────────────────────── termination escalation pins ───────────────────────────
# The four managers stop a process group differently on purpose. These pin the
# first signal each one sends; see the comments at each site.
def test_terminate_escalates_from_sigint(host, monkeypatch):
    """ComfyUI-MPS and vLLM-Metal route _terminate_pid through
    services.terminate_process_group, whose first signal is SIGINT."""
    sent: list[int] = []
    monkeypatch.setattr(host.mod.os, "killpg", lambda pid, sig: sent.append(sig))
    fake = SimpleNamespace(
        _managed_process_alive=lambda pid: False,
        _reap_child=lambda pid: None,
    )

    assert host.manager_cls._terminate_pid(fake, 4242) is True
    assert sent == [signal.SIGINT]


def test_generic_framework_sweep_escalates_from_sigterm(monkeypatch):
    """The generic ManagedHostManager sweep is SIGTERM first, the difference
    documented against services.terminate_process_group."""
    survives = iter([True, False])
    sent: list[int] = []
    monkeypatch.setattr(
        ManagedHostManager, "_group_survives",
        staticmethod(lambda pid: next(survives)),
    )
    monkeypatch.setattr(os, "killpg", lambda pid, sig: sent.append(sig))

    assert ManagedHostManager._sweep_orphaned_group(4242) is True
    assert sent == [signal.SIGTERM]


def test_blender_stop_sends_sigterm_first(tmp_path, monkeypatch):
    """Blender MCP sends SIGTERM first, not the SIGINT that
    services.terminate_process_group sends to ComfyUI and vLLM."""
    mgr = _blender_manager(tmp_path)
    sent: list[int] = []
    monkeypatch.setattr(mgr, "_read_pid", lambda: 4242)
    monkeypatch.setattr(mgr, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(mgr, "_pid_is_stranger", lambda pid: False)
    monkeypatch.setattr(mgr, "_managed_process_alive", lambda pid: False)
    monkeypatch.setattr(blender_mod.os, "killpg", lambda pid, sig: sent.append(sig))

    assert mgr._stop_locked() is True
    assert sent == [blender_mod.signal.SIGTERM]
