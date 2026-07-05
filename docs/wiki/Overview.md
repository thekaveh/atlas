# Overview

## 1. Platform Model

Atlas is a self-hosted, source-configurable platform for AI, RAG, creative workflows, notebooks, automation, observability, and data engineering.

The bootstrapper turns repository-owned metadata into a runnable Docker Compose project. Service manifests describe each service family, topology rows define ports and aliases, tracks narrow the wizard to workflow-specific choices, and generated Kong routes expose browser-friendly local entrypoints.

## 2. Documentation Model

The public site and wiki are generated from the same model:

- `services/<name>/service.yml`
- `services/<name>/README.md`
- `services/topology.py`
- `bootstrapper/tracks.yml`
- generated architecture diagrams
- deployment and reference documents

## 3. Category Summary

| Category | Count | Services |
| --- | --- | --- |
| agents | 7 | airflow, celery, hermes, lightrag, mcp-servers, n8n, openclaw |
| aggregate | 3 | doc-processor, multi2vec-clip, stt-provider |
| apps | 10 | backend, jenkins, jupyterhub, label-studio, llm-graph-builder, local-deep-researcher, mlflow, open-webui, verba, zeppelin |
| data | 10 | iceberg-rest, minio, neo4j, redis, redpanda, spark, supabase, supavisor, trino, weaviate |
| infra | 11 | backup, cloudflared, globals, grafana, kong, langfuse, loki, otel-collector, prometheus, ray, tempo |
| llm | 4 | cloud-providers, litellm, ollama, tei-reranker |
| media | 10 | blender-mcp, chatterbox, comfyui, crawl4ai, docling, parakeet, searxng, speaches, tika, tts-provider |

## 4. Navigation

- Public site home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
- Services: [https://thekaveh.github.io/atlas/site/services/](https://thekaveh.github.io/atlas/site/services/)
- Architecture: [https://thekaveh.github.io/atlas/site/architecture/](https://thekaveh.github.io/atlas/site/architecture/)
- Reference: [https://thekaveh.github.io/atlas/site/reference/](https://thekaveh.github.io/atlas/site/reference/)
