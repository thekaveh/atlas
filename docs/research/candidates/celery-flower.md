---
category-fit: infra
generated: 2026-05-19
license: BSD-3-Clause
name: Celery + Flower
referenced-by: [backend]
slug: celery-flower
type: external-service
upstream: https://github.com/celery/celery
---

# Celery + Flower

## 1. Headline
Redis-backed async worker tier and web monitor so the backend's long-running research, memory-consolidate, and ComfyUI generation calls stop blocking the FastAPI request loop.

## 2. Problem it solves
Several backend endpoints (`/research/start`, `/comfyui/generate?wait_for_completion=true`, `/memory/consolidate`) are unbounded in latency — they can run for minutes. Today they tie up a uvicorn worker for the full duration, starving other requests. There's also no durable retry on transient LiteLLM or Weaviate failures. Redis is already in the stack, so a Celery worker tier is a near-zero-infra add that unlocks fire-and-forget semantics, retries, scheduled jobs (LangMem consolidation), and a Flower dashboard for visibility.

## 3. Stack wiring sketch
- backend (FastAPI) → celery-worker via `redis://redis:6379/1` broker; enqueues research and consolidate jobs.
- celery-worker → litellm, weaviate, comfyui, supabase — same upstream surfaces as backend, just from a worker container reusing `services/backend/app`.
- celery-beat → backend job registry; fires `LANGMEM_CONSOLIDATION_INTERVAL` ticks.
- flower (web UI) → kong as `flower.localhost`; reads broker stats from redis.

## 4. Effort
small — one additional service folder (`services/celery/`) reusing backend's Dockerfile + a `--worker` entrypoint; one Kong alias; existing redis bumps to a second logical DB.

## 5. Risks & open questions
- Code-sharing strategy: bind-mount `services/backend/app` into the worker vs publishing a shared image — both have downsides.
- Task serialization: pydantic models cross the queue, need a json-safe schema layer.
- Backpressure when Ollama/ComfyUI are slow — Celery's prefetch count needs tuning per worker.
- Observability overlaps with Langfuse — decide which owns "job ran" vs "LLM call traced".

## 6. Why now (and why not sooner)
Each new backend endpoint (research, comfyui, memory) has pushed average request duration further from the FastAPI sweet spot. The next obvious feature — batch document ingestion through Docling + MinIO — is impossible on a synchronous request model.

## 7. Upstream evidence
- https://github.com/celery/celery — broker-based distributed task queue, BSD-licensed.
- https://flower.readthedocs.io/en/latest/ — Flower web UI for Celery monitoring.
