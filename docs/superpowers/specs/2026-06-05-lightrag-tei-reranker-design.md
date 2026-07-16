# LightRAG + TEI Reranker — Stack Integration Design

**Status:** Draft — awaiting review
**Date:** 2026-06-05
**Scope:** Add LightRAG (graph-augmented RAG server) and TEI Reranker (BGE-reranker-v2-m3) as two new first-class services in the atlas stack. Wire both into existing storage (Supabase pgvector, Neo4j, Redis), document processing (docling), LLM routing (LiteLLM), and agent runtimes (hermes, n8n, backend).

**Supersession note (2026-07-01):** This design is historical. Current Atlas keeps TEI Reranker as a reusable service but does **not** directly wire stock LightRAG to TEI. LightRAG's built-in Jina/Cohere rerank clients send `query` plus `documents`, while TEI expects `query` plus `texts`, so current runtime config emits `RERANK_BINDING=null` and leaves `RERANK_BINDING_HOST` empty unless a compatible adapter is introduced.

## 1. Summary

Two new services, both default-disabled, both manifest-driven by the existing topology/slot allocator. No new data tier — LightRAG reuses Supabase pgvector, Neo4j, and Redis with graceful in-process fallback when any backend is disabled. Both services participate in the existing `runtime_adaptive` lattice; LightRAG is registered with LiteLLM as a callable `lightrag` model so every downstream consumer (open-webui, openclaw, local-deep-researcher, jupyterhub) reaches RAG transitively without per-service wiring.

RAG-Anything is intentionally **not** added: LightRAG v1.5.0 (released 2026-06-03) absorbed RAG-Anything's multimodal pipeline (MinerU/Docling routing, VLM image processing, equation/table handlers). RAG-Anything has no published image and is now a thin PyPI wrapper.

## 2. Motivation

The stack today routes LLM calls through LiteLLM, has dedicated vector (Weaviate) and graph (Neo4j) stores, and a multimodal parser (docling), but lacks a first-class RAG server that ties them together. Existing agent runtimes (Hermes, OpenClaw) and orchestrators (n8n, backend, local-deep-researcher) can each call LiteLLM, but none provides graph-augmented retrieval or document ingestion as a service. Adding LightRAG closes that gap with a single image (`ghcr.io/hkuds/lightrag:1.5.0`) that ships WebUI + KG extractor + ingestion pipeline.

The TEI Reranker fills the second gap: no existing service exposes a general-purpose `/rerank` endpoint. Weaviate's reranker modules only run inside its query path (and aren't enabled in the vanilla manifest); LiteLLM does not proxy rerankers. BGE-reranker-v2-m3 is the cheapest quality lift available for RAG; it's also reusable by Weaviate or future search features.

## 3. Scope decisions

| Decision | Outcome | Rationale |
|---|---|---|
| RAG-Anything as a separate service | **Dropped** | Library subsumed by LightRAG v1.5+; no published image; transitions toward archival. |
| MinerU as a sidecar parser | **Dropped** | GPU floor; docling covers same scope at acceptable quality. May add later if formula-heavy PDF fidelity becomes a need. |
| Dedicated embedding inference server | **Dropped** | LiteLLM passthrough is sufficient; preserves the stack's single-router invariant. |
| Storage strategy | Reuse Supabase pgvector + Neo4j + Redis, with adaptive fallback to in-process backends | Idiomatic for the stack; LightRAG's KG is visible in Neo4j Browser, vectors in Supabase Studio. |
| Reranker | Add TEI Reranker as a separate `llm`-tier service, default disabled | Reusable beyond LightRAG; lifecycle independent. |
| Default state | Both services default disabled | Mirrors openclaw/airflow precedent. Heavy weight; user opts in. |
| LiteLLM registration | LightRAG registered as a single `lightrag` model entry; query modes encoded as message-prefix | One row in `model_list`, not five variants. |
| Model bindings | Auto-inherit from `LITELLM_DEFAULT_MODEL` / `LITELLM_EMBEDDING_MODEL` | Zero new wizard surface; users override via .env if needed. |

## 4. Architecture overview

### 4.1. 4a. Topology placement

| Service | Folder | Category | Default | Image | Internal port | Wizard band |
|---|---|---|---|---|---|---|
| LightRAG | `services/lightrag/` | `agents` | disabled | `ghcr.io/hkuds/lightrag:1.5.0` | 9621 | 63060–63079 |
| TEI Reranker | `services/tei-reranker/` | `llm` | disabled | `:cpu-1.9` or `:1.9` | 80 | 63030–63039 |

### 4.2. 4b. Port allocation

Slot allocator (`bootstrapper/services/topology.py`) computes host ports from `category` + `depends_on.required`. No hand-coded port literals in either manifest. `--base-port N` relocates everything.

Within the agents band (lexicographic tie-break in topo sort), order becomes: airflow → hermes → **lightrag** → n8n → openclaw. Concrete slots verified against `.env.example`: existing band consumes `AIRFLOW_PORT=63060`, `HERMES_API_PORT=63061`, `HERMES_DASHBOARD_PORT=63062`. `LIGHTRAG_API_PORT` claims the next free slot at the allocator's discretion.

Within the llm band: cloud-providers → litellm → ollama → **tei-reranker**. `LITELLM_PORT=63030` exists; `TEI_RERANKER_PORT` claims the next slot.

**Localhost-port mirrors** are hand-pinned (not slot-allocated; they describe host-installed instances): `LIGHTRAG_LOCALHOST_PORT=63068`, `TEI_RERANKER_LOCALHOST_PORT=63031`.

### 4.3. 4c. No slot-pin needed

The `[supabase, redis, kong, ray]` slot-pin pattern is only required for `infra`-tier additions (where alphabetical tie-break can displace Kong from 63000 or Ray from 63002). Agents and llm tier additions do not need it.

### 4.4. 4d. Dependency edges

```
lightrag
├── required: litellm                       # LLM + embedding routing
├── optional: supabase                      # pgvector vector store; falls back to NanoVectorDB
├── optional: neo4j                         # graph store; falls back to NetworkX
├── optional: redis                         # KV + doc-status; falls back to JsonKV
├── optional: docling                       # multimodal parser; falls back to text-only ingestion
└── optional: tei-reranker                  # quality lift; falls back to no reranking

tei-reranker
└── required: []                            # leaf inference service
```

## 5. Service: `services/lightrag/`

### 5.1. 5a. `service.yml` (illustrative key blocks)

```yaml
name: lightrag
label: "LightRAG (graph-augmented RAG server)"
category: agents
docs: services/lightrag/README.md

containers:
  - lightrag
  - lightrag-init

images:
  - var: LIGHTRAG_IMAGE
    default: "ghcr.io/hkuds/lightrag:1.5.0"
    container: lightrag
  - var: LIGHTRAG_INIT_IMAGE
    default: "alpine:latest"
    container: lightrag-init

sources:
  var: LIGHTRAG_SOURCE
  default: disabled
  options:
    - id: container
    - id: localhost
    - id: disabled

env:
  - { name: LIGHTRAG_SOURCE,           default: disabled }
  - { name: LIGHTRAG_API_PORT }                                  # slot-allocated
  - { name: LIGHTRAG_LOCALHOST_PORT,   default: "63068" }
  - { name: LIGHTRAG_API_KEY,          default: "", secret: true }
  - { name: LIGHTRAG_WORKERS,          default: 2 }
  - { name: LIGHTRAG_MEMORY_LIMIT,     default: 6g }
  - { name: LIGHTRAG_CPU_LIMIT,        default: "2.0" }
  - { name: LIGHTRAG_LLM_BINDING,         default: openai }
  - { name: LIGHTRAG_LLM_BINDING_HOST,    default: "http://litellm:4000/v1" }
  - { name: LIGHTRAG_LLM_MODEL,           default: "" }        # resolved by lightrag-init
  - { name: LIGHTRAG_EMBEDDING_BINDING,   default: openai }
  - { name: LIGHTRAG_EMBEDDING_BINDING_HOST, default: "http://litellm:4000/v1" }
  - { name: LIGHTRAG_EMBEDDING_MODEL,     default: "" }
  - { name: LIGHTRAG_EMBEDDING_DIM,       default: 768 }
  - { name: LIGHTRAG_VLM_PROCESS_ENABLE,  default: "true" }
  - { name: LIGHTRAG_KV_STORAGE,          default: RedisKVStorage }
  - { name: LIGHTRAG_VECTOR_STORAGE,      default: PGVectorStorage }
  - { name: LIGHTRAG_GRAPH_STORAGE,       default: Neo4JStorage }
  - { name: LIGHTRAG_DOC_STATUS_STORAGE,  default: RedisKVStorage }
  - { name: LIGHTRAG_SCALE,         auto_managed: true }
  - { name: LIGHTRAG_INIT_SCALE,    auto_managed: true }
  - { name: LIGHTRAG_ENDPOINT,      auto_managed: true }
  - { name: LIGHTRAG_RERANK_BINDING_HOST, auto_managed: true }
  - { name: LIGHTRAG_DOCLING_ENDPOINT,    auto_managed: true }
  - { name: LIGHTRAG_PG_URI,        auto_managed: true }
  - { name: LIGHTRAG_NEO4J_URI,     auto_managed: true }
  - { name: LIGHTRAG_NEO4J_USERNAME, auto_managed: true }
  - { name: LIGHTRAG_NEO4J_PASSWORD, auto_managed: true }
  - { name: LIGHTRAG_REDIS_URI,     auto_managed: true }

depends_on:
  required:
    - litellm
  optional:
    - supabase
    - neo4j
    - redis
    - docling
    - tei-reranker

rows:
  - display_name: "LightRAG"
    source_var: LIGHTRAG_SOURCE
    port_var: LIGHTRAG_API_PORT
    scale_var: LIGHTRAG_SCALE
    alias: lightrag.localhost
    description: "Graph-augmented RAG server with WebUI, KG extractor, multimodal ingestion."
    localhost_endpoint_var: LIGHTRAG_ENDPOINT
    localhost_port_var: LIGHTRAG_LOCALHOST_PORT

runtime_sc:
  lightrag:
    container:
      scale: 1
      environment: { LIGHTRAG_ENDPOINT: "http://lightrag:9621" }
      extra_hosts: []
    localhost:
      scale: 0
      environment: { LIGHTRAG_ENDPOINT: "http://host.docker.internal:${LIGHTRAG_LOCALHOST_PORT:-63068}" }
      extra_hosts: [ "host.docker.internal:host-gateway" ]
    disabled:
      scale: 0
      environment: { LIGHTRAG_ENDPOINT: "" }
      extra_hosts: []
  lightrag-init:
    container: { scale: 1, environment: {}, extra_hosts: [] }
    disabled:  { scale: 0, environment: {}, extra_hosts: [] }

runtime_adaptive:
  lightrag:
    adapts_to: [ doc_processor, tei_reranker, supabase, neo4j, redis ]
    environment_adaptation:
      LIGHTRAG_DOCLING_ENDPOINT:        ${DOCLING_ENDPOINT}
      LIGHTRAG_RERANK_BINDING_HOST:     ""  # superseded: direct LightRAG->TEI payloads are incompatible without an adapter
      LIGHTRAG_PG_URI:                  postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@supabase-db:5432/${POSTGRES_DB}
      LIGHTRAG_NEO4J_URI:               bolt://neo4j:7687
      LIGHTRAG_NEO4J_USERNAME:          neo4j
      LIGHTRAG_NEO4J_PASSWORD:          ${NEO4J_PASSWORD}
      LIGHTRAG_REDIS_URI:               redis://:${REDIS_PASSWORD}@redis:6379/2
    extra_hosts_adaptation: none
    failure_mode: "Storage falls back to in-process file backends (NanoVectorDB / NetworkX / JsonKV) when supabase/neo4j/redis is disabled. Reranker omitted when disabled. Docling skipped → multimodal images become text-only."

data_flow:
  calls:
    - litellm
    - supabase
    - neo4j
    - redis
    - docling
    - tei-reranker
```

### 5.2. 5b. `compose.yml` (illustrative shape)

```yaml
services:
  lightrag:
    image: ${LIGHTRAG_IMAGE:-ghcr.io/hkuds/lightrag:1.5.0}
    container_name: ${PROJECT_NAME}-lightrag
    deploy:
      replicas: ${LIGHTRAG_SCALE:-0}
      resources:
        limits:
          memory: ${LIGHTRAG_MEMORY_LIMIT:-6g}
          cpus: '${LIGHTRAG_CPU_LIMIT:-2.0}'
    ports:
      - "${LIGHTRAG_API_PORT}:9621"
    environment:
      # Every var listed in runtime_sc.lightrag.<source>.environment MUST also
      # appear here (memory: project_runtime_sc_vs_compose_env_dual_write).
      LIGHTRAG_ENDPOINT: ${LIGHTRAG_ENDPOINT}
      # Static configuration
      HOST: 0.0.0.0
      PORT: 9621
      WORKERS: ${LIGHTRAG_WORKERS:-2}
      LIGHTRAG_API_KEY: ${LIGHTRAG_API_KEY}
      # LLM bindings
      LLM_BINDING: ${LIGHTRAG_LLM_BINDING:-openai}
      LLM_BINDING_HOST: ${LIGHTRAG_LLM_BINDING_HOST:-http://litellm:4000/v1}
      LLM_BINDING_API_KEY: ${LITELLM_MASTER_KEY}
      LLM_MODEL: ${LIGHTRAG_LLM_MODEL}
      EMBEDDING_BINDING: ${LIGHTRAG_EMBEDDING_BINDING:-openai}
      EMBEDDING_BINDING_HOST: ${LIGHTRAG_EMBEDDING_BINDING_HOST:-http://litellm:4000/v1}
      EMBEDDING_BINDING_API_KEY: ${LITELLM_MASTER_KEY}
      EMBEDDING_MODEL: ${LIGHTRAG_EMBEDDING_MODEL}
      EMBEDDING_DIM: ${LIGHTRAG_EMBEDDING_DIM:-768}
      VLM_PROCESS_ENABLE: ${LIGHTRAG_VLM_PROCESS_ENABLE:-true}
      # Storage selectors
      LIGHTRAG_KV_STORAGE: ${LIGHTRAG_KV_STORAGE:-RedisKVStorage}
      LIGHTRAG_VECTOR_STORAGE: ${LIGHTRAG_VECTOR_STORAGE:-PGVectorStorage}
      LIGHTRAG_GRAPH_STORAGE: ${LIGHTRAG_GRAPH_STORAGE:-Neo4JStorage}
      LIGHTRAG_DOC_STATUS_STORAGE: ${LIGHTRAG_DOC_STATUS_STORAGE:-RedisKVStorage}
      # Adaptive storage URIs
      POSTGRES_URI: ${LIGHTRAG_PG_URI}
      NEO4J_URI: ${LIGHTRAG_NEO4J_URI}
      NEO4J_USERNAME: ${LIGHTRAG_NEO4J_USERNAME}
      NEO4J_PASSWORD: ${LIGHTRAG_NEO4J_PASSWORD}
      REDIS_URI: ${LIGHTRAG_REDIS_URI}
      # Parser
      LIGHTRAG_PARSER: "*:native-teP,*:legacy-R"
      DOCLING_ENDPOINT: ${LIGHTRAG_DOCLING_ENDPOINT}
      # Reranker
      RERANK_BINDING: ${LIGHTRAG_RERANK_BINDING:-null}
      RERANK_BINDING_HOST: ${LIGHTRAG_RERANK_BINDING_HOST}
      RERANK_MODEL: BAAI/bge-reranker-v2-m3
    volumes:
      - lightrag-data:/app/data
    networks:
      - backend-network
    depends_on:
      lightrag-init:
        condition: service_completed_successfully
      litellm:
        condition: service_started

  lightrag-init:
    image: ${LIGHTRAG_INIT_IMAGE:-alpine:latest}
    container_name: ${PROJECT_NAME}-lightrag-init
    deploy:
      replicas: ${LIGHTRAG_INIT_SCALE:-0}
    entrypoint: ["sh", "-c", "/scripts/init-lightrag.sh"]
    environment:
      LIGHTRAG_SOURCE: ${LIGHTRAG_SOURCE}
      POSTGRES_URI: ${LIGHTRAG_PG_URI}
      NEO4J_URI: ${LIGHTRAG_NEO4J_URI}
      NEO4J_PASSWORD: ${LIGHTRAG_NEO4J_PASSWORD}
      REDIS_URI: ${LIGHTRAG_REDIS_URI}
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
      LITELLM_DEFAULT_MODEL: ${LITELLM_DEFAULT_MODEL}
      LITELLM_EMBEDDING_MODEL: ${LITELLM_EMBEDDING_MODEL}
    volumes:
      - ./init/scripts:/scripts:ro
      - lightrag-data:/app/data
    networks:
      - backend-network
    depends_on:
      litellm:
        condition: service_started

volumes:
  lightrag-data:
    name: ${PROJECT_NAME}-lightrag-data
```

### 5.3. 5c. `init/scripts/init-lightrag.sh`

Alpine entrypoint following `project_init_container_pattern`: `#!/bin/sh` shebang, `apk add --no-cache bash curl jq postgresql-client cypher-shell ca-certificates`, then `exec bash -- "$0" "$@"` with sentinel. Bash body does:

1. Wait for LiteLLM `/v1/models` (60s timeout).
2. Call `resolve-models.py` to pick LightRAG's LLM + embedding model + dim from `/v1/models`.
3. Patch `lightrag-data/.env` with resolved values.
4. If `POSTGRES_URI` non-empty, run `migrate-pgvector.sql` via `psql` (idempotent).
5. If `NEO4J_URI` non-empty, run `migrate-neo4j.cypher` via `cypher-shell` (idempotent).
6. Exit 0.

## 6. Service: `services/tei-reranker/`

### 6.1. 6a. `service.yml` (illustrative key blocks)

```yaml
name: tei-reranker
label: "TEI Reranker (BGE-reranker-v2-m3)"
category: llm
docs: services/tei-reranker/README.md

containers: [ tei-reranker ]

images:
  - var: TEI_RERANKER_CPU_IMAGE
    default: "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9"
    container: tei-reranker
  - var: TEI_RERANKER_GPU_IMAGE
    default: "ghcr.io/huggingface/text-embeddings-inference:1.9"
    container: tei-reranker

sources:
  var: TEI_RERANKER_SOURCE
  default: disabled
  options:
    - id: container-cpu
      label: "Container (CPU)"
    - id: container-gpu
      label: "Container (GPU, NVIDIA)"
    - id: localhost
      label: "Host (existing TEI rerank)"
    - id: disabled

env:
  - { name: TEI_RERANKER_SOURCE,    default: disabled }
  - { name: TEI_RERANKER_PORT }
  - { name: TEI_RERANKER_LOCALHOST_PORT, default: "63031" }
  - { name: TEI_RERANKER_MODEL_ID,  default: "BAAI/bge-reranker-v2-m3" }
  - { name: TEI_RERANKER_REVISION,  default: "main" }
  - { name: TEI_RERANKER_MAX_CLIENT_BATCH_SIZE, default: 32 }
  - { name: TEI_RERANKER_MEMORY_LIMIT, default: 4g }
  - { name: TEI_RERANKER_CPU_LIMIT,    default: "2.0" }
  - { name: TEI_RERANKER_HF_CACHE_DIR, default: "/data" }
  - { name: TEI_RERANKER_SCALE,            auto_managed: true }
  - { name: TEI_RERANKER_ENDPOINT,         auto_managed: true }
  - { name: TEI_RERANKER_IMAGE_RESOLVED,   auto_managed: true }

depends_on: { required: [], optional: [] }

rows:
  - display_name: "TEI Reranker"
    source_var: TEI_RERANKER_SOURCE
    port_var: TEI_RERANKER_PORT
    scale_var: TEI_RERANKER_SCALE
    alias: rerank.localhost
    description: "BGE-reranker-v2-m3 inference for RAG quality lift."
    localhost_endpoint_var: TEI_RERANKER_ENDPOINT
    localhost_port_var: TEI_RERANKER_LOCALHOST_PORT

runtime_sc:
  tei-reranker:
    container-cpu:
      scale: 1
      environment: { TEI_RERANKER_ENDPOINT: "http://tei-reranker:80" }
      extra_hosts: []
    container-gpu:
      scale: 1
      environment: { TEI_RERANKER_ENDPOINT: "http://tei-reranker:80" }
      deploy:
        resources:
          reservations:
            devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }]
      extra_hosts: []
    localhost:
      scale: 0
      environment: { TEI_RERANKER_ENDPOINT: "http://host.docker.internal:${TEI_RERANKER_LOCALHOST_PORT:-63031}" }
      extra_hosts: [ "host.docker.internal:host-gateway" ]
    disabled:
      scale: 0
      environment: { TEI_RERANKER_ENDPOINT: "" }
      extra_hosts: []

data_flow: { calls: [] }
```

### 6.2. 6b. Compose image selection

The compose fragment uses `image: ${TEI_RERANKER_IMAGE_RESOLVED:-...}`. `_generate_tei_reranker_config()` writes `TEI_RERANKER_IMAGE_RESOLVED` to `.env` based on the source variant (mirrors `DOCLING_GPU_IMAGE` pattern).

## 7. Adaptive integration web

### 7.1. 7a. LightRAG → other services (covered in §5)

| LightRAG env var | Source | Failure mode |
|---|---|---|
| `LIGHTRAG_DOCLING_ENDPOINT` | `DOCLING_ENDPOINT` | Multimodal images become text-only |
| `LIGHTRAG_RERANK_BINDING_HOST` | Empty by default; adapter-owned when present | No reranking; retrieval quality drops |
| `LIGHTRAG_PG_URI` | `POSTGRES_*` from Supabase | Falls back to `NanoVectorDBStorage` (file) |
| `LIGHTRAG_NEO4J_URI`/`_PASSWORD` | `NEO4J_PASSWORD` | Falls back to `NetworkXStorage` (file) |
| `LIGHTRAG_REDIS_URI` | `REDIS_PASSWORD` | Falls back to `JsonKVStorage` (file) |

`lightrag-init` flips `LIGHTRAG_KV_STORAGE`/`LIGHTRAG_VECTOR_STORAGE`/`LIGHTRAG_GRAPH_STORAGE` to the in-process variant when the corresponding URI is empty.

### 7.2. 7b. Three services adapt to LightRAG (explicit)

**`services/hermes/service.yml`**:
```yaml
runtime_adaptive:
  hermes-init:
    adapts_to: [ stt_provider, tts_provider, comfyui, searxng, lightrag ]
    environment_adaptation:
      LIGHTRAG_INTERNAL_URL: ${LIGHTRAG_ENDPOINT}
```
hermes-init appends a `tools.rag_query` block to `config.yaml` when `LIGHTRAG_INTERNAL_URL` is non-empty.

**`services/n8n/service.yml`**:
```yaml
runtime_adaptive:
  n8n:
    adapts_to: [ stt_provider, tts_provider, doc_processor, lightrag ]
    environment_adaptation:
      LIGHTRAG_ENDPOINT: ${LIGHTRAG_ENDPOINT}
      LIGHTRAG_API_KEY:  ${LIGHTRAG_API_KEY}
```
n8n nodes invoke LightRAG via the generic HTTP Request node + documented credential pair (no first-party node in v1).

**`services/backend/service.yml`**:
```yaml
runtime_adaptive:
  backend:
    adapts_to: [ llm_provider, weaviate, stt_provider, tts_provider, doc_processor, ray, lightrag ]
    environment_adaptation:
      LIGHTRAG_ENDPOINT: ${LIGHTRAG_ENDPOINT}
      LIGHTRAG_API_KEY:  ${LIGHTRAG_API_KEY}
```
Backend env is wired; `/rag` route implementation is out of scope for this spec.

### 7.3. 7c. Transitive consumers (no manifest changes)

OpenClaw, open-webui, local-deep-researcher, jupyterhub reach LightRAG through the LiteLLM `lightrag` model entry (§8). No per-service wiring.

### 7.4. 7d. Init ordering

`lightrag-init` runs after `litellm-init`, `weaviate-init`, `hermes-init`, and `tei-reranker` (when enabled). Ordering enforced by compose `depends_on.condition: service_started` for sidecars and by `_generate_*_config` call-chain order in `service_config.py::generate_service_environment()`.

## 8. LiteLLM model registration

Hand-coded edge in `services/litellm/init/scripts/init.py` (mirrors `hermes_model_entry()`):

```python
def lightrag_model_entry() -> dict | None:
    if os.environ.get("LIGHTRAG_SOURCE", "disabled") == "disabled":
        return None
    return {
        "model_name": "lightrag",
        "litellm_params": {
            "model": "openai/lightrag",
            "api_base": f"{os.environ['LIGHTRAG_ENDPOINT']}/api",
            "api_key": os.environ.get("LIGHTRAG_API_KEY", "sk-no-auth"),
        },
        "model_info": {
            "mode": "chat",
            "description": "LightRAG graph-augmented RAG. Encode query mode as system prompt prefix /hybrid|/local|/global|/naive|/mix.",
        },
    }
```

Adapter is `openai/` (not `ollama_chat/`) because LightRAG's Ollama-shim implements `/api/chat` with OpenAI-style messages and the `ollama_chat/` adapter expects the full Ollama tag-listing protocol.

Note the **two layers** of "openai" wiring (not the same thing): the compose env `LLM_BINDING=openai` is *LightRAG's outbound* binding (LightRAG calls LiteLLM as an OpenAI-compat client); the model_list `model: openai/lightrag` is *LiteLLM's inbound* adapter (LiteLLM calls LightRAG's Ollama-shim using OpenAI-style messages). Distinct concerns, both correct.

Single `lightrag` model row in `model_list` — users encode query mode via message prefix.

## 9. Kong routes

Two new methods in `bootstrapper/utils/kong_config_generator.py`:

- `generate_lightrag_service()` → host `lightrag.localhost`, `preserve_host: True` (browser-facing SPA at `/webui`).
- `generate_tei_reranker_service()` → host `rerank.localhost`, NO `preserve_host` (pure REST inference server).

Each gated on `_SOURCE != disabled`. `scripts/check-kong-routes.py::EXPECTED_HOST_ROUTES` gets two new entries.

## 10. Wizard UX

- **"LightRAG · source"** step appears after Hermes in the agents band. Tiles: container / localhost / disabled. Default: disabled.
- **"TEI Reranker · source"** step appears after LiteLLM in the llm band. Tiles: container-cpu / container-gpu / localhost / disabled. Default: disabled.
- **Inline localhost-port override** widgets wired via `LOCALHOST_PORT_WIRING` (`bootstrapper/ui/textual/integration.py:252`).
- **No model picker step** for either service. LightRAG inherits `LITELLM_DEFAULT_MODEL` / `LITELLM_EMBEDDING_MODEL`. TEI Reranker uses `TEI_RERANKER_MODEL_ID` static default.
- **No `--*-models` CLI flag.** Only `--lightrag-source` and `--tei-reranker-source`.

## 11. Bootstrapper plumbing checklist

### 11.1. 11a. New files

```
services/lightrag/{service.yml, compose.yml, README.md, architecture.html, architecture.svg}
services/lightrag/init/scripts/{init-lightrag.sh, resolve-models.py, migrate-pgvector.sql, migrate-neo4j.cypher}
services/tei-reranker/{service.yml, compose.yml, README.md, architecture.html, architecture.svg}
```

No `build/Dockerfile` for either (both use published images). No catalog-init (no model picker).

### 11.2. 11b. `bootstrapper/services/service_config.py`

Add `_generate_lightrag_config()` and `_generate_tei_reranker_config()` methods (signatures and bodies in §5 / §6). Call from `generate_service_environment()` **after** `_generate_doc_processor_config`, `_generate_supabase_config`, `_generate_neo4j_config`, `_generate_redis_config`, `_generate_litellm_config`, `_generate_tei_reranker_config`, then `_generate_lightrag_config`. Adaptive substitution for `LIGHTRAG_PG_URI`/`LIGHTRAG_NEO4J_URI`/etc. happens automatically via `_generate_adaptive_services_config`.

### 11.3. 11c. CLI source flags (four seams per service)

`bootstrapper/start.py`:
- `@click.option('--lightrag-source', type=click.Choice(['container','localhost','disabled']))`
- `@click.option('--tei-reranker-source', type=click.Choice(['container-cpu','container-gpu','localhost','disabled']))`
- `main()` signature additions: `lightrag_source, tei_reranker_source`
- `source_args` dict additions: `'lightrag_source': lightrag_source, 'tei_reranker_source': tei_reranker_source`
- Port-clear list: `'LIGHTRAG_API_PORT', 'TEI_RERANKER_PORT'`

`bootstrapper/utils/source_override_manager.py::source_mapping`:
- `'lightrag_source': 'LIGHTRAG_SOURCE'`
- `'tei_reranker_source': 'TEI_RERANKER_SOURCE'`

`bootstrapper/ui/textual/screens/wizard_screen.py:1317-1319` — **no edit** (no `--*-models` flags).

### 11.4. 11d. Endpoint vars + validators

`bootstrapper/utils/endpoint_vars.py::LOCALHOST_ENDPOINT_VARS`:
- `"lightrag": "LIGHTRAG_ENDPOINT"`
- `"tei-reranker": "TEI_RERANKER_ENDPOINT"`

`bootstrapper/utils/localhost_validator.py::SERVICE_CHECKS`:
- `'LIGHTRAG_SOURCE'` with `LIGHTRAG_LOCALHOST_PORT=63068`, probe `/health`
- `'TEI_RERANKER_SOURCE'` with `TEI_RERANKER_LOCALHOST_PORT=63031`, probe `/health`

`bootstrapper/utils/hosts_manager.py::GENAI_HOSTS`:
- `"lightrag.localhost"`, `"rerank.localhost"`

### 11.5. 11e. Key generator

`bootstrapper/utils/key_generator.py`:
- `generate_lightrag_api_key()` + `generate_and_update_lightrag_api_key()`
- Wire into `generate_missing_keys()` gated on `LIGHTRAG_SOURCE != disabled`.
- TEI Reranker: no key (TEI is unauthenticated by default).

### 11.6. 11f. Audit scripts

`scripts/check-compose-source-deps.py::REQUIRED_DEPENDENCIES`:
- `('lightrag', 'litellm')` — hard runtime call.
- `('lightrag', 'supabase')` / `('lightrag', 'neo4j')` / `('lightrag', 'redis')` — soft, only required when LightRAG's source-validator detects user pinned the storage selector to those backends.

`scripts/check-kong-routes.py::EXPECTED_HOST_ROUTES`:
- `"lightrag.localhost": "http://lightrag:9621/"`
- `"rerank.localhost": "http://tei-reranker:80/"`

### 11.7. 11g. Top-level compose include

`docker-compose.yml`:
- `- services/tei-reranker/compose.yml` after `- services/ollama/compose.yml`.
- `- services/lightrag/compose.yml` between hermes and n8n.

### 11.8. 11h. `.env.example`

Regenerated by `bootstrapper/services/env_assembler.py` after both manifests are added. Section banners must use `─` (U+2500) per `project_env_backfill_unicode_bar_bug` to avoid scattering vars into the unsectioned trailer.

### 11.9. 11i. Documentation

| File | Edit |
|---|---|
| `services/lightrag/README.md` | NEW — numbered sections; §5 (Dependencies & Integrations) auto-regenerated |
| `services/tei-reranker/README.md` | NEW |
| `docs/README.md` | Two new index rows |
| `docs/CHANGELOG.md` | `### Added (LightRAG service)` + `### Added (TEI Reranker service)` |
| `docs/ROADMAP.md` | Mark RAG as shipped (if it was tier 1/2) |
| `docs/deployment/ports-and-routes.md` | New rows for all four ports + two Kong hostnames |
| `docs/deployment/source-configuration.md` | Matrix rows + dedicated `### LIGHTRAG_SOURCE` / `### TEI_RERANKER_SOURCE` |
| `docs/quick-start/interactive-setup-wizard.md` | Two new wizard table rows |
| `services/kong/README.md` | Two route bullets + curl examples |
| `services/hermes/README.md` | "RAG capability" subsection under §8 |
| `services/n8n/README.md` | How to call LightRAG from an HTTP Request node |
| `services/backend/README.md` | New env vars injected |
| `services/litellm/README.md` | `lightrag` model entry; query-mode prefix convention |
| `services/supabase/README.md` | pgvector schema additions |
| `services/neo4j/README.md` | Graph constraints; visible in Neo4j Browser |
| `services/redis/README.md` | KV db index 2 used by LightRAG |
| `README.md` (root) | 5 places: diagram caption, localhost source list, service URL table, service descriptions, CLI example |

### 11.10. 11j. No changes

- `bootstrapper/ui/state_builder.py` — `_SERVICES`/`_HOST_ALIAS` constants retired; rows discovered from `Topology.rows`.
- `bootstrapper/services/sc_synthesizer.py` — automatic from manifests.

## 12. Testing & verification matrix

### 12.1. 12a. New tests

```
bootstrapper/tests/test_lightrag_config.py
  - test_lightrag_disabled_clears_endpoint
  - test_lightrag_container_endpoint
  - test_lightrag_localhost_uses_LIGHTRAG_LOCALHOST_PORT
  - test_lightrag_adapts_to_supabase_fallback
  - test_lightrag_adapts_to_neo4j_fallback
  - test_lightrag_adapts_to_redis_fallback
  - test_lightrag_pg_uri_contains_secrets

bootstrapper/tests/test_tei_reranker_config.py
  - test_tei_reranker_disabled_clears_endpoint
  - test_tei_reranker_container_cpu_image
  - test_tei_reranker_container_gpu_image
  - test_tei_reranker_container_gpu_deploy_block
  - test_tei_reranker_localhost_uses_LOCALHOST_PORT

bootstrapper/tests/test_lightrag_litellm_registration.py
  - test_lightrag_model_entry_returns_none_when_disabled
  - test_lightrag_model_entry_uses_LIGHTRAG_ENDPOINT
  - test_lightrag_model_entry_uses_api_key
  - test_lightrag_model_entry_adapter_is_openai_not_ollama_chat

bootstrapper/tests/test_hermes_n8n_backend_adapts_to_lightrag.py
  - assert 'lightrag' in adapts_to list for each service
  - assert LIGHTRAG_INTERNAL_URL or LIGHTRAG_ENDPOINT in environment_adaptation
```

### 12.2. 12b. Existing test extensions

| Test | Extension |
|---|---|
| `test_user_model_selections_seam_parity.py` | No change. Document why with a comment to prevent future drift. |
| `test_fragment_equivalence.py` | Regenerate `rendered_config_baseline.yml` once via the CI-artifact dance (`project_baseline_regen_via_ci_artifact`). |
| `test_fragment_bind_sources.py` | Passes automatically. |
| `test_localhost_port_consumer_symmetry.py` | Add `LIGHTRAG_LOCALHOST_PORT` and `TEI_RERANKER_LOCALHOST_PORT`. |
| `test_wizard_app_discovery.py` | Assert `lightrag_source` + `tei_reranker_source` in `source_mapping`. |

### 12.3. 12c. Verification matrix

1. `docker compose --env-file .env -f docker-compose.yml config 2>&1 | grep -i warning` → zero output.
2. `python3 scripts/check-compose-source-deps.py` → PASS.
3. `python3 scripts/check-kong-routes.py` → PASS.
4. `python3 scripts/check-docs-drift.py` → PASS.
5. `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check` → exit 0.
6. Bootstrapper end-to-end env generation across all source values:
   - LightRAG: 3 sources × sample 6 storage-backend combos.
   - TEI Reranker: 4 sources.
7. Init-container smoke-test: `docker run --rm alpine:latest /scripts/init-lightrag.sh` (per `project_init_container_pattern`).
8. `--base-port 64000` test: confirm `LIGHTRAG_API_PORT`, `TEI_RERANKER_PORT`, and all four localhost-port vars relocate.

### 12.4. 12d. Live smoke test

1. `LIGHTRAG_SOURCE=container TEI_RERANKER_SOURCE=container-cpu ./start.sh`.
2. Wait for `lightrag` health check (first run downloads weights; 5 min ceiling).
3. `curl -sH "Authorization: Bearer $LIGHTRAG_API_KEY" http://localhost:$LIGHTRAG_API_PORT/health` → 200.
4. `curl -sH "Authorization: Bearer $LITELLM_MASTER_KEY" http://localhost:$LITELLM_PORT/v1/models | jq '.data[].id' | grep -q lightrag` → match.
5. Upload a PDF via `lightrag.localhost/webui`, wait for ingestion.
6. Query via LightRAG native API → 200 with answer.
7. Query via LiteLLM `/v1/chat/completions` with `model=lightrag` → 200 with answer.
8. Open Neo4j Browser → confirm KG nodes/edges populated.
9. Open Supabase Studio → confirm `lightrag.vectors` table populated.

### 12.5. 12e. Regression-class guards

- Init container is `image: alpine:latest`, NO `build:` (memory `project_init_container_pattern`).
- No `./services/lightrag/...` or `./services/tei-reranker/...` self-double paths (memory `project_compose_include_path_resolution`).
- Every `runtime_sc.<container>.<source>.environment` var also appears in `compose.yml::services.<container>.environment` (memory `project_runtime_sc_vs_compose_env_dual_write`).
- Kong `lightrag` route has `preserve_host: True`; `tei-reranker` does NOT (memory `reference_kong_preserve_host`).
- Image tags pinned (`1.5.0`, `cpu-1.9`, `1.9`) — no `:latest` in the manifest defaults to avoid byte-equivalence baseline drift.

## 13. Risks & open questions

| Risk | Mitigation |
|---|---|
| LightRAG v1.5.0 is 2 days old; stability not yet field-tested | Pin to `1.5.0` explicitly; document downgrade path to `1.4.6` if startup-loop bugs appear |
| TEI Reranker container-cpu may be too slow on weak hosts | Default disabled; document GPU variant prominently |
| `LIGHTRAG_EMBEDDING_DIM` mismatch between resolved model and pgvector column type | `lightrag-init`'s `resolve-models.py` writes the resolved DIM before the LightRAG container starts; `migrate-pgvector.sql` is rerun on each up to pick up the value |
| First-run weight downloads can exceed standard health-check timeout | Start period set to 5 min in compose health-check |
| Adaptive substitution conflicts if `POSTGRES_USER` contains URL-special chars | Use URL-encoded form in `LIGHTRAG_PG_URI`; documented in service.yml comment |
| Future LightRAG release deprecates `/api/chat` Ollama-shim | Detect at lightrag-init by probing `/api/version`; raise a setup-time warning rather than runtime failure |

## 14. References

- LightRAG repo: https://github.com/HKUDS/LightRAG
- LightRAG v1.5.0 release notes: https://github.com/HKUDS/LightRAG/releases/tag/v1.5.0
- LightRAG GHCR image: https://github.com/hkuds/LightRAG/pkgs/container/lightrag
- LightRAG Ollama API compatibility: https://deepwiki.com/HKUDS/LightRAG/4.5-ollama-api-compatibility
- TEI repo: https://github.com/huggingface/text-embeddings-inference
- TEI image: https://ghcr.io/huggingface/text-embeddings-inference
- BGE reranker model card: https://huggingface.co/BAAI/bge-reranker-v2-m3
- RAG-Anything repo (informational, not added): https://github.com/HKUDS/RAG-Anything

## 15. Memory cross-references (constraints encoded in this design)

- `project_service_addition_checklist` — the ~25-file touchpoint set.
- `project_init_container_pattern` — alpine + inline apk, no Dockerfile.
- `project_infra_slot_pin_kong_ray` — N/A here (agents/llm tiers don't pin).
- `project_compose_include_path_resolution` — both fragments use `./` paths only.
- `project_runtime_sc_vs_compose_env_dual_write` — every adapted env var listed in both blocks.
- `project_cli_source_flag_three_seams` — four-seam pattern for both services.
- `project_model_picker_pipeline_divergence` — N/A (no pickers added).
- `project_env_example_is_a_view` — `.env.example` regenerated from manifests.
- `project_env_backfill_unicode_bar_bug` — use `─` (U+2500) for new section banners.
- `feedback_localhost_url_override_symmetry` — 4-consumer test extended for new ports.
- `reference_kong_preserve_host` — set for LightRAG, not for TEI Reranker.
- `reference_kong_compose_service_id` — `kong-api-gateway`, not `kong`.
- `project_baseline_regen_via_ci_artifact` — used for `test_fragment_equivalence` baseline refresh.
