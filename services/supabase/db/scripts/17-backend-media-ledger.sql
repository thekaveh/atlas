-- 17-backend-media-ledger.sql
-- OWNER: backend — media-gateway spend ledger (#342). One durable, append-only
-- row per hosted-media operation capturing attribution + estimated/final cost +
-- artifact refs + lifecycle status, so budgets/quotas and a scoped spend read
-- API have a transactional source of truth (distinct from LiteLLM's text-spend
-- accounting). Only this service's objects belong here. Written by
-- media_ledger_postgres.PostgresLedgerStore; enforced only when
-- MEDIA_BUDGET_ENABLED=true.

CREATE TABLE IF NOT EXISTS public.media_spend_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id VARCHAR(255) NOT NULL,
    consumer VARCHAR(255) NOT NULL DEFAULT 'default',
    project VARCHAR(255) NOT NULL DEFAULT 'default',
    provider VARCHAR(100) NOT NULL,
    model VARCHAR(255) NOT NULL,
    model_version VARCHAR(100),
    modality VARCHAR(64) NOT NULL,
    -- reserved | submitted | committed | released | denied
    status VARCHAR(32) NOT NULL,
    currency VARCHAR(8) NOT NULL DEFAULT 'USD',
    -- NULL means "unknown" and is preserved as NULL — an unknown cost is never
    -- silently recorded as $0.
    estimated_cost_usd NUMERIC(12, 6),
    final_cost_usd NUMERIC(12, 6),
    pricing_source_ts TIMESTAMPTZ,
    -- MinIO content hashes / artifact URLs for the generated outputs.
    artifact_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Policy-denial explanation (kill-switch / over-budget / unknown-cost).
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per operation id: UNIQUE both indexes lookups and prevents a re-key
-- collision (a reused provider request id) from creating duplicate,
-- double-counted spend rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_media_spend_ledger_operation
    ON public.media_spend_ledger (operation_id);

-- Scoped reads + budget totals are per consumer/project, most-recent first.
CREATE INDEX IF NOT EXISTS idx_media_spend_ledger_scope
    ON public.media_spend_ledger (consumer, project, created_at);

-- Row Level Security. This ledger is the durable source of truth for budget /
-- quota enforcement. 06-permissions' default privileges grant `authenticated`
-- full table rights and `anon` SELECT, and PostgREST exposes `public`, so
-- WITHOUT RLS any holder of the public anon key could read the entire spend
-- ledger and any authenticated user could DELETE rows or zero final_cost_usd to
-- evade the budget cap. Scope all access to the service_role claim like the
-- memory (14) / research (13) tables; the backend's direct supabase_admin
-- connection bypasses RLS (owner) and is unaffected.
ALTER TABLE public.media_spend_ledger ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can access all media spend ledger" ON public.media_spend_ledger;
CREATE POLICY "Service role can access all media spend ledger" ON public.media_spend_ledger
    FOR ALL USING (auth.role() = 'service_role');
