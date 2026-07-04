# Service Index

Generated from `services/*/service.yml` and `services/*/README.md`.

## 1. Service Families

| Service | Title | Category | Kind | SOURCE |
| --- | --- | --- | --- | --- |
| [airflow](../services/airflow.md) | Apache Airflow (DAG orchestrator) | agents | container | AIRFLOW_SOURCE |
| [backend](../services/backend.md) | Backend API (FastAPI) | apps | container | - |
| [backup](../services/backup.md) | Backup / restore (Postgres + volumes -> S3) | infra | container | BACKUP_SOURCE |
| [blender-mcp](../services/blender-mcp.md) | Blender MCP | media | virtual | BLENDER_MCP_SOURCE |
| [celery](../services/celery.md) | Celery + Flower (async jobs) | agents | container | CELERY_SOURCE |
| [chatterbox](../services/chatterbox.md) | Chatterbox (voice-cloning TTS, GPU) | media | container | - |
| [cloud-providers](../services/cloud-providers.md) | Cloud LLM providers (OpenAI, Anthropic, OpenRouter) | llm | virtual | - |
| [cloudflared](../services/cloudflared.md) | Cloudflare Tunnel (public edge) | infra | container | CLOUDFLARED_SOURCE |
| [comfyui](../services/comfyui.md) | ComfyUI (image generation) | media | container | COMFYUI_SOURCE |
| [crawl4ai](../services/crawl4ai.md) | Crawl4AI (JS-capable web extraction) | media | container | CRAWL4AI_SOURCE |
| [doc-processor](../services/doc-processor.md) | doc-processor | aggregate | doc-only | - |
| [docling](../services/docling.md) | Docling (document processor) | media | container | DOC_PROCESSOR_SOURCE |
| [globals](../services/globals.md) | Globals (project + branding) | infra | virtual | - |
| [grafana](../services/grafana.md) | Grafana (observability UI + alerting) | infra | container | GRAFANA_SOURCE |
| [hermes](../services/hermes.md) | Hermes (programmable AI agent) | agents | container | HERMES_SOURCE |
| [iceberg-rest](../services/iceberg-rest.md) | Apache Iceberg REST Catalog | data | container | ICEBERG_REST_SOURCE |
| [jenkins](../services/jenkins.md) | Jenkins (Maven Spark app builder) | apps | container | JENKINS_SOURCE |
| [jupyterhub](../services/jupyterhub.md) | JupyterHub (DS/ML + LLM notebooks) | apps | container | JUPYTERHUB_SOURCE |
| [kong](../services/kong.md) | Kong (API gateway) | infra | container | - |
| [label-studio](../services/label-studio.md) | Label Studio (dataset review + annotation) | apps | container | LABEL_STUDIO_SOURCE |
| [langfuse](../services/langfuse.md) | Langfuse (LLM traces + evals) | infra | container | LANGFUSE_SOURCE |
| [lightrag](../services/lightrag.md) | LightRAG (graph-augmented RAG server) | agents | container | LIGHTRAG_SOURCE |
| [litellm](../services/litellm.md) | LiteLLM gateway (LLM router) | llm | container | - |
| [llm-graph-builder](../services/llm-graph-builder.md) | Neo4j LLM Graph Builder | apps | container | LLM_GRAPH_BUILDER_SOURCE |
| [local-deep-researcher](../services/local-deep-researcher.md) | Local Deep Researcher (LangGraph research agent) | apps | container | LOCAL_DEEP_RESEARCHER_SOURCE |
| [loki](../services/loki.md) | Loki (queryable log store) | infra | container | LOKI_SOURCE |
| [mcp-servers](../services/mcp-servers.md) | Curated MCP Servers | agents | container | MCP_SERVERS_SOURCE |
| [minio](../services/minio.md) | MinIO (S3-compatible object storage) | data | container | MINIO_SOURCE |
| [mlflow](../services/mlflow.md) | MLflow (experiment tracking + artifacts) | apps | container | MLFLOW_SOURCE |
| [multi2vec-clip](../services/multi2vec-clip.md) | multi2vec-clip | aggregate | doc-only | - |
| [n8n](../services/n8n.md) | n8n (workflow automation) | agents | container | N8N_SOURCE |
| [neo4j](../services/neo4j.md) | Neo4j (graph database) | data | container | NEO4J_GRAPH_DB_SOURCE |
| [ollama](../services/ollama.md) | Ollama (local LLM engine) | llm | container | LLM_PROVIDER_SOURCE |
| [open-webui](../services/open-webui.md) | Open WebUI (chat interface) | apps | container | OPEN_WEB_UI_SOURCE |
| [openclaw](../services/openclaw.md) | OpenClaw (AI agent gateway) | agents | container | OPENCLAW_SOURCE |
| [otel-collector](../services/otel-collector.md) | OpenTelemetry Collector (telemetry ingest) | infra | container | OTEL_COLLECTOR_SOURCE |
| [parakeet](../services/parakeet.md) | Parakeet (NVIDIA STT engine) | media | container | STT_PROVIDER_SOURCE |
| [prometheus](../services/prometheus.md) | Prometheus (metrics scraper + TSDB) | infra | container | PROMETHEUS_SOURCE |
| [ray](../services/ray.md) | Ray (distributed compute substrate) | infra | container | RAY_SOURCE |
| [redis](../services/redis.md) | Redis (cache & queue) | data | container | - |
| [redpanda](../services/redpanda.md) | Redpanda (Kafka API streaming) | data | container | REDPANDA_SOURCE |
| [searxng](../services/searxng.md) | SearXNG (privacy metasearch) | media | container | SEARXNG_SOURCE |
| [spark](../services/spark.md) | Apache Spark (standalone cluster) | data | container | SPARK_SOURCE |
| [speaches](../services/speaches.md) | Speaches (unified TTS + STT) | media | container | - |
| [stt-provider](../services/stt-provider.md) | stt-provider | aggregate | doc-only | - |
| [supabase](../services/supabase.md) | Supabase (db, auth, api, storage, realtime, studio, meta) | data | container | - |
| [supavisor](../services/supavisor.md) | Supavisor (Postgres transaction pooler) | data | container | SUPAVISOR_SOURCE |
| [tei-reranker](../services/tei-reranker.md) | TEI Reranker (mxbai-rerank-base-v1) | llm | container | TEI_RERANKER_SOURCE |
| [tempo](../services/tempo.md) | Tempo (distributed trace store) | infra | container | TEMPO_SOURCE |
| [tika](../services/tika.md) | Apache Tika (fallback extractor) | media | container | TIKA_SOURCE |
| [trino](../services/trino.md) | Trino | data | container | TRINO_SOURCE |
| [tts-provider](../services/tts-provider.md) | TTS provider (text-to-speech engine selector) | media | virtual | TTS_PROVIDER_SOURCE |
| [verba](../services/verba.md) | Verba (archived Weaviate RAG UI) | apps | container | VERBA_SOURCE |
| [weaviate](../services/weaviate.md) | Weaviate (vector database) | data | container | WEAVIATE_SOURCE |
| [zeppelin](../services/zeppelin.md) | Apache Zeppelin (Spark-first notebook) | apps | container | ZEPPELIN_SOURCE |

## 2. Virtual Manifests

Virtual manifests are configuration surfaces without a compose container:
blender-mcp, cloud-providers, globals, tts-provider.

## 3. Doc-only Service Folders

Doc-only service folders are aggregate documentation surfaces without their own
`service.yml`: doc-processor, multi2vec-clip, stt-provider.
