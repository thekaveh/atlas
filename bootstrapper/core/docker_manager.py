"""
Docker operations manager for compose commands and Docker availability checking.

Compose execution layer (start.sh/stop.sh are now thin wrappers that
delegate here).
"""

import hashlib
import os
import json
import re
import signal
import subprocess
import time
from typing import Callable, List, Optional
from pathlib import Path
from core.config_parser import ConfigParser
from core.process_runner import run_with_deadline


_COMPOSE_PROBE_TIMEOUT_SECONDS = 60.0


def _local_build_projection(services: dict) -> dict[str, dict]:
    """Return only image-build inputs and the graph that selects dependencies."""
    return {
        "local_builds": {
            name: {"build": spec["build"], "image": spec.get("image")}
            for name, spec in services.items()
            if isinstance(spec, dict) and spec.get("build") is not None
        },
        "dependency_graph": {
            name: spec.get("depends_on")
            for name, spec in services.items()
            if isinstance(spec, dict) and spec.get("depends_on") is not None
        },
    }


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
        self._build_state_to_mark: Optional[dict[str, object]] = None
        self._build_state_capture_attempted = False

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

    # Minimum Compose version for the per-service modular layout. v2.20.3+ is
    # the floor (top-level `include:` directive + cross-include depends_on
    # merging). v2.26+ is documented as recommended because earlier 2.2x
    # releases had several `include:` + `profiles:` interaction bugs.
    MIN_COMPOSE_VERSION = (2, 20, 3)
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
    
    def _compose_file_args(self, *, include_consumer: bool = True) -> List[str]:
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
        consumer_overlays = (
            list(self.config_parser.load_consumer_config().compose_overlays)
            if include_consumer
            else []
        )
        # Atlas-owned generated overlays, produced during
        # generate_service_configuration when a consumer declares the relevant
        # section: the minio-init storage overlay (#404) and the litellm api-key
        # overlay (#411, injects consumer api-key references into the litellm
        # container so it resolves os.environ/<VAR> at request time).
        from core.consumer_manifest import (
            LIGHTRAG_QUERY_PROFILES_OVERLAY_PATH,
            LITELLM_CONSUMER_OVERLAY_PATH,
            MINIO_STORAGE_OVERLAY_PATH,
            N8N_CONSUMER_OVERLAY_PATH,
            RAG_INGESTION_OVERLAY_PATH,
        )
        storage_overlay = self.root_dir / MINIO_STORAGE_OVERLAY_PATH
        litellm_overlay = self.root_dir / LITELLM_CONSUMER_OVERLAY_PATH
        n8n_overlay = self.root_dir / N8N_CONSUMER_OVERLAY_PATH
        rag_overlay = self.root_dir / RAG_INGESTION_OVERLAY_PATH
        lightrag_query_overlay = self.root_dir / LIGHTRAG_QUERY_PROFILES_OVERLAY_PATH
        if (
            not overlays
            and not consumer_overlays
            and not storage_overlay.exists()
            and not litellm_overlay.exists()
            and not n8n_overlay.exists()
            and not rag_overlay.exists()
            and not lightrag_query_overlay.exists()
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
        if rag_overlay.exists():
            file_args.extend(['-f', str(rag_overlay.relative_to(self.root_dir))])
        if lightrag_query_overlay.exists():
            file_args.extend(
                ['-f', str(lightrag_query_overlay.relative_to(self.root_dir))]
            )
        return file_args

    def _teardown_safe_compose_file_args(
        self, args: List[str]
    ) -> tuple[List[str], bool]:
        """Return optional compose files, or base-only args for broken teardown."""
        try:
            file_args = self._compose_file_args()
        except Exception as exc:
            if not args or args[0] != "down":
                raise
            self._on_command(
                "⚠️  Consumer overlays could not be loaded during teardown "
                f"({type(exc).__name__}); continuing with the base stack."
            )
            return ['-f', 'docker-compose.yml'], False
        return file_args, bool(file_args)

    def _validated_compose_file_args(
        self, args: List[str], command_prefix: List[str]
    ) -> tuple[List[str], bool]:
        """Preflight optional teardown overlays before touching live resources."""
        file_args, optional_included = self._teardown_safe_compose_file_args(args)
        if not optional_included or not args or args[0] != "down":
            return file_args, optional_included
        config_cmd = command_prefix + file_args + ["config", "-q"]
        self._on_command(f"      Preflight: {' '.join(config_cmd)}")
        result = subprocess.run(
            config_cmd,
            cwd=str(self.root_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode:
            self._on_command(
                "⚠️  Optional compose overlays failed teardown preflight; "
                "continuing with the base stack."
            )
            return ['-f', 'docker-compose.yml'], False
        return file_args, True

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

        command_prefix = full_cmd.copy()
        try:
            file_args, _optional_included = self._validated_compose_file_args(
                args, command_prefix
            )
        except Exception as exc:
            self._on_command(f"❌ Error preparing docker compose command: {exc}")
            return 1
        full_cmd.extend(file_args)
        full_cmd.extend(args)

        self._on_command(f"      Command: {' '.join(full_cmd)}")

        try:
            # Run the command in the root directory
            # Docker Compose will read the .env file directly via --env-file flag.
            # stdin=DEVNULL prevents any terminal keystroke from leaking into
            # docker's stdin during long-running passthrough commands like
            # `logs -f` — the keystrokes would otherwise be visible inside an
            # active scroll region.
            try:
                return subprocess.run(
                    full_cmd,
                    cwd=str(self.root_dir),
                    stdin=subprocess.DEVNULL,
                    check=False
                ).returncode
            except KeyboardInterrupt:
                # `docker compose up --build` drives BuildKit inside the Docker
                # daemon, so the build is not ours to stop reliably: it can
                # finish after the abort and create containers for a run the
                # operator already abandoned. Say so, because silence is what
                # makes an interrupted run look like a broken source toggle.
                self._report_interrupted_compose(resolved_project_name)
                raise
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
    
    def enabled_service_targets(self) -> Optional[list[str]]:
        """Derive the enabled-service target set from the RENDERED compose
        projection (#504).

        ``docker compose up`` with no service list evaluates/builds local
        ``build:`` images for the WHOLE assembled graph before honoring zero
        replicas — so a broken build for a disabled service (e.g. asset-baker
        with ``ASSET_BAKER_SOURCE=disabled``) aborts an unrelated track
        bring-up. Passing an explicit target list makes Compose plan only the
        listed services plus their ``depends_on`` companions.

        The set is derived from ``docker compose config --format json`` — the
        resolved configuration itself (env scales, tracks, overrides, and
        consumer overlays all already applied) — never a hand-maintained
        allowlist. A service is enabled when ``deploy.replicas`` is absent or
        non-zero.

        Returns the sorted enabled service names, or ``None`` when the
        projection cannot be computed (fail-open: callers fall back to the
        historical full-graph ``up``, which must never be *less* available
        than before this optimization).
        """
        cmd = self._build_compose_command(['config', '--format', 'json'])
        try:
            result = run_with_deadline(
                cmd,
                cwd=str(self.root_dir),
                timeout_seconds=_COMPOSE_PROBE_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                return None
            services = (json.loads(result.stdout) or {}).get("services") or {}
        except Exception:  # noqa: BLE001 — fail-open by contract
            return None
        if not services:
            return None

        enabled: list[str] = []
        disabled: list[str] = []
        for name, spec in services.items():
            replicas = ((spec or {}).get("deploy") or {}).get("replicas")
            if replicas is None or int(replicas) != 0:
                enabled.append(name)
            else:
                disabled.append(name)
        enabled.sort()
        disabled.sort()
        # Make the selected target set inspectable for debugging (#504 AC).
        if disabled:
            self._on_command(
                f"      Targeting {len(enabled)} enabled services "
                f"(excluding {len(disabled)} disabled: {', '.join(disabled)})"
            )
        return enabled

    def start_services(
        self,
        detached: bool = True,
        wait: bool = False,
        wait_timeout_seconds: int = 900,
        services: Optional[list[str]] = None,
    ) -> int:
        """
        Start Docker compose services.
        Always uses --force-recreate to ensure containers are recreated with new port settings.
        This matches the original Bash script behavior.

        Targets only the enabled services from the rendered projection (#504)
        so Compose never builds/pulls images belonging solely to disabled or
        out-of-track services; dependency companions of enabled services are
        included by Compose automatically. Pass ``services`` to override, or
        rely on the fail-open derivation (None → full graph, as before).

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

        targets = services if services is not None else self.enabled_service_targets()

        # #506: rebuild stale local-build images after source or resolved build
        # configuration changes. `--force-recreate` recreates containers but
        # reuses locally-built images, including when a build arg changed.
        build_args = self.source_build_args(targets)
        args.extend(build_args)

        if wait:
            args.extend(['--wait', '--wait-timeout', str(wait_timeout_seconds)])

        if targets:
            args.extend(targets)

        rc = self.execute_compose_command(args)
        if rc == 0 and build_args:
            self.mark_source_built(targets)
        return rc

    # ------------------------------------------------------------------ #
    # Local-build image freshness (#506)
    # ------------------------------------------------------------------ #
    SOURCE_BUILD_MARKER = ".atlas-build-state"

    def _current_source_commit(self) -> Optional[str]:
        """The selected Atlas source commit (submodule/clone HEAD), or None when
        the root is not a git checkout — in which case the coarse drift signal
        is unavailable and freshness behaves exactly as before (no forced
        rebuild)."""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=str(self.root_dir),
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return None
            return result.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    def _source_marker_path(self) -> Path:
        return self.root_dir / self.SOURCE_BUILD_MARKER

    def _current_build_config_digest(self) -> Optional[str]:
        """Hash the resolved local-build inputs without persisting their values.

        Compose itself resolves its complete interpolation grammar before Atlas
        extracts and hashes only local ``build`` and image fields. This catches
        same-commit changes such as ``MLFLOW_IMAGE`` without reimplementing
        Compose expansion or persisting build arguments. Runtime-only
        environment is excluded because it does not change image content.
        """
        try:
            cmd = self._build_compose_command(['config', '--format', 'json'])
            result = run_with_deadline(
                cmd,
                cwd=str(self.root_dir),
                timeout_seconds=_COMPOSE_PROBE_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                return None
            services = (json.loads(result.stdout) or {}).get("services") or {}
            payload = json.dumps(
                _local_build_projection(services),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return hashlib.sha256(payload).hexdigest()
        except Exception:  # noqa: BLE001 — indeterminate means rebuild fail-safe
            return None

    @staticmethod
    def _normalized_build_targets(targets: Optional[list[str]]) -> Optional[list[str]]:
        """Canonicalize the Compose target set; ``None`` means full graph."""
        return sorted(set(targets)) if targets else None

    def _current_build_state(
        self, targets: Optional[list[str]] = None
    ) -> Optional[dict[str, object]]:
        build_digest = self._current_build_config_digest()
        if build_digest is None:
            return None
        return {
            "version": 2,
            "source_commit": self._current_source_commit(),
            "build_config_sha256": build_digest,
            "targets": self._normalized_build_targets(targets),
        }

    def capture_build_state(
        self, targets: Optional[list[str]] = None
    ) -> Optional[dict[str, object]]:
        """Capture the exact inputs immediately before a tracked build."""
        self._build_state_capture_attempted = True
        self._build_state_to_mark = self._current_build_state(targets)
        return self._build_state_to_mark

    def pending_source_rebuild(self, targets: Optional[list[str]] = None) -> bool:
        """True when source, build inputs, or the actual target set is stale."""
        expected = self.capture_build_state(targets)
        if expected is None:
            return True
        # Preserve the exact pre-build inputs. Re-rendering after a long build
        # is slower and could incorrectly mark a value changed mid-build as
        # already built.
        self._build_state_to_mark = expected
        try:
            recorded = json.loads(
                self._source_marker_path().read_text(encoding='utf-8')
            )
        except (OSError, json.JSONDecodeError, TypeError):
            return True
        return recorded != expected

    def source_build_args(self, targets: Optional[list[str]] = None) -> list[str]:
        """Return ``['--build']`` when local-build image inputs are stale."""
        if self.pending_source_rebuild(targets):
            self._on_command(
                "      Atlas source or local build configuration changed since "
                "images were last built — rebuilding stale images (--build)."
            )
            return ['--build']
        return []

    def prepare_build_args(
        self, cold: bool, targets: Optional[list[str]] = None
    ) -> list[str]:
        """Capture cold-build inputs or return freshness args for a warm build."""
        if cold:
            self.capture_build_state(targets)
            return []
        return self.source_build_args(targets)

    def mark_source_built(self, targets: Optional[list[str]] = None) -> None:
        """Record source and resolved build inputs after a successful build."""
        state = self._build_state_to_mark
        captured = self._build_state_capture_attempted
        self._build_state_to_mark = None
        self._build_state_capture_attempted = False
        normalized_targets = self._normalized_build_targets(targets)
        if captured:
            if state is None or state.get("targets") != normalized_targets:
                return
        else:
            state = self._current_build_state(targets)
        if state is None:
            return
        try:
            self._source_marker_path().write_text(
                json.dumps(state, sort_keys=True) + '\n', encoding='utf-8'
            )
        except OSError:
            pass

    def validate_compose_config(self) -> tuple[int, str, str, list[str]]:
        """Run ``docker compose config -q`` for the assembled Atlas stack.

        The command is built through the same path as start/stream operations,
        so custom env files, project names, and ``services/_user`` overlays are
        included exactly as they are for a real launch.
        """
        cmd = self._build_compose_command(['config', '-q'])
        try:
            result = run_with_deadline(
                cmd,
                cwd=str(self.root_dir),
                timeout_seconds=_COMPOSE_PROBE_TIMEOUT_SECONDS,
            )
            return result.returncode, result.stdout, result.stderr, cmd
        except subprocess.TimeoutExpired:
            # TimeoutExpired embeds the full command list (project name,
            # --env-file path, every -f overlay); don't echo it into the message.
            return (
                1,
                "",
                "docker compose config timed out; subprocess output redacted",
                cmd,
            )
        except Exception:
            return 1, "", "Error executing docker compose config", cmd
    
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
    
    def perform_cold_start_cleanup(self, project_name: str | None = None) -> bool:
        """Stop this project and remove its containers, volumes, and orphans.

        All output flows through the registered command-echo callback
        (`_on_command`) so the wizard's Live region can stream it into
        the log pane without tearing the alternate screen.

        Docker system pruning is intentionally excluded: ``--cold`` must never
        delete images, caches, networks, or volumes owned by other projects.
        """
        previous_project = self.project_name_override
        if project_name is not None:
            self.project_name_override = project_name
        try:
            self._on_command("    - Removing project containers, volumes, and orphans...")
            result = self.stream_compose(
                ['down', '--volumes', '--remove-orphans'],
                on_line=self._on_command,
            )
            return result == 0
        finally:
            self.project_name_override = previous_project
    
    def perform_cold_stop_cleanup(self) -> bool:
        """Stop this project and remove its containers, volumes, and orphans.

        Host-wide Docker pruning is deliberately not part of project cleanup.
        """
        project_name = self.project_name_override or self.config_parser.get_project_name()
        print("    - Stopping containers and removing volumes...")
        result = self.execute_compose_command(
            ['down', '--volumes', '--remove-orphans'],
            project_name=project_name,
        )
        return result == 0
    
    def build_services(
        self,
        no_cache: bool = False,
        pull: bool = False,
        services: Optional[list[str]] = None,
    ) -> int:
        """
        Build Docker compose services with optional flags.

        Args:
            no_cache: Whether to build without cache (--no-cache flag)
            pull: Whether to pull latest images (--pull flag)
            services: Optional explicit target list (#504) — when given,
                only these services' images are built (disabled services'
                broken builds can't abort a cold start).

        Returns:
            int: Return code from the command
        """
        args = ['build']

        if no_cache:
            args.append('--no-cache')

        if pull:
            args.append('--pull')

        if services:
            args.extend(services)

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

    def compose_service_ps_json(self, service: str) -> tuple[list[dict], str | None]:
        """Inspect one compose service, including stopped and unhealthy states."""
        return self._compose_ps_json(service)

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

    def _compose_command_prefix(
        self,
        use_env_file: bool,
        top_level_flags: Optional[List[str]],
    ) -> List[str]:
        full_cmd = self.detect_docker_compose_command().split()
        if top_level_flags:
            full_cmd.extend(top_level_flags)
        project_name = self.project_name_override or self.config_parser.get_project_name()
        full_cmd.extend(['-p', project_name])
        if use_env_file and self.config_parser.env_file_exists():
            full_cmd.extend([f'--env-file={self.config_parser.env_file_path}'])
        return full_cmd

    def _build_compose_command_state(
        self,
        args: List[str],
        use_env_file: bool = True,
        top_level_flags: Optional[List[str]] = None,
    ) -> tuple[List[str], bool]:
        full_cmd = self._compose_command_prefix(use_env_file, top_level_flags)
        file_args, optional_included = self._validated_compose_file_args(args, full_cmd)
        full_cmd.extend(file_args)
        full_cmd.extend(args)
        return full_cmd, optional_included

    def _build_compose_command(
        self,
        args: List[str],
        use_env_file: bool = True,
        top_level_flags: Optional[List[str]] = None,
    ) -> List[str]:
        """Build compose argv without executing it."""
        command, _consumer_included = self._build_compose_command_state(
            args, use_env_file, top_level_flags
        )
        return command

    def _stream_compose_command(
        self, full_cmd: List[str], on_line: Callable[[str], None]
    ) -> int:
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
        except Exception as exc:
            on_line(f"❌ Error launching docker compose: {exc}")
            return 1
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                on_line(line.rstrip("\n"))
            return proc.wait()
        except KeyboardInterrupt:
            return self._terminate_subprocess(proc)
        except BaseException:
            self._terminate_subprocess(proc)
            raise
        finally:
            if proc.stdout is not None:
                proc.stdout.close()

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
        try:
            full_cmd, _optional_included = self._build_compose_command_state(
                args,
                use_env_file=use_env_file,
                top_level_flags=['--ansi=always'],
            )
        except Exception as exc:
            on_line(f"❌ Error preparing docker compose command: {exc}")
            return 1
        self._on_command(f"      Command: {' '.join(full_cmd)}")
        return self._stream_compose_command(full_cmd, on_line)

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

    def _report_interrupted_compose(self, project_name: str) -> None:
        """Tell the operator an aborted bring-up may still be building.

        The build runs in the Docker daemon rather than this process tree, so an
        interrupt does not reliably stop it. A build that completes afterwards
        contributes containers no run explains — including services a later run
        disabled — which reads exactly like a broken source toggle. Naming that
        possibility, and where to look, is what separates the two.
        """
        self._on_command(
            "⚠ Interrupted. The image build runs inside the Docker daemon and "
            "may still be running; it can still create containers for this run.\n"
            "  Check before drawing conclusions: docker ps --filter "
            f"label=com.docker.compose.project={project_name}"
        )

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
