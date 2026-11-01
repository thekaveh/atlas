---
service: jupyterhub
category: apps
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://github.com/jupyterhub/jupyterhub
  - https://github.com/jupyterhub/dockerspawner
  - https://github.com/jupyterlab/jupyter-ai
  - https://jupyterhub.readthedocs.io/en/stable/reference/authenticators.html
  - services/jupyterhub/service.yml
  - services/jupyterhub/build/requirements.txt
  - services/jupyterhub/README.md
  - services/minio/service.yml
  - services/backend/service.yml
  - services/hermes/service.yml
  - services/openclaw/service.yml
  - services/local-deep-researcher/service.yml
---

# jupyterhub — Integration Research

## 1. Missing-pair integrations

- **jupyterhub ↔ minio**
  - Why valuable: Notebooks routinely need durable artifact storage (datasets, model weights, parquet shards, plots). Today the only persistent surface is a Docker volume on `/home/jovyan/work`; sharing artifacts with backend, n8n, or ComfyUI requires copying through Supabase blobs.
  - Mechanism sketch: Inject `AWS_S3_ENDPOINT_URL=http://minio:9000`, `AWS_ACCESS_KEY_ID=${MINIO_ROOT_USER}`, `AWS_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}`, plus pre-install `s3fs`/`boto3` so `pd.read_parquet("s3://bucket/...")` and `fsspec` Just Work.
  - Effort: small (env wiring + two requirements.txt entries; manifest already has `runtime_adaptive` precedent).
  - Risks / open questions: Bucket-creation responsibility (notebook vs init container); root credentials vs scoped service account.
  - Confidence: high (MinIO is S3-API compatible and s3fs/boto3 both accept `endpoint_url`; pattern is widely deployed).

- **jupyterhub ↔ backend**
  - Why valuable: The FastAPI backend already aggregates LiteLLM, Weaviate, Neo4j, ComfyUI, and Hermes behind one API surface. Notebooks reimplementing those calls duplicates logic and skips backend-side auth/observability.
  - Mechanism sketch: Adaptive env `BACKEND_BASE_URL=http://backend:8000` so notebooks can `httpx.get(BACKEND_BASE_URL + "/v1/...")` instead of hand-rolling per-upstream clients.
  - Effort: small (one env var via `environment_adaptation`; backend already exposes the routes).
  - Risks / open questions: Sample notebook 00 should advertise both layers (low-level + backend) to avoid confusion.
  - Confidence: high (both services are container-only and already share the compose network).

- **jupyterhub ↔ hermes**
  - Why valuable: Hermes-agent is the stack's tool-using agent runtime; researchers want to drive it from a notebook (chain prompts, inspect intermediate tool calls) without going through Open WebUI.
  - Mechanism sketch: Hermes is already a `litellm` model alias (`hermes-agent`), so reachable today via the existing `OPENAI_API_BASE`. Add `HERMES_AGENT_MODEL=hermes-agent` env hint + a sample notebook (`07_hermes_agent.ipynb`).
  - Effort: small (env hint + notebook; no new wire).
  - Risks / open questions: Hermes self-loop guard (see the [Hermes README](../../../services/hermes/README.md)) — notebook must use `hermes-agent`, not raw upstream.
  - Confidence: high (LiteLLM gateway path is already proven from open-webui).

- **jupyterhub ↔ local-deep-researcher**
  - Why valuable: LangGraph deep-research runs are long; researchers want to kick off a job from a notebook and poll/stream results into a dataframe for further analysis.
  - Mechanism sketch: Add `DEEP_RESEARCHER_BASE_URL=http://local-deep-researcher:2024` to the adaptive env block; provide a notebook calling LangGraph's `/runs/stream` SSE endpoint.
  - Effort: medium (env + sample notebook + SSE client snippet; depends on LDR exposing a stable run-management API).
  - Risks / open questions: LDR's current API surface stability; whether long jobs survive notebook kernel restarts.
  - Confidence: medium (LangGraph upstream supports the runs API, but our wrapper may not all be exposed).

- **jupyterhub ↔ openclaw**
  - Why valuable: Long-running notebook jobs (training, sweeps, embeddings) finish unattended; a Slack/Discord ping via openclaw closes the loop without leaving Jupyter.
  - Mechanism sketch: Inject `OPENCLAW_WEBHOOK_URL=http://openclaw-gateway:<port>/webhook/notify`; a 5-line helper in a util notebook posts JSON when a cell finishes.
  - Effort: small (one env + helper snippet).
  - Risks / open questions: Openclaw's outbound channel must already be configured; payload schema needs documenting.
  - Confidence: medium (openclaw is in-network, but its webhook surface is less battle-tested than Slack-native flows).

## 2. Candidate new services

- **MLflow** → `../candidates/mlflow.md`
  - Headline: Experiment, run, and model-registry tracking server backed by Supabase Postgres + MinIO artifacts.
  - Other consumers in stack: backend, n8n, hermes.

- **Label Studio** → `../candidates/label-studio.md`
  - Headline: Open-source data-annotation UI for building supervised datasets consumed by notebooks and Weaviate ingestion.
  - Other consumers in stack: backend, weaviate, minio.

## 3. Per-service feature gaps

- **Actual multi-user JupyterHub (DockerSpawner + an Authenticator)** — Today the container is `jupyter/datascience-notebook` (single-user Lab), despite the service being named "jupyterhub". A real Hub with `DockerSpawner` and `NativeAuthenticator`/OAuth would let multiple humans share the stack. Effort: large.
- **Jupyter AI extension wired to LiteLLM** — `jupyter-ai` exposes `%ai` magics and chat, accepting any OpenAI-compatible base URL; pointing it at `LITELLM_BASE_URL` gives every model in the gateway as a first-class notebook magic. Effort: small.
- **GPU enablement for the notebook container** — Image already ships PyTorch + PyG; manifest exposes no `container-gpu` source, so heavy training falls back to CPU even when the host has a GPU. Effort: medium.
- **jupyter-server-proxy for ComfyUI/n8n** — `jupyter-server-proxy` is already in requirements.txt but unused; mounting ComfyUI and n8n behind `/proxy/<service>/` would let notebooks embed those UIs in iframes without leaving the lab. Effort: small.
- **Persistent kernel state via ipykernel + ipyparallel** — Long-running RAG/agent loops lose state on kernel restart; an `ipyparallel` cluster (workers as sidecars) would survive restarts. Effort: medium.
