# 10.4. Ports And Routes

## 1. Generated Ports And Routes Matrix

Generated summary of model-backed service port variables and Kong aliases. Use the deployment route reference for browser-facing hostname details and route behavior.

| Service | Category | Port Variables | Kong Aliases | Route Docs |
| --- | --- | --- | --- | --- |
| airflow | agents | `AIRFLOW_PORT` | `airflow.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| asset-baker | media | `ASSET_BAKER_PORT` | `asset-baker.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| asset-worker | media | `ASSET_WORKER_PORT` | `asset-worker.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| backend | apps | `BACKEND_PORT` | `api.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| blender-mcp | media | `BLENDER_MCP_LOCALHOST_PORT` | `-` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| celery | agents | `FLOWER_PORT` | `flower.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| chatterbox | media | `CHATTERBOX_PORT`, `CHATTERBOX_LOCALHOST_PORT` | `-` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| comfyui | media | `COMFYUI_PORT`, `COMFYUI_LOCALHOST_PORT`, `COMFYUI_MPS_LOCALHOST_PORT` | `comfyui.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| crawl4ai | media | `CRAWL4AI_PORT` | `crawl4ai.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| docling | media | `DOC_PROCESSOR_PORT`, `DOCLING_LOCALHOST_PORT` | `docling.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| globals | infra | `BASE_PORT` | `-` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| grafana | infra | `GRAFANA_PORT` | `grafana.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| hermes | agents | `HERMES_API_PORT`, `HERMES_DASHBOARD_PORT`, `HERMES_LOCALHOST_PORT`, `HERMES_LOCALHOST_DASHBOARD_PORT` | `hermes.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| iceberg-rest | data | `ICEBERG_REST_PORT` | `-` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| jenkins | apps | `JENKINS_PORT` | `jenkins.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| jupyterhub | apps | `JUPYTERHUB_PORT` | `jupyter.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| kong | infra | `KONG_HTTP_PORT`, `KONG_HTTPS_PORT` | `-` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| label-studio | apps | `LABEL_STUDIO_PORT` | `label-studio.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| langfuse | infra | `LANGFUSE_PORT` | `langfuse.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| lightrag | agents | `LIGHTRAG_API_PORT`, `LIGHTRAG_LOCALHOST_PORT` | `lightrag.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| litellm | llm | `LITELLM_PORT` | `litellm.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| llm-graph-builder | apps | `LLM_GRAPH_BUILDER_PORT` | `graphbuilder.localhost`, `graphbuilder-api.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| local-deep-researcher | apps | `LOCAL_DEEP_RESEARCHER_PORT` | `research.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| mcp-servers | agents | `MCP_SERVERS_PORT` | `mcp.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| minio | data | `MINIO_PORT`, `MINIO_CONSOLE_PORT` | `minio.localhost`, `s3.minio.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| mlflow | apps | `MLFLOW_PORT` | `mlflow.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| n8n | agents | `N8N_PORT` | `n8n.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| neo4j | data | `GRAPH_DB_PORT`, `GRAPH_DB_DASHBOARD_PORT`, `NEO4J_LOCALHOST_HTTP_PORT`, `NEO4J_LOCALHOST_BOLT_PORT` | `graph.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| ollama | llm | `OLLAMA_LOCALHOST_PORT` | `ollama.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| open-webui | apps | `OPEN_WEB_UI_PORT` | `chat.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| openclaw | agents | `OPENCLAW_GATEWAY_PORT`, `OPENCLAW_BRIDGE_PORT`, `OPENCLAW_LOCALHOST_PORT` | `openclaw.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| parakeet | media | `STT_PROVIDER_PORT`, `PARAKEET_LOCALHOST_PORT`, `WHISPER_CPP_LOCALHOST_PORT` | `stt.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| prometheus | infra | `PROMETHEUS_PORT`, `NODE_EXPORTER_PORT`, `CADVISOR_PORT` | `prometheus.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| ray | infra | `RAY_DASHBOARD_PORT`, `RAY_GCS_PORT`, `RAY_CLIENT_PORT` | `ray.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| redis | data | `REDIS_PORT`, `REDIS_EXPORTER_PORT` | `-` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| redpanda | data | `REDPANDA_KAFKA_PORT`, `REDPANDA_CONSOLE_PORT` | `redpanda.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| searxng | media | `SEARXNG_PORT` | `search.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| spark | data | `SPARK_MASTER_UI_PORT`, `SPARK_HISTORY_PORT` | `spark.localhost`, `spark-history.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| speaches | media | `SPEACHES_PORT` | `-` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| supabase | data | `SUPABASE_DB_PORT`, `POSTGRES_EXPORTER_PORT`, `SUPABASE_META_PORT`, `SUPABASE_STORAGE_PORT`, `SUPABASE_AUTH_PORT`, `SUPABASE_API_PORT`, `SUPABASE_REALTIME_PORT`, `SUPABASE_STUDIO_PORT` | `supabase-studio.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| tei-reranker | llm | `TEI_RERANKER_PORT`, `TEI_RERANKER_LOCALHOST_PORT` | `rerank.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| tika | media | `TIKA_PORT`, `TIKA_LOCALHOST_PORT` | `tika.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| trino | data | `TRINO_PORT` | `trino.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| tts-provider | media | `TTS_PROVIDER_PORT` | `tts.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| verba | apps | `VERBA_PORT` | `verba.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| vllm-metal | llm | `VLLM_METAL_LOCALHOST_PORT` | `-` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| weaviate | data | `WEAVIATE_PORT`, `WEAVIATE_GRPC_PORT`, `WEAVIATE_LOCALHOST_PORT` | `weaviate.localhost` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
| zeppelin | apps | `ZEPPELIN_PORT` | `-` | [Deployment route reference](../deployment/ports-and-routes.md#2-kong-hostnames) |
