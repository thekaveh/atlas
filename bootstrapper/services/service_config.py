"""
Service configuration generation based on YAML and SOURCE values.

Python implementation of generate_service_environment() and related functions from start.sh.
"""

import os
import re
from typing import Dict, Any, Optional
from core.config_parser import ConfigParser
from utils.system import get_localhost_host, resolve_host_gateway_ip


class ServiceConfig:
    """Generates service configurations based on YAML and SOURCE values."""
    
    def __init__(self, config_parser: Optional[ConfigParser] = None):
        """
        Initialize service configuration manager.
        
        Args:
            config_parser: ConfigParser instance (creates new one if None)
        """
        self.config_parser = config_parser or ConfigParser()
        self.yaml_config = None
        self.service_sources = {}
        self.localhost_host = get_localhost_host()
        
    def load_config(self) -> bool:
        """
        Load YAML configuration and service sources.
        
        Returns:
            bool: True if loaded successfully
        """
        try:
            self.yaml_config = self.config_parser.load_yaml_config()
            self.service_sources = self.config_parser.parse_service_sources()
            return True
        except Exception as e:
            print(f"❌ Failed to load configuration: {e}")
            return False
    
    def get_service_config(self, service_key: str, source_value: str) -> Dict[str, Any]:
        """
        Get configuration for a specific service and source.
        
        Args:
            service_key: Service key in YAML (e.g., "llm_provider")
            source_value: SOURCE value (e.g., "ollama-container-cpu")
            
        Returns:
            dict: Service configuration from YAML
        """
        if not self.yaml_config:
            return {}
            
        source_configurable = self.yaml_config.get('source_configurable', {})
        service_configs = source_configurable.get(service_key, {})
        return service_configs.get(source_value, {})
    
    def generate_service_environment(self) -> Dict[str, str]:
        """
        Generate all service environment variables based on YAML configuration.
        Replicates the generate_service_environment() function from start.sh.

        Returns:
            dict: Dictionary of environment variables to set
        """
        if not self.load_config():
            return {}

        env_vars = {}

        # Resolve host gateway IP for extra_hosts compatibility (Docker vs Podman)
        env_vars['HOST_GATEWAY_IP'] = resolve_host_gateway_ip()

        # Refresh image-pin env vars from manifest defaults. Without this,
        # users who pulled a tag-bump (e.g. postgres-exporter v0.16→v0.18 in
        # PR #62) keep running the old image because the bootstrapper
        # historically preserved their existing .env value. Image pins are
        # deterministic — the manifest is the source of truth. Users who
        # genuinely want to pin a different image should shell-export the
        # var (compose interpolation honors shell env over .env).
        env_vars.update(self._refresh_image_pins_from_manifests())

        # Generate LLM Provider (Ollama) configuration
        llm_config = self._generate_llm_provider_config()
        env_vars.update(llm_config)

        # Generate cloud-provider toggles for the LiteLLM gateway
        cloud_config = self._generate_cloud_providers_config()
        env_vars.update(cloud_config)

        # Generate ComfyUI configuration
        comfyui_config = self._generate_comfyui_config()
        env_vars.update(comfyui_config)
        
        # Generate MinIO configuration
        minio_config = self._generate_minio_config()
        env_vars.update(minio_config)

        # Generate Weaviate configuration
        weaviate_config = self._generate_weaviate_config()
        env_vars.update(weaviate_config)
        
        # Generate Multi2Vec CLIP configuration
        clip_config = self._generate_clip_config()
        env_vars.update(clip_config)

        # Generate STT and TTS Provider configuration.
        # We pass the running env_vars dict through both so the TTS pass sees
        # any SPEACHES_SCALE / COMPOSE_PROFILES that STT already set — this is
        # how the speaches dedup avoids double-adding profile or scale.
        #
        # COMPOSE_PROFILES is fully owned by this pipeline: seed it empty so
        # the final value reflects exactly this run's active sources. Without
        # the seed, a run in which no generator adds a profile leaves the key
        # out of the dict, and update_env_file() then preserves a stale value
        # in .env (e.g. a docling-gpu profile from a since-disabled source).
        env_vars['COMPOSE_PROFILES'] = ''
        stt_config = self._generate_stt_provider_config(shared_env=env_vars)
        env_vars.update(stt_config)

        tts_config = self._generate_tts_provider_config(shared_env=env_vars)
        env_vars.update(tts_config)

        # Resolve the speaches image for the WINNING profile. The compose
        # fragment is a single service interpolating ${SPEACHES_IMAGE}
        # under both profiles, so the gpu profile previously ran the CPU
        # image despite three docs claiming SPEACHES_GPU_IMAGE "is
        # selected by the profile". The pin refresher (top of this
        # method) resets SPEACHES_IMAGE to the manifest CPU default every
        # run, so a gpu→cpu switch self-heals; shell-exported pins win at
        # compose interpolation regardless.
        _profiles_now = (env_vars.get('COMPOSE_PROFILES') or '').split(',')
        if 'speaches-gpu' in _profiles_now:
            # Precedence mirrors the refresher's documented override
            # story: a shell-exported pin wins (the refresher skips
            # shell-exported vars, so env_vars would otherwise carry a
            # stale .env value or nothing); else the refresher-loaded
            # manifest default in env_vars. One of the two is always
            # non-empty, so the gpu profile can't silently fall back to
            # the CPU image.
            import os as _os
            gpu_image = (
                (_os.environ.get('SPEACHES_GPU_IMAGE') or '').strip()
                or (self._resolved_env('SPEACHES_GPU_IMAGE', env_vars) or '').strip()
            )
            if gpu_image:
                env_vars['SPEACHES_IMAGE'] = gpu_image

        # Generate Document Processor configuration
        doc_config = self._generate_doc_processor_config(shared_env=env_vars)
        env_vars.update(doc_config)

        # Generate OpenClaw configuration
        openclaw_config = self._generate_openclaw_config()
        env_vars.update(openclaw_config)

        # Generate Hermes Agent configuration
        hermes_config = self._generate_hermes_config()
        env_vars.update(hermes_config)

        # Generate TEI Reranker configuration
        tei_reranker_config = self._generate_tei_reranker_config()
        env_vars.update(tei_reranker_config)

        # Generate LightRAG configuration
        env_vars.update(self._generate_lightrag_config())

        # Generate vLLM Metal (managed-localhost) configuration
        env_vars.update(self._generate_vllm_metal_config())

        # Generate Local Deep Researcher extraction mode and Crawl4AI API
        # configuration. Crawl4AI runs after the LDR mode helper so the
        # service-level endpoint remains available to n8n whenever the service
        # itself is enabled, even if LDR full-page mode is disabled.
        env_vars.update(self._generate_local_deep_researcher_extraction_config())
        env_vars.update(self._generate_crawl4ai_config())
        env_vars.update(self._generate_tika_config())
        env_vars.update(self._generate_llm_graph_builder_config())
        env_vars.update(self._generate_celery_config())
        env_vars.update(self._generate_supavisor_config())

        # Generate Ray cluster configuration
        ray_source = self.service_sources.get("RAY_SOURCE", "disabled")
        ray_config = self._generate_ray_config(
            source_value=ray_source,
            shared_env=env_vars,
        )
        env_vars.update(ray_config)

        # Generate Spark cluster configuration
        spark_config = self._generate_spark_config()
        env_vars.update(spark_config)

        # Generate Zeppelin configuration (hard-gated on Spark)
        zeppelin_config = self._generate_zeppelin_config()
        env_vars.update(zeppelin_config)

        # Generate Jenkins configuration (hard-gated on MinIO artifact storage)
        jenkins_config = self._generate_jenkins_config()
        env_vars.update(jenkins_config)

        # Generate MLflow configuration (hard-gated on MinIO artifact storage)
        env_vars.update(self._generate_mlflow_config())
        env_vars.update(self._generate_label_studio_config())
        env_vars.update(self._generate_verba_config())

        # Generate curated MCP servers configuration (hard-gated on graph/search targets)
        mcp_servers_config = self._generate_mcp_servers_config()
        env_vars.update(mcp_servers_config)

        # Generate Langfuse LLM observability configuration (hard-gated on MinIO)
        langfuse_config = self._generate_langfuse_config()
        env_vars.update(langfuse_config)

        # Generate OpenTelemetry / Tempo / Loki tracing and log-store configuration.
        otel_config = self._generate_otel_tempo_loki_config()
        env_vars.update(otel_config)

        # Generate Airflow configuration (3-container family scales)
        airflow_config = self._generate_airflow_config()
        env_vars.update(airflow_config)

        # Generate Iceberg REST catalog configuration (REST + DB init scales)
        iceberg_config = self._generate_iceberg_rest_config()
        env_vars.update(iceberg_config)

        # Generate Trino query-engine configuration (hard-gated on lakehouse)
        trino_config = self._generate_trino_config()
        env_vars.update(trino_config)

        # Generate Redpanda Kafka API broker + Spark streaming endpoint.
        redpanda_config = self._generate_redpanda_config()
        env_vars.update(redpanda_config)

        # Generate observability bundle (Prometheus family + cross-manifest
        # sidecar exporter scales for postgres-exporter and redis-exporter).
        prometheus_source = self.service_sources.get("PROMETHEUS_SOURCE", "disabled")
        prom_config = self._generate_prometheus_config(prometheus_source)
        env_vars.update(prom_config)

        # Generate Grafana configuration
        grafana_source = self.service_sources.get("GRAFANA_SOURCE", "disabled")
        grafana_config = self._generate_grafana_config(grafana_source)
        env_vars.update(grafana_config)

        # Generate other service configurations
        other_configs = self._generate_other_services_config()
        env_vars.update(other_configs)
        
        # Generate adaptive service configurations (pass accumulated vars for endpoint lookups)
        adaptive_configs = self._generate_adaptive_services_config(all_env_vars=env_vars)
        env_vars.update(adaptive_configs)
        
        return env_vars
    
    def _generate_llm_provider_config(self) -> Dict[str, str]:
        """Generate LLM engine (Ollama upstream) configuration.

        Emits LITELLM_OLLAMA_UPSTREAM (consumed by the LiteLLM config template
        only) plus LITELLM_BASE_URL (consumed by every LLM-using service).
        OLLAMA_SCALE / OLLAMA_NVIDIA_VISIBLE_DEVICES / OLLAMA_DEPLOY_RESOURCES
        still gate the upstream Ollama service block in compose.
        """
        source_value = self.service_sources.get('LLM_PROVIDER_SOURCE', 'ollama-container-cpu')
        config = self.get_service_config('llm_provider', source_value)

        env_vars = {}

        # LiteLLM is mandatory and listens on a fixed internal address.
        env_vars['LITELLM_BASE_URL'] = 'http://litellm:4000'

        # Set scale (Ollama upstream service replicas)
        scale = config.get('scale', 1)
        env_vars['OLLAMA_SCALE'] = str(scale)

        # Resolve the upstream URL (LiteLLM consumes this when LLM_PROVIDER_SOURCE
        # is one of the ollama-* values). Empty string when source=none.
        endpoint = config.get('environment', {}).get('OLLAMA_ENDPOINT', 'http://ollama:11434')
        endpoint = endpoint.replace('host.docker.internal', self.localhost_host)
        env_vars['LITELLM_OLLAMA_UPSTREAM'] = endpoint

        # Set GPU devices if specified
        gpu_devices = config.get('environment', {}).get('NVIDIA_VISIBLE_DEVICES')
        if gpu_devices:
            env_vars['OLLAMA_NVIDIA_VISIBLE_DEVICES'] = gpu_devices
        else:
            env_vars['OLLAMA_NVIDIA_VISIBLE_DEVICES'] = 'null'

        # Set deployment resources
        deploy_resources = config.get('deploy', {})
        if deploy_resources:
            env_vars['OLLAMA_DEPLOY_RESOURCES'] = str(deploy_resources)
        else:
            env_vars['OLLAMA_DEPLOY_RESOURCES'] = '~'

        return env_vars

    def _generate_cloud_providers_config(self) -> Dict[str, str]:
        """Generate cloud-provider toggle env vars consumed by
        ``model_resolver`` (which gates the active entries it returns
        per-provider). Each cloud_* SOURCE is a binary enabled/disabled
        selector. Tuple list lives in utils/cloud_providers.py.
        """
        from utils.cloud_providers import CLOUD_PROVIDERS

        env_vars: Dict[str, str] = {}
        enabled_providers = []
        for provider in CLOUD_PROVIDERS:
            source_value = self.service_sources.get(provider.source_var, 'disabled')
            is_enabled = source_value == 'enabled'
            env_vars[provider.enabled_flag_var] = 'true' if is_enabled else 'false'
            if is_enabled:
                enabled_providers.append(provider.key)

        env_vars['LITELLM_ENABLED_PROVIDERS'] = ','.join(enabled_providers)
        return env_vars
    
    def _generate_comfyui_config(self) -> Dict[str, str]:
        """Generate ComfyUI configuration."""
        source_value = self.service_sources.get('COMFYUI_SOURCE', 'container-cpu')
        config = self.get_service_config('comfyui', source_value)
        
        env_vars = {}
        
        # Set scale
        scale = config.get('scale', 1)
        env_vars['COMFYUI_SCALE'] = str(scale)
        
        # Set endpoint
        endpoint = config.get('environment', {}).get('COMFYUI_ENDPOINT', 'http://comfyui:18188')
        endpoint = endpoint.replace('host.docker.internal', self.localhost_host)
        env_vars['COMFYUI_ENDPOINT'] = endpoint
        
        # Set deployment resources
        deploy_resources = config.get('deploy', {})
        if deploy_resources:
            env_vars['COMFYUI_DEPLOY_RESOURCES'] = str(deploy_resources)
        else:
            env_vars['COMFYUI_DEPLOY_RESOURCES'] = '~'
            
        return env_vars
    
    def _generate_minio_config(self) -> Dict[str, str]:
        """Generate MinIO env vars from the minio manifest's runtime_sc block."""
        source_value = self.service_sources.get('MINIO_SOURCE', 'container')
        config = self.get_service_config('minio', source_value)
        env_vars: Dict[str, str] = {}

        # Scale follows the source variant directly.
        env_vars['MINIO_SCALE'] = str(config.get('scale', 1))

        # MINIO_ENDPOINT — internal Compose-network URL; written as-is from YAML.
        env_vars['MINIO_ENDPOINT'] = config.get('environment', {}).get('MINIO_ENDPOINT', '')

        # MINIO_PUBLIC_ENDPOINT — host S3 API URL; may contain a ${MINIO_PORT} token
        # from the manifest's runtime_sc block. Expand against the current .env value.
        current_env = self.config_parser.parse_env_file()
        public_template = config.get('environment', {}).get('MINIO_PUBLIC_ENDPOINT', '')
        if public_template:
            minio_port = current_env.get('MINIO_PORT', '63020')
            env_vars['MINIO_PUBLIC_ENDPOINT'] = public_template.replace('${MINIO_PORT}', minio_port)
        else:
            env_vars['MINIO_PUBLIC_ENDPOINT'] = ''

        # MINIO_PUBLIC_CONSOLE_ENDPOINT — host console URL; may contain a
        # ${MINIO_CONSOLE_PORT} token from the manifest. Used by MinIO's
        # MINIO_BROWSER_REDIRECT_URL — must point at the console (port 9001 / host
        # MINIO_CONSOLE_PORT), NOT the S3 API.
        console_template = config.get('environment', {}).get('MINIO_PUBLIC_CONSOLE_ENDPOINT', '')
        if console_template:
            console_port = current_env.get('MINIO_CONSOLE_PORT', '63021')
            env_vars['MINIO_PUBLIC_CONSOLE_ENDPOINT'] = console_template.replace('${MINIO_CONSOLE_PORT}', console_port)
        else:
            env_vars['MINIO_PUBLIC_CONSOLE_ENDPOINT'] = ''

        return env_vars

    def _generate_weaviate_config(self) -> Dict[str, str]:
        """Generate Weaviate configuration."""
        source_value = self.service_sources.get('WEAVIATE_SOURCE', 'container')
        config = self.get_service_config('weaviate', source_value)
        
        env_vars = {}
        
        # Set scale
        scale = config.get('scale', 1)
        env_vars['WEAVIATE_SCALE'] = str(scale)
        
        # Set URL
        weaviate_url = config.get('environment', {}).get('WEAVIATE_URL', 'http://weaviate:8080')
        weaviate_url = weaviate_url.replace('host.docker.internal', self.localhost_host)
        env_vars['WEAVIATE_URL'] = weaviate_url
        
        # Weaviate's text2vec-openai / generative-openai modules talk to LiteLLM.
        # The base URL goes into per-collection module configs (set by
        # weaviate-init), not into Weaviate's startup env.
        env_file_vars = self.config_parser.parse_env_file()
        env_vars['WEAVIATE_LITELLM_API_KEY'] = env_file_vars.get('LITELLM_MASTER_KEY', '')

        # Multi2Vec CLIP is optional. If its service is disabled/scaled to zero,
        # Weaviate must not enable the multi2vec-clip module or it will block
        # startup waiting for a missing remote inference API. Start from the
        # configured module list so advanced users keep any extra modules while
        # the CLIP module is toggled to match MULTI2VEC_CLIP_SOURCE.
        clip_source = self.service_sources.get('MULTI2VEC_CLIP_SOURCE', 'container-cpu')
        default_modules = (
            'text2vec-openai,text2vec-ollama,multi2vec-clip,'
            'generative-openai,generative-ollama'
        )
        configured_modules = env_file_vars.get('WEAVIATE_ENABLE_MODULES', default_modules)
        weaviate_modules = [
            module.strip()
            for module in configured_modules.split(',')
            if module.strip()
        ]

        if clip_source == 'disabled':
            weaviate_modules = [
                module for module in weaviate_modules if module != 'multi2vec-clip'
            ]
            env_vars['CLIP_INFERENCE_API'] = ''
        else:
            if 'multi2vec-clip' not in weaviate_modules:
                insert_after = 'text2vec-ollama'
                insert_index = (
                    weaviate_modules.index(insert_after) + 1
                    if insert_after in weaviate_modules
                    else len(weaviate_modules)
                )
                weaviate_modules.insert(insert_index, 'multi2vec-clip')
            env_vars['CLIP_INFERENCE_API'] = env_file_vars.get(
                'CLIP_INFERENCE_API', 'http://multi2vec-clip:8080'
            ) or 'http://multi2vec-clip:8080'

        env_vars['WEAVIATE_ENABLE_MODULES'] = ','.join(weaviate_modules)

        return env_vars
    
    def _generate_clip_config(self) -> Dict[str, str]:
        """Generate Multi2Vec CLIP configuration."""
        source_value = self.service_sources.get('MULTI2VEC_CLIP_SOURCE', 'container-cpu')
        config = self.get_service_config('multi2vec-clip', source_value)
        
        env_vars = {}
        
        # Set scale
        scale = config.get('scale', 1)  
        env_vars['CLIP_SCALE'] = str(scale)
        
        # Set CUDA enable flag
        cuda_flag = config.get('environment', {}).get('ENABLE_CUDA', '0')
        env_vars['CLIP_ENABLE_CUDA'] = cuda_flag
        
        # Set deployment resources
        deploy_resources = config.get('deploy', {})
        if deploy_resources:
            env_vars['CLIP_DEPLOY_RESOURCES'] = str(deploy_resources)
        else:
            env_vars['CLIP_DEPLOY_RESOURCES'] = '~'
            
        return env_vars

    def _add_compose_profile(self, env_vars: Dict[str, str], profile: str) -> None:
        """Append a docker-compose profile to COMPOSE_PROFILES idempotently.

        Reads the running tally from ``env_vars`` (seeded empty at the top of
        generate_service_environment, so each run rebuilds the value from
        scratch). Skips the add if ``profile`` is already present. Used by
        the speaches dedup path — if both TTS and STT pick speaches, both
        generators try to add the same profile and we don't want it
        duplicated in COMPOSE_PROFILES.
        """
        current = env_vars.get('COMPOSE_PROFILES', '') or ''
        existing = [p for p in current.split(',') if p]
        if profile in existing:
            return
        existing.append(profile)
        env_vars['COMPOSE_PROFILES'] = ','.join(existing)

    def _generate_stt_provider_config(self, shared_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Generate STT Provider configuration.

        ``shared_env`` carries env vars accumulated by earlier generators so
        we can stack COMPOSE_PROFILES additions correctly (and so the TTS
        generator, when it runs next, sees that SPEACHES_SCALE is already 1).
        """
        source_value = self.service_sources.get('STT_PROVIDER_SOURCE', 'disabled')
        config = self.get_service_config('stt_provider', source_value)

        env_vars: Dict[str, str] = dict(shared_env or {})

        # Default: STT not running (zero scale, blank endpoint). Each
        # branch below flips the bits it owns. The provider-level scale
        # is consumed by the wizard ServiceTable to colour the row.
        env_vars.setdefault('STT_PROVIDER_SCALE', '0')

        # Endpoint comes from the YAML entry; for ``http://host.docker.internal``
        # URLs we swap in the platform-correct gateway hostname.
        endpoint = config.get('environment', {}).get('STT_ENDPOINT', '')
        endpoint = endpoint.replace('host.docker.internal', self.localhost_host)
        env_vars['STT_ENDPOINT'] = endpoint

        if source_value.startswith('speaches-container'):
            env_vars['SPEACHES_SCALE'] = '1'
            profile = 'speaches-gpu' if source_value.endswith('-gpu') else 'speaches-cpu'
            self._add_compose_profile(env_vars, profile)
            # Mirror the speaches external port into the wizard's STT slot
            # so the row shows the right :port without resorting to a
            # per-source port_var in state_builder.
            speaches_port = self._resolved_env('SPEACHES_PORT', env_vars)
            if speaches_port:
                env_vars['STT_PROVIDER_PORT'] = speaches_port
            env_vars['STT_PROVIDER_SCALE'] = '1'
            # Parakeet stays off in this branch.
            env_vars.setdefault('PARAKEET_GPU_SCALE', '0')
        elif source_value == 'parakeet-container-gpu':
            env_vars['PARAKEET_GPU_SCALE'] = '1'
            self._add_compose_profile(env_vars, 'parakeet-gpu')
            env_vars['STT_PROVIDER_SCALE'] = '1'
            env_vars.setdefault('SPEACHES_SCALE', '0')
        elif source_value in ('parakeet-localhost', 'whisper-cpp-localhost'):
            env_vars['PARAKEET_GPU_SCALE'] = '0'
            env_vars.setdefault('SPEACHES_SCALE', '0')
            # STT_PROVIDER_SCALE stays 0 — wizard reads port from URL
        else:  # disabled
            env_vars['PARAKEET_GPU_SCALE'] = '0'
            env_vars.setdefault('SPEACHES_SCALE', '0')
            env_vars['STT_ENDPOINT'] = ''

        return env_vars

    def _generate_tts_provider_config(self, shared_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Generate TTS Provider configuration.

        See ``_generate_stt_provider_config`` for the role of ``shared_env``;
        same dedup pattern, applied symmetrically.
        """
        source_value = self.service_sources.get('TTS_PROVIDER_SOURCE', 'disabled')
        config = self.get_service_config('tts_provider', source_value)

        env_vars: Dict[str, str] = dict(shared_env or {})
        env_vars.setdefault('TTS_PROVIDER_SCALE', '0')

        endpoint = config.get('environment', {}).get('TTS_ENDPOINT', '')
        endpoint = endpoint.replace('host.docker.internal', self.localhost_host)
        env_vars['TTS_ENDPOINT'] = endpoint

        if source_value.startswith('speaches-container'):
            # If STT also picked speaches, SPEACHES_SCALE is already 1 and
            # the profile is already in COMPOSE_PROFILES — both adds are
            # idempotent. If STT picked something else, this is the only
            # place speaches gets activated.
            env_vars['SPEACHES_SCALE'] = '1'
            wanted_profile = 'speaches-gpu' if source_value.endswith('-gpu') else 'speaches-cpu'
            stt_source = self.service_sources.get('STT_PROVIDER_SOURCE', 'disabled')
            if stt_source.startswith('speaches-container'):
                # Mixed cpu/gpu: GPU wins. Remove cpu profile if present;
                # add gpu. Either source value already added its own profile,
                # so we only re-add when the resolved winner differs.
                stt_is_gpu = stt_source.endswith('-gpu')
                tts_is_gpu = source_value.endswith('-gpu')
                if stt_is_gpu != tts_is_gpu:
                    wanted_profile = 'speaches-gpu'
                    self._remove_compose_profile(env_vars, 'speaches-cpu')
                    print(
                        "ℹ️  Speaches CPU/GPU mismatch between TTS_PROVIDER_SOURCE "
                        f"({source_value}) and STT_PROVIDER_SOURCE ({stt_source}); "
                        "using speaches-gpu for both."
                    )
            self._add_compose_profile(env_vars, wanted_profile)
            speaches_port = self._resolved_env('SPEACHES_PORT', env_vars)
            if speaches_port:
                env_vars['TTS_PROVIDER_PORT'] = speaches_port
            env_vars['TTS_PROVIDER_SCALE'] = '1'
            env_vars.setdefault('CHATTERBOX_SCALE', '0')
        elif source_value == 'chatterbox-container-gpu':
            env_vars['CHATTERBOX_SCALE'] = '1'
            self._add_compose_profile(env_vars, 'chatterbox-gpu')
            chatterbox_port = self._resolved_env('CHATTERBOX_PORT', env_vars)
            if chatterbox_port:
                env_vars['TTS_PROVIDER_PORT'] = chatterbox_port
            env_vars['TTS_PROVIDER_SCALE'] = '1'
            env_vars.setdefault('SPEACHES_SCALE', '0')
        elif source_value == 'chatterbox-localhost':
            env_vars['CHATTERBOX_SCALE'] = '0'
            env_vars.setdefault('SPEACHES_SCALE', '0')
        else:  # disabled
            env_vars['CHATTERBOX_SCALE'] = '0'
            env_vars.setdefault('SPEACHES_SCALE', '0')
            env_vars['TTS_ENDPOINT'] = ''

        return env_vars

    def _remove_compose_profile(self, env_vars: Dict[str, str], profile: str) -> None:
        """Drop a profile from COMPOSE_PROFILES if present (no-op otherwise)."""
        current = env_vars.get('COMPOSE_PROFILES', '') or ''
        existing = [p for p in current.split(',') if p and p != profile]
        env_vars['COMPOSE_PROFILES'] = ','.join(existing)

    def _resolved_env(self, var: str, env_vars: Dict[str, str]) -> str:
        """Look up ``var`` in this run's accumulated env, then .env, then ''.

        Used by the TTS/STT generators to read the speaches/chatterbox port
        slots that the port allocator wrote earlier in the pipeline, and
        by the speaches image resolution to read the SPEACHES_GPU_IMAGE
        pin (whose shell-export precedence lives at the caller).
        """
        if var in env_vars:
            return env_vars[var]
        return self.config_parser.parse_env_file().get(var, '')

    def _generate_doc_processor_config(self, shared_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Generate Document Processor (Docling) configuration.

        ``shared_env`` carries env vars accumulated by earlier generators so
        the docling-gpu profile stacks onto COMPOSE_PROFILES instead of
        clobbering the STT/TTS profiles added before it.
        """
        source_value = self.service_sources.get('DOC_PROCESSOR_SOURCE', 'disabled')
        config = self.get_service_config('doc_processor', source_value)

        env_vars: Dict[str, str] = dict(shared_env or {})

        # Set DOCLING_ENDPOINT with localhost replacement (matching STT/TTS pattern)
        if source_value == 'disabled':
            env_vars['DOCLING_ENDPOINT'] = ''
        else:
            endpoint = config.get('environment', {}).get('DOCLING_ENDPOINT', 'http://host.docker.internal:18159')
            # For localhost mode, dynamically replace the port with the user-
            # overridable DOCLING_LOCALHOST_PORT (NOT DOC_PROCESSOR_PORT —
            # that's the container's host-bound port). The wizard writes the
            # user's host port to DOCLING_LOCALHOST_PORT; reading the wrong
            # var here silently strands the override (asymmetric-override
            # class — see feedback_localhost_url_override_symmetry.md).
            if source_value == 'docling-localhost':
                current_env = self.config_parser.parse_env_file()
                doc_port = current_env.get('DOCLING_LOCALHOST_PORT', '18159')
                endpoint = f'http://{self.localhost_host}:{doc_port}'
            else:
                # For container mode, just apply localhost_host replacement
                endpoint = endpoint.replace('host.docker.internal', self.localhost_host)
            env_vars['DOCLING_ENDPOINT'] = endpoint

        # Set scale and activate profile based on SOURCE
        if source_value == 'docling-container-gpu':
            env_vars['DOCLING_GPU_SCALE'] = '1'
            # Activate docling-gpu and doc-gpu profiles to enable building the GPU service
            self._add_compose_profile(env_vars, 'docling-gpu')
            self._add_compose_profile(env_vars, 'doc-gpu')
        elif source_value == 'docling-localhost':
            env_vars['DOCLING_GPU_SCALE'] = '0'
        else:  # disabled
            env_vars['DOCLING_GPU_SCALE'] = '0'

        return env_vars

    def _generate_hermes_config(self) -> Dict[str, str]:
        """Generate Hermes Agent (programmable AI agent runtime) configuration.

        Mirrors _generate_openclaw_config(): drives HERMES_SCALE and
        HERMES_ENDPOINT from HERMES_SOURCE, with localhost replacement
        for cross-platform host-gateway addressing. HERMES_INIT_SCALE
        is set in _generate_other_services_config() to keep init-scale
        logic centralized.
        """
        source_value = self.service_sources.get('HERMES_SOURCE', 'container')
        config = self.get_service_config('hermes', source_value)

        env_vars: Dict[str, str] = {}

        if source_value == 'disabled':
            env_vars['HERMES_ENDPOINT'] = ''
            env_vars['HERMES_SCALE'] = '0'
        elif source_value == 'localhost':
            # HERMES_LOCALHOST_PORT is the user-overridable var the wizard
            # writes for host-side Hermes. Reading HERMES_API_PORT here would
            # always be the container's host-bound port, silently
            # stranding any port override — same asymmetric-override class
            # as docling above (feedback_localhost_url_override_symmetry.md).
            current_env = self.config_parser.parse_env_file()
            hermes_port = current_env.get('HERMES_LOCALHOST_PORT', '8642')
            endpoint = f'http://{self.localhost_host}:{hermes_port}'
            env_vars['HERMES_ENDPOINT'] = endpoint
            env_vars['HERMES_SCALE'] = '0'
        else:  # container
            endpoint = config.get('environment', {}).get(
                'HERMES_ENDPOINT', 'http://hermes:8642')
            endpoint = endpoint.replace('host.docker.internal', self.localhost_host)
            env_vars['HERMES_ENDPOINT'] = endpoint
            env_vars['HERMES_SCALE'] = '1'

        return env_vars

    def _generate_tei_reranker_config(self) -> Dict[str, str]:
        """Resolve TEI Reranker endpoint, scale, and per-source image.

        For container-cpu, picks the arm64 image on arm64 hosts and the
        amd64 image otherwise. This matters because TEI's amd64 cpu-1.9
        image uses the ORT backend (requires ONNX weights), while the
        arm64 image uses the candle backend (loads safetensors natively).
        The default model (mxbai-rerank-base-v1) ships ONNX so both
        backends work; some models like bge-reranker-v2-m3 are safetensors-
        only AND too heavy for the arm64 candle backend's warmup path.
        """
        import platform as _platform
        source_value = self.service_sources.get('TEI_RERANKER_SOURCE', 'disabled')
        env_vars: Dict[str, str] = {}
        current_env = self.config_parser.parse_env_file()
        cpu_image = current_env.get(
            'TEI_RERANKER_CPU_IMAGE',
            'ghcr.io/huggingface/text-embeddings-inference:cpu-1.9',
        )
        cpu_arm64_image = current_env.get(
            'TEI_RERANKER_CPU_ARM64_IMAGE',
            'ghcr.io/huggingface/text-embeddings-inference:cpu-arm64-latest',
        )
        gpu_image = current_env.get(
            'TEI_RERANKER_GPU_IMAGE',
            'ghcr.io/huggingface/text-embeddings-inference:1.9',
        )
        host_is_arm64 = _platform.machine().lower() in ('arm64', 'aarch64')
        container_cpu_image = cpu_arm64_image if host_is_arm64 else cpu_image

        if source_value == 'disabled':
            env_vars['TEI_RERANKER_ENDPOINT'] = ''
            env_vars['TEI_RERANKER_SCALE'] = '0'
            env_vars['TEI_RERANKER_IMAGE_RESOLVED'] = container_cpu_image
        elif source_value == 'localhost':
            port = current_env.get('TEI_RERANKER_LOCALHOST_PORT', '63049')
            env_vars['TEI_RERANKER_ENDPOINT'] = f'http://{self.localhost_host}:{port}'
            env_vars['TEI_RERANKER_SCALE'] = '0'
            env_vars['TEI_RERANKER_IMAGE_RESOLVED'] = container_cpu_image
        else:  # container-cpu | container-gpu
            env_vars['TEI_RERANKER_ENDPOINT'] = 'http://tei-reranker:80'
            env_vars['TEI_RERANKER_SCALE'] = '1'
            env_vars['TEI_RERANKER_IMAGE_RESOLVED'] = (
                gpu_image if source_value == 'container-gpu' else container_cpu_image
            )
        return env_vars

    def _generate_lightrag_config(self) -> Dict[str, str]:
        """Resolve LightRAG endpoint and init scale per source.

        Storage URI adaptation (PG/Neo4j/Redis) happens in
        _generate_adaptive_services_config since those are listed in
        service.yml::runtime_adaptive.environment_adaptation.
        """
        source_value = self.service_sources.get('LIGHTRAG_SOURCE', 'disabled')
        env_vars: Dict[str, str] = {}
        if source_value == 'disabled':
            env_vars['LIGHTRAG_ENDPOINT'] = ''
            env_vars['LIGHTRAG_SCALE'] = '0'
            env_vars['LIGHTRAG_INIT_SCALE'] = '0'
        elif source_value == 'localhost':
            current_env = self.config_parser.parse_env_file()
            port = current_env.get('LIGHTRAG_LOCALHOST_PORT', '63068')
            env_vars['LIGHTRAG_ENDPOINT'] = f'http://{self.localhost_host}:{port}'
            env_vars['LIGHTRAG_SCALE'] = '0'
            env_vars['LIGHTRAG_INIT_SCALE'] = '0'
        else:  # container
            cfg = self.get_service_config('lightrag', source_value)
            endpoint = cfg.get('environment', {}).get(
                'LIGHTRAG_ENDPOINT', 'http://lightrag:9621'
            )
            env_vars['LIGHTRAG_ENDPOINT'] = endpoint.replace(
                'host.docker.internal', self.localhost_host
            )
            env_vars['LIGHTRAG_SCALE'] = '1'
            env_vars['LIGHTRAG_INIT_SCALE'] = '1'
        return env_vars

    def _generate_vllm_metal_config(self) -> Dict[str, str]:
        """Resolve vLLM Metal (managed-localhost) endpoint and scale.

        vLLM Metal is a virtual, managed-localhost-only service: the
        bootstrapper installs and supervises a native vLLM process on the
        host (Apple-silicon Metal backend, via the ``vllm-metal`` plugin)
        and registers its OpenAI-compatible endpoint with LiteLLM. There is
        no container source and no Kong route — consumers reach the model
        only through LiteLLM's `/v1` upstream, so the sole auto-managed
        outputs are the docker-internal endpoint and a scale sentinel.

        Mirrors _generate_lightrag_config's `localhost` branch: the endpoint
        resolves to ``http://<localhost_host>:<VLLM_METAL_LOCALHOST_PORT>``
        (host.docker.internal from inside compose) and scale stays 0 because
        nothing runs as a container.
        """
        source_value = self.service_sources.get('VLLM_METAL_SOURCE', 'disabled')
        env_vars: Dict[str, str] = {}
        if source_value == 'managed-localhost':
            current_env = self.config_parser.parse_env_file()
            port = current_env.get('VLLM_METAL_LOCALHOST_PORT', '8000')
            env_vars['VLLM_METAL_ENDPOINT'] = f'http://{self.localhost_host}:{port}'
            env_vars['VLLM_METAL_SCALE'] = '0'
        else:  # disabled
            env_vars['VLLM_METAL_ENDPOINT'] = ''
            env_vars['VLLM_METAL_SCALE'] = '0'
        return env_vars

    def _generate_ray_config(self, source_value: str, shared_env: dict) -> dict:
        """Resolve Ray's auto-managed env vars from RAY_SOURCE + RAY_WORKER_COUNT.

        Sets four env vars based on the active source:
          - RAY_IMAGE — CPU or GPU image tag (compose interpolates this)
          - RAY_HEAD_SCALE — 1 when container source, 0 otherwise
          - RAY_WORKER_SCALE — RAY_WORKER_COUNT when container source, 0 otherwise
          - RAY_ADDRESS — `ray://ray-head:10001` for container sources,
            empty otherwise

        Args:
            source_value: Current RAY_SOURCE value (one of `ray-container-cpu`,
                `ray-container-gpu`, `disabled`).
            shared_env: Env vars accumulated by earlier generators + manifest
                defaults. We read `RAY_IMAGE` / `RAY_GPU_IMAGE` from here (the
                image-pin refresher seeds them). `RAY_WORKER_COUNT` is NOT in
                shared_env (the pipeline builds env_vars fresh and never adds
                it), so it is read from `.env` on disk — mirroring how
                _generate_spark_config reads SPARK_WORKER_COUNT.

        Returns:
            Dict of resolved env-var assignments. The caller merges this into
            the .env-example output.
        """
        cpu_image = shared_env.get("RAY_IMAGE", "rayproject/ray:2.56.0") or "rayproject/ray:2.56.0"
        gpu_image = shared_env.get("RAY_GPU_IMAGE", "rayproject/ray:2.56.0-gpu") or "rayproject/ray:2.56.0-gpu"

        # Read RAY_WORKER_COUNT from disk (where the wizard/CLI persists the
        # user's --ray-worker-count) with a safe fallback to the manifest
        # default (2). Reading shared_env here would silently ignore the
        # override, since env_vars never carries RAY_WORKER_COUNT.
        raw_count = self.config_parser.parse_env_file().get(
            "RAY_WORKER_COUNT", shared_env.get("RAY_WORKER_COUNT", "2")
        )
        try:
            worker_count = int(raw_count)
            if worker_count < 0:
                worker_count = 2
        except (ValueError, TypeError):
            worker_count = 2

        if source_value == "ray-container-cpu":
            return {
                "RAY_IMAGE": cpu_image,
                "RAY_HEAD_SCALE": "1",
                "RAY_WORKER_SCALE": str(worker_count),
                "RAY_ADDRESS": "ray://ray-head:10001",
            }
        if source_value == "ray-container-gpu":
            return {
                "RAY_IMAGE": gpu_image,
                "RAY_HEAD_SCALE": "1",
                "RAY_WORKER_SCALE": str(worker_count),
                "RAY_ADDRESS": "ray://ray-head:10001",
            }
        # disabled (or any unknown source value): everything off, no address
        return {
            "RAY_IMAGE": cpu_image,
            "RAY_HEAD_SCALE": "0",
            "RAY_WORKER_SCALE": "0",
            "RAY_ADDRESS": "",
        }

    def _generate_spark_config(self) -> dict:
        """Generate SPARK_*_SCALE env vars based on SPARK_SOURCE + SPARK_WORKER_COUNT.

        Spark is a 5-container family (master + worker + history + connect + init).
        When the source is `container`, all five scale up (worker count is
        clamped to 1-8 per the wizard contract). When `disabled`, all five
        scale=0. spark-connect is a sidecar to spark-master that runs
        apache/spark's start-connect-server.sh (the upstream path for
        binding the gRPC Connect listener on port 15002).

        Hard-fails if SPARK_SOURCE=container but MINIO_SOURCE=disabled.
        spark-init bootstraps the spark-history bucket via minio-init and
        `depends_on: minio-init: condition: service_completed_successfully`
        — with MinIO off, minio-init never starts and the stack hangs at
        compose-up. Surface the conflict at source-resolution time instead.
        Mirrors the Zeppelin → Spark gate in `_generate_zeppelin_config`.
        """
        source_value = self.service_sources.get("SPARK_SOURCE", "disabled")
        env_vars: dict[str, str] = {}

        if source_value == "disabled":
            env_vars["SPARK_MASTER_SCALE"] = "0"
            env_vars["SPARK_WORKER_SCALE"] = "0"
            env_vars["SPARK_HISTORY_SCALE"] = "0"
            env_vars["SPARK_INIT_SCALE"] = "0"
            env_vars["SPARK_CONNECT_SCALE"] = "0"
            return env_vars

        minio_source = self.service_sources.get("MINIO_SOURCE", "disabled")
        if minio_source == "disabled":
            raise ValueError(
                "Spark requires MinIO to be enabled. spark-init bootstraps "
                "the spark-history bucket via minio-init and would hang at "
                "compose-up without it. Either pass --minio-source container "
                "alongside --spark-source container, or set --spark-source disabled."
            )

        current_env = self.config_parser.parse_env_file()
        raw_count = current_env.get("SPARK_WORKER_COUNT", "2")
        try:
            worker_count = max(1, min(8, int(raw_count)))
        except (TypeError, ValueError):
            worker_count = 2

        env_vars["SPARK_MASTER_SCALE"] = "1"
        env_vars["SPARK_WORKER_SCALE"] = str(worker_count)
        env_vars["SPARK_HISTORY_SCALE"] = "1"
        env_vars["SPARK_INIT_SCALE"] = "1"
        env_vars["SPARK_CONNECT_SCALE"] = "1"
        return env_vars

    def _generate_zeppelin_config(self) -> dict:
        """Generate Zeppelin family scales. Hard-fails if Zeppelin=container but Spark=disabled.

        Zeppelin's value collapses without Spark — the pre-configured Spark
        interpreter has nothing to connect to. Raising at source-resolution
        time surfaces an actionable error rather than letting the container
        boot into a broken state."""
        z_source = self.service_sources.get("ZEPPELIN_SOURCE", "disabled")
        s_source = self.service_sources.get("SPARK_SOURCE", "disabled")
        if z_source == "disabled":
            return {
                "ZEPPELIN_SCALE": "0",
                "ZEPPELIN_INIT_SCALE": "0",
            }
        if z_source == "container" and s_source == "disabled":
            raise ValueError(
                "Zeppelin requires Spark to be enabled. "
                "Either pass --spark-source container alongside "
                "--zeppelin-source container, or set --zeppelin-source disabled."
            )
        return {
            "ZEPPELIN_SCALE": "1",
            "ZEPPELIN_INIT_SCALE": "1",
        }

    def _generate_jenkins_config(self) -> dict:
        """Generate JENKINS_SCALE.

        Jenkins is optional, but its Atlas contract is specifically the Maven
        builder plus MinIO JAR publishing seam. Running it without MinIO would
        boot a UI that cannot satisfy the advertised artifact path, so fail
        early instead of letting compose wait on minio-init forever.
        """
        source = self.service_sources.get("JENKINS_SOURCE", "disabled")
        if source == "disabled":
            return {"JENKINS_SCALE": "0"}

        minio_source = self.service_sources.get("MINIO_SOURCE", "disabled")
        if minio_source == "disabled":
            raise ValueError(
                "Jenkins requires MinIO to be enabled for artifact publishing. "
                "Either pass --minio-source container alongside "
                "--jenkins-source container, or set --jenkins-source disabled."
            )

        return {"JENKINS_SCALE": "1"}

    def _generate_mlflow_config(self) -> dict:
        """Generate MLflow scales and tracking endpoint.

        MLflow's Atlas contract is a tracking server backed by Supabase
        Postgres plus MinIO artifacts. Running it without MinIO would boot a
        UI that cannot persist artifacts, so fail before compose starts.
        """
        source = self.service_sources.get("MLFLOW_SOURCE", "disabled")
        if source == "disabled":
            return {
                "MLFLOW_INIT_SCALE": "0",
                "MLFLOW_SCALE": "0",
                "MLFLOW_ENDPOINT": "",
                "MLFLOW_TRACKING_URI": "",
            }

        minio_source = self.service_sources.get("MINIO_SOURCE", "disabled")
        if minio_source == "disabled":
            raise ValueError(
                "MLflow requires MinIO to be enabled for artifact storage. "
                "Either pass --minio-source container alongside "
                "--mlflow-source container, or set --mlflow-source disabled."
            )

        return {
            "MLFLOW_INIT_SCALE": "1",
            "MLFLOW_SCALE": "1",
            "MLFLOW_ENDPOINT": "http://mlflow:5000",
            "MLFLOW_TRACKING_URI": "http://mlflow:5000",
        }

    def _generate_label_studio_config(self) -> dict:
        """Generate Label Studio scales and notebook/API endpoint."""
        source = self.service_sources.get("LABEL_STUDIO_SOURCE", "disabled")
        if source == "disabled":
            return {
                "LABEL_STUDIO_INIT_SCALE": "0",
                "LABEL_STUDIO_SCALE": "0",
                "LABEL_STUDIO_ENDPOINT": "",
                "LABEL_STUDIO_API_URL": "",
            }

        minio_source = self.service_sources.get("MINIO_SOURCE", "disabled")
        if minio_source == "disabled":
            raise ValueError(
                "Label Studio requires MinIO to be enabled for S3-compatible "
                "media and export storage. Either pass --minio-source container "
                "alongside --label-studio-source container, or set "
                "--label-studio-source disabled."
            )

        return {
            "LABEL_STUDIO_INIT_SCALE": "1",
            "LABEL_STUDIO_SCALE": "1",
            "LABEL_STUDIO_ENDPOINT": "http://label-studio:8080",
            "LABEL_STUDIO_API_URL": "http://label-studio:8080",
        }

    def _generate_verba_config(self) -> dict:
        """Generate Verba scale, endpoint, and Weaviate target."""
        source = self.service_sources.get("VERBA_SOURCE", "disabled")
        if source == "disabled":
            return {
                "VERBA_SCALE": "0",
                "VERBA_ENDPOINT": "",
                "VERBA_WEAVIATE_URL": "",
            }

        weaviate_source = self.service_sources.get("WEAVIATE_SOURCE", "container")
        if weaviate_source == "disabled":
            raise ValueError(
                "Verba requires Weaviate. Either pass --weaviate-source "
                "container or --weaviate-source localhost alongside "
                "--verba-source container, or set --verba-source disabled."
            )

        if weaviate_source == "localhost":
            current_env = self.config_parser.parse_env_file()
            port = current_env.get("WEAVIATE_LOCALHOST_PORT", "8080")
            weaviate_url = f"http://{self.localhost_host}:{port}"
        else:
            weaviate_url = "http://weaviate:8080"

        return {
            "VERBA_SCALE": "1",
            "VERBA_ENDPOINT": "http://verba:8000",
            "VERBA_WEAVIATE_URL": weaviate_url,
        }

    def _generate_mcp_servers_config(self) -> dict:
        """Generate MCP_SERVERS_SCALE.

        The curated MCP package is only useful when its initial target set is
        available. Supabase is locked always-on, but Neo4j and SearXNG are
        source-configurable, so fail before compose if the operator enables
        MCP without the graph or search backends it advertises.
        """
        source = self.service_sources.get("MCP_SERVERS_SOURCE", "disabled")
        if source == "disabled":
            return {"MCP_SERVERS_SCALE": "0"}

        neo4j_source = self.service_sources.get("NEO4J_GRAPH_DB_SOURCE", "container")
        if neo4j_source == "disabled":
            raise ValueError(
                "MCP Servers require Neo4j to be enabled for graph tools. "
                "Either pass --neo4j-graph-db-source container alongside "
                "--mcp-servers-source container, or set "
                "--mcp-servers-source disabled."
            )
        if neo4j_source != "container":
            raise ValueError(
                "MCP Servers require in-stack Neo4j for v1 because the MCP "
                "compose fragment depends on the Neo4j container lifecycle. "
                "Use --neo4j-graph-db-source container or keep "
                "--mcp-servers-source disabled."
            )

        searxng_source = self.service_sources.get("SEARXNG_SOURCE", "container")
        if searxng_source == "disabled":
            raise ValueError(
                "MCP Servers require SearXNG to be enabled for search tools. "
                "Either pass --searxng-source container alongside "
                "--mcp-servers-source container, or set "
                "--mcp-servers-source disabled."
            )

        return {"MCP_SERVERS_SCALE": "1"}

    def _generate_langfuse_config(self) -> dict:
        """Generate Langfuse family scales and endpoint.

        Langfuse v3 requires S3/blob storage for ingestion payloads. Atlas
        reuses MinIO for that layer, so enabling Langfuse while MinIO is
        disabled would boot a UI that cannot ingest traces. Fail before
        compose starts and before LiteLLM renders a stale callback.
        """
        source = self.service_sources.get("LANGFUSE_SOURCE", "disabled")
        if source == "disabled":
            return {
                "LANGFUSE_INIT_SCALE": "0",
                "LANGFUSE_WEB_SCALE": "0",
                "LANGFUSE_WORKER_SCALE": "0",
                "LANGFUSE_CLICKHOUSE_SCALE": "0",
                "LANGFUSE_ENDPOINT": "",
            }

        minio_source = self.service_sources.get("MINIO_SOURCE", "container")
        if minio_source == "disabled":
            raise ValueError(
                "Langfuse requires MinIO to be enabled for S3-backed trace "
                "ingestion. Either pass --minio-source container alongside "
                "--langfuse-source container, or set --langfuse-source disabled."
            )

        return {
            "LANGFUSE_INIT_SCALE": "1",
            "LANGFUSE_WEB_SCALE": "1",
            "LANGFUSE_WORKER_SCALE": "1",
            "LANGFUSE_CLICKHOUSE_SCALE": "1",
            "LANGFUSE_ENDPOINT": "http://langfuse-web:3000",
        }

    def _generate_otel_tempo_loki_config(self) -> dict:
        """Generate disabled-by-default tracing/log-store scales and endpoints.

        OTel Collector is the ingest point, but the first Atlas slice exports
        only traces to Tempo. Loki is admitted as an internal log store and
        Grafana datasource; log shipping stays a follow-up until traces are
        proven. Enabling the collector without Tempo would silently drop spans,
        so fail before compose starts.
        """
        otel_source = self.service_sources.get("OTEL_COLLECTOR_SOURCE", "disabled")
        tempo_source = self.service_sources.get("TEMPO_SOURCE", "disabled")
        loki_source = self.service_sources.get("LOKI_SOURCE", "disabled")

        if otel_source == "container" and tempo_source != "container":
            raise ValueError(
                "OTel Collector requires Tempo to be enabled for trace export. "
                "Either pass --tempo-source container alongside "
                "--otel-collector-source container, or set "
                "--otel-collector-source disabled."
            )

        otel_on = otel_source == "container"
        tempo_on = tempo_source == "container"
        loki_on = loki_source == "container"

        return {
            "OTEL_COLLECTOR_SCALE": "1" if otel_on else "0",
            "OTEL_COLLECTOR_ENDPOINT": "http://otel-collector:4318" if otel_on else "",
            "OTEL_COLLECTOR_OTLP_HTTP_ENDPOINT": "http://otel-collector:4318" if otel_on else "",
            "OTEL_COLLECTOR_OTLP_GRPC_ENDPOINT": "http://otel-collector:4317" if otel_on else "",
            "TEMPO_SCALE": "1" if tempo_on else "0",
            "TEMPO_ENDPOINT": "http://tempo:3200" if tempo_on else "",
            "LOKI_SCALE": "1" if loki_on else "0",
            "LOKI_ENDPOINT": "http://loki:3100" if loki_on else "",
            "ATLAS_OTEL_ENABLED": "true" if otel_on else "false",
        }

    def _generate_crawl4ai_config(self) -> dict:
        """Generate Crawl4AI scale and in-network endpoint."""
        source = self.service_sources.get("CRAWL4AI_SOURCE", "disabled")
        if source == "disabled":
            return {
                "CRAWL4AI_SCALE": "0",
                "CRAWL4AI_ENDPOINT": "",
            }
        return {
            "CRAWL4AI_SCALE": "1",
            "CRAWL4AI_ENDPOINT": "http://crawl4ai:11235",
        }

    def _generate_tika_config(self) -> dict:
        """Generate Apache Tika scale and in-network/localhost endpoint."""
        source = self.service_sources.get("TIKA_SOURCE", "disabled")
        if source == "disabled":
            return {
                "TIKA_SCALE": "0",
                "TIKA_ENDPOINT": "",
            }
        if source == "tika-localhost":
            current_env = self.config_parser.parse_env_file()
            port = current_env.get("TIKA_LOCALHOST_PORT", "9998")
            return {
                "TIKA_SCALE": "0",
                "TIKA_ENDPOINT": f"http://{self.localhost_host}:{port}",
            }
        return {
            "TIKA_SCALE": "1",
            "TIKA_ENDPOINT": "http://tika:9998",
        }

    def _generate_llm_graph_builder_config(self) -> dict:
        """Generate Neo4j LLM Graph Builder scales/endpoints/model config.

        The first Atlas slice uses the in-stack Neo4j container deliberately:
        the compose fragment has a real lifecycle dependency on
        ``neo4j-graph-db`` and the upstream app is most predictable when the
        advertised Bolt URI is Docker-internal. A later localhost source can
        relax this once it has its own route and lifecycle tests.
        """
        source = self.service_sources.get("LLM_GRAPH_BUILDER_SOURCE", "disabled")
        if source == "disabled":
            return {
                "LLM_GRAPH_BUILDER_BACKEND_SCALE": "0",
                "LLM_GRAPH_BUILDER_FRONTEND_SCALE": "0",
                "LLM_GRAPH_BUILDER_ENDPOINT": "",
                "LLM_GRAPH_BUILDER_BACKEND_ENDPOINT": "",
                "LLM_GRAPH_BUILDER_LITELLM_MODEL_CONFIG": "",
            }

        neo4j_source = self.service_sources.get("NEO4J_GRAPH_DB_SOURCE", "container")
        if neo4j_source == "disabled":
            raise ValueError(
                "Neo4j LLM Graph Builder requires Neo4j. Either pass "
                "--neo4j-graph-db-source container alongside "
                "--llm-graph-builder-source container, or set "
                "--llm-graph-builder-source disabled."
            )
        if neo4j_source != "container":
            raise ValueError(
                "Neo4j LLM Graph Builder requires in-stack Neo4j for v1 "
                "because its compose fragment depends on neo4j-graph-db. "
                "Use --neo4j-graph-db-source container or keep "
                "--llm-graph-builder-source disabled."
            )

        current_env = self.config_parser.parse_env_file()
        model = (
            current_env.get("LLM_GRAPH_BUILDER_LLM_MODEL")
            or current_env.get("LITELLM_DEFAULT_MODEL")
            or "gpt-4o-mini"
        )
        model_config = f"{model},http://litellm:4000/v1,${{LITELLM_MASTER_KEY}}"
        return {
            "LLM_GRAPH_BUILDER_BACKEND_SCALE": "1",
            "LLM_GRAPH_BUILDER_FRONTEND_SCALE": "1",
            "LLM_GRAPH_BUILDER_ENDPOINT": "http://llm-graph-builder-frontend:8080",
            "LLM_GRAPH_BUILDER_BACKEND_ENDPOINT": "http://llm-graph-builder-backend:8000",
            "LLM_GRAPH_BUILDER_LITELLM_MODEL_CONFIG": model_config,
        }

    def _generate_celery_config(self) -> dict:
        """Generate Celery worker/Flower scales and Redis URLs."""
        source = self.service_sources.get("CELERY_SOURCE", "disabled")
        if source == "disabled":
            return {
                "CELERY_WORKER_SCALE": "0",
                "FLOWER_SCALE": "0",
                "CELERY_BROKER_URL": "",
                "CELERY_RESULT_BACKEND": "",
            }
        return {
            "CELERY_WORKER_SCALE": "1",
            "FLOWER_SCALE": "1",
            "CELERY_BROKER_URL": "redis://:${REDIS_PASSWORD}@redis:6379/4",
            "CELERY_RESULT_BACKEND": "redis://:${REDIS_PASSWORD}@redis:6379/4",
        }

    def _generate_supavisor_config(self) -> dict:
        """Generate Supavisor scale plus client DB connection envs.

        Disabled mode intentionally emits direct Postgres values. Compose
        fragments consume these vars with direct fallbacks, so rollback is a
        SOURCE flip rather than a manual compose edit.
        """
        source = self.service_sources.get("SUPAVISOR_SOURCE", "disabled")
        if source == "disabled":
            return {
                "SUPAVISOR_SCALE": "0",
                "SUPAVISOR_DB_HOST": "supabase-db",
                "SUPAVISOR_DB_PORT_VALUE": "5432",
                "SUPAVISOR_DB_USER": "${SUPABASE_DB_USER}",
                "SUPAVISOR_DATABASE_URL": (
                    "postgresql://${SUPABASE_DB_USER}:${SUPABASE_DB_PASSWORD}"
                    "@supabase-db:5432/${SUPABASE_DB_NAME}"
                ),
            }

        return {
            "SUPAVISOR_SCALE": "1",
            "SUPAVISOR_DB_HOST": "supavisor",
            "SUPAVISOR_DB_PORT_VALUE": "6543",
            "SUPAVISOR_DB_USER": "${SUPABASE_DB_USER}.${SUPAVISOR_TENANT_ID}",
            "SUPAVISOR_DATABASE_URL": (
                "postgresql://${SUPABASE_DB_USER}.${SUPAVISOR_TENANT_ID}:"
                "${SUPABASE_DB_PASSWORD}@supavisor:6543/${SUPABASE_DB_NAME}"
            ),
        }

    def _generate_local_deep_researcher_extraction_config(self) -> dict:
        """Resolve Local Deep Researcher's full-page extraction mode.

        This is intentionally a MODE value, not a *_SOURCE selector, because
        Atlas validates every *_SOURCE as a service source. Crawl4AI itself is
        the only source-configurable service in this integration.
        """
        env = self.config_parser.parse_env_file()
        mode = (
            env.get("LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE", "disabled")
            or "disabled"
        ).strip().lower()
        valid_modes = {"disabled", "builtin", "crawl4ai"}
        if mode not in valid_modes:
            raise ValueError(
                "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE must be one of: "
                + ", ".join(sorted(valid_modes))
            )

        if mode == "disabled":
            return {
                "FETCH_FULL_PAGE": "false",
                "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "disabled",
                "CRAWL4AI_ENDPOINT": "",
            }

        if mode == "builtin":
            return {
                "FETCH_FULL_PAGE": "true",
                "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "builtin",
                "CRAWL4AI_ENDPOINT": "",
            }

        if self.service_sources.get("CRAWL4AI_SOURCE", "disabled") != "container":
            raise ValueError(
                "Local Deep Researcher Crawl4AI full-page extraction requires "
                "Crawl4AI to be enabled. Set CRAWL4AI_SOURCE=container or "
                "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE=disabled."
            )
        return {
            "FETCH_FULL_PAGE": "true",
            "LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE": "crawl4ai",
            "CRAWL4AI_ENDPOINT": "http://crawl4ai:11235",
        }

    def _generate_airflow_config(self) -> dict:
        """Generate AIRFLOW_*_SCALE based on AIRFLOW_SOURCE.

        Airflow is a 4-container family (webserver + scheduler +
        dag-processor + init). The dag-processor is REQUIRED in Airflow
        3.x — the scheduler no longer parses DAG files in-process, so
        without dag-processor no DAGs are ever loaded into the metadata
        DB. When source=container all four scale to 1 (init is one-shot
        but still scale=1 so it runs once on stack-up). When disabled,
        all 0.
        """
        source_value = self.service_sources.get("AIRFLOW_SOURCE", "disabled")
        if source_value == "disabled":
            return {
                "AIRFLOW_WEBSERVER_SCALE": "0",
                "AIRFLOW_SCHEDULER_SCALE": "0",
                "AIRFLOW_DAG_PROCESSOR_SCALE": "0",
                "AIRFLOW_INIT_SCALE": "0",
            }
        return {
            "AIRFLOW_WEBSERVER_SCALE": "1",
            "AIRFLOW_SCHEDULER_SCALE": "1",
            "AIRFLOW_DAG_PROCESSOR_SCALE": "1",
            "AIRFLOW_INIT_SCALE": "1",
        }

    def _generate_iceberg_rest_config(self) -> dict:
        """Generate ICEBERG_REST_*_SCALE based on ICEBERG_REST_SOURCE.

        The REST service needs MinIO buckets/service-account provisioning and
        Supabase Postgres. Supabase is locked always-on; MinIO is configurable,
        so fail early when the catalog is enabled without object storage.
        """
        source_value = self.service_sources.get("ICEBERG_REST_SOURCE", "disabled")
        if source_value == "disabled":
            return {
                "ICEBERG_REST_SCALE": "0",
                "ICEBERG_REST_INIT_SCALE": "0",
            }

        minio_source = self.service_sources.get("MINIO_SOURCE", "disabled")
        if minio_source == "disabled":
            raise ValueError(
                "Iceberg REST Catalog requires MinIO to be enabled. "
                "Either pass --minio-source container alongside "
                "--iceberg-rest-source container, or set "
                "--iceberg-rest-source disabled."
            )

        return {
            "ICEBERG_REST_SCALE": "1",
            "ICEBERG_REST_INIT_SCALE": "1",
        }

    def _generate_trino_config(self) -> dict:
        """Generate TRINO_SCALE based on TRINO_SOURCE.

        Trino's first Atlas integration is the Iceberg lakehouse query path.
        It needs both MinIO object storage and the Iceberg REST catalog to be
        running; otherwise the coordinator would boot without the advertised
        `lakehouse` catalog. Fail during source resolution instead of letting
        users discover the broken catalog from the UI.
        """
        source_value = self.service_sources.get("TRINO_SOURCE", "disabled")
        if source_value == "disabled":
            return {"TRINO_SCALE": "0"}

        minio_source = self.service_sources.get("MINIO_SOURCE", "disabled")
        if minio_source == "disabled":
            raise ValueError(
                "Trino requires MinIO to be enabled for the Iceberg warehouse. "
                "Either pass --minio-source container alongside "
                "--trino-source container, or set --trino-source disabled."
            )

        iceberg_source = self.service_sources.get("ICEBERG_REST_SOURCE", "disabled")
        if iceberg_source == "disabled":
            raise ValueError(
                "Trino requires Iceberg REST Catalog to be enabled. "
                "Either pass --iceberg-rest-source container alongside "
                "--trino-source container, or set --trino-source disabled."
            )

        return {"TRINO_SCALE": "1"}

    def _generate_redpanda_config(self) -> dict:
        """Generate Redpanda scales and in-network Kafka endpoints."""
        source_value = self.service_sources.get("REDPANDA_SOURCE", "disabled")
        if source_value == "disabled":
            return {
                "REDPANDA_SCALE": "0",
                "REDPANDA_INIT_SCALE": "0",
                "REDPANDA_CONSOLE_SCALE": "0",
                "REDPANDA_BROKERS": "",
                "SPARK_KAFKA_BOOTSTRAP_SERVERS": "",
            }

        return {
            "REDPANDA_SCALE": "1",
            "REDPANDA_INIT_SCALE": "1",
            "REDPANDA_CONSOLE_SCALE": "1",
            "REDPANDA_BROKERS": "redpanda:9092",
            "SPARK_KAFKA_BOOTSTRAP_SERVERS": "redpanda:9092",
        }

    def _generate_prometheus_config(self, source_value: str) -> dict:
        """Resolve scales for the prometheus family + cross-manifest exporter sidecars.

        PROMETHEUS_SCALE / NODE_EXPORTER_SCALE / CADVISOR_SCALE follow PROMETHEUS_SOURCE
        directly. POSTGRES_EXPORTER_SCALE and REDIS_EXPORTER_SCALE are written here too
        because the sidecars live in other manifests (supabase, redis) but are useless
        when nothing scrapes them. This is the canonical cross-manifest scale
        arithmetic pattern — see _generate_stt_provider_config.

        Args:
            source_value: Current PROMETHEUS_SOURCE (`container` | `disabled`).

        Returns:
            Dict of resolved env-var assignments.
        """
        on = "1" if source_value == "container" else "0"
        endpoint = "http://prometheus:9090" if source_value == "container" else ""
        return {
            "PROMETHEUS_SCALE": on,
            "NODE_EXPORTER_SCALE": on,
            "CADVISOR_SCALE": on,
            "POSTGRES_EXPORTER_SCALE": on,
            "REDIS_EXPORTER_SCALE": on,
            "PROMETHEUS_ENDPOINT": endpoint,
        }

    def _generate_grafana_config(self, source_value: str) -> dict:
        """Resolve Grafana's auto-managed scale + endpoint from GRAFANA_SOURCE.

        Args:
            source_value: Current GRAFANA_SOURCE (`container` | `disabled`).

        Returns:
            Dict of resolved env-var assignments.
        """
        if source_value == "container":
            return {
                "GRAFANA_SCALE": "1",
                "GRAFANA_ENDPOINT": "http://grafana:3000",
            }
        return {
            "GRAFANA_SCALE": "0",
            "GRAFANA_ENDPOINT": "",
        }

    def _generate_openclaw_config(self) -> Dict[str, str]:
        """Generate OpenClaw AI Agent configuration."""
        source_value = self.service_sources.get('OPENCLAW_SOURCE', 'disabled')
        config = self.get_service_config('openclaw', source_value)

        env_vars = {}

        # Set OPENCLAW_ENDPOINT with localhost replacement
        if source_value == 'disabled':
            env_vars['OPENCLAW_ENDPOINT'] = ''
            env_vars['OPENCLAW_SCALE'] = '0'
        elif source_value == 'localhost':
            # OPENCLAW_LOCALHOST_PORT is the user-overridable var the wizard
            # writes for host-side OpenClaw. OPENCLAW_GATEWAY_PORT is the
            # container's host-bound port; reading it here would
            # ignore the wizard's port override — same asymmetric-override
            # class as docling / hermes above.
            current_env = self.config_parser.parse_env_file()
            openclaw_port = current_env.get('OPENCLAW_LOCALHOST_PORT', '63065')
            endpoint = f'http://{self.localhost_host}:{openclaw_port}'
            env_vars['OPENCLAW_ENDPOINT'] = endpoint
            env_vars['OPENCLAW_SCALE'] = '0'
        else:  # container
            endpoint = config.get('environment', {}).get(
                'OPENCLAW_ENDPOINT', 'http://openclaw-gateway:18789')
            endpoint = endpoint.replace('host.docker.internal', self.localhost_host)
            env_vars['OPENCLAW_ENDPOINT'] = endpoint
            env_vars['OPENCLAW_SCALE'] = '1'

        return env_vars

    def _generate_other_services_config(self) -> Dict[str, str]:
        """Generate configuration for other services."""
        env_vars = {}
        
        # N8N configuration — scale derives from N8N_SOURCE via the manifest
        # (container → 1, disabled → 0). Reading a pre-existing N8N_SCALE
        # from .env here made `N8N_SOURCE=disabled` a silent no-op (the key
        # always exists in .env, so the manifest value was never consulted)
        # and made the dependency manager's auto-disable sticky forever.
        # The dependency manager runs AFTER this generator (start.py step
        # 4.1 vs step 4), so its violation-driven zeroing still wins for
        # the current run and gets re-evaluated fresh on every later run.
        n8n_source = self.service_sources.get('N8N_SOURCE', 'container')
        n8n_config = self.get_service_config('n8n', n8n_source)
        n8n_scale = str(n8n_config.get('scale', 1))

        env_vars['N8N_SCALE'] = n8n_scale
        env_vars['N8N_WORKER_SCALE'] = n8n_scale  # Worker follows main N8N scale
        env_vars['N8N_INIT_SCALE'] = n8n_scale    # Init follows main N8N scale
        
        # SearxNG configuration  
        searxng_source = self.service_sources.get('SEARXNG_SOURCE', 'container')
        searxng_config = self.get_service_config('searxng', searxng_source)
        env_vars['SEARXNG_SCALE'] = str(searxng_config.get('scale', 1))

        # Asset Worker configuration
        asset_worker_source = self.service_sources.get('ASSET_WORKER_SOURCE', 'disabled')
        asset_worker_config = self.get_service_config('asset-worker', asset_worker_source)
        env_vars['ASSET_WORKER_SCALE'] = str(asset_worker_config.get('scale', 0))

        # Asset Baker configuration (Blender HP→LP bake worker)
        asset_baker_source = self.service_sources.get('ASSET_BAKER_SOURCE', 'disabled')
        asset_baker_config = self.get_service_config('asset-baker', asset_baker_source)
        env_vars['ASSET_BAKER_SCALE'] = str(asset_baker_config.get('scale', 0))

        # Neo4j configuration
        neo4j_source = self.service_sources.get('NEO4J_GRAPH_DB_SOURCE', 'container')
        neo4j_config = self.get_service_config('neo4j-graph-db', neo4j_source)
        env_vars['NEO4J_SCALE'] = str(neo4j_config.get('scale', 1))
        
        # Set Neo4j URI
        neo4j_uri = neo4j_config.get('environment', {}).get('NEO4J_URI', 'bolt://neo4j-graph-db:7687')
        neo4j_uri = neo4j_uri.replace('host.docker.internal', self.localhost_host)
        env_vars['NEO4J_URI'] = neo4j_uri
        
        # Initialization service scales - conditional based on parent service sources
        
        # WEAVIATE_INIT_SCALE follows WEAVIATE_SCALE 
        weaviate_source = self.service_sources.get('WEAVIATE_SOURCE', 'container')
        if weaviate_source == 'disabled':
            env_vars['WEAVIATE_INIT_SCALE'] = '0'
        else:
            weaviate_config = self.get_service_config('weaviate', weaviate_source)
            env_vars['WEAVIATE_INIT_SCALE'] = str(weaviate_config.get('scale', 1))
        
        # OLLAMA_PULL_SCALE: 1 only for in-stack ollama-container-* sources.
        # Host-side Ollama (ollama-localhost) is not pull-controllable
        # from the stack — sending /api/pull at the user's host Ollama
        # would surprise them (for localhost, the operator must `ollama pull`
        # manually).
        # Source=none has no upstream at all.
        llm_source = self.service_sources.get('LLM_PROVIDER_SOURCE', 'ollama-container-cpu')
        if llm_source.startswith('ollama-container-'):
            env_vars['OLLAMA_PULL_SCALE'] = '1'
        else:
            env_vars['OLLAMA_PULL_SCALE'] = '0'
            
        # COMFYUI_INIT_SCALE: 1 only for container sources. For localhost
        # the named-volume `comfyui-models` isn't mounted into the user's
        # host ComfyUI install, so running the wget-based init would write
        # into a volume nothing reads. The user pulls models into their
        # host install themselves — exact mirror of how OLLAMA_PULL_SCALE
        # behaves for ollama-localhost.
        comfyui_source = self.service_sources.get('COMFYUI_SOURCE', 'container-cpu')
        if comfyui_source == 'disabled':
            env_vars['COMFYUI_INIT_SCALE'] = '0'
        elif comfyui_source.startswith('container-'):
            env_vars['COMFYUI_INIT_SCALE'] = '1'
        else:
            # localhost — no wget-into-volume.
            env_vars['COMFYUI_INIT_SCALE'] = '0'

        # OPENCLAW_INIT_SCALE: follows OPENCLAW_SCALE (1 when container, 0 otherwise)
        openclaw_source = self.service_sources.get('OPENCLAW_SOURCE', 'disabled')
        if openclaw_source == 'container':
            env_vars['OPENCLAW_INIT_SCALE'] = '1'
        else:
            env_vars['OPENCLAW_INIT_SCALE'] = '0'

        # HERMES_INIT_SCALE: follows HERMES_SCALE (1 when container, 0 otherwise).
        # Localhost and disabled both skip the init container — for localhost,
        # the operator owns the on-host config file; for disabled, there's
        # nothing to initialize.
        hermes_source = self.service_sources.get('HERMES_SOURCE', 'container')
        if hermes_source == 'container':
            env_vars['HERMES_INIT_SCALE'] = '1'
        else:
            env_vars['HERMES_INIT_SCALE'] = '0'

        # MINIO_INIT_SCALE: follows MINIO_SOURCE (1 when container, 0 when disabled)
        # Critical: without this, minio-init blocks on a never-healthy minio when
        # MinIO is disabled, hanging compose-up indefinitely.
        minio_source = self.service_sources.get('MINIO_SOURCE', 'container')
        if minio_source == 'container':
            env_vars['MINIO_INIT_SCALE'] = '1'
        else:
            env_vars['MINIO_INIT_SCALE'] = '0'

        # Cloudflared tunnel — scale derives from CLOUDFLARED_SOURCE via the
        # manifest (container → 1, disabled → 0). The compose fragment reads
        # replicas: ${CLOUDFLARED_SCALE:-0}, so without this write the tunnel
        # never starts even when the user sets CLOUDFLARED_SOURCE=container.
        cloudflared_source = self.service_sources.get('CLOUDFLARED_SOURCE', 'disabled')
        cloudflared_config = self.get_service_config('cloudflared', cloudflared_source)
        env_vars['CLOUDFLARED_SCALE'] = str(cloudflared_config.get('scale', 0))

        # Backup runner — on-demand only; the manifest pins scale 0 for every
        # source variant (the user invokes it with `docker compose run --rm
        # backup`). Written from the manifest so the auto-managed var is never
        # left blank.
        backup_source = self.service_sources.get('BACKUP_SOURCE', 'disabled')
        backup_config = self.get_service_config('backup', backup_source)
        env_vars['BACKUP_SCALE'] = str(backup_config.get('scale', 0))

        return env_vars
    
    def _generate_adaptive_services_config(self, all_env_vars: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Generate configuration for adaptive services."""
        env_vars = {}
        sources = self.config_parser.parse_service_sources()

        # Backend always enabled (no SOURCE check - always runs)
        env_vars['BACKEND_SCALE'] = '1'

        # Open WebUI - check SOURCE variable
        webui_source = sources.get('OPEN_WEB_UI_SOURCE', 'container')
        env_vars['OPEN_WEB_UI_SCALE'] = '0' if webui_source == 'disabled' else '1'
        env_vars['OPEN_WEB_UI_INIT_SCALE'] = '0' if webui_source == 'disabled' else '1'

        # Open WebUI adaptive TTS/STT (set engine and API base URL when provider is enabled)
        # Read endpoints from already-generated env vars (STT/TTS configs run before adaptive).
        # All current TTS/STT engines (Speaches, Chatterbox, Parakeet, whisper.cpp) expose
        # an OpenAI-compatible /v1/audio/{speech,transcriptions} surface, so the engine name
        # is uniformly 'openai' — only the API base URL differs.
        parent_vars = all_env_vars or {}
        tts_source = sources.get('TTS_PROVIDER_SOURCE', 'disabled')
        env_vars['OPEN_WEB_UI_TTS_ENGINE'] = 'openai' if tts_source != 'disabled' else ''
        tts_endpoint = parent_vars.get('TTS_ENDPOINT', '')
        env_vars['OPEN_WEB_UI_TTS_API_URL'] = f'{tts_endpoint}/v1' if tts_endpoint else ''
        stt_source = sources.get('STT_PROVIDER_SOURCE', 'disabled')
        env_vars['OPEN_WEB_UI_STT_ENGINE'] = 'openai' if stt_source != 'disabled' else ''
        stt_endpoint = parent_vars.get('STT_ENDPOINT', '')
        env_vars['OPEN_WEB_UI_STT_API_URL'] = f'{stt_endpoint}/v1' if stt_endpoint else ''
        # Open WebUI's default TTS model — depends on which engine is active.
        # service_sources only carries ``*_SOURCE`` vars (see parse_service_sources),
        # so we read the model knob directly from .env with a hard-coded fallback.
        if tts_source.startswith('speaches-container'):
            speaches_env = self.config_parser.parse_env_file()
            env_vars['OPEN_WEB_UI_TTS_MODEL'] = speaches_env.get(
                'SPEACHES_TTS_MODEL', 'hexgrad/Kokoro-82M'
            )
            env_vars['OPEN_WEB_UI_TTS_VOICE'] = 'af_heart'
        elif tts_source.startswith('chatterbox'):
            # Chatterbox's /v1/audio/speech accepts any model string; the
            # server uses the loaded checkpoint regardless. "chatterbox-tts-1"
            # is what its /v1/models endpoint advertises.
            env_vars['OPEN_WEB_UI_TTS_MODEL'] = 'chatterbox-tts-1'
            env_vars['OPEN_WEB_UI_TTS_VOICE'] = 'alloy'
        else:
            env_vars['OPEN_WEB_UI_TTS_MODEL'] = ''
            env_vars['OPEN_WEB_UI_TTS_VOICE'] = ''

        # LightRAG adaptive substitutions — all declared in service.yml
        # runtime_adaptive.lightrag.environment_adaptation.
        # Gated on LIGHTRAG_SOURCE so that disabled LightRAG leaves all
        # storage URIs blank (no spurious credentials in the env).
        lightrag_source = sources.get('LIGHTRAG_SOURCE', 'disabled')
        if lightrag_source != 'disabled':
            lightrag_raw_env = self.config_parser.parse_env_file()

            # Reranker (#415). LightRAG's jina/cohere clients POST
            # `{query, documents}`, while Atlas's TEI /rerank endpoint expects
            # `{query, texts}` — wire-incompatible. When the operator opts the
            # backend adapter in (LIGHTRAG_RERANK_ADAPTER_ENABLED=true) AND TEI
            # is enabled, route LightRAG rerank through the backend adapter
            # route, which translates the two shapes. Atlas never wires LightRAG
            # directly at TEI. Otherwise emit the literal `null` rather than a
            # blank binding because LightRAG hard-crashes on an empty value.
            tei_source = sources.get('TEI_RERANKER_SOURCE', 'disabled')
            adapter_enabled = (
                (lightrag_raw_env.get('LIGHTRAG_RERANK_ADAPTER_ENABLED', 'false') or 'false')
                .strip()
                .lower()
                == 'true'
            )
            if adapter_enabled and tei_source != 'disabled':
                # backend listens on :8000 inside backend-network (shared with
                # lightrag). The route is auth-gated by LIGHTRAG_RERANK_ADAPTER_TOKEN,
                # handed to LightRAG below as RERANK_BINDING_API_KEY.
                env_vars['LIGHTRAG_RERANK_BINDING_HOST'] = 'http://backend:8000/lightrag/rerank'
                env_vars['LIGHTRAG_RERANK_BINDING'] = 'jina'
            else:
                env_vars['LIGHTRAG_RERANK_BINDING_HOST'] = ''
                env_vars['LIGHTRAG_RERANK_BINDING'] = 'null'

            # Docling — mirror DOCLING_ENDPOINT.
            env_vars['LIGHTRAG_DOCLING_ENDPOINT'] = parent_vars.get('DOCLING_ENDPOINT', '')

            # Supabase pgvector URI.
            supabase_source = sources.get('SUPABASE_SOURCE', 'container')
            if supabase_source != 'disabled':
                pg_user = lightrag_raw_env.get('SUPABASE_DB_USER', 'supabase_admin')
                pg_password = lightrag_raw_env.get('SUPABASE_DB_PASSWORD', '')
                pg_db = lightrag_raw_env.get('SUPABASE_DB_NAME', 'postgres')
                env_vars['LIGHTRAG_PG_URI'] = (
                    f'postgresql://{pg_user}:{pg_password}@supabase-db:5432/{pg_db}'
                )
            else:
                env_vars['LIGHTRAG_PG_URI'] = ''

            # Neo4j graph URI + credentials.
            neo4j_source = sources.get('NEO4J_GRAPH_DB_SOURCE', 'container')
            if neo4j_source != 'disabled':
                # Neo4j compose service id is `neo4j-graph-db` (NOT `neo4j`).
                # MUST match services/lightrag/service.yml::runtime_adaptive.
                env_vars['LIGHTRAG_NEO4J_URI'] = 'bolt://neo4j-graph-db:7687'
                env_vars['LIGHTRAG_NEO4J_USERNAME'] = 'neo4j'
                env_vars['LIGHTRAG_NEO4J_PASSWORD'] = lightrag_raw_env.get('GRAPH_DB_PASSWORD', '')
            else:
                env_vars['LIGHTRAG_NEO4J_URI'] = ''
                env_vars['LIGHTRAG_NEO4J_USERNAME'] = ''
                env_vars['LIGHTRAG_NEO4J_PASSWORD'] = ''

            # Redis KV / doc-status URI.
            redis_source = sources.get('REDIS_SOURCE', 'container')
            if redis_source != 'disabled':
                redis_password = lightrag_raw_env.get('REDIS_PASSWORD', '')
                env_vars['LIGHTRAG_REDIS_URI'] = (
                    f'redis://:{redis_password}@redis:6379/2'
                )
            else:
                env_vars['LIGHTRAG_REDIS_URI'] = ''

        else:
            # LightRAG disabled — emit blanks so any stale .env values are
            # cleared on next run. RERANK_BINDING still gets `null` (never
            # blank) so a re-enable that races the rewrite can't crash-loop.
            env_vars['LIGHTRAG_RERANK_BINDING_HOST'] = ''
            env_vars['LIGHTRAG_RERANK_BINDING'] = 'null'
            env_vars['LIGHTRAG_DOCLING_ENDPOINT'] = ''
            env_vars['LIGHTRAG_PG_URI'] = ''
            env_vars['LIGHTRAG_NEO4J_URI'] = ''
            env_vars['LIGHTRAG_NEO4J_USERNAME'] = ''
            env_vars['LIGHTRAG_NEO4J_PASSWORD'] = ''
            env_vars['LIGHTRAG_REDIS_URI'] = ''

        # Hermes-init capability wiring. Each *_INTERNAL_URL is read by
        # init-hermes.sh and rendered into config.yaml as a tool/skill block
        # — empty values cause init-hermes.sh's strip_block to omit the
        # capability (per services/hermes/service.yml::runtime_adaptive
        # .hermes-init.failure_mode). The contract here mirrors the env
        # declarations at services/hermes/service.yml lines 169-173; missing
        # an emission silently disables the capability with no warning.
        hermes_source = sources.get('HERMES_SOURCE', 'container')
        hermes_container_up = (hermes_source == 'container')

        lightrag_endpoint = parent_vars.get('LIGHTRAG_ENDPOINT', '')
        if hermes_container_up and lightrag_endpoint:
            env_vars['LIGHTRAG_INTERNAL_URL'] = lightrag_endpoint
        else:
            env_vars.setdefault('LIGHTRAG_INTERNAL_URL', '')

        tts_endpoint_for_hermes = parent_vars.get('TTS_ENDPOINT', '')
        if hermes_container_up and tts_endpoint_for_hermes:
            env_vars['TTS_INTERNAL_URL'] = tts_endpoint_for_hermes
        else:
            env_vars.setdefault('TTS_INTERNAL_URL', '')

        stt_endpoint_for_hermes = parent_vars.get('STT_ENDPOINT', '')
        if hermes_container_up and stt_endpoint_for_hermes:
            env_vars['STT_INTERNAL_URL'] = stt_endpoint_for_hermes
        else:
            env_vars.setdefault('STT_INTERNAL_URL', '')

        comfyui_endpoint_for_hermes = parent_vars.get('COMFYUI_ENDPOINT', '')
        comfyui_source = sources.get('COMFYUI_SOURCE', 'disabled')
        if hermes_container_up and comfyui_source != 'disabled' and comfyui_endpoint_for_hermes:
            env_vars['COMFYUI_INTERNAL_URL'] = comfyui_endpoint_for_hermes
        else:
            env_vars.setdefault('COMFYUI_INTERNAL_URL', '')

        searxng_source = sources.get('SEARXNG_SOURCE', 'container')
        if hermes_container_up and searxng_source != 'disabled':
            # SEARXNG_INTERNAL_URL is a fixed in-network address (no
            # SEARXNG_ENDPOINT exists) — the literal mirrors what the
            # manifest's environment_adaptation declares.
            env_vars['SEARXNG_INTERNAL_URL'] = 'http://searxng:8080'
        else:
            env_vars.setdefault('SEARXNG_INTERNAL_URL', '')

        # Local Deep Researcher - check SOURCE variable
        researcher_source = sources.get('LOCAL_DEEP_RESEARCHER_SOURCE', 'container')
        env_vars['LOCAL_DEEP_RESEARCHER_SCALE'] = '0' if researcher_source == 'disabled' else '1'

        # JupyterHub - check SOURCE variable
        jupyterhub_source = sources.get('JUPYTERHUB_SOURCE', 'container')
        env_vars['JUPYTERHUB_SCALE'] = '0' if jupyterhub_source == 'disabled' else '1'

        return env_vars
    
    def _refresh_image_pins_from_manifests(self) -> Dict[str, str]:
        """Force-refresh `*_IMAGE` env vars from manifest `images[].default`.

        Image pins are deterministic — the manifest is the source of truth.
        Without this refresh, a user who pulled a tag-bump keeps running the
        old image because the bootstrapper preserves their existing .env
        value across launches. Specifically caught:
          - PR #62 bumped postgres-exporter v0.16.0 → v0.18.1 (PG18 schema
            support). User's .env kept v0.16.0; live `checkpoints_timed`
            errors on every scrape until manually patched.

        Override path for users who genuinely pin a different image:
        shell-export the var before start.sh (e.g.
        ``LIGHTRAG_IMAGE=ghcr.io/hkuds/lightrag:v1.4.6 ./start.sh``). Compose
        interpolation honors shell env over .env, AND this method skips any
        var that's already set in os.environ.
        """
        import os
        from services.manifests import load_manifests

        try:
            services_root = self.config_parser.root_dir / "services"
            manifests = load_manifests(services_root)
        except Exception:
            # Defensive: if manifests can't load (unusual), do nothing rather
            # than crash the whole env-generation flow.
            return {}

        env_vars: Dict[str, str] = {}
        for m in manifests:
            for img in getattr(m, 'images', None) or []:
                var = getattr(img, 'var', None) or (img.get('var') if isinstance(img, dict) else None)
                default = getattr(img, 'default', None) or (img.get('default') if isinstance(img, dict) else None)
                if not var or not default:
                    continue
                # Respect shell-export override
                if os.environ.get(var):
                    continue
                env_vars[var] = default
            # Image pins declared as plain env vars (e.g. the speaches
            # CUDA build, which is an alternate tag for the same
            # container rather than an images[] entry) need the same
            # staleness refresh — without this, a cuda bump in the
            # manifest left old user pins running forever.
            for e in getattr(m, 'env', None) or []:
                name = getattr(e, 'name', None)
                default = getattr(e, 'default', None)
                if (name and isinstance(default, str) and default
                        and name.endswith('_IMAGE')
                        and not os.environ.get(name)):
                    env_vars.setdefault(name, default)
        return env_vars

    def update_env_file(self, env_vars: Dict[str, str], create_backup: bool = True) -> bool:
        """
        Update .env file with computed environment variables.
        Replicates the update_env_file() function from start.sh.
        
        Args:
            env_vars: Dictionary of environment variables to set
            create_backup: Whether to create backup before updating
            
        Returns:
            bool: True if successful
        """
        env_file_path = self.config_parser.env_file_path
        
        if not env_file_path.exists():
            print(f"❌ .env file not found: {env_file_path}")
            return False
        
        try:
            # Create backup if requested
            if create_backup:
                self.config_parser.create_env_backup()
            
            # Read current .env content
            with open(env_file_path, 'r', encoding="utf-8") as f:
                content = f.read()
            
            updated_content = content
            
            # Update each environment variable
            for var_name, var_value in env_vars.items():
                # Use regex to find and replace the variable assignment
                pattern = rf'^{re.escape(var_name)}=.*$'
                replacement = f'{var_name}={var_value}'

                if re.search(pattern, updated_content, re.MULTILINE):
                    # Variable exists, replace it. Lambda bypasses re.sub's
                    # backslash interpretation in the replacement string
                    # (matches the source_override_manager.py pattern —
                    # env values may contain literal backslashes).
                    updated_content = re.sub(
                        pattern, lambda _m, r=replacement: r, updated_content, flags=re.MULTILINE
                    )
                else:
                    # Variable doesn't exist, append it
                    updated_content += f'\n{replacement}'
            
            # Write atomically (tmp + os.replace): a crash mid-write on an
            # in-place open(..., 'w') truncates the user's secrets-bearing
            # .env. Preserve the original mode (a user-chmod'd 0600 .env must
            # not come back umask-default) and never leave the tmp behind on
            # failure. Mirrors utils/source_override_manager.py.
            tmp_path = f"{env_file_path}.tmp"
            try:
                original_mode = os.stat(env_file_path).st_mode
                with open(tmp_path, 'w', encoding="utf-8") as f:
                    os.chmod(tmp_path, original_mode)
                    f.write(updated_content)
                os.replace(tmp_path, env_file_path)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            return True
            
        except Exception as e:
            print(f"❌ Failed to update .env file: {e}")
            return False
    
    def check_comfyui_local_models(self, on_line=None) -> None:
        """
        Check ComfyUI local models directory.
        Replicates the ComfyUI local models check from start.sh.

        When `on_line` is provided (TUI mode), output routes through it as
        ``on_line(msg, level)`` — matching show_container_status_and_verify_ports
        — so a late check after the log pane detaches can't smear the bare
        terminal. When None (legacy/linear mode), falls back to print().
        """
        def _emit(msg: str, level: str = "ok") -> None:
            if on_line is not None:
                on_line(msg, level)
            else:
                print(msg)

        comfyui_source = self.service_sources.get('COMFYUI_SOURCE', 'container-cpu')
        is_local = comfyui_source == 'localhost'

        if is_local:
            from pathlib import Path

            # Get local models path from env
            env_vars = self.config_parser.parse_env_file()
            models_path = env_vars.get('COMFYUI_LOCAL_MODELS_PATH', '~/Documents/ComfyUI/models')

            # Expand user home directory
            models_path = Path(models_path).expanduser()

            if models_path.exists():
                _emit(f"  • ✅ ComfyUI local models found: {models_path}", "ok")
            else:
                _emit(f"  • ⚠️  ComfyUI local models directory not found: {models_path}", "warn")
                _emit("    Please ensure your local ComfyUI models are in the correct location", "warn")
    
    def generate_and_update_env(self, create_backup: bool = True) -> bool:
        """
        Generate service environment and update .env file.
        
        Args:
            create_backup: Whether to create backup before updating
            
        Returns:
            bool: True if successful
        """
        env_vars = self.generate_service_environment()
        
        if not env_vars:
            print("❌ Failed to generate service environment variables")
            return False
            
        return self.update_env_file(env_vars, create_backup)
