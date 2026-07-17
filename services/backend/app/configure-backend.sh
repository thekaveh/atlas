#!/bin/sh
set -e

echo "backend: Reading dynamic Weaviate configuration..."

# Check if shared config exists.
# weaviate-init writes the embedding model identifier (LiteLLM-prefixed,
# e.g. "ollama/nomic-embed-text") into /shared/weaviate-config.env on first
# run. Backend reads it as LITELLM_EMBEDDING_MODEL.
if [ -f "/shared/weaviate-config.env" ]; then
  echo "backend: Loading dynamic Weaviate configuration"
  # shellcheck source=/dev/null
  . /shared/weaviate-config.env
  export LITELLM_EMBEDDING_MODEL="${LITELLM_EMBEDDING_MODEL:-ollama/nomic-embed-text}"
  echo "backend: Using LiteLLM embedding model: $LITELLM_EMBEDDING_MODEL"
else
  echo "backend: Warning - No dynamic Weaviate configuration found"
  export LITELLM_EMBEDDING_MODEL="ollama/nomic-embed-text"
  echo "backend: Using default LiteLLM embedding model: $LITELLM_EMBEDDING_MODEL"
fi

# LangMem memory configuration
if [ "${LANGMEM_ENABLED:-true}" = "true" ]; then
  echo "backend: LangMem memory service enabled"
  echo "backend: Memory namespace: ${LANGMEM_NAMESPACE:-default}"
  echo "backend: Max facts per user: ${LANGMEM_MAX_FACTS_PER_USER:-1000}"
  if [ -n "${LANGMEM_EXTRACTION_MODEL}" ]; then
    echo "backend: Extraction model: ${LANGMEM_EXTRACTION_MODEL}"
  else
    echo "backend: Extraction model: (using default content model)"
  fi
else
  echo "backend: LangMem memory service disabled"
fi

echo "backend: Configuration applied - starting backend service..."

# Dev auto-reloader is opt-in (#679). Default OFF: the production/consumer image
# runs plain uvicorn so host-side git churn in a bind-mounted plugin dir can't
# restart or crash-loop the backend. BACKEND_DEV_RELOAD=true restores uvicorn's
# --reload for live plugin editing; the plugin seam installs at boot, so a
# container recreate — not a hot reload — is the correct pickup mechanism.
if [ "${BACKEND_DEV_RELOAD:-false}" = "true" ]; then
  echo "backend: BACKEND_DEV_RELOAD=true - enabling uvicorn --reload (dev hot-reload)"
  exec "$@" --reload
fi

# Execute the original backend command
exec "$@"
