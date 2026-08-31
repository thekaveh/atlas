#!/bin/sh
set -eu

# Expand phase for Atlas database isolation.  The script is intentionally
# idempotent and runs after the schema seed slices on every start, so it covers
# both fresh clusters and upgrades whose objects were created by supabase_admin.

: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${PGDATABASE:?PGDATABASE is required}"

required_vars='SUPABASE_AUTH_DB_USER SUPABASE_AUTH_DB_PASSWORD
SUPABASE_STORAGE_DB_USER SUPABASE_STORAGE_DB_PASSWORD
SUPABASE_API_DB_USER SUPABASE_API_DB_PASSWORD
SUPABASE_REALTIME_DB_USER SUPABASE_REALTIME_DB_PASSWORD
SUPABASE_META_DB_USER SUPABASE_META_DB_PASSWORD
SUPABASE_STUDIO_DB_USER
POSTGRES_EXPORTER_DB_USER POSTGRES_EXPORTER_DB_PASSWORD
SUPAVISOR_DB_ADMIN_USER SUPAVISOR_DB_ADMIN_PASSWORD
BACKEND_DB_USER BACKEND_DB_PASSWORD
N8N_DB_USER N8N_DB_PASSWORD
OPEN_WEBUI_DB_USER OPEN_WEBUI_DB_PASSWORD
LIGHTRAG_DB_USER LIGHTRAG_DB_PASSWORD
LITELLM_DB_USER LITELLM_DB_PASSWORD LITELLM_DB_NAME
AIRFLOW_DB_USER AIRFLOW_DB_PASSWORD
AIRFLOW_ATLAS_DB_USER AIRFLOW_ATLAS_DB_PASSWORD
LANGFUSE_DB_USER LANGFUSE_DB_PASSWORD LANGFUSE_DB_NAME
MLFLOW_DB_USER MLFLOW_DB_PASSWORD MLFLOW_DB_NAME
LABEL_STUDIO_DB_USER LABEL_STUDIO_DB_PASSWORD LABEL_STUDIO_DB_NAME
ICEBERG_DB_USER ICEBERG_DB_PASSWORD
MCP_POSTGRES_DB_USER MCP_POSTGRES_DB_PASSWORD
JUPYTER_DB_USER JUPYTER_DB_PASSWORD
ZEPPELIN_DB_USER ZEPPELIN_DB_PASSWORD'

for var_name in $required_vars; do
  eval "var_value=\${$var_name-}"
  if [ -z "$var_value" ]; then
    echo "scoped-roles: ERROR - $var_name is required" >&2
    exit 1
  fi
done

configuration_error() {
  echo "scoped-roles: ERROR - invalid database role configuration: $*" >&2
  exit 1
}

validate_literal_database_name() {
  database_var=$1
  database_name=$2
  # libpq reinterprets dbname values containing '=' or a PostgreSQL URI as a
  # complete connection string. Reject those spellings so a configurable
  # database name cannot override the separately trusted host/user options.
  case "$database_name" in
    *=*|postgres://*|postgresql://*)
      configuration_error \
        "$database_var must be a literal database name, not libpq connection parameters"
      ;;
  esac
}

validate_literal_database_name PGDATABASE "$PGDATABASE"

# Validate the complete resolved identity set before defining or invoking the
# first psql mutation. PostgreSQL permits quoted identifiers with punctuation,
# so this deliberately compares values without imposing a narrower spelling
# policy. It only rejects authority and ownership collisions.
role_vars='SUPABASE_AUTH_DB_USER SUPABASE_STORAGE_DB_USER SUPABASE_API_DB_USER
SUPABASE_REALTIME_DB_USER SUPABASE_META_DB_USER SUPABASE_STUDIO_DB_USER
POSTGRES_EXPORTER_DB_USER
SUPAVISOR_DB_ADMIN_USER BACKEND_DB_USER N8N_DB_USER OPEN_WEBUI_DB_USER
LIGHTRAG_DB_USER LITELLM_DB_USER AIRFLOW_DB_USER AIRFLOW_ATLAS_DB_USER
LANGFUSE_DB_USER MLFLOW_DB_USER LABEL_STUDIO_DB_USER ICEBERG_DB_USER
MCP_POSTGRES_DB_USER JUPYTER_DB_USER ZEPPELIN_DB_USER'
checked_role_vars=''
for role_var in $role_vars; do
  eval "role_value=\${$role_var}"
  upstream_identity=false
  # shellcheck disable=SC2154 # role_value is assigned by the eval above.
  case "$role_var:$role_value" in
    SUPABASE_AUTH_DB_USER:supabase_auth_admin|\
    SUPABASE_STORAGE_DB_USER:supabase_storage_admin|\
    SUPABASE_API_DB_USER:authenticator)
      upstream_identity=true
      ;;
  esac
  if [ "$role_value" = "$PGUSER" ]; then
    configuration_error "$role_var collides with configured admin role '$role_value'"
  fi
  case "$role_value" in
    postgres|supabase_admin|service_role|dashboard_user|anon|authenticated|\
    authenticator|pgbouncer|pg_*|supabase_*)
      if [ "$upstream_identity" != true ]; then
        configuration_error "$role_var collides with reserved/admin role '$role_value'"
      fi
      ;;
  esac
  for checked_var in $checked_role_vars; do
    eval "checked_value=\${$checked_var}"
    # shellcheck disable=SC2154 # checked_value is assigned by the eval above.
    if [ "$role_value" = "$checked_value" ]; then
      configuration_error "$role_var and $checked_var both resolve to role '$role_value'"
    fi
  done
  checked_role_vars="$checked_role_vars $role_var"
done

# These constants are consumed indirectly through database_vars/eval.
# shellcheck disable=SC2034
ATLAS_AIRFLOW_DB_NAME=airflow
# shellcheck disable=SC2034
ATLAS_ICEBERG_DB_NAME=iceberg
# shellcheck disable=SC2034
ATLAS_SUPAVISOR_DB_NAME=supavisor
database_vars='LITELLM_DB_NAME ATLAS_AIRFLOW_DB_NAME LANGFUSE_DB_NAME
MLFLOW_DB_NAME LABEL_STUDIO_DB_NAME ATLAS_ICEBERG_DB_NAME ATLAS_SUPAVISOR_DB_NAME'
checked_database_vars=''
for database_var in $database_vars; do
  eval "database_value=\${$database_var}"
  # shellcheck disable=SC2154 # database_value is assigned by the eval above.
  validate_literal_database_name "$database_var" "$database_value"
  case "$database_value" in
    postgres|template0|template1)
      configuration_error "$database_var collides with primary/reserved database '$database_value'"
      ;;
  esac
  if [ "$database_value" = "$PGDATABASE" ]; then
    configuration_error "$database_var collides with primary database '$database_value'"
  fi
  for checked_var in $checked_database_vars; do
    eval "checked_value=\${$checked_var}"
    if [ "$database_value" = "$checked_value" ]; then
      configuration_error "$database_var and $checked_var both resolve to database '$database_value'"
    fi
  done
  checked_database_vars="$checked_database_vars $database_var"
done

if ! command -v sha256sum >/dev/null 2>&1; then
  configuration_error "sha256sum is required to compare managed password inputs"
fi

psql_admin() {
  target_database=${1:-$PGDATABASE}
  shift
  psql -X -v ON_ERROR_STOP=1 --host "$PGHOST" --username "$PGUSER" \
    --dbname "$target_database" "$@"
}

ensure_login() {
  role_name=$1
  role_password=$2
  # The admin password acts as a private pepper and the role name as domain
  # separation.  rolconfig is visible to other database users, so never store
  # a directly brute-forceable hash of the scoped password there.
  password_fingerprint=$(
    printf '%s\n%s\n%s' "$PGPASSWORD" "$role_name" "$role_password" | sha256sum
  )
  password_fingerprint=${password_fingerprint%% *}
  printf '%s\n' \
    "SELECT format('CREATE ROLE %I LOGIN', :'role')" \
    "WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \\gexec" \
    "SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'role', :'password')" \
    "WHERE NOT EXISTS (" \
    "  SELECT 1 FROM pg_roles WHERE rolname = :'role'" \
    "  AND format('atlas.password_fingerprint=%s', :'fingerprint') =" \
    "      ANY (COALESCE(rolconfig, ARRAY[]::text[]))" \
    ") \\gexec" \
    "ALTER ROLE :\"role\" SET atlas.password_fingerprint TO :'fingerprint';" \
    | psql_admin "$PGDATABASE" -v role="$role_name" -v password="$role_password" \
        -v fingerprint="$password_fingerprint"
}

ensure_restricted_login() {
  ensure_login "$1" "$2"
  printf '%s\n' \
    'ALTER ROLE :"role" NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT;' \
    | psql_admin "$PGDATABASE" -v role="$1"
}

ensure_database() {
  database_name=$1
  owner_role=$2
  printf '%s\n' \
    "SELECT format('CREATE DATABASE %I OWNER %I', :'database', :'owner')" \
    "WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'database') \\gexec" \
    "SELECT format('ALTER DATABASE %I OWNER TO %I', :'database', :'owner') \\gexec" \
    "SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'database') \\gexec" \
    "SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database', :'owner') \\gexec" \
    | psql_admin "$PGDATABASE" -v database="$database_name" -v owner="$owner_role"

  # A dedicated service database contains only that service's migration
  # objects.  Transfer legacy supabase_admin ownership object-by-object so an
  # upgraded service can keep running ALTER migrations without CREATEDB or
  # cross-database privileges.
  psql_admin "$database_name" -v owner="$owner_role" <<'SQL'
ALTER SCHEMA public OWNER TO :"owner";
GRANT ALL ON SCHEMA public TO :"owner";
SELECT set_config('atlas.owner', :'owner', false);
DO $body$
DECLARE
  target name := current_setting('atlas.owner');
  item record;
  kind text;
BEGIN
  FOR item IN
    SELECT n.nspname, c.relname, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname NOT LIKE 'pg_%'
      AND n.nspname <> 'information_schema'
      AND pg_get_userbyid(c.relowner) <> target
      AND c.relkind IN ('r','p','S','v','m','f')
  LOOP
    kind := CASE item.relkind
      WHEN 'S' THEN 'SEQUENCE'
      WHEN 'v' THEN 'VIEW'
      WHEN 'm' THEN 'MATERIALIZED VIEW'
      WHEN 'f' THEN 'FOREIGN TABLE'
      ELSE 'TABLE'
    END;
    EXECUTE format('ALTER %s %I.%I OWNER TO %I', kind, item.nspname, item.relname, target);
  END LOOP;
  FOR item IN
    SELECT p.oid::regprocedure AS identity
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname NOT LIKE 'pg_%'
      AND n.nspname <> 'information_schema'
      AND pg_get_userbyid(p.proowner) <> target
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO %I', item.identity, target);
  END LOOP;
END
$body$;
SQL
}

# Preserve the upstream Supabase role names and their existing ownership and
# attributes.  Only login passwords are managed here.
ensure_login "$SUPABASE_AUTH_DB_USER" "$SUPABASE_AUTH_DB_PASSWORD"
ensure_login "$SUPABASE_STORAGE_DB_USER" "$SUPABASE_STORAGE_DB_PASSWORD"
ensure_login "$SUPABASE_API_DB_USER" "$SUPABASE_API_DB_PASSWORD"

ensure_restricted_login "$SUPABASE_REALTIME_DB_USER" "$SUPABASE_REALTIME_DB_PASSWORD"
printf '%s\n' 'ALTER ROLE :"role" REPLICATION;' \
  | psql_admin "$PGDATABASE" -v role="$SUPABASE_REALTIME_DB_USER"
ensure_restricted_login "$SUPABASE_META_DB_USER" "$SUPABASE_META_DB_PASSWORD"
printf '%s\n' 'ALTER ROLE :"role" CREATEDB CREATEROLE;' \
  | psql_admin "$PGDATABASE" -v role="$SUPABASE_META_DB_USER"
ensure_restricted_login "$SUPABASE_STUDIO_DB_USER" "$SUPABASE_META_DB_PASSWORD"
ensure_restricted_login "$POSTGRES_EXPORTER_DB_USER" "$POSTGRES_EXPORTER_DB_PASSWORD"
ensure_restricted_login "$SUPAVISOR_DB_ADMIN_USER" "$SUPAVISOR_DB_ADMIN_PASSWORD"
ensure_restricted_login "$BACKEND_DB_USER" "$BACKEND_DB_PASSWORD"
ensure_restricted_login "$N8N_DB_USER" "$N8N_DB_PASSWORD"
ensure_restricted_login "$OPEN_WEBUI_DB_USER" "$OPEN_WEBUI_DB_PASSWORD"
ensure_restricted_login "$LIGHTRAG_DB_USER" "$LIGHTRAG_DB_PASSWORD"
ensure_restricted_login "$AIRFLOW_ATLAS_DB_USER" "$AIRFLOW_ATLAS_DB_PASSWORD"
ensure_restricted_login "$MCP_POSTGRES_DB_USER" "$MCP_POSTGRES_DB_PASSWORD"
ensure_restricted_login "$JUPYTER_DB_USER" "$JUPYTER_DB_PASSWORD"
ensure_restricted_login "$ZEPPELIN_DB_USER" "$ZEPPELIN_DB_PASSWORD"

ensure_restricted_login "$LITELLM_DB_USER" "$LITELLM_DB_PASSWORD"
ensure_restricted_login "$AIRFLOW_DB_USER" "$AIRFLOW_DB_PASSWORD"
ensure_restricted_login "$LANGFUSE_DB_USER" "$LANGFUSE_DB_PASSWORD"
ensure_restricted_login "$MLFLOW_DB_USER" "$MLFLOW_DB_PASSWORD"
ensure_restricted_login "$LABEL_STUDIO_DB_USER" "$LABEL_STUDIO_DB_PASSWORD"
ensure_restricted_login "$ICEBERG_DB_USER" "$ICEBERG_DB_PASSWORD"

ensure_database "$LITELLM_DB_NAME" "$LITELLM_DB_USER"
ensure_database airflow "$AIRFLOW_DB_USER"
ensure_database "$LANGFUSE_DB_NAME" "$LANGFUSE_DB_USER"
ensure_database "$MLFLOW_DB_NAME" "$MLFLOW_DB_USER"
ensure_database "$LABEL_STUDIO_DB_NAME" "$LABEL_STUDIO_DB_USER"
ensure_database iceberg "$ICEBERG_DB_USER"
ensure_database supavisor "$SUPAVISOR_DB_ADMIN_USER"

# Shared-database service schemas and explicitly inventoried public tables.
psql_admin "$PGDATABASE" \
  -v primary_database="$PGDATABASE" \
  -v admin_role="$PGUSER" \
  -v auth_role="$SUPABASE_AUTH_DB_USER" \
  -v storage_role="$SUPABASE_STORAGE_DB_USER" \
  -v api_role="$SUPABASE_API_DB_USER" \
  -v realtime_role="$SUPABASE_REALTIME_DB_USER" \
  -v meta_role="$SUPABASE_META_DB_USER" \
  -v studio_role="$SUPABASE_STUDIO_DB_USER" \
  -v metrics_role="$POSTGRES_EXPORTER_DB_USER" \
  -v supavisor_role="$SUPAVISOR_DB_ADMIN_USER" \
  -v backend_role="$BACKEND_DB_USER" \
  -v n8n_role="$N8N_DB_USER" \
  -v openwebui_role="$OPEN_WEBUI_DB_USER" \
  -v lightrag_role="$LIGHTRAG_DB_USER" \
  -v airflow_reader="$AIRFLOW_ATLAS_DB_USER" \
  -v mcp_reader="$MCP_POSTGRES_DB_USER" \
  -v jupyter_reader="$JUPYTER_DB_USER" \
  -v zeppelin_reader="$ZEPPELIN_DB_USER" <<'SQL'
GRANT CONNECT ON DATABASE :"primary_database" TO :"auth_role", :"storage_role", :"api_role",
  :"realtime_role", :"meta_role", :"studio_role", :"metrics_role", :"backend_role", :"n8n_role",
  :"openwebui_role", :"lightrag_role", :"airflow_reader", :"mcp_reader",
  :"jupyter_reader", :"zeppelin_reader";

GRANT dashboard_user TO :"meta_role";
GRANT USAGE, CREATE ON SCHEMA public TO :"meta_role";
REVOKE dashboard_user FROM :"studio_role";
GRANT pg_monitor TO :"metrics_role";
REVOKE pg_read_all_data FROM :"airflow_reader", :"mcp_reader", :"jupyter_reader", :"zeppelin_reader";
GRANT USAGE ON SCHEMA pgbouncer TO :"supavisor_role";
GRANT EXECUTE ON FUNCTION pgbouncer.get_auth(text) TO :"supavisor_role";

GRANT USAGE ON SCHEMA auth TO :"auth_role", :"openwebui_role";
GRANT ALL ON ALL TABLES IN SCHEMA auth TO :"auth_role";
GRANT ALL ON ALL SEQUENCES IN SCHEMA auth TO :"auth_role";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA auth TO :"auth_role";
GRANT SELECT ON auth.users TO :"openwebui_role";

SELECT set_config('atlas.storage_role', :'storage_role', false);
SELECT set_config('atlas.realtime_role', :'realtime_role', false);
DO $body$
DECLARE
  schema_name text;
  target name;
  item record;
  kind text;
BEGIN
  FOREACH schema_name IN ARRAY ARRAY['storage', 'realtime']
  LOOP
    target := CASE schema_name
      WHEN 'storage' THEN current_setting('atlas.storage_role')
      ELSE current_setting('atlas.realtime_role')
    END;
    EXECUTE format('ALTER SCHEMA %I OWNER TO %I', schema_name, target);
    FOR item IN
      SELECT c.relname, c.relkind
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname=schema_name
        AND c.relkind IN ('r','p','S','v','m','f')
        AND pg_get_userbyid(c.relowner) <> target
    LOOP
      kind := CASE item.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END;
      EXECUTE format('ALTER %s %I.%I OWNER TO %I', kind, schema_name, item.relname, target);
    END LOOP;
    FOR item IN
      SELECT p.oid::regprocedure AS identity
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname=schema_name AND p.prokind <> 'a'
        AND pg_get_userbyid(p.proowner) <> target
    LOOP
      EXECUTE format('ALTER ROUTINE %s OWNER TO %I', item.identity, target);
    END LOOP;
  END LOOP;

  -- Realtime's pinned application-repository migrations manage these tables
  -- in public. Keep the shared GoTrue/Ecto schema_migrations tracker owned by
  -- the platform admin, but transfer every Realtime-only management relation.
  target := current_setting('atlas.realtime_role');
  FOR item IN
    SELECT c.relname, c.relkind
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public'
      AND (c.relname = ANY (ARRAY['tenants','extensions','feature_flags'])
           OR c.relname LIKE 'tenants_%'
           OR c.relname LIKE 'extensions_%'
           OR c.relname LIKE 'feature_flags_%')
      AND c.relkind IN ('r','p','S','v','m','f')
      AND pg_get_userbyid(c.relowner) <> target
  LOOP
    kind := CASE item.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
      WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END;
    EXECUTE format('ALTER %s public.%I OWNER TO %I', kind, item.relname, target);
  END LOOP;
END
$body$;

GRANT ALL ON SCHEMA storage TO :"storage_role";
GRANT ALL ON ALL TABLES IN SCHEMA storage TO :"storage_role";
GRANT ALL ON ALL SEQUENCES IN SCHEMA storage TO :"storage_role";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA storage TO :"storage_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"storage_role" IN SCHEMA storage
  GRANT ALL ON TABLES TO :"storage_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"storage_role" IN SCHEMA storage
  GRANT ALL ON SEQUENCES TO :"storage_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"storage_role" IN SCHEMA storage
  GRANT EXECUTE ON FUNCTIONS TO :"storage_role";

-- Contract the authority of the one prior Backend identity recorded by Atlas
-- before applying any current-role grants. A retired Backend identity may be
-- deliberately reassigned to a current read-only role in the same run.
-- The database-owned setting is the durable source after this version; the
-- exact platform policy is a one-time upgrade fallback for existing installs.
-- Refuse an ambiguous fallback instead of revoking an arbitrary principal.
SELECT set_config('atlas.backend_role', :'backend_role', false);
DO $body$
DECLARE
  current_backend name := current_setting('atlas.backend_role');
  prior_backend name;
  fallback_count integer := 0;
  relation_name text;
BEGIN
  SELECT r.rolname
    INTO prior_backend
    FROM pg_db_role_setting settings
    CROSS JOIN LATERAL unnest(settings.setconfig) AS item(config)
    JOIN pg_roles r
      ON r.rolname = substr(
        item.config, length('atlas.managed_backend_role=') + 1
      )
   WHERE settings.setdatabase = (
           SELECT oid FROM pg_database WHERE datname = current_database()
         )
     AND settings.setrole = 0
     AND item.config LIKE 'atlas.managed_backend_role=%';

  IF prior_backend IS NULL THEN
    SELECT count(*), min(role_entry.rolname)
      INTO fallback_count, prior_backend
      FROM pg_policy policy
      JOIN pg_class relation ON relation.oid = policy.polrelid
      JOIN pg_namespace relation_ns ON relation_ns.oid = relation.relnamespace
      CROSS JOIN LATERAL unnest(policy.polroles) AS policy_role(role_oid)
      JOIN pg_roles role_entry ON role_entry.oid = policy_role.role_oid
     WHERE relation_ns.nspname = 'public'
       AND relation.relname = 'memory_facts'
       AND policy.polname = 'Atlas backend direct role access';
    IF fallback_count > 1 THEN
      RAISE EXCEPTION
        'managed Backend policy has % principals; refusing ambiguous rotation',
        fallback_count;
    END IF;
  END IF;

  IF prior_backend IS NULL OR prior_backend = current_backend THEN
    RETURN;
  END IF;

  FOREACH relation_name IN ARRAY ARRAY[
    'research_sessions', 'research_results', 'research_sources', 'research_logs',
    'memory_facts', 'memory_sessions', 'memory_consolidation_log',
    'memory_embedding_schema_state', 'media_spend_ledger'
  ]
  LOOP
    EXECUTE format(
      'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I',
      relation_name, prior_backend
    );
  END LOOP;

  EXECUTE format(
    'REVOKE ALL PRIVILEGES ON FUNCTION '
    'public.contract_memory_embedding_dimension(integer) FROM %I',
    prior_backend
  );
  EXECUTE format(
    'REVOKE ALL PRIVILEGES ON FUNCTION '
    'public.contract_memory_embedding_contract(text,integer,bigint) FROM %I',
    prior_backend
  );
  EXECUTE format(
    'REVOKE ALL PRIVILEGES ON FUNCTION '
    'public.set_memory_weaviate_rebuild_required(boolean) FROM %I',
    prior_backend
  );
  EXECUTE format(
    'REVOKE ALL PRIVILEGES ON FUNCTION '
    'public.mark_memory_weaviate_dirty() FROM %I',
    prior_backend
  );
  EXECUTE format(
    'REVOKE ALL PRIVILEGES ON FUNCTION '
    'public.ensure_memory_weaviate_identity(text,integer) FROM %I',
    prior_backend
  );
  EXECUTE format(
    'REVOKE ALL PRIVILEGES ON FUNCTION '
    'public.complete_memory_weaviate_rebuild(bigint) FROM %I',
    prior_backend
  );
  EXECUTE format(
    'REVOKE ALL PRIVILEGES ON FUNCTION '
    'public.complete_memory_weaviate_rebuild(bigint,text,integer) FROM %I',
    prior_backend
  );
END
$body$;

SELECT format('GRANT authenticator TO %I', :'api_role')
WHERE :'api_role' <> 'authenticator' \gexec
GRANT ALL ON SCHEMA realtime TO :"realtime_role";
GRANT USAGE, CREATE ON SCHEMA public TO :"realtime_role";
GRANT ALL ON ALL TABLES IN SCHEMA realtime TO :"realtime_role";
GRANT ALL ON ALL SEQUENCES IN SCHEMA realtime TO :"realtime_role";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA realtime TO :"realtime_role";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"realtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.schema_migrations TO
  :"auth_role", :"realtime_role";
SELECT set_config('atlas.auth_role', :'auth_role', false);
DO $body$
DECLARE
  policy_name text := 'Atlas upstream migration roles';
  auth_target name := current_setting('atlas.auth_role');
  realtime_target name := current_setting('atlas.realtime_role');
BEGIN
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.schema_migrations', policy_name);
  EXECUTE format(
    'CREATE POLICY %I ON public.schema_migrations FOR ALL TO %I, %I '
    'USING (true) WITH CHECK (true)',
    policy_name, auth_target, realtime_target
  );
END
$body$;
ALTER DEFAULT PRIVILEGES FOR ROLE :"realtime_role" IN SCHEMA realtime
  GRANT ALL ON TABLES TO :"realtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"realtime_role" IN SCHEMA realtime
  GRANT ALL ON SEQUENCES TO :"realtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"realtime_role" IN SCHEMA realtime
  GRANT EXECUTE ON FUNCTIONS TO :"realtime_role";

GRANT USAGE ON SCHEMA public, n8n, storage TO
  :"airflow_reader", :"mcp_reader", :"jupyter_reader", :"zeppelin_reader";
GRANT SELECT ON ALL TABLES IN SCHEMA public, n8n, storage TO
  :"airflow_reader", :"mcp_reader", :"jupyter_reader", :"zeppelin_reader";
ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_role" IN SCHEMA public
  GRANT SELECT ON TABLES TO
    :"airflow_reader", :"mcp_reader", :"jupyter_reader", :"zeppelin_reader";
ALTER DEFAULT PRIVILEGES FOR ROLE :"n8n_role" IN SCHEMA n8n
  GRANT SELECT ON TABLES TO
    :"airflow_reader", :"mcp_reader", :"jupyter_reader", :"zeppelin_reader";
ALTER DEFAULT PRIVILEGES FOR ROLE :"storage_role" IN SCHEMA storage
  GRANT SELECT ON TABLES TO
    :"airflow_reader", :"mcp_reader", :"jupyter_reader", :"zeppelin_reader";

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"backend_role";

GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.research_sessions, public.research_results, public.research_sources,
  public.research_logs, public.memory_facts, public.memory_sessions,
  public.memory_consolidation_log, public.media_spend_ledger
TO :"backend_role";

GRANT SELECT ON public.memory_embedding_schema_state TO :"backend_role";

REVOKE ALL ON FUNCTION public.contract_memory_embedding_dimension(integer)
    FROM :"backend_role";
GRANT EXECUTE ON FUNCTION public.contract_memory_embedding_contract(text, integer, bigint)
    TO :"backend_role";
GRANT EXECUTE ON FUNCTION public.set_memory_weaviate_rebuild_required(boolean)
TO :"backend_role";
GRANT EXECUTE ON FUNCTION public.mark_memory_weaviate_dirty()
TO :"backend_role";
GRANT EXECUTE ON FUNCTION public.ensure_memory_weaviate_identity(text, integer)
TO :"backend_role";
REVOKE ALL ON FUNCTION public.complete_memory_weaviate_rebuild(bigint)
FROM :"backend_role";
GRANT EXECUTE ON FUNCTION public.complete_memory_weaviate_rebuild(bigint, text, integer)
TO :"backend_role";

DO $body$
DECLARE
  relation_name text;
  policy_name text := 'Atlas backend direct role access';
  target name := current_setting('atlas.backend_role');
BEGIN
  FOREACH relation_name IN ARRAY ARRAY[
    'research_sessions', 'research_results', 'research_sources', 'research_logs',
    'memory_facts', 'memory_sessions', 'memory_consolidation_log',
    'media_spend_ledger'
  ]
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', policy_name, relation_name);
    EXECUTE format(
      'CREATE POLICY %I ON public.%I FOR ALL TO %I USING (true) WITH CHECK (true)',
      policy_name, relation_name, target
    );
  END LOOP;
END
$body$;

-- The embedding schema singleton is internal control-plane state. Backend must
-- inspect it, but it does not need INSERT/UPDATE/DELETE and PostgREST JWT roles
-- receive no policy at all. Keep this separate from the FOR ALL data-table loop
-- so future edits cannot silently widen the direct role's access.
DO $body$
DECLARE
  policy_name text := 'Atlas backend schema-state read';
  target name := current_setting('atlas.backend_role');
BEGIN
  EXECUTE format(
    'DROP POLICY IF EXISTS %I ON public.memory_embedding_schema_state',
    policy_name
  );
  EXECUTE format(
    'CREATE POLICY %I ON public.memory_embedding_schema_state FOR SELECT TO %I USING (true)',
    policy_name, target
  );
END
$body$;

-- Record the successfully provisioned identity only after its grants and both
-- policy families are current. format(%I/%L) protects arbitrary valid names.
SELECT format(
  'ALTER DATABASE %I SET atlas.managed_backend_role TO %L',
  current_database(), :'backend_role'
) \gexec

ALTER SCHEMA n8n OWNER TO :"n8n_role";
REVOKE ALL ON SCHEMA n8n FROM PUBLIC;
REVOKE ALL ON SCHEMA n8n FROM :"admin_role";
GRANT ALL ON SCHEMA n8n TO :"n8n_role";
GRANT ALL ON ALL TABLES IN SCHEMA n8n TO :"n8n_role";
GRANT ALL ON ALL SEQUENCES IN SCHEMA n8n TO :"n8n_role";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA n8n TO :"n8n_role";

GRANT USAGE, CREATE ON SCHEMA public TO :"openwebui_role", :"lightrag_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON public.users TO :"openwebui_role";

SELECT format('CREATE SCHEMA IF NOT EXISTS lightrag AUTHORIZATION %I', :'lightrag_role') \gexec
ALTER SCHEMA lightrag OWNER TO :"lightrag_role";
REVOKE ALL ON SCHEMA lightrag FROM PUBLIC;
GRANT ALL ON SCHEMA lightrag TO :"lightrag_role";
GRANT ALL ON ALL TABLES IN SCHEMA lightrag TO :"lightrag_role";
GRANT ALL ON ALL SEQUENCES IN SCHEMA lightrag TO :"lightrag_role";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA lightrag TO :"lightrag_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"lightrag_role" IN SCHEMA lightrag
  GRANT ALL ON TABLES TO :"lightrag_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"lightrag_role" IN SCHEMA lightrag
  GRANT ALL ON SEQUENCES TO :"lightrag_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"lightrag_role" IN SCHEMA lightrag
  GRANT EXECUTE ON FUNCTIONS TO :"lightrag_role";

SELECT set_config('atlas.n8n_role', :'n8n_role', false);
SELECT set_config('atlas.openwebui_role', :'openwebui_role', false);
SELECT set_config('atlas.lightrag_role', :'lightrag_role', false);
DO $body$
DECLARE
  item record;
  target name;
  kind text;
BEGIN
  -- Existing n8n migrations must become owned by the scoped role.
  target := current_setting('atlas.n8n_role');
  FOR item IN
    SELECT n.nspname, c.relname, c.relkind
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'n8n' AND c.relkind IN ('r','p','S','v','m','f')
      AND pg_get_userbyid(c.relowner) <> target
  LOOP
    kind := CASE item.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
      WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END;
    EXECUTE format('ALTER %s %I.%I OWNER TO %I', kind, item.nspname, item.relname, target);
  END LOOP;
  FOR item IN
    SELECT p.oid::regprocedure AS identity
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'n8n' AND pg_get_userbyid(p.proowner) <> target
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO %I', item.identity, target);
  END LOOP;

  -- Open WebUI owns these public tables in the pinned v0.6.32 migration set.
  target := current_setting('atlas.openwebui_role');
  FOR item IN
    SELECT c.relname, c.relkind
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname = ANY (ARRAY[
        'alembic_version','auth','chat','channel','channel_member','chatidtag',
        'config','document','feedback','file','folder','function','group','knowledge',
        'memory','message','message_reaction','model','note','oauth_session','prompt',
        'tag','tool','user'
      ])
      AND c.relkind IN ('r','p','S','v','m','f')
      AND pg_get_userbyid(c.relowner) <> target
  LOOP
    kind := CASE item.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
      WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END;
    EXECUTE format('ALTER %s public.%I OWNER TO %I', kind, item.relname, target);
  END LOOP;

  -- LightRAG prefixes every PGVectorStorage relation with its workspace.
  target := current_setting('atlas.lightrag_role');
  FOR item IN
    SELECT c.relname, c.relkind
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'lightrag'
      AND c.relkind IN ('r','p','S','v','m','f')
      AND pg_get_userbyid(c.relowner) <> target
  LOOP
    kind := CASE item.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
      WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END;
    EXECUTE format('ALTER %s lightrag.%I OWNER TO %I', kind, item.relname, target);
  END LOOP;
  FOR item IN
    SELECT p.oid::regprocedure AS identity
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='lightrag' AND pg_get_userbyid(p.proowner) <> target
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO %I', item.identity, target);
  END LOOP;
  FOR item IN
    SELECT c.relname, c.relkind
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND lower(c.relname) LIKE 'lightrag%'
      AND c.relkind IN ('r','p','S','v','m','f')
      AND pg_get_userbyid(c.relowner) <> target
  LOOP
    kind := CASE item.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
      WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END;
    EXECUTE format('ALTER %s public.%I OWNER TO %I', kind, item.relname, target);
  END LOOP;
END
$body$;

-- Studio exposes a distinct read-only SQL-editor identity. The pinned Studio
-- image has one password field for both editor identities, so it shares the
-- Meta interface password without sharing Meta's membership or authority.
REVOKE ALL ON SCHEMA auth, public, realtime, storage, n8n, lightrag
  FROM :"studio_role";
GRANT USAGE ON SCHEMA auth, public, realtime, storage, n8n, lightrag
  TO :"studio_role";
GRANT SELECT ON ALL TABLES IN SCHEMA auth, public, realtime, storage, n8n, lightrag
  TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_role" IN SCHEMA public
  GRANT SELECT ON TABLES TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"meta_role" IN SCHEMA public
  GRANT SELECT ON TABLES TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"realtime_role" IN SCHEMA public
  GRANT SELECT ON TABLES TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"openwebui_role" IN SCHEMA public
  GRANT SELECT ON TABLES TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"lightrag_role" IN SCHEMA public
  GRANT SELECT ON TABLES TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"auth_role" IN SCHEMA auth
  GRANT SELECT ON TABLES TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"realtime_role" IN SCHEMA realtime
  GRANT SELECT ON TABLES TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"storage_role" IN SCHEMA storage
  GRANT SELECT ON TABLES TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"n8n_role" IN SCHEMA n8n
  GRANT SELECT ON TABLES TO :"studio_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"lightrag_role" IN SCHEMA lightrag
  GRANT SELECT ON TABLES TO :"studio_role";
SQL

echo "scoped-roles: roles, ownership, and grants are current"
