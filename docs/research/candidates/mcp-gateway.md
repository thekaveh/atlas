---
category-fit: agents
generated: 2026-05-19
license: MIT
name: MCP Gateway
referenced-by: [hermes]
slug: mcp-gateway
type: external-service
upstream: https://github.com/modelcontextprotocol/servers
---

# MCP Gateway

## 1. Headline
A consolidated Model-Context-Protocol server that exposes existing stack services (neo4j, weaviate, minio, n8n, supabase) as MCP tools any MCP-native client — Hermes, Open WebUI, Claude Desktop — can mount.

## 2. Problem it solves
Hermes is "MCP-native" per its README but the stack runs zero MCP servers, so every cross-service capability today is a bespoke Hermes skill that has to be hand-written and rebuilt into the image. An in-stack MCP gateway moves that wiring outside the Hermes image and lets every MCP-aware consumer reuse the same tools.

## 3. Stack wiring sketch
- hermes → mcp-gateway via MCP stdio/SSE at `http://mcp-gateway:8811`, mounting per-tool routes (`/tools/neo4j`, `/tools/weaviate`, `/tools/minio`, `/tools/n8n`).
- mcp-gateway → neo4j via `bolt://neo4j-graph-db:7687`.
- mcp-gateway → weaviate via `http://weaviate:8080`.
- mcp-gateway → minio via S3 SigV4 on `http://minio:9000`.
- mcp-gateway → n8n via webhook POST to `http://n8n:5678/webhook/<id>`.
- open-webui → mcp-gateway via Open WebUI's native MCP-tool support.
- jupyterhub → mcp-gateway (notebooks call MCP via the official Python SDK).

## 4. Effort
medium — A single container running one of the reference MCP server bundles plus per-backend credentials. Most cost is auth/secrets plumbing (each downstream service has its own credential surface).

## 5. Risks & open questions
- MCP server ecosystem is still pre-1.0; spec/protocol drift may force a rewrite.
- No single official "all-tools" reference server — likely composed from `modelcontextprotocol/servers` packages or `mcpo`-style wrappers.
- Capability surface vs blast radius: an MCP tool that writes to MinIO can also delete from it; need scoped credentials per tool.

## 6. Why now (and why not sooner)
The combination of Hermes (MCP client), Open WebUI's recent MCP-tool support, and the maturing reference-server catalogue makes a single MCP gateway viable — six months ago none of the consumers were MCP-native.

## 7. Upstream evidence
- MCP servers reference catalogue: https://github.com/modelcontextprotocol/servers
- MCP spec: https://spec.modelcontextprotocol.io/
- Hermes MCP-client claim: https://github.com/NousResearch/hermes-agent
