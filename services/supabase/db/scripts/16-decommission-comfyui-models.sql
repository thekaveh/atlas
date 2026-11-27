-- 16-decommission-comfyui-models.sql
-- OWNER: comfyui — decommission of the former public.comfyui_models catalog table.
--
-- The ComfyUI model catalog SoT moved to services/comfyui/models.yaml + the
-- custom-models.yaml sidecar, resolved by bootstrapper/utils/comfyui_resolver.py
-- into a manifest (volumes/comfyui/selected-models.yaml) written at start. No
-- service reads or writes public.comfyui_models anymore: comfyui-init downloads
-- from the manifest TSV; the backend GET /comfyui/db/models reads the manifest;
-- comfyui-catalog-init was deleted.
--
-- Fresh installs never create public.comfyui_models (its DDL left 12-comfyui.sql).
-- This drop cleans up pre-existing supabase-db-data volumes. Idempotent; the
-- table held only the regenerable model catalog (no precious user data —
-- selections live in .env / the YAML). public.comfyui_workflows and
-- public.comfyui_generations are not affected.

-- Drop only the table the original Atlas DDL created. Pre-existing volumes have
-- no marker comment, so the discriminator is the shape: exactly the 11 columns
-- of that DDL, all owned by a role the current user is a member of. A consumer's
-- own public.comfyui_models with a different shape is left alone, and a table
-- this role cannot drop does not stop boot (db-init-runner runs psql with
-- ON_ERROR_STOP=1).
DO $$
DECLARE
  atlas_columns text[] := ARRAY[
    'id', 'name', 'type', 'filename', 'download_url', 'file_size_gb',
    'description', 'active', 'essential', 'created_at', 'updated_at'];
  atlas_shape boolean;
BEGIN
  SELECT count(*) = cardinality(atlas_columns)
         AND bool_and(a.attname::text = ANY (atlas_columns))
    INTO atlas_shape
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relname = 'comfyui_models' AND c.relkind = 'r'
     AND pg_has_role(c.relowner, 'MEMBER')
     AND a.attnum > 0 AND NOT a.attisdropped;
  IF coalesce(atlas_shape, false) THEN
    DROP TABLE public.comfyui_models;
  END IF;
END
$$;
