#!/bin/sh
# shellcheck shell=bash
# init-hermes.sh — render /opt/data/config.yaml (and supporting files)
# from environment, then exit. Runs before the hermes service starts
# (see docker-compose.yml depends_on).
#
# Idempotent: re-running overwrites the GENERATED files but leaves
# user-edited files (sessions/, memories/MEMORY.md, etc.) intact.
#
# Three responsibilities:
#   1. Ensure /opt/data exists and is owned by HERMES_UID:HERMES_GID
#      (the hermes container runs as that user).
#   2. Render /opt/data/config.yaml from /templates/config.yaml.tmpl
#      via envsubst, surgically dropping provider blocks whose
#      *_INTERNAL_URL is empty (i.e. the underlying stack service is
#      disabled).
#   3. Drop a comfyui-host override file under /opt/data/skills/ so
#      Hermes's bundled creative-comfyui skill targets our internal
#      comfyui:18188 instead of its hardcoded 127.0.0.1:8188.
#
# Exits non-zero on any unexpected condition so docker compose surfaces
# the failure instead of letting hermes start with a broken config.

# ─── Bootstrap ─────────────────────────────────────────────────────
# Runs on bare alpine — the rest of the script uses bashisms (indirect
# expansion, [[ ... ]]) and pipes (pipefail). Mirrors the openclaw-init /
# weaviate-init pattern: install runtime deps via apk-add, then re-exec
# under bash. Idempotent: apk-add is a no-op when packages are already
# present (e.g. on container restart, or when the user pre-bakes them
# into a custom HERMES_INIT_IMAGE).
#
# The HERMES_INIT_BOOTSTRAPPED sentinel prevents an infinite re-exec
# loop if for some reason bash itself triggers a re-read of this script.
set -eu
if [ "${HERMES_INIT_BOOTSTRAPPED:-0}" != "1" ]; then
  apk add --no-cache bash gettext curl jq ca-certificates >/dev/null 2>&1
  HERMES_INIT_BOOTSTRAPPED=1
  export HERMES_INIT_BOOTSTRAPPED
  exec bash -- "$0" "$@"
fi

# ─── Bash body ─────────────────────────────────────────────────────
# Re-executed under bash from here on; pipefail and bash-only features
# (indirect expansion, [[ ]]) become available.
set -euo pipefail

DATA_DIR=/opt/data
CONFIG_OUT="${DATA_DIR}/config.yaml"
SKILLS_DIR="${DATA_DIR}/skills"
TEMPLATE_DIR=/templates

HERMES_UID="${HERMES_UID:-10000}"
HERMES_GID="${HERMES_GID:-10000}"

log() { printf '[hermes-init] %s\n' "$*"; }

log "starting"
log "  DATA_DIR=${DATA_DIR}"
log "  CONFIG_OUT=${CONFIG_OUT}"
log "  HERMES_UID:HERMES_GID=${HERMES_UID}:${HERMES_GID}"

mkdir -p "${DATA_DIR}" "${SKILLS_DIR}"

# ─── render config.yaml ────────────────────────────────────────────
# envsubst replaces ${VAR}; the template references only the env vars
# we explicitly export below so unrelated env vars (LITELLM_MASTER_KEY
# etc.) don't accidentally land in config.yaml in plain text via
# stray ${...} occurrences.

# Defaults so envsubst never emits a literal "/v1" with no host.
export HERMES_DEFAULT_MODEL="${HERMES_DEFAULT_MODEL:-}"
export HERMES_CONTEXT_LENGTH="${HERMES_CONTEXT_LENGTH:-65536}"
export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-}"

# ─── auto-pick HERMES_DEFAULT_MODEL when blank ─────────────────────
# Hermes is single-default-model: it cannot dispatch a request unless
# config.yaml's ``model.default`` resolves to a real LiteLLM model.
# Leaving HERMES_DEFAULT_MODEL blank means the rendered config carries
# ``default: null``, every Hermes request 500s, and Open WebUI's
# ``hermes-agent`` proxy route (which goes LiteLLM → Hermes) returns
# errors — looks identical to "Hermes can't see any models".
#
# When the operator hasn't pinned a value, query the LiteLLM gateway's
# /v1/models endpoint and pick the first name from a curated priority
# list. The list is ordered: best local-runtime model first (cheapest,
# privacy-friendly), then big-context cloud fallbacks. Models not
# enabled in the gateway today are silently skipped — there's always
# at least one match because the wizard refuses to start if every
# provider is disabled.
#
# This block runs after the apk-add bootstrap, so curl + jq are
# already on the PATH.
#
# We always query /v1/models once here — the result is used for BOTH
# the HERMES_DEFAULT_MODEL auto-pick (when blank) AND the filtered
# provider-picker list rendered below. Querying unconditionally keeps
# the two flows in sync; previously the second flow received an empty
# catalog whenever the operator pinned HERMES_DEFAULT_MODEL.
litellm_url="${LITELLM_BASE_URL:-http://litellm:4000}"
log "querying LiteLLM /v1/models"
models_json=$(curl -fsS --max-time 15 \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  "${litellm_url}/v1/models" 2>/dev/null || true)
if [[ -n "${models_json}" ]]; then
  available_ids=$(printf '%s' "${models_json}" \
    | jq -r '.data[]?.id' 2>/dev/null || true)
else
  available_ids=""
  log "⚠ LiteLLM /v1/models returned empty — picker list will be empty"
fi

if [[ -z "${HERMES_DEFAULT_MODEL}" ]]; then
  log "HERMES_DEFAULT_MODEL is unset — picking from the LiteLLM catalog"
  if [[ -n "${available_ids}" ]]; then
    # Priority order — pick the first one that's actually published.
    # Ollama qwen3.6 is the curated default and clears the 64K floor
    # at 256K. Cloud fallbacks listed by decreasing context window.
    for candidate in \
        "ollama/qwen3.6:latest" \
        "claude-sonnet-4-6" \
        "claude-opus-4-7" \
        "gpt-5" \
        "gpt-5-codex" \
        "gpt-5-mini"; do
      if printf '%s\n' "${available_ids}" | grep -qx "${candidate}"; then
        HERMES_DEFAULT_MODEL="${candidate}"
        log "  auto-selected ${candidate} from LiteLLM model_list"
        break
      fi
    done
    # If nothing on the priority list matched, fall back to the first
    # CHAT model the gateway exposes. Exclude: hermes-agent (Hermes routing
    # to itself = recursive loop), lightrag (also self-loops), and any
    # embedding model — /v1/models lists embeddings (e.g. ollama/nomic-embed-text)
    # ahead of synthesized chat models, so a naive head -n1 could pick an
    # embeddings-only route and 500 every Hermes request. Mirrors the LightRAG
    # resolver's filter (services/lightrag/init/scripts/resolve-models.py).
    if [[ -z "${HERMES_DEFAULT_MODEL}" ]]; then
      # `|| true`: when the filter chain empties (e.g. an Ollama-only stack with
      # just an embedding model pulled), a grep exits 1 and `set -o pipefail`
      # would abort the whole init container instead of falling through to the
      # graceful "set HERMES_DEFAULT_MODEL" warning below.
      HERMES_DEFAULT_MODEL=$(printf '%s\n' "${available_ids}" \
        | grep -vx "hermes-agent" | grep -vx "lightrag" | grep -ivE 'embed' | head -n1 || true)
      if [[ -n "${HERMES_DEFAULT_MODEL}" ]]; then
        log "  auto-selected ${HERMES_DEFAULT_MODEL} (first non-hermes chat model)"
      fi
    fi
  fi
  if [[ -z "${HERMES_DEFAULT_MODEL}" ]]; then
    log "⚠ could not auto-select a default model — config.yaml will have model.default empty; set HERMES_DEFAULT_MODEL in .env explicitly"
  fi
  export HERMES_DEFAULT_MODEL
fi
export TTS_INTERNAL_URL="${TTS_INTERNAL_URL:-}"
export STT_INTERNAL_URL="${STT_INTERNAL_URL:-}"
export STT_INTERNAL_API_KEY="${STT_INTERNAL_API_KEY:-}"
export COMFYUI_INTERNAL_URL="${COMFYUI_INTERNAL_URL:-}"
export SEARXNG_INTERNAL_URL="${SEARXNG_INTERNAL_URL:-}"
export LIGHTRAG_INTERNAL_URL="${LIGHTRAG_INTERNAL_URL:-}"
export LIGHTRAG_API_KEY="${LIGHTRAG_API_KEY:-}"

# ─── compose providers.litellm.models list ────────────────────────
# Hermes's provider picker lists every model returned by
# /v1/models. Two cleanups are needed against the raw LiteLLM
# catalog before it hits the picker:
#
#   1. Drop ``hermes-agent``. LiteLLM advertises Hermes itself as
#      a model so OTHER consumers (Open WebUI, n8n, backend,
#      jupyterhub) can chat with Hermes via the gateway. Inside
#      the Hermes UI it's a recursive loop — Hermes → LiteLLM →
#      Hermes — and just confuses the picker.
#
#   2. Dedupe ``ollama/X`` vs bare ``X`` pairs. LiteLLM emits both
#      as a convenience for callers that hard-code one form or the
#      other; in the Hermes picker they look like the same model
#      listed twice. We keep the prefixed form (canonical) and drop
#      the bare alias ONLY when its ``ollama/``-prefixed twin exists —
#      other prefixed lookalikes (``openrouter/openai/gpt-5`` vs the
#      direct-API ``gpt-5``) are different upstreams, not aliases,
#      and both stay.
#
# We then pin this filtered list via ``discover_models: false`` +
# ``models: [...]`` in providers.litellm. Tradeoff: new models pulled
# AFTER the stack is running (via ``ollama pull`` or wizard updates)
# don't appear until the next ./start.sh. Acceptable since the wizard
# and litellm-init always run at boot.
LITELLM_MODELS_LIST="[]"
if [[ -n "${models_json:-}" ]]; then
  filtered=$(printf '%s' "$models_json" | jq -r '
    .data | map(.id) as $all
    | $all
    | map(
        select(. != "hermes-agent")
        | . as $current
        | select(
            (contains("/"))
            or
            # Only the ollama dual-alias counts as a twin of a bare id.
            # The old endswith("/"+id) check also matched OpenRouter
            # forms (openrouter/openai/gpt-5 vs gpt-5) and silently
            # dropped the DIRECT-API entry — a different upstream with
            # different keys/billing, not an alias.
            (($all | any(. == ("ollama/" + $current))) | not)
          )
      )
    | .[]
  ' 2>/dev/null || true)
  if [[ -n "$filtered" ]]; then
    # Flow-style YAML list (also valid JSON) so envsubst on one line works.
    LITELLM_MODELS_LIST="["
    first=1
    while IFS= read -r m; do
      [[ -z "$m" ]] && continue
      if [[ $first -eq 1 ]]; then
        LITELLM_MODELS_LIST+="\"$m\""
        first=0
      else
        LITELLM_MODELS_LIST+=", \"$m\""
      fi
    done <<< "$filtered"
    LITELLM_MODELS_LIST+="]"
    log "  filtered LiteLLM models for picker: ${LITELLM_MODELS_LIST}"
  fi
fi
export LITELLM_MODELS_LIST

# Build the variable list explicitly so envsubst only touches the
# ones we know about.
# shellcheck disable=SC2016
VARS='${HERMES_DEFAULT_MODEL} ${HERMES_CONTEXT_LENGTH} ${LITELLM_MASTER_KEY}
${LITELLM_MODELS_LIST}
${TTS_INTERNAL_URL} ${STT_INTERNAL_URL} ${STT_INTERNAL_API_KEY} ${COMFYUI_INTERNAL_URL}
${SEARXNG_INTERNAL_URL}
${LIGHTRAG_INTERNAL_URL} ${LIGHTRAG_API_KEY}'

if [[ ! -f "${TEMPLATE_DIR}/config.yaml.tmpl" ]]; then
  log "❌ template ${TEMPLATE_DIR}/config.yaml.tmpl is missing — check the bind mount"
  exit 1
fi

rendered=$(envsubst "$VARS" < "${TEMPLATE_DIR}/config.yaml.tmpl")

# Strip provider blocks whose URL came out empty. The template uses
# sentinel comment lines `# BEGIN-TTS / # END-TTS` etc. so we can
# safely delete a multi-line block when its URL didn't resolve.
strip_block() {
  local tag="$1" url_var="$2"
  if [[ -z "${!url_var:-}" ]]; then
    rendered=$(printf '%s\n' "$rendered" \
      | awk -v t="$tag" '
          $0 ~ "# BEGIN-"t"$" {skip=1; next}
          $0 ~ "# END-"t"$"   {skip=0; next}
          !skip {print}
        ')
    log "  dropped ${tag} block (no ${url_var})"
  fi
}

strip_block TTS    TTS_INTERNAL_URL
strip_block STT    STT_INTERNAL_URL
strip_block SEARCH SEARXNG_INTERNAL_URL
strip_block RAG    LIGHTRAG_INTERNAL_URL

# Atomic write so a crash mid-write doesn't leave a partial config.
tmp="${CONFIG_OUT}.tmp"
printf '%s\n' "$rendered" > "$tmp"
mv "$tmp" "$CONFIG_OUT"
log "wrote ${CONFIG_OUT}"

# ─── ComfyUI host override ─────────────────────────────────────────
# Drop a skill-override that pins comfyui-host to our internal URL,
# bypassing the bundled skill's hardcoded 127.0.0.1:8188.
if [[ -n "${COMFYUI_INTERNAL_URL:-}" && -f "${TEMPLATE_DIR}/comfyui-host-override.md" ]]; then
  out="${SKILLS_DIR}/creative-comfyui-host-override.md"
  # shellcheck disable=SC2016
  COMFYUI_INTERNAL_URL="${COMFYUI_INTERNAL_URL}" \
    envsubst '${COMFYUI_INTERNAL_URL}' < "${TEMPLATE_DIR}/comfyui-host-override.md" > "${out}.tmp"
  mv "${out}.tmp" "${out}"
  log "wrote ${out}"
else
  log "skipped ComfyUI host override (COMFYUI_INTERNAL_URL empty or template missing)"
fi

# ─── ownership ─────────────────────────────────────────────────────
chown -R "${HERMES_UID}:${HERMES_GID}" "${DATA_DIR}" 2>/dev/null || {
  log "⚠ chown failed (running rootless?) — hermes container may need to fix perms itself"
}

log "done"
