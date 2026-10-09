"""
System utilities for OS detection, permission checking, and localhost resolution.

Python implementation of functions from hosts-utils.sh and start.sh.
"""

import os
from pathlib import Path
import platform
import ctypes
import subprocess


def detect_os() -> str:
    """
    Detect the operating system.
    
    Returns:
        str: "macos", "linux", "windows", or "unknown"
    """
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux" 
    elif system == "windows":
        return "windows"
    else:
        return "unknown"


def is_elevated() -> bool:
    """
    Check if running with elevated privileges.
    
    Returns:
        bool: True if running as admin/root, False otherwise
    """
    os_type = detect_os()
    
    if os_type == "windows":
        try:
            # Windows: check if running as administrator
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    else:
        # Unix-like: check if running as root
        try:
            return os.geteuid() == 0
        except AttributeError:
            # Windows doesn't have geteuid - fallback to False
            return False


def get_localhost_host() -> str:
    """
    The hostname containers use to reach a localhost-source service.

    Always ``host.docker.internal``: every container consuming such an
    endpoint maps it with ``extra_hosts: host.docker.internal:${HOST_GATEWAY_IP}``
    (``resolve_host_gateway_ip``, Podman included). The name used to be
    resolved on the host, where it never resolves on Linux, so endpoints were
    rewritten to a hard-coded 172.17.0.1 that a custom bridge, rootless
    Docker or Podman does not serve (#1361).
    """
    return "host.docker.internal"


def detect_container_runtime() -> str:
    """
    Detect whether the Docker CLI is backed by Docker Engine or Podman.

    Returns:
        str: "docker" or "podman"
    """
    try:
        result = subprocess.run(
            ['docker', 'version'],
            capture_output=True, text=True, check=False, timeout=10,
            encoding="utf-8", errors="replace",
        )
        output = (result.stdout + result.stderr).lower()
        if 'podman' in output:
            return "podman"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return "docker"


def resolve_host_gateway_ip() -> str:
    """
    Resolve the IP address that containers should use to reach the host.

    For Docker Desktop: returns the literal string "host-gateway" (Docker
    resolves this at container creation time).

    For Podman: queries the default bridge network gateway IP, which is the
    host from the container's perspective.

    Returns:
        str: IP address or "host-gateway"
    """
    runtime = detect_container_runtime()

    if runtime == "docker":
        return "host-gateway"

    # Podman: resolve from bridge network IPAM config
    try:
        result = subprocess.run(
            ['docker', 'network', 'inspect', 'bridge',
             '--format', '{{range .IPAM.Config}}{{.Gateway}}{{end}}'],
            capture_output=True, text=True, check=False, timeout=10,
            encoding="utf-8", errors="replace",
        )
        gateway = result.stdout.strip()
        if gateway:
            return gateway
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    # Podman fallback: run a throwaway container to read the default route.
    # 60s timeout covers a one-time alpine image pull on a fresh Podman.
    try:
        result = subprocess.run(
            ['docker', 'run', '--rm', 'alpine',
             'sh', '-c', "ip route | awk '/default/{print $3}'"],
            capture_output=True, text=True, check=False, timeout=60,
            encoding="utf-8", errors="replace",
        )
        gateway = result.stdout.strip()
        if gateway:
            return gateway
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    # Ultimate fallback: Podman's typical bridge gateway
    return "10.88.0.1"


def get_hosts_file_path() -> str:
    """
    Get the hosts file path based on the operating system.
    
    Returns:
        str: Path to the hosts file, or empty string if unknown
    """
    os_type = detect_os()
    
    if os_type in ["macos", "linux"]:
        return "/etc/hosts"
    elif os_type == "windows":
        return "C:/Windows/System32/drivers/etc/hosts"
    else:
        return ""


def project_volume_names(project_name: str) -> list | None:
    """Volumes Compose labelled as this project's; None when docker could not
    list them. [] for "unknown" let a failed listing confirm a cold cleanup,
    so secrets rotated while volumes holding the old ones survived
    (2026-10-08 run, cycle 48)."""
    try:
        result = subprocess.run(
            ["docker", "volume", "ls", "-q", "--filter",
             f"label=com.docker.compose.project={project_name}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [line for line in result.stdout.split() if line] if result.returncode == 0 else None


def report_surviving_volumes(leftover: list | None, emit) -> list:
    """Name project volumes `down --volumes` left behind (a consumer overlay
    not loaded for this run), with the remedy. An unknown listing counts as
    a survivor: the removal is not confirmed."""
    if leftover is None:
        emit(
            "    ⚠ Could not list this project's volumes (docker volume ls failed), so "
            "their removal is not confirmed. Check Docker, then re-run."
        )
        return ["<unknown>"]
    if leftover:
        emit(
            "    ⚠ These project volumes were not removed (declared by a consumer "
            f"overlay not loaded for this run?): {', '.join(leftover)}. Re-run with "
            "--consumer <manifest>, or remove them with docker volume rm."
        )
    return leftover


def compose_env(compose_cmd: list) -> dict:
    """The environment for a compose command, with PROJECT_NAME naming the
    same project as its `-p`. Every volume and container is named
    `${PROJECT_NAME}-…`, which Compose resolves from the shell before
    --env-file, so a cold `--project foo` (cleanup runs before .env gets foo)
    or a stray exported PROJECT_NAME ran `down --volumes` under `-p foo`
    against another project's volumes.

    The value Compose would resolve is kept when it already names this
    project (a hand-edited `MyStack` is project `mystack`, and its volumes
    are `MyStack-*`; lowercasing it renamed them and orphaned the data); it
    is replaced with `-p` only when it names a different project."""
    env = os.environ.copy()
    if "-p" not in compose_cmd[:-1]:
        return env
    project = compose_cmd[compose_cmd.index("-p") + 1]
    # An exported empty value still wins over --env-file in Compose, so it
    # must be replaced too, not read as unset (2026-10-08 run, cycle 47).
    resolved = env["PROJECT_NAME"] if "PROJECT_NAME" in env else _env_file_project_name(compose_cmd)
    if not resolved or _normalized_project(resolved) != project:
        env["PROJECT_NAME"] = project
    return env


SINK_COMPOSE_TIMEOUT_SECONDS = 600


def run_compose_child(full_cmd: list, cwd: str, sink=None) -> int:
    """Run compose on the terminal, or into ``sink`` when given: a Textual
    screen owns the terminal, and inherited fds wrote compose's progress
    straight across it (2026-10-08 run, cycle 48)."""
    if not sink:
        return subprocess.run(
            full_cmd, cwd=cwd, stdin=subprocess.DEVNULL, check=False, env=compose_env(full_cmd),
        ).returncode
    return _stream_compose_child(full_cmd, cwd, sink)


def _stream_compose_child(full_cmd: list, cwd: str, sink) -> int:
    """Line by line into ``sink``, bounded: buffered until exit, a stop showed
    nothing until compose ended, and a wedged daemon never ended (cycle 69)."""
    import threading

    process = subprocess.Popen(
        full_cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace", env=compose_env(full_cmd),
    )
    reader = threading.Thread(
        target=lambda: [sink(line.rstrip("\n")) for line in process.stdout], daemon=True,
    )
    reader.start()
    try:
        returncode = process.wait(timeout=SINK_COMPOSE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        sink(f"docker compose did not finish within {SINK_COMPOSE_TIMEOUT_SECONDS} s and was stopped; "
             "check the stack with docker compose ls")
        returncode = 124
    reader.join(5)
    return returncode


def _env_file_project_name(compose_cmd: list) -> str:
    env_file = next((arg.split("=", 1)[1] for arg in compose_cmd if arg.startswith("--env-file=")), "")
    if not env_file:
        return ""
    try:
        from core.config_parser import ConfigParser

        parser = ConfigParser()
        parser.env_file_path = Path(env_file)
        return (parser.parse_env_file().get("PROJECT_NAME") or "").strip()
    except Exception:  # noqa: BLE001 - unreadable .env: pin to -p
        return ""


def _normalized_project(raw: str) -> str:
    from core.config_parser import normalize_project_name

    try:
        return normalize_project_name(raw)
    except ValueError:
        return ""
