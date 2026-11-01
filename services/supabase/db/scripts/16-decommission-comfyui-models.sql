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

DROP TABLE IF EXISTS public.comfyui_models;
