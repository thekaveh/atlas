"""
Service dependency management.

Reads service dependencies from the synthesized runtime config (assembled
from per-manifest `runtime_deps:` blocks under services/<name>/service.yml)
and enforces them during service startup, ensuring required dependencies
are available.

Service enablement (scale/source lookup) is DERIVED from the same manifests
(#503): each ``services/<name>/service.yml`` already declares the canonical
``sources.var`` plus its ``*_SCALE`` env vars, so there is no second
hand-maintained service map to fall out of date. Before this, newer
manifest services (trino / redpanda / iceberg-rest) were missing from the
local dicts and fell through to "assume enabled" — a disabled Trino then
failed startup with a false "trino requires minio" violation.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from core.config_parser import ConfigParser
from utils.source_override_manager import SourceOverrideManager


@dataclass(frozen=True)
class _ServiceEnablementInfo:
    """Manifest-derived enablement metadata for one dependency name.

    ``source_var``  — the manifest's canonical ``sources.var`` (or the unique
                      row-level source var for manifests without a ``sources:``
                      block, e.g. backend). None when ambiguous (supabase) or
                      absent (engine-only manifests like speaches/chatterbox —
                      their availability is decided by their aggregator).
    ``scale_var``   — the best-matching ``*_SCALE`` env var for THIS name:
                      the name-derived exact match when the manifest declares
                      it (``n8n-worker`` → ``N8N_WORKER_SCALE``), else the
                      manifest's primary row scale var (``openclaw-gateway``
                      → ``OPENCLAW_SCALE``). None when neither exists.
    ``all_scale_vars`` — every ``*_SCALE`` env var the manifest declares;
                      the auto-resolve write path zeroes all of them so a
                      disabled family takes its init/worker siblings down
                      (the old hand-written n8n special case, generalized).
    """

    source_var: Optional[str] = None
    scale_var: Optional[str] = None
    all_scale_vars: tuple = field(default_factory=tuple)


# Cache of services-dir → {name: _ServiceEnablementInfo}. Manifest loading
# walks the services tree; dependency checks run several times per start.
_ENABLEMENT_CACHE: Dict[str, Dict[str, _ServiceEnablementInfo]] = {}


#: A replica count is a plain non-negative integer. Deliberately stricter than
#: `int()`, which accepts `1_0` (→10), `+2` and surrounding whitespace —
#: spellings `docker compose --scale` itself rejects, so accepting them made
#: the manager's belief and the launched topology disagree.
_SCALE_RE = re.compile(r"^\d+$")

#: Spellings that unambiguously mean "off". Without these, `SVC_SCALE=false`
#: fails to parse and falls through to "assume enabled" — a disabled service
#: masquerading as enabled.
_FALSY_SCALES = frozenset({"false", "off", "no", "none", "disabled", "0.0"})


def _parse_scale(raw: str) -> Optional[int]:
    """A replica count from a raw env value, or None when unreadable."""
    if _SCALE_RE.match(raw):
        return int(raw)
    return 0 if raw.strip().lower() in _FALSY_SCALES else None


def _compose_replica_vars(manifest) -> Dict[str, str]:
    """container name → the `*_SCALE` var its compose fragment scales it by.

    Read from `deploy.replicas: ${VAR:-0}` in the family's `compose.yml`,
    which is the only place that mapping is actually stated. Returns an empty
    map for virtual manifests and on any read/parse failure; the caller then
    falls back to deriving from the name.
    """
    source_path = getattr(manifest, "source_path", None)
    if source_path is None:
        return {}
    fragment = Path(source_path).parent / "compose.yml"
    try:
        import yaml

        parsed = yaml.safe_load(fragment.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — absent/unreadable fragment → derive
        return {}
    found: Dict[str, str] = {}
    for name, body in (parsed.get("services") or {}).items():
        scale_var = _replica_scale_var(body)
        if scale_var:
            found[str(name)] = scale_var
    return found


def _replica_scale_var(body) -> Optional[str]:
    """The `*_SCALE` var interpolated into one service's `deploy.replicas`."""
    if not isinstance(body, dict):
        return None
    deploy = body.get("deploy")
    if not isinstance(deploy, dict):
        return None
    match = re.search(r"\$\{([A-Z0-9_]+)", str(deploy.get("replicas") or ""))
    if match and match.group(1).endswith("_SCALE"):
        return match.group(1)
    return None


def _manifest_source_var(manifest) -> Optional[str]:
    """The canonical SOURCE var for a manifest, or None when ambiguous."""
    if manifest.sources is not None:
        return manifest.sources.var
    # Manifests without a sources: block (backend) still carry a canonical
    # per-row source var — use it only when every row agrees (supabase's rows
    # diverge → ambiguous → None).
    row_vars = {r.source_var for r in manifest.rows if r.source_var}
    return row_vars.pop() if len(row_vars) == 1 else None


def _manifest_enablement(manifest) -> Dict[str, "_ServiceEnablementInfo"]:
    """name → enablement info for a manifest and each of its containers."""
    source_var = _manifest_source_var(manifest)
    all_scales = tuple(e.name for e in manifest.env if e.name.endswith("_SCALE"))
    primary_scale = next((r.scale_var for r in manifest.rows if r.scale_var), None)
    declared = _compose_replica_vars(manifest)

    out: Dict[str, _ServiceEnablementInfo] = {}
    for name in [manifest.name, *[str(c) for c in manifest.containers]]:
        # The compose fragment states which var scales which container;
        # consult it before falling back to guessing from the name.
        # `docling-lightrag-adapter` is scaled by DOCLING_ADAPTER_SCALE, but
        # the name-derived DOCLING_LIGHTRAG_ADAPTER_SCALE does not exist, so
        # the guess silently fell through to the family's DOCLING_GPU_SCALE
        # and answered for the WRONG container.
        scale_var = declared.get(name)
        if scale_var is None:
            derived = name.upper().replace("-", "_") + "_SCALE"
            scale_var = derived if derived in all_scales else primary_scale
        out[name] = _ServiceEnablementInfo(
            source_var=source_var,
            scale_var=scale_var,
            all_scale_vars=all_scales,
        )
    return out


class DependencyManager:
    """Manages service dependencies based on YAML configuration."""

    def __init__(self, config_parser: Optional[ConfigParser] = None):
        """
        Initialize dependency manager.
        
        Args:
            config_parser: ConfigParser instance (creates new one if None)
        """
        self.config_parser = config_parser or ConfigParser()
        self.yaml_config = None
        self.dependency_violations = []
        
    def load_yaml_config(self) -> bool:
        """
        Load the YAML configuration for dependency checking.
        
        Returns:
            bool: True if loaded successfully
        """
        try:
            self.yaml_config = self.config_parser.load_yaml_config()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to load service manifests: {e}")
            return False
            
    def get_service_dependencies(self) -> Dict[str, Dict]:
        """
        Get service dependencies from YAML configuration.
        
        Returns:
            dict: Service dependencies structure from YAML
        """
        if not self.yaml_config:
            return {}
            
        return self.yaml_config.get('service_dependencies', {})
        
    def _services_dirs(self) -> List[Path]:
        """Candidate services dirs: the configured root first (consumer
        checkouts), then the packaged repo tree (synthetic test roots point
        their ConfigParser at a tmp dir with no services/)."""
        dirs: List[Path] = []
        root = getattr(self.config_parser, "root_dir", None)
        if root:
            dirs.append(Path(root) / "services")
        dirs.append(Path(__file__).resolve().parents[2] / "services")
        return dirs

    def _enablement_lookup(self) -> Dict[str, _ServiceEnablementInfo]:
        """Build (once per services dir) the manifest-derived name →
        enablement-info map covering every manifest name AND container name,
        so dependency keys like ``openclaw-gateway`` / ``n8n-worker`` /
        ``neo4j-graph-db`` resolve without a hand-maintained alias table."""
        for services_dir in self._services_dirs():
            key = str(services_dir.resolve()) if services_dir.exists() else None
            if key is None:
                continue
            cached = _ENABLEMENT_CACHE.get(key)
            if cached is not None:
                return cached
            try:
                from services.manifests import load_manifests

                manifests = load_manifests(services_dir)
            except Exception as exc:  # noqa: BLE001 — fall through to next candidate
                # Say so. A single malformed service.yml makes load_manifests
                # raise for the WHOLE consumer tree, and the manager then
                # answers silently from the packaged Atlas tree — describing a
                # topology the user is not running.
                print(
                    f"  ⚠️  could not load manifests from {services_dir}: {exc}; "
                    f"trying the next candidate"
                )
                continue

            lookup: Dict[str, _ServiceEnablementInfo] = {}
            for m in manifests:
                for name, info in _manifest_enablement(m).items():
                    lookup.setdefault(name, info)
            if not lookup:
                # An EMPTY result is not an answer. Caching and returning it
                # short-circuits the packaged-tree fallback, and every service
                # then falls through to "assume enabled" — verbatim the #503
                # regression this module exists to prevent. Verified: a
                # `services/` dir that exists but holds no manifests made a
                # `N8N_SOURCE=disabled` n8n report scale 1.
                continue
            _ENABLEMENT_CACHE[key] = lookup
            return lookup
        return {}

    def get_service_scale(self, service_name: str) -> int:
        """
        Get the current scale setting for a service from environment.

        Resolution order (manifest-derived, #503):

        1. An explicit integer in the service's ``*_SCALE`` env var wins —
           this is what a completed start computes and what auto-resolve
           writes (``N8N_SCALE=0`` while ``N8N_SOURCE`` stays ``container``).
        2. Blank/garbage scale (fresh ``.env``: auto_managed vars render as
           ``VAR=``) falls through to the SOURCE signal: ``disabled`` → 0.
           This is the #503 fix — a disabled Trino/Redpanda/Iceberg-REST on
           a fresh env no longer masquerades as enabled.
        3. No signal at all → assume enabled (aggregator-selected engines,
           always-on families like supabase).

        Args:
            service_name: Name of the service

        Returns:
            int: Scale value (0 = disabled, 1+ = enabled)
        """
        info = self._enablement_lookup().get(service_name)
        env_vars = self.config_parser.parse_env_file()

        if info and info.scale_var:
            raw = (env_vars.get(info.scale_var, "") or "").strip()
            if raw:
                scale = _parse_scale(raw)
                if scale is not None:
                    return scale
                # Unreadable → fall through to the SOURCE signal, but say so.
                # Silence here is how `SVC_SCALE=0.0` came to read as ENABLED,
                # which is precisely what #503 forbids.
                print(
                    f"  ⚠️  {info.scale_var}={raw!r} is not a replica count; "
                    f"falling back to {info.source_var or 'the default'}"
                )

        if info and info.source_var:
            source_vars = self.config_parser.parse_service_sources()
            if source_vars.get(info.source_var) == 'disabled':
                return 0

        return 1  # Assume enabled if no explicit scale or source
        
    def check_service_dependencies(self) -> bool:
        """
        Check all service dependencies and identify violations.
        
        Returns:
            bool: True if all dependencies are satisfied
        """
        self.dependency_violations = []
        
        if not self.load_yaml_config():
            return False
            
        dependencies = self.get_service_dependencies()
        if not dependencies:
            return True  # No dependencies defined
            
        all_satisfied = True
        
        for service_name, dep_config in dependencies.items():
            service_scale = self.get_service_scale(service_name)
            
            # Only check dependencies for enabled services
            if service_scale == 0:
                continue
                
            # Check required dependencies
            required_deps = dep_config.get('requires', [])
            for required_service in required_deps:
                required_scale = self.get_service_scale(required_service)
                
                if required_scale == 0:
                    # Required dependency is disabled
                    error_msg = dep_config.get('error_message', 
                        f"{service_name} requires {required_service} but it's disabled")
                    
                    self.dependency_violations.append({
                        'service': service_name,
                        'required_service': required_service,
                        'error_message': error_msg
                    })
                    all_satisfied = False
                    
            # Log info about optional dependencies
            optional_deps = dep_config.get('optional', [])
            if optional_deps:
                available_optional = []
                for optional_service in optional_deps:
                    if self.get_service_scale(optional_service) > 0:
                        available_optional.append(optional_service)
                        
                if available_optional:
                    info_msg = dep_config.get('info_message', 
                        f"{service_name} will connect to: {', '.join(available_optional)}")
                    print(f"[INFO] {info_msg}")
                    
        return all_satisfied
        
    def auto_resolve_dependency_violations(self) -> List[str]:
        """
        Automatically resolve dependency violations by disabling dependent services.
        
        Returns:
            list: List of services that were auto-disabled
        """
        disabled_services = []

        # De-duplicate by service. Violations are per (service, requirement),
        # so a service missing two dependencies was resolved twice: `.env` was
        # atomically rewritten a second time with identical content, and the
        # caller printed "Auto-disabled trino..." twice.
        seen: set = set()
        for violation in self.dependency_violations:
            service_name = violation['service']
            if service_name in seen:
                continue
            seen.add(service_name)

            # Manifest-derived (#503): zero EVERY *_SCALE var the violating
            # service's manifest declares, so a disabled family takes its
            # init/worker siblings down with it (n8n → N8N_SCALE +
            # N8N_WORKER_SCALE + N8N_INIT_SCALE — the old hand-written n8n
            # special case, generalized to every manifest family). Using the
            # same lookup as get_service_scale keeps the read and write paths
            # consistent by construction.
            info = self._enablement_lookup().get(service_name)
            scale_vars_to_update = list(info.all_scale_vars) if info else []

            if not scale_vars_to_update:
                # Actionable, explicit refusal (per #503 AC): never silently
                # skip — and never mask a disabled service as enabled.
                print(
                    f"[ERROR] Cannot auto-resolve dependency violation for "
                    f"'{service_name}': its manifest declares no *_SCALE env "
                    f"vars to zero. Disable it explicitly via its *_SOURCE "
                    f"setting instead."
                )
                continue

            # Route through SourceOverrideManager's atomic, mode-preserving
            # writer (tmp + os.replace) instead of an in-place open(...,'w'):
            # a crash mid-write must not truncate the secrets-bearing .env,
            # and a user-chmod'd 0600 .env must keep its mode. This matches
            # every other .env writer in the codebase.
            if SourceOverrideManager(self.config_parser).update_env_file(
                {scale_var: '0' for scale_var in scale_vars_to_update}
            ):
                disabled_services.append(service_name)
            else:
                print(f"[ERROR] Failed to disable {service_name}")

        return disabled_services
        
    def get_dependency_violations(self) -> List[Dict]:
        """
        Get list of dependency violations.
        
        Returns:
            list: List of violation dictionaries
        """
        return self.dependency_violations.copy()
        
    def print_dependency_results(self) -> None:
        """Print dependency check results to console."""
        if self.dependency_violations:
            print("[ERROR] Service dependency violations found:")
            for violation in self.dependency_violations:
                print(f"   {violation['error_message']}")
        else:
            print("[OK] All service dependencies satisfied")
            
    def get_dependency_info(self, service_name: str) -> Dict:
        """
        Get dependency information for a specific service.
        
        Args:
            service_name: Name of the service
            
        Returns:
            dict: Dependency information (requires, optional, messages)
        """
        dependencies = self.get_service_dependencies()
        return dependencies.get(service_name, {})