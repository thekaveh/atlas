---
category-fit: media
generated: 2026-07-04
license: SSPL-1.0
name: Browserless
referenced-by: [n8n, searxng]
slug: browserless
type: external-service
upstream: https://github.com/browserless/browserless
---

# Browserless

## 1. Headline
Containerized browser-automation service for Puppeteer, Playwright, REST, and
WebSocket sessions; useful when Atlas needs persistent browser sessions rather
than Crawl4AI's one-shot extraction API.

## 2. Problem it solves
Browserless becomes interesting when a RAG, agent, or workflow needs true
browser infrastructure: authenticated sessions, multi-step forms, portal
scraping, screenshots, PDF rendering, persistent profiles, live debugging, or
Playwright/Puppeteer compatibility. Current upstream docs also describe queueing,
concurrency controls, token authentication, V2 browser-specific connection
paths, and separate open-source, enterprise Docker, cloud, and private
deployment models.

## 3. Deferred decision (2026-07-04)
Atlas should stay Crawl4AI-first and must not add `services/browserless/service.yml` yet.
Crawl4AI is already implemented as the first-line JS-capable extraction service
for Local Deep Researcher and n8n. Browserless is lower-level browser automation
infrastructure with an SSPL-1.0/commercial-license posture and Chromium memory
cost, so it should not become the default extraction path.

Keep Browserless deferred until Crawl4AI cannot cover a named, reproducible
workflow that specifically requires persistent browser sessions, direct
Puppeteer/Playwright sessions, screenshots/PDF rendering, or authenticated
multi-step browsing. The resource and auth model must be designed before any
route is exposed.

## 4. Stack wiring sketch
No current Atlas wiring should be added while Browserless is deferred. If
adopted later, the expected topology would be:

- n8n -> Browserless for curated browser-automation workflows and HTTP/WebSocket
  render steps.
- SearXNG -> n8n/backend -> Browserless when search results need sessionful
  JavaScript rendering before extraction.
- backend -> Browserless only through explicit routes with validation, timeouts,
  screenshot/PDF size limits, and provenance capture.
- Hermes -> Browserless through a guarded agent-browser or curated MCP path, not
  through an unauthenticated browser endpoint.
- Crawl4AI -> Browserless only as a future fallback for persistent-session cases
  that Crawl4AI cannot handle directly.
- doc-processor, Weaviate, and MinIO consume rendered artifacts through existing
  ingestion and provenance paths rather than direct Browserless writes.

## 5. Effort
Medium. The open-source image is operationally simpler than a crawler stack with
its own database, but Atlas still needs a manifest, topology slot, generated
token, resource limits, WebSocket-aware route policy, consumer opt-ins, docs,
and tests. A production posture may also need volume handling for user-data
profiles, enterprise `KEY` handling, health/pressure checks, and explicit
queue/concurrency defaults.

## 6. Risks & open questions
- SSPL-1.0 or commercial license acceptance is required before adoption.
- Chromium memory and sandboxing costs are higher than the current Crawl4AI
  first slice; defaults for `CONCURRENT`, `QUEUED`, `TIMEOUT`, CPU pressure,
  memory pressure, and browser cleanup must be conservative.
- Browserless uses token authentication. Atlas must generate `BROWSERLESS_TOKEN`
  and keep `KEY` and `TOKEN` semantics separate if enterprise images are ever
  supported.
- WebSocket routing through Kong needs explicit testing; no public route should
  be added before auth, CORS, and path semantics are proven.
- Persistent user-data/profile volumes can leak cookies and session state if
  they are shared, retained too long, or exposed to multiple consumers.

## 7. Revisit criteria
Reconsider Browserless only when all of these are true:

- Crawl4AI cannot cover a critical JavaScript-rendered or sessionful workflow.
- Atlas accepts the SSPL-1.0 or commercial-license posture.
- Atlas has resource defaults for `CONCURRENT`, `QUEUED`, `TIMEOUT`,
  health/pressure thresholds, and leaked-session cleanup.
- Atlas has a token, route, CORS, WebSocket, and optional profile-volume design
  before exposure.

## 8. Future service contract if adopted
- **Tracks:** `gen-ai-rag` and `all`. Consider a future agent/browser-automation
  track only if Atlas creates one.
- **Category:** `media`, matching Crawl4AI and Firecrawl. If Atlas later
  introduces `ingestion` or `browser-automation`, Browserless could move there.
- **Sources:** `BROWSERLESS_SOURCE=disabled|container`; disabled by default.
  Consider `localhost` only for an operator-managed Browserless or Playwright
  endpoint with a documented token contract.
- **Wizard placement:** after Crawl4AI and the Firecrawl deferred note, with copy
  that Browserless is heavier persistent browser infrastructure for proven
  JS/session gaps.
- **Ports and routes:** allocate ports through Atlas topology/category slots and
  custom `BASE_PORT` math. Expected alias is `browserless.localhost` only when
  enabled. There should be no public route by default; REST and WebSocket paths
  must preserve Browserless token semantics.
- **Dependencies:** no database by default for the open-source image; optional
  persistent profile/user-data volume; optional enterprise `KEY`; optional
  metrics/pressure/health endpoints. Downstream consumers may include n8n,
  SearXNG-render pipelines, backend, Hermes, Crawl4AI fallback, doc-processor,
  Weaviate, and MinIO provenance flows.
- **Init/secrets:** generate `BROWSERLESS_TOKEN`; keep enterprise `KEY` separate
  from API `TOKEN`; document `CONCURRENT`, `QUEUED`, `TIMEOUT`, CORS,
  `ALLOW_GET`, `ALLOW_FILE_PROTOCOL`, health thresholds, and optional user-data
  volume settings.
- **Edge cases:** disabled Crawl4AI, missing/rotated token, stale `.env`, custom
  `BASE_PORT`, WebSocket routing through Kong, queue full/429, leaked browser
  sessions, high memory pressure, profile volume collisions, permissive CORS,
  `ALLOW_GET`, `ALLOW_FILE_PROTOCOL`, and generated-doc drift.

## 9. Tests required if adopted later
- Manifest schema and topology tests for source values, category, aliases,
  generated env vars, and track membership.
- Compose/source permutation coverage for disabled/container and any future
  localhost mode.
- Kong route audits for REST and WebSocket paths, proving `browserless.localhost`
  appears only when enabled.
- Consumer config tests for n8n, backend, Hermes/agent-browser, and any Crawl4AI
  fallback path.
- Docs drift, research schema, link checks, and generated README/diagram checks.

## 10. Why now (and why not sooner)
Not now. Browserless should follow demonstrated Crawl4AI gaps, explicit license
acceptance, and a clear browser-automation workflow. It is useful infrastructure,
but Atlas does not yet have the named workflow or auth/resource posture needed
to justify adding it to the service graph.

## 11. Upstream evidence
- https://github.com/browserless/browserless
- https://github.com/browserless/browserless/blob/main/LICENSE
- https://docs.browserless.io/enterprise/open-source
- https://docs.browserless.io/enterprise/docker/config
- https://docs.browserless.io/enterprise/long-queues
- https://docs.browserless.io/enterprise/migrate-from-v1
- https://docs.browserless.io/open-api

## 12. Cross-references
- `../rows/searxng.md` - search -> render -> extract pipeline.
- `../candidates/crawl4ai.md` - current first-line extraction service.
- `../candidates/firecrawl.md` - deferred higher-level crawler alternative.
