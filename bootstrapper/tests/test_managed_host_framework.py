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
import socket
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
    with pytest.raises(ManagedHostError, match="did not open"):
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


def test_an_http_service_exports_an_http_endpoint():
    spec = _spec(port=8799, health=HealthProbe(kind="http", path="/health"))
    fields = {f.name: f.value for f in build_export({}, host_services=[spec])}
    assert fields["ATLAS_SAM3_SEGMENT_HOST_ENDPOINT"] == "http://localhost:8799"


def test_a_tcp_service_exports_a_tcp_endpoint():
    """Exporting `http://` for a raw-socket service hands a consumer a URL no
    HTTP client can use — the same trap ATLAS_BLENDER_MCP_HOST_ENDPOINT
    already avoids."""
    spec = _spec(port=9876, health=HealthProbe(kind="tcp"))
    fields = {f.name: f.value for f in build_export({}, host_services=[spec])}
    assert fields["ATLAS_SAM3_SEGMENT_HOST_ENDPOINT"] == "tcp://localhost:9876"


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
    manager.pid_file.write_text(str(process.pid), encoding="utf-8")

    assert manager.status().running is True
    assert manager.stop() is True
    assert manager.status().running is False
