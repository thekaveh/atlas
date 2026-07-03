---
category-fit: media
generated: 2026-07-03
license: Apache-2.0
name: Crawl4AI
referenced-by: [local-deep-researcher]
slug: crawl4ai
type: external-service
upstream: https://github.com/unclecode/crawl4ai
---

# Crawl4AI

## Headline
LLM-native open-source crawler with chunking, schema-based extraction, and a Docker server image exposing `/crawl` for any HTTP client.

## Problem it solves
LDR's research loop bottlenecks on the quality of page extraction. The current pipeline either uses SearXNG snippets (short) or `FETCH_FULL_PAGE` HTML (noisy). Crawl4AI provides chunk-aware markdown extraction plus a `JsonCssExtractionStrategy` for site-specific schemas, which lets the LDR `summarize_sources` node see semantically coherent passages instead of raw DOM. It is permissively licensed (Apache-2.0), unlike Firecrawl, making it a lower-risk drop-in for forks that need to stay under MIT/Apache. As of the 0.9.0 Docker server, the API is secure-by-default when exposed beyond loopback and requires `CRAWL4AI_API_TOKEN` bearer auth for every endpoint except `/health`.

## Stack wiring sketch
- local-deep-researcher → crawl4ai via `POST http://crawl4ai:11235/crawl` returning markdown for each search-result URL.
- n8n → crawl4ai HTTP node for scheduled ingestion of upstream-doc pages into weaviate.
- weaviate → crawl4ai indirectly: backend writes chunked output as `Source` objects with the existing multi2vec-clip vectorizer.
- backend → crawl4ai as an alternative `FETCH_FULL_PAGE` backend selectable via env.

## Effort
small — Crawl4AI ships a ready-made `unclecode/crawl4ai:0.9.0` Docker image with the server preconfigured on port 11235. New service folder, single container, Kong alias, generated bearer token, SOURCE toggle (container | disabled).

## Risks & open questions
- The hosted server image bundles Playwright (~1 GB on disk) and needs `--shm-size=1g` for stable Chromium.
- Schema-based extraction needs per-site selectors; for ad-hoc research the chunked-markdown mode is what LDR will use.
- Project moves fast; pin `CRAWL4AI_IMAGE=unclecode/crawl4ai:0.9.0` (or a digest-derived override) to avoid silent API breaks.

## Why now (and why not sooner)
Until Phase B the stack had no fetcher with JS rendering. With LDR now active and `FETCH_FULL_PAGE` an obvious next env var, picking the lightweight Apache-2.0 option (vs. AGPL Firecrawl) keeps the stack license-compatible.

## Upstream evidence
- https://github.com/unclecode/crawl4ai
- https://docs.crawl4ai.com/core/self-hosting/
- https://hub.docker.com/r/unclecode/crawl4ai
