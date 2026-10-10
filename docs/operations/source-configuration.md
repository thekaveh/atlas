# 6.2. SOURCE Configuration Guide

Each Atlas service has a `*_SOURCE` variable in `.env` that selects how it is deployed. This guide explains how to set these variables and lists the options for each service.

## 1. Interactive Setup Wizard

The easiest way to configure SOURCE variables is the **interactive setup wizard**. Run `./start.sh` with no arguments to launch it. The wizard walks you through each service, shows the available options with hints, and validates dependencies as you go. See the [Interactive Setup Wizard Guide](../quick-start/interactive-setup-wizard.md) for details.

## 2. Understanding SOURCE Variables

SOURCE variables control how each service is deployed: in a Docker container, through a localhost installation, or not at all.

### 2.1. CLI source flags persist to `.env` (consumer-wrapper trap)

Every `--<service>-source` flag (`--comfyui-source`, `--llm-provider-source`, `--ray-source`, …) is written to `.env` on every start. It is not a one-run override. A wrapper that always passes a defaulted flag, for example `--comfyui-source "${COMFYUI_SOURCE:-container-cpu}"`, resets a hand-set `.env` value to the default on every restart.

When a flag changes a non-empty `.env` value, the bootstrapper prints a warning. The warning appears in the TUI log pane and in `--no-tui` output:

```
⚠ COMFYUI_SOURCE: managed-localhost-mps → container-cpu (overridden by --comfyui-source or the selected track; persisted to .env)
```

`--track` prints the same line when it disables an off-track service that `.env` had enabled. The line names the flag for that variable, because the track writes through the same path. There is no warning when the flag equals the `.env` value or the variable was empty.

**For wrappers:** pass a source flag only to change the persisted value. Otherwise omit it and let `.env` decide.

## 3. SOURCE Values Reference

A SOURCE value is the `*_SOURCE` setting that picks how a service is deployed. The common values are `container` (Docker), a `localhost` or `managed-localhost` variant (host process) and `disabled` (excluded from Compose). Some services add named variants, such as GPU and CPU flavors or provider choices. [SOURCE Values](../reference/source-values.md) lists every `*_SOURCE` variable with its default and valid options. [Environment Variables](../reference/env-vars.md) lists all other variables.

> The `litellm-init` container is mandatory and has no SOURCE toggle. It runs on every start. It provisions the dedicated `litellm` Postgres database and renders `volumes/litellm/config.yaml` through `model_resolver`. The inputs are the YAML model catalogs (`services/ollama/models.yaml`, `services/litellm/models.yaml`) and the wizard's `*_USER_MODELS` variables. No separate catalog-init container takes part in model selection.

### 3.1. Services Supporting Localhost

These services can run on your host machine instead of in containers:

| Service | SOURCE Variable | Localhost Option | Benefits |
|---------|----------------|------------------|----------|
| **Ollama** (LiteLLM upstream) | `LLM_PROVIDER_SOURCE` | `ollama-localhost` | Faster, uses existing models, less memory. LiteLLM still fronts the upstream. |
| **ComfyUI** | `COMFYUI_SOURCE` | `localhost`, `managed-localhost-mps` | Direct access, custom setups, faster; `managed-localhost-mps` lets Atlas install and run a Metal/MPS ComfyUI on Apple Silicon |
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
| **Blender MCP** | `BLENDER_MCP_SOURCE` | `localhost`, `managed-localhost` | Use a host-installed Blender MCP add-on and server, with no Kong route. `managed-localhost` runs an Atlas-provisioned headless Blender and MCP bridge; see the [Blender MCP README](../../services/blender-mcp/README.md). |
| **vLLM Metal** | `VLLM_METAL_SOURCE` | `managed-localhost` | Run a managed Apple-silicon OpenAI-compatible model server as an optional LiteLLM upstream; it has no Kong route. |

Every option in this table is dev-only: `--profile prod` does not offer it (§9).

### 3.2. Container-Only or Stack-Managed Services

Leave container-only and stack-managed services at their defaults unless you are reducing the stack or debugging one component. The always-on tier (`BACKEND_SOURCE`, `KONG_API_GATEWAY_SOURCE`, `LITELLM_SOURCE`, `REDIS_SOURCE` and the `SUPABASE_*_SOURCE` variables) defaults to `container`. The startup flow usually manages the init-service selectors (`*_INIT_SOURCE`); do not change them first.

### 3.3. Feature Flags (Non-SOURCE)

Some features within services are controlled by feature flags rather than SOURCE variables:

| Feature | Variable | Options | Notes |
|---------|----------|---------|-------|
| **LangMem Memory** | `LANGMEM_ENABLED` | `true`, `false` | Persistent conversation memory embedded in the Backend service. |

### 3.4. Wizard Model Selections (Non-SOURCE)

The interactive wizard's per-provider multiselects persist as comma-separated env vars in `.env`. On each `docker compose up`:

- **`litellm-init`** calls `model_resolver.active_models(env)` to render `volumes/litellm/config.yaml`. It reads `services/ollama/models.yaml`, `services/litellm/models.yaml` and the `*_USER_MODELS` variables below. No database query is involved.
- **`ollama-pull`** pre-pulls the same resolved Ollama models for container sources. For `ollama-localhost`, the bootstrapper pulls the same declared set onto the host daemon at every start. Present tags are skipped, missing tags are pulled, and a failed tag gives a warning without stopping the start.

| Variable | Set by | Default | Notes |
|---|---|---|---|
| `OLLAMA_USER_MODELS` | Single unified Ollama models multiselect (source-aware; localhost rows are badged `[pulled]` / `[library]`). | Default-active baseline (qwen3.8:latest, qwen3-embedding:0.6b, nomic-embed-text). | Consumed by `model_resolver` for every Ollama source. `ollama-pull` pulls them for container sources. For `ollama-localhost`, the bootstrapper pulls them onto the host daemon at start. |
| `OLLAMA_CUSTOM_MODELS` | Ollama "additional models to pull" free-text step. | Empty. | Comma-separated. `ollama-pull` pulls them for container sources. For `ollama-localhost`, the bootstrapper pulls them onto the host daemon at start. |
| `OPENAI_USER_MODELS` | OpenAI multiselect (live `/v1/models` fetch; falls back to the curated catalog and says so). | Curated default-active intersection (gpt-5, gpt-5-mini, text-embedding-3-large) when key valid. | Requires `OPENAI_API_KEY`. |
| `ANTHROPIC_USER_MODELS` | Anthropic multiselect (live `/v1/models` fetch; falls back to the curated catalog and says so). | Curated default-active intersection (claude-opus-4-7, claude-sonnet-4-6) when key valid. | Requires `ANTHROPIC_API_KEY`. |
| `OPENROUTER_USER_MODELS` | OpenRouter multiselect (live `/api/v1/models` fetch; falls back to the curated catalog and says so). | `openrouter/auto` when reachable. | Requires `OPENROUTER_API_KEY`. |

Each cloud model row is badged `live`, `catalog` or `saved`. See [Interactive Setup Wizard §4.4.2](../quick-start/interactive-setup-wizard.md#442-where-the-listed-models-came-from).

### 3.5. Numeric wizard entries

The wizard accepts `BASE_PORT` and the inline numbers (`RAY_WORKER_COUNT`, `SPARK_WORKER_COUNT`, `PROMETHEUS_RETENTION_DAYS`, the STT provider ports) as typed, or refuses them. Empty keeps the current value, and `auto` is a valid `BASE_PORT` value. See [Interactive Setup Wizard §7.2](../quick-start/interactive-setup-wizard.md#72-invalid-numbers-are-refused-not-adjusted).

## 4. Detailed SOURCE Configurations

### 4.1. LLM access (LiteLLM gateway + Ollama, vLLM Metal, and cloud upstreams)

**LiteLLM** is the always-on OpenAI-compatible gateway that Atlas-managed consumers read. The upstreams behind it are configurable: Ollama, the optional managed vLLM Metal host and three cloud providers. The [LiteLLM Gateway README](../../services/litellm/README.md) describes the consumer-facing surface. The variables below pick what LiteLLM forwards to.

#### 4.1.1. `LLM_PROVIDER_SOURCE` — Ollama upstream (single-select)

| Value | Use |
|---|---|
| `ollama-container-cpu` (default) | Ollama in a CPU container. No host setup. |
| `ollama-container-gpu` | Ollama in a GPU container. Needs an NVIDIA GPU and the NVIDIA Container Toolkit. |
| `ollama-localhost` | An Ollama on the host, port `OLLAMA_LOCALHOST_PORT` (default `11434`). Run `ollama serve` on the host; Atlas pulls the declared tags at start. |
| `none` | No Ollama upstream. Enable `VLLM_METAL_SOURCE=managed-localhost`, at least one `CLOUD_*_SOURCE`, or both. |

The bootstrapper refuses to start only when `LLM_PROVIDER_SOURCE=none`, vLLM Metal is disabled and every cloud source is disabled. The legacy values `LLM_PROVIDER_SOURCE=api` and `LLM_PROVIDER_SOURCE=disabled` have been removed; use `none` to mean "no Ollama upstream."

#### 4.1.2. `VLLM_METAL_SOURCE` — managed Apple-silicon upstream

`VLLM_METAL_SOURCE=managed-localhost` installs and supervises a native vLLM Metal process on a supported Apple-silicon host. It then registers its `VLLM_METAL_MODEL` alias with LiteLLM. It is independent of `LLM_PROVIDER_SOURCE`: a vLLM-Metal-only configuration with `LLM_PROVIDER_SOURCE=none` and every cloud source disabled is valid. The default is `disabled`. See [vLLM Metal](../../services/vllm-metal/README.md) for platform, model, port and lifecycle requirements.

#### 4.1.3. `CLOUD_OPENAI_SOURCE` / `CLOUD_ANTHROPIC_SOURCE` / `CLOUD_OPENROUTER_SOURCE` (multi-toggle)

Each cloud provider is an independent `enabled` / `disabled` switch (default `disabled`). Turning a provider off keeps its API key; only `remove` in the wizard key step deletes it ([§4.4.1](../quick-start/interactive-setup-wizard.md#441-turning-a-provider-off-is-not-the-same-as-deleting-its-key)). Consumers request model IDs against `LITELLM_BASE_URL`. LiteLLM routes the active model set that `model_resolver` computes on each `docker compose up`.

```bash
CLOUD_OPENAI_SOURCE=enabled          # requires OPENAI_API_KEY
CLOUD_ANTHROPIC_SOURCE=enabled       # requires ANTHROPIC_API_KEY
CLOUD_OPENROUTER_SOURCE=enabled      # requires OPENROUTER_API_KEY
```

The LiteLLM path gives one URL and key for the default Atlas-managed consumer path, with spend logging in LiteLLM. Services that support a native-provider override can bypass that path. Cloud providers add API costs and per-provider quotas.

#### 4.1.4. Per-provider activation rules (applied by `model_resolver` on every `docker compose up`)

| Provider state | `*_USER_MODELS` env var | Result |
|---|---|---|
| `disabled` OR no API key | (any) | Zero active entries for that provider — LiteLLM routes nothing to it. |
| `enabled` + key | non-empty CSV | Exactly those models are active (catalog entries + synthesized entries for unknown names). |
| `enabled` + key | empty | The curated `default_active=True` set from the YAML catalog (e.g. gpt-5 + gpt-5-mini + text-embedding-3-large for OpenAI) so the provider is usable out of the box. |

**Bootstrapper safety net.** `source_validator.enforce_runtime_invariants()` sets `CLOUD_*_SOURCE=enabled` back to `disabled` when the matching API key is empty, and prints a warning. This prevents a provider that looks ready in `.env` but fails at the first request.

**Default models.** At every start, a default model that this launch will not route is replaced with the best active model. This covers `LITELLM_DEFAULT_MODEL`, `LITELLM_VISION_MODEL`, `LITELLM_EMBEDDING_MODEL` and `LANGMEM_EMBEDDING_MODEL`. Examples are an `ollama/*` model when Ollama is off, or a cloud model whose provider is disabled. The embedding dimension follows the new embedding model.

- A routed model, a consumer model and a host-imported Ollama tag are never changed.
- An explicit non-Ollama `LANGMEM_EMBEDDING_MODEL` becomes the embedding model for the pair.
- If no active provider offers a replacement, the value is kept and a warning names it.

### 4.2. Per-service SOURCE options

Each row lists the options from the service's `service.yml` `sources:` block; **bold** marks the default. Options marked † are dev-only and are not offered under `--profile prod`. The service README holds setup, configuration and troubleshooting.

| SOURCE variable | Options | Behaviour and requirements | Service README |
|---|---|---|---|
| `AIRFLOW_SOURCE` | `container`, **`disabled`** | DAG orchestrator (LocalExecutor, metadata in Supabase Postgres). UI and `/api/v2/` at `airflow.localhost`. See §4.3.1. | [Airflow](../../services/airflow/README.md) |
| `ASSET_BAKER_SOURCE` | `container-cpu`, **`disabled`** | Blender high-poly to low-poly bake (Cycles CPU). | [Asset Baker](../../services/asset-baker/README.md) |
| `ASSET_WORKER_SOURCE` | `container`, **`disabled`** | glTF post-processing. | [Asset Worker](../../services/asset-worker/README.md) |
| `BACKUP_SOURCE` | `container`, **`disabled`** | Postgres and database snapshots to S3. Run with `docker compose run --rm backup`. | [Backup](../../services/backup/README.md) |
| `BLENDER_MCP_SOURCE` | `localhost`†, `managed-localhost`†, **`disabled`** | Host Blender MCP bridge. `managed-localhost` runs an Atlas-provisioned headless Blender. No Kong route. | [Blender MCP](../../services/blender-mcp/README.md) |
| `CELERY_SOURCE` | `container`, **`disabled`** | Backend Celery worker and Flower monitor. | [Celery](../../services/celery/README.md) |
| `CLOUDFLARED_SOURCE` | `container`, **`disabled`** | Cloudflare Tunnel public edge, proxied to Kong. | [Cloudflare Tunnel](../../services/cloudflared/README.md) |
| `COMFYUI_SOURCE` | **`container-cpu`**, `container-gpu`, `localhost`†, `managed-localhost-mps`†, `disabled` | `container-gpu` needs the NVIDIA Container Toolkit. `localhost` uses a host ComfyUI on `COMFYUI_LOCALHOST_PORT` (default `8000`; set `8188` for the upstream default). `managed-localhost-mps` runs Metal ComfyUI on Apple Silicon via `./start.sh comfyui-mps <preflight\|install\|provision\|provision-nodes\|start\|stop\|status\|health\|remove>`. | [ComfyUI](../../services/comfyui/README.md) |
| `CRAWL4AI_SOURCE` | `container`, **`disabled`** | Browser-backed extraction API at `crawl4ai.localhost`, with a generated `CRAWL4AI_API_TOKEN`. `LOCAL_DEEP_RESEARCHER_FULL_PAGE_MODE=crawl4ai` requires `container`. Disabled: Local Deep Researcher uses snippets unless the mode is `builtin`. | [Crawl4AI](../../services/crawl4ai/README.md) |
| `DOC_PROCESSOR_SOURCE` | **`disabled`**, `docling-localhost`†, `docling-container-gpu` | Docling document processor. | [Document Processor](../../services/doc-processor/README.md) |
| `FAL_SOURCE` | `enabled`, **`disabled`** | fal.ai cloud media. Needs `FAL_API_KEY` when enabled. See §4.3.5. | [FAL](../../services/fal/README.md) |
| `GRAFANA_SOURCE` | `container`, **`disabled`** | Dashboards and alerting at `grafana.localhost`; admin password generated. Pair it with `PROMETHEUS_SOURCE=container`, or every panel shows "datasource unreachable". | [Grafana](../../services/grafana/README.md) |
| `HERMES_SOURCE` | **`container`**, `localhost`†, `disabled` | Agent runtime; `litellm-init` registers `hermes-agent` in LiteLLM unless disabled. `HERMES_DEFAULT_MODEL` needs a context window of at least 64K. `localhost`: install Hermes on the host, run `hermes gateway run`, and set `HERMES_LOCALHOST_PORT` (default `8642`) if needed. | [Hermes Agent](../../services/hermes/README.md) |
| `ICEBERG_REST_SOURCE` | `container`, **`disabled`** | Iceberg REST catalog (Supabase JDBC catalog, MinIO warehouse). | [Iceberg REST](../../services/iceberg-rest/README.md) |
| `JENKINS_SOURCE` | `container`, **`disabled`** | Maven Spark-app builder at `jenkins.localhost`; it publishes JARs to the MinIO `jars` bucket. Requires `MINIO_SOURCE=container`. Atlas ships no downstream jobs. | [Jenkins](../../services/jenkins/README.md) |
| `JUPYTERHUB_SOURCE` | **`container`**, `disabled` | DS/ML and lakehouse notebooks. Includes `boto3`, `s3fs`, `pyiceberg[s3fs]`, `pyarrow` and `duckdb`. | [JupyterHub](../../services/jupyterhub/README.md) |
| `LABEL_STUDIO_SOURCE` | `container`, **`disabled`** | Dataset review and annotation for the ML Engineering track, at `label-studio.localhost`. Requires `MINIO_SOURCE=container`. | [Label Studio](../../services/label-studio/README.md) |
| `LANGFUSE_SOURCE` | `container`, **`disabled`** | LLM traces and evals for LiteLLM-routed calls, at `langfuse.localhost`. Requires `MINIO_SOURCE=container`, which the `gen-ai-*` tracks disable: pass `--minio-source container` there. | [Langfuse](../../services/langfuse/README.md) |
| `LIGHTRAG_SOURCE` | `container`, `localhost`†, **`disabled`** | `container` stores data in Supabase pgvector, Neo4j and Redis. If one is disabled, LightRAG fails to start unless its `LIGHTRAG_*_STORAGE` names an in-process class. `localhost` uses `LIGHTRAG_LOCALHOST_PORT` (default `63068`). | [LightRAG](../../services/lightrag/README.md) |
| `LLM_GRAPH_BUILDER_SOURCE` | `container`, **`disabled`** | Neo4j LLM Graph Builder (pinned source build). | [LLM Graph Builder](../../services/llm-graph-builder/README.md) |
| `LLM_PROVIDER_SOURCE` | **`ollama-container-cpu`**, `ollama-container-gpu`, `ollama-localhost`†, `none` | Ollama upstream for LiteLLM. See §4.1.1. | [Ollama](../../services/ollama/README.md) |
| `LOCAL_DEEP_RESEARCHER_SOURCE` | **`container`**, `disabled` | LangGraph research agent. | [Local Deep Researcher](../../services/local-deep-researcher/README.md) |
| `LOKI_SOURCE` | `container`, **`disabled`** | Local queryable log store. | [Loki](../../services/loki/README.md) |
| `MCP_SERVERS_SOURCE` | `container`, **`disabled`** | Read-only Postgres, Neo4j and SearXNG tools over Streamable HTTP at `/mcp` (`mcp.localhost`). Requires `NEO4J_GRAPH_DB_SOURCE=container` and `SEARXNG_SOURCE=container`. Tool output is untrusted. | [MCP Servers](../../services/mcp-servers/README.md) |
| `MINIO_SOURCE` | **`container`**, `disabled` | S3-compatible object storage with scoped per-consumer buckets. See §4.3.3. | [MinIO](../../services/minio/README.md) |
| `MLFLOW_SOURCE` | `container`, **`disabled`** | Experiment tracking at `mlflow.localhost`; JupyterHub gets `MLFLOW_TRACKING_URI=http://mlflow:5000`. Requires `MINIO_SOURCE=container`. Model promotion and serving are not included. | [MLflow](../../services/mlflow/README.md) |
| `MULTI2VEC_CLIP_SOURCE` | **`container-cpu`**, `container-gpu`, `disabled` | CLIP vectorizer for Weaviate. `container-gpu` sets `ENABLE_CUDA=1`, but Compose requests no GPU device for it. This variable has no `sources:` block; it is declared in the Weaviate manifest. See §4.3.2. | [multi2vec-clip](../../services/multi2vec-clip/README.md) |
| `N8N_SOURCE` | **`container`**, `disabled` | Workflow automation. | [n8n](../../services/n8n/README.md) |
| `NEO4J_GRAPH_DB_SOURCE` | **`container`**, `localhost`†, `disabled` | Graph database. | [Neo4j](../../services/neo4j/README.md) |
| `OPEN_WEB_UI_SOURCE` | **`container`**, `disabled` | Chat interface. | [Open WebUI](../../services/open-webui/README.md) |
| `OPENCLAW_SOURCE` | **`disabled`**, `container`, `localhost`† | AI agent gateway. `localhost` needs Node.js 22+ and `npm install -g openclaw`. Run `openclaw onboard`, then `openclaw gateway --port 63065`, or set `OPENCLAW_LOCALHOST_PORT` to your port. | [OpenClaw](../../services/openclaw/README.md) |
| `OTEL_COLLECTOR_SOURCE` | `container`, **`disabled`** | OpenTelemetry (OTLP) ingest. | [OpenTelemetry Collector](../../services/otel-collector/README.md) |
| `PROMETHEUS_SOURCE` | `container`, **`disabled`** | Metrics and TSDB with `node-exporter` and `cAdvisor`, at `prometheus.localhost`. It also scales the `postgres-exporter` and `redis-exporter` sidecars. `cAdvisor` and `node-exporter` add steady CPU load. | [Prometheus](../../services/prometheus/README.md) |
| `RAY_SOURCE` | `ray-container-cpu`, `ray-container-gpu`, **`disabled`** | Head plus `RAY_WORKER_COUNT` workers (`0` = head only), dashboard at `ray.localhost`. GPU needs the NVIDIA Container Toolkit. Disabled: Backend `/api/ray/*` returns 503 and `ray.init()` in notebooks fails. | [Ray](../../services/ray/README.md) |
| `REDPANDA_SOURCE` | `container`, **`disabled`** | Kafka-API broker, console and demo topics. | [Redpanda](../../services/redpanda/README.md) |
| `SEARXNG_SOURCE` | **`container`**, `disabled` | Privacy metasearch. | [SearXNG](../../services/searxng/README.md) |
| `SPARK_SOURCE` | `container`, **`disabled`** | Standalone cluster with Spark Connect. Requires `MINIO_SOURCE=container`. With Spark disabled, set `ZEPPELIN_SOURCE=disabled` too. See §4.3.4. | [Spark](../../services/spark/README.md) |
| `STT_PROVIDER_SOURCE` | **`speaches-container-cpu`**, `speaches-container-gpu`, `parakeet-container-gpu`, `parakeet-localhost`†, `whisper-cpp-localhost`†, `disabled` | Speech-to-text engine. | [STT Provider](../../services/stt-provider/README.md) |
| `SUPAVISOR_SOURCE` | `container`, **`disabled`** | Postgres transaction pooler. | [Supavisor](../../services/supavisor/README.md) |
| `TEI_RERANKER_SOURCE` | `container-cpu`, `container-gpu`, `localhost`†, **`disabled`** | Cross-encoder reranker (`mixedbread-ai/mxbai-rerank-base-v1`) with a `/rerank` route. On arm64, `container-cpu` uses the digest-pinned `cpu-arm64-latest` image. LightRAG gets `RERANK_BINDING=null`; direct LightRAG-to-TEI reranking needs an adapter. | [TEI Reranker](../../services/tei-reranker/README.md) |
| `TEMPO_SOURCE` | `container`, **`disabled`** | Local distributed-trace store. | [Tempo](../../services/tempo/README.md) |
| `TIKA_SOURCE` | `container`, `tika-localhost`†, **`disabled`** | Fallback text extractor for formats Docling does not support (EML, MSG, RTF, OpenDocument, archives). `tika-localhost` uses `TIKA_LOCALHOST_PORT` (default `9998`). | [Apache Tika](../../services/tika/README.md) |
| `TRINO_SOURCE` | `container`, **`disabled`** | Federated SQL over Iceberg REST and the MinIO lakehouse. | [Trino](../../services/trino/README.md) |
| `TRUEFORGE_SOURCE` | `container`, **`disabled`** | Agent runtime with MCP tools, approvals and schedules, at `trueforge.localhost`. Uses a LiteLLM virtual key. `MCP_SERVERS_SOURCE=container` is optional. | [TrueForge](../../services/trueforge/README.md) |
| `TTS_PROVIDER_SOURCE` | **`speaches-container-cpu`**, `speaches-container-gpu`, `chatterbox-container-gpu`, `chatterbox-localhost`†, `disabled` | Text-to-speech engine. | [TTS Provider](../../services/tts-provider/README.md) |
| `VERBA_SOURCE` | `container`, **`disabled`** | Archived Weaviate RAG demo UI at `verba.localhost`. Needs `WEAVIATE_SOURCE` `container` or `localhost`. | [Verba](../../services/verba/README.md) |
| `VLLM_METAL_SOURCE` | `managed-localhost`†, **`disabled`** | Managed Apple-silicon LLM server for LiteLLM. See §4.1.2. | [vLLM Metal](../../services/vllm-metal/README.md) |
| `WEAVIATE_SOURCE` | **`container`**, `localhost`†, `disabled` | Vector database. See §4.3.2. | [Weaviate](../../services/weaviate/README.md) |
| `ZEPPELIN_SOURCE` | `container`, **`disabled`** | Spark-first notebook with a loopback-only direct UI at `http://127.0.0.1:${ZEPPELIN_PORT}`. Requires `SPARK_SOURCE=container` and `MINIO_SOURCE=container`; with Spark disabled, the start stops with an error. | [Zeppelin](../../services/zeppelin/README.md) |

The cloud switches (`CLOUD_*_SOURCE`) are in §4.1.3.

### 4.3. Cross-service notes

#### 4.3.1. Airflow Connections

`airflow-init` seeds `postgres_supabase`, `litellm_default` and `redis_default` always. It seeds `spark_default`, `minio_default`, `weaviate_default` and `neo4j_default` only when that sibling's source is `container`. Without `spark_default`, `SparkSubmitOperator` tasks fail.

The image includes `apache-airflow-providers-openai`, wired to LiteLLM. For LangChain, use `langchain-openai` with `PythonOperator`; no `apache-airflow-providers-langchain` package exists. The image also has Java 17, `spark-submit` and the S3A and Iceberg jars. `SparkSubmitOperator` can submit a JAR from `s3a://jars/...` to `spark://spark-master:7077` with `deploy_mode="cluster"`.

To read a seeded Connection from a `docker exec` script outside a task, see the [Airflow README](../../services/airflow/README.md) §4.

The manual `lakehouse_spark_submit_smoke` DAG submits a validation JAR from `s3a://jars/` and records Spark History. It needs Spark, MinIO and Iceberg REST. See the [Airflow README](../../services/airflow/README.md) §4 (seeded Connections) and §5 (sample and smoke DAGs).

`AIRFLOW_SOURCE=container` generates these secrets in `.env` on the first start:

```bash
# Username is hardcoded `admin` — there is no AIRFLOW_ADMIN_USERNAME knob.
AIRFLOW_ADMIN_PASSWORD=...              # auto-generated
AIRFLOW_FERNET_KEY=...                  # auto-generated; encrypts Connections + Variables at rest
AIRFLOW_SECRET_KEY=...                  # auto-generated; AIRFLOW__API__SECRET_KEY
AIRFLOW_JWT_SECRET=...                  # auto-generated; AIRFLOW__API_AUTH__JWT_SECRET signs Execution API and /api/v2 JWTs
AIRFLOW_DB_USER=airflow                 # Postgres role on supabase-db
AIRFLOW_DB_PASSWORD=...                 # auto-generated
```

#### 4.3.2. Weaviate and the CLIP vectorizer

`WEAVIATE_URL` is auto-managed per source (`http://weaviate:8080` for `container`). Text vectorization uses the `text2vec-openai` module against `LITELLM_BASE_URL`, with `OPENAI_APIKEY` set to `LITELLM_MASTER_KEY`. `text2vec-ollama` and `generative-ollama` stay enabled for schemas created before LiteLLM fronted Ollama.

```bash
MULTI2VEC_CLIP_SOURCE=container-cpu
WEAVIATE_ENABLE_MODULES=text2vec-openai,text2vec-ollama,multi2vec-clip,generative-openai,generative-ollama,backup-filesystem
CLIP_INFERENCE_API=http://multi2vec-clip:8080
MULTI2VEC_CLIP_SIGLIP2_IMAGE=semitechnologies/multi2vec-clip:google-siglip2-so400m-patch16-512-1.5.1
```

- With `MULTI2VEC_CLIP_SOURCE=disabled`, the bootstrapper removes `multi2vec-clip` from `WEAVIATE_ENABLE_MODULES` and blanks `CLIP_INFERENCE_API`.
- CLIP runs only beside a container Weaviate. With `WEAVIATE_SOURCE=localhost` or `disabled` it is scaled to 0, because Weaviate is its only consumer.
- `MULTI2VEC_CLIP_SIGLIP2_IMAGE` is an opt-in alternative to `MULTI2VEC_CLIP_IMAGE`. It changes vectors from 512-d to 1152-d, so existing collections that use `multi2vec-clip` must be recreated or revectorized. See the [multi2vec-clip README](../../services/multi2vec-clip/README.md).

#### 4.3.3. MinIO

`MINIO_ENDPOINT` (`http://minio:9000`) and `MINIO_PUBLIC_ENDPOINT` are auto-managed. The S3 API is at `http://localhost:${MINIO_PORT}` and the console at `http://localhost:${MINIO_CONSOLE_PORT}` (63020 and 63021 at the default base port).

Their Compose fragments wire these consumers. They are Airflow, Asset Worker, Asset Baker, Backend, Celery, backup, Iceberg REST, Jenkins, JupyterHub, Label Studio, Langfuse, MLflow, Spark, Trino and Zeppelin. Most get scoped service-account credentials. Airflow and the backup service use the root credentials. Backend and Celery get the endpoint plus the credential variables a consumer manifest's `storage` block declares.

With `MINIO_SOURCE=disabled`, some services refuse to start, and the error names the service. They are Spark, Iceberg REST, Trino, Jenkins, MLflow, Label Studio, Langfuse, the asset worker and baker, and a local-mode backup. Disable them too, set `BACKUP_S3_MODE=external` for backups, or keep MinIO on. See [MinIO](../../services/minio/README.md) for the bucket-to-consumer table.

#### 4.3.4. Spark, Iceberg REST and notebook clients

- Spark runs a master, N workers (`SPARK_WORKER_COUNT`, 1 to 8), a history server, a `spark-connect` gRPC sidecar and a one-shot `spark-init`.
- In-stack clients use Spark Connect at `sc://spark-connect:15002`. JupyterHub gets `SPARK_REMOTE=sc://spark-connect:15002`.
- Zeppelin uses the standalone master (`spark://spark-master:7077`), because its launcher calls `spark-submit`. The Backend has no Spark wiring.
- The Spark image includes `iceberg-spark-runtime-4.1_2.13:1.11.0` and `iceberg-aws-bundle:1.11.0`. It preconfigures a `lakehouse` Iceberg REST catalog at `http://iceberg-rest:8181` on MinIO.
- The catalog works only when `ICEBERG_REST_SOURCE=container`.

Client setup is in the [Spark](../../services/spark/README.md), [JupyterHub](../../services/jupyterhub/README.md) and [Zeppelin](../../services/zeppelin/README.md) READMEs.

#### 4.3.5. FAL compatibility route

```bash
FAL_SOURCE=enabled
FAL_API_KEY=<your-fal-key>
FAL_MODEL=fal-ai/flux/dev
```

- `FAL_SOURCE=disabled` with a populated `FAL_API_KEY` is valid; the wizard key step works as for cloud providers ([§4.4.1](../quick-start/interactive-setup-wizard.md#441-turning-a-provider-off-is-not-the-same-as-deleting-its-key)).
- With FAL enabled, `POST /comfyui/generate` uses FAL, for existing Open WebUI and n8n callers. It accepts only `FAL_MODEL=fal-ai/flux/dev` and synchronous requests; other requests return 400.
- With `MEDIA_BUDGET_ENABLED=true`, `/comfyui/generate` returns `409`, because it cannot reserve budget.
- `POST /media/generate` is the provider-neutral route for FAL image and image-to-3D generation (TRELLIS, Hunyuan3D, Tripo, Rodin). Use its `input.provider_arguments` for other FAL endpoints. The backend's `/docs` endpoint serves the contract.

## 5. Configuration Patterns

### 5.1. Development Setup
Best for local development with minimal resources:

```bash
./start.sh --llm-provider-source ollama-localhost \
          --comfyui-source localhost \
          --weaviate-source container \
          --n8n-source disabled \
          --searxng-source disabled
```

### 5.2. Production Setup
Best for production with full features. `--profile prod` does not accept localhost sources (§9):

```bash
./start.sh --profile prod \
          --llm-provider-source ollama-container-gpu \
          --comfyui-source container-gpu \
          --weaviate-source container \
          --n8n-source container \
          --searxng-source container
```

### 5.3. Minimal Setup
Best for testing or resource-constrained environments:

```bash
./start.sh --llm-provider-source none \
          --cloud-openai-source enabled \
          --comfyui-source disabled \
          --weaviate-source disabled \
          --n8n-source disabled \
          --searxng-source disabled \
          --hermes-source disabled \
          --jupyterhub-source disabled \
          --minio-source disabled
```

Set `OPENAI_API_KEY` in `.env`, or the key for the `CLOUD_*_SOURCE` you enabled.

### 5.4. Mixed Setup
Combine host and container sources:

```bash
# Local LLM, containerized GPU image generation and data services, no search.
./start.sh --llm-provider-source ollama-localhost \
          --comfyui-source container-gpu \
          --weaviate-source container \
          --n8n-source container \
          --searxng-source disabled
```

## 6. Environment File vs CLI Overrides

### 6.1. Using .env File
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

To move the stack to another port range, change `BASE_PORT` or run `./start.sh --base-port <port>`. Container `*_PORT` variables are recomputed from `BASE_PORT` on every start, so a hand edit to one is lost. `*_LOCALHOST_PORT` variables (host-side ports) are kept.

### 6.2. Using CLI flags
CLI flags are written to `.env`, so they also apply to later runs:

```bash
# Flags are persisted to .env, so this choice also applies to later runs
./start.sh --llm-provider-source ollama-localhost --comfyui-source disabled

# Return to the previous sources by passing them again
./start.sh --llm-provider-source ollama-container-cpu --comfyui-source container-cpu
```

## 7. Service Dependencies

This section gives the main runtime dependencies. [Service Dependencies](../reference/service-dependencies.md) lists every declared dependency.

### 7.1. Core Dependencies
- **Backend / n8n / JupyterHub / Local Deep Researcher / OpenClaw** → read `LITELLM_BASE_URL` + `LITELLM_API_KEY` for LLM access. **Open WebUI** reaches the same gateway through `OPENAI_API_BASE_URLS` / `OPENAI_API_KEYS`. LiteLLM is always-on; `LLM_PROVIDER_SOURCE` and the `CLOUD_*_SOURCE` toggles select the upstream.
- **Backend API** → Depends on database services (PostgreSQL, Redis)
- **n8n workflows** → can call Weaviate through `WEAVIATE_URL`; no bundled workflow does, and n8n starts without it

### 7.2. Optional Dependencies
- **ComfyUI** → Independent, can be disabled without affecting other services
- **SearxNG** → Independent privacy search
- **Weaviate** → Optional unless needed for semantic search

## 8. Troubleshooting SOURCE Configurations

### 8.1. Common Issues

**Service won't start with localhost SOURCE**:
```bash
# Check if service is running locally
curl http://localhost:11434/api/tags  # Ollama (LiteLLM upstream when LLM_PROVIDER_SOURCE=ollama-localhost)
curl http://localhost:63040/health/liveliness  # LiteLLM gateway (always-on)
curl http://localhost:8000/           # ComfyUI default localhost URL
curl http://localhost:8188/           # ComfyUI if you overrode COMFYUI_LOCALHOST_PORT to 8188

# Check service logs (after the COMPOSE_PROJECT_NAME setup line in
# troubleshooting.md; PROJECT_NAME is not exported to your shell)
docker compose logs -f backend
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
# Kong config is regenerated from .env at every startup. Print route names and
# hosts only: the file also holds credentials.
grep -nE '^  - name:|^    - [a-z0-9.-]+\.localhost$' volumes/api/kong-dynamic.yml
grep -E '^[A-Z_]+_SOURCE=' .env

# Check the hosts file (read-only)
grep localhost /etc/hosts
# Write the *.localhost entries (sudo) and then start the stack without the wizard
./start.sh --setup-hosts
```

### 8.2. Debug Commands

```bash
# Check active SOURCE values
grep -E '^(LLM_PROVIDER|COMFYUI|N8N|WEAVIATE)_SOURCE=' .env

# Test service connectivity (LLM goes via LiteLLM, not Ollama directly)
# Run after the COMPOSE_PROJECT_NAME setup line in troubleshooting.md.
# The LiteLLM and Kong images ship no curl; probe from backend, which does.
docker compose exec backend curl -sf http://litellm:4000/health/liveliness
docker compose exec litellm python -c "import urllib.request; print(urllib.request.urlopen('http://ollama:11434/api/tags', timeout=5).status)"
docker compose exec backend curl -sf http://comfyui:18188/

# Monitor resource usage
docker stats
```

## 9. Deployment profile (`--profile prod`)

A deployment profile is a bundle defined in `bootstrapper/profiles.yml`. Two ship: `default` and `prod`; `dev` is an alias for `default`. Apply one with `./start.sh --profile prod` or the wizard's profile step. Each bundle has three fields:

- **`sources`**: source selections, either an option id or `auto` (host-adaptive). `prod` sets `prometheus: container` and `grafana: container`. They are re-applied on every start, so a hand edit to `.env` is overwritten.
- **`env`**: values such as `prod`'s `LOG_MAX_SIZE=10m` and `LOG_MAX_FILE=3`. A value is applied when the variable is unset, empty or still the `.env.example` default. On a profile switch, it is also applied when the variable holds the prior profile's value. A value pinned in `.env.user`, `ATLAS_ENV_USER_FILE` or a consumer manifest's `env`, and any other value you set, is kept. Keys only the old profile declared are not reset.
- **`host_bind_ip`**: the published-port interface. Both profiles use `127.0.0.1:`, so ports are reachable only from the host. A non-empty value you set, including `0.0.0.0:`, is kept.

A `--<svc>-source` flag on this run, a `*_SOURCE` in a consumer manifest's `env`, or one in `.env.user` / `ATLAS_ENV_USER_FILE` each overrides the profile's source.

Under `prod`, dev-only sources (every `localhost`, `*-localhost` and `managed-localhost*` option) are not offered, and selecting one fails validation.

The applied profile is recorded in `ATLAS_PROFILE_APPLIED`. On a switch, sources the prior profile set are first reset to their service defaults. Consumers can pin `profile:` and override fields with `profile_overrides:` in `atlas.consumer.yml`; see [reusing-atlas.md](reusing-atlas.md) §6.1. Per-service `*_MEMORY_LIMIT` / `*_CPU_LIMIT` values are `.env` defaults and do not depend on the profile.

For more troubleshooting help, see [../quick-start/troubleshooting.md](../quick-start/troubleshooting.md).
