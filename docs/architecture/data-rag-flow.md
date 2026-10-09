# 8.7. Data And RAG Flow

Ingestion, document processing, object storage, vector and graph stores, backend APIs, Open WebUI, and tool/MCP-adjacent flows.

## 1. Diagram

![Data And RAG Flow architecture diagram](../diagrams/img/architecture-data-rag-flow.png)

[Open the full-size diagram](./data-rag-flow.html).

## 2. Notes

Backend is not the only writer into these stores. LightRAG writes directly to Neo4j over Bolt and to Supabase pgvector. Other MinIO consumers (the Iceberg pipeline, asset-worker) write with their own scoped IAM credentials. This view shows only Backend's ingestion path.

## 3. Source Files

- `services/weaviate/service.yml`
- `services/backend/service.yml`
- `services/lightrag/service.yml`
