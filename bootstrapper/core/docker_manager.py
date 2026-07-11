"""
Docker operations manager for compose commands and Docker availability checking.

Compose execution layer (start.sh/stop.sh are now thin wrappers that
delegate here).
"""

import os
import json
import re
import signal
import subprocess
import time
from typing import Callable, List, Optional
from pathlib import Path
from core.config_parser import ConfigParser


class DockerManager:
    """Manages Docker operations and compose commands."""

    def __init__(self, root_dir: Optional[str] = None):
        """
        Initialize Docker manager.

        Args:
            root_dir: Root directory containing docker-compose files and .env
        """
        if root_dir is None:
            # Default to parent directory of bootstrapper
            self.root_dir = Path(__file__).resolve().parent.parent.parent
        else:
            self.root_dir = Path(root_dir)

        self.config_parser = ConfigParser(str(self.root_dir))
        self._compose_cmd = None
        self.project_name_override: Optional[str] = None

        # Callback for the "Command: docker compose …" echo. Defaults to
        # builtin print so the legacy linear flow is unchanged. The Live
        # presentation sets this to `app.log` to route the echo through the
        # log pane in dim style.
        self._on_command: Callable[[str], None] = print
    
    def detect_docker_compose_command(self) -> str:
        """
        Detect available docker compose command.
        Detects the available compose command (descended from the legacy
        shell helper of the same purpose).
        
        Returns:
            str: Either "docker compose" or "docker-compose"
            
        Raises:
            RuntimeError: If neither Docker nor docker-compose is available
        """
        if self._compose_cmd is not None:
            return self._compose_cmd
            
        # Check if docker is available
        try:
            subprocess.run(['docker', '--version'],
                         capture_output=True, check=True, timeout=10)
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            raise RuntimeError("Docker is not installed or not in PATH")
        
        # Check if 'docker compose' (newer) works
        try:
            subprocess.run(['docker', 'compose', 'version'],
                           capture_output=True, check=True, timeout=10)
            self._compose_cmd = "docker compose"
            return self._compose_cmd
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        
        # Check if 'docker-compose' (legacy) works
        try:
            subprocess.run(['docker-compose', '--version'],
                         capture_output=True, check=True, timeout=10)
            self._compose_cmd = "docker-compose"
            return self._compose_cmd
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            pass
            
        raise RuntimeError("Neither 'docker compose' nor 'docker-compose' command is available")
    
    def check_docker_available(self) -> bool:
        """
        Check if Docker is available.

        Returns:
            bool: True if Docker is available
        """
        try:
            self.detect_docker_compose_command()
            return True
        except RuntimeError:
            return False

    # Minimum Compose version for the per-service modular layout. v2.20+ is
    # the floor (top-level `include:` directive + cross-include depends_on
    # merging). v2.26+ is documented as recommended because earlier 2.2x
    # releases had several `include:` + `profiles:` interaction bugs.
    MIN_COMPOSE_VERSION = (2, 20, 0)
    RECOMMENDED_COMPOSE_VERSION = (2, 26, 0)

    def check_compose_version(self) -> tuple[bool, str]:
        """Check that Docker Compose meets the modular-layout floor.

        Returns:
            (ok, message): ok=True if version ≥ MIN_COMPOSE_VERSION. message
            is a human-readable status string suitable for logging.
        """
        try:
            cmd = self.detect_docker_compose_command().split() + ["version", "--short"]
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False, timeout=10,
                encoding="utf-8", errors="replace",
            )
            if result.returncode != 0:
                return False, f"docker compose version failed: {result.stderr.strip()}"
            raw = result.stdout.strip().lstrip("v")
            parts = raw.split(".")
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
            # Strip pre-release suffixes off the patch number (e.g. "1-rc1" → 1).
            patch_str = parts[2] if len(parts) > 2 else "0"
            patch = int(patch_str.split("-")[0].split("+")[0])
            actual = (major, minor, patch)
            actual_str = ".".join(str(p) for p in actual)
            min_str = ".".join(str(p) for p in self.MIN_COMPOSE_VERSION)
            rec_str = ".".join(str(p) for p in self.RECOMMENDED_COMPOSE_VERSION)
            if actual < self.MIN_COMPOSE_VERSION:
                return False, (
                    f"Docker Compose v{actual_str} is below the minimum v{min_str} required "
                    f"for the modular `services/` layout. Upgrade Docker Desktop or Compose."
                )
            if actual < self.RECOMMENDED_COMPOSE_VERSION:
                return True, (
                    f"Docker Compose v{actual_str} meets the minimum but v{rec_str}+ is "
                    f"recommended (avoids known `include:` + `profiles:` bugs in earlier 2.2x)."
                )
            return True, f"Docker Compose v{actual_str} OK."
        except Exception as e:
            return False, f"Could not detect Docker Compose version: {e}"
    
    def _compose_file_args(self) -> List[str]:
        """Compose ``-f`` arguments.

        Empty by default — Docker Compose auto-discovers ``docker-compose.yml``
        from ``cwd`` (the repo root), so the default invocation (and the
        compose byte-equivalence baseline) is unchanged. When a downstream
        consumer has dropped overlay fragments under
        ``services/_user/<name>/compose.yml`` (a gitignored overlay slot),
        return an explicit base + overlay file list so those services are
        merged into the stack and launched. Sorted for determinism.

        Note: once any ``-f`` is passed, Compose stops auto-discovering the
        default file, so the base ``docker-compose.yml`` must be listed first.
        """
        user_dir = self.root_dir / "services" / "_user"
        overlays = sorted(user_dir.glob("*/compose.yml")) if user_dir.is_dir() else []
        consumer_overlays = list(self.config_parser.load_consumer_config().compose_overlays)
        # Atlas-owned generated overlays, produced during
        # generate_service_configuration when a consumer declares the relevant
        # section: the minio-init storage overlay (#404) and the litellm api-key
        # overlay (#411, injects consumer api-key references into the litellm
        # container so it resolves os.environ/<VAR> at request time).
        from core.consumer_manifest import (
            LITELLM_CONSUMER_OVERLAY_PATH,
            MINIO_STORAGE_OVERLAY_PATH,
            N8N_CONSUMER_OVERLAY_PATH,
        )
        storage_overlay = self.root_dir / MINIO_STORAGE_OVERLAY_PATH
        litellm_overlay = self.root_dir / LITELLM_CONSUMER_OVERLAY_PATH
        n8n_overlay = self.root_dir / N8N_CONSUMER_OVERLAY_PATH
        if (
            not overlays
            and not consumer_overlays
            and not storage_overlay.exists()
            and not litellm_overlay.exists()
            and not n8n_overlay.exists()
        ):
            return []
        file_args: List[str] = ['-f', 'docker-compose.yml']
        for overlay in overlays:
            file_args.extend(['-f', str(overlay.relative_to(self.root_dir))])
        for overlay in consumer_overlays:
            file_args.extend(['-f', str(overlay)])
        if storage_overlay.exists():
            file_args.extend(['-f', str(storage_overlay.relative_to(self.root_dir))])
        if litellm_overlay.exists():
            file_args.extend(['-f', str(litellm_overlay.relative_to(self.root_dir))])
        if n8n_overlay.exists():
            file_args.extend(['-f', str(n8n_overlay.relative_to(self.root_dir))])
        return file_args

    def execute_compose_command(
        self,
        args: List[str],
        use_env_file: bool = True,
        project_name: Optional[str] = None,
    ) -> int:
        """
        Execute a docker compose command with proper error handling.
        Builds and runs a compose command (descended from the legacy
        shell helper of the same purpose).
        
        Args:
            args: List of arguments to pass to docker compose
            use_env_file: Whether to use --env-file=.env flag
            
        Returns:
            int: Return code from the command
        """
        compose_cmd = self.detect_docker_compose_command().split()
        
        # Build the full command
        full_cmd = compose_cmd.copy()

        # Add project name to ensure consistency with PROJECT_NAME from .env
        resolved_project_name = (
            project_name
            or self.project_name_override
            or self.config_parser.get_project_name()
        )
        full_cmd.extend(['-p', resolved_project_name])

        # Add --env-file if .env exists and use_env_file is True
        if use_env_file and self.config_parser.env_file_exists():
            # Use the resolved path (honors ATLAS_ENV_FILE) — hardcoding .env
            # silently ignored custom env files at the compose seam.
            full_cmd.extend([f'--env-file={self.config_parser.env_file_path}'])

        # Merge any downstream services/_user/<name>/compose.yml overlays
        # (no-op when none exist — preserves default behavior).
        full_cmd.extend(self._compose_file_args())
        full_cmd.extend(args)

        self._on_command(f"      Command: {' '.join(full_cmd)}")

        try:
            # Run the command in the root directory
            # Docker Compose will read the .env file directly via --env-file flag.
            # stdin=DEVNULL prevents any terminal keystroke from leaking into
            # docker's stdin during long-running passthrough commands like
            # `logs -f` — the keystrokes would otherwise be visible inside an
            # active scroll region.
            result = subprocess.run(
                full_cmd,
                cwd=str(self.root_dir),
                stdin=subprocess.DEVNULL,
                check=False
            )
            return result.returncode
        except Exception as e:
            self._on_command(f"❌ Error executing docker compose command: {e}")
            return 1

    def set_command_echo_callback(self, callback: Callable[[str], None]) -> None:
        """
        Override where 'Command: docker compose …' echoes get routed.

        The Live presentation passes app.log here so command echoes appear
        in the windowed log pane (in dim style) rather than as raw stdout
        breaking the alternate screen. Reset to `print` for the legacy flow.
        """
        self._on_command = callback
    
    def stop_services(self, remove_volumes: bool = False, remove_orphans: bool = True) -> int:
        """
        Stop Docker compose services.
        
        Args:
            remove_volumes: Whether to remove volumes (--volumes flag)
            remove_orphans: Whether to remove orphan containers
            
        Returns:
            int: Return code from the command
        """
        args = ['down']
        
        if remove_volumes:
            args.append('--volumes')
            
        if remove_orphans:
            args.append('--remove-orphans')
            
        return self.execute_compose_command(args)
    
    def start_services(
        self,
        detached: bool = True,
        wait: bool = False,
        wait_timeout_seconds: int = 900,
    ) -> int:
        """
        Start Docker compose services.
        Always uses --force-recreate to ensure containers are recreated with new port settings.
        This matches the original Bash script behavior.
        
        Args:
            detached: Whether to run in detached mode (-d flag)
            
        Returns:
            int: Return code from the command
        """
        args = ['up']
        
        if detached:
            args.append('-d')
            
        # Always use --force-recreate to match original Bash behavior
        # This ensures containers are recreated with updated port settings
        args.append('--force-recreate')

        if wait:
            args.extend(['--wait', '--wait-timeout', str(wait_timeout_seconds)])
            
        return self.execute_compose_command(args)

    def validate_compose_config(self) -> tuple[int, str, str, list[str]]:
        """Run ``docker compose config -q`` for the assembled Atlas stack.

        The command is built through the same path as start/stream operations,
        so custom env files, project names, and ``services/_user`` overlays are
        included exactly as they are for a real launch.
        """
        cmd = self._build_compose_command(['config', '-q'])
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.root_dir),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
            return result.returncode, result.stdout, result.stderr, cmd
        except Exception as e:
            return 1, "", f"Error executing docker compose config: {e}", cmd
    
    def remove_project_networks(self, project_name: str) -> bool:
        """
        Remove project-specific Docker networks.

        Args:
            project_name: Name of the project

        Returns:
            bool: True if successful or network doesn't exist
        """
        network_name = f"{project_name}-network"

        try:
            subprocess.run(
                ['docker', 'network', 'rm', network_name],
                capture_output=True,
                check=False,
                timeout=10,
            )
            # Return True even if network doesn't exist (exit code 1)
            return True
        except (subprocess.SubprocessError, OSError):
            return False

    def prune_system(self, remove_volumes: bool = False) -> int:
        """
        Run docker system prune to clean up unused resources.

        Args:
            remove_volumes: Whether to also remove volumes (--volumes flag)

        Returns:
            int: Return code from the command
        """
        args = ['docker', 'system', 'prune', '-f']

        if remove_volumes:
            args.append('--volumes')

        self._on_command(f"      Command: {' '.join(args)}")

        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self._on_command(line.rstrip("\n"))
            return proc.wait()
        except Exception as e:
            self._on_command(f"❌ Error running docker system prune: {e}")
            return 1
    
    def get_compose_command_display(self) -> str:
        """
        Get the Docker compose command for display purposes.
        
        Returns:
            str: The detected Docker compose command
        """
        try:
            return self.detect_docker_compose_command()
        except RuntimeError:
            return "docker compose (not available)"
    
    def get_compose_command(self) -> List[str]:
        """
        Get the Docker compose command as list for subprocess calls.
        
        Returns:
            List[str]: The detected Docker compose command as list
        """
        return self.detect_docker_compose_command().split()
    
    def perform_cold_start_cleanup(self) -> bool:
        """Stop containers, remove volumes and orphans, drop the project network, and prune the Docker system (twice — once with volumes, once general).

        All output flows through the registered command-echo callback
        (`_on_command`) so the wizard's Live region can stream it into
        the log pane without tearing the alternate screen.

        Returns True if every step succeeded.
        """
        project_name = self.project_name_override or self.config_parser.get_project_name()
        all_successful = True

        self._on_command("    - Stopping and removing containers...")
        result = self.stream_compose(
            ['down', '--remove-orphans'],
            on_line=self._on_command,
        )
        if result != 0:
            all_successful = False

        self._on_command("    - Removing volumes (cold start)...")
        result = self.stream_compose(
            ['down', '-v'],
            on_line=self._on_command,
        )
        if result != 0:
            all_successful = False

        self._on_command("    - Removing project network (cold start)...")
        self._on_command(f"      Command: docker network rm {project_name}-network")
        if not self.remove_project_networks(project_name):
            self._on_command("      Note: Network may not exist or is already removed")

        self._on_command("    - Performing aggressive Docker system prune (cold start)...")
        result = self.prune_system(remove_volumes=True)
        if result != 0:
            all_successful = False

        self._on_command("    - Performing general Docker system prune...")
        result = self.prune_system(remove_volumes=False)
        if result != 0:
            all_successful = False

        return all_successful
    
    def perform_cold_stop_cleanup(self) -> bool:
        """Stop containers, remove volumes and orphans, drop project networks, and prune the Docker system with volumes.

        Returns True if every step succeeded.
        """
        project_name = self.project_name_override or self.config_parser.get_project_name()
        all_successful = True
        
        print("    - Stopping containers and removing volumes...")
        result = self.execute_compose_command(
            ['down', '--volumes', '--remove-orphans'],
            project_name=project_name,
        )
        if result != 0:
            all_successful = False
            
        print("    - Removing project networks...")
        if not self.remove_project_networks(project_name):
            print("      Note: Network may not exist or is already removed")
            
        print("    - Performing Docker system cleanup...")
        result = self.prune_system(remove_volumes=True)
        if result != 0:
            all_successful = False
            
        return all_successful
    
    def build_services(self, no_cache: bool = False, pull: bool = False) -> int:
        """
        Build Docker compose services with optional flags.
        
        Args:
            no_cache: Whether to build without cache (--no-cache flag)
            pull: Whether to pull latest images (--pull flag)
            
        Returns:
            int: Return code from the command
        """
        args = ['build']
        
        if no_cache:
            args.append('--no-cache')
            
        if pull:
            args.append('--pull')
            
        return self.execute_compose_command(args)
    
    def show_container_status(self) -> int:
        """
        Show container status using docker compose ps.
        Compose ps (descended from the legacy shell flow).
        
        Returns:
            int: Return code from the command
        """
        print("🔍 Verifying port mappings from Docker...")
        return self.execute_compose_command(['ps'])
        
    def are_project_containers_running(self) -> bool:
        """
        Check if any containers from this project's Docker Compose stack are currently running.

        Uses 'docker compose ps -q' which returns container IDs of running services.
        An empty result means no containers are running.

        Returns:
            bool: True if any project containers are running
        """
        try:
            cmd = self.get_compose_command()
            project_name = self.project_name_override or self.config_parser.get_project_name()
            cmd.extend(['-p', project_name])
            if self.config_parser.env_file_exists():
                cmd.append(f'--env-file={self.config_parser.env_file_path}')
            cmd.extend(self._compose_file_args())
            cmd.extend(['ps', '-q'])

            result = subprocess.run(
                cmd,
                cwd=str(self.root_dir),
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )

            return result.returncode == 0 and bool(result.stdout.strip())
        except (subprocess.SubprocessError, OSError):
            return False

    def get_service_port(self, service: str, internal_port: str) -> str:
        """
        Get the actual external port mapped to a service's internal port.
        Resolves the actual published port (descended from the legacy shell flow).

        Args:
            service: Service name
            internal_port: Internal port number

        Returns:
            str: External port number, or empty string if not found
        """
        try:
            cmd = self.get_compose_command()
            project_name = self.project_name_override or self.config_parser.get_project_name()
            cmd.extend(['-p', project_name])
            if self.config_parser.env_file_exists():
                cmd.append(f'--env-file={self.config_parser.env_file_path}')
            cmd.extend(self._compose_file_args())
            cmd.extend(['port', service, internal_port])

            result = subprocess.run(
                cmd,
                cwd=str(self.root_dir),
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            
            if result.returncode == 0 and result.stdout.strip():
                # Extract port number from output like "0.0.0.0:63000"
                match = re.search(r':(\d+)$', result.stdout.strip())
                if match:
                    return match.group(1)
            return ""
        except (subprocess.SubprocessError, OSError):
            return ""

    def failed_one_shot_services(
        self,
        services: list[str],
        *,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 2.0,
    ) -> list[tuple[str, str]]:
        """Return enabled one-shot services that fail or never finish."""
        failures: list[tuple[str, str]] = []
        if not services:
            return failures

        pending = set(services)
        last_reason = {service: "container not observed yet" for service in services}
        deadline = time.monotonic() + timeout_seconds

        while pending:
            for service in list(pending):
                rows, error = self._compose_ps_json(service)
                if error is not None:
                    failures.append((service, error))
                    pending.remove(service)
                    continue

                if not rows:
                    last_reason[service] = "container not observed yet"
                    continue

                all_exited_zero = True
                for row in rows:
                    exit_code = str(row.get("ExitCode", "")).strip()
                    state = str(row.get("State", "")).strip().lower()
                    status = str(row.get("Status", "")).strip()
                    status_lower = status.lower()

                    if exit_code and exit_code not in {"0", "<nil>", "None"}:
                        failures.append((service, f"exit {exit_code}: {status or state or 'exited'}"))
                        pending.remove(service)
                        all_exited_zero = False
                        break
                    if state == "exited":
                        if exit_code == "0" or "exit 0" in status_lower or "exited (0)" in status_lower:
                            continue
                        failures.append((service, status or state))
                        pending.remove(service)
                        all_exited_zero = False
                        break

                    all_exited_zero = False
                    last_reason[service] = status or state or "not exited yet"

                if service in pending and all_exited_zero:
                    pending.remove(service)

            if not pending:
                return failures
            if time.monotonic() >= deadline:
                for service in sorted(pending):
                    failures.append(
                        (service, f"timed out waiting for terminal state ({last_reason[service]})")
                    )
                return failures
            time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))

        return failures

    def compose_ps_json(self) -> tuple[list[dict], str | None]:
        """Inspect all compose services via ``docker compose ps --format json``."""
        return self._compose_ps_json(None)

    def _compose_ps_json(self, service: str | None) -> tuple[list[dict], str | None]:
        """Inspect compose services via ``docker compose ps --format json``."""
        try:
            cmd = self.get_compose_command()
            project_name = self.project_name_override or self.config_parser.get_project_name()
            cmd.extend(['-p', project_name])
            if self.config_parser.env_file_exists():
                cmd.append(f'--env-file={self.config_parser.env_file_path}')
            cmd.extend(self._compose_file_args())
            cmd.extend(['ps', '-a', '--format', 'json'])
            if service is not None:
                cmd.append(service)
            result = subprocess.run(
                cmd,
                cwd=str(self.root_dir),
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            return [], f"could not inspect container: {exc}"

        if result.returncode != 0:
            return [], result.stderr.strip() or "docker compose ps failed"

        rows: list[dict] = []
        payload = result.stdout.strip()
        if not payload:
            return rows, None
        try:
            parsed = json.loads(payload)
            rows = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            for line in payload.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows, None
    
    def show_container_logs(self, follow: bool = True) -> int:
        """
        Show container logs using docker compose logs.
        Compose logs -f (descended from the legacy shell flow).

        Args:
            follow: Whether to follow logs (default True)

        Returns:
            int: Return code from the command
        """
        args = ['logs']
        if follow:
            args.append('-f')

        print("📋 Container logs (press Ctrl+C to exit):")
        return self.execute_compose_command(args)

    # --- Streaming variants for the Live-region presentation -----------------
    # These replace the TTY-passthrough `execute_compose_command` for the
    # log-streaming and long-running build/cleanup phases. Without piping
    # and line-buffering, the alternate-screen Live region would be torn up
    # by raw subprocess output.

    def _build_compose_command(
        self,
        args: List[str],
        use_env_file: bool = True,
        top_level_flags: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Internal helper — build the full `docker compose` argv with project
        name and --env-file flag, mirroring `execute_compose_command` but
        without running it. Used by the streaming variants below.

        `top_level_flags` are inserted between the `docker compose` binary
        and the `-p` / `--env-file` flags — i.e. they're docker-compose
        global flags (like `--ansi=always` or `--progress=plain`) that
        must come before the subcommand.
        """
        full_cmd = self.detect_docker_compose_command().split()
        if top_level_flags:
            full_cmd.extend(top_level_flags)
        project_name = self.project_name_override or self.config_parser.get_project_name()
        full_cmd.extend(['-p', project_name])
        if use_env_file and self.config_parser.env_file_exists():
            # Use the resolved path (honors ATLAS_ENV_FILE) — hardcoding .env
            # silently ignored custom env files at the compose seam.
            full_cmd.extend([f'--env-file={self.config_parser.env_file_path}'])
        # Merge any downstream services/_user/<name>/compose.yml overlays
        # (no-op when none exist — preserves default behavior).
        full_cmd.extend(self._compose_file_args())
        full_cmd.extend(args)
        return full_cmd

    def stream_compose(
        self,
        args: List[str],
        on_line: Callable[[str], None],
        use_env_file: bool = True,
    ) -> int:
        """
        Run a docker compose command with stdout piped, line-buffered, and
        forwarded to `on_line` per line. Used for cold-start cleanup, image
        build, and `up -d` so their output flows into the log pane instead
        of inheriting (and tearing up) the Live region's alternate screen.

        bufsize=1 + text=True is essential — without it Python block-buffers
        piped stdout and the log pane stalls then bursts.

        We pass `--ansi=always` so compose keeps emitting SGR color codes
        even when stdout isn't a TTY (the default --ansi=auto would strip
        them). We deliberately do NOT pass `--progress=plain` — compose
        rejects that combination ("can't use --progress plain while ANSI
        support is forced"). With `--progress=auto` (the default) compose
        auto-detects the piped stdout and emits plain line-based progress
        anyway, so we get plain progress AND colors without the conflict.

        BUILDKIT_PROGRESS=plain in the subprocess env keeps buildkit's
        own renderer (used during `docker compose build`) on plain
        output too — buildkit doesn't share compose's --ansi conflict.

        Returns the subprocess exit code.
        """
        full_cmd = self._build_compose_command(
            args,
            use_env_file=use_env_file,
            top_level_flags=['--ansi=always'],
        )
        self._on_command(f"      Command: {' '.join(full_cmd)}")

        env = os.environ.copy()
        env['BUILDKIT_PROGRESS'] = 'plain'

        try:
            proc = subprocess.Popen(
                full_cmd,
                cwd=str(self.root_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except Exception as e:
            on_line(f"❌ Error launching docker compose: {e}")
            return 1

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                on_line(line.rstrip("\n"))
            return proc.wait()
        except KeyboardInterrupt:
            return self._terminate_subprocess(proc)

    def stream_logs(self, on_line: Callable[[str], None]) -> int:
        """
        Run `docker compose logs -f` with stdout piped and forwarded line
        by line to `on_line`. Replaces show_container_logs's passthrough
        behavior when the Live region is active.

        Blocks until the subprocess exits or the caller raises
        KeyboardInterrupt; on Ctrl+C, sends SIGINT to the subprocess and
        waits up to 3 s before SIGKILL so the user gets a clean detach.
        """
        return self.stream_compose(['logs', '-f'], on_line=on_line)

    @staticmethod
    def _terminate_subprocess(proc: subprocess.Popen) -> int:
        """Best-effort clean termination — SIGINT, wait 3 s, then SIGKILL."""
        try:
            proc.send_signal(signal.SIGINT)
            try:
                return proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                return proc.wait()
        except (subprocess.SubprocessError, OSError, AttributeError):
            # AttributeError covers proc.kill() being called on a
            # subprocess.Popen that crashed before assignment.
            return 1

    def get_services_status(self) -> dict:
        """
        Run `docker compose ps --format json` and return a dict of
        {service_name: state_string}, where state_string is one of
        "running" | "starting" | "unhealthy" | "stopped".

        Tolerates both line-delimited JSON (Compose ≥ 2.21) and the older
        single-array shape. Failures (timeout, docker stopped, malformed
        JSON) return an empty dict so the caller can fall back to .env
        configured state without crashing.
        """
        import json

        full_cmd = self._build_compose_command(['ps', '--format', 'json'])
        try:
            result = subprocess.run(
                full_cmd,
                cwd=str(self.root_dir),
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return {}

        if result.returncode != 0 or not result.stdout.strip():
            return {}

        # Try parsing as a single JSON array first; fall back to JSONL.
        entries = []
        try:
            parsed = json.loads(result.stdout)
            entries = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # skip malformed lines, keep going

        snapshot = {}
        for e in entries:
            service = e.get("Service") or e.get("Name") or ""
            if not service:
                continue
            state = (e.get("State") or "").lower()
            health = (e.get("Health") or "").lower()

            if state == "running" and health in ("healthy", "", "none"):
                snapshot[service] = "running"
            elif health == "unhealthy":
                snapshot[service] = "unhealthy"
            elif health == "starting" or state in ("created", "restarting"):
                snapshot[service] = "starting"
            elif state == "exited":
                snapshot[service] = "stopped"
            else:
                # Unknown state — show as starting so the user sees something
                # is happening rather than a stale "off" dot.
                snapshot[service] = "starting"
        return snapshot
