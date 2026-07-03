#!/bin/sh
# init-iceberg-rest.sh — idempotent Postgres bootstrap for Iceberg JDBC catalog.
set -eu

echo "==> iceberg-rest-init: ensuring iceberg database exists"

: "${SUPABASE_DB_NAME:?SUPABASE_DB_NAME is required}"
: "${SUPABASE_DB_USER:?SUPABASE_DB_USER is required}"
: "${SUPABASE_DB_PASSWORD:?SUPABASE_DB_PASSWORD is required}"
: "${ICEBERG_DB_USER:?ICEBERG_DB_USER is required}"
: "${ICEBERG_DB_PASSWORD:?ICEBERG_DB_PASSWORD is required}"

export PGPASSWORD="${SUPABASE_DB_PASSWORD}"

psql -h supabase-db -U "${SUPABASE_DB_USER}" -d "${SUPABASE_DB_NAME}" -tAc \
     "SELECT 1 FROM pg_database WHERE datname='iceberg'" | grep -q 1 \
  || psql -h supabase-db -U "${SUPABASE_DB_USER}" -d "${SUPABASE_DB_NAME}" \
       -c "CREATE DATABASE iceberg"

echo "==> iceberg-rest-init: ensuring iceberg role exists"
printf "SELECT 1 FROM pg_roles WHERE rolname = :'role';\n" \
  | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
         -v role="${ICEBERG_DB_USER}" -v ON_ERROR_STOP=1 -tA | grep -q 1 \
  || printf "CREATE ROLE :\"role\" WITH LOGIN PASSWORD :'pw';\n" \
       | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
              -v role="${ICEBERG_DB_USER}" -v pw="${ICEBERG_DB_PASSWORD}" \
              -v ON_ERROR_STOP=1

printf "ALTER ROLE :\"role\" WITH PASSWORD :'pw';\n" \
  | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
         -v role="${ICEBERG_DB_USER}" -v pw="${ICEBERG_DB_PASSWORD}" \
         -v ON_ERROR_STOP=1
printf "GRANT ALL PRIVILEGES ON DATABASE iceberg TO :\"role\";\n" \
  | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
         -v role="${ICEBERG_DB_USER}" -v ON_ERROR_STOP=1
printf "ALTER DATABASE iceberg OWNER TO :\"role\";\n" \
  | psql -h supabase-db -U "${SUPABASE_DB_USER}" -d postgres \
         -v role="${ICEBERG_DB_USER}" -v ON_ERROR_STOP=1

unset PGPASSWORD

echo "==> iceberg-rest-init: complete"
