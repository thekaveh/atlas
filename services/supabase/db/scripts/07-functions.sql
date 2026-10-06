-- 07-functions.sql
-- Create custom functions

-- Create health check function (safe to re-run)
CREATE OR REPLACE FUNCTION public.health() RETURNS text AS $$
BEGIN
  RETURN 'healthy';
END;
$$ LANGUAGE plpgsql;

-- Grant access to the health function (safe to re-run)
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'anon') THEN
    GRANT EXECUTE ON FUNCTION public.health() TO anon;
  END IF;
END
$$;

-- Create updated_at trigger function (safe to re-run)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Add any other custom functions here

-- Realtime needs wal_level=logical. The pinned supabase/postgres image sets
-- it at boot (it must be set before server start; ALTER SYSTEM would not take
-- effect until a restart anyway, so it could not help the replication slot
-- created below on this same boot). The previous conditional
-- `ALTER SYSTEM SET wal_level = 'logical'` lived inside a DO block, but
-- ALTER SYSTEM cannot run inside a transaction block (DO = transaction) — a
-- latent abort that was never reached only because the image pre-sets the
-- value. Removed rather than left as a landmine for a non-supabase Postgres.

-- Realtime v2 creates and owns its own replication slots and never reads
-- DB_SLOT, so the `supabase_realtime_slot` this script used to create was
-- never consumed: an idle logical slot pins WAL until
-- max_slot_wal_keep_size and then goes `lost`. Drop the orphan when idle.
SELECT pg_drop_replication_slot(slot_name)
FROM pg_replication_slots
WHERE slot_name = 'supabase_realtime_slot' AND NOT active;
