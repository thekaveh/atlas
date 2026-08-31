#!/bin/sh
# Verify the centrally provisioned MLflow database identity.
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

psql -X -w -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" \
  -d "$MLFLOW_DB_NAME" -v ON_ERROR_STOP=1 -Atqc 'SELECT 1' >/dev/null

echo "mlflow-init: provisioning complete"
