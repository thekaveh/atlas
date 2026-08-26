#!/usr/bin/env python3
"""
Atlas - Stop Script

Python implementation of stop.sh with full feature parity.
Cross-platform stop script for Atlas — the self-hosted engineering platform.
"""

import os
import subprocess
import sys
from pathlib import Path
import click

# Add the current directory to the path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from utils.banner import BannerDisplay
from utils.hosts_manager import HostsManager
from utils.submodule_pin_guard import warn_if_submodule_pin_drifted
from core.config_parser import ConfigParser
from core.docker_manager import DockerManager


def _run_privileged_hosts_cleanup() -> bool:
    """Elevate only the hosts-file mutation, never the repository workflow."""
    from utils.system import is_elevated

    if is_elevated():
        return HostsManager().cleanup_hosts_entries()
    if os.name == "nt":
        print("  • Please run from an Administrator shell to modify the hosts file")
        return False

    bootstrapper_dir = Path(__file__).resolve().parent
    repo_root = bootstrapper_dir.parent
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(bootstrapper_dir)
        if not existing_pythonpath
        else f"{bootstrapper_dir}{os.pathsep}{existing_pythonpath}"
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    helper = (
        "from utils.hosts_manager import HostsManager; "
        "raise SystemExit(0 if HostsManager().cleanup_hosts_entries() else 1)"
    )
    print("  • --clean-hosts needs to edit your hosts file; requesting sudo for that write only.")
    try:
        result = subprocess.run(
            [
                "sudo",
                "env",
                f"PYTHONPATH={env['PYTHONPATH']}",
                "PYTHONDONTWRITEBYTECODE=1",
                sys.executable,
                "-c",
                helper,
            ],
            cwd=repo_root,
            env=env,
            check=False,
        )
    except OSError as exc:
        print(f"  • Could not launch sudo for hosts cleanup: {exc}")
        return False
    return result.returncode == 0


class AtlasStopper:
    """Main class for stopping Atlas."""
    
    def __init__(self):
        # Set root directory first
        self.root_dir = Path(__file__).resolve().parent.parent
        
        # Initialize all managers with correct root_dir
        self.banner = BannerDisplay()
        self.hosts_manager = HostsManager()
        self.config_parser = ConfigParser(str(self.root_dir))
        self.docker_manager = DockerManager(str(self.root_dir))

    def persist_project_name(self, project_name: str) -> bool:
        """Best-effort persist PROJECT_NAME to .env so this teardown — and the
        next bare start/stop — target the same container family. No-op if .env
        is absent (nothing to tear down anyway); the resolved name is still used
        for this invocation."""
        if not project_name or not self.config_parser.env_file_exists():
            return True
        from utils.source_override_manager import SourceOverrideManager
        return SourceOverrideManager(self.config_parser).update_env_file(
            {"PROJECT_NAME": project_name}
        )

    def validate_persisted_project_name(self, project_name_override: str = None) -> bool:
        """Validate the stored PROJECT_NAME before Docker or compose preflights."""
        if project_name_override is not None or not self.config_parser.env_file_exists():
            return True
        try:
            self.config_parser.get_project_name()
        except ValueError as exc:
            click.echo(
                f"stop.sh: invalid PROJECT_NAME in {self.config_parser.env_file_path}: {exc}",
                err=True,
            )
            return False
        return True

    def show_usage(self):
        """Display usage information."""
        usage_text = """
Usage: ./stop.sh [options]   (or: python bootstrapper/stop.py)

Options:
  --project, -p         Compose project to tear down (defaults to PROJECT_NAME / "atlas")
  --cold                Remove volumes (data will be lost)
  --clean-hosts         Remove Atlas hosts file entries (requires sudo/admin)
  --stop-managed-hosts  Also stop HOST-GLOBAL managed runtimes (ComfyUI-MPS / vLLM-Metal / Blender MCP)
  --help-usage          Show this detailed usage message
  --help                Show the option summary

Managed host runtimes:
  Apple-Silicon/Metal ComfyUI-MPS, vLLM-Metal, and Blender MCP run as native HOST-GLOBAL
  processes on fixed loopback ports, shared by every Atlas consumer on this
  machine. A project-scoped stop leaves them running by default (with an
  advisory) so it can't interrupt another consumer. Pass --stop-managed-hosts
  to tear them down explicitly — this affects ALL consumers using them.

Examples:
  ./stop.sh                        # Stop this project's containers, preserve data + managed hosts
  ./stop.sh --cold                 # Stop this project and remove its containers, orphans, and named volumes
  ./stop.sh --clean-hosts          # Stop containers and clean up hosts file
  ./stop.sh --stop-managed-hosts   # Also stop ComfyUI-MPS / vLLM-Metal / Blender MCP
"""
        print(usage_text)
        
    def show_configuration_info(self, cold_stop: bool, clean_hosts: bool,
                                project_name_override: str = None):
        """Display environment configuration information.

        ``project_name_override`` (from --project / -p) wins over the .env value
        so the teardown targets exactly the requested container family.
        """
        self.banner.show_section_header("Environment Configuration", "📋")

        # Check .env file
        if self.config_parser.env_file_exists():
            timestamp = self.config_parser.get_env_file_timestamp()
            self.banner.show_status_message(f"Found .env file with timestamp: {timestamp}", "info")

            # Get project name
            project_name = project_name_override or self.config_parser.get_project_name()
            self.banner.show_status_message(f"Project name: {project_name}", "info")
        else:
            self.banner.show_status_message(".env file not found. Using default configuration.", "warning")
            project_name = project_name_override or self.config_parser.get_project_name()
            
        # Show Docker compose command
        compose_cmd = self.docker_manager.get_compose_command_display()
        self.banner.show_status_message(f"Using Docker Compose command: {compose_cmd}", "info")
        
        # Show stop options
        if cold_stop:
            self.banner.show_status_message("Cold Stop: Yes (removing project volumes)", "warning")
            
        if clean_hosts:
            self.banner.show_status_message("Clean Hosts: Yes (will remove hosts file entries)", "info")
            
        return project_name

    def ensure_dependencies_available(self) -> bool:
        """Validate Docker Compose before running commands that read include:."""
        if not self.docker_manager.check_docker_available():
            self.banner.show_status_message(
                "Docker is not available. Please start Docker Desktop or install Docker.",
                "error",
            )
            return False
        compose_ok, compose_msg = self.docker_manager.check_compose_version()
        level = "info" if compose_ok else "error"
        self.banner.show_status_message(compose_msg, level)
        return compose_ok
        
    def stop_services(self, cold_stop: bool, project_name: str) -> bool:
        """Stop Docker services."""
        self.banner.show_section_header("Stopping Docker Compose Services", "🐳")
        previous_project = self.docker_manager.project_name_override
        self.docker_manager.project_name_override = project_name
        try:
            if cold_stop:
                self.banner.show_status_message("Performing cold stop (removing project volumes)...", "warning")
                self.banner.console.print("⚠️ WARNING: This will permanently delete all data!", style="bold red")
                print()

                # Use the project-scoped cold stop cleanup from Docker manager.
                success = self.docker_manager.perform_cold_stop_cleanup()

                if success:
                    self.banner.show_status_message("Cold stop completed successfully - all containers stopped and data removed", "success")
                else:
                    self.banner.show_status_message("Some issues occurred during cold stop", "warning")

                return success

            else:
                self.banner.show_status_message("Performing standard stop (preserving volumes)...", "info")
                result = self.docker_manager.stop_services(remove_volumes=False, remove_orphans=True)

                if result == 0:
                    self.banner.show_status_message("All containers stopped successfully - data volumes preserved", "success")
                    return True
                else:
                    self.banner.show_status_message("Some issues occurred while stopping containers", "warning")
                    return False
        finally:
            self.docker_manager.project_name_override = previous_project
                
    def stop_managed_comfyui_mps(self) -> bool:
        """Stop the Atlas-managed Apple-Silicon/Metal ComfyUI host process (#335).

        The ``managed-localhost-mps`` source runs a native ComfyUI process on the
        host (not a container), so ``docker compose down`` never touches it — Atlas
        must tear it down explicitly. A no-op when no process is running. The
        current SOURCE value is intentionally ignored: a prior launch may still
        own a process after the selection changed.
        """
        try:
            env = (
                self.config_parser.parse_env_file()
                if self.config_parser.env_file_exists()
                else {}
            )
            from services.comfyui_mps_manager import manager_from_env

            manager = manager_from_env(env)
            before = manager.status()
            stopped = manager.stop()
            after = manager.status()
            if after.running or (
                not stopped and getattr(manager, "pid_file", None) is not None
                and manager.pid_file.exists()
            ):
                self.banner.show_status_message(
                    "Managed ComfyUI (MPS) host is still running after stop.",
                    "warning",
                )
                return False
            if before.running and stopped:
                self.banner.show_status_message(
                    "Stopped the managed Apple-Silicon/Metal ComfyUI host process.",
                    "info",
                )
            return True
        except Exception as exc:  # noqa: BLE001 — teardown must never break stop
            self.banner.show_status_message(
                f"Could not stop the managed ComfyUI (MPS) host: {exc}", "warning"
            )
            return False

    def stop_managed_blender_mcp(self) -> bool:
        """Stop the Atlas-managed headless Blender MCP bridge (#759).

        The ``managed-localhost`` source runs a native headless Blender on the
        host (not a container), so ``docker compose down`` never touches it —
        and it serves an execute_code TCP socket, so it must not silently
        outlive an explicit managed-hosts teardown. A no-op when not running.
        The current SOURCE value is intentionally ignored: a prior launch may
        still own a process after the selection changed.
        """
        try:
            env = (
                self.config_parser.parse_env_file()
                if self.config_parser.env_file_exists()
                else {}
            )
            from services.blender_mcp_manager import manager_from_env

            manager = manager_from_env(env)
            before = manager.status()
            stopped = manager.stop()
            after = manager.status()
            if after.running or (
                not stopped and getattr(manager, "pid_file", None) is not None
                and manager.pid_file.exists()
            ):
                self.banner.show_status_message(
                    "Managed Blender MCP bridge is still running after stop.",
                    "warning",
                )
                return False
            if before.running and stopped:
                self.banner.show_status_message(
                    "Stopped the managed headless Blender MCP bridge.",
                    "info",
                )
            return True
        except Exception as exc:  # noqa: BLE001 — teardown must never break stop
            self.banner.show_status_message(
                f"Could not stop the managed Blender MCP bridge: {exc}", "warning"
            )
            return False

    def stop_managed_vllm_metal(self) -> bool:
        """Stop the Atlas-managed vLLM Metal host process (#379).

        The ``managed-localhost`` source runs a native vLLM process on the host
        (not a container), so ``docker compose down`` never touches it — Atlas
        must tear it down explicitly. A no-op when no process is running. The
        current SOURCE value is intentionally ignored: a prior launch may still
        own a process after the selection changed.
        """
        try:
            env = (
                self.config_parser.parse_env_file()
                if self.config_parser.env_file_exists()
                else {}
            )
            from services.vllm_metal_manager import manager_from_env

            manager = manager_from_env(env)
            before = manager.status()
            stopped = manager.stop()
            after = manager.status()
            if after.running or (
                not stopped and getattr(manager, "pid_file", None) is not None
                and manager.pid_file.exists()
            ):
                self.banner.show_status_message(
                    "Managed vLLM (Metal) host is still running after stop.",
                    "warning",
                )
                return False
            if before.running and stopped:
                self.banner.show_status_message(
                    "Stopped the managed Apple-Silicon/Metal vLLM host process.",
                    "info",
                )
            return True
        except Exception as exc:  # noqa: BLE001 — teardown must never break stop
            self.banner.show_status_message(
                f"Could not stop the managed vLLM (Metal) host: {exc}", "warning"
            )
            return False

    def report_managed_hosts_left_running(self) -> None:
        """Advisory-only counterpart to the managed-host stop methods (#655).

        Managed ComfyUI-MPS, vLLM-Metal, and Blender MCP runtimes are host-global singletons on
        a fixed loopback port, shared by every Atlas consumer on the machine. A
        project-scoped ``./stop.sh`` must NOT terminate it just because *this*
        project stopped — another consumer may still be using it. When one is
        detected running, point the operator at the explicit opt-in. Never stops
        anything; status probes are read-only.
        """
        env = (
            self.config_parser.parse_env_file()
            if self.config_parser.env_file_exists()
            else {}
        )
        try:
            from services.blender_mcp_manager import manager_from_env as _blender_mfe

            if _blender_mfe(env).status().running:
                self.banner.show_status_message(
                    "Managed Blender MCP bridge left running (host-global, shared "
                    "across consumers; serves execute_code on loopback). Stop it "
                    "explicitly with `./stop.sh --stop-managed-hosts` or "
                    "`./start.sh blender-mcp stop`.",
                    "info",
                )
        except Exception:  # noqa: BLE001 — advisory only
            pass
        try:
            from services.comfyui_mps_manager import manager_from_env

            if manager_from_env(env).status().running:
                self.banner.show_status_message(
                    "Managed ComfyUI (MPS) host left running (host-global, shared "
                    "across consumers). Run ./stop.sh --stop-managed-hosts to stop it.",
                    "info",
                )
        except Exception:  # noqa: BLE001 — advisory must never break stop
            pass
        try:
            from services.vllm_metal_manager import manager_from_env

            if manager_from_env(env).status().running:
                self.banner.show_status_message(
                    "Managed vLLM (Metal) host left running (host-global, shared "
                    "across consumers). Run ./stop.sh --stop-managed-hosts to stop it.",
                    "info",
                )
        except Exception:  # noqa: BLE001 — advisory must never break stop
            pass

    def cleanup_hosts_entries(self) -> bool:
        """Clean up hosts file entries if requested."""
        self.banner.show_section_header("Cleaning Up Hosts File", "🧹")

        return _run_privileged_hosts_cleanup()
        
    def show_final_status(self, cold_stop: bool, clean_hosts: bool, services_ok: bool = True, hosts_ok: bool = True, managed_hosts_ok: bool = True):
        """Display final stop status and next steps."""
        print()
        self.banner.console.print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", style="bright_white")

        if not services_ok or not hosts_ok or not managed_hosts_ok:
            self.banner.console.print("⚠️  Atlas stop completed with errors — see messages above", style="bold bright_yellow")
        elif cold_stop:
            self.banner.console.print("🎯 Atlas stopped with complete data cleanup", style="bold bright_green")
            self.banner.console.print("   ✅ All containers stopped and removed")
            self.banner.console.print("   ✅ All data volumes removed")
            self.banner.console.print("   ✅ Project orphans and default network removed")
        else:
            self.banner.console.print("🎯 Atlas stopped successfully", style="bold bright_green")
            self.banner.console.print("   ✅ All containers stopped and removed")
            self.banner.console.print("   ✅ Data volumes preserved")
            
        if clean_hosts:
            if hosts_ok:
                self.banner.console.print("   ✅ Hosts file entries cleaned up")
            else:
                self.banner.console.print(
                    "   ⚠️  Hosts file cleanup FAILED — re-run "
                    "./stop.sh --clean-hosts from an interactive terminal that can approve sudo",
                    style="bold bright_yellow",
                )
            
        self.banner.console.print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", style="bright_white")
        print()
        
        # Show restart instructions
        self.banner.console.print("🔄 To restart the stack, run:", style="bold bright_white")
        self.banner.console.print("   ./start.sh                    # Start with default settings")
        self.banner.console.print("   ./start.sh --base-port 64567  # Start with custom base port")
        
        if cold_stop:
            self.banner.console.print("   ./start.sh --cold             # Recommended after cold stop")
            
        print()
        self.banner.console.print("📚 For more information, check the README.md file", style="bright_white")


def _persist_project_override(stopper: AtlasStopper, project_name: str) -> None:
    """Best-effort persistence; the current teardown still uses the override."""
    try:
        if not stopper.persist_project_name(project_name):
            click.echo(
                "stop.sh: warning: could not persist PROJECT_NAME; "
                "this teardown still uses the requested override",
                err=True,
            )
    except Exception as exc:
        click.echo(
            f"stop.sh: warning: could not persist PROJECT_NAME: {exc}", err=True
        )


@click.command()
@click.option('--project', '-p', 'project_name', type=str, default=None,
              help='Docker Compose project name — the container family to tear '
                   'down (every container/volume/network is prefixed <name>-…). '
                   'Defaults to PROJECT_NAME in .env (or "atlas"), so a bare '
                   './stop.sh tears down exactly what ./start.sh launched. Pass '
                   'this (or set PROJECT_NAME in .env) to stop a specific stack '
                   'when running Atlas as a submodule; it persists to .env.')
@click.option('--cold', is_flag=True, help='Remove volumes (data will be lost)')
@click.option('--clean-hosts', is_flag=True, help='Remove Atlas hosts file entries (requires sudo/admin)')
@click.option('--stop-managed-hosts', is_flag=True,
              help='Also stop host-global managed runtimes (Apple-Silicon/Metal '
                   'ComfyUI-MPS, vLLM-Metal, and Blender MCP). These are HOST-GLOBAL processes '
                   'shared by every consumer on this machine, so a project-scoped '
                   'stop leaves them running by default; pass this to tear them '
                   'down explicitly (affects ALL consumers using them).')
@click.option('--help-usage', is_flag=True, help='Show detailed usage information')
def main(project_name, cold, clean_hosts, stop_managed_hosts, help_usage):
    """Stop Atlas — the self-hosted engineering platform."""

    stopper = AtlasStopper()

    if help_usage:
        stopper.show_usage()
        return

    # ─── Project name (-p / --project) ───────────────────────────────
    # Validate fail-fast, persist to .env (so the next bare start/stop agrees),
    # and use it as the authoritative teardown target for THIS run.
    if project_name is not None:
        from core.config_parser import normalize_project_name
        try:
            project_name = normalize_project_name(project_name)
        except ValueError as exc:
            click.echo(f"stop.sh: {exc}", err=True)
            raise SystemExit(2)
        _persist_project_override(stopper, project_name)

    # Show initial message
    stopper.banner.show_status_message("Stopping Atlas...", "info")
    print()

    try:
        if not stopper.validate_persisted_project_name(project_name):
            sys.exit(2)

        # Step 1: Show configuration information
        project_name = stopper.show_configuration_info(cold, clean_hosts,
                                                       project_name_override=project_name)

        dependencies_ok = stopper.ensure_dependencies_available()

        # Step 2: Stop Docker services when the Docker/Compose preflight is
        # available. Keep going after a preflight or down failure so native
        # hosts and optional hosts-file cleanup are not stranded, but retain a
        # nonzero final status for the container teardown failure.
        services_ok = (
            stopper.stop_services(cold, project_name) if dependencies_ok else False
        )

        # Step 2b/2c: Managed host runtimes (#655). ComfyUI-MPS (#335),
        # vLLM-Metal (#379), and Blender MCP (#759) are native host processes —
        # compose `down` never touches them — but they are HOST-GLOBAL
        # singletons shared by every consumer on this machine, not
        # Compose-project resources. A project-scoped stop must NOT terminate
        # one just because THIS project stopped: another consumer may still be
        # using it (and SOURCE can't prove ownership — `.env` is mutable and the
        # state dir is shared). Default leaves them running with an advisory;
        # --stop-managed-hosts is the explicit, host-global-impact opt-in.
        # Standard and --cold behave identically here.
        if stop_managed_hosts:
            stopper.banner.show_status_message(
                "Stopping HOST-GLOBAL managed runtimes (--stop-managed-hosts) — "
                "this affects every consumer sharing them on this host.",
                "warning",
            )
            comfyui_host_ok = stopper.stop_managed_comfyui_mps()
            vllm_host_ok = stopper.stop_managed_vllm_metal()
            blender_host_ok = stopper.stop_managed_blender_mcp()
            managed_hosts_ok = comfyui_host_ok and vllm_host_ok and blender_host_ok
        else:
            stopper.report_managed_hosts_left_running()
            managed_hosts_ok = True

        # Step 3: Clean up hosts entries if requested
        hosts_ok = True
        if clean_hosts:
            # Don't exit on hosts cleanup failure — but DO tell the truth
            # about it in the final banner instead of a blanket ✅.
            hosts_ok = stopper.cleanup_hosts_entries()

        # #797: read-only pin-drift tripwire (mirrors start.py). After any
        # stop, surface a consumer submodule pin drift loudly instead of
        # silently — the launcher never moves/stages the pin, only warns.
        warn_if_submodule_pin_drifted(stopper.root_dir)

        # Step 4: Show final status
        stopper.show_final_status(
            cold, clean_hosts, services_ok=services_ok, hosts_ok=hosts_ok,
            managed_hosts_ok=managed_hosts_ok,
        )

        if not services_ok or not hosts_ok or not managed_hosts_ok:
            sys.exit(1)
        
    except KeyboardInterrupt:
        print("\n❌ Stop process interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error during stop: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
