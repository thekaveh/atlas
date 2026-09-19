-- 06-permissions.sql
-- Grant permissions assuming base roles (anon, authenticated, service_role) exist

DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'anon') AND
     EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'authenticated') AND
     EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'service_role') THEN

    RAISE NOTICE 'Granting permissions to roles...';

    -- Grant schema usage
    GRANT USAGE ON SCHEMA public, auth, storage TO anon, authenticated, service_role;

    -- Grant table permissions (public schema)
    -- service_role is the trusted backend identity and bypasses RLS anyway.
    -- anon/authenticated are granted per-table further down, restricted to
    -- tables that actually have RLS -- see the loop after this block.
    GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;

    -- Grant sequence permissions (public schema)
    GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO authenticated, service_role;

    -- Grant function permissions (public schema)
    GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO authenticated, service_role;

    -- Set default privileges for future objects (public schema)
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO anon;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO authenticated, service_role;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO authenticated, service_role;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO authenticated, service_role;

    -- Grant access to the auth schema (assuming base image created necessary tables/functions)
    GRANT ALL ON SCHEMA auth TO service_role; -- service_role needs full access
    -- anon/authenticated are deliberately NOT granted SELECT on auth tables. The
    -- auth schema holds credentials (auth.users password hashes + recovery/email
    -- tokens, auth.identities, auth.mfa_factors); upstream Supabase never grants
    -- clients read access to it and never serves it via PostgREST. They keep only
    -- USAGE on the schema (granted above) so RLS policies can call
    -- auth.role()/auth.uid(). PGRST_DB_SCHEMA (supabase/compose.yml) also drops
    -- `auth` so PostgREST cannot serve these tables at all.
    ALTER DEFAULT PRIVILEGES IN SCHEMA auth GRANT ALL ON TABLES TO service_role;
    -- Note: Specific function grants might be needed depending on base image setup

    -- Grant access to the storage schema (assuming base image created necessary tables/functions)
    GRANT ALL ON SCHEMA storage TO service_role; -- service_role needs full access
    -- `anon` EXCLUDED for storage. Unlike `public`, RLS is DISABLED on
    -- storage.buckets/objects (04-storage.sql, "managing access through
    -- GRANTs instead"), so this grant is the ONLY control — and PostgREST
    -- publishes `storage` (PGRST_DB_SCHEMA: "public,storage") on a port that
    -- binds 0.0.0.0 unless HOST_BIND_IP is set. Granting anon here made every
    -- object path, owner and bucket row readable by any unauthenticated
    -- network peer. (For `public` the grant below is fine: RLS is enabled on
    -- every table there and is the intended gate.)
    -- `authenticated` is EXCLUDED here too, for the same reason and not a
    -- milder version of it. GOTRUE_DISABLE_SIGNUP is "false", so an
    -- `authenticated` JWT is self-service, and with RLS off on these tables the
    -- grant carries no owner scoping: it exposed every bucket's object paths,
    -- owners and metadata to any account that signed itself up. This ran on
    -- every boot and the default privilege below extended it to every future
    -- storage table, so 04-storage.sql's exclusions could not hold on their own.
    -- Repair existing deployments — a GRANT already made is not undone by
    -- simply omitting it on a later run.
    REVOKE ALL ON ALL TABLES IN SCHEMA storage FROM anon;
    REVOKE ALL ON ALL TABLES IN SCHEMA storage FROM authenticated;
    -- Explicit grants for storage.buckets table
    GRANT ALL PRIVILEGES ON storage.buckets TO service_role;
    GRANT ALL PRIVILEGES ON storage.objects TO service_role;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA storage TO service_role;
    ALTER DEFAULT PRIVILEGES IN SCHEMA storage GRANT ALL ON TABLES TO service_role;
    ALTER DEFAULT PRIVILEGES IN SCHEMA storage REVOKE SELECT ON TABLES FROM anon;
    ALTER DEFAULT PRIVILEGES IN SCHEMA storage REVOKE SELECT ON TABLES FROM authenticated;
    -- Note: Specific function grants might be needed depending on base image setup

  ELSE
      RAISE WARNING 'One or more standard roles (anon, authenticated, service_role) not found. Skipping permission grants.';
  END IF;
END $$;

-- Client-role grants on `public`, restricted to tables that carry RLS.
--
-- `ON ALL TABLES IN SCHEMA public` took every table in the schema, and this
-- slice re-runs on every boot -- so it also swept in tables created since by
-- whatever else shares this database. Open WebUI and JupyterHub both connect
-- to SUPABASE_DB_NAME_URI ("postgres") with no schema of their own (the
-- `openwebui` schema in 02-schemas.sql is commented out) and neither is
-- RLS-aware, while PGRST_DB_SCHEMA publishes `public`. The result was that
-- after any restart an `anon` caller could SELECT their tables through
-- PostgREST, and an `authenticated` one could write them. ALTER DEFAULT
-- PRIVILEGES did not cause this and does not fix it: it is scoped to the
-- creating role, so it never covered their tables in the first place.
--
-- Granting only where RLS exists keeps the intended gate in front of every
-- row. Every table these seeds create has RLS enabled, so Atlas's own surface
-- is unchanged; a co-tenant table without policies simply is not published.
-- Services that need real isolation should still get their own schema or
-- database -- this bounds the blast radius, it does not replace that.
DO $$
DECLARE
  target record;
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'anon') THEN
    RETURN;
  END IF;
  FOR target IN
    SELECT tablename, rowsecurity
    FROM pg_tables
    WHERE schemaname = 'public'
  LOOP
    IF target.rowsecurity THEN
      EXECUTE format('GRANT SELECT ON public.%I TO anon', target.tablename);
      EXECUTE format('GRANT ALL ON public.%I TO authenticated', target.tablename);
    ELSE
      -- Repair existing deployments: a GRANT already made is not undone by
      -- omitting it on a later run.
      EXECUTE format('REVOKE ALL ON public.%I FROM anon', target.tablename);
      EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', target.tablename);
    END IF;
  END LOOP;
END $$;
