#!/usr/bin/env python3
import os
import json
import sys

# Force line-buffered stdout so init-container progress prints reach
# `docker logs` immediately — mirrors the open-webui/init and
# lightrag/init scripts. Without this, the script's print sites would
# be block-buffered and a crash mid-run would silently drop the
# progress trail.
sys.stdout.reconfigure(line_buffering=True)


def initialize_config():
    """Initialize Local Deep Researcher configuration from env vars.

    Model resolution (in order):
      1. ``LITELLM_DEFAULT_MODEL`` env var (set in .env, injected via compose).
         The value is already correctly formatted (e.g. ``ollama/qwen3.8:latest``
         or a bare cloud model id) — use it as-is without adding a provider prefix.
      2. If the env var is unset or empty: log an error and exit non-zero.
         This surfaces the misconfiguration at compose-up time before the
         first /research request reaches LiteLLM.
    """
    litellm_base_url = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
    litellm_api_key = os.getenv("LITELLM_API_KEY", "")

    # Resolve the content LLM from the LITELLM_DEFAULT_MODEL env var.
    # This var is populated in .env by the bootstrapper (B2) and passed
    # into the container via local-deep-researcher/compose.yml.
    litellm_model = os.getenv("LITELLM_DEFAULT_MODEL", "").strip()
    if not litellm_model:
        print(
            "ERROR: LITELLM_DEFAULT_MODEL is unset or empty. "
            "Set LITELLM_DEFAULT_MODEL to a model id LiteLLM serves "
            "(e.g. ollama/qwen3.8:latest) in .env and restart the stack."
        )
        sys.exit(1)

    print(f"Using LLM from LITELLM_DEFAULT_MODEL: {litellm_model}")

    try:
        # Create configuration for Local Deep Researcher.
        # llm_provider="openai" tells LangGraph/LangChain to use the
        # OpenAI-compatible client, which we point at LiteLLM.
        config = {
            "llm_provider": "openai",
            "local_llm": litellm_model,
            "litellm_base_url": litellm_base_url,
            "search_api": os.getenv("SEARCH_API", "searxng"),
            "max_web_research_loops": int(os.getenv("MAX_WEB_RESEARCH_LOOPS", "3")),
            "fetch_full_page": os.getenv("FETCH_FULL_PAGE", "false").lower()
            == "true",
        }

        os.makedirs("/app/config", exist_ok=True)
        with open("/app/config/runtime_config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        # Create .env file for Local Deep Researcher
        env_content = f"""LLM_PROVIDER=openai
LOCAL_LLM={litellm_model}
OPENAI_API_BASE={litellm_base_url}/v1
OPENAI_API_KEY={litellm_api_key}
LITELLM_BASE_URL={litellm_base_url}
LITELLM_API_KEY={litellm_api_key}
SEARCH_API={config["search_api"]}
MAX_WEB_RESEARCH_LOOPS={config["max_web_research_loops"]}
FETCH_FULL_PAGE={config["fetch_full_page"]}
"""

        with open("/app/.env", "w", encoding="utf-8") as f:
            f.write(env_content)

        print("Configuration initialized successfully")
        print(f"Using LLM: {litellm_model}")
        print(f"LiteLLM URL: {litellm_base_url}")
        print(f"Search API: {config['search_api']}")

    except Exception as e:
        print(f"ERROR initializing config: {e}")
        sys.exit(1)


if __name__ == "__main__":
    initialize_config()
