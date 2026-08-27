"""Security and portability regressions for consumer-managed host processes."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from contextlib import suppress
from pathlib import Path

import pytest
import yaml

from core.consumer_manifest import ConsumerManifestError, load_consumer_config
from core.endpoints_contract import build_export
from services import _listener_address_matches, legacy_process_start_identity
from services.blender_mcp_manager import BlenderMcpManager
from services.managed_host import (
    HealthProbe,
    HostProcessSpec,
    ManagedHostError,
    ManagedHostManager,
)


def _spec(**overrides) -> HostProcessSpec:
    values = {"name": "safe-service", "command": ("python3", "-c", "pass"), "port": 8123}
    values.update(overrides)
    return HostProcessSpec(**values)


def _load(tmp_path: Path, service: dict):
    root = tmp_path / "consumer"
    (root / "app").mkdir(parents=True)
    manifest = root / "atlas.consumer.yml"
    manifest.write_text(
        yaml.safe_dump({"name": "consumer", "managed_host_services": [service]}),
        encoding="utf-8",
    )
    return load_consumer_config(tmp_path, explicit_paths=[str(manifest)])


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_remove_preserves_state_when_live_owner_survives_failed_stop(
    tmp_path, monkeypatch
):
    manager = ManagedHostManager(_spec(), tmp_path)
    manager.pid_file.write_text("999999\n", encoding="utf-8")

    class OwnedProcess:
        pid = 4242

        def poll(self):
            return None

    owned = OwnedProcess()
    manager._owned_process = owned
    manager._owned_group_pid = owned.pid
    monkeypatch.setattr(manager, "_signal", lambda *_args: False)
    monkeypatch.setattr(
        manager,
        "_managed_process_alive",
        lambda pid: pid == owned.pid,
    )

    with pytest.raises(ManagedHostError, match="refusing to remove"):
        manager.remove()

    assert manager.state_dir.exists()
    assert manager._owned_process is owned


def test_remove_preserves_leaderless_group_until_sweep_succeeds(
    tmp_path, monkeypatch
):
    manager = ManagedHostManager(_spec(), tmp_path)

    class ExitedProcess:
        pid = 4242

        def poll(self):
            return 0

    manager._owned_process = ExitedProcess()
    manager._owned_group_pid = 4242
    monkeypatch.setattr(manager, "_pid_alive", lambda _pid: False)
    monkeypatch.setattr(manager, "_group_survives", lambda _pid: True)
    sweep_succeeds = False
    monkeypatch.setattr(
        manager,
        "_sweep_orphaned_group",
        lambda _pid: sweep_succeeds,
    )
    monkeypatch.setattr(
        manager,
        "_managed_process_alive",
        lambda pid: pid == manager._owned_group_pid,
    )

    with pytest.raises(ManagedHostError, match="refusing to remove"):
        manager.remove()
    assert manager.state_dir.exists()
    assert manager._owned_group_pid == 4242

    sweep_succeeds = True
    manager.remove()
    assert not manager.state_dir.exists()
    assert manager._owned_group_pid is None


def test_workdir_relative_executable_passes_preflight_and_launches(tmp_path):
    workdir = tmp_path / "app"
    workdir.mkdir()
    script = workdir / "service"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    manager = ManagedHostManager(
        _spec(command=("./service",), workdir=workdir), tmp_path / "state"
    )
    result = manager.preflight()
    assert next(c for c in result.checks if c["name"] == "command")["status"] == "ok"
    manager.state_dir.mkdir(parents=True)
    assert manager._spawn().wait(timeout=5) == 0


def test_atlas_owned_environment_cannot_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_MANAGED_HOST_BIND", "inherited")
    manager = ManagedHostManager(
        _spec(
            env={
                "ATLAS_MANAGED_HOST_NAME": "spoofed",
                "ATLAS_MANAGED_HOST_PORT": "9999",
                "ATLAS_MANAGED_HOST_BIND": "0.0.0.0",
            }
        ),
        tmp_path,
    )
    child_env = manager._child_env()
    assert child_env["ATLAS_MANAGED_HOST_NAME"] == "safe-service"
    assert child_env["ATLAS_MANAGED_HOST_PORT"] == "8123"
    assert child_env["ATLAS_MANAGED_HOST_BIND"] == "127.0.0.1"


def test_loopback_declared_child_binding_wildcard_is_rejected_and_cleaned_up(tmp_path):
    port = _free_port()
    server = textwrap.dedent(
        """
        import http.server, sys
        http.server.ThreadingHTTPServer(
            ("0.0.0.0", int(sys.argv[1])), http.server.SimpleHTTPRequestHandler
        ).serve_forever()
        """
    )
    manager = ManagedHostManager(
        _spec(command=(sys.executable, "-c", server, str(port)), port=port),
        tmp_path / "state",
    )
    try:
        with pytest.raises(ManagedHostError, match="did not become healthy"):
            manager.start(wait_timeout=0.5)
    finally:
        pid = manager._read_pid()
        if not manager.stop() and pid is not None:
            with suppress(OSError):
                os.killpg(pid, signal.SIGKILL)
            deadline = time.monotonic() + 2
            while manager._pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.02)
    assert not manager.pid_file.exists()
    assert not manager._port_in_use(timeout=0.1)


@pytest.mark.parametrize(
    ("actual", "declared", "matches"),
    [
        ("0.0.0.0", "127.0.0.1", False), ("*", "127.0.0.1", False),
        ("0.0.0.0", "0.0.0.0", True), ("::", "::1", False),
        ("*", "::1", False), ("::", "::", True),
    ],
)
def test_listener_wildcard_matches_only_a_declared_wildcard(actual, declared, matches):
    assert _listener_address_matches(actual, 6 if ":" in declared else 4, declared) is matches


@pytest.mark.parametrize("value", [True, False])
def test_allow_remote_accepts_only_real_yaml_booleans(tmp_path, value):
    spec = _load(
        tmp_path, {"name": "svc", "command": "app", "port": 9001, "allow_remote": value}
    ).managed_host_services[0]
    assert spec.allow_remote is value


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_allow_remote_rejects_non_boolean_values(tmp_path, value):
    with pytest.raises(ConsumerManifestError, match="allow_remote must be a boolean"):
        _load(
            tmp_path,
            {"name": "svc", "command": "app", "port": 9001, "allow_remote": value},
        )


@pytest.mark.parametrize("value", [True, False])
def test_venv_metal_accepts_only_real_yaml_booleans(tmp_path, value):
    spec = _load(
        tmp_path,
        {
            "name": "svc",
            "command": "app",
            "port": 9001,
            "venv": {"metal": value},
        },
    ).managed_host_services[0]
    assert spec.venv is not None
    assert spec.venv.metal is value


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_venv_metal_rejects_non_boolean_values(tmp_path, value):
    with pytest.raises(ConsumerManifestError, match=r"venv\.metal must be a boolean"):
        _load(
            tmp_path,
            {
                "name": "svc",
                "command": "app",
                "port": 9001,
                "venv": {"metal": value},
            },
        )


@pytest.mark.parametrize("manager_kind", ["generic", "blender"])
def test_live_legacy_linux_identity_remains_owned_during_upgrade(
    tmp_path, manager_kind
):
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        manager = (
            ManagedHostManager(_spec(), tmp_path / "generic")
            if manager_kind == "generic"
            else BlenderMcpManager(tmp_path / "blender")
        )
        manager.state_dir.mkdir(parents=True, exist_ok=True)
        legacy = legacy_process_start_identity(process.pid)
        assert legacy and not legacy.startswith("linux-proc-start-v1:")
        manager.pid_file.write_text(
            f"{process.pid}\nstart_utc={legacy}\n", encoding="utf-8"
        )
        assert manager._pid_is_stranger(process.pid) is False
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


@pytest.mark.parametrize("value", [None, ["A=B"], "A=B", 42])
def test_managed_host_env_must_be_a_mapping(tmp_path, value):
    with pytest.raises(ConsumerManifestError, match=r"\.env must be a mapping"):
        _load(tmp_path, {"name": "svc", "command": "app", "port": 9001, "env": value})


def test_managed_host_env_mapping_is_normalized(tmp_path):
    spec = _load(
        tmp_path,
        {"name": "svc", "command": "app", "port": 9001, "env": {"COUNT": 3}},
    ).managed_host_services[0]
    assert spec.env == {"COUNT": "3"}


@pytest.mark.parametrize(
    ("bind", "host"),
    [
        ("192.0.2.10", "192.0.2.10"), ("2001:db8::10", "[2001:db8::10]"),
        ("0.0.0.0", "127.0.0.1"), ("::", "[::1]"),
    ],
)
def test_managed_host_endpoints_advertise_a_reachable_host(bind, host):
    spec = _spec(bind=bind, allow_remote=True)
    expected = f"tcp://{host}:8123"
    assert ManagedHostManager(spec, "/tmp/unused-managed-host-state").endpoint() == expected
    fields = {field.name: field.value for field in build_export({}, host_services=[spec])}
    assert fields["ATLAS_SAFE_SERVICE_HOST_ENDPOINT"] == expected


@pytest.mark.parametrize(
    ("health", "expected"),
    [
        (HealthProbe(kind="http", path="/health"), "http://127.0.0.1:8123"),
        (HealthProbe(kind="tcp"), "tcp://127.0.0.1:8123"),
    ],
)
def test_endpoint_scheme_matches_the_declared_probe(health, expected):
    spec = _spec(health=health)
    fields = {field.name: field.value for field in build_export({}, host_services=[spec])}
    assert fields["ATLAS_SAFE_SERVICE_HOST_ENDPOINT"] == expected
