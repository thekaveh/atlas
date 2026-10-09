# 5.2.14. Docling (Document Processor engine)

Docling is the engine behind the **Document Processor** role, selected with
`DOC_PROCESSOR_SOURCE`. See [Document Processor](../doc-processor/README.md) for
setup, configuration and integration.

## 1. Engine quick reference

- **Image (GPU):** `pytorch/pytorch:2.13.0-cuda12.6-cudnn9-runtime` (digest-pinned; used as
  `BASE_IMAGE` in the GPU provider Dockerfile); the provider requirements keep
  `torch==2.13.0` and its matching `torchvision==0.28.0` patch pair.
- **License:** MIT (IBM)
- **Activation:** `DOC_PROCESSOR_SOURCE=docling-container-gpu` (or
  `docling-localhost` for host-installed Docling)
- **In-container port:** 8000
- **Host port:** `${DOC_PROCESSOR_PORT}` (computed from `BASE_PORT` by the
  bootstrapper)
- **Readiness:** `GET /health` starts configured converter construction off the
  API event loop and returns `503 starting` until it succeeds. Invalid pipeline
  or device configuration returns `503 unavailable`; health reports the
  converter only and does not claim lazily loaded model artifacts.

**Timeouts and fallback.**

- Backend and Celery extraction wait up to `DOCLING_INFERENCE_TIMEOUT_SECONDS` plus 30 s for Docling. `TIKA_TIMEOUT_SECONDS` does not apply.
- Long-tail formats go straight to Tika: legacy Office (`.doc`, `.xls`, `.ppt`), `.epub`, mail, RTF, OpenDocument and archives. Atlas's Docling providers answer an unsupported format with 500, not 415.
- Kong cuts `api.localhost` requests at 300 s. The HTTP client gets a 504 while the conversion continues.
- A Celery RAG ingestion has its own limits, `RAG_INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS` and `RAG_INGESTION_TASK_TIME_LIMIT_SECONDS`. By default they are the larger of 3840 / 3900 s and the global Celery limits.

## 2. Dependencies & Integrations

### 2.1. Current — Upstream (this service calls)

_No upstream calls._

### 2.2. Current — Downstream (services that call this)

| Service | Category | Status |
|---|---|---|
| kong | infra | current |
| docling-lightrag-adapter | media | optional: DOC_PROCESSOR_SOURCE=docling-container-gpu or docling-localhost |
| celery | agents | current |
| n8n | agents | current |
| backend | apps | current |
| jupyterhub | apps | current |

### 2.3. Architecture diagram

![docling architecture](./architecture.svg)

[Open the full-size diagram](./architecture.html) for a full-screen view.

### 2.4. Future — Missing pair integrations

_No high-confidence opportunities identified._

### 2.5. Future — Candidate new services

_No high-confidence opportunities identified._

### 2.6. Future — Unused features in this service

_No high-confidence opportunities identified._

## 3. Capabilities & limitations

Support tier: **experimental** — Capability contract declared; no cited cold-start, workflow, or upgrade qualification run yet (evidence at `v0.1.0`).

| Capability | Status | Verification | Notes |
|---|---|---|---|
| Docling document conversion sources | partial | tested | Atlas provides an NVIDIA GPU container and an existing-host endpoint, but no CPU container or Atlas-managed native Docling lifecycle. |
| Structured extraction and bounded chunking | supported | tested | The provider converts documents once and renders structured markdown or JSON with validated OCR, table, formula, code, chunk-size, overlap, and total-chunk controls. |
| Authenticated bounded provider API | partial | tested | Atlas-managed Docling routes require a generated bearer token and enforce upload, admission, and inference deadlines by default, but AUTH_MODE=disabled is an explicit rollback. |
| Truthful model readiness | partial | tested | Health stays unavailable until converter construction succeeds, but it does not certify every lazily loaded model artifact needed by a later document. |
| LightRAG conversion bundle | supported | tested | An authenticated internal route renders the JSON and Markdown bundle consumed by the isolated asynchronous LightRAG compatibility adapter. |
