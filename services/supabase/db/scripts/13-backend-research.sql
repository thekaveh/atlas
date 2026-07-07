-- 13-backend-research.sql
-- OWNER: backend/local-deep-researcher — research_* tables. user_id FKs
-- reference public.users (slice 10, sorts first). Only this service's objects
-- belong here. Renamed verbatim from the former 09-research-tables.sql.

-- Create research schema
CREATE SCHEMA IF NOT EXISTS research;
GRANT ALL PRIVILEGES ON SCHEMA research TO supabase_admin;

-- Research sessions table - tracks individual research requests
CREATE TABLE IF NOT EXISTS public.research_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    max_loops INTEGER NOT NULL DEFAULT 3,
    search_api VARCHAR(50) NOT NULL DEFAULT 'searxng',
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT
);

-- Research results table - stores the final research output
CREATE TABLE IF NOT EXISTS public.research_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES public.research_sessions(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    sources JSONB DEFAULT '[]'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Research sources table - tracks individual sources found during research
CREATE TABLE IF NOT EXISTS public.research_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES public.research_sessions(id) ON DELETE CASCADE,
    result_id UUID REFERENCES public.research_results(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT,
    content TEXT,
    relevance_score FLOAT,
    scraped_at TIMESTAMPTZ DEFAULT now(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Research logs table - stores step-by-step research process logs
CREATE TABLE IF NOT EXISTS public.research_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES public.research_sessions(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    step_type VARCHAR(50) NOT NULL, -- 'search', 'scrape', 'analyze', 'synthesize', etc.
    message TEXT NOT NULL,
    data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_research_sessions_status ON public.research_sessions(status);
CREATE INDEX IF NOT EXISTS idx_research_sessions_user_id ON public.research_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_research_sessions_created_at ON public.research_sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_research_results_session_id ON public.research_results(session_id);
CREATE INDEX IF NOT EXISTS idx_research_sources_session_id ON public.research_sources(session_id);
CREATE INDEX IF NOT EXISTS idx_research_logs_session_id ON public.research_logs(session_id);

-- updated_at trigger for research_sessions. Reuses update_updated_at_column()
-- from 07-functions.sql (which sorts before this slice) — the prior duplicate
-- CREATE OR REPLACE FUNCTION here was copy-paste drift: identical to 07's and
-- a no-op at runtime, but a hazard if the trigger body ever changes in 07.
DROP TRIGGER IF EXISTS update_research_sessions_updated_at ON public.research_sessions;
CREATE TRIGGER update_research_sessions_updated_at 
    BEFORE UPDATE ON public.research_sessions 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Grant permissions to service roles
GRANT ALL ON public.research_sessions TO service_role;
GRANT ALL ON public.research_results TO service_role;
GRANT ALL ON public.research_sources TO service_role;
GRANT ALL ON public.research_logs TO service_role;

-- Grant read permissions to authenticated users for their own data
GRANT SELECT ON public.research_sessions TO authenticated;
GRANT SELECT ON public.research_results TO authenticated;
GRANT SELECT ON public.research_sources TO authenticated;
GRANT SELECT ON public.research_logs TO authenticated;

-- Row Level Security policies
ALTER TABLE public.research_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_logs ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist
DROP POLICY IF EXISTS "Users can view their own research sessions" ON public.research_sessions;
DROP POLICY IF EXISTS "Service role can access all research sessions" ON public.research_sessions;
DROP POLICY IF EXISTS "Users can view results for their sessions" ON public.research_results;
DROP POLICY IF EXISTS "Service role can access all research results" ON public.research_results;
DROP POLICY IF EXISTS "Users can view sources for their sessions" ON public.research_sources;
DROP POLICY IF EXISTS "Service role can access all research sources" ON public.research_sources;
DROP POLICY IF EXISTS "Users can view logs for their sessions" ON public.research_logs;
DROP POLICY IF EXISTS "Service role can access all research logs" ON public.research_logs;

-- Users can only see their own research sessions
CREATE POLICY "Users can view their own research sessions" ON public.research_sessions
    FOR SELECT USING (auth.uid() = user_id);

-- Service role can access all data
CREATE POLICY "Service role can access all research sessions" ON public.research_sessions
    FOR ALL USING (auth.role() = 'service_role');

-- Results and sources inherit permissions from sessions
CREATE POLICY "Users can view results for their sessions" ON public.research_results
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.research_sessions 
            WHERE id = session_id AND user_id = auth.uid()
        )
    );

CREATE POLICY "Service role can access all research results" ON public.research_results
    FOR ALL USING (auth.role() = 'service_role');

CREATE POLICY "Users can view sources for their sessions" ON public.research_sources
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.research_sessions 
            WHERE id = session_id AND user_id = auth.uid()
        )
    );

CREATE POLICY "Service role can access all research sources" ON public.research_sources
    FOR ALL USING (auth.role() = 'service_role');

CREATE POLICY "Users can view logs for their sessions" ON public.research_logs
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM public.research_sessions 
            WHERE id = session_id AND user_id = auth.uid()
        )
    );

CREATE POLICY "Service role can access all research logs" ON public.research_logs
    FOR ALL USING (auth.role() = 'service_role');
