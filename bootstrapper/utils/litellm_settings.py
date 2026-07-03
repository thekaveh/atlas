"""
Shared LiteLLM proxy settings block.

Single source of truth for the static portion of
``volumes/litellm/config.yaml``. Two writers consume it:

  • ``bootstrapper/utils/litellm_config_generator.py`` writes a stub
    on the host (model_list empty) so the bind mount has a file.
  • ``services/litellm/init/scripts/init.py`` writes the real config from
    the YAML model catalogs + env (via ``model_resolver``) inside the init
    container, importing this module via the ``/catalog`` bind mount.

Keeping both writers in lockstep avoids silent drift if one is updated
without the other.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


def base_settings(env: Mapping[str, str] | None = None) -> Dict[str, Any]:
    """Return the LiteLLM ``litellm_settings`` / ``router_settings`` /
    ``general_settings`` dict. Caller adds ``model_list`` separately.

    All values that LiteLLM resolves at proxy-start use the
    ``os.environ/...`` form so they're read from the LiteLLM
    container's environment, not baked into the YAML at render time.
    """
    source = (env or {}).get("LANGFUSE_SOURCE", "").strip().lower()
    litellm_settings: Dict[str, Any] = {
        "cache": True,
        "cache_params": {
            "type": "redis",
            "host": "os.environ/REDIS_HOST",
            "port": "os.environ/REDIS_PORT",
            "password": "os.environ/REDIS_PASSWORD",
        },
        "drop_params": True,
        # Prometheus metrics — emits per-model request/token/cost/latency
        # series on /metrics (same port as the proxy, 4000). Scraped by
        # the observability bundle's Prometheus when PROMETHEUS_SOURCE=container.
        # When disabled, the callback still loads but its emissions just sit
        # idle — no consumer hits /metrics. Harmless overhead.
        "callbacks": ["prometheus"],
    }
    if source == "container":
        litellm_settings["success_callback"] = ["langfuse"]

    return {
        "litellm_settings": litellm_settings,
        "router_settings": {
            "redis_host": "os.environ/REDIS_HOST",
            "redis_port": "os.environ/REDIS_PORT",
            "redis_password": "os.environ/REDIS_PASSWORD",
        },
        "general_settings": {
            "master_key": "os.environ/LITELLM_MASTER_KEY",
            "database_url": "os.environ/DATABASE_URL",
            "store_model_in_db": True,
        },
    }
