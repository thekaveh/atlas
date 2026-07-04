#!/bin/bash
set -e

REPO_URL="https://github.com/langchain-ai/local-deep-researcher.git"
REPO_DIR="/app/repo"

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
# Fetch latest upstream code (clone on first run, pull on restart)
# -------------------------------------------------------------------
echo "Local Deep Researcher: Syncing upstream repository..."
if [ -d "$REPO_DIR/.git" ]; then
    echo "Local Deep Researcher: Pulling latest changes..."
    if git -C "$REPO_DIR" pull --ff-only 2>/dev/null; then
        echo "Local Deep Researcher: Repository updated successfully"
    else
        echo "Local Deep Researcher: Pull failed — re-cloning..."
        find "$REPO_DIR" -mindepth 1 -delete 2>/dev/null || true
        git clone "$REPO_URL" "$REPO_DIR"
    fi
else
    echo "Local Deep Researcher: Cloning repository..."
    find "$REPO_DIR" -mindepth 1 -delete 2>/dev/null || true
    git clone "$REPO_URL" "$REPO_DIR"
fi

# Copy upstream source into working directory (preserving our custom scripts/config)
cp -r "$REPO_DIR"/src /app/
cp "$REPO_DIR"/pyproject.toml /app/
cp "$REPO_DIR"/langgraph.json /app/

# Optional Atlas integration: when full-page mode is crawl4ai, replace
# upstream's direct httpx fetch helper with a token-authenticated Crawl4AI
# adapter. Other modes leave upstream source untouched.
python3 /app/scripts/patch-litellm-openai-provider.py
python3 /app/scripts/patch-crawl4ai-fetch.py

echo "Local Deep Researcher: Installing dependencies..."
uv pip install --system -r /app/pyproject.toml

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
exec uvx --refresh --from "langgraph-cli[inmem]" --with-editable . --python 3.11 langgraph dev --host 0.0.0.0 --port 2024 --no-reload
