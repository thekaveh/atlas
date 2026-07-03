#!/bin/sh
# Idempotent MLflow substrate provisioning: dedicated Postgres role + database.
set -eu

echo "mlflow-init: starting provisioning..."

: "${PGHOST:?PGHOST is required}"
: "${PGPORT:?PGPORT is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${MLFLOW_DB_NAME:?MLFLOW_DB_NAME is required}"
: "${MLFLOW_DB_USER:?MLFLOW_DB_USER is required}"
: "${MLFLOW_DB_PASSWORD:?MLFLOW_DB_PASSWORD is required}"

export PGPASSWORD

echo "mlflow-init: waiting for Postgres at ${PGHOST}:${PGPORT}..."
i=0
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 30 ]; then
        echo "mlflow-init: ERROR - Postgres not ready after 30 attempts" >&2
        exit 1
    fi
    sleep 2
done

role_exists=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
    -v ON_ERROR_STOP=1 -v role="$MLFLOW_DB_USER" \
    -tAc "SELECT 1 FROM pg_roles WHERE rolname = :'role'")

if [ "$role_exists" = "1" ]; then
    echo "mlflow-init: updating role '${MLFLOW_DB_USER}' password..."
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
        -v ON_ERROR_STOP=1 -v role="$MLFLOW_DB_USER" -v password="$MLFLOW_DB_PASSWORD" \
        -c "ALTER ROLE :\"role\" WITH LOGIN PASSWORD :'password';"
else
    echo "mlflow-init: creating role '${MLFLOW_DB_USER}'..."
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
        -v ON_ERROR_STOP=1 -v role="$MLFLOW_DB_USER" -v password="$MLFLOW_DB_PASSWORD" \
        -c "CREATE ROLE :\"role\" WITH LOGIN PASSWORD :'password';"
fi

db_exists=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
    -v ON_ERROR_STOP=1 -v db="$MLFLOW_DB_NAME" \
    -tAc "SELECT 1 FROM pg_database WHERE datname = :'db'")

if [ "$db_exists" = "1" ]; then
    echo "mlflow-init: database '${MLFLOW_DB_NAME}' already exists"
else
    echo "mlflow-init: creating database '${MLFLOW_DB_NAME}'..."
    createdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" \
        -O "$MLFLOW_DB_USER" "$MLFLOW_DB_NAME"
fi

echo "mlflow-init: provisioning complete"
