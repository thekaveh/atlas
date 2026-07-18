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
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;
    GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated, service_role;

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
    GRANT SELECT ON ALL TABLES IN SCHEMA storage TO anon, authenticated;
    -- Explicit grants for storage.buckets table
    GRANT ALL PRIVILEGES ON storage.buckets TO service_role;
    GRANT ALL PRIVILEGES ON storage.objects TO service_role;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA storage TO service_role;
    ALTER DEFAULT PRIVILEGES IN SCHEMA storage GRANT ALL ON TABLES TO service_role;
    ALTER DEFAULT PRIVILEGES IN SCHEMA storage GRANT SELECT ON TABLES TO anon, authenticated;
    -- Note: Specific function grants might be needed depending on base image setup

  ELSE
      RAISE WARNING 'One or more standard roles (anon, authenticated, service_role) not found. Skipping permission grants.';
  END IF;
END $$;
