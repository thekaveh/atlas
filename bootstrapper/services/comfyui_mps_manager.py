"""Atlas-managed Apple-Silicon / Metal (MPS) ComfyUI host process (#335).

Docker Desktop on macOS cannot pass Metal into a Linux container, so the
``managed-localhost-mps`` ComfyUI source runs a native ComfyUI process on the
HOST and exposes it on a fixed port; containers reach it via
``host.docker.internal`` exactly like the unmanaged ``localhost`` source. This
module owns that host lifecycle: a ComfyUI-specific preflight (OS/arch, unified
memory, Torch/MPS availability, per-model precision), an idempotent pinned
install/update into an Atlas-owned state directory, and start/stop/status/health
with pid/log/status files. One process per host (Apple-Silicon GPU work
serializes — a second instance is net-negative), reusing the existing host models
directory so no weights are duplicated.

All host effects (subprocess, platform, socket, HTTP, filesystem) go through
thin stdlib calls so the manager is fully unit-testable with mocks on generic
Linux CI; a real MPS round-trip is a separate ``live`` Darwin-arm64 test.
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
from pathlib import Path
from typing import Any, Optional

COMFYUI_REPO = "https://github.com/comfyanonymous/ComfyUI.git"

# fp8-scaled weights crash on MPS (Metal); BF16 is required (see #346/#335).
_MPS_UNSAFE_PRECISIONS = frozenset({"fp8", "fp8-scaled", "fp8_e4m3fn", "fp8_e5m2", "float8"})

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"
_SKIPPED = "skipped"

# Standard ComfyUI model subdirs mapped onto the reused host models dir so a
# managed process never re-downloads weights the user already has.
_MODEL_SUBDIRS = (
    "checkpoints", "vae", "loras", "clip", "clip_vision", "controlnet",
    "unet", "diffusion_models", "text_encoders", "upscale_models",
)


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
    installed_ref: Optional[str] = None
    log_file: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "pid": self.pid,
            "port": self.port,
            "installed_ref": self.installed_ref,
            "log_file": self.log_file,
        }


class ComfyUiMpsError(RuntimeError):
    """A managed-MPS lifecycle failure (unsupported host, install/launch error)."""


class ComfyUiMpsManager:
    def __init__(
        self,
        state_dir: Path | str,
        *,
        port: int = 8188,
        ref: str = "v0.27.0",
        models_path: Path | str | None = None,
        min_memory_gb: int = 16,
        listen: str = "127.0.0.1",
    ) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.port = int(port)
        self.ref = ref
        self.models_path = Path(models_path).expanduser() if models_path else None
        self.min_memory_gb = int(min_memory_gb)
        self.listen = listen
        self.repo_dir = self.state_dir / "ComfyUI"
        self.venv_dir = self.state_dir / "venv"
        self.pid_file = self.state_dir / "comfyui-mps.pid"
        self.log_file = self.state_dir / "comfyui-mps.log"
        self.status_file = self.state_dir / "status.json"
        self.model_paths_file = self.state_dir / "extra_model_paths.yaml"

    # ── venv python ──────────────────────────────────────────────────
    @property
    def venv_python(self) -> Path:
        # POSIX layout (macOS/Linux). Windows is out of scope (MPS is macOS-only).
        return self.venv_dir / "bin" / "python"

    # ── preflight (dry-run safe) ─────────────────────────────────────
    def preflight(self, *, models: Optional[list[dict]] = None) -> PreflightResult:
        """ComfyUI-specific managed-host probe. Never launches anything."""
        result = PreflightResult(status=_OK)

        system = platform.system()
        if system == "Darwin":
            result.add("os", _OK, "macOS host detected")
        else:
            result.add(
                "os", _FAIL,
                f"managed-localhost-mps requires macOS (Metal); host is {system or 'unknown'}",
            )

        machine = platform.machine().lower()
        if machine in ("arm64", "aarch64"):
            result.add("arch", _OK, f"Apple Silicon ({machine})")
        else:
            result.add(
                "arch", _FAIL,
                f"managed-localhost-mps requires Apple Silicon (arm64); host is {machine or 'unknown'}",
            )

        if shutil.which("git"):
            result.add("git", _OK, "git available")
        else:
            result.add("git", _FAIL, "git is required to check out the pinned ComfyUI ref")

        if shutil.which("python3"):
            result.add("python3", _OK, "python3 available")
        else:
            result.add("python3", _FAIL, "python3 is required to build the ComfyUI venv")

        mem_gb = self._unified_memory_gb()
        if mem_gb is None:
            result.add("memory", _SKIPPED, "could not read unified memory")
        elif mem_gb >= self.min_memory_gb:
            result.add("memory", _OK, f"{mem_gb} GiB unified memory (>= {self.min_memory_gb})")
        else:
            result.add(
                "memory", _WARN,
                f"{mem_gb} GiB unified memory is below the {self.min_memory_gb} GiB floor; "
                "large BF16 bundles may OOM",
            )

        # Torch/MPS is only meaningful once the venv exists.
        if self.venv_python.exists():
            available = self._torch_mps_available()
            if available is True:
                result.add("mps", _OK, "torch.backends.mps.is_available() is True")
            elif available is False:
                result.add("mps", _FAIL, "Torch is installed but MPS is unavailable")
            else:
                result.add("mps", _WARN, "could not probe Torch/MPS in the venv")
        else:
            result.add("mps", _SKIPPED, "venv not installed yet — run install first")

        for model in models or []:
            precision = str(model.get("precision", "")).lower().replace(".", "_")
            name = model.get("name", "?")
            if precision in _MPS_UNSAFE_PRECISIONS:
                result.add(
                    f"model:{name}", _WARN,
                    f"precision {model.get('precision')!r} crashes on MPS — use a BF16 variant",
                )
            elif precision:
                result.add(f"model:{name}", _OK, f"precision {model.get('precision')} is MPS-safe")

        return result

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

    def _torch_mps_available(self) -> Optional[bool]:
        try:
            out = subprocess.run(
                [str(self.venv_python), "-c",
                 "import torch,sys; sys.stdout.write('1' if torch.backends.mps.is_available() else '0')"],
                capture_output=True, text=True, timeout=60, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip() == "1"

    # ── install / update (idempotent) ────────────────────────────────
    def install(self, *, update: bool = False) -> None:
        pre = self.preflight()
        if not pre.ok:
            fails = [c for c in pre.checks if c["status"] == _FAIL]
            raise ComfyUiMpsError(
                "preflight failed: " + "; ".join(f"{c['name']}: {c['detail']}" for c in fails)
            )
        self.state_dir.mkdir(parents=True, exist_ok=True)

        if not self.repo_dir.exists():
            self._run(["git", "clone", COMFYUI_REPO, str(self.repo_dir)])
        elif update:
            self._run(["git", "-C", str(self.repo_dir), "fetch", "--tags", "--force"])
        # Pin to the requested ref. Idempotent checkout; if the ref isn't in the
        # local object store yet (e.g. COMFYUI_MPS_REF was bumped without
        # --update), fetch once and retry so a plain `start.sh`/install still
        # lands the new ref instead of failing on a missing object.
        self._checkout_ref()

        if not self.venv_python.exists():
            self._run(["python3", "-m", "venv", str(self.venv_dir)])
            self._run([str(self.venv_python), "-m", "pip", "install", "--upgrade", "pip"])
            # torch's default macos-arm64 wheel ships Metal/MPS support.
            self._run([str(self.venv_python), "-m", "pip", "install", "torch", "torchvision", "torchaudio"])
            self._run([str(self.venv_python), "-m", "pip", "install", "-r",
                       str(self.repo_dir / "requirements.txt")])
        elif update:
            # Re-pin Torch too so a security/compat bump lands without a manual
            # venv wipe, then reconcile ComfyUI's own requirements.
            self._run([str(self.venv_python), "-m", "pip", "install", "--upgrade",
                       "torch", "torchvision", "torchaudio"])
            self._run([str(self.venv_python), "-m", "pip", "install", "-r",
                       str(self.repo_dir / "requirements.txt")])

        self._write_model_paths()
        self._write_status(installed_ref=self.ref)

    def _checkout_ref(self) -> None:
        """Force-checkout ``self.ref``, fetching once if the ref is unknown locally."""
        try:
            self._run(["git", "-C", str(self.repo_dir), "checkout", "--force", self.ref])
        except ComfyUiMpsError:
            # Ref not present locally (bumped without --update) — fetch + retry.
            self._run(["git", "-C", str(self.repo_dir), "fetch", "--tags", "--force"])
            self._run(["git", "-C", str(self.repo_dir), "checkout", "--force", self.ref])

    def _write_model_paths(self) -> None:
        """Point ComfyUI at the existing host models dir (no duplicate weights)."""
        if not self.models_path:
            return
        base = str(self.models_path)
        lines = ["# AUTO-GENERATED by Atlas (#335) — reuse the host models dir.", "atlas_host:", f"  base_path: {base}"]
        for sub in _MODEL_SUBDIRS:
            lines.append(f"  {sub}: {base}/{sub}")
        self.model_paths_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── start / stop / status ────────────────────────────────────────
    def start(self) -> ProcessStatus:
        existing = self.status()
        if existing.running:
            return existing  # idempotent — one process per host
        if not self.venv_python.exists():
            raise ComfyUiMpsError("ComfyUI venv is not installed — run install first")
        if self._port_in_use():
            raise ComfyUiMpsError(
                f"port {self.port} is already in use by another process — "
                f"free it or set COMFYUI_MPS_LOCALHOST_PORT"
            )
        args = [
            str(self.venv_python), str(self.repo_dir / "main.py"),
            "--port", str(self.port), "--listen", self.listen,
        ]
        if self.model_paths_file.exists():
            args += ["--extra-model-paths-config", str(self.model_paths_file)]
        self.state_dir.mkdir(parents=True, exist_ok=True)
        log = open(self.log_file, "ab")  # noqa: SIM115 - handed to the child
        try:
            proc = subprocess.Popen(
                args, cwd=str(self.repo_dir), stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        finally:
            log.close()
        self.pid_file.write_text(str(proc.pid), encoding="utf-8")
        self._write_status(installed_ref=self.ref, pid=proc.pid)
        return ProcessStatus(
            running=True, pid=proc.pid, port=self.port,
            installed_ref=self.ref, log_file=str(self.log_file),
        )

    def stop(self) -> bool:
        pid = self._read_pid()
        if pid is None or not self._pid_alive(pid):
            self._clear_pid()
            return False
        # PID-reuse guard: the OS may have recycled a crashed ComfyUI's pid onto
        # an unrelated process. Only signal when we can't positively prove the
        # pid belongs to a stranger — never SIGKILL someone else's process.
        if self._pid_is_stranger(pid):
            self._clear_pid()
            self._write_status(installed_ref=self.ref, pid=None)
            return False
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
        # Reap if we are the parent (same-process start→stop); harmless otherwise.
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
        if self._pid_alive(pid):
            # Be honest: the process outlived SIGKILL (e.g. EPERM). Keep the
            # pidfile so a retry/operator can act; don't claim success.
            return False
        self._clear_pid()
        self._write_status(installed_ref=self.ref, pid=None)
        return True

    def status(self) -> ProcessStatus:
        pid = self._read_pid()
        running = pid is not None and self._pid_alive(pid)
        ref = None
        if self.status_file.exists():
            try:
                ref = json.loads(self.status_file.read_text(encoding="utf-8")).get("installed_ref")
            except (OSError, ValueError):
                ref = None
        return ProcessStatus(
            running=running, pid=pid if running else None, port=self.port,
            installed_ref=ref, log_file=str(self.log_file),
        )

    def ensure_running(self) -> ProcessStatus:
        """Full launch path: preflight (fatal on fail) → install → start."""
        pre = self.preflight()
        if not pre.ok:
            fails = [c for c in pre.checks if c["status"] == _FAIL]
            raise ComfyUiMpsError(
                "unsupported host for managed-localhost-mps: "
                + "; ".join(f"{c['name']}: {c['detail']}" for c in fails)
            )
        self.install()
        return self.start()

    def remove(self) -> None:
        """Stop the process and delete the Atlas-owned state directory."""
        self.stop()
        if self.state_dir.exists():
            shutil.rmtree(self.state_dir, ignore_errors=True)

    # ── health ───────────────────────────────────────────────────────
    def health(self, *, timeout: float = 3.0) -> dict:
        url = f"http://{self.listen}:{self.port}/system_stats"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - loopback only
                body = resp.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - unreachable = cold/not-up
            return {"reachable": False, "device": "unknown", "error": str(exc)}
        try:
            stats = json.loads(body)
        except ValueError:
            return {"reachable": True, "device": "unknown", "error": "non-JSON /system_stats"}
        devices = stats.get("devices") or []
        device_types = [str(d.get("type", "")).lower() for d in devices]
        # A non-CPU device (mps) is the acceptance signal.
        if any(t and t != "cpu" for t in device_types):
            device = next(t for t in device_types if t and t != "cpu")
        elif device_types:
            device = "cpu"
        else:
            device = "unknown"
        return {"reachable": True, "device": device, "devices": devices}

    def wait_healthy(self, *, timeout: float = 60.0, interval: float = 1.0) -> dict:
        """Poll /system_stats until the process answers or ``timeout`` elapses.

        Returns the last health dict either way — the caller decides whether an
        unreachable result is fatal (it usually is not: ComfyUI boots lazily and
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
            raise ComfyUiMpsError(
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
        """Best-effort: True only when we can PROVE ``pid`` is NOT our ComfyUI.

        Reads the process command line via ``ps``. If it clearly belongs to some
        other program (no ComfyUI ``main.py`` / state dir in the argv) we refuse
        to signal it. When ``ps`` is unavailable or the output is ambiguous we
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
        markers = ("main.py", "ComfyUI", str(self.repo_dir), str(self.state_dir))
        return not any(marker in cmdline for marker in markers)

    def _write_status(self, *, installed_ref: Optional[str], pid: Optional[int] = None) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"installed_ref": installed_ref, "port": self.port, "pid": pid}
        self.status_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def manager_from_env(env: dict[str, str]) -> ComfyUiMpsManager:
    """Build a manager from resolved .env values."""
    return ComfyUiMpsManager(
        state_dir=env.get("COMFYUI_MPS_STATE_DIR", "~/.atlas/comfyui-mps"),
        port=int(env.get("COMFYUI_MPS_LOCALHOST_PORT", "8188") or "8188"),
        ref=env.get("COMFYUI_MPS_REF", "v0.27.0"),
        models_path=env.get("COMFYUI_MPS_MODELS_PATH") or None,
        min_memory_gb=int(env.get("COMFYUI_MPS_MIN_MEMORY_GB", "16") or "16"),
    )
