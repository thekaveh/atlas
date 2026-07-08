#!/bin/sh
# Idempotent Langfuse substrate provisioning: dedicated Postgres database.
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

# psql :'var' interpolation only works in SCRIPT input (stdin / -f), NOT
# inside -c / -tAc strings — inside -c the literal :'db' is shipped to the
# server and raises "syntax error at or near ":"", so the existence check
# always evaluated false and createdb ran unconditionally (failing on any
# restart where the DB persists). Same stdin convention init-airflow.sh /
# init-iceberg-rest.sh use.
if printf "SELECT 1 FROM pg_database WHERE datname = :'db';\n" \
    | psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
           -v ON_ERROR_STOP=1 -v db="$LANGFUSE_DB_NAME" -tA | grep -q 1; then
    echo "langfuse-init: database '${LANGFUSE_DB_NAME}' already exists"
else
    echo "langfuse-init: creating database '${LANGFUSE_DB_NAME}'..."
    createdb -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$LANGFUSE_DB_NAME"
fi

echo "langfuse-init: provisioning complete"
