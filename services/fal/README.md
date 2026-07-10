# FAL Cloud Media

## 1. Overview

FAL Cloud Media is a virtual media provider for fal.ai's hosted generation APIs. It adds a cloud path for users who want Atlas creative generation without running local ComfyUI CPU/GPU containers or a host ComfyUI process.

Atlas does not run a FAL container. The backend reads `FAL_SOURCE`, `FAL_API_KEY`, and model defaults from the environment, then routes hosted image-generation operations to the fal.ai Python client when `FAL_SOURCE=enabled`.

## 2. Access

| Surface | URL or command | Notes |
|---|---|---|
| Atlas SOURCE | `FAL_SOURCE=disabled` | Default. No FAL calls are made and no API key is required. |
| FAL provider | `FAL_SOURCE=enabled` | Enables FAL-backed hosted media generation through the backend. |
| Media gateway | `POST /media/generate` | Submits FAL image operations and returns an operation id. |
| Operation polling | `GET /media/operations/{operation_id}` | Polls provider status and returns normalized artifacts, cost, license, and provenance. |
| Compatibility route | `POST /comfyui/generate` | Uses FAL for simple image generation when `FAL_SOURCE=enabled`; otherwise preserves the existing ComfyUI path. |
| Kong | No direct route | FAL is a server-side provider only. The API key stays in the backend environment. |

Enable from the CLI with:

```bash
./start.sh --fal-source enabled --fal-api-key <your-fal-key>
```

## 3. Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `FAL_SOURCE` | `disabled` | Enables or disables the FAL provider. |
| `FAL_API_KEY` | empty | Required when `FAL_SOURCE=enabled`; not required when disabled. |
| `FAL_MODEL` | `fal-ai/flux/dev` | Default FAL model endpoint used by the media gateway for text-to-image generation. |
| `FAL_MODEL_LICENSE` | `fal/provider-terms` | License or terms marker returned in normalized media operation responses when provider-specific model licensing is not more specific. |
| `FAL_TIMEOUT_SECONDS` | `120` | Backend timeout budget for FAL media submit/poll operations and the compatibility route. |
| `FAL_OUTPUT_FORMAT` | `jpeg` | Requested image format for compatible models. |
| `FAL_ENABLE_SAFETY_CHECKER` | `true` | Requests the provider-side safety checker for compatible models. |

## 4. Architecture & Wiring

Atlas models FAL as a virtual media service:

- Track membership: `gen-ai-creative` and `all`.
- Service category: `media`.
- Source values: `disabled` and `enabled`.
- Runtime ownership: no compose service, no container, no volume, and no Kong route.
- Backend integration: `POST /media/generate` submits hosted image operations to FAL and `GET /media/operations/{operation_id}` polls provider status. `POST /comfyui/generate` still chooses FAL first when `FAL_SOURCE=enabled`, keeping existing Open WebUI and n8n callers compatible.
- ComfyUI-specific routes: workflow execution, queue inspection, history lookup, cancellation, and image file proxying remain ComfyUI-specific.
- Secret handling: `FAL_API_KEY` is server-side only. The backend maps it to `FAL_KEY` for the fal.ai Python client and never exposes it to browser clients.
- Operation state: the first media-gateway pass stores submitted operation metadata in the backend process. Restart-durable operation storage, media spend limits, and cost ledgers remain follow-up work.

## 5. Dependencies & Integrations

> Auto-generated section — the **Current** subsections are derived from `services/fal/service.yml`'s `data_flow.calls` field (and inverse passes). Re-run `python -m bootstrapper.docs.regen fal` after manifest changes.

### 5.1 Current — Upstream (this service calls)

_No upstream calls._

### 5.2 Current — Downstream (services that call this)

| Service | Category |
|---|---|
| backend | apps |

### 5.3 Architecture diagram

![fal architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 5.4 Future — Missing pair integrations

- Restart-durable operation storage for hosted media operations if Atlas needs provider polling to survive backend container restarts.
- Optional FAL model catalog prompts if Atlas adopts a curated cloud-media model list.

### 5.5 Future — Candidate new services

- Additional cloud media providers such as Replicate, RunPod Serverless, or provider-specific video generation APIs behind the same backend provider seam.

### 5.6 Future — Unused features in this service

- FAL queue webhooks are not wired in this first media-gateway pass. The backend uses submit/poll operations instead.
