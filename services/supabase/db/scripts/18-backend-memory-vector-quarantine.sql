-- 18-backend-memory-vector-quarantine.sql
-- OWNER: backend — per-fact vector-sync quarantine for public.memory_facts
-- (#1365). Runs after 14-backend-memory.sql, which creates the table.
--
-- The Weaviate failback rebuild records a deterministic per-fact rejection
-- (an HTTP 4xx from Weaviate or its embedder) on the row and continues. After
-- three recorded rejections the fact is quarantined: it stays
-- vector_sync_pending, but it no longer blocks the rebuild completion below,
-- so one fact the embedder refuses cannot hold memory on pgvector for good.
-- The threshold matches VECTOR_SYNC_QUARANTINE_ATTEMPTS in
-- services/backend/app/app/memory_store.py. A successful sync, or an edit to
-- the fact's content, resets the counter.
--
-- This slice replaces the body of
-- public.complete_memory_weaviate_rebuild(bigint, text, integer) that
-- 14-backend-memory.sql defines. db-init-runner applies every slice in order
-- on each start, so this body is the one that stays installed.

BEGIN;
SELECT pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('atlas.memory.embedding.schema'::text, 0::bigint));

ALTER TABLE public.memory_facts
    ADD COLUMN IF NOT EXISTS vector_sync_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE public.memory_facts
    ADD COLUMN IF NOT EXISTS vector_sync_error TEXT;

-- A recorded rejection belongs to the content it rejected. Clearing the
-- pending intent (a successful sync) or changing the content starts over.
CREATE OR REPLACE FUNCTION public.reset_memory_vector_sync_quarantine()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $reset_vector_quarantine$
BEGIN
    IF NOT NEW.vector_sync_pending
       OR NEW.content IS DISTINCT FROM OLD.content THEN
        NEW.vector_sync_attempts := 0;
        NEW.vector_sync_error := NULL;
    END IF;
    RETURN NEW;
END
$reset_vector_quarantine$;

REVOKE ALL ON FUNCTION public.reset_memory_vector_sync_quarantine() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.reset_memory_vector_sync_quarantine()
    FROM anon, authenticated, authenticator;

DROP TRIGGER IF EXISTS reset_memory_facts_vector_sync_quarantine
    ON public.memory_facts;
CREATE TRIGGER reset_memory_facts_vector_sync_quarantine
    BEFORE UPDATE OF vector_sync_pending, content ON public.memory_facts
    FOR EACH ROW EXECUTE FUNCTION public.reset_memory_vector_sync_quarantine();

-- Same contract as 14-backend-memory.sql, except that a fact quarantined
-- after three rejections no longer holds the rebuild open.
CREATE OR REPLACE FUNCTION public.complete_memory_weaviate_rebuild(
    expected_generation bigint,
    expected_model text,
    expected_dimension integer
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $complete_weaviate_identity$
DECLARE
    completed boolean;
BEGIN
    UPDATE public.memory_embedding_schema_state
       SET weaviate_rebuild_required = false,
           weaviate_synced_generation = expected_generation,
           weaviate_synced_model = expected_model,
           weaviate_synced_dimension = expected_dimension,
           updated_at = now()
     WHERE singleton = true
       AND weaviate_dirty_generation = expected_generation
       AND weaviate_target_model = expected_model
       AND target_dimension = expected_dimension
       AND NOT EXISTS (
           SELECT 1 FROM public.memory_facts
            WHERE vector_sync_pending = true
              AND vector_sync_attempts < 3
       )
    RETURNING true INTO completed;
    RETURN COALESCE(completed, false);
END
$complete_weaviate_identity$;

REVOKE ALL ON FUNCTION public.complete_memory_weaviate_rebuild(bigint, text, integer)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.complete_memory_weaviate_rebuild(bigint, text, integer)
    FROM anon, authenticated, authenticator;
GRANT EXECUTE ON FUNCTION public.complete_memory_weaviate_rebuild(bigint, text, integer)
    TO service_role;

COMMIT;
