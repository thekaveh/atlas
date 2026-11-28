---
service: hermes
category: agents
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/hermes/service.yml
  - services/hermes/init/templates/config.yaml.tmpl
  - services/hermes/init/scripts/init-hermes.sh
  - services/hermes/README.md
  - services/neo4j/service.yml
  - services/weaviate/service.yml
  - services/silo/service.yml
  - services/n8n/service.yml
  - services/doc-processor/service.yml
  - services/supabase/service.yml
  - https://github.com/NousResearch/hermes-agent
  - https://hermes-agent.nousresearch.com/docs/
  - https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard
---

# hermes — Integration Research

## 1. Missing-pair integrations

- **hermes ↔ neo4j**
  - Why valuable: Hermes persists state as flat files under `/opt/data`. A graph store adds durable cross-session episodic memory (entities, relations) queryable from other services.
  - Mechanism sketch: Custom skill reading/writing via `bolt://neo4j-graph-db:7687` using `GRAPH_DB_USER` / `GRAPH_DB_PASSWORD`; exposed as a `memory.graph` tool.
  - Effort: medium
  - Risks / open questions: Entity-dedupe ownership; Hermes ships no Neo4j client — needs a Python skill.
  - Confidence: medium (custom-skill mechanism documented upstream; Neo4j bolt already exposed).

- **hermes ↔ weaviate**
  - Why valuable: Semantic recall across sessions and ingested docs. Reuses the in-stack `multi2vec-clip` vectorizer.
  - Mechanism sketch: Skill calling `http://weaviate:8080/v1/objects` with a `HermesMemory` class.
  - Effort: medium
  - Risks / open questions: Schema migration if vectorizer flips; Hermes has no native vector client.
  - Confidence: medium (Weaviate REST stable; Hermes skills documented).

- **hermes ↔ silo**
  - Why valuable: Skill outputs (ComfyUI images, STT transcripts) currently land in the bind-mounted volume. Silo gives shareable URLs other services can fetch.
  - Mechanism sketch: New `hermes-artifacts` bucket via the existing `silo-init` IAM pattern; S3 SigV4 against `http://silo:9000`.
  - Effort: small
  - Risks / open questions: Lifecycle policy; new `HERMES_SILO_*` env wiring.
  - Confidence: high (Silo already pre-provisions per-service buckets).

- **hermes ↔ n8n**
  - Why valuable: Today the edge is one-way (n8n → hermes-agent via LiteLLM). The reverse — Hermes invoking n8n workflows as tools — turns n8n's 400+ connectors into Hermes capabilities without per-platform skills.
  - Mechanism sketch: Skill POSTing to `http://n8n:5678/webhook/<id>` with `N8N_WEBHOOK_TOKEN`; one generic "call-n8n" skill taking a workflow id.
  - Effort: small
  - Risks / open questions: Workflow discovery — Hermes can't enumerate n8n workflows without an index endpoint.
  - Confidence: high (n8n webhooks stable; Hermes skills documented).

- **hermes ↔ doc-processor**
  - Why valuable: Hermes can answer questions about uploaded PDFs only if something parses them first. Docling already exposes a conversion endpoint in-stack.
  - Mechanism sketch: Skill POSTing to `http://docling-gpu:8000/v1/document/convert` (multipart) and feeding the result into context or Weaviate.
  - Effort: small
  - Risks / open questions: Large-file handling; where parsed text lands.
  - Confidence: high (docling endpoint documented in `services/doc-processor/`).

- **hermes ↔ supabase**
  - Why valuable: A JWT-scoped shared session store lets one Hermes session follow a user across Open WebUI, JupyterHub, and OpenClaw — `/opt/data` is single-tenant.
  - Mechanism sketch: Skill writing to `hermes_sessions` via PostgREST at `http://supabase-api:3000`, keyed by Supabase JWT `sub`.
  - Effort: medium
  - Risks / open questions: Hermes session-backend pluggability is unverified — likely a skill convention only.
  - Confidence: medium (Supabase API stable; Hermes-side pluggability unverified).

## 2. Candidate new services

- **Langfuse** → `../candidates/langfuse.md`
  - Headline: Self-hosted LLM-observability platform — traces every Hermes tool call, skill invocation, and downstream LiteLLM completion.
  - Other consumers in stack: litellm, backend, local-deep-researcher, openclaw

- **Open WebUI Tools / MCP Gateway** → `../candidates/mcp-gateway.md`
  - Headline: A dedicated MCP-server gateway that exposes stack services (neo4j, weaviate, silo, n8n) as MCP tools Hermes can mount natively.
  - Other consumers in stack: open-webui, backend, jupyterhub

## 3. Per-service feature gaps

- **MCP server mode** — Hermes is "MCP-native" as a client per the README, but we don't run any MCP servers in-stack today. Why pursue: unlocks tool-use over Neo4j/Weaviate/Silo/n8n via a uniform protocol instead of bespoke skills. Effort: medium.
- **Messaging-platform allowlists** — `GATEWAY_ALLOW_ALL_USERS`, `TELEGRAM_ALLOWED_USERS`, `DISCORD_ALLOWED_USERS` are all unwired. Why pursue: required before OpenClaw can safely bridge Hermes to Telegram/Discord/WhatsApp without an open relay. Effort: small.
- **Per-user / multi-tenant sessions** — Hermes runs single-tenant against `/opt/data`. Why pursue: needed for any shared deployment beyond a single developer's laptop. Effort: large.
- **Voice mode (mic passthrough)** — README notes "live voice mode" requires `localhost` SOURCE because container mic passthrough is non-trivial. Why pursue: enables true voice agent UX in-stack. Effort: large.
- **Skill marketplace / dynamic skill install** — Today skills are baked-in plus our one override file. Hermes upstream supports dynamic skill loading. Why pursue: lets users add capabilities without rebuilding the image. Effort: medium.
