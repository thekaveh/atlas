---
category-fit: data
generated: 2026-07-04
license: AGPL-3.0
name: Honcho
referenced-by: [openclaw]
slug: honcho
type: external-service
upstream: https://github.com/plastic-labs/honcho
---

# Honcho

## Headline
Self-hostable user-and-session memory service that gives AI agents a durable model of each user across applications.

## Problem it solves
The stack currently has no shared, agent-agnostic memory layer: Hermes session state lives in its own volume, OpenClaw conversations vanish when the channel session ends, and Open WebUI history is per-app. Honcho provides a single REST surface where multiple agents (OpenClaw, Hermes, backend) can read and write per-user facts, theory-of-mind summaries, and session traces — enabling continuity across channels (Slack → Telegram → web UI) and across the agents themselves. OpenClaw's docs explicitly list Honcho as a supported memory engine, so wiring is a configuration job, not a custom-adapter job.

## Deferred decision (2026-07-04)

Keep Honcho deferred. Current upstream has become more capable than the original note captured — FastAPI service, SDKs, MCP server, Hermes/OpenClaw/client integrations, self-hosting, Postgres/pgvector persistence, Redis cache, and a background deriver — but that strengthens the admission burden rather than making it an automatic Atlas service. Atlas already has LangMem in the backend and a lighter Graphiti backend-only experiment; Honcho should wait until those prove insufficient for a concrete cross-agent memory workflow.

Future contract if reopened:

- Tracks: `gen-ai-eng`, `gen-ai-rag`, and `all`; do not add Honcho to ML, creative, or data-engineering tracks by default.
- Category: `agents`, even though this research row remains `data`, because the future service would be an agent memory provider rather than a user-facing database UI.
- Source values: `HONCHO_SOURCE=disabled|container|localhost`; disabled by default because long-term memory stores user conversation history and derived conclusions.
- Wizard placement: after Hermes/OpenClaw and after the backend LangMem prompt, with copy explaining the memory ownership model, AGPL-3.0 license, extra service weight, and privacy posture.
- Ports and routes: allocate `HONCHO_API_PORT` through the normal topology allocator, honor custom `BASE_PORT`, and expose no default Kong route until route auth, user scoping, and admin/debug surfaces are specified.
- Dependencies and consumers: required Supabase Postgres and Redis; LiteLLM for reasoning/deriver calls; optional consumers are backend, Hermes, OpenClaw, Open WebUI, and future MCP clients. Consumers must use explicit per-app/per-peer namespaces, not one shared global memory bucket.
- Topology: add `data_flow.calls` only for consumers that Atlas actually configures; do not imply Hermes/OpenClaw/Open WebUI writes until those integrations are tested.
- `init companion`: required for schema migration or schema bootstrap, service-role credentials, pgvector/table setup if Supabase is reused, initial app/peer namespaces, health probes, and safe deriver defaults.
- Edge cases: tenant isolation, conversation consent, memory deletion/export, stale or incorrect derived facts, route auth bypass, AGPL redistribution acceptance, external LLM leakage, deriver retry storms, Redis loss, Postgres migration drift, backup/restore of user memory, disabled LangMem/Graphiti coexistence, and stale `.env`.

Revisit only when Atlas has a named workflow where backend LangMem plus backend-only Graphiti cannot satisfy cross-session or cross-agent continuity, and the operator explicitly accepts a separate memory service with AGPL-3.0 obligations.

## Stack wiring sketch
- openclaw → honcho via `http://honcho:8000/v1/apps/<app>/users/<user>/sessions` (memory engine backend)
- hermes → honcho via REST tool for cross-session recall
- backend → honcho via REST for user-profile features
- honcho → supabase via `postgresql://supabase-db:5432/honcho` (Postgres backing store, schema-isolated)

## Effort
medium — adding a new container family (manifest + compose fragment + Kong alias + init for Postgres schema bootstrap) plus per-consumer client wiring; no GPU, modest memory, and the Postgres dependency reuses Supabase.

## Risks & open questions
- License is AGPL-3.0 — fine for self-host, may complicate downstream redistribution.
- Honcho's "theory of mind" derivations call an LLM; needs LiteLLM gateway integration to keep traffic on-stack.
- Schema migrations on upgrades — pin a tag rather than `:latest`.
- Multi-tenant isolation across agents (single Honcho app vs. per-agent apps) not yet decided.

## Why now (and why not sooner)
Memory continuity was a "later" feature while the stack was still establishing core inference, RAG, and channel surfaces. With both Hermes (long-running agent runtime) and OpenClaw (multi-channel adapter) now in the stack, the lack of a shared memory layer is the single most visible UX gap — a user who talks to the same model on Telegram and Open WebUI sees two strangers. Honcho is also one of the few self-hostable, Postgres-backed memory engines OpenClaw natively recognizes.

## Upstream evidence
- https://github.com/plastic-labs/honcho
- https://honcho.dev/docs/v3/documentation/introduction/vibecoding
- https://github.com/plastic-labs/honcho/blob/main/docker-compose.yml.example
- https://hermes-agent.nousresearch.com/docs/user-guide/features/honcho
- https://docs.openclaw.ai/llms.txt (lists Honcho among memory-engine providers)
