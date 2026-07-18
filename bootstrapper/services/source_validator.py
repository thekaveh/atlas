"""
SOURCE configuration validation against per-service manifests.

Each services/<name>/service.yml declares its `sources.options[]` set; this
module asserts that every *_SOURCE value in .env matches one of those options.

Python implementation of the validate_source_values() function from start.sh.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Set
from core.config_parser import ConfigParser


class SourceValidator:
    """Validates SERVICE SOURCE configurations against YAML definitions."""
    
    def __init__(self, config_parser: Optional[ConfigParser] = None):
        """
        Initialize source validator.
        
        Args:
            config_parser: ConfigParser instance (creates new one if None)
        """
        self.config_parser = config_parser or ConfigParser()
        self.yaml_config = None
        self.validation_errors = []
    
    def load_yaml_config(self) -> bool:
        """
        Load the YAML configuration for validation.
        
        Returns:
            bool: True if loaded successfully
        """
        try:
            self.yaml_config = self.config_parser.load_yaml_config()
            return True
        except Exception as e:
            self.validation_errors.append(f"Failed to load YAML config: {e}")
            return False
    
    def get_valid_sources_for_service(self, service_key: str) -> Set[str]:
        """
        Get valid SOURCE values for a specific service.
        
        Args:
            service_key: Service key in YAML config (e.g., "llm_provider", "comfyui")
            
        Returns:
            set: Set of valid SOURCE values for the service
        """
        if not self.yaml_config:
            return set()
            
        source_configurable = self.yaml_config.get('source_configurable', {})
        service_config = source_configurable.get(service_key, {})
        
        return set(service_config.keys())
    
    def get_service_mapping_from_yaml(self) -> Dict[str, str]:
        """
        Build service mapping dynamically from YAML configuration.
        Maps SOURCE variable names to service keys in YAML.

        Returns:
            dict: Mapping of SOURCE variables to YAML service keys
        """
        if not self.yaml_config:
            return {}

        service_mapping = {}

        # Get source_configurable services
        source_configurable = self.yaml_config.get('source_configurable', {})
        for service_key in source_configurable.keys():
            # Convert service key to SOURCE variable name
            # e.g., 'llm_provider' -> 'LLM_PROVIDER_SOURCE'
            source_var = service_key.upper().replace('-', '_') + '_SOURCE'
            service_mapping[source_var] = service_key

        # Multi-container families (e.g. ray-head + ray-worker, both keyed
        # in source_configurable) have a single canonical env var (RAY_SOURCE)
        # that doesn't match either container's derived <KEY>_SOURCE. Resolve
        # those aliases through SourceOverrideManager.source_mapping, which
        # already carries the runtime_sc-key → canonical-env-var indirection.
        # Without this, the validator reports "Unknown SOURCE variable:
        # RAY_SOURCE" the moment a user writes it to .env.
        from utils.source_override_manager import SourceOverrideManager
        sm = SourceOverrideManager(self.config_parser).source_mapping
        for cli_key, canonical_env_var in sm.items():
            if canonical_env_var in service_mapping:
                continue  # already covered by source_configurable
            # cli_key is e.g. 'ray_head_source' → strip suffix → 'ray_head'
            # → kebab-case → 'ray-head' (the runtime_sc top-level key)
            stem = cli_key[:-len('_source')] if cli_key.endswith('_source') else cli_key
            runtime_sc_key = stem.replace('_', '-')
            if runtime_sc_key in source_configurable:
                service_mapping[canonical_env_var] = runtime_sc_key

        return service_mapping
        
    def get_adaptive_services_from_yaml(self) -> Set[str]:
        """
        Get adaptive services from YAML configuration.
        
        Returns:
            set: Set of adaptive service SOURCE variable names
        """
        if not self.yaml_config:
            return set()
            
        adaptive_services = set()
        adaptive_config = self.yaml_config.get('adaptive_services', {})
        
        for service_key in adaptive_config.keys():
            source_var = service_key.upper().replace('-', '_') + '_SOURCE'
            adaptive_services.add(source_var)
            
        return adaptive_services
    
    def validate_source_value(self, service_var: str, source_value: str) -> bool:
        """
        Validate a single SOURCE value against YAML configuration.

        Args:
            service_var: SOURCE variable name (e.g., "LLM_PROVIDER_SOURCE")
            source_value: SOURCE value to validate

        Returns:
            bool: True if valid
        """
        # NOTE: deliberately no validation_errors reset here. The only
        # caller is the validate_all_sources() loop, which clears the list
        # once before iterating — a per-item reset wiped every error but
        # the last item's, so multi-error runs printed "All SOURCE values
        # are valid" while exiting 1.

        # Get service mappings dynamically from YAML
        service_mapping = self.get_service_mapping_from_yaml()
        adaptive_services = self.get_adaptive_services_from_yaml()

        if service_var in adaptive_services:
            # Check if this adaptive service has explicit source options defined
            # (some adaptive services like jupyterhub have container/disabled options)
            valid_sources = self._get_adaptive_service_sources(service_var)
            if not valid_sources:
                # Also check source_configurable — some services appear in both
                # (e.g., n8n has adapts_to metadata AND source options)
                service_key = service_mapping.get(service_var)
                if service_key:
                    valid_sources = self.get_valid_sources_for_service(service_key)
            if valid_sources:
                if source_value not in valid_sources:
                    valid_list = ', '.join(sorted(valid_sources))
                    self.validation_errors.append(
                        f"❌ {service_var}='{source_value}' is invalid. "
                        f"Valid options: {valid_list}"
                    )
                    return False
                return True
            else:
                # Pure adaptive service — only supports 'container'
                if source_value != 'container':
                    self.validation_errors.append(
                        f"❌ {service_var}='{source_value}' is invalid. "
                        f"Adaptive services only support 'container'"
                    )
                    return False
                return True

        service_key = service_mapping.get(service_var)
        if not service_key:
            self.validation_errors.append(f"❌ Unknown SOURCE variable: {service_var}")
            return False

        valid_sources = self.get_valid_sources_for_service(service_key)
        if not valid_sources:
            self.validation_errors.append(
                f"❌ No valid sources found for service: {service_key}"
            )
            return False

        if source_value not in valid_sources:
            valid_list = ', '.join(sorted(valid_sources))
            self.validation_errors.append(
                f"❌ {service_var}='{source_value}' is invalid. "
                f"Valid options: {valid_list}"
            )
            return False

        return True

    def _get_adaptive_service_sources(self, service_var: str) -> Set[str]:
        """
        Get valid SOURCE options for an adaptive service that has explicit
        source-style options (sub-keys with 'scale' config) in its YAML entry.

        Returns:
            set: Valid SOURCE values, or empty set if pure adaptive service
        """
        if not self.yaml_config:
            return set()

        adaptive_config = self.yaml_config.get('adaptive_services', {})
        # Derive service key from variable name
        service_key = service_var.replace('_SOURCE', '').lower().replace('_', '-')
        # Also try underscore variant
        service_key_underscore = service_var.replace('_SOURCE', '').lower()

        config = adaptive_config.get(service_key) or adaptive_config.get(service_key_underscore)
        if not config or not isinstance(config, dict):
            return set()

        # Check for source-style options (sub-keys whose values contain 'scale')
        sources = set()
        for key, value in config.items():
            if isinstance(value, dict) and 'scale' in value:
                sources.add(key)

        return sources
        
    def validate_scale_values(self) -> bool:
        """
        Validate all scale values from .env file.

        Precondition: call AFTER ``validate_all_sources()`` (which resets
        ``self.validation_errors``). This method intentionally *appends*
        rather than resets, so source-config and scale errors surface
        together; it relies on the caller having reset the error list via
        the preceding sources pass.

        Returns:
            bool: True if all scale values are valid
        """
        env_vars = self.config_parser.parse_env_file()
        all_valid = True
        
        # Get all scale variables from .env
        scale_vars = {k: v for k, v in env_vars.items() if k.endswith('_SCALE')}
        
        for scale_var, scale_value in scale_vars.items():
            if not scale_value.strip():
                continue
                
            try:
                scale_int = int(scale_value)
                
                # Validate scale is 0 or 1 (or positive integer)
                if scale_int < 0:
                    self.validation_errors.append(
                        f"❌ {scale_var}='{scale_value}' is invalid. Scale must be 0 or positive integer"
                    )
                    all_valid = False
                elif scale_int > 1:
                    # Warn about scale > 1 (not common in this stack)
                    print(f"⚠️  {scale_var}='{scale_value}' - High scale values may cause resource issues")
                    
            except ValueError:
                self.validation_errors.append(
                    f"❌ {scale_var}='{scale_value}' is invalid. Scale must be a number"
                )
                all_valid = False
        
        return all_valid
    
    def validate_all_sources(self) -> bool:
        """
        Validate all SOURCE configurations from .env file.
        Replicates the validate_source_values() function from start.sh.

        Returns:
            bool: True if all sources are valid

        Note: this method is now read-only. The previous behaviour of
        auto-disabling cloud providers with missing keys lives in
        ``enforce_runtime_invariants()`` — call it before
        ``validate_all_sources()`` if you want the rewrite-then-validate
        behaviour. ``start.py`` does this in both the linear and TUI
        pipelines.
        """
        self.validation_errors = []

        if not self.load_yaml_config():
            return False

        service_sources = self.config_parser.parse_service_sources()

        if not service_sources:
            self.validation_errors.append("❌ No SOURCE configurations found")
            return False

        all_valid = True

        for service_var, source_value in service_sources.items():
            if not source_value:  # Skip empty values
                continue

            if not self.validate_source_value(service_var, source_value):
                all_valid = False

        # No-upstream guard: if the local engine is `none` AND every cloud
        # provider is disabled, LiteLLM has nothing to serve. Refuse to start.
        if not self._validate_litellm_has_upstream(service_sources):
            all_valid = False

        if not self._validate_fal_key_present(service_sources):
            all_valid = False

        if not self._validate_cloudflared_token_present(service_sources):
            all_valid = False

        if not self._validate_graph_builder_gcp_config(service_sources):
            all_valid = False

        return all_valid

    def enforce_runtime_invariants(self) -> bool:
        """Repair-style step: rewrite .env to a runnable shape.

        Distinct from ``validate_all_sources()`` which is read-only.
        Callers that want the side-effecting auto-disable behaviour
        (e.g. ``start.py``'s pipeline) call this first, then validate.
        Pure tooling (linters, CI checks, future dry-runs) can call
        ``validate_all_sources()`` alone without mutating .env.

        Currently performs:
          - ``_migrate_legacy_tts_stt_sources()`` — flips deprecated
            ``xtts-*`` source values to their maintained successors so
            users who pulled the new bootstrapper against an older .env
            don't fail validation against an archived image.
          - ``_enforce_cloud_keys_present()`` — auto-disables cloud
            providers whose API key is empty.

        Returns:
            bool: ``True`` if the repair pass either had nothing to do
            or successfully persisted the rewrite. ``False`` if a
            required .env write failed — in which case the caller must
            stop the launch pipeline, because a subsequent
            ``validate_all_sources()`` would read pre-rewrite state and
            may pass against values that no longer reflect intent.
        """
        # Run migration first so the later cloud-keys pass sees the
        # post-migration .env (no functional overlap today, but cheaper
        # to keep migrations as the outermost layer for future passes
        # that might depend on each other's rewrites).
        tts_stt_ok = self._migrate_legacy_tts_stt_sources()
        cloud_ok = self._enforce_cloud_keys_present()
        return tts_stt_ok and cloud_ok

    def _migrate_legacy_tts_stt_sources(self) -> bool:
        """Auto-rewrite legacy TTS source values from the pre-Speaches era
        (the STT side of the migration is limited to stripping the
        retired PARAKEET_ENDPOINT var — no STT source values change).

        Background: the previous TTS path used ``ghcr.io/matatonic/openedai-
        speech`` which was archived 2026-01-04, and the previous voice-cloning
        TTS path used XTTS-v2 whose weights are CPML / non-commercial. Both
        were replaced — Speaches for the everyday TTS+STT path, Chatterbox
        (Resemble AI, MIT) for voice cloning. The wizard catalog no longer
        offers ``xtts-container-gpu`` or ``xtts-localhost`` as valid sources,
        so any .env carrying those values would fail validation. This pass
        flips them to the closest maintained equivalent and prints a one-line
        notice so the user knows what changed.
        """
        env_vars = self.config_parser.parse_env_file()
        rewrites: Dict[str, str] = {}

        tts_source = (env_vars.get('TTS_PROVIDER_SOURCE', '') or '').strip()
        if tts_source == 'xtts-container-gpu':
            rewrites['TTS_PROVIDER_SOURCE'] = 'speaches-container-gpu'
            print(
                "ℹ️  TTS_PROVIDER_SOURCE=xtts-container-gpu auto-migrated to "
                "speaches-container-gpu (openedai-speech was archived 2026-01-04). "
                "See services/tts-provider/README.md."
            )
        elif tts_source == 'xtts-localhost':
            rewrites['TTS_PROVIDER_SOURCE'] = 'chatterbox-localhost'
            print(
                "ℹ️  TTS_PROVIDER_SOURCE=xtts-localhost auto-migrated to "
                "chatterbox-localhost. Run `pip install chatterbox-tts-api` on the "
                "host and start its server — see services/tts-provider/provider/localhost/README.md."
            )

        if rewrites:
            try:
                from utils.source_override_manager import SourceOverrideManager
                if not SourceOverrideManager(self.config_parser).update_env_file(rewrites):
                    self.validation_errors.append(
                        "❌ Could not persist TTS/STT migration rewrite. "
                        "Manually edit .env: set TTS_PROVIDER_SOURCE to a current value "
                        "(speaches-container-cpu, speaches-container-gpu, "
                        "chatterbox-container-gpu, chatterbox-localhost, or disabled)."
                    )
                    return False
            except Exception as exc:  # noqa: BLE001
                self.validation_errors.append(
                    f"❌ Could not persist TTS/STT migration rewrite: {exc}. "
                    "Manually edit .env: set TTS_PROVIDER_SOURCE to a current value "
                    "(speaches-container-cpu, speaches-container-gpu, "
                    "chatterbox-container-gpu, chatterbox-localhost, or disabled)."
                )
                return False

        # Clean up stale auto-managed lines from old .env files. These were
        # written by the previous bootstrapper and are now dead. Leaving them
        # is harmless but confusing during debugging.
        self._strip_lines_from_env([
            'XTTS_ENDPOINT', 'PARAKEET_ENDPOINT', 'XTTS_GPU_SCALE'
        ])
        return True

    def _strip_lines_from_env(self, var_names: List[str]) -> None:
        """Remove any ``VAR=…`` line whose key matches ``var_names`` from .env.

        Best-effort: failures here don't fail the launch pipeline because
        leftover stale env lines are inert (no consumer reads them after
        this change).
        """
        env_file = self.config_parser.env_file_path
        if not env_file.exists():
            return
        try:
            with open(env_file, 'r', encoding="utf-8") as f:
                lines = f.readlines()
            keep = []
            stripped_any = False
            for line in lines:
                # Match ``VAR=`` at line start (ignoring leading whitespace).
                # We do NOT touch commented lines or lines that mention the
                # var in passing — we only remove assignments.
                lhs = line.split('=', 1)[0].strip()
                if lhs in var_names:
                    stripped_any = True
                    continue
                keep.append(line)
            if stripped_any:
                with open(env_file, 'w', encoding="utf-8") as f:
                    f.writelines(keep)
        except Exception:  # noqa: BLE001
            pass  # silent — not critical

    def _enforce_cloud_keys_present(self) -> bool:
        """Auto-disable cloud providers whose API key is empty.

        ``CLOUD_OPENAI_SOURCE=enabled`` with an empty ``OPENAI_API_KEY``
        looks ready in .env but errors at the first request. Flipping
        the source back to ``disabled`` keeps the rest of the stack
        runnable; the user gets a clear warning telling them how to fix
        it (paste a key in the wizard or set the env var directly).
        """
        from utils.cloud_providers import CLOUD_PROVIDERS
        env_vars = self.config_parser.parse_env_file()
        rewrites: Dict[str, str] = {}
        for provider in CLOUD_PROVIDERS:
            source_var = provider.source_var
            key_var = provider.api_key_var
            source = (env_vars.get(source_var, 'disabled') or '').strip().lower()
            key = (env_vars.get(key_var, '') or '').strip()
            if source == 'enabled' and not key:
                print(
                    f"⚠️  {source_var}=enabled but {key_var} is empty — "
                    f"auto-disabled. Re-run ./start.sh and paste a key, "
                    f"or set {key_var} in .env."
                )
                rewrites[source_var] = 'disabled'
        if not rewrites:
            return True
        # Persist via the same in-place .env writer used elsewhere so
        # subsequent reads (and downstream services) see the corrected
        # value. Importing here keeps source_validator dependency-free
        # for test contexts.
        try:
            from utils.source_override_manager import SourceOverrideManager
            if not SourceOverrideManager(self.config_parser).update_env_file(rewrites):
                self.validation_errors.append(
                    "❌ Could not persist cloud-provider auto-disable rewrite. "
                    "Manually fix .env (paste keys or set CLOUD_*_SOURCE=disabled) and retry."
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            # A failed repair-pass write is not silently recoverable:
            # subsequent ``validate_all_sources()`` would read stale
            # .env and may pass against pre-rewrite values that no
            # longer reflect intent. Surface the failure as a
            # validation error and return False so the caller halts.
            self.validation_errors.append(
                f"❌ Could not persist cloud-provider auto-disable rewrite: {exc}. "
                "Manually fix .env (paste keys or set CLOUD_*_SOURCE=disabled) and retry."
            )
            return False

    def _validate_litellm_has_upstream(self, service_sources: Dict[str, str]) -> bool:
        """At least one upstream — Ollama engine, a cloud provider, or managed
        vLLM Metal — must be configured, otherwise the LiteLLM gateway has no
        models to route to.
        """
        llm_source = service_sources.get('LLM_PROVIDER_SOURCE', 'ollama-container-cpu')
        cloud_enabled = any(
            service_sources.get(var, 'disabled') == 'enabled'
            for var in ('CLOUD_OPENAI_SOURCE', 'CLOUD_ANTHROPIC_SOURCE', 'CLOUD_OPENROUTER_SOURCE')
        )
        # vLLM Metal is a genuine LiteLLM upstream: litellm-init registers a
        # model_list entry (init.py::vllm_metal_model_entry) when
        # VLLM_METAL_SOURCE=managed-localhost. A vLLM-Metal-only stack
        # (LLM_PROVIDER_SOURCE=none, all cloud disabled) is valid.
        vllm_metal_enabled = (
            service_sources.get('VLLM_METAL_SOURCE', 'disabled') == 'managed-localhost'
        )

        if llm_source == 'none' and not cloud_enabled and not vllm_metal_enabled:
            self.validation_errors.append(
                "❌ LiteLLM has no upstream configured. Set LLM_PROVIDER_SOURCE to an "
                "ollama-* value, enable at least one of CLOUD_OPENAI_SOURCE / "
                "CLOUD_ANTHROPIC_SOURCE / CLOUD_OPENROUTER_SOURCE, or set "
                "VLLM_METAL_SOURCE=managed-localhost."
            )
            return False
        return True

    def _validate_fal_key_present(self, service_sources: Dict[str, str]) -> bool:
        """FAL is disabled by default and needs no key while disabled, but an
        explicitly enabled cloud-media provider must have credentials before
        the backend can call fal.ai.
        """
        if service_sources.get('FAL_SOURCE', 'disabled') != 'enabled':
            return True

        env_vars = self.config_parser.parse_env_file()
        api_key = (
            env_vars.get('FAL_API_KEY', '')
            or env_vars.get('FAL_KEY', '')
            or ''
        ).strip()
        if api_key:
            return True

        self.validation_errors.append(
            "❌ FAL_SOURCE=enabled requires FAL_API_KEY. Set FAL_API_KEY in "
            ".env or pass --fal-api-key, or set FAL_SOURCE=disabled."
        )
        return False

    def _validate_cloudflared_token_present(
        self, service_sources: Dict[str, str]
    ) -> bool:
        """An enabled named tunnel cannot start without its dashboard token."""
        if service_sources.get('CLOUDFLARED_SOURCE', 'disabled') != 'container':
            return True

        env_vars = self.config_parser.parse_env_file()
        if (env_vars.get('CLOUDFLARE_TUNNEL_TOKEN', '') or '').strip():
            return True

        self.validation_errors.append(
            "❌ CLOUDFLARED_SOURCE=container requires CLOUDFLARE_TUNNEL_TOKEN. "
            "Set the token in .env or set CLOUDFLARED_SOURCE=disabled."
        )
        return False

    def _validate_graph_builder_gcp_config(
        self, service_sources: Dict[str, str]
    ) -> bool:
        """GCP-backed Graph Builder features need a complete credential set."""
        if service_sources.get('LLM_GRAPH_BUILDER_SOURCE', 'disabled') != 'container':
            return True

        env_vars = self.config_parser.parse_env_file()
        truthy = {'1', 'true', 'yes'}
        falsy = {'0', 'false', 'no', ''}
        flag_values = {
            name: env_vars.get(name, '').strip().lower()
            for name in (
                'LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED',
                'LLM_GRAPH_BUILDER_GCS_FILE_CACHE',
            )
        }
        invalid_flags = [
            f"{name}={value!r}"
            for name, value in flag_values.items()
            if value not in truthy | falsy
        ]
        if invalid_flags:
            self.validation_errors.append(
                "❌ LLM Graph Builder GCP feature flags accept only "
                "true/false, 1/0, or yes/no: " + ", ".join(invalid_flags) + "."
            )
            return False

        logs_enabled = flag_values['LLM_GRAPH_BUILDER_GCP_LOG_METRICS_ENABLED'] in truthy
        cache_enabled = flag_values['LLM_GRAPH_BUILDER_GCS_FILE_CACHE'] in truthy
        credential_value = env_vars.get(
            'LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE', ''
        ).strip()
        if not logs_enabled and not cache_enabled:
            if credential_value:
                self.validation_errors.append(
                    "❌ LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE is set while both "
                    "Graph Builder GCP features are disabled. Clear the path or enable "
                    "the feature that needs it."
                )
                return False
            return True

        project_id = (
            env_vars.get('LLM_GRAPH_BUILDER_GCP_PROJECT_ID', '')
            or env_vars.get('GOOGLE_CLOUD_PROJECT', '')
        ).strip()
        missing = []
        if not project_id:
            missing.append('LLM_GRAPH_BUILDER_GCP_PROJECT_ID')
        if not credential_value:
            missing.append('LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE')
        if cache_enabled:
            missing.extend(
                name
                for name in (
                    'LLM_GRAPH_BUILDER_GCS_UPLOAD_BUCKET',
                    'LLM_GRAPH_BUILDER_GCS_FAILED_BUCKET',
                )
                if not env_vars.get(name, '').strip()
            )
        if missing:
            self.validation_errors.append(
                "❌ Enabled LLM Graph Builder GCP features require: "
                + ", ".join(missing)
                + "."
            )
            return False

        credential_path = Path(
            os.path.expandvars(credential_value)
        ).expanduser()
        if not credential_path.is_absolute():
            self.validation_errors.append(
                "❌ LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE must be an absolute "
                "host path so Compose and startup validation resolve the same file."
            )
            return False
        try:
            credential_text = credential_path.read_text(encoding='utf-8')
        except OSError:
            self.validation_errors.append(
                "❌ LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE does not name a readable file: "
                f"{credential_path}."
            )
            return False

        try:
            credential_info = json.loads(credential_text)
        except (TypeError, ValueError):
            credential_info = None
        required_by_type = {
            'authorized_user': {'client_id', 'client_secret', 'refresh_token'},
            'service_account': {'client_email', 'private_key', 'token_uri'},
            'external_account': {
                'audience', 'subject_token_type', 'token_url', 'credential_source',
            },
            'external_account_authorized_user': {
                'audience', 'refresh_token', 'token_url', 'client_id', 'client_secret',
            },
            'impersonated_service_account': {
                'source_credentials', 'service_account_impersonation_url',
            },
            'gdch_service_account': {
                'format_version', 'private_key_id', 'private_key', 'name', 'project',
                'token_uri',
            },
        }
        credential_type = (
            credential_info.get('type') if isinstance(credential_info, dict) else None
        )
        required_fields = required_by_type.get(credential_type)
        missing_fields = (
            sorted(
                field
                for field in required_fields
                if not credential_info.get(field)
            )
            if required_fields is not None
            else []
        )
        if required_fields is None or missing_fields:
            detail = (
                f"; missing fields: {', '.join(missing_fields)}"
                if missing_fields
                else ""
            )
            self.validation_errors.append(
                "❌ LLM_GRAPH_BUILDER_GCP_CREDENTIALS_FILE must contain a "
                f"recognized, structurally complete Google ADC JSON document{detail}."
            )
            return False
        return True
    
    def validate_sources_for_profile(
        self, service_sources: dict, profile: str
    ) -> bool:
        """Reject any chosen source not offered under the given profile.

        Uses the declarative ``profiles:`` metadata from the manifests
        (Task 3, Plan B) to decide which source options are available.
        A source that is annotated as dev-only (``profiles: [default]``)
        is rejected under ``--profile prod`` with a clear error message.

        Args:
            service_sources: Mapping of SOURCE env-var names to their
                             current values (e.g. ``{"OLLAMA_SOURCE": "ollama-localhost"}``).
            profile:         The deployment profile to validate against
                             (``"default"`` or ``"prod"``).

        Returns:
            bool: True if every source is available under the profile,
                  False if at least one source is not (errors appended to
                  ``self.validation_errors``).
        """
        from pathlib import Path as _Path
        from services.manifests import load_manifests as _load_manifests, option_in_profile as _oip
        try:
            _services_dir = _Path(self.config_parser.root_dir) / "services"
            _manifests = _load_manifests(_services_dir)
        except Exception as e:  # noqa: BLE001
            self.validation_errors.append(
                f"Could not load manifests for profile validation: {e}"
            )
            return False

        # Build a SOURCE-var → manifest-name mapping directly from the
        # manifests' sources.var field. This is more accurate than
        # get_service_mapping_from_yaml() because the YAML config aggregates
        # sources under synthetic service-config keys (e.g. "llm_provider")
        # that don't match manifest names (e.g. "ollama"). option_in_profile
        # looks up by manifest.name, so we must use this mapping.
        _source_var_to_name: dict[str, str] = {
            mf.sources.var: mf.name
            for mf in _manifests
            if mf.sources is not None
        }

        ok = True
        for var, val in service_sources.items():
            if not val:
                continue
            skey = _source_var_to_name.get(var)
            if skey and not _oip(_manifests, skey, val, profile):
                self.validation_errors.append(
                    f"{var}='{val}' is not available under --profile {profile} "
                    f"(marked for other profiles in services/{skey}/service.yml)."
                )
                ok = False
        return ok

    def get_validation_errors(self) -> List[str]:
        """
        Get list of validation errors.
        
        Returns:
            list: List of error messages
        """
        return self.validation_errors.copy()
    
    def print_validation_results(self) -> None:
        """Print validation results to console."""
        if self.validation_errors:
            print("❌ SOURCE validation failed:")
            for error in self.validation_errors:
                print(f"   {error}")
            print("\n💡 Please check your .env file and fix the invalid SOURCE values.")
            print("   Valid SOURCE options are defined in each service's manifest")
            print("   at services/<service>/service.yml under `sources.options`.")
        else:
            print("✅ All SOURCE values are valid")
    
    def get_service_source_options(self, service_var: str) -> List[str]:
        """
        Get valid SOURCE options for a specific service variable.
        
        Args:
            service_var: SOURCE variable name
            
        Returns:
            list: List of valid SOURCE values
        """
        if not self.yaml_config:
            self.load_yaml_config()
            
        # Get mappings dynamically from YAML
        service_mapping = self.get_service_mapping_from_yaml()
        adaptive_services = self.get_adaptive_services_from_yaml()
        
        if service_var in adaptive_services:
            # Check for explicit source options first
            adaptive_sources = self._get_adaptive_service_sources(service_var)
            if not adaptive_sources:
                # Also check source_configurable
                sk = service_mapping.get(service_var)
                if sk:
                    adaptive_sources = self.get_valid_sources_for_service(sk)
            if adaptive_sources:
                return sorted(list(adaptive_sources))
            return ['container']

        service_key = service_mapping.get(service_var)
        if not service_key:
            return []

        valid_sources = self.get_valid_sources_for_service(service_key)
        return sorted(list(valid_sources))
    
    def suggest_valid_source(self, service_var: str, invalid_source: str) -> Optional[str]:
        """
        Suggest a valid SOURCE value for an invalid one.
        
        Args:
            service_var: SOURCE variable name
            invalid_source: The invalid source value
            
        Returns:
            str: Suggested valid source, or None if no suggestion
        """
        valid_options = self.get_service_source_options(service_var)
        
        if not valid_options:
            return None
            
        # Simple suggestion logic - return first valid option
        # Could be enhanced with fuzzy matching in the future
        return valid_options[0] if valid_options else None
