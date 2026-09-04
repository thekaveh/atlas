#!/bin/sh
# shellcheck shell=bash
# LightRAG init container.
# Required tools are baked into the service-owned init image.
# Bash re-exec with sentinel to avoid loop.
set -e

if [ "${INIT_BOOTSTRAPPED:-}" != "1" ]; then
  export INIT_BOOTSTRAPPED=1
  exec bash -- "$0" "$@"
fi

# ── bash body ─────────────────────────────────────────────────────────────
set -euo pipefail

if [ "${LIGHTRAG_SOURCE:-disabled}" = "disabled" ]; then
  echo "[lightrag-init] LIGHTRAG_SOURCE=disabled, nothing to do"
  exit 0
fi

# LiteLLM readiness is guaranteed by compose: lightrag-init's compose entry
# declares `depends_on: litellm: condition: service_healthy`, so by the time
# this script runs LiteLLM has already passed its /health/liveliness probe.
# No in-script poll loop needed (matches the hermes-init pattern).

echo "[lightrag-init] resolving model bindings..."
# Write resolved LLM_MODEL / EMBEDDING_MODEL / EMBEDDING_DIM to the
# shared volume path /app/data/.env. The lightrag container's compose
# entrypoint overrides the image's direct python invocation with a
# `sh -c 'set -a; . /app/data/.env; exec ...'` wrapper so these values
# enter the server's environment. The lightrag compose env block does
# NOT set LLM_MODEL / EMBEDDING_MODEL / EMBEDDING_DIM — that would
# clobber the resolved values via os.environ precedence.
# Write to a temp file then mv atomically — the redirect `>` truncates
# the destination BEFORE python runs, so a python crash would leave
# /app/data/.env empty and lightrag would boot with no LLM_MODEL /
# EMBEDDING_MODEL / EMBEDDING_DIM. Mirrors init-hermes.sh's pattern.
python3 /scripts/resolve-models.py > /app/data/.env.tmp
mv /app/data/.env.tmp /app/data/.env

if [ -n "${LIGHTRAG_PG_URI:-}" ]; then
  # Postgres (supabase-db) is SOURCE-replaceable — it can be container,
  # localhost, or external — so lightrag-init deliberately does NOT hard
  # depend_on it in compose (scripts/check-compose-source-deps.py FORBIDS
  # the ('lightrag','supabase-db') edge; LightRAG falls back to in-process
  # NanoVectorDB when the source is disabled). The transitive litellm:
  # service_healthy gate makes supabase-db *usually* ready by now, but that
  # isn't guaranteed across source modes — so poll the endpoint here before
  # migrating: the SOURCE-safe equivalent of a compose readiness gate.
  echo "[lightrag-init] waiting for Postgres (pgvector store) to accept connections..."
  pg_ready=0
  attempt=0
  while [ "$attempt" -lt 60 ]; do
    if psql "${LIGHTRAG_PG_URI}" -v ON_ERROR_STOP=1 -tAc 'SELECT 1' >/dev/null 2>&1; then
      pg_ready=1
      break
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  if [ "$pg_ready" -ne 1 ]; then
    echo "[lightrag-init] ERROR: Postgres not reachable at LIGHTRAG_PG_URI after 120s" >&2
    exit 1
  fi
  echo "[lightrag-init] running pgvector migrations..."
  psql "${LIGHTRAG_PG_URI}" -v ON_ERROR_STOP=1 -f /scripts/migrate-pgvector.sql
fi

if [ -n "${LIGHTRAG_NEO4J_URI:-}" ]; then
  echo "[lightrag-init] running Neo4j migrations..."
  # Use Neo4j's HTTP transaction endpoint directly (cypher-shell is not in
  # alpine main; bundling it would inflate the init image significantly).
  # The endpoint takes exactly ONE Cypher statement per statements[] entry
  # — the previous single-entry payload was a guaranteed syntax error that
  # Neo4j reports with HTTP 200 + a populated errors[] array, so the
  # per-statement split AND the errors[] check below are both load-bearing.
  cypher_payload=$(grep -v '^//' /scripts/migrate-neo4j.cypher | jq -Rs '
    split(";")
    | map(gsub("[[:space:]]+"; " ") | gsub("^ | $"; ""))
    | map(select(length > 0))
    | {statements: map({statement: .})}')
  http_status=$(curl -fs --max-time 30 -o /tmp/neo4j-resp.json -w '%{http_code}' \
    -u "${LIGHTRAG_NEO4J_USERNAME}:${LIGHTRAG_NEO4J_PASSWORD}" \
    -H 'Content-Type: application/json' \
    --data "$cypher_payload" \
    "http://neo4j-graph-db:7474/db/neo4j/tx/commit" 2>&1) || {
      echo "[lightrag-init] WARN: Neo4j migration HTTP call failed (curl exit $?)" >&2
      echo "[lightrag-init] response: $(cat /tmp/neo4j-resp.json 2>/dev/null)" >&2
    }
  if [ "$http_status" = "200" ] && jq -e '.errors == []' /tmp/neo4j-resp.json >/dev/null 2>&1; then
    echo "[lightrag-init] Neo4j migration: OK ($(jq -r '.results | length' /tmp/neo4j-resp.json) statements)"
  else
    echo "[lightrag-init] WARN: Neo4j migration returned HTTP $http_status" >&2
    echo "[lightrag-init] response: $(cat /tmp/neo4j-resp.json 2>/dev/null)" >&2
    # Non-fatal — this only pre-creates an index. With the default
    # Neo4JStorage, LightRAG creates the same range index itself on first write
    # (CREATE INDEX ... IF NOT EXISTS); if Neo4j is disabled it falls back to
    # in-memory NetworkXStorage. Either way a skipped migration costs nothing.
  fi
fi

echo "[lightrag-init] done"
