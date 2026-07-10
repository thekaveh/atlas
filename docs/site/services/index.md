# Service Catalog

## 1. Service Catalog

### 1.1. agents

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [airflow](airflow.md) | Apache Airflow (DAG orchestrator) | all, data-eng | AIRFLOW_SOURCE | disabled | container, disabled | supabase, litellm, redis |
| [celery](celery.md) | Celery + Flower (async jobs) | all, gen-ai-eng, gen-ai-rag | CELERY_SOURCE | disabled | container, disabled | redis, backend, supabase, litellm |
| [hermes](hermes.md) | Hermes (programmable AI agent) | all, gen-ai-eng | HERMES_SOURCE | container | container, localhost, disabled | litellm |
| [lightrag](lightrag.md) | LightRAG (graph-augmented RAG server) | all, gen-ai-rag | LIGHTRAG_SOURCE | disabled | container, localhost, disabled | litellm |
| [mcp-servers](mcp-servers.md) | Curated MCP Servers | all, gen-ai-eng, gen-ai-rag | MCP_SERVERS_SOURCE | disabled | container, disabled | supabase, neo4j, searxng |
| [n8n](n8n.md) | n8n (workflow automation) | all, gen-ai-eng, gen-ai-rag | N8N_SOURCE | container | container, disabled | supabase, redis, litellm |
| [openclaw](openclaw.md) | OpenClaw (AI agent gateway) | all, gen-ai-eng | OPENCLAW_SOURCE | disabled | disabled, container, localhost | litellm |

### 1.2. aggregate

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [doc-processor](doc-processor.md) | doc-processor | all, gen-ai-creative, gen-ai-rag | - | - | - | - |
| [multi2vec-clip](multi2vec-clip.md) | multi2vec-clip | all, gen-ai-creative | - | - | - | - |
| [stt-provider](stt-provider.md) | stt-provider | all, gen-ai-creative, gen-ai-eng | - | - | - | - |

### 1.3. apps

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [backend](backend.md) | Backend API (FastAPI) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | BACKEND_SOURCE | container | - | supabase, redis, litellm |
| [jenkins](jenkins.md) | Jenkins (Maven Spark app builder) | all, data-eng | JENKINS_SOURCE | disabled | container, disabled | minio |
| [jupyterhub](jupyterhub.md) | JupyterHub (DS/ML + LLM notebooks) | all, data-eng, gen-ai-eng, ml-eng, trading | JUPYTERHUB_SOURCE | container | container, disabled | supabase, redis, litellm |
| [label-studio](label-studio.md) | Label Studio (dataset review + annotation) | all, ml-eng | LABEL_STUDIO_SOURCE | disabled | container, disabled | supabase, minio |
| [llm-graph-builder](llm-graph-builder.md) | Neo4j LLM Graph Builder | all, gen-ai-rag | LLM_GRAPH_BUILDER_SOURCE | disabled | container, disabled | neo4j, litellm, kong |
| [local-deep-researcher](local-deep-researcher.md) | Local Deep Researcher (LangGraph research agent) | all, gen-ai-eng, gen-ai-rag | LOCAL_DEEP_RESEARCHER_SOURCE | container | container, disabled | searxng, litellm |
| [mlflow](mlflow.md) | MLflow (experiment tracking + artifacts) | all, ml-eng, trading | MLFLOW_SOURCE | disabled | container, disabled | supabase, minio |
| [open-webui](open-webui.md) | Open WebUI (chat interface) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | OPEN_WEB_UI_SOURCE | container | container, disabled | supabase, redis, litellm |
| [verba](verba.md) | Verba (archived Weaviate RAG UI) | all, gen-ai-rag | VERBA_SOURCE | disabled | container, disabled | weaviate, litellm, kong |
| [zeppelin](zeppelin.md) | Apache Zeppelin (Spark-first notebook) | all, data-eng, ml-eng | ZEPPELIN_SOURCE | disabled | container, disabled | spark |

### 1.4. data

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [iceberg-rest](iceberg-rest.md) | Apache Iceberg REST Catalog | all, data-eng | ICEBERG_REST_SOURCE | disabled | container, disabled | minio, supabase |
| [minio](minio.md) | MinIO (S3-compatible object storage) | all, data-eng, ml-eng, trading | MINIO_SOURCE | container | container, disabled | supabase |
| [neo4j](neo4j.md) | Neo4j (graph database) | all, data-eng, gen-ai-eng, gen-ai-rag | NEO4J_GRAPH_DB_SOURCE | container | container, localhost, disabled | supabase |
| [redis](redis.md) | Redis (cache & queue) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | REDIS_SOURCE | container | container | supabase |
| [redpanda](redpanda.md) | Redpanda (Kafka API streaming) | all, data-eng | REDPANDA_SOURCE | disabled | container, disabled | - |
| [spark](spark.md) | Apache Spark (standalone cluster) | all, data-eng, ml-eng | SPARK_SOURCE | disabled | container, disabled | minio |
| [supabase](supabase.md) | Supabase (db, auth, api, storage, realtime, studio, meta) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | SUPABASE_DB_SOURCE, SUPABASE_DB_INIT_SOURCE, SUPABASE_META_SOURCE, SUPABASE_STORAGE_SOURCE, SUPABASE_AUTH_SOURCE, SUPABASE_API_SOURCE, SUPABASE_REALTIME_SOURCE, SUPABASE_STUDIO_SOURCE | container | container, disabled | - |
| [supavisor](supavisor.md) | Supavisor (Postgres transaction pooler) | all, data-eng, gen-ai-eng, gen-ai-rag, ml-eng | SUPAVISOR_SOURCE | disabled | container, disabled | supabase |
| [trino](trino.md) | Trino | all, data-eng | TRINO_SOURCE | disabled | container, disabled | minio, iceberg-rest |
| [weaviate](weaviate.md) | Weaviate (vector database) | all, data-eng, gen-ai-rag | WEAVIATE_SOURCE | container | container, localhost, disabled | supabase, litellm |

### 1.5. infra

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [backup](backup.md) | Backup / restore (Postgres + volumes -> S3) | all | BACKUP_SOURCE | disabled | container, disabled | supabase, minio |
| [cloudflared](cloudflared.md) | Cloudflare Tunnel (public edge) | all | CLOUDFLARED_SOURCE | disabled | container, disabled | kong |
| [globals](globals.md) | Globals (project + branding) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | - | - |
| [grafana](grafana.md) | Grafana (observability UI + alerting) | all | GRAFANA_SOURCE | disabled | container, disabled | prometheus, supabase, kong, ray |
| [kong](kong.md) | Kong (API gateway) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | KONG_API_GATEWAY_SOURCE | container | container | supabase, redis |
| [langfuse](langfuse.md) | Langfuse (LLM traces + evals) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | LANGFUSE_SOURCE | disabled | container, disabled | supabase, redis, minio, litellm, kong, ray |
| [loki](loki.md) | Loki (queryable log store) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | LOKI_SOURCE | disabled | container, disabled | kong, ray |
| [otel-collector](otel-collector.md) | OpenTelemetry Collector (telemetry ingest) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | OTEL_COLLECTOR_SOURCE | disabled | container, disabled | tempo |
| [prometheus](prometheus.md) | Prometheus (metrics scraper + TSDB) | all | PROMETHEUS_SOURCE | disabled | container, disabled | supabase, redis, kong, ray |
| [ray](ray.md) | Ray (distributed compute substrate) | all, ml-eng | RAY_SOURCE | disabled | ray-container-cpu, ray-container-gpu, disabled | supabase, redis |
| [tempo](tempo.md) | Tempo (distributed trace store) | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | TEMPO_SOURCE | disabled | container, disabled | kong, ray |

### 1.6. llm

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [cloud-providers](cloud-providers.md) | Cloud LLM providers (OpenAI, Anthropic, OpenRouter) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | CLOUD_OPENAI_SOURCE, CLOUD_ANTHROPIC_SOURCE, CLOUD_OPENROUTER_SOURCE | disabled | enabled, disabled | litellm |
| [litellm](litellm.md) | LiteLLM gateway (LLM router) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | LITELLM_SOURCE | container | container | supabase, redis |
| [ollama](ollama.md) | Ollama (local LLM engine) | all | LLM_PROVIDER_SOURCE | ollama-container-cpu | ollama-container-cpu, ollama-container-gpu, ollama-localhost, none | supabase, litellm |
| [tei-reranker](tei-reranker.md) | TEI Reranker (mxbai-rerank-base-v1) | all, gen-ai-rag, ml-eng | TEI_RERANKER_SOURCE | disabled | container-cpu, container-gpu, localhost, disabled | - |

### 1.7. media

| Service | Title | Tracks | SOURCE | Default | Values | Dependencies |
| --- | --- | --- | --- | --- | --- | --- |
| [asset-worker](asset-worker.md) | Asset Worker (glTF post-processing) | all, gen-ai-creative | ASSET_WORKER_SOURCE | disabled | container, disabled | minio |
| [blender-mcp](blender-mcp.md) | Blender MCP | all, gen-ai-creative | BLENDER_MCP_SOURCE | disabled | localhost, disabled | - |
| [chatterbox](chatterbox.md) | Chatterbox (voice-cloning TTS, GPU) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | - | tts-provider |
| [comfyui](comfyui.md) | ComfyUI (image generation) | all, gen-ai-creative, gen-ai-eng | COMFYUI_SOURCE | container-cpu | container-cpu, container-gpu, localhost, disabled | supabase, litellm, ollama |
| [crawl4ai](crawl4ai.md) | Crawl4AI (JS-capable web extraction) | all, gen-ai-rag | CRAWL4AI_SOURCE | disabled | container, disabled | - |
| [docling](docling.md) | Docling (document processor) | all | DOC_PROCESSOR_SOURCE | disabled | disabled, docling-localhost, docling-container-gpu | - |
| [fal](fal.md) | FAL Cloud Media | all, gen-ai-creative | FAL_SOURCE | disabled | enabled, disabled | - |
| [parakeet](parakeet.md) | Parakeet (NVIDIA STT engine) | all | STT_PROVIDER_SOURCE | speaches-container-cpu | speaches-container-cpu, speaches-container-gpu, parakeet-container-gpu, parakeet-localhost, whisper-cpp-localhost, disabled | litellm |
| [searxng](searxng.md) | SearXNG (privacy metasearch) | all, gen-ai-eng, gen-ai-rag | SEARXNG_SOURCE | container | container, disabled | redis |
| [speaches](speaches.md) | Speaches (unified TTS + STT) | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | - | parakeet, tts-provider |
| [tika](tika.md) | Apache Tika (fallback extractor) | all, gen-ai-eng, gen-ai-rag | TIKA_SOURCE | disabled | container, tika-localhost, disabled | - |
| [tts-provider](tts-provider.md) | TTS provider (text-to-speech engine selector) | all, gen-ai-creative, gen-ai-eng | TTS_PROVIDER_SOURCE | speaches-container-cpu | speaches-container-cpu, speaches-container-gpu, chatterbox-container-gpu, chatterbox-localhost, disabled | litellm |
