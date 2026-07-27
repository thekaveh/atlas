# 5.2.31. MinIO

## 1. Overview

S3-compatible object storage for the artifact tier of the stack. Complements Supabase Storage rather than replacing it: Supabase Storage stays the app-tier surface (row-level-security uploads, signed URLs, ≤50 MB files); MinIO is the artifact-tier surface for high-throughput, large-blob workloads.

## 2. Endpoints

| Surface | URL | Notes |
|---|---|---|
| Admin console (Kong alias) | `http://minio.localhost:${KONG_HTTP_PORT}` | **Use this from your browser.** Requires `./start.sh --setup-hosts` so `minio.localhost` resolves to `127.0.0.1`. Login `minioadmin` / `${MINIO_ROOT_PASSWORD}`. |
| Admin console (direct port) | `http://localhost:${MINIO_CONSOLE_PORT}` (default `63021`) | Equivalent; no hosts setup required. |
| S3 API (host port) | `http://localhost:${MINIO_PORT}` (default `63020`) | **Recommended for s3 clients** (no proxy hop). Stable per `BASE_PORT`. See §2.1. |
| S3 API (Kong alias) | `http://s3.minio.localhost:${KONG_HTTP_PORT}` | Friendly, `BASE_PORT`-independent host. Requires `./start.sh --setup-hosts`. Proxies to `minio:9000` with `preserve_host` so S3 SigV4 validates. |
| S3 API (internal) | `http://minio:9000` | What sibling containers (backend, n8n, ComfyUI, JupyterHub, docling consumers) call via the per-bucket service-account credentials. |
| Admin console (internal) | `http://minio:9001` | What Kong proxies for the console alias. |

Both Kong routes are generated in `bootstrapper/utils/kong_config_generator.py`, gated on `MINIO_SOURCE != disabled`, and use `preserve_host` so the browser/client keeps its real Host header (the console SPA builds correct redirect URLs; the S3 client's SigV4 signature still validates).

### 2.1. Connecting an external S3-compatible client (CLI, SDK, TUI)

Any S3-compatible tool — `aws` CLI, boto3, `mc`, `s3cmd`, rclone, or a
custom client — connects with these settings. Two endpoints work: the
direct host port (no proxy hop, best for heavy/upload traffic) or the
Kong alias (a stable, `BASE_PORT`-independent hostname; needs
`--setup-hosts`). Both reach the same S3 API and validate SigV4 signatures.

| Setting | Value | Source |
|---|---|---|
| Endpoint URL | `http://localhost:${MINIO_PORT}` (default `63020`) **or** `http://s3.minio.localhost:${KONG_HTTP_PORT}` | `MINIO_PORT` / Kong alias |
| Region | `us-east-1` | `MINIO_REGION` |
| Access key | `minioadmin` (full access), or a per-bucket key (scoped) | `MINIO_ROOT_USER` / `MINIO_<BUCKET>_ACCESS_KEY` |
| Secret key | `grep ^MINIO_ROOT_PASSWORD= .env` (full), or the per-bucket secret | `MINIO_ROOT_PASSWORD` / `MINIO_<BUCKET>_SECRET_KEY` |
| Addressing style | **path-style (required)** | localhost/IP endpoints can't use virtual-host style |
| TLS | none (`http://`) | the in-stack baseline serves plain HTTP |

The endpoint is **stable across restarts** for a given `BASE_PORT` (the
port is `BASE_PORT + 20` by default), so it's safe to hard-code in an
external tool's profile. Use the root credentials for browse-everything
access, or a per-bucket service-account key (see §5) to scope a tool to
one bucket.

## 3. Default credentials

- **Root user:** `MINIO_ROOT_USER` (default `minioadmin`)
- **Root password:** `MINIO_ROOT_PASSWORD` — auto-generated to `.env` on first `./start.sh`. Retrieve with `grep ^MINIO_ROOT_PASSWORD= .env`. Use these credentials to log into the admin console.

Root credentials are NEVER surfaced to consumers — see Service accounts below.

## 4. Bucket layout

Sixteen buckets are pre-provisioned by `minio-init` across thirteen built-in consumers. Bucket names are the bare service identifier unless overridden:

| Bucket | Intended consumer |
|---|---|
| `comfyui` | ComfyUI generated outputs |
| `backend` | Backend (FastAPI) large blobs / embeddings / model checkpoints |
| `n8n` | n8n workflow file inputs and outputs |
| `jupyter` | JupyterHub datasets and model artifacts |
| `docling` | Doc Processor parsed-document persistence |
| `langfuse` | Langfuse trace and media object storage |
| `mlflow` | MLflow experiment and model artifacts |
| `label-studio` | Label Studio import/export and annotation assets |
| `spark-history` | Spark history-server event logs |
| `lakehouse`, `jars`, `checkpoints`, `landing` | Iceberg lakehouse storage, Spark artifacts, checkpoints, and landing data |
| `raw-assets` | Shared input objects written by the scoped asset-ingest identity and accepted by Asset Worker and Asset Baker reference routes |
| `asset-worker` | Asset Worker optimized GLB outputs |
| `asset-baker` | Asset Baker baked GLB and texture outputs |

Bucket names are overridable via `MINIO_BUCKET_<NAME>` env vars. The two
pre-existing processor output settings remain canonical as
`ASSET_WORKER_MINIO_BUCKET` and `ASSET_BAKER_MINIO_BUCKET`; provisioning and
runtime writes consume those same values, so renamed buckets remain aligned.

Parent-owned consumers can add their own bucket and scoped service account
without forking Atlas by passing `MINIO_EXTRA_CONSUMERS` into `minio-init` —
a space-separated list of `CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR` entries
(optionally extended with extra read/write bucket lists), with the referenced
variables supplied by the parent-owned `.env.user` or `ATLAS_ENV_USER_FILE`.
See [reusing-atlas.md](../../docs/deployment/reusing-atlas.md) for the full
grammar and a worked example; §6.1 below covers the newer declarative
`storage:` alternative.

## 5. Service accounts

Each consumer has its own MinIO service account with an inline IAM policy scoped to get/put/delete/list on its own bucket (or a small named set — the Iceberg account has four writable buckets; the shared `MINIO_ASSET_INGEST_*` identity can populate `raw-assets` without root access, and each asset processor can only read/list that shared bucket while writing to its own). Extra consumers declared via `MINIO_EXTRA_CONSUMERS` receive the same idempotent bucket, named policy, and inline service-account provisioning. The policy JSON is generated by the `minio-init` provisioning script.

Built-in credentials are auto-generated to `.env` and exposed as `MINIO_<NAME>_ACCESS_KEY` and `MINIO_<NAME>_SECRET_KEY` where `<NAME>` is one of the built-in consumers. Parent-owned extra consumer credentials are supplied by the parent overlay. A cross-bucket access attempt with a consumer credential returns `403 AccessDenied`.

## 6. Consumer integration recipe (for follow-up PRs)

A consumer connects with any standard S3 SDK or the `mc` CLI, using the
endpoint and per-bucket credentials from §2.1 and §5 (e.g. boto3 with
`Config(s3={"addressing_style": "path"})`, or `mc alias set` against the
host port).

### 6.1. Declarative consumer storage contract (`storage:`)

A downstream consumer (see [reusing-atlas.md](../../docs/deployment/reusing-atlas.md))
can declare object stores in its `atlas.consumer.yml` `storage:` block instead
of hand-writing a `minio-init` compose override. Atlas compiles each declared
store to the existing `MINIO_EXTRA_CONSUMERS` grammar, provisions a scoped
service-account credential, and generates the `minio-init` overlay
automatically — no consumer compose override is required. Each store exports
stable `ATLAS_STORE_<KEY>_*` fields (bucket, internal/public endpoints,
region, credential variable names). Full schema and field reference:
[reusing-atlas.md](../../docs/deployment/reusing-atlas.md).

### 6.2. Browser-safe presigned URLs (sign against the public host)

Presigned-URL signatures cover the request **host**, so signing against the
internal endpoint (`minio:9000`) and then rewriting the URL to the public host
produces an invalid signature. **Never rewrite a signed URL** — sign directly
against the browser-visible public endpoint (e.g. boto3's `endpoint_url` set
to the public base before calling `generate_presigned_url`). Atlas also ships
a dependency-free reference presigner in `bootstrapper/utils/s3_presign.py`.

## 7. Source variants

`MINIO_SOURCE` may be:

- `container` (default) — run MinIO in a Docker Compose container
- `disabled` — turn MinIO off (`MINIO_SCALE=0`); the service is not scheduled

`localhost` and `external` variants are not provided in this release.

## 8. Data persistence

MinIO data lives in the `${PROJECT_NAME}-minio-data` named Docker volume mounted at `/data`. `./stop.sh --cold` removes this volume.

## 9. Operations

- **Add a bucket manually:** `mc mb local/<bucket>` from a host with `mc` and the root alias configured.
- **Add a parent-owned consumer bucket:** set `MINIO_EXTRA_CONSUMERS` plus the referenced bucket/access/secret variables in a `_user` compose overlay and parent-owned env overlay, then run `docker compose up --force-recreate minio-init` or restart Atlas.
- **Rotate a service-account key:** edit `MINIO_<NAME>_ACCESS_KEY` and `MINIO_<NAME>_SECRET_KEY` in `.env`, then run `docker compose up --force-recreate minio-init` to re-provision.
- **Logs:** `docker logs ${PROJECT_NAME}-minio` and `docker logs ${PROJECT_NAME}-minio-init`.

## 10. Dependencies & Integrations

### 10.1. Current — Upstream (this service calls)

_No upstream calls._

### 10.2. Current — Downstream (services that call this)

| Service | Category |
|---|---|
| backup | infra |
| kong | infra |
| langfuse | infra |
| prometheus | infra |
| iceberg-rest | data |
| spark | data |
| trino | data |
| asset-baker | media |
| asset-worker | media |
| airflow | agents |
| celery | agents |
| backend | apps |
| jenkins | apps |
| jupyterhub | apps |
| label-studio | apps |
| mlflow | apps |
| zeppelin | apps |

### 10.3. Architecture diagram

![minio architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 10.4. Future — Missing pair integrations

- **minio ↔ backend (general artifact API)** — *Why:* Backend RAG ingestion now reads consumer-declared corpora with each store's scoped MinIO account, but the built-in `backend` bucket is not yet a general destination for large blobs, model checkpoints, or embedding caches. *Mechanism:* add an artifact client at `http://minio:9000` using `MINIO_BACKEND_ACCESS_KEY`/`SECRET_KEY`, with upload/download routes and path-style addressing. *Effort:* small. *Confidence:* high.
- **minio ↔ n8n** — *Why:* the `n8n` bucket and keys are pre-provisioned, and n8n ships a first-party S3 node with custom-endpoint support; workflows could persist files without hitting Supabase Storage's 50 MB ceiling. *Mechanism:* n8n S3 credential at `http://minio:9000`; optional `N8N_EXTERNAL_BINARY_DATA_MODE=s3`. *Effort:* small. *Confidence:* high.
- **minio ↔ weaviate** — *Why:* Weaviate explicitly supports MinIO as `backup-s3` (upstream docs). Stack has no Weaviate backup story today. *Mechanism:* enable `backup-s3` in `WEAVIATE_ENABLE_MODULES`, set `BACKUP_S3_BUCKET=weaviate-backups`, `BACKUP_S3_ENDPOINT=minio:9000`, `BACKUP_S3_USE_SSL=false`; add `weaviate-backups` entry in `init-minio.sh`. *Effort:* small. *Confidence:* high.
- **minio ↔ comfyui** — *Why:* ComfyUI outputs sit in an ephemeral volume; a `comfyui` bucket exists. Persisting renders lets backend/n8n/open-webui share artifacts across `./stop.sh --cold`. *Mechanism:* post-generation hook (custom node or sidecar) uploads `output/` to `s3://comfyui/` via `MINIO_COMFYUI_*`. *Effort:* medium. *Confidence:* medium.
- **minio ↔ doc-processor** — *Why:* docling parses have no persistent landing zone; the `docling` bucket is unused, blocking downstream RAG flows from finding outputs at stable URIs. *Mechanism:* doc-processor writes payloads to `s3://docling/<source-hash>/` via `MINIO_DOCLING_*` keys. *Effort:* small. *Confidence:* high.

### 10.5. Future — Candidate new services

- **DuckDB** ([details](../../docs/research/candidates/iceberg-duckdb.md)) — *Headline:* embedded analytics engine that queries the shipped `iceberg-rest` tables on MinIO directly, giving the stack a fast in-process SQL tier over object storage. *Wires into:* jupyterhub, backend, n8n.

### 10.6. Future — Unused features in this service

- **Bucket notifications (webhook/Redis/NATS targets)** — *Why pursue:* MinIO can POST object-created events to a webhook or Redis stream; would let backend/n8n/Weaviate react to uploads instead of polling. *Effort:* medium.
- **Object lifecycle rules (expiration + versioning)** — *Why pursue:* `comfyui` and `jupyter` buckets will grow unbounded; per-bucket ILM rules (expire after N days, keep N versions) are a one-shot `mc ilm` config in `init-minio.sh`. *Effort:* small.
- **Server-side encryption (SSE-S3 / SSE-KMS)** — *Why pursue:* stack stores secrets and user uploads in plaintext on the host volume; SSE-S3 with auto-generated KEK gives at-rest encryption without consumer changes. *Effort:* medium.
- **STS / AssumeRole for per-user JupyterHub creds** — *Why pursue:* replaces the single shared `MINIO_JUPYTER_*` credential with short-lived per-user tokens. *Effort:* large.

## 11. Troubleshooting

- **`SignatureDoesNotMatch`** — most often clock skew between host and container. Sync your host clock.
- **Browser-based S3 client fails with CORS** — MinIO's default CORS config rejects unrecognized origins. Configure via `mc admin config` if browser uploads are required.
- **`403 AccessDenied`** — confirm the consumer credential's scoped policy matches the target bucket. Use root credentials to inspect: `mc admin policy info local <consumer>-policy`.
- **Cross-path-style failures** — MinIO requires path-style addressing. In boto3 use `Config(s3={"addressing_style": "path"})`.
- **`minio` container restart-loops** — typically `MINIO_ROOT_PASSWORD` is empty. Confirm `.env` has it populated; if blank, delete the line and re-run `./start.sh` (the bootstrapper will regenerate).
