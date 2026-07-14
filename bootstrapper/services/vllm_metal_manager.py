"""Atlas-managed Apple-Silicon / Metal vLLM host process (#379).

Docker Desktop on macOS cannot pass Metal into a Linux container, so the
``managed-localhost`` vLLM Metal source runs a native vLLM process on the HOST
(via the community-maintained ``vllm-metal`` MLX plugin) and exposes its
OpenAI-compatible API on a fixed loopback port; containers reach it through
``host.docker.internal`` exactly like an unmanaged localhost source. Only
LiteLLM ever registers those models — backend / Open WebUI / Hermes / n8n /
JupyterHub keep talking to LiteLLM, never to vLLM directly.

This module owns that host lifecycle: a vLLM-Metal-specific preflight
(OS/arch, Python 3.12, unified memory, plugin importability, per-model
quantization), an idempotent pinned pip install/update into an Atlas-owned
Python 3.12 virtualenv, and start/stop/status/health with pid/log/status
files. One process per host (a single vLLM instance already saturates the
Apple-Silicon GPU; a second is net-negative), reusing an existing Hugging Face
cache directory so no weights are duplicated.

All host effects (subprocess, platform, socket, HTTP, filesystem) go through
thin stdlib calls so the manager is fully unit-testable with mocks on generic
Linux CI; a real Metal round-trip is a separate ``live`` Darwin-arm64 test.
Mirrors the #335 ComfyUI managed-MPS lifecycle intentionally so the two
managed-host sources stay structurally consistent.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

try:  # Native Windows can import this module for a no-op disabled-source stop.
    import fcntl
except ImportError:  # pragma: no cover - exercised only by native Windows Python
    fcntl = None  # type: ignore[assignment]

# The vLLM Metal hardware plugin (Apache-2.0). Installed from PyPI; it pulls the
# matching vLLM core. Requirements: macOS arm64 + Python 3.12.
# https://github.com/vllm-project/vllm-metal
_VLLM_METAL_PACKAGE = "vllm-metal"
_DEFAULT_PLUGIN_VERSION = "0.3.0"
_DEFAULT_PYTHON = "python3.12"
_REQUIRED_PY = (3, 12)
_LOCK_TIMEOUT_SECONDS = 30.0

# vLLM Metal serves BF16/FP16 cleanly; some aggressive quantizations are not yet
# supported by the MLX backend and would fail at load. Warn (not fail) so an
# operator can still try, but the signal is explicit. Matches the #335 posture.
_METAL_UNSUPPORTED_QUANT = frozenset({"gptq", "awq", "fp8", "fp8_e4m3fn", "fp8_e5m2", "float8"})

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"
_SKIPPED = "skipped"


@dataclass
class PreflightResult:
    status: str  # ok | warn | fail
    checks: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != _FAIL

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append({"name": name, "status": status, "detail": detail})
        # fail dominates warn dominates ok; skipped never lowers the rollup.
        order = {_OK: 0, _SKIPPED: 0, _WARN: 1, _FAIL: 2}
        if order[status] > order[self.status]:
            self.status = status

    def to_dict(self) -> dict:
        return {"status": self.status, "checks": self.checks}


@dataclass
class ProcessStatus:
    running: bool
    pid: Optional[int]
    port: int
    installed_version: Optional[str] = None
    model: Optional[str] = None
    log_file: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "pid": self.pid,
            "port": self.port,
            "installed_version": self.installed_version,
            "model": self.model,
            "log_file": self.log_file,
        }


class VllmMetalError(RuntimeError):
    """A managed vLLM-Metal lifecycle failure (unsupported host, install/launch error)."""

    def __init__(self, message: str, *, surviving_process: bool = False) -> None:
        super().__init__(message)
        self.surviving_process = surviving_process


class VllmMetalManager:
    def __init__(
        self,
        state_dir: Path | str,
        *,
        port: int = 8000,
        model: str = "Qwen/Qwen2.5-7B-Instruct",
        plugin_version: str = _DEFAULT_PLUGIN_VERSION,
        core_version: str = "",
        python_bin: str = _DEFAULT_PYTHON,
        hf_cache_dir: Path | str | None = None,
        min_memory_gb: int = 16,
        listen: str = "127.0.0.1",
    ) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.port = int(port)
        self.model = model
        self.plugin_version = plugin_version
        self.core_version = core_version
        self.python_bin = python_bin or _DEFAULT_PYTHON
        self.hf_cache_dir = Path(hf_cache_dir).expanduser() if hf_cache_dir else None
        self.min_memory_gb = int(min_memory_gb)
        self.listen = listen
        self.venv_dir = self.state_dir / "venv"
        self.pid_file = self.state_dir / "vllm-metal.pid"
        self.log_file = self.state_dir / "vllm-metal.log"
        self.status_file = self.state_dir / "status.json"
        self.launch_lock_file = (
            self.state_dir.parent / f".{self.state_dir.name}.launch.lock"
        )
        self._untracked_pid: Optional[int] = None

    # ── venv python ──────────────────────────────────────────────────
    @property
    def venv_python(self) -> Path:
        # POSIX layout (macOS). Windows is out of scope (Metal is macOS-only).
        return self.venv_dir / "bin" / "python"

    # ── preflight (dry-run safe) ─────────────────────────────────────
    def preflight(self, *, models: Optional[list[dict]] = None) -> PreflightResult:
        """vLLM-Metal-specific managed-host probe. Never launches anything."""
        result = PreflightResult(status=_OK)

        system = platform.system()
        if system == "Darwin":
            result.add("os", _OK, "macOS host detected")
        else:
            result.add(
                "os", _FAIL,
                f"managed-localhost vLLM Metal requires macOS (Metal); host is {system or 'unknown'}",
            )

        machine = platform.machine().lower()
        if machine in ("arm64", "aarch64"):
            result.add("arch", _OK, f"Apple Silicon ({machine})")
        else:
            result.add(
                "arch", _FAIL,
                f"managed-localhost vLLM Metal requires Apple Silicon (arm64); host is {machine or 'unknown'}",
            )

        # vLLM Metal requires Python 3.12 specifically. Probe the interpreter.
        py_path = shutil.which(self.python_bin)
        if not py_path:
            result.add(
                "python", _FAIL,
                f"{self.python_bin} not found — vLLM Metal requires Python 3.12 "
                f"(set VLLM_METAL_PYTHON to a 3.12 interpreter)",
            )
        else:
            version = self._python_version(py_path)
            if version is None:
                result.add("python", _WARN, f"could not determine {self.python_bin} version")
            elif version[:2] == _REQUIRED_PY:
                result.add("python", _OK, f"{self.python_bin} is {version[0]}.{version[1]}")
            else:
                result.add(
                    "python", _FAIL,
                    f"{self.python_bin} is {version[0]}.{version[1]}; vLLM Metal requires Python 3.12",
                )

        mem_gb = self._unified_memory_gb()
        if mem_gb is None:
            result.add("memory", _SKIPPED, "could not read unified memory")
        elif mem_gb >= self.min_memory_gb:
            result.add("memory", _OK, f"{mem_gb} GiB unified memory (>= {self.min_memory_gb})")
        else:
            result.add(
                "memory", _WARN,
                f"{mem_gb} GiB unified memory is below the {self.min_memory_gb} GiB floor; "
                "7B+ models may OOM under paged attention",
            )

        # vLLM importability is only meaningful once the venv exists.
        if self.venv_python.exists():
            importable = self._vllm_importable()
            if importable is True:
                result.add("vllm", _OK, "vllm imports in the managed venv")
            elif importable is False:
                result.add("vllm", _FAIL, "the managed venv exists but vllm is not importable")
            else:
                result.add("vllm", _WARN, "could not probe vllm in the venv")
        else:
            result.add("vllm", _SKIPPED, "venv not installed yet — run install first")

        for model in models or []:
            quant = str(model.get("quantization", "")).lower().replace("-", "_")
            name = model.get("name", "?")
            if quant in _METAL_UNSUPPORTED_QUANT:
                result.add(
                    f"model:{name}", _WARN,
                    f"quantization {model.get('quantization')!r} is not supported on the "
                    f"MLX/Metal backend — use a BF16/FP16 variant",
                )
            elif quant:
                result.add(f"model:{name}", _OK, f"quantization {model.get('quantization')} is Metal-safe")

        return result

    def _python_version(self, py_path: str) -> Optional[tuple[int, int, int]]:
        try:
            out = subprocess.run(
                [py_path, "-c",
                 "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        parts = out.stdout.strip().split(".")
        if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
            return None
        nums = [int(p) for p in parts[:3]]
        while len(nums) < 3:
            nums.append(0)
        return (nums[0], nums[1], nums[2])

    def _unified_memory_gb(self) -> Optional[int]:
        if platform.system() != "Darwin":
            return None
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if out.returncode == 0 and out.stdout.strip().isdigit():
                return int(out.stdout.strip()) // (1024 ** 3)
        except (OSError, subprocess.SubprocessError):
            return None
        return None

    def _vllm_importable(self) -> Optional[bool]:
        try:
            out = subprocess.run(
                [str(self.venv_python), "-c", "import vllm"],
                capture_output=True, text=True, timeout=60, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.returncode == 0

    # ── install / update (idempotent) ────────────────────────────────
    def _pip_spec(self) -> list[str]:
        """Pinned pip targets. The plugin pulls its matching vLLM core; an
        explicit core pin is added only when the operator sets one."""
        specs = [f"{_VLLM_METAL_PACKAGE}=={self.plugin_version}"]
        if self.core_version:
            specs.append(f"vllm=={self.core_version}")
        return specs

    def install(self, *, update: bool = False) -> None:
        pre = self.preflight()
        if not pre.ok:
            fails = [c for c in pre.checks if c["status"] == _FAIL]
            raise VllmMetalError(
                "preflight failed: " + "; ".join(f"{c['name']}: {c['detail']}" for c in fails)
            )
        with self._launch_guard():
            self._install_locked(update=update)

    def _install_locked(self, *, update: bool = False) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

        if not self.venv_python.exists():
            self._run([self.python_bin, "-m", "venv", str(self.venv_dir)])
            self._run([str(self.venv_python), "-m", "pip", "install", "--upgrade", "pip"])
            self._run([str(self.venv_python), "-m", "pip", "install", *self._pip_spec()])
        elif update:
            # Re-pin to the requested versions (a bump or a rollback both land
            # here — pip installs the exact ``==`` spec either direction).
            self._run([str(self.venv_python), "-m", "pip", "install", "--upgrade", *self._pip_spec()])

        self._write_status(installed_version=self.plugin_version)

    # ── start / stop / status ────────────────────────────────────────
    def start(self) -> ProcessStatus:
        status, _created = self.start_with_ownership()
        return status

    def start_with_ownership(self) -> tuple[ProcessStatus, bool]:
        """Start atomically and report whether this call created the process."""
        with self._launch_guard():
            return self._start_locked()

    def _start_locked(self) -> tuple[ProcessStatus, bool]:
        existing = self.status()
        if existing.running:
            return existing, False  # idempotent — one process per host
        if not self.venv_python.exists():
            raise VllmMetalError("vLLM Metal venv is not installed — run install first")
        if self._port_in_use():
            raise VllmMetalError(
                f"port {self.port} is already in use by another process — "
                f"free it or set VLLM_METAL_LOCALHOST_PORT"
            )
        args = [
            str(self.venv_python), "-m", "vllm.entrypoints.openai.api_server",
            "--host", self.listen, "--port", str(self.port),
            "--model", self.model,
        ]
        self.state_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        if self.hf_cache_dir:
            # Reuse an existing HF cache so weights are never re-downloaded.
            env["HF_HOME"] = str(self.hf_cache_dir)
        log = open(self.log_file, "ab")  # noqa: SIM115 - handed to the child
        try:
            proc = subprocess.Popen(
                args, cwd=str(self.state_dir), stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True, env=env,
            )
        finally:
            log.close()
        self._untracked_pid = proc.pid
        try:
            self.pid_file.write_text(str(proc.pid), encoding="utf-8")
            self._write_status(installed_version=self.plugin_version, pid=proc.pid)
        except BaseException as exc:
            if self._terminate_pid(proc.pid):
                self._clear_pid()
                self._untracked_pid = None
                raise VllmMetalError(
                    f"failed to record managed vLLM Metal pid {proc.pid}; the "
                    "child was terminated"
                ) from exc
            raise VllmMetalError(
                f"failed to record managed vLLM Metal pid {proc.pid}, and the "
                "child could not be terminated; retry ./stop.sh or terminate "
                "that pid",
                surviving_process=True,
            ) from exc
        self._untracked_pid = None
        return (
            ProcessStatus(
                running=True, pid=proc.pid, port=self.port,
                installed_version=self.plugin_version, model=self.model,
                log_file=str(self.log_file),
            ),
            True,
        )

    @contextmanager
    def _launch_guard(self):
        """Serialize native installation/start decisions across launchers."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.launch_lock_file.open("a+", encoding="utf-8") as lock:
            if fcntl is None:
                yield
                return
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise VllmMetalError(
                            "timed out waiting for another managed vLLM Metal "
                            "lifecycle operation"
                        ) from exc
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def stop(self) -> bool:
        with self._launch_guard():
            return self._stop_locked()

    def _stop_locked(self) -> bool:
        pid = self._read_pid() or self._untracked_pid
        if pid is None or not self._pid_alive(pid):
            self._clear_pid()
            self._untracked_pid = None
            return False
        # PID-reuse guard: never SIGKILL a process the OS recycled onto our old
        # pid. Only signal when we cannot prove the pid belongs to a stranger.
        if self._pid_is_stranger(pid):
            self._clear_pid()
            self._write_status(installed_version=self.plugin_version, pid=None)
            self._untracked_pid = None
            return False
        if not self._terminate_pid(pid):
            # Honest: the process outlived SIGKILL (e.g. EPERM). Keep the pidfile
            # so a retry/operator can act; don't claim success.
            return False
        self._clear_pid()
        self._write_status(installed_version=self.plugin_version, pid=None)
        self._untracked_pid = None
        return True

    def _terminate_pid(self, pid: int) -> bool:
        try:
            os.kill(pid, signal.SIGINT)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if not self._pid_alive(pid):
                    break
                time.sleep(0.2)
            else:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
        return not self._pid_alive(pid)

    def status(self) -> ProcessStatus:
        pid = self._read_pid() or self._untracked_pid
        running = pid is not None and self._pid_alive(pid)
        version = None
        model = self.model
        if self.status_file.exists():
            try:
                payload = json.loads(self.status_file.read_text(encoding="utf-8"))
                version = payload.get("installed_version")
                model = payload.get("model") or self.model
            except (OSError, ValueError):
                version = None
        return ProcessStatus(
            running=running, pid=pid if running else None, port=self.port,
            installed_version=version, model=model, log_file=str(self.log_file),
        )

    def ensure_running(self) -> ProcessStatus:
        """Full launch path: preflight (fatal on fail) → install → start."""
        status, _created = self.ensure_running_with_ownership()
        return status

    def ensure_running_with_ownership(self) -> tuple[ProcessStatus, bool]:
        """Run the full launch path and atomically report process ownership."""
        pre = self.preflight()
        if not pre.ok:
            fails = [c for c in pre.checks if c["status"] == _FAIL]
            raise VllmMetalError(
                "unsupported host for managed-localhost vLLM Metal: "
                + "; ".join(f"{c['name']}: {c['detail']}" for c in fails)
            )
        with self._launch_guard():
            existing = self.status()
            if existing.running:
                return existing, False
            self._install_locked()
            return self._start_locked()

    def remove(self) -> None:
        """Stop the process and delete the Atlas-owned state directory."""
        with self._launch_guard():
            self._stop_locked()
            if self.status().running:
                raise VllmMetalError(
                    "refusing to remove managed vLLM Metal state while its "
                    "process is still running"
                )
            if self.state_dir.exists():
                shutil.rmtree(self.state_dir)

    # ── health ───────────────────────────────────────────────────────
    def health(self, *, timeout: float = 3.0) -> dict:
        # OpenAI-compatible surface: /v1/models lists the served model(s).
        url = f"http://{self.listen}:{self.port}/v1/models"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - loopback only
                body = resp.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - unreachable = cold/not-up
            return {"reachable": False, "models": [], "error": str(exc)}
        try:
            payload = json.loads(body)
        except ValueError:
            return {"reachable": True, "models": [], "error": "non-JSON /v1/models"}
        models = [str(m.get("id")) for m in (payload.get("data") or []) if m.get("id")]
        return {"reachable": True, "models": models}

    def wait_healthy(self, *, timeout: float = 120.0, interval: float = 2.0) -> dict:
        """Poll /v1/models until the server answers or ``timeout`` elapses.

        Returns the last health dict either way — the caller decides whether an
        unreachable result is fatal (usually not: vLLM loads weights lazily and
        containers retry, so a still-warming host is expected on a cold start).
        """
        deadline = time.monotonic() + timeout
        last = self.health()
        while not last.get("reachable") and time.monotonic() < deadline:
            time.sleep(interval)
            last = self.health()
        return last

    # ── low-level host helpers (mockable) ────────────────────────────
    def _run(self, cmd: list[str]) -> None:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise VllmMetalError(
                f"command failed ({' '.join(cmd[:3])}…): rc={result.returncode} "
                f"{(result.stderr or result.stdout or '').strip()[:400]}"
            )

    def _port_in_use(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((self.listen, self.port)) == 0

    def _read_pid(self) -> Optional[int]:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _clear_pid(self) -> None:
        try:
            self.pid_file.unlink()
        except OSError:
            pass

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _pid_is_stranger(self, pid: int) -> bool:
        """Best-effort: True only when we can PROVE ``pid`` is NOT our vLLM.

        Reads the process command line via ``ps``. If it clearly belongs to some
        other program (no vLLM api_server / state dir in the argv) we refuse to
        signal it. When ``ps`` is unavailable or the output is ambiguous we
        return False (proceed) — never block teardown on an unknowable probe.
        """
        try:
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False  # can't tell — proceed
        cmdline = (out.stdout or "").strip()
        if out.returncode != 0 or not cmdline:
            return False  # can't tell — proceed
        markers = ("vllm.entrypoints", "vllm", str(self.venv_dir), str(self.state_dir))
        return not any(marker in cmdline for marker in markers)

    def _write_status(self, *, installed_version: Optional[str], pid: Optional[int] = None) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "installed_version": installed_version,
            "port": self.port,
            "model": self.model,
            "pid": pid,
        }
        self.status_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def manager_from_env(env: dict[str, str]) -> VllmMetalManager:
    """Build a manager from resolved .env values."""
    return VllmMetalManager(
        state_dir=env.get("VLLM_METAL_STATE_DIR", "~/.atlas/vllm-metal"),
        port=int(env.get("VLLM_METAL_LOCALHOST_PORT", "8000") or "8000"),
        model=env.get("VLLM_METAL_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
        plugin_version=env.get("VLLM_METAL_PLUGIN_VERSION", _DEFAULT_PLUGIN_VERSION) or _DEFAULT_PLUGIN_VERSION,
        core_version=env.get("VLLM_METAL_CORE_VERSION", "") or "",
        python_bin=env.get("VLLM_METAL_PYTHON", _DEFAULT_PYTHON) or _DEFAULT_PYTHON,
        hf_cache_dir=env.get("VLLM_METAL_MODELS_PATH") or None,
        min_memory_gb=int(env.get("VLLM_METAL_MIN_MEMORY_GB", "16") or "16"),
    )
