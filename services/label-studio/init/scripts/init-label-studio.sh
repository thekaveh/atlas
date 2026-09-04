#!/bin/sh
# Verify the centrally provisioned Label Studio database identity.
set -eu

echo "label-studio-init: starting provisioning..."

: "${PGHOST:?PGHOST is required}"
: "${PGPORT:?PGPORT is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${LABEL_STUDIO_DB_NAME:?LABEL_STUDIO_DB_NAME is required}"
: "${LABEL_STUDIO_DB_USER:?LABEL_STUDIO_DB_USER is required}"
: "${LABEL_STUDIO_DB_PASSWORD:?LABEL_STUDIO_DB_PASSWORD is required}"

export PGPASSWORD

echo "label-studio-init: waiting for Postgres at ${PGHOST}:${PGPORT}..."
i=0
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 30 ]; then
        echo "label-studio-init: ERROR - Postgres not ready after 30 attempts" >&2
        exit 1
    fi
    sleep 2
done

psql -X -w -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" \
  -d "$LABEL_STUDIO_DB_NAME" -v ON_ERROR_STOP=1 -Atqc 'SELECT 1' >/dev/null

echo "label-studio-init: provisioning complete"
