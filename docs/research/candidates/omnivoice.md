---
category-fit: media
generated: 2026-07-04
license: Apache-2.0 (OSS) / proprietary (SaaS at omnivoice.app)
name: OmniVoice (k2-fsa)
referenced-by: []
slug: omnivoice
type: external-service
upstream: https://github.com/k2-fsa/OmniVoice
---

# OmniVoice (k2-fsa)

## Headline

A 0.6 B-parameter diffusion-LM TTS built on `Qwen/Qwen3-0.6B-Base`. Apache-2.0, 600+ languages, zero-shot voice cloning from 3–10 s reference clips, 24 kHz output, claimed RTF ~0.025 on GPU. The matching SaaS at omnivoice.app is a paid web UI over the same model with no public developer API.

## Problem it solves

Genuine differentiator versus the existing TTS lineup:

| | Languages |
|---|---|
| Speaches → Kokoro | 8–9 |
| Speaches → Piper | 30+ |
| Chatterbox | 23 |
| **OmniVoice** | **600+** |

For users outside the Indo-European top tier (e.g. Tagalog, Quechua, regional Indian languages), every existing engine forces a fallback to English. OmniVoice closes that gap.

Voice-cloning capability overlaps with Chatterbox (both do 3–10 s zero-shot). The language coverage is the only meaningful net-new capability.

## Status as of 2026-06-03

**Both the SaaS and the OSS are operationally unready as drop-in stack engines:**

- **omnivoice.app (SaaS)** has no documented developer API. Pricing is per-credit ($9.90–$49.90/mo), nav has no "API" / "Developers" entry, no docs page, no key-issuance flow. Until a public REST surface lands, a `omnivoice-cloud` source variant is impossible.
- **k2-fsa/OmniVoice (OSS)** ships **CLI + Python only** — `omnivoice-infer`, `OmniVoice.from_pretrained(...).generate(...)` returning a numpy array. **No HTTP server, no `/v1/audio/speech` route, no Dockerfile, no published image, no upstream FastAPI wrapper analogous to `travisvn/chatterbox-tts-api`.** Library is at 0.1.5 (5 releases in ~2 months, 45 total commits, 41 open issues) — moving fast.

## Deferred decision (2026-07-04)

Keep OmniVoice deferred. Atlas should not add `services/omnivoice/service.yml` and should not extend `TTS_PROVIDER_SOURCE` until a public API, a maintained community HTTP wrapper, or Speaches support exists. The blocker is still the immature HTTP wrapper path: Atlas would otherwise own the serving layer for a young TTS library.

Future contract if reopened:

- Tracks: `gen-ai-creative`, `gen-ai-eng`, and `all`; voice language coverage is useful, but it should remain outside RAG/data tracks by default.
- Category: `media`, matching current STT Provider and TTS Provider engines.
- Source values: extend `TTS_PROVIDER_SOURCE` only after a supported endpoint exists; possible values are `omnivoice-container-gpu` and `omnivoice-localhost`, disabled by default through the existing TTS selector.
- Wizard placement: inside TTS Provider, with copy explaining GPU requirements, voice-cloning consent, language coverage, and wrapper maturity.
- Kong behavior: no standalone `omnivoice.localhost`; if adopted, it should route through the existing `tts.localhost` alias only when selected as the active TTS engine.
- Dependencies and consumers: STT Provider is not involved; TTS Provider, Open WebUI, Hermes, LiteLLM-adjacent flows, backend, and n8n should only see the existing OpenAI-compatible TTS contract.
- Topology: reuse TTS Provider patterns, custom `BASE_PORT`, docs, diagrams, and `data_flow.calls` only after the endpoint exists.
- `init companion`: likely needed for model-cache creation, voice sample validation, GPU/profile checks, and safe default voices.
- Edge cases: missing wrapper, API breakage, disabled TTS Provider, CPU fallback, low VRAM, model-cache growth, unsafe voice-cloning input, missing consent, stale `.env`, and generated-doc drift.

## Stack wiring sketch (if pursued)

Closest precedent is Chatterbox, where a third-party FastAPI wrapper exposes `/v1/audio/speech` over a Resemble library call. For OmniVoice, that wrapper does not exist — the stack would own it.

- `services/omnivoice/` mirroring `services/chatterbox/`:
  - `Dockerfile` — `python:3.12-slim` + torch + `omnivoice` pip + a hand-written `server.py` (~80 lines, FastAPI).
  - `server.py` — exposes `POST /v1/audio/speech` (JSON path) and the multipart-with-`voice_file` form for cloning, matching Chatterbox's contract for routing-layer reuse.
  - `compose.yml` — GPU deploy block, model-cache volume, port `OMNIVOICE_PORT` (new slot in allocator).
  - `service.yml` — manifest with `env` schema (`OMNIVOICE_IMAGE`, `OMNIVOICE_PORT`, `OMNIVOICE_MODEL`, `OMNIVOICE_LOCALHOST_PORT`).
- `services/tts-provider/`: add `omnivoice-container-gpu` / `omnivoice-localhost` to the `TTS_PROVIDER_SOURCE` enum, with the 4-seam CLI flag plumbing (Click decl, `source_mapping`, collector dict, `wizard_screen` lambda) per `project_cli_source_flag_three_seams.md`.
- Auto-regen: `README.md` + `architecture.{svg,html}` via `bootstrapper.docs.regen`.
- Tests: extend `test_fragment_equivalence.py` baseline + AST seam-parity tests + bump the `test_localhost_port_consumer_symmetry.py` expectation set.

## Effort

**~3–5 focused days for the initial wire-up**, then **ongoing maintenance debt** of owning the FastAPI shim against a 0.1.x library that's changing fast. Roughly 1.5× the original Chatterbox add (PR #29 patterns apply: not infra-band so no slot pin needed, but the dual-write rule for compose env + runtime_sc still applies — see `project_runtime_sc_vs_compose_env_dual_write.md`).

## Risks & open questions

- **No upstream HTTP server.** Every other TTS engine in the stack either ships one natively (Speaches) or has a community-maintained wrapper (Chatterbox via travisvn). We'd be the wrapper.
- **0.1.x library cadence.** API surface (`OmniVoice.from_pretrained`, `.generate` kwargs) is likely to break across minors. Pinning a specific commit limits the security/feature pickups.
- **GPU realism.** Claim is RTF 0.025 on H20. fp16 implies ~1.2 GB weights + activation overhead, realistically 2–3 GB VRAM minimum. CPU path exists but RTF unknown.
- **Quality vs Chatterbox** — only the 600-language claim is meaningfully novel. Voice cloning duplicates Chatterbox's surface.

## Why now (and why not sooner)

Not now. The library is too young for the stack to absorb upstream-maintenance risk, and the SaaS path is closed pending a public API.

## Recommendation

**Skip — re-evaluate Q4 2026 / on first 1.0 release.** Specifically, re-open this candidate if ONE of the following lands:

1. omnivoice.app publishes a documented developer API (a `omnivoice-cloud` source variant becomes feasible — small effort).
2. A community FastAPI wrapper for k2-fsa/OmniVoice appears on GitHub with maintenance activity (a `omnivoice-container-gpu` source variant becomes feasible without the stack owning the shim).
3. Speaches adds OmniVoice as a supported backend (Speaches already abstracts Kokoro + Piper; OmniVoice would be a natural third — zero stack work).

In the meantime, Atlas has no supported OmniVoice source value. Users who specifically need OmniVoice today would need a future wrapper plus a future `TTS_PROVIDER_SOURCE` variant before it can be selected through the stack.

## Upstream evidence

- https://omnivoice.app — SaaS marketing site (paywalled web UI, no API)
- https://github.com/k2-fsa/OmniVoice — Apache-2.0 OSS reference implementation (CLI + Python)
- https://github.com/zhu-han/OmniVoice — fork tracking k2-fsa
- https://huggingface.co/k2-fsa/OmniVoice — model weights + repo metadata
