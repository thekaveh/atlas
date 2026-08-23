"""
Service discovery for the interactive setup wizard.

Returns the list of user-configurable services with their valid SOURCE
options. Data flows through ``ConfigParser.load_yaml_config()`` which
synthesises the runtime dict from per-service manifests (each
``services/<name>/service.yml`` contributes a ``runtime_sc:`` slice; the
synthesiser at ``bootstrapper/services/sc_synthesizer.py`` concatenates
them). Nothing is hardcoded — service names, options, and metadata all
come from the manifests at startup.
"""

from dataclasses import dataclass
from typing import List

from core.config_parser import ConfigParser
from utils.cloud_providers import CLOUD_PROVIDERS
from utils.source_override_manager import SourceOverrideManager


# Cloud LLM provider keys. These are NOT regular source-configurable
# services — they're API credentials routed through LiteLLM (scale: 0,
# no compose service). The wizard collects an API key for each via
# bespoke secret-input steps in ui/textual/integration.py rather than
# the standard "enabled / disabled" tile prompt that auto-discovery
# would emit. Discover() filters these out so they don't appear twice.
#
# Derived from the canonical CLOUD_PROVIDERS list so adding a fourth
# provider in utils/cloud_providers.py automatically extends this set
# (otherwise the wizard would double-prompt the new provider).
CLOUD_PROVIDER_KEYS = frozenset(f"cloud_{p.key}" for p in CLOUD_PROVIDERS)

@dataclass
class ServiceInfo:
    """Information about a configurable service for the wizard."""
    key: str
    display_name: str
    description: str
    options: List[str]
    option_labels: dict[str, str]
    current_value: str
    env_var_name: str


class ServiceDiscovery:
    """Discovers user-configurable services from per-service manifests
    (services/<name>/service.yml, assembled into the runtime config dict
    by sc_synthesizer)."""

    def __init__(self, config_parser: ConfigParser):
        self.config_parser = config_parser
        # Get the set of Click parameter keys that have CLI flags
        override_manager = SourceOverrideManager(config_parser)
        self._cli_param_keys = set(override_manager.source_mapping.keys())
        self._source_option_labels = self._load_source_option_labels()

    def discover(self) -> List[ServiceInfo]:
        """
        Discover all user-configurable services from per-service manifests.

        A service is included if and only if:
        1. It has a corresponding CLI flag in source_mapping
        2. It has source-style options (sub-keys with 'scale' config, like
           'container', 'disabled', 'container-gpu', etc.)

        Scans both source_configurable and adaptive_services sections, since
        some configurable services (e.g., jupyterhub) are defined under
        adaptive_services with source-style options.

        Returns:
            Ordered list of ServiceInfo for services the wizard should present.
        """
        yaml_config = self.config_parser.load_yaml_config()
        source_configurable = yaml_config.get('source_configurable', {})
        adaptive_services = yaml_config.get('adaptive_services', {})

        env_vars = self.config_parser.parse_env_file()

        # Merge both sections: source_configurable first (preserves YAML order),
        # then adaptive_services entries not already covered
        all_services = {}
        for key, config in source_configurable.items():
            if self._has_source_options(config):
                all_services[key] = config
        for key, config in adaptive_services.items():
            if key not in all_services and self._has_source_options(config):
                all_services[key] = config

        services = []
        for key, config in all_services.items():
            # Only include services that have a corresponding CLI flag
            cli_key = key.replace('-', '_') + '_source'
            if cli_key not in self._cli_param_keys:
                continue
            # Cloud LLM provider toggles are collected via bespoke
            # secret-input steps elsewhere — skip the auto-discovered
            # "enabled / disabled" tile prompt to avoid double-asking.
            if key in CLOUD_PROVIDER_KEYS:
                continue

            # Locked manifests (1 source variant) skip the wizard entirely.
            from services.topology import get_topology
            topology = get_topology()
            derived_source_var = key.upper().replace('-', '_') + '_SOURCE'
            # Multi-container families (e.g. ray-head + ray-worker) drive
            # discovery off the head's runtime_sc key while the canonical
            # env var lives on the family (RAY_SOURCE, not RAY_HEAD_SOURCE).
            # Source-mapping resolves the canonical var so the wizard saves
            # selections to the right key.
            mapped_var = SourceOverrideManager(self.config_parser).source_mapping.get(cli_key)
            target_source_var = mapped_var or derived_source_var

            is_locked = False
            for r in topology.rows:
                if r.source_var == target_source_var:
                    is_locked = r.locked
                    break
            if is_locked:
                continue

            env_var_name = target_source_var
            current_value = env_vars.get(env_var_name, '')
            options = list(config.keys())
            manifest_labels = self._source_option_labels.get(target_source_var, {})
            option_labels = {
                opt: manifest_labels.get(opt, opt)
                for opt in options
            }

            description = ""
            for r in topology.rows:
                if r.source_var == target_source_var:
                    description = r.description
                    break

            services.append(ServiceInfo(
                key=key,
                display_name=self._get_display_name(key),
                description=description,
                options=options,
                option_labels=option_labels,
                current_value=current_value,
                env_var_name=env_var_name,
            ))

        return services

    def _load_source_option_labels(self) -> dict[str, dict[str, str]]:
        """Map SOURCE env var -> option id -> wizard-facing label."""
        from services.manifests import load_manifests

        labels: dict[str, dict[str, str]] = {}
        for manifest in load_manifests(self.config_parser.root_dir / "services"):
            if manifest.sources is None:
                continue
            labels[manifest.sources.var] = {
                opt.id: opt.label
                for opt in manifest.sources.options
            }
        return labels

    @staticmethod
    def _has_source_options(config: dict) -> bool:
        """
        Check if a service config has source-style options.

        Source options have sub-keys like 'container', 'disabled' whose values
        are dicts containing 'scale'. This distinguishes them from adaptation
        metadata like 'adapts_to', 'environment_adaptation'.
        """
        if not isinstance(config, dict):
            return False
        for value in config.values():
            if isinstance(value, dict) and 'scale' in value:
                return True
        return False

    def _get_display_name(self, key: str) -> str:
        """Get a human-readable display name for a service key via Topology.

        Resolution order:
        1. Derive source-var from the key naming convention (`<key>_SOURCE`).
        2. If that doesn't match any row, fall back to resolving through the
           SourceOverrideManager's source_mapping — this handles families
           whose container key differs from their source-var stem (e.g.
           ray-head's runtime_sc key but RAY_SOURCE is the actual var).
        3. Title-case fallback if neither path resolves.
        """
        from services.topology import get_topology
        topology = get_topology()

        target_source_var = key.upper().replace('-', '_') + '_SOURCE'
        for r in topology.rows:
            if r.source_var == target_source_var:
                return r.display_name

        # Multi-container family: the runtime_sc key (e.g. `ray-head`) and the
        # actual source-var (`RAY_SOURCE`) diverge. Resolve via source_mapping.
        cli_key = key.replace('-', '_') + '_source'
        from utils.source_override_manager import SourceOverrideManager
        mapped_var = SourceOverrideManager(self.config_parser).source_mapping.get(cli_key)
        if mapped_var:
            for r in topology.rows:
                if r.source_var == mapped_var:
                    return r.display_name

        return key.replace('_', ' ').replace('-', ' ').title()
