# ComfyUI

Node-based image generation workflow engine. ComfyUI runs as a single container with a web UI on its own port, exposing an HTTP API (`/prompt`, `/history/{id}`, `/view`) and a WebSocket (`/ws`) that streams `executing`/`executed`/`progress` events while a workflow runs. The stack treats ComfyUI as a media-tier engine: backend, Hermes, and Open WebUI consume it through Kong (browser) or directly via the internal Docker DNS name; n8n reaches it through the backend (`backend:8000/comfyui/*`), not directly.

Three source variants cover the common deployment shapes: containerized CPU and GPU (built from `ai-dock/comfyui` images) and a localhost mode that routes consumers to a host-running ComfyUI. Disabled mode removes it from compose entirely. A short-lived `comfyui-init` container stages model checkpoints into the `comfyui-models` volume based on `COMFYUI_USER_MODELS` (selected via the wizard's "ComfyUI · models" step), while an AI-Dock provisioning hook installs pinned custom-node repositories and their declared requirements inside the ComfyUI runtime environment.

## 1. Overview

Image: `ghcr.io/ai-dock/comfyui:v2-cpu-22.04-v0.2.7` (CPU default) or an operator-provided CUDA ai-dock variant for GPU. Atlas pins the upstream ComfyUI core through `COMFYUI_REF=v0.9.2` and keeps `COMFYUI_AUTO_UPDATE=true` so the ai-dock startup path checks out that release even when the base image tag lags. Output behavior: by default outputs are uploaded to Supabase Storage via `COMFYUI_UPLOAD_TO_SUPABASE=true` and the `COMFYUI_STORAGE_BUCKET=comfyui-images` bucket. A second volume (`comfyui-custom-nodes`) holds allowlisted community nodes cloned from `services/comfyui/custom-nodes.yaml`.

## 2. Access

| Path | URL | Notes |
|---|---|---|
| Direct | `http://localhost:${COMFYUI_PORT}` (default `63053`) | Web UI + REST API. |
| Kong | `http://comfyui.localhost:${KONG_HTTP_PORT}` | Browser-friendly; needs `./start.sh --setup-hosts`. |
| Internal | `${COMFYUI_ENDPOINT}` | Resolved per `COMFYUI_SOURCE`: `http://comfyui:18188` for container, `http://host.docker.internal:${COMFYUI_LOCALHOST_PORT}` for localhost. |
| WebSocket | `ws://comfyui:18188/ws` | Streams progress events; one connection per caller today. |

Canonical port table: [Ports and Routes](../../docs/deployment/ports-and-routes.md).

## 3. Configuration

```bash
COMFYUI_SOURCE=container-cpu                # container-cpu | container-gpu | localhost | disabled
COMFYUI_PORT=63053                          # computed by topology.py
COMFYUI_BASE_URL=http://comfyui:18188       # in-container default
COMFYUI_ARGS=--listen                       # static — passed verbatim; add --cpu (CPU) or --force-fp16 (GPU) yourself (compose default when unset: --listen --cpu)
COMFYUI_PLATFORM=linux/amd64
COMFYUI_USER_MODELS=                         # comma-separated catalog names; set by the wizard
COMFYUI_UPLOAD_TO_SUPABASE=true
COMFYUI_STORAGE_BUCKET=comfyui-images
COMFYUI_AUTO_UPDATE=true                    # AI-Dock startup updates ComfyUI to COMFYUI_REF
COMFYUI_REF=v0.9.2                          # pinned upstream ComfyUI release tag or full commit SHA
COMFYUI_CUSTOM_NODES_FILE=/custom-nodes.yaml # host-side fallback resolves to services/comfyui/custom-nodes.yaml
```

Localhost overrides:

```bash
COMFYUI_LOCALHOST_PORT=8000                 # URL is derived as http://host.docker.internal:8000 at compose-render time
COMFYUI_LOCAL_MODELS_PATH=~/Documents/ComfyUI/models   # bind-mounted when SOURCE=localhost
```

Auto-managed (do not edit manually):

```bash
COMFYUI_ENDPOINT=...                        # what backend/n8n/jupyterhub/open-webui consume
COMFYUI_SCALE / COMFYUI_INIT_SCALE
```

## 4. Architecture & wiring

**Request flow.** The backend POSTs a workflow JSON to `${COMFYUI_ENDPOINT}/prompt` and receives a `prompt_id` (n8n workflows call the backend's `/comfyui/*` routes rather than ComfyUI directly). To track progress, the caller either polls `GET /history/{prompt_id}` or opens a `/ws` websocket and filters by `prompt_id`. Outputs land under `output/` inside the container; the `/view` endpoint serves them by filename.

**Init flow** (`comfyui-init`): plain alpine + `apk add wget ca-certificates`. At bootstrapper start, `comfyui_resolver` (host-side, DB-free) computes the active model set from `COMFYUI_USER_MODELS` + `services/comfyui/custom-models.yaml` and writes `volumes/comfyui/selected-models.yaml` (full manifest) and `volumes/comfyui/active-models.tsv` (shell-consumable TSV). A single logical catalog entry may declare `files:` to download multiple artifacts as one selectable bundle; the manifest expands that bundle into one row per file with `bundle_id`, `bundle_file_role`, `precision`, `variant`, and explicit `target_dir` metadata. The same pass maps active `requires_custom_node` values through `services/comfyui/custom-nodes.yaml` and writes `volumes/comfyui/active-custom-nodes.tsv`; only allowlisted GitHub repos pinned to full commit SHAs are auto-installed. `comfyui-init` bind-mounts `volumes/comfyui/` and downloads each model TSV row into the `comfyui-models` volume via resumable `wget -c` with optional SHA256 verification. The main ComfyUI container mounts the same manifest directory plus `services/comfyui/provisioning/provision_custom_nodes.sh` as AI-Dock's provisioning hook; that hook clones each custom-node TSV row into `comfyui-custom-nodes` and installs `requirements.txt` through the ComfyUI Python environment when `install_requirements=true`. Failure mode is non-fatal — ComfyUI starts even if downloads or custom-node provisioning are incomplete, you just get model-not-found or node-missing errors at workflow time. The former `comfyui-catalog-init` container and `public.comfyui_models` DB table have been removed.

**Hard dependencies** (`depends_on.required`): `supabase`, `litellm`, `ollama`. The Supabase dep covers the Storage upload path; LiteLLM and Ollama are listed for **canonical wizard/row ordering** (the topology backbone — see ollama/parakeet for the same convention), NOT because ComfyUI calls them at startup. ComfyUI's only `runtime_adaptive` entry is `adapts_to: comfyui`.

**Volumes:** `comfyui-models` (checkpoints, VAEs, LoRAs), `comfyui-custom-nodes` (allowlisted community nodes cloned at pinned refs), `comfyui-input` (input images at `/opt/ComfyUI/input`), `comfyui-output` (generated images, also uploaded to Supabase).

**Output deduplication.** None today — the same workflow run twice generates two outputs and two Supabase uploads. There is no content-hash dedup pass.

## 5. Dependencies & Integrations

> Auto-generated section — the **Current** subsections are derived from `services/comfyui/service.yml`'s `data_flow.calls` field (and inverse passes). Re-run `python -m bootstrapper.docs.regen comfyui` after manifest changes.

### 5.1 Current — Upstream (this service calls)

| Service | Category |
|---|---|
| supabase | data |

### 5.2 Current — Downstream (services that call this)

| Service | Category |
|---|---|
| kong | infra |
| hermes | agents |
| backend | apps |
| jupyterhub | apps |
| open-webui | apps |

### 5.3 Architecture diagram

![comfyui architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 5.4 Future — Missing pair integrations

- **comfyui ↔ minio** — *Why:* ComfyUI currently uploads outputs to Supabase Storage via `COMFYUI_UPLOAD_TO_SUPABASE`/`COMFYUI_STORAGE_BUCKET`, but `services/minio/service.yml` already provisions a dedicated `comfyui` bucket plus `MINIO_COMFYUI_ACCESS_KEY` that is never consumed. Routing outputs to MinIO keeps generated media in the artifact tier and gives downstream services a stable S3 URL. *Mechanism:* small ComfyUI custom node (or sidecar reading the `executed` event on `ws://comfyui:18188/ws`) that pushes `/view`-rendered artifacts to `s3://comfyui` on `http://minio:9000` using `MINIO_COMFYUI_ACCESS_KEY`. Add `minio` to `runtime_deps.optional`. *Effort:* small. *Confidence:* high.
- **comfyui ↔ weaviate (via multi2vec-clip)** — *Why:* every ComfyUI generation produces an image plus the prompt that made it. The stack already runs `multi2vec-clip` as part of the weaviate family, so generated outputs can be auto-embedded for similarity search with zero new infra. *Mechanism:* post-execution hook PUTs `{image, prompt, workflow_id}` into a `ComfyImage` Weaviate class with `vectorizer: multi2vec-clip` on `http://weaviate:8080/v1/objects`. *Effort:* medium. *Confidence:* high.
- **comfyui ↔ n8n** — *Why:* `services/n8n/service.yml` already installs `n8n-nodes-comfyui` and the image-to-image package, but the comfyui manifest declares no `runtime_deps.optional` link to n8n and the credentials store is not pre-seeded. *Mechanism:* pre-seed an n8n credential at startup (n8n REST API `POST /credentials`) pointing at `${COMFYUI_ENDPOINT}`; add `n8n` to comfyui's `runtime_deps.optional`. *Effort:* small. *Confidence:* medium.
- **comfyui ↔ redis** — *Why:* compose already lists `redis` in `depends_on` but Redis isn't actually used by ComfyUI. A small queue-state bridge would let n8n/backend poll job status without holding a websocket open per request. *Mechanism:* custom node subscribing to its own websocket and mirroring `executing`/`executed`/`progress` events into Redis pubsub channels `comfyui:job:<prompt_id>`. *Effort:* medium. *Confidence:* low (cheaper path is polling `/history`).

### 5.5 Future — Candidate new services

- **Langfuse** ([details](../../docs/research/candidates/langfuse.md)) — *Headline:* self-hostable trace store capturing LiteLLM + ComfyUI + Hermes generation pipelines end-to-end. *Wires into:* litellm, hermes, n8n, backend, open-webui.

### 5.6 Future — Unused features in this service

- **ComfyUI-Manager + `cm-cli` for richer custom-node lifecycle management** — *Why pursue:* Atlas now clones pinned custom-node repositories from an allowlist, but it does not use ComfyUI-Manager for enable/disable/remove operations or dependency reconciliation inside the runtime Python environment. Manager remains a future decision because its GPL-3.0 license and runtime behavior need explicit acceptance. *Effort:* small.
- **Workflow-API mode + `/prompt` ingestion from non-UI clients** — *Why pursue:* backend and Hermes have no documented call pattern; a worked example of POSTing a workflow JSON to `/prompt` and tracking it via `/history/{prompt_id}` would unlock programmatic image-gen from agents. *Effort:* small.
- **Video model support (Mochi / LTX-Video)** — *Why pursue:* ComfyUI upstream supports video diffusion; the picker catalog includes a `video` category filter but the initial curated list is thin. Expanding the catalog with production-ready video checkpoints (Mochi, LTX-Video, Wan) would give GPU users first-class video generation. *Effort:* medium.
- **Authentication on the ComfyUI endpoint** — *Why pursue:* `server.py` ships no auth and Kong fronts ComfyUI on `comfyui.localhost`. A Kong basic-auth or JWT plugin would prevent any LAN peer from queueing GPU jobs. *Effort:* small.

## 6. Troubleshooting

**`AssertionError: Torch not compiled with CUDA enabled` on GPU mode.** You selected `container-gpu` but the host lacks NVIDIA Container Toolkit. Verify with `docker info | grep -i runtime`; expect `nvidia` listed. Otherwise switch to `container-cpu` or install the toolkit.

**Init container downloads stall mid-workflow.** `comfyui-init` runs in the background of the first `./start.sh`; large model sets (`full`) take ~10 GB and 5-15 min. Workflows referencing not-yet-downloaded models 404 until init exits. `docker logs <project>-comfyui-init -f` shows progress.

**Generated images don't appear in Supabase.** Confirm `COMFYUI_UPLOAD_TO_SUPABASE=true` and `SUPABASE_SERVICE_KEY` is valid. Upload happens after each successful workflow; failure mode is silent retry (check `docker logs <project>-comfyui` for `[supabase upload] …`).

**Localhost mode (`COMFYUI_SOURCE=localhost`) — containers can't reach host.** Linux Docker needs `host.docker.internal` mapped to the host gateway. The bootstrapper injects `extra_hosts: ["host.docker.internal:host-gateway"]` automatically; if you bypassed it, that's the gap. Kong's compose has the same wiring for the same reason.

**`ws://comfyui:18188/ws` 502s through Kong.** Kong's WebSocket support is wired but consumers using `comfyui.localhost` instead of `comfyui:18188` may hit timeout-related drops. From sibling containers prefer the internal DNS name.

```bash
docker compose ps comfyui comfyui-init
docker compose logs -f comfyui
curl -s http://localhost:${COMFYUI_PORT}/system_stats | jq .   # GPU/CPU info, queue depth
```

For general startup and routing issues, see [Troubleshooting](../../docs/quick-start/troubleshooting.md).

## 7. Operations

**Choosing models.** Run `./start.sh` (or the wizard standalone) and navigate to the "ComfyUI · models" step. The step shows for every non-`disabled` source (container-cpu / container-gpu / localhost) — same shape as the Ollama picker. Use filter chips (`f` key) to browse by category (Image / Image-edit / Video / Audio / 3D), `/` or `Tab` to search by name, `Space` to toggle rows, and `Enter` to confirm. Selected names are persisted as `COMFYUI_USER_MODELS` in `.env`. On the next `./start.sh`:

- **`container-cpu` / `container-gpu`:** the bootstrapper resolves the active set via `comfyui_resolver` and writes `volumes/comfyui/selected-models.yaml`, `volumes/comfyui/active-models.tsv`, and `volumes/comfyui/active-custom-nodes.tsv`. `comfyui-init` downloads each model in the model TSV into the `comfyui-models` volume. The AI-Dock provisioning hook in the main ComfyUI container clones each allowlisted custom-node row into `comfyui-custom-nodes` and installs its requirements when enabled.
- **`localhost`:** the bootstrapper still writes the manifest (so the backend `/comfyui/db/models` endpoint surfaces the active set to Open WebUI + n8n). `comfyui-init` does NOT run (scale=0) — you populate your host ComfyUI install's models directory yourself, same way you'd run `ollama pull <name>` on the host for Ollama localhost.

CLI alternative (works for all non-disabled sources):
```bash
./start.sh --comfyui-models=sdxl-base-1.0,sdxl-vae,flux1-dev-Q4_K_S
```
Unknown names log a warning at bootstrapper start but don't block startup.

**Required custom_nodes.** Some models (Flux GGUF, AnimateDiff, IP-Adapter, InstantID, 3D-Pack, etc.) need specific ComfyUI custom_nodes installed before they will load. The wizard surfaces a `⚠ <node-name>` badge on those rows. For container sources, Atlas maps those names through `services/comfyui/custom-nodes.yaml` and writes `active-custom-nodes.tsv`; the AI-Dock provisioning hook clones only the allowlisted repos at their pinned commit refs and installs requirements when `install_requirements=true`. Unknown node names still warn and are not auto-cloned. When adding a new catalog requirement, add the node to `custom-nodes.yaml` with a GitHub HTTPS repo, a full 40-character commit SHA, and an explicit `install_requirements` flag.

**Adding models not in the catalog.** Edit `services/comfyui/custom-models.yaml`. The wizard surfaces additions on the next run with a `[Custom]` family badge; the bootstrapper ingests them via `comfyui_resolver` at start and adds them to the download manifest. Single-file entries use `url`/`filename` directly. Multi-file entries use `files:` so one logical selection can stage, for example, diffusion weights, text encoders, and a VAE into distinct ComfyUI model directories. Per-file `target_dir` is authoritative and lets mesh/3D loaders place weights in `checkpoints` when required. Schema is documented in the file's header comment.

**Removing models.** Unchecking a model in the wizard sets it inactive on the next start (it is removed from the manifest and won't be re-downloaded). The underlying file is NOT deleted from the volume (same behavior as Ollama). To reclaim disk:

```bash
# Nuke the entire volume:
./stop.sh --cold

# Selective delete:
docker run --rm -v <project>-comfyui-models:/m alpine \
  rm /m/checkpoints/<file>
```

**Backend REST view.** `GET /comfyui/db/models?active_only=true` on the backend service returns the active catalog rows for Open WebUI + n8n consumers.

**Queue a workflow programmatically.**

```bash
curl -X POST http://localhost:${COMFYUI_PORT}/prompt \
  -H 'content-type: application/json' \
  -d @workflow.json
# → {"prompt_id": "abc-123", "number": 0, "node_errors": {}}

curl http://localhost:${COMFYUI_PORT}/history/abc-123
# → {"abc-123": {"prompt": [...], "outputs": {...}}}
```

**Monitor a running workflow.**

```bash
# Open ws://comfyui:18188/ws and filter messages by prompt_id
# Event types: status, executing, executed, progress, execution_error
```

## 8. Performance notes

- **CPU mode is slow.** A 512×512 SD 1.5 generation takes ~30-90s on CPU; the same on a modest GPU takes 2-5s. Use CPU mode for testing workflows, not for production.
- **GPU FP16.** Add `--force-fp16` to `COMFYUI_ARGS` in `.env` when running the GPU variant; halves VRAM usage with negligible quality impact for most SD/SDXL workloads.
- **Model loading dominates first-run latency.** Each checkpoint is ~2-7 GB; the first workflow using a model pays a 5-30s load cost as ComfyUI maps it into memory. Subsequent runs reuse the cached model.
- **No batching today.** ComfyUI processes one workflow at a time; concurrent requests queue. For high throughput, add replicas (out of scope for the default stack).
