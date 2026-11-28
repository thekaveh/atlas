---
service: backend
category: apps
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/backend/service.yml
  - services/backend/compose.yml
  - services/backend/app/app/main.py
  - services/backend/app/app/requirements.txt
  - services/silo/init/scripts/init-silo.sh
  - services/silo/service.yml
  - services/hermes/compose.yml
  - services/hermes/README.md
  - services/jupyterhub/service.yml
  - services/backend/README.md
---

# backend — Integration Research

## 1. Missing-pair integrations

- **backend ↔ silo**
  - Why valuable: `silo-init` already provisions a dedicated `backend` bucket plus a scoped `SILO_BACKEND_ACCESS_KEY`/`SECRET_KEY` service account, but the backend container receives none of the `SILO_*` env vars and ships no S3 client. Artifact-tier storage (research outputs, ComfyUI image cache, large user uploads) currently spills into Supabase Storage, which is sized for app data, not blobs.
  - Mechanism sketch: pass `SILO_ENDPOINT=http://silo:9000`, `SILO_BUCKET_BACKEND`, and the backend access/secret keys into `services/backend/compose.yml`; add `boto3` (or the `silo` py SDK) to `requirements.txt`; expose `POST /storage/artifact` and `GET /storage/artifact/{key}` next to the existing Supabase `/storage/upload`.
  - Effort: small
  - Risks / open questions: dual-storage UX (which backend goes to Supabase vs Silo?); presigned-URL TTL policy; CORS for browser direct-upload.
  - Confidence: high (the bucket + creds are visibly declared in `services/silo/init/scripts/init-silo.sh:28`).

- **backend ↔ hermes**
  - Why valuable: `HERMES_ENDPOINT` and `HERMES_API_KEY` are already passed into the backend container, but no Python client or FastAPI endpoint consumes them. Talking to Hermes only through the LiteLLM `hermes-agent` model loses Hermes-native surfaces (skill/tool registration, session state at `/opt/data`, dashboard introspection).
  - Mechanism sketch: add `hermes_client.py` next to `n8n_client.py`; call Hermes API at `${HERMES_ENDPOINT}/v1/sessions` and `/skills` with `Authorization: Bearer ${HERMES_API_KEY}`; expose `POST /agents/hermes/run` and `GET /agents/hermes/sessions/{id}`.
  - Effort: small
  - Risks / open questions: Hermes upstream API surface is sparsely documented in-repo; need to validate session schema against the upstream container before wiring.
  - Confidence: medium (env wiring exists in `services/backend/compose.yml:62-63` and `services/hermes/README.md` confirms a separate API port, but exact JSON schema needs upstream confirmation).

- **backend ↔ jupyterhub**
  - Why valuable: notebook users currently can't reach backend's research/memory/ComfyUI APIs except through Kong + tokens — and the backend has no view into JupyterHub state. A thin bridge enables programmatic notebook launches for batch evaluations (e.g. ranking research outputs, replaying memory consolidations).
  - Mechanism sketch: backend calls JupyterHub REST at `http://jupyterhub:8000/hub/api` using `Authorization: token ${JUPYTERHUB_TOKEN}`; expose `POST /notebooks/users/{name}/server` and `GET /notebooks/users` proxies; share `SILO_BUCKET_JUPYTER` for artifact handoff.
  - Effort: medium
  - Risks / open questions: JupyterHub admin token rotation; auth model mismatch (backend uses Supabase JWT, JupyterHub uses its own token); spawner type may not support headless start.
  - Confidence: medium (JupyterHub manifest exists and exposes `JUPYTERHUB_TOKEN`; upstream REST API is well-known but not verified in-repo).

- **backend ↔ neo4j (knowledge-graph endpoints)**
  - Why valuable: `neo4j`, `langchain-neo4j`, and `NEO4J_URI`/`USER`/`PASSWORD` are all installed and injected, but the backend exposes zero graph endpoints. LangMem facts and research sources are natural graph citizens (user → fact → source → entity), and pairing the existing pgvector LangMem layer with a Neo4j-backed entity graph would let the research and memory APIs answer "why" questions, not just "what".
  - Mechanism sketch: add `graph_service.py`; on memory-extract, mirror canonical entities into Neo4j via `bolt://neo4j-graph-db:7687`; expose `GET /memory/user/{id}/graph` and `GET /research/{session_id}/entities`.
  - Effort: medium
  - Risks / open questions: dual-write consistency between pgvector and Neo4j; schema design (who owns entity canonicalization); cost of synchronous bolt calls in request path.
  - Confidence: high (driver + env wiring already present in `services/backend/app/app/requirements.txt:43` and `services/backend/compose.yml:44-46`).

## 2. Candidate new services

- **Langfuse** → `../candidates/langfuse.md`
  - Headline: Self-hostable LLM observability — traces, evals, prompt versioning — that drops in between backend/hermes/n8n and LiteLLM.
  - Other consumers in stack: hermes, n8n, local-deep-researcher, litellm, open-webui

- **Celery + Flower** → `../candidates/celery-flower.md`
  - Headline: Redis-backed async worker tier so backend's long-running research, memory-consolidate, and ComfyUI generation calls stop blocking the FastAPI request loop.
  - Other consumers in stack: redis, supabase, comfyui, local-deep-researcher

- **MLflow** → `../candidates/mlflow.md`
  - Headline: Experiment tracking + model registry for the LangMem extraction/embedding models, ComfyUI checkpoints, and Hermes skill evaluations.
  - Other consumers in stack: jupyterhub, comfyui, hermes, silo

## 3. Per-service feature gaps

- **LangMem auto-consolidate scheduler** — Why pursue: `LANGMEM_AUTO_CONSOLIDATE` and `LANGMEM_CONSOLIDATION_INTERVAL` are declared, `apscheduler` is in `requirements.txt`, but no scheduler runs in `main.py`. Effort: small.
- **STT/TTS proxy endpoints** — Why pursue: `STT_ENDPOINT` and `TTS_ENDPOINT` reach the container but the FastAPI surface exposes neither — clients must hit the engines directly, bypassing auth/quota. Effort: small.
- **Supabase Realtime channels** — Why pursue: `supabase-realtime` is a `depends_on` of backend yet no WebSocket fan-out endpoints exist for streaming research logs or memory updates. Effort: medium.
- **Per-user storage namespacing** — Why pursue: `/storage/upload` accepts a `bucket` query but no per-user prefix or quota; trivial to abuse. Effort: small.
