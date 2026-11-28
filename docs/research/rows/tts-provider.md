---
service: tts-provider
category: media
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://github.com/speaches-ai/speaches
  - https://github.com/speaches-ai/speaches/blob/master/README.md
  - https://github.com/travisvn/chatterbox-tts-api
  - https://github.com/travisvn/chatterbox-tts-api/blob/main/README.md
  - https://github.com/resemble-ai/chatterbox
  - https://github.com/kyutai-labs/unmute
  - services/tts-provider/service.yml
  - services/chatterbox/service.yml
  - services/speaches/service.yml
  - services/tts-provider/README.md
---

# tts-provider — Integration Research

## 1. Missing-pair integrations

- **tts-provider ↔ silo**
  - Why valuable: Chatterbox's `/voices` library and any persisted reference WAVs live on the container's ephemeral FS today, so a rebuild wipes user-registered voices. Silo already hosts other artifact buckets; a `tts-voices` bucket plus an audio-output cache bucket gives durable storage.
  - Mechanism sketch: bind-mount a fuse-mounted or rclone-synced Silo bucket at `/app/voices` for chatterbox, or sidecar that mirrors `/voices` POSTs to `s3://tts-voices/` via the upstream `/voices` GET/POST endpoint.
  - Effort: medium
  - Risks / open questions: chatterbox container expects local filesystem semantics; sidecar adds a sync race; speaches has no equivalent custom-voice slot.
  - Confidence: high (chatterbox `/voices` GET/POST + multipart `voice_file=` per upstream docs).

- **tts-provider ↔ redis**
  - Why valuable: notification/UI phrases repeat (welcome messages, n8n alerts, hermes ack lines). Caching `(model, voice, text-hash) → wav-bytes` cuts realtime-factor-bound CPU cost on Kokoro/Piper and the >2s cold weights load on Chatterbox.
  - Mechanism sketch: small FastAPI shim or LiteLLM-style middleware in front of `TTS_ENDPOINT` that looks up `redis://redis:6379` before forwarding to `/v1/audio/speech`.
  - Effort: medium
  - Risks / open questions: cache keys must include all knobs (exaggeration, cfg_weight, temperature, voice_file digest); audio blobs may be large — consider TTL + size cap.
  - Confidence: medium (Redis is already in-stack; the shim is new code).

- **tts-provider ↔ doc-processor**
  - Why valuable: turns ingested PDFs/HTML into audiobook WAVs — a natural "read this document" feature surfaced through backend/Open WebUI. Closes the loop: doc-processor extracts text, TTS narrates.
  - Mechanism sketch: backend orchestrator chunks doc-processor's markdown output, POSTs each chunk to `${TTS_ENDPOINT}/v1/audio/speech`, concatenates WAV segments, writes to Silo.
  - Effort: medium
  - Risks / open questions: chunk-boundary prosody artifacts; long-document jobs need async queue (n8n or backend background task); voice consistency across chunks.
  - Confidence: high (both services already speak HTTP; orchestration is application code).

- **tts-provider ↔ supabase**
  - Why valuable: voice metadata (owner, language, source clip, registered-by user) belongs in a relational table, not just chatterbox's in-memory `/voices` registry. Lets Open WebUI users see their own voices and admins audit usage.
  - Mechanism sketch: backend writes a `tts_voices` row in Supabase on every chatterbox `POST /voices`; on startup, a small reconciler re-POSTs registered voices from Silo+Supabase back into chatterbox.
  - Effort: medium
  - Risks / open questions: dual write needs idempotency; speaches doesn't expose user-uploaded voices, so the table only covers chatterbox.
  - Confidence: medium (depends on chatterbox `/voices` POST semantics stabilising upstream).

- **tts-provider ↔ openclaw**
  - Why valuable: openclaw is the messaging-platform agent gateway; voice-message replies to Telegram/Discord/etc. dramatically lift presence over text-only bots. Pairs naturally with stt-provider for the inbound side.
  - Mechanism sketch: openclaw calls `${TTS_ENDPOINT}/v1/audio/speech` per outgoing message; uploads the returned WAV as a voice attachment via its platform adapters.
  - Effort: small
  - Risks / open questions: per-platform audio-format constraints (Telegram wants OGG/Opus, Discord wants Opus) — needs ffmpeg transcode hop.
  - Confidence: medium (openclaw can already hit arbitrary HTTP; transcode wiring is the unknown).

## 2. Candidate new services

- **Unmute (Kyutai)** → `../candidates/unmute.md`
  - Headline: WebSocket OpenAI-Realtime-compatible voice loop that wraps any text LLM behind streaming STT + TTS.
  - Other consumers in stack: open-webui, backend, hermes, openclaw

## 3. Per-service feature gaps

- **Speaches Realtime API / speech-to-speech** — Why pursue: upstream README advertises a Realtime API and async speech-to-speech but the stack only consumes `/v1/audio/speech` and `/v1/audio/transcriptions`. Wiring it would enable low-latency voice agents in Open WebUI + hermes. Effort: large.
- **Chatterbox streaming endpoints (`/v1/audio/speech/stream`, SSE)** — Why pursue: cuts perceived latency to ~1-2s (per upstream README) versus waiting for full WAV; Open WebUI's audio player supports streaming chunks. Effort: small.
- **Chatterbox `/v1/audio/speech/upload` + `/voices` POST in Open WebUI** — Why pursue: end-users can clone their own voice from the chat UI, today only API callers can. Effort: medium (UI work + auth wiring).
- **Chatterbox paralinguistic tags (`[laugh]`, `[cough]`)** — Why pursue: richer narration for doc-processor audiobooks and hermes responses; available on the Turbo model per upstream. Effort: small (prompt-template tweak in backend).
- **Speaches dynamic model load/unload** — Why pursue: stack pins `SPEACHES_TTS_MODEL` at boot; upstream auto-loads requested models then unloads on idle. Lets users pick Kokoro vs Piper per request without restart. Effort: small.
- **Chatterbox `exaggeration` / `cfg_weight` / `temperature` knobs** — Why pursue: emotion + pace controls (defaults 0.5/0.5/0.8) are exposed only via raw API; Open WebUI doesn't surface them. Effort: small.
- **Chatterbox `/status`, `/memory`, `/config` introspection** — Why pursue: feed into backend health dashboard and Grafana when added; surfaces VRAM pressure before OOM. Effort: small.
