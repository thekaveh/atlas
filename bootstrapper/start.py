#!/usr/bin/env python3
"""
Atlas - Start Script

Python implementation of start.sh with full feature parity.
Cross-platform startup script for Atlas — the self-hosted engineering platform.
"""

import re
import sys
import os
import subprocess
from datetime import date
from pathlib import Path
import click
from typing import Dict, Optional

from services.migrations.migration_v1 import (
    apply as _apply_v1,
    needs_migration as _needs_v1,
    stamp_version as _stamp_v1,
)
from services.migrations.migration_v2 import (
    URL_VAR_TO_PORT_VAR as _V2_URL_TO_PORT,
    apply as _apply_v2,
    needs_migration as _needs_v2,
    stamp_version as _stamp_v2,
)
from services.migrations.migration_v3 import (
    apply as _apply_v3,
    needs_migration as _needs_v3,
    stamp_version as _stamp_v3,
)


def _format_today() -> str:
    """Return today's date as ``YYYY-MM-DD`` for env-backfill markers.
    Factored to a tiny helper so it's trivial to monkey-patch in tests
    without freezing the system clock globally.
    """
    return date.today().isoformat()


def _run_privileged_hosts_setup() -> bool:
    """Run only the hosts-file mutation in a sudo child process.

    The shell wrapper intentionally refuses to run the whole startup flow as
    root because that creates root-owned repo artifacts. For `--setup-hosts`,
    ask for elevation only around the one operation that needs it.
    """
    from utils.system import is_elevated

    if is_elevated():
        return HostsManager().setup_hosts_entries()

    if os.name == "nt":
        print("  • ❌ Please run from an Administrator shell to modify hosts file")
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
        "raise SystemExit(0 if HostsManager().setup_hosts_entries() else 1)"
    )
    print("  • --setup-hosts needs to edit your hosts file; requesting sudo for that write only.")
    result = subprocess.run(
        ["sudo", sys.executable, "-c", helper],
        cwd=repo_root,
        env=env,
        check=False,
    )
    return result.returncode == 0

# Add the current directory to the path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from utils.banner import BannerDisplay
from utils.hosts_manager import HostsManager
from utils.key_generator import KeyGenerator
from utils.localhost_validator import LocalhostValidator
from core.config_parser import ConfigParser, DEFAULT_BASE_PORT
from core.docker_manager import DockerManager
from core.port_manager import PortManager
from services.source_validator import SourceValidator
from services.service_config import ServiceConfig
from services.dependency_manager import DependencyManager
from utils.source_override_manager import SourceOverrideManager


def _detect_env_image_drift(
    existing_env: dict, env_example_path,
) -> list[tuple[str, str, str]]:
    """Return [(key, user_value, example_value), ...] for every ``*_IMAGE``
    key whose value in the user's ``.env`` differs from ``.env.example``.

    Why this matters: CI tests `docker compose ... --env-file .env.example
    config -q`, so divergence in the user's `.env` is invisible to CI but
    breaks `docker build` at user-side launch. Example incident: PR #35
    migrated SPARK_IMAGE bitnami/spark:4.1.2 → apache/spark:4.1.2 (Bitnami
    went paywalled), but a user's pre-migration `.env` retained the stale
    Bitnami pin → `docker.io/bitnami/spark:4.1.2: not found` at the
    spark-history image-pull step. See PR #35 docs.

    Scope: ONLY ``*_IMAGE`` keys (image pins control what gets pulled /
    built and are the only class with this CI-blind failure mode). Other
    env divergence (ports, secrets, source toggles) is often intentional
    and would produce noisy false-positives.

    Empty user values are skipped — placeholder lines in `.env` and
    auto-managed keys correctly defer to compose `:-` fallbacks.

    Kept as a module-level free function so it can be unit-tested without
    spinning up the full Starter.
    """
    if not env_example_path.exists():
        return []
    example_pins: dict[str, str] = {}
    for raw_line in env_example_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if '_IMAGE=' not in line:
            continue
        key, _, val = line.partition('=')
        if key.endswith('_IMAGE') and val:
            example_pins[key] = val.split('#', 1)[0].strip()
    drift: list[tuple[str, str, str]] = []
    for key, example_val in example_pins.items():
        user_val = (existing_env.get(key, '') or '').strip()
        if not user_val:
            continue
        if user_val != example_val:
            drift.append((key, user_val, example_val))
    return drift


def _detect_port_collisions(rows) -> list[str]:
    """Return human-readable warning strings, one per colliding host port.

    A *collision* is two or more rows whose port value (the ":<num>"
    suffix or just the bare number) is equal AND nonempty. Disabled
    rows (port = "-" / "" / None) don't participate.

    `rows` is an iterable of ``(name, port_val)`` tuples — the same
    shape the pre-launch summary builder already accumulates as it
    iterates services. Kept as a module-level free function so it can
    be unit-tested without instantiating ``AtlasStarter``.

    The warnings are purely informational — launch still proceeds.
    Compose-up would otherwise fail with an opaque "address already in
    use" error from Docker, so this gives the user a chance to ack and
    continue or step back and pick another port.
    """
    by_port: dict[str, list[str]] = {}
    for name, port_val in rows:
        port = (port_val or "").lstrip(":").strip()
        if not port or port == "-":
            continue
        # Only digits count as a host port. Skip anything that doesn't
        # look like a numeric port (e.g. an external URL).
        if not port.isdigit():
            continue
        by_port.setdefault(port, []).append(name or "<unknown>")
    warnings: list[str] = []
    for port, names in by_port.items():
        if len(names) >= 2:
            warnings.append(
                f"⚠  port {port} used by {' + '.join(names)} — "
                f"compose-up may fail to bind."
            )
    return warnings


class AtlasStarter:
    """Main class for starting Atlas."""
    
    def __init__(self):
        # Set root directory first
        self.root_dir = Path(__file__).resolve().parent.parent
        
        # Initialize all managers with correct root_dir
        self.banner = BannerDisplay()
        self.hosts_manager = HostsManager()
        self.key_generator = KeyGenerator(str(self.root_dir))
        self.config_parser = ConfigParser(str(self.root_dir))
        self.localhost_validator = LocalhostValidator(self.config_parser)
        self.docker_manager = DockerManager(str(self.root_dir))
        self.port_manager = PortManager(str(self.root_dir))
        self.source_validator = SourceValidator(self.config_parser)
        self.service_config = ServiceConfig(self.config_parser)
        self.dependency_manager = DependencyManager(self.config_parser)
        self.source_override_manager = SourceOverrideManager(self.config_parser)
        self.active_track = None
        self.active_track_overrides = frozenset()


    def show_banner(self):
        """Display the startup banner (hero + wordmark/credits)."""
        self.banner.show_hero(no_splash=getattr(self, "no_splash", False))
        self.banner.show_banner()

    def ensure_dependencies_available(self) -> bool:
        """Ensure all required dependencies are available."""
        self.banner.show_section_header("Checking Dependencies", "🔍")
        
        # Check Docker availability
        if not self.docker_manager.check_docker_available():
            self.banner.show_status_message(
                "Docker is not available. Please install Docker and ensure it's running.", 
                "error"
            )
            return False
            
        # Show detected Docker compose command
        compose_cmd = self.docker_manager.get_compose_command_display()
        self.banner.show_status_message(f"Using Docker Compose command: {compose_cmd}", "info")
        compose_ok, compose_message = self.docker_manager.check_compose_version()
        self.banner.show_status_message(
            compose_message,
            "success" if compose_ok else "error",
        )
        if not compose_ok:
            return False

        # Check docker-compose.yml exists
        compose_file = self.root_dir / "docker-compose.yml"
        if not compose_file.exists():
            self.banner.show_status_message(
                f"Docker Compose file not found: {compose_file}", 
                "error"
            )
            return False
        self.banner.show_status_message(f"Docker Compose file found: {compose_file}", "success")
        
        # Python YAML parsing replaces yq dependency
        self.banner.show_status_message("Using native Python YAML parsing (replaces yq dependency)", "info")
        
        return True
    
    def apply_source_overrides(self, **kwargs) -> bool:
        """
        Apply SOURCE overrides from command-line arguments.

        Args:
            **kwargs: Command-line SOURCE override arguments

        Returns:
            bool: True if successful
        """
        overrides = self.source_override_manager.collect_overrides(**kwargs)
        if overrides:
            return self.source_override_manager.apply_overrides(overrides)
        return True

    def apply_profile_overrides(
        self,
        profile: str,
        *,
        explicit_prometheus: str | None = None,
        explicit_grafana: str | None = None,
    ) -> bool:
        """Apply deployment-profile env overrides to .env.

        Called from both the linear (--no-tui) path and the TUI wizard
        pipeline so prod-profile configuration applies regardless of how
        Atlas is started.

        Args:
            profile:             The resolved profile ("default" or "prod").
            explicit_prometheus: Non-None when --prometheus-source was passed
                                 explicitly (CLI flag or wizard selection); the
                                 default-ON override is then skipped. Note: a
                                 persisted PROMETHEUS_SOURCE=disabled in .env
                                 with NO explicit flag is still overridden to
                                 container under prod (the flag, not the .env
                                 value, gates the skip).
            explicit_grafana:    Same for grafana.

        Returns:
            bool: True on success.
        """
        if profile != "prod":
            # Clear the prod-managed bind-IP if it is exactly the value
            # prod writes, so switching back to the default profile does
            # not leave ports silently bound to 127.0.0.1. Any other
            # (user-set) value is left untouched.
            _PROD_BIND = "127.0.0.1:"
            env_vars = self.config_parser.parse_env_file()
            current_bind = env_vars.get("HOST_BIND_IP", "")
            if current_bind == _PROD_BIND:
                print("profile=default: cleared HOST_BIND_IP (ports return to all-interfaces)")
                return self.source_override_manager.update_env_file({"HOST_BIND_IP": ""})
            return True
        prod_overrides: Dict[str, str] = {"HOST_BIND_IP": "127.0.0.1:"}
        if explicit_prometheus is None:
            prod_overrides["PROMETHEUS_SOURCE"] = "container"
        if explicit_grafana is None:
            prod_overrides["GRAFANA_SOURCE"] = "container"
        prod_overrides["LOG_MAX_SIZE"] = "10m"
        prod_overrides["LOG_MAX_FILE"] = "3"
        return self.source_override_manager.update_env_file(prod_overrides)

    def apply_cloud_api_keys(self, keys: Dict[str, str]) -> bool:
        """
        Persist cloud LLM provider API keys (OPENAI_API_KEY,
        ANTHROPIC_API_KEY, OPENROUTER_API_KEY) into ``.env``.

        Reuses the in-place .env writer the source-override manager
        already employs, so format and comment lines are preserved.
        Empty values are written verbatim — used to clear a key.

        Args:
            keys: Mapping of env-var name to value (e.g.
                {'OPENAI_API_KEY': 'sk-...'}). Empty dict is a no-op.

        Returns:
            bool: True on success (or no-op).
        """
        if not keys:
            return True
        return self.source_override_manager.update_env_file(keys)

    def apply_user_model_selections(self, selections: Dict[str, str]) -> bool:
        """
        Persist user-selected model lists (OPENAI_USER_MODELS,
        ANTHROPIC_USER_MODELS, OPENROUTER_USER_MODELS, OLLAMA_USER_MODELS,
        OLLAMA_CUSTOM_MODELS) into ``.env``.

        Values are comma-separated model names. ``litellm-init``
        consumes them on the next ``docker compose up`` via
        ``model_resolver`` (YAML catalogs + env) to build the active
        model set.

        Args:
            selections: Mapping of env-var name to comma-separated
                model names. Empty dict is a no-op.

        Returns:
            bool: True on success (or no-op).
        """
        if not selections:
            return True
        # Dimension-safety guard (warn, don't block): the wizard may carry a
        # LITELLM_EMBEDDING_MODEL pick in `selections`. A non-768-dim embedding
        # model breaks the backend memory_facts vector(768) pgvector inserts at
        # runtime with no obvious cause, so surface it here at write time.
        embed = (selections.get("LITELLM_EMBEDDING_MODEL", "") or "").strip()
        if embed:
            from utils.model_resolver import embedding_dim_warning  # noqa: PLC0415
            warning = embedding_dim_warning(embed)
            if warning:
                self.banner.console.print(f"[bright_yellow]⚠ {warning}[/bright_yellow]")
        return self.source_override_manager.update_env_file(selections)

    def validate_source_configurations(self) -> bool:
        """Validate all SOURCE configurations and scale values against YAML.

        Calls the validator's repair pass first (auto-disables cloud
        providers with missing keys, etc.), then runs the read-only
        validation. Splitting the two lets pure-tooling callers
        (linters, CI dry-runs) run validation alone without mutating
        .env — see SourceValidator.enforce_runtime_invariants.
        """
        # Repair pass — mutates .env when necessary. A failed write
        # (disk full, permissions, etc.) is not silently recoverable:
        # halt before the read-only validate pass, which would
        # otherwise see stale .env.
        if not self.source_validator.enforce_runtime_invariants():
            self.source_validator.print_validation_results()
            return False
        # Read-only validation pass.
        sources_valid = self.source_validator.validate_all_sources()
        if not sources_valid:
            self.source_validator.print_validation_results()
            return False

        scales_valid = self.source_validator.validate_scale_values()
        if not scales_valid:
            self.banner.console.print("[bright_red]❌ Scale validation failed:[/bright_red]")
            for error in self.source_validator.get_validation_errors():
                self.banner.console.print(f"   {error}")
            return False

        return True
        
    def _persist_project_name(self, project_name: Optional[str]) -> bool:
        """Persist ``PROJECT_NAME`` to .env so start AND stop (and every
        ``docker compose -p``) target the same container family.

        Sticky by design: a later bare ``./stop.sh`` reads PROJECT_NAME back
        from .env and tears down exactly what ``./start.sh`` launched. Consumer
        stacks reusing Atlas as a submodule just set this once (via --project or
        by editing .env) to isolate their namespace from a base Atlas stack.
        """
        if not project_name:
            return True
        if self.source_override_manager.update_env_file({"PROJECT_NAME": project_name}):
            self.docker_manager.project_name_override = project_name
            self.banner.show_status_message(
                f"Project name set to '{project_name}' (persisted to .env "
                f"PROJECT_NAME; container family: {project_name}-*)",
                "info",
            )
            return True
        self.banner.show_status_message(
            f"Failed to persist PROJECT_NAME={project_name!r}; aborting so "
            "docker compose does not target a stale project.",
            "error",
        )
        return False

    def validate_persisted_project_name(self) -> bool:
        """Fail before mutating .env when its stored PROJECT_NAME is invalid."""
        try:
            self.config_parser.get_project_name()
        except ValueError as e:
            self.banner.show_status_message(
                f"Invalid PROJECT_NAME in {self.config_parser.env_file_path}: {e}",
                "error",
            )
            return False
        return True

    def setup_env_file(self, cold_start: bool, base_port: Optional[int] = None,
                       project_name: Optional[str] = None) -> bool:
        """
        Setup .env file from .env.example if needed.
        Supports custom .env file paths via ATLAS_ENV_FILE environment variable
        (and the deprecated GENAI_ENV_FILE alias).
        Replicates the .env setup logic from the original start.sh.

        Args:
            cold_start: Whether this is a cold start
            base_port: Optional custom base port
            project_name: Optional normalized PROJECT_NAME to persist (from
                --project / -p). Written AFTER the .env is created/preserved so
                a cold-start copy of .env.example doesn't reset it to the default.

        Returns:
            bool: True if successful
        """
        # Use config_parser paths which respect ATLAS_ENV_FILE
        env_file_path = self.config_parser.env_file_path
        env_example_path = self.config_parser.env_example_path

        # Show which env file we're using if custom
        if self.config_parser.is_using_custom_env_file():
            self.banner.show_status_message(
                f"Using custom env file: {env_file_path}",
                "info"
            )

        if project_name is None and env_file_path.exists() and not cold_start:
            if not self.validate_persisted_project_name():
                return False

        # Check if .env exists, if not or if cold start is requested, create from .env.example
        if not env_file_path.exists() or cold_start:
            if not env_example_path.exists():
                self.banner.show_status_message(
                    f".env.example file not found: {env_example_path}",
                    "error"
                )
                return False

            self.banner.show_section_header("Setting Up Environment", "📋")

            if cold_start:
                self.banner.show_status_message("Creating new .env file from .env.example (cold start)...", "info")
            else:
                self.banner.show_status_message("Creating new .env file from .env.example", "info")

            try:
                # Ensure parent directory exists (important for custom paths)
                env_file_path.parent.mkdir(parents=True, exist_ok=True)

                # Copy .env.example to target path (default or custom)
                import shutil
                shutil.copy2(env_example_path, env_file_path)
                self.banner.show_status_message(f"  • Copied {env_example_path}", "info")
                self.banner.show_status_message(f"  •     to {env_file_path}", "info")

                # Unset potentially lingering port environment variables if cold start and custom base port are used
                effective_base_port = base_port if base_port is not None else DEFAULT_BASE_PORT
                if cold_start and effective_base_port != DEFAULT_BASE_PORT:
                    self.unset_port_environment_variables()

                self.banner.show_status_message("Environment file setup completed", "success")
                return self._persist_project_name(project_name)

            except Exception as e:
                self.banner.show_status_message(f"Failed to create .env file: {e}", "error")
                return False

        return self._persist_project_name(project_name)  # .env already exists and not cold start

    def backfill_missing_env_vars(self) -> bool:
        """Append variables present in ``.env.example`` but missing from
        the user's ``.env`` (preserving every existing value).

        Catches the merge-from-upstream case: a worktree adds new
        services to ``.env.example`` (e.g. MinIO's ``MINIO_IMAGE``,
        ``MINIO_PORT``, bucket names) but the user's pre-existing
        ``.env`` doesn't carry those keys. ``docker compose config``
        then warns ``variable X not set, defaulting to blank`` and
        fails with ``service has neither an image nor a build context``
        when a critical key like ``${MINIO_IMAGE}`` is empty.

        Preserves the source file's organisation: missing vars are
        emitted under their original section heading (the
        ``# === FOO ===`` banner above them in ``.env.example``),
        with the immediate context comment block kept intact. The
        result reads as if the new entries had been there from the
        start — no flat "everything dumped at the bottom" lump.

        Idempotent — running again with no missing keys is a no-op.
        Only appends new keys; never rewrites existing values or
        reorders the file (so user-edited values and pre-existing
        comments stay put). Auto-managed keys with empty defaults in
        the example (passwords, access keys) are appended blank; the
        cold-start secret-generation step fills them later.
        """
        env_file_path = self.config_parser.env_file_path
        env_example_path = self.config_parser.env_example_path
        if not env_file_path.exists() or not env_example_path.exists():
            return True  # Nothing to backfill against.

        try:
            example_text = env_example_path.read_text(encoding="utf-8")
            env_text = env_file_path.read_text(encoding="utf-8")
        except OSError as e:
            self.banner.show_status_message(
                f"Could not read env files for backfill: {e}", "warning",
            )
            return True  # Non-fatal — surface compose's own error later.

        existing_keys: set[str] = set()
        blank_keys: set[str] = set()
        for line in env_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, _, raw_value = stripped.partition("=")
                key = key.strip()
                existing_keys.add(key)
                if not raw_value.split("#", 1)[0].strip():
                    blank_keys.add(key)

        # Keys the migration chain (services/migrations) is about to
        # write: backfill must NOT seed them from .env.example, or
        # run_port_migration() — which runs AFTER backfill — finds them
        # already present and skips the user's legacy values. Concretely:
        # seeding the sentinel stamps a legacy .env as already-migrated
        # (every migration silently skips); seeding a *_LOCALHOST_PORT
        # while the legacy *_LOCALHOST_URL is still in the file makes v2
        # discard the URL's custom port; seeding the COMFYUI model vars
        # while COMFYUI_MODEL_SET exists pre-empts v3's translation.
        migration_owned: set[str] = {"BOOTSTRAPPER_PORT_LAYOUT_VERSION"}
        for _url_var, _port_var in _V2_URL_TO_PORT.items():
            if _url_var in existing_keys:
                migration_owned.add(_port_var)
        if "COMFYUI_MODEL_SET" in existing_keys:
            migration_owned.update(
                {"COMFYUI_USER_MODELS", "COMFYUI_CUSTOM_MODELS_FILE"}
            )

        # Build a lookup of .env.example values so we can fill in BLANK
        # entries (a key exists in .env but with no value) using a
        # non-blank manifest default. This handles the case where the
        # user's .env was created when a secret's manifest `default` was
        # different from the current one — e.g., the supabase DB
        # password placeholder got reintroduced to the example after a
        # secret-emission policy change. Intentional autogen blanks
        # (LITELLM_MASTER_KEY etc.) have `default: ""` in the manifest
        # and therefore stay blank in .env.example, so this branch is a
        # no-op for them.
        example_values: dict[str, str] = {}
        for line in example_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, _, raw_value = stripped.partition("=")
                example_values[key.strip()] = raw_value.split("#", 1)[0].strip()
        blank_fills = {
            key: example_values[key]
            for key in blank_keys
            if example_values.get(key) and key not in migration_owned
        }
        if blank_fills:
            new_lines: list[str] = []
            for line in env_text.splitlines(keepends=True):
                stripped = line.strip()
                if "=" in stripped and not stripped.startswith("#"):
                    key, _, raw_value = stripped.partition("=")
                    key = key.strip()
                    if (
                        key in blank_fills
                        and not raw_value.split("#", 1)[0].strip()
                    ):
                        eol = "\r\n" if line.endswith("\r\n") else "\n"
                        new_lines.append(f"{key}={blank_fills[key]}{eol}")
                        continue
                new_lines.append(line)
            env_file_path.write_text("".join(new_lines), encoding="utf-8")
            env_text = env_file_path.read_text(encoding="utf-8")
            self.banner.show_status_message(
                f"Filled {len(blank_fills)} blank value(s) from .env.example: "
                f"{', '.join(sorted(blank_fills)[:4])}"
                f"{' …' if len(blank_fills) > 4 else ''}",
                "info",
            )

        groups = self._parse_env_example_sections(
            example_text, existing_keys | migration_owned,
        )
        if not groups:
            return True

        # Insert each group AT THE END of its matching section in the
        # user's .env. If the section doesn't exist in .env (older
        # layout, e.g. a brand-new service family), the section banner
        # plus its entries land in an "Auto-backfilled" trailer at the
        # bottom. This preserves the source-of-truth grouping the
        # docstring promised — historically the regex below was
        # `[=]{3,}` which never matched .env.example's `─` (U+2500)
        # bars, so EVERY key fell into "(unsectioned)" and got dumped
        # at the bottom. The fix is to (a) match both bar chars and
        # (b) actually splice in-place.
        new_env_text, total, in_place_sections, trailer_sections = (
            self._splice_backfill_in_place(env_text, groups)
        )

        try:
            env_file_path.write_text(new_env_text, encoding="utf-8")
        except OSError as e:
            self.banner.show_status_message(
                f"Failed to backfill {env_file_path}: {e}", "error",
            )
            return False

        msg_bits = []
        if in_place_sections:
            msg_bits.append(
                f"into {len(in_place_sections)} existing section"
                f"{'s' if len(in_place_sections) != 1 else ''} "
                f"({', '.join(in_place_sections[:3])}"
                f"{' …' if len(in_place_sections) > 3 else ''})"
            )
        if trailer_sections:
            msg_bits.append(
                f"with {len(trailer_sections)} new section"
                f"{'s' if len(trailer_sections) != 1 else ''} appended "
                f"({', '.join(trailer_sections[:3])}"
                f"{' …' if len(trailer_sections) > 3 else ''})"
            )
        self.banner.show_status_message(
            f"Backfilled {total} missing env var(s) from .env.example, "
            + ("; ".join(msg_bits) or "no placement available"),
            "info",
        )
        return True

    @staticmethod
    def _splice_backfill_in_place(
        env_text: str,
        groups: "list[tuple[str, list[tuple[list[str], str, str]]]]",
    ) -> "tuple[str, int, list[str], list[str]]":
        """Insert each backfill group at the END of its matching section
        in ``env_text``. Sections that don't exist in ``env_text`` get
        an auto-backfilled trailer at the bottom.

        Returns ``(new_env_text, total_keys_added, in_place_section_names,
        trailer_section_names)``.

        Section identity matches the banner-title text emitted by
        ``env_assembler`` (e.g. ``"data: Apache Spark (standalone
        cluster)  (services/spark/service.yml)"``). We tolerate both
        ``─`` and ``=`` bar chars for back-compat with hand-edited
        ``.env`` files.
        """
        bar_re = re.compile(r"^#\s*[=─]{3,}\s*$")
        lines = env_text.splitlines(keepends=True)
        n = len(lines)
        # Walk env_text and record [(section_name, start_idx, end_idx)]
        # — start_idx is the line AFTER the closing bar of the banner
        # block; end_idx is exclusive of the next banner's opening bar.
        sections: list[tuple[str, int, int]] = []
        current_name = "(preamble)"
        current_start = 0
        i = 0
        while i < n:
            line = lines[i]
            # Banner = bar / # TITLE / bar (3 lines).
            if (
                bar_re.match(line.rstrip("\r\n"))
                and i + 2 < n
                and lines[i + 1].lstrip().startswith("#")
                and bar_re.match(lines[i + 2].rstrip("\r\n"))
            ):
                title = lines[i + 1].lstrip("#").strip()
                # Close out the prior section at the line that holds
                # this banner's opening bar.
                sections.append((current_name, current_start, i))
                current_name = title or "(unnamed)"
                current_start = i + 3
                i += 3
                continue
            i += 1
        sections.append((current_name, current_start, n))

        # For each group, find an in-place insertion point or queue for
        # the trailer.
        section_lookup = {name: idx for idx, (name, _, _) in enumerate(sections)}
        # Map section index → list of additional lines to splice in
        # right BEFORE the section's end (so they land at the bottom of
        # the section, before any blank-line gap to the next banner).
        per_section_splice: dict[int, list[str]] = {}
        trailer_groups: list[tuple[str, list[tuple[list[str], str, str]]]] = []
        in_place_names: list[str] = []
        trailer_names: list[str] = []
        total = 0

        for section_name, entries in groups:
            if section_name in section_lookup:
                in_place_names.append(section_name)
                idx = section_lookup[section_name]
                bucket = per_section_splice.setdefault(idx, [])
                for context, key, value in entries:
                    for ctx_line in context:
                        bucket.append(ctx_line + "\n")
                    bucket.append(f"{key}={value}\n")
                    total += 1
            else:
                trailer_names.append(section_name)
                trailer_groups.append((section_name, entries))
                total += len(entries)

        # Reassemble env_text with in-place splices applied. Walk from
        # the end backwards so prior splices don't shift later indices.
        out_lines = list(lines)
        for idx in sorted(per_section_splice.keys(), reverse=True):
            name, start, end = sections[idx]
            # Trim trailing blank lines from the section body so the
            # spliced entries sit flush with the prior content; the
            # blank is reinserted between sections.
            insertion_point = end
            while (
                insertion_point > start
                and out_lines[insertion_point - 1].strip() == ""
            ):
                insertion_point -= 1
            splice = per_section_splice[idx]
            out_lines[insertion_point:insertion_point] = splice

        # Append the trailer for any groups whose section didn't exist.
        if trailer_groups:
            trailer: list[str] = []
            joined = "".join(out_lines)
            if joined and not joined.endswith("\n"):
                trailer.append("\n")
            trailer.extend([
                "\n",
                "# ────────────────────────────────────────────────────────\n",
                f"# Auto-backfilled from .env.example on {_format_today()}\n",
                "# Sections new in .env.example since this .env was written.\n",
                "# ────────────────────────────────────────────────────────\n",
            ])
            for section_name, entries in trailer_groups:
                trailer.append("\n")
                trailer.append(f"# === {section_name} ===\n")
                for context, key, value in entries:
                    for ctx_line in context:
                        trailer.append(ctx_line + "\n")
                    trailer.append(f"{key}={value}\n")
            out_lines.extend(trailer)

        return "".join(out_lines), total, in_place_names, trailer_names

    @staticmethod
    def _parse_env_example_sections(
        example_text: str, existing_keys: set[str],
    ) -> "list[tuple[str, list[tuple[list[str], str, str]]]]":
        """Walk ``example_text`` and group missing variables by section.

        Returns a list of ``(section_name, entries)`` where entries is
        a list of ``(context_comments, key, value)``. Section name
        comes from the most recent ``# ============`` banner block.
        Context comments are the contiguous comment lines immediately
        preceding the variable (an inline description like
        ``# Required when COMFYUI_SOURCE=localhost:``),
        capped to the previous variable or section banner so the
        backfill doesn't drag unrelated commentary along.
        """
        # Match the 3-line section banner pattern in .env.example.
        # env_assembler emits box-drawing `─` (U+2500) — the canonical
        # form after PR #X. Legacy `=` bars are also tolerated for
        # backwards-compat with hand-edited `.env.example` files.
        #
        # Example match:
        #   # ──────────────────────────────────────────────────
        #   # data: Apache Spark (standalone cluster)  (services/spark/service.yml)
        #   # ──────────────────────────────────────────────────
        bar_re = re.compile(r"^#\s*[=─]{3,}\s*$")
        lines = example_text.splitlines()
        current_section = "(unsectioned)"
        # Buffer of comment lines accumulated since the last variable
        # line or section banner — used as the immediate context for
        # the next variable we encounter.
        comment_buf: list[str] = []
        # Section name → list of (context, key, value).
        per_section: dict[str, list[tuple[list[str], str, str]]] = {}
        ordered_sections: list[str] = []
        seen_keys: set[str] = set()

        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            # Section banner detection: bar, title, bar (3 lines).
            if (
                bar_re.match(line)
                and i + 2 < n
                and lines[i + 1].startswith("#")
                and bar_re.match(lines[i + 2])
            ):
                title = lines[i + 1].lstrip("#").strip()
                if title:
                    current_section = title
                comment_buf = []
                i += 3
                continue
            stripped = line.strip()
            if not stripped:
                # Blank line resets the running comment buffer so we
                # don't paste unrelated text above a variable two
                # sections later.
                comment_buf = []
                i += 1
                continue
            if stripped.startswith("#"):
                comment_buf.append(line)
                i += 1
                continue
            if "=" not in stripped:
                comment_buf = []
                i += 1
                continue
            key, _, raw_value = stripped.partition("=")
            key = key.strip()
            value = raw_value
            if "#" in value:
                # Strip inline `# trailing comment` from the value but
                # keep the value itself verbatim.
                value = value.split("#", 1)[0]
            value = value.rstrip()
            if key and key not in existing_keys and key not in seen_keys:
                seen_keys.add(key)
                if current_section not in per_section:
                    per_section[current_section] = []
                    ordered_sections.append(current_section)
                per_section[current_section].append(
                    (list(comment_buf), key, value),
                )
            comment_buf = []
            i += 1
        return [(s, per_section[s]) for s in ordered_sections]

    def unset_port_environment_variables(self) -> None:
        """
        Unset potentially lingering port environment variables.
        Replicates the unset logic from the original start.sh.
        """
        # Every container-PORT slot the bootstrapper allocates. A stale
        # shell-exported value would shadow the freshly-computed value
        # on cold-start with a custom --base-port, so unset before we
        # re-allocate. Only *_LOCALHOST_PORT vars (host-side override
        # ports) stay out of the slot allocator and out of this list;
        # every slot-allocated *_PORT must appear here — including the
        # cAdvisor / node / postgres / redis exporter ports, which DO
        # recompute from BASE_PORT (test_port_unset_covers_topology guards
        # this list against topology.port_defaults).
        port_variables = [
            'SUPABASE_DB_PORT',
            'REDIS_PORT',
            'KONG_HTTP_PORT',
            'KONG_HTTPS_PORT',
            'SUPABASE_META_PORT',
            'SUPABASE_STORAGE_PORT',
            'SUPABASE_AUTH_PORT',
            'SUPABASE_API_PORT',
            'SUPABASE_REALTIME_PORT',
            'SUPABASE_STUDIO_PORT',
            'GRAPH_DB_PORT',
            'GRAPH_DB_DASHBOARD_PORT',
            'LITELLM_PORT',
            'LOCAL_DEEP_RESEARCHER_PORT',
            'SEARXNG_PORT',
            'CRAWL4AI_PORT',
            'FLOWER_PORT',
            'OPEN_WEB_UI_PORT',
            'BACKEND_PORT',
            'N8N_PORT',
            'COMFYUI_PORT',
            'WEAVIATE_PORT',
            'WEAVIATE_GRPC_PORT',
            'DOC_PROCESSOR_PORT',
            'STT_PROVIDER_PORT',
            'TTS_PROVIDER_PORT',
            'SPEACHES_PORT',
            'CHATTERBOX_PORT',
            'OPENCLAW_GATEWAY_PORT',
            'OPENCLAW_BRIDGE_PORT',
            'HERMES_API_PORT',
            'HERMES_DASHBOARD_PORT',
            'MCP_SERVERS_PORT',
            'LANGFUSE_PORT',
            'TEI_RERANKER_PORT',
            'LIGHTRAG_API_PORT',
            'ICEBERG_REST_PORT',
            'MINIO_PORT',
            'MINIO_CONSOLE_PORT',
            'JUPYTERHUB_PORT',
            'JENKINS_PORT',
            'MLFLOW_PORT',
            # PR #29 / PR #35 additions: ray + spark + airflow + zeppelin
            # + prometheus + grafana. Without these the previous-run
            # exports silently shadow the freshly-computed slots.
            'RAY_DASHBOARD_PORT',
            'RAY_CLIENT_PORT',
            'RAY_GCS_PORT',
            'SPARK_MASTER_UI_PORT',
            'SPARK_HISTORY_PORT',
            'AIRFLOW_PORT',
            'ZEPPELIN_PORT',
            'PROMETHEUS_PORT',
            'GRAFANA_PORT',
            # Observability exporters: all four are slot-allocated (recompute
            # from BASE_PORT) and host-published, so a stale shell export
            # shadows the fresh slot on a --base-port cold start just like any
            # other port. They were missing here for several releases.
            'CADVISOR_PORT',
            'NODE_EXPORTER_PORT',
            'POSTGRES_EXPORTER_PORT',
            'REDIS_EXPORTER_PORT',
        ]
        
        self.banner.show_status_message("  • Unsetting potentially lingering port environment variables...", "info")
        for var in port_variables:
            if var in os.environ:
                del os.environ[var]
    
    def validate_supabase_keys(self, cold_start: bool = False) -> bool:
        """
        Ensure required Supabase JWT keys are present.

        Three outcomes:
          - All three keys present → no-op.
          - All three keys blank (fresh clone, or post-cold-reset of .env) →
            auto-generate. No --cold flag required.
          - Mixed state (some present, some blank) → refuse and direct the
            user to ./bootstrapper/generate_supabase_keys.sh, since the anon
            and service keys are HMAC-signed by SUPABASE_JWT_SECRET and the
            generator always rewrites all three. Auto-regenerating here would
            silently clobber whatever values the user hand-pasted.

        Args:
            cold_start: Phrases the status message only. Auto-generation
                triggers on the all-blank case regardless of this flag.

        Returns:
            bool: True if all keys are present or successfully generated.
        """
        env_vars = self.config_parser.parse_env_file()

        keys = {
            'SUPABASE_JWT_SECRET': env_vars.get('SUPABASE_JWT_SECRET', '').strip(),
            'SUPABASE_ANON_KEY': env_vars.get('SUPABASE_ANON_KEY', '').strip(),
            'SUPABASE_SERVICE_KEY': env_vars.get('SUPABASE_SERVICE_KEY', '').strip(),
        }
        missing_keys = [name for name, value in keys.items() if not value]

        if not missing_keys:
            return True

        # Mixed state: some keys set, others blank. Don't auto-regenerate —
        # the SupabaseKeyGenerator rewrites all three together, which would
        # destroy whatever the user pasted into the present ones.
        if len(missing_keys) < len(keys):
            present_keys = [name for name in keys if name not in missing_keys]
            self.banner.show_section_header("Inconsistent Supabase Keys", "⚠️")
            self.banner.show_status_message(
                "Some Supabase keys are set and others are blank — refusing to "
                "auto-regenerate to avoid clobbering the values you've already set.",
                "warning",
            )
            self.banner.show_status_message(
                f"  Present: {', '.join(present_keys)}", "warning",
            )
            self.banner.show_status_message(
                f"  Missing: {', '.join(missing_keys)}", "warning",
            )
            self.banner.show_status_message("  To resolve:", "info")
            self.banner.show_status_message(
                "    • Run ./bootstrapper/generate_supabase_keys.sh to regenerate "
                "ALL three (overwrites existing), or",
                "info",
            )
            self.banner.show_status_message(
                "    • Manually fill in the missing keys in .env so all three "
                "are populated.",
                "info",
            )
            return False

        self.banner.show_section_header("Generating Supabase Keys", "🔐")
        if cold_start:
            self.banner.show_status_message(
                "Cold start detected - generating fresh Supabase JWT keys...",
                "info",
            )
        else:
            self.banner.show_status_message(
                "No Supabase keys found in .env; generating fresh JWT keys...",
                "info",
            )

        from utils.supabase_keys import SupabaseKeyGenerator
        key_generator = SupabaseKeyGenerator(str(self.root_dir))

        if key_generator.generate_and_update_env():
            self.banner.show_status_message(
                "Supabase keys generated and applied successfully!", "success"
            )
            return True

        self.banner.show_status_message("Failed to generate Supabase keys", "error")
        return False
        
    def handle_port_configuration(self, base_port: Optional[int]) -> bool:
        """Handle port configuration and updates."""
        # No --base-port flag: preserve the BASE_PORT already configured in
        # .env (e.g. from an earlier --base-port 64000 run) instead of
        # silently rewriting every *_PORT back to the default layout. This
        # mirrors the TUI path's fallback in ui/textual/integration.py.
        if base_port is None:
            current = (self.config_parser.parse_env_file()
                       .get('BASE_PORT', '') or '').strip()
            try:
                base_port = int(current)
            except ValueError:
                base_port = DEFAULT_BASE_PORT

        # Validate base port
        if not self.port_manager.validate_base_port(base_port):
            offsets = self.port_manager.port_offsets()
            max_offset = max(offsets.values()) if offsets else 0
            self.banner.show_status_message(
                f"Invalid base port: {base_port}. Must be between 1024 and "
                f"{65535 - max_offset}",
                "error"
            )
            return False
            
        # Check for port conflicts
        conflicts = self.port_manager.get_port_conflicts(base_port)
        if conflicts:
            # Check if conflicts are from our own project's containers
            if self.docker_manager.are_project_containers_running():
                self.banner.show_status_message(
                    "Previous instance detected — stopping existing containers...",
                    "info"
                )
                stop_result = self.docker_manager.stop_services(
                    remove_volumes=False, remove_orphans=True
                )
                if stop_result != 0:
                    self.banner.show_status_message(
                        "Failed to stop previous instance", "error"
                    )
                    return False

                self.banner.show_status_message(
                    "Previous instance stopped successfully", "success"
                )

                # Re-check ports after cleanup
                conflicts = self.port_manager.get_port_conflicts(base_port)

            # If conflicts remain, show the original error
            if conflicts:
                self.banner.show_status_message("Port conflicts detected:", "warning")
                for port_var, port in conflicts.items():
                    self.banner.show_status_message(
                        f"  • {port_var}: Port {port} is already in use", "warning"
                    )

                # Suggest alternative base port
                suggested_port = self.port_manager.suggest_available_base_port()
                if suggested_port:
                    self.banner.show_status_message(
                        f"Suggested available base port: {suggested_port}",
                        "info"
                    )
                return False
            
        # Update ports in .env file
        if not self.port_manager.update_env_ports(base_port):
            return False

        return True

    def run_port_migration(self, no_port_migrate: bool) -> None:
        """Chained .env migrations: v0 → v1 (port-layout), v1 → v2 (URL→PORT),
        v2 → v3 (COMFYUI_MODEL_SET → COMFYUI_USER_MODELS schema).

        Idempotent. Each step is gated by its own ``_needs_*`` predicate
        so re-running is safe and stamping is independent. Reads
        ``BOOTSTRAPPER_PORT_LAYOUT_VERSION`` from the active env file
        (honors ``ATLAS_ENV_FILE``).

        v1: rewrites every port var whose current value matches the v0
        default to the topology-derived v1 default. User-customized
        values are left alone.

        v2: rewrites legacy ``<SVC>_LOCALHOST_URL`` lines into
        ``<SVC>_LOCALHOST_PORT`` lines, commenting the old URLs for
        audit. Drives both compose runtime and Kong routes off the new
        PORT schema.

        v3: translates the old ``COMFYUI_MODEL_SET`` enum to the new
        ``COMFYUI_USER_MODELS`` CSV + sidecar/cache vars introduced in
        the model-picker feature. Removes the old enum var and any
        preceding comment block.

        When ``no_port_migrate`` is True we skip all three migrations AND skip
        the sentinel stamps so the next run re-prompts — matches the
        user intent "skip this run, ask next time."

        Must be called AFTER setup_env_file + backfill so the file
        exists and is fully populated, and BEFORE any caller that
        relies on the v2 port values.
        """
        env_path = self.config_parser.env_file_path

        # v0 → v1: port-layout rewrite.
        if _needs_v1(env_path):
            if no_port_migrate:
                self.banner.console.print(
                    "[dim]Skipping port-layout v1 migration (--no-port-migrate); "
                    "will re-prompt next run.[/dim]"
                )
            else:
                from services.topology import get_topology as _get_topology
                services_root = self.root_dir / "services"
                env_vars = self.config_parser.parse_env_file()
                # ``.get(key, default)`` returns the empty string when the key is
                # present-but-blank — only missing keys hit the default. A blank
                # BASE_PORT (auto-managed quirk) would crash ``int("")``.
                _raw_base = (env_vars.get("BASE_PORT") or "").strip()
                try:
                    base_port = int(_raw_base) if _raw_base else DEFAULT_BASE_PORT
                except ValueError:
                    base_port = DEFAULT_BASE_PORT
                topology = _get_topology(services_root, base_port=base_port)
                result = _apply_v1(env_path, topology.port_defaults, base_port=base_port)
                if result.backup_path:
                    self.banner.console.print(
                        f"[green]• Backed up .env to {result.backup_path}[/green]"
                    )
                self.banner.console.print(
                    f"[green]• Port layout updated (v0 → v1)[/green]: "
                    f"rewrote {len(result.rewritten)} ports; "
                    f"preserved {len(result.preserved)} customizations."
                )
                if result.rewritten:
                    self.banner.console.print("[dim]  Changes:[/dim]")
                    for var, (old, new) in sorted(result.rewritten.items()):
                        self.banner.console.print(
                            f"[dim]    {var}: {old} → {new}[/dim]"
                        )
                _stamp_v1(env_path, 1)

        # v1 → v2: URL → PORT schema rewrite. Idempotent on re-run.
        # Runs after v1 so the sentinel transitions cleanly 0/none → 1 → 2
        # rather than skipping intermediate state on a v0 .env (the
        # combined behavior is what we want for users on older checkouts
        # who haven't run any bootstrapper since the topology refactor).
        if _needs_v2(env_path):
            if no_port_migrate:
                self.banner.console.print(
                    "[dim]Skipping LOCALHOST schema migration "
                    "(--no-port-migrate); will re-prompt next run.[/dim]"
                )
            else:
                self.banner.show_status_message(
                    "Migrating .env to LOCALHOST_PORT schema (v2) ...",
                    "info",
                )
                _apply_v2(env_path)
                _stamp_v2(env_path)
                self.banner.show_status_message(
                    "LOCALHOST schema migration complete (v2). "
                    "Old <SVC>_LOCALHOST_URL lines are commented out for "
                    "audit; new <SVC>_LOCALHOST_PORT lines drive both "
                    "compose runtime and Kong routes.",
                    "success",
                )

        # v2 → v3: COMFYUI_MODEL_SET → COMFYUI_USER_MODELS schema rewrite.
        # Runs after v2 so the sentinel transitions cleanly: … → 2 → 3.
        if _needs_v3(env_path):
            if no_port_migrate:
                self.banner.console.print(
                    "[dim]Skipping model-set schema migration "
                    "(--no-port-migrate); will re-prompt next run.[/dim]"
                )
            else:
                self.banner.show_status_message(
                    "Migrating .env to COMFYUI_USER_MODELS schema (v3) ...",
                    "info",
                )
                _apply_v3(env_path)
                _stamp_v3(env_path)
                self.banner.show_status_message(
                    "Model-set migration complete (v3). "
                    "COMFYUI_MODEL_SET translated to COMFYUI_USER_MODELS.",
                    "success",
                )

    def generate_service_configuration(self) -> bool:
        """Generate and update service configuration."""
        return self.service_config.generate_and_update_env()
    
    def generate_litellm_configuration(self) -> bool:
        """Write a STUB volumes/litellm/config.yaml so the bind mount has
        a file to attach to. The real model_list is rendered by
        ``litellm-init`` from the YAML catalogs + env on every ``docker compose up``.

        ``force=True`` here, but the writer is NOT unconditionally
        destructive — ``LiteLLMConfigGenerator.write_config`` checks
        for the litellm-init sentinel header + non-empty model_list
        and preserves that file even with force=True. This protects
        the previous run's real config across re-runs that haven't
        yet completed a docker compose up. Stub / corrupt / missing
        files DO get overwritten. See ``_is_litellm_init_managed``.
        """
        try:
            from utils.litellm_config_generator import LiteLLMConfigGenerator
            generator = LiteLLMConfigGenerator(self.config_parser)
            config_path = self.root_dir / "volumes/litellm/config.yaml"
            # Pre-flight: ensure the bind-mount target directory is
            # writable. Earlier docker compose runs (litellm-init runs
            # as root inside the container) can leave the host directory
            # root-owned, blocking subsequent container writes —
            # symptom is ``PermissionError: '/litellm-config/config.yaml.tmp'``.
            self._ensure_volume_dir_writable(config_path.parent)
            generator.write_config(config_path, force=True)
            return True
        except Exception as e:
            self.banner.show_status_message(f"Failed to generate LiteLLM configuration: {e}", "error")
            return False

    def generate_comfyui_manifest(self) -> bool:
        """Write ``volumes/comfyui/selected-models.yaml`` and
        ``volumes/comfyui/active-models.tsv`` so ``comfyui-init`` can
        download the active model set without querying the DB.

        Resolves the active set via ``comfyui_resolver.active_comfyui_models``
        (C2 — DB-free, pure env + catalog computation).  Both files are
        written atomically; the TSV uses the same column order as the former
        ``psql SELECT`` so ``download_models.sh``'s existing loop is unchanged.

        Skipped cleanly when ``COMFYUI_SOURCE == "disabled"``; that case
        returns True (not an error).

        Mirrors ``generate_litellm_configuration`` — see that method for the
        general pattern.
        """
        try:
            from utils.comfyui_manifest_generator import ComfyUIManifestGenerator
            env = self.config_parser.parse_env_file()
            generator = ComfyUIManifestGenerator(env)
            if not generator.is_enabled():
                return True  # disabled — nothing to write
            manifest_dir = self.root_dir / "volumes/comfyui"
            self._ensure_volume_dir_writable(manifest_dir)
            generator.write(manifest_dir)
            return True
        except Exception as e:
            self.banner.show_status_message(
                f"Failed to generate ComfyUI manifest: {e}", "error"
            )
            return False

    def _ensure_volume_dir_writable(self, path: "Path") -> None:
        """Make sure a bootstrapper-managed host directory is writable
        by both the current host user and any future container that
        bind-mounts it. Earlier container runs can leave the directory
        root-owned with 755 mode, blocking subsequent re-writes.

        Strategy: if the directory exists and is not writable, attempt
        a 777 chmod. If chmod also fails (very rare — usually root-owned
        with strict mode), wipe and recreate. Both branches log what
        they did so the user can see why their permissions changed.

        Never raises — falls back to letting the original write fail
        with its native error if neither chmod nor recreate works.
        """
        import shutil

        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            return
        if not path.is_dir():
            return  # caller's problem; not our job to second-guess
        if os.access(path, os.W_OK):
            return  # already writable, nothing to do

        # Try chmod 0o777 first (cheapest, least destructive).
        try:
            path.chmod(0o777)
            if os.access(path, os.W_OK):
                self.banner.show_status_message(
                    f"  • Relaxed permissions on {path} (was root-owned from a prior container run)",
                    "info",
                )
                return
        except OSError:
            pass

        # chmod failed — last-ditch: wipe and recreate as the current user.
        try:
            shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
            self.banner.show_status_message(
                f"  • Recreated {path} (prior run left it unwritable)",
                "info",
            )
        except OSError as exc:
            self.banner.show_status_message(
                f"  • Could not fix permissions on {path}: {exc} — "
                f"if a container write fails, run `sudo rm -rf {path}` "
                f"and re-run ./start.sh",
                "warning",
            )

    def generate_kong_configuration(self) -> bool:
        """Generate dynamic Kong configuration based on SOURCE values."""
        try:
            from utils.kong_config_generator import KongConfigGenerator
            generator = KongConfigGenerator(self.config_parser)
            generator.track_key = getattr(self, "active_track", None)
            generator.overridden_services = getattr(
                self, "active_track_overrides", frozenset()
            )
            # Pre-flight: same root-owned-from-prior-container guard as
            # the litellm bind-mount uses (kong-api-gateway also writes
            # nothing into volumes/api but the bootstrapper drops the
            # dynamic config there for the container to read).
            self._ensure_volume_dir_writable(self.root_dir / "volumes/api")

            kong_config = generator.generate_kong_config()

            errors = generator.validate_config(kong_config)
            if errors:
                self.banner.show_status_message("Kong configuration validation failed:", "error")
                for error in errors:
                    self.banner.console.print(f"  • {error}")
                return False

            config_path = self.root_dir / "volumes/api/kong-dynamic.yml"
            if not generator.write_config(kong_config, config_path):
                return False

            return True

        except Exception as e:
            self.banner.show_status_message(f"Failed to generate Kong configuration: {e}", "error")
            return False
        
    def check_service_dependencies(self) -> bool:
        """Check and enforce service dependencies. Silent on success."""
        dependencies_satisfied = self.dependency_manager.check_service_dependencies()

        if not dependencies_satisfied:
            violations = self.dependency_manager.get_dependency_violations()
            self.banner.show_status_message("Service dependency violations found:", "warning")
            for violation in violations:
                self.banner.console.print(f"   ⚠️  {violation['error_message']}")

            disabled_services = self.dependency_manager.auto_resolve_dependency_violations()
            if disabled_services:
                for service in disabled_services:
                    self.banner.show_status_message(f"Auto-disabled {service} due to missing dependencies", "warning")
                return True
            else:
                self.banner.show_status_message("Could not auto-resolve dependency violations", "error")
                return False

        return True
        
    def handle_hosts_configuration(self, setup_hosts: bool, skip_hosts: bool) -> bool:
        """Handle hosts file configuration. Silent unless setting up or errors."""
        if skip_hosts:
            return True

        if setup_hosts:
            return self.hosts_manager.setup_hosts_entries()

        # Default: silent check, no warnings for missing entries
        return True
            
    def perform_cold_start_cleanup(self) -> bool:
        """Perform cold start cleanup if requested."""
        self.banner.show_section_header("Cold Start Cleanup", "🧹")
        
        self.banner.show_status_message("Performing cold start cleanup...", "info")
        
        # Use the enhanced cold start cleanup
        success = self.docker_manager.perform_cold_start_cleanup()
        
        if not success:
            self.banner.show_status_message("Some issues occurred during cleanup", "warning")
        else:
            self.banner.show_status_message("Cold cleanup completed successfully", "success")
            
        # Add small delay as per original script
        import time
        time.sleep(2)
            
        return True  # Continue even if cleanup had issues
        
    def generate_encryption_keys(self, cold_start: bool = False) -> bool:
        """
        Generate missing encryption keys for services.

        Always generates missing keys; regenerates ALL keys on cold start
        (``cold_start=True``).

        Args:
            cold_start: If True, regenerate all keys. If False, only generate missing ones.

        Returns:
            bool: True if successful
        """
        force_regenerate = cold_start

        try:
            results = self.key_generator.generate_missing_keys(force_regenerate=force_regenerate)

            if all(results.values()):
                return True
            else:
                failed_keys = [key for key, success in results.items() if not success]
                self.banner.show_status_message(
                    f"Failed to generate encryption keys: {', '.join(failed_keys)}",
                    "error"
                )
                return False

        except Exception as e:
            self.banner.show_status_message(f"Error generating encryption keys: {e}", "error")
            return False
    
    def validate_localhost_services(self) -> bool:
        """Validate localhost services are accessible before starting."""
        # Check if any services are configured for localhost
        if not self.localhost_validator.has_localhost_services():
            return True  # No localhost services to validate
            
        self.banner.show_section_header("Validating Localhost Services", "🔍")
        
        try:
            results = self.localhost_validator.validate_all_localhost_services()
            
            if not results:
                return True  # No localhost services found
            
            # Display results
            all_valid = True
            # STT_PROVIDER_SOURCE / TTS_PROVIDER_SOURCE use the per_source
            # SERVICE_CHECKS shape (service_name lives inside each variant, not
            # at the top level), so index via the per_source-aware resolver —
            # raw SERVICE_CHECKS[source_var]['service_name'] KeyErrors for them.
            service_sources = self.localhost_validator.config_parser.parse_service_sources()
            for source_var, (is_valid, messages) in results.items():
                config = self.localhost_validator._resolve_source_config(
                    source_var, service_sources.get(source_var, "")
                ) or {}
                service_name = config.get('service_name', source_var)
                level = "info" if is_valid else "warning"

                self.banner.show_status_message(f"  • {service_name}:", level)
                for message in messages:
                    self.banner.show_status_message(f"    {message}", level)

                if not is_valid:
                    all_valid = False

            if all_valid:
                self.banner.show_status_message("All localhost services are accessible", "success")
            else:
                self.banner.show_status_message(
                    "Some localhost services are not accessible (warnings above)",
                    "warning"
                )
                self.banner.show_status_message(
                    "  • The stack will still start, but affected services may not work correctly",
                    "warning",
                )
                self.banner.show_status_message(
                    "  • Please ensure localhost services are running as indicated",
                    "warning",
                )
                
            return True  # Always continue, just show warnings
            
        except Exception as e:
            self.banner.show_status_message(f"Error validating localhost services: {e}", "error")
            return True  # Continue anyway
        
    def start_docker_services(self, cold_start: bool = False) -> bool:
        """Start Docker services with optional fresh build for cold start."""
        self.banner.show_section_header("Starting Services", "🚀")
        
        if cold_start:
            self.banner.show_status_message("Starting containers with fresh build (cold start)...", "info")
            
            # Build images without cache (matching original Bash script behavior)
            print("    - Building images without cache...")
            build_result = self.docker_manager.build_services(no_cache=True, pull=False)
            
            if build_result != 0:
                self.banner.show_status_message("Failed to build some services", "error")
                return False
                
            print("    - Starting containers...")
            # Start with force recreate for cold start 
            result = self.docker_manager.execute_compose_command(['up', '-d', '--force-recreate'])
            
        else:
            self.banner.show_status_message("Starting Atlas services...", "info")
            result = self.docker_manager.start_services(detached=True)
        
        if result != 0:
            self.banner.show_status_message("Failed to start some services", "error")
            return False
        if not self.verify_one_shot_init_containers():
            return False
        self.banner.show_status_message("All services started successfully", "success")
        return True

    def verify_one_shot_init_containers(self, on_line=None) -> bool:
        """Fail startup if enabled post-start one-shot init containers failed."""
        env_vars = self.config_parser.parse_env_file()
        services: list[str] = []
        if env_vars.get("N8N_INIT_SCALE", "0") != "0":
            services.append("n8n-init")
        if env_vars.get("OPEN_WEB_UI_INIT_SCALE", "0") != "0":
            services.append("open-webui-init")
        if env_vars.get("COMFYUI_INIT_SCALE", "0") != "0":
            services.append("comfyui-init")
        if not services:
            return True

        failures = self.docker_manager.failed_one_shot_services(
            services,
            timeout_seconds=900.0,
            poll_interval_seconds=5.0,
        )
        if not failures:
            return True

        for service, reason in failures:
            msg = f"{service} failed after compose up ({reason})"
            if on_line is None:
                self.banner.show_status_message(msg, "error")
            else:
                on_line(f"❌ {msg}", "error")
        return False
            
    def show_pre_launch_summary(self, *, track: str | None = None) -> bool:
        """
        Display the combined configuration summary table with access URLs
        and hosted endpoints, then prompt for confirmation.

        ``track`` — forwarded to ``build_pre_launch_summary_table`` so the
        ``Track: <display_name>`` banner line is emitted when a track is active.

        Returns:
            bool: True if user confirms, False to cancel.
        """
        table = self.build_pre_launch_summary_table(track=track)
        self.banner.console.print(table)
        self.banner.console.print()
        from rich.text import Text as _Text
        from ui.textual.palette import style_for_category as _style_for_category
        from services.topology import CATEGORY_LABELS, CATEGORY_ORDER
        _legend = _Text()
        _first = True
        for _slug in CATEGORY_ORDER:
            if not _first:
                _legend.append("   ")
            _first = False
            _legend.append("▰", style=_style_for_category(_slug))
            _legend.append(f" {CATEGORY_LABELS[_slug]}")
        self.banner.console.print(_legend)
        self.banner.console.print()

        # Confirmation prompt — legacy linear flow only. TUI mode runs the
        # launch confirmation as the wizard's last step; this branch is
        # reached only when --no-tui or non-TTY.
        if sys.stdin.isatty():
            response = self.banner.console.input(
                "  [color(245)]Launch the stack? (Y/n):[/color(245)] "
            ).strip().lower()
            return response in ('', 'y', 'yes')
        return True  # non-TTY: auto-confirm

    def build_pre_launch_summary_table(self, *, track: str | None = None):
        """
        Build the configuration summary as a Rich Table renderable —
        used by the --no-tui / non-TTY linear flow (`show_pre_launch_summary`).
        The Textual wizard renders its own info-box and never reaches this
        table.

        ``track`` — the active track key (e.g. ``"gen-ai-rag"``), or None
        when no track was selected. When set, a ``Track: <display_name>``
        line is prepended above the services table per spec §5.2 #7.
        """
        from rich.table import Table
        from rich.text import Text
        from rich.box import HEAVY_HEAD
        from ui.state_builder import all_services, all_cloud_apis, alias_for, cloud_api_status_text
        from services.topology import get_topology
        from ui.textual.palette import style_for_category

        _topology = get_topology()
        _category_by_name = {r.display_name: r.category for r in _topology.rows}

        env_vars = self.config_parser.parse_env_file()
        service_sources = self.config_parser.parse_service_sources()
        kong_port = env_vars.get('KONG_HTTP_PORT', '63000')

        # Check if hosts entries are configured (yields the set of hostnames
        # that are PRESENT in /etc/hosts).
        hosts_present = set()
        try:
            existing_missing = self.hosts_manager.check_missing_hosts()
            all_hosts = self.hosts_manager.get_atlas_hosts()
            hosts_present = set(all_hosts) - set(existing_missing)
        except Exception:
            pass

        table = Table(
            title="Stack Services Overview",
            title_style="bold bright_white",
            box=HEAVY_HEAD,
            border_style="color(240)",
            header_style="bold bright_white",
            show_lines=True,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("PORT", style="color(248)", justify="left", ratio=1, no_wrap=True)
        table.add_column("SERVICE", style="color(252)", justify="left", ratio=3, no_wrap=True)
        # Category marker — between SERVICE and SOURCE, mirroring the
        # TUI box layout so both surfaces speak the same visual language.
        table.add_column("", justify="left", width=2, no_wrap=True)
        table.add_column("SOURCE", justify="left", ratio=3, no_wrap=True)
        table.add_column("ALIAS", justify="left", ratio=4, no_wrap=True)
        table.add_column("STATUS", justify="left", ratio=2, no_wrap=True)

        # Service definitions come from state_builder.all_services() — single
        # source of truth shared with the TUI info-box (no more inline list
        # to drift out of sync).
        services = list(all_services())

        # Sort by port number ascending; services with no port go to the end.
        def _sort_key(svc):
            name, source_var, port_var, _scale_var = svc
            source = service_sources.get(source_var, env_vars.get(source_var, 'container'))
            if source == 'disabled' or not port_var:
                return (2, 99999)
            if 'localhost' in source:
                lp = self._get_localhost_port(name, env_vars)
                match = re.search(r':(\d+)', lp)
                return (1, int(match.group(1)) if match else 99999)
            try:
                return (0, int(env_vars.get(port_var, '99999')))
            except ValueError:
                return (2, 99999)

        services.sort(key=_sort_key)

        from ui.textual.palette import style_for_source_choice as _style_for_source
        # Collected `(name, port_val)` for post-loop collision detection.
        # Disabled / portless rows still flow through here as ("-",); the
        # detector filters them out.
        collision_rows: list[tuple[str, str]] = []
        for name, source_var, port_var, scale_var in services:
            source = service_sources.get(source_var, env_vars.get(source_var, 'container'))
            scale = env_vars.get(scale_var, '0') if scale_var else '1'
            status_text, status_style = self._get_service_status(source, scale)
            # Color the SOURCE cell with the same helper the TUI uses:
            # container → green, localhost / external / api → blue,
            # disabled → muted grey. Previously hardcoded grey, which
            # made localhost variants visually indistinguishable from
            # containerised ones.
            source_style = _style_for_source(source)

            # PORT column
            if source == 'disabled':
                port_val = "-"
            elif 'localhost' in source:
                port_val = self._get_localhost_port(name, env_vars)
            elif port_var:
                port_val = f":{env_vars.get(port_var, '?')}"
            else:
                port_val = "-"

            # ALIAS column — alias map from state_builder (single source).
            hostname = alias_for(name)
            if hostname and hostname in hosts_present and source != 'disabled':
                alias_text = Text(f"{hostname}:{kong_port}", style="color(75)")
            else:
                alias_text = Text("-", style="color(243)")

            category = _category_by_name.get(name, "")
            bar = Text("▰", style=style_for_category(category))
            table.add_row(
                port_val,
                name,
                bar,
                Text(source, style=source_style),
                alias_text,
                Text(status_text, style=status_style),
            )
            collision_rows.append((name, port_val))

        # Cloud APIs panel — renders below the services table. Cloud
        # providers don't run as containers (scale: 0) so they don't
        # belong as rows in the services grid; this keeps them visible
        # without misleading the user about what's getting started.
        from rich.console import Group
        from rich.panel import Panel
        cloud_lines = []
        for name, source_var, api_key_var in all_cloud_apis():
            source = (
                service_sources.get(source_var, env_vars.get(source_var, 'disabled'))
                or ''
            ).strip().lower()
            key_set = bool((env_vars.get(api_key_var, '') or '').strip())
            enabled = source == 'enabled'
            line = Text()
            line.append(f"  {name:<11}", style="bright_white")
            # Status string is shared with the Textual CloudApisRow via
            # state_builder.cloud_api_status_text — only the Rich style
            # is local to this renderer.
            if enabled and key_set:
                style = "bright_green"
            elif enabled and not key_set:
                style = "bright_yellow"
            else:
                style = "color(243)"
            line.append(cloud_api_status_text(enabled, key_set), style=style)
            cloud_lines.append(line)
        cloud_panel = Panel(
            Group(*cloud_lines) if cloud_lines else Text("(none)", style="color(243)"),
            title="[bold bright_white]Cloud APIs[/bold bright_white]  "
                  "[color(243)](LiteLLM-routed, no containers)[/color(243)]",
            border_style="color(240)",
            padding=(0, 1),
            expand=True,
        )

        # Track banner line (spec §5.2 #7): when a track was active,
        # prepend a "Track: <display_name>" line above the services table.
        track_line: list = []
        if track:
            _track_label = track
            try:
                from tracks import load_tracks as _lt_sum
                _reg_sum = _lt_sum()
                _t_sum = _reg_sum.by_key.get(track)
                if _t_sum:
                    _track_label = _t_sum.display_name
            except Exception:  # noqa: BLE001
                pass
            track_line = [Text.from_markup(
                f"[bold bright_white]Track:[/bold bright_white] "
                f"[color(75)]{_track_label}[/color(75)]"
            )]

        # Port-collision warnings — informational only (warn-don't-block).
        # When two rows resolve to the same host port (e.g. the user
        # picked ollama-localhost on Kong's port), surface that here so
        # the user can step back and adjust before Docker barfs with an
        # opaque "address already in use" error.
        warning_lines = _detect_port_collisions(collision_rows)
        if warning_lines:
            warning_texts = [
                Text.from_markup(f"[yellow]{msg}[/yellow]")
                for msg in warning_lines
            ]
            return Group(*track_line, table, cloud_panel, *warning_texts)
        return Group(*track_line, table, cloud_panel)

    @staticmethod
    def _get_localhost_port(service_name: str, env_vars: dict) -> str:
        """Extract the actual localhost port from the service's endpoint env var."""
        from services.topology import get_topology
        _topology = get_topology()
        var = None
        for r in _topology.rows:
            if r.display_name == service_name:
                var = r.localhost_endpoint_var
                break
        if var:
            endpoint = env_vars.get(var, '')
            match = re.search(r':(\d+)', endpoint)
            if match:
                return f":{match.group(1)}"
        return "-"

    @staticmethod
    def _get_service_status(source: str, scale: str) -> tuple:
        """Get a status label with ● indicator and style for a service."""
        if source == 'disabled':
            return "● off", "color(245)"
        if 'localhost' in source:
            return "● local", "bright_cyan"
        if 'external' in source:
            return "● external", "bright_yellow"
        if source == 'api':
            return "● API", "bright_yellow"
        if 'gpu' in source:
            return "● GPU", "bright_green"
        if scale == '0':
            return "● off", "color(245)"
        return "● on", "bright_green"

    def check_comfyui_models(self, on_line=None):
        """Check ComfyUI local models."""
        self.service_config.check_comfyui_local_models(on_line=on_line)
        
    def show_container_status_and_verify_ports(self, on_line=None):
        """
        Show container status and verify actual vs expected ports.
        Replicates the verification logic from original start.sh.

        When `on_line` is provided (TUI mode), the redundant `docker compose ps`
        text dump is dropped and per-service results route through `on_line`
        with a level keyword ("ok"/"warn"/"error"). When `on_line` is None
        (legacy mode), behavior is unchanged from the original implementation.
        """
        # Get expected ports from .env (used by both branches)
        env_vars = self.config_parser.parse_env_file()

        def include_port_check(
            source_var: str | None = None,
            scale_var: str | None = None,
        ) -> bool:
            if source_var:
                source = env_vars.get(source_var, "").strip()
                if source == "disabled" or "localhost" in source:
                    return False
            if scale_var and env_vars.get(scale_var, "1").strip() == "0":
                return False
            return True

        # Service definitions matching original Bash script
        services_to_check = [
            ("supabase-db", "5432", env_vars.get("SUPABASE_DB_PORT", ""), None, None),
            ("redis", "6379", env_vars.get("REDIS_PORT", ""), None, None),
            ("supabase-meta", "8080", env_vars.get("SUPABASE_META_PORT", ""), None, None),
            ("supabase-storage", "5000", env_vars.get("SUPABASE_STORAGE_PORT", ""), None, None),
            ("supabase-auth", "9999", env_vars.get("SUPABASE_AUTH_PORT", ""), None, None),
            ("supabase-api", "3000", env_vars.get("SUPABASE_API_PORT", ""), None, None),
            ("supabase-realtime", "4000", env_vars.get("SUPABASE_REALTIME_PORT", ""), None, None),
            ("supabase-studio", "3000", env_vars.get("SUPABASE_STUDIO_PORT", ""), None, None),
            ("neo4j-graph-db", "7687", env_vars.get("GRAPH_DB_PORT", ""), "NEO4J_GRAPH_DB_SOURCE", "NEO4J_SCALE"),
            ("weaviate", "8080", env_vars.get("WEAVIATE_PORT", ""), "WEAVIATE_SOURCE", "WEAVIATE_SCALE"),
            ("local-deep-researcher", "2024", env_vars.get("LOCAL_DEEP_RESEARCHER_PORT", ""), "LOCAL_DEEP_RESEARCHER_SOURCE", "LOCAL_DEEP_RESEARCHER_SCALE"),
            ("open-web-ui", "8080", env_vars.get("OPEN_WEB_UI_PORT", ""), "OPEN_WEB_UI_SOURCE", "OPEN_WEB_UI_SCALE"),
            ("backend", "8000", env_vars.get("BACKEND_PORT", ""), "BACKEND_SOURCE", "BACKEND_SCALE"),
            ("kong-api-gateway", "8000", env_vars.get("KONG_HTTP_PORT", ""), None, None),
            ("kong-api-gateway", "8443", env_vars.get("KONG_HTTPS_PORT", ""), None, None),
            ("n8n", "5678", env_vars.get("N8N_PORT", ""), "N8N_SOURCE", "N8N_SCALE"),
            ("searxng", "8080", env_vars.get("SEARXNG_PORT", ""), "SEARXNG_SOURCE", "SEARXNG_SCALE"),
            ("crawl4ai", "11235", env_vars.get("CRAWL4AI_PORT", ""), "CRAWL4AI_SOURCE", "CRAWL4AI_SCALE"),
            ("flower", "5555", env_vars.get("FLOWER_PORT", ""), "CELERY_SOURCE", "FLOWER_SCALE"),
            ("jupyterhub", "8888", env_vars.get("JUPYTERHUB_PORT", ""), "JUPYTERHUB_SOURCE", "JUPYTERHUB_SCALE"),
        ]
        services_to_check = [
            (service_name, internal_port, expected_port)
            for service_name, internal_port, expected_port, source_var, scale_var in services_to_check
            if include_port_check(source_var, scale_var)
        ]

        # LiteLLM is the always-on LLM front door — its host port is the
        # canonical LLM-stack port now.
        services_to_check.append(("litellm", "4000", env_vars.get("LITELLM_PORT", "")))

        # Add conditional services based on their scales
        ollama_scale = env_vars.get("OLLAMA_SCALE", "0")
        if ollama_scale != "0":
            # Ollama upstream is internal-only; no host port mapping to verify.
            pass

        comfyui_scale = env_vars.get("COMFYUI_SCALE", "0")
        if comfyui_scale != "0":
            services_to_check.append(("comfyui", "18188", env_vars.get("COMFYUI_PORT", "")))

        if on_line is None:
            # Legacy linear flow — preserve today's exact behavior including
            # the `docker compose ps` text dump.
            print()
            self.docker_manager.show_container_status()
            print()
            print("🔍 Checking if Docker assigned the expected ports...")

            for service_name, internal_port, expected_port in services_to_check:
                if not expected_port:
                    continue
                actual_port = self.docker_manager.get_service_port(service_name, internal_port)
                if not actual_port:
                    print(f"  • ❌ {service_name}: Could not determine port mapping")
                elif actual_port == expected_port:
                    print(f"  • ✅ {service_name}: Using expected port {expected_port}")
                else:
                    print(f"  • ⚠️  {service_name}: Expected port {expected_port} but got {actual_port}")
            return

        # TUI mode — route per-service lines through on_line, skip the ps dump.
        # The dots in the anchored box already convey "is the container up";
        # this verification is specifically about port-mapping correctness.
        mismatches = 0
        for service_name, internal_port, expected_port in services_to_check:
            if not expected_port:
                continue
            actual_port = self.docker_manager.get_service_port(service_name, internal_port)
            if not actual_port:
                on_line(f"❌ {service_name}: could not determine port mapping", "error")
                mismatches += 1
            elif actual_port == expected_port:
                on_line(f"✅ {service_name}: port {expected_port} ok", "ok")
            else:
                on_line(f"⚠️  {service_name}: expected :{expected_port}, got :{actual_port}", "warn")
                mismatches += 1
        return mismatches
                
    def show_container_logs(self):
        """
        Show container logs with follow option.
        Replicates the logs display from original start.sh.
        """
        try:
            self.docker_manager.show_container_logs(follow=True)
        except KeyboardInterrupt:
            print("\n🔄 Log viewing interrupted by user")
            print("   Use 'docker compose logs -f' to view logs again")
        


@click.command()
@click.option('--project', '-p', 'project_name', type=str, default=None,
              help='Docker Compose project name — the container-family namespace '
                   '(every container/volume/network is prefixed <name>-…). Persists '
                   'to .env as PROJECT_NAME, so a later ./stop.sh tears down exactly '
                   'this stack. Defaults to PROJECT_NAME in .env (or "atlas"). Set it '
                   'when running Atlas as a submodule so you do not collide with a '
                   'base Atlas stack.')
@click.option('--base-port', type=int, help=f'Base port for all services (default: {DEFAULT_BASE_PORT})')
@click.option('--cold', is_flag=True, help='Perform cold start with cleanup')
@click.option('--setup-hosts', is_flag=True, help='Setup hosts file entries (requires admin/sudo)')
@click.option('--skip-hosts', is_flag=True, help='Skip hosts file checks and setup')
@click.option('--track', type=str, default=None,
              help='Pre-select a wizard profile (track) — gen-ai-rag, '
                   'gen-ai-eng, gen-ai-creative, ml-eng, data-eng, all. '
                   'Skips the wizard track-picker. In-track services are '
                   'prompted as usual; out-of-track services are disabled. '
                   'Use --list-tracks to see members.')
@click.option('--list-tracks', is_flag=True,
              help='Print the available tracks and their service '
                   'membership, then exit.')
@click.option('--llm-provider-source',
              type=click.Choice(['ollama-container-cpu', 'ollama-container-gpu', 'ollama-localhost',
                                'none'], case_sensitive=False),
              help='Override LLM_PROVIDER_SOURCE (Ollama upstream for the LiteLLM gateway). '
                   'Use "none" for cloud-only operation.')
@click.option('--cloud-openai-source',
              type=click.Choice(['enabled', 'disabled'], case_sensitive=False),
              help='Enable/disable the OpenAI cloud provider in LiteLLM (requires OPENAI_API_KEY).')
@click.option('--cloud-anthropic-source',
              type=click.Choice(['enabled', 'disabled'], case_sensitive=False),
              help='Enable/disable the Anthropic cloud provider in LiteLLM (requires ANTHROPIC_API_KEY).')
@click.option('--cloud-openrouter-source',
              type=click.Choice(['enabled', 'disabled'], case_sensitive=False),
              help='Enable/disable the OpenRouter cloud provider in LiteLLM (requires OPENROUTER_API_KEY).')
@click.option('--openai-api-key', type=str, default=None,
              help='OpenAI API key (sk-...). Persists to .env as OPENAI_API_KEY and '
                   'implies --cloud-openai-source=enabled.')
@click.option('--anthropic-api-key', type=str, default=None,
              help='Anthropic API key (sk-ant-...). Persists to .env as ANTHROPIC_API_KEY '
                   'and implies --cloud-anthropic-source=enabled.')
@click.option('--openrouter-api-key', type=str, default=None,
              help='OpenRouter API key (sk-or-...). Persists to .env as OPENROUTER_API_KEY '
                   'and implies --cloud-openrouter-source=enabled.')
@click.option('--openai-models', type=str, default=None,
              help='Comma-separated OpenAI model names to activate (e.g. "gpt-5,gpt-5-mini,o3"). '
                   'Persists to .env as OPENAI_USER_MODELS; litellm-init activates these via model_resolver on the next docker compose up.')
@click.option('--anthropic-models', type=str, default=None,
              help='Comma-separated Anthropic model names to activate. Persists as ANTHROPIC_USER_MODELS.')
@click.option('--openrouter-models', type=str, default=None,
              help='Comma-separated OpenRouter model names to activate. Persists as OPENROUTER_USER_MODELS.')
@click.option('--ollama-models', type=str, default=None,
              help='Comma-separated Ollama model names to activate from the curated catalog. '
                   'Persists as OLLAMA_USER_MODELS.')
@click.option('--ollama-custom-models', type=str, default=None,
              help='Comma-separated extra Ollama model names to pull (not in catalog). '
                   'Persists as OLLAMA_CUSTOM_MODELS; ollama-pull fetches them at startup.')
@click.option('--comfyui-models',
              help='Comma-separated catalog model names to pull for ComfyUI '
                   '(e.g. "sd_xl_base_1.0,sdxl-vae,flux1-dev-Q4_K_S"). '
                   'Overrides wizard selection and existing COMFYUI_USER_MODELS '
                   'in .env. Pass "" to clear. Unknown names skip with warning '
                   '(comfyui-init logs unknown names at start).')
@click.option('--comfyui-custom-models-file',
              type=click.Path(exists=False, dir_okay=False),
              help='Path to a sidecar custom-models.yaml. Default: '
                   'services/comfyui/custom-models.yaml. Override to point at '
                   'a file outside the repo (e.g. /etc/atlas/my-models.yaml).')
@click.option('--comfyui-source',
              type=click.Choice(['container-cpu', 'container-gpu', 'localhost',
                                'disabled'], case_sensitive=False),
              help='Override COMFYUI_SOURCE')
@click.option('--weaviate-source',
              type=click.Choice(['container', 'localhost', 'disabled'], case_sensitive=False),
              help='Override WEAVIATE_SOURCE')
@click.option('--minio-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override MinIO source')
@click.option('--n8n-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override N8N_SOURCE')
@click.option('--searxng-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override SEARXNG_SOURCE')
@click.option('--crawl4ai-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override CRAWL4AI_SOURCE — token-protected browser-backed extraction API.')
@click.option('--celery-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override CELERY_SOURCE — backend async worker tier with Flower monitor.')
@click.option('--mcp-servers-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override MCP_SERVERS_SOURCE')
@click.option('--jupyterhub-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override JUPYTERHUB_SOURCE')
@click.option('--open-web-ui-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override OPEN_WEB_UI_SOURCE')
@click.option('--local-deep-researcher-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override LOCAL_DEEP_RESEARCHER_SOURCE')
@click.option('--stt-provider-source',
              type=click.Choice(['speaches-container-cpu', 'speaches-container-gpu',
                                'parakeet-container-gpu', 'parakeet-localhost',
                                'whisper-cpp-localhost', 'disabled'],
                                case_sensitive=False),
              help='Override STT_PROVIDER_SOURCE')
@click.option('--tts-provider-source',
              type=click.Choice(['speaches-container-cpu', 'speaches-container-gpu',
                                'chatterbox-container-gpu', 'chatterbox-localhost',
                                'disabled'], case_sensitive=False),
              help='Override TTS_PROVIDER_SOURCE')
@click.option('--doc-processor-source',
              type=click.Choice(['docling-container-gpu', 'docling-localhost',
                                'disabled'], case_sensitive=False),
              help='Override DOC_PROCESSOR_SOURCE')
@click.option('--openclaw-source',
              type=click.Choice(['container', 'localhost',
                                'disabled'], case_sensitive=False),
              help='Override OPENCLAW_SOURCE')
@click.option('--hermes-source',
              type=click.Choice(['container', 'localhost',
                                'disabled'], case_sensitive=False),
              help='Override HERMES_SOURCE')
@click.option('--lightrag-source',
              type=click.Choice(['container', 'localhost', 'disabled'],
                                case_sensitive=False),
              help='Override LIGHTRAG_SOURCE')
@click.option('--tei-reranker-source',
              type=click.Choice(['container-cpu', 'container-gpu',
                                 'localhost', 'disabled'],
                                case_sensitive=False),
              help='Override TEI_RERANKER_SOURCE')
@click.option('--neo4j-graph-db-source',
              type=click.Choice(['container', 'localhost',
                                'disabled'], case_sensitive=False),
              help='Override NEO4J_GRAPH_DB_SOURCE')
@click.option('--multi2vec-clip-source',
              type=click.Choice(['container-cpu', 'container-gpu',
                                'disabled'], case_sensitive=False),
              help='Override MULTI2VEC_CLIP_SOURCE')
@click.option('--ray-source',
              type=click.Choice(['ray-container-cpu', 'ray-container-gpu',
                                'disabled'], case_sensitive=False),
              help='Override RAY_SOURCE (Ray distributed-compute cluster).')
@click.option('--ray-worker-count', type=int, default=None,
              help='Override RAY_WORKER_COUNT — number of ray-worker replicas '
                   'when --ray-source is ray-container-cpu or ray-container-gpu. '
                   '0 = head-only single-node mode. Defaults to 2 in .env.example.')
@click.option('--prometheus-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override PROMETHEUS_SOURCE — observability scraping stack '
                   '(prometheus + node-exporter + cAdvisor + postgres/redis exporters).')
@click.option('--prometheus-retention-days', type=int, default=None,
              help='Override PROMETHEUS_RETENTION_DAYS — TSDB retention in days '
                   '(default 7).')
@click.option('--grafana-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override GRAFANA_SOURCE — observability dashboards + alerting UI.')
@click.option('--langfuse-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override LANGFUSE_SOURCE — LLM traces and prompt/eval observability.')
@click.option('--spark-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override SPARK_SOURCE — standalone Spark cluster (master + workers + history).')
@click.option('--spark-workers', type=int, default=None,
              help='Override SPARK_WORKER_COUNT — number of spark-worker replicas '
                   'when --spark-source is container. Range 1-8 (clamped). '
                   'Mirrors --ray-worker-count. Defaults to 2 in .env.example.')
@click.option('--zeppelin-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override ZEPPELIN_SOURCE — Spark-first notebook UI (requires Spark).')
@click.option('--jenkins-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override JENKINS_SOURCE — Maven Spark app builder with MinIO artifact publishing.')
@click.option('--mlflow-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override MLFLOW_SOURCE — experiment tracking with MinIO artifacts.')
@click.option('--airflow-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override AIRFLOW_SOURCE — code-defined DAG orchestrator (LocalExecutor + LLM operators).')
@click.option('--iceberg-rest-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override ICEBERG_REST_SOURCE — durable Iceberg REST catalog over MinIO.')
@click.option('--no-tui', is_flag=True,
              help='Disable the TUI (wizard + Textual log app). Falls back to the legacy '
                   'linear flow with passthrough docker output. Useful for log capture, '
                   'debugging, and terminals that don\'t support the alternate screen buffer.')
@click.option('--no-splash', is_flag=True, default=False,
              help='Disable the opening splash animation in the wizard.')
@click.option('--no-port-migrate', is_flag=True, default=False,
              help='Skip the chained .env migrations (port-layout v1, URL→PORT v2, '
                   'model-set v3) for this run. Version sentinels are NOT stamped, '
                   'so the migration re-prompts on the next run.')
@click.option('--profile',
              type=click.Choice(['default', 'prod'], case_sensitive=False),
              help='Deployment profile. "prod": bind all service ports to '
                   '127.0.0.1 (public edge fronts Kong), enable log rotation, '
                   'default observability ON, and hide dev-only (localhost) '
                   'sources. Does not bypass the wizard. (Resource limits are '
                   'always-on .env defaults, not profile-gated.)')
def main(project_name, base_port, track, list_tracks, cold, setup_hosts, skip_hosts, llm_provider_source,
         cloud_openai_source, cloud_anthropic_source, cloud_openrouter_source,
         openai_api_key, anthropic_api_key, openrouter_api_key,
         openai_models, anthropic_models, openrouter_models,
         ollama_models, ollama_custom_models,
         comfyui_models, comfyui_custom_models_file,
         comfyui_source, weaviate_source, minio_source, n8n_source, searxng_source,
         crawl4ai_source, celery_source, mcp_servers_source,
         jupyterhub_source, open_web_ui_source, local_deep_researcher_source,
         stt_provider_source, tts_provider_source,
         doc_processor_source, openclaw_source, hermes_source,
         lightrag_source, tei_reranker_source,
         neo4j_graph_db_source,
         multi2vec_clip_source,
         ray_source, ray_worker_count,
         prometheus_source, prometheus_retention_days, grafana_source,
         langfuse_source,
         spark_source, spark_workers,
         zeppelin_source,
         jenkins_source,
         mlflow_source,
         airflow_source,
         iceberg_rest_source,
         no_tui, no_splash, no_port_migrate, profile):
    """Start Atlas — the self-hosted engineering platform."""

    # ─── Project name (-p / --project) ───────────────────────────────
    # Validate + normalize fail-fast, before any work. Persisted to .env
    # inside setup_env_file so every compose -p and a later ./stop.sh agree.
    if project_name is not None:
        from core.config_parser import normalize_project_name
        try:
            project_name = normalize_project_name(project_name)
        except ValueError as exc:
            click.echo(f"start.sh: {exc}", err=True)
            raise SystemExit(2)

    # ─── Track override warnings ─────────────────────────────────────
    # Fires when --track is set AND any explicit --*-source flag picks
    # a service that's out-of-track. Runs BEFORE --list-tracks early
    # exit so the warning surfaces even when the user listed tracks.
    if track is not None:
        try:
            from tracks import load_tracks as _load_tracks_for_warn
            from tracks import is_in_track as _is_in_track_for_warn
            _reg_w = _load_tracks_for_warn()
        except Exception:  # noqa: BLE001
            _reg_w = None
        if _reg_w is not None:
            _track_w = _reg_w.by_key.get(track)
            if _track_w is not None and _track_w.services is not None:
                # Map of Click kwarg → value, restricted to the
                # source-style flags. Cloud provider toggles
                # (cloud_openai_source, ...) are intentionally absent —
                # cloud keys are always-on and never reach the track
                # skip predicate, so a --cloud-openai-source flag should
                # never emit a track warning.
                _flag_values = {
                    'llm_provider_source': llm_provider_source,
                    'comfyui_source': comfyui_source,
                    'weaviate_source': weaviate_source,
                    'minio_source': minio_source,
                    'n8n_source': n8n_source,
                    'searxng_source': searxng_source,
                    'crawl4ai_source': crawl4ai_source,
                    'celery_source': celery_source,
                    'mcp_servers_source': mcp_servers_source,
                    'jupyterhub_source': jupyterhub_source,
                    'open_web_ui_source': open_web_ui_source,
                    'local_deep_researcher_source': local_deep_researcher_source,
                    'stt_provider_source': stt_provider_source,
                    'tts_provider_source': tts_provider_source,
                    'doc_processor_source': doc_processor_source,
                    'openclaw_source': openclaw_source,
                    'hermes_source': hermes_source,
                    'lightrag_source': lightrag_source,
                    'tei_reranker_source': tei_reranker_source,
                    'neo4j_graph_db_source': neo4j_graph_db_source,
                    'multi2vec_clip_source': multi2vec_clip_source,
                    'ray_source': ray_source,
                    'prometheus_source': prometheus_source,
                    'grafana_source': grafana_source,
                    'langfuse_source': langfuse_source,
                    'spark_source': spark_source,
                    'zeppelin_source': zeppelin_source,
                    'jenkins_source': jenkins_source,
                    'mlflow_source': mlflow_source,
                    'airflow_source': airflow_source,
                    'iceberg_rest_source': iceberg_rest_source,
                }
                for cli_key, value in _flag_values.items():
                    if value is None or value == "disabled":
                        continue
                    svc_key = cli_key.removesuffix("_source").replace("_", "-")
                    if _is_in_track_for_warn(
                        _track_w, svc_key, always_on=_reg_w.always_on,
                    ):
                        continue
                    # Look up display name from topology rows for nicer
                    # warning text; fall back to svc_key if no match.
                    derived_var = svc_key.upper().replace("-", "_") + "_SOURCE"
                    display = svc_key
                    try:
                        from services.topology import get_topology as _gt
                        _topo = _gt()
                        for _r in _topo.rows:
                            if _r.source_var == derived_var:
                                display = _r.display_name
                                break
                    except Exception:  # noqa: BLE001
                        pass
                    print(
                        f"[warn] --{cli_key.replace('_', '-')} "
                        f"{value} overrides the {track} track, "
                        f"which excludes {display}. Enabling "
                        f"{display} anyway.",
                        file=sys.stderr,
                    )

    # --list-tracks is side-effect-free and runs before any other init
    # (no Supabase key gen, no env migration). Exits 0.
    if list_tracks:
        from tracks import load_tracks, format_track_list
        try:
            reg = load_tracks()
        except Exception as e:  # noqa: BLE001 — surface load errors to stderr
            print(f"Error loading tracks.yml: {e}", file=sys.stderr)
            sys.exit(2)
        print(format_track_list(reg))
        sys.exit(0)

    # Validate --track before doing anything else.
    if track is not None:
        from tracks import load_tracks
        try:
            _track_registry = load_tracks()
        except Exception as e:  # noqa: BLE001
            print(f"Error loading tracks.yml: {e}", file=sys.stderr)
            sys.exit(2)
        if track not in _track_registry.by_key:
            valid = ", ".join(t.key for t in _track_registry.tracks)
            print(
                f"Error: unknown track '{track}'. Available: {valid}.",
                file=sys.stderr,
            )
            sys.exit(2)

    starter = AtlasStarter()

    try:
        # Cloud LLM provider keys passed via CLI flags. Persisting to
        # .env happens later, alongside source overrides — gathered
        # here so the implied --cloud-*-source toggles are applied
        # together with the explicit ones.
        cloud_api_keys: Dict[str, str] = {}
        if openai_api_key is not None:
            cloud_api_keys['OPENAI_API_KEY'] = openai_api_key
            if cloud_openai_source is None:
                cloud_openai_source = 'enabled'
        if anthropic_api_key is not None:
            cloud_api_keys['ANTHROPIC_API_KEY'] = anthropic_api_key
            if cloud_anthropic_source is None:
                cloud_anthropic_source = 'enabled'
        if openrouter_api_key is not None:
            cloud_api_keys['OPENROUTER_API_KEY'] = openrouter_api_key
            if cloud_openrouter_source is None:
                cloud_openrouter_source = 'enabled'

        # User-selected model lists from CLI flags. litellm-init
        # consumes these on the next docker compose up via model_resolver
        # (YAML catalogs + env) to build the active model set.
        user_model_selections: Dict[str, str] = {}
        if openai_models is not None:
            user_model_selections['OPENAI_USER_MODELS'] = openai_models
        if anthropic_models is not None:
            user_model_selections['ANTHROPIC_USER_MODELS'] = anthropic_models
        if openrouter_models is not None:
            user_model_selections['OPENROUTER_USER_MODELS'] = openrouter_models
        if ollama_models is not None:
            user_model_selections['OLLAMA_USER_MODELS'] = ollama_models
        if ollama_custom_models is not None:
            user_model_selections['OLLAMA_CUSTOM_MODELS'] = ollama_custom_models
        if comfyui_models is not None:
            user_model_selections['COMFYUI_USER_MODELS'] = comfyui_models
        if comfyui_custom_models_file is not None:
            user_model_selections['COMFYUI_CUSTOM_MODELS_FILE'] = comfyui_custom_models_file

        # Warn on cloud --*-models flags passed WITHOUT enabling the
        # provider. model_resolver produces zero active entries for a disabled
        # provider, so the persisted CSV would be inert. Surface this to
        # the user instead of silently no-op'ing. The matching key flag
        # implies enabling above; a bare --openai-models is the case to
        # warn on. We check the .env-resolved source too — the user may
        # have already set CLOUD_OPENAI_SOURCE=enabled in .env without
        # passing --cloud-openai-source on this invocation.
        try:
            _existing_env = starter.config_parser.parse_env_file()
        except Exception:  # noqa: BLE001
            _existing_env = {}

        # ─── .env vs .env.example image-pin drift warning ────────────────
        # See _detect_env_image_drift() for the rationale. CI is blind to
        # this class because it tests against .env.example, so the warning
        # is the only signal a user with a stale .env gets.
        try:
            _drift = _detect_env_image_drift(
                _existing_env, starter.config_parser.env_example_path,
            )
        except Exception:  # noqa: BLE001
            # Pre-flight warning must never break the start path.
            _drift = []
        if _drift:
            print(
                "⚠️  .env image-pin drift vs .env.example "
                "(CI tests .env.example so this is CI-invisible — "
                "may break docker build):",
                file=sys.stderr,
            )
            for _key, _user_val, _example_val in _drift:
                print(
                    f"     {_key}: .env={_user_val!r} → "
                    f".env.example={_example_val!r}",
                    file=sys.stderr,
                )
            print(
                "     Update .env to match (sed -i '' "
                "'s|^<KEY>=.*|<KEY>=<value>|' .env) or accept "
                "the override if intentional.",
                file=sys.stderr,
            )

        for _models_flag, _source_kwarg, _source_var in (
            (openai_models,     cloud_openai_source,     'CLOUD_OPENAI_SOURCE'),
            (anthropic_models,  cloud_anthropic_source,  'CLOUD_ANTHROPIC_SOURCE'),
            (openrouter_models, cloud_openrouter_source, 'CLOUD_OPENROUTER_SOURCE'),
        ):
            if _models_flag is None:
                continue
            _effective = (_source_kwarg
                          or _existing_env.get(_source_var, 'disabled')
                          or '').strip().lower()
            if _effective != 'enabled':
                _provider = _source_var.removeprefix('CLOUD_').removesuffix('_SOURCE').lower()
                print(
                    f"⚠️  --{_provider}-models was set but {_source_var}={_effective} — "
                    f"model_resolver produces no active entries for a disabled provider, so the "
                    f"persisted list won't take effect. Pass --{_provider}-api-key, "
                    f"--cloud-{_provider}-source=enabled, or set {_source_var}=enabled "
                    f"in .env.",
                    file=sys.stderr,
                )

        # Warn if user passed --comfyui-models but COMFYUI_SOURCE isn't container.
        if comfyui_models is not None:
            _comfyui_source = (
                comfyui_source
                or _existing_env.get('COMFYUI_SOURCE', 'disabled')
                or ''
            ).strip().lower()
            if not _comfyui_source.startswith('container-'):
                print(
                    f"⚠️  --comfyui-models was set but COMFYUI_SOURCE={_comfyui_source} — "
                    f"comfyui-init won't run (COMFYUI_INIT_SCALE=0 for non-container sources), "
                    f"so the selection won't take effect. Pass --comfyui-source=container-cpu "
                    f"(or -gpu) first.",
                    file=sys.stderr,
                )

        # Step 1.6: Apply SOURCE overrides from CLI arguments
        source_args = {
            'llm_provider_source': llm_provider_source,
            'cloud_openai_source': cloud_openai_source,
            'cloud_anthropic_source': cloud_anthropic_source,
            'cloud_openrouter_source': cloud_openrouter_source,
            'comfyui_source': comfyui_source,
            'weaviate_source': weaviate_source,
            'minio_source': minio_source,
            'n8n_source': n8n_source,
            'searxng_source': searxng_source,
            'crawl4ai_source': crawl4ai_source,
            'celery_source': celery_source,
            'mcp_servers_source': mcp_servers_source,
            'jupyterhub_source': jupyterhub_source,
            'open_web_ui_source': open_web_ui_source,
            'local_deep_researcher_source': local_deep_researcher_source,
            'stt_provider_source': stt_provider_source,
            'tts_provider_source': tts_provider_source,
            'doc_processor_source': doc_processor_source,
            'openclaw_source': openclaw_source,
            'hermes_source': hermes_source,
            'lightrag_source': lightrag_source,
            'tei_reranker_source': tei_reranker_source,
            'neo4j_graph_db_source': neo4j_graph_db_source,
            'multi2vec_clip_source': multi2vec_clip_source,
            'ray_source': ray_source,
            'prometheus_source': prometheus_source,
            'grafana_source': grafana_source,
            'langfuse_source': langfuse_source,
            'spark_source': spark_source,
            'zeppelin_source': zeppelin_source,
            'jenkins_source': jenkins_source,
            'mlflow_source': mlflow_source,
            'airflow_source': airflow_source,
            'iceberg_rest_source': iceberg_rest_source,
        }
        # Ray non-SOURCE settings (worker count) get plumbed via
        # update_env_file the same way the cloud-API keys do. Clamp 0-64 to
        # match the wizard's SecondaryNumberInput contract (integration.py),
        # mirroring the --spark-workers guard below.
        if ray_worker_count is not None:
            if not 0 <= ray_worker_count <= 64:
                raise click.UsageError("--ray-worker-count must be in 0-64")
            user_model_selections['RAY_WORKER_COUNT'] = str(ray_worker_count)
        # Prometheus retention days — same pattern.
        if prometheus_retention_days is not None:
            user_model_selections['PROMETHEUS_RETENTION_DAYS'] = str(prometheus_retention_days)
        # Spark worker count — same pattern as Ray's worker count. Clamp 1-8
        # to match the wizard's SecondaryNumberInput contract.
        if spark_workers is not None:
            if not 1 <= spark_workers <= 8:
                raise click.UsageError("--spark-workers must be in 1-8")
            user_model_selections['SPARK_WORKER_COUNT'] = str(spark_workers)

        # Determine if wizard mode — only when NO flags are provided at all.
        # Both the model-list flags (--openai-models / --ollama-models / etc.)
        # and the cloud-key flags (--openai-api-key / etc.) count as "non-wizard
        # intent": presence of either means the user is configuring via CLI
        # and the wizard would silently overwrite their input.
        # NOTE: this must be computed BEFORE the track synthesis block so that
        # `--track <key>` alone (no --*-source flags) still routes to the wizard.
        # The synthesis block only writes "disabled" into source_args for
        # non-wizard paths; the wizard handles off-track disabling itself via
        # _selections_to_args (Task 9).
        no_source_flags = all(v is None for v in source_args.values())
        no_stack_flags = (base_port is None and not cold and not setup_hosts and not skip_hosts)
        no_model_flags = not user_model_selections
        no_key_flags = not cloud_api_keys
        wizard_requested = (
            no_source_flags and no_stack_flags
            and no_model_flags and no_key_flags
        )
        will_run_wizard = (
            wizard_requested
            and sys.stdin.isatty()
        )

        # ─── Track override-set + force-disable synthesis ────────────
        # Two outcomes:
        #   1. `overridden_services`: the set of off-track svc.keys that
        #      were explicitly enabled via a CLI flag. Threaded into the
        #      wizard step builder so their prompts re-appear.
        #   2. Mirror _selections_to_args (TUI wizard path): force-disable
        #      every off-track configurable service in source_args so
        #      --no-tui and run_launch_flow honor the track without going
        #      through the wizard. Overridden services keep their
        #      CLI-supplied value (flag wins).
        #      Guard: skip the force-disable writes in wizard mode —
        #      the wizard's _selections_to_args already handles them, and
        #      writing "disabled" here would incorrectly cause the wizard to
        #      be skipped (source_args would look non-empty).
        overridden_services: set = set()
        if track is not None:
            try:
                from tracks import load_tracks as _ld
                from tracks import synthesize_track_source_args as _synth
                _rg2 = _ld()
            except Exception:  # noqa: BLE001
                _rg2 = None
            if _rg2 is not None:
                overridden_services |= _synth(
                    source_args,
                    track_key=track,
                    registry=_rg2,
                    force_disable=not wizard_requested,
                )
        starter.active_track = track
        starter.active_track_overrides = frozenset(overridden_services)

        # Detect legacy `external` source values left in .env from versions
        # before PR #(observability bundle). These options have been removed
        # pending a stack-wide authenticated-remote design; users must
        # switch to `container` or `disabled` (or `none` for LLM_PROVIDER_SOURCE).
        # Reuses _existing_env parsed above — nothing writes .env in between.
        _legacy_env = _existing_env
        _LEGACY_EXTERNAL = {
            'COMFYUI_SOURCE':       'external',
            'LLM_PROVIDER_SOURCE':  'ollama-external',
            'RAY_SOURCE':           'ray-external',
        }
        _found = [(k, v) for k, v in _LEGACY_EXTERNAL.items()
                  if (_legacy_env.get(k, '') or '').strip() == v]
        if _found:
            print(
                "\n❌ Legacy `external` source values found in .env:\n"
                + "\n".join(f"     {k}={v}" for k, v in _found)
                + "\n\n   The `external` / `ollama-external` / `ray-external` source "
                  "variants were removed pending a stack-wide authenticated-remote "
                  "design.\n   See docs/CHANGELOG.md → [Unreleased] → Removed for "
                  "migration. Switch each to `container` (or `disabled` / `none`)\n"
                  "   and re-run.\n",
                file=sys.stderr,
            )
            sys.exit(2)

        # Step 0: Early hosts setup for CLI --setup-hosts. The wrapper
        # refuses root, so request elevation only for the hosts-file write.
        if setup_hosts:
            starter.banner.console.print("\n  [bright_yellow]⚠️  --setup-hosts requires admin privileges.[/bright_yellow]")
            if not _run_privileged_hosts_setup():
                starter.banner.console.print(
                    "  [bright_white]Hosts setup did not complete. Re-run[/bright_white] "
                    "[bright_cyan]./start.sh --setup-hosts[/bright_cyan] "
                    "[bright_white]from a terminal that can approve sudo, or add the entries manually.[/bright_white]"
                )
                sys.exit(1)
            setup_hosts = False

        # Check dependencies early — silently in wizard mode (wizard clears screen)
        if not will_run_wizard:
            if not starter.ensure_dependencies_available():
                sys.exit(1)
        else:
            if not starter.docker_manager.check_docker_available():
                print("❌ Docker is not available. Please install Docker and ensure it's running.")
                sys.exit(1)
            compose_ok, compose_message = starter.docker_manager.check_compose_version()
            if not compose_ok:
                print(f"❌ {compose_message}")
                sys.exit(1)

        if wizard_requested:
            # Setup .env first so wizard can read current defaults.
            if not starter.setup_env_file(cold_start=cold, base_port=base_port, project_name=project_name):
                sys.exit(1)
            # Backfill any keys added to .env.example since the user's
            # .env was last written — run BEFORE the wizard reads it,
            # otherwise new vars (MinIO image / ports / bucket names,
            # etc.) won't appear as defaults in the wizard's prompts
            # and ``docker compose config`` will fail later with
            # ``variable X not set``.
            if not starter.backfill_missing_env_vars():
                sys.exit(1)

            # The Textual wizard owns the entire interactive flow when the
            # terminal can host it. Non-TUI shells (--no-tui, non-TTY,
            # narrow terminals) skip the wizard and use the user's .env
            # defaults plus any CLI flags they passed.
            from ui.term_caps import is_tui_capable as _is_tui_capable
            if will_run_wizard and _is_tui_capable(no_tui_flag=no_tui):
                # Single-Textual-app flow: wizard + pipeline + docker
                # compose log streaming all run inside one App. start.py
                # exits when the user detaches.
                from ui.textual.integration import run_setup_flow
                rc = run_setup_flow(
                    starter.config_parser, starter.hosts_manager,
                    starter=starter,
                    no_port_migrate=no_port_migrate,
                    track=track,
                    overridden_services=frozenset(overridden_services),
                    no_splash=no_splash,
                    profile=profile,
                )
                sys.exit(rc)

            # No-TUI fallback (spec §6.2 / §8.6): we're in will_run_wizard mode
            # but is_tui_capable returned False (--no-tui flag or non-TTY /
            # narrow terminal). The Textual picker won't fire, so resolve the
            # track on stdin if it wasn't preset — defaulting to gen-ai-rag
            # (first entry in tracks.yml) on empty input or non-interactive
            # stdin (CI / pipe).
            from tracks import load_tracks as _lt
            from tracks import format_track_list as _ftl
            try:
                _reg = _lt()
            except Exception:  # noqa: BLE001
                _reg = None
            if track is None and _reg is not None:
                print(_ftl(_reg), file=sys.stderr)
                print(
                    "Pick a track (Enter for default 'gen-ai-rag'): ",
                    end="", file=sys.stderr, flush=True,
                )
                if sys.stdin.isatty():
                    selected = input().strip()
                else:
                    selected = ""
                    print("(non-interactive stdin — using default)",
                          file=sys.stderr)
                if not selected:
                    selected = _reg.tracks[0].key  # gen-ai-rag
                if selected not in _reg.by_key:
                    print(
                        f"Warning: unknown track '{selected}', "
                        f"using default 'gen-ai-rag'.",
                        file=sys.stderr,
                    )
                    selected = _reg.tracks[0].key
                track = selected
            # Force-disable synthesis for the RESOLVED track — whether it was
            # preset via --track or just prompted above. The early-synthesis
            # block (search "Track override-set") deliberately skips this when
            # will_run_wizard is True, on the assumption the Textual wizard's
            # _selections_to_args handles it — but the wizard is NOT running on
            # this no-TUI path, so `--no-tui --track <key>` would otherwise
            # leave every off-track service at its .env default (track contract
            # silently violated). Mirrors _selections_to_args; off-track
            # services the user explicitly flagged keep their value and are
            # recorded as overrides. No-op for the 'all' track (services is None).
            if _reg is not None and track is not None:
                try:
                    from tracks import synthesize_track_source_args as _synth
                    overridden_services |= _synth(
                        source_args,
                        track_key=track,
                        registry=_reg,
                        force_disable=True,
                    )
                except Exception:  # noqa: BLE001
                    pass

        # CLI-flag mode + TUI capable: skip the wizard but still use the
        # Textual launch screen, pre-loaded with the user's CLI args.
        # Falls through to the linear stdout flow only when --no-tui or
        # the terminal can't host the TUI. This block must run BEFORE
        # the banner / setup_env_file / apply_source_overrides pipeline
        # below so its output stays out of the terminal and ends up
        # inside the log pane.
        if not no_tui:
            from ui.term_caps import is_tui_capable as _is_tui_capable
            if _is_tui_capable(no_tui_flag=no_tui):
                # Make sure .env exists so the launch screen can build
                # the Stack overview.
                if not starter.setup_env_file(cold_start=cold, base_port=base_port, project_name=project_name):
                    sys.exit(1)
                # Backfill new .env.example keys before the launch
                # screen renders the Stack overview from .env.
                if not starter.backfill_missing_env_vars():
                    sys.exit(1)
                from ui.textual.integration import run_launch_flow
                stack_options = {
                    "base_port": base_port,
                    "cold": cold,
                    "setup_hosts": setup_hosts,
                    "skip_hosts": skip_hosts,
                    "launch_confirmed": True,
                    # Forward the resolved deployment profile into the
                    # launch pipeline so wizard_screen sets starter.profile
                    # and calls apply_profile_overrides before compose up.
                    "profile": profile or "default",
                    # Forward any CLI-supplied cloud API keys into the
                    # launch pipeline; the wizard pipeline writes them
                    # to .env via SourceOverrideManager.update_env_file.
                    "cloud_api_keys": cloud_api_keys,
                    # Forward CLI-supplied user model selections (and
                    # any other --x-y --z scalar env-write flags like
                    # COMFYUI_CUSTOM_MODELS_FILE, RAY_WORKER_COUNT,
                    # PROMETHEUS_RETENTION_DAYS, SPARK_WORKER_COUNT)
                    # through the same apply_user_model_selections
                    # pipeline as the wizard's multiselect output. The
                    # wizard splits its dict into cloud/ollama/comfyui
                    # buckets for purely-cosmetic step grouping; here
                    # we forward the entire dict in a fourth catch-all
                    # bucket so no flag is silently dropped. wizard_screen
                    # merges all four buckets into one update_env_file
                    # call so the bucket boundaries are irrelevant for
                    # persistence.
                    "cloud_user_models": {
                        k: v for k, v in user_model_selections.items()
                        if k.endswith("_USER_MODELS") and not k.startswith("OLLAMA_")
                    },
                    "ollama_user_models": {
                        k: v for k, v in user_model_selections.items()
                        if k.startswith("OLLAMA_")
                    },
                    "user_env_writes": {
                        k: v for k, v in user_model_selections.items()
                        if not k.endswith("_USER_MODELS") and not k.startswith("OLLAMA_")
                    },
                }
                rc = run_launch_flow(
                    starter.config_parser, starter.hosts_manager,
                    starter=starter,
                    source_args=source_args,
                    stack_options=stack_options,
                    no_port_migrate=no_port_migrate,
                    track=track,
                    overridden_services=frozenset(overridden_services),
                    no_splash=no_splash,
                    profile=profile,
                )
                sys.exit(rc)

        # Linear (--no-tui / non-TTY) flow from here on — the wizard and
        # CLI-flag TUI branches above both sys.exit() before this point.
        starter.no_splash = no_splash
        starter.profile = profile
        starter.show_banner()

        if not starter.setup_env_file(cold_start=cold, base_port=base_port, project_name=project_name):
            sys.exit(1)

        # Pull in any keys added to .env.example since the user's .env
        # was written (e.g. a worktree merge added a new service like
        # MinIO and its config block). Idempotent; preserves all
        # existing values and auto-generated secrets.
        if not starter.backfill_missing_env_vars():
            sys.exit(1)

        if not starter.apply_source_overrides(**source_args):
            sys.exit(1)

        # Apply prod-profile env overrides (and default-profile cleanup)
        # after source overrides so the observability default-ON only
        # fires when the user didn't pass an explicit
        # --prometheus-source / --grafana-source flag. The default
        # branch clears HOST_BIND_IP when it equals the prod-managed
        # "127.0.0.1:" value, preventing it from persisting across runs.
        if not starter.apply_profile_overrides(
            profile or "default",
            explicit_prometheus=prometheus_source,
            explicit_grafana=grafana_source,
        ):
            sys.exit(1)

        # Persist any CLI-supplied cloud API keys to .env. No-op when
        # the dict is empty.
        if not starter.apply_cloud_api_keys(cloud_api_keys):
            sys.exit(1)

        # Persist any CLI-supplied user model selections to .env.
        # litellm-init reads these on the next docker compose up via model_resolver.
        if not starter.apply_user_model_selections(user_model_selections):
            sys.exit(1)

        # v0 → v1 port-layout migration. Runs after .env is fully populated
        # (setup_env_file + backfill + overrides) but before port_manager
        # rewrites ports, so we act on the user's pre-existing values rather
        # than ones we've just computed. The TUI flows call this helper
        # themselves immediately after backfill (see run_setup_flow /
        # run_launch_flow); this branch covers the --no-tui linear path.
        starter.run_port_migration(no_port_migrate)

        # Step 1.7: Cold start cleanup if requested (before port check).
        # TUI-capable runs exit before reaching this point; the Textual
        # wizard handles cold cleanup inline. This branch only fires for
        # the --no-tui / non-TTY linear flow.
        if cold:
            starter.perform_cold_start_cleanup()
        
        # Step 2: Validate SOURCE configurations.
        # Profile-source compatibility check runs first so --profile prod
        # with a dev-only source (e.g. ollama-localhost) fails fast with a
        # clear message before the heavier structural validation pass.
        if profile == "prod":
            _svc_srcs = starter.config_parser.parse_service_sources()
            starter.source_validator.validation_errors = []
            if not starter.source_validator.validate_sources_for_profile(_svc_srcs, "prod"):
                starter.source_validator.print_validation_results()
                sys.exit(1)
        if not starter.validate_source_configurations():
            sys.exit(1)
        
        # Step 3: Handle port configuration. Clear stale shell-exported
        # *_PORT vars first so they can't shadow the freshly computed
        # assignments (parity with the TUI pipeline's "Clear stale port
        # environment" step, which runs unconditionally).
        starter.unset_port_environment_variables()
        if not starter.handle_port_configuration(base_port):
            sys.exit(1)
        
        # Secrets MUST be generated/rotated BEFORE any config-gen step that
        # bakes a secret into a derived value. generate_service_configuration
        # embeds SUPABASE_DB_PASSWORD / GRAPH_DB_PASSWORD / REDIS_PASSWORD into
        # LIGHTRAG_PG_URI / LIGHTRAG_NEO4J_PASSWORD / LIGHTRAG_REDIS_URI; the
        # Kong/LiteLLM configs embed their own keys. If those ran first and a
        # password then rotated (cold start, or a first-run placeholder→real
        # upgrade), the derived value would carry the STALE secret while the DB
        # volume is initdb'd with the NEW one → "password authentication failed
        # for user supabase_admin" from lightrag-init against a fresh volume.

        # Step 3.9a: Validate Supabase keys (auto-generate for cold start)
        if not starter.validate_supabase_keys(cold_start=cold):
            sys.exit(1)

        # Step 3.9b: Generate encryption keys — rotate/ensure ALL secrets before
        # any derived-config step below reads them.
        if not starter.generate_encryption_keys(cold_start=cold):
            sys.exit(1)

        # Step 4: Generate service configuration
        if not starter.generate_service_configuration():
            sys.exit(1)

        # Step 4.1: Check service dependencies
        if not starter.check_service_dependencies():
            sys.exit(1)

        # Step 4.5: Generate dynamic Kong configuration
        if not starter.generate_kong_configuration():
            sys.exit(1)

        # Step 4.55: Write LiteLLM stub config.yaml so the bind mount has
        # a file. The real model_list is rendered later by litellm-init
        # from the YAML catalogs + env — see services/litellm/init/scripts/init.py.
        if not starter.generate_litellm_configuration():
            sys.exit(1)

        # Step 4.56: Write ComfyUI manifest (selected-models.yaml + active-models.tsv)
        # so comfyui-init can download the active set without querying the DB.
        # Skipped when COMFYUI_SOURCE=disabled. Mirrors generate_litellm_configuration.
        if not starter.generate_comfyui_manifest():
            sys.exit(1)

        # Step 5: Handle hosts configuration
        if not starter.handle_hosts_configuration(setup_hosts, skip_hosts):
            sys.exit(1)

        # Step 6.1: Prod-launch gate — refuse to start if any managed secret
        # still equals its shipped placeholder. Runs AFTER generate_encryption_keys
        # so a normal first-run auto-rotation happens first and does NOT trip this
        # gate. Only fires when --profile prod is active.
        if getattr(starter, "profile", "default") == "prod":
            try:
                starter.key_generator.assert_no_placeholders_remaining()
            except RuntimeError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                sys.exit(1)

        # Step 7: Validate localhost services before starting
        if not starter.validate_localhost_services():
            sys.exit(1)

        # Defensive final backfill — see notes on the wizard pipeline's
        # matching step. Catches any case where an intermediate pipeline
        # step regenerated .env from a parsed snapshot rather than the
        # in-place regex replacement, dropping keys present in
        # .env.example but not (yet) tracked by service_config.
        if not starter.backfill_missing_env_vars():
            sys.exit(1)

        # Pre-launch summary + docker streaming. TUI-capable runs exit
        # before reaching this point (the Textual wizard owns its own
        # summary, confirmation, and live log streaming). This linear
        # flow runs only for --no-tui / non-TTY contexts: show the
        # Rich-Table summary, prompt for confirm, then stream docker
        # output via TTY passthrough.
        if not starter.show_pre_launch_summary(track=track):
            starter.banner.console.print("\n  [color(245)]Launch cancelled.[/color(245)]")
            sys.exit(0)
        if not starter.start_docker_services(cold_start=cold):
            sys.exit(1)
        starter.show_container_status_and_verify_ports()
        starter.check_comfyui_models()
        starter.show_container_logs()

    except click.ClickException:
        # Let click render its own usage/parameter errors (e.g. the
        # --spark-workers range check) with their conventional exit code
        # (2 for UsageError) instead of masking them as an "unexpected
        # error" with exit 1 via the catch-all below.
        raise
    except KeyboardInterrupt:
        print("\n❌ Startup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error during startup: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
