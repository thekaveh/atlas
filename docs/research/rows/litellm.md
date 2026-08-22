---
service: litellm
category: llm
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/litellm/service.yml
  - services/litellm/init/scripts/init.py
  - services/litellm/README.md
  - services/backend/service.yml
  - services/docling/service.yml
  - services/minio/service.yml
  - services/neo4j/service.yml
  - services/searxng/service.yml
  - https://docs.litellm.ai/docs/proxy/logging
  - https://docs.litellm.ai/docs/proxy/guardrails/quick_start
  - https://docs.litellm.ai/docs/proxy/bucket
  - https://docs.litellm.ai/docs/audio_transcription
  - https://docs.litellm.ai/docs/text_to_speech
  - https://docs.litellm.ai/docs/proxy/virtual_keys
---

# litellm — Integration Research

## 1. Missing-pair integrations

- **litellm ↔ minio**
  - Why valuable: LiteLLM ships a first-class S3 logger that persists full request/response payloads (incl. prompts, tool calls, token counts) to any S3-compatible bucket. MinIO is already in the stack and unused for LLM telemetry — wiring it would give offline replay, prompt-regression diffs, and an audit trail without adding a new dependency.
  - Mechanism sketch: set `litellm_settings.success_callback: ["s3"]` + `s3_callback_params` pointing at `http://minio:9000` with `MINIO_ROOT_USER/MINIO_ROOT_PASSWORD`; provision a `litellm-logs` bucket via the existing `minio-init` container.
  - Effort: small
  - Risks / open questions: prompts can contain secrets — bucket should be private and lifecycle-policied; cost of writing every call to S3 on high-RPS workloads.
  - Confidence: high (LiteLLM S3 logger is documented and stable; MinIO is S3-API-compatible).

- **litellm ↔ stt-provider** (parakeet / speaches engine)
  - Why valuable: LiteLLM exposes a unified `/v1/audio/transcriptions` (Whisper-shaped) endpoint that proxies to any backend. Today every consumer (n8n, backend, hermes, jupyterhub) hits `STT_ENDPOINT` directly, bypassing LiteLLM's auth, rate-limit, spend, and Kong-alias affordances. Routing STT through LiteLLM gives one URL + one bearer token for *every* model surface — matching the existing chat/embeddings pattern.
  - Mechanism sketch: add an `audio_transcription` row in `model_list` with `model: openai/<name>` and `api_base: ${STT_ENDPOINT}` (the speaches engine is OpenAI-compatible; parakeet would need a thin shim or be excluded). Init script extension lives in `services/litellm/init/scripts/init.py`.
  - Effort: medium
  - Risks / open questions: parakeet's native API is not OpenAI-compatible — needs an adapter or restriction to `speaches-*` sources only; large file uploads through Kong add overhead.
  - Confidence: medium (LiteLLM audio routing is documented; engine compatibility per-source is the unknown).

- **litellm ↔ tts-provider** (chatterbox / speaches engine)
  - Why valuable: same argument as STT — LiteLLM has `/v1/audio/speech` routing. Today consumers hit TTS engines directly. Routing through LiteLLM aligns telemetry, auth, and the Kong alias surface.
  - Mechanism sketch: add a TTS row in `model_list` with `model: openai/<voice>` and `api_base: ${TTS_ENDPOINT}` for the speaches engine; chatterbox uses a non-OpenAI shape (port 4123) and would need an adapter.
  - Effort: medium
  - Risks / open questions: chatterbox API shape differs; voice/model naming conventions vary by engine.
  - Confidence: medium.

- **litellm ↔ doc-processor** (docling)
  - Why valuable: docling extracts text/structure from PDFs and feeds RAG pipelines. Today there is no LiteLLM model that lets an OpenAI-compatible client say "parse this PDF" through the unified gateway. Exposing docling as a LiteLLM custom provider gives n8n / hermes / open-webui a single auth surface for document ingestion.
  - Mechanism sketch: register docling as a LiteLLM custom provider (Python plugin loaded via `custom_provider_map`) routing to `http://docling-gpu:8000/v1/document/convert`; or add a thin OpenAI-compatible shim service in front of docling.
  - Effort: large
  - Risks / open questions: docling isn't an LLM — shoehorning into the chat/completions taxonomy is awkward; the custom-provider hook may force a synthetic prompt template.
  - Confidence: low (LiteLLM custom providers exist but are intended for LLM-shaped APIs).

- **litellm ↔ searxng**
  - Why valuable: LiteLLM's MCP server / tools integration lets you advertise an external tool to every chat-completions client. Wiring searxng as a built-in `search_web` tool via LiteLLM means open-webui, n8n, hermes, jupyterhub get web search for free — without per-consumer plumbing.
  - Mechanism sketch: define a LiteLLM MCP server in `litellm_settings.mcp_servers` that calls `http://searxng:8080/search?format=json`; expose under tool name `web_search`.
  - Effort: medium
  - Risks / open questions: MCP server support in LiteLLM is newer (post-1.60) — version pin (`v1.83.14-stable.patch.2`) needs verification; tool-calling capability varies by model.
  - Confidence: medium.

_No high-confidence pair identified for **neo4j** — LiteLLM does not natively read/write graphs._

## 2. Candidate new services

- **Langfuse** → `../candidates/langfuse.md`
  - Headline: Self-hosted LLM observability (traces, prompts, evals) with a documented LiteLLM callback.
  - Other consumers in stack: hermes, n8n, open-webui, backend, local-deep-researcher, jupyterhub.

## 3. Per-service feature gaps

- **Guardrails** — Why pursue: presidio PII redaction, lakera prompt-injection scanning, hide-secrets — all configurable per virtual key. Stack handles user data but has zero LLM-side PII controls today. Effort: medium.
- **Virtual keys + team budgets** — Why pursue: the master key is the only credential; consumers all share it. Per-service virtual keys with spend caps would give n8n / jupyterhub / open-webui isolated budgets and revocable creds. Effort: small.
- **Prompt caching** — Why pursue: LiteLLM can transparently cache prompts in Redis (already deployed) keyed by content hash, slashing cost on repeated tool-call chains common in hermes/n8n flows. Effort: small.
- **`/v1/audio/transcriptions` + `/v1/audio/speech` routing** — Why pursue: see pair-integrations above. Effort: medium.
- **Fallback model chains** — Why pursue: declare `fallbacks: [{ "gpt-5": ["claude-opus", "ollama/qwen3.8"] }]` so a cloud outage degrades gracefully to local Ollama. Effort: small.
