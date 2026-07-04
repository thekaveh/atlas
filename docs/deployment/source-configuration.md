# SOURCE Configuration Guide

This guide explains the SOURCE-based configuration system that makes Atlas flexible and modular.

## 1. Interactive Setup Wizard

The easiest way to configure SOURCE variables is the **interactive setup wizard**. Run `./start.sh` with no arguments to launch it. The wizard walks you through each service, shows available options with contextual hints, and validates dependencies in real time. See the [Interactive Setup Wizard Guide](../quick-start/interactive-setup-wizard.md) for details.

## 2. Understanding SOURCE Variables

SOURCE variables control how each service is deployed — whether in a Docker container, using a localhost installation, or disabling the service entirely. (The legacy `external` and `api` source values were retired earlier in 2026; see the `LLM_PROVIDER_SOURCE` migration note below.)

## 3. Service SOURCE Support Matrix

This matrix lists every `*_SOURCE` variable currently exposed in `.env.example`. Detailed prose below focuses on the most common user-facing services; init/internal rows are included here so operators can understand what appears in `.env`.

| SOURCE variable | Default | Options | Category | Notes |
|---|---|---|---|---|
| `LLM_PROVIDER_SOURCE` | `ollama-container-cpu` | `ollama-container-cpu`, `ollama-container-gpu`, `ollama-localhost`, `none` | User-facing | Local Ollama upstream behind LiteLLM. Use `none` for cloud-only operation. |
| `CLOUD_OPENAI_SOURCE` | `disabled` | `enabled`, `disabled` | User-facing | Toggles OpenAI as a LiteLLM upstream. Requires `OPENAI_API_KEY`. |
| `CLOUD_ANTHROPIC_SOURCE` | `disabled` | `enabled`, `disabled` | User-facing | Toggles Anthropic as a LiteLLM upstream. Requires `ANTHROPIC_API_KEY`. |
| `CLOUD_OPENROUTER_SOURCE` | `disabled` | `enabled`, `disabled` | User-facing | Toggles OpenRouter as a LiteLLM upstream. Requires `OPENROUTER_API_KEY`. |
| `LITELLM_SOURCE` | `container` | `container` | Infra / always-on | LiteLLM gateway. Always on; not user-disableable. |
| `COMFYUI_SOURCE` | `container-cpu` | `container-cpu`, `container-gpu`, `localhost`, `disabled` | User-facing | Image generation service. |
| `PROMETHEUS_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Observability scraper + TSDB. Bundles node-exporter and cAdvisor; gates postgres-exporter / redis-exporter sidecars. |
| `GRAFANA_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Observability dashboards + unified alerting. Pre-provisions the Prometheus datasource and 7 starter dashboards. |
| `WEAVIATE_SOURCE` | `container` | `container`, `localhost`, `disabled` | User-facing | Vector database. |
| `MINIO_SOURCE` | `container` | `container`, `disabled` | User-facing | S3-compatible artifact-tier object storage. |
| `N8N_SOURCE` | `container` | `container`, `disabled` | User-facing | Workflow automation. |
| `SEARXNG_SOURCE` | `container` | `container`, `disabled` | User-facing | Privacy metasearch. |
| `CRAWL4AI_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Browser-backed extraction API for Local Deep Researcher and n8n HTTP workflows. Token-protected and disabled by default. |
| `TIKA_SOURCE` | `disabled` | `container`, `tika-localhost`, `disabled` | User-facing optional | Apache Tika fallback extractor for long-tail document formats. Disabled by default and degraded/plain-text by design. |
| `LLM_GRAPH_BUILDER_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Neo4j Labs document-to-knowledge-graph builder UI/API for the RAG track. Requires in-stack Neo4j and LiteLLM. |
| `CELERY_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Redis-backed async backend worker tier plus Flower monitor for long-running memory/research-style jobs. |
| `SUPAVISOR_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Internal-only Supabase Postgres transaction pooler for selected app clients; no Kong alias or host slot-allocated port in v1. |
| `MCP_SERVERS_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Curated MCP package exposing read-oriented Postgres, Neo4j, and SearXNG tools. Hard-gated on Neo4j and SearXNG. |
| `BLENDER_MCP_SOURCE` | `disabled` | `localhost`, `disabled` | User-facing optional | Host-only Blender MCP bridge for creative 3D experiments. Development-only, disabled by default, and intentionally not exposed through Kong. |
| `LANGFUSE_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | LLM trace, prompt, eval, latency, and cost observability for LiteLLM-routed calls. Hard-gated on MinIO. |
| `OTEL_COLLECTOR_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Internal-only OpenTelemetry ingest for backend/LiteLLM traces; requires `TEMPO_SOURCE=container` when enabled. No Kong route in v1. |
| `TEMPO_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Internal-only Grafana Tempo trace store with local development storage and Grafana datasource provisioning. No Kong route in v1. |
| `LOKI_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Internal-only Grafana Loki log store with short local retention and Grafana datasource provisioning. Log shipping remains a follow-up. |
| `MLFLOW_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Experiment tracking and MinIO-backed artifacts for the ML Engineering track. Hard-gated on MinIO. |
| `VERBA_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Archived/discontinued Weaviate RAG demo UI for the RAG track. Disabled by default; hard-gated on Weaviate and wired to LiteLLM. |
| `OPENCLAW_SOURCE` | `disabled` | `container`, `localhost`, `disabled` | User-facing | AI messaging agent. |
| `HERMES_SOURCE` | `container` | `container`, `localhost`, `disabled` | User-facing | Programmable AI agent runtime (Nous Research). Routes reasoning through LiteLLM and appears as the `hermes-agent` model to every consumer. |
| `STT_PROVIDER_SOURCE` | `speaches-container-cpu` | `speaches-container-cpu`, `speaches-container-gpu`, `parakeet-container-gpu`, `parakeet-localhost`, `whisper-cpp-localhost`, `disabled` | User-facing optional | Speech-to-text provider. Speaches is the CPU-friendly default; Parakeet remains for SOTA NVIDIA; whisper.cpp is the best Apple Silicon native option. |
| `TEI_RERANKER_SOURCE` | `disabled` | `container-cpu`, `container-gpu`, `localhost`, `disabled` | User-facing optional | Cross-encoder reranker (default `mxbai-rerank-base-v1`) for RAG quality lift. LightRAG direct wiring is disabled until a compatible adapter exists. |
| `TTS_PROVIDER_SOURCE` | `speaches-container-cpu` | `speaches-container-cpu`, `speaches-container-gpu`, `chatterbox-container-gpu`, `chatterbox-localhost`, `disabled` | User-facing optional | Text-to-speech provider. Speaches serves Kokoro/Piper voices; Chatterbox adds 5-sec zero-shot voice cloning. |
| `DOC_PROCESSOR_SOURCE` | `disabled` | `docling-container-gpu`, `docling-localhost`, `disabled` | User-facing optional | Document processing provider. |
| `JUPYTERHUB_SOURCE` | `container` | `container`, `disabled` | User-facing optional | Data science, PySpark, and PyIceberg lakehouse notebooks; adaptive integrations. |
| `RAY_SOURCE` | `disabled` | `ray-container-cpu`, `ray-container-gpu`, `disabled` | User-facing optional | Distributed compute cluster (head + workers). Backend `/api/ray/*` and notebook 07 light up when enabled. |
| `AIRFLOW_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Workflow orchestration with seeded Connections and SparkSubmit/S3A lakehouse smoke. |
| `SPARK_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Spark master/workers + Connect sidecar + history server; lakehouse-ready when Iceberg REST is enabled. |
| `ZEPPELIN_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Zeppelin notebooks; seeded for standalone Spark (`spark://spark-master:7077`) plus MinIO/Iceberg (hard-gated on `SPARK_SOURCE=container`). |
| `JENKINS_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Jenkins controller with Maven and MinIO JAR publishing seam for data-eng Spark apps. |
| `ICEBERG_REST_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Internal Iceberg REST catalog backed by Supabase Postgres and MinIO lakehouse buckets. |
| `TRINO_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | SQL query engine over the Iceberg REST + MinIO lakehouse path. Hard-gated on MinIO and Iceberg REST. |
| `REDPANDA_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Kafka-compatible streaming broker and Console for the data-eng track. Spark receives `SPARK_KAFKA_BOOTSTRAP_SERVERS=redpanda:9092` when enabled. |
| `MULTI2VEC_CLIP_SOURCE` | `container-cpu` | `container-cpu`, `container-gpu`, `disabled` | User-facing optional | Multimodal Weaviate vectorizer. |
| `LIGHTRAG_SOURCE` | `disabled` | `container`, `localhost`, `disabled` | User-facing optional | Graph-augmented RAG server. Storage adapts to Supabase pgvector, Neo4j, Redis. |
| `LOCAL_DEEP_RESEARCHER_SOURCE` | `container` | `container`, `disabled` | User-facing optional | Local research/orchestration service. |
| `OPEN_WEB_UI_SOURCE` | `container` | `container`, `disabled` | Adaptive application | Main chat UI; adapts to LLM provider. |
| `BACKEND_SOURCE` | `container` | `container` | Adaptive core | Always-on Backend API; not disableable in this remediation track. |
| `REDIS_SOURCE` | `container` | `container` | Infra | Cache/session/queue service. |
| `KONG_API_GATEWAY_SOURCE` | `container` | `container` | Infra | API gateway and friendly host routing. |
| `NEO4J_GRAPH_DB_SOURCE` | `container` | `container`, `localhost`, `disabled` | Infra / user-facing data | Graph database. |
| `SUPABASE_DB_SOURCE` | `container` | `container` | Infra | PostgreSQL database. |
| `SUPABASE_META_SOURCE` | `container` | `container`, `disabled` | Infra | Supabase metadata service. |
| `SUPABASE_STORAGE_SOURCE` | `container` | `container`, `disabled` | Infra | Supabase storage service. |
| `SUPABASE_AUTH_SOURCE` | `container` | `container`, `disabled` | Infra | Supabase auth service. |
| `SUPABASE_API_SOURCE` | `container` | `container`, `disabled` | Infra | Supabase REST API. |
| `SUPABASE_REALTIME_SOURCE` | `container` | `container`, `disabled` | Infra | Supabase realtime service. |
| `SUPABASE_STUDIO_SOURCE` | `container` | `container`, `disabled` | Infra UI | Supabase admin UI. |
| `WEAVIATE_INIT_SOURCE` | `container` | `container`, `disabled` | Auto-managed init | Initializes Weaviate schemas/config. |
| `MINIO_INIT_SOURCE` | `container` | `container`, `disabled` | Auto-managed init | Initializes MinIO buckets, IAM policies, and service accounts. |
| `COMFYUI_INIT_SOURCE` | `container` | `container`, `disabled` | Auto-managed init | Initializes ComfyUI assets/config. |
| `N8N_INIT_SOURCE` | `container` | `container`, `disabled` | Auto-managed init | Installs n8n community nodes on first boot; workflow templates are imported manually. |
| `OPENCLAW_INIT_SOURCE` | `container` | `container`, `disabled` | Auto-managed init | Initializes OpenClaw config where applicable. |
| `HERMES_INIT_SOURCE` | `container` | `container`, `disabled` | Auto-managed init | Renders `/opt/data/config.yaml` for Hermes from environment (model, TTS, STT, ComfyUI host override). |
| `SUPABASE_DB_INIT_SOURCE` | `container` | `container`, `disabled` | Auto-managed init | Initializes Supabase database state. |
| `CLOUDFLARED_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | Cloudflare Tunnel public edge — terminates TLS at Cloudflare and proxies to Kong (egress-only, no inbound ports). Requires `CLOUDFLARE_TUNNEL_TOKEN`. |
| `BACKUP_SOURCE` | `disabled` | `container`, `disabled` | User-facing optional | On-demand backup runner — Postgres dump + named-volume snapshots pushed to MinIO/S3. Invoke via `docker compose run --rm backup`. |

> The `litellm-init` container is mandatory and has no SOURCE toggle — it always runs when the stack starts. `litellm-init` provisions the dedicated `litellm` Postgres database and renders `volumes/litellm/config.yaml` from the YAML model catalogs (`services/ollama/models.yaml`, `services/litellm/models.yaml`) + the wizard's `*_USER_MODELS` env vars, via `model_resolver`. No separate catalog-init container is involved in LLM model selection.

### 3.1 Services Supporting Localhost

These services can run on your host machine instead of in containers:

| Service | SOURCE Variable | Localhost Option | Benefits |
|---------|----------------|------------------|----------|
| **Ollama** (LiteLLM upstream) | `LLM_PROVIDER_SOURCE` | `ollama-localhost` | Faster, uses existing models, less memory. LiteLLM still fronts the upstream. |
| **ComfyUI** | `COMFYUI_SOURCE` | `localhost` | Direct access, custom setups, faster |
| **Weaviate** | `WEAVIATE_SOURCE` | `localhost` | Custom configuration, performance |
| **Neo4j** | `NEO4J_GRAPH_DB_SOURCE` | `localhost` | Use an existing graph database |
| **OpenClaw** | `OPENCLAW_SOURCE` | `localhost` | Native performance, existing config |
| **Hermes Agent** | `HERMES_SOURCE` | `localhost` | Operate your real machine (shell, browser, microphone); host-installed Hermes |
| **LightRAG** | `LIGHTRAG_SOURCE` | `localhost` | Use a host-installed LightRAG process |
| **STT Provider** | `STT_PROVIDER_SOURCE` | `parakeet-localhost`, `whisper-cpp-localhost` | Run STT natively (best on Apple Silicon — Metal+ANE for whisper.cpp, MLX for Parakeet) |
| **TEI Reranker** | `TEI_RERANKER_SOURCE` | `localhost` | Use a host-installed TEI reranker process |
| **TTS Provider** | `TTS_PROVIDER_SOURCE` | `chatterbox-localhost` | Run Chatterbox voice cloning natively (macOS MPS / Linux) |
| **Document Processor** | `DOC_PROCESSOR_SOURCE` | `docling-localhost` | Use a host Docling service |
| **Apache Tika** | `TIKA_SOURCE` | `tika-localhost` | Use a host Tika server for long-tail fallback extraction |
| **Blender MCP** | `BLENDER_MCP_SOURCE` | `localhost` | Use a host-installed Blender MCP add-on/server without exposing it through Kong |

### 3.2 Container-Only or Stack-Managed Services

Container-only and stack-managed services should normally be left at their defaults unless you are intentionally reducing the stack or debugging a specific component. Init service SOURCE variables are usually managed by the startup flow and should not be the first knob users change.

### 3.3 Feature Flags (Non-SOURCE)

Some features within services are controlled by feature flags rather than SOURCE variables:

| Feature | Variable | Options | Notes |
|---------|----------|---------|-------|
| **LangMem Memory** | `LANGMEM_ENABLED` | `true`, `false` | Persistent conversation memory embedded in the Backend service. |

### 3.4 Wizard Model Selections (Non-SOURCE)

The interactive wizard's per-provider multiselects persist as comma-separated env vars in `.env`. On each `docker compose up`:

- **`litellm-init`** calls `model_resolver.active_models(env)` — which reads `services/ollama/models.yaml`, `services/litellm/models.yaml`, and the `*_USER_MODELS` vars below — to render `volumes/litellm/config.yaml`. No DB query involved.
- **`ollama-pull`** pre-pulls Ollama models (container sources only) using the same resolved active set.

| Variable | Set by | Default | Notes |
|---|---|---|---|
| `OLLAMA_USER_MODELS` | Single unified Ollama models multiselect (source-aware; localhost rows are badged `[pulled]` / `[library]`). | Default-active baseline (qwen3.6:latest, qwen3-embedding:0.6b, nomic-embed-text). | Consumed by `model_resolver` for every Ollama source. Pulled by `ollama-pull` only for container sources. |
| `OLLAMA_CUSTOM_MODELS` | Ollama "additional models to pull" free-text step. | Empty. | Comma-separated. Pulled by `ollama-pull` for container sources only. |
| `OPENAI_USER_MODELS` | OpenAI multiselect (live `/v1/models` fetch). | Curated default-active intersection (gpt-5, gpt-5-mini, text-embedding-3-large) when key valid. | Requires `OPENAI_API_KEY`. |
| `ANTHROPIC_USER_MODELS` | Anthropic multiselect (live `/v1/models` fetch). | Curated default-active intersection (claude-opus-4-7, claude-sonnet-4-6) when key valid. | Requires `ANTHROPIC_API_KEY`. |
| `OPENROUTER_USER_MODELS` | OpenRouter multiselect (live `/api/v1/models` fetch). | `openrouter/auto` when reachable. | Requires `OPENROUTER_API_KEY`. |

## 4. Detailed SOURCE Configurations

### 4.1 LLM access (LiteLLM gateway + Ollama upstream + cloud toggles)

LLM access in this stack is split between **LiteLLM** (the always-on OpenAI-compatible gateway every consumer reads) and four configurable upstreams behind it: an Ollama engine plus three cloud providers. See [LiteLLM Gateway](../../services/litellm/README.md) for the consumer-facing surface; the variables below pick what LiteLLM forwards to.

#### 4.1.1 `LLM_PROVIDER_SOURCE` — Ollama upstream (single-select)

##### 4.1.1.1 `ollama-container-cpu` (Default)
```bash
LLM_PROVIDER_SOURCE=ollama-container-cpu
```
- **Use case**: Default setup, no local Ollama required
- **Pros**: No setup needed, works everywhere
- **Cons**: Higher memory usage, slower model loading
- **Requirements**: None

##### 4.1.1.2 `ollama-container-gpu`
```bash
LLM_PROVIDER_SOURCE=ollama-container-gpu
```
- **Use case**: GPU acceleration in container
- **Pros**: GPU acceleration, no local setup
- **Cons**: Requires NVIDIA GPU + Docker GPU support
- **Requirements**: NVIDIA Container Toolkit

##### 4.1.1.3 `ollama-localhost`
```bash
LLM_PROVIDER_SOURCE=ollama-localhost
```
- **Use case**: Use existing Ollama installation
- **Pros**: Faster startup, reuse models, less container memory
- **Cons**: Requires local Ollama setup
- **Requirements**: Ollama installed and running locally

Setup for localhost:
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama service
ollama serve &

# Pull required models
ollama pull qwen3.6:latest
ollama pull qwen3-embedding:0.6b
```

##### 4.1.1.4 `none`
```bash
LLM_PROVIDER_SOURCE=none
```
- **Use case**: Cloud-only operation (no local Ollama engine)
- **Pros**: Minimal local resource usage; LiteLLM forwards everything to enabled cloud providers
- **Cons**: API costs, internet dependency
- **Requirements**: At least one of `CLOUD_OPENAI_SOURCE`, `CLOUD_ANTHROPIC_SOURCE`, `CLOUD_OPENROUTER_SOURCE` must be `enabled`. The bootstrapper refuses to start when `LLM_PROVIDER_SOURCE=none` AND every cloud source is `disabled`.

The legacy values `LLM_PROVIDER_SOURCE=api` and `LLM_PROVIDER_SOURCE=disabled` have been removed — use `none` together with the per-provider cloud toggles below instead.

#### 4.1.2 `CLOUD_OPENAI_SOURCE` / `CLOUD_ANTHROPIC_SOURCE` / `CLOUD_OPENROUTER_SOURCE` (multi-toggle)

Each cloud provider is an independent `enabled` / `disabled` switch — turn on as many as you want simultaneously. Consumers request model IDs against `LITELLM_BASE_URL`; LiteLLM routes per-provider based on the active model set that `model_resolver` computes from the YAML catalogs + env on each `docker compose up`.

```bash
CLOUD_OPENAI_SOURCE=enabled          # requires OPENAI_API_KEY
CLOUD_ANTHROPIC_SOURCE=enabled       # requires ANTHROPIC_API_KEY
CLOUD_OPENROUTER_SOURCE=enabled      # requires OPENROUTER_API_KEY
```

#### 4.1.3 Per-provider activation rules (applied by `model_resolver` on every `docker compose up`)

| Provider state | `*_USER_MODELS` env var | Result |
|---|---|---|
| `disabled` OR no API key | (any) | Zero active entries for that provider — LiteLLM routes nothing to it. |
| `enabled` + key | non-empty CSV | Exactly those models are active (catalog entries + synthesized entries for unknown names). |
| `enabled` + key | empty | The curated `default_active=True` set from the YAML catalog (e.g. gpt-5 + gpt-5-mini + text-embedding-3-large for OpenAI) so the provider is usable out of the box. |

**Bootstrapper safety net** — `source_validator.enforce_runtime_invariants()` flips `CLOUD_*_SOURCE=enabled` back to `disabled` when the matching API key is empty and prints a warning. This protects against the "looks ready in .env, errors at first request" failure mode.

- **Use case**: Mix-and-match local + cloud, or run cloud-only with `LLM_PROVIDER_SOURCE=none`
- **Pros**: One URL/key for every consumer; provider failover and spend logging handled by LiteLLM
- **Cons**: API costs and per-provider quota considerations
- **Requirements**: The provider's API key must be present in `.env`

### 4.2 COMFYUI_SOURCE

#### 4.2.1 `container-cpu` (Default)
```bash
COMFYUI_SOURCE=container-cpu
```
- **Use case**: Default image generation
- **Pros**: Works everywhere, automatic model download
- **Cons**: Slow generation, high memory usage
- **Requirements**: None

#### 4.2.2 `container-gpu`
```bash
COMFYUI_SOURCE=container-gpu
```
- **Use case**: Fast image generation
- **Pros**: GPU acceleration, fast generation
- **Cons**: Requires NVIDIA GPU
- **Requirements**: NVIDIA Container Toolkit

#### 4.2.3 `localhost`
```bash
COMFYUI_SOURCE=localhost
```
- **Use case**: Existing ComfyUI installation
- **Pros**: Custom workflows, existing setups
- **Cons**: Manual setup required
- **Requirements**: ComfyUI running locally on the port given by `COMFYUI_LOCALHOST_PORT` (default `8000`; override to e.g. `8188` if your installation uses another port). The URL is derived as `http://host.docker.internal:${COMFYUI_LOCALHOST_PORT}` at compose-render time.

Setup for localhost:
```bash
# Clone ComfyUI
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI

# Install dependencies
pip install -r requirements.txt

# Start ComfyUI on the stack default localhost port
python main.py --port 8000

# If your local ComfyUI uses the common native/default port 8188 instead, set:
# COMFYUI_LOCALHOST_PORT=8188
# (URL is derived as http://host.docker.internal:8188 at compose-render time.)
```

#### 4.2.4 `disabled`
```bash
COMFYUI_SOURCE=disabled
```
- **Use case**: No image generation needed
- **Pros**: Saves resources
- **Cons**: No image generation
- **Requirements**: None

### 4.3 WEAVIATE_SOURCE

#### 4.3.1 `container` (Default)
```bash
WEAVIATE_SOURCE=container
WEAVIATE_URL=http://weaviate:8080
```
- **Use case**: Standard vector database
- **Pros**: Easy setup, automatic configuration
- **Cons**: Container resource usage
- **Requirements**: None

The default stack also enables the optional CLIP vectorizer service. Text vectorization talks to LiteLLM via the `text2vec-openai` module — the OpenAI-compatible URL points at `LITELLM_BASE_URL` and `OPENAI_APIKEY` is set to `LITELLM_MASTER_KEY`. The default module list also keeps `text2vec-ollama` and `generative-ollama` enabled for back-compat with schemas created before the LiteLLM-fronted setup.

```bash
MULTI2VEC_CLIP_SOURCE=container-cpu
WEAVIATE_ENABLE_MODULES=text2vec-openai,text2vec-ollama,multi2vec-clip,generative-openai,generative-ollama
CLIP_INFERENCE_API=http://multi2vec-clip:8080
```

If `MULTI2VEC_CLIP_SOURCE=disabled`, remove `multi2vec-clip` from `WEAVIATE_ENABLE_MODULES` (leaving `text2vec-openai,text2vec-ollama,generative-openai,generative-ollama`) and set `CLIP_INFERENCE_API=` so Weaviate does not advertise a disabled inference endpoint.

#### 4.3.2 `localhost`
```bash
WEAVIATE_SOURCE=localhost
```
- **Use case**: Custom Weaviate setup
- **Pros**: Custom configuration, performance tuning
- **Cons**: Manual setup and maintenance
- **Requirements**: Weaviate running locally

#### 4.3.3 `disabled`
```bash
WEAVIATE_SOURCE=disabled
```
- **Use case**: No vector search needed
- **Pros**: Reduced resource usage
- **Cons**: No semantic search capabilities
- **Requirements**: None

### 4.4 MINIO_SOURCE

#### 4.4.1 `container` (Default)
```bash
MINIO_SOURCE=container
MINIO_ENDPOINT=http://minio:9000
MINIO_PUBLIC_ENDPOINT=http://localhost:63020
```
- **Use case**: S3-compatible artifact-tier object storage (ComfyUI outputs, Backend blobs, n8n files, JupyterHub datasets, Doc Processor output)
- **Pros**: Five pre-provisioned buckets with scoped service-account credentials; complements Supabase Storage; admin console at `http://localhost:63021` (S3 API on `:63020`)
- **Cons**: Container resource usage
- **Requirements**: None

Consumer code is not auto-wired in the current release — credentials and bucket names are in `.env` so each consumer integration can opt in via env-only changes in a follow-up PR.

#### 4.4.2 `disabled`
```bash
MINIO_SOURCE=disabled
```
- **Use case**: No artifact-tier object storage needed
- **Pros**: Saves resources; consumers fall back to Supabase Storage / local volumes
- **Cons**: No S3-compatible artifact surface available
- **Requirements**: None

### 4.5 OPENCLAW_SOURCE

#### 4.5.1 `container`
```bash
OPENCLAW_SOURCE=container
```
- **Use case**: Run OpenClaw agent in Docker
- **Pros**: Easy setup, isolated environment
- **Cons**: Container resource usage
- **Requirements**: None

#### 4.5.2 `localhost`
```bash
OPENCLAW_SOURCE=localhost
```
- **Use case**: Use existing OpenClaw installation
- **Pros**: Native performance, persistent config
- **Cons**: Manual setup required
- **Requirements**: Node.js 22+, `npm install -g openclaw`, running `openclaw gateway`

Setup for localhost:
```bash
# Install OpenClaw
npm install -g openclaw

# Run onboarding
openclaw onboard

# Start the gateway on the stack default localhost port
openclaw gateway --port 63065

# If your local OpenClaw uses its native/default port 18789 instead, set:
# OPENCLAW_LOCALHOST_PORT=18789
# (URL is derived as http://host.docker.internal:18789 at compose-render time.)
```

#### 4.5.3 `disabled` (Default)
```bash
OPENCLAW_SOURCE=disabled
```
- **Use case**: No AI agent needed
- **Pros**: Saves resources
- **Cons**: No messaging integration
- **Requirements**: None

### 4.6 HERMES_SOURCE

The programmable AI agent runtime by Nous Research. Hermes reasons over the LiteLLM gateway and exposes an OpenAI-compatible API; `litellm-init` auto-registers `hermes-agent` as a model in the gateway when `HERMES_SOURCE != disabled`, so Open WebUI / n8n / backend / jupyterhub / openclaw all see Hermes for free.

See [Hermes Agent](../../services/hermes/README.md) for the full service doc.

#### 4.6.1 `container` (Default)
```bash
HERMES_SOURCE=container
```
- **Use case**: Run Hermes as a stack service consumed by Open WebUI, n8n, OpenClaw, etc.
- **Pros**: Easy setup, isolated environment, available to every consumer without per-service wiring
- **Cons**: ~2–4 GB RAM, ~5.66 GB image on disk, no GPU required
- **Requirements**: `HERMES_DEFAULT_MODEL` must reference a model with ≥64K context window (stock Ollama context defaults are VRAM-dependent (4k/32k/256k) and usually below 64K — set `OLLAMA_CONTEXT_LENGTH=65536` on the Ollama server, or `/set parameter num_ctx 65536` + `/save <model>` inside `ollama run`; or use a cloud model)

#### 4.6.2 `localhost`
```bash
HERMES_SOURCE=localhost
```
- **Use case**: Hermes operates your real dev machine — read/write your real files, drive your real browser, use a real microphone for voice mode
- **Pros**: Native shell/browser/audio access; bigger context budget; React/Ink TUI as a daily-driver
- **Cons**: Manual install per host; consumers still reach it via the same `HERMES_ENDPOINT` (auto-set to `http://host.docker.internal:<port>`)
- **Requirements**: Host-installed Hermes (`curl -fsSL https://hermes-agent.nousresearch.com/install.sh | sh`), then `hermes gateway run`

Setup for localhost:
```bash
# Install Hermes on the host
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | sh

# Start the gateway on the stack default localhost port
hermes gateway run

# If your local Hermes uses a different port, set:
# HERMES_LOCALHOST_PORT=<your-port>
# (URL is derived as http://host.docker.internal:<your-port> at compose-render time.)
```

#### 4.6.3 `disabled`
```bash
HERMES_SOURCE=disabled
```
- **Use case**: No agent runtime needed; consumers see only direct LLM models in the LiteLLM dropdown
- **Pros**: Saves ~5.66 GB image disk and 2–4 GB RAM
- **Cons**: No agent loop, skills, voice, or programmable behaviour
- **Requirements**: None — `litellm-init` automatically omits the `hermes-agent` row from the model_list when disabled

### 4.7 LIGHTRAG_SOURCE

LightRAG runs out-of-process as either an in-stack container or a host-installed process.

- **`container`** — Pulls `ghcr.io/hkuds/lightrag:v1.5.4` and runs it on `backend-network`. Storage backends are adapted from existing services (Supabase pgvector, Neo4j, Redis); when any of those is `disabled`, LightRAG falls back to in-process file backends.
- **`localhost`** — Expects an existing LightRAG running on the host at `LIGHTRAG_LOCALHOST_PORT` (default 63068). Backend-network consumers reach it via `host.docker.internal`.
- **`disabled`** — `LIGHTRAG_ENDPOINT` empties; hermes/n8n/backend skip the LightRAG capability; LiteLLM's `model_list` omits the `lightrag` entry.

Role-specific LLM overrides are optional and preserve the single-model fallback when left empty:

```bash
LIGHTRAG_LLM_MODEL=qwen3.6:latest
LIGHTRAG_EXTRACT_LLM_MODEL=mistral-small3.2:24b
LIGHTRAG_KEYWORD_LLM_MODEL=mistral-small3.2:24b
LIGHTRAG_QUERY_LLM_MODEL=qwen3.6:latest
LIGHTRAG_QUERY_ENABLE_RERANK=false
LIGHTRAG_QUERY_TOP_K=10
LIGHTRAG_QUERY_CHUNK_TOP_K=5
LIGHTRAG_QUERY_MAX_TOTAL_TOKENS=12000
```

Use `EXTRACT` and `KEYWORD` for high-volume structured extraction work and `QUERY` for final answer generation. For local Ollama deployments, a cheaper non-reasoning extraction model usually keeps indexing responsive while allowing query answering to use the project-selected stronger model. Empty role-specific values inherit the base `LLM_MODEL`, so existing deployments do not need to set these variables.

The `LIGHTRAG_QUERY_*` knobs map to LightRAG's native query defaults. Numeric query defaults stay concrete because LightRAG parses these env vars as integers and does not accept empty strings. `LIGHTRAG_QUERY_ENABLE_RERANK` defaults to `false` because LightRAG's built-in Jina/Cohere rerank clients send `{query, documents}`, while TEI's `/rerank` route expects `{query, texts}`. Keep it off unless routing LightRAG through a compatible adapter or custom rerank binding.

### 4.8 RAY_SOURCE

Ray is the stack's distributed-compute substrate (head + worker containers, `infra` category). Consumers reach it via `RAY_ADDRESS` set per source by the bootstrapper's `_generate_ray_config()` hook. See [Ray service README](../../services/ray/README.md) for the full configuration reference.

#### 4.8.1 `disabled` (Default)
```bash
RAY_SOURCE=disabled
```
- **Use case**: No distributed compute needed; Backend's `/api/ray/*` returns 503 and JupyterHub notebooks calling `ray.init()` error cleanly
- **Pros**: Zero footprint
- **Cons**: No parallel job submission
- **Requirements**: None

#### 4.8.2 `ray-container-cpu`
```bash
RAY_SOURCE=ray-container-cpu
RAY_WORKER_COUNT=2   # number of ray-worker replicas; 0 = head-only
```
- **Use case**: Default container deployment; suitable for dev machines without GPU passthrough
- **Pros**: Head + N workers, dashboard at `ray.localhost`, REST job-submission API, client server reachable from host Python via `ray://localhost:${RAY_CLIENT_PORT}`
- **Cons**: CPU-only — slow for heavy ML workloads. `shm_size: 4gb` required (compose handles this; rootless Docker may not honor it)
- **Requirements**: ~2-3 GB image disk + ~1 GB RAM per worker

#### 4.8.3 `ray-container-gpu`
```bash
RAY_SOURCE=ray-container-gpu
RAY_WORKER_COUNT=2
```
- **Use case**: GPU-accelerated parallel work (multi-host Linux primarily — Mac Docker has no GPU passthrough)
- **Pros**: NVIDIA-runtime workers, same API surface as CPU mode
- **Cons**: Requires NVIDIA Container Toolkit on host. Image is ~5.9 GB
- **Requirements**: NVIDIA GPU + Container Toolkit installed on host

### 4.9 PROMETHEUS_SOURCE

Prometheus is the stack's metrics scraper + TSDB, bundled with `node-exporter` (host metrics) and `cAdvisor` (container metrics) as one co-lifecycled family. The bootstrapper's `_generate_prometheus_config()` hook also scales the `postgres-exporter` (in `services/supabase/`) and `redis-exporter` (in `services/redis/`) sidecars from this same source. See [Prometheus service README](../../services/prometheus/README.md) for scrape targets and configuration details.

#### 4.9.1 `disabled` (Default)
```bash
PROMETHEUS_SOURCE=disabled
```
- **Use case**: Cold-start fast, no observability overhead
- **Pros**: Zero footprint
- **Cons**: No metrics — Grafana shows "datasource unreachable" if also `container`
- **Requirements**: None

#### 4.9.2 `container`
```bash
PROMETHEUS_SOURCE=container
PROMETHEUS_RETENTION_DAYS=7   # 1..365 — wizard prompts inline on the source step
```
- **Use case**: Stack-wide observability — scrapes Kong, LiteLLM, Weaviate, n8n (web + worker), MinIO, Backend, plus the postgres/redis sidecars and cAdvisor/node-exporter. JupyterHub + Hermes scrape jobs were retired (the JupyterHub image is single-user `jupyter/datascience-notebook` with no `/metrics`; the third-party Hermes image likewise has no `/metrics` endpoint)
- **Pros**: 13 pre-configured scrape jobs, recording-rules folder ready to extend, Kong-aliased UI at `prometheus.localhost`
- **Cons**: cAdvisor polls every container every 5s and node-exporter polls `/proc` continuously — non-trivial overhead on a laptop
- **Requirements**: ~500 MB image disk + retention-day-dependent disk for the TSDB volume

### 4.10 GRAFANA_SOURCE

Grafana is the user-facing dashboards + unified alerting UI on top of Prometheus. The Prometheus datasource is pre-provisioned (URL interpolated from `${PROMETHEUS_ENDPOINT}` at boot) plus 7 starter dashboards (stack overview, LiteLLM, Kong, Postgres+Redis, containers+host, n8n, app-tier). See [Grafana service README](../../services/grafana/README.md) for the dashboard catalog and admin-password lifecycle.

#### 4.10.1 `disabled` (Default)
```bash
GRAFANA_SOURCE=disabled
```
- **Use case**: Cold-start fast; no UI overhead. Useful even when Prometheus is `container` if you only want raw metrics via Prom's own UI
- **Pros**: Zero footprint
- **Cons**: No dashboards
- **Requirements**: None

#### 4.10.2 `container`
```bash
GRAFANA_SOURCE=container
GRAFANA_ADMIN_USERNAME=admin    # override only if you want a different login
GRAFANA_ADMIN_PASSWORD=...       # auto-generated on first bootstrap; persisted to .env
```
- **Use case**: User-facing observability — 7 dashboards in the "Atlas" folder, unified alerting enabled (no rules pre-provisioned), Kong-aliased UI at `grafana.localhost`
- **Pros**: Admin login + datasource provisioning happen automatically; sign-up disabled; anonymous-read off by default
- **Cons**: When `PROMETHEUS_SOURCE=disabled`, every panel shows "datasource unreachable" — pair with `--prometheus-source container` for a working setup
- **Requirements**: ~300 MB image disk + small named volume for SQLite

### 4.11 SPARK_SOURCE

Spark is a standalone Apache Spark cluster (master + N workers + history server + dedicated `spark-connect` gRPC sidecar + one-shot `spark-init`) sitting in the `data` band. It exposes a Spark Connect endpoint on `:15002` via the sidecar for in-stack thin clients. JupyterHub receives `SPARK_REMOTE=sc://spark-connect:15002` for PySpark Connect notebooks, while Zeppelin is seeded for the stock standalone Spark interpreter path (`spark.master=spark://spark-master:7077`) because Zeppelin's launcher uses `spark-submit`. Backend wiring remains a future service-level integration. The local Spark image also bakes `iceberg-spark-runtime-4.1_2.13:1.11.0` plus `iceberg-aws-bundle:1.11.0` and preconfigures a `lakehouse` Iceberg REST catalog at `http://iceberg-rest:8181`, including MinIO S3FileIO endpoint, scoped Iceberg service-account credentials, path-style access, and `client.region=us-east-1`; this catalog is active when `ICEBERG_REST_SOURCE=container` and inert for ML-only Spark users who leave Iceberg REST disabled. JupyterHub also carries `boto3`, `s3fs`, `pyiceberg[s3fs]`, `pyarrow`, and `duckdb` with MinIO and Iceberg REST env so Python notebooks can list buckets, load the REST catalog, and query Arrow data locally. See [Spark service README](../../services/spark/README.md), [JupyterHub service README](../../services/jupyterhub/README.md), and [Zeppelin service README](../../services/zeppelin/README.md) for the client paths.

#### 4.11.1 `disabled` (Default)
```bash
SPARK_SOURCE=disabled
```
- **Use case**: No Spark workloads; saves ~3 GB image disk + per-worker RAM
- **Pros**: Zero footprint; Zeppelin is also gated off (Zeppelin without Spark errors out at start)
- **Cons**: No batch / SQL / DataFrame compute; LLM operators in Airflow that import `pyspark` will fail
- **Requirements**: None

#### 4.11.2 `container`
```bash
SPARK_SOURCE=container
SPARK_WORKER_COUNT=2     # number of spark-worker replicas; 1..8 — wizard prompts inline
```
- **Use case**: Local Spark cluster for batch / SQL / DataFrame jobs and Spark Connect clients
- **Pros**: Master + N workers + history server, Kong-aliased UIs at `spark.localhost` + `spark-history.localhost`, Spark Connect on `:15002`, default `lakehouse` Iceberg REST catalog when `iceberg-rest` is enabled.
- **Cons**: Each worker reserves CPU + RAM (defaults to 1 core / 1 GB); heavy on laptops above 2 workers
- **Containers**: `spark-master`, `spark-worker-1..N`, `spark-history`, `spark-connect` (gRPC Connect sidecar), `spark-init` (one-shot — creates the spark-history MinIO bucket)
- **Requirements**: ~3 GB image disk + ~1 GB RAM per worker

### 4.12 TEI_RERANKER_SOURCE

Cross-encoder reranker inference server (default model `mixedbread-ai/mxbai-rerank-base-v1`). Exposes TEI's `/rerank` endpoint for consumers that send TEI-compatible request bodies.

- **`container-cpu`** — `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`. Runs anywhere; ~150 ms per pair latency.
- **`container-gpu`** — `:1.9` image with NVIDIA reservation. ~15 ms per pair on RTX-class GPU.
- **`localhost`** — Existing TEI process on host at `TEI_RERANKER_LOCALHOST_PORT` (default 63031).
- **`disabled`** — `TEI_RERANKER_ENDPOINT` empties. LightRAG's `RERANK_BINDING` is emitted as `null` in all stock SOURCE combinations so LightRAG disables reranking instead of crashing on an empty binding; direct LightRAG-to-TEI reranking requires an adapter because the request bodies differ.

### 4.13 ZEPPELIN_SOURCE

Zeppelin is the Spark-first notebook UI. `zeppelin-init` pre-configures the stock Spark interpreter against the in-cluster standalone master (`spark.master=spark://spark-master:7077`) plus MinIO S3A and the Iceberg REST `lakehouse` catalog; Spark Connect remains the JupyterHub/direct-client path. The JDBC interpreter ships with Supabase Postgres credentials in env vars but requires a one-time UI-driven `postgres` profile setup (see [Zeppelin service README](../../services/zeppelin/README.md) §4). **Hard-gated on Spark** — `ZEPPELIN_SOURCE=container` with `SPARK_SOURCE=disabled` errors out at bootstrap.

#### 4.13.1 `disabled` (Default)
```bash
ZEPPELIN_SOURCE=disabled
```
- **Use case**: No notebook UI for Spark; saves ~1.5 GB image disk
- **Pros**: Zero footprint
- **Cons**: No Spark notebook authoring (Jupyter notebooks can still drive Spark Connect though)
- **Requirements**: None

#### 4.13.2 `container`
```bash
ZEPPELIN_SOURCE=container
SPARK_SOURCE=container   # REQUIRED — Zeppelin hard-fails without Spark
```
- **Use case**: Web-based notebook authoring against the in-cluster Spark master
- **Pros**: Pre-configured Spark interpreter (standalone master RPC + MinIO S3A + Iceberg REST catalog), Kong-aliased UI at `zeppelin.localhost`, persists notebooks to a named volume. JDBC interpreter ships with credentials in env but needs a one-time UI setup.
- **Cons**: Adds ~1.5 GB image disk + ~512 MB RAM
- **Containers**: `zeppelin`, `zeppelin-init` (one-shot — seeds and restarts the Spark interpreter when Atlas-owned settings drift)
- **Requirements**: `SPARK_SOURCE=container`

### 4.14 JENKINS_SOURCE

Jenkins is the optional Maven Spark app builder for the data-eng track. Atlas provides the Jenkins controller, JCasC configuration, Maven runtime, MinIO `mc` client, and generated admin login. Downstream projects provide repositories, Jenkinsfiles, seed jobs, and project credentials. **Hard-gated on MinIO** — `JENKINS_SOURCE=container` with `MINIO_SOURCE=disabled` errors out at bootstrap because publishing to the `jars` bucket is part of the service contract.

#### 4.14.1 `disabled` (Default)
```bash
JENKINS_SOURCE=disabled
```
- **Use case**: No in-stack CI builder; downstream projects can use external CI or GitHub Actions
- **Pros**: Zero footprint
- **Cons**: No local Maven build/publish UI for Spark app JARs
- **Requirements**: None

#### 4.14.2 `container`
```bash
JENKINS_SOURCE=container
MINIO_SOURCE=container     # REQUIRED — Jenkins publishes artifacts to MinIO
JENKINS_ADMIN_PASSWORD=... # auto-generated on first bootstrap; persisted to .env
```
- **Use case**: Local Jenkins controller for `mvn -q package` and `mc cp target/*.jar` to `s3a://jars/<app>/<version>/app.jar`
- **Pros**: JCasC-managed admin user, Kong-aliased UI at `jenkins.localhost`, Maven + MinIO client baked into the image, persistent Jenkins home
- **Cons**: Adds controller image/build time and a persistent volume; Atlas intentionally ships no downstream project jobs
- **Containers**: `jenkins`
- **Requirements**: `MINIO_SOURCE=container`

### 4.15 AIRFLOW_SOURCE

Airflow is a code-defined DAG orchestrator running LocalExecutor (no Celery / Redis broker — the metadata DB is Supabase Postgres). The image bundles `apache-airflow-providers-openai` (LiteLLM-wired) — LangChain support runs via `langchain-openai` + `PythonOperator`; there is no `apache-airflow-providers-langchain` package on PyPI. It also installs Java 17, exposes PySpark's `spark-submit`, and carries S3A/Iceberg jars so `SparkSubmitOperator` can submit a JAR from `s3a://jars/...` to `spark://spark-master:7077`. The documented lakehouse path uses `deploy_mode="cluster"` so the driver runs on Atlas Spark workers while Airflow acts as the submit client. `airflow-init` seeds Connection objects per sibling source: `postgres_supabase`, `litellm_default`, and `redis_default` (always-on — required deps and locked-source services), `spark_default` (gated on `SPARK_SOURCE=container`, seeded for cluster SparkSubmit), `minio_default` (gated on `MINIO_SOURCE=container`), `weaviate_default` (gated on `WEAVIATE_SOURCE=container`), `neo4j_default` (gated on `NEO4J_GRAPH_DB_SOURCE=container`). See [Airflow service README](../../services/airflow/README.md) §4 for the full seeded Connections matrix, the example DAG, and the `lakehouse_spark_submit_smoke` validation DAG.

#### 4.15.1 `disabled` (Default)
```bash
AIRFLOW_SOURCE=disabled
```
- **Use case**: No orchestrated workflows; saves ~2 GB image disk + Postgres metadata schema
- **Pros**: Zero footprint
- **Cons**: No scheduled DAGs; no Hermes → Airflow trigger pattern
- **Requirements**: None

#### 4.15.2 `container`
```bash
AIRFLOW_SOURCE=container
# Username is hardcoded `admin` — there is no AIRFLOW_ADMIN_USERNAME knob.
AIRFLOW_ADMIN_PASSWORD=...              # auto-generated on first bootstrap; persisted to .env
AIRFLOW_FERNET_KEY=...                  # auto-generated; encrypts Connections + Variables at rest
AIRFLOW_SECRET_KEY=...                  # auto-generated; AIRFLOW__API__SECRET_KEY signs inter-process payloads (DagFileProcessor→scheduler RPC, deferrable triggers, multi-scheduler JWTs) in Airflow 3.x
AIRFLOW_DB_USER=airflow                 # Postgres role on supabase-db
AIRFLOW_DB_PASSWORD=...                 # auto-generated
```
- **Use case**: Scheduled / triggered DAG runs (ETL, model fine-tunes, scheduled LLM evals) with first-class LiteLLM-wired LLM operators and SparkSubmit lakehouse jobs
- **Pros**: LocalExecutor (no broker), Supabase Postgres metadata DB, Kong-aliased UI at `airflow.localhost`, REST API under the same alias at `/api/v2/`, 7 Connections auto-seeded (`postgres_supabase` / `litellm_default` / `redis_default` always; `spark_default` / `minio_default` / `weaviate_default` / `neo4j_default` gated on the matching sibling being `container`-sourced), manual `lakehouse_spark_submit_smoke` DAG submits a validation JAR from `s3a://jars/` and records Spark History when Spark + MinIO + Iceberg REST are enabled
- **Cons**: ~2 GB image disk + ~1.5 GB RAM for the webserver + scheduler + dag-processor combo
- **Containers**: `airflow-init` (one-shot), `airflow-webserver`, `airflow-scheduler`, `airflow-dag-processor` (Airflow 3.x REQUIRES a standalone DAG processor — the scheduler no longer parses DAGs in-process)
- **Requirements**: Supabase Postgres reachable (always-on)

### 4.16 MCP_SERVERS_SOURCE

Curated MCP Servers expose Atlas' first Model Context Protocol tool surface. The first slice is intentionally narrow: read-only Postgres queries, Neo4j schema/read Cypher, and SearXNG web search over Streamable HTTP at `/mcp`. Open WebUI and Hermes should consume it directly where possible; LiteLLM MCP Gateway remains an explicit opt-in path for model-facing tools under LiteLLM policy.

#### 4.16.1 `disabled` (Default)
```bash
MCP_SERVERS_SOURCE=disabled
```
- **Use case**: Default safe startup; no shared tool surface is exposed.
- **Pros**: No extra credential or prompt-injection surface.
- **Cons**: MCP-native clients do not get Atlas database/search tools.
- **Requirements**: None.

#### 4.16.2 `container`
```bash
MCP_SERVERS_SOURCE=container
NEO4J_GRAPH_DB_SOURCE=container   # REQUIRED
SEARXNG_SOURCE=container          # REQUIRED
```
- **Use case**: Give MCP-native clients a small, reviewed Atlas tool package.
- **Pros**: One curated endpoint for Postgres, Neo4j, and SearXNG; no one-server-per-service sprawl; Kong alias `mcp.localhost`.
- **Cons**: Tool output is untrusted and may include sensitive local data; clients need explicit operator consent and credentials.
- **Requirements**: `NEO4J_GRAPH_DB_SOURCE=container` and `SEARXNG_SOURCE=container`.

### 4.17 CRAWL4AI_SOURCE

Crawl4AI is Atlas' optional browser-backed extraction API. When enabled, Atlas runs the upstream Docker server on port 11235, publishes `crawl4ai.localhost`, generates `CRAWL4AI_API_TOKEN`, and exposes `CRAWL4AI_ENDPOINT=http://crawl4ai:11235` to Local Deep Researcher and n8n.

#### 4.17.1 `disabled` (Default)
```bash
CRAWL4AI_SOURCE=disabled
LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE=disabled
```
- **Use case**: Default safe startup with no browser crawler.
- **Pros**: Zero footprint; no browser sandbox, shared memory, or crawling surface.
- **Cons**: Local Deep Researcher uses snippets unless `LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE=builtin` is selected.
- **Requirements**: None.

#### 4.17.2 `container`
```bash
CRAWL4AI_SOURCE=container
LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE=crawl4ai  # optional consumer mode
CRAWL4AI_API_TOKEN=...                         # auto-generated on first bootstrap
```
- **Use case**: Render JavaScript-heavy pages and return markdown for research or ingestion workflows.
- **Pros**: Kong-aliased UI/API at `crawl4ai.localhost`, bearer-token protected API, n8n HTTP Request compatibility, Local Deep Researcher full-page adapter.
- **Cons**: Adds a Playwright/Chromium-based container; crawling arbitrary internal URLs remains disabled unless `CRAWL4AI_ALLOW_INTERNAL_URLS=true` is deliberately set.
- **Containers**: `crawl4ai`.
- **Requirements**: None for the service itself. `LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE=crawl4ai` requires `CRAWL4AI_SOURCE=container` and fails early otherwise.

### 4.18 TIKA_SOURCE

Apache Tika is Atlas' optional fallback text extractor for long-tail document formats. When enabled, Atlas exposes `TIKA_ENDPOINT` to the Backend and n8n. The Backend keeps Docling first for supported/unknown formats and uses Tika only for explicit unsupported-format responses or known long-tail formats such as EML, MSG, RTF, ODT, ODS, ODP, ZIP, TAR, GZIP, and BZIP2.

#### 4.18.1 `disabled` (Default)
```bash
TIKA_SOURCE=disabled
```
- **Use case**: Default safe startup with no JVM parser for untrusted documents.
- **Pros**: Zero footprint; no additional document parsing attack surface.
- **Cons**: Docling unsupported-format failures do not have an in-stack plain-text fallback.
- **Requirements**: None.

#### 4.18.2 `container`
```bash
TIKA_SOURCE=container
TIKA_ENDPOINT=http://tika:9998   # auto-managed
```
- **Use case**: Add a local fallback extractor for email, RTF/OpenDocument, archives, and obscure MIME types.
- **Pros**: Kong alias `tika.localhost`, direct host port via `TIKA_PORT`, backend/n8n env wiring, bounded backend size and timeout controls.
- **Cons**: Plain-text-only degraded extraction; no malware scanning or archive quarantine in v1.
- **Containers**: `tika`.
- **Requirements**: None.

#### 4.18.3 `tika-localhost`
```bash
TIKA_SOURCE=tika-localhost
TIKA_LOCALHOST_PORT=9998
```
- **Use case**: Reuse an existing host-running Tika server.
- **Pros**: No Tika container footprint; Kong still routes `tika.localhost` through `host.docker.internal`.
- **Cons**: Operator must keep the host Tika process patched and running.
- **Requirements**: Host Tika server listening on `TIKA_LOCALHOST_PORT`.

### 4.19 LANGFUSE_SOURCE

Langfuse is Atlas' optional LLM observability surface. When enabled, Atlas runs Langfuse web, worker, and ClickHouse containers; provisions a dedicated Supabase Postgres database plus Langfuse object-store credentials; and wires LiteLLM with Langfuse tracing keys so OpenAI-compatible requests through LiteLLM produce traces, latency, and cost records. It appears in the AI and ML tracks (`gen-ai-rag`, `gen-ai-eng`, `gen-ai-creative`, `ml-eng`, `all`) and stays out of the data-engineering track unless a future data-quality/eval workflow needs it directly.

#### 4.19.1 `disabled` (Default)
```bash
LANGFUSE_SOURCE=disabled
```
- **Use case**: Default safe startup with no tracing datastore or extra UI.
- **Pros**: Zero footprint; no persisted LLM trace records.
- **Cons**: LiteLLM requests are not captured in Langfuse.
- **Requirements**: None.

#### 4.19.2 `container`
```bash
LANGFUSE_SOURCE=container
MINIO_SOURCE=container       # REQUIRED — Langfuse uses S3-compatible blob storage
LANGFUSE_PUBLIC_KEY=...      # auto-generated on first bootstrap
LANGFUSE_SECRET_KEY=...      # auto-generated on first bootstrap
```
- **Use case**: Inspect LLM traces, prompt experiments, evals, latency, and spend for LiteLLM-routed calls from Open WebUI, Backend, Hermes, Airflow, notebooks, and other Atlas consumers.
- **Pros**: Kong-aliased UI/API at `langfuse.localhost`, generated first-run credentials, dedicated ClickHouse analytics store, dedicated Supabase Postgres database, dedicated MinIO bucket and service account, automatic LiteLLM `success_callback` tracing.
- **Cons**: Adds a stateful ClickHouse volume plus web/worker containers; only LiteLLM-routed calls are traced in the first slice.
- **Containers**: `langfuse-init` (one-shot), `langfuse-web`, `langfuse-worker`, `langfuse-clickhouse`.
- **Requirements**: Supabase Postgres and Redis are always-on; `MINIO_SOURCE=container` is required.

### 4.20 MLFLOW_SOURCE

MLflow is Atlas' optional experiment tracking and artifact registry surface for the ML Engineering track. When enabled, Atlas runs a tracking server backed by a dedicated Supabase Postgres database and a scoped MinIO artifact bucket. JupyterHub receives `MLFLOW_TRACKING_URI=http://mlflow:5000` so notebooks can log runs, metrics, parameters, and artifacts without direct MinIO credentials.

#### 4.20.1 `disabled` (Default)
```bash
MLFLOW_SOURCE=disabled
```
- **Use case**: Default safe startup with no experiment tracking UI/API.
- **Pros**: Zero footprint; no persisted ML run history.
- **Cons**: Notebook experiments remain local to the notebook session unless users configure an external tracker.
- **Requirements**: None.

#### 4.20.2 `container`
```bash
MLFLOW_SOURCE=container
MINIO_SOURCE=container       # REQUIRED — MLflow stores run artifacts in MinIO
MLFLOW_TRACKING_URI=...      # auto-managed as http://mlflow:5000
```
- **Use case**: Durable experiment tracking for JupyterHub notebooks and future backend/n8n workflows.
- **Pros**: Kong-aliased UI/API at `mlflow.localhost`, Postgres-backed run metadata, MinIO-backed artifact persistence, generated DB and MinIO credentials, notebook-friendly tracking URI.
- **Cons**: Adds an app container plus one-shot DB init; model promotion automations and serving are out of scope for the first slice.
- **Containers**: `mlflow-init` (one-shot), `mlflow`.
- **Requirements**: Supabase Postgres is always-on; `MINIO_SOURCE=container` is required.

### 4.21 LABEL_STUDIO_SOURCE

Label Studio is Atlas' optional dataset review and annotation surface for the ML Engineering track. When enabled, Atlas runs Label Studio CE with a dedicated Supabase Postgres database and a scoped MinIO bucket for S3-compatible media/upload storage. JupyterHub receives `LABEL_STUDIO_URL`, `LABEL_STUDIO_API_URL`, and `LABEL_STUDIO_API_KEY` so notebooks can create projects, push tasks, export annotations, and then hand reviewed outputs to MLflow or Weaviate.

#### 4.21.1 `disabled` (Default)
```bash
LABEL_STUDIO_SOURCE=disabled
```
- **Use case**: Default safe startup with no annotation UI/API.
- **Pros**: Zero footprint; no separate Label Studio auth surface or review data.
- **Cons**: Dataset review remains a notebook/manual workflow.
- **Requirements**: None.

#### 4.21.2 `container`
```bash
LABEL_STUDIO_SOURCE=container
MINIO_SOURCE=container       # REQUIRED — Label Studio stores media/uploads in MinIO
LABEL_STUDIO_API_URL=...     # auto-managed as http://label-studio:8080
```
- **Use case**: Human review and annotation loops for ML, RAG, and creative datasets.
- **Pros**: Kong-aliased UI/API at `label-studio.localhost`, Postgres-backed app metadata, MinIO-backed media storage, generated admin/API credentials, notebook-friendly SDK path.
- **Cons**: Adds an app container plus one-shot DB init; Label Studio CE has its own auth model, so broad multi-user usage should wait for SSO/permissions work.
- **Containers**: `label-studio-init` (one-shot), `label-studio`.
- **Requirements**: Supabase Postgres is always-on; `MINIO_SOURCE=container` is required.

### 4.22 VERBA_SOURCE

Verba is Atlas' optional Weaviate RAG demo UI for the RAG track. It is useful as a visible sample ingest/query path over Atlas Weaviate and LiteLLM, but upstream Verba is archived and discontinued, so Atlas keeps it disabled by default and documents it as a reference UI rather than a maintained strategic runtime.

#### 4.22.1 `disabled` (Default)
```bash
VERBA_SOURCE=disabled
```
- **Use case**: Default safe startup with no archived RAG UI.
- **Pros**: Zero footprint; no extra single-user UI or Verba-managed Weaviate classes.
- **Cons**: Users must rely on Open WebUI, LightRAG, notebooks, or other RAG surfaces for interactive demos.
- **Requirements**: None.

#### 4.22.2 `container`
```bash
VERBA_SOURCE=container
WEAVIATE_SOURCE=container    # REQUIRED — localhost Weaviate is also supported
VERBA_ENDPOINT=...           # auto-managed as http://verba:8000
```
- **Use case**: A browser-based sample ingest/query path that exercises Weaviate and LiteLLM with Verba-managed classes such as `VERBA_Document`.
- **Pros**: Kong-aliased UI at `verba.localhost`, isolated Verba-owned Weaviate classes, LiteLLM OpenAI-compatible generator/embedding wiring, and an explicit sample workflow for RAG demos.
- **Cons**: Upstream is archived/discontinued, single-user, and latest-only on Docker Hub; Atlas pins the observed image digest and does not treat Verba as a secure multi-user product surface.
- **Containers**: `verba`.
- **Requirements**: LiteLLM is always-on; `WEAVIATE_SOURCE` must be `container` or `localhost`. Docling is optional and documented as a manual pre-processing path, not a hard dependency.

## 5. Configuration Patterns

### 5.1 Development Setup
Best for local development with minimal resources:

```bash
./start.sh --llm-provider-source ollama-localhost \
          --comfyui-source localhost \
          --weaviate-source container \
          --n8n-source disabled \
          --searxng-source disabled
```

Benefits:
- Lower memory usage
- Faster AI inference
- Reduced container count
- Easy debugging

### 5.2 Production Setup
Best for production with full features:

```bash
./start.sh --llm-provider-source ollama-container-gpu \
          --comfyui-source container-gpu \
          --weaviate-source container \
          --n8n-source container \
          --searxng-source container
```

Benefits:
- GPU acceleration
- All features enabled
- Consistent environment
- Scalable architecture

### 5.3 Minimal Setup
Best for testing or resource-constrained environments:

```bash
./start.sh --llm-provider-source none \
          --cloud-openai-source enabled \
          --comfyui-source disabled \
          --weaviate-source disabled \
          --n8n-source disabled \
          --searxng-source disabled
```

Benefits:
- Minimal resource usage (no local Ollama)
- Cloud-powered AI through LiteLLM
- Fast startup
- Basic chat functionality

Make sure `OPENAI_API_KEY` (or whichever cloud key matches your enabled `CLOUD_*_SOURCE`) is set in `.env`.

### 5.4 Mixed Setup
Combine different approaches for optimal performance:

```bash
./start.sh --llm-provider-source ollama-localhost \  # Local for speed
          --comfyui-source container-gpu \           # Container for GPU
          --weaviate-source container \              # Container for ease
          --n8n-source container \                   # Full workflow features
          --searxng-source disabled                  # Skip if not needed
```

## 6. Environment File vs CLI Overrides

### 6.1 Using .env File
Persistent configuration for regular use:

```bash
# Edit .env file
BASE_PORT=63000
LLM_PROVIDER_SOURCE=ollama-localhost
COMFYUI_SOURCE=container-gpu
N8N_SOURCE=container

# Start with file configuration
./start.sh
```

`BASE_PORT` is the preferred way to move the whole stack to another port range. Individual `*_PORT` variables are advanced overrides; normal users should change `BASE_PORT` manually or run `./start.sh --base-port <port>`.

### 6.2 Using CLI Overrides
Temporary configuration for testing:

```bash
# Override without changing .env
./start.sh --llm-provider-source ollama-localhost --comfyui-source disabled

# Next run uses .env settings again
./start.sh
```

## 7. Service Dependencies

Understanding which services depend on others:

### 7.1 Core Dependencies
- **Open WebUI / Backend / n8n / JupyterHub / Local Deep Researcher / OpenClaw** → All read `LITELLM_BASE_URL` + `LITELLM_API_KEY` for LLM access. LiteLLM is always-on; the actual upstream is whatever `LLM_PROVIDER_SOURCE` and the `CLOUD_*_SOURCE` toggles select.
- **Backend API** → Depends on database services (PostgreSQL, Redis)
- **n8n workflows** → Often use Weaviate for vector operations

### 7.2 Optional Dependencies
- **ComfyUI** → Independent, can be disabled without affecting other services
- **SearxNG** → Independent privacy search
- **Weaviate** → Optional unless needed for semantic search

## 8. Performance Considerations

### 8.1 Memory Usage by Configuration

**High Memory** (12GB+ recommended):
- All services containerized
- GPU services enabled
- Large models loaded

**Medium Memory** (8GB recommended):
- Mix of localhost and container
- Some services disabled
- Smaller models

**Low Memory** (4GB minimum):
- API-based LLM
- Most services disabled
- Minimal container footprint

### 8.2 CPU Usage

**CPU Intensive**:
- Container-based AI services
- Multiple simultaneous AI tasks
- All services enabled

**CPU Efficient**:
- Localhost AI services
- GPU-accelerated containers
- Selective service enabling

## 9. Troubleshooting SOURCE Configurations

### 9.1 Common Issues

**Service won't start with localhost SOURCE**:
```bash
# Check if service is running locally
curl http://localhost:11434/api/tags  # Ollama (LiteLLM upstream when LLM_PROVIDER_SOURCE=ollama-localhost)
curl http://localhost:63040/health/liveliness  # LiteLLM gateway (always-on)
curl http://localhost:8000/           # ComfyUI default localhost URL
curl http://localhost:8188/           # ComfyUI if you overrode COMFYUI_LOCALHOST_PORT to 8188

# Check service logs
docker logs ${PROJECT_NAME}-backend -f
```

**Port conflicts**:
```bash
# Use different base port
./start.sh --base-port 64000

# Check port usage (Open WebUI default; substitute your conflicting port)
lsof -i :63096
```

**Kong routing not working**:
```bash
# Kong config is dynamically generated at every startup — to debug routes,
# inspect the generator + the KONG_* env vars it consumes:
cat bootstrapper/utils/kong_config_generator.py
env | grep ^KONG_

# Verify hosts file
./start.sh --setup-hosts
```

### 9.2 Debug Commands

```bash
# Check active SOURCE values
env | grep -E "(OLLAMA|COMFYUI|N8N|WEAVIATE)_SOURCE"

# Test service connectivity (LLM goes via LiteLLM, not Ollama directly)
docker exec ${PROJECT_NAME}-backend curl http://${PROJECT_NAME}-litellm:4000/health/liveliness
docker exec ${PROJECT_NAME}-litellm curl http://${PROJECT_NAME}-ollama:11434/api/tags
docker exec ${PROJECT_NAME}-kong-api-gateway curl http://${PROJECT_NAME}-comfyui:18188/

# Monitor resource usage
docker stats
```

## 10. Deployment profile (`--profile prod`)

Beyond the per-service `*_SOURCE` variables above, a small set of global variables is managed by the **deployment profile**. `./start.sh --profile prod` (or the wizard's profile step) writes them; `--profile default` clears the prod-managed bind IP. They are not `*_SOURCE` toggles, so they don't appear in the §2 matrix.

| Variable | Default | Set by `--profile prod` | Meaning |
|----------|---------|-------------------------|---------|
| `HOST_BIND_IP` | _(empty)_ | `127.0.0.1:` | Host-interface prefix on every published port. Empty → `0.0.0.0` (dev); `127.0.0.1:` → ports reachable only from the host, with the public edge (Cloudflare Tunnel / reverse proxy) fronting Kong. |
| `LOG_MAX_SIZE` | `10m` | `10m` | Per-container json-file log max size (Docker logging option). |
| `LOG_MAX_FILE` | `3` | `3` | Per-container json-file log file count. |

Under `--profile prod`, `PROMETHEUS_SOURCE` and `GRAFANA_SOURCE` are also defaulted to `container` (observability on) unless you set them explicitly. Per-service resource limits (`*_MEMORY_LIMIT` / `*_CPU_LIMIT`) are always-on `.env` defaults, independent of the profile. See [reusing-atlas.md](reusing-atlas.md) and the `--profile prod` line in the README for the full behavior.

For more troubleshooting help, see [../quick-start/troubleshooting.md](../quick-start/troubleshooting.md).
