---
service: openclaw
category: agents
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/openclaw/service.yml
  - services/openclaw/compose.yml
  - services/openclaw/README.md
  - https://github.com/openclaw/openclaw
  - https://docs.openclaw.ai/
  - https://docs.openclaw.ai/llms.txt
  - https://docs.openclaw.ai/cli/mcp.md
  - https://docs.openclaw.ai/plugins/webhooks.md
  - https://docs.openclaw.ai/skills/
  - https://docs.openclaw.ai/configuration/
  - https://docs.openclaw.ai/reference/
---

# openclaw — Integration Research

## 1. Missing-pair integrations

- **openclaw ↔ hermes**
  - Why valuable: OpenClaw is positioned as a channel adapter (40+ messaging surfaces); Hermes is the programmable agent runtime already in the stack. Routing inbound DMs to Hermes sessions turns Hermes into a multi-channel assistant without writing per-channel code. The compose file already passes `HERMES_ENDPOINT`/`HERMES_API_KEY` — only the bridge wiring is missing.
  - Mechanism sketch: OpenClaw skill or webhook plugin forwarding inbound messages to `http://hermes:8642/v1/chat/completions`; replies posted back via OpenClaw's `send` RPC.
  - Effort: medium
  - Risks / open questions: session-id mapping across channels; auth token rotation.
  - Confidence: high (compose envs already provisioned; skills system supports custom tools per docs.openclaw.ai/skills/)

- **openclaw ↔ n8n**
  - Why valuable: OpenClaw's webhooks plugin explicitly lists n8n as a primary trigger source; gives non-developers a visual surface to wire messaging events to stack workflows (RAG, image gen, ticket creation).
  - Mechanism sketch: n8n HTTP Request node → `POST http://openclaw-gateway:18789/webhooks/<route>` with `Authorization: Bearer <route-secret>`; payload `{action: "run_task", ...}`.
  - Effort: small
  - Risks / open questions: per-route secret distribution; rate-limit tuning.
  - Confidence: high (plugins/webhooks.md documents the exact action verbs)

- **openclaw ↔ silo**
  - Why valuable: Workspace files, voice notes, and media attachments currently live only in the `openclaw-workspace` Docker volume — not addressable by other stack services. Silo is the stack's S3 backend; OpenClaw's docs list S3/object-storage as a supported backend.
  - Mechanism sketch: configure S3 backend with `endpoint=http://silo:9000`, `SILO_ROOT_USER`/`SILO_ROOT_PASSWORD`, dedicated `openclaw` bucket alongside the existing `SILO_BUCKET_*` set.
  - Effort: small
  - Risks / open questions: confirm exact config keys (S3 storage page returned 404 — likely under a different path); bucket-creation init step needed.
  - Confidence: medium (S3 backend listed in llms.txt; exact config schema unverified)

- **openclaw ↔ doc-processor**
  - Why valuable: When users drop PDFs/Office docs into a chat, OpenClaw's built-in PDF handling is shallow; doc-processor (Docling) already produces structured markdown + chunks the rest of the stack uses for RAG.
  - Mechanism sketch: custom OpenClaw skill posting attachments to `http://docling-gpu:8000/v1/document/convert`; persist markdown to workspace + Silo.
  - Effort: medium
  - Risks / open questions: attachment-size limits (Docling 50 MB default); async polling for long jobs.
  - Confidence: high (Docling REST is documented; OpenClaw skills can shell out to HTTP)

- **openclaw ↔ weaviate**
  - Why valuable: OpenClaw's memory engines page is 404 but llms.txt lists "memory search across persistent knowledge bases"; Weaviate is the stack's vector DB. Letting OpenClaw recall prior conversations and ingested docs across sessions removes a major gap vs. cloud assistants.
  - Mechanism sketch: skill or MCP server bridging to `http://weaviate:8080/v1/objects` (REST) or `:50051` (gRPC); embedding via LiteLLM's embeddings endpoint.
  - Effort: medium
  - Risks / open questions: which memory engine slot (builtin vs. Honcho) accepts a Weaviate backend is undocumented; may need a custom MCP server.
  - Confidence: medium (Weaviate has stock APIs; OpenClaw memory backend pluggability not fully documented)

- **openclaw ↔ searxng**
  - Why valuable: OpenClaw ships web-search tools with multiple providers but defaults to commercial APIs; SearXNG is the stack's privacy-preserving metasearch, already exposed at a known internal URL.
  - Mechanism sketch: set OpenClaw's web-search provider to a custom HTTP backend pointing at `${SEARXNG_INTERNAL_URL}/search?format=json&q=...`.
  - Effort: small
  - Risks / open questions: SearXNG JSON output gating; rate caps.
  - Confidence: medium (web-search tool listed in llms.txt; provider extensibility implied but not detailed)

## 2. Candidate new services

- **Honcho** → `../candidates/honcho.md`
  - Headline: Hosted/self-hostable user-memory store explicitly listed as an OpenClaw memory-engine backend.
  - Other consumers in stack: hermes, backend, local-deep-researcher

## 3. Per-service feature gaps

- **MCP CLI / external MCP server support** — Why pursue: lets OpenClaw consume any MCP server (Neo4j, Weaviate, GitHub) over stdio/SSE/streamable-http, unlocking RAG and graph tools without bespoke skills. Effort: medium.
- **Webhooks plugin (inbound TaskFlow trigger)** — Why pursue: standard surface for n8n/CI/external triggers; auth model already defined. Effort: small.
- **Sandbox runners (Docker/SSH backends)** — Why pursue: non-main sessions can run tools in Docker sandboxes — meaningfully safer than current host-bound execution. Effort: medium.
- **TTS / STT (Deepgram, Azure Speech, voice wake)** — Why pursue: stack already runs `tts-provider` and `stt-provider`; swap OpenClaw's cloud STT/TTS for local providers to keep voice fully on-device. Effort: small.
- **Memory engines (Honcho / QMD search)** — Why pursue: persistent cross-session memory currently absent. Effort: medium.
- **S3 / object-storage backend** — Why pursue: wire workspace + media to Silo for cross-service file sharing. Effort: small.
