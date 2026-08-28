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
import errno
import socket
from contextlib import ExitStack, suppress
import re
from typing import Optional, Dict, List
from pathlib import Path
from core.config_parser import ConfigParser, DEFAULT_BASE_PORT
from utils.atomic_write import atomic_write_text


def _bind_probe(family: int, address: tuple) -> tuple[Optional[socket.socket], Exception | None]:
    probe = None
    try:
        probe = socket.socket(family, socket.SOCK_STREAM)
        if family == socket.AF_INET6:
            probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        probe.bind(address)
        return probe, None
    except Exception as exc:  # availability must fail closed on unknown errors
        if probe is not None:
            with suppress(OSError):
                probe.close()
        return None, exc


def _assignment_pattern(var: str) -> str:
    """Match one `VAR=value` line, with an optional trailing comment.

    Group 3 must swallow trailing whitespace even without a comment, or
    `VAR=63002 ` (trailing space) silently no-ops.

    `[^\\S\\n]`, NOT `\\s`, around the `=`. Under `re.MULTILINE` the `$`
    matches before a newline, but `\\s` MATCHES that newline — so for a blank
    `VAR=` the value group runs on into the NEXT line and swallows the whole
    following assignment. No current caller reaches that state (`update_env_ports`
    leaves a blank value alone), so this is a latent trap rather than a live
    bug; the pattern now means what its name says regardless of who calls it.
    """
    horizontal = r'[^\S\n]*'
    return rf'^({re.escape(var)}{horizontal}={horizontal})([^\s#]*)([ \t]*(?:#.*)?)$'


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
        Check whether wildcard listeners can bind a specific port.

        A connect probe is not a bindability probe: a bound-but-not-listening
        socket refuses connections, and an IPv6-only listener is invisible to
        an IPv4 loopback connect. Hold successful IPv4 and IPv6 wildcard binds
        together so a later Docker/host listener can claim both families.

        Args:
            port: Port number to check

        Returns:
            bool: True if port is available
        """
        unsupported_ipv6 = {
            errno.EAFNOSUPPORT,
            errno.EPROTONOSUPPORT,
            errno.EADDRNOTAVAIL,
        }
        families = [(socket.AF_INET, ("0.0.0.0", port))]
        if socket.has_ipv6:
            families.append((socket.AF_INET6, ("::", port)))
        with ExitStack() as cleanup:
            for family, address in families:
                probe, error = _bind_probe(family, address)
                if error is not None:
                    error_number = getattr(error, "errno", None)
                    if family == socket.AF_INET6 and error_number in unsupported_ipv6:
                        continue
                    return False
                assert probe is not None
                cleanup.callback(probe.close)
            return True

    def check_port_range_availability(self, base_port: int) -> List[int]:
        """
        Check availability of all ports in the range starting from base_port.

        Args:
            base_port: Starting port number

        Returns:
            list: List of ports that are in use
        """
        used_ports = []
        skip = self._disabled_port_vars()

        for port_var, port in self.port_defaults_for(base_port).items():
            if port_var in skip:
                continue  # see _disabled_port_vars
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
                pattern = _assignment_pattern(port_var)
                replacement = rf'\g<1>{new_port}\g<3>'
                updated_content = re.sub(pattern, replacement, updated_content, flags=re.MULTILINE)

            atomic_write_text(env_file_path, updated_content, mode=0o600)

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
        skip = self._disabled_port_vars()

        for port_var, port in port_assignments.items():
            if port_var in skip:
                continue
            if not self.check_port_availability(port):
                conflicts[port_var] = port

        return conflicts

    def _disabled_port_vars(self) -> set:
        """Port vars owned by services this `.env` has set to `disabled`.

        Nothing will ever bind them, so a foreign process sitting on one must
        not block the launch. Roughly HALF the probed ports are in this set on
        a default `.env` — Airflow, Grafana, Prometheus, Ray, Spark, Trino,
        Jenkins, Zeppelin, Redpanda, Langfuse, MLflow and more all ship
        disabled — and `handle_port_configuration` turns any hit into an abort.
        The same list feeds `auto_base_port`, so a squatted slot on a disabled
        service also shifted every port of a durable `BASE_PORT: auto`.

        Fails OPEN: if sources cannot be read, nothing is skipped and the
        previous over-broad behavior stands, which is the safe direction.
        """
        try:
            sources = self.config_parser.parse_service_sources()
        except Exception:  # noqa: BLE001 — unreadable .env: probe everything
            return set()
        if not sources:
            return set()
        try:
            from services.topology import get_topology

            rows = get_topology().rows
        except Exception:  # noqa: BLE001
            return set()
        return {
            row.port_var
            for row in rows
            if row.port_var
            and row.source_var
            and sources.get(row.source_var) == 'disabled'
        }

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

    def auto_base_port(self, start_from: int = 20000, max_attempts: int = 200) -> Optional[int]:
        """Find the first wholly-free BASE_PORT block for ``--base-port auto``.

        Steps by the topology's full port span (``max offset + 1``) so candidate
        blocks never overlap, scans below the IANA ephemeral range to dodge
        transient OS-assigned listeners, and never returns ``DEFAULT_BASE_PORT``
        — so an auto-selected consumer stack can't silently squat the default
        port a bare atlas checkout binds. Returns None if no wholly-free block
        is found within ``max_attempts``.

        Args:
            start_from: First base-port candidate (below the ephemeral floor).
            max_attempts: Number of span-stepped candidates to probe.

        Returns:
            int: The first wholly-free base port, or None if none was found.
        """
        offsets = self.port_offsets()
        span = (max(offsets.values()) if offsets else 0) + 1
        for attempt in range(max_attempts):
            candidate = start_from + attempt * span
            if candidate == DEFAULT_BASE_PORT:
                continue
            if not self.validate_base_port(candidate):
                continue
            if not self.check_port_range_availability(candidate):
                return candidate
        return None
