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

-- Required for logical replication used by Realtime
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Add any other required extensions here
