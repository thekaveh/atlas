# 5.2.16. FAL Cloud Media

## 1. Overview

FAL Cloud Media is a virtual media provider for fal.ai's hosted generation APIs. It adds a cloud path for users who want Atlas creative generation without running local ComfyUI CPU/GPU containers or a host ComfyUI process.

Atlas does not run a FAL container. The backend reads `FAL_SOURCE`, `FAL_API_KEY`, and model defaults from the environment, then routes hosted image-generation operations to the fal.ai Python client when `FAL_SOURCE=enabled`.

## 2. Access

| Surface | URL or command | Notes |
|---|---|---|
| Atlas SOURCE | `FAL_SOURCE=disabled` | Default. No FAL calls are made and no API key is required. |
| FAL provider | `FAL_SOURCE=enabled` | Enables FAL-backed hosted media generation through the backend. |
| Media gateway (image) | `POST /media/generate` with `{"provider":"fal","modality":"image"}` | Submits a FAL image operation (text→image via `fal-ai/flux/dev`, or image-to-image given an init image) and returns an operation id. Custom endpoints take a provider-native `input.provider_arguments` object. The gateway is multi-provider — `provider=comfyui` routes the same seam to the managed/local ComfyUI host; FAL is the cloud/keyed path. The full request schema is served at the backend's `/docs` (OpenAPI) endpoint. |
| Media gateway (image→3D) | `POST /media/generate` with `{"modality":"image_to_3d"}` | Submits a hosted image→3D operation through a verified Hunyuan3D, TRELLIS, Tripo, or Rodin endpoint and returns an operation id. |
| Operation polling | `GET /media/operations/{operation_id}` | Polls provider status and returns normalized artifacts (the GLB is the primary `artifact_url`), cost, license, and provenance. |
| Operation cancel | `POST /media/operations/{operation_id}/cancel` | Requests cancellation from FAL; budget stays reserved until polling confirms a terminal provider outcome. Idempotent. Status-code semantics are in the backend's `/docs` OpenAPI. |
| Ambiguous submission reconciliation | `POST /media/operations/{operation_id}/reconcile` | An operator authenticated with `BACKEND_INTERNAL_API_TOKEN` records `outcome=commit|release` after checking provider billing. Missing provider ids are treated as ambiguous. The operation intent and recovery row do not expire before settlement, same-outcome retries are safe, and `recovery_ledger_ids` identifies cleanup candidates if operation persistence fails. The default Postgres store survives restarts; `memory` is ephemeral. |
| Spend read | `GET /media/spend?consumer=<c>` | Scoped spend read (committed/reserved totals + rows for one consumer). Empty unless `MEDIA_BUDGET_ENABLED=true`. |
| Compatibility route | `POST /comfyui/generate` | Uses FAL for simple image generation when `FAL_SOURCE=enabled`; otherwise preserves the existing ComfyUI path. |
| Kong | No direct route | FAL is a server-side provider only. The API key stays in the backend environment. |

Enable from the CLI with:

```bash
./start.sh --fal-source enabled --fal-api-key <your-fal-key>
```

**Interactive wizard.** FAL is a paid cloud provider, so — like the OpenAI / Anthropic / OpenRouter cloud LLM keys — the wizard prompts for it with a **masked API-token step** placed right after the ComfyUI step (both are `media`), rather than a plain enabled/disabled tile: **enter a key to enable fal, or leave it blank to keep it disabled.** Entering a key sets `FAL_SOURCE=enabled` + `FAL_API_KEY`; a blank / `clear` leaves (or sets) `FAL_SOURCE=disabled` and wipes the key — the backend rejects `FAL_SOURCE=enabled` without `FAL_API_KEY`, so this step guarantees a valid key whenever fal is enabled. FAL still appears in the services grid as a media service.

## 3. Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `FAL_SOURCE` | `disabled` | Enables or disables the FAL provider. |
| `FAL_API_KEY` | empty | Required when `FAL_SOURCE=enabled`; not required when disabled. |
| `FAL_MODEL` | `fal-ai/flux/dev` | Default FAL model endpoint used by the media gateway for text-to-image generation. |
| `FAL_IMAGE_TO_3D_MODEL` | `fal-ai/trellis` | Default endpoint id for the `image_to_3d` modality. Must resolve to a curated registry entry (see below); TRELLIS is the MIT-licensed default. |
| `FAL_MODEL_LICENSE` | `fal/provider-terms` | License or terms marker returned in normalized media operation responses when provider-specific model licensing is not more specific. For `image_to_3d`, the per-model registry license overrides this. |
| `BACKEND_MEDIA_INPUT_BUCKET` | `default` | Atlas storage bucket the gateway hosts `image_to_3d` inputs in (under the `media-inputs/` prefix) when a provider rejects data-URI inputs. Declared on the backend service. |
| `BACKEND_MEDIA_INPUT_PUBLIC_BASE_URL` | empty | Optional public base URL for hosted inputs (`<base>/<bucket>/<key>`) so the provider's cloud can fetch them through a reachable ingress; empty falls back to the storage client's public URL. Declared on the backend service. |
| `FAL_TIMEOUT_SECONDS` | `120` | Finite Backend timeout budget for FAL media submit/poll operations and the compatibility route; must be greater than zero and at most 3,600 seconds. |
| `FAL_OUTPUT_FORMAT` | `jpeg` | Requested image format for compatible models. |
| `FAL_ENABLE_SAFETY_CHECKER` | `true` | Requests the provider-side safety checker for compatible models. |

## 4. Architecture & Wiring

Atlas models FAL as a virtual media service:

- Track membership: `gen-ai-creative` and `all`.
- Service category: `media`.
- Source values: `disabled` and `enabled`.
- Runtime ownership: no compose service, no container, no volume, and no Kong route.
- Backend integration: `POST /media/generate` validates the complete selected-model schema before state, budget, storage, or provider work and then submits hosted image operations to FAL. `GET /media/operations/{operation_id}` polls provider status. `POST /media/operations/{operation_id}/cancel` records a nonterminal cancellation request and retains spend until polling confirms the provider's terminal outcome. `POST /comfyui/generate` chooses FAL first when `FAL_SOURCE=enabled` for the default `fal-ai/flux/dev` compatibility contract; custom endpoint schemas use `POST /media/generate` with explicit `input.provider_arguments` instead.
- ComfyUI-specific routes: workflow execution, queue inspection, history lookup, cancellation, and image file proxying remain ComfyUI-specific.
- Secret handling: `FAL_API_KEY` is server-side only. The backend maps it to `FAL_KEY` for the fal.ai Python client and never exposes it to browser clients.
- Operation state: ordinary submitted and terminal metadata is shared in Redis with a bounded TTL, so polling and cancellation survive Backend restarts and remain consistent across replicas. An unresolved `submission_unknown` intent and a budget-tracked terminal transition remain unexpired until ledger settlement, after which the normal TTL resumes. Owner scope is recorded at submission and enforced on reads and cancellation. The Postgres recovery/spend ledger and optional budget enforcement are described in §4.2.

### 4.1. Image→3D modality

`{"modality":"image_to_3d","provider":"fal","model":<id>,"input":{"image":<url-or-data-uri>}}` submits a hosted image→3D job. `model` resolves against a curated registry of verified vendor endpoint ids (aliases and case tolerated; an unknown or unverified id returns HTTP 400 listing the supported ids), and omitting it uses `FAL_IMAGE_TO_3D_MODEL`. The backend owns provider quirks centrally so consumers do not re-discover them: it normalizes each provider's differing request field names and seed ranges onto one `input` shape, normalizes the response so the GLB is always the primary `artifact_url` (with `license` and estimated `cost_usd` from the registry entry), uploads inputs to Atlas storage for providers that reject data URIs (`BACKEND_MEDIA_INPUT_BUCKET`; set `BACKEND_MEDIA_INPUT_PUBLIC_BASE_URL` when the provider's cloud needs a public ingress), and composites transparent inputs onto a neutral background before submission to avoid a known fal Hunyuan3D v2 crop bug. The exact per-provider field names, seed ranges, and response-key normalization list are documented at the backend's `/docs` OpenAPI endpoint.

A successful GLB (`artifact_url` + `license`/`provenance`) is ready for the asset-worker bake contract (the asset-baker `/assets/bake/ref` endpoint), but the gateway never invokes it automatically.

| Endpoint id | Family | License | Commercial use | Input hosting |
|---|---|---|---|---|
| `fal-ai/trellis` | TRELLIS | MIT | yes | data URI ok |
| `fal-ai/hunyuan3d/v2` | Hunyuan3D | tencent-hunyuan-community | yes via fal (self-host Tencent-gated, EU/UK/KR excluded) | data URI ok |
| `tripo3d/tripo/v2.5/image-to-3d` | Tripo | tripo-commercial-gated | gated to Pro/Enterprise | **requires hosted URL** |
| `fal-ai/hyper3d/rodin` | Rodin (Hyper3D) | hyper3d-provider-terms | conditional | data URI ok |

Pixal3D remains in the internal research registry as an unverified candidate and is not advertised or routable until its current endpoint id and request contract are validated.

### 4.2. Spend ledger & budgets

Hosted media generation has no LiteLLM-style spend accounting of its own, so the media gateway carries its own cost ledger + budget engine — **enforcement disabled by default** (`MEDIA_BUDGET_ENABLED=false`), backend-owned, no new service SOURCE. Even with enforcement off, an ambiguous FAL submission writes a minimal recovery row to `MEDIA_BUDGET_STORE`; keep the default `postgres` selection for cross-process and restart durability because `memory` is intentionally ephemeral. When enforcement is enabled, every generation reserves its estimated cost before the provider call and reconciles to the final cost on completion, recorded per operation in `public.media_spend_ledger`. Submissions over the configured cap (`MEDIA_BUDGET_DEFAULT_USD` and per-scope `MEDIA_BUDGET_CONSUMER_CAPS`) are rejected before any provider call or storage write, and `MEDIA_DISABLED_PROVIDERS` (CSV) can kill-switch a specific provider. `GET /media/spend?consumer=<c>[&project=<p>]` returns that consumer's totals + rows only. Cancellation acceptance is not settlement — spend stays reserved until a provider poll proves a terminal outcome. The full status-code, concurrency-safety, and unknown-cost-handling contract is documented at the backend's `/docs` OpenAPI endpoint.

Attribution comes from the authenticated Backend principal plus optional request `consumer`/`project` fields or `X-Atlas-Consumer`/`X-Atlas-Project` headers (default `default`). Operation polling and cancellation are owner-scoped; spend reads require the same Backend application-auth boundary. `MEDIA_BUDGET_*` and `MEDIA_DISABLED_PROVIDERS` are declared on the backend service.

### 4.3. LiteLLM text→image route

When `FAL_SOURCE=enabled` with `FAL_API_KEY` set, `litellm-init` also registers a **`fal-image`** model on the LiteLLM gateway via LiteLLM's native `fal_ai` image provider (`model: fal_ai/${FAL_MODEL}`, gated + disabled-tolerant like the `hermes`/`vllm-metal` rows). This lets OpenAI-shaped clients (Open WebUI image generation, n8n, notebooks) reach fal **text→image** through the single `http://litellm:4000/v1/images/generations` surface with LiteLLM's unified auth, spend logging, and retries — no bespoke backend call needed.

- **Scope is text→image only.** fal **image→3D** (§4.1) and video/audio stay on the Backend media gateway — LiteLLM has no 3D/video modality, and the gateway owns the curated registry + normalized provenance + MinIO storage.
- **Provenance boundary.** The LiteLLM route returns raw image data (b64/URL) and does **not** perform Atlas provenance/storage; the Backend media gateway (`POST /media/generate`) remains authoritative wherever durable provenance/storage is required. The two paths **complement** each other.
- **Key wiring.** The `fal-image` row references `os.environ/FAL_AI_API_KEY`, which the LiteLLM *server* resolves at request time; the compose fragment sets `FAL_AI_API_KEY=${FAL_API_KEY}` on the litellm container. The key is never written into `config.yaml`.

## 5. Dependencies & Integrations

### 5.1. Current — Upstream (this service calls)

_No upstream calls._

### 5.2. Current — Downstream (services that call this)

| Service | Category |
|---|---|
| litellm | llm |
| backend | apps |

### 5.3. Architecture diagram

![fal architecture](./architecture.svg)

[Open the full-size diagram](./architecture.html) for a full-screen view.

### 5.4. Future — Missing pair integrations

- Optional FAL model catalog prompts if Atlas adopts a curated cloud-media model list.

### 5.5. Future — Candidate new services

- Additional cloud media providers such as Replicate, RunPod Serverless, or provider-specific video generation APIs behind the same backend provider seam.

### 5.6. Future — Unused features in this service

- FAL queue webhooks are not wired in this first media-gateway pass. The backend uses submit/poll operations instead.
