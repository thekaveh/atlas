---
service: ollama
category: llm
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://github.com/ollama/ollama/blob/main/docs/api.md
  - https://github.com/ollama/ollama/blob/main/envconfig/config.go
  - https://github.com/ollama/ollama/blob/main/README.md
  - services/ollama/service.yml
  - services/ollama/README.md
  - services/litellm/README.md
---

# ollama — Integration Research

## 1. Missing-pair integrations

Note: backend, open-webui, n8n, jupyterhub, local-deep-researcher, hermes, and weaviate already reach Ollama indirectly through LiteLLM (`LITELLM_BASE_URL`). The pairs below cover gaps where the LiteLLM proxy hides Ollama-native surface (model management, runtime introspection, private GGUF import) that has real value if exposed directly.

- **ollama ↔ backend**
  - Why valuable: backend has no view of Ollama runtime state. `/api/ps` exposes loaded models, VRAM footprint, and TTL; `/api/show` exposes capabilities and Modelfile. An admin endpoint that surfaces this turns "is the model warm?" from a guess into a fact, and lets the UI warn when KEEP_ALIVE is about to evict a model mid-session.
  - Mechanism sketch: backend reads `OLLAMA_ENDPOINT` (already in `runtime_sc.environment`) and calls `GET ${OLLAMA_ENDPOINT}/api/ps` + `/api/show`. New `/admin/llm/status` route, container-source only.
  - Effort: small
  - Risks / open questions: not reachable when `LLM_PROVIDER_SOURCE=none`; needs a graceful 404 surface. Auth assumed network-local.
  - Confidence: high (endpoints documented in Ollama api.md).

- **ollama ↔ jupyterhub**
  - Why valuable: notebooks doing model research want raw `/api/pull`, `/api/create`, `/api/show`, embeddings, and structured-output `format` — none of which round-trip cleanly through LiteLLM. Right now JupyterHub users can only hit the OpenAI-shaped subset.
  - Mechanism sketch: inject `OLLAMA_HOST=http://ollama:11434` into jupyterhub singleuser env (parallel to existing `LITELLM_BASE_URL`); pre-install `ollama` Python client in the notebook image.
  - Effort: small
  - Risks / open questions: host must be omitted when `LLM_PROVIDER_SOURCE=none`; document that pulled models persist in the shared `ollama-data` volume.
  - Confidence: high.

- **ollama ↔ minio**
  - Why valuable: `ollama-pull` only fetches from the public registry. Private GGUFs (licensed, fine-tuned, air-gapped) cannot enter the stack today. MinIO is already provisioned for artifacts.
  - Mechanism sketch: new `ollama-import` init step that reads `OLLAMA_MINIO_BUCKET` keys, streams each GGUF to `/root/.ollama/blobs`, then `POST /api/create` with a generated `FROM ./blob` Modelfile.
  - Effort: medium
  - Risks / open questions: blob sha addressing vs. MinIO ETag mismatch; resumable uploads for multi-GB GGUFs; cleanup on key deletion.
  - Confidence: medium.

- **ollama ↔ n8n**
  - Why valuable: n8n workflows already call LiteLLM but can't drive `/api/pull` — meaning "nightly, ensure `qwen3:8b` is pulled" or "on webhook, hot-swap a model" cannot be authored.
  - Mechanism sketch: ship an n8n credential pointing at `http://ollama:11434` and a short README recipe; n8n's HTTP Request node handles streaming `pull` progress lines.
  - Effort: small
  - Risks / open questions: container-source only; long-poll connections may collide with n8n's default execution timeout.
  - Confidence: medium.

## 2. Candidate new services

- **Langfuse** → `../candidates/langfuse.md`
  - Headline: self-hosted LLM trace, eval, and prompt-management store.
  - Other consumers in stack: litellm (native callback), hermes, backend, n8n, local-deep-researcher, open-webui.

- **OpenLIT** → `../candidates/openlit.md`
  - Headline: Deferred OpenTelemetry-native GenAI observability platform for a future Ollama/GPU telemetry gap not covered by Langfuse plus OTel/Tempo/Loki.
  - Other consumers in stack: backend, hermes, jupyterhub, weaviate, litellm.

## 3. Per-service feature gaps

- **Quantized KV cache (`OLLAMA_KV_CACHE_TYPE=q8_0` / `q4_0`)** — Why pursue: ~2× context length at the same VRAM budget, currently unset (defaults to f16). Effort: small.
- **`OLLAMA_FLASH_ATTENTION=1`** — Why pursue: free throughput on supported GPUs; currently unset. Effort: small.
- **`OLLAMA_NUM_PARALLEL` / `OLLAMA_MAX_LOADED_MODELS`** — Why pursue: stack runs multi-tenant (backend + open-webui + n8n + hermes) but uses Ollama defaults (1 / per-GPU). Tuning prevents head-of-line blocking. Effort: small.
- **`/api/ps` + `/api/show` surface** — Why pursue: gives the UI and ops scripts visibility into VRAM occupancy, model capabilities, and load TTL — currently invisible. Effort: small.
- **Native structured-output `format` (JSON schema)** — Why pursue: richer than the OpenAI `response_format` LiteLLM forwards; useful for hermes skills and backend agents that need strict schemas. Effort: medium.
- **Modelfile customization pipeline** — Why pursue: stack-specific system prompts, templates, and ADAPTERs (LoRA) cannot be authored today; `ollama-pull` only consumes public tags. Effort: medium.
- **`OLLAMA_KEEP_ALIVE` tuning** — Why pursue: default 5m evicts models between idle bursts; per-model overrides via `keep_alive` request field would cut cold-start tail latency for hermes/backend. Effort: small.
