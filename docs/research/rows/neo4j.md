---
service: neo4j
category: data
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/neo4j/service.yml
  - services/neo4j/compose.yml
  - services/neo4j/README.md
  - services/backend/compose.yml
  - services/jupyterhub/service.yml
  - services/local-deep-researcher/service.yml
  - https://neo4j.com/docs/cypher-manual/current/indexes/semantic-indexes/vector-indexes/
  - https://neo4j.com/docs/cypher-manual/current/genai-integrations/
  - https://neo4j.com/labs/genai-ecosystem/llm-graph-builder/
  - https://help.getzep.com/graphiti/getting-started/welcome
  - https://docs.n8n.io/integrations/builtin/credentials/neo4j/
  - https://docs.openwebui.com/features/plugin/functions/
---

# neo4j — Integration Research

## 1. Missing-pair integrations

- **neo4j ↔ hermes**
  - Why valuable: Persistent agent memory + entity/relation recall across sessions. Hermes skills can write structured episodic memory as a graph and traverse it for context.
  - Mechanism sketch: Hermes custom tool/skill talking Bolt at `bolt://neo4j-graph-db:7687` using the official neo4j Python driver; `GRAPH_DB_USER` / `GRAPH_DB_PASSWORD` from `.env`.
  - Effort: medium
  - Risks / open questions: schema discipline for episodic vs semantic memory; potential overlap with backend's LangMem (Supabase-backed).
  - Confidence: medium

- **neo4j ↔ n8n**
  - Why valuable: Unlocks no-code graph automation (entity sync, alerting on graph patterns, hydrating workflows from Cypher). n8n ships a first-party Neo4j credential + node.
  - Mechanism sketch: n8n Neo4j node configured with `bolt://neo4j-graph-db:7687`, `neo4j` / `${GRAPH_DB_PASSWORD}`. Add `NEO4J_URI` to `services/n8n/compose.yml` and a credential seed in n8n init.
  - Effort: small
  - Risks / open questions: credential seeding pattern in n8n init container; needs an opinionated default starter workflow.
  - Confidence: high

- **neo4j ↔ weaviate**
  - Why valuable: GraphRAG patterns — Weaviate finds semantically similar chunks, Neo4j expands the neighbourhood (entities, citations, relationships) for grounded answers. Closes the "vector-only retrieval is shallow" gap.
  - Mechanism sketch: Backend orchestrator: Weaviate `nearText` → take payload `entity_ids` → Cypher `MATCH (e)-[*1..2]-(n) RETURN n`. No direct DB-to-DB link; coordinated via backend.
  - Effort: medium
  - Risks / open questions: schema convention for cross-store IDs; backend currently doesn't expose a GraphRAG endpoint.
  - Confidence: medium

- **neo4j ↔ doc-processor (docling)**
  - Why valuable: Docling already extracts structured document elements (sections, tables, references). Persisting them as a graph turns the doc corpus into a navigable knowledge graph for RAG and analytics.
  - Mechanism sketch: New backend route or n8n flow: docling JSON → entity/relation extractor (LiteLLM) → Cypher `MERGE` over Bolt.
  - Effort: medium
  - Risks / open questions: entity-resolution / dedup strategy; LLM cost for extraction at ingest time.
  - Confidence: medium

- **neo4j ↔ local-deep-researcher**
  - Why valuable: LDR already lists neo4j as optional in `runtime_deps` but no concrete wiring exists. Research runs naturally produce claim/source/entity graphs that benefit later sessions.
  - Mechanism sketch: LDR LangGraph node that emits Cypher on each research step via `bolt://neo4j-graph-db:7687`; reuse `GRAPH_DB_*` creds.
  - Effort: small
  - Risks / open questions: needs a research-trace schema; opt-in vs always-on persistence.
  - Confidence: medium

- **neo4j ↔ open-webui**
  - Why valuable: Lets chat users query the knowledge graph directly (GraphRAG pipeline). Open WebUI Functions can call a backend `/graphrag` endpoint per turn.
  - Mechanism sketch: Open WebUI Function (Python) → backend `POST /graphrag` → backend runs Cypher on `bolt://neo4j-graph-db:7687`, returns grounded context to the LLM.
  - Effort: medium
  - Risks / open questions: latency budget per turn; safe Cypher generation (read-only role).
  - Confidence: medium

- **neo4j ↔ minio**
  - Why valuable: Neo4j currently dumps backups to a local bind mount (`./services/neo4j/build/snapshot/`). Pushing dumps to MinIO gives durable, versioned, off-node backup with the same S3 tooling used elsewhere.
  - Mechanism sketch: Modify `backup.sh` to `mc cp` the dump to `s3://${MINIO_BUCKET}/neo4j-backups/`. Or add a sidecar cron container.
  - Effort: small
  - Risks / open questions: secret handoff for MinIO creds inside the neo4j container; retention policy.
  - Confidence: high

## 2. Candidate new services

- **Neo4j LLM Knowledge Graph Builder** → `../candidates/neo4j-llm-graph-builder.md`
  - Headline: First-party Neo4j Labs app that turns PDFs / web pages / YouTube transcripts into a queryable knowledge graph.
  - Other consumers in stack: backend, doc-processor, open-webui, minio.

- **Graphiti (Zep)** → `../candidates/graphiti.md`
  - Headline: Temporal knowledge-graph framework for agent memory, built on Neo4j.
  - Other consumers in stack: hermes, backend, n8n, local-deep-researcher.

- **NeoDash** → `../candidates/neodash.md`
  - Headline: Watchlisted low-code Cypher dashboards for future namespaced graph-native Atlas data.
  - Other consumers in stack: kong (future `neodash.localhost` route), backend, llm-graph-builder.

## 3. Per-service feature gaps

- **Native vector index (HNSW)** — Why pursue: Neo4j 5 ships an HNSW vector index, letting us store embeddings on graph nodes and combine ANN search with graph traversal in one DB. Effort: small.
- **GenAI plugin (`genai.vector.encode*`)** — Why pursue: Embed text directly inside Cypher via OpenAI / Vertex / Bedrock — wire it to LiteLLM and ingestion becomes one query. Effort: small.
- **APOC core + extended** — Why pursue: Image is plain `neo4j:5.26.27`; APOC is not preinstalled. APOC unlocks bulk import, periodic-iterate, JSON / HTTP, and the LLM procedures the rest of the stack assumes. Effort: small.
- **Neosemantics (n10s)** — Why pursue: RDF / ontology import/export bridges Neo4j with external semantic-web sources (Wikidata, schema.org). Effort: medium.
- **Read-only role for LLM-generated Cypher** — Why pursue: Safe execution of model-authored queries from open-webui / hermes; mitigates prompt-injection-to-`DETACH DELETE`. Effort: small.
- **Cluster / causal-read replica mode** — Why pursue: Currently single-instance; replicas enable horizontal read scale-out once GraphRAG traffic grows. Effort: large.
