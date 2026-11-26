-- 15-decommission-llms.sql
-- OWNER: litellm — decommission of the former public.llms catalog table.
--
-- The LLM model source-of-truth moved out of Postgres into per-service YAML
-- (services/ollama/models.yaml + services/litellm/models.yaml), resolved by
-- bootstrapper/utils/model_resolver.py. No service reads or writes public.llms
-- anymore (litellm-init renders config.yaml from the YAML; ollama-pull reads
-- OLLAMA_USER_MODELS/CUSTOM; weaviate/LDR/backend use LITELLM_*_MODEL env vars;
-- the n8n research workflow uses LITELLM_DEFAULT_MODEL).
--
-- Fresh installs never create public.llms (the former 11-litellm.sql is gone).
-- This drop cleans up pre-existing supabase-db-data volumes. Idempotent: a
-- no-op when the table is absent. The table held only the regenerable model
-- catalog (no precious user data — selections live in .env / the YAML), so the
-- drop is safe. DROP TABLE removes the table's own updated_at trigger; the
-- shared public.update_updated_at_column() function (07-functions.sql) is left
-- intact (still used by the research + memory triggers).

-- Drop only the table the original Atlas DDL created. Pre-existing volumes have
-- no marker comment, so the discriminator is the shape: exactly the 15 columns
-- of that DDL, all owned by a role the current user is a member of. A consumer's
-- own public.llms with a different shape is left alone, and a table this role
-- cannot drop does not stop boot (db-init-runner runs psql with ON_ERROR_STOP=1).
DO $$
DECLARE
  atlas_columns text[] := ARRAY[
    'id', 'active', 'vision', 'content', 'structured_content', 'embeddings',
    'provider', 'name', 'description', 'size_gb', 'context_window',
    'api_key', 'api_endpoint', 'created_at', 'updated_at'];
  atlas_shape boolean;
BEGIN
  SELECT count(*) = cardinality(atlas_columns)
         AND bool_and(a.attname::text = ANY (atlas_columns))
    INTO atlas_shape
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relname = 'llms' AND c.relkind = 'r'
     AND pg_has_role(c.relowner, 'MEMBER')
     AND a.attnum > 0 AND NOT a.attisdropped;
  IF coalesce(atlas_shape, false) THEN
    DROP TABLE public.llms;
  END IF;
END
$$;
