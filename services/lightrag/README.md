# 5.2.26. LightRAG

> **Image:** `ghcr.io/hkuds/lightrag:v1.5.4`
> **Container port:** 9621 (API + WebUI)  · **Default host port:** allocated by `topology.py` (agents band 63070–63089)
> **Default:** disabled

## 1. Overview

[LightRAG](https://github.com/HKUDS/LightRAG) is a graph-augmented RAG server. It ingests documents (PDF, Office, images, tables, equations — multimodal pipeline absorbed from RAG-Anything in v1.5.0), extracts a knowledge graph via LLM-driven entity/relation extraction, embeds chunks and entities into a vector store, and exposes a unified query API that combines graph traversal with vector search.

In this stack, LightRAG reuses existing infrastructure:

- **LLM + embeddings** routed through LiteLLM (`LLM_BINDING_HOST=http://litellm:4000/v1`).
- **Vector store** → Supabase pgvector (`PGVectorStorage`).
- **Graph store** → Neo4j (`Neo4JStorage`). `LIGHTRAG_NEO4J_URI` follows `NEO4J_GRAPH_DB_SOURCE`: `bolt://neo4j-graph-db:7687` for the container, `bolt://host.docker.internal:${NEO4J_LOCALHOST_BOLT_PORT}` for a host-run Neo4j.
- **KV + doc-status** → Redis (`RedisKVStorage`).
- **Document parsing** → LightRAG's `native` engine for docx/md/textpack and its `legacy` text extraction for everything else, including PDFs. Atlas sets `LIGHTRAG_PARSER` to `*:native-teP,*:legacy-R` in compose (not configurable from `.env`). The isolated Docling compatibility adapter (which authenticates to Docling without exposing its provider credential) is wired as `LIGHTRAG_DOCLING_ENDPOINT` when in-stack LightRAG and Docling are enabled, but a file only goes to Docling when its name carries the `.[docling].` hint (e.g. `report.[docling].pdf`), the one per-file way to opt in.
- **Credentials** → the base and embedding LLM bindings always send `LITELLM_MASTER_KEY`, so point `LIGHTRAG_LLM_BINDING_HOST` / `LIGHTRAG_EMBEDDING_BINDING_HOST` only at LiteLLM; another host (native Ollama included, which ignores it) would still receive the master key.
- **Reranking** defaults off. LightRAG's built-in Jina/Cohere rerank clients send a payload shape (`{query, documents}`) that TEI's `/rerank` endpoint (`{query, texts}`) does not accept, so Atlas never wires LightRAG directly to TEI. To enable reranking, route it through the backend rerank adapter (`POST /lightrag/rerank`, #415): set `LIGHTRAG_RERANK_ADAPTER_ENABLED=true` with `TEI_RERANKER_SOURCE` enabled — see the [backend README §5.1](../backend/README.md#51-lightrag--tei-rerank-adapter-post-lightragrerank-415).

When Supabase, Neo4j, or Redis is disabled, Atlas clears that backend's connection URI but leaves the corresponding external storage selector unchanged. Atlas does not select file-backed storage automatically; operators who want a file-backed mode must set the relevant `LIGHTRAG_*_STORAGE` selector explicitly and provide suitable persistence. Images are extracted as text unless a file opts into Docling with the `.[docling].` hint and Docling is enabled.

## 2. Source variants

| Source | Scale | Endpoint | Notes |
|---|---|---|---|
| `container` | 1 | `http://lightrag:9621` | In-stack LightRAG |
| `localhost` | 0 | `http://host.docker.internal:${LIGHTRAG_LOCALHOST_PORT}` | Host-installed LightRAG |
| `disabled` | 0 | `""` | LightRAG off; consumers see empty endpoint |

## 3. Configuration

Storage selectors and model bindings can be overridden via `.env`:

```env
LIGHTRAG_SOURCE=disabled                            # default
LIGHTRAG_KV_STORAGE=RedisKVStorage                  # alt: JsonKVStorage
LIGHTRAG_VECTOR_STORAGE=PGVectorStorage             # alt: NanoVectorDBStorage, QdrantVectorDBStorage, ...
LIGHTRAG_GRAPH_STORAGE=Neo4JStorage                 # alt: NetworkXStorage, MemgraphStorage, AGEStorage
LIGHTRAG_DOC_STATUS_STORAGE=RedisDocStatusStorage   # alt: PGDocStatusStorage, JsonDocStatusStorage
LIGHTRAG_LLM_MODEL=                                 # empty = inherit LITELLM_DEFAULT_MODEL
LIGHTRAG_EXTRACT_LLM_MODEL=                         # empty = inherit LLM_MODEL
LIGHTRAG_KEYWORD_LLM_MODEL=                         # empty = inherit LLM_MODEL
LIGHTRAG_QUERY_LLM_MODEL=                           # empty = inherit LLM_MODEL
LIGHTRAG_EXTRACT_MAX_ASYNC_LLM=                     # empty = inherit MAX_ASYNC_LLM
LIGHTRAG_QUERY_LLM_TIMEOUT=                         # empty = inherit LLM_TIMEOUT
LIGHTRAG_QUERY_ENABLE_RERANK=false                  # rerank via backend adapter (#415); needs LIGHTRAG_RERANK_ADAPTER_ENABLED=true + TEI
LIGHTRAG_QUERY_TOP_K=10                             # graph query KG top-k
LIGHTRAG_QUERY_CHUNK_TOP_K=5                        # graph query chunk top-k
LIGHTRAG_QUERY_MAX_TOTAL_TOKENS=12000               # graph query context budget
LIGHTRAG_EMBEDDING_MODEL=                           # empty = inherit LITELLM_EMBEDDING_MODEL
LIGHTRAG_VLM_PROCESS_ENABLE=true                    # vision LLM for images/figures
```

LightRAG v1.5 supports role-specific LLM settings for extraction, keyword extraction, and final query answering. Atlas exposes those as `LIGHTRAG_EXTRACT_*`, `LIGHTRAG_KEYWORD_*`, and `LIGHTRAG_QUERY_*` inputs, then maps them to LightRAG's native `EXTRACT_*`, `KEYWORD_*`, and `QUERY_*` runtime environment names. Leave a role value empty to inherit the base LightRAG runtime `LLM_*` settings; the base model name itself is resolved by `lightrag-init` when `LIGHTRAG_LLM_MODEL` is empty. An empty role LLM-binding API key (`LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY` / `LIGHTRAG_KEYWORD_LLM_BINDING_API_KEY` / `LIGHTRAG_QUERY_LLM_BINDING_API_KEY`) becomes `${LITELLM_MASTER_KEY}` only when that role's effective host is the in-network LiteLLM (`litellm:4000`). The container decides at start, in `init/scripts/resolve-role-keys.py`, using LightRAG 1.5.4's own host resolution (#1271): an empty role host means the base `LLM_BINDING_HOST`, except that an `azure_openai` role on its own binding defaults to `AZURE_OPENAI_ENDPOINT`. LiteLLM-routed roles therefore still need no key wiring (#721, #796). A role with its own binding or its own host that resolves anywhere else must set its key, or the container stops at start and names the variable; without that, LightRAG would either stop itself (a role on its own binding needs a key) or hand the role the base key, which is the master key (#1291). A role that simply mirrors the base binding and host is left as it is.

> **Observability caveat.** Pointing a role's `*_LLM_BINDING_HOST` at a native provider (e.g. Ollama directly) takes that role **off the LiteLLM gateway**, and Langfuse tracing in Atlas is gateway-level — so those calls produce no traces and nothing warns about it. If you run Langfuse and override a role's binding host, expect a coverage gap for that role. See [Langfuse §4.2](../langfuse/README.md).

**Catalog request defaults across the role boundary (#658).** A model's `request_defaults` in the Ollama catalog (`services/ollama/models.yaml`; today `think: false` on `qwen3.8:latest`) reach that model only through LiteLLM: `litellm-init` renders them into the model's `litellm_params`. A native LightRAG 1.5.4 binding does not send them, and the Ollama one cannot: it forwards only Ollama's `options` object ([`binding_options.py#L435`](https://github.com/HKUDS/LightRAG/blob/v1.5.4/lightrag/llm/binding_options.py#L435), [`lightrag_server.py#L1739-L1740`](https://github.com/HKUDS/LightRAG/blob/v1.5.4/lightrag/api/lightrag_server.py#L1739-L1740)), and `think` is a top-level request field beside it. So `init/scripts/resolve-role-keys.py`, which reads the catalog from a read-only mount, settles each role's transport at start:

- **A role that would inherit a native base stays on LiteLLM.** Suppose `LIGHTRAG_LLM_BINDING` and `LIGHTRAG_LLM_BINDING_HOST` point LightRAG at a provider directly, for example `ollama` at `http://host.docker.internal:11434` with `LLM_PROVIDER_SOURCE=ollama-localhost`. A role whose model declares catalog `request_defaults`, and that sets none of its own binding, host or key, is then bound to `openai` at `http://litellm:4000/v1` with the LiteLLM master key.
  - LiteLLM applies the defaults, so the role sends the same request it would on the default path.
  - The cost is one gateway hop for that role, which also brings it back under Langfuse tracing.
  - The role's model must be one LiteLLM serves. Every Ollama catalog model is while an `ollama-*` `LLM_PROVIDER_SOURCE` is selected.
  - A routed `EXTRACT` is no longer bound to Ollama, so the `EXTRACT_OLLAMA_LLM_*` caps below do not apply to it.
- **A role whose model declares none keeps the base binding.** Embedding entries and chat entries without `request_defaults` resolve exactly as before, so a role on such a model still goes native.
- **Explicit role settings win.** A role that sets its own `LIGHTRAG_<ROLE>_LLM_BINDING`, `_BINDING_HOST` or `_BINDING_API_KEY` is never re-routed. If those settings put it on a native host, it cannot receive its model's defaults, and the start log and doctor say so.
- **Where to see it.** At start the container logs one `lightrag: <ROLE> role: …` line per role. It names the binding, host, model and the request defaults the role's calls carry, and never a key. `./start.sh doctor` reports the same in its `lightrag-role-transport` check, and warns for a role that loses its defaults. LightRAG's own `/health` shows each role's effective `binding`, `host` and `model` under `configuration.role_llm_config`, with keys stripped.

Atlas also exposes LightRAG's query defaults as `LIGHTRAG_QUERY_ENABLE_RERANK`, `LIGHTRAG_QUERY_TOP_K`, `LIGHTRAG_QUERY_CHUNK_TOP_K`, and `LIGHTRAG_QUERY_MAX_TOTAL_TOKENS`. Numeric query values default to concrete integers because LightRAG v1.5 parses those env vars as integers and does not accept empty strings. `LIGHTRAG_QUERY_ENABLE_RERANK` defaults to `false` because direct LightRAG-to-TEI reranking is not wire-compatible: LightRAG sends `{query, documents}` through its Jina/Cohere clients, while TEI expects `{query, texts}`. Enable reranking by routing through the backend rerank adapter (#415): set `LIGHTRAG_RERANK_ADAPTER_ENABLED=true` (with `TEI_RERANKER_SOURCE` enabled), which wires `RERANK_BINDING=jina` / `RERANK_BINDING_HOST=http://backend:8000/lightrag/rerank` and hands LightRAG the adapter's bearer token as `RERANK_BINDING_API_KEY`. See the [backend README §5.1](../backend/README.md#51-lightrag--tei-rerank-adapter-post-lightragrerank-415).

For local Ollama graph RAG, use a fast non-reasoning model for `EXTRACT` and `KEYWORD`, and reserve the stronger answer model for `QUERY`:

```env
LIGHTRAG_LLM_MODEL=qwen3.8:latest
LIGHTRAG_EXTRACT_LLM_MODEL=mistral-small3.2:24b
LIGHTRAG_KEYWORD_LLM_MODEL=mistral-small3.2:24b
LIGHTRAG_QUERY_LLM_MODEL=qwen3.8:latest
```

Atlas intentionally does not ship those model names as defaults; deployments that do not set role variables keep the existing single-model behavior.

**Extract-role generation caps on native Ollama (#796).** To run the EXTRACT role on native Ollama while the other roles stay on LiteLLM, set the model, binding, host and key:

```env
LIGHTRAG_EXTRACT_LLM_MODEL=mistral-small3.2:24b          # required for a role on its own binding
LIGHTRAG_EXTRACT_LLM_BINDING=ollama
LIGHTRAG_EXTRACT_LLM_BINDING_HOST=http://host.docker.internal:11434
LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY=ollama              # required; any value, see the key note below
LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT=4096              # max output tokens per extract call
LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX=16384                 # context window for each extract call
```

In LightRAG v1.5.4, a role whose binding differs from the base binding (`openai`, through LiteLLM) sends no provider options unless you set role-scoped ones. Atlas sets the last two lines by default, and LightRAG reads them as `EXTRACT_OLLAMA_LLM_*`.

- **Why the caps matter.** Without them a degenerate extraction generates until a timeout fires, because Ollama's own `num_predict` default is unbounded.
- **Choosing `NUM_PREDICT`.**
  - A dense chunk can legitimately need several thousand output tokens, and a cap that cuts one off loses entities without any error.
  - Extraction results are cached, and the cache key ignores these options, so after raising the cap clear LightRAG's LLM cache (`POST /documents/clear_cache`) to re-extract.
- **Choosing `NUM_CTX`.** The same role runs three kinds of call, so the context window has to fit the largest:
  - the extraction prompt, about 1.75k tokens, with a paragraph chunk of up to 2000 tokens;
  - the gleaning pass, which resends the first answer;
  - merge summaries of up to 12000 input tokens.

  16384 covers all three plus the output cap. It overrides Ollama's VRAM-based default and any `OLLAMA_CONTEXT_LENGTH` for these calls. If other callers load the same model at a different context length, Ollama reloads it on each switch.
- **Keep them numeric.** LightRAG sends a non-integer value, including an empty one, as a string, and Ollama then rejects every extract call. Compose falls back to the defaults above when a value is empty.
- **When they apply.** Both caps take effect only while `EXTRACT` is bound to Ollama. Other bindings ignore them.
- **EXTRACT API key.**
  - An empty `LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY` becomes `${LITELLM_MASTER_KEY}` only while EXTRACT's effective host is LiteLLM, so an EXTRACT role routed to LiteLLM needs no key wiring (#796, #1271).
  - On native Ollama it is required: LightRAG needs a key for a role on its own binding, and Ollama ignores the value, so any string works. Left empty, the container stops at start and names the variable instead of sending Ollama the master key.

**Timeouts and failed chunks are upstream behaviour.** Checked against the pinned `lightrag-hku` 1.5.4 source:

- **Per-call timeout.**
  - `LIGHTRAG_EXTRACT_LLM_TIMEOUT` (`EXTRACT_LLM_TIMEOUT`, default `LLM_TIMEOUT`, which is 240 s) reaches the Ollama client as its HTTP timeout ([`lightrag_server.py#L1639-L1643`](https://github.com/HKUDS/LightRAG/blob/v1.5.4/lightrag/api/lightrag_server.py#L1639-L1643), [`ollama.py#L157`](https://github.com/HKUDS/LightRAG/blob/v1.5.4/lightrag/llm/ollama.py#L157)).
  - That timeout is per read, not per request. It works as a per-call deadline here only because extraction calls do not stream, so Ollama sends nothing until it finishes.
  - The hard wall-clock cap is the worker's `asyncio.wait_for` at twice the timeout ([`utils.py#L929-L938`](https://github.com/HKUDS/LightRAG/blob/v1.5.4/lightrag/utils.py#L929-L938), [`#L1274-L1277`](https://github.com/HKUDS/LightRAG/blob/v1.5.4/lightrag/utils.py#L1274-L1277)).
  - `num_predict` only stops a runaway before the timeout does when the model produces `NUM_PREDICT` tokens within it. At the defaults that means roughly 17 tokens per second (4096 in 240 s). On slower hosts, raise `LIGHTRAG_EXTRACT_LLM_TIMEOUT` too.
- **A failed chunk fails its document.**
  - One chunk that times out cancels the document's other chunk tasks ([`operate.py#L3746-L3779`](https://github.com/HKUDS/LightRAG/blob/v1.5.4/lightrag/operate.py#L3746-L3779)).
  - The whole document is marked `FAILED` with nothing merged into the graph ([`pipeline.py#L2534-L2554`](https://github.com/HKUDS/LightRAG/blob/v1.5.4/lightrag/pipeline.py#L2534-L2554)).
  - The drain moves on to the next document ([`pipeline.py#L1998-L2029`](https://github.com/HKUDS/LightRAG/blob/v1.5.4/lightrag/pipeline.py#L1998-L2029)), and the failed one is re-queued on the next processing pass.
  - Skipping just the chunk and keeping a partial graph for that document is not configurable in 1.5.4.

The Docling compatibility endpoint is derived rather than user-managed. It is populated only for `LIGHTRAG_SOURCE=container` with an enabled Docling source; localhost LightRAG receives an empty endpoint because the adapter deliberately has no host port.

Two security-relevant vars are auto-generated by the bootstrapper on first
launch and persisted to `.env`:

```env
LIGHTRAG_API_KEY=<auto>          # X-API-Key header for document/query routes; forwarded to LiteLLM
LIGHTRAG_TOKEN_SECRET=<auto>     # JWT signing secret for /login flows
```

LightRAG's default `WHITELIST_PATHS=/health,/api/*` leaves `/health` and the
Ollama-compatible `/api/*` chat routes open (Atlas does not override it; the
LiteLLM `lightrag` alias relies on that path). A Bearer header carrying the API
key is parsed as a JWT and rejected with 401, even alongside a valid
`X-API-Key`.

Without `LIGHTRAG_TOKEN_SECRET`, LightRAG falls back to a hardcoded default
JWT key (real security risk in any non-trivial deploy). Both are
generate-when-absent — hand-supplied values stick. Rotation requires a
litellm-init re-seed.

## 4. Usage

### 4.1. Web UI

Browse `http://lightrag.localhost:${KONG_HTTP_PORT}` (after `--setup-hosts`) or `http://localhost:${LIGHTRAG_API_PORT}/webui`. Upload documents, view the KG, run queries.

### 4.2. Native API

```bash
# Insert a document
curl -sX POST http://localhost:${LIGHTRAG_API_PORT}/documents/upload \
  -H "X-API-Key: ${LIGHTRAG_API_KEY}" \
  -F "file=@my-paper.pdf"

# Query
curl -sX POST http://localhost:${LIGHTRAG_API_PORT}/query \
  -H "X-API-Key: ${LIGHTRAG_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query": "/hybrid What is graph-augmented RAG?"}'
```

Query mode prefixes: `/hybrid`, `/local`, `/global`, `/naive`, `/mix`. Default is `/hybrid`.

### 4.3. Docling adapter protocol

LightRAG v1.5.4 submits document parsing to `POST /v1/convert/file/async` (multipart field `files`), polls `GET /v1/status/poll/{task_id}`, downloads `GET /v1/result/{task_id}`, and probes `GET /health`. Atlas implements exactly those routes in `docling-lightrag-adapter`. The adapter, LightRAG, and `docling-gpu` share only `docling-lightrag-network`; the adapter has no published port and no backend-network membership. LightRAG receives only the adapter endpoint, while the adapter alone receives `DOCLING_API_TOKEN` for its upstream call.

The adapter accepts at most two outstanding jobs by default and returns `429` before reading an upload when full. Result artifacts expire after 900 seconds by default and are deleted after download, failure, cancellation, or expiry. An expired job must be resubmitted. These values are controlled by `DOCLING_ADAPTER_MAX_JOBS` and `DOCLING_ADAPTER_RESULT_TTL_SECONDS` in the Docling manifest.

### 4.4. Via LiteLLM (recommended for other stack services)

LightRAG is registered with LiteLLM as the `lightrag` model when enabled. Any LiteLLM consumer (open-webui, openclaw, n8n, hermes, backend, local-deep-researcher, jupyterhub) can invoke it:

```bash
curl -sX POST http://localhost:${LITELLM_PORT}/v1/chat/completions \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "lightrag",
    "messages": [
      {"role": "user", "content": "/hybrid What is graph-augmented RAG?"}
    ]
  }'
```

## 5. Dependencies & Integrations

### 5.1. Current — Upstream (this service calls)

| Service | Category |
|---|---|
| neo4j | data |
| redis | data |
| supabase | data |
| litellm ↔ | llm |
| docling-lightrag-adapter | media |
| backend ↔ | apps |

### 5.2. Current — Downstream (services that call this)

| Service | Category |
|---|---|
| kong | infra |
| litellm ↔ | llm |
| celery | agents |
| hermes | agents |
| n8n | agents |
| backend ↔ | apps |

### 5.3. Architecture diagram

![lightrag architecture](./architecture.svg)

[Open the full-size diagram](./architecture.html) for a full-screen view.

### 5.4. Future — Missing pair integrations

_No high-confidence opportunities identified._

### 5.5. Future — Candidate new services

_No high-confidence opportunities identified._

### 5.6. Future — Unused features in this service

_No high-confidence opportunities identified._

## 6. Storage backend matrix

| Storage role | Default selector | Behavior when the external source is disabled |
|---|---|---|
| KV | `RedisKVStorage` on Redis `db=2` | Redis URI is cleared; selector remains `RedisKVStorage`. |
| Vector | `PGVectorStorage` on Supabase pgvector | Postgres URI is cleared; selector remains `PGVectorStorage`. |
| Graph | `Neo4JStorage` on Neo4j | Neo4j URI is cleared; selector remains `Neo4JStorage`. |
| Doc-status | `RedisDocStatusStorage` on Redis `db=2` | Redis URI is cleared; selector remains `RedisDocStatusStorage`. |

A cleared URI with the default selector makes LightRAG fail at startup (the bootstrapper prints a warning naming the selector); set the `LIGHTRAG_*_STORAGE` variable to a local class to run without that backend. The KV and doc-status stores in Redis `db=2` are not covered by the stack backup (`services/backup`), so a restore brings back the graph and vectors without the documents and chunks they reference. `LIGHTRAG_WORKERS` has no effect: the server runs as a single uvicorn process.

## 7. Init container

`lightrag-init` runs once per `docker compose up`. It:

1. Runs after LiteLLM's Compose health gate and reads LiteLLM `/v1/models`.
2. Resolves the base `LIGHTRAG_LLM_MODEL` / `LIGHTRAG_EMBEDDING_MODEL` / `LIGHTRAG_EMBEDDING_DIM` from explicit overrides, LiteLLM defaults, or LiteLLM's model list, then writes LightRAG's native `LLM_MODEL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` to `/app/data/.env`. If no chat model can be resolved, init exits non-zero instead of starting LightRAG with an empty `LLM_MODEL`. Role-specific `LIGHTRAG_EXTRACT_*`, `LIGHTRAG_KEYWORD_*`, and `LIGHTRAG_QUERY_*` variables are passed directly to the runtime container.
3. Polls Postgres until it accepts connections (a readiness gate — `supabase-db` is SOURCE-replaceable, so `lightrag-init` intentionally has no hard compose `depends_on` on it), then runs the idempotent pgvector migration. The Neo4j migration runs separately and is non-fatal — it pre-creates the range index on `(:base).entity_id` that LightRAG otherwise creates on first write. It posts to the HTTP endpoint matching the Bolt URI's host: `neo4j-graph-db:7474`, or `NEO4J_LOCALHOST_HTTP_PORT` on the host for `NEO4J_GRAPH_DB_SOURCE=localhost`.

## 8. Troubleshooting

- **First boot exceeds health-check timeout** — `start_period` is 300 s. Initial tokenizer, embedding-model, and document-parser setup can take several minutes.
- **First boot logs missing PostgreSQL tables** — expected on a cold volume. LightRAG probes for its tables, logs relation-missing errors, then creates the tables and indexes before reporting healthy.
- **`OPENAI_API_KEY` warning at startup** — LightRAG checks env even when using `openai`-compatible Ollama. Harmless; the actual key is the `LITELLM_MASTER_KEY` forwarded as `LLM_BINDING_API_KEY`.
- **`lightrag: LightRAG <ROLE> role uses binding … and has no API key`, then the container restarts** — a role with its own binding or host points somewhere other than the in-network LiteLLM, and its key is empty. `init/scripts/resolve-role-keys.py` stops before LightRAG starts rather than sending that host the LiteLLM master key (#1271). Set the named `LIGHTRAG_<ROLE>_LLM_BINDING_API_KEY` in `.env`; for native Ollama any value works.
- **A role shows `openai` at `http://litellm:4000` although `LIGHTRAG_LLM_BINDING` points at native Ollama** — expected for a role whose model declares catalog `request_defaults` such as `think: false`. A native LightRAG 1.5.4 binding does not send them, so the role stays on LiteLLM (#658). To run it natively anyway and accept the reasoning cost, set its binding, host and key explicitly. See [§3](#3-configuration).
- **Empty KG after ingestion** — verify `LIGHTRAG_LLM_MODEL` actually points at a chat-capable model. Some embedding-only Ollama tags will silently produce empty triples.
- **Rerank does not run even when TEI is enabled** — expected unless the rerank adapter is enabled. LightRAG's direct rerank clients and TEI's `/rerank` request body are incompatible, so Atlas emits `RERANK_BINDING=null` by default. To turn reranking on, set `LIGHTRAG_RERANK_ADAPTER_ENABLED=true` (with `TEI_RERANKER_SOURCE` enabled) so LightRAG reranks through the backend adapter (`POST /lightrag/rerank`, #415); `./start.sh doctor` warns if the flag is on but TEI/LightRAG is off.
- **`pgvector` dim mismatch** — without an explicit `LIGHTRAG_EMBEDDING_DIM`, init takes the dimension from a built-in table of known models (nomic-embed-text 768, qwen3-embedding:0.6b 1024, bge-m3 1024, mxbai-embed-large 1024, OpenAI's text-embedding-3-small/-large 1536/3072); for any other model it probes LiteLLM and falls back to 768 if the probe fails (for example before `ollama-pull` has finished on first boot), so set `LIGHTRAG_EMBEDDING_DIM` for models outside that table. Drop and rerun the migration when changing `LIGHTRAG_EMBEDDING_DIM`: `psql ... -c "DROP SCHEMA lightrag CASCADE"` then restart.

## 9. Capabilities & limitations

Support tier: **experimental** — Capability contract declared (#967); no cited cold-start, workflow, or upgrade qualification run yet (evidence at `v0.1.0`).

| Capability | Status | Verification | Notes |
|---|---|---|---|
| Graph-augmented retrieval through LiteLLM | supported | tested | Atlas resolves LightRAG chat and embedding models through LiteLLM and exposes graph-aware query modes. |
| External persistent storage | partial | tested | Atlas wires Supabase pgvector, Neo4j, and Redis when enabled; disabling them clears connection URIs without selecting file-backed storage implementations. |
| LightRAG reranking | partial | tested | Reranking requires TEI plus the opt-in backend adapter because direct LightRAG-to-TEI request payloads are incompatible. |
