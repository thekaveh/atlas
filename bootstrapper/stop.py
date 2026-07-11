#!/usr/bin/env python3
"""
Atlas - Stop Script

Python implementation of stop.sh with full feature parity.
Cross-platform stop script for Atlas — the self-hosted engineering platform.
"""

import sys
from pathlib import Path
import click

# Add the current directory to the path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from utils.banner import BannerDisplay
from utils.hosts_manager import HostsManager
from core.config_parser import ConfigParser
from core.docker_manager import DockerManager


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

    def persist_project_name(self, project_name: str) -> None:
        """Best-effort persist PROJECT_NAME to .env so this teardown — and the
        next bare start/stop — target the same container family. No-op if .env
        is absent (nothing to tear down anyway); the resolved name is still used
        for this invocation."""
        if not project_name or not self.config_parser.env_file_exists():
            return
        from utils.source_override_manager import SourceOverrideManager
        SourceOverrideManager(self.config_parser).update_env_file(
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
  --cold             Remove volumes (data will be lost)
  --clean-hosts      Remove Atlas hosts file entries (requires sudo/admin)
  --help-usage       Show this detailed usage message
  --help             Show the option summary

Examples:
  ./stop.sh                 # Stop all containers, preserve data
  ./stop.sh --cold          # Stop containers, remove project volumes, AND run a global `docker system prune -f --volumes` (touches unused images/volumes of OTHER projects too)
  ./stop.sh --clean-hosts   # Stop containers and clean up hosts file
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
            self.banner.show_status_message("Cold Stop: Yes (removing volumes and aggressive cleanup)", "warning")
            
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
                self.banner.show_status_message("Performing cold stop (removing volumes and aggressive cleanup)...", "warning")
                self.banner.console.print("⚠️ WARNING: This will permanently delete all data!", style="bold red")
                print()

                # Use the enhanced cold stop cleanup from Docker manager
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
                
    def stop_managed_comfyui_mps(self) -> None:
        """Stop the Atlas-managed Apple-Silicon/Metal ComfyUI host process (#335).

        The ``managed-localhost-mps`` source runs a native ComfyUI process on the
        host (not a container), so ``docker compose down`` never touches it — Atlas
        must tear it down explicitly. A no-op when the source isn't selected or no
        process is running. Best-effort: never blocks the rest of the stop flow.
        """
        try:
            if not self.config_parser.env_file_exists():
                return
            env = self.config_parser.parse_env_file()
            if str(env.get("COMFYUI_SOURCE", "")).strip() != "managed-localhost-mps":
                return
            from services.comfyui_mps_manager import manager_from_env

            manager = manager_from_env(env)
            if manager.stop():
                self.banner.show_status_message(
                    "Stopped the managed Apple-Silicon/Metal ComfyUI host process.",
                    "info",
                )
        except Exception as exc:  # noqa: BLE001 — teardown must never break stop
            self.banner.show_status_message(
                f"Could not stop the managed ComfyUI (MPS) host: {exc}", "warning"
            )

    def stop_managed_vllm_metal(self) -> None:
        """Stop the Atlas-managed vLLM Metal host process (#379).

        The ``managed-localhost`` source runs a native vLLM process on the host
        (not a container), so ``docker compose down`` never touches it — Atlas
        must tear it down explicitly. A no-op when the source isn't selected or no
        process is running. Best-effort: never blocks the rest of the stop flow.
        """
        try:
            if not self.config_parser.env_file_exists():
                return
            env = self.config_parser.parse_env_file()
            if str(env.get("VLLM_METAL_SOURCE", "")).strip() != "managed-localhost":
                return
            from services.vllm_metal_manager import manager_from_env

            manager = manager_from_env(env)
            if manager.stop():
                self.banner.show_status_message(
                    "Stopped the managed Apple-Silicon/Metal vLLM host process.",
                    "info",
                )
        except Exception as exc:  # noqa: BLE001 — teardown must never break stop
            self.banner.show_status_message(
                f"Could not stop the managed vLLM (Metal) host: {exc}", "warning"
            )

    def cleanup_hosts_entries(self) -> bool:
        """Clean up hosts file entries if requested."""
        self.banner.show_section_header("Cleaning Up Hosts File", "🧹")

        return self.hosts_manager.cleanup_hosts_entries()
        
    def show_final_status(self, cold_stop: bool, clean_hosts: bool, services_ok: bool = True, hosts_ok: bool = True):
        """Display final stop status and next steps."""
        print()
        self.banner.console.print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", style="bright_white")

        if not services_ok:
            self.banner.console.print("⚠️  Atlas stop completed with errors — see messages above", style="bold bright_yellow")
        elif cold_stop:
            self.banner.console.print("🎯 Atlas stopped with complete data cleanup", style="bold bright_green")
            self.banner.console.print("   ✅ All containers stopped and removed")
            self.banner.console.print("   ✅ All data volumes removed")
            self.banner.console.print("   ✅ Project networks cleaned up")
            self.banner.console.print("   ✅ Docker system pruned")
        else:
            self.banner.console.print("🎯 Atlas stopped successfully", style="bold bright_green")
            self.banner.console.print("   ✅ All containers stopped and removed")
            self.banner.console.print("   ✅ Data volumes preserved")
            
        if clean_hosts:
            if hosts_ok:
                self.banner.console.print("   ✅ Hosts file entries cleaned up")
            else:
                self.banner.console.print(
                    "   ⚠️  Hosts file cleanup FAILED (needs sudo?) — run "
                    "./stop.sh --clean-hosts again with privileges",
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
@click.option('--help-usage', is_flag=True, help='Show detailed usage information')
def main(project_name, cold, clean_hosts, help_usage):
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
        stopper.persist_project_name(project_name)

    # Show initial message
    stopper.banner.show_status_message("Stopping Atlas...", "info")
    print()

    try:
        if not stopper.validate_persisted_project_name(project_name):
            sys.exit(2)

        # Step 1: Show configuration information
        project_name = stopper.show_configuration_info(cold, clean_hosts,
                                                       project_name_override=project_name)

        if not stopper.ensure_dependencies_available():
            sys.exit(1)
        
        # Step 2: Stop Docker services. Keep going on failure so hosts
        # cleanup and the final status still run, but exit non-zero at the
        # end — scripts/CI need a truthful exit code for a failed `down`.
        services_ok = stopper.stop_services(cold, project_name)

        # Step 2b: Stop the Atlas-managed Apple-Silicon/Metal ComfyUI host
        # process (#335) — a native host process compose `down` never touches.
        stopper.stop_managed_comfyui_mps()

        # Step 2c: Stop the Atlas-managed Apple-Silicon/Metal vLLM host
        # process (#379) — likewise a native host process compose ignores.
        stopper.stop_managed_vllm_metal()

        # Step 3: Clean up hosts entries if requested
        hosts_ok = True
        if clean_hosts:
            # Don't exit on hosts cleanup failure — but DO tell the truth
            # about it in the final banner instead of a blanket ✅.
            hosts_ok = stopper.cleanup_hosts_entries()

        # Step 4: Show final status
        stopper.show_final_status(
            cold, clean_hosts, services_ok=services_ok, hosts_ok=hosts_ok,
        )

        if not services_ok:
            sys.exit(1)
        
    except KeyboardInterrupt:
        print("\n❌ Stop process interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error during stop: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
