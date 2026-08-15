# Unsloth as an Atlas service — feasibility study

**Date:** 2026-08-11 (rev. 2 — Studio-centred, three source flavours)
**Status:** Feasibility study — no implementation, no decision taken
**Upstream:** https://github.com/unslothai/unsloth (71.5k stars, Apache-2.0 core / AGPL-3.0 Studio UI)

## 1. Recommendation

**Feasible, and it fills a real gap.** Model it on ComfyUI — the service in Atlas that already has exactly this problem — with **three source flavours**, and make **Unsloth Studio** the training surface rather than an afterthought.

| Source | Who owns the process | Analogue in Atlas |
|---|---|---|
| `localhost` | You already run Unsloth; Atlas just wires the endpoint | `comfyui: localhost`, `ollama-localhost` |
| `managed-localhost` | Atlas installs, pins, supervises, removes | `comfyui: managed-localhost-mps`, `vllm-metal` |
| `container-gpu` | Linux + NVIDIA/AMD only | `comfyui: container-gpu` |
| `disabled` | Default | — |

Two findings drive this, and both invert a reasonable prior assumption:

1. **Unsloth trains on Apple Silicon now.** The "CUDA-only, Triton kernels" understanding is stale. Upstream: *"macOS: Training, MLX and GGUF inference are ALL supported."*
2. **Its Docker image does not.** `unsloth/unsloth:latest` publishes **`linux/amd64` only** — no arm64 manifest. On Apple Silicon the container is emulated and GPU-less, i.e. useless for training.

So on this stack's primary hardware the *host* flavours are the real product and the container is the speculative one. That is the same conclusion ComfyUI reached, for the same reason.

## 2. Unsloth Studio is the training surface

Studio is not a chat skin — it is where local fine-tuning actually happens, and it is the answer to "what about the AI studio?":

- **Data Recipes** — build training datasets from PDFs, CSVs, DOCX
- **Training** — LoRA, QLoRA, full fine-tuning, pretraining; GRPO/DPO/FP8 RL
- **Export** — GGUF, NVFP4, FP8
- **Serving** — OpenAI-compatible `/v1/chat/completions`, `/v1/models`, plus Anthropic `/v1/messages`
- **Backend picker** — Settings → System → GGUF inference engine (CPU/CUDA/ROCm/Vulkan; macOS always uses the Metal build)

Runs on Windows, Linux, WSL and macOS. Launch is `unsloth studio -p <port>`, and it **binds `127.0.0.1` by default** — which matches Atlas's managed-host doctrine without any coaxing.

Three upstream affordances make it unusually well-suited to Atlas management:

| Affordance | Why it matters here |
|---|---|
| `UNSLOTH_STUDIO_HOME` | Isolated venv, `auth/`, `studio.db`, cache and llama.cpp build in one directory → maps straight onto `UNSLOTH_STATE_DIR` (`~/.atlas/unsloth`), and makes `remove` a directory delete |
| `./install.sh --local` from a git clone | Installs from a checkout, so Atlas can **pin a SHA** instead of piping the vendor's `curl \| sh` — the same discipline used for ComfyUI custom nodes |
| `UNSLOTH_STUDIO_PASSWORD` env var | Non-interactive admin password. Upstream explicitly prefers it over `--password VALUE`, which leaks into `ps` and shell history. Atlas already generates secrets in `key_generator.py` |

## 3. Security posture — read this before the design

Upstream, verbatim:

> Server-side tools (web search, Python and terminal code execution) run as your user and are on by default. Anyone who can reach the server with the API key can run code on this machine.

**This outranks every other risk in this document.** Atlas's normal reflex is to route a web surface through Kong and publish it on `HOST_BIND_IP`. Doing that here would expose arbitrary code execution as the host user, on a stack whose other services are deliberately locked down. Non-negotiables for any integration:

1. **Loopback only.** Never `-H 0.0.0.0`. Never a default Kong route. `localhost`-flavour users who have exposed it themselves should be warned by `doctor`, not silently trusted.
2. **`--disable-tools` by default**, with enabling it an explicit, documented opt-in.
3. **`UNSLOTH_STUDIO_DISABLE_PUBLIC_CHECK=1`.** On a wildcard bind Studio asks `ifconfig.me` and `check-host.net` about reachability. A self-hosted stack should not phone third parties; set this regardless.
4. **Never `--secure` / `--cloudflare`.** Atlas already ships `cloudflared`; a second, vendor-owned tunnel is both redundant and a surprise egress path.
5. **Admin password from `key_generator.py`**, injected via env, rotated with `unsloth studio reset-password`.

Note the bootstrap deadline as a safety net, not a control: with an auto-generated password and a public URL, Studio shuts down after `UNSLOTH_STUDIO_BOOTSTRAP_TIMEOUT` (default 1h) unless the password is changed.

## 4. The gap it fills

Atlas has **no training capability at all** — verified by scanning every `services/*/service.yml`. Ollama, vLLM-Metal, LiteLLM and ComfyUI are inference or serving. The `ml-eng` track carries everything *around* training — MLflow, MinIO, JupyterHub, Zeppelin, Ray, Spark, Label Studio — with nothing in the middle that fine-tunes a model.

Unsloth closes the loop against services Atlas already runs:

```
Label Studio ──► dataset ──► MinIO
                               │
                    Unsloth Studio (Data Recipes → train) ──► MLflow
                               │
                        GGUF export ──► Ollama / vLLM-Metal
                               │
                          LiteLLM ──► Langfuse
```

The exported GGUF is consumed by a serving path Atlas already owns, so this is a closed loop rather than a bolt-on.

## 5. Service shape

### 5.1. Manifest sketch

Follows the `vllm_metal_manager.py` / `comfyui_mps_manager.py` template:

```
UNSLOTH_SOURCE, UNSLOTH_LOCALHOST_PORT, UNSLOTH_ENDPOINT, UNSLOTH_SCALE,
UNSLOTH_STATE_DIR, UNSLOTH_PIN_REF, UNSLOTH_MODELS_PATH,
UNSLOTH_MIN_MEMORY_GB, UNSLOTH_MIN_DISK_GB,
UNSLOTH_ADMIN_PASSWORD (secret), UNSLOTH_API_KEY (secret),
UNSLOTH_ENABLE_TOOLS (default false)
```

Lifecycle, matching the existing managed hosts:
`./start.sh unsloth preflight|install|start|stop|status|health|remove`

### 5.2. Flavour differences

| Concern | `localhost` | `managed-localhost` |
|---|---|---|
| Install | yours | Atlas, pinned clone + `install.sh --local` |
| State dir | yours | `UNSLOTH_STUDIO_HOME=$UNSLOTH_STATE_DIR` |
| Password | yours | generated, injected via env |
| Port | you declare it | Atlas assigns and passes `-p` |
| `stop.sh` | never touched | host-global singleton — left running, reported |
| Preflight | reachability + a tools/bind warning | memory, disk, Python, port |

The `stop.sh` row matters: like ComfyUI-MPS and vLLM-Metal, this is a **host-global singleton shared across consumers**, so a project-scoped teardown must not kill it. `report_managed_hosts_left_running` already exists for exactly this.

### 5.3. Category and track

`category: llm`, `ml-eng` track. Adding it to `gen-ai-eng` as well would double the wizard surface for a workload most users will not enable.

## 6. Integration surface

| Peer | Wiring | Confidence |
|---|---|---|
| **LiteLLM** | Register the OpenAI endpoint as an upstream, like vLLM-Metal. Bearer `sk-unsloth-…` | High |
| **MinIO** | Datasets in, adapters/GGUF out | High |
| **Ollama / vLLM-Metal** | Serve the exported GGUF | High |
| **Langfuse** | Free once served through LiteLLM (gateway-level tracing) | High |
| **Hermes / OpenClaw** | Upstream ships `unsloth start hermes` and `unsloth start openclaw` — both are Atlas services | High, and a real coincidence of design |
| **MLflow** | Built on HF TRL, so autologging is the likely hook | Medium — unverified |
| **Kong** | **Deliberately none by default** (§3) | — |

## 7. Remaining risks

### 7.1. GPU contention — the gating risk

Fine-tuning is the heaviest GPU workload there is, and this stack has a documented contention problem: concurrent stacks sharing host-global GPU runtimes have caused machine-level panics. Training beside a resident 18 GB `qwen3.8:latest`, ComfyUI holding Krea 2, and vLLM-Metal is a memory-exhaustion event waiting to happen on unified memory.

**Treat aggregate-residency admission control as a prerequisite, not a companion.**

### 7.2. Long-running jobs vs. Atlas's lifecycle

Atlas services are start/stop daemons; a fine-tune is an hours-long *job* with artifacts. `./stop.sh` mid-run destroys work. Needs an explicit answer: does Studio own job state, or does Ray/Airflow schedule it?

### 7.3. Licensing

Core is Apache-2.0 (matching Atlas). **Studio UI is AGPL-3.0** — and §2 makes Studio the centrepiece, so this is now load-bearing rather than incidental. Atlas does not redistribute upstream artifacts, and a locally-run UI is not a network-service distribution by Atlas; but a consumer shipping Atlas as a product should know one component's licence differs from the rest of the stack.

### 7.4. Jupyter overlap (container flavour only)

The image bundles Jupyter on `8888` and expects `JUPYTER_PASSWORD`. Atlas already runs JupyterHub. If the container flavour ships, the bundled Jupyter should be disabled or explicitly documented as separate.

### 7.5. Nightly-by-default install

`./install.sh --local` builds from `main`, i.e. nightly. Atlas must pin `UNSLOTH_PIN_REF` to a tag or SHA and treat updates as deliberate, exactly as it does for ComfyUI custom nodes.

## 8. Phased plan

| Phase | Deliverable | Gate to proceed |
|---|---|---|
| **0** | GPU aggregate-residency admission control | Do not add the heaviest GPU consumer to an unbounded stack |
| **1** | `localhost` flavour: manifest, endpoint wiring, LiteLLM registration, `doctor` checks for tools-enabled/wildcard-bind | Cheapest slice; proves the contract with zero install risk |
| **2** | `managed-localhost`: manager module, pinned install, state dir, generated password, lifecycle verbs | `status`/`health` clean without a training run |
| **3** | Train → export GGUF → serve through Ollama → route via LiteLLM, end to end | A fine-tuned adapter answers through the gateway |
| **4** | `container-gpu` for Linux/NVIDIA hosts | Only if a consumer actually has that hardware |
| **5** | MLflow autologging; Ray/Airflow job scheduling | Only after Phase 3 proves the loop |

Phase 1 is deliberately first now: the `localhost` flavour needs no installer, so it validates the endpoint contract, the security posture and the LiteLLM wiring before Atlas takes on lifecycle ownership.

## 9. Open decisions

1. **Is Phase 0 a prerequisite?** The central judgment call.
2. **Job model** — Studio-owned, or Ray/Airflow-scheduled (§7.2)?
3. **Tools on or off?** Default off is the safe call, but it disables Studio's agentic features (§3).
4. **Container flavour at all** on Apple-Silicon-only infrastructure (§8 Phase 4)?
5. **Track membership** — `ml-eng` only, or `gen-ai-eng` too (§5.3)?

## 10. Evidence log

Verified against upstream rather than recalled:

| Claim | How verified |
|---|---|
| macOS training + MLX + Metal | README hardware-support section |
| Image is amd64-only | Docker registry manifest query — `linux/amd64` only, no arm64 |
| Studio = training surface (Data Recipes, export, serve) | README feature list + Studio section |
| Binds `127.0.0.1` by default | README remote-access section |
| Tools run as your user, on by default | README security paragraph, quoted verbatim in §3 |
| `UNSLOTH_STUDIO_HOME` isolation | README advanced-install section |
| `UNSLOTH_STUDIO_PASSWORD` automation | README headless-setup guidance |
| Public-reachability check contacts third parties | README wildcard-bind paragraph |
| Apache-2.0 core / AGPL-3.0 Studio | GitHub API `spdx_id` + README licence section |
| Atlas has no training service | Manifest scan across `services/*/service.yml` |
| Dual-flavour precedent | `services/comfyui/service.yml` — `localhost` *and* `managed-localhost-mps` both ship |

**Not verified, flagged as such:** MLflow autologging behaviour, real memory footprint of a training run on this hardware, whether the container's bundled Jupyter can be cleanly disabled, and whether Studio exposes a non-interactive way to mint the `sk-unsloth-…` API key that LiteLLM registration would need.
