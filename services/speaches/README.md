# 5.2.48. Speaches (unified TTS + STT engine)

Speaches is an STT and TTS engine. One container serves
`/v1/audio/transcriptions` (Faster-Whisper) and `/v1/audio/speech` (Kokoro
and Piper voices). Select it with `STT_PROVIDER_SOURCE=speaches-*` or
`TTS_PROVIDER_SOURCE=speaches-*`. When both select it, one container serves both.

Atlas preloads no model, and Speaches does not download a model on request.
Requests return `404` until you download the model (issue #799). See
[the STT Provider README](../stt-provider/README.md) for STT and
[the TTS Provider README](../tts-provider/README.md) for TTS.

## 1. Engine quick reference

- **Images:**
  - CPU: `ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cpu`
  - GPU: `ghcr.io/speaches-ai/speaches:0.9.0-rc.3-cuda`
- **License:** MIT
- **Activation:** any of
  - `STT_PROVIDER_SOURCE=speaches-container-cpu`
  - `STT_PROVIDER_SOURCE=speaches-container-gpu`
  - `TTS_PROVIDER_SOURCE=speaches-container-cpu`
  - `TTS_PROVIDER_SOURCE=speaches-container-gpu`
- **GPU caveat:** `speaches-container-gpu` selects the CUDA image, but the service gets no NVIDIA device (issue #1373, open). It runs on CPU or fails CUDA initialisation. Use `speaches-container-cpu`.
- **In-container port:** 8000
- **Host port:** `${SPEACHES_PORT}` (computed from `BASE_PORT`)

## 2. Capabilities & limitations

<a id="3-capabilities-limitations"></a>
<a id="3-capabilities--limitations"></a>

Support tier: **experimental** — Capability contract declared; no cited cold-start, workflow, or upgrade qualification run yet (evidence at `v0.1.0`).

| Capability | Status | Verification | Notes |
|---|---|---|---|
| OpenAI-compatible text-to-speech | partial | tested | Speaches serves /v1/audio/speech, but Atlas does not preload Kokoro; the model must be downloaded before requests succeed. |
| OpenAI-compatible speech-to-text | partial | untested | Speaches exposes /v1/audio/transcriptions, but Atlas has not validated the current preload and Open WebUI model path against a live container. |
| Configurable STT model selection | stubbed | documented | SPEACHES_STT_MODEL is declared but does not alter the hard-coded PRELOAD_MODELS value or Open WebUI's STT model. |

## 3. Dependencies & Integrations

<a id="2-dependencies-integrations"></a>
<a id="2-dependencies--integrations"></a>

### 3.1. Current — Upstream (this service calls)

<a id="21-current-upstream-this-service-calls"></a>
<a id="21-current--upstream-this-service-calls"></a>

_No upstream calls._

### 3.2. Current — Downstream (services that call this)

<a id="22-current-downstream-services-that-call-this"></a>
<a id="22-current--downstream-services-that-call-this"></a>

| Service | Category |
|---|---|
| kong | infra |
| hermes | agents |
| n8n | agents |
| jupyterhub | apps |
| open-webui | apps |

### 3.3. Architecture diagram

<a id="23-architecture-diagram"></a>

![speaches architecture](./architecture.svg)

[Open the full-size diagram](./architecture.html) for a full-screen view.

### 3.4. Future — Missing pair integrations

<a id="24-future-missing-pair-integrations"></a>
<a id="24-future--missing-pair-integrations"></a>

_No high-confidence opportunities identified._

### 3.5. Future — Candidate new services

<a id="25-future-candidate-new-services"></a>
<a id="25-future--candidate-new-services"></a>

_No high-confidence opportunities identified._

### 3.6. Future — Unused features in this service

<a id="26-future-unused-features-in-this-service"></a>
<a id="26-future--unused-features-in-this-service"></a>

_No high-confidence opportunities identified._
