# 10.4. Ports and Routes Reference

## 1. Generated Ports and Routes Matrix

Generated summary of model-backed service port variables and Kong aliases. For browser-facing hostnames and route behavior, see the [Ports and Routes](../operations/ports-and-routes.md#2-kong-hostnames).

| Service | Category | Port Variables | Kong Aliases |
| --- | --- | --- | --- |
| airflow | agents | `AIRFLOW_PORT` | `airflow.localhost` |
| asset-baker | media | `ASSET_BAKER_PORT` | `asset-baker.localhost` |
| asset-worker | media | `ASSET_WORKER_PORT` | `asset-worker.localhost` |
| backend | apps | `BACKEND_PORT` | `api.localhost` |
| blender-mcp | media | `BLENDER_MCP_LOCALHOST_PORT` | `-` |
| celery | agents | `FLOWER_PORT` | `flower.localhost` |
| chatterbox | media | `CHATTERBOX_PORT`, `CHATTERBOX_LOCALHOST_PORT` | `-` |
| comfyui | media | `COMFYUI_PORT`, `COMFYUI_LOCALHOST_PORT`, `COMFYUI_MPS_LOCALHOST_PORT` | `comfyui.localhost` |
| crawl4ai | media | `CRAWL4AI_PORT` | `crawl4ai.localhost` |
| docling | media | `DOC_PROCESSOR_PORT`, `DOCLING_LOCALHOST_PORT` | `docling.localhost` |
| globals | infra | `BASE_PORT` | `-` |
| grafana | infra | `GRAFANA_PORT` | `grafana.localhost` |
| hermes | agents | `HERMES_API_PORT`, `HERMES_DASHBOARD_PORT`, `HERMES_LOCALHOST_PORT`, `HERMES_LOCALHOST_DASHBOARD_PORT` | `hermes.localhost` |
| iceberg-rest | data | `ICEBERG_REST_PORT` | `-` |
| jenkins | apps | `JENKINS_PORT` | `jenkins.localhost` |
| jupyterhub | apps | `JUPYTERHUB_PORT` | `jupyter.localhost` |
| kong | infra | `KONG_HTTP_PORT`, `KONG_HTTPS_PORT` | `-` |
| label-studio | apps | `LABEL_STUDIO_PORT` | `label-studio.localhost` |
| langfuse | infra | `LANGFUSE_PORT` | `langfuse.localhost` |
| lightrag | agents | `LIGHTRAG_API_PORT`, `LIGHTRAG_LOCALHOST_PORT` | `lightrag.localhost` |
| litellm | llm | `LITELLM_PORT` | `litellm.localhost` |
| llm-graph-builder | apps | `LLM_GRAPH_BUILDER_PORT` | `graphbuilder.localhost`, `graphbuilder-api.localhost` |
| local-deep-researcher | apps | `LOCAL_DEEP_RESEARCHER_PORT` | `research.localhost` |
| mcp-servers | agents | `MCP_SERVERS_PORT` | `mcp.localhost` |
| minio | data | `MINIO_PORT`, `MINIO_CONSOLE_PORT` | `minio.localhost`, `s3.minio.localhost` |
| mlflow | apps | `MLFLOW_PORT` | `mlflow.localhost` |
| n8n | agents | `N8N_PORT` | `n8n.localhost` |
| neo4j | data | `GRAPH_DB_PORT`, `GRAPH_DB_DASHBOARD_PORT`, `NEO4J_LOCALHOST_HTTP_PORT`, `NEO4J_LOCALHOST_BOLT_PORT` | `graph.localhost` |
| ollama | llm | `OLLAMA_LOCALHOST_PORT` | `ollama.localhost` |
| open-webui | apps | `OPEN_WEB_UI_PORT` | `chat.localhost` |
| openclaw | agents | `OPENCLAW_GATEWAY_PORT`, `OPENCLAW_BRIDGE_PORT`, `OPENCLAW_LOCALHOST_PORT` | `openclaw.localhost` |
| parakeet | media | `STT_PROVIDER_PORT`, `PARAKEET_LOCALHOST_PORT`, `WHISPER_CPP_LOCALHOST_PORT` | `stt.localhost` |
| prometheus | infra | `PROMETHEUS_PORT`, `NODE_EXPORTER_PORT`, `CADVISOR_PORT` | `prometheus.localhost` |
| ray | infra | `RAY_DASHBOARD_PORT`, `RAY_GCS_PORT`, `RAY_CLIENT_PORT` | `ray.localhost` |
| redis | data | `REDIS_PORT`, `REDIS_EXPORTER_PORT` | `-` |
| redpanda | data | `REDPANDA_KAFKA_PORT`, `REDPANDA_CONSOLE_PORT` | `redpanda.localhost` |
| searxng | media | `SEARXNG_PORT` | `search.localhost` |
| spark | data | `SPARK_MASTER_UI_PORT`, `SPARK_HISTORY_PORT` | `spark.localhost`, `spark-history.localhost` |
| speaches | media | `SPEACHES_PORT` | `-` |
| supabase | data | `SUPABASE_DB_PORT`, `POSTGRES_EXPORTER_PORT`, `SUPABASE_META_PORT`, `SUPABASE_STORAGE_PORT`, `SUPABASE_AUTH_PORT`, `SUPABASE_API_PORT`, `SUPABASE_REALTIME_PORT`, `SUPABASE_STUDIO_PORT` | `supabase-studio.localhost` |
| tei-reranker | llm | `TEI_RERANKER_PORT`, `TEI_RERANKER_LOCALHOST_PORT` | `rerank.localhost` |
| tika | media | `TIKA_PORT`, `TIKA_LOCALHOST_PORT` | `tika.localhost` |
| trino | data | `TRINO_PORT` | `trino.localhost` |
| trueforge | agents | `TRUEFORGE_PORT` | `trueforge.localhost` |
| tts-provider | media | `TTS_PROVIDER_PORT` | `tts.localhost` |
| verba | apps | `VERBA_PORT` | `verba.localhost` |
| vllm-metal | llm | `VLLM_METAL_LOCALHOST_PORT` | `-` |
| weaviate | data | `WEAVIATE_PORT`, `WEAVIATE_GRPC_PORT`, `WEAVIATE_LOCALHOST_PORT` | `weaviate.localhost` |
| zeppelin | apps | `ZEPPELIN_PORT` | `-` |
