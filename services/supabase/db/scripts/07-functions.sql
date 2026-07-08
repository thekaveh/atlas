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

-- Create replication slot for realtime if it doesn't exist
SELECT pg_create_logical_replication_slot('supabase_realtime_slot', 'pgoutput')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_replication_slots 
  WHERE slot_name = 'supabase_realtime_slot'
);
