"""
Port management utilities for validating and updating service ports.

All port defaults are derived from ``services.topology.get_topology`` —
the single source of truth for slot allocation. There is no hard-coded
PORT_MAPPING here anymore: ``Topology.port_defaults`` is computed from
the live manifests and re-derived for any base port the caller supplies.
The topology is cached process-wide by the canonical accessor, so each
call to ``port_defaults_for`` is effectively free after the first.
"""

import os
import socket
import re
import tempfile
from typing import Optional, Dict, List
from pathlib import Path
from core.config_parser import ConfigParser, DEFAULT_BASE_PORT


class PortManager:
    """Manages port validation and assignment for Atlas services."""

    def __init__(self, root_dir: Optional[str] = None):
        """
        Initialize port manager.

        Args:
            root_dir: Root directory containing .env file
        """
        if root_dir is None:
            # Default to parent directory of bootstrapper
            self.root_dir = Path(__file__).resolve().parent.parent.parent
        else:
            self.root_dir = Path(root_dir)

        self.config_parser = ConfigParser(str(self.root_dir))
        self._services_root = self.root_dir / "services"

    # ─── topology-derived helpers ────────────────────────────────────

    def port_defaults_for(self, base_port: int) -> Dict[str, int]:
        """Return the topology-derived {port_var: port} mapping for the
        given base port. Backed by the canonical ``get_topology`` LRU —
        the first call per (services_root, base_port) tuple does the disk
        scan; subsequent calls hit the cache.
        """
        # Local import keeps ``services.topology`` out of the import chain
        # at PortManager class definition time (it transitively touches
        # PyYAML and the manifest loader).
        from services.topology import get_topology
        topology = get_topology(self._services_root, base_port=base_port)
        return topology.port_defaults

    def port_offsets(self) -> Dict[str, int]:
        """Return the {port_var: offset_from_DEFAULT_BASE_PORT} mapping
        derived from topology at DEFAULT_BASE_PORT. Used by callers that
        need the relative slot for synthetic env rebuilds (the Textual
        launch / wizard "what would the ports be if base_port=X" logic).
        """
        defaults = self.port_defaults_for(DEFAULT_BASE_PORT)
        return {var: port - DEFAULT_BASE_PORT for var, port in defaults.items()}

    # ─── public API ──────────────────────────────────────────────────

    def validate_base_port(self, port: int) -> bool:
        """
        Validate that a base port is in valid range.

        Args:
            port: Base port number to validate

        Returns:
            bool: True if port is valid (1024-65535 minus the largest
            slot offset declared by the topology)
        """
        offsets = self.port_offsets()
        max_offset = max(offsets.values()) if offsets else 0
        return 1024 <= port <= 65535 - max_offset

    def check_port_availability(self, port: int) -> bool:
        """
        Check if a specific port is available.

        Args:
            port: Port number to check

        Returns:
            bool: True if port is available
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', port))
                return result != 0  # Port is available if connection fails
        except Exception:
            return True  # Assume available on error

    def check_port_range_availability(self, base_port: int) -> List[int]:
        """
        Check availability of all ports in the range starting from base_port.

        Args:
            base_port: Starting port number

        Returns:
            list: List of ports that are in use
        """
        used_ports = []

        for port_var, port in self.port_defaults_for(base_port).items():
            if not self.check_port_availability(port):
                used_ports.append(port)

        return used_ports

    def calculate_port_assignments(self, base_port: int) -> Dict[str, int]:
        """
        Calculate all port assignments based on base port.

        Args:
            base_port: Base port number

        Returns:
            dict: Dictionary mapping port variable names to port numbers
        """
        return dict(self.port_defaults_for(base_port))

    def update_env_ports(self, base_port: int, create_backup: bool = True) -> bool:
        """
        Update port assignments in .env file based on base port.
        Replicates the update_port() function from start.sh.

        Args:
            base_port: Base port number
            create_backup: Whether to create a backup before updating

        Returns:
            bool: True if successful
        """
        if not self.validate_base_port(base_port):
            print(f"❌ Invalid base port: {base_port}")
            return False

        env_file_path = self.config_parser.env_file_path

        if not env_file_path.exists():
            print(f"❌ .env file not found: {env_file_path}")
            return False

        try:
            # Create backup if requested
            if create_backup:
                self.config_parser.create_env_backup()

            # Read current .env file
            with open(env_file_path, 'r', encoding="utf-8") as f:
                content = f.read()

            # Calculate new port assignments
            port_assignments = self.calculate_port_assignments(base_port)

            # Update each port in the content. Preserve any inline
            # comment that follows the value — only the numeric portion
            # is replaced. BASE_PORT itself is persisted too: it's the
            # allocator's anchor, not a slot, so calculate_port_assignments
            # omits it — but without rewriting it here, a `--base-port
            # 64000` run left BASE_PORT=63000 in .env and the next
            # flagless run (which now PRESERVES .env's BASE_PORT) silently
            # reverted every port to the old layout.
            updated_content = content
            port_assignments = dict(port_assignments)
            port_assignments['BASE_PORT'] = base_port
            for port_var, new_port in port_assignments.items():
                # Group 3 must swallow trailing whitespace even without a
                # comment, or `VAR=63002 ` (trailing space) silently no-ops.
                pattern = rf'^({re.escape(port_var)}\s*=\s*)([^\s#]*)([ \t]*(?:#.*)?)$'
                replacement = rf'\g<1>{new_port}\g<3>'
                updated_content = re.sub(pattern, replacement, updated_content, flags=re.MULTILINE)

            # Write atomically: a crash/SIGKILL/OOM during an in-place
            # open(...,'w') truncates the secrets-bearing .env (every MINIO
            # secret, JWT, DB password). Mirror the atomic tmp+os.replace
            # pattern used by source_override_manager / service_config /
            # hosts_manager. chmod the tmp to the original's mode BEFORE
            # writing so a user-chmod'd 0600 .env isn't briefly exposed via
            # a umask-default tmp; the empty tmp between mkstemp and chmod
            # holds no secrets.
            try:
                original_mode = env_file_path.stat().st_mode
            except OSError:
                original_mode = 0o600
            fd, tmp = tempfile.mkstemp(
                dir=str(env_file_path.parent), prefix=".env.", suffix=".tmp"
            )
            try:
                os.chmod(tmp, original_mode)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(updated_content)
                os.replace(tmp, str(env_file_path))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

            return True

        except Exception as e:
            print(f"❌ Failed to update ports in .env file: {e}")
            return False

    def get_port_conflicts(self, base_port: int) -> Dict[str, int]:
        """
        Get a mapping of port variables to conflicting port numbers.

        Args:
            base_port: Base port to check from

        Returns:
            dict: Dictionary mapping port variable names to conflicting ports
        """
        conflicts = {}
        port_assignments = self.calculate_port_assignments(base_port)

        for port_var, port in port_assignments.items():
            if not self.check_port_availability(port):
                conflicts[port_var] = port

        return conflicts

    def suggest_available_base_port(self, start_from: int = 50000, max_attempts: int = 100) -> Optional[int]:
        """
        Suggest an available base port by checking ranges.

        Args:
            start_from: Port number to start checking from
            max_attempts: Maximum number of base ports to try

        Returns:
            int: Suggested base port, or None if none found
        """
        for attempt in range(max_attempts):
            candidate_base = start_from + (attempt * 100)  # Try every 100 ports

            if not self.validate_base_port(candidate_base):
                continue

            used_ports = self.check_port_range_availability(candidate_base)
            if not used_ports:
                return candidate_base

        return None
