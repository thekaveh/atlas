# Blender MCP

## 1. Overview

Blender MCP is a disabled, host-installed profile for experimenting with MCP-assisted 3D scene work from a local Blender session. Atlas records the source option, wizard placement, environment contract, and docs, but it does not run Blender or the MCP server as a container.

This profile is intentionally conservative. Current Blender MCP workflows depend on a local Blender add-on, an MCP client/server process, and a socket opened by Blender. They can execute generated Python code inside Blender, so Atlas keeps the bridge disabled by default and does not publish it through Kong.

## 2. Access

| Surface | URL or command | Notes |
|---|---|---|
| Atlas SOURCE | `BLENDER_MCP_SOURCE=disabled` | Default. No Blender MCP bridge is active. |
| Host Blender MCP | `BLENDER_MCP_SOURCE=localhost` | Development-only source. Requires host-installed Blender, Blender MCP add-on, and MCP server/client configuration. |
| Blender socket | `${BLENDER_MCP_HOST}:${BLENDER_MCP_LOCALHOST_PORT}` | Defaults to `localhost:9876`, matching common Blender MCP socket defaults. |
| Kong | No Kong route | There is no `blender-mcp.localhost` route and no `blender.localhost` route by design. |

Enable the wizard profile with:

```bash
./start.sh --blender-mcp-source localhost
```

The profile is hidden/rejected under `--profile prod`.

## 3. Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `BLENDER_MCP_SOURCE` | `disabled` | Enables the host-only Blender MCP profile when set to `localhost`. |
| `BLENDER_MCP_HOST` | `localhost` | Hostname where the Blender MCP add-on socket listens. |
| `BLENDER_MCP_LOCALHOST_PORT` | `9876` | Host-tool socket port. This is not allocated from Atlas topology because Atlas does not own the Blender process. |
| `BLENDER_MCP_ENDPOINT` | generated | Runtime endpoint hint for future MCP-client integrations. Empty when disabled. |

## 4. Architecture & Wiring

Atlas models Blender MCP as a virtual media service:

- Track membership: `gen-ai-creative` and `all`.
- Service category: `media`.
- Source values: `disabled` and dev-only `localhost`.
- Wizard placement: the creative track prompt appears as “Blender MCP”.
- Port strategy: `BLENDER_MCP_LOCALHOST_PORT` is a host-tool override and does not consume an Atlas topology slot.
- Kong behavior: no alias, no route, no extra host entry, and no gateway proxy by default.
- Direct access: configure the host MCP client/server according to the Blender MCP implementation you choose, then point it at `${BLENDER_MCP_HOST}:${BLENDER_MCP_LOCALHOST_PORT}`.
- Downstream consumers: none are auto-wired in this ticket. Future Open WebUI, Hermes, or curated MCP integrations must add explicit consumer docs, env wiring, and `data_flow.calls` edges when they actually call the bridge.
- Init companion: none. Atlas does not install Blender, the Blender add-on, `uvx`, Python packages, or client config.
- Volumes and secrets: none by default. Asset-provider credentials such as Sketchfab, Poly Haven, Hyper3D, or Hunyuan-style keys remain host-side user configuration until Atlas adopts a dedicated integration.

## 5. Dependencies & Integrations

### 5.1 Current — Upstream (this service calls)

_No upstream calls._

### 5.2 Current — Downstream (services that call this)

_No downstream consumers._

### 5.3 Architecture diagram

![blender-mcp architecture](./architecture.svg)

[Open the interactive HTML diagram](./architecture.html) for a full-screen view.

### 5.4 Future — Missing pair integrations

- Optional MCP-client registration for Open WebUI or Hermes once Atlas has a policy for host-side code-execution tools.
- Optional asset export path from ComfyUI-generated concepts to Blender scene construction, with explicit human approval before code execution.

### 5.5 Future — Candidate new services

- A drivable, in-network **`container` source** (headed-but-virtual Blender via Xvfb/EGL) for the agentic composition stage — under evaluation, gated behind a validation spike and go/no-go thresholds. See [`docs/strategy/blender-mcp-container-source-evaluation.md`](../../docs/strategy/blender-mcp-container-source-evaluation.md) (#410). Until that spike passes, this service stays `localhost | disabled`.
- Asset validation queue that runs glTF-Transform checks on generated GLB files before publication.

### 5.6 Future — Unused features in this service

- Remote Blender MCP access is intentionally out of scope for this profile.
- Asset-provider credentials are not projected into Atlas services yet.

## 6. Security & Guardrails

- Treat Blender MCP as a code-execution bridge. Current workflows can execute generated Python code inside Blender, which may read, modify, delete, or exfiltrate local data accessible to that Blender process.
- Use a separate OS account, VM, or machine without sensitive files for experiments.
- Keep `BLENDER_MCP_SOURCE=disabled` unless you are actively using a trusted local Blender session.
- Do not expose the Blender MCP socket on public interfaces. Prefer `BLENDER_MCP_HOST=localhost`.
- Do not add a Kong route without a separate design review covering auth, network reachability, tool approval, and prompt-injection behavior.
- Do not paste Atlas database, cloud-provider, Supabase, MinIO, or GitHub credentials into host MCP client configuration for this bridge.

## 7. glTF-Transform Asset Postprocess

Atlas includes a helper for inspecting and optimizing GLB assets without adding a long-running service:

```bash
scripts/gltf-transform-postprocess.sh input.glb output.glb
```

The script runs the official `@gltf-transform/cli` in a temporary Node container. It performs:

- `gltf-transform inspect`
- `gltf-transform validate`
- `gltf-transform optimize --compress meshopt --texture-compress webp`

Use this as a postprocess step for exported Blender assets, ComfyUI-assisted 3D experiments, or future creative-3D pipelines. Inspect the output visually before treating it as production-ready; optimization can change geometry, textures, and extension usage.

## 8. Troubleshooting

- If the wizard profile is missing, confirm you selected the `gen-ai-creative` or `all` track, or pass `--blender-mcp-source localhost` explicitly.
- If `--profile prod` rejects the source, that is expected: Blender MCP localhost mode is development-only.
- If a client cannot connect, confirm the Blender add-on is installed, enabled, and listening on `${BLENDER_MCP_HOST}:${BLENDER_MCP_LOCALHOST_PORT}`.
- If `uvx` is not found by a GUI MCP client, configure the absolute path to `uvx` or the installed Blender MCP command in that client.
- If `scripts/gltf-transform-postprocess.sh` fails before optimization, inspect the validation output first; invalid GLB input should be fixed at the source.
