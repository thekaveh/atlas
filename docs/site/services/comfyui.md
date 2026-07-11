# ComfyUI (image generation)

## 1. Overview

`comfyui` is an Atlas service family in the `media` category. Its implementation and service-owned documentation live under `services/comfyui/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `media`
- Kind: `container`
- Tracks: `all, gen-ai-creative, gen-ai-eng`

## 4. Access

- Kong aliases: `comfyui.localhost`
- Port variables: `COMFYUI_PORT, COMFYUI_LOCALHOST_PORT, COMFYUI_MPS_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variables: `COMFYUI_SOURCE`
- Default SOURCE values: `container-cpu`
- Available SOURCE values: `container-cpu, container-gpu, localhost, managed-localhost-mps, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, litellm, ollama`
- Optional dependencies: `-`
- Runtime calls: `supabase`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| COMFYUI_SOURCE | container-cpu | container-cpu, container-gpu, localhost, managed-localhost-mps, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `supabase`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/comfyui/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/comfyui/architecture.svg)
- Diagram HTML: [`services/comfyui/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/comfyui/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/comfyui/README.md](https://github.com/thekaveh/atlas/blob/main/services/comfyui/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)

## 12. Krea 2 Curated Bundles

Atlas provides separate Krea 2 Turbo and Krea 2 RAW BF16 selections. Each logical bundle uses the same pinned Qwen3-VL 4B text encoder and Qwen-Image VAE; the generated download plan retrieves those shared target files once when both bundles are selected.

### 12.1 Bundle Matrix

| Bundle | Catalog ID | Precision | Disk | RAM | VRAM |
| --- | --- | --- | --- | --- | --- |
| Krea 2 Turbo | `krea2-turbo-bf16` | bf16 | 35.413 GB | 32 GB | 32 GB |
| Krea 2 RAW | `krea2-raw-bf16` | bf16 | 35.413 GB | 32 GB | 32 GB |

### 12.2 Pinned Artifacts

| Bundle | Role | Target | Bytes | SHA-256 |
| --- | --- | --- | --- | --- |
| Krea 2 Turbo | diffusion | `diffusion_models/krea2_turbo_bf16.safetensors` | 26,283,332,608 | `78bbf8f4165eda19cea3cb06c78089221932a39e2eed8af9da741f942c47ffb3` |
| Krea 2 Turbo | text_encoder | `text_encoders/qwen3vl_4b_bf16.safetensors` | 8,875,719,384 | `36f3ff447ef59201722e8f9ce6020c9819fdcfba6aa2608c4e09b1c0ce114e34` |
| Krea 2 Turbo | vae | `vae/qwen_image_vae.safetensors` | 253,806,246 | `a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f` |
| Krea 2 RAW | diffusion | `diffusion_models/krea2_raw_bf16.safetensors` | 26,283,332,608 | `f99bb0ff8e362b77342bc4994e0c50906fe7ef7074864b181b7d48d2fa6d03d7` |
| Krea 2 RAW | text_encoder | `text_encoders/qwen3vl_4b_bf16.safetensors` | 8,875,719,384 | `36f3ff447ef59201722e8f9ce6020c9819fdcfba6aa2608c4e09b1c0ce114e34` |
| Krea 2 RAW | vae | `vae/qwen_image_vae.safetensors` | 253,806,246 | `a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f` |

Every artifact URL is pinned to Hugging Face revision `8038ce89b91b042141541ad0fa51b985ca262c5f`.

### 12.3 Workflow

The API-ready example is `services/comfyui/workflows/krea2-turbo-api.json`. It uses only ComfyUI core nodes with `CLIPLoader` type `krea2`, 8 steps, CFG 1.0, Euler sampling, the simple scheduler, `ConditioningZeroOut`, and a 1024 by 1024 latent. Atlas pins ComfyUI `v0.27.0`, which includes the core Krea 2 support introduced in `v0.26.0`.

### 12.4 License And Operations

Model weights use the [Krea 2 Community License](https://huggingface.co/krea/Krea-2-Turbo/blob/1161245028ef398cd0a951101b2bbf486464f841/LICENSE.pdf). Operators must review the authoritative license before deployment:

- Enterprise license required at or above $1,000,000 USD ($1M) annual revenue for commercial use.
- Reasonable and appropriate content filtering is required for deployments.
- The license does not state a seat-count threshold; do not apply the previously reported 50-seat limit.

The 1024-square generation check is an opt-in `live` pytest and is not part of generic CI.

Container sources default `COMFYUI_MEMORY_LIMIT` to a 40 GB hard ceiling. Docker does not reserve that memory; smaller workloads consume only what they need, while Krea 2 can exceed the former 4 GB limit.

## 13. Managed Apple-Silicon / Metal (MPS) Source

`COMFYUI_SOURCE=managed-localhost-mps` is a managed host source for Apple Silicon Macs. Docker Desktop on macOS cannot pass Metal into a Linux container, so Atlas installs and runs a native ComfyUI process on the host and points `COMFYUI_ENDPOINT` at it. Every downstream consumer — backend, Open WebUI, JupyterHub, and Celery — resolves the identical `COMFYUI_ENDPOINT` contract, so nothing downstream depends on whether the source is a container or a host process. One process runs per host: a single instance already saturates the Apple Silicon GPU, and a second is net-negative.

### 13.1 What Atlas Manages

Atlas checks out a pinned ComfyUI ref (`COMFYUI_MPS_REF`, default `v0.27.0`) into an Atlas-owned state directory (`COMFYUI_MPS_STATE_DIR`, default `~/.atlas/comfyui-mps`) with a dedicated venv holding Metal-enabled Torch. Install is idempotent — only the first run downloads Torch. The process reuses the existing host models directory (`COMFYUI_MPS_MODELS_PATH`, default `~/Documents/ComfyUI/models`) through a generated `extra_model_paths.yaml`, so weights are never duplicated. It listens on a fixed loopback port (`COMFYUI_MPS_LOCALHOST_PORT`, default `8188`) with PID, log, and status files under the state directory, and refuses to start if the port is already taken.

### 13.2 Lifecycle And Preflight

A normal `./start.sh` with this source runs preflight, install, and start automatically before Compose; `./stop.sh` stops the host process. Explicit control is available headless:

```bash
./start.sh comfyui-mps preflight
./start.sh comfyui-mps install [--update]
./start.sh comfyui-mps start
./start.sh comfyui-mps status
./start.sh comfyui-mps health
./start.sh comfyui-mps stop
./start.sh comfyui-mps remove
```

The read-only preflight checks OS (macOS) and arch (arm64) — a hard fail elsewhere — plus git/python3 presence, unified-memory headroom against `COMFYUI_MPS_MIN_MEMORY_GB` (default `16`), Torch/MPS availability once the venv exists, and per-model precision: `fp8`/`fp8-scaled` weights crash on MPS and warn with a "use a BF16 variant" hint. The same preflight runs as a CI-safe `comfyui-mps` doctor check.

### 13.3 Cold/Warm Health, Unsupported Hosts, Upgrades, Logs, Removal

Weights load lazily on the first request, so a freshly launched process is reachable but cold; `health` reports reachability and the compute device (`mps` when `/system_stats` shows a non-CPU device). On non-Apple hosts (Linux, Intel Macs, Windows) the preflight fails with an explicit unsupported-host message and install refuses — Atlas never claims a Linux container is Metal-capable. Upgrade or roll back by setting `COMFYUI_MPS_REF` and running `comfyui-mps install --update` then `stop`/`start`. Logs are at `${COMFYUI_MPS_STATE_DIR}/comfyui-mps.log`. `comfyui-mps remove` stops the process and deletes the state directory while leaving the reused host models directory untouched. n8n receives no `COMFYUI_ENDPOINT` injection for any ComfyUI source and is documented as excluded here; the managed source is consumed identically to every other source by the consumers that do receive the endpoint.
