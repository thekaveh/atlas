"""Atlas-managed headless Blender + MCP bridge (#759).

Manager lifecycle (preflight / pinned install / start / stop / status),
loopback security policy, manifest wiring for the new ``managed-localhost``
source, start-flow + doctor + CLI + endpoint-export integration. All host
effects mocked — the real headless round-trip was verified live on Darwin
(Blender 4.3.2 + upstream 6641189: get_scene_info + execute_code through the
queue-shim launcher).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

import services.blender_mcp_manager as bm  # noqa: E402
from services.blender_mcp_manager import (  # noqa: E402
    DEFAULT_ADDON_REF,
    DEFAULT_ADDON_SHA256,
    BlenderMcpError,
    BlenderMcpManager,
    manager_from_env,
)

ADDON_BYTES = b"# fake pinned addon\nclass BlenderMCPServer: pass\n"
ADDON_SHA = hashlib.sha256(ADDON_BYTES).hexdigest()


def _manager(tmp_path: Path, **kw) -> BlenderMcpManager:
    defaults = dict(
        state_dir=tmp_path / "state", port=19876,
        addon_sha256=ADDON_SHA,
    )
    defaults.update(kw)
    return BlenderMcpManager(**defaults)


class _FakeDownload:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── env parsing + defaults ───────────────────────────────────────────


def test_manager_from_env_defaults_and_overrides(tmp_path):
    m = manager_from_env({})
    assert m.port == 9876 and m.bind == "127.0.0.1" and not m.allow_remote
    assert m.addon_ref == DEFAULT_ADDON_REF
    assert m.addon_sha256 == DEFAULT_ADDON_SHA256
    assert str(m.state_dir).endswith(".atlas/blender-mcp")
    m = manager_from_env({
        "BLENDER_MCP_STATE_DIR": str(tmp_path),
        "BLENDER_MCP_LOCALHOST_PORT": "7777",
        "BLENDER_MCP_BIND": "0.0.0.0",
        "BLENDER_MCP_ALLOW_REMOTE": "true",
        "BLENDER_MCP_BLENDER_PATH": "/opt/blender",
    })
    assert m.port == 7777 and m.bind == "0.0.0.0" and m.allow_remote
    assert m.blender_path == "/opt/blender"


# ── security: loopback-only ──────────────────────────────────────────


def test_non_loopback_bind_refused_without_explicit_opt_in(tmp_path):
    m = _manager(tmp_path, bind="0.0.0.0")
    assert m.preflight().status == "fail"
    with pytest.raises(BlenderMcpError, match="arbitrary Python"):
        m.start()
    # explicit double opt-in downgrades to a warning
    m2 = _manager(tmp_path, bind="0.0.0.0", allow_remote=True)
    binds = [c for c in m2.preflight().checks if c["name"] == "bind"]
    assert binds[0]["status"] == "warn"


# ── preflight ────────────────────────────────────────────────────────


def test_preflight_fails_without_blender(tmp_path, monkeypatch):
    m = _manager(tmp_path, blender_path=str(tmp_path / "nope"))
    monkeypatch.setattr(bm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bm.shutil, "which", lambda name: None)
    result = m.preflight()
    assert result.status == "fail"
    blender = [c for c in result.checks if c["name"] == "blender"][0]
    assert "BLENDER_MCP_BLENDER_PATH" in blender["detail"]


# ── pinned install ───────────────────────────────────────────────────


def test_install_downloads_verifies_and_is_idempotent(tmp_path, monkeypatch):
    m = _manager(tmp_path)
    calls = {"n": 0}

    def fake_urlopen(url, timeout=30):
        calls["n"] += 1
        assert m.addon_ref in url
        return _FakeDownload(ADDON_BYTES)

    monkeypatch.setattr(bm.urllib.request, "urlopen", fake_urlopen)
    m.install()
    assert m.addon_path.read_bytes() == ADDON_BYTES
    assert "main_q" in m.launcher_path.read_text()  # the queue shim
    assert "bpy.app.timers.register" in m.launcher_path.read_text()
    m.install()  # sha matches → no re-download
    assert calls["n"] == 1


def test_install_refuses_sha_mismatch(tmp_path, monkeypatch):
    m = _manager(tmp_path, addon_sha256="0" * 64)
    monkeypatch.setattr(
        bm.urllib.request, "urlopen", lambda url, timeout=30: _FakeDownload(ADDON_BYTES)
    )
    with pytest.raises(BlenderMcpError, match="sha256 mismatch"):
        m.install()
    assert not m.addon_path.exists()  # unverified code never lands


def test_install_local_addon_override(tmp_path):
    local = tmp_path / "my_addon.py"
    local.write_bytes(b"# local dev addon")
    m = _manager(tmp_path, addon_file=str(local))
    m.install()
    assert m.addon_path.read_bytes() == b"# local dev addon"
    addon_checks = [c for c in m.preflight().checks if c["name"] == "addon"]
    assert addon_checks[0]["status"] == "warn"  # no sha pin → warn


# ── lifecycle (mocked process) ───────────────────────────────────────


def test_start_requires_provisioned_state(tmp_path):
    m = _manager(tmp_path, blender_path=__file__)  # any existing path
    with pytest.raises(BlenderMcpError, match="install first"):
        m.start()


def test_start_spawns_and_waits_for_port(tmp_path, monkeypatch):
    m = _manager(tmp_path)
    m.state_dir.mkdir(parents=True)
    m.addon_path.write_bytes(ADDON_BYTES)
    m.launcher_path.write_text("launcher")
    monkeypatch.setattr(m, "blender_binary", lambda: "/fake/blender")
    spawned = {}

    class _FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(argv, **kw):
        spawned["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(bm.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(m, "_port_in_use", lambda: True)
    status = m.start()
    assert status.running and status.pid == 4242
    assert m._read_pid() == 4242
    assert spawned["argv"][:2] == ["/fake/blender", "--background"]
    assert str(m.launcher_path) in spawned["argv"]
    assert "19876" in spawned["argv"] and "127.0.0.1" in spawned["argv"]


def test_start_failure_reports_log_tail(tmp_path, monkeypatch):
    m = _manager(tmp_path)
    m.state_dir.mkdir(parents=True)
    m.addon_path.write_bytes(ADDON_BYTES)
    m.launcher_path.write_text("launcher")
    m.log_file.write_text("boom: no GPU context\n")
    monkeypatch.setattr(m, "blender_binary", lambda: "/fake/blender")

    class _DeadProc:
        pid = 4243

        def poll(self):
            return 1  # exited immediately

    monkeypatch.setattr(bm.subprocess, "Popen", lambda *a, **k: _DeadProc())
    monkeypatch.setattr(m, "_port_in_use", lambda: False)
    with pytest.raises(BlenderMcpError, match="no GPU context"):
        m.start(wait_timeout=1.0)


def test_status_and_stop_with_dead_pid(tmp_path):
    m = _manager(tmp_path)
    m.state_dir.mkdir(parents=True)
    m.pid_file.write_text("999999999")  # not a live pid
    status = m.status()
    assert not status.running and status.pid is None
    assert m.stop() is True  # dead pid → trivially stopped
    assert not m.pid_file.exists()


# ── manifest wiring ──────────────────────────────────────────────────


def test_manifest_declares_managed_localhost_source():
    from services.manifests import load_manifests

    manifest = next(
        m for m in load_manifests(REPO_ROOT / "services") if m.name == "blender-mcp"
    )
    ids = [o.id for o in manifest.sources.options]
    assert ids == ["localhost", "managed-localhost", "disabled"]
    managed = next(o for o in manifest.sources.options if o.id == "managed-localhost")
    assert managed.profiles == ["default"]  # dev-only, like other host sources
    env_names = {e.name for e in manifest.env}
    assert {
        "BLENDER_MCP_STATE_DIR", "BLENDER_MCP_BIND", "BLENDER_MCP_ALLOW_REMOTE",
        "BLENDER_MCP_BLENDER_PATH", "BLENDER_MCP_ADDON_REF",
        "BLENDER_MCP_ADDON_SHA256", "BLENDER_MCP_ADDON_FILE",
    } <= env_names
    assert "managed-localhost" in manifest.runtime_sc["blender_mcp"]


# ── start-flow / doctor / CLI / export integration ───────────────────


class _Banner:
    def __init__(self):
        self.messages = []

    def show_status_message(self, message, level="info", *a, **k):
        self.messages.append((level, message))


def test_finalize_noop_for_other_sources(monkeypatch):
    import start

    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(parse_env_file=lambda: {"BLENDER_MCP_SOURCE": "localhost"})
    s.banner = _Banner()
    assert s._finalize_managed_blender_mcp() is True
    assert s.banner.messages == []


def test_finalize_managed_runs_and_registers_rollback(monkeypatch):
    import start

    fake_manager = NS(
        ensure_running=lambda: (NS(running=True, pid=1), True),
        health=lambda: {"reachable": True, "objects": 3},
        bind="127.0.0.1", port=9876,
    )
    monkeypatch.setattr(
        bm, "manager_from_env", lambda env: fake_manager
    )
    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(
        parse_env_file=lambda: {"BLENDER_MCP_SOURCE": "managed-localhost"}
    )
    s.banner = _Banner()
    s._managed_hosts_started_this_run = []
    assert s._finalize_managed_blender_mcp() is True
    assert ("Blender MCP", fake_manager) in s._managed_hosts_started_this_run
    assert any("healthy" in m for _, m in s.banner.messages)


def test_doctor_check_gated_and_registered():
    import start

    assert start._doctor_check_blender_mcp in start.DOCTOR_CHECKS
    s = start.AtlasStarter.__new__(start.AtlasStarter)
    s.config_parser = NS(parse_env_file=lambda: {"BLENDER_MCP_SOURCE": "disabled"})
    assert start._doctor_check_blender_mcp(s)["status"] == "pass"


def test_cli_group_registered_with_lifecycle_commands():
    import start

    commands = set(start.blender_mcp_group.commands)
    assert {"preflight", "install", "start", "stop", "status", "health", "remove"} <= commands


def test_endpoint_exported_for_managed_source():
    from core.endpoints_contract import build_export

    d = {f.name: f.value for f in build_export({"BLENDER_MCP_SOURCE": "managed-localhost"})}
    assert d["ATLAS_BLENDER_MCP_HOST_ENDPOINT"] == "tcp://localhost:9876"
    d = {f.name: f.value for f in build_export({"BLENDER_MCP_SOURCE": "disabled"})}
    assert "ATLAS_BLENDER_MCP_HOST_ENDPOINT" not in d
