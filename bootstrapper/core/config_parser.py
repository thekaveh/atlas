"""
Configuration parsing for YAML service configs and .env files.

Python implementation of configuration parsing from start.sh and stop.sh.
"""

import os
import re
import sys
from typing import Dict, Optional, Any
from pathlib import Path

from utils.atomic_write import create_private_backup


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


class ConfigParser:
    """Configuration parser for Atlas."""
    
    def __init__(self, root_dir: Optional[str] = None):
        """
        Initialize the config parser.

        Args:
            root_dir: Root directory containing .env and config files.
                     If None, uses the parent of the bootstrapper directory.
        """
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
        # the flag from .env so profile validation matches the runtime wiring.
        env = self.parse_env_file()
        adapter_enabled = (
            (env.get("LIGHTRAG_RERANK_ADAPTER_ENABLED") or "").strip().lower() == "true"
        )
        return load_consumer_config(
            self.root_dir,
            lightrag_rerank_adapter_enabled=adapter_enabled,
        )
    
    def parse_env_file(self) -> Dict[str, str]:
        """
        Parse .env file for all variables.
        
        Returns:
            dict: Dictionary of environment variables from .env file
        """
        env_vars = {}
        
        if not self.env_file_path.exists():
            return env_vars
            
        with open(self.env_file_path, 'r', encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                    
                # Split on first = only
                if '=' in line:
                    key, value = line.split('=', 1)
                    value = value.strip()
                    if value[:1] in ('"', "'"):
                        # Quoted value: take the quoted span verbatim —
                        # a `#` inside quotes is data, not a comment
                        # (PASSWORD="ab#cd" used to be read as `ab`).
                        quote = value[0]
                        end = value.find(quote, 1)
                        if end != -1:
                            value = value[1:end]
                        else:
                            # Unterminated quote — legacy cleanup.
                            value = value.strip('"').strip("'")
                    else:
                        # Unquoted: a comment starts only at a hash
                        # preceded by whitespace (`ab#cd` is a value;
                        # `abc  # note` carries a comment).
                        for i, ch in enumerate(value):
                            if ch == '#' and (i == 0 or value[i - 1] in ' \t'):
                                value = value[:i]
                                break
                        value = value.strip()
                    env_vars[key.strip()] = value
                    
        return env_vars
    
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
            
        # Parse SOURCE variables from .env file using the same regex as start.sh
        if self.env_file_path.exists():
            with open(self.env_file_path, 'r', encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Use the same regex pattern as start.sh: ^([A-Z0-9_]+_SOURCE)=([^#]*)
                    match = re.match(r'^([A-Z0-9_]+_SOURCE)=([^#]*)', line)
                    if match:
                        var_name = match.group(1)
                        var_value = match.group(2).strip().strip('"').strip("'")
                        source_mapping[var_name] = var_value
                        
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
