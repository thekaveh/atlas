# Services

## 1. Service Catalog

| Service | Category | Tracks | SOURCE | Values | Dependencies |
| --- | --- | --- | --- | --- | --- |
| airflow | agents | all, data-eng | AIRFLOW_SOURCE | container, disabled | supabase, litellm, redis |
| backend | apps | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | BACKEND_SOURCE | - | supabase, redis, litellm |
| backup | infra | all | BACKUP_SOURCE | container, disabled | supabase, minio |
| blender-mcp | media | all, gen-ai-creative | BLENDER_MCP_SOURCE | localhost, disabled | - |
| celery | agents | all, gen-ai-eng, gen-ai-rag | CELERY_SOURCE | container, disabled | redis, backend, supabase, litellm |
| chatterbox | media | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | none | - | tts-provider |
| cloud-providers | llm | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | CLOUD_OPENAI_SOURCE | enabled, disabled | litellm |
| cloudflared | infra | all | CLOUDFLARED_SOURCE | container, disabled | kong |
| comfyui | media | all, gen-ai-creative, gen-ai-eng | COMFYUI_SOURCE | container-cpu, container-gpu, localhost, disabled | supabase, litellm, ollama |
| crawl4ai | media | all, gen-ai-rag | CRAWL4AI_SOURCE | container, disabled | - |
| doc-processor | aggregate | all, gen-ai-creative, gen-ai-rag | none | - | - |
| docling | media | all | DOC_PROCESSOR_SOURCE | disabled, docling-localhost, docling-container-gpu | - |
| globals | infra | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | none | - | - |
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
| multi2vec-clip | aggregate | all, gen-ai-creative | none | - | - |
| n8n | agents | all, gen-ai-eng | N8N_SOURCE | container, disabled | supabase, redis, litellm |
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
| speaches | media | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | none | - | parakeet, tts-provider |
| stt-provider | aggregate | all, gen-ai-creative, gen-ai-eng | none | - | - |
| supabase | data | all, data-eng, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng, trading | SUPABASE_DB_SOURCE | container | - |
| supavisor | data | all, data-eng, gen-ai-eng, gen-ai-rag, ml-eng | SUPAVISOR_SOURCE | container, disabled | supabase |
| tei-reranker | llm | all, gen-ai-rag, ml-eng | TEI_RERANKER_SOURCE | container-cpu, container-gpu, localhost, disabled | - |
| tempo | infra | all, gen-ai-creative, gen-ai-eng, gen-ai-rag, ml-eng | TEMPO_SOURCE | container, disabled | kong, ray |
| tika | media | all, gen-ai-eng, gen-ai-rag | TIKA_SOURCE | container, tika-localhost, disabled | - |
| trino | data | all, data-eng | TRINO_SOURCE | container, disabled | minio, iceberg-rest |
| tts-provider | media | all, gen-ai-creative, gen-ai-eng | TTS_PROVIDER_SOURCE | speaches-container-cpu, speaches-container-gpu, chatterbox-container-gpu, chatterbox-localhost, disabled | litellm |
| verba | apps | all, gen-ai-rag | VERBA_SOURCE | container, disabled | weaviate, litellm, kong |
| weaviate | data | all, data-eng, gen-ai-rag | WEAVIATE_SOURCE | container, localhost, disabled | supabase, litellm |
| zeppelin | apps | all, data-eng, ml-eng | ZEPPELIN_SOURCE | container, disabled | spark |
