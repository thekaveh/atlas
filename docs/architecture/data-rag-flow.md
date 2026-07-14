# Data And RAG Flow

Ingestion, document processing, object storage, vector and graph stores, backend APIs, Open WebUI, and tool/MCP-adjacent flows.

## 1. Diagram

[Open the interactive diagram](./data-rag-flow.html).

## 2. How To Read This View

The Backend coordinates ingestion: processors extract source material, MinIO preserves objects, Weaviate stores vector representations, and Neo4j stores graph relationships. Open WebUI and tool callers consume that assembled retrieval surface through Backend APIs.

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## 4. Maintenance

Regenerate this page and `data-rag-flow.html` after changing a represented service,
route, SOURCE mode, track, dependency, or data-flow boundary.
