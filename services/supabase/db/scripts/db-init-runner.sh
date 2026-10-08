#!/bin/sh
set -e # Exit immediately if a command exits with a non-zero status.

# Check required env vars are passed from compose file
if [ -z "$PGHOST" ] || [ -z "$PGUSER" ] || [ -z "$PGPASSWORD" ] || [ -z "$PGDATABASE" ]; then
  echo "db-init-runner: Error: One or more database connection environment variables are not set."
  exit 1
fi

LANGMEM_EMBEDDING_DIM="${LANGMEM_EMBEDDING_DIM:-768}"
ATLAS_MEMORY_EMBEDDING_MODEL="${LANGMEM_EMBEDDING_MODEL:-${LITELLM_EMBEDDING_MODEL:-ollama/nomic-embed-text}}"
case "$LANGMEM_EMBEDDING_DIM" in
  ''|*[!0-9]*)
    echo "db-init-runner: ERROR - LANGMEM_EMBEDDING_DIM must be an integer from 1 through 4000." >&2
    exit 1
    ;;
esac
if [ "$LANGMEM_EMBEDDING_DIM" -lt 1 ] || [ "$LANGMEM_EMBEDDING_DIM" -gt 4000 ]; then
  echo "db-init-runner: ERROR - LANGMEM_EMBEDDING_DIM must be an integer from 1 through 4000." >&2
  exit 1
fi

echo "db-init-runner: Waiting for database service $PGHOST..."
# Use pg_isready to wait for the database server to accept connections.
# Bounded (300s) like every other init wait loop — depends_on's
# service_healthy gate normally makes this instant, but a wedged DB
# should fail the init container, not hang it forever.
WAITED=0
until pg_isready -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -q; do
  WAITED=$((WAITED + 1))
  if [ "$WAITED" -ge 300 ]; then
    echo "db-init-runner: ERROR - database not ready after 300s; giving up." >&2
    exit 1
  fi
  echo "db-init-runner: Database unavailable - sleeping 1s"
  sleep 1
done

ATLAS_SQL_DIR="${ATLAS_DB_INIT_SCRIPT_DIR:-/scripts}"
USER_SQL_DIR="${ATLAS_DB_INIT_USER_SCRIPT_DIR:-/user-scripts}"

run_sql_directory() {
  sql_dir="$1"
  phase_name="$2"
  required="$3"
  list_file="$4"

  if [ ! -d "$sql_dir" ]; then
    if [ "$required" = "true" ]; then
      echo "db-init-runner: ERROR - required SQL directory not found: $sql_dir" >&2
      exit 1
    fi
    echo "db-init-runner: No user SQL directory found at $sql_dir; skipping user migrations."
    return 0
  fi

  # Loop through SQL files in mounted directory in alphabetical/numerical order.
  # Use find to handle potential spaces or special characters in filenames and
  # sort to ensure numerical order (01, 02, ..., 10, etc.).
  # Redirect the file list into the loop (instead of `find | sort | while`) so
  # the loop body runs in the CURRENT shell, where `set -e` unambiguously aborts
  # on a failing psql. Under dash, a `while` on the RHS of a pipe runs in a
  # subshell where set-e propagation is implementation-dependent — a failing
  # migration could be missed. The temp-file pattern is what the other init
  # scripts use.
  find "$sql_dir" -maxdepth 1 -type f -name '*.sql' -print | sort > "$list_file"
  while IFS= read -r f; do
    if [ -f "$f" ]; then
      echo "db-init-runner: Running $phase_name SQL script: $f"
      # Execute script using psql, stop on error.
      psql -v ON_ERROR_STOP=1 \
        -v "atlas_memory_embedding_dim=$LANGMEM_EMBEDDING_DIM" \
        -v "atlas_memory_embedding_model=$ATLAS_MEMORY_EMBEDDING_MODEL" \
        --host "$PGHOST" --username "$PGUSER" --dbname "$PGDATABASE" -a -f "$f"
    fi
  done < "$list_file"
  rm -f "$list_file"
}

# Superuser-run SQL below calls built-ins (format, convert_to, =, <>) whose
# argument types are not always exact, and co-tenant roles (Open WebUI,
# LightRAG, pg-meta, Realtime) can CREATE in public, which is on the
# superuser's search_path. A planted public.format(text, name) or
# public.=(varchar, varchar) then beat pg_catalog's and ran as superuser (one
# captured a plaintext role password). Refuse to run while any non-superuser
# owns a function or operator named like a superuser-owned one in pg_catalog
# or a schema on that path (extension routines such as vector_dims live in
# public, #1456); dropping them is the operator's call.
shadowing="$(PGOPTIONS="-c search_path=pg_catalog,pg_temp" psql -X -At -v ON_ERROR_STOP=1 \
  --host "$PGHOST" --username "$PGUSER" --dbname "$PGDATABASE" <<'SQL'
SELECT n.nspname || '.' || p.proname || '(' || pg_catalog.pg_get_function_identity_arguments(p.oid) || ') owned by ' || r.rolname
  FROM pg_catalog.pg_proc AS p
  JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
  JOIN pg_catalog.pg_roles AS r ON r.oid = p.proowner
 WHERE n.nspname IN ('public', 'auth', 'extensions') AND NOT r.rolsuper
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_proc AS b
                 JOIN pg_catalog.pg_namespace AS bn ON bn.oid = b.pronamespace
                 JOIN pg_catalog.pg_roles AS br ON br.oid = b.proowner
                WHERE bn.nspname IN ('pg_catalog', 'public', 'auth', 'extensions')
                  AND br.rolsuper AND b.proname = p.proname)
UNION ALL
SELECT n.nspname || '.' || o.oprname || ' operator owned by ' || r.rolname
  FROM pg_catalog.pg_operator AS o
  JOIN pg_catalog.pg_namespace AS n ON n.oid = o.oprnamespace
  JOIN pg_catalog.pg_roles AS r ON r.oid = o.oprowner
 WHERE n.nspname IN ('public', 'auth', 'extensions') AND NOT r.rolsuper
   AND EXISTS (SELECT 1 FROM pg_catalog.pg_operator AS b
                 JOIN pg_catalog.pg_namespace AS bn ON bn.oid = b.oprnamespace
                 JOIN pg_catalog.pg_roles AS br ON br.oid = b.oprowner
                WHERE bn.nspname IN ('pg_catalog', 'public', 'auth', 'extensions')
                  AND br.rolsuper AND b.oprname = o.oprname);
SQL
)"
if [ -n "$shadowing" ]; then
  echo "db-init-runner: ERROR - non-superuser objects shadow PostgreSQL built-ins that init runs as superuser:" >&2
  printf '  %s\n' "$shadowing" >&2
  echo "db-init-runner: drop them (they would execute with superuser rights), then restart." >&2
  exit 1
fi

echo "db-init-runner: Database is ready. Running Atlas post-initialization scripts from $ATLAS_SQL_DIR..."
run_sql_directory "$ATLAS_SQL_DIR" "Atlas" "true" "/tmp/_db_init_atlas_sql_files"

ROLE_SCRIPT="$ATLAS_SQL_DIR/05-scoped-roles.sh"
if [ ! -f "$ROLE_SCRIPT" ]; then
  echo "db-init-runner: ERROR - required scoped-role provisioner not found: $ROLE_SCRIPT" >&2
  exit 1
fi
echo "db-init-runner: Applying scoped PostgreSQL roles and grants..."
/bin/sh "$ROLE_SCRIPT"

echo "db-init-runner: Running optional user post-initialization scripts from $USER_SQL_DIR..."
run_sql_directory "$USER_SQL_DIR" "user" "false" "/tmp/_db_init_user_sql_files"

# A public table a user script creates inherits the default anon /
# authenticated grants; 06 publishes only tables with RLS and revokes the
# rest, but it ran before the user scripts. Re-apply it so a table without
# RLS is not readable through PostgREST until the next boot.
if [ -d "$USER_SQL_DIR" ] && find "$USER_SQL_DIR" -maxdepth 1 -type f -name '*.sql' | grep -q .; then
  echo "db-init-runner: Re-applying RLS-gated client grants after user scripts..."
  psql -v ON_ERROR_STOP=1 --host "$PGHOST" --username "$PGUSER" --dbname "$PGDATABASE" \
    -a -f "$ATLAS_SQL_DIR/06-permissions.sql"
fi

echo "db-init-runner: All post-initialization scripts finished successfully."
