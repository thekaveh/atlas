"""
SOURCE override manager for command-line arguments.
Handles runtime overrides of SERVICE SOURCE configurations.
"""

from typing import Dict
import os
import re
from pathlib import Path

class SourceOverrideManager:
    """Manages command-line SOURCE overrides for services."""
    
    def __init__(self, config_parser):
        """
        Initialize the SOURCE override manager.
        
        Args:
            config_parser: ConfigParser instance for file operations
        """
        self.config_parser = config_parser
        self.applied_overrides = {}
        
        # Map CLI argument names to environment variable names
        self.source_mapping = {
            'llm_provider_source': 'LLM_PROVIDER_SOURCE',
            'cloud_openai_source': 'CLOUD_OPENAI_SOURCE',
            'cloud_anthropic_source': 'CLOUD_ANTHROPIC_SOURCE',
            'cloud_openrouter_source': 'CLOUD_OPENROUTER_SOURCE',
            'comfyui_source': 'COMFYUI_SOURCE',
            'weaviate_source': 'WEAVIATE_SOURCE',
            'minio_source': 'MINIO_SOURCE',
            'n8n_source': 'N8N_SOURCE',
            'searxng_source': 'SEARXNG_SOURCE',
            'mcp_servers_source': 'MCP_SERVERS_SOURCE',
            'langfuse_source': 'LANGFUSE_SOURCE',
            # Langfuse is a multi-container family (init + web + worker +
            # ClickHouse), so runtime_sc is keyed by langfuse-web while the
            # user-facing knob stays family-level LANGFUSE_SOURCE.
            'langfuse_web_source': 'LANGFUSE_SOURCE',
            'jupyterhub_source': 'JUPYTERHUB_SOURCE',
            'mlflow_source': 'MLFLOW_SOURCE',
            'open_web_ui_source': 'OPEN_WEB_UI_SOURCE',
            'local_deep_researcher_source': 'LOCAL_DEEP_RESEARCHER_SOURCE',
            'stt_provider_source': 'STT_PROVIDER_SOURCE',
            'tts_provider_source': 'TTS_PROVIDER_SOURCE',
            'doc_processor_source': 'DOC_PROCESSOR_SOURCE',
            'openclaw_source': 'OPENCLAW_SOURCE',
            'hermes_source': 'HERMES_SOURCE',
            'neo4j_graph_db_source': 'NEO4J_GRAPH_DB_SOURCE',
            'multi2vec_clip_source': 'MULTI2VEC_CLIP_SOURCE',
            'ray_source': 'RAY_SOURCE',
            # Ray's runtime_sc has two containers (ray-head + ray-worker)
            # so source_configurable carries both as separate keys. Mapping
            # `ray_head_source` (the head — main container) here makes
            # ServiceDiscovery treat it as the wizard's discovery anchor;
            # ray-worker has no entry here and gets filtered out, same way
            # init containers like comfyui-init / hermes-init are filtered.
            # Both `ray_source` and `ray_head_source` resolve to the same
            # underlying env var (RAY_SOURCE) — only ray_source is wired to
            # a CLI flag (--ray-source); ray_head_source exists purely as
            # a discovery shim.
            'ray_head_source': 'RAY_SOURCE',
            'prometheus_source': 'PROMETHEUS_SOURCE',
            'grafana_source': 'GRAFANA_SOURCE',
            'spark_source': 'SPARK_SOURCE',
            # Spark's runtime_sc carries four containers (spark-master +
            # spark-worker + spark-history + spark-connect) so
            # source_configurable holds them as separate keys. Mapping
            # `spark_master_source` (the master — main container) here
            # makes ServiceDiscovery treat it as the wizard's discovery
            # anchor; spark-worker / spark-history / spark-connect have no
            # entry here and get filtered out, same way Ray's ray-worker
            # is filtered. spark-init is one-shot and not source-toggleable
            # at all. Both `spark_source` and
            # `spark_master_source` resolve to SPARK_SOURCE — only
            # spark_source is wired to a CLI flag (--spark-source);
            # spark_master_source exists purely as a discovery shim.
            'spark_master_source': 'SPARK_SOURCE',
            'zeppelin_source': 'ZEPPELIN_SOURCE',
            'jenkins_source': 'JENKINS_SOURCE',
            'airflow_source': 'AIRFLOW_SOURCE',
            'iceberg_rest_source': 'ICEBERG_REST_SOURCE',
            # Airflow has a multi-container family (webserver + scheduler +
            # init) so source_configurable carries three keys. Map the
            # webserver as the discovery anchor (mirroring Ray's
            # ray_head_source and Spark's spark_master_source shims).
            'airflow_webserver_source': 'AIRFLOW_SOURCE',
            'tei_reranker_source': 'TEI_RERANKER_SOURCE',
            'lightrag_source': 'LIGHTRAG_SOURCE',
        }
    
    def collect_overrides(self, **kwargs) -> Dict[str, str]:
        """
        Collect non-None SOURCE overrides from CLI arguments.
        
        Args:
            **kwargs: Keyword arguments from CLI (click options)
            
        Returns:
            dict: Mapping of environment variable names to override values
        """
        overrides = {}
        for cli_arg, env_var in self.source_mapping.items():
            value = kwargs.get(cli_arg)
            if value is not None:
                overrides[env_var] = value
        return overrides
    
    def apply_overrides(self, overrides: Dict[str, str]) -> bool:
        """
        Apply SOURCE overrides to .env file.
        
        Args:
            overrides: Dictionary of environment variables to override
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not overrides:
            return True

        # Update .env file with overrides (silent — shown in summary table)
        if self.update_env_file(overrides):
            self.applied_overrides = overrides
            return True
        
        return False
    
    def update_env_file(self, overrides: Dict[str, str]) -> bool:
        """
        Update SOURCE values in .env file using regex replacement.
        
        Args:
            overrides: Dictionary of environment variables to update
            
        Returns:
            bool: True if successful, False otherwise
        """
        env_file_path = self.config_parser.env_file_path
        
        if not env_file_path.exists():
            print(f"❌ .env file not found: {env_file_path}")
            return False
        
        try:
            # Read current .env content
            with open(env_file_path, 'r', encoding="utf-8") as f:
                content = f.read()
            
            updated_content = content
            
            # Update each environment variable using regex
            for var_name, var_value in overrides.items():
                pattern = rf'^{re.escape(var_name)}=.*$'
                replacement = f'{var_name}={var_value}'

                if re.search(pattern, updated_content, re.MULTILINE):
                    # Variable exists, replace it. Use a lambda so re.sub
                    # does NOT interpret backslash sequences in the
                    # replacement (\1, \g<name>) — env values that contain
                    # literal backslashes (rare but legal in JWT secrets,
                    # base64-encoded keys, etc.) would otherwise corrupt
                    # silently.
                    updated_content = re.sub(
                        pattern, lambda _m, r=replacement: r, updated_content, flags=re.MULTILINE
                    )
                else:
                    # Variable doesn't exist, append it (shouldn't happen with SOURCE vars)
                    print(f"⚠️  {var_name} not found in .env, appending...")
                    updated_content += f'\n{replacement}'
            
            # Write atomically (tmp + os.replace): a crash mid-write on
            # an in-place open(..., 'w') truncates the user's .env.
            # Preserve the original mode (a user-chmod'd 0600 .env must
            # not come back umask-default), and never leave the
            # secrets-bearing tmp file behind on failure.
            tmp_path = Path(str(env_file_path) + '.tmp')
            try:
                original_mode = os.stat(env_file_path).st_mode
                with open(tmp_path, 'w', encoding="utf-8") as f:
                    # Clamp the mode BEFORE writing secrets — a
                    # umask-default tmp next to a 0600 .env would
                    # briefly expose the content otherwise.
                    os.chmod(tmp_path, original_mode)
                    f.write(updated_content)
                os.replace(tmp_path, env_file_path)
            finally:
                tmp_path.unlink(missing_ok=True)
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to apply SOURCE overrides: {e}")
            return False
    
    def show_override_summary(self):
        """Display summary of applied SOURCE overrides."""
        if self.applied_overrides:
            print("\n📋 Active SOURCE Overrides:")
            for var, value in self.applied_overrides.items():
                print(f"  • {var}: {value}")
        else:
            print("\n📋 No SOURCE overrides active (using .env defaults)")
    
    def get_applied_overrides(self) -> Dict[str, str]:
        """
        Get dictionary of applied overrides.
        
        Returns:
            dict: Currently applied SOURCE overrides
        """
        return self.applied_overrides.copy()
    
    def has_overrides(self) -> bool:
        """
        Check if any overrides are currently applied.
        
        Returns:
            bool: True if overrides are active, False otherwise
        """
        return bool(self.applied_overrides)
