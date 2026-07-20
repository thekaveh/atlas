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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Pinned upstream add-on (ahujasid/blender-mcp). Override via
# BLENDER_MCP_ADDON_REF / BLENDER_MCP_ADDON_SHA256 when deliberately moving
# the pin; both must move together.
DEFAULT_ADDON_REF = "6641189231caf3752302ae20591bc87fda85fc4e"
DEFAULT_ADDON_SHA256 = "bba60831f5f89a74deda0294b131668a086cf46eb35a6a01abbd0d21d9e92630"
ADDON_URL_TEMPLATE = "https://raw.githubusercontent.com/ahujasid/blender-mcp/{ref}/addon.py"

_OK, _WARN, _FAIL, _SKIPPED = "ok", "warn", "fail", "skipped"

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
server.socket = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
server.socket.setsockopt(socket_mod.SOL_SOCKET, socket_mod.SO_REUSEADDR, 1)
server.socket.bind((server.host, server.port))
server.socket.listen(1)
server.server_thread = threading.Thread(target=server._server_loop, daemon=True)
server.server_thread.start()
print(f"atlas-blender-mcp: serving on {BIND}:{PORT}", flush=True)

while True:
    fn = main_q.get()
    fn()
'''


class BlenderMcpError(RuntimeError):
    """Raised for managed blender-mcp lifecycle failures."""


@dataclass
class PreflightResult:
    status: str = _OK
    checks: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != _FAIL

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append({"name": name, "status": status, "detail": detail})
        if status == _FAIL:
            self.status = _FAIL
        elif status == _WARN and self.status == _OK:
            self.status = _WARN

    def to_dict(self) -> dict:
        return {"status": self.status, "checks": list(self.checks)}


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
        if not self._bind_is_loopback() and not self.allow_remote:
            raise BlenderMcpError(
                f"refusing non-loopback bind {self.bind} (execute_code runs "
                f"arbitrary Python); set BLENDER_MCP_ALLOW_REMOTE=true to override"
            )
        status = self.status()
        if status.running:
            return status
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
        self.pid_file.write_text(str(process.pid), encoding="utf-8")
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            if self._port_in_use():
                return ProcessStatus(running=True, pid=process.pid, port_open=True)
            if process.poll() is not None:
                break
            time.sleep(0.5)
        tail = self._log_tail()
        self.stop()
        raise BlenderMcpError(
            f"headless Blender did not open {self.bind}:{self.port} within "
            f"{wait_timeout:.0f}s. Log tail:\n{tail}"
        )

    def stop(self) -> bool:
        pid = self._read_pid()
        self.pid_file.unlink(missing_ok=True)
        if pid is None or not self._pid_alive(pid):
            return True
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return False
        for _ in range(20):
            if not self._pid_alive(pid):
                return True
            time.sleep(0.25)
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
        for _ in range(20):  # SIGKILL is not instantaneous — grant a grace window
            if not self._pid_alive(pid):
                return True
            time.sleep(0.25)
        return not self._pid_alive(pid)

    def status(self) -> ProcessStatus:
        pid = self._read_pid()
        running = pid is not None and self._pid_alive(pid)
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
                    return {
                        "reachable": True,
                        "status": payload.get("status"),
                        "objects": (payload.get("result") or {}).get("object_count"),
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
        already = self.status().running
        self.install()
        status = self.start()
        return status, not already

    def remove(self) -> None:
        self.stop()
        shutil.rmtree(self.state_dir, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────
    def _probe_host(self) -> str:
        return "127.0.0.1" if self.bind in ("0.0.0.0", "::") else self.bind

    def _port_in_use(self) -> bool:
        try:
            with socket.create_connection((self._probe_host(), self.port), timeout=1.0):
                return True
        except OSError:
            return False

    def _read_pid(self) -> Optional[int]:
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

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

    return BlenderMcpManager(
        state_dir=_get("BLENDER_MCP_STATE_DIR", "~/.atlas/blender-mcp"),
        port=int(_get("BLENDER_MCP_LOCALHOST_PORT", "9876")),
        bind=_get("BLENDER_MCP_BIND", "127.0.0.1"),
        blender_path=_get("BLENDER_MCP_BLENDER_PATH"),
        addon_ref=_get("BLENDER_MCP_ADDON_REF", DEFAULT_ADDON_REF),
        addon_sha256=_get("BLENDER_MCP_ADDON_SHA256", DEFAULT_ADDON_SHA256),
        addon_file=_get("BLENDER_MCP_ADDON_FILE"),
        allow_remote=_get("BLENDER_MCP_ALLOW_REMOTE", "false").lower() == "true",
    )
