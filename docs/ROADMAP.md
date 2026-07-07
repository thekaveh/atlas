# Atlas Roadmap

This document outlines future development plans and enhancements for Atlas.

## 1. Current status

The stack now orchestrates 53 service families (48 containerized + 5 virtual — re-derived from `services/*/service.yml`) across AI inference, workflow automation, data science, distributed compute (Ray, Spark), DAG orchestration (Airflow), notebook UIs (JupyterHub, Zeppelin), observability (Prometheus + Grafana + Loki + Tempo + OTel), document processing, speech, and the Supabase ecosystem. An additional set of candidate services is tracked across the Tier 1/2/3 sections below, including labelled sub-sections for the **3D / game-generation**, **financial / trading-AI**, and **RAG-enhancement** strategic tracks. Architectural milestones to date:
- Dynamic Kong API Gateway configuration
- Python cross-platform bootstrapping with CLI SOURCE overrides
- Service integration spanning Ollama, ComfyUI, n8n, Open WebUI, SearxNG, Supabase, Neo4j, OpenClaw, Weaviate, JupyterHub, and more

## 2. Development roadmap

### 2.1 Completed

**JupyterHub data science IDE**
- Interactive Jupyter Lab environment
- Pre-configured AI/ML libraries (Ollama, LangChain, LlamaIndex, Transformers)
- Sample notebooks for all service integrations
- Persistent workspace with Docker volumes

**Speech-to-Text layer (pluggable)**
- OpenAI-compatible `/v1/audio/transcriptions` across all backends
- Speaches (Faster-Whisper) — CPU-friendly default, multilingual
- NVIDIA Parakeet-TDT — CC-BY-4.0 SOTA for English/EU langs
- whisper.cpp — first-class Apple Silicon (Metal + Core ML / ANE)
- Parakeet-MLX — alternative macOS-native option
- Integration with Open WebUI for voice chat

**Text-to-Speech layer (pluggable)**
- OpenAI-compatible `/v1/audio/speech` across all backends
- Speaches (Kokoro + Piper voices) — CPU-friendly default
- Chatterbox (Resemble AI, MIT) — 5-sec zero-shot voice cloning, 23 langs
- Previously shipped with XTTS v2 (CPML / non-commercial) — retired
  2026-05 after the openedai-speech upstream archived its image

**Document processing service (Docling)**
- Document processing with IBM Docling
- PDF, DOCX, PPTX, HTML, image parsing
- Table extraction (DocLayNet + TableFormer)
- RAG-ready chunking with structure awareness
- GPU acceleration (4.3x speedup for tables)

**Unified LLM gateway (LiteLLM)**
- Always-on OpenAI-compatible front door for every LLM provider. Pinned image: `ghcr.io/berriai/litellm:v1.83.14-stable.patch.2`. Listens on port 63040.
- Wizard model: locked LiteLLM tile + selectable LLM Engine (single-select Ollama upstream: `ollama-container-cpu/gpu`, `ollama-localhost`, `none`) + three multi-enable Cloud tiles (OpenAI, Anthropic, OpenRouter).
- Bootstrapper auto-generates `LITELLM_MASTER_KEY` on first start and refuses to start when no upstream is configured (engine=none + all cloud disabled).
- Persistence: dedicated `litellm` database on the existing Supabase Postgres (Prisma migrations run automatically). Redis used for response cache + rate-limit state.
- Consumers (Backend, Open WebUI, n8n, JupyterHub, Local Deep Researcher, OpenClaw Gateway, Weaviate) all read `LITELLM_BASE_URL` + `LITELLM_API_KEY`. Documented backup option: Portkey AI Gateway.

**LangMem persistent memory**
- Automated fact extraction from conversations via Ollama LLM
- Semantic memory recall via Weaviate with pgvector fallback
- Memory consolidation and deduplication (nightly via n8n)
- Open WebUI tool for manual memory management (remember, recall, forget)
- Auto-extraction filter for conversations
- Embedded in Backend service (no separate container)
- Dual vector backend: Weaviate preferred, pgvector fallback

**MinIO object storage (artifact tier)**
- S3-compatible artifact-tier storage server (Go, AGPL-v3). Pinned to the dated Docker Hub release tag `minio/minio:RELEASE.2025-09-07T16-13-09Z`; the GitHub-only service-account-CVE release `RELEASE.2025-10-15T17-29-55Z` is not yet available as a Docker image — bump the pin when a fixed image lands upstream.
- Five pre-provisioned buckets — `comfyui`, `backend`, `n8n`, `jupyter`, `docling` — each with a scoped service-account credential surfaced as `MINIO_<NAME>_ACCESS_KEY` / `MINIO_<NAME>_SECRET_KEY` in `.env`.
- Admin console at `http://localhost:63021`; S3 API at `http://localhost:63020` (host) / `http://minio:9000` (internal).
- Complements Supabase Storage rather than replacing it. Per-consumer wiring (ComfyUI, Backend, n8n, JupyterHub, Doc Processor) ships in dedicated follow-up PRs — credentials and bucket names are in `.env` from day one for opt-in by env-only change.

**Ray distributed-compute cluster**
- Apache-2.0; the de-facto 2026 OSS distributed-compute framework. Generic substrate for "run N independent units of work in parallel across many CPUs and/or GPUs."
- `services/ray/` ships head + worker containers under `RAY_SOURCE` with variants `ray-container-cpu`, `ray-container-gpu`, and `disabled`. (Authenticated remote Ray endpoints are deferred to the stack-wide authenticated-remote design.)
- Wizard wires `RAY_WORKER_COUNT` inline on the source step via the `SecondaryNumberInput` widget (the same pattern later generalised for the localhost-port override).
- Backend exposes `/api/ray/{submit,status,stop,cluster-status}` REST endpoints gated on `RAY_ADDRESS` — returns 503 when Ray is disabled.
- JupyterHub picks up `ray[client]` in its build image and ships a seeded `07_ray_cluster.ipynb` notebook.
- Hermes Agent + Backend agents can dispatch compute jobs to the cluster via the Backend REST surface (Ray's `JobSubmissionClient` is not exposed directly outside Backend today).
- Pipeline-agnostic by design — useful to every strategic track: parallel backtest sweeps (financial / trading-AI), parallel batch rendering and asset preparation (3D / Dreamscapes), parallel embedding pipelines and batch reranking (RAG specializations), Ray Tune parameter sweeps (data engineering).
- **Honest scope note (preserved from the Tier 1 candidate write-up):** Ray's throughput value scales with parallel inference capacity. On a single-GPU Mac dev machine with no Docker GPU passthrough, Ray adds orchestration polish (pipeline chaining, retries, result aggregation, checkpointing) but **not transformative throughput** — a well-written `asyncio` script captures ~70–80% of the value at that scale. Ray earns its keep when crossing into (a) multi-GPU Linux hosts, (b) batch jobs in the millions-of-units range, or (c) workloads long enough that fault tolerance and resumability matter.
- **Dask** (BSD-3) remains documented as the lighter single-machine alternative if Ray's footprint feels heavy for a given deployment.

**LightRAG (graph-enhanced RAG framework)** — shipped 2026-06-05
- Graph + vector hybrid retrieval framework combining knowledge graph extraction with semantic search
- 52-85% comprehensiveness gains over traditional vector-only RAG (EMNLP 2025, peer-reviewed)
- 5 query modes: naive, local, global, hybrid, mix; `LIGHTRAG_SOURCE` variants: `container`, `localhost`, `disabled`
- Init container runs pgvector and Neo4j migrations on first start
- Hermes Agent wired as a `rag_query` tool when both services are enabled; n8n and Backend consume the API
- Storage: Neo4j (graph), Supabase pgvector (vector), Redis (KV + doc-status); all adaptive vars cleared when `LIGHTRAG_SOURCE=disabled`
- Kong host-alias `lightrag.localhost` (HTTP at `LIGHTRAG_API_PORT`, default 63074); `LIGHTRAG_API_KEY` auto-generated by bootstrapper

**TEI Reranker (Text Embeddings Inference + reranker mode)** — shipped 2026-06-05
- HuggingFace's high-performance embedding and reranking inference server (Apache-2.0; Rust/CUDA core)
- OpenAI-compatible `/embeddings` + dedicated `/rerank` routes; `TEI_RERANKER_SOURCE` variants: `container-cpu`, `container-gpu`, `localhost`, `disabled`
- Default model: `mixedbread-ai/mxbai-rerank-base-v1` (arm64-compatible; the originally spec'd BGE-reranker-v2-m3 crashed the arm64 candle backend on warmup, swapped 2026-06-07); LightRAG direct reranking remains disabled by default because LightRAG's built-in rerank clients and TEI's `/rerank` endpoint use different request body shapes
- Kong host-alias `rerank.localhost` (HTTP at `TEI_RERANKER_PORT`, default 63041); model weights cached in a named volume

---

### 2.2 TUI & developer experience

**Upgrade Textual for native log selection + copy**
- The live-logs pane (`bootstrapper/ui/textual/widgets/log_pane.py`, a `RichLog` subclass) currently can't be click-drag-selected: Textual 0.89's mouse capture pre-empts the terminal's own selection, and in-app text selection (`ALLOW_SELECT` / `Screen.get_selection` / copy bindings) only landed in **Textual 1.0**. Bumping the pinned `textual>=0.85` (currently resolving to 0.89.1) to the latest 1.x/3.x line brings click-drag selection + copy to the logs pane — and every other widget — out of the box, so no bespoke copy affordance is needed.
- Caveats to handle during the bump: breaking CSS / message-API changes across the 1.0 and 3.0 boundaries (the entire wizard + launch + streaming-logs TUI is built on 0.89 APIs), re-verifying the pinned `textual-image>=0.12,<0.13` still co-installs, and preserving the Python 3.10 floor (`textual-image` 0.13+ requires 3.12).
- Interim workaround for users today (no upgrade needed): hold **Option (⌥, macOS)** / **Shift** and drag to use the terminal's own selection, bypassing Textual's mouse reporting. Textual 0.89 does expose `App.copy_to_clipboard()` (OSC 52), so a stop-gap "copy visible logs" key is also possible without the upgrade if needed before then.

---

### 2.3 Tier 1: high-priority candidates

**Enhanced vector search (Weaviate optimization)**
- Multi-model embedding support
- Advanced query capabilities
- Performance optimizations for large datasets
- Better integration with research workflows

**Python migration completion**
- Complete migration from Bash to Python for all scripts
- Cross-platform compatibility
- Dependency management with UV
- Improved error handling and logging

_Delivered — see "Completed" section below for the LiteLLM gateway entry._

**Per-service configuration modularization** — ✅ delivered (Phases A–E, May 2026; fragment/container counts below are as of that milestone)
- Compose: 1,425-line monolithic `docker-compose.yml` → 55-line thin `include:`-shell that pulls in `services/<name>/compose.yml` fragments (one per service family — supabase, redis, minio, neo4j, litellm, ollama, weaviate, comfyui, n8n, open-webui, backend, searxng, jupyterhub, parakeet, speaches, chatterbox, docling, openclaw, hermes, local-deep-researcher, kong, plus virtual cloud-providers, tts-provider, and globals manifests).
- Manifests: `services/<name>/service.yml` is the single source of truth for env vars (with auto_managed/secret flags), source variants, image refs, and dependencies. JSON-schema-validated. Bootstrapper runtime data lives under per-manifest `runtime_sc:`/`runtime_adaptive:`/`runtime_deps:` blocks; `bootstrapper/services/sc_synthesizer.py` reassembles the dict that `ConfigParser.load_yaml_config()` returns.
- Safety nets: `bootstrapper/services/manifest_validator.py` (cross-manifest checks), `tools/validate_fragments.py` CLI lint with `--check-env-example`, and `tests/test_fragment_equivalence.py` (golden `rendered_config_baseline.yml` diff — byte-equivalence proven across the 36-container stack).
- Locality: every service's source code, init scripts, build context, and config files live under `services/<name>/<subdir>/`. Repo top-level is just `bootstrapper/`, `docs/`, `services/`, plus standard files.

**Monitoring stack (Prometheus + Grafana)** — *Shipped 2026-05-31 (observability bundle); JupyterHub + Hermes scrape jobs subsequently removed as unreachable.*
- ✅ Prometheus scraper + TSDB with bundled node-exporter (host metrics) and cAdvisor (container metrics), bundled as `services/prometheus/`.
- ✅ Grafana with 7 pre-provisioned dashboards (stack overview, LiteLLM, Kong, Postgres+Redis, Containers+Host, n8n, app-tier) — `services/grafana/`.
- ✅ 13 scrape targets — Kong, LiteLLM, Weaviate, n8n + n8n-worker, MinIO, Backend, Prom+Grafana self, node-exporter, cAdvisor, plus postgres-exporter and redis-exporter sidecars. (JupyterHub + Hermes scrape jobs were removed as unreachable post-ship — JupyterHub ships single-user, no `/metrics`; the upstream Hermes image has no exporter. See `services/prometheus/README.md` §4 and the CHANGELOG entry.)
- ✅ Unified Grafana alerting enabled (no separate Alertmanager); contact points / rules to be added by users.
- ✅ Loki (logs) + Tempo (traces) + OpenTelemetry collector — the full observability triangle, shipped as `services/{loki,tempo,otel-collector}/` with Tempo + Loki datasources provisioned in Grafana (`services/grafana/config/provisioning/datasources/tempo-loki.yml`).

**Enhanced security features**
- Service-to-service authentication
- API rate limiting enhancements
- Audit logging capabilities
- Security hardening guides

**MCP gateway and curated server set**

The Model Context Protocol (Anthropic, late 2024) turns LLM-callable tools into a uniform protocol so every LLM consumer in the stack (Open WebUI, Backend, Hermes, n8n, OpenClaw, Local Deep Researcher) reaches the same tool surface through one interface. Architecturally the deployment is **three layers**:

```
Layer 3 — Consumers  : Open WebUI · Backend · Hermes · n8n · OpenClaw · LDR
                         │
                         ▼  (MCP protocol natively, OR OpenAPI via the aggregator)
Layer 2 — Aggregator : one of two options below
                         │
                         ▼  (MCP protocol)
Layer 1 — MCP servers : N small adapter containers wrapping individual
                        stack services (postgres-mcp, neo4j-mcp, …)
```

Two architecturally distinct adoption paths are supported. Both are real OSS in 2026. Either can ship.

#### Option A (recommended default): MetaMCP + curated server sidecars

- **MetaMCP** (metatool-ai, MIT, v2.4.22 Dec 2025, 2.3k+ stars) — single aggregator container, **native OpenAPI surface** (no separate translator needed), namespace-based RBAC for per-consumer tool scopes (Open WebUI can see one set, Hermes another), Postgres-backed metadata co-located on the existing Supabase Postgres.
- **MCP server sidecars** — small (50–100 MB) adapter containers connecting to existing stack services via their native protocols (Postgres wire, Bolt, REST/gRPC). Existing service images are **unchanged**.
- **No `/var/run/docker.sock` mount required.** No special privileges.
- Compose footprint: 1 aggregator + N small sidecars + 1 schema on the existing Supabase Postgres.

**Phase 1 starter set (three MCP servers):**
- `mcp/postgres` (Anthropic official, MIT) → Supabase queries
- `mcp/neo4j` (Neo4j official, MIT) → Cypher queries
- `mcp-server-searxng` (community) → web search via existing SearXNG

**Phase 2 expansions (as value is proven):**
- `mcp-weaviate` (community — evaluate quality before pinning) → vector search
- `mcp-server-s3` against MinIO → artifact ops
- `n8n-mcp` (community) → workflow triggering and inspection
- Custom Backend MCP server (~150–200 LOC Python wrapping app-specific routes including LangMem)

#### Option B (documented alternative): Docker MCP Gateway + Catalog + mcpo

- **Docker MCP Gateway** (`docker/mcp-gateway`, MIT, v0.42.1 May 2026) — single binary / container. Pulls MCP server images from the **Docker MCP Catalog** (hub.docker.com/mcp, 300+ vendor-signed images) **on demand**, spawning them as sibling containers via the mounted host docker socket.
- **`mcpo`** (Open WebUI org, MIT) — small protocol translator. Required in front of the Gateway because the Gateway speaks MCP only (no native OpenAPI). Open WebUI consumers can hit MCP directly; FastAPI Backend / n8n / OpenClaw typically go through `mcpo`.
- **Requires `/var/run/docker.sock` mount** so the Gateway can spawn sibling containers. This is a non-trivial container-escape attack surface — a compromise of the Gateway grants effective root-equivalent control of the host's Docker daemon.
- Compose footprint: 1 Gateway service + 1 `mcpo` service + dynamically-spawned server containers (lifecycle managed by the Gateway, not declared in compose).

**When Option B beats Option A:**
- Deployment integrates many SaaS-vendor MCP servers out of the box (Stripe, GitHub, Notion, AWS, MongoDB Atlas, Grafana Cloud, etc.) — the 300+ catalog is the genuine differentiator.
- Vendor-signed image provenance is a hard organisational requirement.
- *For most self-hosted AI-stack deployments wrapping their own internal services, Option A wins on every other axis.*

#### Coverage matrix — which stack services need MCP, and which don't

MCP is for **LLM-callable tools**, not arbitrary service-to-service integration. These categories help avoid wasted wrapper work:

**Worth MCP-wrapping (target of an MCP server adapter):**
- **Data stores** — Supabase (Postgres), Neo4j, Weaviate, Redis (limited use-cases)
- **Search** — SearXNG
- **Storage** — MinIO (via generic S3 MCP)
- **Workflow** — n8n (triggering / inspection)
- **Notebooks** — JupyterHub (execute / inspect; community implementations vary)
- **Custom application surface** — Backend (FastAPI) custom routes (LangMem operations, app-specific endpoints) — requires writing the wrapper ourselves

**Already reachable through other paths (MCP-wrapping would be redundant):**
- **LiteLLM, Ollama** — they *are* the LLM endpoint; MCP-wrapping is circular
- **ComfyUI, Speaches, Parakeet, Chatterbox, Docling** — OpenAI-compatible HTTP surfaces routed via LiteLLM; LLMs reach them directly
- **Cloud Providers** (virtual manifest) — toggles only

**Consumers, not targets** (they *use* MCP rather than expose it):
- **Hermes, Open WebUI, Local Deep Researcher, OpenClaw**

**Infrastructure with no LLM-callable surface:**
- **Kong, Globals, Bootstrapper, TTS Provider / STT Provider** (virtual manifests)

So "the full MCP-ification of the stack" is realistically ~6–9 MCP servers, not one-per-service. The Phase 1 starter set covers the most useful 3.

**Stack integration points:**

Depends on (services the MCP gateway would consume):
- **MCP server adapters** wrapping individual stack services (Postgres / Neo4j / Weaviate / SearXNG / MinIO / n8n / custom Backend)
- **Kong API Gateway** — exposes the MCP gateway via an `mcp.localhost` route
- **Supabase (PostgreSQL)** — MetaMCP namespace + auth persistence (Option A), or Gateway's small config (Option B)

Consumed by (services that would call the MCP gateway):
- **Open WebUI** — native MCP client (v0.6.31+); reaches MCP directly
- **Backend (FastAPI)** — programmatic tool invocation via OpenAPI (Option A native; Option B via `mcpo`)
- **Hermes** — agent tool surface
- **n8n** — workflow nodes invoking MCP tools
- **OpenClaw** — messaging-platform agents reuse the same tool catalog
- **Local Deep Researcher** — research workflows tap MCP-exposed sources

**Langfuse — LLM observability and evaluation** — ✅ Shipped
- Open-source LLM tracing, prompt management, dataset, and evaluation platform (MIT)
- LiteLLM ships a **native Langfuse callback** — zero-glue integration of every gateway-routed call (consumers automatically gain traces)
- Closes the LLM-specific observability gap that Prometheus + Grafana (infra metrics) does not address: prompt versioning, prompt-level cost/latency attribution, eval scoring, conversation replay
- Self-hosted footprint reuses the stack's Postgres + Redis + MinIO; adds ClickHouse for high-volume trace events

**Stack integration points:**

Depends on (services Langfuse consumes):
- **Supabase (PostgreSQL)** — control-plane metadata
- **ClickHouse** — high-volume trace event store (added alongside)
- **Redis** — queueing and rate-limit state
- **MinIO** — large-payload offload (optional)

Consumed by (services that would call Langfuse):
- **LiteLLM gateway** — primary callback emitter; all routed traffic gets traced
- **Hermes** — agent traces with tool-call spans
- **Backend (FastAPI)** — application-level traces
- **n8n** — workflow LLM-node tracing
- **Open WebUI** — chat-session traces
- **Local Deep Researcher** — multi-step research trace timelines

**Infisical — secrets management**
- Open-source secrets manager (API + UI) with versioning, rotation, audit logs, and per-environment scoping
- Removes the current pattern of every service reading credentials directly from `.env`; secrets are fetched at runtime (or injected at container start via the Infisical agent)
- Critical preparation step before the stack adopts use-cases that hold third-party credentials at scale: exchange API keys (financial / trading track), signed model-download URLs (3D track), tenant-scoped API tokens (RAG SaaS variants)
- Self-hosted footprint reuses the existing Supabase Postgres and Redis

**Stack integration points:**

Depends on (services Infisical would consume):
- **Supabase (PostgreSQL)** — secret and version storage
- **Redis** — caching and session state

Consumed by (services that would call Infisical):
- **All `.env`-reading services** — Backend, n8n, Hermes, OpenClaw, LiteLLM, Weaviate, Neo4j integrations, ComfyUI, JupyterHub, and the trading- / 3D-track candidates — via the Infisical agent / SDK injection pattern at container start
- **Bootstrapper** — `.env` generation can pull live values from Infisical instead of hard-coding them in `.env.example`

**OpenBao — production-grade key custody (complement to Infisical)**
- Open-source fork of HashiCorp Vault (MPL-2.0; Linux Foundation–stewarded), pre-dating Vault's BSL relicense. Provides a Transit engine where private keys never leave the vault — encrypt and sign happen inside.
- Different threat model from **Infisical** (Tier 1): Infisical is the right home for *ops secrets* (`.env` rotation, third-party-API keys for non-financial services). OpenBao is for *high-value cryptographic keys* — trading-account signing keys, web3 / wallet keys, code-signing keys, JWT signers.
- Pair with a hardware root (YubiHSM, AWS / GCP / Azure cloud KMS) for the unseal key; per-consumer scoped tokens with deny-by-default policies.
- Phase-2 prerequisite for the financial / trading-AI track moving from paper to live trading.

**Stack integration points:**

Depends on (services OpenBao would consume):
- **Supabase (PostgreSQL)** — storage backend (alternative to file or Raft storage)
- Optional hardware root (YubiHSM / cloud KMS) for unseal-key custody

Consumed by (services that would call OpenBao):
- **NautilusTrader / Hummingbot workers** (trading track) — request signatures for orders without ever holding the private key
- **Backend (FastAPI)** — application-level signing operations (audit-trail anchors, signed promotion records, signed scene-export URLs)
- **n8n / Windmill** — workflow steps that need cryptographic signing
- **Audit-sealer worker** — Merkle-anchor signing for the immutable-archive pipeline

---

### 2.4 Tier 2: planned candidates

> Entries marked ✅ shipped (e.g. Hermes) have landed in the stack; their write-ups stay here for the original evaluation context.

#### Cross-cutting infrastructure

These services are pipeline-agnostic — useful to every strategic track (3D / game-generation, financial / trading-AI, RAG specializations, data-engineering) rather than belonging to any one. **Ray** was promoted out of this sub-section to Tier 1 (2026-05-22) and shipped 2026-05-24 — see the Completed section above; only E2B remains here today.

**E2B (self-hosted) — Firecracker sandbox for untrusted code (cross-cutting)**
- Apache-2.0; the 2026 reference for safely executing untrusted code (LLM-generated, user-uploaded, or agent-authored). Firecracker microVM isolation, not just container kernel-sharing.
- **Trading:** LLM-generated strategy code (FinGPT / FinRL outputs, agent-tuned variants), promotion-gate go / no-go execution.
- **Dreamscapes / 3D:** user-authored or LLM-authored procedural-generation rules, scene scripts, custom shaders, asset-pipeline plugins.
- **Open WebUI / Hermes:** code-interpreter mode for agent tool calls; sandboxed n8n custom-code nodes.
- **Daytona** (Apache-2.0) is the lighter Docker-rootless alternative but kernel-sharing is *not* sufficient for untrusted code; use Daytona only for trusted (human-authored) research notebooks.

**Stack integration points:**

Depends on (services E2B would consume):
- **MinIO** — code and artifact storage
- **OpenSearch** — execution-trace search and forensic queries

Consumed by (services that would call E2B):
- **Hermes** — LLM-generated tool-call execution
- **Open WebUI** — code-interpreter mode for chat
- **Backend (FastAPI)** — sandbox-managed code runs for any application feature
- **Hummingbot API** — sandboxed strategy evaluation
- **Windmill** — promotion-gate flows and any "run untrusted code" workflow step
- **n8n** — custom-code workflow nodes

#### Specialized capabilities

**LangFlow (AI workflow & agent builder)**
- Low-code visual builder for AI agents and RAG pipelines
- Python-native extensibility aligned with the AI/ML ecosystem
- Complements n8n: LangFlow for deep AI pipelines, n8n for system orchestration
- MIT licensed, 147k+ GitHub stars, backed by DataStax

**Stack integration points:**

Depends on (services LangFlow would consume):
- **Ollama** — local LLM inference for model nodes
- **Weaviate** — vector store for RAG retrieval pipelines
- **Supabase (PostgreSQL)** — flow persistence and metadata storage
- **Redis** — caching and session state
- **SearxNG** — web search tool for AI agents

Consumed by (services that would call LangFlow):
- **n8n** — orchestrates business logic around LangFlow AI endpoints
- **Backend (FastAPI)** — invokes LangFlow flows as API calls
- **JupyterHub** — notebook-based experimentation with LangFlow APIs
- **Open WebUI** — potential frontend for LangFlow-powered agents

**RAG-Anything (multimodal document processing for RAG)**
- All-in-one multimodal RAG framework handling text, images, tables, equations, charts, and multimedia
- Built by the same team as LightRAG (HKUDS), designed as its multimodal preprocessor
- Unified knowledge entity processing across all document modalities
- Complements Docling: Docling excels at structured document parsing (PDF tables, layout analysis), RAG-Anything excels at multimodal knowledge entity extraction for graph construction

**Stack integration points:**

Depends on (services RAG-Anything would consume):
- **Ollama** — LLM inference for multimodal entity extraction (already deployed)

Consumed by (services that would call RAG-Anything):
- **LightRAG** — primary consumer for multimodal document ingestion into knowledge graphs
- **Backend (FastAPI)** — multimodal document processing API

**SilverBullet — server-first markdown personal knowledge management**
- Server-hosted markdown PKM (MIT); notes are plain `.md` files on disk with a Lua-scriptable query and automation layer
- Different shape from Obsidian / Logseq (client-centric) and AppFlowy / AFFiNE (database-backed) — files-on-disk is what RAG ingestion wants
- Becomes the authored-content surface in a personal-RAG workflow: human-curated notes flow directly into LightRAG / Weaviate / OpenSearch indexes

**Stack integration points:**

Depends on (services SilverBullet would consume):
- None at runtime (filesystem volume only)

Consumed by (services that would call SilverBullet):
- **LightRAG** — ingestion source for entity-graph construction over authored notes
- **Weaviate** — alternative vector ingestion target (semantic search across notes)
- **Backend (FastAPI)** — read / search / edit-note APIs exposed via Kong route
- **Open WebUI** — knowledge tools can cite SilverBullet pages

**Karakeep — AI-native bookmark, note, and image hoarder**
- Self-hosted personal-RAG capture surface (formerly Hoarder, MIT); auto-tagging via Ollama, OCR'd image search, browser extension and mobile apps
- Complements SilverBullet — Karakeep is *captured* content (links, articles, screenshots), SilverBullet is *authored* content. Both feed the same downstream RAG indexes.
- Roadmapped embedding and RAG features upstream; today's value is a MeiliSearch-indexed, AI-tagged personal corpus

**Stack integration points:**

Depends on (services Karakeep would consume):
- **Ollama** — auto-tagging and summarization model calls (built-in)
- **MeiliSearch** — internal full-text index (bundled by Karakeep)

Consumed by (services that would call Karakeep):
- **Backend (FastAPI)** — query saved items as a RAG retrieval source
- **LightRAG** — ingestion of bookmark archives into the entity graph
- **Weaviate** — semantic search over captured content
- **n8n** — capture-trigger workflows (e.g. "RSS item → Karakeep")

**Crawl4AI — LLM-friendly web crawler** — ✅ Shipped
- Apache-2.0 Playwright-based crawler purpose-built for LLM ingestion: clean Markdown output, structured extraction strategies, sitemap traversal, JavaScript-rendered page support
- Apache-2.0 license is intentional — Firecrawl-self-hosted (AGPL-3.0) is incompatible with this stack's permissive boilerplate posture
- Fills the web-ingestion gap for both RAG (article corpora) and trading-AI (filings / news / sentiment scrape) use-cases
- Lighter than running a full Browserless cluster for one-shot scrapes; pair them when both batch crawl and persistent sessions are needed

**Stack integration points:**

Depends on (services Crawl4AI consumes):
- None at runtime (bundled Chromium)
- Optional **MinIO** — large-output offload bucket

Consumed by (services that would call Crawl4AI):
- **Hermes** — web-fetch tool for agents
- **n8n** — HTTP node invokes crawl jobs as workflow steps
- **Backend (FastAPI)** — programmatic ingest endpoints
- **LightRAG** — bulk ingestion source for fresh web content
- **Local Deep Researcher** — supplements SearXNG metasearch with deep page extraction

**GROBID — scholarly PDF extraction**
- Apache-2.0 machine-learning PDF parser specializing in scholarly / research documents (citation extraction, header / section structure, bibliography normalization, table-of-figures)
- Complements Docling: Docling is general-purpose structured parsing (DocLayNet + TableFormer); GROBID is scholarly-specific (TEI XML output, citation graphs, DOI / PubMed enrichment)
- Critical for research-RAG and financial-research-RAG (analyst reports, 10-K / 10-Q filings, earnings transcripts) where citation lineage matters

**Stack integration points:**

Depends on (services GROBID would consume):
- None at runtime

Consumed by (services that would call GROBID):
- **Backend (FastAPI)** — scholarly-PDF ingestion endpoints
- **LightRAG** — extracted citations feed the entity graph naturally
- **n8n** — batch document-processing workflows
- **JupyterHub** — research-notebook citation workflows

**Apache Tika — broad-format text and metadata extraction** — ✅ Shipped
- Apache-2.0 content analysis toolkit supporting 1000+ formats (Office docs, email, archives, audio / video metadata, source code, structured data)
- Pre-Docling fallback for the long tail of formats Docling does not target (mailbox archives, legacy Office, multimedia metadata)
- Standalone server mode exposes a REST endpoint suitable for direct consumption by n8n / backend
- Pairs naturally with Stirling-PDF (PDF preprocessing) and GROBID (scholarly PDF) to cover the full ingestion-format matrix

**Stack integration points:**

Depends on (services Apache Tika consumes):
- None at runtime (JVM only)

Consumed by (services that would call Apache Tika):
- **Backend (FastAPI)** — generic file-extraction route
- **n8n** — format-detection and extraction nodes in ingestion workflows
- **LightRAG / Weaviate** — upstream of indexing for non-Docling formats

**Stirling-PDF — PDF preprocessing toolkit**
- Self-hosted PDF manipulation suite (MIT): split / merge / rotate / sign / redact / OCR-via-Tesseract, plus a web UI for ad-hoc operations
- Sits *before* Docling and GROBID in the ingestion pipeline — clean up malformed / scanned / multi-document PDFs so structured parsers can do their job
- Closes the "user uploads a weird PDF" gap without burdening Docling

**Stack integration points:**

Depends on (services Stirling-PDF would consume):
- None at runtime

Consumed by (services that would call Stirling-PDF):
- **Backend (FastAPI)** — preprocessing route called before handing off to Docling / GROBID
- **n8n** — preprocessing step in document-ingest workflows
- **Open WebUI** — user-uploaded-PDF preprocessing before chat-with-PDF flows

**Mem0 — external agent memory layer**
- Open-source agent-memory framework (Apache-2.0) providing per-user, per-agent persistent memory with automatic extraction, deduplication, and recall
- Different scope from the existing **LangMem** (which is embedded in the backend service and Open WebUI-centric): Mem0 is an external service usable across Hermes, OpenClaw, and other agent runtimes uniformly
- Reuses the existing Weaviate (vector) and Supabase Postgres — no new datastore

**Stack integration points:**

Depends on (services Mem0 would consume):
- **Weaviate** — vector backend for memory recall
- **Supabase (PostgreSQL)** — structured memory metadata
- **LiteLLM gateway** — extraction-LLM calls during memory writes
- **TEI** — preferred embedding backend (or LiteLLM-routed embeddings as fallback)

Consumed by (services that would call Mem0):
- **Hermes** — primary consumer; replaces the file-based `/opt/data` memory with structured recall
- **OpenClaw** — per-channel-user memory across messaging platforms
- **Backend (FastAPI)** — multi-tenant memory for external API clients
- **Open WebUI** — alternative memory backend to LangMem

**whisperX / faster-whisper-server — diarization and word-level timestamps**
- Adds speaker diarization (pyannote.audio) and forced-alignment word-level timestamps on top of Whisper-style STT
- Parakeet and Speaches handle transcription; neither does diarization — adding whisperX unlocks meeting / podcast / earnings-call RAG with per-speaker turn attribution
- Optional companion or replacement depending on `STT_PROVIDER_SOURCE` choice

**Stack integration points:**

Depends on (services whisperX would consume):
- Bundled Faster-Whisper plus pyannote models; no runtime service dependencies

Consumed by (services that would call whisperX):
- **Backend (FastAPI)** — long-form audio transcription endpoint with speaker turns
- **n8n** — audio-ingest workflows (recordings → diarized transcripts → RAG)
- **LightRAG** — transcripts feed the entity graph with speaker-as-entity edges
- **Local Deep Researcher** — research from podcast / earnings-call audio sources

**Browserless v2 — persistent headless-Chrome sessions**
- Headless Chrome managed via Playwright / Puppeteer WebSocket endpoints; sits one layer below Crawl4AI
- **Different role from Crawl4AI**: Browserless gives long-lived authenticated browser sessions for *agentic* browsing (form fill, multi-step login flows, broker dashboards, paywalled research portals); Crawl4AI does one-shot scraping
- **Licensing flag:** Browserless v2 is SSPL-1.0 — review against deployment posture before adoption; consider Playwright-server (Apache-2.0) as an alternative

**Stack integration points:**

Depends on (services Browserless would consume):
- None at runtime (bundled Chromium)

Consumed by (services that would call Browserless):
- **Hermes** — agentic browsing tool (e.g. `browser-use` SDK as a Hermes skill)
- **Backend (FastAPI)** — automated portal-scrape and form-submit routes
- **n8n** — multi-step browser-automation workflow nodes
- **Crawl4AI** — falls back to Browserless when a target needs persistent session handling

**Three.js + react-three-fiber + glTF-Transform — browser 3D pipeline foundation**
- The browser-first half of the 3D / game-generation track. **Three.js** (MIT) is the dominant self-hostable WebGL framework in 2026; **react-three-fiber** (MIT) is the React renderer for it; **glTF-Transform** (MIT) is the CLI / library that wraps KTX2 + Draco + meshopt asset compression in a single pass.
- Three.js + r3f are frontend dependencies (no new container); glTF-Transform runs as a small FastAPI worker that ingests raw meshes from Hunyuan3D-2 / TRELLIS / InstantMesh / Blender exports and emits optimized glTF for browser delivery.
- **Babylon.js** (Apache-2.0) is an acceptable secondary if physics + native WebXR become priorities; same integration shape.
- Initial scope for Dreamscapes-like applications; native-desktop (Godot / Unreal export) and cloud-rendered (LiveKit-streamed) paths are forward-looking.

**Stack integration points:**

Depends on (services the 3D pipeline foundation would consume):
- **Hunyuan3D-2 / TRELLIS / InstantMesh** — raw mesh outputs
- **Blender** — assembled scene exports
- **MinIO** — optimized-asset bucket (signed URLs via Backend)
- **Kong** — `viewer.<host>` route for the WebGL app static delivery

Consumed by (services that would call the 3D pipeline foundation):
- **Backend (FastAPI)** — orchestrates the optimize → upload → index pipeline
- **n8n** — asset-pipeline workflows
- **Open WebUI** — embedded 3D-preview extensions
- **Weaviate / LightRAG** — index the optimized assets for retrieval

**Hummingbot API + Hummingbot Dashboard — trading-bot fleet manager**
- Apache-2.0 ecosystem (Hummingbot Foundation): **`hummingbot-api`** is a multi-instance fleet manager (April 2026 update) that creates / backtests / deploys / monitors N bots with a one-click paper-or-live toggle; **`hummingbot-dashboard`** is the Streamlit / React UI on top.
- The only self-hostable *fleet plane* in 2026; sits **above** the strategy engine. Use **NautilusTrader** (Tier 3 financial track) as a worker engine underneath when its Rust event loop is preferred; Hummingbot's own strategy runners cover the rest.
- Phase 1 of the financial / trading-AI track — the paper-trading fleet ships against this orchestrator first.

**Stack integration points:**

Depends on (services Hummingbot would consume):
- **Supabase (PostgreSQL)** — bot configuration and run history
- **CCXT** (Tier 2) — exchange adapter library inside each bot worker
- **OpenBB Platform** (Tier 3) — supplementary market-data feeds
- **Redpanda** (shipped) — order-event fan-out to the risk-control service
- **OpenBao** (Tier 1) — live-mode signing keys (Phase 2)

Consumed by (services that would call Hummingbot):
- **Backend (FastAPI)** — bot lifecycle management API surface
- **MCP gateway** — exposes bot-fleet operations as MCP tools for LLM consumers
- **Hermes** — agent-driven bot creation and tuning
- **Windmill** — promotion-gate flows that flip bots from paper to live
- **Grafana** — additional time-series dashboards alongside Hummingbot Dashboard

**CCXT — unified crypto exchange adapter**
- MIT-licensed library (not a service) supporting 100+ crypto exchanges with a single Python / JavaScript API. Certified-tier coverage on Binance / Coinbase / Kraken; the de-facto standard in 2026 for crypto trading bots.
- Library inside the **Backend (FastAPI)** and **Hummingbot** workers — not a new container. Wrapped behind the in-house wallet / ledger service so paper-mode and live-mode share a single internal API.
- No equivalent exists for traditional brokerages — equities use `alpaca-py`, `ib_insync`, etc., similarly wrapped behind the in-house abstraction. Documented for visibility.

**Stack integration points:**

Depends on (services CCXT would consume):
- **OpenBao** — live-mode API credentials (read via Transit, never exfiltrated)
- **Infisical** — paper-mode / read-only API credentials

Consumed by (services that would use CCXT):
- **Backend (FastAPI)** — internal wallet / ledger service
- **Hummingbot** workers — direct exchange access
- **NautilusTrader** workers — same
- **JupyterHub** — research notebooks
- **n8n / Windmill** — workflow steps that need exchange-data queries

**Alternative vector database (Qdrant)**
- Qdrant as alternative to Weaviate
- Comparative performance analysis
- Migration tools between vector databases
- User choice in vector database backend

**Enhanced n8n integration**
- Pre-built AI workflow templates
- Better integration with vector databases
- Advanced data processing workflows
- Custom node development

**Hermes Agent (programmable AI agent for chat & messaging)** ✓ **shipped**

Lives in [services/hermes/README.md](https://github.com/thekaveh/atlas/blob/main/services/hermes/README.md). Shipped as the
`hermes` service (`nousresearch/hermes-agent:latest` — upstream publishes
only `latest` + per-commit `sha-<digest>` tags, no semver; production
pins via `HERMES_IMAGE=nousresearch/hermes-agent:sha-...`) plus
`hermes-init` companion. Registered in the LiteLLM model catalog as
`hermes-agent`, so every consumer (Open WebUI, n8n, backend, jupyterhub,
openclaw) sees it in the model dropdown automatically. Dashboard exposed
at `http://hermes.localhost:63000`.

Consumes (when enabled, via the LiteLLM gateway and `hermes-init`'s
config.yaml rendering):
- **LiteLLM gateway** — required; Hermes reasons over `http://litellm:4000/v1`
- **ComfyUI** — image generation invoked as a tool from agent personas (auto-wired via skill-override file)
- **TTS provider (Speaches / Chatterbox)** — speech output for voice-enabled responses (`TTS_ENDPOINT`)
- **STT provider (Speaches / Parakeet / whisper.cpp)** — speech input for voice-driven conversations (`STT_ENDPOINT`)
- **SearXNG** — web search tool (no API key required)

Consumed by:
- **Open WebUI** — primary chat surface; `hermes-agent` appears in the model dropdown via LiteLLM
- **OpenClaw** — bridges Hermes agents to WhatsApp / Telegram / Discord channels
- **Backend (FastAPI)** — programmatic agent invocation from application code
- **n8n** — agent-driven automation workflows

Correction to the prior Tier-2 sketch: Hermes is **file-based**, not
Postgres-backed. The earlier line claiming Supabase as a Hermes dependency
was wrong — Hermes persists everything under `/opt/data` (the `hermes-data`
named volume). Supabase is not in Hermes's dependency set.

**Alternative TTS/STT engines (already explored)**
- Piper — shipped via Speaches's bundled CPU-friendly path
- Voice cloning — shipped via Chatterbox (`chatterbox-container-gpu` /
  `chatterbox-localhost`)
- Streaming audio — Speaches and Chatterbox both expose chunked output
- Future candidates: Orpheus-TTS (streaming, GPU-only), SenseVoice (50+
  langs with emotion labels), Higgs Audio v2

---

### 2.5 Tier 3: future candidates

> Entries marked ✅ shipped (e.g. Airflow, Spark) have landed in the stack; their write-ups stay here for the original evaluation context.

Tier 3 is organized into four named sub-sections so the use-case tracks are scannable at a glance: **General-purpose** (horizontal candidates), **3D / game-generation track**, **Financial / trading-AI track**, and **Real-time / collaboration**.

#### General-purpose

**Apache Airflow integration** — ✅ **Shipped 2026-06-04** (PR #35; Apache Airflow 3.2.2, LocalExecutor)
- Workflow orchestration
- Data pipeline management
- Scheduled AI processing jobs
- Complex workflow dependencies
- Cross-track role: alternative endpoint of the **`ORCHESTRATOR_SOURCE`** source-variant pattern proposed under the Data engineering track (Tier 3). **Dagster** is the recommended primary orchestrator for lakehouse / asset-centric work; Airflow is the alternative for task-centric workloads and for teams with existing Airflow muscle memory.
- Shipped with: 9-provider bundle (apache-spark, amazon, postgres, redis, common-sql, weaviate, neo4j, openai, fab) + `pyspark[connect]==4.1.2` + 7 seeded Connections (3 unconditional: `postgres_supabase` / `litellm_default` / `redis_default`; 4 conditional on sibling source: `spark_default` / `minio_default` / `weaviate_default` / `neo4j_default`) + Spark/MinIO/LiteLLM-wired sample DAG + Hermes → Airflow REST trigger documentation. The sample DAG uses `OpenAIHook` (the openai provider has no `OpenAIOperator`); a commented LangChain block shows the recommended chain pattern via `PythonOperator` + `langchain-openai` (there is no published `apache-airflow-providers-langchain`). See `services/airflow/README.md` and the PR #35 design at `docs/superpowers/specs/2026-06-04-airflow-spark-zeppelin-design.md`.

**MeiliSearch (lightweight full-text search)**
- Fast full-text search capabilities
- Rust-based, lightweight single binary (<1 GB RAM)
- Typo-tolerant, sub-50ms search responses out of the box
- Hybrid search (keyword + semantic) with tunable semantic ratio
- MIT licensed, 57k+ GitHub stars
- Best suited for application-level search on small-to-medium datasets

**OpenSearch (search, analytics & RAG platform)**
- Distributed search and analytics suite (Apache 2.0 licensed Elasticsearch fork)
- Full-text search, observability, security analytics (SIEM), and OpenSearch Dashboards (Kibana equivalent)
- RAG capabilities: native text chunking with configurable overlap, ingest pipelines (chunk -> embed -> index), Reciprocal Rank Fusion for hybrid search, ML Commons for internal model hosting
- Vector search via k-NN plugin (HNSW, IVF, Lucene) supporting up to 16,000 dimensions and billions of documents
- Multiple quantization options (binary 32x, scalar 50-85%, product up to 90% compression)
- Backed by AWS / Linux Foundation, 12.7k+ GitHub stars
- Heavier footprint than MeiliSearch (8-16+ GB RAM, JVM tuning required) in exchange for deeper RAG and analytics capabilities

**Stack integration points:**

Depends on (services OpenSearch would consume):
- **Supabase (PostgreSQL)** — content sync for indexing via CDC or batch jobs
- **Redis** — query result caching
- **Ollama** — embedding generation for vector indexing

Consumed by (services that would call OpenSearch):
- **Backend (FastAPI)** — full-text and hybrid search API for research results and documents
- **n8n** — search workflows, log analysis pipelines, alerting
- **JupyterHub** — data exploration and analytics notebooks
- **Open WebUI** — search across conversation history and knowledge bases
- **Local Deep Researcher** — search internal knowledge alongside web results
- **LangFlow** — retrieval component in RAG pipelines
- **OpenSearch Dashboards** — standalone UI for search analytics and visualization

**InvokeAI — Stable Diffusion canvas / inpainting workflow**
- Image-generation UI (Apache-2.0) focused on canvas, inpainting, and creative editing — genuinely complementary to ComfyUI's graph / batch model rather than a duplicate
- ComfyUI remains the existing default for parametric / batch generation; InvokeAI fills the canvas-editor gap for artist-driven workflows
- Don't adopt A1111 (Automatic1111): the project has stagnated relative to ComfyUI, Forge, and InvokeAI as of 2026

**Stack integration points:**

Depends on (services InvokeAI would consume):
- Optional **MinIO** — model and output bucket

Consumed by (services that would call InvokeAI):
- **Backend (FastAPI)** — alternative image-gen route alongside ComfyUI
- **Open WebUI** — second image-generation provider in the model picker
- **n8n** — canvas / inpaint workflow nodes
- **Hermes** — image-editing tool for agent personas

**vLLM — high-throughput GPU inference**
- High-performance LLM inference engine (Apache-2.0); PagedAttention scheduler, OpenAI-compatible REST surface, multi-tenant batching
- Parallel option to Ollama behind LiteLLM: Ollama for low-friction local serving, vLLM for production-grade GPU throughput
- Registered as another LiteLLM upstream — no consumer-side changes; consumers continue to call LiteLLM as usual

**Stack integration points:**

Depends on (services vLLM would consume):
- None at runtime (model weights cached in a named volume)

Consumed by (services that would call vLLM):
- **LiteLLM gateway** — vLLM appears as another routed-model upstream
- All existing LiteLLM consumers indirectly (Backend, Open WebUI, Hermes, n8n, JupyterHub, Local Deep Researcher, OpenClaw)

**Letta (MemGPT) — heavyweight stateful agent memory**
- Open-source MemGPT framework (Apache-2.0); agents reason over their own memory hierarchy (core, archival, recall) rather than receiving a passive recall snippet
- Heavier alternative to Mem0 for agents that need to manage their own memory state across long-running interactions
- Pair-or-pick decision rather than additive: if Mem0 (Tier 2) lands, treat Letta as the upgrade path for the subset of agents that need stateful memory management

**Stack integration points:**

Depends on (services Letta would consume):
- **Supabase (PostgreSQL)** — memory persistence
- **Weaviate** — vector recall backend
- **LiteLLM gateway** — model calls for memory operations
- **TEI** — preferred embedding backend

Consumed by (services that would call Letta):
- **Hermes** — stateful-agent variant for long-running personas
- **Backend (FastAPI)** — agents that need to plan over their memory

**Windmill — code-first workflow engine**
- Open-source workflow engine (AGPL-3.0) executing TypeScript / Python / Bash / Go / SQL scripts as composable jobs with a visual flow editor on top
- Complements n8n rather than replacing it: n8n excels at low-code system orchestration; Windmill excels at code-native ML / data pipelines (training jobs, evals, periodic backtests, scheduled ingestion)
- Self-host trade-off: AGPL license requires attention if the stack is redistributed; consider Activepieces (MIT) or Trigger.dev as alternatives

**Stack integration points:**

Depends on (services Windmill would consume):
- **Supabase (PostgreSQL)** — job metadata and run history
- **MinIO** — large input / output artifacts

Consumed by (services that would call Windmill):
- **Backend (FastAPI)** — schedules data-pipeline and eval jobs
- **n8n** — invokes Windmill code-heavy steps from low-code flows
- **JupyterHub** — exports notebooks as Windmill jobs
- **Hermes** — agents trigger backtests / evals as Windmill runs (financial track)

**AudioCraft / MusicGen — audio and music generation**
- Meta's open-source audio generation suite (MIT for code; model licenses vary — MusicGen weights are CC-BY-NC for non-commercial use; AudioGen and EnCodec have more permissive options) — text-to-music, text-to-sound-effect, conditional generation
- Closes the audio-generation gap in the media category (the stack has speech and image generation but no music / SFX synthesis)
- Useful adjacent to ComfyUI / Hermes for content-creator workflows; also pairs with the 3D track (procedural game audio)

**Stack integration points:**

Depends on (services AudioCraft would consume):
- Optional **MinIO** — generated-audio bucket

Consumed by (services that would call AudioCraft):
- **Backend (FastAPI)** — audio-gen endpoint
- **Open WebUI** — extension for inline music / SFX generation
- **n8n** — content-pipeline workflows
- **Hermes** — content-creator agent personas
- **Godot / Blender** (3D track) — procedural background music and sound effects

**Paperless-ngx — OCR'd document archive**
- Self-hosted document management (GPL-3.0): receives scanned / imported PDFs, runs OCR, auto-classifies into tagged correspondents / types, and exposes a searchable full-text index
- Long-term archival layer distinct from Docling (which parses on demand) and Stirling-PDF (which manipulates): Paperless is the durable retained-document store with metadata
- Ingestion source for personal / business RAG; useful adjacent to Karakeep (web captures) for a complete personal-corpus index

**Stack integration points:**

Depends on (services Paperless-ngx would consume):
- **Supabase (PostgreSQL)** — document metadata
- **Redis** — task queue
- Bundled Tesseract OCR; can be replaced or augmented by Docling + Stirling-PDF

Consumed by (services that would call Paperless-ngx):
- **Backend (FastAPI)** — search and retrieve archived documents as a RAG retrieval source
- **LightRAG / Weaviate** — ingestion of the archive into entity graph / vector index
- **n8n** — document-routing workflows (e.g. inbox → Paperless → tag → notify)

**Enterprise identity management (Keycloak or Authentik)**
- Advanced user management
- Single sign-on (SSO) capabilities across stack UIs (Open WebUI, Supabase Studio, Grafana, LangFlow, n8n, JupyterHub)
- Role-based access control
- Integration with external identity providers (OIDC, SAML, LDAP)
- Trade-off: **Keycloak** is enterprise-feature-complete (legacy SAML flows, fine-grained RBAC); **Authentik** is the lighter OSS-first alternative (modern UI, Python-extensible, simpler operations). Pick Authentik for greenfield deployments and Keycloak when integrating with enterprise IdPs that demand legacy protocols.

**Cloud deployment enhancements**
- AWS ECS Fargate optimization
- Kubernetes deployment manifests
- Auto-scaling capabilities
- Multi-region deployment support

**DevOps enhancements**
- CI/CD pipeline templates
- Automated testing frameworks
- Deployment automation
- Infrastructure as Code (IaC) templates

**Additional model integrations**
- Support for larger language models
- Multi-modal model support
- Specialized model integrations
- Custom model deployment pipelines

**Experimental features**
- Newer model integrations as they stabilize
- Experimental workflow patterns
- Advanced RAG techniques
- Novel application patterns

**Federation & interoperability**
- Multi-stack federation
- Standardized APIs
- Cross-platform compatibility
- Open source ecosystem integration

#### 3D / game-generation track

This track composes a full pipeline: **ComfyUI** (concept image, exists) → **Hunyuan3D-2 / TRELLIS** (image-to-3D mesh) → **Blender headless** (scene assembly and render) → **Godot headless** (game runtime). **NerfStudio** runs in parallel for real-scene capture. **LightRAG** indexes the asset graph; **AudioCraft / MusicGen** provides procedural audio.

**Blender (headless + Python API)**
- Long-established open-source 3D suite (GPL-2.0+); headless mode plus the `bpy` Python API make Blender scriptable as a backend service for rendering, mesh manipulation, scene assembly, and asset format conversion (FBX, GLB, USD, OBJ)
- The mature foundation for the 3D / game-generation track: ComfyUI generates concept images or textures, image-to-3D models (Hunyuan3D-2 / TRELLIS) produce meshes, Blender assembles them into scenes and exports to game-engine-friendly formats
- Single GPU container handles both production rendering and AI-assisted asset cleanup

**Stack integration points:**

Depends on (services Blender would consume):
- **MinIO** — texture and exported-asset bucket
- **ComfyUI** — upstream texture / concept-image generation

Consumed by (services that would call Blender):
- **Backend (FastAPI)** — scene-assembly and render-job routes
- **n8n** — asset-pipeline workflows
- **Hermes** — 3D-asset agent personas
- **Godot** (and external game engines like Unreal) — downstream asset consumer

**Hunyuan3D-2 (Tencent) or TRELLIS (Microsoft) — image-to-3D generation**
- State-of-the-art image-to-3D mesh generation (2025 / 2026). Hunyuan3D-2 is more textured-mesh focused; TRELLIS is broader (mesh, Gaussian splatting, radiance field outputs from a single image)
- Slots between ComfyUI and Blender in the 3D pipeline: image → mesh → scene assembly
- GPU-only; expect 12–24 GB VRAM for production-quality outputs

**Stack integration points:**

Depends on (services Hunyuan3D-2 / TRELLIS would consume):
- **ComfyUI** — upstream concept-image generation
- **MinIO** — output mesh bucket

Consumed by (services that would call the image-to-3D service):
- **Backend (FastAPI)** — text / image → 3D-asset API
- **Blender** — refines and re-meshes the generated outputs
- **Hermes** — 3D-generation tool for agents
- **n8n** — asset-pipeline workflows

**NerfStudio — neural-radiance-field and Gaussian-splatting reconstruction**
- Open-source 3D reconstruction toolkit (Apache-2.0): video or photo set → NeRF or 3D Gaussian Splatting scene
- Different shape from Hunyuan3D-2 / TRELLIS: NerfStudio reconstructs *existing* real-world scenes from images, where image-to-3D models *generate* new assets from concepts
- Combined value: ComfyUI generates concept → Hunyuan3D synthesizes new asset; NerfStudio captures real environments. Both feed Blender / Godot.

**Stack integration points:**

Depends on (services NerfStudio would consume):
- **MinIO** — capture-input and reconstructed-scene bucket

Consumed by (services that would call NerfStudio):
- **Backend (FastAPI)** — capture-ingest and reconstruction-status routes
- **Blender** — imports reconstructed scenes for refinement
- **Godot** — consumes reconstructed environments as game backdrops
- **n8n** — reconstruction-pipeline workflows

**Godot (headless server) — open-source game engine runtime**
- Open-source game engine (MIT) with a headless export mode usable as a server for running and testing AI-generated game scenes
- Provides the runtime half of the 3D / game-generation pipeline: Blender produces assets, Hunyuan3D-2 / NerfStudio contribute generated and reconstructed content, Godot runs the resulting game scene for automated testing or agent-driven play
- Lightweight by game-engine standards (single binary, small Docker image)

**Stack integration points:**

Depends on (services Godot would consume):
- **MinIO** — game-asset bucket
- **Blender** — primary asset upstream
- **Hunyuan3D-2 / NerfStudio** — additional asset upstreams

Consumed by (services that would call Godot):
- **Backend (FastAPI)** — scene-runtime API (load scene, capture screenshot, simulate input)
- **Hermes** — agents that play / test generated games as a tool
- **n8n** — automated game-test workflows

**InstantMesh — fast-path image-to-3D**
- TencentARC (Apache-2.0). Sub-15-second image-to-mesh, well below Hunyuan3D-2 / TRELLIS fidelity but ideal for drafts, previews, and A/B candidate generation. Pair with Hunyuan3D-2 for finals.
- Phase 1 of Dreamscapes-class pipelines — lets the LLM-driven UI offer near-real-time iteration on the user's prompt before committing GPU time to a final mesh.

**Stack integration points:**

Depends on: **ComfyUI** (concept image), **MinIO** (mesh bucket).

Consumed by: **Backend (FastAPI)** (draft-mesh API), **Hunyuan3D-2 / TRELLIS** (downstream refinement), **Blender** (cleanup and re-mesh), **Hermes** (agent draft-iteration tool).

**DreamGaussian — Gaussian-splat output for snowdome-style scenes**
- MIT. Produces both Gaussian Splats *and* meshes from a single image. The splat output skips the meshing step entirely — ideal when a snowdome-style interior doesn't need polygonal geometry.
- Complements Hunyuan3D-2 / TRELLIS / InstantMesh (all mesh-output). Choose at generation time based on whether the user wants a "snow-globe" (splat) or a "mini-RTS-tile-map" (mesh).

**Stack integration points:**

Depends on: **ComfyUI**, **MinIO**.

Consumed by: **Backend (FastAPI)**, **SuperSplat worker** (downstream optimization), **Three.js + r3f viewer** (3DGS loader in browser).

**SuperSplat — Gaussian-Splat asset conversion worker**
- MIT (PlayCanvas org). Headless `.ply → .splat / .ksplat` conversion plus in-browser editor; the canonical 2026 piece for the 3DGS branch of the pipeline.

**Stack integration points:**

Depends on: **DreamGaussian** (raw splat output), **MinIO**.

Consumed by: **Three.js + r3f viewer**, **Backend (FastAPI)**, **n8n** (batch-splat workflows).

**Vengi voxconvert — voxel format converter**
- MIT, regularly released; the only currently-maintained permissive `.vox` ↔ `.obj / .ply / .gltf` converter in 2026 (npm `vox-to-gltf` is abandoned, MagicaVoxel CLI is closed-source freeware, Goxel is GPL-3).
- Wraps the converter CLI as a small worker; emits glTF for the Three.js + r3f viewer.
- Phase 2 of Dreamscapes-class pipelines — when the world representation graduates from free-form snowdomes to voxel grids.

**Stack integration points:**

Depends on: **MinIO** (voxel input + glTF output buckets).

Consumed by: **Backend (FastAPI)**, **glTF-Transform worker** (downstream optimization), **n8n** (voxel-ingest workflows), **Three.js + r3f viewer** (final delivery).

**PostGIS + Tegola + MapLibre GL JS — vector-tile stack**
- The RTS-tile / GIS-style-map evolution of the 3D track. **PostGIS** (PostgreSQL License) is an extension on the existing Supabase Postgres (zero new container). **Tegola** (MIT, Go) is a single-binary vector-tile server that reads PostGIS and emits MVT. **MapLibre GL JS** (BSD-3) is the browser-side tile renderer.
- Pairs naturally with the **WaveFunctionCollapse worker** (next entry): WFC generates tile constraints, PostGIS stores them, Tegola serves them as MVT, MapLibre renders them at scale.
- Phase 2 of Dreamscapes-class pipelines.

**Stack integration points:**

Depends on (services the vector-tile stack would consume):
- **Supabase (PostgreSQL)** — host for the PostGIS extension; zero new container
- **MinIO** — large-asset storage for non-vector layers

Consumed by (services that would call the vector-tile stack):
- **Three.js + r3f viewer** — combined glTF (3D) + MapLibre (tile) renderer
- **Backend (FastAPI)** — tile-pipeline orchestration
- **n8n / Windmill** — batched tile-generation workflows
- **WaveFunctionCollapse worker** — write-side producer of generated tiles

**WaveFunctionCollapse worker — procedural tile generation**
- mxgmn/WaveFunctionCollapse (MIT) — the reference open-source PCG algorithm in 2026. Wrap a Python / Rust port as a small worker; generates constraint-consistent tile-worlds from a small text seed.
- Phase 2 of Dreamscapes-class pipelines; pairs with the vector-tile stack above for RTS-style worlds.

**Stack integration points:**

Depends on: **PostGIS + Tegola** (tile write-side persistence), **Ollama / LiteLLM** (LLM-derived constraint generation).

Consumed by: **Backend (FastAPI)**, **n8n / Windmill** (tile-pipeline workflows).

**Colyseus — authoritative multiplayer server**
- MIT (Node.js); room-based authoritative state sync with schema diffing. Built for tick-rate game-style updates, distinct from Yjs / Hocuspocus (CRDT, document-shaped).
- Phase 3 of Dreamscapes-class pipelines — enables shared "fly inside" sessions and multi-user world editing.

**Stack integration points:**

Depends on: **Supabase (Postgres + Auth)** — user identity and room persistence; **Redis** — pub/sub for room state.

Consumed by: **Three.js + r3f viewer** (multiplayer client), **Backend (FastAPI)** (room provisioning), **Hermes** (agent NPC participants).

**Excalidraw + excalidraw-room — collaborative sketch input surface**
- Excalidraw (MIT) is a static frontend; `excalidraw-room` (MIT) is a tiny WebSocket collaboration server. End-to-end encrypted by default — the LLM consumes the user's exported PNG, not the room state, preserving privacy.
- Phase 1 of Dreamscapes — the surface where the user draws (or co-draws) the sketch that ComfyUI conditions on.

**Stack integration points:**

Depends on: none at runtime.

Consumed by: **ComfyUI** (sketch-conditioned image generation via ControlNet), **Backend (FastAPI)** (sketch export → image-gen pipeline).

#### Financial / trading-AI track

This track composes a full pipeline: **OpenBB Platform** provides multi-provider financial data; **TimescaleDB** (Postgres extension on the existing Supabase Postgres) stores tick / OHLC history; **Redpanda** carries real-time exchange feeds; **NautilusTrader** runs backtests, paper-trade, and live execution; **Hermes** and **Backend** generate or tune strategies via **LiteLLM**; **Langfuse** observes LLM-driven decisions; **Infisical** holds exchange credentials.

**NautilusTrader (or Freqtrade) — algorithmic trading engine**
- High-performance Python / Rust open-source algorithmic-trading platform (LGPL-3.0); supports backtesting, paper trading, and live execution across crypto, FX, equities, and futures venues
- Alternative for crypto-only deployments: **Freqtrade** (GPL-3.0) — large community, simpler scope, Python-only
- LLM angle: backend / Hermes generate or tune strategies; Langfuse traces strategy-LLM interactions; Infisical-managed exchange API keys

**Stack integration points:**

Depends on (services NautilusTrader would consume):
- **TimescaleDB extension on Supabase Postgres** — backtest and live market-data store
- **Redpanda** — real-time market-data ingest
- **Infisical** — exchange API credentials
- **LiteLLM gateway** — strategy-tuning and signal-explanation calls

Consumed by (services that would call NautilusTrader):
- **Backend (FastAPI)** — strategy management and order-routing API
- **Hermes** — agent-driven strategy invocation
- **n8n / Windmill** — scheduled backtest and rebalance workflows
- **JupyterHub** — research notebooks
- **Langfuse** — strategy-LLM observability

**OpenBB Platform — open financial-data aggregator**
- Open-source financial-data layer (AGPL-3.0; commercial license available): unified Python and REST interface to equities, crypto, macro, news, fundamentals across many providers (yfinance, Alpha Vantage, Polygon, FRED, SEC filings, …)
- LLM-friendly schema; tool-perfect surface for trading agents — exactly the kind of capability the MCP gateway (Tier 1) is designed to expose to LiteLLM consumers

**Stack integration points:**

Depends on (services OpenBB would consume):
- **Infisical** — third-party-data-provider API keys
- **Redis** — response caching

Consumed by (services that would call OpenBB):
- **MCP gateway** — exposes OpenBB endpoints as MCP tools for all LLM consumers
- **Backend (FastAPI)** — direct integration for financial features
- **Hermes** — financial-data tool for trading-research agents
- **n8n** — data-ingest workflows
- **NautilusTrader** — fundamentals enrichment alongside live market feeds
- **JupyterHub** — research notebooks

**TimescaleDB extension — time-series on existing Postgres**
- Postgres extension (Apache-2.0 + Timescale License) adding hypertables, continuous aggregates, retention policies, and time-bucketed query operators
- Zero new container — enabled on the **existing Supabase Postgres** instance via `CREATE EXTENSION timescaledb`
- Provides the storage layer for tick / OHLC market data underpinning the trading-AI track; also useful for any time-series workload (Langfuse trace aggregates, IoT, observability rollups)

**Stack integration points:**

Depends on (services TimescaleDB would consume):
- **Supabase (PostgreSQL)** — same Postgres instance, extension-loaded

Consumed by (services that would call TimescaleDB):
- **NautilusTrader** — primary consumer; backtest and live market-data store
- **OpenBB Platform** — historical-data cache target
- **Backend (FastAPI)** — time-series query routes
- **JupyterHub** — research notebooks
- **Langfuse** — optional storage backend for trace event aggregates

**Redpanda — Kafka-compatible streaming broker** — ✅ Shipped (services/redpanda/)
- Single-binary Kafka-compatible streaming platform (BSL-1.1 / RCL); no ZooKeeper / KRaft hassle, dramatically lighter ops footprint than Apache Kafka
- Carries real-time market-data fan-out for the trading-AI track (one exchange WebSocket → multiple strategy consumers without re-subscribing)
- Also a general-purpose event-streaming layer for n8n event-driven workflows, observability event pipelines, and inter-service eventing as the stack scales

**Stack integration points:**

Depends on (services Redpanda would consume):
- None at runtime

Consumed by (services that would call Redpanda):
- **NautilusTrader** — live market-data subscriber
- **Backend (FastAPI)** — event-emitter for cross-service signals
- **n8n** — event-trigger nodes
- **Windmill** — event-driven job triggers
- **OpenBB Platform** — streaming-quote relay

**Note: cross-cutting Phase-3 dependencies (Ray and E2B)** — the financial track's parallel-backtest substrate (**Ray**) shipped 2026-05-24 and is documented in the **Completed** section above. The Firecracker sandbox for LLM-generated strategy code (**E2B self-hosted**) remains in **Tier 2 under "Cross-cutting infrastructure"**, because it serves the 3D / Dreamscapes, RAG, and forthcoming data-engineering tracks equally. See the Tier 2 entry for full details.

**FinRL / FinGPT image flavors — AI-assisted strategy authoring**
- AI4Finance Foundation (MIT). FinRL is the reinforcement-learning library for financial markets; FinGPT is the open-weights financial-LLM family. **Both are libraries, not services** — they ride inside JupyterHub kernel images and E2B sandbox templates.
- Phase 3 of the financial / trading-AI track — packaged as image flavors that any environment can opt into; no new compose service required.

**Stack integration points:**

Depends on (services FinRL / FinGPT would consume):
- **LiteLLM gateway** — model routing for FinGPT and any prompted-strategy generation
- **OpenBB Platform** — features and market data
- **TimescaleDB** — training-set storage

Consumed by (services that would use FinRL / FinGPT):
- **JupyterHub** — researcher notebooks
- **E2B sandboxes** — agent-driven strategy generation
- **Hermes** — finance-specialized agent personas

**In-house components (documented for scope visibility, not third-party)**
- **Wallet / ledger service** (Phase 1) — thin FastAPI service exposing a unified API to bots regardless of mode. Paper-mode is a synthetic ledger on Supabase Postgres tables; live-mode delegates to CCXT (crypto) and `alpaca-py` / `ib_insync` (equities) behind the same interface.
- **Risk-control service** (Phase 2) — small FastAPI worker that subscribes to Redpanda order-event topics, enforces per-strategy notional caps, per-account drawdown, and market-wide circuit breakers, and publishes a `halt` topic every bot subscribes to.
- **Audit-sealer worker** (Phase 2) — small Python worker that batches order events from Redpanda, computes a Merkle root, signs it via **OpenBao**, and writes a WORM-locked archive to MinIO Object Lock. The anchor record lives in Supabase Postgres for queryability.
- These pieces are intentionally in-house: the OSS landscape for these specific roles is thin in 2026, and small targeted FastAPI services are cheaper to maintain than evaluating unmaintained third-party alternatives.

#### Data engineering track

This track composes a lakehouse + ingestion + BI + (optional) MLOps platform alongside the AI services, with the JVM / Scala lane explicitly available but **opt-in** (the rest of the stack stays Python-native via Spark Connect). Three notable divergences from the obvious 2024 picks: **Apache Zeppelin** shipped 2026-06-04 (PR #35) as a Spark-first notebook UI alongside the **Almond Scala kernel** on JupyterHub — they coexist with different audiences (Zeppelin for Spark-first SQL/Scala notebook authoring; Almond/JupyterHub for general-purpose Scala kernels in the Python notebook environment); **Dagster** is the primary asset-centric orchestrator with **Apache Airflow** (also shipped 2026-06-04 in PR #35) as a permitted alternative (via `ORCHESTRATOR_SOURCE`); and **Spark Connect** makes Scala client-side optional. Unusually strong reuse: the lake is MinIO (existing), every catalog stores metadata in Postgres (existing), Feast's online store is Redis (existing), OpenMetadata's search is OpenSearch (Tier 3 roadmap), Debezium's sink is Redpanda (shipped), and parallel work is the now-shipped **Ray** cluster (see the Completed section above).

**Apache Spark (standalone + Spark Connect) — distributed compute** — ✅ **Shipped 2026-06-04** (PR #35; Spark 4.1.2)
- Apache-2.0; Spark 4.x. 5-container family on `apache/spark:4.1.2`: four long-running roles (master, worker, history, `spark-connect` gRPC sidecar) plus a one-shot minio/mc-based `spark-init` that creates the `spark-history` bucket. Spark Connect (GA since Spark 3.4, recommended in 4.x) is a gRPC server that exposes Spark to Python / Scala / Go / Rust clients transparently — the cluster runs JVM, clients do not.
- Phase 1 anchor of the data-engineering track. **The Spark Connect server is the architectural unlock**: it lets the FastAPI backend, JupyterHub Python kernels, Dagster / Airflow workers, and Hermes-orchestrated jobs use Spark without a JVM in their containers.
- **Follow-up — Spark × Prometheus + Grafana observability**: spec §5.1 marked this CRITICAL-opt-in (JMX exporter sidecar + scrape job + a starter `spark.json` Grafana dashboard) but the wiring did not ship in PR #35. cAdvisor's container-level metrics cover the gap in existing dashboards until the JMX integration lands.

**Stack integration points:**

Depends on (services Spark would consume):
- **MinIO** — `s3a://` access for lake reads/writes
- **Lakekeeper** — Iceberg REST catalog
- **Supabase (PostgreSQL)** — operational metadata when needed

Consumed by (services that would call Spark):
- **Dagster / Apache Airflow** — workflow steps invoking Spark jobs
- **Backend (FastAPI)** — programmatic Spark Connect calls
- **JupyterHub** — Python kernels via PySpark Connect, Scala kernels via Almond
- **Trino** — federated reads / writes against Iceberg tables Spark wrote
- **Hermes** — agent-driven analytical queries

**Lakekeeper — Iceberg REST catalog** — ✅ Shipped (services/iceberg-rest/)
- Apache-2.0; Rust single-binary REST catalog for Apache Iceberg. Postgres-backed metadata.
- Lighter alternative to **Apache Polaris** (Snowflake-donated, Apache governance, Spring Boot stack) for this stack's footprint. Polaris is a defensible upgrade if Apache governance is a hard requirement.

**Stack integration points:**

Depends on: **Supabase (PostgreSQL)** — catalog metadata storage.

Consumed by: **Apache Spark**, **Trino**, **DuckDB** (via Iceberg connector), **Dagster** (Iceberg-as-asset definitions), **Backend (FastAPI)** (catalog-aware data APIs).

**Trino — federated SQL engine** — ✅ Shipped (services/trino/)
- Apache-2.0; JVM. The canonical 2026 lakehouse-SQL engine; first-class connectors for Iceberg, Postgres, Mongo, Kafka, Redpanda, OpenSearch, and many more. Heavy (JVM tuning) but well-understood.
- **StarRocks** (Apache-2.0, C++) is a documented future accelerator if Trino dashboard latency becomes a real pain point — different shape (analytical DB with federation bolted on rather than pure federation engine), so not a like-for-like swap.

**Stack integration points:**

Depends on: **Lakekeeper** (Iceberg catalog), **Supabase (PostgreSQL)** (federation source + Trino's own metadata), **MinIO** (lake reads).

Consumed by: **Apache Superset** (BI queries), **Backend (FastAPI)** (application analytical queries), **JupyterHub** (research notebooks), **Hermes / MCP gateway** (text-to-SQL agent tool), **Feast** (offline feature reads).

**Apache Superset — BI dashboards**
- Apache-2.0; deepest OSS viz library, native LDAP / OAuth / OIDC / SAML / row-level security in the free tier; Trino + Iceberg + Postgres connectors first-class. Postgres-backed metadata, Redis-backed caching.
- **Metabase** (AGPL community + paid commercial features) is the open-core alternative; faster time-to-value for non-technical users but core RLS / audit / caching features are paywalled.

**Stack integration points:**

Depends on: **Supabase (PostgreSQL)** (metadata), **Redis** (caching), **Trino** + **Lakekeeper** + **MinIO** (data path).

Consumed by: end users (browser dashboards), **Kong** (`superset.localhost` route), **Backend (FastAPI)** (programmatic dashboard embedding).

**Dagster — primary asset-centric orchestrator**
- Apache-2.0; the 2026 consensus pick for lakehouse / dbt / Iceberg-centric workflows. Software-defined Assets model fits Iceberg-as-asset thinking exactly; first-class dbt / Spark / Iceberg / OpenMetadata integration; better UI lineage than Airflow.
- Roadmapped together with **Apache Airflow** (existing Tier 3 General-purpose entry) via the **`ORCHESTRATOR_SOURCE` source-variant pattern** (allowed values: `dagster-container`, `airflow-container`, `dagster-localhost`, `airflow-localhost`, `disabled`). Mirrors the existing `STT_PROVIDER_SOURCE` / `TTS_PROVIDER_SOURCE` patterns.

**Stack integration points:**

Depends on: **Supabase (PostgreSQL)** (run history + asset materialization records), **Redis** (sensor / queue state), **MinIO** (artifacts).

Consumed by: **Apache Spark** (jobs run as Dagster assets), **dbt-core** (transformations run as Dagster assets), **JupyterHub** (notebooks-as-assets), **n8n / Windmill** (cross-orchestrator workflows), **Hermes / MCP gateway** (agent-driven asset materialization), **OpenMetadata** (lineage events).

**dlt (data load tool) — Python-first ingestion**
- Apache-2.0 library, not a service. Lives inside Dagster / Airflow workers and the FastAPI backend. 2.8–6× faster than Airbyte / Sling on SQL replication; vast schema-inference and incremental-load library.
- **Skip Airbyte:** Docker Compose support deprecated in 2025; production now requires Kubernetes + Postgres + Redis + Temporal; ELv2 license is incompatible with the stack's permissive-boilerplate posture.
- **Meltano** (MIT) is documented as the fallback when Singer connectors are specifically needed.

**Soda Core + Elementary — data-quality observability**
- Both Apache-2.0 libraries, not services. **Soda Core** uses YAML-based SodaCL for low-friction continuous data-quality assertions. **Elementary** is a dbt-augmenting observability layer (anomaly detection on top of dbt tests). Combine with **dbt tests** themselves and **Great Expectations** as a heavier-weight alternative.

**Debezium Server — change-data-capture**
- Apache-2.0; Debezium 3.x. Standalone mode (no Kafka Connect required) writing directly to **Redpanda** (shipped), NATS, Pulsar, Kinesis, or HTTP sinks. Single-purpose, lightweight.

**Stack integration points:**

Depends on: **Supabase (PostgreSQL)** (WAL source), **Redpanda** (event sink).

Consumed by: **Dagster sensors** (asset-trigger from change events), **Backend (FastAPI)** (real-time application updates), **OpenMetadata** (lineage events), **n8n / Windmill** (event-driven flows).

**OpenMetadata — data catalog with column-level lineage**
- Apache-2.0; four-component architecture (Postgres / MySQL + Elasticsearch / OpenSearch + server + UI). **No Kafka, no separate graph DB** — much lighter than **DataHub** (LinkedIn, Apache-2.0 but GMS + MAE/MCE Kafka consumers + Elasticsearch + Neo4j footprint).
- Broader connector list than Atlas / Marquez; column-level lineage; dataset / glossary / governance features.

**Stack integration points:**

Depends on: **Supabase (PostgreSQL)** (catalog metadata), **OpenSearch** (Tier 3 roadmap) (search + lineage graph).

Consumed by: **Backend (FastAPI)** (catalog-aware data APIs), **Hermes / MCP gateway** (catalog-as-tool for agents), **Dagster** (asset materialization → lineage events), **Trino / Spark / dbt-core** (push lineage events on every run).

**MLflow — experiment + model tracking** — ✅ Shipped
- Apache-2.0; still dominant in 2026 for OSS experiment tracking. Postgres-backed registry + MinIO artifact store — both reuse existing services.
- **ClearML** (Apache-2.0) is documented as a defensible richer alternative when MLflow's tracking-only feature surface becomes insufficient (datasets + pipelines + agent).
- **W&B Local** requires a commercial license and is skipped on license grounds.

**Stack integration points:**

Depends on: **Supabase (PostgreSQL)** (registry), **MinIO** (artifacts).

Consumed by: **JupyterHub** (notebook training runs), **Dagster** (training assets), **Backend (FastAPI)** (model-version-aware inference routing).

**lakeFS — Git-like data versioning over MinIO (Phase 3)**
- Apache-2.0; branches and merges over object-storage data, scales to petabytes without copying. Sits on top of the existing MinIO buckets.
- **DVC** (Apache-2.0) is the Git-side companion library-in-image for Git-tracked ML experiments (small datasets, model snapshots).
- **Pachyderm** is **skipped**: HPE discontinued OSS releases in 2024.

**Stack integration points:**

Depends on: **MinIO** (object backend), **Supabase (PostgreSQL)** (lakeFS's own metadata).

Consumed by: **MLflow** (versioned dataset references), **Dagster** (asset materialization against branches), **JupyterHub** (branch-aware experiments), **Backend (FastAPI)** (versioned data exports).

**Feast — feature store (Phase 3)**
- Apache-2.0; the reference OSS feature-store impl in 2026. Online store on the existing **Redis**; offline store on **Apache Iceberg** read via **Trino**.
- **Featureform** is documented as a smaller-community alternative; defer.

**Stack integration points:**

Depends on: **Redis** (online store), **Trino** + **Lakekeeper** + **MinIO** (offline store), **Supabase (PostgreSQL)** (registry).

Consumed by: **JupyterHub** (feature-engineering notebooks), **Backend (FastAPI)** (online feature reads during inference), **Dagster** (feature-pipeline assets).

**Almond Scala kernel on existing JupyterHub — Scala lane image flavor**
- BSD-3 library, not a service. Adds a Scala / Spark / Iceberg notebook kernel to the already-shipped JupyterHub. The dominant 2026 Scala-notebook path. **Coexists with the (now-shipped) Apache Zeppelin** — Almond serves the Python-notebook audience that wants Scala kernels in the JupyterHub Lab UI; Zeppelin serves the Spark-first audience that wants paragraph-based interpreter cells (Spark + SQL + JDBC pre-configured). Polynote is abandoned (last release 2022) — Almond is the still-active Scala kernel.

**Library-in-image companions (Phase 1–3)**
- **dbt-core** (Apache-2.0) — SQL transformations; lives inside the Dagster / Airflow worker image.
- **SQLMesh** (Apache-2.0) — documented as a permitted dbt-core alternative (SQLGlot-based compile-time validation, virtual environments, native Python models).
- **DuckDB** (MIT) — in-process columnar SQL; embed in Backend and JupyterHub for ad-hoc Iceberg reads without spinning up the cluster.
- **dlt**, **Soda Core**, **Elementary**, **DVC** — see entries above; all libraries-in-image, not compose services.

**`ORCHESTRATOR_SOURCE` source-variant pattern**
- Mirrors the existing `STT_PROVIDER_SOURCE` and `TTS_PROVIDER_SOURCE` patterns. Allowed values: `dagster-container`, `airflow-container`, `dagster-localhost`, `airflow-localhost`, `disabled`. The bootstrapper enforces exactly one orchestrator is active when `ORCHESTRATOR_SOURCE != disabled`.
- The existing **Apache Airflow integration** entry (under `#### General-purpose`) is the alternative endpoint of this source variant.

#### Real-time / collaboration

**Real-time audio / video (LiveKit)**
- WebRTC server (Apache-2.0) bridging the existing STT and TTS layers into a real-time voice-agent pipeline rather than batch round-trips
- Video conferencing capabilities and multi-user AI interactions
- Audio / video streaming integration usable as transport for agent dashboards

**Stack integration points:**

Depends on (services LiveKit would consume):
- **STT provider (Parakeet / Speaches / whisper.cpp)** — speech input
- **TTS provider (Chatterbox / Speaches)** — speech output
- **LiteLLM gateway** — reasoning step between STT and TTS turns

Consumed by (services that would call LiveKit):
- **Hermes** — primary consumer; turns the agent into a voice-first surface
- **Backend (FastAPI)** — programmatic real-time session orchestration
- **Open WebUI** — potential voice-chat frontend extension

## 3. Technology comparisons & decisions

### 3.1 Search engine analysis
- **MeiliSearch vs OpenSearch**: lightweight application search vs full search/analytics/RAG platform
- **OpenSearch vs Elasticsearch**: Apache 2.0 fork vs AGPL/SSPL original; similar capabilities, OpenSearch avoids vendor lock-in
- **SearxNG vs OpenSearch/MeiliSearch**: external web metasearch vs internal content search (complementary, not competing)

### 3.2 RAG frameworks
- **LightRAG vs OpenRAG**: graph-enhanced retrieval (new capability) vs pre-packaged platform (duplicates existing services)
- **LightRAG vs GraphRAG (Microsoft)**: flexible storage backends + academic benchmarks vs enterprise pipeline focus
- **Docling vs RAG-Anything**: structured document parsing (tables, layout) vs multimodal knowledge entity extraction (complementary)
- **RAGFlow**: alternative worth tracking for deep document understanding and citation grounding (77.6k stars)

### 3.3 Authentication solutions
- **Keycloak vs Supabase Auth**: enterprise features vs simplicity
- **Self-hosted vs cloud**: control vs convenience

### 3.4 Vector database options
- **pgvector vs Weaviate vs Qdrant**: SQL integration vs specialized features vs performance
- **Single vs multi-vendor**: simplicity vs flexibility

### 3.5 AI workflow & agent builders
- **n8n vs LangFlow**: general automation with AI add-ons vs AI-native pipeline builder
- **Complementary use**: n8n for system orchestration, LangFlow for deep AI pipelines

### 3.6 Container orchestration
- **Docker Compose vs Kubernetes**: development vs production scaling
- **Local vs cloud**: resource efficiency vs unlimited scaling

### 3.7 LLM observability
- **Langfuse vs Helicone vs Phoenix (Arize)**: Langfuse is the actively-developed choice with a native LiteLLM callback; Helicone entered maintenance mode in early 2026; Phoenix is stronger for offline eval than in-production tracing
- **LLM observability vs infra metrics**: Langfuse covers prompts / traces / evals / cost attribution; Prometheus + Grafana covers container and host metrics. Both belong in the stack — they don't substitute.

### 3.8 Agent memory
- **LangMem (existing, in-backend) vs Mem0 (external, light) vs Letta / MemGPT (external, stateful)**: LangMem is Open WebUI-centric and embedded; Mem0 is the multi-agent external default; Letta is the upgrade for agents that need to reason over their own memory hierarchy
- **Complementary, not competing**: a deployment can hold all three for different consumers (backend uses LangMem; Hermes / OpenClaw use Mem0; long-running Hermes personas optionally use Letta)

### 3.9 Image-generation UIs
- **ComfyUI (existing) vs InvokeAI vs A1111 / Automatic1111**: ComfyUI for graph and batch generation; InvokeAI for canvas and inpainting; A1111 is stagnant and not recommended in 2026
- **Forge** is a reasonable A1111-compatible fork if user preference demands the A1111 UX, but InvokeAI is the better complement to ComfyUI for a generative-AI stack

### 3.10 MCP gateway
- **`mcpo` vs MetaMCP vs IBM ContextForge**: `mcpo` is the lowest-friction MCP→OpenAPI wrapper (from the Open WebUI org); MetaMCP adds namespace and RBAC aggregation; IBM ContextForge has the broadest transport support
- **Recommended path**: start with `mcpo` (Open WebUI v0.6.31+ already speaks MCP); add MetaMCP when more than 2–3 MCP servers warrant aggregated, namespaced routing

### 3.11 Personal-knowledge surfaces
- **SilverBullet vs Karakeep vs Paperless-ngx**: SilverBullet for authored markdown notes (files on disk); Karakeep for captured / AI-tagged web content; Paperless-ngx for OCR'd document archive. All three feed the same downstream RAG indexes — pick by capture mode, not as alternatives.
- **Skip for the AI-stack lens**: Logseq, Trilium, AppFlowy, AFFiNE — client-centric or database-backed designs that don't expose files to RAG ingestion as cleanly

### 3.12 Code-first vs low-code workflows
- **n8n (existing) vs Windmill**: n8n for low-code system orchestration; Windmill for code-native ML / data pipelines (training, evals, scheduled backtests). They complement rather than substitute.
- **Activepieces (MIT) and Trigger.dev** are alternatives to Windmill when AGPL is a deployment concern

### 3.13 Secrets management
- **`.env` (current) vs Infisical vs HashiCorp Vault vs Vaultwarden**: `.env` is the current baseline; Infisical adds rotation, audit, and per-environment scoping with reasonable operational cost; Vault is overkill for this stack's scale; Vaultwarden is a password vault (wrong layer entirely)

### 3.14 Embedding inference
- **LiteLLM-routed (current) vs TEI (dedicated)**: LiteLLM is fine for chat completions and ad-hoc embedding calls; TEI is the right layer once embedding throughput becomes dominant (bulk RAG indexing, reranking pipelines). Register TEI as a LiteLLM upstream so consumer code does not change.
- **Reranking** is unique to TEI within the stack — neither Weaviate's bundled multi2vec-clip nor LiteLLM provide a `/rerank` endpoint today

### 3.15 Image-to-3D models
- **Hunyuan3D-2 (Tencent) vs TRELLIS (Microsoft) vs InstantMesh**: Hunyuan3D-2 leans textured-mesh; TRELLIS produces mesh, Gaussian splatting, and radiance fields from a single image; InstantMesh is faster but lower fidelity. Pick TRELLIS for breadth, Hunyuan3D-2 for textured-mesh quality.
- **NerfStudio is orthogonal**: reconstruction from real captures rather than generation from concept, and runs in parallel rather than competing

### 3.16 Trading engines
- **NautilusTrader vs Freqtrade vs OctoBot**: NautilusTrader is Python / Rust, multi-asset (crypto, FX, equities, futures), with paper and live execution; Freqtrade is crypto-only with a larger community and simpler scope; OctoBot is similar in scope to Freqtrade
- **For this stack**: NautilusTrader is the default candidate because the multi-asset surface matches the broader AI-app framing; Freqtrade is a fine fit when crypto-only is the explicit goal

### 3.17 Time-series storage
- **TimescaleDB extension (on existing Postgres) vs standalone QuestDB / InfluxDB**: TimescaleDB is the zero-new-container option (extension on Supabase Postgres) and the recommended default; QuestDB / InfluxDB are worth considering only if write throughput exceeds Postgres + TimescaleDB headroom

### 3.18 Considered and rejected

The following candidates were evaluated and explicitly *not* recommended at this time. Recording them prevents the same suggestions from cycling back into roadmap discussions.

- **A1111 (Automatic1111) / Forge as a default SD UI** — stagnant or fork-only as of 2026 relative to ComfyUI and InvokeAI. ComfyUI remains the existing default; InvokeAI fills the canvas / inpaint gap.
- **Firecrawl self-hosted** — AGPL-3.0 license contaminates the stack's permissive boilerplate posture. Crawl4AI (Apache-2.0) is the choice for web crawling.
- **Helicone** — entered maintenance mode in early 2026; Langfuse is the actively-developed equivalent with native LiteLLM callback support.
- **Vaultwarden as a secrets manager** — Vaultwarden is a Bitwarden-compatible *password* vault, not a secrets manager. Infisical (or HashiCorp Vault for enterprise scenarios) is the right layer.
- **Logseq / Trilium / AppFlowy / AFFiNE as "Obsidian-as-a-service"** — all client-centric or 5+ container database-backed stacks. For an LLM/RAG stack the value is files RAG can read; SilverBullet's files-on-disk model is the right fit, with Karakeep and Paperless-ngx covering capture and archive.
- **Phoenix (Arize) as the primary LLM observability backend** — strong for offline eval but Langfuse's LiteLLM-callback integration and richer prompt management make it the better in-production fit.
- **"LiteRAG"** — not a deployable framework. The canonical project the name evokes is **LightRAG (HKUDS)**; see the Tier 2 entry.
- **Sourcegraph self-hosted** — heavyweight code-intelligence platform out of scope for the stack's AI-application focus.
- **A standalone JupyterHub alternative (Marimo-only)** — JupyterHub already ships; Marimo as a *complement* is possible but not on the Tier 1/2/3 path today.
- **Mailpit / Mailhog as a roadmap item** — useful for development but too narrow to warrant Tier 1/2/3 placement; can be added ad-hoc when an email-capture use-case actually lands.

**From the vertical-scenarios stack-fit research (2026-05-17 — see git log for the design doc, retired with `docs/superpowers/` 2026-05-22):**

- **Needle Engine** — proprietary EULA with license-server requirement; fails the stack's permissive-boilerplate posture.
- **PlayCanvas Engine as a primary viewer** — engine is MIT but its value is the proprietary cloud editor that cannot be self-hosted. Three.js + react-three-fiber covers the same ground without the lock-in.
- **Stable Fast 3D (SF3D, Stability)** — Stability AI Community License is source-available but commercial-restricted; functionally overlaps Hunyuan3D-2 / TRELLIS. Pick those instead.
- **Wonder3D / Wonder3D++** — CC-BY-NC non-commercial license; unusable for the stack's posture.
- **Rodin (Hyper3D)** — closed-source SaaS; no self-host path.
- **tldraw as the sketch surface** — commercial use requires watermark or paid license. Excalidraw + `excalidraw-room` covers the same ground under MIT.
- **Drawpile / Goxel** — GPL-3.0 viral copyleft; Excalidraw and Vengi cover those slots permissively.
- **Liveblocks self-hosted** — Apache-2.0 core but the practical production stack is SaaS-only; Colyseus (game-state) and Yjs / Hocuspocus (document-state, future) cover the multiplayer story.
- **Yjs + Hocuspocus** as primary Dreamscapes multiplayer — CRDT shape suits *co-editing the scene graph*, not 60Hz position sync. Revisit only when co-edit features land.
- **TileServer-GL** — for pre-baked MBTiles; Dreamscapes generates worlds dynamically, so Tegola is the better fit.
- **Freqtrade / OctoBot as fleet manager** — GPL-3.0 viral copyleft; Hummingbot's API + Dashboard is the only Apache-2.0 fleet plane in 2026.
- **NautilusTrader as the orchestrator** — its docs explicitly state distributed orchestration is out of scope. Run it as a worker engine under Hummingbot, not as the fleet plane.
- **Jesse** — MIT but single-bot framework without fleet primitives.
- **Lean / QuantConnect** — Apache-2.0 but C#-centric and heavy; the brokerage-abstraction value is realised better via direct CCXT + per-broker libraries inside the in-house wallet service.
- **Silent Shard / Web3Auth MPC wallets** — over-engineered for a single-operator fleet; OpenBao Transit + per-exchange API-key scoping covers the threat model. Revisit if threshold signing across multiple machines becomes a real requirement.
- **Daytona for the LLM-strategy sandbox** — Docker rootless gives only kernel-shared isolation; E2B's Firecracker microVMs are the correct safety bar for untrusted LLM-generated code.
- **Blockchain-anchored / specialized trading audit-trail products** — proprietary, expensive, over-engineered at boutique scale; OpenSearch + MinIO Object Lock + Merkle anchors in Postgres is sufficient.

**From the data-engineering stack-fit research (2026-05-21 — see git log for the design doc, retired with `docs/superpowers/` 2026-05-22):**

- **Apache Zeppelin** — ✅ **Shipped 2026-06-04** (PR #35) as the Spark-first notebook UI. The original rejection rationale (cadence slow + Almond on JupyterHub covers Scala) was overridden by user request for a dedicated Spark-first notebook interface. Both surfaces coexist: Zeppelin for Spark-first paragraph-style authoring with a pre-configured Spark interpreter (JDBC requires one-time UI setup — see service README); JupyterHub + Almond for general-purpose Scala kernels alongside Python.
- **Polynote (Netflix)** — abandoned; last release 2022.
- **Airbyte** — Docker Compose support deprecated in 2025; production now requires Kubernetes + Postgres + Redis + Temporal; ELv2 license is incompatible with the stack's permissive-boilerplate posture. **dlt** (Apache-2.0 Python library) is the recommended Python-first replacement; **Meltano** (MIT) is the documented Singer-based fallback.
- **DataHub** as the data catalog — GMS + MAE/MCE Kafka consumers + Elasticsearch + Neo4j is too heavy for compose; **OpenMetadata** (Postgres + OpenSearch + server + UI) has the right footprint.
- **Apache Atlas** — legacy Hadoop-era; superseded by OpenMetadata / DataHub for modern lakehouse work.
- **Apache Hive** — legacy; Trino + Iceberg replaces it.
- **Apache Hudi** — declining mindshare in 2026; Apache Iceberg owns the table-format space.
- **Apache Polaris** as the Iceberg catalog (vs Lakekeeper) — defensible (Apache governance, Snowflake-donated, supports Iceberg + Delta + Hudi) but operationally heavier than Lakekeeper's Rust single-binary for this stack's footprint. Documented as a permitted alternative.
- **Pachyderm** — HPE discontinued OSS releases in 2024.
- **Featureform** — smaller community than **Feast**; defer.
- **W&B Local** — requires commercial license.
- **StarRocks** as primary OLAP — different shape from Trino (analytical DB with federation bolted on, not a pure federation engine); defer until Trino dashboard latency becomes a real pain point.
- **ClearML** as MLOps primary — richer than MLflow but operationally heavier; defer until MLflow's tracking-only feature surface is insufficient.

**From the MCP gateway research (2026-05-22 — both options documented in the Tier 1 MCP entry):**

- **Smithery** — SaaS-only control plane (no self-hosted server inventory); a June 2025 path-traversal CVE exposed 3,000 customer-deployed servers. Not a self-host product, regardless of operational appeal.
- **PulseMCP** — a directory / catalog site indexing other people's MCP servers; not a self-hostable component.
- **`hwdsl2/docker-mcp-gateway`** — Caddy + MCPHub community wrapper with a handful of MCP servers bundled. Only ~4 GitHub stars at time of evaluation; insufficient community trust for production roadmap. The underlying engine (MCPHub) is fine on its own.
- **Pure MCPHub** (samanhappy/mcphub) as the aggregator — Apache-2.0 and active, but **MetaMCP is strictly more capable for the same operational cost** (MetaMCP also has native OpenAPI, namespacing, RBAC, OAuth, and an inspector; MCPHub lacks OpenAPI). Pick MCPHub only if a deployment specifically needs its lighter footprint and accepts MCP-only protocol exposure.
- **Klavis "Strata" aggregator** (Apache-2.0, 100+ pre-built vendor server images) — defer; smaller ecosystem than MetaMCP and Docker MCP Gateway, and the "one Compose service per server" model is operationally heavier than MetaMCP for our scale. Worth revisiting if vendor-blessed OAuth-shaped SaaS connectors become a hard requirement.
- **Anthropic reference MCP servers as standalone offering (no aggregator)** — fine as the *source* of individual MCP servers (used in our Phase 1 starter set), but does not solve the aggregation / namespacing / OpenAPI problem on its own. Combine with MetaMCP (Option A) or Docker MCP Gateway (Option B), not as a replacement for either.

## 4. Implementation strategy

### 4.1 Development principles

1. **Backward compatibility**: new features should not break existing deployments
2. **Opt-in features**: capabilities are added as optional integrations
3. **Documentation**: every feature ships with reference and quick-start docs
4. **Testing coverage**: automated testing for new capabilities
5. **Community driven**: feature priorities reflect user feedback

### 4.2 Release schedule

- **Monthly releases**: bug fixes and minor enhancements
- **Quarterly releases**: feature additions
- **Annual releases**: architecture improvements and breaking changes

### 4.3 Feature development process

1. **Community feedback**: gather requirements from users
2. **Design documents**: technical specifications
3. **Implementation**: development with automated testing
4. **Beta testing**: community testing phase
5. **Documentation**: reference and example docs
6. **Release**: stable release with migration guides

## 5. Community & ecosystem

### 5.1 Open source contributions

- **Plugin system**: allow community-developed service integrations
- **Template library**: community-contributed templates and workflows
- **Documentation improvements**: community-driven documentation enhancements
- **Testing & validation**: community testing on various platforms

### 5.2 Integration partnerships

- **AI model providers**: partnerships with model hosting services
- **Cloud providers**: deployment guides and templates
- **Enterprise vendors**: integration with enterprise tools and platforms

## 6. Long-term vision

**Ecosystem platform**
- Grow from a development stack into a multi-tenant deployment platform
- Support for plugin architectures and third-party integrations
- Marketplace for AI workflows and components

**AI-first operations**
- AI-powered stack management and optimization
- Predictive scaling and resource management
- Automated troubleshooting and self-healing capabilities

**Universal integration**
- Standard APIs for AI service integration
- Protocol standardization across AI tools
- Cross-platform and cross-stack compatibility

**Foundation for vertical AI applications**
- **3D and game generation** — host the full pipeline end-to-end: text or sketch (**Excalidraw** + ControlNet) → image (**ComfyUI**) → 3D mesh fast-path (**InstantMesh**) or fidelity-path (**Hunyuan3D-2** / **TRELLIS**) or splat-path (**DreamGaussian**) → scene assembly (**Blender headless**) → real-scene capture (**NerfStudio**) → optimized asset pipeline (**glTF-Transform** / **SuperSplat**) → browser viewer (**Three.js** + **react-three-fiber**) → multiplayer (**Colyseus**); world representations evolve from snowdome-style free-form scenes to voxel (**Vengi**-converted) and RTS-tile (**PostGIS** + **Tegola** + **MapLibre**) maps, with **LightRAG** indexing the asset graph and **AudioCraft / MusicGen** providing procedural audio
- **Financial / trading AI** — paper-first fleet (**Hummingbot API** + Dashboard orchestrating **NautilusTrader** workers via **CCXT** and equities adapters) over **OpenBB Platform** data, **TimescaleDB**-backed market history on the existing Supabase Postgres, **Redpanda** for streaming fan-out; strategies promoted to live via signed **Windmill** flows, executed against **OpenBao**-custodied keys, gated by an in-house risk-control service, evaluated at fleet scale on **Ray** with **E2B** sandboxes for LLM-generated code, monitored via **Langfuse** + **Grafana** + Hummingbot Dashboard, audited via **OpenSearch** + **MinIO Object Lock** with Merkle anchors, and exposed to every LLM consumer through the **MCP gateway**
- **RAG specializations** — compose domain-tuned RAG pipelines (legal, academic, financial-research, personal-knowledge) from **TEI** + **LightRAG** + **GROBID** / **Apache Tika** / **Docling** / **whisperX**, with the appropriate ingestion surface (**Karakeep** for captures, **SilverBullet** for authored notes, **Paperless-ngx** for OCR'd archives, **Crawl4AI** + **Browserless** for the open web)
- **Data Engineering — lakehouse + ML platform** — host a complete data-engineering pipeline: **MinIO** lake → **Apache Iceberg** tables in **Lakekeeper** catalog → **Trino** + **DuckDB** for federated SQL → **dbt-core** transformations → **Apache Superset** for BI; orchestrated by **Dagster** (or **Apache Airflow** under `ORCHESTRATOR_SOURCE`); ingested via **dlt** + **Debezium Server** → Redpanda; quality enforced by **Soda Core** + **Elementary**; cataloged in **OpenMetadata**; MLOps via **MLflow** + **lakeFS** + **Feast**; Scala notebooks via **Almond** on existing **JupyterHub**, with **Spark Connect** keeping the rest of the stack Python-native

---

## 7. Contributing to the roadmap

Community input on roadmap priorities is welcome:

- **Vote on features**: GitHub discussions for feature voting
- **Suggest features**: submit feature requests via GitHub issues
- **Contribute code**: help implement roadmap features
- **Improve documentation**: help document new capabilities

**Join the conversation**: [GitHub Discussions](https://github.com/thekaveh/atlas/discussions)

---

*This roadmap is a living document and will be updated regularly based on community feedback and technological developments.*
