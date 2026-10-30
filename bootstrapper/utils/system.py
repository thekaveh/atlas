"""
System utilities for OS detection, permission checking, and localhost resolution.

Python implementation of functions from hosts-utils.sh and start.sh.
"""

import os
from pathlib import Path
import platform
import ctypes
import subprocess
from urllib.parse import quote


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
    is replaced with `-p` only when it names a different project.

    It also applies Redis overrides to this child only: a shell
    REDIS_PASSWORD re-derives REDIS_PASSWORD_URI and the Redis URLs the env
    file sets. When the env file's REDIS_PASSWORD holds a `${...}`
    reference, a blocking `docker compose config` probe (10 s deadline)
    resolves it first. If that probe fails, including when Compose rejects
    the env file, this raises RuntimeError rather than run with Redis URLs
    that do not match the password."""
    env = os.environ.copy()
    if "-p" in compose_cmd[:-1]:
        project = compose_cmd[compose_cmd.index("-p") + 1]
        # Empty shell values also win over the file. Pin before resolving a
        # Redis password reference to PROJECT_NAME in that same child.
        resolved = env["PROJECT_NAME"] if "PROJECT_NAME" in env else _env_file_project_name(compose_cmd)
        if not resolved or _normalized_project(resolved) != project:
            env["PROJECT_NAME"] = project
    _redis_compose_environment(env, compose_cmd)
    return env


def _redis_compose_environment(env: dict, compose_cmd: list) -> None:
    """Apply shell Redis overrides to this child, never to the persisted env."""
    if "REDIS_PASSWORD" in env:
        env["REDIS_PASSWORD_URI"] = quote(env["REDIS_PASSWORD"], safe="")
    elif "REDIS_PASSWORD_URI" not in env and _redis_password_expands(compose_cmd):
        env["REDIS_PASSWORD"] = _resolve_redis_password(compose_cmd, env)
        env["REDIS_PASSWORD_URI"] = quote(env["REDIS_PASSWORD"], safe="")
    if "REDIS_PASSWORD_URI" not in env:
        return
    values = _compose_env_file_values(compose_cmd)
    for name, database in (
        ("CELERY_BROKER_URL", 4), ("CELERY_RESULT_BACKEND", 4),
        ("LIGHTRAG_REDIS_URI", 2),
    ):
        if name not in env and values.get(name):
            env[name] = f'redis://:{env["REDIS_PASSWORD_URI"]}@redis:6379/{database}'


def _compose_options(compose_cmd: list, names: set) -> list:
    """Retain ordered explicit options in either supported argument form."""
    options = []
    for index, argument in enumerate(compose_cmd):
        name = argument.split("=", 1)[0]
        if name not in names:
            continue
        if "=" in argument:
            options.append((name, argument.split("=", 1)[1]))
        elif index + 1 < len(compose_cmd):
            options.append((name, compose_cmd[index + 1]))
    return options


def _redis_password_expands(compose_cmd: list) -> bool:
    """Detect syntax only; Compose owns the actual interpolation rules."""
    import re
    from utils.atomic_write import decode_env_value, env_assignment_pattern, env_lines

    expands = False
    for _, filename in _compose_options(compose_cmd, {"--env-file"}):
        try:
            content = Path(filename).read_text(encoding="utf-8-sig")
            for line in env_lines(content):
                if env_assignment_pattern("REDIS_PASSWORD").match(line):
                    raw = line.split("=", 1)[1].lstrip()
                    expands = not raw.startswith("'") and re.search(r"\$\{[^}]+\}", decode_env_value(raw)) is not None
        except (OSError, ValueError):
            # A file this reader cannot decode skips the probe, so the original
            # Compose command reports it. One that decodes here but that Compose
            # rejects fails the probe instead (_resolve_redis_password raises).
            continue
    return expands


def _redis_probe_command(compose_cmd: list, model: Path) -> list:
    """Replace stack models with a constant probe, retaining file/name inputs."""
    command = compose_cmd[:2] if compose_cmd[1:2] == ["compose"] else compose_cmd[:1]
    options = _compose_options(compose_cmd, {"--env-file", "-p", "--project-name", "--project-directory"})
    for name, value in options:
        command.extend([name, value])
    if not any(name == "--project-directory" for name, _ in options):
        files = _compose_options(compose_cmd, {"-f", "--file"})
        directory = Path(files[0][1]).resolve().parent if files and files[0][1] != "-" else Path.cwd()
        command.extend(["--project-directory", str(directory)])
    return command + ["-f", str(model), "config", "--format", "json"]


def _resolve_redis_password(compose_cmd: list, env: dict) -> str:
    """Resolve file references privately, bounded, without saving credentials."""
    import json
    import tempfile
    from core.process_runner import run_with_deadline

    try:
        with tempfile.TemporaryDirectory(prefix="atlas-redis-env-") as directory:
            model = Path(directory) / "compose.json"
            model.write_text(json.dumps({"services": {"atlas-redis-env-probe": {
                "image": "scratch", "environment": {"VALUE": "${REDIS_PASSWORD}"},
            }}}), encoding="utf-8")
            result = run_with_deadline(
                _redis_probe_command(compose_cmd, model), env=env,
                timeout_seconds=10, max_output_bytes=1024 * 1024,
            )
            if result.returncode != 0:
                raise ValueError("probe failed")
            value = json.loads(result.stdout)["services"]["atlas-redis-env-probe"]["environment"]["VALUE"]
            if not isinstance(value, str):
                raise ValueError("invalid probe value")
            # Compose config escapes dollars when serializing for later reload.
            return value.replace("$$", "$")
    except Exception:
        raise RuntimeError("Could not resolve Redis password references with Compose") from None


def _compose_env_file_values(compose_cmd: list) -> dict:
    """Read explicit env files in Compose order, using Atlas's env grammar."""
    from core.config_parser import ConfigParser

    values = {}
    for index, argument in enumerate(compose_cmd):
        if argument.startswith("--env-file="):
            env_file = argument.split("=", 1)[1]
        elif argument == "--env-file" and index + 1 < len(compose_cmd):
            env_file = compose_cmd[index + 1]
        else:
            continue
        try:
            parser = ConfigParser()
            parser.env_file_path = Path(env_file)
            values.update(parser.parse_env_file())
        except (OSError, ValueError):
            # Compose owns diagnostics for an unreadable or invalid env file.
            continue
    return values


SINK_COMPOSE_TIMEOUT_SECONDS = 600


def resolved_minio_environment(compose_cmd: list, cwd: str | Path, timeout_seconds: float) -> dict:
    """Resolve bucket names using Compose's shell, env-file and overlay rules.

    Capture privately: rendered configuration can contain credentials.
    Apply the caller's probe bounds; failures carry no command output.
    """
    import json

    from core.process_runner import run_with_deadline

    result = run_with_deadline(
        compose_cmd, cwd=str(cwd), env=compose_env(compose_cmd),
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not resolve MinIO bucket configuration with Compose")
    try:
        environment = json.loads(result.stdout)['services']['minio-init']['environment']
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("Could not read MinIO bucket configuration from Compose") from None
    if not isinstance(environment, dict):
        raise RuntimeError("Compose returned invalid MinIO bucket configuration")
    return environment


def run_compose_child(full_cmd: list, cwd: str, sink=None) -> int:
    """Run compose on the terminal, or into ``sink`` when given: a Textual
    screen owns the terminal, and inherited fds wrote compose's progress
    straight across it (2026-10-08 run, cycle 48)."""
    if not sink:
        return subprocess.run(
            full_cmd, cwd=cwd, stdin=subprocess.DEVNULL, check=False, env=compose_env(full_cmd),
        ).returncode
    return _stream_compose_child(full_cmd, cwd, sink)


def _emit_compose_output(stream, sink) -> None:
    try:
        for line in stream:
            sink(line.rstrip("\n"))
    finally:
        stream.close()


def _stream_compose_child(full_cmd: list, cwd: str, sink) -> int:
    """Line by line into ``sink``, bounded: buffered until exit, a stop showed
    nothing until compose ended, and a wedged daemon never ended (cycle 69)."""
    import threading

    process = subprocess.Popen(
        full_cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace", env=compose_env(full_cmd),
    )
    reader = threading.Thread(
        target=_emit_compose_output, args=(process.stdout, sink), daemon=True,
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


def _replica_count(value) -> int:
    """Accept only rendered nonnegative integer replica counts."""
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and value.isdecimal()
    ):
        raise ValueError(f"invalid replica count {value!r}")
    count = int(value)
    if count < 0:
        raise ValueError("negative replica count")
    return count


def _service_replica_count(spec: dict) -> int:
    deploy = spec.get("deploy", {})
    if not isinstance(deploy, dict):
        raise ValueError("invalid deploy specification")
    counts = [_replica_count(mapping[key]) for mapping, key in
              ((spec, "scale"), (deploy, "replicas")) if key in mapping]
    if len(set(counts)) > 1:
        raise ValueError("scale and deploy.replicas disagree")
    return counts[0] if counts else 1


def service_replica_targets(services: dict) -> dict[str, int]:
    """Validate rendered service specs and their effective replica counts."""
    if not isinstance(services, dict) or not services:
        raise ValueError("rendered Compose services are empty or unavailable")
    targets = {}
    for name, spec in services.items():
        if not isinstance(name, str) or not name or not isinstance(spec, dict):
            raise ValueError("invalid rendered Compose service specification")
        try:
            targets[name] = _service_replica_count(spec)
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from exc
    return targets
