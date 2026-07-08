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

# psql :'var' interpolation quotes values server-side (safe for passwords
# containing shell/SQL-special characters), but it only works in SCRIPT
# input (stdin / -f), NOT inside -c / -tAc strings — inside -c the literal
# :'var' is shipped to the server and raises "syntax error at or near ":"".
# Same convention init-airflow.sh / init-iceberg-rest.sh use: pipe each
# statement through stdin so :'var' resolves.
role_exists=$(printf "SELECT 1 FROM pg_roles WHERE rolname = :'role';\n" \
    | psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
           -v ON_ERROR_STOP=1 -v role="$MLFLOW_DB_USER" -tA)

if [ "$role_exists" = "1" ]; then
    echo "mlflow-init: updating role '${MLFLOW_DB_USER}' password..."
    printf "ALTER ROLE :\"role\" WITH LOGIN PASSWORD :'password';\n" \
      | psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
             -v ON_ERROR_STOP=1 -v role="$MLFLOW_DB_USER" -v password="$MLFLOW_DB_PASSWORD"
else
    echo "mlflow-init: creating role '${MLFLOW_DB_USER}'..."
    printf "CREATE ROLE :\"role\" WITH LOGIN PASSWORD :'password';\n" \
      | psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
             -v ON_ERROR_STOP=1 -v role="$MLFLOW_DB_USER" -v password="$MLFLOW_DB_PASSWORD"
fi

db_exists=$(printf "SELECT 1 FROM pg_database WHERE datname = :'db';\n" \
    | psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
           -v ON_ERROR_STOP=1 -v db="$MLFLOW_DB_NAME" -tA)

if [ "$db_exists" = "1" ]; then
    echo "mlflow-init: database '${MLFLOW_DB_NAME}' already exists"
else
    echo "mlflow-init: creating database '${MLFLOW_DB_NAME}'..."
    createdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" \
        -O "$MLFLOW_DB_USER" "$MLFLOW_DB_NAME"
fi

echo "mlflow-init: provisioning complete"
