"""Generic consumer-declarable managed-host-process framework (#795).

The lifecycle tests spawn a real process on purpose. A managed host service
exists precisely because a container cannot stand in for it, so a mocked
``Popen`` would assert the mock rather than the contract. What is spawned is
a stdlib HTTP server on a loopback port picked at runtime — no GPU, no
Metal, no network exposure, nothing that could collide with a stack already
running on this host.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from core.consumer_manifest import ConsumerManifestError, load_consumer_config
from core.endpoints_contract import build_export
from services.managed_host import (
    HealthProbe,
    HostProcessSpec,
    HostProcessStatus,
    ManagedHostError,
    ManagedHostManager,
    PreflightResult,
    VenvSpec,
    split_command,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _spec(**kwargs) -> HostProcessSpec:
    base = {
        "name": "sam3-segment",
        "command": ("python3", "-c", "pass"),
        "port": 8799,
    }
    base.update(kwargs)
    return HostProcessSpec(**base)


# ── command normalization ────────────────────────────────────────────


def test_a_string_command_splits_to_argv():
    argv = split_command("python -m app --flag=a b", origin="o", field_name="command")
    assert argv == ("python", "-m", "app", "--flag=a", "b")


def test_a_list_command_passes_through():
    argv = split_command(["python", "-m", "app"], origin="o", field_name="command")
    assert argv == ("python", "-m", "app")


@pytest.mark.parametrize("raw", ["", "   ", [], None, 42])
def test_an_empty_or_non_command_is_rejected(raw):
    with pytest.raises(ManagedHostError):
        split_command(raw, origin="o", field_name="command")


def test_shell_metacharacters_are_argv_tokens_not_shell_syntax():
    """The manifest says WHAT to run; it never gets an interpreter to run it
    THROUGH. If this ever regresses to shell=True, a declared command becomes
    arbitrary code execution with a semicolon."""
    argv = split_command("app --name a;rm -rf /", origin="o", field_name="command")
    assert argv == ("app", "--name", "a;rm", "-rf", "/")
    assert ";" not in argv[0]


def test_an_unterminated_quote_is_a_manifest_error_not_a_traceback():
    with pytest.raises(ManagedHostError, match="not parseable"):
        split_command('app --name "unclosed', origin="o", field_name="command")


# ── preflight ────────────────────────────────────────────────────────


def test_loopback_bind_passes_preflight(tmp_path):
    result = ManagedHostManager(_spec(bind="127.0.0.1"), tmp_path).preflight()
    bind = next(c for c in result.checks if c["name"] == "bind")
    assert bind["status"] == "ok"


def test_a_non_loopback_bind_fails_without_allow_remote(tmp_path):
    result = ManagedHostManager(_spec(bind="0.0.0.0"), tmp_path).preflight()
    bind = next(c for c in result.checks if c["name"] == "bind")
    assert bind["status"] == "fail"
    assert result.ok is False


def test_a_non_loopback_bind_warns_when_explicitly_opted_in(tmp_path):
    spec = _spec(bind="0.0.0.0", allow_remote=True)
    result = ManagedHostManager(spec, tmp_path).preflight()
    bind = next(c for c in result.checks if c["name"] == "bind")
    assert bind["status"] == "warn"
    assert result.ok is True


def test_a_metal_venv_fails_preflight_off_darwin(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    spec = _spec(venv=VenvSpec(python="python3", metal=True))
    result = ManagedHostManager(spec, tmp_path).preflight()
    venv = next(c for c in result.checks if c["name"] == "venv")
    assert venv["status"] == "fail"
    assert "macOS" in venv["detail"]


def test_a_missing_command_fails_preflight(tmp_path):
    spec = _spec(command=("definitely-not-a-real-binary-xyz",))
    result = ManagedHostManager(spec, tmp_path).preflight()
    cmd = next(c for c in result.checks if c["name"] == "command")
    assert cmd["status"] == "fail"


def test_preflight_never_downgrades_a_failure(tmp_path):
    result = PreflightResult()
    result.add("a", "fail", "broken")
    result.add("b", "ok", "fine")
    result.add("c", "skipped", "unreadable")
    assert result.status == "fail"
    assert result.ok is False


# ── the venv interpreter rewrite ─────────────────────────────────────


def test_a_declared_python_resolves_to_the_venv_interpreter(tmp_path):
    """Without this a declared `python -m app` runs on the HOST interpreter
    and imports none of what install provisioned — surfacing as a confusing
    ImportError rather than as the configuration mistake it is."""
    spec = _spec(command=("python", "-m", "app"), venv=VenvSpec())
    manager = ManagedHostManager(spec, tmp_path)
    assert manager._resolved_command()[0] == str(manager.venv_python)


def test_a_non_python_command_is_left_alone(tmp_path):
    spec = _spec(command=("blender", "--background"), venv=VenvSpec())
    manager = ManagedHostManager(spec, tmp_path)
    assert manager._resolved_command()[0] == "blender"


def test_no_venv_means_no_rewrite(tmp_path):
    manager = ManagedHostManager(_spec(command=("python", "-m", "app")), tmp_path)
    assert manager._resolved_command()[0] == "python"


# ── real lifecycle ───────────────────────────────────────────────────


_SERVER = textwrap.dedent(
    """
    import json, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass

    HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
    """
)


@pytest.fixture
def running_service(tmp_path):
    """Start a real loopback HTTP process and guarantee it is reaped."""
    script = tmp_path / "server.py"
    script.write_text(_SERVER, encoding="utf-8")
    port = _free_port()
    spec = _spec(
        command=(sys.executable, str(script), str(port)),
        port=port,
        health=HealthProbe(kind="http", path="/health", expect_json={"status": "ok"}),
    )
    manager = ManagedHostManager(spec, tmp_path / "state")
    try:
        yield manager
    finally:
        manager.stop()


def test_start_status_health_stop_round_trip(running_service):
    manager = running_service
    assert manager.status().running is False

    status = manager.start(wait_timeout=30.0)
    assert status.running is True
    assert status.pid is not None
    assert manager.status().running is True

    health = manager.health(timeout=10.0)
    assert health["reachable"] is True
    assert health["matched"] is True

    assert manager.stop() is True
    assert manager.status().running is False


def test_starting_an_already_running_service_is_idempotent(running_service):
    manager = running_service
    first = manager.start(wait_timeout=30.0)
    second = manager.start(wait_timeout=30.0)
    assert second.pid == first.pid


def test_expect_json_mismatch_is_reachable_but_unmatched(running_service):
    manager = running_service
    manager.start(wait_timeout=30.0)
    manager.spec = _spec(
        command=manager.spec.command,
        port=manager.spec.port,
        health=HealthProbe(kind="http", path="/health", expect_json={"status": "degraded"}),
    )
    health = manager.health(timeout=10.0)
    assert health["reachable"] is True
    assert health["matched"] is False


def test_a_command_that_never_opens_the_port_raises_with_a_log_tail(tmp_path):
    script = tmp_path / "noisy.py"
    script.write_text("import sys; print('boom', flush=True); sys.exit(3)", encoding="utf-8")
    spec = _spec(command=(sys.executable, str(script)), port=_free_port())
    manager = ManagedHostManager(spec, tmp_path / "state")
    with pytest.raises(ManagedHostError, match="did not become healthy"):
        manager.start(wait_timeout=5.0)
    assert "boom" in manager._log_tail()


def test_stop_is_true_when_the_pid_is_already_dead(tmp_path):
    manager = ManagedHostManager(_spec(), tmp_path)
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("999999999", encoding="utf-8")
    assert manager.stop() is True
    assert manager.pid_file.exists() is False


def test_remove_clears_the_state_dir(tmp_path):
    manager = ManagedHostManager(_spec(), tmp_path / "state")
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    (manager.state_dir / "junk").write_text("x", encoding="utf-8")
    manager.remove()
    assert manager.state_dir.exists() is False


# ── manifest declaration ─────────────────────────────────────────────


def _write_consumer(tmp_path: Path, block: dict, *, name: str = "daydreams") -> Path:
    root = tmp_path / name
    (root / "app").mkdir(parents=True, exist_ok=True)
    manifest = root / "atlas.consumer.yml"
    manifest.write_text(yaml.safe_dump({"name": name, **block}), encoding="utf-8")
    return manifest


def _load(tmp_path: Path, block: dict, **kwargs):
    manifest = _write_consumer(tmp_path, block, **kwargs)
    return load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


_FULL_DECLARATION = {
    "managed_host_services": [{
        "name": "sam3-segment",
        "command": "python -m sam3_service",
        "port": 8799,
        "workdir": "app",
        "venv": {"python": "3.13", "metal": True, "packages": ["mlx"]},
        "health": {"path": "/health", "expect_json": {"status": "ok"}},
    }],
}


def test_a_declared_service_becomes_a_spec(tmp_path):
    spec = _load(tmp_path, _FULL_DECLARATION).managed_host_services[0]
    assert (spec.name, spec.port, spec.owner) == ("sam3-segment", 8799, "daydreams")
    assert spec.command == ("python", "-m", "sam3_service")


def test_a_declared_venv_normalizes_a_bare_python_version(tmp_path):
    """`3.13` is the natural thing to write and means the python3.13 binary."""
    venv = _load(tmp_path, _FULL_DECLARATION).managed_host_services[0].venv
    assert (venv.python, venv.metal, venv.packages) == ("python3.13", True, ("mlx",))


def test_a_declaration_defaults_to_loopback_and_no_remote(tmp_path):
    spec = _load(tmp_path, _FULL_DECLARATION).managed_host_services[0]
    assert (spec.bind, spec.allow_remote, spec.health.kind) == ("127.0.0.1", False, "http")


def test_a_declared_path_implies_an_http_probe(tmp_path):
    """Requiring both `kind` and `path` is a trap that silently degrades a
    real health check into a bare port knock."""
    config = _load(tmp_path, {
        "managed_host_services": [
            {"name": "svc", "command": "app", "port": 9001, "health": {"path": "/healthz"}},
        ],
    })
    assert config.managed_host_services[0].health.kind == "http"


def test_no_health_block_means_a_tcp_probe(tmp_path):
    config = _load(tmp_path, {
        "managed_host_services": [{"name": "svc", "command": "app", "port": 9001}],
    })
    assert config.managed_host_services[0].health.kind == "tcp"


@pytest.mark.parametrize("bad_name", ["Sam3", "-svc", "svc_name", "", "svc!"])
def test_an_unusable_name_is_rejected(tmp_path, bad_name):
    with pytest.raises(ConsumerManifestError, match="must match"):
        _load(tmp_path, {
            "managed_host_services": [{"name": bad_name, "command": "app", "port": 9001}],
        })


def test_an_unknown_field_is_rejected(tmp_path):
    with pytest.raises(ConsumerManifestError, match="unknown field"):
        _load(tmp_path, {
            "managed_host_services": [
                {"name": "svc", "command": "app", "port": 9001, "gpu": True},
            ],
        })


@pytest.mark.parametrize("port", [0, -1, 70000, "http"])
def test_an_out_of_range_port_is_rejected(tmp_path, port):
    with pytest.raises(ConsumerManifestError):
        _load(tmp_path, {
            "managed_host_services": [{"name": "svc", "command": "app", "port": port}],
        })


def test_a_duplicate_name_within_one_manifest_is_rejected(tmp_path):
    with pytest.raises(ConsumerManifestError, match="duplicate"):
        _load(tmp_path, {
            "managed_host_services": [
                {"name": "svc", "command": "app", "port": 9001},
                {"name": "svc", "command": "other", "port": 9002},
            ],
        })


def test_two_consumers_cannot_claim_one_name(tmp_path):
    """The name owns ~/.atlas/<name> AND the endpoint var, so a collision is
    two lifecycles writing one pid file — it cannot dedupe benignly."""
    a = _write_consumer(tmp_path, {
        "managed_host_services": [{"name": "svc", "command": "app", "port": 9001}],
    }, name="alpha")
    b = _write_consumer(tmp_path, {
        "managed_host_services": [{"name": "svc", "command": "other", "port": 9002}],
    }, name="beta")
    with pytest.raises(ConsumerManifestError, match="cannot be shared"):
        load_consumer_config(tmp_path, explicit_paths=[str(a), str(b)])


def test_a_workdir_escaping_the_consumer_root_is_rejected(tmp_path):
    """Stricter than the sibling blocks on purpose: this one declares things
    Atlas executes."""
    with pytest.raises(ConsumerManifestError, match="outside the consumer root"):
        _load(tmp_path, {
            "managed_host_services": [
                {"name": "svc", "command": "app", "port": 9001, "workdir": "../../etc"},
            ],
        })


def test_a_requirements_file_escaping_the_consumer_root_is_rejected(tmp_path):
    with pytest.raises(ConsumerManifestError, match="outside the consumer root"):
        _load(tmp_path, {
            "managed_host_services": [{
                "name": "svc", "command": "app", "port": 9001,
                "venv": {"requirements": "/etc/passwd"},
            }],
        })


def test_a_malformed_command_surfaces_as_a_manifest_error(tmp_path):
    with pytest.raises(ConsumerManifestError, match="not parseable"):
        _load(tmp_path, {
            "managed_host_services": [
                {"name": "svc", "command": 'app "unclosed', "port": 9001},
            ],
        })


# ── endpoints contract ───────────────────────────────────────────────


def test_the_export_is_ordered_by_name_not_discovery_order():
    specs = [_spec(name="zulu", port=1), _spec(name="alpha", port=2)]
    names = [f.name for f in build_export({}, host_services=specs)]
    assert names.index("ATLAS_ALPHA_HOST_ENDPOINT") < names.index("ATLAS_ZULU_HOST_ENDPOINT")


def test_no_declared_services_export_nothing_new():
    before = {f.name for f in build_export({})}
    after = {f.name for f in build_export({}, host_services=[])}
    assert before == after


# ── doctor ───────────────────────────────────────────────────────────


def test_doctor_passes_cleanly_when_nothing_is_declared(monkeypatch):
    import start as start_module

    class _Parser:
        def load_consumer_config(self):
            return type("C", (), {"managed_host_services": ()})()

        def parse_env_file(self):
            return {}

    starter = type("S", (), {"config_parser": _Parser()})()
    row = start_module._doctor_check_managed_host_services(starter)
    assert row["status"] == "pass"
    assert "No managed_host_services" in row["message"]


def test_doctor_reports_a_failing_declared_service(tmp_path, monkeypatch):
    import start as start_module

    spec = _spec(bind="0.0.0.0")  # non-loopback without allow_remote → fail

    class _Parser:
        def load_consumer_config(self):
            return type("C", (), {"managed_host_services": (spec,)})()

        def parse_env_file(self):
            return {"ATLAS_MANAGED_HOST_STATE_ROOT": str(tmp_path)}

    starter = type("S", (), {"config_parser": _Parser()})()
    row = start_module._doctor_check_managed_host_services(starter)
    assert row["status"] == "fail"
    assert "sam3-segment" in row["message"]
    assert row["details"]["services"][0]["name"] == "sam3-segment"


def test_doctor_degrades_to_skipped_when_manifests_cannot_load():
    """A manifest problem must not blank the rest of the doctor report."""
    import start as start_module

    class _Parser:
        def load_consumer_config(self):
            raise RuntimeError("bad manifest")

        def parse_env_file(self):
            return {}

    starter = type("S", (), {"config_parser": _Parser()})()
    row = start_module._doctor_check_managed_host_services(starter)
    assert row["status"] == "skipped"


# ── CLI ──────────────────────────────────────────────────────────────


def test_the_cli_names_the_declared_services_when_one_is_missing(monkeypatch):
    import start as start_module
    from click.testing import CliRunner

    monkeypatch.setattr(
        start_module, "_managed_host_specs", lambda: {"sam3-segment": _spec()}
    )
    result = CliRunner().invoke(start_module.main, ["managed-host", "status", "nope"])
    assert result.exit_code == 2
    assert "sam3-segment" in result.output


def test_the_cli_lists_declared_services(monkeypatch):
    import start as start_module
    from click.testing import CliRunner

    monkeypatch.setattr(
        start_module, "_managed_host_specs", lambda: {"sam3-segment": _spec()}
    )
    result = CliRunner().invoke(start_module.main, ["managed-host", "list"])
    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert rows[0]["endpoint_var"] == "ATLAS_SAM3_SEGMENT_HOST_ENDPOINT"


def test_a_killed_child_is_reaped_not_reported_alive(tmp_path):
    """A child that exits lingers as a zombie until waited on, and a zombie
    still answers kill(0). Without the reap, stop() polls its full grace
    window and then reports failure for a process it just killed."""
    script = tmp_path / "sleeper.py"
    script.write_text("import time; time.sleep(300)", encoding="utf-8")
    spec = _spec(command=(sys.executable, str(script)), port=_free_port())
    manager = ManagedHostManager(spec, tmp_path / "state")
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    process = manager._spawn()
    manager._write_pid_file(process.pid)

    assert manager.status().running is True
    assert manager.stop() is True
    assert manager.status().running is False


# ── PID-reuse ownership guard (#647/#947) ────────────────────────────────
#
# `stop()` escalates to `os.killpg`, so a wrong verdict takes out a stranger's
# whole process group; a wrong verdict the other way deletes the pid file while
# our service keeps running, untracked. Identity is `(pid, start time)`, which
# is unique on POSIX — NOT the argv, which a wrapper script, `exec`,
# `setproctitle` or a gunicorn/celery master rewrites at will.


def _manager(tmp_path: Path, name: str, command: tuple[str, ...]):
    return ManagedHostManager(
        HostProcessSpec(name=name, command=command, port=8399),
        state_dir=tmp_path / name,
    )


def test_start_records_the_process_start_time_alongside_the_pid(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "import time; time.sleep(30)"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        manager._write_pid_file(proc.pid)
        assert manager._read_pid() == proc.pid
        recorded = manager._recorded_start_time()
        assert recorded, "start time must be stamped so a recycled pid is detectable"
        assert recorded == manager._process_start_time(proc.pid)
        # The process we launched is never a stranger to itself.
        assert manager._pid_is_stranger(proc.pid) is False
    finally:
        proc.kill()
        proc.wait()


def test_a_recycled_pid_is_identified_as_a_stranger(tmp_path: Path) -> None:
    """A crashed service's pid file outlives it; the OS reuses the number."""
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    stranger = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # Stamp the stranger's pid with a start time that is NOT its own —
        # exactly what a stale pid file looks like after pid reuse.
        manager.pid_file.write_text(
            f"{stranger.pid}\nstart_utc=Thu Jan  1 00:00:00 2020\n", encoding="utf-8"
        )
        assert manager._pid_is_stranger(stranger.pid) is True
        # ...so stop() must not signal it.
        assert manager.stop() is False         # refused, not signalled
        assert manager.pid_file.exists(), "ownership evidence was discarded"
        assert stranger.poll() is None, "stop() signalled a process it did not own"
    finally:
        stranger.kill()
        stranger.wait()


def test_our_own_process_is_never_disowned_whatever_its_argv_looks_like(
    tmp_path: Path,
) -> None:
    """The argv-matching guard this replaced failed exactly here.

    A spec whose tokens are all generic (`api` running `python -m app`) left no
    marker that appears in a command line, so the guard called our own live
    process a stranger: `stop()` deleted the pid file and returned success
    while the service kept running, untracked.
    """
    manager = _manager(tmp_path, "api", ("python", "-m", "app"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    ours = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        manager._write_pid_file(ours.pid)
        assert manager._pid_is_stranger(ours.pid) is False
        assert manager.status().running is True
    finally:
        ours.kill()
        ours.wait()


def test_a_legacy_single_line_pid_file_is_treated_as_unknown(
    tmp_path: Path,
) -> None:
    """No recorded start time means ownership cannot be proven."""
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\n", encoding="utf-8")

    assert manager._read_pid() == 4242
    assert manager._recorded_start_time() is None
    assert manager._pid_is_stranger(4242) is True


def test_start_refuses_live_unknown_pid_and_preserves_tracking(tmp_path, monkeypatch):
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\n", encoding="utf-8")
    before = manager.pid_file.read_bytes()
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(manager, "_pid_is_stranger", lambda _pid: True)
    monkeypatch.setattr(
        manager,
        "_spawn",
        lambda: pytest.fail("unknown ownership must not spawn a replacement"),
    )

    with pytest.raises(ManagedHostError, match="ownership is mismatched or unknown"):
        manager.start()

    assert manager.pid_file.read_bytes() == before


def test_pid_is_stranger_refuses_when_ps_cannot_answer(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\nstart_utc=Thu Jan  1 00:00:00 2020\n", encoding="utf-8")

    def failing(argv, **kwargs):
        raise OSError("ps missing")

    monkeypatch.setattr(subprocess, "run", failing)
    assert manager._pid_is_stranger(4242) is True


def test_permission_denied_pid_is_live_but_never_adopted(tmp_path, monkeypatch):
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\n", encoding="utf-8")
    before = manager.pid_file.read_bytes()
    monkeypatch.setattr(
        "services.managed_host.os.kill",
        lambda *_args: (_ for _ in ()).throw(PermissionError()),
    )

    assert manager._pid_alive(4242) is True
    assert manager.stop() is False
    with pytest.raises(ManagedHostError, match="ownership is mismatched or unknown"):
        manager.start()
    assert manager.pid_file.read_bytes() == before


def test_a_pre_normalization_start_stamp_is_ignored_not_trusted(tmp_path: Path) -> None:
    """The first version of this stamp used the ambient TZ and locale.

    Comparing such a value against a UTC probe would call every
    already-running managed host a stranger exactly once, so an unrecognized
    stamp must read as absent and refuse to authorize signalling.
    """
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\nstart=Thu Aug 20 19:00:00 2026\n", encoding="utf-8")

    assert manager._read_pid() == 4242
    assert manager._recorded_start_time() is None
    assert manager._pid_is_stranger(4242) is True


def test_the_recorded_stamp_does_not_depend_on_ambient_timezone_or_locale(
    tmp_path: Path, monkeypatch
) -> None:
    """`lstart` renders through TZ/LC_TIME, so an unpinned probe is not an identity.

    A service is typically started from an interactive shell and stopped from
    launchd, cron or CI — all UTC. Without pinning, the stop would not match
    its own record and would orphan the process it meant to stop.
    """
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    ours = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        manager._write_pid_file(ours.pid)
        for tz in ("UTC", "Asia/Tokyo", "America/New_York"):
            monkeypatch.setenv("TZ", tz)
            assert manager._pid_is_stranger(ours.pid) is False, (
                f"our own process read as a stranger under TZ={tz}"
            )
    finally:
        ours.kill()
        ours.wait()


def test_remove_refuses_while_the_process_is_still_running(tmp_path: Path, monkeypatch) -> None:
    """`stop()` KEEPS the pid file on failure so the process stays tracked.

    `remove()` used to `rmtree` the state dir regardless, throwing that away and
    orphaning a live process — the same outcome the PID-reuse work exists to
    prevent, reached through a different door.
    """
    manager = _manager(tmp_path, "stubborn", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\n", encoding="utf-8")

    # Refusal is gated on LIVENESS, not on stop()'s return value: a process
    # that exits mid-signal makes stop() report False while already being gone,
    # and refusing on that would block a removal that should succeed.
    monkeypatch.setattr(manager, "stop", lambda: False)
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: True)

    with pytest.raises(ManagedHostError, match="may still be alive"):
        manager.remove()

    assert manager.state_dir.exists(), "state dir deleted despite a failed stop"
    assert manager.pid_file.exists(), "pid file deleted — the process is now untracked"


def test_remove_deletes_state_once_the_process_is_gone(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, "gone", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\n", encoding="utf-8")

    monkeypatch.setattr(manager, "stop", lambda: True)
    monkeypatch.setattr(manager, "status", lambda: HostProcessStatus(running=False))

    manager.remove()
    assert not manager.state_dir.exists()


def test_remove_succeeds_when_stop_reports_failure_for_an_already_dead_process(
    tmp_path: Path, monkeypatch
) -> None:
    """`_signal` sees ProcessLookupError from both killpg and kill when the
    process exits mid-signal. That is an OSError, so `stop()` returns False for
    a process that is already gone — gating removal on it would refuse a
    teardown that should succeed, and `managed-host remove` has no handler."""
    manager = _manager(tmp_path, "raced", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("4242\n", encoding="utf-8")

    monkeypatch.setattr(manager, "stop", lambda: False)
    monkeypatch.setattr(manager, "status", lambda: HostProcessStatus(running=False))

    manager.remove()
    assert not manager.state_dir.exists()


# ── pid-file integrity (pass 15) ─────────────────────────────────────


@pytest.mark.parametrize(
    "recorded",
    ["0", "-1", "  0  ", "+0", "-99999", "0\nstart_utc=Thu Jan  1 00:00:00 1970"],
)
def test_a_non_positive_recorded_pid_is_refused(tmp_path, recorded):
    """`_signal` escalates to `os.killpg`, where 0 and -1 are WILDCARDS.

    `killpg(0, sig)` signals the CALLER's process group — `stop()` would
    SIGTERM and then SIGKILL the bootstrapper itself — and `os.kill(-1, sig)`
    broadcasts to every process this uid may signal. Neither is caught
    downstream: `_pid_alive(0)` returns True, because `kill(0, 0)` succeeds
    against our own group.
    """
    manager = ManagedHostManager(_spec(), tmp_path)
    manager.pid_file.parent.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text(recorded + "\n", encoding="utf-8")

    assert manager._read_pid() is None
    # ...and a pid that cannot be read is reported not-running, not signalled.
    assert manager.status().running is False


def test_a_positive_recorded_pid_still_parses(tmp_path):
    manager = ManagedHostManager(_spec(), tmp_path)
    manager.pid_file.parent.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("12345\nstart_utc=x\n", encoding="utf-8")
    assert manager._read_pid() == 12345


def test_the_pid_file_is_written_atomically(tmp_path, monkeypatch):
    """A torn write silently disables the PID-reuse guard.

    `write_text` truncates and then writes, and `start_utc=` lands after the
    pid in the same call — so a reader arriving mid-write sees a pid with no
    stamp. `_recorded_start_time` returns None and `_pid_is_stranger` treats
    ownership as untrusted. Atomic replacement prevents the torn state while
    retaining the fail-closed signal guard.
    """
    manager = ManagedHostManager(_spec(), tmp_path)
    manager.pid_file.parent.mkdir(parents=True, exist_ok=True)
    manager.pid_file.write_text("stale\n", encoding="utf-8")

    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        seen.append(str(dst))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    monkeypatch.setattr(ManagedHostManager, "_process_start_time", staticmethod(lambda pid: "STAMP"))
    manager._write_pid_file(4242)

    # The visible file is never a partial state: it is swapped in by rename.
    assert str(manager.pid_file) in seen
    body = manager.pid_file.read_text(encoding="utf-8")
    assert body.splitlines() == ["4242", "start_utc=STAMP"]


def test_a_failed_pid_file_write_does_not_orphan_the_child(tmp_path, monkeypatch):
    """Otherwise the child runs on, untracked, still holding the port."""
    manager = ManagedHostManager(_spec(command=("sleep", "300")), tmp_path)

    class FakeProcess:
        pid = 999_999
        signalled: list[int] = []

        def poll(self):
            return None if not self.signalled else 0

        def send_signal(self, sig):
            self.signalled.append(sig)

        def wait(self, timeout=None):
            if not self.signalled:
                raise subprocess.TimeoutExpired("cmd", timeout)
            return 0

    fake = FakeProcess()
    monkeypatch.setattr(ManagedHostManager, "_spawn", lambda self: fake)
    monkeypatch.setattr(
        ManagedHostManager,
        "_write_pid_file",
        lambda self, pid: (_ for _ in ()).throw(OSError("read-only state dir")),
    )
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(ManagedHostError) as excinfo:
        manager.start(wait_timeout=1.0)

    assert "pid file" in str(excinfo.value)
    assert "terminated" in str(excinfo.value).lower()
    assert killed and killed[0] == (fake.pid, signal.SIGTERM)


def test_missing_launch_identity_does_not_orphan_the_child(tmp_path, monkeypatch):
    """A live child must be terminated if its durable identity is unavailable."""
    manager = ManagedHostManager(_spec(command=("sleep", "300")), tmp_path)

    class FakeProcess:
        pid = 999_998

    fake = FakeProcess()
    terminated: list[int] = []
    monkeypatch.setattr(ManagedHostManager, "_spawn", lambda self: fake)
    monkeypatch.setattr(
        ManagedHostManager, "_process_start_time", staticmethod(lambda _pid: None)
    )
    monkeypatch.setattr(
        ManagedHostManager,
        "_terminate_untracked",
        staticmethod(lambda process: terminated.append(process.pid) or True),
    )
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(ManagedHostError, match="identity.*could not be recorded"):
        manager.start(wait_timeout=1.0)

    assert terminated == [fake.pid]
    assert not manager.pid_file.exists()


# ── pass 15: a leader can die while its group lives ──────────────────


def test_stop_sweeps_a_group_whose_leader_died(tmp_path):
    """`_spawn` uses `start_new_session=True` so the TREE is killable.

    `stop()` short-circuited on the leader being dead and never signalled the
    group, so a double-forking command — or a crashed gunicorn/uvicorn master
    whose workers survive — left the port held forever: `status()` reports
    not-running, `stop()` reports success, and every later `start()` fails to
    bind. A permanent wedge.
    """
    leader = subprocess.Popen(
        [sys.executable, "-c",
         "import os,sys,time\n"
         "if os.fork()==0:\n"
         "    time.sleep(120)\n"
         "sys.exit(0)\n"],
        start_new_session=True,
    )
    gid = leader.pid
    leader.wait()
    time.sleep(0.5)

    try:
        assert ManagedHostManager._pid_alive(gid) is False, "precondition: leader is dead"
        assert ManagedHostManager._group_survives(gid) is True, "precondition: group lives"

        manager = ManagedHostManager(_spec(), tmp_path)
        manager.pid_file.parent.mkdir(parents=True, exist_ok=True)
        manager.pid_file.write_text(f"{gid}\n", encoding="utf-8")
        # Current in-memory launch ownership is what makes signalling the
        # leaderless PGID safe. A stale pid file alone must fail closed.
        manager._untracked_pid = gid

        assert manager.stop() is True
        assert ManagedHostManager._group_survives(gid) is False, "group not swept"
    finally:
        try:
            os.killpg(gid, signal.SIGKILL)
        except OSError:
            pass


def test_a_live_or_unreadable_pid_is_never_swept():
    """Signalling a group whose leader is merely UNREADABLE hits a stranger.

    The sweep is safe only because it proves the leader is genuinely gone
    first: POSIX keeps a pid allocated while it is still referenced as a pgid,
    so the kernel cannot have handed that number to an unrelated process while
    members of the group remain.
    """
    # our own pid exists, so it is never treated as a leaderless remnant
    assert ManagedHostManager._group_survives(os.getpid()) is False
    # pid 1 exists and is not signalable by us — also never swept
    assert ManagedHostManager._group_survives(1) is False


def test_an_ambiguous_start_time_probe_is_treated_as_unknowable(tmp_path, monkeypatch):
    """A two-line `ps` answer wrote extra pid-file lines.

    `_recorded_start_time` reads only the first `start_utc=` line, so a
    multi-line stamp could never match a later probe — and our own live
    process was disowned as a stranger.
    """
    monkeypatch.setattr(sys, "platform", "darwin")

    class TwoLines:
        returncode = 0
        stdout = "Mon Jan  1 00:00:00 2024\nMon Jan  2 00:00:00 2024\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: TwoLines())
    assert ManagedHostManager._process_start_time(1234) is None

    class OneLine:
        returncode = 0
        stdout = "  Mon Jan  1 00:00:00 2024  \n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: OneLine())
    assert ManagedHostManager._process_start_time(1234) == "Mon Jan  1 00:00:00 2024"


def test_linux_process_identity_uses_boot_relative_start_ticks(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    # Fields after the final ')' begin at proc-stat field 3. Field 22 is the
    # twentieth token here; a comm containing spaces and ')' must not shift it.
    stat = "4242 (worker ) with spaces) " + " ".join(
        ["S", *("0" for _ in range(18)), "987654"]
    )
    original_read_text = Path.read_text

    def read_text(path, *args, **kwargs):
        if str(path) == "/proc/4242/stat":
            return stat
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Linux identity must not use wall time"),
    )

    assert ManagedHostManager._process_start_time(4242) == (
        "linux-proc-start-v1:987654"
    )


def test_linux_identity_mismatch_fails_closed_as_a_recycled_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    manager = _manager(tmp_path, "identity", (sys.executable, "-c", "pass"))
    manager.state_dir.mkdir(parents=True)
    manager.pid_file.write_text(
        "4242\nstart_utc=linux-proc-start-v1:111\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        manager, "_process_start_time", lambda _pid: "linux-proc-start-v1:222"
    )
    assert manager._pid_is_stranger(4242) is True


def test_lifecycle_lock_timeout_is_bounded(tmp_path, monkeypatch):
    import services.managed_host as managed_host

    if managed_host.fcntl is None:
        pytest.skip("fcntl lock timeout is POSIX-only")
    manager = ManagedHostManager(_spec(), tmp_path)
    ticks = iter([0.0, 31.0])
    monkeypatch.setattr(managed_host.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(managed_host.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        managed_host.fcntl,
        "flock",
        lambda *_args: (_ for _ in ()).throw(BlockingIOError()),
    )

    with pytest.raises(ManagedHostError, match="timed out waiting"):
        manager.start()


def test_remove_does_not_self_deadlock_on_the_lifecycle_lock(tmp_path):
    """flock is per open-file description: a second acquisition would hang.

    `remove()` takes the public lifecycle lock once and calls `_stop_locked()`;
    it must never re-enter the public `stop()` wrapper.
    """
    manager = ManagedHostManager(_spec(), tmp_path)
    manager.state_dir.mkdir(parents=True, exist_ok=True)
    manager.remove()  # must return, not hang
