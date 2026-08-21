"""Generic managed host-process lifecycle (#795).

A Metal/MLX-native service cannot be handed a GPU through a Linux container
on macOS, so it has to run as a *host process*. Atlas hand-built three of
those before this module existed — ComfyUI-MPS, vLLM-Metal, Blender-MCP —
and they converged on one shape: ``preflight → install → start → status →
health → stop → remove`` over a ``~/.atlas/<name>`` state dir holding a pid
file and a log. This module is that shape, extracted, so a *consumer* can
declare a host microservice in ``atlas.consumer.yml`` instead of a fourth
bespoke manager landing upstream.

The abstraction is a managed **host process**. A Metal venv is one optional
flavor of it, not the definition: ``blender-mcp`` is a managed host process
with no venv at all. Naming this "managed MPS" would have baked the
exception into the interface.

Three constraints are load-bearing rather than stylistic:

* **No shell, ever.** A declared command is an argv list (a string is
  ``shlex.split``); ``subprocess`` is called with a fixed argv and never
  ``shell=True``. The manifest is trusted-but-declarative — it says *what*
  to run, and never gets an interpreter to run it *through*.
* **Loopback unless told otherwise.** A declared service binds ``127.0.0.1``
  and a non-loopback bind is refused without ``allow_remote: true``. This
  mirrors the blender-mcp doctrine: these processes are unauthenticated by
  construction, so exposing one is a deliberate act, not a default.
* **Paths stay inside the consumer.** ``workdir``, ``requirements`` and
  install scripts resolve under the declaring manifest's root; a path that
  escapes it is a manifest error, not a warning.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from utils.atomic_write import atomic_write_text

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"
#: A check that could not be run. Deliberately NOT an escalation: warning
#: about a value that could not be read trains people to ignore doctor output.
_SKIPPED = "skipped"

#: A declared name becomes a state directory and an env-var infix, so it is
#: constrained at both ends rather than sanitized at each use site.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_STOP_POLL_SECONDS = 0.25
_STOP_POLL_ROUNDS = 20
_INSTALL_TIMEOUT_SECONDS = 30 * 60.0


class ManagedHostError(RuntimeError):
    """Raised for declared managed-host-process lifecycle failures."""


@dataclass
class PreflightResult:
    """Read-only verdict, shared by the built-in host managers and this one.

    ``add`` escalates monotonically and never downgrades: once a check has
    failed, a later ``ok`` cannot talk the verdict back down. ``skipped``
    is inert by design (see ``_SKIPPED``).
    """

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
class HostProcessStatus:
    running: bool
    pid: Optional[int] = None
    port_open: bool = False

    def to_dict(self) -> dict:
        return {"running": self.running, "pid": self.pid, "port_open": self.port_open}


@dataclass(frozen=True)
class VenvSpec:
    """An optional per-service virtualenv.

    ``metal`` is advisory metadata, not a switch: nothing here installs a
    Metal wheel on the consumer's behalf, because the pin belongs in the
    consumer's own requirements file where it is visible and reviewable.
    What the flag *does* is let preflight refuse early on a non-macOS host
    instead of failing deep inside a torch import.
    """

    python: str = "python3"
    metal: bool = False
    requirements: Optional[Path] = None
    packages: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "python": self.python,
            "metal": self.metal,
            "requirements": str(self.requirements) if self.requirements else None,
            "packages": list(self.packages),
        }


@dataclass(frozen=True)
class HealthProbe:
    """How to ask a declared service whether it is actually serving.

    ``tcp`` only proves something holds the port — which is why a declared
    ``http`` probe is preferred and ``expect_json`` is supported: a stale
    process from a previous run also holds a port.
    """

    kind: str = "tcp"
    path: str = "/"
    expect_json: Mapping[str, Any] = field(default_factory=dict)
    timeout: float = 5.0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "path": self.path,
            "expect_json": dict(self.expect_json),
            "timeout": self.timeout,
        }


@dataclass(frozen=True)
class HostProcessSpec:
    """One consumer-declared managed host process."""

    name: str
    command: tuple[str, ...]
    port: int
    workdir: Optional[Path] = None
    bind: str = "127.0.0.1"
    env: Mapping[str, str] = field(default_factory=dict)
    venv: Optional[VenvSpec] = None
    install: tuple[tuple[str, ...], ...] = ()
    health: HealthProbe = field(default_factory=HealthProbe)
    allow_remote: bool = False
    owner: str = ""

    @property
    def env_infix(self) -> str:
        """``sam3-segment`` → ``SAM3_SEGMENT`` for the endpoints contract."""
        return self.name.upper().replace("-", "_")

    @property
    def endpoint_var(self) -> str:
        return f"ATLAS_{self.env_infix}_HOST_ENDPOINT"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command": list(self.command),
            "port": self.port,
            "workdir": str(self.workdir) if self.workdir else None,
            "bind": self.bind,
            "venv": self.venv.to_dict() if self.venv else None,
            "install": [list(step) for step in self.install],
            "health": self.health.to_dict(),
            "allow_remote": self.allow_remote,
            "owner": self.owner,
        }


def split_command(raw: Any, *, origin: str, field_name: str) -> tuple[str, ...]:
    """Normalize a declared command to argv.

    A string is split with POSIX rules so ``python -m foo --flag=a b`` works,
    but the result is still argv handed to ``subprocess`` directly — the
    split is a convenience for manifest authors, not a shell.
    """
    if isinstance(raw, (list, tuple)):
        argv = [str(part) for part in raw]
    elif isinstance(raw, str):
        try:
            argv = shlex.split(raw)
        except ValueError as exc:
            raise ManagedHostError(f"{field_name} is not parseable as a command ({origin}): {exc}")
    else:
        raise ManagedHostError(f"{field_name} must be a string or a list ({origin})")
    if not argv:
        raise ManagedHostError(f"{field_name} must not be empty ({origin})")
    return tuple(argv)


class ManagedHostManager:
    """Generic lifecycle for one :class:`HostProcessSpec`.

    Deliberately duck-type-compatible with the three built-in managers'
    surface (``status`` / ``ensure_running_with_ownership`` / ``wait_healthy``
    / ``stop``), because ``start.py`` already orchestrates those by protocol
    rather than by type — so a declared service reaches the same launch and
    teardown paths as a built-in without any of them being special-cased.
    """

    def __init__(self, spec: HostProcessSpec, state_dir: Path | str) -> None:
        self.spec = spec
        self.state_dir = Path(state_dir).expanduser()
        self.pid_file = self.state_dir / f"{spec.name}.pid"
        self.log_file = self.state_dir / f"{spec.name}.log"
        self.venv_dir = self.state_dir / "venv"

    # ── resolution ───────────────────────────────────────────────────
    @property
    def venv_python(self) -> Path:
        return self.venv_dir / "bin" / "python"

    def _bind_is_loopback(self) -> bool:
        return self.spec.bind in _LOOPBACK

    def _probe_host(self) -> str:
        return "127.0.0.1" if self.spec.bind in ("0.0.0.0", "::") else self.spec.bind

    def endpoint(self) -> str:
        scheme = "http" if self.spec.health.kind == "http" else "tcp"
        return f"{scheme}://localhost:{self.spec.port}"

    def _resolved_command(self) -> list[str]:
        """Rewrite a leading ``python`` to the venv interpreter when one exists.

        Without this a declared ``python -m app`` silently runs against the
        host interpreter and imports none of what install just provisioned —
        the failure surfaces as a confusing ImportError rather than as the
        configuration mistake it is.
        """
        argv = list(self.spec.command)
        if self.spec.venv and argv and Path(argv[0]).name in ("python", "python3"):
            argv[0] = str(self.venv_python)
        return argv

    # ── preflight (read-only) ────────────────────────────────────────
    def preflight(self) -> PreflightResult:
        result = PreflightResult()
        self._preflight_bind(result)
        self._preflight_venv(result)
        self._preflight_command(result)
        self._preflight_process(result)
        return result

    def _preflight_bind(self, result: PreflightResult) -> None:
        if self._bind_is_loopback():
            result.add("bind", _OK, f"loopback bind {self.spec.bind}")
        elif self.spec.allow_remote:
            result.add(
                "bind", _WARN,
                f"non-loopback bind {self.spec.bind} with allow_remote: true — a "
                f"managed host process is unauthenticated; make sure the network "
                f"boundary is yours.",
            )
        else:
            result.add(
                "bind", _FAIL,
                f"non-loopback bind {self.spec.bind} refused — set allow_remote: "
                f"true on {self.spec.name!r} only if you own the network boundary.",
            )

    def _preflight_venv(self, result: PreflightResult) -> None:
        venv = self.spec.venv
        if venv is None:
            result.add("venv", _OK, "no venv declared; command runs on the host interpreter")
            return
        if venv.metal and sys.platform != "darwin":
            result.add(
                "venv", _FAIL,
                f"venv.metal: true requires macOS (running on {sys.platform}) — a "
                f"Metal build cannot be provisioned here.",
            )
            return
        if venv.requirements and not venv.requirements.exists():
            result.add("venv", _FAIL, f"requirements file not found: {venv.requirements}")
            return
        if self.venv_python.exists():
            result.add("venv", _OK, f"venv provisioned at {self.venv_dir}")
        elif shutil.which(venv.python):
            result.add("venv", _OK, f"venv will be created with {venv.python} on install")
        else:
            result.add("venv", _FAIL, f"interpreter {venv.python!r} not found on PATH")

    def _preflight_command(self, result: PreflightResult) -> None:
        argv = self._resolved_command()
        binary = argv[0]
        if self.spec.venv and binary == str(self.venv_python):
            result.add("command", _OK, f"runs the venv interpreter: {' '.join(argv[:3])}…")
        elif shutil.which(binary) or Path(binary).expanduser().exists():
            result.add("command", _OK, f"executable resolves: {binary}")
        else:
            result.add("command", _FAIL, f"command not found on PATH: {binary!r}")
        if self.spec.workdir and not self.spec.workdir.exists():
            result.add("workdir", _FAIL, f"workdir does not exist: {self.spec.workdir}")

    def _preflight_process(self, result: PreflightResult) -> None:
        status = self.status()
        if status.running:
            result.add("process", _OK, f"managed process already running (pid {status.pid})")
        elif self._port_in_use():
            result.add(
                "process", _WARN,
                f"port {self.spec.port} is in use by an unmanaged process — stop it "
                f"or change the declared port for {self.spec.name!r}.",
            )
        else:
            result.add("process", _OK, "port free; not yet running")

    # ── provisioning ─────────────────────────────────────────────────
    def install(self, *, update: bool = False) -> None:
        """Create the venv (if declared), install deps, run declared steps.

        Idempotent: an existing venv is reused unless ``update`` is set.
        """
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.spec.venv is not None:
            self._install_venv(update=update)
        for step in self.spec.install:
            self._run_step(list(step), what="install step")

    def _install_venv(self, *, update: bool) -> None:
        venv = self.spec.venv
        assert venv is not None  # guarded by the caller
        if update or not self.venv_python.exists():
            interpreter = shutil.which(venv.python)
            if interpreter is None:
                raise ManagedHostError(f"interpreter {venv.python!r} not found on PATH")
            self._run_step([interpreter, "-m", "venv", str(self.venv_dir)], what="venv create")
        pip = [str(self.venv_python), "-m", "pip", "install", "--upgrade"]
        if venv.requirements is not None:
            self._run_step(pip + ["-r", str(venv.requirements)], what="requirements install")
        if venv.packages:
            self._run_step(pip + list(venv.packages), what="package install")

    def _run_step(self, argv: list[str], *, what: str) -> None:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, never shell=True
                argv,
                cwd=str(self.spec.workdir) if self.spec.workdir else None,
                env=self._child_env(),
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=_INSTALL_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagedHostError(f"{what} failed for {self.spec.name!r}: {exc}") from exc
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-12:]
            raise ManagedHostError(
                f"{what} failed for {self.spec.name!r} "
                f"(exit {completed.returncode}):\n" + "\n".join(tail)
            )

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in self.spec.env.items()})
        env.setdefault("ATLAS_MANAGED_HOST_NAME", self.spec.name)
        env.setdefault("ATLAS_MANAGED_HOST_PORT", str(self.spec.port))
        env.setdefault("ATLAS_MANAGED_HOST_BIND", self.spec.bind)
        return env

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self, *, wait_timeout: float = 60.0) -> HostProcessStatus:
        if not self._bind_is_loopback() and not self.spec.allow_remote:
            raise ManagedHostError(
                f"refusing non-loopback bind {self.spec.bind} for {self.spec.name!r}; "
                f"set allow_remote: true to override"
            )
        status = self.status()
        if status.running:
            return status
        self.state_dir.mkdir(parents=True, exist_ok=True)
        process = self._spawn()
        try:
            self._write_pid_file(process.pid)
        except OSError as exc:
            # Without this the child keeps running while `status()` reports
            # False: untracked, unstoppable, still holding the port. The
            # dedicated ComfyUI manager already guarded this; the generic
            # extraction dropped it.
            self._terminate_untracked(process)
            raise ManagedHostError(
                f"{self.spec.name!r} started (pid {process.pid}) but its pid file "
                f"could not be written: {exc}. The process was terminated."
            ) from exc
        return self._await_port(process, wait_timeout)

    @staticmethod
    def _terminate_untracked(process: subprocess.Popen) -> None:
        """Kill a child we can no longer track, so it cannot outlive us."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if process.poll() is not None:
                return
            try:
                os.killpg(process.pid, sig)
            except OSError:
                try:
                    process.send_signal(sig)
                except OSError:
                    return
            try:
                process.wait(timeout=_STOP_POLL_ROUNDS * _STOP_POLL_SECONDS)
                return
            except subprocess.TimeoutExpired:
                continue

    def _write_pid_file(self, pid: int) -> None:
        """Record the pid together with the process start time.

        A pid alone is not an identity: the OS recycles it, and the pid file
        outlives a crash. ``(pid, start time)`` IS unique on POSIX, so recording
        the start time at spawn lets a later stop prove whether the pid still
        refers to the process we launched — without guessing from its argv,
        which a wrapper script, ``exec``, ``setproctitle`` or a gunicorn/celery
        master can rewrite at will.

        Written as an optional second line so an older single-line pid file
        still parses; that case degrades to the pre-existing behavior rather
        than to a wrong answer.
        """
        started = self._process_start_time(pid)
        body = str(pid) if started is None else f"{pid}\nstart_utc={started}"
        # ATOMIC. `write_text` truncates and then writes, and the `start_utc=`
        # line lands after the pid in the same call — so a crash or a
        # concurrent read mid-write yields a file with a pid and NO stamp.
        # `_recorded_start_time` then returns None, `_pid_is_stranger` reads
        # that as "unknowable -> proceed", and the reuse guard this function
        # exists to feed is silently disabled. Measured 18% torn reads under
        # concurrent access before this change.
        atomic_write_text(self.pid_file, body + "\n")

    def _spawn(self) -> subprocess.Popen:
        argv = self._resolved_command()
        try:
            with open(self.log_file, "ab") as log_handle:
                return subprocess.Popen(  # noqa: S603 - fixed argv, never shell=True
                    argv,
                    cwd=str(self.spec.workdir) if self.spec.workdir else None,
                    env=self._child_env(),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as exc:
            raise ManagedHostError(f"could not launch {self.spec.name!r}: {exc}") from exc

    def _await_port(self, process: subprocess.Popen, wait_timeout: float) -> HostProcessStatus:
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break  # child died — a port held by a FOREIGN process is not success
            if self._port_in_use():
                return HostProcessStatus(running=True, pid=process.pid, port_open=True)
            time.sleep(0.5)
        tail = self._log_tail()
        self.stop()
        raise ManagedHostError(
            f"{self.spec.name!r} did not open {self.spec.bind}:{self.spec.port} within "
            f"{wait_timeout:.0f}s. Log tail:\n{tail}"
        )

    def stop(self) -> bool:
        pid = self._read_pid()
        if pid is None or not self._pid_alive(pid):
            if pid is not None:
                self._sweep_orphaned_group(pid)
            self.pid_file.unlink(missing_ok=True)
            return True
        # PID-reuse guard (#947). The three built-in managers solve the same
        # problem by matching the process argv; this one compares the start
        # time recorded at spawn, which is an identity rather than a guess
        # (see _pid_is_stranger). A crashed process
        # leaves its pid file behind; the OS can then recycle that pid onto
        # an unrelated process owned by the same user — so `_pid_alive` says
        # yes and the PermissionError arm of it never fires. Signalling blind
        # here is worse than in the built-in managers, because `_signal`
        # escalates to `os.killpg`: it would take out the stranger's whole
        # process group. Drop the stale pid instead.
        if self._pid_is_stranger(pid):
            self.pid_file.unlink(missing_ok=True)
            return True
        if not self._signal(pid, signal.SIGTERM):
            return False
        if self._await_exit(pid):
            return True
        self._signal(pid, signal.SIGKILL)
        if self._await_exit(pid):  # SIGKILL is not instantaneous — grant a grace window
            return True
        # a failed stop KEEPS the pid file so the process is not orphan-tracked
        return False

    def _sweep_orphaned_group(self, pid: int) -> None:
        """Kill the process group when its LEADER died but members did not.

        `_spawn` passes `start_new_session=True` precisely so the whole tree is
        killable as one group, but `stop()` short-circuited on the leader being
        dead and never signalled it. A double-forking command, or a crashed
        gunicorn/uvicorn master whose workers survive, therefore left the port
        held forever: `status()` reports not-running, `stop()` reports success,
        and every later `start()` fails to bind. Verified as a permanent wedge.
        """
        if not self._group_survives(pid):
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except OSError:
                return
            for _ in range(_STOP_POLL_ROUNDS):
                if not self._group_survives(pid):
                    return
                time.sleep(_STOP_POLL_SECONDS)

    @staticmethod
    def _group_survives(pid: int) -> bool:
        """True only for a LEADERLESS group still bearing `pid` as its gid.

        Signalling a group whose leader is merely unreadable would hit a
        stranger, so this proves the leader is genuinely GONE first. That makes
        the group ours: POSIX keeps a pid allocated while it is still
        referenced as a pgid, so the kernel cannot have handed that number to
        an unrelated process while members of the group remain.
        """
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass          # the leader is genuinely gone — the good case
        except OSError:
            return False  # cannot prove anything; never signal on a guess
        else:
            return False  # the pid EXISTS: recycled or alive, not a remnant
        try:
            os.killpg(pid, 0)
        except OSError:
            return False
        return True

    @staticmethod
    def _signal(pid: int, sig: int) -> bool:
        try:
            os.killpg(pid, sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, sig)
                return True
            except OSError:
                return False

    def _await_exit(self, pid: int) -> bool:
        for _ in range(_STOP_POLL_ROUNDS):
            if not self._pid_alive(pid):
                self.pid_file.unlink(missing_ok=True)
                return True
            time.sleep(_STOP_POLL_SECONDS)
        return False

    def status(self) -> HostProcessStatus:
        pid = self._read_pid()
        # A pidfile + kill-0 probe alone trusts a RECYCLED PID: after a reboot
        # or crash another process can inherit the number, and kill-0 then
        # reports a dead service as running — so ensure_running_with_ownership
        # no-ops while nothing listens, and the later stop() signals the
        # stranger. Also require that the PID is not provably a stranger —
        # the built-in managers guard this same site for the same reason
        # (#647/#947), though by a weaker argv test.
        running = (
            pid is not None
            and self._pid_alive(pid)
            and not self._pid_is_stranger(pid)
        )
        return HostProcessStatus(
            running=running, pid=pid if running else None, port_open=self._port_in_use()
        )

    def health(self, *, timeout: Optional[float] = None) -> dict:
        probe = self.spec.health
        limit = timeout if timeout is not None else probe.timeout
        if probe.kind == "http":
            return self._health_http(limit)
        return {"reachable": self._port_in_use(timeout=limit)}

    def _health_http(self, timeout: float) -> dict:
        url = f"http://{self._probe_host()}:{self.spec.port}{self.spec.health.path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                body = response.read(65536).decode("utf-8", errors="replace")
                code = response.getcode()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"reachable": False, "error": str(exc)}
        out: dict[str, Any] = {"reachable": True, "code": code}
        expect = self.spec.health.expect_json
        if expect:
            out["matched"] = self._json_matches(body, expect, out)
        return out

    @staticmethod
    def _json_matches(body: str, expect: Mapping[str, Any], out: dict) -> bool:
        import json

        try:
            payload = json.loads(body)
        except ValueError:
            out["error"] = "response was not JSON"
            return False
        if not isinstance(payload, Mapping):
            out["error"] = "response JSON was not an object"
            return False
        return all(payload.get(key) == value for key, value in expect.items())

    def wait_healthy(self, *, timeout: float = 60.0, interval: float = 1.0) -> dict:
        deadline = time.monotonic() + timeout
        result: dict = {"reachable": False}
        while time.monotonic() < deadline:
            result = self.health()
            if result.get("reachable") and result.get("matched", True):
                return result
            time.sleep(interval)
        return result

    def ensure_running(self) -> HostProcessStatus:
        status, _ = self.ensure_running_with_ownership()
        return status

    def ensure_running_with_ownership(self) -> tuple[HostProcessStatus, bool]:
        """preflight → install → start. Returns ``(status, created_by_us)``.

        The ownership flag is what keeps a shared host singleton from being
        torn down by a stack that merely *used* it — the same contract the
        built-in managers report to ``stop.py``.
        """
        result = self.preflight()
        if not result.ok:
            failures = "; ".join(c["detail"] for c in result.checks if c["status"] == _FAIL)
            raise ManagedHostError(f"preflight failed for {self.spec.name!r}: {failures}")
        already = self.status().running
        self.install()
        return self.start(), not already

    def remove(self) -> None:
        """Stop the process and delete the Atlas-owned state directory.

        Refuses to delete the state dir while the process is still alive.
        `stop()` deliberately KEEPS the pid file when it fails, so the process
        stays tracked rather than becoming an orphan — and `rmtree` would throw
        that away, reaching the same orphan outcome the PID-reuse work exists to
        prevent, just through a different door. Mirrors the contract
        comfyui_mps_manager enforces: attempt the stop, then refuse on LIVENESS,
        not on the stop's return value.
        """
        # Gate on liveness ONLY, not on stop()'s return. A process that exits
        # between `_pid_alive` and the signal makes `_signal` see
        # ProcessLookupError from both killpg and kill — that is an OSError, so
        # stop() reports False for a process that is already gone, and gating on
        # it would refuse a removal that should succeed. comfyui_mps_manager
        # checks only `status().running` for the same reason.
        self.stop()
        if self.status().running:
            raise ManagedHostError(
                f"refusing to remove managed state for {self.spec.name!r} while "
                f"its process is still running"
            )
        shutil.rmtree(self.state_dir, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────
    def _port_in_use(self, *, timeout: float = 1.0) -> bool:
        try:
            with socket.create_connection((self._probe_host(), self.spec.port), timeout=timeout):
                return True
        except OSError:
            return False

    def _read_pid(self) -> Optional[int]:
        # The pid is the first line; `_write_pid_file` may append a `start=`
        # line after it. Reading the first line keeps single-line pid files
        # written by an earlier version parsing unchanged.
        try:
            first = self.pid_file.read_text(encoding="utf-8").splitlines()[0]
            pid = int(first.strip())
        except (OSError, ValueError, IndexError):
            return None
        # A pid must be POSITIVE. `_signal` escalates to `os.killpg`, and the
        # non-positive arguments are wildcards, not process ids:
        #   killpg(0, sig)  -> signals the CALLER's process group, i.e. the
        #                      bootstrapper kills itself, twice (TERM then KILL)
        #   kill(-1, sig)   -> broadcasts to every process this uid may signal
        # `_pid_alive(0)` returns True (kill(0, 0) succeeds against our own
        # group), so nothing downstream catches it. A pid file holding `0` is
        # reachable from a truncated or zero-filled write, and a hand-edited
        # or foreign-written file can hold anything at all.
        return pid if pid > 0 else None

    @staticmethod
    def _process_start_time(pid: int) -> Optional[str]:
        """Absolute start time of ``pid`` per ``ps``, or None if unknowable.

        ``lstart`` renders through the ambient ``TZ`` and ``LC_TIME``, so the
        SAME live process reads back differently depending on who asks — this
        machine returns four distinct strings for one pid under local time,
        ``TZ=UTC``, ``TZ=Asia/Tokyo`` and ``LC_ALL=de_DE``. That matters because
        a service is typically started from an interactive shell and stopped
        from launchd, cron or CI, which default to UTC: the comparison would
        call our own process a stranger and orphan it. Pinning both makes the
        rendered value a function of the process alone.
        """
        try:
            out = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5, check=False,
                encoding="utf-8", errors="replace",
                env={**os.environ, "TZ": "UTC", "LC_ALL": "C", "LANG": "C"},
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return (out.stdout or "").strip() or None

    def _recorded_start_time(self) -> Optional[str]:
        """The start time stamped into the pid file at spawn, if present."""
        try:
            body = self.pid_file.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in body.splitlines()[1:]:
            # Only the normalized key is trusted. A pid file stamped by the
            # first version of this format used the ambient TZ/locale, so its
            # value is not comparable against a UTC probe — reading it would
            # make every already-running managed host look like a stranger
            # exactly once, which is the failure this guard exists to prevent.
            # An unrecognized stamp simply reads as absent, and the guard then
            # degrades to proceed.
            if line.startswith("start_utc="):
                return line[len("start_utc="):].strip() or None
        return None

    def _pid_is_stranger(self, pid: int) -> bool:
        """True only when we can PROVE ``pid`` is NOT the process we launched.

        A pid is not an identity — the OS recycles it, and a crashed service
        leaves its pid file behind — but ``(pid, start time)`` is unique on
        POSIX. `start()` stamps the start time into the pid file, so this
        compares the recorded value against the live process and gets a
        definitive answer.

        Two earlier attempts matched the process argv instead, and each failed
        in both directions: seeding markers from the whole argv cleared 87
        unrelated processes on one developer machine (``-m``, ``127.0.0.1``,
        the port number), while curating the markers meant a spec whose tokens
        were all generic reduced to evidence that never appears in a command
        line at all — disowning our own live process, so `stop()` deleted the
        pid file and returned success while the service kept running. An argv
        is simply not an identity: a wrapper script, ``exec``, ``setproctitle``
        or a gunicorn/celery master rewrites it.

        Falls back to False (proceed) when the answer is unknowable — a pid
        file written before this format, or a ``ps`` that will not answer —
        which is the pre-existing behavior and matches the built-in managers'
        rule that an unknowable probe must never block teardown.

        KNOWN LIMITATION (Linux, unfixed): ``ps -o lstart=`` is computed there
        as ``/proc/stat btime + starttime/Hz``, and ``btime`` derives from the
        REALTIME clock — so ANY realtime adjustment that moves `btime`
        across a second boundary — an NTP step, a VM suspend/resume, or
        ordinary chrony/ntpd SLEW, which needs no step at all — shifts a live
        process's rendered start time and this comparison
        would call it a stranger, orphaning it. macOS is immune (``ki_start``
        is an absolute timestamp frozen at fork). The robust Linux fix is to
        read ``/proc/<pid>/stat`` field 22 directly, which is boot-relative and
        clock-step immune. That is deliberately NOT done here: it needs a third
        stamp format, and a format mismatch between stamp and probe is exactly
        the shape that produced the earlier orphaning bugs. Under a steady
        clock the value is stable (3000/3000 probes on procps-ng 4.0.2).

        On ``lstart`` granularity, since it invites the question: it resolves to
        one second, so two processes started back-to-back do share a value.
        That does not weaken this comparison. A pid is only recycled after the
        kernel's pid counter wraps — tens of thousands of spawns — so the
        stranger holding it necessarily started long after ours exited, in a
        different second. Measured on macOS: the value is stable across
        repeated probes of one live process, and is readable immediately after
        ``Popen`` (0 misses in 40 spawn-and-probe cycles), so neither drift nor
        a spawn race can silently turn our own process into a stranger.
        """
        recorded = self._recorded_start_time()
        if recorded is None:
            return False  # legacy pid file — no better answer than before
        current = self._process_start_time(pid)
        if current is None:
            return False  # can't tell — proceed
        return current != recorded

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            # When THIS process spawned it, an exited child lingers as a
            # zombie until it is waited on — and a zombie still answers
            # kill(0). Without this reap, stop() polls a dead process
            # forever and reports failure for a process it just killed.
            reaped, _ = os.waitpid(pid, os.WNOHANG)
            if reaped == pid:
                return False
        except (ChildProcessError, OSError):
            pass  # not our child — fall through to the signal probe
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # #647 doctrine (mirrors the built-in managers): a process we
            # cannot signal is not ours — treat a recycled/stale pid as
            # not-running rather than adopting (or later SIGTERMing) a
            # stranger.
            return False
        return True

    def _log_tail(self, lines: int = 12) -> str:
        try:
            content = self.log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "(no log)"
        return "\n".join(content.splitlines()[-lines:])


def default_state_dir(name: str, env: Mapping[str, str] | None = None) -> Path:
    """``~/.atlas/<name>``, matching where the built-in managers keep state."""
    root = ((env or {}).get("ATLAS_MANAGED_HOST_STATE_ROOT", "") or "").strip()
    base = Path(root).expanduser() if root else Path("~/.atlas").expanduser()
    return base / name


def manager_for(
    spec: HostProcessSpec, env: Mapping[str, str] | None = None
) -> ManagedHostManager:
    return ManagedHostManager(spec, default_state_dir(spec.name, env))
