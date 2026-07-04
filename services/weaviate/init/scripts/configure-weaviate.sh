#!/bin/sh
set -e

echo "weaviate: Reading dynamic configuration..."

# Check if shared config exists
if [ ! -f "/shared/weaviate-config.env" ]; then
  echo "weaviate: Warning - No dynamic configuration found, using defaults"
  export LITELLM_EMBEDDING_MODEL="ollama/nomic-embed-text"
else
  # Source the dynamic configuration
  echo "weaviate: Loading dynamic configuration from weaviate-init"
  # shellcheck source=/dev/null
  . /shared/weaviate-config.env
  echo "weaviate: Using LiteLLM embedding model: $LITELLM_EMBEDDING_MODEL"
fi

# Weaviate's text2vec-openai module reads its base URL from per-collection
# moduleConfig (set by consumers when creating collections). The API key
# is delivered via Weaviate's OPENAI_APIKEY env, populated from
# LITELLM_MASTER_KEY in docker-compose.yml.

# NOTE: no DEFAULT_OPENAI_BASE_URL export here — Weaviate never read
# that env var (the per-collection moduleConfig.baseURL set by the
# backend is the real seam), and the old value carried a wrong /v1
# suffix on top of being dead.
if [ -n "$LITELLM_BASE_URL" ]; then
  echo "weaviate: LiteLLM endpoint (per-collection baseURL is set by the backend): $LITELLM_BASE_URL"
else
  echo "weaviate: default LiteLLM endpoint: http://litellm:4000"
fi

echo "weaviate: init checks done - embedding model: $LITELLM_EMBEDDING_MODEL"

# Execute the original Weaviate command with dynamic configuration
exec "$@"
