#!/bin/sh
# Verify the centrally provisioned Langfuse database identity.
set -eu

echo "langfuse-init: starting provisioning..."

: "${PGHOST:?PGHOST is required}"
: "${PGPORT:?PGPORT is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${LANGFUSE_DB_NAME:?LANGFUSE_DB_NAME is required}"

export PGPASSWORD

echo "langfuse-init: waiting for Postgres at ${PGHOST}:${PGPORT}..."
i=0
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 30 ]; then
        echo "langfuse-init: ERROR - Postgres not ready after 30 attempts" >&2
        exit 1
    fi
    sleep 2
done

psql -X -w -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" \
  -d "$LANGFUSE_DB_NAME" -v ON_ERROR_STOP=1 -Atqc 'SELECT 1' >/dev/null

echo "langfuse-init: provisioning complete"
