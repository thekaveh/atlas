---
category-fit: media
generated: 2026-07-04
license: MIT
name: Unmute (Kyutai)
referenced-by: [tts-provider]
slug: unmute
type: external-service
upstream: https://github.com/kyutai-labs/unmute
---

# Unmute (Kyutai)

## Headline
A self-hosted, MIT-licensed WebSocket service that wraps any text LLM with Kyutai's streaming STT + TTS in an OpenAI-Realtime-compatible protocol.

## Problem it solves
Today the stack's voice loop is half-duplex: open-webui or backend records a full utterance, sends it to parakeet/speaches for transcription, calls LiteLLM, then waits for the full WAV back from chatterbox/speaches. End-to-end latency is several seconds and there's no barge-in. Unmute terminates a single browser WebSocket and streams partial tokens in both directions, matching the OpenAI Realtime API format the JS SDKs already speak, so it slots in without rewriting clients.

## Deferred decision (2026-07-04)

Keep Unmute deferred. It is the right shape for a future realtime speech workflow, but Atlas should not add `services/unmute/service.yml` or `UNMUTE_SOURCE=disabled|container-gpu|localhost` until OpenAI Realtime WebSocket compatibility is verified against actual clients, VRAM and runtime budgets are acceptable, and it can reuse or coexist with the current STT Provider and TTS Provider without duplicating the whole audio stack.

Future contract if reopened:

- Tracks: `gen-ai-creative`, `gen-ai-eng`, and `all`; do not add it to RAG, ML, or data tracks by default.
- Category: `media`, because it is a realtime voice/audio surface rather than a general platform daemon.
- Source values: `UNMUTE_SOURCE=disabled|container-gpu|localhost`; disabled by default, with localhost reserved for users who run Unmute outside Atlas.
- Wizard placement: after STT Provider and TTS Provider, with copy describing realtime speech workflow, OpenAI Realtime WebSocket compatibility, 16 GB VRAM x86_64 risk, no Apple Silicon or aarch64 container guarantee, and no replacement for batch STT/TTS.
- Kong behavior: no default Kong route until WebSocket auth, CORS, backpressure, session lifetime, and route exposure are specified. Any route must use the media topology allocator, honor custom `BASE_PORT`, and remain explicitly opt in.
- Dependencies and consumers: LiteLLM, STT Provider, TTS Provider, Open WebUI, backend, Hermes, and n8n may connect only through explicit consumer configuration; Atlas should not auto-wire clients before compatibility tests exist.
- Topology: add `data_flow.calls`, docs, and diagrams only when consumer integration actually exists; otherwise a manifest would imply a graph contract Atlas cannot honor.
- `init companion`: likely needed for model-cache setup, `voices.yaml` validation, GPU checks, session defaults, and safe demo voice provisioning.
- Edge cases: disabled STT/TTS, missing GPU, low VRAM, OpenAI Realtime protocol drift, WebSocket auth/CORS failures, stale sessions, turn interruption, voice consent, logs or recordings that capture private speech, stale `.env`, and generated-doc drift.

## Stack wiring sketch
- open-webui → unmute via WebSocket (Kong route `unmute.localhost` → `ws://unmute:8000/v1/realtime`)
- unmute → litellm via `http://litellm:4000/v1/chat/completions` (the wrapped text LLM)
- unmute → parakeet (STT) and chatterbox / speaches (TTS) — reuses the engines already in the stack as the audio I/O legs
- backend → unmute for server-initiated voice sessions (e.g. hermes voice agent)

## Effort
large — Kong needs WebSocket route handling, the bootstrapper needs an `UNMUTE_SOURCE` toggle, and unmute brings its own Kyutai STT/TTS models that may compete with parakeet/speaches for VRAM.

## Risks & open questions
- Kyutai's bundled STT/TTS may not be substitutable with parakeet/speaches without forking unmute; if not, this is a third audio stack rather than a wrapper.
- 16 GB VRAM minimum on x86_64 only; no aarch64 / Apple Silicon — would need a `localhost`-equivalent fallback path.
- Protocol is "based on" OpenAI Realtime with "extra messages" — Open WebUI / SDK compatibility needs verification.
- Watermarking / licensing of Kyutai voices for commercial use needs separate audit.

## Why now (and why not sooner)
The OpenAI Realtime API became the de-facto streaming voice protocol over 2025; open-source clones (unmute, plus Pipecat etc.) only stabilised in late 2025. Before that, the stack would have had to invent its own duplex protocol. Unmute now gives us the spec for free.

## Upstream evidence
- https://github.com/kyutai-labs/unmute — README confirms MIT license, Docker Compose deploy, OpenAI Realtime-format WebSocket, 16 GB VRAM requirement, custom `voices.yaml`.
