# Services

## 1. Service Catalog

| Service | Category | Tracks | SOURCE | Values | Dependencies |
| --- | --- | --- | --- | --- | --- |
| airflow | agents | all, data-eng | AIRFLOW_SOURCE | container, disabled | supabase, litellm, redis |
| asset-baker | media | all, gen-ai-creative | ASSET_BAKER_SOURCE | container-cpu, disabled | minio |
| asset-worker | media | all, gen-ai-creative | ASSET_WORKER_SOURCE | container, disabled | minio |
| backend | apps | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | BACKEND_SOURCE | - | supabase, redis, litellm |
| backup | infra | all | BACKUP_SOURCE | container, disabled | supabase, minio |
| blender-mcp | media | all, gen-ai-creative | BLENDER_MCP_SOURCE | localhost, disabled | - |
| celery | agents | all, gen-ai-eng, gen-ai-rag | CELERY_SOURCE | container, disabled | redis, backend, supabase, litellm |
| chatterbox | media | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | tts-provider |
| cloud-providers | llm | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | CLOUD_OPENAI_SOURCE, CLOUD_ANTHROPIC_SOURCE, CLOUD_OPENROUTER_SOURCE | enabled, disabled | litellm |
| cloudflared | infra | all | CLOUDFLARED_SOURCE | container, disabled | kong |
| comfyui | media | all, gen-ai-creative, gen-ai-eng | COMFYUI_SOURCE | container-cpu, container-gpu, localhost, managed-localhost-mps, disabled | supabase, litellm, ollama |
| crawl4ai | media | all, gen-ai-rag | CRAWL4AI_SOURCE | container, disabled | - |
| doc-processor | aggregate | all, gen-ai-creative, gen-ai-rag | - | - | - |
| docling | media | all | DOC_PROCESSOR_SOURCE | disabled, docling-localhost, docling-container-gpu | - |
| fal | media | all, gen-ai-creative | FAL_SOURCE | enabled, disabled | - |
| globals | infra | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | - |
| grafana | infra | all | GRAFANA_SOURCE | container, disabled | prometheus, supabase, kong, ray |
| hermes | agents | all, gen-ai-eng | HERMES_SOURCE | container, localhost, disabled | litellm |
| iceberg-rest | data | all, data-eng | ICEBERG_REST_SOURCE | container, disabled | minio, supabase |
| jenkins | apps | all, data-eng | JENKINS_SOURCE | container, disabled | minio |
| jupyterhub | apps | all, data-eng, gen-ai-eng, ml-eng, trading | JUPYTERHUB_SOURCE | container, disabled | supabase, redis, litellm |
| kong | infra | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | KONG_API_GATEWAY_SOURCE | container | supabase, redis |
| label-studio | apps | all, ml-eng | LABEL_STUDIO_SOURCE | container, disabled | supabase, minio |
| langfuse | infra | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | LANGFUSE_SOURCE | container, disabled | supabase, redis, minio, litellm, kong, ray |
| lightrag | agents | all, gen-ai-rag | LIGHTRAG_SOURCE | container, localhost, disabled | litellm |
| litellm | llm | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | LITELLM_SOURCE | container | supabase, redis |
| llm-graph-builder | apps | all, gen-ai-rag | LLM_GRAPH_BUILDER_SOURCE | container, disabled | neo4j, litellm, kong |
| local-deep-researcher | apps | all, gen-ai-eng, gen-ai-rag | LOCAL_DEEP_RESEARCHER_SOURCE | container, disabled | searxng, litellm |
| loki | infra | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | LOKI_SOURCE | container, disabled | kong, ray |
| mcp-servers | agents | all, gen-ai-eng, gen-ai-rag | MCP_SERVERS_SOURCE | container, disabled | supabase, neo4j, searxng |
| minio | data | all, data-eng, ml-eng, trading | MINIO_SOURCE | container, disabled | supabase |
| mlflow | apps | all, ml-eng, trading | MLFLOW_SOURCE | container, disabled | supabase, minio |
| multi2vec-clip | aggregate | all, gen-ai-creative | - | - | - |
| n8n | agents | all, gen-ai-eng, gen-ai-rag | N8N_SOURCE | container, disabled | supabase, redis, litellm |
| neo4j | data | all, data-eng, gen-ai-eng, gen-ai-rag | NEO4J_GRAPH_DB_SOURCE | container, localhost, disabled | supabase |
| ollama | llm | all | LLM_PROVIDER_SOURCE | ollama-container-cpu, ollama-container-gpu, ollama-localhost, none | supabase, litellm |
| open-webui | apps | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | OPEN_WEB_UI_SOURCE | container, disabled | supabase, redis, litellm |
| openclaw | agents | all, gen-ai-eng | OPENCLAW_SOURCE | disabled, container, localhost | litellm |
| otel-collector | infra | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | OTEL_COLLECTOR_SOURCE | container, disabled | tempo |
| parakeet | media | all | STT_PROVIDER_SOURCE | speaches-container-cpu, speaches-container-gpu, parakeet-container-gpu, parakeet-localhost, whisper-cpp-localhost, disabled | litellm |
| prometheus | infra | all | PROMETHEUS_SOURCE | container, disabled | supabase, redis, kong, ray |
| ray | infra | all, ml-eng | RAY_SOURCE | ray-container-cpu, ray-container-gpu, disabled | supabase, redis |
| redis | data | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | REDIS_SOURCE | container | supabase |
| redpanda | data | all, data-eng | REDPANDA_SOURCE | container, disabled | - |
| searxng | media | all, gen-ai-eng, gen-ai-rag | SEARXNG_SOURCE | container, disabled | redis |
| spark | data | all, data-eng, ml-eng | SPARK_SOURCE | container, disabled | minio |
| speaches | media | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | parakeet, tts-provider |
| stt-provider | aggregate | all, gen-ai-creative, gen-ai-eng | - | - | - |
| supabase | data | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | SUPABASE_DB_SOURCE, SUPABASE_DB_INIT_SOURCE, SUPABASE_META_SOURCE, SUPABASE_STORAGE_SOURCE, SUPABASE_AUTH_SOURCE, SUPABASE_API_SOURCE, SUPABASE_REALTIME_SOURCE, SUPABASE_STUDIO_SOURCE | container, disabled | - |
| supavisor | data | all, data-eng, gen-ai-eng, gen-ai-rag, ml-eng | SUPAVISOR_SOURCE | container, disabled | supabase |
| tei-reranker | llm | all, gen-ai-rag, ml-eng | TEI_RERANKER_SOURCE | container-cpu, container-gpu, localhost, disabled | - |
| tempo | infra | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | TEMPO_SOURCE | container, disabled | kong, ray |
| tika | media | all, gen-ai-eng, gen-ai-rag | TIKA_SOURCE | container, tika-localhost, disabled | - |
| trino | data | all, data-eng | TRINO_SOURCE | container, disabled | minio, iceberg-rest |
| tts-provider | media | all, gen-ai-creative, gen-ai-eng | TTS_PROVIDER_SOURCE | speaches-container-cpu, speaches-container-gpu, chatterbox-container-gpu, chatterbox-localhost, disabled | litellm |
| verba | apps | all, gen-ai-rag | VERBA_SOURCE | container, disabled | weaviate, litellm, kong |
| weaviate | data | all, data-eng, gen-ai-rag | WEAVIATE_SOURCE | container, localhost, disabled | supabase, litellm |
| zeppelin | apps | all, data-eng, ml-eng | ZEPPELIN_SOURCE | container, disabled | spark |

## 2. Category Catalog

| Service | Title | Category | Kind | Tracks | SOURCE |
| --- | --- | --- | --- | --- | --- |
| airflow | Apache Airflow (DAG orchestrator) | agents | container | all, data-eng | AIRFLOW_SOURCE |
| asset-baker | Asset Baker (Blender HP→LP bake) | media | container | all, gen-ai-creative | ASSET_BAKER_SOURCE |
| asset-worker | Asset Worker (glTF post-processing) | media | container | all, gen-ai-creative | ASSET_WORKER_SOURCE |
| backend | Backend API (FastAPI) | apps | container | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | BACKEND_SOURCE |
| backup | Backup / restore (Postgres + volumes -> S3) | infra | container | all | BACKUP_SOURCE |
| blender-mcp | Blender MCP | media | virtual | all, gen-ai-creative | BLENDER_MCP_SOURCE |
| celery | Celery + Flower (async jobs) | agents | container | all, gen-ai-eng, gen-ai-rag | CELERY_SOURCE |
| chatterbox | Chatterbox (voice-cloning TTS, GPU) | media | container | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - |
| cloud-providers | Cloud LLM providers (OpenAI, Anthropic, OpenRouter) | llm | virtual | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | CLOUD_OPENAI_SOURCE, CLOUD_ANTHROPIC_SOURCE, CLOUD_OPENROUTER_SOURCE |
| cloudflared | Cloudflare Tunnel (public edge) | infra | container | all | CLOUDFLARED_SOURCE |
| comfyui | ComfyUI (image generation) | media | container | all, gen-ai-creative, gen-ai-eng | COMFYUI_SOURCE |
| crawl4ai | Crawl4AI (JS-capable web extraction) | media | container | all, gen-ai-rag | CRAWL4AI_SOURCE |
| doc-processor | doc-processor | aggregate | doc-only | all, gen-ai-creative, gen-ai-rag | - |
| docling | Docling (document processor) | media | container | all | DOC_PROCESSOR_SOURCE |
| fal | FAL Cloud Media | media | virtual | all, gen-ai-creative | FAL_SOURCE |
| globals | Globals (project + branding) | infra | virtual | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - |
| grafana | Grafana (observability UI + alerting) | infra | container | all | GRAFANA_SOURCE |
| hermes | Hermes (programmable AI agent) | agents | container | all, gen-ai-eng | HERMES_SOURCE |
| iceberg-rest | Apache Iceberg REST Catalog | data | container | all, data-eng | ICEBERG_REST_SOURCE |
| jenkins | Jenkins (Maven Spark app builder) | apps | container | all, data-eng | JENKINS_SOURCE |
| jupyterhub | JupyterHub (DS/ML + LLM notebooks) | apps | container | all, data-eng, gen-ai-eng, ml-eng, trading | JUPYTERHUB_SOURCE |
| kong | Kong (API gateway) | infra | container | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | KONG_API_GATEWAY_SOURCE |
| label-studio | Label Studio (dataset review + annotation) | apps | container | all, ml-eng | LABEL_STUDIO_SOURCE |
| langfuse | Langfuse (LLM traces + evals) | infra | container | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | LANGFUSE_SOURCE |
| lightrag | LightRAG (graph-augmented RAG server) | agents | container | all, gen-ai-rag | LIGHTRAG_SOURCE |
| litellm | LiteLLM gateway (LLM router) | llm | container | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | LITELLM_SOURCE |
| llm-graph-builder | Neo4j LLM Graph Builder | apps | container | all, gen-ai-rag | LLM_GRAPH_BUILDER_SOURCE |
| local-deep-researcher | Local Deep Researcher (LangGraph research agent) | apps | container | all, gen-ai-eng, gen-ai-rag | LOCAL_DEEP_RESEARCHER_SOURCE |
| loki | Loki (queryable log store) | infra | container | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | LOKI_SOURCE |
| mcp-servers | Curated MCP Servers | agents | container | all, gen-ai-eng, gen-ai-rag | MCP_SERVERS_SOURCE |
| minio | MinIO (S3-compatible object storage) | data | container | all, data-eng, ml-eng, trading | MINIO_SOURCE |
| mlflow | MLflow (experiment tracking + artifacts) | apps | container | all, ml-eng, trading | MLFLOW_SOURCE |
| multi2vec-clip | multi2vec-clip | aggregate | doc-only | all, gen-ai-creative | - |
| n8n | n8n (workflow automation) | agents | container | all, gen-ai-eng, gen-ai-rag | N8N_SOURCE |
| neo4j | Neo4j (graph database) | data | container | all, data-eng, gen-ai-eng, gen-ai-rag | NEO4J_GRAPH_DB_SOURCE |
| ollama | Ollama (local LLM engine) | llm | container | all | LLM_PROVIDER_SOURCE |
| open-webui | Open WebUI (chat interface) | apps | container | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | OPEN_WEB_UI_SOURCE |
| openclaw | OpenClaw (AI agent gateway) | agents | container | all, gen-ai-eng | OPENCLAW_SOURCE |
| otel-collector | OpenTelemetry Collector (telemetry ingest) | infra | container | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | OTEL_COLLECTOR_SOURCE |
| parakeet | Parakeet (NVIDIA STT engine) | media | container | all | STT_PROVIDER_SOURCE |
| prometheus | Prometheus (metrics scraper + TSDB) | infra | container | all | PROMETHEUS_SOURCE |
| ray | Ray (distributed compute substrate) | infra | container | all, ml-eng | RAY_SOURCE |
| redis | Redis (cache & queue) | data | container | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | REDIS_SOURCE |
| redpanda | Redpanda (Kafka API streaming) | data | container | all, data-eng | REDPANDA_SOURCE |
| searxng | SearXNG (privacy metasearch) | media | container | all, gen-ai-eng, gen-ai-rag | SEARXNG_SOURCE |
| spark | Apache Spark (standalone cluster) | data | container | all, data-eng, ml-eng | SPARK_SOURCE |
| speaches | Speaches (unified TTS + STT) | media | container | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - |
| stt-provider | stt-provider | aggregate | doc-only | all, gen-ai-creative, gen-ai-eng | - |
| supabase | Supabase (db, auth, api, storage, realtime, studio, meta) | data | container | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | SUPABASE_DB_SOURCE, SUPABASE_DB_INIT_SOURCE, SUPABASE_META_SOURCE, SUPABASE_STORAGE_SOURCE, SUPABASE_AUTH_SOURCE, SUPABASE_API_SOURCE, SUPABASE_REALTIME_SOURCE, SUPABASE_STUDIO_SOURCE |
| supavisor | Supavisor (Postgres transaction pooler) | data | container | all, data-eng, gen-ai-eng, gen-ai-rag, ml-eng | SUPAVISOR_SOURCE |
| tei-reranker | TEI Reranker (mxbai-rerank-base-v1) | llm | container | all, gen-ai-rag, ml-eng | TEI_RERANKER_SOURCE |
| tempo | Tempo (distributed trace store) | infra | container | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | TEMPO_SOURCE |
| tika | Apache Tika (fallback extractor) | media | container | all, gen-ai-eng, gen-ai-rag | TIKA_SOURCE |
| trino | Trino | data | container | all, data-eng | TRINO_SOURCE |
| tts-provider | TTS provider (text-to-speech engine selector) | media | virtual | all, gen-ai-creative, gen-ai-eng | TTS_PROVIDER_SOURCE |
| verba | Verba (archived Weaviate RAG UI) | apps | container | all, gen-ai-rag | VERBA_SOURCE |
| weaviate | Weaviate (vector database) | data | container | all, data-eng, gen-ai-rag | WEAVIATE_SOURCE |
| zeppelin | Apache Zeppelin (Spark-first notebook) | apps | container | all, data-eng, ml-eng | ZEPPELIN_SOURCE |

## 3. SOURCE Surface Summary

| SOURCE | Service | Default | Values |
| --- | --- | --- | --- |
| AIRFLOW_SOURCE | airflow | disabled | container, disabled |
| ASSET_BAKER_SOURCE | asset-baker | disabled | container-cpu, disabled |
| ASSET_WORKER_SOURCE | asset-worker | disabled | container, disabled |
| BACKEND_SOURCE | backend | container | - |
| BACKUP_SOURCE | backup | disabled | container, disabled |
| BLENDER_MCP_SOURCE | blender-mcp | disabled | localhost, disabled |
| CELERY_SOURCE | celery | disabled | container, disabled |
| CLOUD_OPENAI_SOURCE | cloud-providers | disabled | enabled, disabled |
| CLOUD_ANTHROPIC_SOURCE | cloud-providers | disabled | enabled, disabled |
| CLOUD_OPENROUTER_SOURCE | cloud-providers | disabled | enabled, disabled |
| CLOUDFLARED_SOURCE | cloudflared | disabled | container, disabled |
| COMFYUI_SOURCE | comfyui | container-cpu | container-cpu, container-gpu, localhost, managed-localhost-mps, disabled |
| CRAWL4AI_SOURCE | crawl4ai | disabled | container, disabled |
| DOC_PROCESSOR_SOURCE | docling | disabled | disabled, docling-localhost, docling-container-gpu |
| FAL_SOURCE | fal | disabled | enabled, disabled |
| GRAFANA_SOURCE | grafana | disabled | container, disabled |
| HERMES_SOURCE | hermes | container | container, localhost, disabled |
| ICEBERG_REST_SOURCE | iceberg-rest | disabled | container, disabled |
| JENKINS_SOURCE | jenkins | disabled | container, disabled |
| JUPYTERHUB_SOURCE | jupyterhub | container | container, disabled |
| KONG_API_GATEWAY_SOURCE | kong | container | container |
| LABEL_STUDIO_SOURCE | label-studio | disabled | container, disabled |
| LANGFUSE_SOURCE | langfuse | disabled | container, disabled |
| LIGHTRAG_SOURCE | lightrag | disabled | container, localhost, disabled |
| LITELLM_SOURCE | litellm | container | container |
| LLM_GRAPH_BUILDER_SOURCE | llm-graph-builder | disabled | container, disabled |
| LOCAL_DEEP_RESEARCHER_SOURCE | local-deep-researcher | container | container, disabled |
| LOKI_SOURCE | loki | disabled | container, disabled |
| MCP_SERVERS_SOURCE | mcp-servers | disabled | container, disabled |
| MINIO_SOURCE | minio | container | container, disabled |
| MLFLOW_SOURCE | mlflow | disabled | container, disabled |
| N8N_SOURCE | n8n | container | container, disabled |
| NEO4J_GRAPH_DB_SOURCE | neo4j | container | container, localhost, disabled |
| LLM_PROVIDER_SOURCE | ollama | ollama-container-cpu | ollama-container-cpu, ollama-container-gpu, ollama-localhost, none |
| OPEN_WEB_UI_SOURCE | open-webui | container | container, disabled |
| OPENCLAW_SOURCE | openclaw | disabled | disabled, container, localhost |
| OTEL_COLLECTOR_SOURCE | otel-collector | disabled | container, disabled |
| STT_PROVIDER_SOURCE | parakeet | speaches-container-cpu | speaches-container-cpu, speaches-container-gpu, parakeet-container-gpu, parakeet-localhost, whisper-cpp-localhost, disabled |
| PROMETHEUS_SOURCE | prometheus | disabled | container, disabled |
| RAY_SOURCE | ray | disabled | ray-container-cpu, ray-container-gpu, disabled |
| REDIS_SOURCE | redis | container | container |
| REDPANDA_SOURCE | redpanda | disabled | container, disabled |
| SEARXNG_SOURCE | searxng | container | container, disabled |
| SPARK_SOURCE | spark | disabled | container, disabled |
| SUPABASE_DB_SOURCE | supabase | container | container |
| SUPABASE_DB_INIT_SOURCE | supabase | container | container, disabled |
| SUPABASE_META_SOURCE | supabase | container | container, disabled |
| SUPABASE_STORAGE_SOURCE | supabase | container | container, disabled |
| SUPABASE_AUTH_SOURCE | supabase | container | container, disabled |
| SUPABASE_API_SOURCE | supabase | container | container, disabled |
| SUPABASE_REALTIME_SOURCE | supabase | container | container, disabled |
| SUPABASE_STUDIO_SOURCE | supabase | container | container, disabled |
| SUPAVISOR_SOURCE | supavisor | disabled | container, disabled |
| TEI_RERANKER_SOURCE | tei-reranker | disabled | container-cpu, container-gpu, localhost, disabled |
| TEMPO_SOURCE | tempo | disabled | container, disabled |
| TIKA_SOURCE | tika | disabled | container, tika-localhost, disabled |
| TRINO_SOURCE | trino | disabled | container, disabled |
| TTS_PROVIDER_SOURCE | tts-provider | speaches-container-cpu | speaches-container-cpu, speaches-container-gpu, chatterbox-container-gpu, chatterbox-localhost, disabled |
| VERBA_SOURCE | verba | disabled | container, disabled |
| WEAVIATE_SOURCE | weaviate | container | container, localhost, disabled |
| ZEPPELIN_SOURCE | zeppelin | disabled | container, disabled |

## 4. Dependency Summary

| Service | Required | Optional | Runtime Calls |
| --- | --- | --- | --- |
| airflow | supabase, litellm, redis | spark, minio, iceberg-rest, redpanda, weaviate, neo4j | supabase, spark, redpanda, minio, iceberg-rest, litellm, weaviate, neo4j, redis |
| asset-baker | minio | backend, comfyui, fal, blender-mcp, asset-worker | minio |
| asset-worker | minio | backend, comfyui, fal, blender-mcp | minio |
| backend | supabase, redis, litellm | weaviate, kong, celery, supavisor | supabase, weaviate, litellm, comfyui, fal, n8n, ray, local-deep-researcher, celery, supavisor, tika, lightrag, minio, otel-collector |
| backup | supabase, minio | - | supabase, minio |
| blender-mcp | - | - | - |
| celery | redis, backend, supabase, litellm | weaviate, supavisor | redis, supabase, litellm, weaviate, supavisor |
| chatterbox | tts-provider | - | - |
| cloud-providers | litellm | - | - |
| cloudflared | kong | - | kong |
| comfyui | supabase, litellm, ollama | - | supabase |
| crawl4ai | - | local-deep-researcher, n8n, backend, weaviate | - |
| doc-processor | - | - | - |
| docling | - | - | - |
| fal | - | - | - |
| globals | - | - | - |
| grafana | prometheus, supabase, kong, ray | - | prometheus, tempo, loki |
| hermes | litellm | - | litellm, stt-provider, tts-provider, comfyui, searxng, airflow, lightrag |
| iceberg-rest | minio, supabase | - | minio, supabase |
| jenkins | minio | airflow, spark | minio |
| jupyterhub | supabase, redis, litellm | minio, iceberg-rest, spark, redpanda | litellm, hermes, weaviate, neo4j, supabase, ray, spark, redpanda, comfyui, n8n, backend, searxng, minio, iceberg-rest, mlflow, label-studio |
| kong | supabase, redis | - | backend, open-webui, jupyterhub, n8n, hermes, openclaw, local-deep-researcher, minio, supabase, weaviate, neo4j, comfyui, searxng, stt-provider, tts-provider, doc-processor, litellm, ollama, airflow, spark, zeppelin, lightrag, tei-reranker, verba, grafana, prometheus, ray |
| label-studio | supabase, minio | jupyterhub, mlflow | supabase, minio |
| langfuse | supabase, redis, minio, litellm, kong, ray | - | supabase, redis, minio, litellm |
| lightrag | litellm | supabase, neo4j, redis, docling | litellm, supabase, neo4j, redis, docling |
| litellm | supabase, redis | - | supabase, redis, ollama, cloud-providers, hermes, lightrag, otel-collector |
| llm-graph-builder | neo4j, litellm, kong | minio, docling | neo4j, litellm, minio, docling |
| local-deep-researcher | searxng, litellm | crawl4ai | litellm, searxng, crawl4ai |
| loki | kong, ray | - | - |
| mcp-servers | supabase, neo4j, searxng | - | supabase, neo4j, searxng |
| minio | supabase | - | - |
| mlflow | supabase, minio | jupyterhub | supabase, minio |
| multi2vec-clip | - | - | - |
| n8n | supabase, redis, litellm | supavisor | supabase, redis, weaviate, backend, doc-processor, tika, hermes, litellm, stt-provider, tts-provider, searxng, lightrag, crawl4ai, supavisor |
| neo4j | supabase | - | - |
| ollama | supabase, litellm | - | - |
| open-webui | supabase, redis, litellm | hermes | litellm, supabase, redis, backend, comfyui, stt-provider, tts-provider, local-deep-researcher |
| openclaw | litellm | - | litellm |
| otel-collector | tempo | loki | tempo |
| parakeet | litellm | - | - |
| prometheus | supabase, redis, kong, ray | - | kong, litellm, backend, n8n, weaviate, minio, supabase, redis, grafana |
| ray | supabase, redis | - | - |
| redis | supabase | - | - |
| redpanda | - | spark, jupyterhub, zeppelin, airflow, iceberg-rest, minio | - |
| searxng | redis | - | - |
| spark | minio | supabase, iceberg-rest, redpanda | minio, iceberg-rest, redpanda |
| speaches | parakeet, tts-provider | - | - |
| stt-provider | - | - | - |
| supabase | - | - | - |
| supavisor | supabase | backend, n8n, celery | supabase |
| tei-reranker | - | - | - |
| tempo | kong, ray | - | - |
| tika | - | backend, n8n | - |
| trino | minio, iceberg-rest | spark, zeppelin, jupyterhub, airflow | iceberg-rest, minio |
| tts-provider | litellm | - | - |
| verba | weaviate, litellm, kong | docling, open-webui, jupyterhub | weaviate, litellm |
| weaviate | supabase, litellm | - | litellm, multi2vec-clip |
| zeppelin | spark | supabase, minio, iceberg-rest, redpanda, trino | spark, supabase, minio, iceberg-rest, redpanda, trino |

## 5. Krea 2 Curated Bundles

Atlas provides separate Krea 2 Turbo and Krea 2 RAW BF16 selections. Each logical bundle uses the same pinned Qwen3-VL 4B text encoder and Qwen-Image VAE; the generated download plan retrieves those shared target files once when both bundles are selected.

### 5.1 Bundle Matrix

| Bundle | Catalog ID | Precision | Disk | RAM | VRAM |
| --- | --- | --- | --- | --- | --- |
| Krea 2 Turbo | `krea2-turbo-bf16` | bf16 | 35.413 GB | 32 GB | 32 GB |
| Krea 2 RAW | `krea2-raw-bf16` | bf16 | 35.413 GB | 32 GB | 32 GB |

### 5.2 Pinned Artifacts

| Bundle | Role | Target | Bytes | SHA-256 |
| --- | --- | --- | --- | --- |
| Krea 2 Turbo | diffusion | `diffusion_models/krea2_turbo_bf16.safetensors` | 26,283,332,608 | `78bbf8f4165eda19cea3cb06c78089221932a39e2eed8af9da741f942c47ffb3` |
| Krea 2 Turbo | text_encoder | `text_encoders/qwen3vl_4b_bf16.safetensors` | 8,875,719,384 | `36f3ff447ef59201722e8f9ce6020c9819fdcfba6aa2608c4e09b1c0ce114e34` |
| Krea 2 Turbo | vae | `vae/qwen_image_vae.safetensors` | 253,806,246 | `a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f` |
| Krea 2 RAW | diffusion | `diffusion_models/krea2_raw_bf16.safetensors` | 26,283,332,608 | `f99bb0ff8e362b77342bc4994e0c50906fe7ef7074864b181b7d48d2fa6d03d7` |
| Krea 2 RAW | text_encoder | `text_encoders/qwen3vl_4b_bf16.safetensors` | 8,875,719,384 | `36f3ff447ef59201722e8f9ce6020c9819fdcfba6aa2608c4e09b1c0ce114e34` |
| Krea 2 RAW | vae | `vae/qwen_image_vae.safetensors` | 253,806,246 | `a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f` |

Every artifact URL is pinned to Hugging Face revision `8038ce89b91b042141541ad0fa51b985ca262c5f`.

### 5.3 Workflow

The API-ready example is `services/comfyui/workflows/krea2-turbo-api.json`. It uses only ComfyUI core nodes with `CLIPLoader` type `krea2`, 8 steps, CFG 1.0, Euler sampling, the simple scheduler, `ConditioningZeroOut`, and a 1024 by 1024 latent. Atlas pins ComfyUI `v0.27.0`, which includes the core Krea 2 support introduced in `v0.26.0`.

### 5.4 License And Operations

Model weights use the [Krea 2 Community License](https://huggingface.co/krea/Krea-2-Turbo/blob/1161245028ef398cd0a951101b2bbf486464f841/LICENSE.pdf). Operators must review the authoritative license before deployment:

- Enterprise license required at or above $1,000,000 USD ($1M) annual revenue for commercial use.
- Reasonable and appropriate content filtering is required for deployments.
- The license does not state a seat-count threshold; do not apply the previously reported 50-seat limit.

The 1024-square generation check is an opt-in `live` pytest and is not part of generic CI.

Container sources default `COMFYUI_MEMORY_LIMIT` to a 40 GB hard ceiling. Docker does not reserve that memory; smaller workloads consume only what they need, while Krea 2 can exceed the former 4 GB limit.

## 6. Managed Apple-Silicon / Metal (MPS) Source

`COMFYUI_SOURCE=managed-localhost-mps` is a managed host source for Apple Silicon Macs. Docker Desktop on macOS cannot pass Metal into a Linux container, so Atlas installs and runs a native ComfyUI process on the host and points `COMFYUI_ENDPOINT` at it. Every downstream consumer — backend, Open WebUI, JupyterHub, and Celery — resolves the identical `COMFYUI_ENDPOINT` contract, so nothing downstream depends on whether the source is a container or a host process. One process runs per host: a single instance already saturates the Apple Silicon GPU, and a second is net-negative.

### 6.1 What Atlas Manages

Atlas checks out a pinned ComfyUI ref (`COMFYUI_MPS_REF`, default `v0.27.0`) into an Atlas-owned state directory (`COMFYUI_MPS_STATE_DIR`, default `~/.atlas/comfyui-mps`) with a dedicated venv holding Metal-enabled Torch. Install is idempotent — only the first run downloads Torch. The process reuses the existing host models directory (`COMFYUI_MPS_MODELS_PATH`, default `~/Documents/ComfyUI/models`) through a generated `extra_model_paths.yaml`, so weights are never duplicated. It listens on a fixed loopback port (`COMFYUI_MPS_LOCALHOST_PORT`, default `8188`) with PID, log, and status files under the state directory, and refuses to start if the port is already taken.

### 6.2 Lifecycle And Preflight

A normal `./start.sh` with this source runs preflight, install, and start automatically before Compose; `./stop.sh` stops the host process. Explicit control is available headless:

```bash
./start.sh comfyui-mps preflight
./start.sh comfyui-mps install [--update]
./start.sh comfyui-mps start
./start.sh comfyui-mps status
./start.sh comfyui-mps health
./start.sh comfyui-mps stop
./start.sh comfyui-mps remove
```

The read-only preflight checks OS (macOS) and arch (arm64) — a hard fail elsewhere — plus git/python3 presence, unified-memory headroom against `COMFYUI_MPS_MIN_MEMORY_GB` (default `16`), Torch/MPS availability once the venv exists, and per-model precision: `fp8`/`fp8-scaled` weights crash on MPS and warn with a "use a BF16 variant" hint. The same preflight runs as a CI-safe `comfyui-mps` doctor check.

### 6.3 Cold/Warm Health, Unsupported Hosts, Upgrades, Logs, Removal

Weights load lazily on the first request, so a freshly launched process is reachable but cold; `health` reports reachability and the compute device (`mps` when `/system_stats` shows a non-CPU device). On non-Apple hosts (Linux, Intel Macs, Windows) the preflight fails with an explicit unsupported-host message and install refuses — Atlas never claims a Linux container is Metal-capable. Upgrade or roll back by setting `COMFYUI_MPS_REF` and running `comfyui-mps install --update` then `stop`/`start`. Logs are at `${COMFYUI_MPS_STATE_DIR}/comfyui-mps.log`. `comfyui-mps remove` stops the process and deletes the state directory while leaving the reused host models directory untouched. n8n receives no `COMFYUI_ENDPOINT` injection for any ComfyUI source and is documented as excluded here; the managed source is consumed identically to every other source by the consumers that do receive the endpoint.

## 7. Hunyuan3D-2 Native Image to 3D

Atlas curates the ComfyUI-core **native** Hunyuan3D-2 single-image shape generator. Unlike TRELLIS/Pixal3D (which need CUDA sparse kernels), Hunyuan3D-2's DiT is pure Torch, so it runs on Apple-Silicon **MPS** through the managed source. The entry is a large optional download — it is never `essential`, so it stages only when explicitly selected (`COMFYUI_USER_MODELS=hunyuan3d-2`).

Native support is **shape-only**: geometry generation with no texture, PBR, or material stage (that path is CUDA-bound and intentionally excluded from this bundle).

### 7.1 Inventory

| Model | Catalog ID | Precision | Disk | RAM | VRAM |
| --- | --- | --- | --- | --- | --- |
| Hunyuan3D-2 | `hunyuan3d-2` | fp16 | 4.928 GB | 16 GB | 8 GB |

### 7.2 Pinned Artifact

| Role | Target | Bytes | SHA-256 |
| --- | --- | --- | --- |
| dit checkpoint | `checkpoints/hunyuan3d-dit-v2.safetensors` | 4,928,151,562 | `360bc281fc956d4acac0c3d36d5ec0ebf8cdddbf4b8892e894d12419388d479b` |

The URL and license are pinned to Hugging Face revision `9cd649ba6913f7a852e3286bad86bfa9a2d83dcf`. The dit checkpoint is a `mesh_model` but its `target_dir` overrides to `checkpoints` so `ImageOnlyCheckpointLoader` resolves it.

### 7.3 Workflow

The API-ready example is `services/comfyui/workflows/hunyuan3d-2-image-to-glb-api.json`. It uses only ComfyUI-core native nodes — `ImageOnlyCheckpointLoader` → `CLIPVisionEncode` → `Hunyuan3Dv2Conditioning` → `KSampler` → `VAEDecodeHunyuan3D` → `VoxelToMeshBasic` → `SaveGLB` — so no custom node and no CUDA sparse kernels are required. The terminal `SaveGLB` writes a shape-only `.glb`. A marked `live` MPS smoke (opt-in, `ATLAS_COMFYUI_LIVE_ENDPOINT`) renders a real mesh and validates the GLB container; it is not part of generic CI.

### 7.4 License

Model weights use the [Tencent Hunyuan Community License](https://huggingface.co/tencent/Hunyuan3D-2/blob/9cd649ba6913f7a852e3286bad86bfa9a2d83dcf/LICENSE.txt). Operators must review the authoritative license before deployment:

- Territory-restricted — not licensed for use in the European Union, the United Kingdom, or South Korea.
- Products or services with over 100 million monthly active users require a separate license from Tencent.
- Use is subject to the Tencent Hunyuan Community License Agreement and its Acceptable Use Policy.
