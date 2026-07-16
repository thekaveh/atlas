---
category-fit: apps
generated: 2026-07-04
license: MIT
name: Perplexica (Vane)
referenced-by: [searxng]
slug: perplexica
type: external-service
upstream: https://github.com/ItzCrazyKns/Vane
---

# Perplexica (Vane)

## 1. Headline
Self-hosted, privacy-focused "AI answering engine" that turns SearXNG results into cited, conversational answers — drops in next to Open WebUI as a dedicated research-style chat.

## 2. Problem it solves
SearXNG already returns ranked link results, but the stack has no UI that *combines* those results with an LLM call to produce a Perplexity-style cited answer in one turn. Open WebUI's RAG-web-search feature does retrieval-augmented chat but isn't optimised for that flow, and local-deep-researcher is async/multi-step. Perplexica is the missing single-shot "ask the web" front-end that uses the SearXNG instance already running.

## 3. Deferred decision (2026-07-04)

Keep Perplexica/Vane deferred. Upstream is now Vane under `ItzCrazyKns/Vane`, latest reviewed release is Vane v1.12.2, and the project remains MIT. The product is stronger than the old note captured: Docker can run a full image with bundled SearXNG or a `slim-latest` image against an existing SearXNG, and Vane exposes `/api/providers`, `/api/search`, citations, streaming via Server-Sent Events, uploads, widgets, and search modes. That is useful, but Atlas should not add another browser app until it intentionally wants a distinct single-shot cited-answer surface separate from Open WebUI and Local Deep Researcher.

Future contract if reopened:

- Tracks: `gen-ai-rag`, `gen-ai-eng`, and `all`; do not add Vane to creative, ML, or data-engineering tracks by default.
- Category: `apps`, because Vane is a user-facing browser application.
- Source values: `VANE_SOURCE=disabled|container|localhost`; disabled by default. Use `VANE_SOURCE`, not a new `PERPLEXICA_SOURCE`, while documenting the old name as historical.
- Wizard placement: after Open WebUI, SearXNG, and Local Deep Researcher prompts, with copy explaining duplicate UX, cited-answer positioning, upload storage, and model/provider setup.
- Ports and routes: allocate `VANE_PORT`, honor custom `BASE_PORT`, and expose only a protected Kong route once route auth and provenance policy are settled. Preferred future alias is `vane.localhost`; avoid adding both `perplexica.localhost` and `vane.localhost`.
- Dependencies and consumers: required SearXNG and LiteLLM for the slim/local-stack path; optional Ollama, Crawl4AI, MinIO for upload/report artifacts, backend for mediated sessions, n8n for automation, and Open WebUI/Local Deep Researcher only as overlap/reference surfaces rather than runtime dependencies.
- Topology: add `data_flow.calls` for Vane -> SearXNG and Vane -> LiteLLM only if Atlas wires provider configuration at startup. Do not imply Open WebUI, Local Deep Researcher, backend, or n8n call Vane until a real integration exists.
- `init companion`: likely needed to render provider settings, point Vane at LiteLLM/SearXNG, seed safe default models, configure upload storage, and avoid requiring manual first-run setup in the browser.
- Edge cases: route auth, source provenance, stale citations, duplicate UX with Open WebUI web search, overlap with Local Deep Researcher reports, bundled SearXNG accidentally starting a second search stack, provider secrets in UI state, uploaded-file retention, MinIO ownership, model mismatch, custom `BASE_PORT`, stale `.env`, and generated-doc drift.

Revisit when Atlas deliberately designs a product-level cited-answer app that is faster and simpler than Local Deep Researcher but more focused than Open WebUI chat with web search.

## 4. Stack wiring sketch
- perplexica → searxng via `SEARXNG_API_URL=http://searxng:8080` for the search backend.
- perplexica → litellm via `OPENAI_API_BASE_URL=http://litellm:4000/v1` (Perplexica supports OpenAI-compatible endpoints, so LiteLLM's gateway covers every provider in the stack).
- perplexica → ollama via `OLLAMA_API_URL=http://ollama:11434` for local embeddings when LiteLLM passthrough is undesirable.
- kong → perplexica as `perplexica.localhost` alias for browser access.

## 5. Effort
small — single docker image (`itzcrazykns1337/vane:latest`), one new manifest under `services/perplexica/`, two env vars, one Kong alias. No DB (uses SearXNG + LLM at request time).

## 6. Risks & open questions
- Project was recently renamed Perplexica → Vane; upstream image tag and config keys may shift. Pin to a known-good tag.
- Duplicates some Open WebUI functionality once `ENABLE_RAG_WEB_SEARCH` is wired (see searxng row, missing-pair #1). Worth keeping both only if the UX really diverges.
- Requires SearXNG `formats: [..., json]` to be enabled (already true in `services/searxng/config/settings.yml`).
- No native auth; sits behind Kong but Kong does not currently enforce auth on alias routes.

## 7. Why now (and why not sooner)
SearXNG + LiteLLM + Ollama are all in place, and an `ENABLE_RAG_WEB_SEARCH` wiring for Open WebUI (the higher-priority gap) covers most of the use case. Perplexica becomes interesting once users want a UI tuned specifically for cited web answers rather than a general chat with a toggle.

## 8. Upstream evidence
- https://github.com/ItzCrazyKns/Vane
- https://github.com/ItzCrazyKns/Vane/releases/tag/v1.12.2
- https://github.com/ItzCrazyKns/Vane/tree/master/docs/API/SEARCH.md
- Docker setup documented at the repo root README, including `SEARXNG_API_URL=http://your-searxng-url:8080`.
