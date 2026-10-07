#!/usr/bin/env bash
# Precondition: run against a disposable or development Atlas stack.
# Precondition: .env has LIGHTRAG_SOURCE=container and role model variables set.
# Precondition: the configured models are available through LiteLLM.
# LIGHTRAG_SMOKE_WAIT_SECONDS (default 30) is the wait for extraction calls.
set -euo pipefail

project="${PROJECT_NAME:-atlas}"
lightrag_url="${LIGHTRAG_URL:-http://localhost:${LIGHTRAG_API_PORT:-63074}}"
api_key="${LIGHTRAG_API_KEY:-}"
extract_model="${LIGHTRAG_EXTRACT_LLM_MODEL:-}"
query_model="${LIGHTRAG_QUERY_LLM_MODEL:-}"

if [ -z "$api_key" ]; then
  echo "LIGHTRAG_API_KEY must be exported from .env before running this smoke." >&2
  echo "Example: set -a; . ./.env; set +a; scripts/smoke-lightrag-role-models.sh" >&2
  exit 2
fi

if [ -z "$extract_model" ] || [ -z "$query_model" ]; then
  echo "Set LIGHTRAG_EXTRACT_LLM_MODEL and LIGHTRAG_QUERY_LLM_MODEL before running this smoke." >&2
  exit 2
fi

# One request would satisfy both checks, so equal models prove nothing (#1389).
if [ "$extract_model" = "$query_model" ]; then
  echo "LIGHTRAG_EXTRACT_LLM_MODEL and LIGHTRAG_QUERY_LLM_MODEL are both $extract_model; set different models to tell the roles apart." >&2
  exit 2
fi

# Whether a log line names exactly this model: `gpt-4o` must not match a
# `gpt-4o-mini` line (#1389). LiteLLM logs the routed name, such as
# `model=openai/gpt-4o`, so a provider prefix ending in `/` still counts.
mentions_model() {
  local escaped
  escaped="$(printf '%s' "$2" | sed 's/[][\.*^$+?(){}|]/\\&/g')"
  printf '%s\n' "$1" | grep -Eq "(^|[^A-Za-z0-9._:-])${escaped}([^A-Za-z0-9._/:-]|\$)"
}

echo "[smoke] LightRAG URL: $lightrag_url"
echo "[smoke] expected EXTRACT model: $extract_model"
echo "[smoke] expected QUERY model: $query_model"
smoke_started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "[smoke] runtime role environment:"
docker compose -p "$project" exec -T lightrag sh -lc \
  'env | sort | grep -E "^(LLM_MODEL|EXTRACT_LLM_MODEL|KEYWORD_LLM_MODEL|QUERY_LLM_MODEL|EXTRACT_MAX_ASYNC_LLM|QUERY_LLM_TIMEOUT)="'

tmp_doc="$(mktemp)"
upload_response="$(mktemp "${TMPDIR:-/tmp}/lightrag-upload-response.XXXXXX")"
query_response="$(mktemp "${TMPDIR:-/tmp}/lightrag-query-response.XXXXXX")"
trap 'rm -f "$tmp_doc" "$upload_response" "$query_response"' EXIT
# LightRAG skips a document whose name or text it already stored, and serves
# a repeated query from its LLM cache, so a rerun would reach neither model:
# the file name, the text and the query all carry a per-run nonce (#1389).
nonce="$(date -u +%Y%m%dT%H%M%SZ)-$$"
upload_name="atlas-lightrag-role-smoke-${nonce}.txt"
cat > "$tmp_doc" <<DOC
Atlas is a self-hosted engineering platform. LightRAG is the graph-augmented RAG service. Role-specific LLM configuration lets extraction use a fast model while answers use a stronger model. Smoke run ${nonce}.
DOC

# LightRAG authenticates the key as X-API-Key; a Bearer header is parsed as
# a JWT and rejected with 401 (services/lightrag/README.md). The header goes
# on curl's stdin (-K-) so `ps` never shows the key.
lightrag_curl() {
  printf 'header = "X-API-Key: %s"\n' "$api_key" | curl -fsS -K- "$@"
}

echo "[smoke] uploading one small document"
lightrag_curl -X POST "$lightrag_url/documents/upload" \
  -F "file=@${tmp_doc};filename=${upload_name}" >"$upload_response"
if ! grep -Eq '"status"[[:space:]]*:[[:space:]]*"success"' "$upload_response"; then
  echo "[smoke] upload was not accepted for extraction: $(cat "$upload_response")" >&2
  exit 1
fi

wait_seconds="${LIGHTRAG_SMOKE_WAIT_SECONDS:-30}"
echo "[smoke] waiting ${wait_seconds} seconds for extraction calls to reach LiteLLM"
sleep "$wait_seconds"

echo "[smoke] querying LightRAG"
lightrag_curl -X POST "$lightrag_url/query" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"/hybrid What does role-specific LightRAG configuration allow Atlas to do in smoke run ${nonce}?\"}" \
  >"$query_response"

echo "[smoke] recent LiteLLM log lines mentioning expected models:"
litellm_model_logs="$(
  docker compose -p "$project" logs --since="$smoke_started_at" litellm \
    | grep -F -e "$extract_model" -e "$query_model" \
    | tail -40 || true
)"
printf '%s\n' "$litellm_model_logs"

if ! mentions_model "$litellm_model_logs" "$extract_model"; then
  echo "[smoke] missing expected EXTRACT model in LiteLLM logs: $extract_model" >&2
  exit 1
fi

if ! mentions_model "$litellm_model_logs" "$query_model"; then
  echo "[smoke] missing expected QUERY model in LiteLLM logs: $query_model" >&2
  exit 1
fi

echo "[smoke] upload response: $(cat "$upload_response")"
echo "[smoke] query response: $(cat "$query_response")"
echo "[smoke] passed: runtime env shows EXTRACT/QUERY values, and LiteLLM logs show requests for both expected models."
