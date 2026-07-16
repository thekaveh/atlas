---
category-fit: media
generated: 2026-07-04
license: AGPL-3.0
name: Firecrawl
referenced-by: [local-deep-researcher]
slug: firecrawl
type: external-service
upstream: https://github.com/firecrawl/firecrawl
---

# Firecrawl

## 1. Headline
Self-hostable web-context API for search, scrape, crawl, map, batch scrape,
browser interaction, agent workflows, and MCP access that can return LLM-ready
markdown, structured JSON, screenshots, and parsed document output.

## 2. Problem it solves
Firecrawl is attractive when Atlas needs a higher-level extraction product than
a single crawler endpoint. Current upstream docs describe v2 search, scrape,
interact, agent, parse, and hosted MCP flows, while the self-host guide keeps
the service oriented around a local API plus worker/browser infrastructure.
That could matter for RAG workflows that need batch crawling, scripted browser
actions, or MCP-facing web extraction instead of only one-shot markdown fetches.

## 3. Deferred decision (2026-07-04)
Atlas should stay Crawl4AI-first and must not add `services/firecrawl/service.yml` yet.
Crawl4AI is already implemented as the first-line RAG extraction service: it is
disabled by default, token-protected, Apache-2.0, routed at `crawl4ai.localhost`,
and wired to Local Deep Researcher and n8n without adding another
queue/database/browser topology.

Firecrawl remains deferred because its AGPL-3.0 license and larger worker/Playwright footprint
are not justified until Atlas has a reproduced gap that Crawl4AI plus SearXNG,
n8n, Docling, LiteLLM, Weaviate, and future Browserless-style infrastructure
cannot cover. The current upstream
self-hosting guide also calls out extra operational responsibility: manual
environment configuration, Playwright customization, optional Supabase-backed
authentication, PostgreSQL credentials, queue/admin secrets, proxy credentials,
CPU/RAM thresholds, and limits around advanced Fire-engine functionality in
self-hosted deployments.

## 4. Stack wiring sketch
No current Atlas wiring should be added while Firecrawl is deferred. If adopted
later, the expected topology would be:

- Local Deep Researcher -> Firecrawl for full-page extraction only after
  Crawl4AI gaps are proven.
- n8n -> Firecrawl through HTTP Request nodes for curated ingestion workflows.
- backend -> Firecrawl through the existing async HTTP client pattern, with
  backend-owned validation, timeout, provenance, and size limits.
- Hermes/MCP -> Firecrawl only through the curated MCP package or an explicit
  tool registration, not through an ad hoc exposed browser endpoint.
- Firecrawl output -> Weaviate and MinIO through existing ingestion/provenance
  paths rather than direct writes from the crawler service.

## 5. Effort
Medium to large. A credible Atlas integration would not be a single container:
it would need an API service, worker/queue posture, Playwright/browser runtime,
Redis isolation, optional PostgreSQL/Supabase-auth decisions, generated secrets,
Kong route handling, consumer opt-ins, and resource limits. That effort is not
worth spending until Firecrawl-specific functionality is required.

## 6. Risks & open questions
- AGPL-3.0 is a stronger copyleft posture than Atlas' preferred permissive
  service candidates; adoption needs an explicit owner decision.
- The browser/worker footprint is heavier than Crawl4AI and raises memory,
  sandboxing, update, and cold-start concerns.
- Self-hosted Firecrawl configuration includes queue/admin secrets, optional
  database/auth settings, optional proxy settings, CPU/RAM thresholds, and
  webhook/public URL exposure choices that need an operator story.
- Atlas must decide whether Firecrawl would use its own Redis/Postgres
  containers, Atlas Redis with isolated DBs, or Supabase-backed auth.
- The exact cloud-vs-self-host feature boundary for agent, MCP, search, parse,
  and advanced Fire-engine behavior must be verified again before any adoption
  ticket.

## 7. Revisit criteria
Reconsider Firecrawl only when all of these are true:

- Crawl4AI leaves an important extraction gap with a reproducible Atlas workflow
  or page class.
- Atlas explicitly accepts the AGPL-3.0 boundary for the intended distribution
  and deployment model.
- Atlas needs Firecrawl-specific functionality such as v2 search, scrape, crawl,
  map, batch scrape, interact, agent, parse, hosted/local MCP integration, or
  richer structured extraction that cannot be achieved through existing
  services.
- The heavier browser, queue, Redis, optional PostgreSQL/Supabase-auth, proxy,
  token, webhook, and resource-limit story has an operator-ready design.

## 8. Future service contract if adopted
- **Tracks:** `gen-ai-rag` and `all`. Do not add a new track unless Atlas later
  creates a dedicated content-ingestion track.
- **Category:** `media`, matching Crawl4AI and Browserless. If Atlas later
  introduces an `ingestion` category, Firecrawl would be a candidate for it.
- **Sources:** `FIRECRAWL_SOURCE=disabled|container`; disabled by default.
  Consider `localhost` only after a stable external endpoint/auth contract is
  documented.
- **Wizard placement:** after Crawl4AI and before Browserless-style browser
  infrastructure, with prompt copy that describes Firecrawl as a heavier
  AGPL-licensed alternative for proven Crawl4AI gaps.
- **Ports and routes:** allocate ports through Atlas topology/category slots and
  custom `BASE_PORT` math. Expected Kong alias is `firecrawl.localhost` only in
  container mode. There should be no public route by default; Kong auth must be
  paired with Firecrawl API-key or bearer-token requirements.
- **Dependencies:** Kong for routing; optional SearXNG for search delegation;
  queue/worker components; Redis or an isolated Redis DB; Playwright/browser
  runtime; optional PostgreSQL/Supabase authentication; optional proxy and LLM
  credentials. Prefer LiteLLM for LLM calls when upstream allows an
  OpenAI-compatible base URL.
- **Downstream consumers:** Local Deep Researcher, n8n, backend, Hermes/MCP,
  Weaviate ingestion, and MinIO provenance/archive flows. Add explicit
  `data_flow.calls` edges for every consumer.
- **Init/secrets:** generate API/admin tokens, queue/admin secrets such as
  `BULL_AUTH_KEY`, optional proxy credentials, optional webhook settings, and
  any DB/auth credentials. Do not scatter unrestricted tokens through notebooks
  or n8n nodes.
- **Edge cases:** disabled Crawl4AI, disabled SearXNG, internal URL crawling
  disabled by default, stale `.env`, missing tokens, custom `BASE_PORT`,
  track-disabled behavior, high-memory browser failures, proxy failures,
  webhook/public URL exposure, rate limits, and generated-doc drift.

## 9. Tests required if adopted later
- Manifest schema and topology tests for source values, category, aliases,
  generated env vars, and track membership.
- Compose/source permutation coverage for disabled/container and any future
  localhost mode.
- Kong route audits that prove `firecrawl.localhost` appears only when enabled.
- Consumer config tests for Local Deep Researcher, n8n, backend, and any
  Hermes/MCP registration.
- Docs drift, research schema, link checks, and generated README/diagram checks.

## 10. Upstream evidence
- https://github.com/firecrawl/firecrawl
- https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md
- https://github.com/firecrawl/firecrawl/blob/main/docker-compose.yaml
- https://github.com/firecrawl/firecrawl/blob/main/LICENSE
- https://docs.firecrawl.dev/features/interact
- https://docs.firecrawl.dev/features/parse
- https://docs.firecrawl.dev/use-cases/developers-mcp
