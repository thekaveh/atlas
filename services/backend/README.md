# 5.2.4. Backend API (FastAPI)

Always-on adaptive FastAPI service that orchestrates the rest of the stack. It is the only "apps"-tier service that explicitly declares itself as a hub: at runtime it calls Supabase (Postgres + Storage), Weaviate, LiteLLM, ComfyUI, n8n, Ray, Local Deep Researcher, and the optional Celery worker tier; Neo4j/Hermes env wiring is injected for future use but unconsumed by backend code today (STT/TTS/doc-processor likewise sit behind "future proxy" env). Health checks, LangMem-backed long-term memory, async jobs, file uploads, and orchestration endpoints all live here.

The backend is `_SOURCE`-trivial — it has only one variant, `container` — because nothing in the design contemplates running FastAPI off-stack or as an external dependency. Instead, the variability lives in *what* the backend talks to: adaptive logic in `runtime_adaptive.backend.adapts_to` flips capabilities on or off based on the active `LLM_PROVIDER_SOURCE`, `WEAVIATE_SOURCE`, `STT_PROVIDER_SOURCE`, `TTS_PROVIDER_SOURCE`, `DOC_PROCESSOR_SOURCE`, `TIKA_SOURCE`, `RAY_SOURCE`, `LIGHTRAG_SOURCE`, `SUPAVISOR_SOURCE`, and `OTEL_COLLECTOR_SOURCE`.

## 1. Overview

Source: `services/backend/app/`. The FastAPI app boots in `app/main.py`, mounts feature routes (`/memory`, `/research`, `/storage`, `/health`, `/ready`, `/workflows`, `/media/*`, `/comfyui/*`, `/api/ray/*`, `/api/chunk`, `/api/rag/evaluate`, `/api/rag/ingestions`), and reads adaptive env vars at startup. LangMem (LangChain's long-term-memory layer) is bundled in: `LANGMEM_ENABLED=true` by default, with extraction/embedding models resolved from `LITELLM_DEFAULT_MODEL` / `LITELLM_EMBEDDING_MODEL` (set by `litellm-init` from the YAML catalog + env). Chonkie powers `/api/chunk` so n8n, notebooks, and downstream services can request token, recursive, or semantic text chunks through the Backend rather than importing the library independently. Ragas powers `/api/rag/evaluate` so callers can score supplied questions, answers, contexts, and optional references through Atlas-owned LiteLLM routing instead of adding evaluator packages to each service. A small pytest suite lives at `app/app/tests/` (Ray client/routes, chunking service/API tests, Ragas contract/API tests, and the RAG ingestion engine/API tests; run in the required CI job). Runtime dependencies live in `app/requirements.txt`; pytest and its plugins live in `app/requirements-dev.txt` and are installed only by test environments. Local iteration is edit-in-place — the compose fragment bind-mounts `./app/app` onto `/app`. The dev auto-reloader is opt-in: set `BACKEND_DEV_RELOAD=true` to run `uvicorn[standard] --reload` so host-side source edits hot-reload; it is off by default to avoid bind-mounted plugin-directory git churn restarting or crash-looping the backend. With reload off, apply changes by recreating the backend: `docker compose up -d --force-recreate backend` (also required after runtime dependency changes; test-only dependency changes don't affect the production image).

## 2. Access

| Path | URL | Notes |
|---|---|---|
| Direct | `http://localhost:${BACKEND_PORT}` (default `63093`) | Always exposed when the container is up; application authentication is identical to Kong access. |
| Kong | `http://api.localhost:${KONG_HTTP_PORT}` | Requires `./start.sh --setup-hosts`. Kong policy is an optional outer gate; application identity remains required on protected routes. |
| Public diagnostics | `GET /`, `GET /health`, `GET /ready`, `GET /metrics`, API schema/docs | No bearer token. `/health` is process liveness; `/ready` probes PostgreSQL, Redis, and LiteLLM and returns `503` until all are available. Do not publish metrics or schema routes beyond the intended network boundary. |
| Chunking | `POST /api/chunk` | Chonkie-backed splitting; accepts a Supabase user JWT, the internal-service token, or the scoped notebook token. |
| RAG evaluation | `POST /api/rag/evaluate` | Ragas-backed metrics; accepts the same stateless-route credentials as chunking. |
| RAG ingestion | `POST /api/rag/ingestions`, `GET /api/rag/ingestions[/{id}]`, `POST /api/rag/ingestions/{id}/cancel` | Internal-service only. Generic ingestion job over a consumer `rag_ingestion_profile` with machine-readable per-phase status. |
| Ray jobs | `POST /api/ray/jobs/submit`, `GET`/`DELETE /api/ray/jobs/{job_id}`, `/api/ray/cluster/status` | Requires `Authorization: Bearer ${RAY_JOB_API_TOKEN}` on direct and Kong access paths. |

Canonical port table: [Ports and Routes](../../docs/deployment/ports-and-routes.md).

## 3. Configuration

The backend has no source-variants beyond `container`. Customization happens through `.env` and through which upstream services are enabled.

```bash
BACKEND_SOURCE=container          # only value
BACKEND_PORT=63093                # computed by topology.py from BASE_PORT
```

Backend Kong route authentication:

```bash
BACKEND_KONG_AUTH=disabled        # disabled (default) or key-auth
BACKEND_KONG_API_KEY=             # auto-generated; send as apikey when key-auth is enabled
```

Application identity (required by default on every non-public route):

```bash
BACKEND_IDENTITY_AUTH=required
BACKEND_INTERNAL_API_TOKEN=       # auto-generated; full operator scope
BACKEND_N8N_API_TOKEN=            # auto-generated; n8n workflow scope
BACKEND_NOTEBOOK_API_TOKEN=       # auto-generated; stateless notebook routes only
BACKEND_OPEN_WEBUI_API_TOKEN=     # auto-generated; memory/legacy ComfyUI scope
COMFYUI_MAX_IMAGE_BYTES=20971520  # bounded ComfyUI image proxy response
COMFYUI_COMPLETION_TIMEOUT_SECONDS=300  # synchronous generation deadline
SUPABASE_JWT_SECRET=              # verifies authenticated Supabase user JWTs
```

Supabase user JWTs bind memory, research, hosted-media operations, and spend
reads to the JWT `sub`; caller-supplied user or consumer identifiers cannot
impersonate another subject. The internal token is the full operator
credential. Open WebUI and n8n use separate generated tokens that can delegate
identifiers only within the route families their bundled integrations need.
The notebook token is narrower still and is accepted only by
`/documents/extract`, `/api/chunk`, and `/api/rag/evaluate`. Operator surfaces
such as workflow administration, RAG ingestion, plugin inventory, and generic
jobs require the internal token. Ray and LightRAG adapter routes retain their
own dedicated machine tokens.

`BACKEND_IDENTITY_AUTH=disabled` is an explicit emergency rollback mode. It
removes the application identity boundary and must not be used on an exposed
deployment.

`BACKEND_KONG_AUTH=disabled` preserves the local-development default: Kong adds
only CORS to `api.localhost`. Set `BACKEND_KONG_AUTH=key-auth` before exposing
the gateway beyond a trusted workstation or private reverse proxy. In that
mode, Kong requires:

```bash
curl -H "Host: api.localhost" \
  -H "apikey: ${BACKEND_KONG_API_KEY}" \
  http://localhost:${KONG_HTTP_PORT}/health
```

The direct host port bypasses Kong's optional API-key gate, but it does not
bypass application identity. Bind host ports to loopback or firewall them in
shared environments because the public diagnostics remain reachable.

Ray job API authentication is independent of the optional Kong setting:

```bash
RAY_JOB_API_TOKEN=             # auto-generated during Atlas setup

curl -H "Authorization: Bearer ${RAY_JOB_API_TOKEN}" \
  http://localhost:${BACKEND_PORT}/api/ray/cluster/status
```

Every `/api/ray` route requires this bearer token, including requests through
the direct Backend port and deployments where `BACKEND_KONG_AUTH=disabled`.

LangMem long-term memory:

```bash
LANGMEM_ENABLED=true
LANGMEM_MEMORY_NAMESPACE=default
LANGMEM_AUTO_CONSOLIDATE=true
LANGMEM_CONSOLIDATION_INTERVAL=86400
LANGMEM_MAX_FACTS_PER_USER=1000
LANGMEM_EXTRACTION_MODEL=          # empty = LITELLM_DEFAULT_MODEL (resolved by litellm-init from YAML catalogs + env)
LANGMEM_EMBEDDING_MODEL=
```

Extraction runs the LLM call outside the database transaction, then commits accepted facts and the completed session atomically, with a per-user lock enforcing `LANGMEM_MAX_FACTS_PER_USER` across replicas. Failed extractions record a terminal failed session rather than partial facts. The full transaction and locking sequence is documented in the LangMem extraction module's docstring.

Memory writes (edits, soft deletes, consolidation, retention) mark a durable `vector_sync_pending` intent alongside the Postgres change, and a reconciliation pass syncs the corresponding Weaviate objects and clears the marker on success; failures remain retryable, and Postgres `is_active` stays the recall authority throughout. The deterministic-ID and stale-version comparison logic is documented in the vector-sync reconciliation module.

Graphiti temporal graph memory experiment:

```bash
GRAPHITI_ENABLED=false
GRAPHITI_GROUP_ID_PREFIX=atlas
GRAPHITI_DEFAULT_NAMESPACE=langmem
GRAPHITI_LLM_MODEL=                  # empty = LANGMEM_EXTRACTION_MODEL, then LITELLM_DEFAULT_MODEL
GRAPHITI_EMBEDDING_MODEL=            # empty = LANGMEM_EMBEDDING_MODEL, then LITELLM_EMBEDDING_MODEL
GRAPHITI_EXPOSE_TO_AGENTS=false
```

This is a backend-only evaluation scaffold, not a new service — LangMem remains the default and canonical memory API, and Graphiti is reserved as an optional temporal graph projection once a concrete backend workflow is chosen. `GRAPHITI_EXPOSE_TO_AGENTS=false` keeps Hermes/OpenClaw integration and the upstream Graphiti MCP server deferred.

Adaptive env (injected automatically based on active SOURCE values):

```bash
LITELLM_BASE_URL=http://litellm:4000
LITELLM_API_KEY=${LITELLM_MASTER_KEY}
WEAVIATE_URL=http://weaviate:8080
STT_ENDPOINT=...                  # resolved per STT_PROVIDER_SOURCE
TTS_ENDPOINT=...                  # resolved per TTS_PROVIDER_SOURCE
DOCLING_ENDPOINT=...              # resolved per DOC_PROCESSOR_SOURCE
HERMES_ENDPOINT=http://hermes:8642
HERMES_API_KEY=${HERMES_API_KEY}
NEO4J_URI=bolt://neo4j-graph-db:7687
NEO4J_USER=${GRAPH_DB_USER}
NEO4J_PASSWORD=${GRAPH_DB_PASSWORD}
KONG_URL=http://kong-api-gateway:8000
SUPABASE_SERVICE_KEY=${SUPABASE_SERVICE_KEY}
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
RAY_JOB_API_TOKEN=${RAY_JOB_API_TOKEN}
CELERY_SOURCE=disabled
CELERY_BROKER_URL=                 # auto-managed when CELERY_SOURCE=container
CELERY_RESULT_BACKEND=             # auto-managed when CELERY_SOURCE=container
RAG_INGESTION_EXECUTION_LEASE_SECONDS=30
```

Adaptive listing comes from `runtime_adaptive.backend.adapts_to` in `services/backend/service.yml`.

`POST /documents/extract` treats Docling and Tika as untrusted upstream boundaries: malformed success payloads fail validation rather than being indexed as empty documents, and the public route returns a stable generic error so provider details and document content never cross the API boundary. The exact required response shape is documented in the extraction route's code.

Hosted media gateway:

```bash
FAL_SOURCE=disabled
FAL_API_KEY=
FAL_MODEL=fal-ai/flux/dev
FAL_MODEL_LICENSE=fal/provider-terms
FAL_TIMEOUT_SECONDS=120
FAL_OUTPUT_FORMAT=jpeg
FAL_ENABLE_SAFETY_CHECKER=true
MEDIA_REQUEST_MAX_BYTES=41943040
MEDIA_INPUT_MAX_BYTES=26214400
MEDIA_INPUT_MAX_PIXELS=40000000
MEDIA_OPERATION_TTL_SECONDS=604800
```

When `FAL_SOURCE=enabled`, `POST /media/generate` accepts a provider-neutral request (`provider`, `modality`, `model`, `input`) and dispatches to FAL (image, image-to-3D) or the managed/localhost ComfyUI host (image); it returns `202` with an operation id, and `GET /media/operations/{operation_id}` polls normalized status/artifacts while `POST /media/operations/{operation_id}/cancel` requests cancellation without releasing the budget reservation until a terminal state is confirmed. `artifact_url` is provider-dependent — an absolute CDN URL for FAL, a gateway-relative backend proxy path for ComfyUI — so consumers must resolve relative URLs against their own base before fetching; the older `POST /comfyui/generate` route remains a narrower FAL-only compatibility surface. Set the request's top-level `timeout_seconds` above the provider's cold-start worst case — a cold Krea 2 BF16 load on the managed-MPS ComfyUI host is ~90–120 s before the first sampler step — or an otherwise-successful generation is timed out and cancelled mid-flight. The full field-by-field request/response contract, validation rules, and byte/pixel limits are served live at the backend's `/docs` (Swagger) endpoint.

**Spend ledger & budgets (`MEDIA_BUDGET_ENABLED`, disabled by default).** When enabled, each generation reserves its estimated cost before the provider is invoked and records an immutable ledger row in `public.media_spend_ledger` (Postgres), hard-stopping over-budget or provider-disabled requests before any provider call; `GET /media/spend` returns a consumer's committed/reserved totals. The full ledger schema, concurrency guarantees, and reconciliation behavior are served at the backend's `/docs` (Swagger) endpoint.

Chonkie chunking surface:

```bash
CHONKIE_SEMANTIC_EMBEDDING_MODEL=minishlab/potion-base-32M
```

`CHONKIE_SEMANTIC_EMBEDDING_MODEL` is an optional environment override read at
runtime; it is not surfaced in `.env.example` and defaults to
`minishlab/potion-base-32M` in code.

`POST /api/chunk` splits text using Chonkie's `recursive` (default), `token`, or
`semantic` strategy, returning stable offsets and chunk indexes; only token
chunking honors the requested `overlap`, since the current Chonkie APIs don't
expose overlap controls for the other strategies. The full request/response
schema is served at the backend's `/docs` (Swagger) endpoint.

Ragas evaluation surface:

```bash
RAGAS_EVALUATOR_MODEL=            # empty = LITELLM_DEFAULT_MODEL
RAGAS_EMBEDDINGS_MODEL=           # empty = LITELLM_EMBEDDING_MODEL
```

`POST /api/rag/evaluate` scores one or more question/answer/context records
against Ragas metrics (`faithfulness`, `answer_relevancy`, `context_precision`,
`context_recall`), routing evaluator model calls through LiteLLM;
`context_precision`/`context_recall` additionally require `ground_truth`. The
full request/response schema is served at the backend's `/docs` (Swagger)
endpoint.

## 4. Architecture & wiring

**Request flow (typical Open WebUI ↔ backend ↔ LiteLLM ↔ Ollama):**

1. Open WebUI sends a chat completion to Kong at `api.localhost/v1/...`.
2. Kong proxies to `backend:8000`.
3. Backend route either:
   - forwards directly to `litellm:4000/v1/chat/completions`, or
   - invokes a LangMem-augmented pipeline (retrieve facts from Supabase pgvector → enrich prompt → call LiteLLM → extract & store new facts).
4. LiteLLM dispatches to the registered provider (Ollama, Anthropic, OpenAI, etc.).

**Required hard dependencies** (from `depends_on.required`):
- `supabase` — Postgres (LangMem facts, public tables) and Storage (file uploads default to 100 MiB via `MAX_UPLOAD_BYTES`). The backend uses service credentials for outbound storage/database work and verifies inbound authenticated-user JWTs for user-scoped routes. Supabase Auth users are synchronized into `public.users` so research and memory foreign keys remain satisfiable.
- `redis` — declared required; `REDIS_URL` database 0 stores shared hosted-media operation state and RAG ingestion state, while the optional Celery worker tier uses Redis database 4 for async job broker/result state.
- `litellm` — gated `service_healthy` in compose; the Backend readiness endpoint probes the gateway's liveness endpoint.

**Optional adaptive dependencies** (from `runtime_deps.backend.optional`):
- `neo4j-graph-db`, `searxng`, `n8n`, `weaviate`, `parakeet`, `speaches`, `chatterbox`, `docling`, `tika`, `celery`.

When any optional service is `disabled`, the corresponding backend feature degrades gracefully — `/storage/upload` returns 503 if Supabase Storage is down, and `/research/start` persists sessions in Supabase while `research_client.py` creates LangGraph threads and runs them through `/threads/{id}/runs/stream` on the Local Deep Researcher service.

Research sessions use Supabase as their durable state boundary: session state and heartbeats are tracked atomically so a session past its `RESEARCH_SESSION_LEASE_SECONDS` lease is marked failed, and a cancelled or expired session cannot be revived by a late remote response, even across Backend replicas. The full heartbeat/lease/row-lock failover sequence is documented in `research_client.py`.

**Internal network:** all upstream calls use Docker DNS names on `backend-network`. No host-port hops; nothing reaches the host filesystem outside the mounted `./services/backend/app/` source directory.

**Init container:** none. The backend has no `backend-init`; one-time setup (DB migrations) is delegated to `supabase-db-init` which runs SQL scripts from `services/supabase/db/scripts/`.

**Downstream plugin seam (`BACKEND_PLUGINS_DIR`):** after mounting its built-in routers, the app scans `$BACKEND_PLUGINS_DIR` (default `/app/plugins`) and imports each subdirectory exposing a FastAPI `router`, installing any package-level `requirements.txt` first. It is a no-op when the directory is absent, so base Atlas is unaffected — the seam exists so a downstream consumer (e.g. one vendoring Atlas as a submodule) can add its own API routes without forking the backend. A plugin whose requirements fail to install or that fails to import is logged and skipped, never crashing the backend. Consumer-side walkthrough: [reusing-atlas.md §6.3](../../docs/deployment/reusing-atlas.md#63-adding-backend-api-routes-via-the-plugin-seam).

**Optional typed plugin manifest (`plugin.yml`, #402):** a plugin package MAY ship a `plugin.yml` declaring its name, route prefix, and an `auth` mode (`inherit` the Backend identity boundary, `key-auth`, or `open`); malformed manifests or prefix conflicts skip only the affected plugin. `GET /plugins` is internal-service only. See [reusing-atlas.md §6.3.1](../../docs/deployment/reusing-atlas.md#631-declaring-a-typed-plugin-contract-with-pluginyml); canonical schema: [`bootstrapper/schemas/plugin.schema.json`](../../bootstrapper/schemas/plugin.schema.json).

**Graphiti experiment status:** `GET /memory/graphiti/status` returns the disabled-by-default experiment configuration and namespace pattern without importing `graphiti-core` or writing to Neo4j. Treat it as a readiness/contract endpoint for future backend-only Graphiti work, not as an active memory writer.

**RAG chunking gateway:** `POST /api/chunk` centralizes Chonkie text splitting in the Backend. n8n workflows, notebooks, and future ingestion routes should call this endpoint so chunking defaults, offsets, overlap behavior, and semantic model selection stay consistent across Atlas. JupyterHub also installs Chonkie for exploratory notebook work, but the Backend endpoint is the canonical runtime API.

**RAG ingestion job engine (`rag_ingestion/`, #413):** `POST /api/rag/ingestions` runs a generic, idempotent ingestion lifecycle (discover → parse → chunk → embed → vector-store write → LightRAG upload → drain → finalize) over a consumer-declared `rag_ingestion_profile`, executing through the Celery tier when enabled or synchronously otherwise. Each phase is protected by an owner-fenced execution lease (`RAG_INGESTION_EXECUTION_LEASE_SECONDS`) and reports per-phase status, counts, and actionable errors; corpus inputs are bounded by `RAG_INGESTION_MAX_FILE_BYTES` / `RAG_INGESTION_MAX_CORPUS_BYTES` / `RAG_INGESTION_MAX_FILES`. Consumer-side walkthrough: [reusing-atlas.md §6.3.4](../../docs/deployment/reusing-atlas.md#634-declaring-rag-ingestion-profiles-with-rag_ingestion_profiles). Covered by `app/app/tests/test_rag_ingestion.py` + `test_rag_ingestion_api.py`.

Submission snapshots the corpus and profile definition with the job so a queued worker executes what was submitted even if the registry changes before delivery, and each run reconciles Weaviate by removing prior-generation objects no longer present in the source corpus.

The Backend API and Celery worker share the same Redis state, profile registry, and resource limits; a lease-renewal failure reschedules the ingestion rather than leaving it stuck, and LightRAG uploads are idempotent under retry via deterministic content-and-path identities.

## 5. LightRAG integration

When `LIGHTRAG_SOURCE != disabled`, the backend receives `LIGHTRAG_ENDPOINT` and `LIGHTRAG_API_KEY` env vars. The RAG ingestion job engine (§4, #413) uses them as a `graph_target` — uploading parsed documents and draining the extraction pipeline with a timeout. A consumer can still add a bespoke `/rag` route without manifest changes via the plugin seam described in §4 (mount a `rag` route package under `BACKEND_PLUGINS_DIR`).

<a id="51-lightrag--tei-rerank-adapter-post-lightragrerank-415"></a>

### 5.1. LightRAG → TEI rerank adapter (`POST /lightrag/rerank`, #415)

LightRAG can rerank its retrieved chunks with a cross-encoder for a quality lift, but its built-in Jina/Cohere rerank clients POST `{"query", "documents"}` and read back `{"results": [{"index", "relevance_score"}]}`, while Atlas's [TEI reranker](../tei-reranker/README.md) `/rerank` speaks a *different* wire shape — `{"query", "texts"}` in, a sorted top-level array of `{"index", "score"}` out. The two are not wire-compatible, which is why Atlas historically kept `RERANK_BINDING=null` and [#414](../../docs/deployment/reusing-atlas.md#635-declaring-lightrag-query-profiles-with-lightrag_query_profiles) rejected `enable_rerank: true` query profiles.

`POST /lightrag/rerank` is the translation seam that closes that gap. It is a thin backend route (`app/app/lightrag_rerank_adapter.py`), not a new service: it owns no model and holds no state — it rewrites LightRAG's request into TEI's shape, calls the TEI reranker, and maps TEI's `score` back to LightRAG's `relevance_score` (preserving the original document index, best-first order, and honoring `top_n`). Direct LightRAG→TEI wiring stays forbidden.

**Enabling it.** Off by default. Set `LIGHTRAG_RERANK_ADAPTER_ENABLED=true` **with** `TEI_RERANKER_SOURCE` enabled (and LightRAG enabled). The bootstrapper then wires LightRAG's rerank binding to `http://backend:8000/lightrag/rerank` (binding `jina`) and consumer query profiles may set `enable_rerank: true`. `./start.sh doctor` warns (`lightrag-rerank-adapter` check) if the flag is on but a prerequisite service is off, since reranking would silently be a no-op.

| Env var | Default | Purpose |
|---|---|---|
| `LIGHTRAG_RERANK_ADAPTER_ENABLED` | `false` | Opt-in. Gates the LightRAG↔TEI wiring and `enable_rerank` query profiles. |
| `LIGHTRAG_RERANK_ADAPTER_TOKEN` | *(auto-generated)* | Bearer token guarding the route; handed to LightRAG as `RERANK_BINDING_API_KEY` so both sides share one secret. Masked as a `secret`. |
| `LIGHTRAG_RERANK_ADAPTER_TIMEOUT_SECONDS` | `30` | Finite per-request TEI timeout; must be greater than 0 and no greater than 3,600 seconds or Backend startup fails. |
| `TEI_RERANKER_ENDPOINT` | *(resolved)* | Resolved by the TEI reranker service; the route forwards `{query, texts}` here. |

**Auth & errors.** The route requires `Authorization: Bearer <LIGHTRAG_RERANK_ADAPTER_TOKEN>`; input and the TEI response shape are validated before use, with distinct error codes for auth, timeout, and upstream failures documented in the route's code. Enabling reranking trades a little latency (an extra cross-encoder pass) for better passage ordering; leave it off if latency-sensitive.

```bash
curl -X POST http://localhost:${BACKEND_PORT}/lightrag/rerank \
  -H "Authorization: Bearer ${LIGHTRAG_RERANK_ADAPTER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query": "what is graph-augmented RAG?", "documents": ["…", "…"], "top_n": 3}'
```

## 6. Dependencies & Integrations

### 6.1. Current — Upstream (this service calls)

| Service | Category |
|---|---|
| otel-collector | infra |
| ray | infra |
| minio | data |
| redis | data |
| supabase | data |
| supavisor | data |
| weaviate | data |
| litellm | llm |
| tei-reranker | llm |
| comfyui | media |
| docling | media |
| fal | media |
| tika | media |
| celery | agents |
| lightrag | agents |
| n8n ↔ | agents |
| local-deep-researcher | apps |

### 6.2. Current — Downstream (services that call this)

| Service | Category |
|---|---|
| kong | infra |
| prometheus | infra |
| n8n ↔ | agents |
| jupyterhub | apps |
| open-webui | apps |

### 6.3. Architecture diagram

![backend architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 6.4. Future — Missing pair integrations

- **backend ↔ minio (general artifact API)** — *Why:* RAG ingestion now reads consumer-declared MinIO corpora through manifest-bound scoped credentials, but research outputs, ComfyUI image caches, and large user uploads still use Supabase Storage rather than the built-in `backend` bucket. *Mechanism:* add a dedicated artifact client using `MINIO_BUCKET_BACKEND` plus `MINIO_BACKEND_ACCESS_KEY`/`SECRET_KEY`; expose `POST /storage/artifact` + `GET /storage/artifact/{key}`. *Effort:* small. *Confidence:* high.
- **backend ↔ hermes** — *Why:* `HERMES_ENDPOINT` + `HERMES_API_KEY` are passed in but no client consumes them. Talking to Hermes only through LiteLLM's `hermes-agent` model loses Hermes-native surfaces (skill/tool registration, session state at `/opt/data`, dashboard introspection). *Mechanism:* add `hermes_client.py` next to `n8n_client.py`; call `${HERMES_ENDPOINT}/v1/sessions` and `/skills` with `Authorization: Bearer ${HERMES_API_KEY}`; expose `POST /agents/hermes/run` + `GET /agents/hermes/sessions/{id}`. *Effort:* small. *Confidence:* medium.
- **backend ↔ jupyterhub** — *Why:* notebook users can't reach backend's research/memory/ComfyUI APIs except through Kong + tokens, and backend has no view of JupyterHub state. A thin bridge enables programmatic notebook launches for batch evaluations. *Mechanism:* backend calls JupyterHub REST at `http://jupyterhub:8000/hub/api` with `Authorization: token ${JUPYTERHUB_TOKEN}`; expose `POST /notebooks/users/{name}/server` proxy; share `MINIO_BUCKET_JUPYTER` for artifact handoff. *Effort:* medium. *Confidence:* medium.
- **backend ↔ neo4j (knowledge-graph endpoints)** — *Why:* `neo4j`, `langchain-neo4j`, `NEO4J_URI`/`USER`/`PASSWORD` are all installed and injected, but no graph endpoints exist. LangMem facts and research sources are natural graph citizens. *Mechanism:* add `graph_service.py`; on memory-extract, mirror canonical entities into Neo4j via `bolt://neo4j-graph-db:7687`; expose `GET /memory/user/{id}/graph` and `GET /research/{session_id}/entities`. *Effort:* medium. *Confidence:* high.

### 6.5. Future — Candidate new services

- **Celery + Flower** ([details](../../docs/research/candidates/celery-flower.md)) — *Headline:* Redis-backed async worker tier so long-running research/memory-consolidate/ComfyUI calls stop blocking the FastAPI request loop. *Wires into:* redis, supabase, comfyui, local-deep-researcher.

### 6.6. Future — Unused features in this service

- **LangMem auto-consolidate scheduler** — *Why pursue:* `LANGMEM_AUTO_CONSOLIDATE` + `LANGMEM_CONSOLIDATION_INTERVAL` are declared, `apscheduler` is in `requirements.txt`, but no scheduler runs in `main.py`. Wiring it lights up nightly fact-consolidation. *Effort:* small.
- **STT/TTS proxy endpoints** — *Why pursue:* `STT_ENDPOINT` and `TTS_ENDPOINT` reach the container but the FastAPI surface exposes neither; clients must hit the engines directly, bypassing auth/quota. *Effort:* small.
- **Supabase Realtime channels** — *Why pursue:* `supabase-realtime` is a `depends_on` of backend yet no WebSocket fan-out endpoints exist for streaming research logs or memory updates. *Effort:* medium.
- **Per-user storage namespacing** — *Why pursue:* `/storage/upload` accepts a `bucket` query but no per-user prefix or quota; trivial to abuse. *Effort:* small.

## 7. Troubleshooting

**`/ready` returns 503 for a required upstream.** Read which of `postgres`, `redis`, or `litellm` is `unavailable` in the response payload, then inspect that service's logs. `/health` remains a cheap process-liveness check so orchestration can distinguish a running process from one ready to serve traffic.

**LangMem extraction silently fails.** Check that `LITELLM_DEFAULT_MODEL` is set (it is written into `.env` by `litellm-init` on first start from the YAML catalogs + env). Without a resolved default model, `LANGMEM_EXTRACTION_MODEL` remains empty and the consolidation loop short-circuits. Set it explicitly to a known model id (e.g. `ollama/qwen3:8b`).

**Cold-start hangs on Supabase.** Backend `depends_on: supabase-db-init: { condition: service_completed_successfully }`. If `supabase-db-init` is stuck (usually a bad SQL script in `services/supabase/db/scripts/`), backend will wait forever. Check `docker logs <project>-supabase-db-init`.

**`HERMES_ENDPOINT` reachable but feature returns 404.** Hermes-native endpoints are not wired (see Future — Missing pair integrations above). Calls go through LiteLLM's `hermes-agent` model only.

```bash
docker compose ps backend
docker compose logs -f backend
docker exec <project>-backend env | grep -E 'LITELLM|WEAVIATE|HERMES|NEO4J|STT|TTS|DOCLING'
```

For general startup and routing issues, see [Troubleshooting](../../docs/quick-start/troubleshooting.md).
