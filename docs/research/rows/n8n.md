---
service: n8n
category: agents
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/n8n/service.yml
  - services/n8n/compose.yml
  - services/n8n/init/config/nodes.json
  - services/n8n/init/scripts/init-n8n.sh
  - services/minio/service.yml
  - services/comfyui/service.yml
  - services/weaviate/service.yml
  - services/redis/service.yml
  - services/n8n/README.md
  - https://docs.n8n.io/integrations/builtin/app-nodes/
  - https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcptrigger/
  - https://docs.n8n.io/external-secrets/
---

# n8n — Integration Research

## 1. Missing-pair integrations

- **n8n ↔ comfyui**
  - Why valuable: `n8n-nodes-comfyui` is already installed by `n8n-init`, but no `COMFYUI_ENDPOINT` env var is injected into the n8n container, so users must hand-enter `http://comfyui:18188` in every workflow credential. ComfyUI's manifest already declares n8n as a consumer.
  - Mechanism sketch: inject `COMFYUI_ENDPOINT=${COMFYUI_ENDPOINT}` into n8n's compose env block (matches the STT/TTS/DOCLING pattern) and add `comfyui` to `runtime_deps.optional`.
  - Effort: small
  - Risks / open questions: ComfyUI source can be `disabled`, `localhost`, or a container variant — the env var already resolves per-source, so n8n just needs to read it.
  - Confidence: high (community node verified in `init/config/nodes.json`; endpoint pattern proven for STT/TTS).

- **n8n ↔ minio**
  - Why valuable: MinIO already provisions an `n8n` bucket plus a scoped `MINIO_N8N_ACCESS_KEY` / `MINIO_N8N_SECRET_KEY` pair, but neither credentials nor the S3 endpoint are passed to n8n, so the dedicated bucket sits unused. n8n's built-in **S3** node supports custom endpoints.
  - Mechanism sketch: env-inject `S3_ENDPOINT=http://minio:9000`, `S3_BUCKET=${MINIO_BUCKET_N8N}`, `S3_ACCESS_KEY=${MINIO_N8N_ACCESS_KEY}`, `S3_SECRET_KEY=${MINIO_N8N_SECRET_KEY}` and add `minio` to `runtime_deps.optional`.
  - Effort: small
  - Risks / open questions: path-style vs virtual-hosted-style addressing — MinIO needs `forcePathStyle=true` on the n8n S3 credential template.
  - Confidence: high (MinIO manifest already provisions the bucket + IAM policy).

- **n8n ↔ neo4j**
  - Why valuable: Neo4j is the stack's graph store but has no first-party n8n node. Workflows that build knowledge graphs from `doc_processor` output currently can't write to Neo4j without a custom HTTP-node call. Wiring this enables KG-from-document flows.
  - Mechanism sketch: inject `NEO4J_URI=bolt://neo4j-graph-db:7687` plus `NEO4J_USER`/`NEO4J_PASSWORD`; orchestrate via the HTTP Request node hitting the Cypher transactional endpoint (`http://neo4j-graph-db:7474/db/neo4j/tx/commit`) until a community node is adopted.
  - Effort: medium
  - Risks / open questions: no official n8n Neo4j node — relies on HTTP-node patterns; community nodes (e.g. `n8n-nodes-neo4j`) exist but are untrusted; needs vetting before adding to `N8N_INIT_NODES`.
  - Confidence: medium (Neo4j endpoint + auth verified in `services/neo4j/compose.yml`; node availability is the open question).

- **n8n ↔ searxng**
  - Why valuable: SearXNG is privacy-preserving metasearch; n8n already advertises a `SearXNG Tool` sub-node for AI-agent workflows but the endpoint is not injected. Enables in-stack research workflows without external APIs.
  - Mechanism sketch: inject `SEARXNG_ENDPOINT=http://searxng:8080` and add `searxng` to `runtime_deps.optional`; workflows hit `/search?q=&format=json`.
  - Effort: small
  - Risks / open questions: SearXNG's botdetection trusts only configured proxies — n8n's request needs `X-Forwarded-For` or the trusted-proxy list must include the n8n container.
  - Confidence: high (SearXNG node confirmed in upstream docs; endpoint pattern matches existing usage).

- **n8n ↔ openclaw**
  - Why valuable: openclaw is the messaging-platform gateway (Slack/Discord/Telegram). Wiring it to n8n turns every n8n webhook into a chat-triggered automation, and lets n8n reply through openclaw — the natural "agent loop" surface the stack currently lacks.
  - Mechanism sketch: openclaw → n8n via a webhook hitting `http://n8n:5678/webhook/<path>`; n8n → openclaw via HTTP Request node to openclaw's send-message endpoint, plus shared bearer secret in both manifests.
  - Effort: medium
  - Risks / open questions: webhook auth strategy (HMAC vs bearer); openclaw's response schema needs to be stable across providers.
  - Confidence: medium (both services expose HTTP today; no first-party n8n openclaw node).

## 2. Candidate new services

- **Langfuse** → `../candidates/langfuse.md`
  - Headline: Self-hostable LLM/diffusion trace + eval store; n8n's HTTP node can log per-step trace events.
  - Other consumers in stack: litellm, hermes, comfyui.

- **Browserless** → `../candidates/browserless.md`
  - Headline: Deferred headless-browser backend for n8n workflows that prove they need persistent sessions, screenshots, PDFs, or Playwright/Puppeteer automation beyond Crawl4AI.
  - Other consumers in stack: searxng, doc_processor, backend.

- **NocoDB** → `../candidates/nocodb.md`
  - Headline: Watchlisted spreadsheet UI for a future concrete human-review queue, with a first-party n8n node for row CRUD.
  - Other consumers in stack: supabase, backend.

## 3. Per-service feature gaps

- **MCP Server Trigger node** — Why pursue: n8n can expose workflows as MCP tools that hermes/litellm clients consume, completing the bidirectional MCP story (we install the *client* node `n8n-nodes-mcp` but never run a server). Effort: small.
- **Weaviate Vector Store cluster node** — Why pursue: upstream ships a native Weaviate vector-store node — currently workflows talk to Weaviate via raw HTTP. Switching unlocks embeddings + retrievers without custom code. Effort: small.
- **Built-in webhook auth (header auth + signature verification)** — Why pursue: openclaw and external triggers need verified webhooks; n8n supports this but no defaults are baked into the manifest. Effort: small.
