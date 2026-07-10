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
- Port variables: `COMFYUI_PORT, COMFYUI_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variables: `COMFYUI_SOURCE`
- Default SOURCE values: `container-cpu`
- Available SOURCE values: `container-cpu, container-gpu, localhost, disabled`

## 6. Dependencies And Topology

- Required dependencies: `supabase, litellm, ollama`
- Optional dependencies: `-`
- Runtime calls: `supabase`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| COMFYUI_SOURCE | container-cpu | container-cpu, container-gpu, localhost, disabled |

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
