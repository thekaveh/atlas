-- 14-backend-memory.sql
-- OWNER: backend — memory_* tables + their idempotent migrations. user_id FKs
-- reference public.users (slice 10, sorts first). Only this service's objects
-- belong here. Assembled from the former 10-langmem-tables.sql and
-- 10a-langmem-migrations.sql (appended below).

-- db-init-runner supplies this from the manifest-owned contract. Keep direct
-- psql/test invocations compatible with existing 768-dimensional installs.
\if :{?atlas_memory_embedding_dim}
\else
\set atlas_memory_embedding_dim 768
\endif
\if :{?atlas_memory_embedding_model}
\else
\set atlas_memory_embedding_model ollama/nomic-embed-text
\endif

BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('atlas.memory.embedding.schema', 0));
SELECT set_config(
    'atlas.memory_embedding_dim', :'atlas_memory_embedding_dim', true
);
SELECT set_config(
    'atlas.memory_embedding_model', :'atlas_memory_embedding_model', true
);

DO $validate_dimension$
DECLARE
    desired integer;
BEGIN
    BEGIN
        desired := current_setting('atlas.memory_embedding_dim')::integer;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'LANGMEM_EMBEDDING_DIM must be an integer from 1 through 4000';
    END;
    IF desired < 1 OR desired > 4000 THEN
        RAISE EXCEPTION 'LANGMEM_EMBEDDING_DIM must be an integer from 1 through 4000';
    END IF;
    IF btrim(current_setting('atlas.memory_embedding_model')) = '' THEN
        RAISE EXCEPTION 'LITELLM_EMBEDDING_MODEL must not be empty';
    END IF;
END
$validate_dimension$;

-- Core memory facts table - stores extracted facts from conversations
CREATE TABLE IF NOT EXISTS public.memory_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    namespace VARCHAR(100) NOT NULL DEFAULT 'default',
    content TEXT NOT NULL,
    fact_type VARCHAR(50) NOT NULL DEFAULT 'observation'
        CHECK (fact_type IN ('observation', 'preference', 'instruction', 'relationship', 'event')),
    confidence FLOAT DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    source_conversation_id UUID,
    source_message_ids JSONB DEFAULT '[]'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    embedding vector,                     -- Full-precision pgvector fallback. Dimension is enforced by the migration contract below.
    embedding_model TEXT,                 -- Full LiteLLM model id that produced embedding.
    embedding_generation BIGINT NOT NULL DEFAULT 0,
    weaviate_id VARCHAR(255),             -- Weaviate vector reference (used when Weaviate available)
    vector_sync_pending BOOLEAN NOT NULL DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    superseded_by UUID REFERENCES public.memory_facts(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ
);

ALTER TABLE public.memory_facts
    ADD COLUMN IF NOT EXISTS vector_sync_pending BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.memory_facts
    ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE public.memory_facts
    ADD COLUMN IF NOT EXISTS embedding_generation BIGINT NOT NULL DEFAULT 0;

-- Expand: remove the legacy vector(768) typmod without changing vector values.
-- Existing rows remain full-precision vector values and are re-embedded in
-- place by Backend before the target-dimension constraint is contracted.
DO $expand_embedding$
DECLARE
    embedding_typmod integer;
BEGIN
    SELECT a.atttypmod
      INTO embedding_typmod
      FROM pg_attribute a
     WHERE a.attrelid = 'public.memory_facts'::regclass
       AND a.attname = 'embedding'
       AND NOT a.attisdropped;
    IF embedding_typmod IS DISTINCT FROM -1 THEN
        ALTER TABLE public.memory_facts
            ALTER COLUMN embedding TYPE vector USING embedding::vector;
    END IF;
END
$expand_embedding$;

CREATE TABLE IF NOT EXISTS public.memory_embedding_schema_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    active_dimension INTEGER NOT NULL CHECK (active_dimension BETWEEN 1 AND 4000),
    target_dimension INTEGER NOT NULL CHECK (target_dimension BETWEEN 1 AND 4000),
    pgvector_active_model TEXT,
    pgvector_target_model TEXT NOT NULL,
    pgvector_active_generation BIGINT NOT NULL DEFAULT 0,
    pgvector_target_generation BIGINT NOT NULL DEFAULT 1,
    phase TEXT NOT NULL CHECK (phase IN ('backfill', 'ready')),
    weaviate_rebuild_required BOOLEAN NOT NULL DEFAULT true,
    weaviate_dirty_generation BIGINT NOT NULL DEFAULT 1,
    weaviate_synced_generation BIGINT NOT NULL DEFAULT 0,
    weaviate_target_model TEXT,
    weaviate_synced_model TEXT,
    weaviate_synced_dimension INTEGER
        CHECK (weaviate_synced_dimension BETWEEN 1 AND 4000),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS weaviate_rebuild_required BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS weaviate_dirty_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS weaviate_synced_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS weaviate_target_model TEXT;
ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS weaviate_synced_model TEXT;
ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS weaviate_synced_dimension INTEGER;
ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS pgvector_active_model TEXT;
ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS pgvector_target_model TEXT;
ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS pgvector_active_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE public.memory_embedding_schema_state
    ADD COLUMN IF NOT EXISTS pgvector_target_generation BIGINT NOT NULL DEFAULT 1;

-- Upgrade a legacy boolean-only dirty state without ever treating it as clean.
UPDATE public.memory_embedding_schema_state
   SET weaviate_dirty_generation = weaviate_synced_generation + 1
 WHERE weaviate_rebuild_required
   AND weaviate_dirty_generation <= weaviate_synced_generation;

DO $record_embedding_target$
DECLARE
    desired integer := current_setting('atlas.memory_embedding_dim')::integer;
    desired_model text := current_setting('atlas.memory_embedding_model');
    observed integer;
    state public.memory_embedding_schema_state%ROWTYPE;
    changed boolean;
    next_generation bigint;
    complete boolean;
BEGIN
    SELECT min(vector_dims(embedding))
      INTO observed
      FROM public.memory_facts
     WHERE embedding IS NOT NULL;

    SELECT * INTO state
      FROM public.memory_embedding_schema_state
     WHERE singleton = true
     FOR UPDATE;

    IF NOT FOUND THEN
        next_generation := 1;
        SELECT NOT EXISTS (
            SELECT 1 FROM public.memory_facts
             WHERE embedding IS NULL
                OR vector_dims(embedding) <> desired
                OR embedding_model IS DISTINCT FROM desired_model
                OR embedding_generation <> next_generation
        ) INTO complete;
        INSERT INTO public.memory_embedding_schema_state (
            singleton, active_dimension, target_dimension,
            pgvector_active_model, pgvector_target_model,
            pgvector_active_generation, pgvector_target_generation, phase
        ) VALUES (
            true, COALESCE(observed, desired), desired,
            CASE WHEN complete THEN desired_model ELSE NULL END,
            desired_model,
            CASE WHEN complete THEN next_generation ELSE 0 END,
            next_generation,
            CASE WHEN complete THEN 'ready' ELSE 'backfill' END
        );
    ELSE
        changed := state.target_dimension IS DISTINCT FROM desired
                   OR state.pgvector_target_model IS DISTINCT FROM desired_model;
        next_generation := state.pgvector_target_generation
                           + CASE WHEN changed THEN 1 ELSE 0 END;
        SELECT NOT EXISTS (
            SELECT 1 FROM public.memory_facts
             WHERE embedding IS NULL
                OR vector_dims(embedding) <> desired
                OR embedding_model IS DISTINCT FROM desired_model
                OR embedding_generation <> next_generation
        ) INTO complete;
        UPDATE public.memory_embedding_schema_state
           SET target_dimension = desired,
               pgvector_target_model = desired_model,
               pgvector_target_generation = next_generation,
               phase = CASE WHEN complete THEN 'ready' ELSE 'backfill' END,
               weaviate_rebuild_required = weaviate_rebuild_required OR changed,
               weaviate_dirty_generation = weaviate_dirty_generation
                   + CASE WHEN changed THEN 1 ELSE 0 END,
               updated_at = now()
         WHERE singleton = true;
    END IF;
END
$record_embedding_target$;

ALTER TABLE public.memory_embedding_schema_state
    ALTER COLUMN pgvector_target_model SET NOT NULL;

-- Memory extraction sessions - tracks conversation-to-memory processing
CREATE TABLE IF NOT EXISTS public.memory_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    conversation_id UUID,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    facts_extracted INTEGER DEFAULT 0,
    facts_consolidated INTEGER DEFAULT 0,
    processing_started_at TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Memory consolidation audit log - tracks merge/update/supersede operations
CREATE TABLE IF NOT EXISTS public.memory_consolidation_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL
        CHECK (action IN ('merged', 'updated', 'superseded', 'expired')),
    source_fact_ids UUID[] NOT NULL,
    result_fact_id UUID REFERENCES public.memory_facts(id),
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_memory_facts_user_id ON public.memory_facts(user_id);
CREATE INDEX IF NOT EXISTS idx_memory_facts_namespace ON public.memory_facts(namespace);
CREATE INDEX IF NOT EXISTS idx_memory_facts_type ON public.memory_facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_memory_facts_active ON public.memory_facts(is_active);
CREATE INDEX IF NOT EXISTS idx_memory_facts_created_at ON public.memory_facts(created_at);
CREATE INDEX IF NOT EXISTS idx_memory_facts_user_active ON public.memory_facts(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_memory_facts_vector_sync_pending
    ON public.memory_facts(vector_sync_pending) WHERE vector_sync_pending = true;
CREATE INDEX IF NOT EXISTS idx_memory_facts_conversation ON public.memory_facts(source_conversation_id);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_user_id ON public.memory_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_conversation ON public.memory_sessions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_status ON public.memory_sessions(status);
CREATE INDEX IF NOT EXISTS idx_memory_consolidation_user_id ON public.memory_consolidation_log(user_id);

-- Index: vector HNSW supports up to 2,000 dimensions. Wider embeddings retain
-- their full-precision `vector` storage and use a halfvec expression only in
-- the HNSW search key. Runtime uses this exact expression for ORDER BY.
DO $create_embedding_index$
DECLARE
    desired integer := current_setting('atlas.memory_embedding_dim')::integer;
    desired_generation bigint;
    existing_index regclass;
    existing_relation oid;
    existing_relation_name text;
    index_matches boolean := false;
    expected_expression text;
    expected_predicate text;
    expected_opclass text;
    validation_pass integer;
BEGIN
    SELECT pgvector_target_generation INTO desired_generation
      FROM public.memory_embedding_schema_state
     WHERE singleton = true;
    expected_expression := CASE WHEN desired <= 2000
        THEN format('(embedding)::vector(%s)', desired)
        ELSE format('(embedding)::halfvec(%s)', desired)
    END;
    expected_predicate := format(
        '((embedding IS NOT NULL) AND (vector_dims(embedding) = %s) '
        'AND (embedding_generation = %s))',
        desired, desired_generation
    );
    expected_opclass := CASE WHEN desired <= 2000
                             THEN 'vector_cosine_ops'
                             ELSE 'halfvec_cosine_ops' END;

    -- Pass 1 validates/reuses or safely replaces an owned mismatch. Pass 2
    -- validates the durable postcondition, including an IF-NOT-EXISTS race.
    FOR validation_pass IN 1..2 LOOP
        existing_index := to_regclass('public.idx_memory_facts_embedding');
        existing_relation := NULL;
        existing_relation_name := NULL;
        index_matches := false;
        IF existing_index IS NOT NULL THEN
            SELECT i.indrelid,
                   format('%I.%I', table_ns.nspname, table_rel.relname),
                   table_ns.nspname = 'public'
                   AND table_rel.relname = 'memory_facts'
                   AND am.amname = 'hnsw'
                   AND i.indisvalid AND i.indisready AND i.indislive
                   AND NOT i.indisunique AND NOT i.indisprimary
                   AND NOT i.indisexclusion
                   AND i.indnkeyatts = 1 AND i.indnatts = 1
                   AND i.indkey[0] = 0
                   AND i.indcollation[0] = 0
                   AND i.indoption[0] = 0
                   AND opclass_ns.nspname = 'public'
                   AND opclass.opcname = expected_opclass
                   AND pg_get_expr(i.indexprs, i.indrelid, false)
                       = expected_expression
                   AND pg_get_expr(i.indpred, i.indrelid, false)
                       = expected_predicate
              INTO existing_relation, existing_relation_name, index_matches
              FROM pg_index i
              JOIN pg_class index_rel ON index_rel.oid = i.indexrelid
              JOIN pg_am am ON am.oid = index_rel.relam
              JOIN pg_class table_rel ON table_rel.oid = i.indrelid
              JOIN pg_namespace table_ns ON table_ns.oid = table_rel.relnamespace
              JOIN pg_opclass opclass ON opclass.oid = i.indclass[0]
              JOIN pg_namespace opclass_ns ON opclass_ns.oid = opclass.opcnamespace
             WHERE i.indexrelid = existing_index;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'public.idx_memory_facts_embedding exists but is not an index';
            END IF;
            IF existing_relation <> 'public.memory_facts'::regclass THEN
                RAISE EXCEPTION
                    'public.idx_memory_facts_embedding belongs to %, not public.memory_facts',
                    existing_relation_name;
            END IF;
        END IF;

        IF validation_pass = 2 THEN
            IF NOT COALESCE(index_matches, false) THEN
                RAISE EXCEPTION 'memory embedding index postcondition failed';
            END IF;
            CONTINUE;
        END IF;
        IF index_matches THEN
            RETURN;
        END IF;
        IF existing_index IS NOT NULL THEN
            DROP INDEX public.idx_memory_facts_embedding;
        END IF;

        IF desired <= 2000 THEN
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS idx_memory_facts_embedding ON public.memory_facts '
                'USING hnsw ((embedding::vector(%s)) vector_cosine_ops) '
                'WHERE embedding IS NOT NULL AND vector_dims(embedding) = %s '
                'AND embedding_generation = %s',
                desired, desired, desired_generation
            );
        ELSE
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS idx_memory_facts_embedding ON public.memory_facts '
                'USING hnsw ((embedding::halfvec(%s)) halfvec_cosine_ops) '
                'WHERE embedding IS NOT NULL AND vector_dims(embedding) = %s '
                'AND embedding_generation = %s',
                desired, desired, desired_generation
            );
        END IF;
    END LOOP;
END
$create_embedding_index$;

-- Validate/contract is intentionally split from expand. A NOT VALID check
-- preserves pre-existing vectors while enforcing the selected dimension on
-- every new/updated row. Backend atomically re-embeds old rows, then invokes
-- the guarded SECURITY DEFINER function; a crash simply resumes the remaining
-- mismatches and can never mark the contract ready early.
DO $add_embedding_constraint$
DECLARE
    desired integer := current_setting('atlas.memory_embedding_dim')::integer;
    existing_definition text;
BEGIN
    SELECT pg_get_constraintdef(oid)
      INTO existing_definition
      FROM pg_constraint
     WHERE conrelid = 'public.memory_facts'::regclass
       AND conname = 'memory_facts_embedding_dimension';
    IF existing_definition IS NOT NULL
       AND position(format('vector_dims(embedding) = %s', desired)
                    IN existing_definition) > 0 THEN
        RETURN;
    END IF;
    ALTER TABLE public.memory_facts
        DROP CONSTRAINT IF EXISTS memory_facts_embedding_dimension;
    EXECUTE format(
        'ALTER TABLE public.memory_facts ADD CONSTRAINT '
        'memory_facts_embedding_dimension CHECK '
        '(embedding IS NULL OR vector_dims(embedding) = %s) NOT VALID',
        desired
    );
END
$add_embedding_constraint$;

CREATE OR REPLACE FUNCTION public.contract_memory_embedding_dimension(
    expected_dimension integer
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $legacy_contract$
BEGIN
    RAISE EXCEPTION
        'memory embedding contraction requires model and generation identity';
END
$legacy_contract$;

REVOKE ALL ON FUNCTION public.contract_memory_embedding_dimension(integer)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.contract_memory_embedding_dimension(integer)
    FROM anon, authenticated, authenticator;

CREATE OR REPLACE FUNCTION public.contract_memory_embedding_contract(
    expected_model text,
    expected_dimension integer,
    expected_generation bigint
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $contract$
DECLARE
    target integer;
    target_model text;
    target_generation bigint;
    mismatches bigint;
BEGIN
    IF expected_model IS NULL OR btrim(expected_model) = '' THEN
        RAISE EXCEPTION 'memory embedding model identity must not be empty';
    END IF;
    IF expected_dimension < 1 OR expected_dimension > 4000 THEN
        RAISE EXCEPTION 'invalid memory embedding dimension %', expected_dimension;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('atlas.memory.embedding.schema', 0));
    SELECT target_dimension, pgvector_target_model, pgvector_target_generation
      INTO target, target_model, target_generation
      FROM public.memory_embedding_schema_state
     WHERE singleton = true
     FOR UPDATE;
    IF target IS DISTINCT FROM expected_dimension THEN
        RAISE EXCEPTION 'memory embedding target is %, not %', target, expected_dimension;
    END IF;
    IF target_model IS DISTINCT FROM expected_model
       OR target_generation IS DISTINCT FROM expected_generation THEN
        RAISE EXCEPTION 'memory embedding target identity is % generation %, not % generation %',
            target_model, target_generation, expected_model, expected_generation;
    END IF;
    SELECT count(*) INTO mismatches
      FROM public.memory_facts
     WHERE embedding IS NULL
        OR vector_dims(embedding) <> expected_dimension
        OR embedding_model IS DISTINCT FROM expected_model
        OR embedding_generation <> expected_generation;
    IF mismatches <> 0 THEN
        RAISE EXCEPTION 'memory embedding backfill incomplete: % mismatched row(s)', mismatches;
    END IF;
    ALTER TABLE public.memory_facts
        VALIDATE CONSTRAINT memory_facts_embedding_dimension;
    UPDATE public.memory_embedding_schema_state
       SET active_dimension = expected_dimension,
           pgvector_active_model = expected_model,
           pgvector_active_generation = expected_generation,
           phase = 'ready',
           updated_at = now()
     WHERE singleton = true;
END
$contract$;

REVOKE ALL ON FUNCTION public.contract_memory_embedding_contract(text, integer, bigint)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.contract_memory_embedding_contract(text, integer, bigint)
    FROM anon, authenticated, authenticator;

CREATE OR REPLACE FUNCTION public.mark_memory_weaviate_dirty()
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $mark_weaviate$
DECLARE
    generation bigint;
BEGIN
    UPDATE public.memory_embedding_schema_state
       SET weaviate_rebuild_required = true,
           weaviate_dirty_generation = weaviate_dirty_generation + 1,
           updated_at = now()
     WHERE singleton = true
    RETURNING weaviate_dirty_generation INTO generation;
    IF generation IS NULL THEN
        RAISE EXCEPTION 'memory embedding schema state is missing';
    END IF;
    RETURN generation;
END
$mark_weaviate$;

REVOKE ALL ON FUNCTION public.mark_memory_weaviate_dirty() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.mark_memory_weaviate_dirty()
    FROM anon, authenticated, authenticator;

CREATE OR REPLACE FUNCTION public.ensure_memory_weaviate_identity(
    expected_model text,
    expected_dimension integer
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $ensure_weaviate_identity$
DECLARE
    state public.memory_embedding_schema_state%ROWTYPE;
BEGIN
    IF expected_model IS NULL OR btrim(expected_model) = '' THEN
        RAISE EXCEPTION 'memory Weaviate model identity must not be empty';
    END IF;
    IF expected_dimension < 1 OR expected_dimension > 4000 THEN
        RAISE EXCEPTION 'invalid memory Weaviate dimension %', expected_dimension;
    END IF;
    SELECT * INTO state
      FROM public.memory_embedding_schema_state
     WHERE singleton = true
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'memory embedding schema state is missing';
    END IF;
    IF state.target_dimension IS DISTINCT FROM expected_dimension THEN
        RAISE EXCEPTION 'memory embedding target is %, not %',
            state.target_dimension, expected_dimension;
    END IF;

    IF state.weaviate_target_model IS DISTINCT FROM expected_model THEN
        UPDATE public.memory_embedding_schema_state
           SET weaviate_target_model = expected_model,
               weaviate_rebuild_required = true,
               weaviate_dirty_generation = weaviate_dirty_generation + 1,
               updated_at = now()
         WHERE singleton = true
        RETURNING weaviate_dirty_generation INTO state.weaviate_dirty_generation;
    ELSIF (state.weaviate_synced_model IS DISTINCT FROM expected_model
           OR state.weaviate_synced_dimension IS DISTINCT FROM expected_dimension)
          AND NOT state.weaviate_rebuild_required THEN
        UPDATE public.memory_embedding_schema_state
           SET weaviate_rebuild_required = true,
               weaviate_dirty_generation = weaviate_dirty_generation + 1,
               updated_at = now()
         WHERE singleton = true
        RETURNING weaviate_dirty_generation INTO state.weaviate_dirty_generation;
    END IF;
    RETURN state.weaviate_dirty_generation;
END
$ensure_weaviate_identity$;

REVOKE ALL ON FUNCTION public.ensure_memory_weaviate_identity(text, integer)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.ensure_memory_weaviate_identity(text, integer)
    FROM anon, authenticated, authenticator;

-- Compatibility wrapper from the boolean-only contract. Clearing through it
-- is intentionally forbidden: only the generation CAS below may declare the
-- secondary synchronized.
CREATE OR REPLACE FUNCTION public.set_memory_weaviate_rebuild_required(
    required boolean
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $legacy_mark_weaviate$
BEGIN
    IF NOT required THEN
        RAISE EXCEPTION 'Weaviate rebuild may only be cleared with generation CAS';
    END IF;
    PERFORM public.mark_memory_weaviate_dirty();
END
$legacy_mark_weaviate$;

REVOKE ALL ON FUNCTION public.set_memory_weaviate_rebuild_required(boolean)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.set_memory_weaviate_rebuild_required(boolean)
    FROM anon, authenticated, authenticator;

CREATE OR REPLACE FUNCTION public.complete_memory_weaviate_rebuild(
    expected_generation bigint
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $legacy_complete_weaviate$
BEGIN
    RAISE EXCEPTION
        'Weaviate rebuild completion requires generation, model, and dimension';
END
$legacy_complete_weaviate$;

REVOKE ALL ON FUNCTION public.complete_memory_weaviate_rebuild(bigint)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.complete_memory_weaviate_rebuild(bigint)
    FROM anon, authenticated, authenticator;

CREATE OR REPLACE FUNCTION public.complete_memory_weaviate_rebuild(
    expected_generation bigint,
    expected_model text,
    expected_dimension integer
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
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
       )
    RETURNING true INTO completed;
    RETURN COALESCE(completed, false);
END
$complete_weaviate_identity$;

REVOKE ALL ON FUNCTION public.complete_memory_weaviate_rebuild(bigint, text, integer)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.complete_memory_weaviate_rebuild(bigint, text, integer)
    FROM anon, authenticated, authenticator;

DO $finish_fresh_embedding_contract$
DECLARE
    desired integer := current_setting('atlas.memory_embedding_dim')::integer;
    desired_model text := current_setting('atlas.memory_embedding_model');
    desired_generation bigint;
BEGIN
    SELECT pgvector_target_generation INTO desired_generation
      FROM public.memory_embedding_schema_state
     WHERE singleton = true;
    IF NOT EXISTS (
        SELECT 1 FROM public.memory_facts
         WHERE embedding IS NULL
            OR vector_dims(embedding) <> desired
            OR embedding_model IS DISTINCT FROM desired_model
            OR embedding_generation <> desired_generation
    ) THEN
        PERFORM public.contract_memory_embedding_contract(
            desired_model, desired, desired_generation
        );
    END IF;
END
$finish_fresh_embedding_contract$;

-- Apply updated_at trigger (reusing function from 07-functions.sql)
DROP TRIGGER IF EXISTS update_memory_facts_updated_at ON public.memory_facts;
CREATE TRIGGER update_memory_facts_updated_at
    BEFORE UPDATE ON public.memory_facts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Enable Row Level Security
ALTER TABLE public.memory_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memory_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memory_consolidation_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memory_embedding_schema_state ENABLE ROW LEVEL SECURITY;

-- This singleton is internal migration coordination state, not a PostgREST
-- client API.  06-permissions.sql grants broad DEFAULT PRIVILEGES before this
-- table is created, so repair those inherited grants explicitly.  Backend's
-- dedicated login receives a SELECT-only RLS policy in 05-scoped-roles.sh;
-- SECURITY DEFINER maintenance functions remain the service_role interface.
REVOKE ALL ON public.memory_embedding_schema_state
    FROM anon, authenticated, service_role;

-- Drop existing policies if they exist (idempotent)
DROP POLICY IF EXISTS "Users can view their own memory facts" ON public.memory_facts;
DROP POLICY IF EXISTS "Service role can access all memory facts" ON public.memory_facts;
DROP POLICY IF EXISTS "Users can view their own memory sessions" ON public.memory_sessions;
DROP POLICY IF EXISTS "Service role can access all memory sessions" ON public.memory_sessions;
DROP POLICY IF EXISTS "Users can view their own consolidation logs" ON public.memory_consolidation_log;
DROP POLICY IF EXISTS "Service role can access all consolidation logs" ON public.memory_consolidation_log;

-- Service role (backend) can access all memory data. Scoped to the
-- service_role claim like the research tables in 09 — the previous
-- USING (true) made the policy a no-op, and 06-permissions' default
-- privileges grant `authenticated` table rights, so any authenticated
-- PostgREST caller had full CRUD on every user's memories. The
-- backend's direct supabase_admin connection bypasses RLS (owner) and
-- is unaffected.
CREATE POLICY "Service role can access all memory facts" ON public.memory_facts
    FOR ALL USING (auth.role() = 'service_role');

CREATE POLICY "Service role can access all memory sessions" ON public.memory_sessions
    FOR ALL USING (auth.role() = 'service_role');

CREATE POLICY "Service role can access all consolidation logs" ON public.memory_consolidation_log
    FOR ALL USING (auth.role() = 'service_role');

-- Grant permissions to service roles
GRANT ALL ON public.memory_facts TO service_role;
GRANT ALL ON public.memory_sessions TO service_role;
GRANT ALL ON public.memory_consolidation_log TO service_role;
REVOKE ALL ON FUNCTION public.contract_memory_embedding_dimension(integer)
    FROM service_role;
GRANT EXECUTE ON FUNCTION public.contract_memory_embedding_contract(text, integer, bigint)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.set_memory_weaviate_rebuild_required(boolean)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.mark_memory_weaviate_dirty()
    TO service_role;
GRANT EXECUTE ON FUNCTION public.ensure_memory_weaviate_identity(text, integer)
    TO service_role;
REVOKE ALL ON FUNCTION public.complete_memory_weaviate_rebuild(bigint)
    FROM service_role;
GRANT EXECUTE ON FUNCTION public.complete_memory_weaviate_rebuild(bigint, text, integer)
    TO service_role;

-- ── Migrations (formerly 10a-langmem-migrations.sql) ───────────────────────
-- Idempotent: converts legacy VARCHAR(255) user_id → UUID and re-points the FK
-- at public.users(id). Safe to re-run; no-op on fresh installs.
DO $$
DECLARE
    tbl text;
    legacy_fk text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'memory_facts',
        'memory_sessions',
        'memory_consolidation_log'
    ]
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = tbl
               AND column_name = 'user_id'
               AND data_type = 'character varying'
        ) THEN
            CONTINUE;
        END IF;

        SELECT conname
          INTO legacy_fk
          FROM pg_constraint
         WHERE conrelid = ('public.' || tbl)::regclass
           AND contype = 'f'
           AND array_length(conkey, 1) = 1
           AND (SELECT attname FROM pg_attribute
                 WHERE attrelid = conrelid
                   AND attnum  = conkey[1]) = 'user_id';
        -- #800: guard the VARCHAR→UUID cast. A pre-existing volume whose legacy
        -- user_id holds a non-UUID string makes `USING user_id::uuid` raise
        -- `invalid input syntax for type uuid`, which would abort the whole DO
        -- block and fail DB init on EVERY start. Wrap the per-table migration in
        -- a nested block: on any error the implicit savepoint rolls back THIS
        -- table's changes (the FK drop included) and a WARNING is raised, leaving
        -- the table in its legacy shape for the operator to clean up — instead of
        -- aborting init for the whole stack. Fresh installs never reach here (the
        -- data_type guard above CONTINUEs on already-uuid columns).
        BEGIN
            IF legacy_fk IS NOT NULL THEN
                EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', tbl, legacy_fk);
                RAISE NOTICE 'public.%: dropped legacy user_id FK %', tbl, legacy_fk;
            END IF;

            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN user_id TYPE uuid USING user_id::uuid',
                tbl
            );

            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE',
                tbl, tbl || '_user_id_fkey'
            );

            RAISE NOTICE 'public.%: user_id migrated VARCHAR(255) → UUID, FK → public.users(id)', tbl;
        EXCEPTION
            WHEN others THEN
                RAISE WARNING 'public.%: legacy user_id VARCHAR→UUID migration skipped (%); non-UUID values present — column left as-is, clean them up and re-run', tbl, SQLERRM;
        END;
    END LOOP;
END $$;

COMMIT;
