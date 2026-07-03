#!/usr/bin/env bash
# init-airflow.sh — one-shot init container for Airflow.
#
# Responsibilities (idempotent — re-runs are no-ops):
# 1. Create the `airflow` database in Supabase Postgres if missing.
# 2. Create the `${AIRFLOW_DB_USER}` Postgres role if missing.
# 3. Run `airflow db migrate`.
# 4. Create the admin user.
# 5. Seed Connection objects for every sibling service whose source is
#    not 'disabled'.
set -euo pipefail

echo "==> airflow-init: ensuring airflow database exists"
# Use Supabase admin creds to CREATE DATABASE if absent. supabase-db
# entrypoint runs as 'postgres' superuser; we connect via the
# Supabase-managed db with the configured admin user.
export PGPASSWORD="${SUPABASE_DB_PASSWORD}"
psql -h supabase-db -U "${SUPABASE_DB_USER}" -d "${SUPABASE_DB_NAME}" -tAc \
     "SELECT 1 FROM pg_database WHERE datname='airflow'" | grep -q 1 \
  || psql -h supabase-db -U "${SUPABASE_DB_USER}" -d "${SUPABASE_DB_NAME}" \
       -c "CREATE DATABASE airflow"

echo "==> airflow-init: ensuring airflow role exists"
# psql :'var' interpolation quotes the password server-side, avoiding
# SQL breakage on quoted passwords — but it only works in SCRIPT input
# (stdin / -f), NOT inside -c strings, so the statements are piped in.
printf "SELECT 1 FROM pg_roles WHERE rolname = :'role';\n" \
  | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
         -v role="${AIRFLOW_DB_USER}" -v ON_ERROR_STOP=1 -tA | grep -q 1 \
  || printf "CREATE ROLE :\"role\" WITH LOGIN PASSWORD :'pw';\n" \
       | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
              -v role="${AIRFLOW_DB_USER}" -v pw="${AIRFLOW_DB_PASSWORD}" \
              -v ON_ERROR_STOP=1
# Re-apply the password every run so that AIRFLOW_DB_PASSWORD rotations
# in .env are picked up — CREATE ROLE only runs the first time.
# Idempotent: setting the role's password to its current value is a no-op.
printf "ALTER ROLE :\"role\" WITH PASSWORD :'pw';\n" \
  | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
         -v role="${AIRFLOW_DB_USER}" -v pw="${AIRFLOW_DB_PASSWORD}" \
         -v ON_ERROR_STOP=1
printf "GRANT ALL PRIVILEGES ON DATABASE airflow TO :\"role\";\n" \
  | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
         -v role="${AIRFLOW_DB_USER}" -v ON_ERROR_STOP=1
# Postgres 15+ (the stack ships supabase/postgres:17.x) tightened the
# public-schema default: ALL PRIVILEGES on the database does NOT include
# CREATE on `public` — only the database OWNER has that, via the magic
# pg_database_owner role. CREATE DATABASE made supabase_admin the owner,
# so airflow's `db migrate` would fail with "permission denied for schema
# public" on every cold start. Re-owning the database to airflow flips
# pg_database_owner over (idempotent — `ALTER ... OWNER TO already_owner`
# is a no-op).
printf "ALTER DATABASE airflow OWNER TO :\"role\";\n" \
  | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
         -v role="${AIRFLOW_DB_USER}" -v ON_ERROR_STOP=1
unset PGPASSWORD

echo "==> airflow-init: running airflow db migrate"
airflow db migrate

echo "==> airflow-init: creating admin user (idempotent)"
# Branch on exit code, not on stdout keywords. The earlier
# `grep -qiE "error|exception|traceback"` heuristic over-matched
# (e.g. a success message containing "no errors" would trigger exit 1).
# `airflow users create` returns rc=0 on success AND on the documented
# idempotent already-exists case in 3.x; we still grep stdout to suppress
# the "already exists" message when the user is unchanged.
# The `equals` form of every flag below (not the space-separated form)
# is REQUIRED for any value that may legitimately start with `-`.
# `secrets.token_urlsafe` emits the URL-safe Base64 alphabet
# `[A-Za-z0-9_-]`, so ~3% of generated passwords start with a leading
# dash. argparse then interprets the value as another flag and rejects:
# `argument -p: expected one argument`. The equals form binds the value
# to the flag in a single token regardless. Same applies to every
# conn-password flag in the add_conn calls below.
if create_output=$(airflow users create \
  --username=admin \
  --firstname=Admin \
  --lastname=User \
  --role=Admin \
  --email=admin@localhost \
  --password="${AIRFLOW_ADMIN_PASSWORD}" 2>&1); then
  if echo "$create_output" | grep -qE "already exists|already a user"; then
    echo "(admin user already exists — skipping)"
  else
    echo "$create_output"
  fi
else
  rc=$?
  echo "airflow-init: ERROR during admin user creation (rc=$rc):" >&2
  echo "$create_output" >&2
  exit 1
fi

echo "==> airflow-init: seeding Connections (gated on sibling source)"

# Helper: idempotent add (delete + add to update on second run)
add_conn() {
  local conn_id=$1; shift
  airflow connections delete "$conn_id" >/dev/null 2>&1 || true
  airflow connections add "$conn_id" "$@"
}

# Orphan-cleanup pass: drop any source-gated Connection up front. The
# guarded add_conn calls below will re-create the ones still active. This
# prevents stale entries from a prior run where the sibling source was
# `container` from sitting in the metadata DB after the user flips it
# back to `disabled` — orphan Connections would point at dead DNS names
# and confuse DAGs that reference them.
for orphan in spark_default minio_default weaviate_default neo4j_default; do
  airflow connections delete "$orphan" >/dev/null 2>&1 || true
done

# Gating convention: every gate uses `= "container"` (NOT `!= "disabled"`).
# The `!= "disabled"` form silently includes the `localhost` source variant
# for services that support it (weaviate / neo4j), which would seed a
# Connection pointing at the in-Compose DNS name (e.g. `weaviate:8080`)
# that does NOT resolve when the user is running the service on the host.
# In-container DNS is only valid when source == container. For host-side
# variants, the user must manage Connection objects manually (or extend
# this script to read the LOCALHOST URL override env vars).
#
# LiteLLM + Postgres + Redis are unconditional: LiteLLM is locked
# always-on (services/litellm/service.yml: "Locked. Mandatory."), Postgres
# is a required dependency, and Redis ships as a container-only always-on
# service (services/redis/service.yml).

if [ "${SPARK_SOURCE}" = "container" ]; then
  deploy_mode="${ATLAS_LAKEHOUSE_SPARK_DEPLOY_MODE:-cluster}"
  add_conn spark_default \
    --conn-type spark \
    --conn-host spark-master \
    --conn-port 7077 \
    --conn-extra "{\"deploy-mode\": \"${deploy_mode}\", \"spark-binary\": \"spark-submit\"}"
fi

if [ "${MINIO_SOURCE}" = "container" ]; then
  # MinIO doesn't use DNS-style addressing (bucket.minio:9000); boto3
  # defaults to virtual-hosted style and would fail DNS for any
  # bucket-level S3 op. region_name avoids NoRegionError on newer
  # boto3. Mirrors the spark.hadoop.fs.s3a.path.style.access=true
  # already set on Spark's compose.
  add_conn minio_default \
    --conn-type aws \
    --conn-extra "{\"endpoint_url\": \"http://minio:9000\", \"aws_access_key_id\": \"${MINIO_ROOT_USER}\", \"aws_secret_access_key\": \"${MINIO_ROOT_PASSWORD}\", \"region_name\": \"us-east-1\", \"config_kwargs\": {\"s3\": {\"addressing_style\": \"path\"}}}"
fi

# OpenAIHook.get_conn() does:
#   base_url = openai_client_kwargs.pop("base_url", None) or conn.host
# It does NOT recognize `api_base` (silently ignored). So the `/v1` must
# live IN conn.host — otherwise the OpenAI client POSTs to
# http://litellm:4000/chat/completions and LiteLLM 404s the path
# (correct URL is http://litellm:4000/v1/chat/completions).
add_conn litellm_default \
  --conn-type openai \
  --conn-host http://litellm:4000/v1 \
  --conn-password="${LITELLM_MASTER_KEY}"

# NOTE: postgres_supabase intentionally uses the SUPABASE_DB_USER (admin)
# credentials today. The .env declares SUPABASE_DB_APP_USER / _PASSWORD
# for a least-privilege application-tier user, but supabase-db-init's
# scripts do not actually CREATE that role yet — switching this seed to
# app_user without first creating the role would break every DAG that
# uses postgres_supabase. Tracked as a known follow-up in CHANGELOG —
# the safer migration is: (1) wire app_user creation into supabase-db-init,
# (2) flip this seed + add an Airflow Variable for the admin escape hatch.
add_conn postgres_supabase \
  --conn-type postgres --conn-host supabase-db --conn-port 5432 \
  --conn-schema "${SUPABASE_DB_NAME}" \
  --conn-login="${SUPABASE_DB_USER}" \
  --conn-password="${SUPABASE_DB_PASSWORD}"

if [ "${WEAVIATE_SOURCE}" = "container" ]; then
  # WeaviateHook.get_conn() passes conn.host straight into weaviate-client's
  # `connect_to_custom(http_host=..., http_port=conn.port or 80,
  #   grpc_host=extras['grpc_host'] or conn.host, grpc_port=extras['grpc_port'] or 80)`.
  # So host must be bare (`weaviate`, NOT `http://weaviate:8080`) and the
  # gRPC port must be set via extras — Weaviate's gRPC API lives on 50051,
  # not 80, and the v4 client requires gRPC for query operations.
  add_conn weaviate_default \
    --conn-type weaviate \
    --conn-host weaviate --conn-port 8080 \
    --conn-extra '{"grpc_host": "weaviate", "grpc_port": 50051}'
fi

# NOTE: var name is NEO4J_GRAPH_DB_SOURCE (NOT NEO4J_SOURCE — the latter
# does not exist in .env.example and was silently undefined → "disabled"
# → neo4j_default never seeded prior to this fix).
# Compose service id is `neo4j-graph-db` (NOT `neo4j`); every other
# in-stack consumer uses bolt://neo4j-graph-db:7687. Credentials come
# from GRAPH_DB_USER / GRAPH_DB_PASSWORD (the Neo4j family's canonical
# env vars), passed through compose.yml's airflow-init environment.
#
# Neo4jHook.get_uri() builds `f"{scheme}://{conn.host}:{port}"` where
# scheme is `bolt` by default. So conn.host must NOT include the
# `bolt://` prefix — otherwise the URI becomes `bolt://bolt://neo4j-...`
# and the Neo4j driver rejects it with a parse error.
if [ "${NEO4J_GRAPH_DB_SOURCE}" = "container" ]; then
  add_conn neo4j_default \
    --conn-type neo4j \
    --conn-host "neo4j-graph-db" \
    --conn-port 7687 \
    --conn-login="${GRAPH_DB_USER}" \
    --conn-password="${GRAPH_DB_PASSWORD}"
fi

add_conn redis_default --conn-type redis --conn-host redis --conn-port 6379 --conn-password="${REDIS_PASSWORD}"

echo "==> airflow-init: complete"
