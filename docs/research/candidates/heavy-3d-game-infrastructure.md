---
category-fit: media
generated: 2026-07-04
license: mixed
name: Heavy 3D Game Infrastructure
referenced-by: []
slug: heavy-3d-game-infrastructure
type: external-service
upstream: https://github.com/tencent-hunyuan/hunyuan3d-2.1
---

# Heavy 3D Game Infrastructure

## Headline
Deferred 3D model, reconstruction, editor-automation, and realtime infrastructure for the creative-3D track; Atlas should stay asset pipeline first before adding heavyweight engines.

## Problem it solves
Hunyuan3D and TRELLIS/TRELLIS.2 can generate high-fidelity 3D assets from images, Nerfstudio can reconstruct scenes from photo/video captures, Unreal MCP can automate a local Unreal Editor, and LiveKit can power realtime collaborative or voice-driven review. These are compelling pieces of a future 3D/game track, but they are large, fast-moving, workstation-heavy, and risky if Atlas presents them before the asset store, thumbnailing, glTF validation, and editor-automation safety posture are real.

## Deferred decision (2026-07-04)
Atlas should keep heavy 3D/game infrastructure deferred and must not add `services/hunyuan3d/service.yml`, must not add `services/trellis/service.yml`, must not add `services/nerfstudio/service.yml`, must not add `services/unreal-mcp/service.yml`, and must not add `services/livekit/service.yml` in this decision ticket.

The approved posture is asset pipeline first: ComfyUI concepts, MinIO asset buckets, imgproxy-style thumbnails, glTF-Transform inspection/optimization, and the existing disabled localhost-only Blender MCP profile. Atlas should build that product path before adding GPU-heavy 3D foundation models, reconstruction stacks, game-engine editor automation, or realtime media infrastructure. This is not a default generate-a-whole-game promise.

Current upstream checks reinforce the boundary:

- Hunyuan3D-2.1 and TRELLIS.2 are capable image-to-3D systems, but they are large model stacks with meaningful VRAM, model-cache size, license, output-format, and GPU-support questions.
- Nerfstudio is a reconstruction/training toolkit for NeRFs and Gaussian splats, not a general asset generator.
- Unreal MCP runs inside the Unreal Editor process; official docs describe same-machine use, no authentication layer by default, and no remote-use design.
- LiveKit is strong realtime audio/video/data infrastructure and its Agents SDK is a real voice-agent framework, but Atlas needs a concrete collaborative review or voice workflow before adding a WebRTC service tier.

## Stack wiring sketch
No current Atlas wiring should be added while heavy 3D/game infrastructure remains deferred. If future tickets reopen individual pieces, the expected topology is:

- ComfyUI -> MinIO for concept images, textures, previews, and source prompts.
- MinIO -> glTF-Transform for GLB inspection, validation, optimization, meshopt compression, texture compression, metadata extraction, and artifact lineage.
- imgproxy -> root dashboard or asset-browser surfaces for cheap thumbnails and previews of generated media and 3D-adjacent assets.
- Blender MCP -> host-local review, cleanup, and scene assembly only after explicit human approval and no Kong route for editor automation.
- Hunyuan3D/TRELLIS -> MinIO/Blender MCP/glTF-Transform only after VRAM, model-cache size, output-format contracts, and license posture are pinned.
- Nerfstudio -> MinIO/glTF-Transform only for reconstruction/scanning workflows with capture provenance and GPU budgets.
- Unreal MCP -> host-local developer option only; no default container, no public route, and no route through Kong for editor automation.
- LiveKit -> collaborative realtime review or voice-driven creator workflow only after Atlas has a product use case, auth, TURN/networking, and observability.
- Open WebUI and Hermes may become downstream consumers of safe asset summaries or curated MCP tools only after policy and prompt-injection controls exist.

## Effort
Large. Each candidate has a different runtime shape: GPU model service, reconstruction training stack, local editor automation, or realtime media infrastructure. Atlas needs asset storage, thumbnails, validation, artifact provenance, policy, routes, resource budgets, and user-facing workflow design before adopting any of them safely.

## Risks & open questions
- Resource cost: Hunyuan3D/TRELLIS/TRELLIS.2 and Nerfstudio can require large GPU memory, long model downloads, local CUDA compatibility, and large cache volumes.
- Output contracts: Atlas must define GLB/glTF/OBJ/USD/3DGS outputs, texture/material expectations, preview generation, validation failures, and asset lineage before models write into shared buckets.
- Editor automation: Blender MCP and Unreal MCP can execute generated or external code in desktop editor processes. Do not expose editor automation remotely or through Kong by default.
- Licensing: model weights, code, generated assets, and commercial use terms can differ across Hunyuan3D, TRELLIS, Unreal tooling, and related model repos.
- Security: MCP/editor tools, model downloads, asset importers, and user-supplied scenes can read files, execute scripts, or trigger parser bugs if not sandboxed.
- Product scope: LiveKit is a real infrastructure tier, not a thumbnail or asset-processing helper. It should not be added without a concrete review/voice/collaboration workflow.

## Future service contract if reopened
- **Tracks:** `gen-ai-creative` and `all`. Do not add these to `gen-ai-rag`, `gen-ai-eng`, `ml-eng`, or `data-eng` by default. A future `creative-3d` track key may be justified only if Atlas promotes the 3D asset pipeline beyond the current creative track.
- **Categories:** `media` for Hunyuan3D, TRELLIS/TRELLIS.2, and Nerfstudio; `apps` for Unreal MCP if represented as a developer/editor bridge; `infra` for LiveKit if it becomes realtime media infrastructure.
- **Sources:** `HUNYUAN3D_SOURCE=disabled|container-gpu|localhost`, `TRELLIS_SOURCE=disabled|container-gpu|localhost`, `NERFSTUDIO_SOURCE=disabled|container-gpu|localhost`, `UNREAL_MCP_SOURCE=disabled|localhost`, and `LIVEKIT_SOURCE=disabled|container|localhost`; all disabled by default.
- **Wizard placement:** after ComfyUI, imgproxy/thumbnailing, glTF-Transform, and Blender MCP. Prompt copy must say these are heavy optional 3D/game integrations, not a default generate-a-whole-game promise.
- **Ports and aliases:** allocate ports through Atlas topology/category slot rules with custom `BASE_PORT` for any container service. Expected aliases, if ever enabled, are service-specific and only for safe UIs/APIs. There must be no Kong route for editor automation, no public Unreal/Blender MCP route, and no model-download or asset-import endpoint without auth and limits.
- **Required dependencies:** MinIO, ComfyUI, glTF-Transform asset postprocess, thumbnail/preview path, and the existing Blender MCP safety pattern before Hunyuan3D/TRELLIS/Nerfstudio. LiveKit additionally needs auth, TURN/networking decisions, observability, and a voice/review workflow.
- **Optional dependencies:** imgproxy for thumbnails, backend for mediated jobs, Open WebUI and Hermes for safe consumer surfaces, LiteLLM for prompt orchestration, Langfuse for LLM traceability, and Ray only for later batch rendering or evaluation scale-out.
- **Downstream consumers:** asset browser/root dashboard, backend mediated asset jobs, Open WebUI previews, Hermes agent workflows, Blender MCP cleanup, and future review UIs. Consumers must use bounded job APIs and validated artifacts rather than raw editor-control sockets.
- **Manifest and topology:** each reopened service needs a manifest, topology row, source validation, env assembly, docs, diagrams, and `data_flow.calls` entries covering asset reads/writes, model cache, editor-control paths, realtime media paths, and downstream consumers.
- **Init companion:** likely yes for GPU model services and LiveKit. The init companion must create asset/model-cache directories, validate GPU/profile settings, seed safe config, refuse prod-unsafe editor automation, check credentials, and keep model downloads explicit.
- **Volumes and secrets:** separate model caches, raw inputs, generated assets, thumbnails, optimized GLB output, logs, and credentials. Signed asset-provider/model-download credentials must not be scattered through `.env` once secrets manager support exists.
- **Tests required:** focused decision tests, manifest/source/topology/env tests, disabled-default behavior, prod-profile restrictions, no-Kong-editor-route audits, custom `BASE_PORT`, GPU/CPU profile gating, model-cache env validation, source permutations, docs drift, research schema, route checks, link checks, and service-specific smoke tests with tiny fixtures where possible.
- **Edge cases:** disabled ComfyUI, disabled MinIO, missing glTF-Transform, missing imgproxy, missing GPU, insufficient VRAM, stale model cache, failed downloads, incompatible CUDA, unsupported asset format, invalid GLB, huge textures, unsafe Blender/Unreal scripts, Kong exposure drift, LiveKit TURN/networking failures, prod profile restrictions, stale `.env`, and generated-doc drift.

## Revisit criteria
Reconsider individual heavy 3D/game services only when all of these are true:

- Asset pipeline, thumbnails, glTF processing, and MCP safety posture are already real.
- The specific candidate has a named workflow, resource budget, license posture, output-format contract, and owner.
- The service is disabled by default and track-scoped.
- Blender/Unreal-style editor automation is never exposed through Kong by default.
- The first reopened ticket covers exactly one candidate family, not the entire 3D/game stack at once.

## Why now (and why not sooner)
The current stack already contains ComfyUI and a conservative Blender MCP profile, so future workers may be tempted to add the rest of the 3D/game stack in one leap. Capturing the deferral now keeps Atlas oriented around inspectable, portable assets before it absorbs heavyweight model, editor, engine, and realtime infrastructure.

## Upstream evidence
- https://github.com/tencent-hunyuan/hunyuan3d-2.1
- https://github.com/Tencent-Hunyuan/Hunyuan3D-2
- https://github.com/microsoft/TRELLIS.2
- https://microsoft.github.io/TRELLIS.2/
- https://github.com/microsoft/TRELLIS
- https://docs.nerf.studio/
- https://github.com/nerfstudio-project/nerfstudio
- https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor
- https://docs.livekit.io/agents/
- https://docs.livekit.io/transport/self-hosting/
- https://github.com/livekit/livekit

## Cross-references
- `../../strategy/atlas-vnext-strategy-report.md#81-3d--game-generation-track`
- `../../strategy/atlas-vnext-strategy-report.md#94-reject-or-defer-for-now`
- `https://github.com/thekaveh/atlas/blob/main/services/blender-mcp/README.md`
- `../candidates/imgproxy.md`
