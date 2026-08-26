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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


def _generic_manager(tmp_path: Path):
    from services.managed_host import HostProcessSpec, ManagedHostManager

    return ManagedHostManager(
        HostProcessSpec(name="remove-test", command=("sleep", "300"), port=8399),
        tmp_path,
    )


def _observe_contender_lock(monkeypatch, module, contender_threads, attempted):
    if module.fcntl is None:
        return
    real_flock = module.fcntl.flock

    def observed_flock(fd, operation):
        if (
            threading.get_ident() in contender_threads
            and operation & module.fcntl.LOCK_EX
        ):
            attempted.set()
        return real_flock(fd, operation)

    monkeypatch.setattr(module.fcntl, "flock", observed_flock)


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_remove_wraps_state_directory_deletion_failure(
    tmp_path, monkeypatch, manager_kind,
):
    from services import blender_mcp_manager as blender_module
    from services import managed_host as managed_module
    from services.managed_host import ManagedHostError

    if manager_kind == "generic":
        manager, module, error_type = (
            _generic_manager(tmp_path / manager_kind), managed_module, ManagedHostError
        )
    else:
        manager, module, error_type = (
            BlenderMcpManager(tmp_path / manager_kind), blender_module, BlenderMcpError
        )
    manager.state_dir.mkdir(parents=True)
    monkeypatch.setattr(manager, "_stop_locked", lambda: True)

    def fail_unless_ignored(*_args, **kwargs):
        if kwargs.get("ignore_errors"):
            return
        raise PermissionError("denied")

    monkeypatch.setattr(module.shutil, "rmtree", fail_unless_ignored)
    with pytest.raises(error_type, match="could not remove.*denied"):
        manager.remove()


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_remove_is_idempotent_when_state_directory_is_absent(tmp_path, manager_kind):
    manager = (
        _generic_manager(tmp_path / manager_kind)
        if manager_kind == "generic"
        else BlenderMcpManager(tmp_path / manager_kind)
    )

    manager.remove()
    manager.remove()

    assert not manager.state_dir.exists()


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_remove_rejects_descendant_not_found_when_state_root_remains(
    tmp_path, monkeypatch, manager_kind,
):
    from services import blender_mcp_manager as blender_module
    from services import managed_host as managed_module
    from services.managed_host import ManagedHostError

    if manager_kind == "generic":
        manager, module, error_type = (
            _generic_manager(tmp_path / manager_kind), managed_module, ManagedHostError
        )
    else:
        manager, module, error_type = (
            BlenderMcpManager(tmp_path / manager_kind), blender_module, BlenderMcpError
        )
    manager.state_dir.mkdir(parents=True)
    monkeypatch.setattr(manager, "_stop_locked", lambda: True)
    monkeypatch.setattr(
        module.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("vanished child")),
    )

    with pytest.raises(error_type, match="could not remove.*vanished child"):
        manager.remove()

    assert manager.state_dir.is_dir()


@pytest.mark.parametrize("entrypoint", ["start", "ensure_running"])
def test_concurrent_starts_are_serialized_across_manager_instances(
    tmp_path, monkeypatch, entrypoint,
):
    managers = [_manager(tmp_path), _manager(tmp_path)]
    active = 0
    max_active = 0
    counter_lock = threading.Lock()
    first_entered = threading.Event()
    release_first = threading.Event()
    contender_attempted_lock = threading.Event()
    contender_threads: set[int] = set()

    _observe_contender_lock(
        monkeypatch, bm, contender_threads, contender_attempted_lock
    )

    def fake_start_locked(self, _wait_timeout):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        if self is managers[0]:
            first_entered.set()
            assert release_first.wait(2)
        with counter_lock:
            active -= 1
        return bm.ProcessStatus(True, 4242, True)

    monkeypatch.setattr(
        BlenderMcpManager, "_start_locked", fake_start_locked, raising=False
    )
    monkeypatch.setattr(
        BlenderMcpManager, "preflight", lambda self: NS(ok=True, checks=[])
    )
    monkeypatch.setattr(BlenderMcpManager, "_install_locked", lambda self: None)
    monkeypatch.setattr(
        BlenderMcpManager, "status", lambda self: bm.ProcessStatus(False)
    )

    def invoke(manager):
        if entrypoint == "ensure_running":
            return manager.ensure_running()[0]
        return manager.start()

    def invoke_contender():
        contender_threads.add(threading.get_ident())
        return invoke(managers[1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, managers[0])
        assert first_entered.wait(1)
        contender = pool.submit(invoke_contender)
        if bm.fcntl is not None:
            assert contender_attempted_lock.wait(1)
            assert not contender.done()
        release_first.set()
        results = [first.result(), contender.result()]

    assert all(result.running for result in results)
    assert max_active == 1


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
@pytest.mark.parametrize(
    "operations",
    [
        ("start", "stop"),
        ("start", "remove"),
        ("start", "install"),
        ("install", "remove"),
        ("install", "install"),
    ],
)
def test_lifecycle_mutations_share_one_lock(
    tmp_path, monkeypatch, manager_kind, operations,
):
    from services.managed_host import HostProcessSpec, ManagedHostManager

    if manager_kind == "generic":
        spec = HostProcessSpec(name="locked-test", command=("sleep", "30"), port=8399)
        managers = [ManagedHostManager(spec, tmp_path / "state") for _ in range(2)]
        manager_type = ManagedHostManager
        status = NS(running=True, pid=4242)
    else:
        managers = [_manager(tmp_path), _manager(tmp_path)]
        manager_type = BlenderMcpManager
        status = bm.ProcessStatus(True, 4242, True)

    managers[0].state_dir.mkdir(parents=True, exist_ok=True)
    marker = managers[0].state_dir / "installed.marker"
    marker.write_text("keep", encoding="utf-8")
    entered = threading.Event()
    release = threading.Event()
    contender_attempted_lock = threading.Event()
    contender_threads: set[int] = set()
    held_operation, competing_operation = operations
    module = __import__(manager_type.__module__, fromlist=["fcntl"])
    _observe_contender_lock(monkeypatch, module, contender_threads, contender_attempted_lock)

    def fake_start_locked(self, _wait_timeout):
        if self is managers[0] and held_operation == "start":
            entered.set()
            assert release.wait(2)
        return status

    def fake_install_locked(self, *args, **kwargs):
        if self is managers[0] and held_operation == "install":
            entered.set()
            assert release.wait(2)

    monkeypatch.setattr(manager_type, "_start_locked", fake_start_locked)
    monkeypatch.setattr(
        manager_type, "_install_locked", fake_install_locked, raising=False
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        held_future = pool.submit(getattr(managers[0], held_operation))
        assert entered.wait(1)
        def invoke_competing():
            contender_threads.add(threading.get_ident())
            return getattr(managers[1], competing_operation)()

        competing_future = pool.submit(invoke_competing)
        if module.fcntl is not None:
            assert contender_attempted_lock.wait(1)
            assert not competing_future.done()
        assert marker.exists()
        release.set()
        held_future.result()
        competing_future.result()

    if competing_operation == "remove":
        assert not managers[0].state_dir.exists()


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


def test_missing_launch_identity_terminates_new_process(tmp_path, monkeypatch):
    m = _manager(tmp_path)
    m.state_dir.mkdir(parents=True)
    m.addon_path.write_bytes(ADDON_BYTES)
    m.launcher_path.write_text("launcher")
    monkeypatch.setattr(m, "blender_binary", lambda: "/fake/blender")

    class _LiveProc:
        pid = 4245

        def poll(self):
            return None

    process = _LiveProc()
    monkeypatch.setattr(bm.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: None),
    )
    monkeypatch.setattr(bm.time, "sleep", lambda _seconds: None)
    terminated: list[int] = []
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._terminate_untracked",
        staticmethod(lambda child: terminated.append(child.pid) or True),
    )

    with pytest.raises(BlenderMcpError, match="child was terminated"):
        m.start()

    assert terminated == [process.pid]
    assert not m.pid_file.exists()


def test_failed_identity_compensation_preserves_pid_evidence(tmp_path, monkeypatch):
    m = _manager(tmp_path)
    m.state_dir.mkdir(parents=True)
    m.addon_path.write_bytes(ADDON_BYTES)
    m.launcher_path.write_text("launcher")
    monkeypatch.setattr(m, "blender_binary", lambda: "/fake/blender")
    process = type("LiveProc", (), {"pid": 4246, "poll": lambda self: None})()
    monkeypatch.setattr(bm.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._process_start_time",
        staticmethod(lambda _pid: None),
    )
    monkeypatch.setattr(bm.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._terminate_untracked",
        staticmethod(lambda _child: False),
    )

    with pytest.raises(BlenderMcpError, match="terminate pid 4246 manually"):
        m.start()

    assert m.pid_file.read_text(encoding="utf-8").splitlines() == ["4246"]


def test_launch_identity_interrupt_compensates_and_propagates(tmp_path, monkeypatch):
    m = _manager(tmp_path)
    m.state_dir.mkdir(parents=True)
    m.addon_path.write_bytes(ADDON_BYTES)
    m.launcher_path.write_text("launcher")
    monkeypatch.setattr(m, "blender_binary", lambda: "/fake/blender")
    process = type("LiveProc", (), {"pid": 4247, "poll": lambda self: None})()
    monkeypatch.setattr(bm.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        "services.managed_host.require_process_start_time",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    terminated: list[int] = []
    monkeypatch.setattr(
        "services.managed_host.ManagedHostManager._terminate_untracked",
        staticmethod(lambda child: terminated.append(child.pid) or True),
    )

    with pytest.raises(KeyboardInterrupt):
        m.start()

    assert terminated == [4247]
    assert not m.pid_file.exists()


def test_manager_from_env_malformed_port_falls_back(tmp_path):
    m = manager_from_env({"BLENDER_MCP_LOCALHOST_PORT": "not-a-port",
                          "BLENDER_MCP_STATE_DIR": str(tmp_path)})
    assert m.port == 9876  # degrade, never traceback the launch/CLI


@pytest.mark.parametrize(
    "payload",
    [[], {"status": "success", "result": "bad"}, {"status": "error", "result": {}}],
)
def test_health_rejects_unexpected_protocol_shapes(tmp_path, monkeypatch, payload):
    manager = _manager(tmp_path)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def sendall(self, _payload):
            pass

        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            return bm.json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(bm.socket, "create_connection", lambda *_a, **_k: Connection())

    health = manager.health()
    assert health["reachable"] is False
    assert "error" in health


def test_permission_denied_pid_is_live_but_never_adopted(tmp_path, monkeypatch):
    """Permission denial proves existence, not ownership."""
    def perm(pid, sig):
        raise PermissionError

    monkeypatch.setattr(bm.os, "kill", perm)
    assert BlenderMcpManager._pid_alive(12345) is True
    manager = BlenderMcpManager(state_dir=tmp_path, port=59995)
    tmp_path.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("12345\n", encoding="utf-8")
    before = manager.pid_file.read_bytes()
    assert manager.stop() is False
    with pytest.raises(BlenderMcpError, match="ownership is mismatched or unknown"):
        manager.start()
    assert manager.pid_file.read_bytes() == before


def test_start_foreign_port_holder_is_not_success(tmp_path, monkeypatch):
    """An open foreign port is rejected under the lock before spawning."""
    m = _manager(tmp_path)
    m.state_dir.mkdir(parents=True)
    m.addon_path.write_bytes(ADDON_BYTES)
    m.launcher_path.write_text("launcher")
    m.log_file.write_text("Address already in use\n")
    monkeypatch.setattr(m, "blender_binary", lambda: "/fake/blender")

    monkeypatch.setattr(
        bm.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("occupied port reached Popen"),
    )
    monkeypatch.setattr(m, "_port_in_use", lambda: True)  # foreign holder
    with pytest.raises(BlenderMcpError, match="port 19876 is already in use"):
        m.start(wait_timeout=1.0)


def test_stop_py_tears_down_managed_blender(monkeypatch, tmp_path):
    """#759 hardening: ./stop.sh --stop-managed-hosts must stop the code-exec
    bridge like the other managed hosts."""
    import stop as stop_module

    assert hasattr(stop_module.AtlasStopper, "stop_managed_blender_mcp")
    stopper = stop_module.AtlasStopper.__new__(stop_module.AtlasStopper)
    calls = {}

    class _FakeMgr:
        def status(self):
            calls.setdefault("status", 0)
            calls["status"] += 1
            return NS(running=calls["status"] == 1)  # running before, stopped after

        def stop(self):
            calls["stop"] = True
            return True

    stopper.config_parser = NS(env_file_exists=lambda: False, parse_env_file=lambda: {})
    stopper.banner = NS(show_status_message=lambda *a, **k: None)
    monkeypatch.setattr(bm, "manager_from_env", lambda env: _FakeMgr())
    assert stopper.stop_managed_blender_mcp() is True
    assert calls.get("stop") is True


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


def test_stop_reaps_its_own_child_instead_of_polling_a_zombie(tmp_path):
    """A child of this process that has exited but not been waited on is a
    zombie, and a zombie still answers kill(0). Without the reap, stop()
    burned its full 10s grace window and then returned False for a process
    it had just killed, leaving a stale pid file behind (#795 follow-up).

    Spawns a real child on purpose: the bug lives in the interaction between
    Popen ownership and os.kill(pid, 0), which a mocked process cannot show.
    """
    import subprocess
    import sys
    import time

    manager = BlenderMcpManager(state_dir=tmp_path, port=59997)
    tmp_path.mkdir(parents=True, exist_ok=True)
    # Stamp the real child's start time so the guard can prove ownership;
    # otherwise it must short-circuit and this would not exercise the reap.
    from services.managed_host import (
        ManagedHostManager,
        require_process_start_time,
        write_pid_file_with_identity,
    )
    child = subprocess.Popen(  # noqa: S603 - fixed argv, test-local
        [sys.executable, "-c", f"import time; time.sleep(300)  # {tmp_path}"],
        start_new_session=True,
    )
    try:
        write_pid_file_with_identity(
            manager.pid_file,
            child.pid,
            require_process_start_time(
                child.pid, ManagedHostManager._process_start_time
            ),
        )
        assert manager._pid_is_stranger(child.pid) is False, (
            "test setup: the child must read as ours, or the guard short-circuits"
        )
        assert manager.status().running is True
        started = time.monotonic()
        stopped = manager.stop()
        elapsed = time.monotonic() - started

        assert stopped is True, "stop() reported failure for a process it killed"
        assert manager.pid_file.exists() is False, "stale pid file left behind"
        assert elapsed < 5.0, f"stop() polled a zombie for {elapsed:.1f}s"
    finally:
        try:
            if child.poll() is None:
                child.kill()
        except OSError:
            pass
        try:
            child.wait(timeout=5)
        except ChildProcessError:
            pass
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


# ── PID-reuse guard (#795 follow-up) ─────────────────────────────────


def test_stop_refuses_to_signal_a_recycled_pid_owned_by_a_stranger(tmp_path):
    """A crashed bridge's pid can be recycled by the OS onto an unrelated
    process while the pid file outlives the crash. Signalling blind would
    SIGTERM (then SIGKILL) a stranger — someone else's editor or build.

    Spawns a real long-lived process that is plainly NOT Blender, points the
    pid file at it, and asserts stop() leaves it alone.
    """
    import subprocess
    import sys

    manager = BlenderMcpManager(state_dir=tmp_path, port=59996)
    tmp_path.mkdir(parents=True, exist_ok=True)
    stranger = subprocess.Popen(  # noqa: S603 - fixed argv, test-local
        [sys.executable, "-c", "import time; time.sleep(300)"],
        start_new_session=True,
    )
    manager.pid_file.write_text(str(stranger.pid), encoding="utf-8")
    try:
        assert manager.stop() is False, "unknown ownership must refuse signalling"
        assert stranger.poll() is None, "stop() killed an unrelated process"
        assert manager.pid_file.exists(), "ownership evidence was discarded"
    finally:
        stranger.kill()
        stranger.wait()


def test_an_ambiguous_ps_probe_refuses_teardown(tmp_path, monkeypatch):
    """When `ps` is unavailable, ownership is unknown and signalling is refused."""
    manager = BlenderMcpManager(state_dir=tmp_path, port=59995)

    def _boom(*_args, **_kwargs):
        raise OSError("ps unavailable")

    monkeypatch.setattr("services.blender_mcp_manager.subprocess.run", _boom)
    assert manager._pid_is_stranger(4242) is True


def test_unstamped_blender_marker_still_refuses_ownership(tmp_path, monkeypatch):
    manager = BlenderMcpManager(state_dir=tmp_path, port=59995)
    manager.pid_file.write_text("4242\n", encoding="utf-8")
    result = type("Result", (), {
        "returncode": 0,
        "stdout": "/Applications/Blender.app/Contents/MacOS/Blender --background",
    })()
    monkeypatch.setattr(
        "services.blender_mcp_manager.subprocess.run",
        lambda *_args, **_kwargs: result,
    )
    assert manager._pid_is_stranger(4242) is True


def test_start_refuses_live_unknown_pid_and_preserves_tracking(tmp_path, monkeypatch):
    manager = BlenderMcpManager(state_dir=tmp_path, port=59995)
    tmp_path.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\n", encoding="utf-8")
    before = manager.pid_file.read_bytes()
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(manager, "_pid_is_stranger", lambda _pid: True)

    with pytest.raises(BlenderMcpError, match="ownership is mismatched or unknown"):
        manager.start()

    assert manager.pid_file.read_bytes() == before


def test_a_blender_command_line_without_identity_is_not_adopted(tmp_path, monkeypatch):
    manager = BlenderMcpManager(state_dir=tmp_path, port=59994)
    manager.pid_file.write_text("4242\n", encoding="utf-8")

    class _Out:
        returncode = 0
        stdout = f"/Applications/Blender.app/Contents/MacOS/Blender --background --python {manager.launcher_path}"

    monkeypatch.setattr(
        "services.blender_mcp_manager.subprocess.run", lambda *a, **k: _Out()
    )
    assert manager._pid_is_stranger(4242) is True


def test_ownership_uses_start_time_not_the_command_line(tmp_path, monkeypatch):
    """Replaces a test that pinned `-ww` on the old argv probe.

    That probe is gone. It substring-matched the command line against
    `("blender", "Blender", <launcher>, <state_dir>)` — generic strings, not
    an identity — so a recycled pid landing on any Blender process was
    signalled, and `os.killpg(pid, SIGKILL)` took out its whole group. The
    `-ww` flag existed because Linux procps truncates the command line to 80
    columns without a tty, which made our OWN process read as a stranger; that
    whole failure mode disappears with a fixed-width `ps -o lstart=` probe.

    `(pid, start time)` is unique on POSIX. The locale/TZ concern that
    replaces truncation is handled once, in
    `services.legacy_process_start_identity` (it pins TZ=UTC and LC_ALL=C),
    and is tested there.
    """
    from services.managed_host import write_pid_file_with_identity

    manager = BlenderMcpManager(state_dir=tmp_path, port=59993)
    manager.state_dir.mkdir(parents=True, exist_ok=True)

    write_pid_file_with_identity(manager.pid_file, 4242, "Mon Jan  1 00:00:00 2024")
    monkeypatch.setattr(
        "services.legacy_process_start_identity",
        lambda pid: "Tue Feb  2 02:02:02 2027",
    )
    assert manager._pid_is_stranger(4242) is True, "a recycled pid would be signalled"

    monkeypatch.setattr(
        "services.legacy_process_start_identity",
        lambda pid: "Mon Jan  1 00:00:00 2024",
    )
    assert manager._pid_is_stranger(4242) is False

def test_a_real_child_identity_round_trips(tmp_path):
    """A real child stamped with its start time is recognised as ours."""
    import subprocess
    import sys

    manager = BlenderMcpManager(state_dir=tmp_path, port=59992)
    child = subprocess.Popen(  # noqa: S603 - fixed argv, test-local
        [sys.executable, "-c", "import time; time.sleep(300)"],
        start_new_session=True,
    )
    try:
        from services.managed_host import (
            ManagedHostManager,
            require_process_start_time,
            write_pid_file_with_identity,
        )
        write_pid_file_with_identity(
            manager.pid_file,
            child.pid,
            require_process_start_time(child.pid, ManagedHostManager._process_start_time),
        )
        assert manager._pid_is_stranger(child.pid) is False
    finally:
        child.kill()
        child.wait()


def test_the_pid_file_round_trips_through_its_own_reader(tmp_path):
    """The writer and the reader must agree.

    `write_pid_file_with_identity` emits `pid\nstart_utc=...`; a reader that
    parses the WHOLE file with `int()` raises ValueError and returns None on
    every host where `ps` answers, inverting the lifecycle — status reports
    not-running while it runs, stop deletes the record and leaves it alive.

    This exact defect shipped in the ComfyUI-MPS manager and was invisible to a
    full green suite, because every test there stubbed `ps` so the two-line
    file never existed. That is why this pins the PAIR directly rather than
    going through a stubbed start path.
    """
    from services.managed_host import write_pid_file_with_identity

    mgr = BlenderMcpManager(state_dir=tmp_path, port=59991)
    mgr.state_dir.mkdir(parents=True, exist_ok=True)

    write_pid_file_with_identity(mgr.pid_file, 55163, "Fri Aug 21 22:53:12 2026")
    assert mgr.pid_file.read_text(encoding="utf-8").count("\n") == 2
    assert mgr._read_pid() == 55163, "the writer's own output is unreadable"

    mgr.pid_file.write_text("4242\n", encoding="utf-8")
    assert mgr._read_pid() == 4242, "a legacy single-line file must still parse"

    for bad in ("0\n", "-1\n", "garbage\n", ""):
        mgr.pid_file.write_text(bad, encoding="utf-8")
        assert mgr._read_pid() is None, bad
