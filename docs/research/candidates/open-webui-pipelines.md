---
category-fit: apps
generated: 2026-05-19
license: MIT
name: Open WebUI Pipelines
referenced-by: [open-webui]
slug: open-webui-pipelines
type: external-service
upstream: https://github.com/open-webui/pipelines
---

# Open WebUI Pipelines

## Headline
First-party plugin server that runs Python "pipes" and "filters" in front of any OpenAI-compatible client, enabling rate-limiting, content filtering, custom RAG, function-calling handlers, and third-party tracing (Langfuse, Opik) without forking Open WebUI.

## Problem it solves
The original candidate aimed to centralize cross-cutting chat middleware such as logging, redaction, quota checks, and A/B routing in one OpenAI-compatible sidecar that LiteLLM and Open WebUI could both target. That sidecar model is no longer the conservative default for Atlas because current Open WebUI Filter Functions cover the first redaction/inspection slice without adding another worker container.

## Atlas 2026 update
Open WebUI's current documentation marks Pipelines as legacy for new deployments and recommends in-process Functions, Tools, OpenAPI servers, or MCP servers instead, so standalone Pipelines are intentionally not added in the current Atlas slice. The Atlas implementation follows that guidance with the disabled-by-default `Atlas Safe Prompt Middleware` Filter Function under `services/open-webui/extras/functions/`.

The function path preserves the useful part of this candidate - a middleware hook for redaction before Open WebUI calls LiteLLM - without adding a Pipelines SOURCE env var, a new compose service, a new port, or a Kong alias. LiteLLM + Langfuse remains the stack-wide observability path, and OpenLIT remains deferred as a standalone service/UI.

## Stack wiring sketch
- open-webui → pipelines via `OPENAI_API_BASE_URLS=http://pipelines:9099` (added alongside the existing LiteLLM URL, or fronted by litellm)
- litellm → pipelines as a regular `openai`-compatible upstream so Hermes and other LiteLLM consumers get the same filter chain
- pipelines → langfuse for trace export (companion candidate)
- kong → pipelines via a `pipelines.localhost` alias for admin UI

## Effort
medium — new compose fragment, new SOURCE variants (container, disabled), Kong alias, and an init step to drop curated pipeline scripts into the pipelines volume; minimal env wiring beyond that.

## Risks & open questions
- Decide whether pipelines fronts LiteLLM or sits behind it — affects which service "owns" the gateway role.
- Pipelines is single-tenant: scaling needs sticky sessions or a queue.
- Filter pipelines require explicit client support — Hermes (non-WebUI client) gets pipe-type only.

## Why now (and why not sooner)
The stack already has the natural consumers (Open WebUI, LiteLLM, Hermes) and an obvious tracing target (Langfuse, also proposed). Earlier, the stack lacked a unified gateway role — LiteLLM filled it in 2025, and Pipelines slots in as the request-time middleware layer next to it.

## Upstream evidence
- https://github.com/open-webui/pipelines — repo, `ghcr.io/open-webui/pipelines:main`, port 9099, MIT license.
- https://docs.openwebui.com/features/ — "Pipelines: Modular plugin framework for filters, providers, and custom logic."
