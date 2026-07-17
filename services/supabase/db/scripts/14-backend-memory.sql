-- 14-backend-memory.sql
-- OWNER: backend — memory_* tables + their idempotent migrations. user_id FKs
-- reference public.users (slice 10, sorts first). Only this service's objects
-- belong here. Assembled from the former 10-langmem-tables.sql and
-- 10a-langmem-migrations.sql (appended below).

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
    embedding vector(768),                -- pgvector fallback (Weaviate-unavailable path). 768 assumes the default LITELLM_EMBEDDING_MODEL (nomic-embed-text); a LANGMEM_EMBEDDING_MODEL with a different dimension breaks this fallback insert (use the Weaviate backend or a 768-dim model).
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

-- pgvector index for embedding similarity search (HNSW for approximate nearest neighbor)
-- HNSW is preferred over IVFFlat because it works correctly on empty/small tables
CREATE INDEX IF NOT EXISTS idx_memory_facts_embedding ON public.memory_facts
    USING hnsw (embedding vector_cosine_ops);

-- Apply updated_at trigger (reusing function from 07-functions.sql)
DROP TRIGGER IF EXISTS update_memory_facts_updated_at ON public.memory_facts;
CREATE TRIGGER update_memory_facts_updated_at
    BEFORE UPDATE ON public.memory_facts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Enable Row Level Security
ALTER TABLE public.memory_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memory_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memory_consolidation_log ENABLE ROW LEVEL SECURITY;

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
    END LOOP;
END $$;
