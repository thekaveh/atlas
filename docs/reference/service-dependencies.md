# 10.5. Service Dependencies

## 1. Generated Dependency Matrix

The start-order column comes from each manifest's `depends_on.required`. It orders startup and display; some entries only pin a port slot. It does not list runtime requirements. Runtime edges are in the Runtime Calls column.

| Service | Start order (depends_on.required) | Optional | Runtime Calls |
| --- | --- | --- | --- |
| airflow | supabase, litellm, redis | spark, silo, iceberg-rest, redpanda, weaviate, neo4j | supabase, spark, redpanda (optional: an operator-authored DAG; Atlas passes only SPARK_KAFKA_BOOTSTRAP_SERVERS), silo, iceberg-rest, litellm, weaviate (optional: an operator-authored DAG; airflow-init only seeds the Connection), neo4j (optional: an operator-authored DAG; airflow-init only seeds the Connection), redis (optional: an operator-authored DAG; airflow-init only seeds the Connection) |
| asset-baker | silo | backend, comfyui, fal, blender-mcp, asset-worker | silo |
| asset-worker | silo | backend, comfyui, fal, blender-mcp | silo |
| backend | supabase, redis, litellm | weaviate, kong, celery, supavisor | supabase, weaviate, litellm, comfyui, fal, n8n, ray, local-deep-researcher, celery, supavisor, tika, docling, lightrag, tei-reranker, silo, redis, otel-collector, kong, neo4j (planned) |
| backup | supabase | silo | supabase (optional: BACKUP_SOURCE=container; runs on demand, not resident), silo, neo4j, weaviate |
| blender-mcp | - | - | - |
| celery | redis, backend, supabase, litellm | weaviate, supavisor, docling, tika, lightrag, silo, otel-collector | redis, supabase, litellm, weaviate, supavisor, docling, tika, lightrag, silo, otel-collector |
| chatterbox | tts-provider | - | - |
| cloud-providers | litellm | - | - |
| cloudflared | kong | - | kong (optional: public hostnames configured in the Cloudflare dashboard) |
| comfyui | supabase, litellm, ollama | - | - |
| crawl4ai | - | local-deep-researcher, n8n, backend, weaviate | - |
| doc-processor | - | - | - |
| docling | - | - | - |
| docling-lightrag-adapter | - | - | docling (optional: DOC_PROCESSOR_SOURCE=docling-container-gpu or docling-localhost) |
| fal | - | - | - |
| globals | - | - | - |
| grafana | prometheus, supabase, kong, ray | - | prometheus, tempo, loki |
| hermes | litellm | - | litellm, stt-provider, tts-provider, comfyui, searxng, airflow (planned), lightrag (planned) |
| iceberg-rest | silo, supabase | - | silo, supabase |
| jenkins | silo | airflow, spark | silo (optional: an operator-authored Jenkins job; none ships with Atlas) |
| jupyterhub | supabase, redis, litellm | silo, iceberg-rest, spark, mcp-servers, redpanda | litellm, hermes, weaviate, neo4j, supabase, ray, spark, redpanda, redis, comfyui, n8n, backend, searxng, silo, iceberg-rest, mlflow, label-studio, docling, stt-provider, tts-provider, mcp-servers (optional: MCP_SERVERS_SOURCE=container) |
| kong | supabase, redis | - | backend, open-webui, jupyterhub, n8n, hermes, openclaw, local-deep-researcher, silo, supabase, weaviate, neo4j, comfyui, searxng, stt-provider, tts-provider, doc-processor, litellm, ollama, airflow, spark, lightrag, tei-reranker, verba, trino, redpanda, tika, crawl4ai, langfuse, mlflow, label-studio, jenkins, llm-graph-builder, mcp-servers, celery, asset-baker, asset-worker, grafana, prometheus, ray (optional: RAY_SOURCE=ray-container-cpu or ray-container-gpu), trueforge (optional: TRUEFORGE_SOURCE=container) |
| label-studio | supabase, silo | jupyterhub, mlflow | supabase, silo |
| langfuse | supabase, redis, silo, litellm, kong, ray | - | supabase, redis, silo |
| lightrag | litellm | supabase, neo4j, redis, docling | litellm, supabase, neo4j, redis, docling-lightrag-adapter, backend |
| litellm | supabase, redis | - | supabase, redis, ollama, cloud-providers, hermes, lightrag, vllm-metal, fal, tei-reranker, otel-collector, langfuse |
| llm-graph-builder | neo4j, litellm, kong | silo, docling | neo4j, litellm |
| local-deep-researcher | searxng, litellm | crawl4ai | litellm, searxng, crawl4ai |
| loki | kong, ray | - | - |
| mcp-servers | supabase, neo4j, searxng | - | supabase, neo4j, searxng |
| mlflow | supabase, silo | jupyterhub | supabase, silo |
| multi2vec-clip | - | - | - |
| n8n | supabase, redis, litellm | supavisor | supabase, redis, weaviate, backend, doc-processor, tika, hermes, litellm, stt-provider, tts-provider, searxng, lightrag, crawl4ai, supavisor |
| neo4j | supabase | - | - |
| ollama | supabase, litellm | - | - |
| open-webui | supabase, redis, litellm | hermes | litellm, supabase, redis, backend, comfyui, stt-provider, tts-provider, local-deep-researcher |
| openclaw | litellm | - | litellm (optional: openclaw-init sets models.providers.litellm.baseUrl when unset; an operator value is kept) |
| otel-collector | tempo, loki | - | tempo, loki |
| parakeet | litellm | - | - |
| prometheus | supabase, redis, kong, ray | - | kong, litellm, backend, asset-worker, asset-baker, n8n, weaviate, silo, supabase, redis, grafana |
| ray | supabase, redis | - | - |
| redis | supabase | - | - |
| redpanda | - | spark, jupyterhub, zeppelin, airflow, iceberg-rest, silo | - |
| searxng | redis | - | - |
| silo | supabase | - | - |
| spark | silo | supabase, iceberg-rest, redpanda | silo, iceberg-rest, redpanda |
| speaches | parakeet, tts-provider | - | - |
| speech-gate | speaches | - | speaches (optional: SPEECH_GATE_SOURCE=container and STT_PROVIDER_SOURCE=speaches-container-cpu or speaches-container-gpu) |
| stt-provider | - | - | - |
| supabase | - | - | - |
| supavisor | supabase | backend, n8n, celery | supabase |
| tei-reranker | - | - | - |
| tempo | kong, ray | - | - |
| tika | - | backend, n8n | - |
| trino | silo, iceberg-rest | spark, zeppelin, jupyterhub, airflow | iceberg-rest, silo |
| trueforge | supabase, redis, litellm | mcp-servers | supabase, redis, litellm, mcp-servers |
| tts-provider | litellm | - | - |
| verba | weaviate, litellm, kong | docling, open-webui, jupyterhub | weaviate, litellm |
| vllm-metal | litellm | - | - |
| weaviate | supabase, litellm | - | litellm (optional: only collections whose text2vec-openai baseURL is LiteLLM, as the backend's are), multi2vec-clip |
| zeppelin | spark, silo | supabase, iceberg-rest, redpanda, trino | spark, supabase, silo, iceberg-rest, redpanda (optional: Kafka connector baked into the Spark runtime (#1376); Atlas passes only SPARK_KAFKA_BOOTSTRAP_SERVERS), trino |
