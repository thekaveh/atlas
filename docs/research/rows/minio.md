---
service: minio
category: data
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://docs.weaviate.io/weaviate/configuration/backups
  - https://docs.n8n.io/integrations/builtin/credentials/s3/
  - https://min.io/docs/minio/linux/administration/monitoring/bucket-notifications.html
  - https://min.io/docs/minio/linux/administration/object-management/object-lifecycle-management.html
  - https://docs.lakekeeper.io/docs/latest/concepts/
  - https://superset.apache.org/user-docs/databases/
  - https://docs.dagster.io/deployment/oss/deployment-options/docker
  - services/minio/service.yml
  - services/minio/init/scripts/init-minio.sh
  - services/minio/README.md
  - services/weaviate/service.yml
---

# minio — Integration Research

## 1. Missing-pair integrations

- **minio ↔ backend**
  - Why valuable: `minio-init` already provisions a `backend` bucket plus scoped keys, but FastAPI never consumes them — large blobs, model checkpoints, and embedding caches have nowhere durable to land.
  - Mechanism sketch: boto3 client against `http://minio:9000` with `MINIO_BACKEND_ACCESS_KEY`/`SECRET_KEY`, path-style addressing (recipe in `services/minio/README.md`).
  - Effort: small
  - Risks / open questions: backend lacks a storage abstraction; need MinIO-vs-Supabase-Storage routing rules.
  - Confidence: high (credentials and bucket exist in `init-minio.sh`; only consumer wiring is absent).

- **minio ↔ n8n**
  - Why valuable: the `n8n` bucket and keys are pre-provisioned, and n8n ships a first-party S3 node with custom-endpoint support — workflows could persist files without hitting Supabase Storage's 50 MB ceiling, and it unlocks the workflow-artifact-handoff teased in `services/open-webui/README.md`.
  - Mechanism sketch: n8n S3 credential pointing at `http://minio:9000`; optional `N8N_EXTERNAL_BINARY_DATA_MODE=s3` for binary offload.
  - Effort: small
  - Risks / open questions: binary-data mode needs `N8N_BINARY_DATA_TTL` tuning; credential injected as env var or workflow secret?
  - Confidence: high (n8n S3 node docs list DigitalOcean Spaces/Wasabi — same path covers MinIO).

- **minio ↔ comfyui**
  - Why valuable: ComfyUI outputs sit in an ephemeral volume; a `comfyui` bucket already exists. Persisting renders lets backend/n8n/open-webui share artifacts and survives `./stop.sh --cold`.
  - Mechanism sketch: post-generation hook (custom node or sidecar) uploads `output/` to `s3://comfyui/` via `MINIO_COMFYUI_*` keys.
  - Effort: medium
  - Risks / open questions: no first-party ComfyUI S3 node — need community node or sidecar; large PNG/MP4 outputs stress single-host MinIO.
  - Confidence: medium (bucket pre-provisioned; upstream lacks an official S3 sink).

- **minio ↔ jupyterhub**
  - Why valuable: notebooks need a durable, sharable dataset tier outside the per-user volume. The `jupyter` bucket and keys exist.
  - Mechanism sketch: inject `MINIO_JUPYTER_*` + `AWS_S3_ENDPOINT=http://minio:9000` into singleuser env via DockerSpawner; expose via `s3fs`/`boto3`.
  - Effort: small
  - Risks / open questions: shared single-tenant key — multi-user isolation needs per-user STS or sub-policies.
  - Confidence: high (credentials exist; standard JupyterHub S3 pattern).

- **minio ↔ weaviate**
  - Why valuable: Weaviate explicitly supports MinIO as `backup-s3` (upstream docs). Stack has no Weaviate backup story today — class data is lost on volume wipe.
  - Mechanism sketch: enable `backup-s3` in `WEAVIATE_ENABLE_MODULES`, set `BACKUP_S3_BUCKET=weaviate-backups`, `BACKUP_S3_ENDPOINT=minio:9000`, `BACKUP_S3_USE_SSL=false`; add a sixth consumer entry in `init-minio.sh`.
  - Effort: small
  - Risks / open questions: backup scheduling (manual API call vs cron sidecar) is undecided.
  - Confidence: high (Weaviate docs: "Works with AWS S3 and S3-compatible services (e.g., MinIO)").

- **minio ↔ doc-processor**
  - Why valuable: docling parses have no persistent landing zone; the `docling` bucket is unused, blocking backend/Weaviate-ingest/n8n RAG flows from finding outputs at stable URIs.
  - Mechanism sketch: doc-processor writes parsed payloads to `s3://docling/<source-hash>/` via `MINIO_DOCLING_*` keys.
  - Effort: small
  - Risks / open questions: object-key convention (content-hash vs upload-id) needs locking down before consumers depend on it.
  - Confidence: high (bucket + credentials already provisioned; only upload path is missing).

## 2. Candidate new services

- **Langfuse** → `../candidates/langfuse.md`
  - Headline: LLM observability platform that uses S3 (MinIO) for long-term trace/blob storage.
  - Other consumers in stack: litellm, hermes, backend, open-webui, local-deep-researcher.

- **Apache Iceberg + DuckDB** → `../candidates/iceberg-duckdb.md`
  - Headline: Open table format on top of MinIO that gives the stack a queryable analytics tier.
  - Other consumers in stack: jupyterhub, backend, n8n.

- **Lakekeeper** → `../candidates/lakekeeper.md`
  - Headline: Managed Iceberg REST catalog candidate for future warehouse governance, vended credentials, and multi-engine catalog pressure.
  - Other consumers in stack: iceberg-rest replacement path, spark, trino, jupyterhub, zeppelin, airflow.

- **Apache Superset** → `../candidates/superset.md`
  - Headline: Analyst BI dashboards over the MinIO-backed lakehouse through Trino after datasets and SSO exist.
  - Other consumers in stack: trino, iceberg-rest or lakekeeper, supabase/postgres analytics, root dashboard.

- **Dagster** → `../candidates/dagster.md`
  - Headline: Asset-aware orchestration for MinIO/Iceberg and ML asset graphs once the Airflow boundary is explicit.
  - Other consumers in stack: airflow, spark, trino, mlflow, label-studio.

## 3. Per-service feature gaps

- **Bucket notifications (webhook / Redis / NATS targets)** — Why pursue: MinIO can POST object-created events to a webhook or Redis stream; would let backend/n8n/Weaviate react to uploads (auto-ingest docling output into Weaviate, trigger n8n workflows on n8n-bucket drops) instead of polling. Effort: medium.
- **Object lifecycle rules (expiration + versioning)** — Why pursue: `comfyui` and `jupyter` buckets will grow unbounded; per-bucket ILM rules (expire after N days, keep N versions) are a one-shot `mc ilm` config in `init-minio.sh`. Effort: small.
- **Server-side encryption (SSE-S3 / SSE-KMS)** — Why pursue: stack stores secrets and user uploads in plaintext on the host volume; SSE-S3 with auto-generated KEK gives at-rest encryption without consumer changes. Effort: medium.
- **STS / AssumeRole for per-user JupyterHub creds** — Why pursue: replaces the single shared `MINIO_JUPYTER_*` credential with short-lived tokens scoped per notebook user, addressing the multi-tenant gap noted above. Effort: large.
