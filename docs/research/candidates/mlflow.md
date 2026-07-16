---
category-fit: apps
generated: 2026-05-19
license: Apache-2.0
name: MLflow
referenced-by: [backend, jupyterhub]
slug: mlflow
type: external-service
upstream: https://github.com/mlflow/mlflow
---

# MLflow

## 1. Headline
A self-hosted experiment-tracking, run-history, and model-registry server that plugs notebook training loops into Supabase Postgres + MinIO artifact storage.

## 2. Problem it solves
Notebook-driven experiments today have no durable record beyond the cell output buffer: parameters, metrics, and model binaries vanish when the kernel dies. Backend + n8n workflows that "promote a model" have nowhere canonical to fetch its weights from. MLflow gives the stack a versioned run history, side-by-side metric comparison, and an addressable model registry that the rest of the stack can pull from.

## 3. Stack wiring sketch
- jupyterhub → mlflow via `MLFLOW_TRACKING_URI=http://mlflow:5000` (added to jupyterhub's `environment_adaptation`).
- mlflow → supabase via `MLFLOW_BACKEND_STORE_URI=postgresql://...@supabase:5432/mlflow` (new schema in the existing Postgres).
- mlflow → minio via `MLFLOW_S3_ENDPOINT_URL=http://minio:9000` for artifact persistence (run files, model binaries).
- backend → mlflow via the same tracking URI to fetch promoted models for inference endpoints.
- n8n → mlflow via the REST API (`/api/2.0/mlflow/...`) to gate workflows on registry stage transitions.

## 4. Effort
medium — One new compose fragment + manifest, a one-time `mlflow` schema migration in Supabase, a MinIO bucket policy, and notebook docs. No model-side code changes needed.

## 5. Risks & open questions
- Authentication: MLflow's built-in auth is basic-auth only; Kong-fronted routing + bearer token is preferable but needs design.
- Storage growth: artifact bucket can balloon; lifecycle policy on MinIO is in-scope.
- Schema coupling: sharing the Supabase Postgres means migrations need coordination (separate schema mitigates this).
- Overlap with Weaviate "experiments" use cases is minimal but worth documenting.

## 6. Why now (and why not sooner)
The stack only recently gained MinIO (S3-compatible artifacts) and stabilized Supabase as the always-on Postgres — the two prerequisites MLflow needs to run as a server rather than a file-backed embedded store. Before those, MLflow would have required its own dedicated DB and bucket store, doubling infra. Now it's a thin compose addition.

## 7. Upstream evidence
- https://github.com/mlflow/mlflow — README confirms Postgres backend store and S3 artifact store as first-class configurations.
- https://mlflow.org/docs/latest/tracking.html — tracking-server reference (default port 5000, `MLFLOW_TRACKING_URI`).

## 8. Cross-references
- Referenced from `docs/research/rows/backend.md` — backend chooses LangMem extraction/embedding models and ComfyUI checkpoints with no run history; MLflow provides the registry plus an artifact path through MinIO.
