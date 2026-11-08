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
# LITELLM_MASTER_KEY in services/weaviate/compose.yml.

# NOTE: no DEFAULT_OPENAI_BASE_URL export here — Weaviate never read
# that env var (the per-collection moduleConfig.baseURL set by the
# backend is the real seam), and the old value carried a wrong /v1
# suffix on top of being dead. Compose never passes LITELLM_BASE_URL to
# this container, so only the default endpoint line was ever printed.
echo "weaviate: default LiteLLM endpoint: http://litellm:4000"

echo "weaviate: init checks done - embedding model: $LITELLM_EMBEDDING_MODEL"

# Go has no memory limit of its own, so a heap that grows to the container
# cap is OOM-killed instead of collected (#1356). Unless WEAVIATE_GOMEMLIMIT
# set GOMEMLIMIT, use 80% of the cgroup v2 (or v1) memory limit.
if [ -z "${GOMEMLIMIT:-}" ]; then
  unset GOMEMLIMIT
  limit=""
  if [ -r /sys/fs/cgroup/memory.max ]; then
    limit=$(cat /sys/fs/cgroup/memory.max)
  elif [ -r /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then
    limit=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)
  fi
  case "$limit" in
    '' | max | *[!0123456789]*) echo "weaviate: no container memory limit; GOMEMLIMIT unset" ;;
    *)
      # Divide first: cgroup v1 reports "no limit" as ~9.2e18, and
      # multiplying that by 4 would overflow 64-bit shell arithmetic.
      # shellcheck disable=SC2017
      GOMEMLIMIT=$((limit / 5 * 4))
      export GOMEMLIMIT
      echo "weaviate: GOMEMLIMIT=${GOMEMLIMIT} (80% of the container memory limit)"
      ;;
  esac
else
  export GOMEMLIMIT
  echo "weaviate: GOMEMLIMIT=${GOMEMLIMIT} (WEAVIATE_GOMEMLIMIT)"
fi

# Execute the original Weaviate command with dynamic configuration
exec "$@"
