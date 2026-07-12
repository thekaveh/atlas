# Service Dependencies

## 1. Generated Dependency Matrix

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
| litellm | supabase, redis | - | supabase, redis, ollama, cloud-providers, hermes, lightrag, vllm-metal, fal, tei-reranker, otel-collector |
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
| vllm-metal | litellm | - | - |
| weaviate | supabase, litellm | - | litellm, multi2vec-clip |
| zeppelin | spark | supabase, minio, iceberg-rest, redpanda, trino | spark, supabase, minio, iceberg-rest, redpanda, trino |
