-- 01-extensions.sql
-- Enable necessary extensions

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;

-- PostGIS ships ST_EstimatedExtent as SECURITY DEFINER (it reads
-- pg_statistic) with no search_path, so it resolved names through the
-- caller's path, which includes public, where co-tenant roles can create
-- objects (#1456). Pin it: the schema-qualified three-argument form is
-- unchanged; the two-argument form no longer finds a table by search path.
DO $$
DECLARE
    definer pg_catalog.regprocedure;
BEGIN
    FOR definer IN
        SELECT p.oid::pg_catalog.regprocedure
          FROM pg_catalog.pg_proc AS p
         WHERE p.pronamespace = 'public'::pg_catalog.regnamespace
           AND p.proname = 'st_estimatedextent' AND p.prosecdef
    LOOP
        EXECUTE pg_catalog.format(
            'ALTER FUNCTION %s SET search_path = pg_catalog, pg_temp', definer);
    END LOOP;
END
$$;

-- pgcrypto (cryptographic functions). The pinned supabase/postgres image
-- already installs it in schema `extensions`, so this statement is a no-op
-- there. No Atlas script calls a pgcrypto function. Realtime's logical
-- replication needs wal_level=logical, not pgcrypto (see 07-functions.sql).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Add any other required extensions here
