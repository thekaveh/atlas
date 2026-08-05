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
directory so no weights are duplicated — and provisioning declared-but-missing
catalog models into it idempotently (#754: the same resolved file set the
container init downloads, delivered host-side).

All host effects (subprocess, platform, socket, HTTP, filesystem) go through
thin stdlib calls so the manager is fully unit-testable with mocks on generic
Linux CI; a real MPS round-trip is a separate ``live`` Darwin-arm64 test.
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
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import yaml

from core.process_runner import CommandOutputTooLarge, run_with_deadline

try:  # Native Windows can import this module for a no-op disabled-source stop.
    import fcntl
except ImportError:  # pragma: no cover - exercised only by native Windows Python
    fcntl = None  # type: ignore[assignment]

COMFYUI_REPO = "https://github.com/comfyanonymous/ComfyUI.git"

# fp8-scaled weights crash on MPS (Metal); BF16 is required (see #346/#335).
_MPS_UNSAFE_PRECISIONS = frozenset({"fp8", "fp8-scaled", "fp8_e4m3fn", "fp8_e5m2", "float8"})

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"
_SKIPPED = "skipped"
_LOCK_TIMEOUT_SECONDS = 30.0
_INSTALL_COMMAND_TIMEOUT_SECONDS = 30 * 60.0

# Standard ComfyUI model subdirs mapped onto the reused host models dir so a
# managed process never re-downloads weights the user already has.
_MODEL_SUBDIRS = (
    "checkpoints", "vae", "loras", "clip", "clip_vision", "controlnet",
    "unet", "diffusion_models", "text_encoders", "upscale_models",
)

# Pinned Torch/vision/audio for a REPRODUCIBLE managed-MPS install (#648).
# Unpinned `pip install torch …` pulls whatever is newest that day, so fresh
# installs on different days diverge against the same ComfyUI ref. This is the
# same coherent, security-floor-blessed triple JupyterHub's image installs; the
# default is overridable (and bumped alongside COMFYUI_MPS_REF) via the
# COMFYUI_MPS_TORCH_PIN env var. macOS/arm64 torch wheels carry Metal/MPS.
_DEFAULT_TORCH_PIN = "torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0"


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


@dataclass
class ProvisionResult:
    """Outcome of a host-side model provision run (#754).

    Mirrors the container ``download_models.sh`` philosophy: per-file failures
    are collected, not raised — one bad URL must not abort a stack launch."""

    provisioned: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict:
        return {
            "provisioned": list(self.provisioned),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


class ComfyUiMpsError(RuntimeError):
    """A managed-MPS lifecycle failure (unsupported host, install/launch error)."""

    def __init__(self, message: str, *, surviving_process: bool = False) -> None:
        super().__init__(message)
        self.surviving_process = surviving_process


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
        torch_pin: str | None = None,
    ) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.port = int(port)
        self.ref = ref
        self.models_path = Path(models_path).expanduser() if models_path else None
        # pip spec tokens for the pinned Torch stack (#648). A blank override
        # falls back to the reproducible default rather than an unpinned install.
        self.torch_pin = ((torch_pin or "").strip() or _DEFAULT_TORCH_PIN).split()
        self.min_memory_gb = int(min_memory_gb)
        self.listen = listen
        self.repo_dir = self.state_dir / "ComfyUI"
        self.venv_dir = self.state_dir / "venv"
        self.pid_file = self.state_dir / "comfyui-mps.pid"
        self.log_file = self.state_dir / "comfyui-mps.log"
        self.status_file = self.state_dir / "status.json"
        self.model_paths_file = self.state_dir / "extra_model_paths.yaml"
        self.launch_lock_file = (
            self.state_dir.parent / f".{self.state_dir.name}.launch.lock"
        )
        self._untracked_pid: Optional[int] = None

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

        # Host models dir: a typo'd COMFYUI_MPS_MODELS_PATH otherwise yields an
        # empty model list at generation time with no earlier signal (#648).
        if self.models_path is None:
            result.add("models_dir", _SKIPPED, "COMFYUI_MPS_MODELS_PATH not set")
        elif not self.models_path.is_dir():
            result.add(
                "models_dir", _WARN,
                f"COMFYUI_MPS_MODELS_PATH {self.models_path} does not exist — no host "
                "models will be reused; check the path",
            )
        elif not any((self.models_path / sub).is_dir() for sub in _MODEL_SUBDIRS):
            result.add(
                "models_dir", _WARN,
                f"COMFYUI_MPS_MODELS_PATH {self.models_path} has none of the expected "
                f"model subdirs ({', '.join(_MODEL_SUBDIRS)}) — likely the wrong path",
            )
        else:
            result.add("models_dir", _OK, f"host models dir {self.models_path} looks valid")

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
        with self._launch_guard():
            self._install_locked(update=update)

    def _install_locked(self, *, update: bool = False) -> None:
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

        requirements_sha256 = self._requirements_sha256()
        fresh = not self.venv_python.exists()
        if fresh:
            self._run(["python3", "-m", "venv", str(self.venv_dir)])
            self._run([str(self.venv_python), "-m", "pip", "install", "--upgrade", "pip"])
            # Pinned Torch stack for a reproducible install (#648). The macos-arm64
            # wheels carry Metal/MPS support.
            self._run([str(self.venv_python), "-m", "pip", "install", *self.torch_pin])
            self._run([str(self.venv_python), "-m", "pip", "install", "-r",
                       str(self.repo_dir / "requirements.txt")])
        elif update or not self._installed_environment_matches(requirements_sha256):
            # Re-apply the Torch pin so a security/compat bump (of the pin itself)
            # lands without a manual venv wipe, then reconcile ComfyUI's own
            # requirements. `==` pins are exact — no --upgrade needed, and it
            # honors the pin rather than jumping to the newest build (#648).
            self._run([str(self.venv_python), "-m", "pip", "install", *self.torch_pin])
            self._run([str(self.venv_python), "-m", "pip", "install", "-r",
                       str(self.repo_dir / "requirements.txt")])

        self._write_model_paths()
        self._write_status(
            installed_ref=self.ref,
            requirements_sha256=requirements_sha256,
        )

    def _requirements_sha256(self) -> str:
        requirements = self.repo_dir / "requirements.txt"
        try:
            return hashlib.sha256(requirements.read_bytes()).hexdigest()
        except OSError as exc:
            raise ComfyUiMpsError("pinned ComfyUI checkout lacks requirements.txt") from exc

    def _installed_environment_matches(self, requirements_sha256: str) -> bool:
        try:
            payload = json.loads(self.status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            payload.get("installed_ref") == self.ref
            and payload.get("requirements_sha256") == requirements_sha256
        )

    def _checkout_ref(self) -> None:
        """Force-checkout ``self.ref``, fetching once if the ref is unknown locally."""
        try:
            self._run(["git", "-C", str(self.repo_dir), "checkout", "--force", self.ref])
        except ComfyUiMpsError:
            # Ref not present locally (bumped without --update) — fetch + retry.
            self._run(["git", "-C", str(self.repo_dir), "fetch", "--tags", "--force"])
            self._run(["git", "-C", str(self.repo_dir), "checkout", "--force", self.ref])

    def _write_model_paths(self) -> None:
        """Point ComfyUI at the existing host models dir (no duplicate weights).

        Emitted via a YAML dumper so a models path containing YAML-special
        characters (``:``, ``#``, …) is quoted rather than silently corrupting
        the file (#648).
        """
        if not self.models_path:
            return
        base = str(self.models_path)
        config = {
            "atlas_host": {
                "base_path": base,
                **{sub: f"{base}/{sub}" for sub in _MODEL_SUBDIRS},
            }
        }
        header = "# AUTO-GENERATED by Atlas (#335) — reuse the host models dir.\n"
        body = yaml.safe_dump(config, default_flow_style=False, sort_keys=False)
        self.model_paths_file.write_text(header + body, encoding="utf-8")

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
        # Not running, but a pidfile may linger from a dead/recycled process.
        # Clear it so we relaunch cleanly instead of leaving a stale pointer
        # (status() already refuses to trust it, but the file must not survive a
        # fresh launch and mislead a later probe) (#647).
        self._clear_pid()
        self._untracked_pid = None
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
        self._untracked_pid = proc.pid
        try:
            self.pid_file.write_text(str(proc.pid), encoding="utf-8")
            self._write_status(
                installed_ref=self.ref,
                requirements_sha256=self._requirements_sha256(),
                pid=proc.pid,
            )
        except BaseException as exc:
            if self._terminate_pid(proc.pid):
                self._clear_pid()
                self._untracked_pid = None
                raise ComfyUiMpsError(
                    f"failed to record managed ComfyUI pid {proc.pid}; the child "
                    "was terminated"
                ) from exc
            raise ComfyUiMpsError(
                f"failed to record managed ComfyUI pid {proc.pid}, and the child "
                "could not be terminated; retry ./stop.sh or terminate that pid",
                surviving_process=True,
            ) from exc
        self._untracked_pid = None
        return (
            ProcessStatus(
                running=True, pid=proc.pid, port=self.port,
                installed_ref=self.ref, log_file=str(self.log_file),
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
                        raise ComfyUiMpsError(
                            "timed out waiting for another managed ComfyUI "
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
        if pid is None or not self._managed_process_alive(pid):
            self._clear_pid()
            self._untracked_pid = None
            return False
        # PID-reuse guard: the OS may have recycled a crashed ComfyUI's pid onto
        # an unrelated process. Only signal when we can't positively prove the
        # pid belongs to a stranger — never SIGKILL someone else's process.
        if self._pid_is_stranger(pid):
            self._clear_pid()
            self._write_status(installed_ref=self.ref, pid=None)
            self._untracked_pid = None
            return False
        if not self._terminate_pid(pid):
            # Be honest: the process outlived SIGKILL (e.g. EPERM). Keep the
            # pidfile so a retry/operator can act; don't claim success.
            return False
        self._clear_pid()
        self._write_status(installed_ref=self.ref, pid=None)
        self._untracked_pid = None
        return True

    def _terminate_pid(self, pid: int) -> bool:
        try:
            os.killpg(pid, signal.SIGINT)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                self._reap_child(pid)
                if not self._managed_process_alive(pid):
                    break
                time.sleep(0.2)
            else:
                os.killpg(pid, signal.SIGKILL)
                deadline = time.monotonic() + 5
                while self._managed_process_alive(pid) and time.monotonic() < deadline:
                    self._reap_child(pid)
                    time.sleep(0.1)
        except OSError:
            pass
        self._reap_child(pid)
        return not self._managed_process_alive(pid)

    @staticmethod
    def _reap_child(pid: int) -> None:
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass

    def status(self) -> ProcessStatus:
        pid = self._read_pid() or self._untracked_pid
        # A pidfile + kill-0 probe alone trusts a RECYCLED PID: after a reboot or
        # crash another process can inherit the number, and kill-0 then reports a
        # dead ComfyUI as running (so start() no-ops while nothing listens). Also
        # require that the PID is not provably a stranger — the argv/state-dir
        # ownership check that previously only stop() consulted (#647).
        running = (
            pid is not None
            and self._managed_process_alive(pid)
            and not self._pid_is_stranger(pid)
        )
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
        status, _created = self.ensure_running_with_ownership()
        return status

    def ensure_running_with_ownership(self) -> tuple[ProcessStatus, bool]:
        """Run the full launch path and atomically report process ownership."""
        pre = self.preflight()
        if not pre.ok:
            fails = [c for c in pre.checks if c["status"] == _FAIL]
            raise ComfyUiMpsError(
                "unsupported host for managed-localhost-mps: "
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
                raise ComfyUiMpsError(
                    "refusing to remove managed ComfyUI state while its process "
                    "is still running"
                )
            if self.state_dir.exists():
                shutil.rmtree(self.state_dir)

    # ── health ───────────────────────────────────────────────────────
    def health(self, *, timeout: float = 3.0) -> dict:
        url = f"http://{self._probe_host}:{self.port}/system_stats"
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
        try:
            result = run_with_deadline(
                cmd, timeout_seconds=_INSTALL_COMMAND_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired as exc:
            raise ComfyUiMpsError(
                f"command timed out after {_INSTALL_COMMAND_TIMEOUT_SECONDS:.0f}s "
                f"({' '.join(cmd[:3])}…)"
            ) from exc
        except CommandOutputTooLarge as exc:
            raise ComfyUiMpsError(
                f"command exceeded its output limit ({' '.join(cmd[:3])}…)"
            ) from exc
        if result.returncode != 0:
            raise ComfyUiMpsError(
                f"command failed ({' '.join(cmd[:3])}…): rc={result.returncode} "
                f"{(result.stderr or result.stdout or '').strip()[:400]}"
            )

    @property
    def _probe_host(self) -> str:
        """Loopback-reachable address for local health / port probes.

        When ComfyUI binds all interfaces (``COMFYUI_MPS_LISTEN=0.0.0.0`` — used
        on Linux engines where ``host.docker.internal`` maps via ``host-gateway``
        to a bridge address a loopback-bound listener can't answer), a client
        cannot connect to the ``0.0.0.0`` wildcard, so probe ``127.0.0.1``
        instead (#651). A concrete bind address is probed as-is.
        """
        return "127.0.0.1" if self.listen in ("", "0.0.0.0", "::") else self.listen

    def _port_in_use(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((self._probe_host, self.port)) == 0

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
            # We can't signal it, so it is NOT our (user-owned) managed process
            # — a foreign, likely root-owned, process recycled the PID (#647).
            return False
        return True

    @staticmethod
    def _process_group_alive(pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Same as _pid_alive: a group we can't signal is not ours (#647).
            return False
        return True

    def _managed_process_alive(self, pid: int) -> bool:
        return self._pid_alive(pid) or self._process_group_alive(pid)

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

    def _write_status(
        self,
        *,
        installed_ref: Optional[str],
        requirements_sha256: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if requirements_sha256 is None and installed_ref and self.repo_dir.exists():
            requirements_sha256 = self._requirements_sha256()
        payload = {
            "installed_ref": installed_ref,
            "requirements_sha256": requirements_sha256,
            "port": self.port,
            "pid": pid,
        }
        self.status_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


    # ── model provisioning (#754: #718 warn → provision) ─────────────
    _PROVISION_STATE_NAME = ".atlas_provisioned.json"
    _DISK_HEADROOM = 1.05  # 5% slack over the summed missing-file sizes

    def provision_models(
        self,
        rows: list[dict],
        *,
        verify: bool = False,
        log=None,
    ) -> ProvisionResult:
        """Idempotently provision the resolved model set into the host tree.

        ``rows`` is the SAME per-file structure the container TSV is built from
        (``comfyui_resolver.manifest_dict(...)["models"]``: name · type ·
        filename · download_url · sha256 · file_size_bytes · target_dir …) —
        one source of truth, two delivery mechanisms (container init vs. host
        provision). Semantics mirror ``download_models.sh``:

        - present + matching sha256 → skipped (a state sidecar caches verified
          stat+sha so unchanged files skip without re-hashing multi-GB blobs;
          ``verify=True`` forces a full re-hash);
        - present, sha declared, mismatch → re-fetched (corrupt/partial repair);
        - present, no sha declared → presence is a hit (container parity);
        - downloads stream to ``<dest>.part`` with HTTP-Range resume, then
          sha-verify and ``os.replace`` — an interrupted pull never leaves a
          corrupt weight in place, and the next run resumes the ``.part``;
        - MPS-unsafe precisions (fp8*) are skipped with a warning instead of
          pulling gigabytes that crash on Metal (#346);
        - a disk-space preflight fails the whole run early (before any byte is
          fetched) when free space can't hold the missing files;
        - per-file failures are collected, never raised (stack launch parity
          with the non-fatal container init).

        License notice (#756): rows carrying a license are announced — name,
        URL, and material restrictions — before their download starts, so the
        catalog's license surface fires on the provisioning path too.
        """
        emit = log or (lambda message: None)
        result = ProvisionResult()
        if self.models_path is None:
            result.failed.append(
                "COMFYUI_MPS_MODELS_PATH is not set — nowhere to provision"
            )
            return result

        # Dedupe by physical path (multiple logical bundles may share a file —
        # the TSV writer enforces metadata agreement; first row wins here).
        plan: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (str(row.get("target_dir", "")), str(row.get("filename", "")))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            precision = str(row.get("precision") or "").lower()
            if precision in _MPS_UNSAFE_PRECISIONS:
                result.warnings.append(
                    f"{key[0]}/{key[1]}: precision {precision!r} crashes on MPS "
                    f"(Metal) — skipped; select a BF16 variant (#346)"
                )
                continue
            plan.append(row)

        state = self._load_provision_state()

        # Disk preflight over files that would actually download.
        missing_bytes = 0
        for row in plan:
            dest = self._provision_dest(row)
            size = row.get("file_size_bytes")
            if not dest.exists() and isinstance(size, (int, float)):
                part = self._part_path(dest)
                already = part.stat().st_size if part.exists() else 0
                missing_bytes += max(0, int(size) - already)
        if missing_bytes:
            try:
                free = shutil.disk_usage(self.models_path).free
            except OSError:
                free = None
            if free is not None and free < missing_bytes * self._DISK_HEADROOM:
                result.failed.append(
                    f"insufficient disk space: need ~{missing_bytes / 1e9:.1f} GB "
                    f"for missing model files but only {free / 1e9:.1f} GB free "
                    f"under {self.models_path} — freeing space or trimming "
                    f"COMFYUI_USER_MODELS required (nothing was downloaded)"
                )
                return result

        # License surface: announce each distinct license among files that are
        # not already present, before any byte of them is fetched.
        announced: set[str] = set()
        for row in plan:
            name = str(row.get("license_name") or "").strip()
            if not name or name in announced:
                continue
            if self._provision_dest(row).exists():
                continue
            announced.add(name)
            restrictions = row.get("license_restrictions") or []
            url = str(row.get("license_url") or "").strip()
            notice = f"license: downloads governed by \'{name}\'"
            if url:
                notice += f" ({url})"
            emit(notice)
            for item in restrictions:
                emit(f"license:   - {item}")

        for row in plan:
            label = f"{row.get('target_dir')}/{row.get('filename')}"
            dest = self._provision_dest(row)
            sha = str(row.get("sha256") or "").strip()
            try:
                outcome = self._provision_one(row, dest, sha, state, verify=verify, emit=emit)
            except Exception as exc:  # noqa: BLE001 — per-file isolation
                result.failed.append(f"{label}: {exc}")
                emit(f"✗ {label} failed: {exc}")
                continue
            if outcome == "skipped":
                result.skipped.append(label)
                emit(f"✔ {label} (already present, skipped)")
            else:
                result.provisioned.append(label)
                emit(f"✓ {label} downloaded and verified")

        self._save_provision_state(state)
        return result

    def models_satisfied(self, rows: list[dict]) -> tuple[bool, list[str]]:
        """Presence check for the doctor lint: (all present, missing labels).

        MPS-unsafe rows are excluded — the provisioner deliberately skips them,
        so they must not keep the lint red forever."""
        if self.models_path is None:
            return False, ["COMFYUI_MPS_MODELS_PATH not set"]
        missing: list[str] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (str(row.get("target_dir", "")), str(row.get("filename", "")))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            if str(row.get("precision") or "").lower() in _MPS_UNSAFE_PRECISIONS:
                continue
            if not self._provision_dest(row).exists():
                missing.append(f"{key[0]}/{key[1]}")
        return not missing, missing

    def _provision_dest(self, row: dict) -> Path:
        assert self.models_path is not None
        return self.models_path / str(row.get("target_dir") or "") / str(row.get("filename"))

    @staticmethod
    def _part_path(dest: Path) -> Path:
        return dest.with_name(dest.name + ".part")

    def _provision_one(
        self,
        row: dict,
        dest: Path,
        sha: str,
        state: dict,
        *,
        verify: bool,
        emit,
    ) -> str:
        """Provision one file; returns "skipped" or "provisioned". Raises on failure."""
        state_key = str(dest.relative_to(self.models_path))
        if dest.exists():
            if sha:
                cached = state.get(state_key) or {}
                stat = dest.stat()
                if (
                    not verify
                    and cached.get("sha256") == sha
                    and cached.get("size") == stat.st_size
                    and cached.get("mtime_ns") == stat.st_mtime_ns
                ):
                    return "skipped"  # fast path: previously verified, unchanged
                actual = self._sha256_file(dest)
                if actual == sha:
                    self._record_state(state, state_key, dest, sha)
                    return "skipped"
                emit(f"↻ {state_key}: sha256 mismatch — re-fetching corrupt file")
                dest.unlink()
                state.pop(state_key, None)
            else:
                return "skipped"  # no checksum declared: presence is a hit

        url = str(row.get("download_url") or "").strip()
        if not url:
            raise ComfyUiMpsError(f"no download_url for {state_key}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = self._part_path(dest)
        self._fetch_to_part(url, part)
        if sha:
            actual = self._sha256_file(part)
            if actual != sha:
                part.unlink(missing_ok=True)  # poisoned bytes: no resume
                raise ComfyUiMpsError(
                    f"sha256 mismatch after download (expected {sha[:12]}…, got "
                    f"{actual[:12]}…) — partial removed; re-run to retry"
                )
        os.replace(part, dest)
        if sha:
            self._record_state(state, state_key, dest, sha)
        return "provisioned"

    def _fetch_to_part(self, url: str, part: Path, *, chunk_size: int = 1 << 20) -> None:
        """Stream ``url`` into ``part`` with HTTP-Range resume.

        A transport error KEEPS the partial (the next run resumes it — wget -c
        parity); an HTTP error status drops it (a served error page must never
        be mistaken for model bytes)."""
        resume_from = part.stat().st_size if part.exists() else 0
        request = urllib.request.Request(url)
        if resume_from:
            request.add_header("Range", f"bytes={resume_from}-")
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and resume_from:
                # Range not satisfiable — the part is already the full file.
                return
            part.unlink(missing_ok=True)
            raise ComfyUiMpsError(f"HTTP {exc.code} fetching {url}") from exc
        with response:
            status = getattr(response, "status", 200)
            mode = "ab" if (resume_from and status == 206) else "wb"
            with open(part, mode) as handle:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    handle.write(chunk)

    @staticmethod
    def _sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _provision_state_path(self) -> Path:
        assert self.models_path is not None
        return self.models_path / self._PROVISION_STATE_NAME

    def _load_provision_state(self) -> dict:
        try:
            return json.loads(self._provision_state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_provision_state(self, state: dict) -> None:
        try:
            path = self._provision_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:  # state is an optimization, never a failure
            pass

    @staticmethod
    def _record_state(state: dict, key: str, dest: Path, sha: str) -> None:
        stat = dest.stat()
        state[key] = {"sha256": sha, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def manager_from_env(env: dict[str, str]) -> ComfyUiMpsManager:
    """Build a manager from resolved .env values."""
    return ComfyUiMpsManager(
        state_dir=env.get("COMFYUI_MPS_STATE_DIR", "~/.atlas/comfyui-mps"),
        port=int(env.get("COMFYUI_MPS_LOCALHOST_PORT", "8188") or "8188"),
        ref=env.get("COMFYUI_MPS_REF", "v0.27.0"),
        models_path=env.get("COMFYUI_MPS_MODELS_PATH") or None,
        min_memory_gb=int(env.get("COMFYUI_MPS_MIN_MEMORY_GB", "16") or "16"),
        torch_pin=env.get("COMFYUI_MPS_TORCH_PIN") or None,
        listen=env.get("COMFYUI_MPS_LISTEN") or "127.0.0.1",
    )
