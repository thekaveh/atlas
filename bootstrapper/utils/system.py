"""
System utilities for OS detection, permission checking, and localhost resolution.

Python implementation of functions from hosts-utils.sh and start.sh.
"""

import os
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


def project_volume_names(project_name: str) -> list:
    """Volumes Compose labelled as this project's; [] when unknown."""
    try:
        result = subprocess.run(
            ["docker", "volume", "ls", "-q", "--filter",
             f"label=com.docker.compose.project={project_name}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in result.stdout.split() if line] if result.returncode == 0 else []


def report_surviving_volumes(leftover: list, emit) -> list:
    """Name project volumes `down --volumes` left behind (a consumer overlay
    not loaded for this run), with the remedy."""
    if leftover:
        emit(
            "    ⚠ These project volumes were not removed (declared by a consumer "
            f"overlay not loaded for this run?): {', '.join(leftover)}. Re-run with "
            "--consumer <manifest>, or remove them with docker volume rm."
        )
    return leftover


def compose_env(compose_cmd: list) -> dict:
    """The environment for a compose command, with PROJECT_NAME pinned to its
    `-p`. Every volume and container is named `${PROJECT_NAME}-…`, which
    Compose resolves from the shell before --env-file, so a cold
    `--project foo` (cleanup runs before .env gets foo) or a stray exported
    PROJECT_NAME ran `down --volumes` under `-p foo` against another
    project's volumes."""
    env = os.environ.copy()
    if "-p" in compose_cmd[:-1]:
        env["PROJECT_NAME"] = compose_cmd[compose_cmd.index("-p") + 1]
    return env
