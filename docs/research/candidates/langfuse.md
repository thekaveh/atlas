---
category-fit: agents
generated: 2026-05-19
license: MIT
name: Langfuse
referenced-by:
  - backend
  - comfyui
  - hermes
  - litellm
  - local-deep-researcher
  - minio
  - n8n
  - ollama
  - open-webui
slug: langfuse
type: external-service
upstream: https://github.com/langfuse/langfuse
---

# Langfuse

## 1. Headline
Self-hostable observability and prompt-trace store for LLM and diffusion workflows, capturing structured traces, evaluations, and cost telemetry.

## 2. Problem it solves
The stack runs multi-step generation through LiteLLM, Hermes, n8n, and ComfyUI but has no shared trace store — a failed n8n→ComfyUI image step or a Hermes tool call is only visible in raw container logs. Langfuse provides a single backend for trace ingest (LiteLLM has a first-class callback) plus a UI to inspect prompts, images, and latency. It also unblocks offline evals over historical generations.

## 3. Stack wiring sketch
- litellm → langfuse via `LANGFUSE_HOST` + `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` callbacks (built-in LiteLLM integration).
- hermes → langfuse via OpenTelemetry / Langfuse SDK trace export for tool calls.
- n8n → langfuse via HTTP node logging ComfyUI prompt + output URLs to a generation trace.
- comfyui → langfuse via a custom node that posts the executed workflow JSON + output `/view` URL on `executed` websocket events.
- langfuse → supabase (reuses the existing Postgres) for its persistence layer; langfuse → redis for queue/cache.

## 4. Effort
medium — Langfuse ships an official Docker Compose; the work is one new manifest, two SOURCE variants (container, disabled), Kong alias, and wiring LiteLLM callbacks. Postgres reuse needs schema isolation.

## 5. Risks & open questions
- Langfuse v3 requires ClickHouse for analytics; v2 stays on Postgres. Pick v2 to avoid pulling in a new datastore.
- Reusing the stack Postgres needs a dedicated schema/role to avoid colliding with Supabase auth.
- Image payloads in traces can grow; needs S3/MinIO blob offload via Langfuse's media-upload API.

## 6. Why now (and why not sooner)
With Hermes, n8n, and ComfyUI now all callable from LiteLLM and from each other, a generation can touch 3+ services per request. Without a trace store the only debugging surface is `docker compose logs`, which doesn't correlate ComfyUI websocket events with the n8n flow that triggered them.

## 7. Upstream evidence
- https://github.com/langfuse/langfuse
- https://langfuse.com/docs/integrations/litellm
- https://langfuse.com/self-hosting/docker-compose

## 8. Cross-references
- Referenced from `docs/research/rows/n8n.md` — n8n is a strong producer of traces (multi-step image/automation flows benefit from a shared trace store).
- Referenced from `docs/research/rows/litellm.md` — LiteLLM is the universal LLM funnel; one callback config captures every chat/embedding call in the stack.
- Referenced from `docs/research/rows/hermes.md` — Hermes adds nested agent-loop / tool-call traces that are unreadable in plain logs; Langfuse spans tie them to LiteLLM completions.
- Referenced from `docs/research/rows/minio.md` — Langfuse needs S3-compatible blob storage for long-term trace media payloads; the new MinIO tier provides that in-network.
- Referenced from `docs/research/rows/ollama.md` — Ollama is the inference upstream; Langfuse's built-in LiteLLM callback captures every Ollama request end-to-end without per-consumer instrumentation.
- Referenced from `docs/research/rows/open-webui.md` — Open WebUI is the primary chat surface; pairs with the proposed Pipelines candidate to capture interactive-chat traces alongside the LiteLLM-side ones.
- Referenced from `docs/research/rows/local-deep-researcher.md` — LDR is a LangGraph app with first-class Langfuse callback support; tracing the iterative research loop surfaces per-loop token spend and per-source latency.
- Referenced from `docs/research/rows/backend.md` — backend's research/memory/ComfyUI endpoints are the primary in-stack callers of LiteLLM and would benefit most from per-trace prompt + cost visibility.
