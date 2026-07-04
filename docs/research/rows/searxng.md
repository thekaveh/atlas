---
service: searxng
category: media
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://docs.searxng.org/dev/search_api.html
  - https://docs.searxng.org/admin/settings/settings_engines.html
  - https://docs.searxng.org/admin/settings/settings_server.html
  - https://github.com/ItzCrazyKns/Perplexica
  - https://github.com/open-webui/open-webui
  - https://docs.n8n.io/integrations/builtin/credentials/httprequest/
  - services/searxng/service.yml
  - services/searxng/config/settings.yml
  - services/searxng/compose.yml
  - services/n8n/init/config/searxng-research-workflow.json
  - services/open-webui/service.yml
  - services/open-webui/extras/tools/research_tool.py
  - services/searxng/README.md
---

# searxng — Integration Research

## 1. Missing-pair integrations

- **searxng ↔ open-webui**
  - Why valuable: Open WebUI ships a first-class "Web Search" toggle (per the upstream README: "Perform web searches using 15+ providers including SearXNG"). Today it is silently disabled — no env vars are set in `services/open-webui/service.yml` — so chat sessions have no live-web grounding without going through the side-loaded `research_tool.py` extra.
  - Mechanism sketch: in `runtime_adaptive.open-web-ui.adapts_to`, add `searxng`; set `ENABLE_RAG_WEB_SEARCH=true`, `RAG_WEB_SEARCH_ENGINE=searxng`, `SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>` when `SEARXNG_SOURCE != disabled`.
  - Effort: small.
  - Risks / open questions: SearXNG `settings.yml` already enables `formats: [html, json]` (confirmed line 78-80) so no upstream change is needed. The current `method: POST` is fine — Open WebUI uses GET against `SEARXNG_QUERY_URL`. Hermes already templates a `SEARXNG_INTERNAL_URL`; converge on one var name to avoid drift (see `reference_litellm_quirks` for prior dual-alias pain).
  - Confidence: high (upstream README explicitly names SearXNG as a supported web-search provider).

- **searxng ↔ n8n**
  - Why valuable: the repo ships `services/n8n/init/config/searxng-research-workflow.json`, a seeded workflow that HTTP-calls `http://searxng:8080/search`, but n8n's manifest does **not** declare searxng in `depends_on.optional` or `runtime_deps.optional`. If the user disables searxng, the workflow imports fine and silently 404s on first run.
  - Mechanism sketch: add `searxng` to `services/n8n/service.yml` `runtime_deps.n8n.optional` and emit an info_message; optionally add the `n8n-nodes-langchain.toolSearXng` LangChain sub-node to the seeded workflow so AI-agent nodes can call SearXNG without raw HTTP.
  - Effort: small.
  - Risks / open questions: the seeded workflow uses `format=json` which requires SearXNG's `formats:` list to include `json` (already true). LangChain `toolSearXng` requires an n8n version with the AI-cluster sub-nodes; gate behind a version check.
  - Confidence: high (workflow file exists in-repo; n8n upstream confirms the native LangChain SearXNG sub-node).

- **searxng ↔ weaviate**
  - Why valuable: searxng results are ephemeral — every query re-hits external engines. Caching the top-N hits (URL, title, snippet, fetched-at) in a Weaviate class lets backend / hermes do hybrid retrieval over "what we've already searched" without re-burning engine quota or tripping CAPTCHAs.
  - Mechanism sketch: an n8n workflow or a small fetcher in `services/backend/` calls `GET http://searxng:8080/search?format=json`, then POSTs each hit to `http://weaviate:8080/v1/objects` in a `WebSearchResult` class (vectorized by multi2vec-clip or a text2vec module).
  - Effort: medium.
  - Risks / open questions: cache-invalidation policy (TTL vs re-fetch on demand). Schema must coexist with existing Weaviate classes. License/ToS for caching third-party engine snippets.
  - Confidence: medium (mechanism is straightforward; product question is fuzzy).

- **searxng ↔ comfyui**
  - Why valuable: SearXNG's image category (Bing Images, Flickr, Wallhaven, Unsplash, etc., per upstream engines docs) returns direct image URLs. ComfyUI workflows that want a reference image for img2img currently require the user to paste URLs by hand. An n8n bridge "search images → seed ComfyUI workflow" closes that loop.
  - Mechanism sketch: n8n `HTTP Request → http://searxng:8080/search?q=<q>&categories=images&format=json` → pipe top URL into ComfyUI's `LoadImage` node via `http://comfyui:18188/prompt`.
  - Effort: medium.
  - Risks / open questions: SearXNG image proxy off by default (`image_proxy: false` in `settings.yml:101`) — URLs are third-party-hosted and may 403 ComfyUI. Enabling `SEARXNG_IMAGE_PROXY` mitigates but adds memory pressure.
  - Confidence: medium.

## 2. Candidate new services

- **Perplexica (Vane)** → `../candidates/perplexica.md`
  - Headline: Self-hosted Perplexity-style AI answering engine that consumes SearXNG + an OpenAI-compatible LLM (LiteLLM).
  - Other consumers in stack: searxng, litellm, ollama, kong.

- **Browserless** → `../candidates/browserless.md`
  - Headline: Deferred headless-browser service for SearXNG result pipelines that prove they need sessionful JavaScript rendering beyond Crawl4AI.
  - Other consumers in stack: n8n, doc-processor, backend.

## 3. Per-service feature gaps

- **`open_metrics` Prometheus endpoint** — `services/searxng/config/settings.yml:18` leaves `open_metrics: ''`, so the `/metrics` endpoint is disabled. With Prometheus already in the Phase B candidate set (`docs/research/candidates/prometheus.md`), exposing engine-latency and error-rate stats is a near-free win. Effort: small.
- **`image_proxy: true`** — currently off (settings line 101). Turning it on (or wiring `SEARXNG_IMAGE_PROXY`) lets ComfyUI / Open WebUI fetch image results without third-party-host 403s, at the cost of RAM. Effort: small.
- **Per-engine tuning for science / academic engines** — SearXNG ships arXiv, Crossref, OpenAlex, PubMed, Semantic Scholar engines that are currently all default-off. Enabling them upgrades local-deep-researcher and hermes from "general web" to "scholarly" search. Effort: small.
- **`limiter: true` with Redis/Valkey bot detection** — searxng is already wired to Redis (required dep), but the limiter plugin is off, so the rate-limiter never engages even though the backing store exists. Effort: small.
- **JSON-RPC `engines=` filter exposure in Open WebUI / Hermes** — both upstreams call `/search` with no engine pinning, so a slow / failing engine drags the whole p99. Pass `engines=duckduckgo,brave` (or similar) from the calling service. Effort: small.
