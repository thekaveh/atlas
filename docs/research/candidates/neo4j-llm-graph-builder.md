---
category-fit: apps
generated: 2026-05-19
license: Apache-2.0
name: Neo4j LLM Knowledge Graph Builder
referenced-by: [neo4j]
slug: neo4j-llm-graph-builder
type: external-service
upstream: https://github.com/neo4j-labs/llm-graph-builder
---

# Neo4j LLM Knowledge Graph Builder

## 1. Headline
First-party Neo4j Labs app that turns PDFs, web pages, YouTube transcripts, S3 buckets and Wikipedia into a queryable Neo4j knowledge graph with chat-with-graph UI.

## 2. Problem it solves
The stack has Neo4j but no opinionated ingestion path from documents to graph — users must hand-roll extraction. LLM Graph Builder closes that gap with a polished UI, citations, and entity/relation extraction tuned for Neo4j 5 vector + graph indexes. It also doubles as a GraphRAG demo against the existing graph.

## 3. Stack wiring sketch
- Open browser → `kong` (route `graphbuilder.localhost`) → llm-graph-builder UI
- llm-graph-builder backend → `neo4j` via `bolt://neo4j-graph-db:7687`
- llm-graph-builder → `litellm` for LLM calls (OpenAI-compatible base URL)
- llm-graph-builder → `minio` (S3) for source-document buckets
- `doc-processor` → llm-graph-builder ingestion endpoint for already-parsed docs (optional)

## 4. Effort
medium — official Docker images exist (`neo4j/llm-graph-builder-backend` + `-frontend`), but we'd need a new `services/llm-graph-builder/` family (manifest, compose, kong route, env wiring to LiteLLM and MinIO).

## 5. Risks & open questions
- App assumes OpenAI-compatible endpoint; need to confirm LiteLLM base-URL override works end-to-end for the embeddings call.
- Pulls in a Python+Node stack; image footprint is non-trivial.
- Default schema overlaps with anything backend writes — need a namespace convention.

## 6. Why now (and why not sooner)
Neo4j 5 vector indexes and the GenAI plugin only stabilized recently; before that, GraphRAG required a custom orchestrator. With LiteLLM and Weaviate already in the stack, adding LLM Graph Builder is the cheapest path to an end-to-end GraphRAG demo.

## 7. Upstream evidence
- https://github.com/neo4j-labs/llm-graph-builder
- https://neo4j.com/labs/genai-ecosystem/llm-graph-builder/
