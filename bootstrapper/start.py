#!/usr/bin/env python3
"""
Atlas - Start Script

Python implementation of start.sh with full feature parity.
Cross-platform startup script for Atlas — the self-hosted engineering platform.
"""

import re
import sys
import os
import json
import shlex
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


def _write_private_text(path: Path, text: str) -> None:
    """Atomically replace *path* with an owner-readable text file."""
    atomic_write_text(path, text, mode=0o600)


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
    return result.returncode == 0

# Add the current directory to the path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from utils.atomic_write import atomic_write_text
from utils.banner import BannerDisplay
from utils.hosts_manager import HostsManager
from utils.key_generator import KeyGenerator
from core.linear_startup import LinearStartupOptions, run_linear_startup
from utils.localhost_validator import LocalhostValidator
from core.config_parser import ConfigParser, DEFAULT_BASE_PORT, DEFAULT_PROJECT_NAME
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
        # Managers recorded here were started by this AtlasStarter invocation.
        # Pre-existing native hosts are deliberately never rollback-owned.
        self._managed_hosts_started_this_run: list[tuple[str, object]] = []
        # True once a nonzero `up --wait` was reclassified as converged only
        # after re-polling still-`starting` rows within the grace window
        # (#677/#681) — surfaced in the --detach --json summary so automation
        # can see the health race happened.
        self._up_converged_after_grace: bool = False


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
        # Remember which SOURCE vars the operator set explicitly THIS run, so
        # the profile applier can honor operator-wins for any profile-managed
        # source (generalizes the historical prometheus/grafana-only guards).
        self._explicit_source_vars = set(overrides.keys())
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
        """Apply the active deployment profile's declarative bundle to .env (#755).

        Bundles live in ``bootstrapper/profiles.yml`` (platform-defined
        ``default``/``prod``; ``dev`` aliases ``default``) with consumer
        ``profile_overrides:`` merged on top. Field semantics (preserving the
        behaviors this method used to hard-code):

        - ``host_bind_ip``: non-empty → asserted on every start of this
          profile (the defining prod property). Empty/undeclared → cleared
          only when the current value equals another profile's non-empty
          bind (sentinel discipline; an operator's custom bind is kept).
        - ``sources``: asserted on every start of this profile, EXCEPT when
          that service's source was set by an explicit CLI flag this run
          (operator wins — tracked via ``_explicit_source_vars`` plus the
          legacy ``explicit_prometheus``/``explicit_grafana`` params).
          ``auto`` delegates to the durable ``<SVC>_SOURCE: auto`` resolver
          (#753). On a profile SWITCH (tracked via ``ATLAS_PROFILE_APPLIED``),
          sources the prior profile asserted are reset to their service
          default first, so transitions leave no residue; a same-profile
          restart never resets (a wizard/operator selection that happens to
          equal a bundle value is safe).
        - ``env``: applied only when the var is unset/empty; an operator-set
          value is kept with a one-line notice (the LOG_MAX_* discipline).

        Called from both the linear (--no-tui) path and the TUI wizard
        pipeline so profile configuration applies regardless of how Atlas is
        started.
        """
        from services.host_capabilities import probe_host_capabilities  # noqa: F401
        from services.manifests import load_manifests, option_in_profile
        from services.profiles import (
            ProfileConfigError,
            canonical_profile,
            load_profile_bundles,
            merge_consumer_profile_overrides,
        )

        active = canonical_profile(profile)
        try:
            bundles = load_profile_bundles()
            consumer_config = self.config_parser.load_consumer_config()
            bundles = merge_consumer_profile_overrides(
                bundles, getattr(consumer_config, "profile_overrides", None) or {}
            )
        except ProfileConfigError as exc:
            print(f"profile={active}: invalid profile configuration: {exc}")
            return False
        bundle = bundles.get(active)
        if bundle is None:
            print(f"profile={active}: unknown profile")
            return False

        manifests = load_manifests(self.root_dir / "services")
        by_name = {m.name: m for m in manifests if m.sources is not None}
        env_vars = self.config_parser.parse_env_file()
        prior_applied = (env_vars.get("ATLAS_PROFILE_APPLIED", "") or "").strip()
        switching = bool(prior_applied) and prior_applied != active

        explicit_vars = set(getattr(self, "_explicit_source_vars", set()) or set())
        for legacy_service, legacy_value in (
            ("prometheus", explicit_prometheus),
            ("grafana", explicit_grafana),
        ):
            if legacy_value is not None and legacy_service in by_name:
                explicit_vars.add(by_name[legacy_service].sources.var)

        overrides: Dict[str, str] = {}

        # ── host_bind_ip ─────────────────────────────────────────────
        other_binds = {
            b.host_bind_ip
            for name, b in bundles.items()
            if name != active and b.host_bind_ip
        }
        declared_bind = bundle.host_bind_ip
        current_bind = env_vars.get("HOST_BIND_IP", "")
        if declared_bind:
            overrides["HOST_BIND_IP"] = declared_bind
        elif current_bind and current_bind in other_binds:
            print(
                f"profile={active}: cleared HOST_BIND_IP (ports return to all-interfaces)"
            )
            overrides["HOST_BIND_IP"] = ""

        # ── sources: undo the prior profile's asserts on a SWITCH ────
        auto_vars: list[str] = []
        if switching:
            prior_bundle = bundles.get(prior_applied)
            if prior_bundle is not None:
                for svc, prior_id in prior_bundle.sources.items():
                    if svc not in by_name:
                        continue
                    var = by_name[svc].sources.var
                    if var in explicit_vars or svc in bundle.sources:
                        continue  # operator wins / new profile re-declares it
                    if prior_id == "auto":
                        # A prior bundle-`auto` resolution: reset only when the
                        # resolved value is not offered under the NEW profile
                        # (dev-only residue that would fail validation).
                        current_value = (env_vars.get(var, "") or "").strip()
                        if current_value and not option_in_profile(
                            manifests, svc, current_value, active
                        ):
                            overrides[var] = by_name[svc].sources.default
                            print(
                                f"profile={active}: reset {var} to "
                                f"'{by_name[svc].sources.default}' (prior profile's "
                                f"auto-resolved '{current_value}' is not offered here)"
                            )
                        continue
                    if (env_vars.get(var, "") or "").strip() == prior_id:
                        overrides[var] = by_name[svc].sources.default
                        print(
                            f"profile={active}: reset {var} to "
                            f"'{by_name[svc].sources.default}' (was asserted by "
                            f"profile '{prior_applied}')"
                        )

        # ── sources: assert this profile's selections ────────────────
        for svc, source_id in bundle.sources.items():
            manifest = by_name.get(svc)
            if manifest is None:
                print(
                    f"profile={active}: sources.{svc} names a service without a "
                    f"sources block; ignoring"
                )
                continue
            var = manifest.sources.var
            if var in explicit_vars:
                continue  # explicit CLI flag this run wins
            if source_id == "auto":
                current = (env_vars.get(var, "") or "").strip()
                if switching:
                    prior_bundle = bundles.get(prior_applied)
                    prior_id = (prior_bundle.sources.get(svc) if prior_bundle else None)
                    if prior_id and prior_id != "auto" and current == prior_id:
                        # Clear the prior profile's assert so `auto` re-resolves
                        # instead of durably keeping the residue.
                        self._merge_env_file_overrides({var: manifest.sources.default})
                auto_vars.append(var)
                continue
            option_ids = {opt.id for opt in manifest.sources.options}
            if source_id not in option_ids:
                print(
                    f"profile={active}: sources.{svc}='{source_id}' is not one of "
                    f"{sorted(option_ids)}"
                )
                return False
            if not option_in_profile(manifests, svc, source_id, active):
                print(
                    f"profile={active}: sources.{svc}='{source_id}' is not offered "
                    f"under this profile"
                )
                return False
            overrides[var] = source_id

        # ── env: defaults unless operator-set ────────────────────────
        for env_key, declared_value in bundle.env.items():
            current = env_vars.get(env_key)
            if not current:
                overrides[env_key] = declared_value
            elif current != declared_value:
                print(
                    f"profile={active}: keeping operator-set {env_key}={current!r} "
                    f"(profile default is {declared_value!r})"
                )

        # Write the marker only when this run actually changes something (or
        # completes a switch) — a no-op run must leave .env byte-identical.
        if overrides or auto_vars or switching:
            overrides["ATLAS_PROFILE_APPLIED"] = active
        if overrides and not self.source_override_manager.update_env_file(overrides):
            return False

        # `auto` selections route through the durable #753 resolver (which
        # reads the just-updated .env for its keep/resolve decision).
        if auto_vars:
            resolved = self._resolve_auto_source_overrides(
                {var: "auto" for var in auto_vars}
            )
            concrete = {k: v for k, v in resolved.items() if v != "auto"}
            if concrete:
                self._merge_env_file_overrides(concrete)
        return True

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

    def _env_user_overlay_path(self) -> Path:
        """Return the optional user-owned env overlay next to the active .env."""
        return self.config_parser.env_file_path.parent / ".env.user"

    def _external_env_user_overlay_path(self) -> Optional[Path]:
        """Return the optional external env overlay from ATLAS_ENV_USER_FILE."""
        raw_path = os.environ.get("ATLAS_ENV_USER_FILE", "").strip()
        if not raw_path:
            return None

        overlay_path = Path(raw_path).expanduser()
        if not overlay_path.is_absolute():
            invoker_cwd = os.environ.get("ATLAS_INVOKER_CWD", "").strip()
            base_dir = Path(invoker_cwd).expanduser() if invoker_cwd else Path.cwd()
            overlay_path = base_dir / overlay_path
        return overlay_path.resolve()

    def _parse_env_overlay_file(self, overlay_path: Path) -> Dict[str, str]:
        """Parse a user env overlay with the same line semantics as .env."""
        env_vars: Dict[str, str] = {}
        with open(overlay_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                value = value.strip()
                if value[:1] in ('"', "'"):
                    quote = value[0]
                    end = value.find(quote, 1)
                    if end != -1:
                        value = value[1:end]
                    else:
                        value = value.strip('"').strip("'")
                else:
                    for i, ch in enumerate(value):
                        if ch == "#" and (i == 0 or value[i - 1] in " \t"):
                            value = value[:i]
                            break
                    value = value.strip()
                env_vars[key.strip()] = value
        return env_vars

    def _apply_single_env_user_overlay(
        self,
        overlay_path: Path,
        *,
        label: str,
        warn_if_missing: bool,
    ) -> Dict[str, str]:
        if not overlay_path.exists():
            if warn_if_missing:
                self.banner.show_status_message(
                    f"{label} points to {overlay_path}, but that file does not exist; skipping",
                    "warning",
                )
            return {}
        if not overlay_path.is_file():
            self.banner.show_status_message(
                f"{label} points to {overlay_path}, but it is not a file; skipping",
                "warning",
            )
            return {}

        try:
            overrides = self._parse_env_overlay_file(overlay_path)
        except OSError as e:
            self.banner.show_status_message(
                f"{label} points to {overlay_path}, but it could not be read ({e}); skipping",
                "warning",
            )
            return {}

        if not overrides:
            self.banner.show_status_message(f"  • {overlay_path} has no env overrides", "info")
            return {}

        self._merge_env_file_overrides(overrides)
        self.banner.show_status_message(
            f"  • Applied {overlay_path} ({len(overrides)} override{'s' if len(overrides) != 1 else ''})",
            "info",
        )
        return overrides

    def _apply_env_user_overlay(self) -> Dict[str, str]:
        """Merge optional user-owned env overlays into the active .env file.

        Precedence is deterministic: the sibling .env.user is applied first,
        then ATLAS_ENV_USER_FILE, then any consumer manifest env values.
        """
        applied_overrides: Dict[str, str] = {}

        sibling_overrides = self._apply_single_env_user_overlay(
            self._env_user_overlay_path(),
            label=".env.user",
            warn_if_missing=False,
        )
        applied_overrides.update(sibling_overrides)

        external_overlay_path = self._external_env_user_overlay_path()
        if external_overlay_path is not None:
            external_overrides = self._apply_single_env_user_overlay(
                external_overlay_path,
                label="ATLAS_ENV_USER_FILE",
                warn_if_missing=True,
            )
            applied_overrides.update(external_overrides)

        consumer_config = self.config_parser.load_consumer_config()
        if consumer_config.env_overrides:
            resolved_overrides = self._resolve_auto_source_overrides(
                self._resolve_auto_base_port_override(
                    dict(consumer_config.env_overrides)
                )
            )
            self._merge_env_file_overrides(resolved_overrides)
            count = len(resolved_overrides)
            names = ", ".join(consumer.name for consumer in consumer_config.consumers)
            self.banner.show_status_message(
                f"  • Applied consumer manifest env ({count} override{'s' if count != 1 else ''})"
                f" for {names}",
                "info",
            )
            applied_overrides.update(resolved_overrides)

        return applied_overrides

    def materialize_consumer_env_for_preflight(self) -> Dict[str, str]:
        """Persist the consumer manifest's derived ``env_overrides`` into ``.env``
        before a standalone ``doctor`` / ``compose validate``.

        The full ``start`` flow materializes these (via ``_apply_env_user_overlay``),
        but the preflight subcommands validate the assembled compose *without*
        applying them — so a consumer overlay that interpolates ``${BACKEND_PLUGINS_DIR}``
        (etc.) fails on a fresh checkout that has never started (#451). This applies
        the same derived, deterministic values a real start would write, so preflight
        validates the same assembled config a launch produces.

        Quiet by construction (no banner) so ``doctor --format json`` stays
        machine-clean. No-op when there is no ``.env`` yet or no overrides.
        Returns the overrides applied (empty dict when none).
        """
        if not self.config_parser.env_file_path.exists():
            return {}
        try:
            consumer_config = self.config_parser.load_consumer_config()
        except Exception:  # noqa: BLE001 — best-effort: a malformed manifest
            # must surface through the doctor's own consumer-manifest check
            # (which reports it as a structured `fail`), not crash preflight
            # before any check has run.
            return {}
        if getattr(self, "profile", None) in (None, ""):
            # Standalone doctor/endpoints runs never pass --profile; resolve
            # against the consumer manifest's declared default so `auto`
            # cannot poison a prod deployment's .env with dev-only sources.
            self.profile = getattr(consumer_config, "profile", None) or "default"
        overrides = self._resolve_auto_source_overrides(
            self._resolve_auto_base_port_override(
                dict(consumer_config.env_overrides or {})
            ),
            quiet=True,
        )
        if overrides:
            self._merge_env_file_overrides(overrides)
        return overrides

    def _merge_env_file_overrides(self, overrides: Dict[str, str]) -> None:
        """Replace or append env keys without duplicate-key ambiguity."""
        env_file_path = self.config_parser.env_file_path
        content = env_file_path.read_text(encoding="utf-8")
        updated_content = content

        for var_name, var_value in overrides.items():
            pattern = rf"^{re.escape(var_name)}=.*$"
            replacement = f"{var_name}={var_value}"
            if re.search(pattern, updated_content, re.MULTILINE):
                updated_content = re.sub(
                    pattern,
                    lambda _m, r=replacement: r,
                    updated_content,
                    flags=re.MULTILINE,
                )
            else:
                separator = "" if updated_content.endswith("\n") else "\n"
                updated_content += f"{separator}{replacement}\n"

        tmp_path = Path(str(env_file_path) + ".tmp")
        try:
            original_mode = os.stat(env_file_path).st_mode
            with open(tmp_path, "w", encoding="utf-8") as f:
                os.chmod(tmp_path, original_mode)
                f.write(updated_content)
            os.replace(tmp_path, env_file_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _resolve_auto_base_port_override(self, overrides: Dict[str, str]) -> Dict[str, str]:
        """Resolve a manifest ``BASE_PORT: auto`` to a concrete, **durable** free
        block (returns a new dict; non-auto overrides are returned unchanged).

        Manifest ``auto`` means "give this consumer a stable free BASE_PORT
        block". It resolves to the first wholly-free ``BASE_PORT+0..N`` block —
        which skips the default 63000 AND any block whose ports are in use by
        another running stack, so several consumers started in turn each land on
        a **distinct** block. The resolved value is persisted to ``.env`` and
        **kept** on later starts (a previously-resolved non-default block is
        reused as-is, so a warm restart never moves and never mistakes its own
        running containers for another stack's). A cold start (regenerated
        ``.env`` → default) re-resolves, still skipping occupied blocks.

        Unlike the one-off ``--base-port auto`` CLI flag (which resolves fresh
        every time it is passed), the manifest form is committed and must be
        stable — that is the whole point of pinning identity in the manifest.
        """
        if str(overrides.get("BASE_PORT", "")).strip().lower() != "auto":
            return overrides
        current = (self.config_parser.parse_env_file().get("BASE_PORT", "") or "").strip()
        try:
            current_int: Optional[int] = int(current)
        except ValueError:
            current_int = None
        if (
            current_int is not None
            and current_int != DEFAULT_BASE_PORT
            and self.port_manager.validate_base_port(current_int)
        ):
            # Durable keep — but only when the block is actually OURS to keep.
            # Several consumers resolving `auto` at different times (while the
            # others are down) can each persist the SAME "first free" block;
            # the first to start binds it and every later stack must re-resolve
            # or refuse to boot (#727 composed-run finding). Distinguish:
            #   block free                     → keep (durable, incl. cold)
            #   occupied + our containers up   → keep (warm restart)
            #   occupied + no containers of ours → FOREIGN stack: re-resolve
            in_use = self.port_manager.check_port_range_availability(current_int)
            if not in_use or self._project_has_running_containers(overrides):
                resolved = current_int  # durable: keep this consumer's block
            else:
                self.banner.show_status_message(
                    f"BASE_PORT=auto: persisted block {current_int} is bound by "
                    f"another stack on this host — re-resolving to a free block "
                    f"(the prior block was not ours to keep).",
                    "warning",
                )
                resolved = self.port_manager.auto_base_port()
                if resolved is None:
                    self.banner.show_status_message(
                        "BASE_PORT=auto could not find a free port block; keeping "
                        f"{current_int} (expect bind conflicts).",
                        "warning",
                    )
                    resolved = current_int
        else:
            resolved = self.port_manager.auto_base_port()
            if resolved is None:
                self.banner.show_status_message(
                    "BASE_PORT=auto could not find a free port block; using the "
                    f"default {DEFAULT_BASE_PORT}. Set an explicit BASE_PORT.",
                    "warning",
                )
                resolved = DEFAULT_BASE_PORT
        merged = dict(overrides)
        merged["BASE_PORT"] = str(resolved)
        return merged

    def _project_has_running_containers(self, overrides: Dict[str, str]) -> bool:
        """True when this consumer's compose project has containers up — the
        warm-restart signal that its persisted BASE_PORT block is genuinely
        its own. Conservative on any docker error: returns True (keep the
        block; never surprise-move ports when ownership can't be verified)."""
        project = (
            (overrides.get("PROJECT_NAME") or "").strip()
            or (self.config_parser.parse_env_file().get("PROJECT_NAME", "") or "").strip()
            or DEFAULT_PROJECT_NAME
        )
        try:
            probe = subprocess.run(
                [
                    "docker", "ps", "-q",
                    "--filter", f"label=com.docker.compose.project={project}",
                ],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if probe.returncode != 0:
                return True  # docker unreachable → conservative keep
            return bool(probe.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            return True  # conservative keep

    def _resolve_auto_source_overrides(
        self, overrides: Dict[str, str], *, quiet: bool = False
    ) -> Dict[str, str]:
        """Resolve manifest ``<SVC>_SOURCE: auto`` sentinels to concrete,
        **durable**, host-correct option ids (#753) — the source-selection
        analog of ``_resolve_auto_base_port_override`` above, sharing its
        durability contract:

        1. **Durable keep.** A concrete, valid, **non-default** value already
           in ``.env`` (a prior ``auto`` resolution or an operator's explicit
           ``--<svc>-source`` override) is kept — provided the active profile
           offers it (a dev-only value under ``--profile prod`` re-resolves,
           or prod validation would fail every start). KNOWN LIMIT: an
           operator override *to the service default id* is indistinguishable
           from a cold regen and is re-resolved on the next start — to durably
           force the default, commit the concrete id instead of ``auto``.
        2. **Platform-adaptive resolve.** Otherwise pick the first
           ``sources.auto_prefer`` entry whose host capability holds
           (services/host_capabilities.py) and whose option is offered under
           the active deployment profile (``option_in_profile``). Entries are
           declarative, ordered, and end in an unconditional terminal
           fallback (lint-enforced), so resolution never dead-ends.
        3. **Persist & keep.** The concrete id is merged into ``.env``; the
           next start hits step 1. A cold regen (``.env`` back to defaults)
           re-resolves — still host-correct.

        Runs before source validation, so the validator only ever sees
        concrete ids. Vars set to ``auto`` that match no manifest sources
        block are left untouched — the validator then rejects the literal
        ``auto`` with its normal valid-options error, keeping the mistake
        loud rather than silently dropped.
        """
        auto_keys = [
            key
            for key, value in overrides.items()
            if key != "BASE_PORT" and str(value).strip().lower() == "auto"
        ]
        if not auto_keys:
            return overrides

        from services.host_capabilities import probe_host_capabilities
        from services.manifests import load_manifests, option_in_profile

        manifests = load_manifests(self.root_dir / "services")
        by_source_var = {m.sources.var: m for m in manifests if m.sources is not None}
        env = self.config_parser.parse_env_file()
        profile = getattr(self, "profile", "default") or "default"
        capabilities = probe_host_capabilities()

        merged = dict(overrides)
        for key in auto_keys:
            manifest = by_source_var.get(key)
            if manifest is None or manifest.sources is None:
                if not quiet:
                    self.banner.show_status_message(
                        f"{key}=auto has no manifest sources block to resolve against; "
                        "leaving as-is (source validation will reject it).",
                        "warning",
                    )
                continue
            sources = manifest.sources
            option_ids = {opt.id for opt in sources.options}

            current = (env.get(key, "") or "").strip()
            if (
                current
                and current.lower() != "auto"
                and current != sources.default
                and current in option_ids
            ):
                if option_in_profile(manifests, manifest.name, current, profile):
                    merged[key] = current  # durable: keep prior resolution/override
                    continue
                # The kept value is not offered under the ACTIVE profile (e.g.
                # a dev-only managed-localhost-mps under --profile prod): keeping
                # it would fail prod source validation on every start until a
                # hand-edit. Re-resolve instead — self-healing beats durability
                # when the durable value is invalid for this run.
                if not quiet:
                    self.banner.show_status_message(
                        f"  • [auto] {key}: prior value '{current}' is not offered "
                        f"under profile '{profile}' — re-resolving.",
                        "info",
                    )

            resolved: Optional[str] = None
            matched = ""
            for pref in sources.auto_prefer:
                if pref.id not in option_ids:
                    continue  # lint catches this; stay safe at runtime too
                if not option_in_profile(manifests, manifest.name, pref.id, profile):
                    continue
                if pref.requires_capability is not None and not capabilities.has(
                    pref.requires_capability
                ):
                    continue
                resolved = pref.id
                matched = pref.requires_capability or "terminal fallback"
                break

            if resolved is None:
                reason = (
                    "no auto_prefer entries declared"
                    if not sources.auto_prefer
                    else "no auto_prefer entry eligible on this host/profile"
                )
                if not quiet:
                    self.banner.show_status_message(
                        f"{key}=auto could not resolve ({reason}); using the service "
                        f"default '{sources.default}'.",
                        "warning",
                    )
                resolved = sources.default
                matched = "service default"

            merged[key] = resolved
            if not quiet:
                self.banner.show_status_message(
                    f"  • [auto] {key}=auto → {resolved} (matched capability: {matched})",
                    "info",
                )
        return merged

    def _remove_env_keys_by_prefix(self, prefix: str) -> None:
        """Drop every ``.env`` line whose KEY starts with ``prefix``. Used to
        clear stale storage export vars before re-emitting the current set, so
        a removed/renamed store leaves no dangling export."""
        env_file_path = self.config_parser.env_file_path
        if not env_file_path.exists():
            return
        content = env_file_path.read_text(encoding="utf-8")
        kept = [
            line
            for line in content.splitlines(keepends=True)
            if not line.lstrip().startswith(prefix)
        ]
        updated = "".join(kept)
        if updated == content:
            return
        tmp_path = Path(str(env_file_path) + ".tmp")
        try:
            original_mode = os.stat(env_file_path).st_mode
            with open(tmp_path, "w", encoding="utf-8") as f:
                os.chmod(tmp_path, original_mode)
                f.write(updated)
            os.replace(tmp_path, env_file_path)
        finally:
            tmp_path.unlink(missing_ok=True)

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

    def prepare_environment(
        self,
        cold_start: bool,
        base_port: Optional[int] = None,
        project_name: Optional[str] = None,
    ) -> bool:
        """Clean a cold project before replacing its credential-bearing env file."""
        if cold_start:
            # A fresh clone has no env file for Compose interpolation, and no
            # existing credentials to preserve. Materialize it first. Existing
            # installs must clean first so a failed `down --volumes` leaves the
            # credential file matching any surviving volumes.
            if not self.config_parser.env_file_exists():
                if not self.setup_env_file(
                    cold_start=True,
                    base_port=base_port,
                    project_name=project_name,
                ):
                    return False
                return self.perform_cold_start_cleanup(project_name=project_name)
            # Backfill is additive and preserves every existing credential. It
            # gives upgraded Compose fragments all required interpolation keys
            # without replacing the env file before volume deletion succeeds.
            if not self.backfill_missing_env_vars():
                return False
            if not self.perform_cold_start_cleanup(project_name=project_name):
                return False
        return self.setup_env_file(
            cold_start=cold_start,
            base_port=base_port,
            project_name=project_name,
        )

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

        existing_project_name: Optional[str] = None
        if cold_start and env_file_path.exists():
            try:
                existing_project_name = self.config_parser.get_project_name()
            except ValueError:
                existing_project_name = None

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

                # Materialize atomically with owner-only permissions. A direct
                # copy can leave a partial or briefly world-readable secret file.
                _write_private_text(
                    env_file_path,
                    env_example_path.read_text(encoding="utf-8"),
                )
                self.banner.show_status_message(f"  • Copied {env_example_path}", "info")
                self.banner.show_status_message(f"  •     to {env_file_path}", "info")

                overlay_overrides = self._apply_env_user_overlay()

                # Unset potentially lingering port environment variables if cold start and custom base port are used
                effective_base_port = base_port if base_port is not None else DEFAULT_BASE_PORT
                if cold_start and effective_base_port != DEFAULT_BASE_PORT:
                    self.unset_port_environment_variables()

                self.banner.show_status_message("Environment file setup completed", "success")
                project_name_to_persist = project_name
                if (
                    project_name_to_persist is None
                    and existing_project_name
                    and "PROJECT_NAME" not in overlay_overrides
                ):
                    project_name_to_persist = existing_project_name
                if not self._persist_project_name(project_name_to_persist):
                    return False
                return self.validate_persisted_project_name()

            except Exception as e:
                self.banner.show_status_message(f"Failed to create .env file: {e}", "error")
                return False

        os.chmod(env_file_path, 0o600)
        overlay_overrides = self._apply_env_user_overlay()
        if not self._persist_project_name(project_name):  # .env already exists and not cold start
            return False
        if project_name is None and overlay_overrides:
            return self.validate_persisted_project_name()
        return True

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
            'ASSET_WORKER_PORT',
            'ASSET_BAKER_PORT',
            'SEARXNG_PORT',
            'CRAWL4AI_PORT',
            'TIKA_PORT',
            'LLM_GRAPH_BUILDER_PORT',
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
            'TRINO_PORT',
            'REDPANDA_KAFKA_PORT',
            'REDPANDA_CONSOLE_PORT',
            'MINIO_PORT',
            'MINIO_CONSOLE_PORT',
            'JUPYTERHUB_PORT',
            'JENKINS_PORT',
            'MLFLOW_PORT',
            'LABEL_STUDIO_PORT',
            'VERBA_PORT',
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
            if current.lower() == "auto":
                # Defensive: a manifest BASE_PORT=auto is normally resolved to a
                # concrete value by _resolve_auto_base_port_override during the
                # env-overlay merge; if it ever reaches here unresolved, pick a
                # free block rather than int()-failing to the default 63000.
                base_port = self.port_manager.auto_base_port() or DEFAULT_BASE_PORT
            else:
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
        if not self.service_config.generate_and_update_env():
            return False
        # Finalize consumer object-storage (#404) AFTER endpoints are resolved
        # into .env by generate_and_update_env, and before compose up. Covers
        # both the linear flow and the TUI/launch pipeline (single call site).
        if not self._finalize_consumer_storage():
            return False
        # Finalize consumer LiteLLM model rows (#411): write the generated
        # consumer-models.yaml (merged by litellm-init) + the api-key overlay,
        # or remove them when no consumer declares litellm_models. Same call
        # site so both the linear and TUI/launch flows are covered.
        if not self._finalize_consumer_litellm_models():
            return False
        # Finalize consumer n8n workflow seeding (#412): write the normalized
        # workflow JSONs + plan + seed overlay, or remove stale artifacts when no
        # consumer declares n8n_workflows.
        if not self._finalize_consumer_n8n_workflows():
            return False
        # Finalize consumer RAG ingestion profiles (#413): write the compiled
        # profiles JSON + backend-mount overlay, or remove them when no consumer
        # declares rag_ingestion_profiles.
        if not self._finalize_consumer_rag_ingestion_profiles():
            return False
        # Finalize consumer LightRAG query profiles (#414): write the compiled
        # query-profile registry JSON + backend-mount overlay, or remove them
        # when no consumer declares lightrag_query_profiles.
        if not self._finalize_consumer_lightrag_query_profiles():
            return False
        return True

    def _finalize_consumer_storage(self) -> bool:
        """Provision manifest-declared object stores (#404): generate scoped
        credentials (blank-only), export stable endpoint/credential-reference
        fields, and write the Atlas-owned minio-init overlay so no consumer
        compose override is needed. Idempotent; a no-storage config removes any
        stale generated overlay.
        """
        from core.consumer_manifest import (
            MINIO_STORAGE_OVERLAY_PATH,
            compile_storage_exports,
            storage_credential_tokens,
        )

        try:
            config = self.config_parser.load_consumer_config()
        except Exception as exc:  # ConsumerManifestError etc. — surface + fail
            self.banner.show_status_message(
                f"Consumer storage manifest error: {exc}", "error"
            )
            return False

        overlay_path = self.root_dir / MINIO_STORAGE_OVERLAY_PATH
        # Always clear stale export vars first — a removed or renamed store must
        # not leave dangling ATLAS_STORE_* fields; the current set is re-emitted
        # below. Provisioning (buckets / MINIO_EXTRA_CONSUMERS) lives only in the
        # regenerated overlay, so there is nothing stale to strip from .env there.
        self._remove_env_keys_by_prefix("ATLAS_STORE_")

        if not config.storage:
            if overlay_path.exists():
                overlay_path.unlink()
            return True

        env = self.config_parser.parse_env_file()
        if str(env.get("MINIO_SOURCE", "container")).strip() == "disabled":
            # A storage declaration against disabled MinIO can't be provisioned;
            # skip loudly rather than emit an unusable (empty-endpoint) contract.
            if overlay_path.exists():
                overlay_path.unlink()
            self.banner.show_status_message(
                "  • MINIO_SOURCE=disabled — skipping consumer storage "
                f"provisioning for {len(config.storage)} declared store(s)",
                "warning",
            )
            return True

        # 1. Scoped credentials — generated once, persist across restarts.
        tokens = storage_credential_tokens(config.storage)
        self.key_generator.generate_and_update_extra_minio_consumer_keys(tokens)

        # 2. Stable export fields (resolved endpoints track BASE_PORT/host).
        exports = compile_storage_exports(
            config.storage,
            minio_endpoint=env.get("MINIO_ENDPOINT", ""),
            minio_public_endpoint=env.get("MINIO_PUBLIC_ENDPOINT", ""),
            minio_region=env.get("MINIO_REGION", "us-east-1"),
        )
        if exports:
            self._merge_env_file_overrides(exports)

        # 3. Atlas-owned minio-init overlay (routed via consumer overlays).
        overlay = config.storage_overlay
        if overlay is not None:
            overlay.path.parent.mkdir(parents=True, exist_ok=True)
            overlay.path.write_text(overlay.content, encoding="utf-8")

        self.banner.show_status_message(
            f"  • Provisioned {len(config.storage)} consumer object store(s)",
            "info",
        )
        return True

    def _finalize_consumer_litellm_models(self) -> bool:
        """Materialize consumer-owned LiteLLM model rows (#411): write the
        generated ``consumer-models.yaml`` (merged by litellm-init into the
        LiteLLM config before startup) and the companion api-key compose overlay
        (injects consumer api-key references into the litellm container).
        Idempotent; a no-models config removes any stale generated artifacts so a
        removed manifest drops only its own rows on a warm restart.
        """
        from core.consumer_manifest import (
            LITELLM_CONSUMER_MODELS_PATH,
            LITELLM_CONSUMER_OVERLAY_PATH,
        )

        try:
            config = self.config_parser.load_consumer_config()
        except Exception as exc:  # ConsumerManifestError etc. — surface + fail
            self.banner.show_status_message(
                f"Consumer LiteLLM manifest error: {exc}", "error"
            )
            return False

        models_path = self.root_dir / LITELLM_CONSUMER_MODELS_PATH
        overlay_path = self.root_dir / LITELLM_CONSUMER_OVERLAY_PATH

        # No consumer declares litellm_models → remove any stale generated
        # artifacts so nothing leaks into a later, model-less start.
        if not config.litellm_models:
            for stale in (models_path, overlay_path):
                if stale.exists():
                    stale.unlink()
            return True

        artifact = config.litellm_models_file
        if artifact is not None:
            artifact.path.parent.mkdir(parents=True, exist_ok=True)
            artifact.path.write_text(artifact.content, encoding="utf-8")

        # The overlay only exists when at least one model declares an api_key_var.
        # Remove a stale overlay when the current config no longer needs one.
        if config.litellm_overlay is not None:
            config.litellm_overlay.path.parent.mkdir(parents=True, exist_ok=True)
            config.litellm_overlay.path.write_text(
                config.litellm_overlay.content, encoding="utf-8"
            )
        elif overlay_path.exists():
            overlay_path.unlink()

        owners = sorted({model.consumer for model in config.litellm_models})
        self.banner.show_status_message(
            f"  • Merged {len(config.litellm_models)} consumer LiteLLM model(s) "
            f"from {', '.join(owners)}",
            "info",
        )
        return True

    def _finalize_consumer_n8n_workflows(self) -> bool:
        """Materialize consumer-owned n8n workflow seeding (#412): write the
        normalized workflow JSONs + plan.json into the gitignored seed dir and the
        seed compose overlay, or remove any stale generated artifacts when no
        consumer declares n8n_workflows. Idempotent; a removed manifest (or a
        removed single workflow) drops exactly its own artifacts on the next start.
        """
        from core.consumer_manifest import (
            N8N_CONSUMER_OVERLAY_PATH,
            N8N_CONSUMER_WORKFLOWS_DIR,
        )

        try:
            config = self.config_parser.load_consumer_config()
        except Exception as exc:  # ConsumerManifestError etc. — surface + fail
            self.banner.show_status_message(
                f"Consumer n8n workflow manifest error: {exc}", "error"
            )
            return False

        seed_dir = self.root_dir / N8N_CONSUMER_WORKFLOWS_DIR
        overlay_path = self.root_dir / N8N_CONSUMER_OVERLAY_PATH

        # No consumer declares n8n_workflows → remove any stale generated
        # artifacts so a warm restart doesn't re-seed removed workflows.
        if not config.n8n_workflows:
            if seed_dir.exists():
                for stale in seed_dir.glob("*.json"):
                    stale.unlink()
            if overlay_path.exists():
                overlay_path.unlink()
            return True

        seed_dir.mkdir(parents=True, exist_ok=True)
        # Drop stale *.json (workflows removed since the last start) before
        # writing the current set — otherwise a removed workflow's file lingers
        # and the seed would re-import it.
        current_files = {artifact.path.name for artifact in config.n8n_artifacts}
        for stale in seed_dir.glob("*.json"):
            if stale.name not in current_files:
                stale.unlink()
        for artifact in config.n8n_artifacts:
            artifact.path.parent.mkdir(parents=True, exist_ok=True)
            artifact.path.write_text(artifact.content, encoding="utf-8")

        if config.n8n_overlay is not None:
            config.n8n_overlay.path.parent.mkdir(parents=True, exist_ok=True)
            config.n8n_overlay.path.write_text(
                config.n8n_overlay.content, encoding="utf-8"
            )

        owners = sorted({wf.consumer for wf in config.n8n_workflows})
        self.banner.show_status_message(
            f"  • Seeding {len(config.n8n_workflows)} consumer n8n workflow(s) "
            f"from {', '.join(owners)}",
            "info",
        )
        return True

    def _finalize_consumer_rag_ingestion_profiles(self) -> bool:
        """Materialize consumer-owned RAG ingestion profiles (#413): write the
        compiled profiles JSON + the backend-mount compose overlay, or remove any
        stale generated artifacts when no consumer declares rag_ingestion_profiles.
        Idempotent; a removed manifest drops exactly its own artifacts next start.
        """
        from core.consumer_manifest import (
            RAG_INGESTION_OVERLAY_PATH,
            RAG_INGESTION_PROFILES_PATH,
        )

        try:
            config = self.config_parser.load_consumer_config()
        except Exception as exc:  # ConsumerManifestError etc. — surface + fail
            self.banner.show_status_message(
                f"Consumer RAG ingestion manifest error: {exc}", "error"
            )
            return False

        profiles_path = self.root_dir / RAG_INGESTION_PROFILES_PATH
        overlay_path = self.root_dir / RAG_INGESTION_OVERLAY_PATH

        if not config.rag_ingestion_profiles:
            # Remove stale artifacts so a warm restart doesn't mount removed
            # profiles into the backend.
            if profiles_path.exists():
                profiles_path.unlink()
            if overlay_path.exists():
                overlay_path.unlink()
            return True

        if config.rag_ingestion_file is not None:
            config.rag_ingestion_file.path.parent.mkdir(parents=True, exist_ok=True)
            config.rag_ingestion_file.path.write_text(
                config.rag_ingestion_file.content, encoding="utf-8"
            )
        if config.rag_ingestion_overlay is not None:
            config.rag_ingestion_overlay.path.parent.mkdir(parents=True, exist_ok=True)
            config.rag_ingestion_overlay.path.write_text(
                config.rag_ingestion_overlay.content, encoding="utf-8"
            )

        owners = sorted({p.consumer for p in config.rag_ingestion_profiles})
        self.banner.show_status_message(
            f"  • Registering {len(config.rag_ingestion_profiles)} consumer RAG "
            f"ingestion profile(s) from {', '.join(owners)}",
            "info",
        )
        return True

    def _finalize_consumer_lightrag_query_profiles(self) -> bool:
        """Materialize consumer-owned LightRAG query profiles (#414): write the
        compiled query-profile registry JSON + the backend-mount compose overlay,
        or remove any stale generated artifacts when no consumer declares
        lightrag_query_profiles. Idempotent; a removed manifest drops exactly its
        own artifacts next start, so a no-profile deployment stays compatible.
        """
        from core.consumer_manifest import (
            LIGHTRAG_QUERY_PROFILES_OVERLAY_PATH,
            LIGHTRAG_QUERY_PROFILES_PATH,
        )

        try:
            config = self.config_parser.load_consumer_config()
        except Exception as exc:  # ConsumerManifestError etc. — surface + fail
            self.banner.show_status_message(
                f"Consumer LightRAG query profile manifest error: {exc}", "error"
            )
            return False

        profiles_path = self.root_dir / LIGHTRAG_QUERY_PROFILES_PATH
        overlay_path = self.root_dir / LIGHTRAG_QUERY_PROFILES_OVERLAY_PATH

        if not config.lightrag_query_profiles:
            # Remove stale artifacts so a warm restart doesn't mount removed
            # profiles into the backend.
            if profiles_path.exists():
                profiles_path.unlink()
            if overlay_path.exists():
                overlay_path.unlink()
            return True

        if config.lightrag_query_profiles_file is not None:
            config.lightrag_query_profiles_file.path.parent.mkdir(
                parents=True, exist_ok=True
            )
            config.lightrag_query_profiles_file.path.write_text(
                config.lightrag_query_profiles_file.content, encoding="utf-8"
            )
        if config.lightrag_query_profiles_overlay is not None:
            config.lightrag_query_profiles_overlay.path.parent.mkdir(
                parents=True, exist_ok=True
            )
            config.lightrag_query_profiles_overlay.path.write_text(
                config.lightrag_query_profiles_overlay.content, encoding="utf-8"
            )

        owners = sorted({p.consumer for p in config.lightrag_query_profiles})
        self.banner.show_status_message(
            f"  • Registering {len(config.lightrag_query_profiles)} consumer LightRAG "
            f"query profile(s) from {', '.join(owners)}",
            "info",
        )
        return True

    def start_managed_host_processes(self) -> bool:
        """Start selected native hosts immediately before Compose startup.

        Configuration generation remains side-effect free, and a later managed
        host failure rolls back only processes created by this invocation.
        """
        try:
            if not self._finalize_managed_comfyui_mps():
                self.rollback_managed_host_processes()
                return False
            if not self._finalize_managed_vllm_metal():
                self.rollback_managed_host_processes()
                return False
            if not self._finalize_managed_blender_mcp():
                self.rollback_managed_host_processes()
                return False
            # #757: non-fatal — a host daemon the user hasn't started (or a
            # typo'd tag) must never abort the stack; it only warns.
            self._finalize_ollama_localhost_models()
        except BaseException:
            self.rollback_managed_host_processes()
            raise
        return True

    def rollback_managed_host_processes(self) -> bool:
        """Stop native hosts started by this invocation, in reverse order."""
        remaining: list[tuple[str, object]] = []
        all_stopped = True
        for label, manager in reversed(self._managed_hosts_started_this_run):
            try:
                stopped = manager.stop()
                still_running = manager.status().running
            except Exception as exc:  # noqa: BLE001 - preserve other cleanup
                self.banner.show_status_message(
                    f"Could not roll back managed {label} host: {exc}", "warning"
                )
                remaining.append((label, manager))
                all_stopped = False
                continue
            if stopped or not still_running:
                self.banner.show_status_message(
                    f"Rolled back managed {label} host started by this launch.",
                    "info",
                )
            else:
                self.banner.show_status_message(
                    f"Managed {label} host is still running after rollback.",
                    "warning",
                )
                remaining.append((label, manager))
                all_stopped = False
        self._managed_hosts_started_this_run = list(reversed(remaining))
        return all_stopped

    def commit_managed_host_processes(self) -> None:
        """Release rollback ownership after the stack has converged."""
        self._managed_hosts_started_this_run.clear()

    def _finalize_managed_blender_mcp(self) -> bool:
        """Bring up the Atlas-managed headless Blender + MCP bridge (#759).

        ``BLENDER_MCP_SOURCE=managed-localhost`` provisions the pinned add-on
        + launcher and runs `blender --background` with the main-thread queue
        shim, so composition automation needs no GUI and no manual Connect
        click. Fatal on failure (the user explicitly chose this source);
        no-op for localhost (user-run GUI) and disabled.
        """
        from services.blender_mcp_manager import BlenderMcpError, manager_from_env

        env = self.config_parser.parse_env_file()
        if (env.get("BLENDER_MCP_SOURCE", "") or "").strip() != "managed-localhost":
            return True
        self.banner.show_status_message(
            "  • BLENDER_MCP_SOURCE=managed-localhost — provisioning the pinned "
            "blender-mcp add-on and launching headless Blender…",
            "info",
        )
        try:
            manager = manager_from_env(env)
            status, created = manager.ensure_running()
        except BlenderMcpError as exc:
            self.banner.show_status_message(
                f"Managed Blender MCP bridge could not start: {exc}", "error"
            )
            return False
        if created:
            self._managed_hosts_started_this_run.append(("Blender MCP", manager))
        health = manager.health()
        if health.get("reachable"):
            self.banner.show_status_message(
                f"  • Blender MCP bridge healthy on tcp://{manager.bind}:{manager.port} "
                f"(scene objects: {health.get('objects')})",
                "info",
            )
        else:
            self.banner.show_status_message(
                "  • Blender MCP bridge started but not yet answering commands — "
                "first load can lag; check `./start.sh blender-mcp health`.",
                "warning",
            )
        return True

    def _finalize_ollama_localhost_models(self) -> None:
        """Pull declared models onto the host Ollama for ``ollama-localhost``
        (#757) — the host analog of the ``ollama-pull`` init container, so a
        consumer's ``model_sidecars.ollama`` declaration provisions identically
        across sources. Idempotent (present tags skip; Ollama verifies layers
        natively) and strictly non-fatal: an unreachable daemon or a failed tag
        warns and moves on. Pulled models surface in LiteLLM ``/v1/models``
        without a restart via OLLAMA_AUTO_IMPORT_LOCAL_MODELS (default true).
        """
        from services.ollama_localhost import declared_models, pull_declared_models

        env = self.config_parser.parse_env_file()
        if (env.get("LLM_PROVIDER_SOURCE", "") or "").strip() != "ollama-localhost":
            return
        declared = declared_models(env)
        if not declared:
            return
        self.banner.show_status_message(
            f"  • LLM_PROVIDER_SOURCE=ollama-localhost — ensuring {len(declared)} "
            f"declared model tag(s) on the host Ollama (present tags skip)…",
            "info",
        )
        result = pull_declared_models(
            env, log=lambda m: self.banner.show_status_message(f"    {m}", "info")
        )
        if not result.reachable:
            self.banner.show_status_message(
                "    Host Ollama is not reachable — start it (`ollama serve`) and "
                "re-run ./start.sh to provision the declared models.",
                "warning",
            )
            return
        if result.failed:
            for failure in result.failed:
                self.banner.show_status_message(f"    ✗ {failure}", "warning")
            self.banner.show_status_message(
                "  • Some Ollama models could not be pulled — the stack starts "
                "anyway; fix the tag(s) and re-run ./start.sh.",
                "warning",
            )

    def _finalize_managed_comfyui_mps(self) -> bool:
        """Bring up the Atlas-managed Apple-Silicon/Metal ComfyUI host (#335).

        Docker Desktop on macOS can't pass Metal into a Linux container, so the
        ``managed-localhost-mps`` source runs a native ComfyUI process on the HOST
        and containers reach it via ``host.docker.internal`` (identical wiring to
        the unmanaged ``localhost`` source). When that source is selected, preflight
        + install (idempotent — only the first run downloads Torch) + start the
        host process here, before ``docker compose up``, so the endpoint is live
        when downstream containers come up. A no-op for every other source, so CI
        (whose ``.env.example`` default is ``container-cpu``) never touches it.

        Fatal on failure: the user explicitly chose this source, and downstream
        image generation is broken without the host process — surface it loudly
        rather than boot a half-configured stack.
        """
        from services.comfyui_mps_manager import ComfyUiMpsError, manager_from_env

        env = self.config_parser.parse_env_file()
        if str(env.get("COMFYUI_SOURCE", "")).strip() != "managed-localhost-mps":
            return True

        manager = manager_from_env(env)
        self.banner.show_status_message(
            "  • COMFYUI_SOURCE=managed-localhost-mps — preparing the native "
            "Apple-Silicon/Metal ComfyUI host (first run downloads Torch; this "
            "can take several minutes)…",
            "info",
        )

        # #754: provision the resolved model set into the host tree BEFORE the
        # process serves requests — same catalog selection the container init
        # would pull, same non-fatal philosophy (a failed download must not
        # abort the stack; generation just 404s for that model until re-run).
        rows = _resolved_comfyui_model_rows(env)
        if rows and not manager.preflight().ok:
            self.banner.show_status_message(
                "  • Skipping model provisioning — host preflight fails (the "
                "start below will report why); nothing multi-GB is downloaded "
                "onto an unsupported host.",
                "warning",
            )
            rows = []
        if rows:
            self.banner.show_status_message(
                f"  • Provisioning {len(rows)} declared ComfyUI model file(s) "
                f"into {manager.models_path} (idempotent; present files skip)…",
                "info",
            )
            provision = manager.provision_models(
                rows, log=lambda m: self.banner.show_status_message(f"    {m}", "info")
            )
            for warning in provision.warnings:
                self.banner.show_status_message(f"    {warning}", "warning")
            if not provision.ok:
                for failure in provision.failed:
                    self.banner.show_status_message(f"    ✗ {failure}", "warning")
                self.banner.show_status_message(
                    "  • Some ComfyUI models could not be provisioned — the host "
                    "starts anyway; re-run `./start.sh comfyui-mps provision` "
                    "after fixing the issue.",
                    "warning",
                )

        try:
            status, created = manager.ensure_running_with_ownership()
        except ComfyUiMpsError as exc:
            if exc.surviving_process:
                self._managed_hosts_started_this_run.append(
                    ("ComfyUI (MPS)", manager)
                )
            self.banner.show_status_message(
                f"Managed ComfyUI (MPS) host could not start: {exc}", "error"
            )
            return False
        if created:
            self._managed_hosts_started_this_run.append(("ComfyUI (MPS)", manager))

        health = manager.wait_healthy(timeout=60.0)
        if health.get("reachable"):
            self.banner.show_status_message(
                f"  • Managed ComfyUI (MPS) is up on port {status.port} "
                f"(device={health.get('device')}, pid={status.pid})",
                "info",
            )
        else:
            # Not fatal: ComfyUI loads lazily and downstream containers retry.
            self.banner.show_status_message(
                f"  • Managed ComfyUI (MPS) launched (pid={status.pid}, port "
                f"{status.port}); still warming up — the first request loads the "
                f"model. Logs: {status.log_file}",
                "warning",
            )
        return True

    def _finalize_managed_vllm_metal(self) -> bool:
        """Bring up the Atlas-managed vLLM Metal host (#379).

        Docker Desktop on macOS can't pass Metal into a Linux container, so the
        ``managed-localhost`` source runs a native vLLM process on the HOST (via
        the ``vllm-metal`` plugin) and LiteLLM reaches it as an OpenAI-compatible
        upstream at ``host.docker.internal:<port>/v1``. When that source is
        selected, preflight + install (idempotent — only the first run installs
        the wheel + downloads weights) + start the host process here, before
        ``docker compose up``, so the endpoint is live when litellm-init renders
        the model_list. A no-op for every other source, so CI (whose
        ``.env.example`` default is ``disabled``) never touches it.

        Fatal on failure: the user explicitly chose this source, so surface a
        broken host process loudly rather than boot a stack whose LiteLLM
        upstream is dead.
        """
        from services.vllm_metal_manager import VllmMetalError, manager_from_env

        env = self.config_parser.parse_env_file()
        if str(env.get("VLLM_METAL_SOURCE", "")).strip() != "managed-localhost":
            return True

        manager = manager_from_env(env)
        self.banner.show_status_message(
            "  • VLLM_METAL_SOURCE=managed-localhost — preparing the native "
            "Apple-Silicon/Metal vLLM host (first run installs the wheel and "
            "downloads model weights; this can take several minutes)…",
            "info",
        )
        try:
            status, created = manager.ensure_running_with_ownership()
        except VllmMetalError as exc:
            if exc.surviving_process:
                self._managed_hosts_started_this_run.append(
                    ("vLLM (Metal)", manager)
                )
            self.banner.show_status_message(
                f"Managed vLLM (Metal) host could not start: {exc}", "error"
            )
            return False
        if created:
            self._managed_hosts_started_this_run.append(("vLLM (Metal)", manager))

        health = manager.wait_healthy(timeout=120.0)
        if health.get("reachable"):
            self.banner.show_status_message(
                f"  • Managed vLLM (Metal) is up on port {status.port} "
                f"(pid={status.pid}); LiteLLM will register model "
                f"'{env.get('VLLM_METAL_MODEL', '').strip()}'.",
                "info",
            )
        else:
            # Not fatal: vLLM loads weights lazily and litellm-init retries the
            # upstream; the first completion request blocks until the model is
            # resident.
            self.banner.show_status_message(
                f"  • Managed vLLM (Metal) launched (pid={status.pid}, port "
                f"{status.port}); still loading weights — the first request "
                f"blocks until ready. Logs: {status.log_file}",
                "warning",
            )
        return True

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
        returns True (not an error) after ensuring ``volumes/comfyui/``
        exists — the always-on backend bind-mounts the directory, and a
        Docker-auto-created one would be root-owned on rootful Linux
        daemons (#568).

        Mirrors ``generate_litellm_configuration`` — see that method for the
        general pattern.
        """
        try:
            from utils.comfyui_manifest_generator import ComfyUIManifestGenerator
            env = self.config_parser.parse_env_file()
            generator = ComfyUIManifestGenerator(env)
            manifest_dir = self.root_dir / "volumes/comfyui"
            if not generator.is_enabled():
                # Disabled — nothing to write, but keep the directory
                # present: the always-on backend bind-mounts it, and a
                # Docker-auto-created host dir is root-owned on rootful
                # Linux daemons (#568). Plain mkdir only — no permission
                # repair, so the tracked marker files are never wiped.
                manifest_dir.mkdir(parents=True, exist_ok=True)
                return True
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
        a 777 chmod. If chmod also fails (usually because another user owns
        it), preserve the directory and report the ownership repair command.

        Never raises — falls back to letting the original write fail with its
        native error when ownership prevents the permission repair.
        """
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

        # Never delete a bind-mounted directory to recover permissions: it may
        # contain user models, generated configuration, or application state.
        self.banner.show_status_message(
            f"  • Could not make {path} writable; existing contents were preserved. "
            f"Repair ownership with `sudo chown -R $(id -u):$(id -g) "
            f"{shlex.quote(str(path))}` "
            f"and re-run ./start.sh",
            "warning",
        )

    def _derive_plugin_route_auth(self) -> list:
        """Derive per-prefix Kong auth overrides from plugin.yml manifests (#402).

        Returns an ordered list of (route_prefix, mode) for non-inherit auth.
        A malformed/conflicting *plugin* manifest is handled inside
        ``discover_plugin_manifests`` (collected as an error, its plugin dropped)
        and never raises — the consumer doctor surfaces those. This deliberately
        does NOT swallow other exceptions: an Atlas-internal failure (missing
        schema, import error) must **fail closed** through the caller's handler,
        which aborts Kong generation with a visible message, rather than silently
        downgrading a ``key-auth`` prefix to the (possibly open) default
        (#402 review M2).
        """
        from core.plugin_manifest import (
            derive_route_auth,
            discover_plugin_manifests,
        )

        plugin_dirs = _resolve_plugin_dirs(self)
        if not plugin_dirs:
            return []
        discovery = discover_plugin_manifests(plugin_dirs)
        return derive_route_auth(discovery.manifests)

    def generate_kong_configuration(self) -> bool:
        """Generate dynamic Kong configuration based on SOURCE values."""
        try:
            from utils.kong_config_generator import KongConfigGenerator
            generator = KongConfigGenerator(self.config_parser)
            generator.track_key = getattr(self, "active_track", None)
            generator.overridden_services = getattr(
                self, "active_track_overrides", frozenset()
            )
            # Per-plugin Kong auth (#402): read plugin.yml manifests from the
            # resolved plugin dirs and derive the (route_prefix, mode) overrides
            # so key-auth/open can be expressed per prefix. Empty for base Atlas
            # (no plugins) → the backend route is emitted exactly as before.
            generator.plugin_route_auth = self._derive_plugin_route_auth()
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
            
    def perform_cold_start_cleanup(self, project_name: Optional[str] = None) -> bool:
        """Perform cold start cleanup if requested."""
        self.banner.show_section_header("Cold Start Cleanup", "🧹")

        self.banner.show_status_message("Performing cold start cleanup...", "info")

        # Use the enhanced cold start cleanup. Forward the CLI project name so a
        # `--cold --project foo` run tears down `foo` rather than the stale
        # project still recorded in .env (the override is not persisted until
        # setup_env_file runs later).
        success = self.docker_manager.perform_cold_start_cleanup(project_name=project_name)
        
        if not success:
            self.banner.show_status_message("Cold cleanup failed; secrets were not rotated", "error")
        else:
            self.banner.show_status_message("Cold cleanup completed successfully", "success")

        return success
        
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
        
    def start_docker_services(self, cold_start: bool = False, wait: bool = False) -> bool:
        """Start Docker services with optional fresh build for cold start."""
        self.banner.show_section_header("Starting Services", "🚀")
        
        if cold_start:
            self.banner.show_status_message("Starting containers with fresh build (cold start)...", "info")

            # Enabled-service target set from the rendered projection (#504) —
            # a broken build for a disabled/out-of-track service must not
            # abort the cold start. None (projection failure) falls back to
            # the historical full-graph build/up.
            targets = self.docker_manager.enabled_service_targets()

            # Build images without cache (matching original Bash script behavior)
            print("    - Building images without cache...")
            build_result = self.docker_manager.build_services(
                no_cache=True, pull=False, services=targets
            )

            if build_result != 0:
                self.banner.show_status_message("Failed to build some services", "error")
                self.rollback_managed_host_processes()
                return False

            # Cold start just built every enabled local image fresh — record the
            # source commit so the next warm start (#506) doesn't rebuild them
            # again until the source actually changes.
            self.docker_manager.mark_source_built()

            print("    - Starting containers...")
            # Start with force recreate for cold start
            up_args = ['up', '-d', '--force-recreate']
            if wait:
                up_args.extend(['--wait', '--wait-timeout', '900'])
            if targets:
                up_args.extend(targets)
            result = self.docker_manager.execute_compose_command(up_args)

        else:
            self.banner.show_status_message("Starting Atlas services...", "info")
            result = self.docker_manager.start_services(detached=True, wait=wait)
        
        if result != 0:
            # Known benign race (#508): `up -d --wait` can return nonzero when
            # an enabled one-shot init exits 0 during the wait window (Compose
            # reports e.g. "container …-n8n-init exited (0)"), even though the
            # whole stack converged. Before failing, inspect the resolved
            # state — if every reported service is running/healthy or exited
            # 0, continue to the normal one-shot verification instead of
            # false-failing automation consumers. Genuine failures (nonzero
            # exits, unhealthy/non-running services) still fail, with names.
            if not (wait and self._up_wait_race_converged()):
                self.banner.show_status_message("Failed to start some services", "error")
                self.rollback_managed_host_processes()
                return False
        if not self.verify_one_shot_init_containers():
            self.rollback_managed_host_processes()
            return False
        self._reactivate_n8n_if_needed()
        self.commit_managed_host_processes()
        self.banner.show_status_message("All services started successfully", "success")
        return True

    def _reactivate_n8n_if_needed(self) -> None:
        """Restart n8n once after seeding so a consumer's production webhook
        registers when the workflow was activated **without** an ``N8N_API_KEY``.

        n8n CE registers a workflow's production webhook only when the n8n
        *server* (re)starts. With a key, the seed activates over the public API
        and the webhook registers immediately (no restart). Without a key, the
        seed persists ``active=true`` via ``n8n publish:workflow`` — but the
        running server ignores that until it restarts (empirically verified on
        n8nio/n8n:2.28.2: publish prints "restart required" and the production
        webhook stays 404 until a restart, then 200). So Atlas performs the one
        restart the consumer would otherwise do by hand. No-op with a key, with
        n8n disabled, or when no active consumer workflow is declared.
        """
        try:
            consumer = self.config_parser.load_consumer_config()
            env = self.config_parser.parse_env_file()
        except Exception:
            return
        if not _n8n_needs_reactivation_restart(env, consumer):
            return
        self.banner.show_status_message(
            "Restarting n8n to register consumer webhook(s) (no N8N_API_KEY)...",
            "info",
        )
        self.docker_manager.execute_compose_command(["restart", "n8n"])

    def _poll_until_converged(
        self,
        *,
        grace_seconds: float = 120.0,
        poll_interval_seconds: float = 2.0,
        poll_rows=None,
        sleep=None,
        monotonic=None,
    ) -> tuple[list[dict], bool, bool, str | None]:
        """Inspect ``compose ps`` and re-poll convergent-pending rows within a
        bounded grace window (#677/#681).

        A ``state=running, health=starting`` row is *convergent-pending*, not a
        failure — its healthcheck is simply still in its start period. This
        re-polls while any row is pending, up to ``grace_seconds``, so a stack
        that is merely mid-probe at the first snapshot is given time to settle
        before being classified.

        Returns ``(services, converged, waited, error)``:
        - ``services`` — the classified rows from the final poll.
        - ``converged`` — True iff there is at least one service and every row
          is ok (no genuine failure, nothing still pending).
        - ``waited`` — True iff a pending row was observed and re-polled, so a
          caller can flag "converged after grace".
        - ``error`` — a ``compose ps`` inspection error, or None.

        A genuine failure (``not ok and not pending``) short-circuits
        immediately — there is no point waiting on a doomed start. Dependencies
        (``poll_rows`` / ``sleep`` / ``monotonic``) are injectable so the
        grace loop is unit-testable with fake ps snapshots and a fake clock.
        """
        import time as _time

        poll_rows = poll_rows or self.docker_manager.compose_ps_json
        sleep = sleep or _time.sleep
        monotonic = monotonic or _time.monotonic

        waited = False
        deadline = monotonic() + grace_seconds
        while True:
            rows, error = poll_rows()
            if error is not None:
                return [], False, waited, error
            services = [self._compose_row_status(row) for row in rows]
            genuine_failure = any(
                not entry["ok"] and not entry["pending"] for entry in services
            )
            pending = [entry for entry in services if entry["pending"]]
            # Classify now on a genuine failure (don't wait on a doomed start),
            # an empty stack, or once nothing is still pending.
            if genuine_failure or not services or not pending:
                converged = bool(services) and all(
                    entry["ok"] for entry in services
                )
                return services, converged, waited, None
            # Only ``starting`` rows remain and nothing has failed — grace-wait.
            if monotonic() >= deadline:
                # Grace exhausted; still-pending rows now count as failures.
                return services, False, waited, None
            waited = True
            sleep(min(poll_interval_seconds, max(0.0, deadline - monotonic())))

    def _up_wait_race_converged(
        self,
        *,
        grace_seconds: float = 120.0,
        poll_interval_seconds: float = 2.0,
        poll_rows=None,
        sleep=None,
        monotonic=None,
    ) -> bool:
        """Classify a nonzero ``up -d --wait`` as a benign, converged start.

        Two benign races are covered: the #508 one-shot-init race (a one-shot
        exits 0 during the wait window) and the #677/#681 long-lived-healthcheck
        race (a service is still ``health=starting`` at the snapshot). Returns
        True only when ``compose ps`` reports at least one service and every row
        is ok per ``_compose_row_status`` — after re-polling any still-starting
        rows within ``grace_seconds``. Any other outcome — inspection error,
        empty stack, unhealthy/non-running service, or a row still starting
        after the grace window — returns False and names the offenders, so a
        genuine failure keeps failing loudly. Injectable deps mirror
        ``_poll_until_converged`` for testing.
        """
        services, converged, waited, error = self._poll_until_converged(
            grace_seconds=grace_seconds,
            poll_interval_seconds=poll_interval_seconds,
            poll_rows=poll_rows,
            sleep=sleep,
            monotonic=monotonic,
        )
        if error is not None:
            self.banner.show_status_message(
                f"Could not inspect services after nonzero `up --wait`: {error}",
                "error",
            )
            return False
        if not services:
            return False
        if not converged:
            for entry in services:
                if entry["ok"]:
                    continue
                exit_note = (
                    f", exit code {entry['exit_code']}" if entry["exit_code"] else ""
                )
                reason = entry["reason"]
                if entry["pending"]:
                    # Still starting after the full grace window — a genuine
                    # timeout now, not a benign mid-probe.
                    reason = f"{reason} (still not healthy after grace window)"
                self.banner.show_status_message(
                    f"{entry['service']}: {reason}{exit_note}",
                    "error",
                )
            return False

        self._up_converged_after_grace = waited
        detail = (
            "services converged within the health-check grace window"
            if waited
            else "known successful one-shot init race"
        )
        self.banner.show_status_message(
            "Compose `up --wait` returned nonzero, but every service is "
            f"running/healthy and every one-shot exited 0 — continuing ({detail}).",
            "warning",
        )
        return True

    @staticmethod
    def _compose_row_status(row: dict) -> dict:
        service = str(row.get("Service") or row.get("Name") or "unknown")
        state = str(row.get("State") or "").strip().lower()
        health = str(row.get("Health") or "").strip().lower()
        status = str(row.get("Status") or "").strip()
        exit_code = str(row.get("ExitCode") if row.get("ExitCode") is not None else "").strip()
        status_lower = status.lower()

        ok = False
        pending = False
        reason = status or state or health or "unknown"
        if state == "running":
            # A running container's exit code is meaningless (Compose reports 0
            # for a live container); never surface it as a failure signal — it
            # is the source of the misleading "starting, exit code 0" line.
            exit_code = ""
            if health == "starting":
                # Convergent-pending, NOT failed: the healthcheck is still in
                # its start period. Callers re-poll pending rows within a grace
                # window before classifying (#677/#681).
                pending = True
                reason = "starting"
            else:
                ok = health in {"", "healthy"}
                if health and health != "healthy":
                    reason = health
        elif state == "exited":
            ok = (
                exit_code in {"", "0", "<nil>", "None"}
                or "exit 0" in status_lower
                or "exited (0)" in status_lower
            )
        elif state in {"created", "restarting", "paused", "dead"}:
            ok = False

        return {
            "service": service,
            "state": state or None,
            "health": health or None,
            "status": status or None,
            "exit_code": exit_code or None,
            "ok": ok,
            "pending": pending,
            "reason": reason,
        }

    def show_detached_status_summary(
        self,
        *,
        json_output: bool = False,
        grace_seconds: float = 120.0,
        poll_interval_seconds: float = 2.0,
        poll_rows=None,
        sleep=None,
        monotonic=None,
    ) -> bool:
        """Print final compose status for automation-friendly detached starts.

        Grace-aware (#677/#681): a service still ``health=starting`` at the
        summary moment is re-polled within a bounded window before classifying,
        so the final summary never false-fails on a mid-probe healthcheck. The
        ``--json`` payload carries ``converged_after_grace`` so automation can
        tell a health race apart from a first-pass-healthy start.
        """
        services, converged, waited, error = self._poll_until_converged(
            grace_seconds=grace_seconds,
            poll_interval_seconds=poll_interval_seconds,
            poll_rows=poll_rows,
            sleep=sleep,
            monotonic=monotonic,
        )
        if error is not None:
            payload = {
                "ok": False,
                "error": error,
                "services": [],
                "converged_after_grace": False,
            }
            if json_output:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                self.banner.show_status_message(f"Could not inspect services: {error}", "error")
            return False

        ok = converged
        # The race happened if this summary had to wait, or the earlier
        # `up --wait` reclassification did.
        converged_after_grace = bool(self._up_converged_after_grace or waited)
        payload = {
            "ok": ok,
            "services": services,
            "converged_after_grace": converged_after_grace,
        }

        if json_output:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return ok

        self.banner.show_section_header("Detached Status", "📊")
        if not services:
            self.banner.show_status_message("No compose services were reported", "error")
            return False
        for entry in services:
            level = "success" if entry["ok"] else "error"
            health = f" / {entry['health']}" if entry["health"] else ""
            self.banner.show_status_message(
                f"{entry['service']}: {entry['state'] or 'unknown'}{health} — {entry['reason']}",
                level,
            )
        return ok

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
        if env_vars.get("REDPANDA_INIT_SCALE", "0") != "0":
            services.append("redpanda-init")
        if env_vars.get("ZEPPELIN_INIT_SCALE", "0") != "0":
            services.append("zeppelin-init")
        try:
            consumer_config = self.config_parser.load_consumer_config()
        except Exception as exc:
            message = (
                "Could not inspect consumer one-shot services "
                f"(error_type={type(exc).__name__})"
            )
            if on_line is None:
                self.banner.show_status_message(message, "error")
            else:
                on_line(message, "error")
            return False
        if consumer_config.n8n_workflows:
            services.append("n8n-seed")
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
            
    def show_pre_launch_summary(
        self,
        *,
        track: str | None = None,
        assume_yes: bool = False,
    ) -> bool:
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
        if assume_yes:
            return True
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

        consumer_config = self.config_parser.load_consumer_config()
        consumer_panel = None
        if consumer_config.consumers:
            consumer_lines = []
            for consumer in consumer_config.consumers:
                line = Text()
                line.append(f"  {consumer.name:<16}", style="bright_white")
                details = []
                if consumer.compose_overlays:
                    details.append(f"{len(consumer.compose_overlays)} overlay")
                if consumer.backend_plugins:
                    details.append(f"{len(consumer.backend_plugins)} plugin dir")
                if consumer.comfyui_sidecars:
                    details.append(f"{len(consumer.comfyui_sidecars)} ComfyUI sidecar")
                if consumer.ollama_models:
                    details.append(f"{len(consumer.ollama_models)} Ollama model")
                line.append(", ".join(details) if details else "registered", style="color(75)")
                consumer_lines.append(line)
            consumer_panel = Panel(
                Group(*consumer_lines),
                title="[bold bright_white]Consumers[/bold bright_white]  "
                      "[color(243)](atlas.consumer.yml)[/color(243)]",
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
        panels = [table, cloud_panel]
        if consumer_panel is not None:
            panels.append(consumer_panel)
        if warning_lines:
            warning_texts = [
                Text.from_markup(f"[yellow]{msg}[/yellow]")
                for msg in warning_lines
            ]
            return Group(*track_line, *panels, *warning_texts)
        return Group(*track_line, *panels)

    @staticmethod
    def _get_localhost_port(service_name: str, env_vars: dict) -> str:
        """Extract the localhost port for the pre-launch summary. Prefers the
        service's endpoint env var (which encodes the active source variant's
        port — e.g. ComfyUI's MPS port), then falls back to the dedicated
        localhost_port_var for services that declare only a port number and no
        endpoint URL (e.g. OpenClaw, Neo4j). Without the fallback those rows
        rendered '-' even when a localhost port was configured."""
        from services.topology import get_topology
        _topology = get_topology()
        row = None
        for r in _topology.rows:
            if r.display_name == service_name:
                row = r
                break
        if row is None:
            return "-"
        if row.localhost_endpoint_var:
            endpoint = env_vars.get(row.localhost_endpoint_var, '')
            match = re.search(r':(\d+)', endpoint)
            if match:
                return f":{match.group(1)}"
        if row.localhost_port_var:
            port = env_vars.get(row.localhost_port_var, '').strip()
            if port:
                return f":{port}"
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
        


def _parse_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw_value = stripped.partition("=")
        values[key.strip()] = raw_value.split("#", 1)[0].strip()
    return values


def _env_example_section_by_key(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    bar_re = re.compile(r"^#\s*[=─]{3,}\s*$")
    lines = path.read_text(encoding="utf-8").splitlines()
    current_section = "(unsectioned)"
    by_key: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            bar_re.match(line)
            and i + 2 < len(lines)
            and lines[i + 1].startswith("#")
            and bar_re.match(lines[i + 2])
        ):
            current_section = lines[i + 1].lstrip("#").strip() or "(unnamed)"
            i += 3
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, _raw_value = stripped.partition("=")
            by_key[key.strip()] = current_section
        i += 1
    return by_key


def _print_env_backfill_summary(
    *,
    env_path: Path,
    env_example_path: Path,
    before: dict[str, str],
    after: dict[str, str],
) -> None:
    added = sorted(key for key in after if key not in before)
    filled = sorted(
        key
        for key, prior in before.items()
        if key in after and not prior and bool(after[key])
    )
    if not added and not filled:
        click.echo(f"No env changes needed: {env_path}")
        return

    section_by_key = _env_example_section_by_key(env_example_path)
    grouped: dict[str, list[str]] = {}
    for key in added:
        section = section_by_key.get(key, "(unsectioned)")
        grouped.setdefault(section, []).append(f"{key} (added)")
    for key in filled:
        section = section_by_key.get(key, "(unsectioned)")
        grouped.setdefault(section, []).append(f"{key} (filled blank)")

    click.echo(f"Env backfill updated {env_path}:")
    for section in sorted(grouped):
        click.echo(f"{section}:")
        for item in grouped[section]:
            click.echo(f"  - {item}")


def _compose_validation_summary(output: str) -> str | None:
    variable: str | None = None
    service: str | None = None

    variable_match = re.search(
        r'["\\]*([A-Z0-9_]+)["\\]* variable is not set',
        output,
    )
    if variable_match:
        variable = variable_match.group(1)

    service_match = re.search(
        r'service ["\\]*([^"\\]+)["\\]* has neither an image nor a build context',
        output,
    )
    if service_match:
        service = service_match.group(1)

    if variable and service:
        return f"Service {service} is missing compose variable {variable}."
    if variable:
        return f"Compose variable {variable} is not set."
    if service:
        return f"Service {service} has neither an image nor a build context."
    return None


def _n8n_workflow_effective_active(wf) -> bool:
    """Mirror seed-workflows.js ``effectiveActive``: ``'true'`` → active,
    ``'false'`` → not, ``'fromJson'`` → read the workflow file's own ``active``
    flag (defaulting to inactive on any read/parse error)."""
    if wf.active == "true":
        return True
    if wf.active == "false":
        return False
    try:
        data = json.loads(Path(wf.source_path).read_text(encoding="utf-8"))
        return bool(data.get("active"))
    except Exception:
        return False


def _n8n_needs_reactivation_restart(env: "dict[str, str]", consumer) -> bool:
    """True when Atlas must restart n8n after seeding to register a consumer's
    production webhook: an active n8n workflow is declared, n8n is enabled, and
    **no** ``N8N_API_KEY`` is configured. With a key the seed activates over the
    API and the webhook registers immediately (no restart needed)."""
    if (env.get("N8N_API_KEY", "") or "").strip():
        return False
    if (env.get("N8N_SOURCE", "") or "").strip() in ("", "disabled"):
        return False
    return any(
        _n8n_workflow_effective_active(wf)
        for wf in getattr(consumer, "n8n_workflows", ())
    )


def _doctor_result(
    check_id: str,
    status: str,
    message: str,
    *,
    details: dict | None = None,
) -> dict:
    return {
        "id": check_id,
        "status": status,
        "message": message,
        "details": details or {},
    }


def _resolved_comfyui_model_rows(env: dict) -> list[dict]:
    """The per-file ComfyUI model rows the container TSV is built from (#754).

    One source of truth for "what models this stack needs": the same
    ``comfyui_resolver`` output drives the container init download AND the
    managed-host provisioner. Returns [] when nothing is selected or the
    resolver cannot run (a malformed catalog surfaces through its own lint)."""
    try:
        from utils.comfyui_resolver import active_comfyui_models, manifest_dict

        return list(manifest_dict(active_comfyui_models(env)).get("models", []))
    except Exception:  # noqa: BLE001 — resolver issues have their own surfaces
        return []


def _doctor_check_compose(starter: "AtlasStarter") -> dict:
    try:
        returncode, stdout, stderr, _cmd = starter.docker_manager.validate_compose_config()
    except ValueError as exc:
        return _doctor_result(
            "compose",
            "fail",
            f"Compose config validation could not load consumer manifests: {exc}",
        )
    except RuntimeError as exc:
        return _doctor_result(
            "compose",
            "skipped",
            f"Docker Compose unavailable: {exc}",
        )
    if returncode == 0:
        return _doctor_result("compose", "pass", "Compose config is valid.")
    output = "\n".join(part for part in (stderr.strip(), stdout.strip()) if part)
    summary = _compose_validation_summary(output)
    return _doctor_result(
        "compose",
        "fail",
        summary or "Compose config validation failed.",
        details={"returncode": returncode, "output": output},
    )


def _doctor_check_consumer_manifests(starter: "AtlasStarter") -> dict:
    try:
        consumer_config = starter.config_parser.load_consumer_config()
    except ValueError as exc:
        return _doctor_result(
            "consumer-manifests",
            "fail",
            f"Consumer manifest validation failed: {exc}",
        )
    if consumer_config.is_empty:
        return _doctor_result(
            "consumer-manifests",
            "pass",
            "No consumer manifests configured.",
        )
    names = [consumer.name for consumer in consumer_config.consumers]
    overlay_count = sum(len(consumer.compose_overlays) for consumer in consumer_config.consumers)
    return _doctor_result(
        "consumer-manifests",
        "pass",
        f"{len(names)} consumer manifest(s) valid: {', '.join(names)}.",
        details={"consumers": names, "compose_overlays": overlay_count},
    )


_COMPOSE_VAR_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}"
)


def _doctor_compose_var_refs(text: str) -> list[tuple[str, bool]]:
    refs: list[tuple[str, bool]] = []
    for match in _COMPOSE_VAR_RE.finditer(text):
        var_name = match.group(1)
        operator = match.group(2) or ""
        has_default = "-" in operator
        refs.append((var_name, has_default))
    return refs


def _doctor_check_overlay_env(starter: "AtlasStarter") -> dict:
    root = starter.config_parser.root_dir
    user_dir = root / "services" / "_user"
    overlays = sorted(user_dir.glob("*/compose.yml")) if user_dir.is_dir() else []
    if not overlays:
        return _doctor_result(
            "overlay-env",
            "pass",
            "No services/_user compose overlays found.",
        )

    env_values = dict(os.environ)
    env_values.update(starter.config_parser.parse_env_file())
    missing: list[dict[str, str]] = []
    checked_refs = 0
    for overlay in overlays:
        text = overlay.read_text(encoding="utf-8")
        for var_name, has_default in _doctor_compose_var_refs(text):
            checked_refs += 1
            if has_default or var_name in env_values:
                continue
            missing.append(
                {
                    "file": str(overlay.relative_to(root)),
                    "variable": var_name,
                }
            )

    if missing:
        first = missing[0]
        return _doctor_result(
            "overlay-env",
            "fail",
            f"{first['file']} references unresolved variable {first['variable']}.",
            details={"missing": missing, "checked_refs": checked_refs},
        )
    return _doctor_result(
        "overlay-env",
        "pass",
        f"All {checked_refs} overlay env reference(s) resolve or provide defaults.",
        details={"overlays": [str(path.relative_to(root)) for path in overlays]},
    )


def _resolve_plugin_dirs(starter: "AtlasStarter", env_values: dict | None = None) -> list[Path]:
    """Resolve BACKEND_PLUGINS_DIR to existing host directories.

    Relative parts resolve from the bootstrapper root (matching how the compose
    mount is authored). Nonexistent parts are skipped — this is a best-effort
    host-side view used by the doctor and the Kong plugin-auth derivation.
    """
    if env_values is None:
        env_values = starter.config_parser.parse_env_file()
    raw_path = env_values.get("BACKEND_PLUGINS_DIR", "").strip()
    dirs: list[Path] = []
    for raw_part in raw_path.split(os.pathsep):
        raw_part = raw_part.strip()
        if not raw_part:
            continue
        plugins_dir = Path(raw_part).expanduser()
        if not plugins_dir.is_absolute():
            plugins_dir = starter.config_parser.root_dir / plugins_dir
        if plugins_dir.exists():
            dirs.append(plugins_dir)
    return dirs


def _doctor_check_plugins(starter: "AtlasStarter") -> dict:
    env_values = starter.config_parser.parse_env_file()
    raw_path = env_values.get("BACKEND_PLUGINS_DIR", "").strip()
    if not raw_path:
        return _doctor_result(
            "plugins",
            "pass",
            "No BACKEND_PLUGINS_DIR configured.",
        )
    plugin_dirs_to_check: list[Path] = []
    for raw_part in raw_path.split(os.pathsep):
        raw_part = raw_part.strip()
        if not raw_part:
            continue
        plugins_dir = Path(raw_part).expanduser()
        if not plugins_dir.is_absolute():
            plugins_dir = starter.config_parser.root_dir / plugins_dir
        if not plugins_dir.exists():
            return _doctor_result(
                "plugins",
                "skipped",
                f"Plugin directory does not exist: {plugins_dir}",
            )
        plugin_dirs_to_check.append(plugins_dir)
    problems: list[str] = []
    requirement_files = 0
    requirement_entries = 0
    plugin_names: list[str] = []
    for plugins_dir in plugin_dirs_to_check:
        for req in [plugins_dir / "requirements.txt", *plugins_dir.glob("*/requirements.txt")]:
            try:
                if not req.exists():
                    continue
                requirement_files += 1
                for line in req.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    requirement_entries += 1
            except OSError as exc:
                problems.append(f"{req} could not be read: {exc}")
        plugin_names.extend(path.name for path in plugins_dir.iterdir() if path.is_dir())
    if problems:
        return _doctor_result(
            "plugins",
            "fail",
            problems[0],
            details={"problems": problems},
        )
    return _doctor_result(
        "plugins",
        "pass",
        f"Plugin directory readable; {len(plugin_names)} plugin dir(s) found.",
        details={
            "plugins": plugin_names,
            "requirement_files": requirement_files,
            "requirement_entries": requirement_entries,
        },
    )


def _doctor_check_plugin_manifests(starter: "AtlasStarter") -> dict:
    """Validate optional plugin.yml manifests + their declared env (#402).

    Reports malformed manifests and env problems (required-missing, type/enum
    mismatch) as warnings naming the plugin and var, so a bad manifest surfaces
    as a startup diagnostic rather than a runtime 500. Secret values are never
    echoed. Manifest-less plugins are ignored here (the plugins check covers the
    directory itself).
    """
    from core.plugin_manifest import (
        discover_plugin_manifests,
        validate_plugin_env,
    )

    env_values = starter.config_parser.parse_env_file()
    plugin_dirs = _resolve_plugin_dirs(starter, env_values)
    if not plugin_dirs:
        return _doctor_result(
            "plugin-manifests",
            "pass",
            "No plugin directories to scan for manifests.",
        )
    discovery = discover_plugin_manifests(plugin_dirs)
    warnings: list[str] = list(discovery.errors)
    for manifest in discovery.manifests:
        warnings.extend(validate_plugin_env(manifest, env_values))
    names = [m.name for m in discovery.manifests]
    if warnings:
        return _doctor_result(
            "plugin-manifests",
            "warn",
            warnings[0],
            details={"warnings": warnings, "plugins": names},
        )
    if not names:
        return _doctor_result(
            "plugin-manifests",
            "pass",
            "No plugin.yml manifests found.",
        )
    return _doctor_result(
        "plugin-manifests",
        "pass",
        f"{len(names)} plugin manifest(s) valid: {', '.join(names)}.",
        details={"plugins": names},
    )


def _doctor_check_model_sidecars(starter: "AtlasStarter") -> dict:
    env_values = starter.config_parser.parse_env_file()
    raw_path = env_values.get("COMFYUI_CUSTOM_MODELS_FILE", "").strip()
    if not raw_path:
        return _doctor_result(
            "model-sidecars",
            "pass",
            "No COMFYUI_CUSTOM_MODELS_FILE configured.",
        )
    sidecars: list[Path] = []
    for raw_part in raw_path.split(os.pathsep):
        raw_part = raw_part.strip()
        if not raw_part:
            continue
        sidecar = Path(raw_part).expanduser()
        if not sidecar.is_absolute():
            sidecar = starter.config_parser.root_dir / sidecar
        sidecars.append(sidecar)

    import yaml

    parsed = 0
    for sidecar in sidecars:
        if not sidecar.exists():
            return _doctor_result(
                "model-sidecars",
                "skipped",
                f"Model sidecar does not exist: {sidecar}",
            )
        try:
            data = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return _doctor_result(
                "model-sidecars",
                "fail",
                f"Could not parse model sidecar {sidecar}: {exc}",
            )
        if data is not None and not isinstance(data, (dict, list)):
            return _doctor_result(
                "model-sidecars",
                "fail",
                f"Model sidecar {sidecar} must parse to a mapping or list.",
            )
        parsed += 1
    return _doctor_result(
        "model-sidecars",
        "pass",
        f"{parsed} model sidecar file(s) parse.",
    )


def _doctor_check_unpullable_models(starter: "AtlasStarter") -> dict:
    """Presence-check declared models for the selected sources (#718 → #754/#757).

    Container sources are provisioned by init containers; managed host sources
    are provisioned by Atlas at start (ComfyUI managed-localhost-mps downloads
    the resolved catalog, ollama-localhost pulls declared tags onto the host
    daemon). This lint passes when the declared set is present, and warns —
    naming what's missing and the command that provisions it — when not. Only
    an unmanaged ComfyUI ``localhost`` install remains hands-off (Atlas never
    writes into a user-owned tree).
    """
    env = starter.config_parser.parse_env_file()
    warnings: list[str] = []

    llm_source = (env.get("LLM_PROVIDER_SOURCE", "") or "").strip()
    if llm_source == "ollama-localhost":
        # #757: Atlas provisions the host daemon now. Presence of the declared
        # tag set (OLLAMA_USER_MODELS ∪ OLLAMA_CUSTOM_MODELS — the same union
        # the container pull uses) decides pass vs. actionable warn, naming
        # each missing tag (the host analogue of the #718 container lint).
        from services.ollama_localhost import (
            declared_models,
            host_base_url,
            list_host_tags,
        )

        declared = declared_models(env)
        if declared:
            try:
                present = list_host_tags(host_base_url(env))
            except Exception as exc:  # noqa: BLE001 — a malformed port/env must
                # degrade to a warning, never crash the whole doctor run.
                present = None
                warnings.append(f"Could not query the host Ollama: {exc}")
            if present is None:
                warnings.append(
                    f"Host Ollama at {host_base_url(env)} is not reachable — the "
                    f"{len(declared)} declared model tag(s) will be pulled on the "
                    f"next ./start.sh once the daemon is running (`ollama serve`)."
                )
            else:
                normalized = {t if ":" in t else f"{t}:latest" for t in declared}
                missing = sorted(normalized - present)
                if missing:
                    warnings.append(
                        f"Declared Ollama model(s) missing from the host daemon: "
                        f"{', '.join(missing)}. Atlas pulls these on the next "
                        f"./start.sh (or pull now: `ollama pull "
                        f"{' && ollama pull '.join(missing)}`)."
                    )

    comfy_models = (env.get("COMFYUI_USER_MODELS", "") or "").strip()
    comfy_source = (env.get("COMFYUI_SOURCE", "") or "").strip()
    if (
        comfy_models
        and comfy_source
        and not comfy_source.startswith("container")
        and comfy_source != "disabled"
    ):
        if comfy_source == "managed-localhost-mps":
            # #754: the managed host IS provisioned by Atlas now. Presence of
            # the resolved file set decides pass vs. actionable warn.
            try:
                from services.comfyui_mps_manager import manager_from_env

                rows = _resolved_comfyui_model_rows(env)
                if not rows:
                    # A resolver failure yields [] — declared models with an
                    # unresolvable catalog must warn, not silently pass (#754).
                    satisfied, missing = False, [
                        "declared catalog could not be resolved (see catalog lint)"
                    ]
                else:
                    satisfied, missing = manager_from_env(env).models_satisfied(rows)
            except Exception as exc:  # noqa: BLE001 - defensive
                satisfied, missing = False, [f"could not check host tree: {exc}"]
            if not satisfied:
                warnings.append(
                    f"COMFYUI_USER_MODELS ({comfy_models}) is declared but the "
                    f"host tree is missing: {', '.join(missing)}. Atlas "
                    f"provisions these on the next `./start.sh` (or run "
                    f"`./start.sh comfyui-mps provision` now)."
                )
        else:
            warnings.append(
                f"COMFYUI_USER_MODELS ({comfy_models}) is declared but "
                f"COMFYUI_SOURCE={comfy_source} reuses a host weight tree Atlas "
                f"does not manage — the catalog is downloaded for container* "
                f"sources and provisioned for managed-localhost-mps (#754), but "
                f"not for an unmanaged localhost install. Provision manually: "
                f"place the weights in your host ComfyUI models directory."
            )

    if warnings:
        return _doctor_result(
            "unpullable-models",
            "warn",
            " ".join(warnings),
            details={"warnings": warnings},
        )
    return _doctor_result(
        "unpullable-models",
        "pass",
        "No declared-but-unpullable model provisioning for the selected sources.",
    )


def _doctor_check_litellm_models(starter: "AtlasStarter") -> dict:
    """Validate consumer-declared LiteLLM model rows (#411).

    Load-time already resolved api_base against the approved endpoint allowlist
    and stamped manifest-derived ownership (a parse failure surfaces as a fail
    here). This check adds the cross-surface validation the triage calls for:
    each backend-hosted model's first route segment is cross-checked against the
    declared #402 backend plugin route_prefixes (when any manifests are present),
    so a model pointing at a route no plugin serves surfaces as a startup
    diagnostic rather than a dead /v1/models entry. Secrets never appear (rows
    carry only ``os.environ/<VAR>`` references).
    """
    from core.consumer_manifest import LITELLM_ENDPOINT_TEMPLATES

    try:
        config = starter.config_parser.load_consumer_config()
    except ValueError as exc:
        return _doctor_result(
            "litellm-models",
            "fail",
            f"Consumer LiteLLM model validation failed: {exc}",
        )
    if not config.litellm_models:
        return _doctor_result(
            "litellm-models",
            "pass",
            "No consumer LiteLLM models declared.",
        )

    import urllib.parse

    backend_host = urllib.parse.urlparse(
        LITELLM_ENDPOINT_TEMPLATES["ATLAS_BACKEND_INTERNAL"]
    ).netloc

    # Declared #402 backend plugin route heads (empty when no manifests present)
    # plus the built-in backend route prefixes (research/health/… are legitimate
    # targets even with no plugin manifest, so they must never trip the warning).
    from core.plugin_manifest import RESERVED_ROUTE_PREFIXES

    env_values = starter.config_parser.parse_env_file()
    plugin_dirs = _resolve_plugin_dirs(starter, env_values)
    plugin_heads: set[str] = set()
    have_manifests = False
    if plugin_dirs:
        from core.plugin_manifest import discover_plugin_manifests

        discovery = discover_plugin_manifests(plugin_dirs)
        have_manifests = bool(discovery.manifests)
        plugin_heads = {m.prefix_head for m in discovery.manifests}

    warnings: list[str] = []
    for model in config.litellm_models:
        parsed = urllib.parse.urlparse(model.api_base)
        if parsed.netloc != backend_host:
            continue  # non-backend endpoints have no plugin route to match
        segments = [seg for seg in parsed.path.split("/") if seg]
        head = segments[0] if segments else ""
        # Only warn when plugin manifests exist but none serves this route AND
        # it isn't a built-in backend route — a route with no manifests may be
        # served by a built-in route or a plugin added later, so we don't cry
        # wolf on the manifest-less case or on a legitimate built-in prefix.
        if (
            have_manifests
            and head
            and head not in plugin_heads
            and head not in RESERVED_ROUTE_PREFIXES
        ):
            warnings.append(
                f"model {model.name!r} (owner {model.consumer}) points at backend "
                f"route /{head}, which matches no declared plugin route_prefix "
                f"({', '.join('/' + h for h in sorted(plugin_heads)) or 'none'})"
            )

    owners = sorted({model.consumer for model in config.litellm_models})
    names = [model.name for model in config.litellm_models]
    if warnings:
        return _doctor_result(
            "litellm-models",
            "warn",
            warnings[0],
            details={"warnings": warnings, "models": names, "owners": owners},
        )
    return _doctor_result(
        "litellm-models",
        "pass",
        f"{len(names)} consumer LiteLLM model(s) valid: {', '.join(names)}.",
        details={"models": names, "owners": owners},
    )


def _doctor_check_n8n_workflows(starter: "AtlasStarter") -> dict:
    """Validate consumer-declared n8n workflows to seed (#412).

    Load-time already validated the files (JSON parseable, credential-safe,
    checksum, stable/unique id, non-colliding webhook routes); a parse failure
    surfaces as a fail here. This check re-confirms each source file still exists
    and adds an operational signal: an active workflow with declared webhooks but
    no ``N8N_API_KEY`` will only register its production webhook after an n8n
    restart (the seed can't activate it live without a key). Secrets never appear.
    """
    try:
        config = starter.config_parser.load_consumer_config()
    except ValueError as exc:
        return _doctor_result(
            "n8n-workflows",
            "fail",
            f"Consumer n8n workflow validation failed: {exc}",
        )
    if not config.n8n_workflows:
        return _doctor_result(
            "n8n-workflows",
            "pass",
            "No consumer n8n workflows declared.",
        )

    warnings: list[str] = []
    for wf in config.n8n_workflows:
        if not wf.source_path.is_file():
            warnings.append(
                f"workflow {wf.id!r} (owner {wf.consumer}) source file is missing: "
                f"{wf.source_path}"
            )

    def _effective_active(wf) -> bool:
        # Resolve the actual imported-active state so the warning only fires for
        # workflows that will really be active. ``fromJson`` defers to the file's
        # own ``active`` flag, so a fromJson workflow whose file is inactive is
        # NOT a live-activation case and must not trigger the no-key warning.
        if wf.active == "true":
            return True
        if wf.active == "false":
            return False
        try:
            return bool(json.loads(wf.source_path.read_text(encoding="utf-8")).get("active"))
        except (OSError, ValueError):
            return False

    env_values = starter.config_parser.parse_env_file()
    has_api_key = bool(env_values.get("N8N_API_KEY", "").strip())
    if not has_api_key:
        needs_live = [
            wf.id
            for wf in config.n8n_workflows
            if wf.webhooks and _effective_active(wf)
        ]
        if needs_live:
            warnings.append(
                "N8N_API_KEY is unset — active workflows with declared webhooks "
                f"({', '.join(needs_live)}) register their production webhook only "
                "after an n8n restart (the seed cannot activate them live)."
            )

    ids = [wf.id for wf in config.n8n_workflows]
    owners = sorted({wf.consumer for wf in config.n8n_workflows})
    if warnings:
        return _doctor_result(
            "n8n-workflows",
            "warn",
            warnings[0],
            details={"warnings": warnings, "workflows": ids, "owners": owners},
        )
    return _doctor_result(
        "n8n-workflows",
        "pass",
        f"{len(ids)} consumer n8n workflow(s) valid: {', '.join(ids)}.",
        details={"workflows": ids, "owners": owners},
    )


def _doctor_check_rag_ingestion_profiles(starter: "AtlasStarter") -> dict:
    """Validate consumer-declared RAG ingestion profiles to register (#413).

    Load-time already validated the profiles (unique names, corpus path safety,
    parser/chunker/target schema, collection collisions); a parse failure surfaces
    as a fail here. This check adds an operational signal: a profile whose vector
    or graph target has ``on_unavailable: fail`` but whose backend endpoint is
    unset in .env will hard-fail that ingestion at runtime — surface it now.
    """
    try:
        config = starter.config_parser.load_consumer_config()
    except ValueError as exc:
        return _doctor_result(
            "rag-ingestion-profiles",
            "fail",
            f"Consumer RAG ingestion profile validation failed: {exc}",
        )
    if not config.rag_ingestion_profiles:
        return _doctor_result(
            "rag-ingestion-profiles",
            "pass",
            "No consumer RAG ingestion profiles declared.",
        )

    env_values = starter.config_parser.parse_env_file()
    # Endpoint env var that gates each target backend (empty = disabled).
    endpoint_var = {"weaviate": "WEAVIATE_URL", "lightrag": "LIGHTRAG_ENDPOINT"}
    warnings: list[str] = []
    for profile in config.rag_ingestion_profiles:
        targets = [
            (t.backend, t.on_unavailable) for t in profile.vector_targets
        ] + [(t.backend, t.on_unavailable) for t in profile.graph_targets]
        for backend, on_unavailable in targets:
            var = endpoint_var.get(backend)
            enabled = bool(env_values.get(var, "").strip()) if var else True
            if on_unavailable == "fail" and not enabled:
                warnings.append(
                    f"profile {profile.name!r} target {backend} is on_unavailable=fail "
                    f"but {var} is unset — ingestion will hard-fail until it is enabled."
                )

    names = [p.name for p in config.rag_ingestion_profiles]
    owners = sorted({p.consumer for p in config.rag_ingestion_profiles})
    if warnings:
        return _doctor_result(
            "rag-ingestion-profiles",
            "warn",
            warnings[0],
            details={"warnings": warnings, "profiles": names, "owners": owners},
        )
    return _doctor_result(
        "rag-ingestion-profiles",
        "pass",
        f"{len(names)} consumer RAG ingestion profile(s) valid: {', '.join(names)}.",
        details={"profiles": names, "owners": owners},
    )


def _doctor_check_lightrag_query_profiles(starter: "AtlasStarter") -> dict:
    """Validate consumer-declared LightRAG query profiles to register (#414).

    Load-time already validated each profile (unique namespaced names, supported
    mode, bounded positive integers, rerank rejection, alias contract); a parse
    failure surfaces as a fail here. This check adds an operational signal: a
    profile can only be *served* when LightRAG itself is reachable, so warn when
    profiles are declared but ``LIGHTRAG_ENDPOINT`` is unset (the registry mounts
    fine, but every flavor would 5xx at query time until LightRAG is enabled).
    """
    try:
        config = starter.config_parser.load_consumer_config()
    except ValueError as exc:
        return _doctor_result(
            "lightrag-query-profiles",
            "fail",
            f"Consumer LightRAG query profile validation failed: {exc}",
        )
    if not config.lightrag_query_profiles:
        return _doctor_result(
            "lightrag-query-profiles",
            "pass",
            "No consumer LightRAG query profiles declared.",
        )

    names = [p.name for p in config.lightrag_query_profiles]
    owners = sorted({p.consumer for p in config.lightrag_query_profiles})
    aliases = sorted(
        {p.litellm_alias for p in config.lightrag_query_profiles if p.litellm_alias}
    )
    env_values = starter.config_parser.parse_env_file()
    lightrag_enabled = bool(env_values.get("LIGHTRAG_ENDPOINT", "").strip())
    if not lightrag_enabled:
        return _doctor_result(
            "lightrag-query-profiles",
            "warn",
            f"{len(names)} LightRAG query profile(s) declared but LIGHTRAG_ENDPOINT is "
            f"unset — the registry mounts but flavors cannot be served until LightRAG "
            f"is enabled.",
            details={"profiles": names, "owners": owners, "aliases": aliases},
        )
    return _doctor_result(
        "lightrag-query-profiles",
        "pass",
        f"{len(names)} consumer LightRAG query profile(s) valid: {', '.join(names)}.",
        details={"profiles": names, "owners": owners, "aliases": aliases},
    )


def _doctor_check_lightrag_rerank_adapter(starter: "AtlasStarter") -> dict:
    """Operational signal for the LightRAG → TEI rerank adapter (#415).

    The adapter route always exists on the backend, but it only reranks when
    LightRAG is wired to it. Wiring happens only when the operator opts in
    (``LIGHTRAG_RERANK_ADAPTER_ENABLED=true``) AND both LightRAG and the TEI
    reranker are enabled. Warn on the mismatch — flag on but a prerequisite
    service off — because reranking would silently be a no-op (RERANK_BINDING
    stays ``null``).
    """
    env_values = starter.config_parser.parse_env_file()
    flag = (env_values.get("LIGHTRAG_RERANK_ADAPTER_ENABLED", "") or "").strip().lower() == "true"
    if not flag:
        return _doctor_result(
            "lightrag-rerank-adapter",
            "pass",
            "LightRAG rerank adapter disabled (default) — LightRAG will not rerank through TEI.",
        )
    lightrag_enabled = (env_values.get("LIGHTRAG_SOURCE", "disabled") or "disabled").strip() != "disabled"
    tei_enabled = (env_values.get("TEI_RERANKER_SOURCE", "disabled") or "disabled").strip() != "disabled"
    missing = []
    if not lightrag_enabled:
        missing.append("LIGHTRAG_SOURCE")
    if not tei_enabled:
        missing.append("TEI_RERANKER_SOURCE")
    if missing:
        return _doctor_result(
            "lightrag-rerank-adapter",
            "warn",
            "LIGHTRAG_RERANK_ADAPTER_ENABLED=true but "
            f"{' and '.join(missing)} is disabled — reranking will be a no-op "
            "until both LightRAG and the TEI reranker are enabled.",
            details={"missing": missing},
        )
    if not (env_values.get("LIGHTRAG_RERANK_ADAPTER_TOKEN", "") or "").strip():
        return _doctor_result(
            "lightrag-rerank-adapter",
            "warn",
            "LightRAG rerank adapter enabled but LIGHTRAG_RERANK_ADAPTER_TOKEN is empty — "
            "the /lightrag/rerank route will 503 until a token is generated (re-run start).",
        )
    return _doctor_result(
        "lightrag-rerank-adapter",
        "pass",
        "LightRAG rerank adapter enabled and wired to TEI through the backend route.",
    )


def _doctor_check_endpoints(starter: "AtlasStarter") -> dict:
    env_values = starter.config_parser.parse_env_file()
    endpoints = {
        "COMFYUI_ENDPOINT": env_values.get("COMFYUI_ENDPOINT", ""),
        "LITELLM_URL": env_values.get("LITELLM_URL", ""),
        "MINIO_ENDPOINT": env_values.get("MINIO_ENDPOINT", ""),
        "MINIO_BROWSER_REDIRECT_URL": env_values.get("MINIO_BROWSER_REDIRECT_URL", ""),
    }
    visible = {key: value for key, value in endpoints.items() if value}
    if not visible:
        return _doctor_result(
            "endpoints",
            "warn",
            "No consumer endpoint values are currently resolved in .env.",
            details={"endpoints": endpoints},
        )
    message = ", ".join(f"{key}={value}" for key, value in visible.items())
    return _doctor_result(
        "endpoints",
        "pass",
        message,
        details={"endpoints": endpoints},
    )


def _doctor_check_submodule_clean(starter: "AtlasStarter") -> dict:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=starter.config_parser.root_dir,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return _doctor_result(
            "submodule-cleanliness",
            "skipped",
            f"Could not inspect git status: {exc}",
        )
    if result.returncode != 0:
        return _doctor_result(
            "submodule-cleanliness",
            "skipped",
            result.stderr.strip() or "Not a git checkout.",
        )
    dirty = [line for line in result.stdout.splitlines() if line.strip()]
    if dirty:
        return _doctor_result(
            "submodule-cleanliness",
            "fail",
            f"{len(dirty)} tracked Atlas file(s) are dirty.",
            details={"dirty": dirty[:20]},
        )
    return _doctor_result(
        "submodule-cleanliness",
        "pass",
        "No tracked Atlas files are dirty.",
    )


def _doctor_check_comfyui_mps(starter: "AtlasStarter") -> dict:
    """Preflight the Atlas-managed Apple-Silicon/Metal ComfyUI host (#335).

    Only meaningful when COMFYUI_SOURCE=managed-localhost-mps. The preflight is
    read-only (no install, no launch) and CI-safe: on a non-Darwin/arm64 host it
    reports ``fail`` (the source can't run here) with an actionable message; when
    the source isn't selected the check is skipped entirely.
    """
    env_values = starter.config_parser.parse_env_file()
    if str(env_values.get("COMFYUI_SOURCE", "")).strip() != "managed-localhost-mps":
        return _doctor_result(
            "comfyui-mps",
            "skipped",
            "COMFYUI_SOURCE is not managed-localhost-mps.",
        )

    from services.comfyui_mps_manager import manager_from_env

    manager = manager_from_env(env_values)
    pre = manager.preflight()
    fails = [c for c in pre.checks if c["status"] == "fail"]
    warns = [c for c in pre.checks if c["status"] == "warn"]
    if fails:
        return _doctor_result(
            "comfyui-mps",
            "fail",
            "; ".join(f"{c['name']}: {c['detail']}" for c in fails),
            details=pre.to_dict(),
        )
    status = manager.status()
    if warns:
        return _doctor_result(
            "comfyui-mps",
            "warn",
            warns[0]["detail"],
            details={**pre.to_dict(), "running": status.running, "pid": status.pid},
        )
    running = "running" if status.running else "not started"
    return _doctor_result(
        "comfyui-mps",
        "pass",
        f"Managed MPS host preflight passed (host process {running}; "
        f"port {manager.port}, ref {manager.ref}).",
        details={**pre.to_dict(), "running": status.running, "pid": status.pid},
    )


def _doctor_check_vllm_metal(starter: "AtlasStarter") -> dict:
    """Preflight the Atlas-managed vLLM Metal host (#379).

    Only meaningful when VLLM_METAL_SOURCE=managed-localhost. The preflight is
    read-only (no install, no launch) and CI-safe: on a non-Darwin/arm64 host it
    reports ``fail`` (the source can't run here) with an actionable message; when
    the source isn't selected the check is skipped entirely.
    """
    env_values = starter.config_parser.parse_env_file()
    if str(env_values.get("VLLM_METAL_SOURCE", "")).strip() != "managed-localhost":
        return _doctor_result(
            "vllm-metal",
            "skipped",
            "VLLM_METAL_SOURCE is not managed-localhost.",
        )

    from services.vllm_metal_manager import manager_from_env

    manager = manager_from_env(env_values)
    pre = manager.preflight()
    fails = [c for c in pre.checks if c["status"] == "fail"]
    warns = [c for c in pre.checks if c["status"] == "warn"]
    if fails:
        return _doctor_result(
            "vllm-metal",
            "fail",
            "; ".join(f"{c['name']}: {c['detail']}" for c in fails),
            details=pre.to_dict(),
        )
    status = manager.status()
    if warns:
        return _doctor_result(
            "vllm-metal",
            "warn",
            warns[0]["detail"],
            details={**pre.to_dict(), "running": status.running, "pid": status.pid},
        )
    running = "running" if status.running else "not started"
    return _doctor_result(
        "vllm-metal",
        "pass",
        f"Managed vLLM Metal host preflight passed (host process {running}; "
        f"port {manager.port}, model {manager.model}, "
        f"plugin vllm-metal=={manager.plugin_version}).",
        details={**pre.to_dict(), "running": status.running, "pid": status.pid},
    )


def _doctor_check_base_port(starter: "AtlasStarter") -> dict:
    """Warn when a consumer stack squats the default BASE_PORT (63000).

    ``project_name`` isolates Docker resource names (container/volume/network)
    but NOT host port bindings, so a consumer that keeps the default BASE_PORT
    while running under a non-default project collides with a bare atlas
    checkout on the same host. Use ``--base-port auto`` or a distinct block.
    """
    try:
        env = starter.config_parser.parse_env_file()
    except Exception as exc:  # pragma: no cover - defensive
        return _doctor_result("base-port", "skipped", f"Could not read .env: {exc}")
    raw = (env.get("BASE_PORT", "") or "").strip()
    try:
        base_port = int(raw) if raw else DEFAULT_BASE_PORT
    except ValueError:
        base_port = DEFAULT_BASE_PORT
    project = starter.config_parser.get_project_name()
    details = {"base_port": base_port, "project_name": project}
    if base_port == DEFAULT_BASE_PORT and project != DEFAULT_PROJECT_NAME:
        return _doctor_result(
            "base-port",
            "warn",
            f"Consumer project '{project}' is on the default BASE_PORT "
            f"{DEFAULT_BASE_PORT}. project_name isolates Docker resources but not "
            f"host ports, so this stack collides with a bare atlas checkout on the "
            f"same host. Use '--base-port auto' or pin a non-default BASE_PORT.",
            details=details,
        )
    return _doctor_result(
        "base-port",
        "pass",
        f"BASE_PORT {base_port} / project '{project}' — no default-port squat.",
        details=details,
    )


def _doctor_check_auto_sources(starter: "AtlasStarter") -> dict:
    """Report how each consumer ``<SVC>_SOURCE: auto`` declaration resolved
    (#753) — the concrete id now in ``.env`` plus the host capability that
    matches, so "why did it pick this?" is answerable without reading code.
    Analogous to the base-port lint for ``BASE_PORT: auto`` (#751)."""
    try:
        consumer_config = starter.config_parser.load_consumer_config()
        env = starter.config_parser.parse_env_file()
    except Exception as exc:  # pragma: no cover - defensive
        return _doctor_result("auto-sources", "skipped", f"Could not read config: {exc}")

    declared = sorted(
        key
        for key, value in (consumer_config.env_overrides or {}).items()
        if key != "BASE_PORT" and str(value).strip().lower() == "auto"
    )
    if not declared:
        return _doctor_result(
            "auto-sources", "pass", "No <SVC>_SOURCE: auto declarations."
        )

    from services.host_capabilities import probe_host_capabilities
    from services.manifests import load_manifests

    capabilities = probe_host_capabilities()
    matched_caps = [c for c in ("apple_silicon", "nvidia_gpu", "host_ollama") if capabilities.has(c)]
    by_source_var = {
        m.sources.var: m
        for m in load_manifests(starter.root_dir / "services")
        if m.sources is not None
    }

    details: dict = {"host_capabilities": matched_caps}
    unresolved: list[str] = []
    lines: list[str] = []
    for key in declared:
        resolved = (env.get(key, "") or "").strip()
        manifest = by_source_var.get(key)
        if manifest is None:
            unresolved.append(f"{key}: no manifest sources block")
            continue
        if not resolved or resolved.lower() == "auto":
            unresolved.append(f"{key}: not yet resolved (run start or preflight)")
            continue
        cap = next(
            (
                p.requires_capability or "terminal fallback"
                for p in manifest.sources.auto_prefer
                if p.id == resolved
            ),
            "explicit/default",
        )
        details[key] = {"resolved": resolved, "matched": cap}
        lines.append(f"{key}=auto → {resolved} ({cap})")

    if unresolved:
        return _doctor_result(
            "auto-sources",
            "warn",
            "; ".join(unresolved + lines),
            details=details,
        )
    return _doctor_result("auto-sources", "pass", "; ".join(lines), details=details)


def _doctor_check_profile(starter: "AtlasStarter") -> dict:
    """Report the resolved deployment-profile environment (#755): the active
    profile (consumer `profile:` default unless a --profile flag overrides at
    launch), the effective bundle after consumer `profile_overrides:`, and the
    precedence tier each managed value currently comes from."""
    from services.profiles import (
        ProfileConfigError,
        canonical_profile,
        load_profile_bundles,
        merge_consumer_profile_overrides,
    )

    try:
        consumer_config = starter.config_parser.load_consumer_config()
        env = starter.config_parser.parse_env_file()
    except Exception as exc:  # pragma: no cover - defensive
        return _doctor_result("profile", "skipped", f"Could not read config: {exc}")
    try:
        bundles = merge_consumer_profile_overrides(
            load_profile_bundles(),
            getattr(consumer_config, "profile_overrides", None) or {},
        )
    except ProfileConfigError as exc:
        return _doctor_result("profile", "fail", str(exc))

    declared = getattr(consumer_config, "profile", None)
    active = canonical_profile(declared)
    applied = (env.get("ATLAS_PROFILE_APPLIED", "") or "").strip()
    bundle = bundles.get(active)

    from services.manifests import load_manifests

    by_name = {
        m.name: m
        for m in load_manifests(starter.root_dir / "services")
        if m.sources is not None
    }
    fields: dict = {}
    if bundle is not None:
        if bundle.host_bind_ip is not None:
            current = env.get("HOST_BIND_IP", "")
            fields["HOST_BIND_IP"] = {
                "profile_value": bundle.host_bind_ip,
                "current": current,
                "tier": "profile" if current == bundle.host_bind_ip else "operator",
            }
        for svc, sid in bundle.sources.items():
            manifest = by_name.get(svc)
            if manifest is None:
                continue
            var = manifest.sources.var
            current = (env.get(var, "") or "").strip()
            tier = (
                "profile"
                if (sid != "auto" and current == sid)
                else ("auto" if sid == "auto" else "operator")
            )
            fields[var] = {"profile_value": sid, "current": current, "tier": tier}
        for var, value in bundle.env.items():
            current = env.get(var, "")
            fields[var] = {
                "profile_value": value,
                "current": current,
                "tier": "profile" if current == value else "operator",
            }

    details = {
        "declared_default": declared or "(none — implicit default)",
        "active": active,
        "last_applied": applied or "(never)",
        "fields": fields,
    }
    return _doctor_result(
        "profile",
        "pass",
        f"profile={active} (declared: {declared or 'none'}; last applied: "
        f"{applied or 'never'}); {len(fields)} managed value(s).",
        details=details,
    )


def _doctor_check_blender_mcp(starter: "AtlasStarter") -> dict:
    """Preflight the managed Blender MCP bridge (#759) when selected."""
    env = starter.config_parser.parse_env_file()
    if (env.get("BLENDER_MCP_SOURCE", "") or "").strip() != "managed-localhost":
        return _doctor_result(
            "blender-mcp", "pass", "BLENDER_MCP_SOURCE is not managed-localhost."
        )
    from services.blender_mcp_manager import manager_from_env

    try:
        result = manager_from_env(env).preflight()
    except Exception as exc:  # pragma: no cover - defensive
        return _doctor_result("blender-mcp", "skipped", f"Could not preflight: {exc}")
    status = {"ok": "pass", "warn": "warn", "fail": "fail"}.get(result.status, "warn")
    summary = "; ".join(f"{c['name']}: {c['detail']}" for c in result.checks)
    return _doctor_result("blender-mcp", status, summary, details=result.to_dict())


DOCTOR_CHECKS = [
    _doctor_check_consumer_manifests,
    _doctor_check_base_port,
    _doctor_check_auto_sources,
    _doctor_check_profile,
    _doctor_check_compose,
    _doctor_check_overlay_env,
    _doctor_check_plugins,
    _doctor_check_plugin_manifests,
    _doctor_check_model_sidecars,
    _doctor_check_unpullable_models,
    _doctor_check_litellm_models,
    _doctor_check_n8n_workflows,
    _doctor_check_rag_ingestion_profiles,
    _doctor_check_lightrag_query_profiles,
    _doctor_check_lightrag_rerank_adapter,
    _doctor_check_comfyui_mps,
    _doctor_check_vllm_metal,
    _doctor_check_blender_mcp,
    _doctor_check_endpoints,
    _doctor_check_submodule_clean,
]


def _run_consumer_doctor(starter: "AtlasStarter") -> list[dict]:
    return [check(starter) for check in DOCTOR_CHECKS]


def _print_doctor_text(results: list[dict]) -> None:
    click.echo("Consumer Doctor")
    for result in results:
        status = result["status"].upper()
        click.echo(f"[{status}] {result['id']}: {result['message']}")


def _parse_base_port_option(ctx, param, value):
    """Accept an integer or the literal ``auto`` for --base-port. ``auto`` is
    resolved to a concrete free block in ``main`` (needs the topology + live
    port scan), so here it's passed through as the sentinel string."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "auto":
        return "auto"
    try:
        return int(text)
    except (TypeError, ValueError):
        raise click.BadParameter("must be an integer or 'auto'")


@click.group(invoke_without_command=True)
@click.option('--project', '-p', 'project_name', type=str, default=None,
              help='Docker Compose project name — the container-family namespace '
                   '(every container/volume/network is prefixed <name>-…). Persists '
                   'to .env as PROJECT_NAME, so a later ./stop.sh tears down exactly '
                   'this stack. Defaults to PROJECT_NAME in .env (or "atlas"). Set it '
                   'when running Atlas as a submodule so you do not collide with a '
                   'base Atlas stack.')
@click.option('--consumer', 'consumer_manifests', multiple=True,
              type=click.Path(exists=False, dir_okay=False),
              help='Path to an atlas.consumer.yml manifest in a parent project. '
                   'May be passed multiple times. Relative paths resolve from '
                   'the directory that invoked start.sh.')
@click.option('--base-port', type=str, callback=_parse_base_port_option,
              help=f'Base port for all services (default: {DEFAULT_BASE_PORT}). '
                   f'Use "auto" to select the first wholly-free BASE_PORT block — '
                   f'recommended for submodule consumers so the stack never squats '
                   f'the default {DEFAULT_BASE_PORT} a bare atlas checkout binds.')
@click.option('--cold', is_flag=True, help='Perform cold start with cleanup')
@click.option('--setup-hosts', is_flag=True, help='Setup hosts file entries (requires admin/sudo)')
@click.option('--skip-hosts', is_flag=True, help='Skip hosts file checks and setup')
@click.option('--track', type=str, default=None,
              help='Pre-select a wizard profile (track) — gen-ai-rag, '
                   'gen-ai-eng, gen-ai-creative, ml-eng, data-eng, trading, all. '
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
@click.option('--fal-api-key', type=str, default=None,
              help='fal.ai API key. Persists to .env as FAL_API_KEY and implies --fal-source=enabled.')
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
                                'managed-localhost-mps', 'disabled'], case_sensitive=False),
              help='Override COMFYUI_SOURCE')
@click.option('--asset-worker-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override ASSET_WORKER_SOURCE — glTF post-processing worker.')
@click.option('--asset-baker-source',
              type=click.Choice(['container-cpu', 'disabled'], case_sensitive=False),
              help='Override ASSET_BAKER_SOURCE — Blender headless HP→LP bake worker.')
@click.option('--fal-source',
              type=click.Choice(['enabled', 'disabled'], case_sensitive=False),
              help='Override FAL_SOURCE — cloud media provider for backend generation routes.')
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
@click.option('--tika-source',
              type=click.Choice(['container', 'tika-localhost', 'disabled'], case_sensitive=False),
              help='Override TIKA_SOURCE — Apache Tika fallback extractor.')
@click.option('--llm-graph-builder-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override LLM_GRAPH_BUILDER_SOURCE — Neo4j Labs document-to-graph builder UI/API.')
@click.option('--celery-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override CELERY_SOURCE — backend async worker tier with Flower monitor.')
@click.option('--supavisor-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override SUPAVISOR_SOURCE — internal Supabase Postgres transaction pooler.')
@click.option('--mcp-servers-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override MCP_SERVERS_SOURCE')
@click.option('--blender-mcp-source',
              type=click.Choice(['localhost', 'managed-localhost', 'disabled'], case_sensitive=False),
              help='Override BLENDER_MCP_SOURCE — host Blender MCP bridge: '
                   'localhost (user-run GUI) or managed-localhost '
                   '(Atlas-provisioned headless, #759).')
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
@click.option('--vllm-metal-source',
              type=click.Choice(['managed-localhost', 'disabled'],
                                case_sensitive=False),
              help='Override VLLM_METAL_SOURCE (Apple-silicon Metal managed host)')
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
@click.option('--otel-collector-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override OTEL_COLLECTOR_SOURCE — internal OpenTelemetry ingest.')
@click.option('--tempo-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override TEMPO_SOURCE — local trace store for Grafana.')
@click.option('--loki-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override LOKI_SOURCE — local log store for Grafana.')
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
@click.option('--label-studio-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override LABEL_STUDIO_SOURCE — annotation review UI with MinIO media storage.')
@click.option('--verba-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override VERBA_SOURCE — archived Weaviate RAG demo UI.')
@click.option('--airflow-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override AIRFLOW_SOURCE — code-defined DAG orchestrator (LocalExecutor + LLM operators).')
@click.option('--iceberg-rest-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override ICEBERG_REST_SOURCE — durable Iceberg REST catalog over MinIO.')
@click.option('--trino-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override TRINO_SOURCE — SQL query engine over the Iceberg lakehouse.')
@click.option('--redpanda-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override REDPANDA_SOURCE — Kafka API streaming broker + console.')
@click.option('--backup-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override BACKUP_SOURCE — authorize the on-demand backup/restore runner.')
@click.option('--cloudflared-source',
              type=click.Choice(['container', 'disabled'], case_sensitive=False),
              help='Override CLOUDFLARED_SOURCE — outbound Cloudflare Tunnel public edge.')
@click.option('--no-tui', is_flag=True,
              help='Disable the TUI (wizard + Textual log app). Falls back to the legacy '
                   'linear flow with passthrough docker output. Useful for log capture, '
                   'debugging, and terminals that don\'t support the alternate screen buffer.')
@click.option('--detach', '--no-follow', 'detach', is_flag=True, default=False,
              help='Run the start pipeline, wait for compose health gates, print a final '
                   'status summary, and exit instead of following docker logs.')
@click.option('--json', 'json_output', is_flag=True, default=False,
              help='With --detach/--no-follow, emit the final status summary as JSON for automation.')
@click.option('--no-splash', is_flag=True, default=False,
              help='Disable the opening splash animation in the wizard.')
@click.option('--no-port-migrate', is_flag=True, default=False,
              help='Skip the chained .env migrations (port-layout v1, URL→PORT v2, '
                   'model-set v3) for this run. Version sentinels are NOT stamped, '
                   'so the migration re-prompts on the next run.')
@click.option('--profile',
              type=click.Choice(['default', 'dev', 'prod'], case_sensitive=False),
              help='Deployment profile (declarative bundles in '
                   'bootstrapper/profiles.yml; "dev" aliases "default"). '
                   '"prod": bind all service ports to 127.0.0.1 (public edge '
                   'fronts Kong), enable log rotation, default observability '
                   'ON, and hide dev-only (localhost) sources. Unset: the '
                   'consumer manifest may name its default via `profile:`. '
                   'Does not bypass the wizard.')
@click.pass_context
def main(ctx, project_name, consumer_manifests, base_port, track, list_tracks, cold, setup_hosts, skip_hosts, llm_provider_source,
         cloud_openai_source, cloud_anthropic_source, cloud_openrouter_source,
         openai_api_key, anthropic_api_key, openrouter_api_key, fal_api_key,
         openai_models, anthropic_models, openrouter_models,
         ollama_models, ollama_custom_models,
         comfyui_models, comfyui_custom_models_file,
         comfyui_source, asset_worker_source, asset_baker_source, fal_source, weaviate_source, minio_source, n8n_source, searxng_source,
         crawl4ai_source, tika_source, llm_graph_builder_source,
         celery_source, supavisor_source, mcp_servers_source, blender_mcp_source,
         jupyterhub_source, open_web_ui_source, local_deep_researcher_source,
         stt_provider_source, tts_provider_source,
         doc_processor_source, openclaw_source, hermes_source,
         lightrag_source, tei_reranker_source,
         vllm_metal_source,
         neo4j_graph_db_source,
         multi2vec_clip_source,
         ray_source, ray_worker_count,
         prometheus_source, prometheus_retention_days, grafana_source,
         langfuse_source,
         otel_collector_source, tempo_source, loki_source,
         spark_source, spark_workers,
         zeppelin_source,
         jenkins_source,
         mlflow_source,
         label_studio_source,
         verba_source,
         airflow_source,
         iceberg_rest_source,
         trino_source,
         redpanda_source,
         backup_source,
         cloudflared_source,
         no_tui, detach, json_output, no_splash, no_port_migrate, profile):
    """Start Atlas — the self-hosted engineering platform."""

    if consumer_manifests:
        previous_consumer_manifest = os.environ.get("ATLAS_CONSUMER_MANIFEST")
        os.environ["ATLAS_CONSUMER_MANIFEST"] = os.pathsep.join(consumer_manifests)

        def _restore_consumer_manifest_env() -> None:
            if previous_consumer_manifest is None:
                os.environ.pop("ATLAS_CONSUMER_MANIFEST", None)
            else:
                os.environ["ATLAS_CONSUMER_MANIFEST"] = previous_consumer_manifest

        ctx.call_on_close(_restore_consumer_manifest_env)

    if ctx.invoked_subcommand is not None:
        return

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

    # ─── Base port (--base-port auto) ────────────────────────────────
    # Resolve the 'auto' sentinel to a concrete free BASE_PORT block up front
    # so every downstream path (Textual + linear + setup_env_file) sees a plain
    # int. auto scans below the ephemeral range and never returns the default,
    # so a submodule consumer can't silently squat the port a bare atlas binds.
    if base_port == "auto":
        from core.port_manager import PortManager
        chosen = PortManager().auto_base_port()
        if chosen is None:
            click.echo(
                "start.sh: --base-port auto could not find a wholly-free port "
                "block; pass an explicit --base-port.", err=True)
            raise SystemExit(2)
        click.echo(f"start.sh: --base-port auto selected {chosen}.")
        base_port = chosen

    # JSON output is only useful for automation, so make it imply the
    # non-following detached path even if the caller omitted --detach.
    if json_output:
        detach = True

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
                    'asset_worker_source': asset_worker_source,
                    'asset_baker_source': asset_baker_source,
                    'fal_source': fal_source,
                    'weaviate_source': weaviate_source,
                    'minio_source': minio_source,
                    'n8n_source': n8n_source,
                    'searxng_source': searxng_source,
                    'crawl4ai_source': crawl4ai_source,
                    'tika_source': tika_source,
                    'llm_graph_builder_source': llm_graph_builder_source,
                    'celery_source': celery_source,
                    'supavisor_source': supavisor_source,
                    'mcp_servers_source': mcp_servers_source,
                    'blender_mcp_source': blender_mcp_source,
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
                    'vllm_metal_source': vllm_metal_source,
                    'neo4j_graph_db_source': neo4j_graph_db_source,
                    'multi2vec_clip_source': multi2vec_clip_source,
                    'ray_source': ray_source,
                    'prometheus_source': prometheus_source,
                    'grafana_source': grafana_source,
                    'langfuse_source': langfuse_source,
                    'otel_collector_source': otel_collector_source,
                    'tempo_source': tempo_source,
                    'loki_source': loki_source,
                    'spark_source': spark_source,
                    'zeppelin_source': zeppelin_source,
                    'jenkins_source': jenkins_source,
                    'mlflow_source': mlflow_source,
                    'label_studio_source': label_studio_source,
                    'verba_source': verba_source,
                    'airflow_source': airflow_source,
                    'iceberg_rest_source': iceberg_rest_source,
                    'trino_source': trino_source,
                    'redpanda_source': redpanda_source,
                    'backup_source': backup_source,
                    'cloudflared_source': cloudflared_source,
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
        # Resolve the deployment profile: explicit --profile wins; else the
        # consumer manifest's `profile:` default (#755); else "default".
        # `dev` is an alias for `default` (services/profiles.py).
        from services.profiles import canonical_profile as _canonical_profile

        if profile is None:
            try:
                profile = starter.config_parser.load_consumer_config().profile
            except Exception:  # noqa: BLE001 — a malformed manifest surfaces
                # through the consumer-manifests doctor check, not here.
                profile = None
        if profile is not None:
            # Canonicalize (dev → default) but PRESERVE None: a bare
            # ./start.sh with no manifest default must keep the wizard's
            # profile-picker step (which keys off profile is None).
            profile = _canonical_profile(profile)
        # Make the resolved profile visible to the <SVC>_SOURCE:auto resolver
        # on EVERY path (wizard + TUI-launch call prepare_environment before
        # the wizard pipeline would otherwise set starter.profile) — a
        # profile-blind resolution can durably pin a dev-only source that
        # prod validation then rejects on every start.
        starter.profile = profile or "default"

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
        if fal_api_key is not None:
            cloud_api_keys['FAL_API_KEY'] = fal_api_key
            if fal_source is None:
                fal_source = 'enabled'

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
            'asset_worker_source': asset_worker_source,
            'asset_baker_source': asset_baker_source,
            'fal_source': fal_source,
            'weaviate_source': weaviate_source,
            'minio_source': minio_source,
            'n8n_source': n8n_source,
            'searxng_source': searxng_source,
            'crawl4ai_source': crawl4ai_source,
            'tika_source': tika_source,
            'llm_graph_builder_source': llm_graph_builder_source,
            'celery_source': celery_source,
            'supavisor_source': supavisor_source,
            'mcp_servers_source': mcp_servers_source,
            'blender_mcp_source': blender_mcp_source,
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
            'vllm_metal_source': vllm_metal_source,
            'neo4j_graph_db_source': neo4j_graph_db_source,
            'multi2vec_clip_source': multi2vec_clip_source,
            'ray_source': ray_source,
            'prometheus_source': prometheus_source,
            'grafana_source': grafana_source,
            'langfuse_source': langfuse_source,
            'otel_collector_source': otel_collector_source,
            'tempo_source': tempo_source,
            'loki_source': loki_source,
            'spark_source': spark_source,
            'zeppelin_source': zeppelin_source,
            'jenkins_source': jenkins_source,
            'mlflow_source': mlflow_source,
            'label_studio_source': label_studio_source,
            'verba_source': verba_source,
            'airflow_source': airflow_source,
            'iceberg_rest_source': iceberg_rest_source,
            'trino_source': trino_source,
            'redpanda_source': redpanda_source,
            'backup_source': backup_source,
            'cloudflared_source': cloudflared_source,
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
            if not 1 <= prometheus_retention_days <= 365:
                raise click.UsageError(
                    "--prometheus-retention-days must be in 1-365"
                )
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
        no_stack_flags = (
            base_port is None
            and not cold
            and not setup_hosts
            and not skip_hosts
            and not detach
            and not json_output
        )
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
        # #783: SOURCE vars the consumer manifest declares in env.values —
        # declared intent must survive the track force-disable (a manifest's
        # MINIO_SOURCE: container was silently reverted to disabled when the
        # track excluded minio, forcing consumers into workaround flags).
        consumer_declared_source_keys: frozenset = frozenset()
        try:
            _cc = starter.config_parser.load_consumer_config()
            consumer_declared_source_keys = frozenset(
                var.lower()
                for var in (_cc.env_overrides or {})
                if var.endswith("_SOURCE") and var.lower() in source_args
            )
        except Exception:  # noqa: BLE001 — malformed manifests surface via doctor
            pass

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
                    consumer_declared=consumer_declared_source_keys,
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
            if not starter.prepare_environment(cold_start=cold, base_port=base_port, project_name=project_name):
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
                        consumer_declared=consumer_declared_source_keys,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Synthesis is the only thing enforcing the --track
                    # contract on this no-TUI path. If it raises, surface a
                    # stderr warning so the user knows off-track services will
                    # fall back to their .env defaults (i.e. --track did not
                    # take effect) instead of silently continuing.
                    print(
                        f"[warn] track '{track}' force-disable synthesis failed "
                        f"({type(exc).__name__}: {exc}); off-track services will "
                        f"use their .env defaults. Re-run without --track or report this.",
                        file=sys.stderr,
                    )

        # CLI-flag mode + TUI capable: skip the wizard but still use the
        # Textual launch screen, pre-loaded with the user's CLI args.
        # Falls through to the linear stdout flow only when --no-tui or
        # the terminal can't host the TUI. This block must run BEFORE
        # the banner / setup_env_file / apply_source_overrides pipeline
        # below so its output stays out of the terminal and ends up
        # inside the log pane.
        if not no_tui and not detach and not json_output:
            from ui.term_caps import is_tui_capable as _is_tui_capable
            if _is_tui_capable(no_tui_flag=no_tui):
                # Make sure .env exists so the launch screen can build
                # the Stack overview.
                if not starter.prepare_environment(cold_start=cold, base_port=base_port, project_name=project_name):
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
        exit_code = run_linear_startup(
            starter,
            LinearStartupOptions(
                cold=cold,
                base_port=base_port,
                project_name=project_name,
                source_args=source_args,
                profile=profile,
                explicit_prometheus=prometheus_source,
                explicit_grafana=grafana_source,
                cloud_api_keys=cloud_api_keys,
                user_model_selections=user_model_selections,
                no_port_migrate=no_port_migrate,
                setup_hosts=setup_hosts,
                skip_hosts=skip_hosts,
                track=track,
                detach=detach,
                json_output=json_output,
                no_splash=no_splash,
            ),
        )
        sys.exit(exit_code)

    except click.ClickException:
        # Let click render its own usage/parameter errors (e.g. the
        # --spark-workers range check) with their conventional exit code
        # (2 for UsageError) instead of masking them as an "unexpected
        # error" with exit 1 via the catch-all below.
        raise
    except KeyboardInterrupt:
        starter.rollback_managed_host_processes()
        print("\n❌ Startup interrupted by user")
        sys.exit(1)
    except Exception as e:
        starter.rollback_managed_host_processes()
        # Anything reaching here is an unexpected bug (click.ClickException
        # and KeyboardInterrupt are handled above). Emit the full traceback
        # to stderr so the failure is triageable; the prior handler's bare
        # str(e) was not enough to locate defects across the ~620-line body.
        import traceback

        print(f"\n❌ Unexpected error during startup: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


@main.group("env")
def env_group() -> None:
    """Headless environment maintenance commands."""


@env_group.command("backfill")
def env_backfill_command() -> None:
    """Add missing .env keys from .env.example without starting services."""
    starter = AtlasStarter()
    env_path = starter.config_parser.env_file_path
    env_example_path = starter.config_parser.env_example_path
    before = _parse_env_values(env_path)
    if not starter.backfill_missing_env_vars():
        raise click.exceptions.Exit(1)
    after = _parse_env_values(env_path)
    _print_env_backfill_summary(
        env_path=env_path,
        env_example_path=env_example_path,
        before=before,
        after=after,
    )


@main.group("compose")
def compose_group() -> None:
    """Headless Docker Compose maintenance commands."""


@compose_group.command("validate")
def compose_validate_command() -> None:
    """Validate the assembled Compose config, including user overlays."""
    starter = AtlasStarter()
    # Materialize the consumer manifest's derived env (#451) so overlays that
    # interpolate ${BACKEND_PLUGINS_DIR} etc. resolve — mirroring a real start.
    starter.materialize_consumer_env_for_preflight()
    try:
        returncode, stdout, stderr, _cmd = starter.docker_manager.validate_compose_config()
    except (RuntimeError, ValueError) as exc:
        click.echo(f"Compose config validation failed: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc

    if returncode == 0:
        click.echo("Compose config is valid.")
        return

    output = "\n".join(part for part in (stderr.strip(), stdout.strip()) if part)
    click.echo("Compose config validation failed.", err=True)
    summary = _compose_validation_summary(output)
    if summary:
        click.echo(summary, err=True)
    if output:
        click.echo(output, err=True)
    raise click.exceptions.Exit(returncode)


@main.command("doctor")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format for consumer CI.",
)
def doctor_command(output_format: str) -> None:
    """Run headless consumer preflight checks without starting services."""
    starter = AtlasStarter()
    # Materialize the consumer manifest's derived env (#451) before the checks
    # (which validate the assembled compose) so ${BACKEND_PLUGINS_DIR}-style
    # overlays resolve on a fresh checkout. Quiet — keeps --format json clean.
    starter.materialize_consumer_env_for_preflight()
    results = _run_consumer_doctor(starter)
    ok = not any(result["status"] == "fail" for result in results)
    payload = {"ok": ok, "checks": results}

    if output_format == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_doctor_text(results)

    if not ok:
        raise click.exceptions.Exit(1)


@main.group("endpoints")
def endpoints_group() -> None:
    """Consumer endpoint export commands (stable machine-readable contract)."""


@endpoints_group.command("export")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["env", "json"], case_sensitive=False),
    default="env",
    show_default=True,
    help="Output format for consumers.",
)
@click.option(
    "--with-secrets",
    is_flag=True,
    help="Resolve consumer-scoped credentials (storage keys). Requires --output; "
    "refused to stdout. Never resolves infra secrets (e.g. the Redis password).",
)
@click.option(
    "--output",
    "output_path",
    type=str,
    default=None,
    help="Write to PATH instead of stdout (required with --with-secrets).",
)
def endpoints_export_command(
    output_format: str, with_secrets: bool, output_path: str | None
) -> None:
    """Export the stable consumer endpoint contract as env or JSON.

    Emits canonical, distinct container/host/Kong/public endpoints and active
    SOURCE modes per consumer-relevant service, plus per-consumer storage
    fields. Secrets are ``${VAR}`` references unless ``--with-secrets`` (which
    resolves only consumer-scoped credentials and refuses stdout). Deterministic
    and byte-stable for the same inputs.
    """
    from core.endpoints_contract import build_export, render_env, render_json

    if with_secrets and not output_path:
        click.echo(
            "Refusing to write secrets to stdout; pass --output PATH with "
            "--with-secrets.",
            err=True,
        )
        raise click.exceptions.Exit(2)

    starter = AtlasStarter()
    env = starter.config_parser.parse_env_file()
    fields = build_export(env, with_secrets=with_secrets)
    text = render_json(fields) if output_format.lower() == "json" else render_env(fields)

    if output_path:
        out = Path(output_path).expanduser()
        _write_private_text(out, text)
        click.echo(f"Wrote {len(fields)} endpoint field(s) to {out}")
    else:
        click.echo(text, nl=False)


@endpoints_group.command("assert")
@click.option(
    "--require",
    "required",
    type=str,
    default=None,
    help="Comma/space-separated export field names that MUST be present for the "
    "current stack; exits non-zero if any is absent. Run this in consumer CI so "
    "an Atlas field rename/removal fails loudly instead of silently degrading.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="With no --require, list the available export field names.",
)
def endpoints_assert_command(required: str | None, output_format: str) -> None:
    """Assert the consumer endpoint contract — a CI drift gate for submodule consumers.

    Consumers pin Atlas as a submodule and read specific ``ATLAS_*`` export
    fields (e.g. ``ATLAS_LITELLM_HOST_ENDPOINT``). Run
    ``./infra/start.sh endpoints assert --require ATLAS_LITELLM_HOST_ENDPOINT,…``
    in consumer CI against the configured stack so a future Atlas rename/removal
    of a depended-on field fails loudly. With no ``--require``, prints the
    available field names (``--format json`` for machine parsing).
    """
    from core.endpoints_contract import build_export

    starter = AtlasStarter()
    try:
        env = starter.config_parser.parse_env_file()
    except Exception:  # pragma: no cover - defensive (no .env yet)
        env = {}
    available = sorted({f.name for f in build_export(env, with_secrets=False)})

    if required:
        want = [f for f in required.replace(",", " ").split() if f]
        missing = [f for f in want if f not in available]
        if missing:
            click.echo(
                "endpoints assert: missing required export field(s): "
                + ", ".join(sorted(missing))
                + f".\nAvailable ({len(available)}): {', '.join(available)}",
                err=True,
            )
            raise click.exceptions.Exit(1)
        click.echo(f"endpoints assert: all {len(want)} required field(s) present.")
        return

    if output_format.lower() == "json":
        click.echo(json.dumps(available))
    else:
        for name in available:
            click.echo(name)


@main.group("comfyui-mps")
def comfyui_mps_group() -> None:
    """Manage the native Apple-Silicon/Metal (MPS) ComfyUI host (#335).

    Docker Desktop can't pass Metal into a Linux container, so
    COMFYUI_SOURCE=managed-localhost-mps runs a native ComfyUI process on the
    host that containers reach via host.docker.internal. These commands preflight,
    install/update the pinned checkout + venv, and start/stop/inspect that process.
    A normal ``./start.sh`` with that source runs install+start automatically; use
    these for explicit lifecycle control or CI-safe preflight.
    """


def _comfyui_mps_manager():
    from services.comfyui_mps_manager import manager_from_env

    starter = AtlasStarter()
    env = starter.config_parser.parse_env_file()
    return manager_from_env(env)


@comfyui_mps_group.command("preflight")
def comfyui_mps_preflight_command() -> None:
    """Run the read-only host probe (OS/arch, memory, Torch/MPS). No install."""
    manager = _comfyui_mps_manager()
    result = manager.preflight()
    for check in result.checks:
        click.echo(f"[{check['status'].upper()}] {check['name']}: {check['detail']}")
    click.echo(f"\nPreflight: {result.status.upper()}")
    if not result.ok:
        raise click.exceptions.Exit(1)


@comfyui_mps_group.command("install")
@click.option("--update", is_flag=True, help="Refresh the checkout + reinstall requirements.")
def comfyui_mps_install_command(update: bool) -> None:
    """Idempotently install (or --update) the pinned ComfyUI checkout + venv."""
    from services.comfyui_mps_manager import ComfyUiMpsError

    manager = _comfyui_mps_manager()
    try:
        manager.install(update=update)
    except ComfyUiMpsError as exc:
        click.echo(f"Install failed: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc
    click.echo(f"Installed ComfyUI {manager.ref} into {manager.state_dir}.")


@comfyui_mps_group.command("start")
def comfyui_mps_start_command() -> None:
    """Launch the managed host process (idempotent — one process per host)."""
    from services.comfyui_mps_manager import ComfyUiMpsError

    manager = _comfyui_mps_manager()
    try:
        status = manager.start()
    except ComfyUiMpsError as exc:
        click.echo(f"Start failed: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc
    click.echo(f"ComfyUI (MPS) running: pid={status.pid} port={status.port}")
    click.echo(f"Logs: {status.log_file}")


@comfyui_mps_group.command("stop")
def comfyui_mps_stop_command() -> None:
    """Stop the managed host process (SIGINT then SIGKILL)."""
    manager = _comfyui_mps_manager()
    stopped = manager.stop()
    click.echo("Stopped." if stopped else "No managed ComfyUI (MPS) process was running.")


@comfyui_mps_group.command("status")
def comfyui_mps_status_command() -> None:
    """Show the managed host process status (running / pid / installed ref)."""
    manager = _comfyui_mps_manager()
    status = manager.status()
    click.echo(json.dumps(status.to_dict(), indent=2))


@comfyui_mps_group.command("health")
def comfyui_mps_health_command() -> None:
    """Probe /system_stats and report reachability + compute device (mps/cpu)."""
    manager = _comfyui_mps_manager()
    health = manager.health()
    click.echo(json.dumps(health, indent=2))
    if not health.get("reachable"):
        raise click.exceptions.Exit(1)


@comfyui_mps_group.command("remove")
@click.confirmation_option(prompt="Stop the process and delete the managed state directory?")
def comfyui_mps_remove_command() -> None:
    """Stop the process and delete the Atlas-owned state directory."""
    manager = _comfyui_mps_manager()
    manager.remove()
    click.echo(f"Removed {manager.state_dir}.")

@comfyui_mps_group.command("provision")
@click.option("--verify", is_flag=True,
              help="Force a full sha256 re-hash of present files instead of "
                   "trusting the cached verification state.")
def comfyui_mps_provision(verify: bool) -> None:
    """Provision the declared ComfyUI model set into the host tree (#754).

    Fetches the resolved COMFYUI_USER_MODELS catalog selection (the same
    per-file set the container init would download) into
    COMFYUI_MPS_MODELS_PATH — idempotent, checksum-verified, resumable.
    Runs automatically on a managed-localhost-mps start; this command is the
    manual re-run for repair or pre-staging."""
    starter = AtlasStarter()
    env = starter.config_parser.parse_env_file()
    from services.comfyui_mps_manager import manager_from_env

    manager = manager_from_env(env)
    rows = _resolved_comfyui_model_rows(env)
    if not rows:
        print("No ComfyUI models declared (COMFYUI_USER_MODELS is empty) — nothing to do.")
        return
    print(f"Provisioning {len(rows)} model file(s) into {manager.models_path} …")
    result = manager.provision_models(rows, verify=verify, log=print)
    for warning in result.warnings:
        print(f"⚠ {warning}")
    print(
        f"provision summary: downloaded={len(result.provisioned)} "
        f"skipped={len(result.skipped)} failed={len(result.failed)}"
    )
    if not result.ok:
        for failure in result.failed:
            print(f"✗ {failure}")
        sys.exit(1)

@main.group("blender-mcp")
def blender_mcp_group() -> None:
    """Manage the Atlas-managed headless Blender + MCP bridge (#759).

    BLENDER_MCP_SOURCE=managed-localhost provisions the pinned blender-mcp
    add-on + a headless launcher and runs `blender --background` with a
    main-thread pump, so MCP composition automation needs no GUI. A normal
    ./start.sh with that source runs install+start automatically; these
    commands are the manual lifecycle. Loopback-bound by default —
    execute_code runs arbitrary Python inside Blender."""


def _blender_mcp_manager():
    starter = AtlasStarter()
    env = starter.config_parser.parse_env_file()
    from services.blender_mcp_manager import manager_from_env

    return manager_from_env(env)


@blender_mcp_group.command("preflight")
def blender_mcp_preflight() -> None:
    """Read-only host probe (Blender binary, bind policy, add-on pin, port)."""
    result = _blender_mcp_manager().preflight()
    print(json.dumps(result.to_dict(), indent=2))
    if not result.ok:
        sys.exit(1)


@blender_mcp_group.command("install")
def blender_mcp_install() -> None:
    """Provision the pinned add-on (sha256-verified) + headless launcher."""
    from services.blender_mcp_manager import BlenderMcpError

    manager = _blender_mcp_manager()
    try:
        manager.install()
    except BlenderMcpError as exc:
        print(f"install failed: {exc}")
        sys.exit(1)
    print(f"provisioned {manager.addon_path} + launcher (ref {manager.addon_ref[:12]})")


@blender_mcp_group.command("start")
def blender_mcp_start() -> None:
    """Launch the headless bridge (installs first if needed)."""
    from services.blender_mcp_manager import BlenderMcpError

    manager = _blender_mcp_manager()
    try:
        manager.install()
        status = manager.start()
    except BlenderMcpError as exc:
        print(f"start failed: {exc}")
        sys.exit(1)
    print(json.dumps(status.to_dict(), indent=2))


@blender_mcp_group.command("stop")
def blender_mcp_stop() -> None:
    """Stop the managed bridge process."""
    print("stopped" if _blender_mcp_manager().stop() else "could not stop (see log)")


@blender_mcp_group.command("status")
def blender_mcp_status() -> None:
    """Pid/port status of the managed bridge."""
    print(json.dumps(_blender_mcp_manager().status().to_dict(), indent=2))


@blender_mcp_group.command("health")
def blender_mcp_health() -> None:
    """Live JSON round-trip (get_scene_info) through the bridge socket."""
    health = _blender_mcp_manager().health()
    print(json.dumps(health, indent=2))
    if not health.get("reachable"):
        sys.exit(1)


@blender_mcp_group.command("remove")
def blender_mcp_remove() -> None:
    """Stop the bridge and delete the state dir (add-on, launcher, logs)."""
    _blender_mcp_manager().remove()
    print("removed")




@main.group("vllm-metal")
def vllm_metal_group() -> None:
    """Manage the native Apple-Silicon/Metal vLLM host (#379).

    Docker Desktop can't pass Metal into a Linux container, so
    VLLM_METAL_SOURCE=managed-localhost runs a native vLLM process on the host
    (via the ``vllm-metal`` plugin) that LiteLLM reaches as an OpenAI-compatible
    upstream at host.docker.internal:<port>/v1. These commands preflight,
    install/update the pinned wheel + venv, and start/stop/inspect that process.
    A normal ``./start.sh`` with that source runs install+start automatically;
    use these for explicit lifecycle control or CI-safe preflight.
    """


def _vllm_metal_manager():
    from services.vllm_metal_manager import manager_from_env

    starter = AtlasStarter()
    env = starter.config_parser.parse_env_file()
    return manager_from_env(env)


@vllm_metal_group.command("preflight")
def vllm_metal_preflight_command() -> None:
    """Run the read-only host probe (OS/arch, Python 3.12, memory, quant). No install."""
    manager = _vllm_metal_manager()
    result = manager.preflight()
    for check in result.checks:
        click.echo(f"[{check['status'].upper()}] {check['name']}: {check['detail']}")
    click.echo(f"\nPreflight: {result.status.upper()}")
    if not result.ok:
        raise click.exceptions.Exit(1)


@vllm_metal_group.command("install")
@click.option("--update", is_flag=True, help="Reinstall/upgrade the pinned vllm-metal wheel.")
def vllm_metal_install_command(update: bool) -> None:
    """Idempotently install (or --update) the pinned vllm-metal wheel + venv."""
    from services.vllm_metal_manager import VllmMetalError

    manager = _vllm_metal_manager()
    try:
        manager.install(update=update)
    except VllmMetalError as exc:
        click.echo(f"Install failed: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc
    click.echo(
        f"Installed vllm-metal=={manager.plugin_version} into {manager.state_dir}."
    )


@vllm_metal_group.command("start")
def vllm_metal_start_command() -> None:
    """Launch the managed host process (idempotent — one process per host)."""
    from services.vllm_metal_manager import VllmMetalError

    manager = _vllm_metal_manager()
    try:
        status = manager.start()
    except VllmMetalError as exc:
        click.echo(f"Start failed: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc
    click.echo(f"vLLM (Metal) running: pid={status.pid} port={status.port}")
    click.echo(f"Logs: {status.log_file}")


@vllm_metal_group.command("stop")
def vllm_metal_stop_command() -> None:
    """Stop the managed host process (SIGINT then SIGKILL)."""
    manager = _vllm_metal_manager()
    stopped = manager.stop()
    click.echo("Stopped." if stopped else "No managed vLLM (Metal) process was running.")


@vllm_metal_group.command("status")
def vllm_metal_status_command() -> None:
    """Show the managed host process status (running / pid / installed version)."""
    manager = _vllm_metal_manager()
    status = manager.status()
    click.echo(json.dumps(status.to_dict(), indent=2))


@vllm_metal_group.command("health")
def vllm_metal_health_command() -> None:
    """Probe /v1/models and report reachability + served model ids."""
    manager = _vllm_metal_manager()
    health = manager.health()
    click.echo(json.dumps(health, indent=2))
    if not health.get("reachable"):
        raise click.exceptions.Exit(1)


@vllm_metal_group.command("remove")
@click.confirmation_option(prompt="Stop the process and delete the managed state directory?")
def vllm_metal_remove_command() -> None:
    """Stop the process and delete the Atlas-owned state directory."""
    manager = _vllm_metal_manager()
    manager.remove()
    click.echo(f"Removed {manager.state_dir}.")


if __name__ == "__main__":
    main()
