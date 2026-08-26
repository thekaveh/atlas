"""Atlas-managed headless Blender + MCP bridge host process (#759).

``BLENDER_MCP_SOURCE=managed-localhost`` turns the hand-run GUI workflow
(open Blender → install add-on → click Connect → keep the window alive) into
a managed host lifecycle, mirroring ``comfyui_mps_manager`` for ComfyUI:
preflight → provision → launch headless → health-check → stop/remove.

What Atlas provisions is the **bridge**, pinned and reproducible: the
upstream ``blender-mcp`` add-on file (pinned ref + sha256, #505 doctrine) and
a small launcher into an Atlas-owned state dir. The Blender application
itself is **required, not provisioned** (preflight fails with an actionable
message) — the same posture as ``ollama-localhost`` requiring the host
daemon; shipping a ~300 MB Blender.app is a deliberate non-goal for v1.

Headless mechanism (empirically proven on Blender 4.3.2 + upstream
``6641189``): the stock add-on accepts connections on a background thread but
executes every command on Blender's main thread via
``bpy.app.timers.register`` — which only fires when the GUI event loop pumps
timers, hence upstream's explicit ``--background`` guard. The launcher shims
timer registration into a queue drained by its own main-thread loop — the
same main-thread execution contract, no GUI, no add-on patching.

Security (#759 C3): the source ships **disabled**; the managed bridge binds
**loopback only** (the manager refuses any other bind unless
``BLENDER_MCP_ALLOW_REMOTE=true``), because ``execute_code`` runs arbitrary
Python inside Blender. Exposing it beyond localhost is explicitly the
operator's decision, twice over.

All host effects go through thin stdlib calls so the manager is fully
unit-testable with mocks on CI; the real round-trip is a Darwin live test.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from services import (
    acquire_lifecycle_lock,
    add_lifecycle_preflight,
    await_owned_process_readiness,
    await_spawned_process_readiness,
    lifecycle_support_error,
    process_group_owns_tcp_listener,
    refuse_occupied_port,
    remove_state_directory,
    require_lifecycle_support,
    tracked_process_may_survive,
)

try:  # POSIX advisory locking; absent on native Windows
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

# Pinned upstream add-on (ahujasid/blender-mcp). Override via
# BLENDER_MCP_ADDON_REF / BLENDER_MCP_ADDON_SHA256 when deliberately moving
# the pin; both must move together.
DEFAULT_ADDON_REF = "6641189231caf3752302ae20591bc87fda85fc4e"
DEFAULT_ADDON_SHA256 = "bba60831f5f89a74deda0294b131668a086cf46eb35a6a01abbd0d21d9e92630"
ADDON_URL_TEMPLATE = "https://raw.githubusercontent.com/ahujasid/blender-mcp/{ref}/addon.py"

_OK, _WARN, _FAIL, _SKIPPED = "ok", "warn", "fail", "skipped"


def _lifecycle_support_error() -> str | None:
    return lifecycle_support_error(fcntl, os, signal, "managed Blender MCP")


# The proven headless launcher (see module docstring). Written verbatim into
# the state dir; parametrized entirely via argv after ``--``.
_LAUNCHER = '''"""Atlas blender-mcp headless launcher (#759) — generated; do not edit.

Runs the stock blender-mcp add-on's socket server under `blender
--background`. The add-on executes commands on Blender's main thread via
bpy.app.timers.register (pumped by the GUI event loop, absent headless), so
registration is shimmed into a queue this main-thread loop drains — same
main-thread contract, no GUI.
"""
import importlib.util
import queue
import socket as socket_mod
import sys
import threading

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
ADDON, PORT, BIND = argv[0], int(argv[1]), argv[2]

spec = importlib.util.spec_from_file_location("blender_mcp_addon", ADDON)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.register()  # scene properties some command handlers read

main_q = queue.Queue()
bpy.app.timers.register = (
    lambda fn, first_interval=0.0, persistent=False: main_q.put(fn)
)

server = mod.BlenderMCPServer(host=BIND, port=PORT)
# The stock start() refuses under bpy.app.background because timers would
# never fire — the queue shim above IS the missing main-thread pump, so
# replicate start() minus that guard (instance attrs only).
server.running = True
last_bind_error = None
for family, socktype, protocol, _canonname, address in socket_mod.getaddrinfo(
    BIND, PORT, type=socket_mod.SOCK_STREAM
):
    candidate = None
    try:
        candidate = socket_mod.socket(family, socktype, protocol)
        candidate.setsockopt(socket_mod.SOL_SOCKET, socket_mod.SO_REUSEADDR, 1)
        candidate.bind(address)
    except OSError as exc:
        last_bind_error = exc
        if candidate is not None:
            candidate.close()
        continue
    server.socket = candidate
    break
else:
    raise last_bind_error or OSError(f"no bindable address for {BIND}:{PORT}")
server.socket.listen(1)
server.server_thread = threading.Thread(target=server._server_loop, daemon=True)
server.server_thread.start()
print(f"atlas-blender-mcp: serving on {BIND}:{PORT}", flush=True)

while True:
    fn = main_q.get()
    fn()
'''


# #795: the preflight verdict is shared with the generic managed-host
# framework rather than copied a fourth time. ProcessStatus stays local
# --- each manager reports different fields (device, served models,
# port_open), so unifying it would mean a union type nobody reads.
try:
    from services.managed_host import PreflightResult
except ImportError:  # pragma: no cover - defensive loose-module fallback
    from managed_host import PreflightResult  # type: ignore[no-redef]


class BlenderMcpError(RuntimeError):
    """Raised for managed blender-mcp lifecycle failures."""

    def __init__(self, message: str, *, surviving_process: bool = False) -> None:
        super().__init__(message)
        self.surviving_process = surviving_process


@dataclass
class ProcessStatus:
    running: bool
    pid: Optional[int] = None
    port_open: bool = False

    def to_dict(self) -> dict:
        return {"running": self.running, "pid": self.pid, "port_open": self.port_open}


class BlenderMcpManager:
    def __init__(
        self,
        state_dir: Path | str,
        *,
        port: int = 9876,
        bind: str = "127.0.0.1",
        blender_path: str = "",
        addon_ref: str = DEFAULT_ADDON_REF,
        addon_sha256: str = DEFAULT_ADDON_SHA256,
        addon_file: str = "",
        allow_remote: bool = False,
    ) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.port = int(port)
        self.bind = (bind or "127.0.0.1").strip()
        self.blender_path = (blender_path or "").strip()
        self.addon_ref = addon_ref
        self.addon_sha256 = (addon_sha256 or "").strip().lower()
        self.addon_file = (addon_file or "").strip()
        self.allow_remote = allow_remote
        self.addon_path = self.state_dir / "addon.py"
        self.launcher_path = self.state_dir / "launcher.py"
        self.pid_file = self.state_dir / "blender-mcp.pid"
        self.log_file = self.state_dir / "blender-mcp.log"
        self.launch_lock_file = (
            self.state_dir.parent / f".{self.state_dir.name}.launch.lock"
        )
        self._untracked_pid: Optional[int] = None

    # ── resolution ───────────────────────────────────────────────────
    def blender_binary(self) -> Optional[str]:
        """Explicit override > macOS app bundle > `blender` on PATH."""
        if self.blender_path:
            path = Path(self.blender_path).expanduser()
            return str(path) if path.exists() else None
        if platform.system() == "Darwin":
            bundled = Path("/Applications/Blender.app/Contents/MacOS/Blender")
            if bundled.exists():
                return str(bundled)
        return shutil.which("blender")

    def _bind_is_loopback(self) -> bool:
        return self.bind in ("127.0.0.1", "localhost", "::1")

    # ── preflight (read-only) ────────────────────────────────────────
    def preflight(self) -> PreflightResult:
        result = PreflightResult()
        add_lifecycle_preflight(result, _lifecycle_support_error(), (_OK, _FAIL))
        binary = self.blender_binary()
        if binary:
            result.add("blender", _OK, f"Blender binary: {binary}")
        else:
            result.add(
                "blender", _FAIL,
                "No Blender install found — install Blender (blender.org) or "
                "set BLENDER_MCP_BLENDER_PATH to its binary. Atlas manages the "
                "MCP bridge, not the Blender application itself.",
            )
        if self._bind_is_loopback():
            result.add("bind", _OK, f"loopback bind {self.bind}")
        elif self.allow_remote:
            result.add(
                "bind", _WARN,
                f"non-loopback bind {self.bind} with BLENDER_MCP_ALLOW_REMOTE=true "
                f"— execute_code runs arbitrary Python; make sure the network "
                f"boundary is yours.",
            )
        else:
            result.add(
                "bind", _FAIL,
                f"non-loopback bind {self.bind} refused — the bridge executes "
                f"arbitrary Python. Set BLENDER_MCP_ALLOW_REMOTE=true only if "
                f"you own the network boundary.",
            )
        if self.addon_file:
            override = Path(self.addon_file).expanduser()
            if override.exists():
                result.add("addon", _WARN, f"local add-on override: {override} (no sha pin)")
            else:
                result.add("addon", _FAIL, f"BLENDER_MCP_ADDON_FILE {override} does not exist")
        elif self.addon_path.exists() and self._sha256(self.addon_path) == self.addon_sha256:
            result.add("addon", _OK, f"pinned add-on provisioned ({self.addon_ref[:12]})")
        else:
            result.add(
                "addon", _OK,
                f"pinned add-on will be downloaded on install "
                f"(ahujasid/blender-mcp@{self.addon_ref[:12]})",
            )
        status = self.status()
        if status.running:
            result.add("process", _OK, f"managed process already running (pid {status.pid})")
        elif self._port_in_use():
            result.add(
                "process", _WARN,
                f"port {self.port} is in use by an unmanaged process — a GUI "
                f"Blender with the add-on connected? stop it or change "
                f"BLENDER_MCP_LOCALHOST_PORT.",
            )
        else:
            result.add("process", _OK, "port free; not yet running")
        return result

    # ── provisioning ─────────────────────────────────────────────────
    def install(self) -> None:
        """Idempotently provision the pinned add-on + launcher into the state
        dir. Raises BlenderMcpError on a sha mismatch (never installs
        unverified code that will later execute arbitrary Python)."""
        with self._launch_guard():
            self._install_locked()

    def _install_locked(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.addon_file:
            source = Path(self.addon_file).expanduser()
            if not source.exists():
                raise BlenderMcpError(f"BLENDER_MCP_ADDON_FILE {source} does not exist")
            shutil.copyfile(source, self.addon_path)
        elif not (
            self.addon_path.exists() and self._sha256(self.addon_path) == self.addon_sha256
        ):
            url = ADDON_URL_TEMPLATE.format(ref=self.addon_ref)
            tmp = self.addon_path.with_name(self.addon_path.name + ".tmp")
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    tmp.write_bytes(response.read())
            except (urllib.error.URLError, OSError) as exc:
                tmp.unlink(missing_ok=True)
                raise BlenderMcpError(f"could not download pinned add-on {url}: {exc}") from exc
            actual = self._sha256(tmp)
            if actual != self.addon_sha256:
                tmp.unlink(missing_ok=True)
                raise BlenderMcpError(
                    f"pinned add-on sha256 mismatch (expected {self.addon_sha256[:12]}…, "
                    f"got {actual[:12]}…) — refusing to install unverified code. "
                    f"Move BLENDER_MCP_ADDON_REF and _SHA256 together."
                )
            os.replace(tmp, self.addon_path)
        self.launcher_path.write_text(_LAUNCHER, encoding="utf-8")

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self, *, wait_timeout: float = 45.0) -> ProcessStatus:
        with self._launch_guard():
            return self._start_locked(wait_timeout)

    @contextmanager
    def _launch_guard(self):
        self.state_dir.parent.mkdir(parents=True, exist_ok=True)
        require_lifecycle_support(_lifecycle_support_error(), BlenderMcpError)
        with open(self.launch_lock_file, "w", encoding="utf-8") as lock:
            acquire_lifecycle_lock(
                lock, fcntl, ("managed Blender MCP", BlenderMcpError), time,
            )
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _start_locked(self, wait_timeout: float) -> ProcessStatus:
        if not self._bind_is_loopback() and not self.allow_remote:
            raise BlenderMcpError(
                f"refusing non-loopback bind {self.bind} (execute_code runs "
                f"arbitrary Python); set BLENDER_MCP_ALLOW_REMOTE=true to override"
            )
        from services import refuse_untrusted_tracked_pid
        refuse_untrusted_tracked_pid(
            (self._read_pid() or self._untracked_pid, self.pid_file),
            self._managed_process_alive, self._pid_is_stranger,
            ("Blender MCP", BlenderMcpError),
        )
        status = self.status()
        if status.running:
            return await_owned_process_readiness(
                self,
                status,
                wait_timeout,
                ("Blender MCP", self.bind, self.port, BlenderMcpError, time),
            )
        refuse_occupied_port(
            status, self._port_in_use,
            (
                f"port {self.port} is already in use by an unmanaged process; "
                "stop it or change BLENDER_MCP_LOCALHOST_PORT",
                BlenderMcpError,
            ),
        )
        binary = self.blender_binary()
        if binary is None:
            raise BlenderMcpError(
                "no Blender install found — install Blender or set BLENDER_MCP_BLENDER_PATH"
            )
        if not self.addon_path.exists() or not self.launcher_path.exists():
            raise BlenderMcpError("state dir not provisioned — run install first")
        addon = (
            str(Path(self.addon_file).expanduser()) if self.addon_file else str(self.addon_path)
        )
        with open(self.log_file, "ab") as log_handle:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                [
                    binary, "--background",
                    "--python", str(self.launcher_path),
                    "--", addon, str(self.port), self.bind,
                ],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self._untracked_pid = process.pid
        # Atomic + stamped with the start time, so the ownership guard
        # has an identity to compare and a torn read cannot silently
        # disable it.
        from services.managed_host import (
            ManagedHostManager as _MHM,
            compensate_failed_launch as _compensate,
            raise_launch_recording_failure as _raise_recording_failure,
            require_process_start_time as _require_started,
            write_pid_file_with_identity as _write_pid,
        )

        if process.poll() is None:
            try:
                _started = _require_started(process.pid, _MHM._process_start_time)
                _write_pid(self.pid_file, process.pid, _started)
            except BaseException as exc:
                outcome = _compensate(
                    process.pid,
                    self.pid_file,
                    lambda: _MHM._terminate_untracked(process),
                )
                if outcome.terminated:
                    self._untracked_pid = None
                _raise_recording_failure(
                    exc,
                    process.pid,
                    outcome,
                    ("managed Blender process identity", BlenderMcpError),
                )
        ready = self._await_spawned_readiness(process, wait_timeout)
        if ready is not None:
            self._untracked_pid = None
            return ready
        tail = self._log_tail()
        self._stop_locked()
        _pid, may_survive = tracked_process_may_survive(self)
        raise BlenderMcpError(
            f"headless Blender did not become healthy on {self.bind}:{self.port} within "
            f"{wait_timeout:.0f}s. Log tail:\n{tail}",
            surviving_process=may_survive,
        )

    def _await_spawned_readiness(
        self, process: subprocess.Popen, wait_timeout: float
    ) -> ProcessStatus | None:
        if await_spawned_process_readiness(
            self,
            process,
            wait_timeout,
            (time, "headless Blender", BlenderMcpError),
        ):
            return ProcessStatus(running=True, pid=process.pid, port_open=True)
        return None

    def _spawned_endpoint_owned(self, pid: int) -> bool:
        return process_group_owns_tcp_listener(pid, self.bind, self.port)

    def stop(self) -> bool:
        with self._launch_guard():
            return self._stop_locked()

    def _stop_locked(self) -> bool:
        pid = self._read_pid() or self._untracked_pid
        if pid is None:
            _pid, evidence_may_survive = tracked_process_may_survive(self)
            if evidence_may_survive:
                return False
            self.pid_file.unlink(missing_ok=True)
            self._untracked_pid = None
            return True
        if not self._pid_alive(pid):
            group_survives = self._managed_process_alive(pid)
            stopped = (
                pid is None
                or not group_survives
                or (
                    self._untracked_pid == pid
                    and self._sweep_orphaned_group(pid)
                )
            )
            if stopped:
                self.pid_file.unlink(missing_ok=True)
                self._untracked_pid = None
            return stopped
        # PID-reuse guard: signal only when start-time identity positively
        # proves ownership; unknown or mismatched evidence is preserved.
        if self._pid_is_stranger(pid):
            # Ownership is mismatched or unknowable. Preserve the pid file for
            # manual inspection; never signal or silently orphan the process.
            return False
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return False
        for _ in range(20):
            if not self._managed_process_alive(pid):
                self.pid_file.unlink(missing_ok=True)
                self._untracked_pid = None
                return True
            time.sleep(0.25)
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
        for _ in range(20):  # SIGKILL is not instantaneous — grant a grace window
            if not self._managed_process_alive(pid):
                self.pid_file.unlink(missing_ok=True)
                self._untracked_pid = None
                return True
            time.sleep(0.25)
        stopped = not self._managed_process_alive(pid)
        if stopped:
            self.pid_file.unlink(missing_ok=True)
            self._untracked_pid = None
        # a failed stop KEEPS the pid file so the process is not orphan-tracked
        return stopped

    def status(self) -> ProcessStatus:
        pid = self._read_pid()
        running = (
            pid is not None
            and self._pid_alive(pid)
            and not self._pid_is_stranger(pid)
        )
        return ProcessStatus(
            running=running, pid=pid if running else None, port_open=self._port_in_use()
        )

    def health(self, *, timeout: float = 10.0) -> dict:
        """TCP + JSON round-trip: a real get_scene_info through the bridge."""
        try:
            with socket.create_connection((self._probe_host(), self.port), timeout=timeout) as conn:
                conn.sendall(json.dumps({"type": "get_scene_info", "params": {}}).encode())
                conn.settimeout(timeout)
                buffer = b""
                while True:
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    buffer += chunk
                    try:
                        payload = json.loads(buffer.decode())
                    except ValueError:
                        continue
                    if not isinstance(payload, Mapping):
                        return {
                            "reachable": False,
                            "error": "response JSON was not an object",
                        }
                    result = payload.get("result")
                    if payload.get("status") != "success" or not isinstance(
                        result, Mapping
                    ):
                        return {
                            "reachable": False,
                            "status": payload.get("status"),
                            "error": "Blender MCP returned an unsuccessful response",
                        }
                    return {
                        "reachable": True,
                        "status": payload.get("status"),
                        "objects": result.get("object_count"),
                    }
        except OSError:
            pass
        return {"reachable": False}

    def ensure_running(self) -> tuple[ProcessStatus, bool]:
        """preflight → install → start. Returns (status, created_by_us)."""
        result = self.preflight()
        if not result.ok:
            failures = "; ".join(
                c["detail"] for c in result.checks if c["status"] == _FAIL
            )
            raise BlenderMcpError(f"preflight failed: {failures}")
        with self._launch_guard():
            already = self.status().running
            self._install_locked()
            status = self._start_locked(45.0)
        return status, not already

    def remove(self) -> None:
        with self._launch_guard():
            self._stop_locked()
            pid, may_survive = tracked_process_may_survive(self)
            if may_survive:
                detail = f"pid {pid}" if pid is not None else "retained PID evidence"
                raise BlenderMcpError(
                    "refusing to remove managed Blender MCP state while its tracked "
                    f"{detail} may still be alive"
                )
            remove_state_directory(
                self.state_dir,
                ("managed Blender MCP state directory", BlenderMcpError),
            )

    # ── helpers ──────────────────────────────────────────────────────
    def _probe_host(self) -> str:
        return {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(
            self.bind, self.bind
        )

    def _port_in_use(self) -> bool:
        try:
            with socket.create_connection((self._probe_host(), self.port), timeout=1.0):
                return True
        except OSError:
            return False

    def _read_pid(self) -> Optional[int]:
        try:
            # FIRST LINE only — `write_pid_file_with_identity` appends
            # `start_utc=<ps lstart>` after the pid. Parsing the whole file
            # makes `int()` raise on every launch where `ps` answers, which
            # inverts the entire lifecycle (status says not-running while it
            # runs, stop deletes the record and leaves the process alive).
            first = self.pid_file.read_text(encoding="utf-8").splitlines()[0]
            pid = int(first.strip())
        except (OSError, ValueError, IndexError):
            return None
        # A pid must be POSITIVE: termination escalates to `os.killpg`, where
        # 0 means the CALLER's process group and -1 is a broadcast.
        return pid if pid > 0 else None

    def _pid_is_stranger(self, pid: int) -> bool:
        """True unless ``pid`` can be positively proven to be our process.

        Uses `(pid, start time)` — the identity the generic managed-host
        framework settled on — via its shared implementation.

        This previously substring-matched the process argv. Those markers are
        generic strings, not an identity, so after a crash left the pid file
        behind and the OS recycled the pid onto any process whose argv
        contains one, termination escalated to `os.killpg(pid, SIGKILL)` on
        the stranger's whole process group. An argv fails in the other
        direction too — a wrapper script, `exec`, `setproctitle` or a
        gunicorn/celery master rewrites it.

        Unknown ownership refuses teardown; a manual cleanup is safer than
        signalling an unrelated recycled PID.
        """
        from services.managed_host import ManagedHostManager as _MHM, pid_is_stranger

        return pid_is_stranger(pid, self.pid_file, _MHM._process_start_time)

    @staticmethod
    def _reap_child(pid: int) -> None:
        """Reap an exited child so it stops answering ``kill(0)``.

        Mirrors the identically-named helper in comfyui_mps_manager and
        vllm_metal_manager, which blender-mcp was missing.
        """
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass  # not our child — the signal probe below is the answer

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        # A child of THIS process that has exited but not been waited on is a
        # zombie, and a zombie still answers kill(0). Without the reap, stop()
        # polls its full 10s grace window and then reports failure for a
        # process it just killed — leaving a stale pid file behind.
        BlenderMcpManager._reap_child(pid)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, but ownership is untrusted
        return True

    def _managed_process_alive(self, pid: int) -> bool:
        from services.managed_host import ManagedHostManager as _MHM

        return self._pid_alive(pid) or _MHM._group_survives(pid)

    @staticmethod
    def _sweep_orphaned_group(pid: int) -> bool:
        from services.managed_host import ManagedHostManager as _MHM

        return _MHM._sweep_orphaned_group(pid)

    def _log_tail(self, lines: int = 12) -> str:
        try:
            content = self.log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "(no log)"
        return "\n".join(content.splitlines()[-lines:])

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()


def manager_from_env(env: dict[str, str]) -> BlenderMcpManager:
    def _get(key: str, default: str = "") -> str:
        return (env.get(key, "") or "").strip() or default

    raw_port = _get("BLENDER_MCP_LOCALHOST_PORT", "9876")
    if not raw_port.isdigit():  # malformed env must not traceback the launch/CLI
        raw_port = "9876"
    return BlenderMcpManager(
        state_dir=_get("BLENDER_MCP_STATE_DIR", "~/.atlas/blender-mcp"),
        port=int(raw_port),
        bind=_get("BLENDER_MCP_BIND", "127.0.0.1"),
        blender_path=_get("BLENDER_MCP_BLENDER_PATH"),
        addon_ref=_get("BLENDER_MCP_ADDON_REF", DEFAULT_ADDON_REF),
        addon_sha256=_get("BLENDER_MCP_ADDON_SHA256", DEFAULT_ADDON_SHA256),
        addon_file=_get("BLENDER_MCP_ADDON_FILE"),
        allow_remote=_get("BLENDER_MCP_ALLOW_REMOTE", "false").lower() == "true",
    )
