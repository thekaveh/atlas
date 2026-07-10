# Services

## 1. Service Catalog

| Service | Category | Tracks | SOURCE | Values | Dependencies |
| --- | --- | --- | --- | --- | --- |
| airflow | agents | all, data-eng | AIRFLOW_SOURCE | container, disabled | supabase, litellm, redis |
| asset-worker | media | all, gen-ai-creative | ASSET_WORKER_SOURCE | container, disabled | minio |
| backend | apps | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | BACKEND_SOURCE | - | supabase, redis, litellm |
| backup | infra | all | BACKUP_SOURCE | container, disabled | supabase, minio |
| blender-mcp | media | all, gen-ai-creative | BLENDER_MCP_SOURCE | localhost, disabled | - |
| celery | agents | all, gen-ai-eng, gen-ai-rag | CELERY_SOURCE | container, disabled | redis, backend, supabase, litellm |
| chatterbox | media | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | - | - | tts-provider |
| cloud-providers | llm | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | CLOUD_OPENAI_SOURCE, CLOUD_ANTHROPIC_SOURCE, CLOUD_OPENROUTER_SOURCE | enabled, disabled | litellm |
| cloudflared | infra | all | CLOUDFLARED_SOURCE | container, disabled | kong |
| comfyui | media | all, gen-ai-creative, gen-ai-eng | COMFYUI_SOURCE | container-cpu, container-gpu, localhost, disabled | supabase, litellm, ollama |
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
| ASSET_WORKER_SOURCE | asset-worker | disabled | container, disabled |
| BACKEND_SOURCE | backend | container | - |
| BACKUP_SOURCE | backup | disabled | container, disabled |
| BLENDER_MCP_SOURCE | blender-mcp | disabled | localhost, disabled |
| CELERY_SOURCE | celery | disabled | container, disabled |
| CLOUD_OPENAI_SOURCE | cloud-providers | disabled | enabled, disabled |
| CLOUD_ANTHROPIC_SOURCE | cloud-providers | disabled | enabled, disabled |
| CLOUD_OPENROUTER_SOURCE | cloud-providers | disabled | enabled, disabled |
| CLOUDFLARED_SOURCE | cloudflared | disabled | container, disabled |
| COMFYUI_SOURCE | comfyui | container-cpu | container-cpu, container-gpu, localhost, disabled |
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
| asset-worker | minio | backend, comfyui, fal, blender-mcp | minio |
| backend | supabase, redis, litellm | weaviate, kong, celery, supavisor | supabase, weaviate, litellm, comfyui, fal, n8n, ray, local-deep-researcher, celery, supavisor, tika, otel-collector |
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
