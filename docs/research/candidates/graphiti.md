---
category-fit: agents
generated: 2026-05-19
license: Apache-2.0
name: Graphiti
referenced-by: [neo4j]
slug: graphiti
type: external-service
upstream: https://github.com/getzep/graphiti
---

# Graphiti

## Headline
Temporal knowledge-graph framework for AI agents — episodes are timestamped, entities are versioned, and Neo4j is the storage backend.

## Problem it solves
Agent memory in this stack today is either Postgres rows plus semantic recall (LangMem) or agent-local state. Graphiti gives the backend a structured, time-aware graph projection with bi-temporal modelling (event-time + ingestion-time), entity dedup via embeddings, and Cypher-level recall. It should augment LangMem for relationship/event memory, not replace LangMem's default fact store.

## Stack wiring sketch
- `backend` → Graphiti Python SDK → `neo4j` via `bolt://neo4j-graph-db:7687`
- Graphiti embeddings call → `litellm` (OpenAI-compatible)
- Future, after backend proof: `hermes` / `openclaw` can consume backend-curated memory or a Graphiti MCP server, but neither should write directly in the first slice.

## Effort
small — the first Atlas slice is backend-only evaluation scaffolding: env/config, strict `group_id` naming, a status endpoint, and docs. A later implementation can add a pinned `graphiti-core` dependency plus a Neo4j schema/index bootstrap step once a concrete backend workflow is selected. No new container is mandatory; upstream now also ships a Graphiti MCP server, but Atlas should defer that until the backend experiment proves value.

## Risks & open questions
- Schema collisions if multiple consumers write episodes — Atlas should enforce `atlas:<project>:backend:<namespace>:user:<uuid>` for the backend experiment and require new prefixes before Hermes/OpenClaw write.
- Embedding cost: Graphiti embeds every node and edge; budget vs LiteLLM rate limits.
- Versioning churn — Graphiti is pre-1.0; breaking changes are likely.
- LLM compatibility: Graphiti works best with structured-output-capable models. Atlas' local-first default models need a real ingestion smoke test before Graphiti is enabled by default.
- MCP temptation: Graphiti's MCP server is useful, but exposing it now would bypass the backend-only safety goal and blur ownership of `group_id` namespaces.

## Why now (and why not sooner)
The backend already has LangMem, LiteLLM model configuration, Neo4j credentials, and an explicit "future graph endpoints" gap in its docs. With Neo4j wired in, Graphiti is the lowest-friction way to evaluate temporal graph memory without standing up a new database. LangMem remains the production memory path until a backend workflow proves which relationship/event episodes deserve graph projection.

## Upstream evidence
- https://github.com/getzep/graphiti
- https://help.getzep.com/graphiti/getting-started/welcome
- https://help.getzep.com/graphiti/core-concepts/graph-namespacing
- https://help.getzep.com/graphiti/configuration/llm-configuration
- https://help.getzep.com/graphiti/getting-started/mcp-server
