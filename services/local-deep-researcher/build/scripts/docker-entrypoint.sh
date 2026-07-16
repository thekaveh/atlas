#!/bin/bash
set -e

REPO_URL="https://github.com/langchain-ai/local-deep-researcher.git"
REPO_DIR="/app/repo"
REPO_REF="${LOCAL_DEEP_RESEARCHER_REF:?LOCAL_DEEP_RESEARCHER_REF is required}"
LANGGRAPH_CLI_VERSION="${LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION:?LOCAL_DEEP_RESEARCHER_LANGGRAPH_CLI_VERSION is required}"
UPSTREAM_LOCK_SHA256="${LOCAL_DEEP_RESEARCHER_UPSTREAM_LOCK_SHA256:?LOCAL_DEEP_RESEARCHER_UPSTREAM_LOCK_SHA256 is required}"
RUNTIME_LOCK="/app/config/runtime-requirements.lock"
VENV_DIR="/app/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

# This absolute path exists inside the image.
# shellcheck disable=SC1091
source /app/scripts/runtime-lib.sh

# Guard against unbounded glob expansion of $REPO_DIR (e.g. empty or "/").
# `rm -rf "$REPO_DIR"/.*` can match `..` on some shells and walk into the
# parent directory; use `find -mindepth 1 -delete` instead which skips
# `.` and `..` by design.
if [ -z "$REPO_DIR" ] || [ "$REPO_DIR" = "/" ]; then
    echo "Local Deep Researcher: ERROR - REPO_DIR is unsafe: '$REPO_DIR'"
    exit 1
fi

echo "Local Deep Researcher: Starting initialization..."

# -------------------------------------------------------------------
# Materialize the manifest-pinned upstream commit.
# -------------------------------------------------------------------
if [[ ! "$REPO_REF" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Local Deep Researcher: ERROR - LOCAL_DEEP_RESEARCHER_REF must be a full lowercase commit SHA"
    exit 1
fi

echo "Local Deep Researcher: Materializing pinned upstream commit $REPO_REF..."
ensure_git_repo "$REPO_DIR" "$REPO_URL"

current_ref=$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || true)
if [ "$current_ref" != "$REPO_REF" ]; then
    git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_REF"
    git -C "$REPO_DIR" checkout --detach --force FETCH_HEAD
else
    git -C "$REPO_DIR" checkout --detach --force "$REPO_REF"
fi
git -C "$REPO_DIR" clean -ffd

resolved_ref=$(git -C "$REPO_DIR" rev-parse HEAD)
if [ "$resolved_ref" != "$REPO_REF" ]; then
    echo "Local Deep Researcher: ERROR - resolved $resolved_ref, expected $REPO_REF"
    exit 1
fi
echo "Local Deep Researcher: Pinned source verified"

if [[ ! "$UPSTREAM_LOCK_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Local Deep Researcher: ERROR - LOCAL_DEEP_RESEARCHER_UPSTREAM_LOCK_SHA256 must be a lowercase SHA-256 digest"
    exit 1
fi
printf '%s  %s\n' "$UPSTREAM_LOCK_SHA256" "$REPO_DIR/uv.lock" | sha256sum -c -
echo "Local Deep Researcher: Upstream dependency lock verified"

# Copy upstream source into working directory (preserving our custom scripts/config)
rm -rf -- /app/src
cp -r "$REPO_DIR"/src /app/
cp "$REPO_DIR"/pyproject.toml /app/
cp "$REPO_DIR"/langgraph.json /app/

# Optional Atlas integration: when full-page mode is crawl4ai, replace
# upstream's direct httpx fetch helper with a token-authenticated Crawl4AI
# adapter. Other modes leave upstream source untouched.
python3 /app/scripts/patch-litellm-openai-provider.py
python3 /app/scripts/patch-crawl4ai-fetch.py

echo "Local Deep Researcher: Installing dependencies..."
if ! grep -Fqx "# upstream-ref: $REPO_REF" "$RUNTIME_LOCK"; then
    echo "Local Deep Researcher: ERROR - runtime lock was not generated for upstream ref $REPO_REF"
    exit 1
fi
if ! grep -Fqx "# upstream-lock-sha256: $UPSTREAM_LOCK_SHA256" "$RUNTIME_LOCK"; then
    echo "Local Deep Researcher: ERROR - runtime lock was not generated from the verified upstream lock"
    exit 1
fi
if ! grep -Fqx "# langgraph-cli-version: $LANGGRAPH_CLI_VERSION" "$RUNTIME_LOCK"; then
    echo "Local Deep Researcher: ERROR - runtime lock was not generated for langgraph-cli==$LANGGRAPH_CLI_VERSION"
    exit 1
fi
if ! grep -Eq "^langgraph-cli==${LANGGRAPH_CLI_VERSION}([[:space:]]|\\\\|$)" "$RUNTIME_LOCK"; then
    echo "Local Deep Researcher: ERROR - runtime lock does not contain langgraph-cli==$LANGGRAPH_CLI_VERSION"
    exit 1
fi
ensure_python_venv "$VENV_DIR" 3.11
uv pip sync --python "$VENV_PYTHON" --require-hashes "$RUNTIME_LOCK"
uv pip install --python "$VENV_PYTHON" --no-deps --no-build-isolation -e /app

# -------------------------------------------------------------------
# Initialize configuration from env vars (LITELLM_DEFAULT_MODEL, etc.)
# -------------------------------------------------------------------
echo "Local Deep Researcher: Initializing configuration from env vars..."
if ! python3 /app/scripts/init-config.py; then
    echo "Local Deep Researcher: ERROR - Failed to initialize configuration"
    echo "Local Deep Researcher: Ensure LITELLM_DEFAULT_MODEL is set and dependencies are installed"
    exit 1
fi

# Wait for the LiteLLM gateway to be available
echo "Local Deep Researcher: Checking LiteLLM gateway availability..."
if [ ! -f /app/.env ]; then
    echo "Local Deep Researcher: ERROR - Configuration file /app/.env not found"
    exit 1
fi

# set -e at the top of this script would otherwise abort here when
# grep finds no LITELLM_BASE_URL= line (exit code 1), making the
# explicit empty-handler below unreachable. Append || LITELLM_URL=""
# so the intended ERROR message can actually fire.
LITELLM_URL=$(grep '^LITELLM_BASE_URL=' /app/.env | cut -d'=' -f2-) || LITELLM_URL=""
if [ -z "$LITELLM_URL" ]; then
    echo "Local Deep Researcher: ERROR - LITELLM_BASE_URL not found in configuration"
    exit 1
fi
echo "Local Deep Researcher: Using LiteLLM at: $LITELLM_URL"

# Wait for LiteLLM /health/liveliness
max_retries=30
retry_count=0
until curl -s --fail --max-time 5 "$LITELLM_URL/health/liveliness" > /dev/null 2>&1; do
    retry_count=$((retry_count + 1))
    if [ $retry_count -ge $max_retries ]; then
        echo "Local Deep Researcher: ERROR - LiteLLM not available after $max_retries attempts"
        exit 1
    fi
    echo "Local Deep Researcher: Waiting for LiteLLM (attempt $retry_count/$max_retries)..."
    sleep 5
done

echo "Local Deep Researcher: LiteLLM gateway is available"

# Start the LangGraph server
echo "Local Deep Researcher: Starting LangGraph development server..."
cd /app

# Verify required files exist
if [ ! -f "/app/pyproject.toml" ]; then
    echo "Local Deep Researcher: ERROR - pyproject.toml not found"
    exit 1
fi

# Use the langgraph dev command to start the server
echo "Local Deep Researcher: Executing langgraph dev command..."
exec "$VENV_DIR/bin/langgraph" dev --host 0.0.0.0 --port 2024 --no-reload
