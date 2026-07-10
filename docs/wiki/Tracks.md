# Tracks

## 1. Track Matrix

| Track | Description | Services |
| --- | --- | --- |
| gen-ai-rag | Retrieval-augmented generation — vectors, graph, reranker, doc ingest, web search, workflow automation. | open-webui, supavisor, n8n, weaviate, neo4j, lightrag, doc-processor, tei-reranker, searxng, mcp-servers, langfuse, otel-collector, tempo, loki, local-deep-researcher, crawl4ai, tika, llm-graph-builder, verba, celery |
| gen-ai-eng | Agentic apps + workflows with voice, vision, and search. | open-webui, supavisor, n8n, hermes, openclaw, jupyterhub, comfyui, neo4j, stt-provider, tts-provider, searxng, mcp-servers, langfuse, otel-collector, tempo, loki, local-deep-researcher, tika, celery |
| gen-ai-creative | Multimodal generation — image, voice, vision, doc. | open-webui, comfyui, asset-worker, fal, stt-provider, tts-provider, multi2vec-clip, doc-processor, blender-mcp, langfuse, otel-collector, tempo, loki |
| ml-eng | Distributed training/inference + notebooks + experiment storage. | spark, ray, jupyterhub, zeppelin, open-webui, supavisor, minio, tei-reranker, langfuse, otel-collector, tempo, loki, mlflow, label-studio |
| data-eng | Batch + lakehouse + graph + vector with orchestration. | spark, airflow, jupyterhub, zeppelin, jenkins, supavisor, minio, iceberg-rest, trino, redpanda, weaviate, neo4j |
| trading | Read-only financial research and paper portfolios in notebooks; no live trading. | jupyterhub, minio, mlflow, langfuse |
| all | Every configurable service — full wizard, no filtering. | all services (no filtering) |

## 2. Selection Behavior

- `all` means no track filtering.
- Explicit CLI SOURCE flags override track defaults with a warning.
- Prometheus, Grafana, cloud keys, and the LLM engine stay globally prompted, while their defaults can still be disabled.

## 3. Wizard Behavior

- Track selection happens before service SOURCE prompts.
- Out-of-track source-configurable services are skipped and force-disabled.
- Explicit command-line SOURCE flags are preserved even when they cross track boundaries.
- Locked core services remain part of the runtime foundation.

## 4. Adding Or Changing Tracks

- Update `bootstrapper/tracks.yml`.
- Confirm every listed service has manifest/topology coverage.
- Regenerate docs and wiki output.
- Run track-membership and docs-site checks before opening a PR.
