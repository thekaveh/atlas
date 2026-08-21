-- 12-comfyui.sql
-- OWNER: comfyui — comfyui_workflows / comfyui_generations, their indexes,
-- and the default workflow seeds. Only this service's objects belong here.
-- public.comfyui_models was decommissioned (16-decommission-comfyui-models.sql);
-- the ComfyUI model catalog SoT is now services/comfyui/models.yaml.
-- Assembled verbatim from the former 05-public-tables.sql (comfyui tables +
-- indexes) and 08-seed-data.sql (default workflow seeds). Tables are created
-- before the seed blocks that depend on them.

CREATE TABLE IF NOT EXISTS public.comfyui_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    workflow_data JSONB NOT NULL,
    category VARCHAR(100) DEFAULT 'custom',
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_comfyui_workflow_name UNIQUE (name)
);

CREATE INDEX IF NOT EXISTS idx_comfyui_workflows_active ON public.comfyui_workflows(active);
CREATE INDEX IF NOT EXISTS idx_comfyui_workflows_category ON public.comfyui_workflows(category);

CREATE TABLE IF NOT EXISTS public.comfyui_generations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt TEXT NOT NULL,
    negative_prompt TEXT,
    workflow_id UUID REFERENCES public.comfyui_workflows(id),
    image_url TEXT,
    image_path TEXT,
    parameters JSONB, -- Store generation parameters like seed, steps, cfg, etc.
    status VARCHAR(50) DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_comfyui_generations_status ON public.comfyui_generations(status);
CREATE INDEX IF NOT EXISTS idx_comfyui_generations_created_at ON public.comfyui_generations(created_at DESC);

-- Default workflow seeds (formerly 08-seed-data.sql).
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM public.comfyui_workflows WHERE name = 'Basic Text to Image') THEN
    INSERT INTO public.comfyui_workflows (name, description, workflow_data, category, active) VALUES
      ('Basic Text to Image', 'Simple text-to-image workflow using SD1.5',
       '{"nodes": [{"id": 1, "type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"}}, {"id": 2, "type": "CLIPTextEncode", "inputs": {"text": "a beautiful landscape", "clip": [1, 1]}}, {"id": 3, "type": "CLIPTextEncode", "inputs": {"text": "bad quality, blurry", "clip": [1, 1]}}, {"id": 4, "type": "KSampler", "inputs": {"seed": 42, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "model": [1, 0], "positive": [2, 0], "negative": [3, 0]}}, {"id": 5, "type": "VAEDecode", "inputs": {"samples": [4, 0], "vae": [1, 2]}}, {"id": 6, "type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": [5, 0]}}]}',
       'basic', true);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM public.comfyui_workflows WHERE name = 'SDXL Text to Image') THEN
    INSERT INTO public.comfyui_workflows (name, description, workflow_data, category, active) VALUES
      ('SDXL Text to Image', 'High-quality text-to-image workflow using SDXL',
       '{"nodes": [{"id": 1, "type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}}, {"id": 2, "type": "CLIPTextEncode", "inputs": {"text": "a beautiful landscape", "clip": [1, 1]}}, {"id": 3, "type": "CLIPTextEncode", "inputs": {"text": "bad quality, blurry", "clip": [1, 1]}}, {"id": 4, "type": "KSampler", "inputs": {"seed": 42, "steps": 25, "cfg": 7.5, "sampler_name": "dpmpp_2m", "scheduler": "karras", "model": [1, 0], "positive": [2, 0], "negative": [3, 0]}}, {"id": 5, "type": "VAEDecode", "inputs": {"samples": [4, 0], "vae": [1, 2]}}, {"id": 6, "type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI_SDXL", "images": [5, 0]}}]}',
       'basic', true);
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────
-- Row Level Security.
--
-- 06-permissions grants `anon` SELECT on every table in `public` (:15) and
-- sets the same as a DEFAULT PRIVILEGE (:25), and the pinned
-- supabase/postgres image ships default privileges granting anon full
-- arwdDxtm. PostgREST publishes `public` (compose.yml PGRST_DB_SCHEMA) with
-- PGRST_DB_ANON_ROLE=anon, and its port binds 0.0.0.0 unless HOST_BIND_IP is
-- set. RLS is therefore the ONLY thing between an unauthenticated network
-- peer and these tables.
--
-- Verified against a running stack before this was added: an unauthenticated
-- GET returned every row of comfyui_workflows, and a DELETE removed them.
-- comfyui_generations holds user prompts and output paths.
--
-- Scoped to the service_role claim exactly like the memory (14), research
-- (13) and media-ledger (17) tables. The backend and GoTrue connect as
-- supabase_admin, which OWNS these tables and so bypasses RLS — this does not
-- affect them. Re-running the slice is idempotent, so existing deployments
-- are repaired on their next start.
ALTER TABLE public.comfyui_workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.comfyui_generations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can access all comfyui workflows" ON public.comfyui_workflows;
CREATE POLICY "Service role can access all comfyui workflows" ON public.comfyui_workflows
    FOR ALL USING (auth.role() = 'service_role');

DROP POLICY IF EXISTS "Service role can access all comfyui generations" ON public.comfyui_generations;
CREATE POLICY "Service role can access all comfyui generations" ON public.comfyui_generations
    FOR ALL USING (auth.role() = 'service_role');
