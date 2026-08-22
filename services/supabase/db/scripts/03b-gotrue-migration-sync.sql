-- 03b-gotrue-migration-sync.sql
-- Sync GoTrue (pop) migration tracker with migrations already applied by
-- the Supabase postgres image.
--
-- Problem: supabase/postgres:17.x ships with auth schema pre-configured
-- (tracked in auth.schema_migrations), but GoTrue uses a separate tracker
-- (public.schema_migrations). Migrations present in GoTrue's embedded list
-- but missing from its tracker are re-executed on startup. Migration
-- 20221208132122 uses a uuid=text comparison removed in PostgreSQL 17,
-- causing GoTrue to crash-loop.
--
-- Fix: Pre-populate public.schema_migrations with the versions the postgres
-- image already applied, so GoTrue skips them.

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
-- availability kill: the four rows above exist precisely so GoTrue skips
-- migration 20221208132122, whose uuid=text comparison was removed in
-- PostgreSQL 17. Emptying the table returns GoTrue to the crash-loop this
-- slice exists to prevent.
--
-- GoTrue connects as supabase_admin (GOTRUE_DB_DATABASE_URL uses
-- SUPABASE_DB_USER), which owns this table and so bypasses RLS.
ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can access the gotrue migration tracker" ON public.schema_migrations;
CREATE POLICY "Service role can access the gotrue migration tracker" ON public.schema_migrations
    FOR ALL USING (auth.role() = 'service_role');
