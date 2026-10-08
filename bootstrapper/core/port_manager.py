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
import time
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


def _runs_no_container(source: Optional[str]) -> bool:
    """A source that publishes none of the service's container ports:
    disabled, the LLM provider's cloud-only ``none`` (Ollama not run), or a
    host-run ``*localhost*`` variant (scaled to 0; its slot never binds)."""
    value = (source or "").strip()
    return value in ("disabled", "none") or "localhost" in value


def _free_despite_time_wait(port: int, bind_ip: str = "") -> bool:
    """Whether a port refused by the strict probe is only held by TIME_WAIT.

    Docker's (Go) listeners set SO_REUSEADDR, so a recently closed connection
    does not stop them binding. Retry with SO_REUSEADDR on every address a
    real listener could hold: on macOS a reusable wildcard bind alone would
    hide a live 127.0.0.1 listener, so all four must succeed. (On Linux a
    bound-but-not-listening SO_REUSEADDR socket also passes; Docker could
    bind there too, so only a not-yet-listening server can race this.)
    """
    return all(_reusable_bind(family, address) for family, address in _reuse_probe_addresses(port, bind_ip))


def _reuse_probe_addresses(port: int, bind_ip: str) -> list:
    addresses = [(socket.AF_INET, ("0.0.0.0", port)), (socket.AF_INET, ("127.0.0.1", port))]
    if socket.has_ipv6:
        addresses += [(socket.AF_INET6, ("::", port)), (socket.AF_INET6, ("::1", port))]
    if bind_ip and all(bind_ip != address[0] for _family, address in addresses):
        # A specific HOST_BIND_IP (LAN address) is where compose will bind;
        # macOS lets the four reusable binds above coexist with a live
        # listener there.
        family = socket.AF_INET6 if ":" in bind_ip else socket.AF_INET
        addresses.append((family, (bind_ip, port)))
    return addresses


def _reusable_bind(family: int, address: tuple) -> bool:
    """SO_REUSEADDR bind; an unsupported IPv6 stack counts as free."""
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            probe.bind(address)
    except OSError as exc:
        return family == socket.AF_INET6 and exc.errno in (
            errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL,
        )
    return True


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
    # An `export VAR=` line is VAR to the reader and Compose; group 1 keeps
    # the prefix so the rewrite does too (#1368).
    return (
        rf'^([ \t]*(?:export[ \t]+)?{re.escape(var)}{horizontal}={horizontal})'
        rf'([^\s#]*)([ \t]*(?:#.*)?)$'
    )


def _with_source_overrides(sources: Optional[dict], overrides: Optional[dict]) -> dict:
    """``.env``'s sources with this run's overrides applied. Keys may come as
    env vars or as ``--<svc>-source`` parameter names (#1391)."""
    applied = {key.upper(): value for key, value in (overrides or {}).items() if value}
    return {**(sources or {}), **applied}


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
        in_use = False
        with ExitStack() as cleanup:
            for family, address in families:
                probe, error = _bind_probe(family, address)
                if error is not None:
                    error_number = getattr(error, "errno", None)
                    if family == socket.AF_INET6 and error_number in unsupported_ipv6:
                        continue
                    if error_number != errno.EADDRINUSE:
                        return False
                    in_use = True
                    break
                assert probe is not None
                cleanup.callback(probe.close)
        # Outside the stack: a still-held strict IPv4 probe would make the
        # fallback's own 0.0.0.0 bind fail (IPv6 TIME_WAIT read as in use).
        return _free_despite_time_wait(port, self._host_bind_ip()) if in_use else True

    def _host_bind_ip(self) -> str:
        """HOST_BIND_IP as a bare address ("127.0.0.1:" -> "127.0.0.1").

        An exported value wins, as it does for compose's interpolation.
        """
        raw = os.environ.get("HOST_BIND_IP")
        if raw is None:
            try:
                raw = self.config_parser.parse_env_file().get("HOST_BIND_IP", "")
            except (OSError, UnicodeDecodeError):  # unreadable .env: defaults
                return ""
        return (raw or "").strip().rstrip(":").strip("[]")

    def check_port_range_availability(
        self, base_port: int, source_overrides: Optional[dict] = None
    ) -> List[int]:
        """
        Check availability of all ports in the range starting from base_port.

        Args:
            base_port: Starting port number
            source_overrides: ``*_SOURCE`` values this run applies over `.env`
                (``--<svc>-source`` flags), so a service the run enables is
                probed and one it disables is not (#1391)

        Returns:
            list: List of ports that are in use
        """
        used_ports = []
        skip = self._disabled_port_vars(source_overrides)

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

    def conflicts_after_release(
        self, base_port: int, timeout_s: float = 15.0, interval_s: float = 0.5
    ) -> Dict[str, int]:
        """Port conflicts once a just-stopped stack's ports are released.

        Right after ``compose down`` Docker Desktop's port forwarder frees the
        published ports a moment later, so an immediate probe reported the
        stack's own port as in use and the warm start aborted with every
        container down (#1438). Re-probe only the conflicting ports until they
        clear or ``timeout_s`` passes; a genuinely foreign listener still
        conflicts after the wait.
        """
        conflicts = self.get_port_conflicts(base_port)
        deadline = time.monotonic() + timeout_s
        while conflicts and time.monotonic() < deadline:
            time.sleep(interval_s)
            conflicts = {
                port_var: port for port_var, port in conflicts.items()
                if not self.check_port_availability(port)
            }
        return conflicts

    def _disabled_port_vars(self, source_overrides: Optional[dict] = None) -> set:
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
        sources = _with_source_overrides(sources, source_overrides)
        if not sources:
            return set()
        try:
            from services.topology import get_topology

            rows = get_topology().rows
        except Exception:  # noqa: BLE001
            return set()
        disabled = {
            row.port_var
            for row in rows
            if row.port_var
            and row.source_var
            and _runs_no_container(sources.get(row.source_var))
        }
        return disabled | self._disabled_manifest_port_vars(sources)

    def _disabled_manifest_port_vars(self, sources: dict) -> set:
        """Every host ``*_PORT`` a disabled manifest declares.

        A topology row names one port per service; Ray (GCS, client),
        Redpanda (Kafka), OpenClaw (bridge), the exporters and others publish
        more, all equally unbound. Fails open (empty set) like the caller.
        """
        try:
            from pathlib import Path

            from services.manifests import load_manifests

            manifests = load_manifests(Path(self.config_parser.root_dir) / "services")
        except Exception:  # noqa: BLE001 — fail open: probe these ports
            return set()
        return {
            decl.name
            for manifest in manifests
            if manifest.sources and _runs_no_container(sources.get(manifest.sources.var))
            for decl in manifest.env
            if decl.name.endswith("_PORT") and "_LOCALHOST_" not in decl.name
        }

    def suggest_available_base_port(self, start_from: int = 20000, max_attempts: int = 100) -> Optional[int]:
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

    def auto_base_port(
        self, start_from: int = 20000, max_attempts: int = 200,
        source_overrides: Optional[dict] = None,
    ) -> Optional[int]:
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
            if not self.check_port_range_availability(candidate, source_overrides):
                return candidate
        return None
