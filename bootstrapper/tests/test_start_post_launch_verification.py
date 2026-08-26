from __future__ import annotations

import select
import socket
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import services as services_module
from services import blender_mcp_manager
from services.blender_mcp_manager import BlenderMcpManager
from services.managed_host import HealthProbe, HostProcessSpec, ManagedHostManager


@contextmanager
def _managed_child(*args, **kwargs):
    child = subprocess.Popen(*args, **kwargs)
    try:
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=5)


def _free_ipv6_port() -> int:
    with socket.socket(socket.AF_INET6) as reservation:
        try:
            reservation.bind(("::", 0))
        except OSError as exc:
            pytest.skip(f"IPv6 wildcard binding is unavailable: {exc}")
        return reservation.getsockname()[1]


def _minimal_port_env(**overrides):
    env = {
        "SUPABASE_DB_PORT": "",
        "REDIS_PORT": "",
        "SUPABASE_META_PORT": "",
        "SUPABASE_STORAGE_PORT": "",
        "SUPABASE_AUTH_PORT": "",
        "SUPABASE_API_PORT": "",
        "SUPABASE_REALTIME_PORT": "",
        "SUPABASE_STUDIO_PORT": "",
        "GRAPH_DB_PORT": "",
        "WEAVIATE_PORT": "63020",
        "WEAVIATE_SOURCE": "container",
        "WEAVIATE_SCALE": "1",
        "LOCAL_DEEP_RESEARCHER_PORT": "",
        "OPEN_WEB_UI_PORT": "",
        "BACKEND_PORT": "",
        "KONG_HTTP_PORT": "",
        "KONG_HTTPS_PORT": "",
        "N8N_PORT": "",
        "SEARXNG_PORT": "",
        "JUPYTERHUB_PORT": "",
        "LITELLM_PORT": "",
        "COMFYUI_SCALE": "0",
    }
    env.update(overrides)
    return env


def test_port_verification_skips_disabled_services(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    env = _minimal_port_env(WEAVIATE_SOURCE="disabled", WEAVIATE_SCALE="0")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(starter.config_parser, "parse_env_file", lambda: env)
    monkeypatch.setattr(
        starter.docker_manager,
        "get_service_port",
        lambda service, port: calls.append((service, port)) or "",
    )

    starter.show_container_status_and_verify_ports(on_line=lambda _msg, _level: None)

    assert ("weaviate", "8080") not in calls


def test_port_verification_skips_localhost_services(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    env = _minimal_port_env(WEAVIATE_SOURCE="localhost", WEAVIATE_SCALE="1")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(starter.config_parser, "parse_env_file", lambda: env)
    monkeypatch.setattr(
        starter.docker_manager,
        "get_service_port",
        lambda service, port: calls.append((service, port)) or "",
    )

    starter.show_container_status_and_verify_ports(on_line=lambda _msg, _level: None)

    assert ("weaviate", "8080") not in calls


def test_one_shot_init_failure_fails_startup(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {"N8N_INIT_SCALE": "1"},
    )
    monkeypatch.setattr(
        starter.docker_manager,
        "failed_one_shot_services",
        lambda services, **_kwargs: [("n8n-init", "exit 1")],
    )

    assert starter.verify_one_shot_init_containers() is False


def test_one_shot_init_skipped_when_scale_zero(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "N8N_INIT_SCALE": "0",
            "OPEN_WEB_UI_INIT_SCALE": "0",
            "COMFYUI_INIT_SCALE": "0",
        },
    )
    def fail_if_called(_services, **_kwargs):
        raise AssertionError("disabled init container should not be inspected")

    monkeypatch.setattr(starter.docker_manager, "failed_one_shot_services", fail_if_called)

    assert starter.verify_one_shot_init_containers() is True


def test_one_shot_init_checks_enabled_post_start_init_services(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "N8N_INIT_SCALE": "1",
            "OPEN_WEB_UI_INIT_SCALE": "1",
            "COMFYUI_INIT_SCALE": "1",
            "REDPANDA_INIT_SCALE": "1",
            "ZEPPELIN_INIT_SCALE": "1",
        },
    )
    monkeypatch.setattr(
        starter.config_parser,
        "load_consumer_config",
        lambda: type("ConsumerConfig", (), {"n8n_workflows": [object()]})(),
    )
    calls = []

    def fake_failed_one_shot_services(services, **kwargs):
        calls.append((services, kwargs))
        return []

    monkeypatch.setattr(
        starter.docker_manager,
        "failed_one_shot_services",
        fake_failed_one_shot_services,
    )

    assert starter.verify_one_shot_init_containers() is True
    assert calls == [
        (
            [
                "n8n-init",
                "open-webui-init",
                "comfyui-init",
                "redpanda-init",
                "zeppelin-init",
                "n8n-seed",
            ],
            {"timeout_seconds": 900.0, "poll_interval_seconds": 5.0},
        )
    ]


def test_one_shot_waits_for_terminal_failure(monkeypatch):
    from core.docker_manager import DockerManager

    dm = DockerManager()
    states = iter([
        ([{"State": "running", "Status": "Up 1 second", "ExitCode": ""}], None),
        ([{"State": "exited", "Status": "Exited (1)", "ExitCode": "1"}], None),
    ])
    monkeypatch.setattr(dm, "_compose_ps_json", lambda _service: next(states))

    failures = dm.failed_one_shot_services(
        ["n8n-init"],
        timeout_seconds=5,
        poll_interval_seconds=0,
    )

    assert failures == [("n8n-init", "exit 1: Exited (1)")]


def test_one_shot_times_out_when_not_terminal(monkeypatch):
    from core.docker_manager import DockerManager

    dm = DockerManager()
    monkeypatch.setattr(
        dm,
        "_compose_ps_json",
        lambda _service: ([{"State": "running", "Status": "Up", "ExitCode": ""}], None),
    )

    failures = dm.failed_one_shot_services(
        ["n8n-init"],
        timeout_seconds=0,
        poll_interval_seconds=0,
    )

    assert failures == [("n8n-init", "timed out waiting for terminal state (Up)")]


def test_blender_live_child_cannot_adopt_foreign_tcp_listener(
    tmp_path, monkeypatch,
):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        manager = BlenderMcpManager(tmp_path / "blender-foreign", port=port)
        monkeypatch.setattr(manager, "health", lambda **_kwargs: {"reachable": True})
        with _managed_child(["sleep", "5"], start_new_session=True) as child:
            assert manager._await_spawned_readiness(child, 0.05) is None
            assert child.poll() is None


def test_blender_accepts_child_owned_listener(tmp_path, monkeypatch):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    server = textwrap.dedent("""
        import socket, sys, time
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", int(sys.argv[1])))
        listener.listen()
        print("READY", flush=True)
        time.sleep(10)
    """)
    manager = BlenderMcpManager(tmp_path / "blender-owned", port=port)
    monkeypatch.setattr(manager, "health", lambda **_kwargs: {"reachable": True})
    with _managed_child(
        [sys.executable, "-c", server, str(port)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as child:
        assert child.stdout is not None
        ready, _, _ = select.select([child.stdout], [], [], 5)
        assert ready and child.stdout.readline().strip() == "READY"
        status = manager._await_spawned_readiness(child, 2)
        assert status is not None
        assert status.pid == child.pid and status.port_open is True


def test_generic_start_reaches_ipv6_wildcard_listener(tmp_path):
    port = _free_ipv6_port()
    server = textwrap.dedent("""
        import json, socket, sys
        from http.server import BaseHTTPRequestHandler, HTTPServer
        class IPv6Server(HTTPServer):
            address_family = socket.AF_INET6
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"status": "ok"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass
        IPv6Server(("::", int(sys.argv[1])), Handler).serve_forever()
    """)
    manager = ManagedHostManager(
        HostProcessSpec(
            name="ipv6-wildcard",
            command=(sys.executable, "-c", server, str(port)),
            port=port,
            bind="::",
            allow_remote=True,
            health=HealthProbe(
                kind="http", path="/health", expect_json={"status": "ok"}
            ),
        ),
        tmp_path / "generic-ipv6",
    )
    try:
        status = manager.start(wait_timeout=5)
        assert status.running and status.port_open
        assert manager._probe_host() == "::1"
    finally:
        manager.stop()


def test_blender_readiness_reaches_ipv6_wildcard_listener(tmp_path):
    port = _free_ipv6_port()
    server = textwrap.dedent("""
        import json, socket, sys
        listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("::", int(sys.argv[1])))
        listener.listen()
        print("READY", flush=True)
        while True:
            connection, _address = listener.accept()
            with connection:
                connection.recv(4096)
                response = {"status": "success", "result": {}}
                connection.sendall((json.dumps(response) + "\\n").encode())
    """)
    manager = BlenderMcpManager(
        tmp_path / "blender-ipv6", port=port, bind="::", allow_remote=True
    )
    with _managed_child(
        [sys.executable, "-c", server, str(port)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as child:
        assert child.stdout is not None
        ready, _, _ = select.select([child.stdout], [], [], 5)
        assert ready and child.stdout.readline().strip() == "READY"
        status = manager._await_spawned_readiness(child, 2)
        assert status is not None and status.port_open
        assert manager._probe_host() == "::1"
        assert manager._port_in_use() is True


def _write_fake_blender_launcher(tmp_path, fail_socket_family=None):
    tmp_path.joinpath("bpy.py").write_text(
        "class Timers: pass\n"
        "class App: pass\n"
        "app = App()\n"
        "app.timers = Timers()\n",
        encoding="utf-8",
    )
    addon = tmp_path / "addon.py"
    addon.write_text(
        "def register(): pass\n"
        "class BlenderMCPServer:\n"
        "    def __init__(self, host, port):\n"
        "        self.host, self.port = host, port\n"
        "    def _server_loop(self): pass\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "launcher.py"
    launcher_text = blender_mcp_manager._LAUNCHER
    if fail_socket_family is not None:
        socket_failure = f"""
real_socket = socket_mod.socket
def reject_family(*args, **kwargs):
    if args and args[0] == {int(fail_socket_family)}:
        raise OSError("simulated unsupported address family")
    return real_socket(*args, **kwargs)
socket_mod.socket = reject_family
"""
        launcher_text = launcher_text.replace(
            "import socket as socket_mod\n",
            "import socket as socket_mod\n" + socket_failure,
        )
    launcher.write_text(launcher_text, encoding="utf-8")
    return addon, launcher


def _assert_fake_blender_launcher_serves(
    tmp_path, host, port, fail_socket_family=None,
):
    addon, launcher = _write_fake_blender_launcher(
        tmp_path, fail_socket_family=fail_socket_family
    )
    with _managed_child(
        [sys.executable, str(launcher), "--", str(addon), str(port), host],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as child:
        assert child.stdout is not None
        ready, _, _ = select.select([child.stdout], [], [], 5)
        assert ready, "launcher produced no ready line"
        output = child.stdout.readline().strip()
        if not output and child.poll() is not None:
            pytest.fail(child.stderr.read() if child.stderr else "launcher exited")
        assert output == f"atlas-blender-mcp: serving on {host}:{port}"


def test_generated_blender_launcher_binds_ipv6_loopback(tmp_path):
    with socket.socket(socket.AF_INET6) as reservation:
        try:
            reservation.bind(("::1", 0))
        except OSError as exc:
            pytest.skip(f"IPv6 loopback is unavailable: {exc}")
        port = reservation.getsockname()[1]
    _assert_fake_blender_launcher_serves(tmp_path, "::1", port)


def test_generated_blender_launcher_falls_back_across_localhost_addresses(tmp_path):
    candidates = socket.getaddrinfo("localhost", 0, type=socket.SOCK_STREAM)
    families = []
    for candidate in candidates:
        if candidate[0] not in {entry[0] for entry in families}:
            families.append(candidate)
    if len(families) < 2:
        pytest.skip("localhost does not resolve to two address families")
    first, second = families[:2]
    with socket.socket(first[0], first[1], first[2]) as blocker:
        blocker.bind(first[4])
        blocker.listen()
        port = blocker.getsockname()[1]
        second_address = socket.getaddrinfo(
            "localhost", port, family=second[0], type=socket.SOCK_STREAM
        )[0][4]
        with socket.socket(second[0], second[1], second[2]) as probe:
            probe.bind(second_address)
        _assert_fake_blender_launcher_serves(tmp_path, "localhost", port)


def test_generated_blender_launcher_survives_unsupported_first_family(tmp_path):
    candidates = socket.getaddrinfo("localhost", 0, type=socket.SOCK_STREAM)
    if len({candidate[0] for candidate in candidates}) < 2:
        pytest.skip("localhost does not resolve to two address families")
    second_family = next(
        candidate for candidate in candidates if candidate[0] != candidates[0][0]
    )
    with socket.socket(*second_family[:3]) as reservation:
        reservation.bind(second_family[4])
        port = reservation.getsockname()[1]
    _assert_fake_blender_launcher_serves(
        tmp_path,
        "localhost",
        port,
        fail_socket_family=candidates[0][0],
    )


@pytest.mark.parametrize(
    ("output", "bind", "expected"),
    [
        ("tIPv4\nn127.0.0.1:8123\n", "127.0.0.1", True),
        ("tIPv4\nn127.0.0.2:8123\n", "127.0.0.1", False),
        ("tIPv4\nn*:8123\n", "127.0.0.1", False),
        ("tIPv4\nn*:8123\n", "0.0.0.0", True),
        ("tIPv6\nn[::1]:8123\n", "::1", True),
        ("tIPv6\nn[::1]:8123\n", "127.0.0.1", False),
        ("tIPv6\nn[::1]:8124\n", "::1", False),
    ],
)
def test_lsof_listener_proof_is_endpoint_specific(output, bind, expected):
    assert services_module._lsof_output_has_endpoint(output, bind, 8123) is expected


def test_linux_listener_proof_falls_back_after_lsof_miss(monkeypatch):
    calls = []
    monkeypatch.setattr(services_module.sys, "platform", "linux")
    monkeypatch.setattr(services_module, "_lsof_path", lambda: "/usr/bin/lsof")
    monkeypatch.setattr(
        services_module.subprocess,
        "run",
        lambda *_args, **_kwargs: NS(returncode=1, stdout=""),
    )
    monkeypatch.setattr(
        services_module,
        "_linux_group_owns_listener",
        lambda *args: calls.append(args) or True,
    )

    assert services_module.process_group_owns_tcp_listener(
        4242, "127.0.0.1", 8123
    ) is True
    assert calls == [(4242, "127.0.0.1", 8123)]


@pytest.mark.parametrize(
    "case",
    [
        ("0100007F", 4, "127.0.0.1"),
        ("00000000000000000000000001000000", 6, "::1"),
    ],
)
def test_linux_proc_address_decoding(case):
    encoded, family, expected = case
    assert services_module._decode_proc_address(encoded, family) == expected


def test_linux_proc_table_filters_address_family_state_and_malformed_rows(
    monkeypatch,
):
    header = "sl local rem st queue timer retr uid timeout inode\n"
    row = "0: {address}:{port} 00000000:0000 {state} q t r 1000 0 {inode}\n"
    tables = {
        "/proc/net/tcp": header + "".join(
            [
                row.format(address="0100007F", port="1FBB", state="0A", inode="111"),
                row.format(address="0200007F", port="1FBB", state="0A", inode="112"),
                row.format(address="00000000", port="1FBB", state="0A", inode="113"),
                row.format(address="0100007F", port="1FBB", state="01", inode="114"),
                row.format(address="NOTHEX", port="1FBB", state="0A", inode="115"),
                "truncated row\n",
            ]
        ),
        "/proc/net/tcp6": header + row.format(
            address="00000000000000000000000001000000",
            port="1FBB",
            state="0A",
            inode="211",
        ),
    }
    real_read_text = Path.read_text

    def fake_read_text(path, *args, **kwargs):
        if str(path) in tables:
            return tables[str(path)]
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    assert services_module._linux_listening_socket_inodes(
        "127.0.0.1", 8123
    ) == {"111"}
    assert services_module._linux_listening_socket_inodes(
        "127.0.0.3", 8123
    ) == set()
    assert services_module._linux_listening_socket_inodes(
        "0.0.0.0", 8123
    ) == {"113"}
    assert services_module._linux_listening_socket_inodes("::1", 8123) == {"211"}


def test_linux_proc_table_tolerates_inaccessible_family(monkeypatch):
    header = "sl local rem st queue timer retr uid timeout inode\n"
    row = "0: 0100007F:1FBB 00000000:0000 0A q t r 1000 0 111\n"
    real_read_text = Path.read_text

    def fake_read_text(path, *args, **kwargs):
        if str(path) == "/proc/net/tcp":
            return header + row
        if str(path) == "/proc/net/tcp6":
            raise PermissionError("hidden")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    assert services_module._linux_listening_socket_inodes(
        "127.0.0.1", 8123
    ) == {"111"}


def test_linux_process_group_members_tolerates_bad_proc_entries(
    tmp_path, monkeypatch,
):
    matching = tmp_path / "101"
    mismatched = tmp_path / "102"
    malformed = tmp_path / "103"
    missing = tmp_path / "104"
    for process_dir in (matching, mismatched, malformed, missing):
        process_dir.mkdir()
    matching.joinpath("stat").write_text(
        "101 (worker name) S 1 4242 0\n", encoding="utf-8"
    )
    mismatched.joinpath("stat").write_text(
        "102 (worker) S 1 7777 0\n", encoding="utf-8"
    )
    malformed.joinpath("stat").write_text("bad", encoding="utf-8")
    real_glob = Path.glob

    def fake_glob(path, pattern):
        if str(path) == "/proc" and pattern == "[0-9]*":
            return iter((matching, mismatched, malformed, missing))
        return real_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", fake_glob)
    assert services_module._linux_process_group_members(4242) == [matching]


def test_linux_group_listener_matches_fd_inode(tmp_path, monkeypatch):
    process_dir = tmp_path / "101"
    fd_dir = process_dir / "fd"
    fd_dir.mkdir(parents=True)
    good = fd_dir / "3"
    bad = fd_dir / "4"
    regular = fd_dir / "5"
    good.symlink_to("socket:[111]")
    bad.symlink_to("socket:[222]")
    regular.write_text("not a symlink", encoding="utf-8")
    monkeypatch.setattr(
        services_module,
        "_linux_listening_socket_inodes",
        lambda _bind, _port: {"111"},
    )
    monkeypatch.setattr(
        services_module,
        "_linux_process_group_members",
        lambda _pgid: [process_dir],
    )

    assert services_module._linux_group_owns_listener(
        4242, "127.0.0.1", 8123
    ) is True
    good.unlink()
    assert services_module._linux_group_owns_listener(
        4242, "127.0.0.1", 8123
    ) is False


def test_non_linux_listener_probe_failure_is_actionable(monkeypatch):
    monkeypatch.setattr(services_module.sys, "platform", "darwin")
    monkeypatch.setattr(services_module, "_lsof_path", lambda: "/usr/sbin/lsof")
    monkeypatch.setattr(
        services_module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )

    with pytest.raises(RuntimeError, match="verify listener ownership.*denied"):
        services_module.process_group_owns_tcp_listener(
            4242, "127.0.0.1", 8123
        )


def test_non_linux_preflight_reports_missing_lsof(monkeypatch):
    monkeypatch.setattr(services_module.sys, "platform", "darwin")
    monkeypatch.setattr(services_module, "_lsof_path", lambda: None)
    error = services_module.lifecycle_support_error(
        blender_mcp_manager.fcntl,
        blender_mcp_manager.os,
        blender_mcp_manager.signal,
        "managed Blender MCP",
    )
    assert error is not None and "lsof" in error
