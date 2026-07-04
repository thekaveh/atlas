---
category-fit: media
generated: 2026-07-04
license: BSD-2-Clause
name: WhisperX
referenced-by: [stt-provider]
slug: whisperx
type: external-service
upstream: https://github.com/m-bain/whisperx
---

# WhisperX

## Headline
A drop-in fourth STT engine that adds speaker diarization and wav2vec2 word-aligned timestamps — capabilities no engine in the stack ships today.

## Watchlist decision (2026-07-04)

Keep WhisperX on the watchlist for now: Atlas **must not add `services/whisperx/service.yml` yet** because the stack does not have a named meeting/audio ingestion workflow that needs diarization, word-level timestamps, and long-form transcript provenance. Existing Atlas STT providers already expose OpenAI-shaped `/v1/audio/transcriptions`; WhisperX is not needed for generic transcription.

WhisperX becomes valuable when Atlas can name a product path such as meeting recordings, podcasts, earnings calls, voice-note RAG, or multi-speaker support transcripts. Until then, adding a GPU-heavy diarization service would add model-token, licensing, artifact, and queueing complexity without a workflow that proves the cost.

Future service shape, if a later audio-ingestion ticket promotes this:

- Track membership: `rag`, `voice`, and `all`. Do not add it to default setup paths.
- Service category: `media`, matching STT/TTS and document-processing services.
- Source values/default: `WHISPERX_SOURCE=disabled|container-gpu|localhost`, disabled by default.
- Relationship to STT selector: explicitly decide whether WhisperX is a separate service or a new `STT_PROVIDER_SOURCE` option. Do not silently make it the default STT engine.
- Wizard placement: RAG/voice ingestion section after STT provider selection, with copy that this is for diarized long-form audio, not the default STT path.
- Topology and port strategy: allocate one `media` topology slot for the API only if a service manifest is added. Worker/model-cache containers stay internal.
- Kong alias and route behavior: `whisperx.localhost` only when a container API exists; route should be treated as an internal/local ingestion API, not a broad public upload surface.
- Required dependencies: likely `whisperx -> minio` for source audio and transcript artifacts, and `whisperx -> supabase` for transcript metadata/provenance.
- Optional dependencies: `whisperx -> weaviate` or LightRAG/doc-processor for RAG indexing, n8n/backend/open-webui/hermes/openclaw as consumers, and Redis/Celery for long-running job orchestration.
- Diarization gate: document Hugging Face/pyannote token requirements, model access/user agreement, model license/terms, offline-cache behavior, and missing/invalid-token behavior. Diarization must be optional or fail clearly.
- Resource gate: document expected CPU/GPU behavior, VRAM/model-cache needs, long-file timeouts, concurrency limits, and why a GPU source is preferred for long meetings.
- Provenance gate: define source audio URI, sha256, duration, language, model/revision, diarization model, alignment model, speaker IDs, segment start/end/word timestamps, user/session/project namespace, and downstream chunk IDs before indexing transcripts.
- Init companion: maybe. Add one only if Atlas seeds model caches, creates transcript artifact buckets/metadata schemas, or validates token availability.
- Tests required for a future service PR: manifest validation, source validation, env assembly, topology/category, track membership, Kong route/auth, disabled default, token/env validation, missing-token behavior, provenance schema fixtures, artifact path conventions, compose source-permutation coverage, custom `BASE_PORT`, docs drift, and an opt-in smoke over a short audio fixture if legally safe.
- Edge cases: no Hugging Face token, token accepted but model access not granted, CPU-only host, insufficient VRAM, very long files, multi-channel audio, speaker-label instability across reruns, duplicate transcript chunks, PII retention, stale `.env`, disabled MinIO/Supabase/Weaviate, and generated-doc drift.

## Problem it solves
Speaches (Faster-Whisper) and Parakeet both transcribe but neither attributes utterances to speakers, and Whisper's native timestamps are utterance-level only. Meeting summaries, podcast pipelines, and conversational analytics need "who said what when" with provenance. WhisperX wraps Faster-Whisper + wav2vec2 alignment + pyannote diarization behind one CLI/Python surface, but Atlas should only package it once that meeting/audio workflow exists.

## Stack wiring sketch
- backend → whisperx via `POST http://whisperx:8000/v1/audio/transcriptions` (OpenAI-shape wrapper around `whisperx.transcribe`)
- whisperx → minio via `s3://transcripts/<session-id>.json` for diarized transcript artifacts
- n8n → whisperx via the same HTTP endpoint for meeting-recording workflows
- weaviate ← whisperx-emitted utterance chunks (speaker-keyed) for semantic search across long-form audio
- hermes → whisperx as a skill, surfacing speaker-attributed transcripts as agent context

## Effort
medium-to-large — no official Atlas-ready service image is available, so Atlas would need a thin API wrapper, model-cache strategy, GPU source handling, artifact/provenance plumbing, and a token-aware diarization path.

## Risks & open questions
- pyannote diarization model is HF-gated — requires `HUGGING_FACE_HUB_TOKEN` with explicit ToS acceptance.
- WhisperX is CPU-viable but realistically GPU-only for long files; would need a `container-gpu` variant only.
- BSD-2 + MIT (pyannote) license stack is permissive but the diarization model weights have their own non-commercial caveats — needs a docs callout.
- Maintenance velocity: WhisperX historically lags upstream Whisper releases; we'd pin a known-good revision.

## Why now (and why not sooner)
Not now. Revisit when meeting/audio ingestion becomes a named RAG or voice workflow with an owner, sample audio, retention policy, and downstream transcript consumer.

## Upstream evidence
- https://github.com/m-bain/whisperx
- https://github.com/pyannote/pyannote-audio
- https://huggingface.co/pyannote/speaker-diarization-community-1
- https://huggingface.co/pyannote/speaker-diarization-3.1
