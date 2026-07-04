# Cross-service Integration Matrix

> **Generated** by `python -m bootstrapper.docs.merge_research`. Do not edit by hand — your changes will be overwritten on the next run.

## By service

| Service | Category | Sources | Row file |
|---|---|---|---|
| backend | apps | 10 | [rows/backend.md](./rows/backend.md) |
| comfyui | media | 12 | [rows/comfyui.md](./rows/comfyui.md) |
| doc-processor | media | 7 | [rows/doc-processor.md](./rows/doc-processor.md) |
| hermes | agents | 13 | [rows/hermes.md](./rows/hermes.md) |
| jupyterhub | apps | 12 | [rows/jupyterhub.md](./rows/jupyterhub.md) |
| kong | infra | 8 | [rows/kong.md](./rows/kong.md) |
| litellm | llm | 14 | [rows/litellm.md](./rows/litellm.md) |
| local-deep-researcher | apps | 9 | [rows/local-deep-researcher.md](./rows/local-deep-researcher.md) |
| minio | data | 11 | [rows/minio.md](./rows/minio.md) |
| multi2vec-clip | data | 6 | [rows/multi2vec-clip.md](./rows/multi2vec-clip.md) |
| n8n | agents | 12 | [rows/n8n.md](./rows/n8n.md) |
| neo4j | data | 12 | [rows/neo4j.md](./rows/neo4j.md) |
| ollama | llm | 6 | [rows/ollama.md](./rows/ollama.md) |
| open-webui | apps | 8 | [rows/open-webui.md](./rows/open-webui.md) |
| openclaw | agents | 11 | [rows/openclaw.md](./rows/openclaw.md) |
| redis | data | 11 | [rows/redis.md](./rows/redis.md) |
| searxng | media | 13 | [rows/searxng.md](./rows/searxng.md) |
| stt-provider | media | 15 | [rows/stt-provider.md](./rows/stt-provider.md) |
| supabase | data | 12 | [rows/supabase.md](./rows/supabase.md) |
| tts-provider | media | 10 | [rows/tts-provider.md](./rows/tts-provider.md) |
| weaviate | data | 11 | [rows/weaviate.md](./rows/weaviate.md) |

## By category

### agents

- [hermes](./rows/hermes.md)
- [n8n](./rows/n8n.md)
- [openclaw](./rows/openclaw.md)

### apps

- [backend](./rows/backend.md)
- [jupyterhub](./rows/jupyterhub.md)
- [local-deep-researcher](./rows/local-deep-researcher.md)
- [open-webui](./rows/open-webui.md)

### data

- [minio](./rows/minio.md)
- [multi2vec-clip](./rows/multi2vec-clip.md)
- [neo4j](./rows/neo4j.md)
- [redis](./rows/redis.md)
- [supabase](./rows/supabase.md)
- [weaviate](./rows/weaviate.md)

### infra

- [kong](./rows/kong.md)

### llm

- [litellm](./rows/litellm.md)
- [ollama](./rows/ollama.md)

### media

- [comfyui](./rows/comfyui.md)
- [doc-processor](./rows/doc-processor.md)
- [searxng](./rows/searxng.md)
- [stt-provider](./rows/stt-provider.md)
- [tts-provider](./rows/tts-provider.md)

## Candidate new services

| Candidate | Category fit | Referenced by | One-pager |
|---|---|---|---|
| Apache Tika | media | doc-processor | [candidates/apache-tika.md](./candidates/apache-tika.md) |
| Browserless | media | n8n, searxng | [candidates/browserless.md](./candidates/browserless.md) |
| Celery + Flower | infra | backend | [candidates/celery-flower.md](./candidates/celery-flower.md) |
| Crawl4AI | media | local-deep-researcher | [candidates/crawl4ai.md](./candidates/crawl4ai.md) |
| Dagster | agents | minio | [candidates/dagster.md](./candidates/dagster.md) |
| Docling MCP Server | agents | doc-processor | [candidates/docling-mcp.md](./candidates/docling-mcp.md) |
| Firecrawl | media | local-deep-researcher | [candidates/firecrawl.md](./candidates/firecrawl.md) |
| Grafana Loki | infra | kong | [candidates/grafana-loki.md](./candidates/grafana-loki.md) |
| Graphiti | agents | neo4j | [candidates/graphiti.md](./candidates/graphiti.md) |
| Honcho | data | openclaw | [candidates/honcho.md](./candidates/honcho.md) |
| Apache Iceberg + DuckDB | data | minio | [candidates/iceberg-duckdb.md](./candidates/iceberg-duckdb.md) |
| imgproxy | media | supabase | [candidates/imgproxy.md](./candidates/imgproxy.md) |
| Keycloak | infra | kong | [candidates/keycloak.md](./candidates/keycloak.md) |
| Label Studio | apps | jupyterhub | [candidates/label-studio.md](./candidates/label-studio.md) |
| Lakekeeper | data | minio | [candidates/lakekeeper.md](./candidates/lakekeeper.md) |
| Langfuse | agents | backend, comfyui, hermes, litellm, local-deep-researcher, minio, n8n, ollama, open-webui | [candidates/langfuse.md](./candidates/langfuse.md) |
| MCP Gateway | agents | hermes | [candidates/mcp-gateway.md](./candidates/mcp-gateway.md) |
| mcpo (MCP-to-OpenAPI Proxy) | agents | open-webui | [candidates/mcpo.md](./candidates/mcpo.md) |
| MLflow | apps | backend, jupyterhub | [candidates/mlflow.md](./candidates/mlflow.md) |
| Neo4j LLM Knowledge Graph Builder | apps | neo4j | [candidates/neo4j-llm-graph-builder.md](./candidates/neo4j-llm-graph-builder.md) |
| NeoDash | apps | neo4j | [candidates/neodash.md](./candidates/neodash.md) |
| NocoDB | apps | n8n | [candidates/nocodb.md](./candidates/nocodb.md) |
| OmniVoice (k2-fsa) | media | _(none)_ | [candidates/omnivoice.md](./candidates/omnivoice.md) |
| Open WebUI Pipelines | apps | open-webui | [candidates/open-webui-pipelines.md](./candidates/open-webui-pipelines.md) |
| OpenLIT | infra | ollama | [candidates/openlit.md](./candidates/openlit.md) |
| Perplexica (Vane) | apps | searxng | [candidates/perplexica.md](./candidates/perplexica.md) |
| Prometheus | infra | kong | [candidates/prometheus.md](./candidates/prometheus.md) |
| Redis Stack (redis-stack-server) | data | redis | [candidates/redis-stack.md](./candidates/redis-stack.md) |
| RedisInsight | data | redis | [candidates/redisinsight.md](./candidates/redisinsight.md) |
| SigLIP 2 Vectorizer | data | multi2vec-clip | [candidates/siglip2-vectorizer.md](./candidates/siglip2-vectorizer.md) |
| Supabase Edge Functions (Deno runtime) | apps | supabase | [candidates/supabase-edge-functions.md](./candidates/supabase-edge-functions.md) |
| Supavisor | data | supabase | [candidates/supavisor.md](./candidates/supavisor.md) |
| Apache Superset | apps | minio | [candidates/superset.md](./candidates/superset.md) |
| TimescaleDB | data | supabase | [candidates/timescaledb.md](./candidates/timescaledb.md) |
| Unmute (Kyutai) | media | tts-provider | [candidates/unmute.md](./candidates/unmute.md) |
| Verba | apps | weaviate | [candidates/verba.md](./candidates/verba.md) |
| Voicebox (jamiepine) | media | _(none)_ | [candidates/voicebox.md](./candidates/voicebox.md) |
| WhisperX | media | stt-provider | [candidates/whisperx.md](./candidates/whisperx.md) |
