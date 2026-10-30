"""
Configuration parsing for YAML service configs and .env files.

Python implementation of configuration parsing from start.sh and stop.sh.
"""

import os
import re
import sys
from typing import Dict, Optional, Any, Callable
from pathlib import Path

from utils.atomic_write import assert_safe_env_assignment, create_private_backup, decode_env_assignment


# Single source of truth for the default base port. Imported by start.py and
# the Textual wizard (ui/textual/integration.py) so both stay in sync.
# Mirrors the BASE_PORT default from the original start.sh.
DEFAULT_BASE_PORT = 63000

# The default Docker Compose project name / container-family namespace when
# PROJECT_NAME is unset. Every container, volume, and the network are prefixed
# with the resolved PROJECT_NAME (``<name>-<service>``, ``<name>-network``).
DEFAULT_PROJECT_NAME = "atlas"

# Docker Compose project names must be lowercase and match this pattern (it is
# what `docker compose -p` accepts; consumer stacks reusing Atlas as a submodule
# set their own via PROJECT_NAME / --project so start & stop target the right
# family of containers).
_PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def normalize_project_name(raw: str) -> str:
    """Validate + normalize a Compose project name.

    Lower-cases the input (Docker Compose does too) and enforces the
    ``[a-z0-9][a-z0-9_-]*`` shape. Raises ``ValueError`` with an actionable
    message on an empty or otherwise invalid name, so ``--project`` /
    ``PROJECT_NAME`` fail fast with a clear error instead of a cryptic
    ``docker compose`` rejection later.
    """
    name = (raw or "").strip().lower()
    if not name:
        raise ValueError("project name must not be empty")
    if not _PROJECT_NAME_RE.match(name):
        raise ValueError(
            f"invalid project name {raw!r}: must start with a letter or digit and "
            f"contain only lowercase letters, digits, '-' or '_' "
            f"(Docker Compose project-name rules)"
        )
    return name


# Module-level flag: GENAI_ENV_FILE deprecation warning fires once
# per process, not once per ConfigParser instance.
_DEPRECATION_WARNED = False


def _read_custom_env_file_var() -> str:
    """Return the custom .env path from ATLAS_ENV_FILE, or the deprecated
    GENAI_ENV_FILE alias.

    When only GENAI_ENV_FILE is set, emits a one-shot stderr warning
    pointing the user at the new name. ATLAS_ENV_FILE takes precedence
    if both are set.
    """
    primary = os.environ.get('ATLAS_ENV_FILE', '').strip()
    if primary:
        return primary
    legacy = os.environ.get('GENAI_ENV_FILE', '').strip()
    if legacy:
        global _DEPRECATION_WARNED
        if not _DEPRECATION_WARNED:
            print(
                "WARNING: GENAI_ENV_FILE is deprecated; "
                "use ATLAS_ENV_FILE instead.",
                file=sys.stderr,
            )
            _DEPRECATION_WARNED = True
        return legacy
    return ''


def external_env_user_overlay_path() -> Path | None:
    """Resolve an external user overlay relative to the invoking directory."""
    raw_path = os.environ.get("ATLAS_ENV_USER_FILE", "").strip()
    if not raw_path:
        return None
    overlay_path = Path(raw_path).expanduser()
    if not overlay_path.is_absolute():
        invoker_cwd = os.environ.get("ATLAS_INVOKER_CWD", "").strip()
        base_dir = Path(invoker_cwd).expanduser() if invoker_cwd else Path.cwd()
        overlay_path = base_dir / overlay_path
    return overlay_path.resolve()


def _file_stamp(path: Path):
    """Identity of ``path`` as it is right now, or None if unreadable."""
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size, info.st_ino)


def parse_env_overlay_file(overlay_path: Path) -> Dict[str, str]:
    """Read the canonical env grammar without changing the overlay or .env."""
    env_vars: Dict[str, str] = {}
    with open(overlay_path, "r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            assignment = decode_env_assignment(line, origin=f"{overlay_path}:{line_number}")
            if assignment is not None:
                key, value = assignment
                env_vars[key] = value
    return env_vars


def read_env_user_overlay(
    overlay_path: Path, *, label: str,
    reader: Callable[[Path], Dict[str, str]] = parse_env_overlay_file,
    warn: Callable[[str], None] | None = None,
) -> Dict[str, str] | None:
    """Read a whole writable overlay, or warn and skip it without writing."""
    if warn is None:
        warn = lambda message: print(f"WARNING: {message}", file=sys.stderr)
    if not overlay_path.exists():
        warn(f"{label} points to {overlay_path}, but that file does not exist; skipping")
        return None
    if not overlay_path.is_file():
        warn(f"{label} points to {overlay_path}, but it is not a file; skipping")
        return None
    try:
        overrides = reader(overlay_path)
    except (OSError, ValueError) as exc:
        warn(f"{label} points to {overlay_path}, but it could not be read ({exc}); skipping")
        return None
    try:
        for key, value in overrides.items():
            assert_safe_env_assignment(key, value)
    except ValueError as exc:
        warn(f"{label} points to {overlay_path}, but one of its entries "
             f"cannot be written safely ({exc}); skipping the overlay")
        return None
    return overrides


def project_name_error(value: str) -> str | None:
    """Why ``value`` can't be the project name, for the wizard step (#1390)."""
    if value.strip().lower() == "clear":
        return "`clear` cannot be a project name here; type another, or press Enter to keep the current one"
    try:
        normalize_project_name(value)
    except ValueError as exc:
        return str(exc)
    return None


def launch_base_ports(cli_base_port, env_vars: dict, current_base_port: int):
    """(base port the overview shows, base port the launch is given).

    With BASE_PORT=auto in .env and no --base-port the launch gets None, so
    handle_port_configuration resolves a free block; it used to pin the
    default 63000 that ``current_base_port`` falls back to (#1390)."""
    shown = int(cli_base_port if cli_base_port is not None else current_base_port)
    env_auto = (env_vars.get("BASE_PORT") or "").strip().lower() == "auto"
    return shown, (None if cli_base_port is None and env_auto else shown)


class ConfigParser:
    """Configuration parser for Atlas."""
    
    def __init__(self, root_dir: Optional[str] = None):
        """
        Initialize the config parser.

        Args:
            root_dir: Root directory containing .env and config files.
                     If None, uses the parent of the bootstrapper directory.
        """
        #: Keyed on the .env file's own stat identity; see parse_env_file.
        self._env_cache: dict = {}
        #: Per overlay path: (stat identity, overrides); see _effective_overlay_env.
        self._overlay_cache: dict = {}
        if root_dir is None:
            # Default to parent directory of bootstrapper
            self.root_dir = Path(__file__).resolve().parent.parent.parent
        else:
            self.root_dir = Path(root_dir)

        # .env.example always lives in repository root
        self.env_example_path = self.root_dir / ".env.example"

        # .env path can be customized via ATLAS_ENV_FILE environment
        # variable. GENAI_ENV_FILE is honored as a deprecated alias
        # (stderr warning fires once per process).
        self.env_file_path = self._resolve_env_file_path()

        self.service_sources = {}

    def _resolve_env_file_path(self) -> Path:
        """
        Resolve .env file path from ATLAS_ENV_FILE environment variable.
        Falls back to default .env in repository root if not set.

        Honors GENAI_ENV_FILE as a deprecated alias when ATLAS_ENV_FILE
        is unset; emits a stderr warning the first time a process reads it.

        This allows users to specify custom .env file locations, useful for:
        - CI/CD pipelines with secret injection
        - Multiple deployments with different configurations
        - Parent projects managing infrastructure config centrally

        Returns:
            Path: Resolved .env file path
        """
        custom_env_path = _read_custom_env_file_var()

        if custom_env_path:
            # User specified custom path - expand, then anchor relative
            # paths at the repo root. Resolving against CWD made the same
            # command pick different files depending on the launcher:
            # `./start.sh` runs via `uv run --directory bootstrapper`
            # (CWD=bootstrapper/) but falls back to system python at the
            # repo root when uv is absent.
            expanded = Path(custom_env_path).expanduser()
            if not expanded.is_absolute():
                expanded = self.root_dir / expanded
            return expanded.resolve()

        # Default: .env in repository root
        return self.root_dir / ".env"

    def get_env_file_location(self) -> str:
        """
        Get human-readable env file location for display.

        Returns:
            str: Path to the env file being used
        """
        return str(self.env_file_path)

    def is_using_custom_env_file(self) -> bool:
        """
        Check if using a custom env file path via ATLAS_ENV_FILE
        (or its deprecated alias GENAI_ENV_FILE).

        Returns:
            bool: True if either env var is set
        """
        return bool(_read_custom_env_file_var())
        
    def load_yaml_config(self) -> Dict[str, Any]:
        """
        Return the runtime service configuration dict.

        Each manifest at ``services/<name>/service.yml`` owns its per-service
        slice under ``runtime_sc:``, ``runtime_adaptive:``, ``runtime_deps:``
        blocks. ``sc_synthesizer`` concatenates those slices into the dict
        shape (source_configurable, adaptive_services, dependencies,
        service_dependencies) that consumers expect.
        """
        from services.manifests import load_manifests
        from services.sc_synthesizer import synthesize_legacy
        manifests = load_manifests(self.root_dir / "services")
        if not manifests:
            raise FileNotFoundError(
                f"No service manifests found under {self.root_dir / 'services'}"
            )
        return synthesize_legacy(manifests)

    def load_consumer_config(self):
        """Load optional parent-owned consumer manifests for this checkout."""
        from core.consumer_manifest import load_consumer_config

        # #415: a consumer LightRAG query profile may only set enable_rerank=true
        # when this deployment has opted the backend rerank adapter in. Resolve
        # the flag from the effective env, before any early validation writes.
        env = self.parse_env_file()
        env.update(self._effective_overlay_env())
        adapter_enabled = (
            (env.get("LIGHTRAG_RERANK_ADAPTER_ENABLED") or "").strip().lower() == "true"
        )
        return load_consumer_config(
            self.root_dir,
            lightrag_rerank_adapter_enabled=adapter_enabled,
        )

    def _effective_overlay_env(self) -> Dict[str, str]:
        """`.env.user` then ATLAS_ENV_USER_FILE overrides, read once per file version.

        load_consumer_config runs for every compose command, so re-reading
        here repeated each skip and `${...}` warning on every call. Memoized
        on the overlay's stat identity, like parse_env_file: an edit re-reads."""
        merged: Dict[str, str] = {}
        for path, label, warn_if_missing in (
            (self.env_file_path.parent / ".env.user", ".env.user", False),
            (external_env_user_overlay_path(), "ATLAS_ENV_USER_FILE", True),
        ):
            if path is None or not (warn_if_missing or path.exists()):
                continue
            stamp = _file_stamp(path)
            cached = self._overlay_cache.get(path)
            if cached is None or cached[0] != stamp:
                cached = (stamp, read_env_user_overlay(path, label=label) or {})
                self._overlay_cache[path] = cached
            merged.update(cached[1])
        return merged

    def parse_env_file(self) -> Dict[str, str]:
        """
        Parse .env file for all variables.
        
        Returns:
            dict: Dictionary of environment variables from .env file
        """
        env_vars = {}
        
        if not self.env_file_path.exists():
            return env_vars

        # Memoized on the file's own identity. One `check_service_dependencies()`
        # re-read `.env` 77 times and re-parsed SOURCE vars 65 more — and since
        # each was a fresh open, a `.env` edited mid-check yielded an
        # internally INCONSISTENT view. Keying on (mtime_ns, size, inode) means
        # any write invalidates it automatically, so no caller has to remember
        # to flush: `auto_resolve` rewrites `.env` and the next read sees it.
        stamp = self._env_file_stamp()
        cached = self._env_cache.get(stamp)
        if cached is not None:
            return dict(cached)

        # utf-8-sig: a byte-order mark would otherwise become part of the
        # first key's name (#1391).
        with open(self.env_file_path, 'r', encoding="utf-8-sig") as f:
            for line_number, line in enumerate(f, 1):
                assignment = decode_env_assignment(line, origin=f"{self.env_file_path}:{line_number}")
                if assignment is not None:
                    key, value = assignment
                    env_vars[key] = value

        self._env_cache = {stamp: dict(env_vars)}  # single entry: only the current file
        return env_vars

    def _env_file_stamp(self):
        """Identity of the `.env` file as it is right now, or None if unreadable."""
        return _file_stamp(self.env_file_path)
    
    def parse_service_sources(self) -> Dict[str, str]:
        """
        Parse service SOURCE configurations from .env file.
        Replicates the parse_service_sources() function from start.sh.
        
        Returns:
            dict: Dictionary mapping SOURCE variable names to their values
        """
        # Start with only non-customizable services  
        # Backend is always 'container' and not exposed in .env.example
        source_mapping = {
            'BACKEND_SOURCE': 'container',
        }
            
        # Same parser as every other .env reader (and Compose): a stricter
        # `^KEY=` regex missed `WEAVIATE_SOURCE = disabled`, so profile gates,
        # source validation and scaling saw no source Compose then used.
        if self.env_file_path.exists():
            for var_name, var_value in self.parse_env_file().items():
                if re.fullmatch(r'[A-Z0-9_]+_SOURCE', var_name):
                    source_mapping[var_name] = (var_value or "").strip()
                        
        self.service_sources = source_mapping
        return source_mapping
    
    def get_service_source(self, service_var: str) -> str:
        """
        Get the SOURCE value for a specific service.
        
        Args:
            service_var: The SOURCE variable name (e.g. 'LLM_PROVIDER_SOURCE')
            
        Returns:
            str: The SOURCE value for the service
        """
        if not self.service_sources:
            self.parse_service_sources()
        return self.service_sources.get(service_var, '')
    
    def get_project_name(self) -> str:
        """
        Get the project name from .env file.

        Returns:
            str: Project name, defaults to 'atlas' if not found
        """
        env_vars = self.parse_env_file()
        raw = env_vars.get('PROJECT_NAME', DEFAULT_PROJECT_NAME)
        if raw is None or not str(raw).strip():
            return DEFAULT_PROJECT_NAME
        return normalize_project_name(str(raw))
    
    def stored_project_name_matches(self, project_name: str) -> bool:
        """True when .env already names this project in any letter case.

        A hand-edited ``PROJECT_NAME=MyStack`` is project ``mystack``, but its
        volumes interpolate as ``MyStack-*``; rewriting it lower-case would
        point the next start at new, empty volumes.
        """
        if not project_name or not self.env_file_exists():
            return False
        raw = self.parse_env_file().get("PROJECT_NAME")
        return bool(raw) and str(raw).strip().lower() == project_name

    def env_file_exists(self) -> bool:
        """
        Check if .env file exists.
        
        Returns:
            bool: True if .env file exists
        """
        return self.env_file_path.exists()
    
    def get_env_file_timestamp(self) -> Optional[str]:
        """
        Get the .env file modification timestamp.
        
        Returns:
            str: Formatted timestamp string, or None if file doesn't exist
        """
        if not self.env_file_exists():
            return None
            
        import datetime
        mtime = self.env_file_path.stat().st_mtime
        return datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
    
    def create_env_backup(self) -> str:
        """
        Create a backup of the .env file with timestamp.
        Works with both default and custom .env file paths.
        Backup is created in the same directory as the .env file.

        Returns:
            str: Path to the backup file

        Raises:
            FileNotFoundError: If .env file doesn't exist
        """
        if not self.env_file_exists():
            raise FileNotFoundError("Cannot backup .env file - it doesn't exist")

        backup_path = create_private_backup(self.env_file_path)
        return str(backup_path)
