#!/bin/sh
# Idempotent Label Studio substrate provisioning: dedicated Postgres role + database.
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

role_exists=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
    -v ON_ERROR_STOP=1 -v role="$LABEL_STUDIO_DB_USER" \
    -tAc "SELECT 1 FROM pg_roles WHERE rolname = :'role'")

if [ "$role_exists" = "1" ]; then
    echo "label-studio-init: updating role '${LABEL_STUDIO_DB_USER}' password..."
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
        -v ON_ERROR_STOP=1 -v role="$LABEL_STUDIO_DB_USER" -v password="$LABEL_STUDIO_DB_PASSWORD" \
        -c "ALTER ROLE :\"role\" WITH LOGIN PASSWORD :'password';"
else
    echo "label-studio-init: creating role '${LABEL_STUDIO_DB_USER}'..."
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
        -v ON_ERROR_STOP=1 -v role="$LABEL_STUDIO_DB_USER" -v password="$LABEL_STUDIO_DB_PASSWORD" \
        -c "CREATE ROLE :\"role\" WITH LOGIN PASSWORD :'password';"
fi

db_exists=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
    -v ON_ERROR_STOP=1 -v db="$LABEL_STUDIO_DB_NAME" \
    -tAc "SELECT 1 FROM pg_database WHERE datname = :'db'")

if [ "$db_exists" = "1" ]; then
    echo "label-studio-init: database '${LABEL_STUDIO_DB_NAME}' already exists"
else
    echo "label-studio-init: creating database '${LABEL_STUDIO_DB_NAME}'..."
    createdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" \
        -O "$LABEL_STUDIO_DB_USER" "$LABEL_STUDIO_DB_NAME"
fi

echo "label-studio-init: provisioning complete"
