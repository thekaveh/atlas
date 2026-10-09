-- 10-users.sql
-- OWNER: backend/auth — public.users. FK-referenced by the research (13) and
-- memory (14) slices, so this MUST sort before them. Only this service's
-- objects belong here. Moved verbatim from the former 05-public-tables.sql.

CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- The auth.users sync runs as this role, never as the init superuser.
-- auth.users belongs to GoTrue's login role, which can retype a column and
-- add an implicit cast: the superuser-owned SECURITY DEFINER trigger (and
-- the backfill below) then ran that cast as superuser, and GoTrue's
-- credential made itself superuser (2026-10-08 run, cycle 58). This role
-- can only read the synced auth.users columns and write public.users.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'atlas_auth_sync') THEN
    CREATE ROLE atlas_auth_sync NOLOGIN;
  END IF;
END $$;
ALTER ROLE atlas_auth_sync NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS;
GRANT USAGE ON SCHEMA public, auth TO atlas_auth_sync;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.users TO atlas_auth_sync;
GRANT SELECT (id, email, raw_user_meta_data, created_at) ON auth.users TO atlas_auth_sync;

-- Keep the shared ownership table aligned with Supabase Auth. Memory and
-- research use public.users as their foreign-key target, while JWT subjects
-- originate in auth.users. The trigger covers future inserts and profile
-- updates; the backfill makes the contract true for existing installations.
CREATE OR REPLACE FUNCTION public.handle_auth_user_sync()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM public.users WHERE id = OLD.id;
        RETURN OLD;
    END IF;
    INSERT INTO public.users (id, name, created_at)
    VALUES (
        NEW.id,
        COALESCE(
            NULLIF(BTRIM(NEW.raw_user_meta_data ->> 'name'), ''),
            NULLIF(BTRIM(NEW.raw_user_meta_data ->> 'full_name'), ''),
            NULLIF(SPLIT_PART(COALESCE(NEW.email, ''), '@', 1), ''),
            'Atlas user'
        ),
        COALESCE(NEW.created_at, now())
    )
    ON CONFLICT (id) DO UPDATE
    SET name = EXCLUDED.name;
    RETURN NEW;
END;
$$;

-- Trigger functions are not client RPCs. The public schema's broad default
-- function grants would otherwise expose this SECURITY DEFINER function.
REVOKE ALL ON FUNCTION public.handle_auth_user_sync()
    FROM PUBLIC, anon, authenticated, service_role;
ALTER FUNCTION public.handle_auth_user_sync() OWNER TO atlas_auth_sync;

DROP TRIGGER IF EXISTS on_auth_user_sync ON auth.users;
CREATE TRIGGER on_auth_user_sync
    AFTER INSERT OR DELETE OR UPDATE OF email, raw_user_meta_data ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_auth_user_sync();

-- Run a statement block as a given role inside a SECURITY DEFINER function.
-- There SET ROLE / RESET ROLE is refused, so code that role planted (a
-- trigger, a cast, a column default) cannot climb back to the init superuser.
-- SET LOCAL ROLE could: the session user stays superuser, and a planted
-- function ran RESET ROLE (2026-10-08 run, cycle 59).
CREATE OR REPLACE FUNCTION pg_temp.atlas_as_role(runner name, body text)
RETURNS void LANGUAGE plpgsql AS $atlas$
BEGIN
  DROP FUNCTION IF EXISTS public.atlas_role_step();
  EXECUTE 'CREATE FUNCTION public.atlas_role_step() RETURNS void LANGUAGE plpgsql '
       || 'SECURITY DEFINER SET search_path = '''' AS '
       || pg_catalog.quote_literal('BEGIN ' || body || ' END');
  REVOKE ALL ON FUNCTION public.atlas_role_step() FROM PUBLIC;
  EXECUTE pg_catalog.format('ALTER FUNCTION public.atlas_role_step() OWNER TO %I', runner);
  PERFORM public.atlas_role_step();
  DROP FUNCTION public.atlas_role_step();
END $atlas$;

CREATE OR REPLACE FUNCTION pg_temp.atlas_owner_of(target regclass)
RETURNS name LANGUAGE sql AS $atlas$
  SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class AS c WHERE c.oid = target
$atlas$;

SELECT pg_temp.atlas_as_role('atlas_auth_sync', $body$
INSERT INTO public.users (id, name, created_at)
SELECT
    id,
    COALESCE(
        NULLIF(BTRIM(raw_user_meta_data ->> 'name'), ''),
        NULLIF(BTRIM(raw_user_meta_data ->> 'full_name'), ''),
        NULLIF(SPLIT_PART(COALESCE(email, ''), '@', 1), ''),
        'Atlas user'
    ),
    COALESCE(created_at, now())
FROM auth.users
-- DO NOTHING, not DO UPDATE. This slice re-runs on every `docker compose up`,
-- so overwriting `name` silently reverted every profile rename on the next
-- restart — directly undoing the write the "Users can update own profile"
-- policy below explicitly permits. This is a BACKFILL: it exists to create a
-- profile row for an auth user that has none. Keeping an existing row is the
-- whole point, and the `on_auth_user_sync` trigger above already propagates
-- genuine auth-side changes as they happen.
ON CONFLICT (id) DO NOTHING;
$body$);

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can read own profile" ON public.users;
CREATE POLICY "Users can read own profile" ON public.users
    FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users can update own profile" ON public.users;
CREATE POLICY "Users can update own profile" ON public.users
    FOR UPDATE USING (auth.uid() = id) WITH CHECK (auth.uid() = id);

DROP POLICY IF EXISTS "Service role can access all user profiles" ON public.users;
CREATE POLICY "Service role can access all user profiles" ON public.users
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- Users signed up before supabase-auth set GOTRUE_JWT_AUD and
-- GOTRUE_JWT_DEFAULT_GROUP_NAME carry an empty aud and role, so every token
-- they receive is refused by the backend and PostgREST (2026-10-08 run,
-- cycle 56). Run as the table's owner inside a definer function, so a
-- trigger it planted never runs as the init superuser (cycle 59).
SELECT pg_temp.atlas_as_role(pg_temp.atlas_owner_of('auth.users'), $body$
  UPDATE auth.users SET aud = 'authenticated', role = 'authenticated'
   WHERE COALESCE(aud, '') = '' AND COALESCE(role, '') = '';
$body$);
