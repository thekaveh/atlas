---
service: comfyui
category: media
generated: 2026-05-19
generator: phase-b-subagent
sources_consulted:
  - https://github.com/comfyanonymous/ComfyUI
  - https://github.com/comfyanonymous/ComfyUI/blob/master/server.py
  - https://github.com/ltdrdata/ComfyUI-Manager
  - https://docs.comfy.org/essentials/api
  - services/comfyui/service.yml
  - services/comfyui/compose.yml
  - services/comfyui/init/scripts/download_models.sh
  - services/open-webui/service.yml
  - services/n8n/service.yml
  - services/minio/service.yml
  - services/weaviate/service.yml
  - services/comfyui/README.md
---

# comfyui — Integration Research

## 1. Missing-pair integrations

- **comfyui ↔ minio**
  - Why valuable: ComfyUI currently uploads outputs to Supabase Storage via the `COMFYUI_UPLOAD_TO_SUPABASE`/`COMFYUI_STORAGE_BUCKET` env path, but `services/minio/service.yml` already provisions a dedicated `comfyui` bucket plus a scoped `MINIO_COMFYUI_ACCESS_KEY` that is never consumed. Routing outputs to MinIO keeps generated media in the artifact tier (where backend/n8n/jupyter also write) instead of the auth-coupled Supabase store, and gives Langfuse/Weaviate a stable S3 URL to reference.
  - Mechanism sketch: a small ComfyUI custom node (or a sidecar reading the `executed` event on `ws://comfyui:18188/ws`) that pushes `/view`-rendered artifacts to `s3://comfyui` on `http://minio:9000` using `MINIO_COMFYUI_ACCESS_KEY`. Add `minio` to `depends_on.optional` and a `MINIO_URL`/`MINIO_BUCKET_COMFYUI` env to the manifest.
  - Effort: small
  - Risks / open questions: dual-write (Supabase + MinIO) would duplicate storage until the Supabase path is feature-flagged off; custom-node code lives outside the upstream image and needs the `comfyui-custom-nodes` volume to be seeded.
  - Confidence: high (minio manifest already declares the bucket + credential pair).

- **comfyui ↔ weaviate (via multi2vec-clip)**
  - Why valuable: every ComfyUI generation produces an image plus the prompt that made it. The stack already runs `semitechnologies/multi2vec-clip` as part of the weaviate family, so generated outputs can be auto-embedded and made searchable ("find images like this", "retrieve prior renders matching this prompt") with zero new infra.
  - Mechanism sketch: post-execution hook (ComfyUI custom node or n8n flow listening on the websocket) PUTs `{image: <b64|url>, prompt: <str>, workflow_id: <str>}` into a `ComfyImage` Weaviate class with `vectorizer: multi2vec-clip` on `http://weaviate:8080/v1/objects`. Image bytes fetched from ComfyUI `/view?filename=...`.
  - Effort: medium
  - Risks / open questions: schema bootstrap (a `weaviate-init`-style step would need to create the class); reruns must not re-embed identical outputs.
  - Confidence: high (multi2vec-clip is already enabled in `WEAVIATE_ENABLE_MODULES`).

- **comfyui ↔ n8n**
  - Why valuable: `services/n8n/service.yml` already installs `n8n-nodes-comfyui` and `@ksc1234/n8n-nodes-comfyui-image-to-image` into `N8N_INIT_NODES`, but the comfyui manifest declares no `runtime_deps.optional` link to n8n and the n8n credentials store is not pre-seeded with the ComfyUI endpoint, so users still wire it by hand.
  - Mechanism sketch: pre-seed an n8n credential at startup (n8n REST API `POST /credentials`) pointing at `${COMFYUI_ENDPOINT}`; add `n8n` to comfyui's `runtime_deps.optional` so downstream docs/diagrams reflect the relationship.
  - Effort: small
  - Risks / open questions: n8n credential bootstrapping needs an admin API token; ordering vs `n8n-init` import flow.
  - Confidence: medium (nodes-list evidence in n8n manifest; credential-seed API is documented but not yet used).

- **comfyui ↔ redis**
  - Why valuable: `services/comfyui/compose.yml` already lists `redis` in `depends_on`, but the manifest's `depends_on.required` does not, and Redis isn't actually used by ComfyUI today. A small queue-state bridge would let n8n / backend poll job status without holding a websocket open to ComfyUI for every request.
  - Mechanism sketch: a ComfyUI custom node subscribes to its own websocket and mirrors `executing`/`executed`/`progress` events into Redis pubsub channels `comfyui:job:<prompt_id>` on `redis://redis:6379`.
  - Effort: medium
  - Risks / open questions: redundant with directly hitting `/history/{prompt_id}`; mainly worth doing if multiple consumers need fan-out.
  - Confidence: low (motivation is real but the cheaper path is polling `/history`).

## 2. Candidate new services

- **Langfuse** → `../candidates/langfuse.md`
  - Headline: Self-hostable trace store capturing LiteLLM + ComfyUI + Hermes generation pipelines end-to-end.
  - Other consumers in stack: litellm, hermes, n8n, backend, open-webui.

## 3. Per-service feature gaps

- **ComfyUI-Manager + cm-cli for richer custom-node lifecycle management** — Why pursue: Atlas now clones allowlisted custom-node repositories at pinned commit refs from `services/comfyui/custom-nodes.yaml`, but it does not use ComfyUI-Manager for enable/disable/remove operations or runtime Python dependency reconciliation. Manager remains a future decision because its GPL-3.0 license and runtime behavior need explicit acceptance. Effort: small.
- **Workflow-API mode (`--listen --enable-cors-header`) + `/prompt` ingestion from non-UI clients** — Why pursue: backend and Hermes currently have no documented call pattern; exposing a worked example of POSTing a workflow JSON to `/prompt` and tracking it via `/history/{prompt_id}` would unlock programmatic image-gen from agents without the websocket dance. Effort: small.
- **Video model support (Mochi / LTX-Video)** — Why pursue: ComfyUI upstream supports video diffusion models. The new picker's catalog already carries a `video_model` category (HunyuanVideo, CogVideoX GGUF in the bundled fallback), so this gap is largely closed by the catalog rewrite. Remaining work: expand the curated allowlist with Mochi + LTX entries and verify the AnimateDiff / motion_lora pipelines work end-to-end on GPU. Effort: medium.
- **Authentication on the ComfyUI endpoint** — Why pursue: `server.py` ships no auth layer and Kong fronts ComfyUI on `comfyui.localhost`. A Kong basic-auth or JWT plugin on the `/comfyui` route would prevent any LAN peer from queueing GPU jobs. Effort: small.
