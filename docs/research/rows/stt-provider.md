---
service: stt-provider
category: media
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - services/parakeet/service.yml
  - services/speaches/service.yml
  - services/stt-provider/README.md
  - services/minio/service.yml
  - services/weaviate/service.yml
  - services/redis/service.yml
  - services/doc-processor/service.yml
  - services/openclaw/service.yml
  - services/supabase/service.yml
  - services/neo4j/service.yml
  - https://github.com/speaches-ai/speaches
  - https://github.com/speaches-ai/speaches/blob/master/README.md
  - https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
  - https://github.com/m-bain/whisperx
  - https://github.com/pyannote/pyannote-audio
---

# stt-provider — Integration Research

## 1. Missing-pair integrations

- **stt-provider ↔ minio**
  - Why valuable: Transcripts are an HTTP response nothing persists. Pushing source audio + transcript JSON to MinIO gives every service a stable URL and enables re-transcription on engine swap without re-uploading audio.
  - Mechanism sketch: New bucket `stt-transcripts` provisioned by `minio-init`; producers put `s3://stt-transcripts/<sha256>.wav` plus a sidecar `.json` from a post-transcribe hook in the backend; S3 SigV4 over `http://minio:9000`.
  - Effort: small
  - Risks / open questions: Where the hook lives (backend vs n8n vs each producer); raw-audio lifecycle policy.
  - Confidence: high (MinIO already pre-provisions per-service buckets via `minio-init`).

- **stt-provider ↔ weaviate**
  - Why valuable: Indexing durable transcripts turns long-form audio (meetings, podcasts, voice notes) into a semantically searchable corpus alongside the doc-processor pipeline. No engine ships search; this is the natural downstream.
  - Mechanism sketch: `Transcript` class with `text`, `start_ms`, `end_ms`, `source_audio_uri`, vectorized by the running `text2vec-*` module; writes via `http://weaviate:8080/v1/objects` from the same post-transcribe hook.
  - Effort: medium
  - Risks / open questions: Chunking strategy (utterance vs sliding window) undecided; speaker attribution missing until diarization lands (see WhisperX).
  - Confidence: medium (Weaviate REST is stable; chunking design is open).

- **stt-provider ↔ redis**
  - Why valuable: Transcription is expensive and deterministic in `(audio-sha256, model, language)`. A cache cuts repeat cost to ~zero for n8n loops, re-runs, demos. Redis is also the natural broker for streaming chunks once Speaches' realtime API is wired.
  - Mechanism sketch: `redis://redis:6379/2`, key `stt:{sha256}:{model}:{lang}` → transcript JSON, TTL 30d. Sidecar wrapper in the backend, or a Kong plugin in front of `STT_ENDPOINT`.
  - Effort: small
  - Risks / open questions: Cache placement (sidecar vs in-engine) — Speaches has no upstream cache hook.
  - Confidence: medium (Redis is in-stack and stable; placement is open).

- **stt-provider ↔ doc-processor**
  - Why valuable: Docling parses PDFs/Office docs; it does not handle audio. Composing `stt-provider → doc-processor` gives a unified "any media → markdown" ingest, where audio is transcribed first then chunked/cleaned by docling.
  - Mechanism sketch: Producer-side composition — caller hits `STT_ENDPOINT`, then POSTs transcript text to `http://docling-gpu:8000/v1/document/convert` as `text/plain`. No new service.
  - Effort: small
  - Risks / open questions: Whether docling's text branch preserves STT-JSON timestamps (likely lost).
  - Confidence: medium (both endpoints documented; composed path unverified end-to-end).

- **stt-provider ↔ openclaw**
  - Why valuable: Telegram/WhatsApp/Discord deliver voice notes as audio. OpenClaw routes text through Hermes but has no audio path; wiring STT lets the gateway accept voice messages and reply in text or (via tts-provider) voice.
  - Mechanism sketch: OpenClaw middleware POSTing incoming audio to `${STT_ENDPOINT}/v1/audio/transcriptions` (multipart), forwarding the result to its existing LLM-routing path.
  - Effort: small
  - Risks / open questions: Per-platform format normalization (OGG-Opus, AAC) — engines accept both but detection is on the caller.
  - Confidence: medium (OpenClaw supports custom middleware; exact hook point needs verification).

- **stt-provider ↔ supabase**
  - Why valuable: Transcript metadata (user, session, source URI, model, language, duration) belongs in a relational store. Gives Open WebUI / backend a "my transcripts" view keyed by Supabase JWT `sub`.
  - Mechanism sketch: `transcripts` table via PostgREST at `http://supabase-api:3000`, RLS on `auth.uid()`; the post-transcribe hook writes rows pointing at MinIO URIs.
  - Effort: medium
  - Risks / open questions: RLS policy design; keeping MinIO + Weaviate + Supabase writes consistent (or a single hook owns all three).
  - Confidence: medium (Supabase + PostgREST are in-stack; orchestrator undesigned).

## 2. Candidate new services

- **WhisperX** → `../candidates/whisperx.md`
  - Headline: Watchlisted diarized long-form audio engine for future named meeting/audio ingestion workflows.
  - Other consumers in stack: backend, n8n, open-webui, hermes, openclaw, minio, weaviate

## 3. Per-service feature gaps

- **Streaming / Realtime SSE+WebSocket** — Speaches ships SSE-streamed transcription and a WebSocket realtime API; we only expose the batch `/v1/audio/transcriptions`. Why pursue: enables live captions in Open WebUI and live agent voice loops in Hermes. Effort: medium.
- **Translation endpoint** — Speaches/Faster-Whisper support speech translation; we never set `AUDIO_STT_TRANSLATE_*` or expose `/v1/audio/translations`. Why pursue: cheap multilingual UX gain. Effort: small.
- **Sentiment analysis** — Upstream Speaches advertises emotional-tone analysis from audio. Why pursue: feeds n8n / backend dashboards without a separate NLP service. Effort: small.
- **Per-engine model hot-swap** — Speaches loads/unloads models on demand; we hard-pin one model per engine. Why pursue: lets users A/B `distil-large-v3` vs `large-v3` without restarting. Effort: small.
- **Diarization** — no engine in-stack does it; covered by the WhisperX candidate above. Why pursue: prerequisite for meeting-grade transcripts. Effort: medium (via the candidate).
- **Word/segment timestamps in API responses** — Parakeet and Speaches both expose them; our Open WebUI wiring requests plain `json` and discards them. Why pursue: needed for click-to-seek UX and for Weaviate chunking by utterance. Effort: small.
