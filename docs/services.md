# 5.1. Service Catalog

## 1. Services by Category

### 1.1. agents

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [airflow](../services/airflow/README.md) | Apache Airflow (DAG orchestrator) | all, data-eng | AIRFLOW_SOURCE | disabled | container, disabled | supabase, litellm, redis |
| [celery](../services/celery/README.md) | Celery + Flower (async jobs) | all, gen-ai-eng, gen-ai-rag | CELERY_SOURCE | disabled | container, disabled | redis, backend, supabase, litellm |
| [hermes](../services/hermes/README.md) | Hermes (programmable AI agent) | all, gen-ai-eng | HERMES_SOURCE, HERMES_INIT_SOURCE | container | container, localhost, disabled | litellm |
| [lightrag](../services/lightrag/README.md) | LightRAG (graph-augmented RAG server) | all, gen-ai-rag | LIGHTRAG_SOURCE | disabled | container, localhost, disabled | litellm |
| [mcp-servers](../services/mcp-servers/README.md) | Curated MCP Servers | all, gen-ai-eng, gen-ai-rag | MCP_SERVERS_SOURCE | disabled | container, disabled | supabase, neo4j, searxng |
| [n8n](../services/n8n/README.md) | n8n (workflow automation) | all, gen-ai-eng, gen-ai-rag | N8N_SOURCE, N8N_INIT_SOURCE | container | container, disabled | supabase, redis, litellm |
| [openclaw](../services/openclaw/README.md) | OpenClaw (AI agent gateway) | all, gen-ai-eng | OPENCLAW_SOURCE, OPENCLAW_INIT_SOURCE | disabled, container | disabled, container, localhost | litellm |

### 1.2. aggregate

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [doc-processor](../services/doc-processor/README.md) | doc-processor | all, gen-ai-creative, gen-ai-rag | - | - | - | - |
| [multi2vec-clip](../services/multi2vec-clip/README.md) | multi2vec-clip | all, gen-ai-creative | - | - | - | - |
| [stt-provider](../services/stt-provider/README.md) | stt-provider | all, gen-ai-creative, gen-ai-eng | - | - | - | - |

### 1.3. apps

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [backend](../services/backend/README.md) | Backend API (FastAPI) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | BACKEND_SOURCE | container | - | supabase, redis, litellm |
| [jenkins](../services/jenkins/README.md) | Jenkins (Maven Spark app builder) | all, data-eng | JENKINS_SOURCE | disabled | container, disabled | minio |
| [jupyterhub](../services/jupyterhub/README.md) | JupyterHub (DS/ML + LLM notebooks) | all, data-eng, gen-ai-eng, ml-eng, trading | JUPYTERHUB_SOURCE | container | container, disabled | supabase, redis, litellm |
| [label-studio](../services/label-studio/README.md) | Label Studio (dataset review + annotation) | all, ml-eng | LABEL_STUDIO_SOURCE | disabled | container, disabled | supabase, minio |
| [llm-graph-builder](../services/llm-graph-builder/README.md) | Neo4j LLM Graph Builder | all, gen-ai-rag | LLM_GRAPH_BUILDER_SOURCE | disabled | container, disabled | neo4j, litellm, kong |
| [local-deep-researcher](../services/local-deep-researcher/README.md) | Local Deep Researcher (LangGraph research agent) | all, gen-ai-eng, gen-ai-rag | LOCAL_DEEP_RESEARCHER_SOURCE | container | container, disabled | searxng, litellm |
| [mlflow](../services/mlflow/README.md) | MLflow (experiment tracking + artifacts) | all, ml-eng, trading | MLFLOW_SOURCE | disabled | container, disabled | supabase, minio |
| [open-webui](../services/open-webui/README.md) | Open WebUI (chat interface) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | OPEN_WEB_UI_SOURCE | container | container, disabled | supabase, redis, litellm |
| [verba](../services/verba/README.md) | Verba (archived Weaviate RAG UI) | all, gen-ai-rag | VERBA_SOURCE | disabled | container, disabled | weaviate, litellm, kong |
| [zeppelin](../services/zeppelin/README.md) | Apache Zeppelin (Spark-first notebook) | all, data-eng, ml-eng | ZEPPELIN_SOURCE | disabled | container, disabled | spark, minio |

### 1.4. data

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [iceberg-rest](../services/iceberg-rest/README.md) | Apache Iceberg REST Catalog | all, data-eng | ICEBERG_REST_SOURCE | disabled | container, disabled | minio, supabase |
| [minio](../services/minio/README.md) | MinIO (S3-compatible object storage) | all, data-eng, ml-eng, trading | MINIO_SOURCE, MINIO_INIT_SOURCE | container | container, disabled | supabase |
| [neo4j](../services/neo4j/README.md) | Neo4j (graph database) | all, data-eng, gen-ai-eng, gen-ai-rag | NEO4J_GRAPH_DB_SOURCE | container | container, localhost, disabled | supabase |
| [redis](../services/redis/README.md) | Redis (cache & queue) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | REDIS_SOURCE | container | container | supabase |
| [redpanda](../services/redpanda/README.md) | Redpanda (Kafka API streaming) | all, data-eng | REDPANDA_SOURCE | disabled | container, disabled | - |
| [spark](../services/spark/README.md) | Apache Spark (standalone cluster) | all, data-eng, ml-eng | SPARK_SOURCE | disabled | container, disabled | minio |
| [supabase](../services/supabase/README.md) | Supabase (db, auth, api, storage, realtime, studio, meta) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | SUPABASE_DB_SOURCE, SUPABASE_DB_INIT_SOURCE, SUPABASE_META_SOURCE, SUPABASE_STORAGE_SOURCE, SUPABASE_AUTH_SOURCE, SUPABASE_API_SOURCE, SUPABASE_REALTIME_SOURCE, SUPABASE_STUDIO_SOURCE | container | container, disabled | - |
| [supavisor](../services/supavisor/README.md) | Supavisor (Postgres transaction pooler) | all, data-eng, gen-ai-eng, gen-ai-rag, ml-eng | SUPAVISOR_SOURCE | disabled | container, disabled | supabase |
| [trino](../services/trino/README.md) | Trino | all, data-eng | TRINO_SOURCE | disabled | container, disabled | minio, iceberg-rest |
| [weaviate](../services/weaviate/README.md) | Weaviate (vector database) | all, data-eng, gen-ai-rag | WEAVIATE_SOURCE, WEAVIATE_INIT_SOURCE, MULTI2VEC_CLIP_SOURCE | container, container-cpu | container, localhost, disabled, container-cpu, container-gpu | supabase, litellm |

### 1.5. infra

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [backup](../services/backup/README.md) | Backup / restore (Postgres + volumes -> S3) | all, data-eng, ml-eng, trading | BACKUP_SOURCE | disabled | container, disabled | supabase, minio |
| [cloudflared](../services/cloudflared/README.md) | Cloudflare Tunnel (public edge) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | CLOUDFLARED_SOURCE | disabled | container, disabled | kong |
| [globals](../services/globals/README.md) | Globals (project + branding) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | - | - |
| [grafana](../services/grafana/README.md) | Grafana (observability UI + alerting) | all | GRAFANA_SOURCE | disabled | container, disabled | prometheus, supabase, kong, ray |
| [kong](../services/kong/README.md) | Kong (API gateway) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | KONG_API_GATEWAY_SOURCE | container | container | supabase, redis |
| [langfuse](../services/langfuse/README.md) | Langfuse (LLM traces + evals) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | LANGFUSE_SOURCE | disabled | container, disabled | supabase, redis, minio, litellm, kong, ray |
| [loki](../services/loki/README.md) | Loki (queryable log store) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | LOKI_SOURCE | disabled | container, disabled | kong, ray |
| [otel-collector](../services/otel-collector/README.md) | OpenTelemetry Collector (telemetry ingest) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | OTEL_COLLECTOR_SOURCE | disabled | container, disabled | tempo |
| [prometheus](../services/prometheus/README.md) | Prometheus (metrics scraper + TSDB) | all | PROMETHEUS_SOURCE | disabled | container, disabled | supabase, redis, kong, ray |
| [ray](../services/ray/README.md) | Ray (distributed compute substrate) | all, ml-eng | RAY_SOURCE | disabled | ray-container-cpu, ray-container-gpu, disabled | supabase, redis |
| [tempo](../services/tempo/README.md) | Tempo (distributed trace store) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | TEMPO_SOURCE | disabled | container, disabled | kong, ray |

### 1.6. llm

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [cloud-providers](../services/cloud-providers/README.md) | Cloud LLM providers (OpenAI, Anthropic, OpenRouter) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | CLOUD_OPENAI_SOURCE, CLOUD_ANTHROPIC_SOURCE, CLOUD_OPENROUTER_SOURCE | disabled | enabled, disabled | litellm |
| [litellm](../services/litellm/README.md) | LiteLLM gateway (LLM router) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | LITELLM_SOURCE | container | container | supabase, redis |
| [ollama](../services/ollama/README.md) | Ollama (local LLM engine) | all | LLM_PROVIDER_SOURCE | ollama-container-cpu | ollama-container-cpu, ollama-container-gpu, ollama-localhost, none | supabase, litellm |
| [tei-reranker](../services/tei-reranker/README.md) | TEI Reranker (mxbai-rerank-base-v1) | all, gen-ai-rag, ml-eng | TEI_RERANKER_SOURCE | disabled | container-cpu, container-gpu, localhost, disabled | - |
| [vllm-metal](../services/vllm-metal/README.md) | vLLM (Metal) — managed Apple-silicon LLM server | all, gen-ai-eng | VLLM_METAL_SOURCE | disabled | managed-localhost, disabled | litellm |

### 1.7. media

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [asset-baker](../services/asset-baker/README.md) | Asset Baker (Blender HP→LP bake) | all, gen-ai-creative | ASSET_BAKER_SOURCE | disabled | container-cpu, disabled | minio |
| [asset-worker](../services/asset-worker/README.md) | Asset Worker (glTF post-processing) | all, gen-ai-creative | ASSET_WORKER_SOURCE | disabled | container, disabled | minio |
| [blender-mcp](../services/blender-mcp/README.md) | Blender MCP | all, gen-ai-creative | BLENDER_MCP_SOURCE | disabled | localhost, managed-localhost, disabled | - |
| [chatterbox](../services/chatterbox/README.md) | Chatterbox (voice-cloning TTS, GPU) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | - | tts-provider |
| [comfyui](../services/comfyui/README.md) | ComfyUI (image generation) | all, gen-ai-creative, gen-ai-eng | COMFYUI_SOURCE, COMFYUI_INIT_SOURCE | container-cpu, container | container-cpu, container-gpu, localhost, managed-localhost-mps, disabled, container | supabase, litellm, ollama |
| [crawl4ai](../services/crawl4ai/README.md) | Crawl4AI (JS-capable web extraction) | all, gen-ai-rag | CRAWL4AI_SOURCE | disabled | container, disabled | - |
| [docling](../services/docling/README.md) | Docling (document processor) | all | DOC_PROCESSOR_SOURCE | disabled | disabled, docling-localhost, docling-container-gpu | - |
| [fal](../services/fal/README.md) | FAL Cloud Media | all, gen-ai-creative | FAL_SOURCE | disabled | enabled, disabled | - |
| [parakeet](../services/parakeet/README.md) | Parakeet (NVIDIA STT engine) | all | STT_PROVIDER_SOURCE | speaches-container-cpu | speaches-container-cpu, speaches-container-gpu, parakeet-container-gpu, parakeet-localhost, whisper-cpp-localhost, disabled | litellm |
| [searxng](../services/searxng/README.md) | SearXNG (privacy metasearch) | all, gen-ai-eng, gen-ai-rag | SEARXNG_SOURCE | container | container, disabled | redis |
| [speaches](../services/speaches/README.md) | Speaches (unified TTS + STT) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | - | parakeet, tts-provider |
| [tika](../services/tika/README.md) | Apache Tika (fallback extractor) | all, gen-ai-eng, gen-ai-rag | TIKA_SOURCE | disabled | container, tika-localhost, disabled | - |
| [tts-provider](../services/tts-provider/README.md) | TTS provider (text-to-speech engine selector) | all, gen-ai-creative, gen-ai-eng | TTS_PROVIDER_SOURCE | speaches-container-cpu | speaches-container-cpu, speaches-container-gpu, chatterbox-container-gpu, chatterbox-localhost, disabled | litellm |
