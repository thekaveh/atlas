-- 03b-gotrue-migration-sync.sql
-- Sync GoTrue (pop) migration tracker with migrations already applied by
-- the Supabase postgres image.
--
-- Measured against supabase/gotrue:v2.191.0 (#1825). GoTrue's tracker is an
-- unqualified schema_migrations, resolved through the connecting role's
-- search_path. As supabase_auth_admin (search_path auth), GoTrue reads and writes
-- auth.schema_migrations, not this table. As supabase_admin (search_path
-- "$user", public, ...), it wrote this table, and the four rows below made it
-- skip those migrations. With the current role they are inert: on a fresh volume
-- GoTrue applies them, and skipping them changes the auth schema. Volumes from
-- before #991 are handled by 03c-gotrue-legacy-tracker.sql.

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version bigint NOT NULL,
    inserted_at timestamp(0) without time zone,
    CONSTRAINT schema_migrations_pkey PRIMARY KEY (version)
);

INSERT INTO public.schema_migrations (version, inserted_at) VALUES
    (20221208132122, NOW()),
    (20221215195500, NOW()),
    (20221215195800, NOW()),
    (20221215195900, NOW())
ON CONFLICT DO NOTHING;

-- Row Level Security. Without it, PostgREST exposes this table to `anon`
-- (see the note in 12-comfyui.sql for the full grant chain), and a single
-- unauthenticated DELETE empties it. That is not a data leak — it is an
-- availability kill for Realtime, which writes this table. GoTrue v2.191.0 does
-- not read it, so emptying the table does not change what GoTrue applies.
--
-- Identity note: GoTrue does NOT connect as supabase_admin. compose.yml builds
-- GOTRUE_DB_DATABASE_URL from SUPABASE_AUTH_DB_USER_URI, which defaults to
-- supabase_auth_admin. In the pinned supabase/postgres:17.6.1.139 image that
-- role has neither rolsuper nor rolbypassrls (supabase_admin has both;
-- service_role has rolbypassrls), so it does not bypass this policy by role
-- attribute. On a fresh install this table is created (and owned) by the
-- init role before GoTrue starts, so GoTrue's access comes from the explicit
-- grant and the "Atlas upstream migration roles" policy in
-- 05-scoped-roles.sh, not from ownership. Re-verify against a live stack before
-- changing that grant chain or adding FORCE ROW LEVEL SECURITY, because Realtime
-- writes this table.
ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can access the gotrue migration tracker" ON public.schema_migrations;
CREATE POLICY "Service role can access the gotrue migration tracker" ON public.schema_migrations
    FOR ALL USING (auth.role() = 'service_role');
