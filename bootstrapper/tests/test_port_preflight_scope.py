"""Port pre-flight must probe only ports something will actually bind.

`handle_port_configuration` turns any conflict into `return False`, and both
the linear and Textual pipelines abort on it — so a port probed for a service
that ships disabled is a launch blocker for no reason.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _manager(tmp_path: Path, env_body: str):
    from core.port_manager import PortManager

    (tmp_path / ".env").write_text(env_body, encoding="utf-8")
    manager = PortManager(str(REPO_ROOT))
    manager.config_parser.env_file_path = tmp_path / ".env"
    return manager


def test_ports_of_disabled_services_are_not_probed(tmp_path):
    """Roughly half the probed ports belong to services shipping disabled.

    Airflow, Grafana, Prometheus, Ray, Spark, Trino, Jenkins, Zeppelin,
    Redpanda, Langfuse and MLflow all default to `disabled`; nothing will ever
    bind their ports, yet an unrelated host process on one aborted `./start.sh`.
    """
    manager = _manager(tmp_path, "GRAFANA_SOURCE=disabled\nPROMETHEUS_SOURCE=disabled\n")
    assignments = manager.calculate_port_assignments(63000)
    grafana_port = assignments["GRAFANA_PORT"]

    # Drive the REAL entry point with the port occupied, rather than asserting
    # on the private helper — a test that only calls the helper stays green
    # even if the call site stops consulting it.
    occupied = {grafana_port}
    manager.check_port_availability = lambda port: port not in occupied

    assert manager.get_port_conflicts(63000) == {}, (
        "a disabled service's port aborted the launch"
    )
    assert grafana_port not in manager.check_port_range_availability(63000)


def test_an_enabled_service_is_still_probed(tmp_path):
    manager = _manager(tmp_path, "GRAFANA_SOURCE=container\n")
    grafana_port = manager.calculate_port_assignments(63000)["GRAFANA_PORT"]
    manager.check_port_availability = lambda port: port != grafana_port

    assert manager.get_port_conflicts(63000) == {"GRAFANA_PORT": grafana_port}
    assert grafana_port in manager.check_port_range_availability(63000)


def test_unreadable_sources_fail_open(tmp_path):
    """Skipping nothing is the safe direction — probe everything."""
    manager = _manager(tmp_path, "")
    grafana_port = manager.calculate_port_assignments(63000)["GRAFANA_PORT"]
    manager.check_port_availability = lambda port: port != grafana_port
    assert manager.get_port_conflicts(63000) == {"GRAFANA_PORT": grafana_port}


def test_the_assignment_pattern_does_not_run_past_a_blank_value():
    """`\\s*` around the `=` matches a NEWLINE.

    Under `re.MULTILINE` the `$` matches before a newline, but `\\s` matches the
    newline itself — so for a blank `VAR=` the value group ran into the
    following line and swallowed the whole next assignment.

    Scope, stated honestly: no current caller reaches this. `update_env_ports`
    leaves a blank value alone, and I could not reproduce end-to-end corruption
    through it. This pins the pattern's own contract so the trap cannot be
    sprung by a future caller.
    """
    import re

    from core.port_manager import _assignment_pattern

    text = "KONG_HTTP_PORT=\nKONG_HTTPS_PORT=63001\n"
    match = re.search(_assignment_pattern("KONG_HTTP_PORT"), text, re.MULTILINE)
    assert match is not None
    assert match.group(2) == "", "the value group ran into the next line"

    rewritten = re.sub(
        _assignment_pattern("KONG_HTTP_PORT"),
        lambda m: m.group(1) + "64000" + m.group(3),
        text,
        flags=re.MULTILINE,
    )
    assert rewritten == "KONG_HTTP_PORT=64000\nKONG_HTTPS_PORT=63001\n"


def test_the_assignment_pattern_still_handles_comments_and_padding():
    import re

    from core.port_manager import _assignment_pattern

    for line, value in [
        ("N8N_PORT=63075", "63075"),
        ("N8N_PORT=63075   ", "63075"),
        ("N8N_PORT=63075  # the n8n UI", "63075"),
        ("N8N_PORT = 63075", "63075"),
    ]:
        match = re.search(_assignment_pattern("N8N_PORT"), line, re.MULTILINE)
        assert match is not None, line
        assert match.group(2) == value, line


def test_secondary_ports_of_a_disabled_service_are_not_probed(tmp_path):
    """The topology row names one port per service; Ray also publishes GCS
    and client ports. A squatter on RAY_GCS_PORT aborted a Ray-disabled start."""
    manager = _manager(tmp_path, "RAY_SOURCE=disabled\n")
    assignments = manager.calculate_port_assignments(63000)
    secondary = {assignments["RAY_GCS_PORT"], assignments["RAY_CLIENT_PORT"]}
    manager.check_port_availability = lambda port: port not in secondary

    conflicts = manager.get_port_conflicts(63000)
    assert "RAY_GCS_PORT" not in conflicts and "RAY_CLIENT_PORT" not in conflicts


def test_ports_of_host_run_sources_are_not_probed(tmp_path):
    # A localhost source scales the container to 0, so its slot never binds.
    manager = _manager(tmp_path, "COMFYUI_SOURCE=localhost\n")
    comfy_port = manager.calculate_port_assignments(63000)["COMFYUI_PORT"]
    manager.check_port_availability = lambda port: port != comfy_port
    assert "COMFYUI_PORT" not in manager.get_port_conflicts(63000)


def test_time_wait_is_not_a_conflict_but_a_live_listener_is(tmp_path):
    # Docker binds through TIME_WAIT (SO_REUSEADDR); the strict probe did not,
    # so a just-closed Kong connection aborted the restart it had just stopped.
    import socket

    from core.port_manager import PortManager

    manager = PortManager(str(REPO_ROOT))
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    accepted, _ = server.accept()
    server.close()
    accepted.close()  # server side closes first -> TIME_WAIT on `port`
    client.close()
    assert manager.check_port_availability(port) is True

    live = socket.socket()
    live.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as Docker does
    live.bind(("127.0.0.1", port))
    live.listen(1)
    try:
        assert manager.check_port_availability(port) is False
    finally:
        live.close()



def test_ipv6_time_wait_is_not_a_conflict():
    # The strict IPv4 probe was still held when the fallback ran, so a ::1
    # TIME_WAIT (a browser hitting localhost over IPv6) stayed a conflict.
    import socket

    import pytest

    from core.port_manager import PortManager

    if not socket.has_ipv6:
        pytest.skip("no IPv6")
    server = socket.socket(socket.AF_INET6)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("::1", 0))
    except OSError:
        pytest.skip("no ::1")
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("::1", port))
    accepted, _ = server.accept()
    server.close()
    accepted.close()
    client.close()
    assert PortManager(str(REPO_ROOT)).check_port_availability(port) is True


def test_listener_on_the_configured_host_bind_ip_is_a_conflict(tmp_path):
    # macOS lets the reusable wildcard/loopback probes coexist with a live
    # listener on a LAN address, which is exactly where compose binds when
    # HOST_BIND_IP names it.
    import socket

    import pytest

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        lan_ip = probe.getsockname()[0]
    except OSError:
        pytest.skip("no routable IPv4 address")
    finally:
        probe.close()
    if lan_ip.startswith("127."):
        pytest.skip("no non-loopback IPv4 address")
    manager = _manager(tmp_path, f"HOST_BIND_IP={lan_ip}:\n")
    live = socket.socket()
    live.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    live.bind((lan_ip, 0))
    live.listen(1)
    try:
        assert manager.check_port_availability(live.getsockname()[1]) is False
    finally:
        live.close()


def test_a_just_released_port_does_not_abort_the_warm_start(tmp_path, monkeypatch):
    """Right after compose down, Docker Desktop frees published ports a moment
    later; the immediate re-probe reported the stack's own BACKEND_PORT as in
    use and the start aborted with every container down (#1438)."""
    import core.port_manager as port_manager_module

    manager = _manager(tmp_path, "")
    backend_port = manager.calculate_port_assignments(63000)["BACKEND_PORT"]
    probes = {"count": 0}

    def available(port):
        if port != backend_port:
            return True
        probes["count"] += 1
        return probes["count"] > 3  # released on the fourth probe

    manager.check_port_availability = available
    monkeypatch.setattr(port_manager_module.time, "sleep", lambda _s: None)
    assert manager.conflicts_after_release(63000) == {}

    # A foreign listener still conflicts once the wait runs out.
    manager.check_port_availability = lambda port: port != backend_port
    assert manager.conflicts_after_release(63000, timeout_s=0.0) == {"BACKEND_PORT": backend_port}


def test_the_warm_start_rechecks_ports_through_the_release_wait():
    source = (REPO_ROOT / "bootstrapper" / "start.py").read_text(encoding="utf-8")
    stop = source.index("Previous instance stopped successfully")
    assert "self.port_manager.conflicts_after_release(base_port)" in source[stop:stop + 600]


def test_a_cold_start_waits_for_released_ports_too(tmp_path, monkeypatch):
    """The release wait ran only when project containers were running at the
    port check; a cold start had already stopped them, so a port Docker was
    still releasing aborted the start (2026-10-08 run, cycle 7)."""
    from start import AtlasStarter

    starter = AtlasStarter()
    calls = []
    monkeypatch.setattr(starter.port_manager, "get_port_conflicts", lambda bp: {"BACKEND_PORT": bp + 20})
    monkeypatch.setattr(
        starter.port_manager, "conflicts_after_release", lambda bp: calls.append(bp) or {}
    )
    monkeypatch.setattr(starter.port_manager, "update_env_ports", lambda bp: True)
    monkeypatch.setattr(starter, "_port_block_moves", lambda bp: False)
    starter.project_stopped_this_run = True
    assert starter.handle_port_configuration(63000) is True
    assert calls == [63000]


def test_the_test_suite_cannot_stop_a_real_stack():
    from core.docker_manager import DockerManager

    assert DockerManager().are_project_containers_running() is False
