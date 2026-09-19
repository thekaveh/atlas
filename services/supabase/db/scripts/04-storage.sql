-- 04-storage.sql
-- Configure storage schema tables, policies, and create default bucket

-- Create storage.buckets table
CREATE TABLE IF NOT EXISTS storage.buckets (
    id text primary key,
    name text not null,
    owner uuid references auth.users,
    created_at timestamptz default now(),
    updated_at timestamptz default now(),
    file_size_limit bigint,
    allowed_mime_types text[],
    avif_autodetection boolean default false
);

-- Create storage.objects table.
-- IF NOT EXISTS, like every other table in these scripts: supabase-db-init
-- re-runs all scripts on EVERY `docker compose up`, and the previous
-- DROP-and-recreate wiped all object metadata (ComfyUI uploads, anything
-- via supabase-storage) on every restart — worse, storage-api's own
-- migrations stayed marked applied in storage.migrations, so columns it
-- had added never came back after the wipe.
CREATE TABLE IF NOT EXISTS storage.objects (
    id uuid primary key default gen_random_uuid(),
    bucket_id text references storage.buckets(id),
    name text,
    owner uuid references auth.users,
    created_at timestamptz default now(),
    updated_at timestamptz default now(),
    last_accessed_at timestamptz default now(),
    metadata jsonb,
    path_tokens text[] generated always as (string_to_array(name, '/')) stored
);

-- Backfill path_tokens onto pre-existing storage.objects tables.
-- The CREATE TABLE IF NOT EXISTS above preserves the original schema
-- when the table already exists, so any column added there never
-- reaches existing supabase-db-data volumes without an explicit
-- ALTER. ADD COLUMN IF NOT EXISTS is itself idempotent — no effect
-- on fresh creates where the column is already present.
ALTER TABLE storage.objects
    ADD COLUMN IF NOT EXISTS path_tokens text[]
        GENERATED ALWAYS AS (string_to_array(name, '/')) STORED;

-- Create indexes
CREATE INDEX IF NOT EXISTS bname ON storage.buckets (name);
-- Index names are unique per schema, not per table: buckets and objects both
-- live in schema `storage`, so a shared `owner` name silently drops the second
-- CREATE (IF NOT EXISTS turns it into a no-op) and leaves storage.objects(owner)
-- unindexed. Use distinct names so both indexes are actually created.
CREATE INDEX IF NOT EXISTS idx_storage_buckets_owner ON storage.buckets (owner);
CREATE INDEX IF NOT EXISTS bucket_id ON storage.objects (bucket_id);
CREATE INDEX IF NOT EXISTS name ON storage.objects (name);
CREATE INDEX IF NOT EXISTS idx_storage_objects_owner ON storage.objects (owner);
CREATE INDEX IF NOT EXISTS path_tokens_idx ON storage.objects USING gin (path_tokens);

-- Disable RLS since we're managing access through GRANTs
ALTER TABLE storage.buckets DISABLE ROW LEVEL SECURITY;
ALTER TABLE storage.objects DISABLE ROW LEVEL SECURITY;


-- Grant privileges to roles
GRANT ALL ON storage.buckets TO service_role;
GRANT ALL ON storage.objects TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA storage TO service_role;

-- `anon` is deliberately EXCLUDED here. RLS is disabled on these tables (see
-- above), so a GRANT is the only control — and PostgREST publishes `storage`
-- (PGRST_DB_SCHEMA: "public,storage") on a port that binds 0.0.0.0 unless
-- HOST_BIND_IP is set. Granting anon SELECT therefore made every object's
-- path, owner and metadata, and every bucket row, readable by any
-- unauthenticated network peer. Verified before this change: an
-- unauthenticated request with `Accept-Profile: storage` returned
-- `alice/private/tax-return-2025.pdf`.
--
-- Nothing reads these tables as anon: the Storage service has its own HTTP
-- API and its own credentials, and no code in the tree queries them through
-- PostgREST.
-- `authenticated` is excluded for the same reason, and it is not a weaker
-- case. GOTRUE_DISABLE_SIGNUP is "false" (supabase/compose.yml), so anyone who
-- can reach the gateway can self-register and hold an `authenticated` JWT. With
-- RLS off there is no owner scoping left: SELECT exposed every object's path,
-- owner and metadata across every bucket, and INSERT/UPDATE/DELETE let any such
-- account rewrite or erase every row in storage.objects -- other people's
-- objects included. Upstream Supabase grants this role only because it runs
-- these tables with RLS ON and owner-scoped policies; this slice deliberately
-- does not.
--
-- Nothing in the tree needs it. The Storage service reaches these tables as
-- SUPABASE_STORAGE_DB_USER with its own DDL/DML/READ contract (pinned by
-- tests/test_database_role_boundaries.py), and service_role keeps ALL above.
-- If an out-of-tree consumer really must read storage through PostgREST, give
-- it owner-scoped RLS policies rather than restoring a blanket grant.

-- Repair existing deployments: the grants above were previously issued to anon
-- and to authenticated, and a GRANT already made is not undone by re-running
-- the slice.
REVOKE ALL ON storage.buckets FROM anon;
REVOKE ALL ON storage.objects FROM anon;
REVOKE ALL ON storage.buckets FROM authenticated;
REVOKE ALL ON storage.objects FROM authenticated;

-- Create default storage bucket (safe to re-run)
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'authenticated') THEN
    INSERT INTO storage.buckets (id, name)
    VALUES ('default', 'default')
    ON CONFLICT (id) DO NOTHING;
  END IF;
END $$;
