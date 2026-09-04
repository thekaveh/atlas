#!/bin/sh
# init-iceberg-rest.sh — idempotent Postgres bootstrap for Iceberg JDBC catalog.
set -eu

echo "==> iceberg-rest-init: verifying scoped iceberg database"

: "${ICEBERG_DB_USER:?ICEBERG_DB_USER is required}"
: "${ICEBERG_DB_PASSWORD:?ICEBERG_DB_PASSWORD is required}"

PGPASSWORD="${ICEBERG_DB_PASSWORD}" psql -X -w -h supabase-db \
  -U "${ICEBERG_DB_USER}" -d iceberg -v ON_ERROR_STOP=1 -Atqc 'SELECT 1' >/dev/null

echo "==> iceberg-rest-init: complete"
