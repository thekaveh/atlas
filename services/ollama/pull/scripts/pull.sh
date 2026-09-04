#!/bin/sh
set -e

# ollama-pull: pulls the active Ollama model set into a container-Ollama
# instance.  The model list is the union of:
#
#   OLLAMA_USER_MODELS   — comma-separated names set by the wizard
#                          (pre-filled in .env.example with the catalog
#                          defaults; overwritten by the wizard with the
#                          user's selection on each wizard run)
#   OLLAMA_CUSTOM_MODELS — free-text extra models added by the user
#
# The two vars are split on commas, trimmed, de-duplicated, and blanks
# dropped.  An empty union means no models to pull; the script logs a
# message and exits 0 cleanly.
#
# NOTE: this script no longer queries the Supabase DB via psql.
# The only required env var is OLLAMA_HOST_URL (the Ollama /api endpoint
# inside the Docker network).

echo "ollama-pull: Starting model pull process..."

# Check required env vars
if [ -z "$OLLAMA_HOST_URL" ]; then
  echo "ollama-pull: Error: OLLAMA_HOST_URL is not set."
  exit 1
fi

echo "ollama-pull: Waiting for Ollama API at $OLLAMA_HOST_URL..."
sleep 5 # Initial wait
# Bounded wait (300s, mirroring weaviate/minio/n8n init): ollama has no
# compose healthcheck and comfyui-init gates on this container completing,
# so an unbounded loop here used to wedge the whole init chain forever.
WAITED=0
until curl -sf --max-time 5 "$OLLAMA_HOST_URL/" > /dev/null; do
  WAITED=$((WAITED + 5))
  if [ "$WAITED" -ge 300 ]; then
    echo "ollama-pull: ERROR - Ollama API not reachable after 300s; giving up." >&2
    exit 1
  fi
  echo "ollama-pull: Waiting for Ollama API... (${WAITED}s)"
  sleep 5
done
echo "ollama-pull: Ollama API is available."

# Build the model list: union of OLLAMA_USER_MODELS and OLLAMA_CUSTOM_MODELS.
# Split on commas, trim whitespace, drop blanks, de-duplicate (preserve order
# of first occurrence).
_raw_list=$(
  printf '%s\n' "${OLLAMA_USER_MODELS:-}" "${OLLAMA_CUSTOM_MODELS:-}" \
    | tr ',' '\n' \
    | while IFS= read -r _m; do
        # xargs exits 1 on empty input in busybox; the fallback keeps the raw value so the -n guard below drops blanks.
        _mc=$(printf '%s' "$_m" | xargs 2>/dev/null || printf '%s' "$_m")
        [ -n "$_mc" ] && printf '%s\n' "$_mc"
      done \
    | awk '!seen[$0]++'
)

if [ -z "$_raw_list" ]; then
  echo "ollama-pull: No models configured (OLLAMA_USER_MODELS and OLLAMA_CUSTOM_MODELS are both empty). Nothing to pull."
  echo "ollama-pull: Finished model pulling process."
  exit 0
fi

echo "ollama-pull: Models to pull:"
echo "$_raw_list"

pull_stall_timeout="${OLLAMA_PULL_STALL_TIMEOUT_SECONDS:-120}"
case "$pull_stall_timeout" in
  ''|*[!0-9]*|0)
    echo "ollama-pull: ERROR - OLLAMA_PULL_STALL_TIMEOUT_SECONDS must be a positive integer." >&2
    exit 1
    ;;
esac

echo "$_raw_list" | while IFS= read -r model_clean; do
  if [ -n "$model_clean" ]; then
    echo "ollama-pull: Pulling $model_clean model from $OLLAMA_HOST_URL..."
    # Construct JSON payload
    json_payload="{\"name\":\"$model_clean\"}"
    # Pull with bounded retries. A transient registry/network blip on a
    # default model (e.g. qwen3-embedding:0.6b) would otherwise surface as a
    # one-off ERROR and leave the model missing until the next compose up.
    # /api/pull streams NDJSON and can report failures (bad model name,
    # registry errors) inside the body with HTTP 200, so each attempt checks
    # BOTH the exit code AND the body for an error line. `set -e` above would
    # abort on the first failed curl, so we tolerate non-zero exits here and
    # branch on the captured status instead.
    max_attempts=3
    attempt=1
    pulled=0
    while [ "$attempt" -le "$max_attempts" ]; do
      curl_exit_code=0
      curl_output=$(curl -sf --connect-timeout 20 \
        --speed-time "$pull_stall_timeout" --speed-limit 1024 \
        -X POST "$OLLAMA_HOST_URL/api/pull" -d "$json_payload" 2>&1) || curl_exit_code=$?
      echo "ollama-pull: Curl exit code: $curl_exit_code (attempt $attempt/$max_attempts)"
      echo "ollama-pull: Curl output: $curl_output"
      if [ "$curl_exit_code" -eq 0 ] && ! printf '%s' "$curl_output" | grep -q '"error"'; then
        pulled=1
        break
      fi
      echo "ollama-pull: WARN - pull of $model_clean failed (attempt $attempt/$max_attempts)." >&2
      attempt=$((attempt + 1))
      if [ "$attempt" -le "$max_attempts" ]; then
        # Linear backoff: 10s, then 15s before the final attempt.
        sleep $((attempt * 5))
      fi
    done
    # Non-fatal: a typo'd OLLAMA_USER_MODELS entry or a persistently
    # unavailable model only surfaces as a LiteLLM 404 at request time, so we
    # log and continue rather than aborting the whole pull set.
    if [ "$pulled" -ne 1 ]; then
       echo "ollama-pull: ERROR - Failed to pull model $model_clean after $max_attempts attempts. See output above." >&2
    fi
    echo # Newline after each pull attempt output
  fi
done

echo "ollama-pull: Finished model pulling process."
