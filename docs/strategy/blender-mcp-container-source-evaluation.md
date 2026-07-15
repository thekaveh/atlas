# Blender MCP Container-Source Evaluation

Generated for issue #410, "blender-mcp: container source — a drivable, in-network Blender for agentic composition in production".

This is an **evaluation artifact**: it records a go/no-go decision framework and the evidence, contracts, thresholds, and threat model that a *separate future implementation ticket* must satisfy **before** a `container` source may ship. It is deliberately **not** an implementation — no `container` source is added, the virtual manifest is not converted, and `BLENDER_MCP_SOURCE` is unchanged (`localhost | disabled`, default `disabled`). It mirrors the existing Atlas evaluation deliverables [`docs/strategy/rag-evaluation-matrix-evaluation.md`](./rag-evaluation-matrix-evaluation.md), [`docs/strategy/authentik-sso-pilot-evaluation.md`](./authentik-sso-pilot-evaluation.md), and [`docs/strategy/infisical-secrets-manager-evaluation.md`](./infisical-secrets-manager-evaluation.md).

Part of the #333 creative-media epic. It complements — and never replaces — the deterministic headless bake worker ([#407 asset-baker](https://github.com/thekaveh/atlas/blob/main/services/asset-baker/README.md)): the "two Blenders" split is a **drivable studio** (this ticket, agentic, GUI/virtual-display) versus a **headless batch worker** (asset-baker, deterministic, `blender -b`).

## 1. Decision

**READY only as an isolated validation spike. A shippable `container` source is CONDITIONAL on that spike meeting the go/no-go thresholds in §7.**

The agentic composition stage (compose → screenshot/render → judge → adjust) genuinely needs a *drivable* Blender the agent can see, and Atlas's `blender-mcp` service is the correct seam. But the container path crosses four unproven risks — add-on behaviour under a virtual display, GPU-less capture fidelity, in-network socket binding, and an arbitrary-code-execution security surface — that must be measured on an isolated spike before any manifest change. Until the spike passes, the production Composer has no in-network Blender and continues to depend on a human-attended host running Blender (`localhost:9876`); that is an accepted, documented gap, not a regression.

This evaluation does not itself run Blender containers or report measured benchmark numbers. It defines *what the spike measures*, *the thresholds it must clear*, and *the contracts the eventual `container` source must honour*.

## 2. Current state on `main`

`services/blender-mcp/service.yml` is a **virtual manifest** (`virtual: true`, `containers: []`) that owns only the source toggle and endpoint hint:

| Field | Value |
|---|---|
| `BLENDER_MCP_SOURCE` | `localhost` (host add-on, `profiles: [default]`) \| `disabled` — **default `disabled`** |
| `BLENDER_MCP_HOST` | `localhost` |
| `BLENDER_MCP_LOCALHOST_PORT` | `9876` — documented as "a host-tool port hint, **not** a Kong or container port" |
| `BLENDER_MCP_ENDPOINT` (auto-managed) | `tcp://${BLENDER_MCP_HOST}:${BLENDER_MCP_LOCALHOST_PORT}` for `localhost`; blank for `disabled` |

Posture (see `services/blender-mcp/README.md` §6 "Security & Guardrails"): no container, no Kong route, no exposed stack port, disabled by default, `depends_on` empty, `data_flow.calls: []`. The service is a **development-only host bridge**: the operator runs Blender with the MCP add-on on their own machine and points a host-run MCP client at `tcp://localhost:9876`.

The endpoint is already modelled as a **raw `tcp://` socket**, not an HTTP URL — the manifest is correct on that point and the container source must preserve it.

## 3. Protocol & binding correction (the load-bearing misconception)

The original ticket text implied "expose the MCP socket in-network" as a one-line change. The triage is correct that this rests on a wrong mental model; the spike must be designed around the real topology.

**There are two distinct processes, not one.** The upstream [`ahujasid/blender-mcp`](https://github.com/ahujasid/blender-mcp) design is:

1. **The Blender add-on** runs *inside* the Blender process and opens a **raw JSON-over-TCP control socket** (default `localhost:9876`). It receives `{"type": ..., "params": ...}` command frames and executes them in Blender's Python — including arbitrary `execute_code`.
2. **The FastMCP server** (`uvx blender-mcp`, a separate Python process) speaks **MCP over stdio** to the agent/client on one side, and connects as a **TCP client to `9876`** on the other. It is *not* an HTTP server and does *not* listen on `9876`.

Consequences the spike must respect:

- **"In-network reachability" means the add-on's `9876` TCP socket must bind a container-routable interface** (e.g. `0.0.0.0` inside the container, reachable as `blender-mcp:9876`). Upstream binds `localhost`, so this requires an **intentional, maintained configuration or patch of the add-on**, pinned to an exact revision — not a compose port mapping alone. A compose `ports:`/network entry cannot reach a socket bound to `127.0.0.1` inside the container.
- **Where does FastMCP run?** Two candidate topologies, to be decided by the spike: (a) FastMCP stays *consumer-side* (the Composer runs `uvx blender-mcp` itself and connects to `blender-mcp:9876` over the stack network — Atlas ships only the drivable Blender + socket), or (b) FastMCP runs as a sidecar in the same container exposing a network transport. Option (a) is the LiteLLM-style "substrate, not agent" division and is strongly preferred; the spike should validate (a) first.
- **Scene portability is not free.** The add-on operates on Blender's *in-memory* scene and host filesystem paths. Adding a volume does **not** make an unsaved scene or a host-absolute asset path portable. Persistence requires an explicit `.blend` **save** step and asset paths rooted in a mounted, in-container directory (see §6).

## 4. The validation spike (isolated, non-shipping)

The spike is a throwaway harness in a scratch branch/dir — **never merged, never added to the compose include list, never given a manifest source**. Its only output is measurements + logs + this document's go/no-go verdict, filled in by whoever runs it.

### 4.1. Pinned revisions (exact, recorded)

- Blender: an exact tagged release (e.g. `blender-4.2.x-linux-x64`) — record the full version string and build hash.
- `ahujasid/blender-mcp` add-on: an exact commit SHA (not `main`), plus the exact `blender-mcp` FastMCP package version.
- Base image + Xvfb/EGL/mesa package versions, recorded from the built image.
- Platform: **Linux amd64** (the production target; Docker Desktop on macOS cannot pass Metal into a Linux container, which is the whole reason the host `localhost` source exists).

### 4.2. Spike test matrix

Each row is pass/fail with evidence (log excerpt, saved image, or metric):

| # | Check | Evidence required |
|---|---|---|
| 1 | **Virtual-display startup** — Blender launches headed under Xvfb (and, separately, EGL) and the add-on's socket server starts | add-on log line showing the socket bound; process stays up |
| 2 | **Wire-schema compatibility** — the pinned add-on's current JSON command schema matches what an in-network client sends | successful round-trip of a trivial command frame |
| 3 | **Intentional container binding** — the add-on socket is reachable at `blender-mcp:9876` from another container (not just `127.0.0.1`) | `nc`/client connect from a sibling container |
| 4 | **execute_code** — run arbitrary Blender Python via the socket | returned result of a scene mutation |
| 5 | **import GLB from MinIO** — pull a GLB via a scoped object-storage call and import it | object visible in scene (via #7) |
| 6 | **save `.blend`** — persist the scene to a mounted path | file present on the volume after save |
| 7 | **viewport screenshot** — capture the *headed* viewport (the agent's "eyes") under Xvfb/EGL with **no GPU** | a non-blank PNG whose content matches the scene |
| 8 | **CPU render** — trigger an EEVEE/Cycles **CPU** render to a MinIO-persisted path | rendered image + wall-clock timing |
| 9 | **MinIO asset transfer** — round-trip import-asset and export-render through scoped object-storage calls (no FUSE) | objects land in the expected bucket/prefix |
| 10 | **autosave / restart recovery** — kill and restart the container; the last saved scene reloads | scene state present after restart |
| 11 | **graceful shutdown** — SIGTERM stops Blender + the socket cleanly without orphaning the display server | clean exit, no zombie Xvfb |

### 4.3. Metrics to record (Linux amd64)

Container image size; idle and under-load CPU/memory (RSS); **cold start** to socket-ready; **viewport screenshot latency + visual correctness** (does the PNG actually show the scene, or a blank/garbage frame under GPU-less capture?); **CPU render latency** for a fixed reference scene; add-on log completeness on errors; and failure-recovery time after a forced restart. Screenshot correctness and CPU render latency are the two highest-risk measurements — the agent's eyes are the entire point, and GPU-less viewport capture is the most likely failure mode.

## 5. Threat model

The add-on executes **arbitrary Python inside Blender** by design; a container source turns a host-local, developer-attended risk into an in-network service. The future implementation must ship with, and this evaluation requires, an explicit threat model covering:

| Surface | Control the container source must enforce |
|---|---|
| **Arbitrary code execution** | The socket accepts `execute_code`. Treat the endpoint as a **trusted-callers-only** internal service. Never expose through Kong; never bind a host-published port by default; in-network only. Default `BLENDER_MCP_SOURCE=disabled`. |
| **Network egress** | Blender + arbitrary Python can reach the internet (asset downloads, exfiltration). The spike must document the egress posture and the future source should support a restricted/deny-by-default egress policy. |
| **Mounts** | Only a dedicated, scoped volume for `.blend`/render artifacts. No host-root mounts, no Docker socket, no broad bind mounts. Asset I/O goes through scoped object-storage calls, not host paths. |
| **Secrets** | The container must not receive stack-wide credentials. MinIO access uses **consumer-scoped, least-privilege** credentials limited to the composition bucket/prefix (mirrors the #404 consumer object-storage contract). |
| **Resource limits** | CPU render and an arbitrary-code surface invite DoS. Require CPU/memory limits and a render/timeout budget; a runaway `execute_code` must not starve the stack. |
| **Denial of service** | Single-session assumption; concurrent drivers can corrupt scene state and exhaust memory. Document the concurrency model (single active session) and the eviction/timeout behaviour. |

Unauthenticated exposure beyond the stack network is **forbidden**. If a future need arises to reach it from outside, that is a separate authenticated-gateway decision, not a default.

## 6. Persistence contract (no FUSE)

Per the triage, **do not add a MinIO FUSE mount.** Persistence is achieved by either:

- **Scoped object-storage calls** — the driver (or an in-container helper) `PUT`s the saved `.blend` and rendered images to a consumer-scoped MinIO bucket/prefix and `GET`s assets to import, using least-privilege credentials; or
- **A dedicated volume with explicit export** — `.blend`/render artifacts persist to a named volume, and a deliberate export step copies them to object storage.

In-memory scene state is **not** durable until a `.blend` save; the container source must make the save step explicit in its lifecycle, and asset paths must be rooted in the mounted directory so a restart can reload them.

## 7. Go/No-Go thresholds

A **GO** (spike passes; a `container` source may be built by the future ticket) requires **all** of:

1. Matrix rows **1–11 all pass** on the pinned revisions on Linux amd64.
2. **Viewport screenshot correctness**: captured PNGs demonstrably show the scene (not blank/garbage) under GPU-less Xvfb/EGL — this is non-negotiable; the agent's eyes are the point.
3. **CPU render** completes for the reference scene within a recorded, documented latency budget acceptable for an agent loop (a value the spike proposes and the reviewer accepts).
4. **Intentional in-network binding** achieved via a *pinned, maintained* add-on config/patch — not a fork that can silently drift.
5. **Threat-model controls (§5) are all implementable** with the chosen base image and Atlas's existing scoped-credential mechanisms.

Any of the following is a **NO-GO** (keep `localhost | disabled`; do not add a container source): blank/garbage screenshots under GPU-less capture; add-on refuses to run or bind under the virtual display without an unmaintainable patch; CPU render latency incompatible with an interactive agent loop; or a threat-model control that cannot be enforced.

## 8. What NOT to do before a passing spike

- **Do not** add a `container` id to `BLENDER_MCP_SOURCE` or convert the virtual manifest to a container family.
- **Do not** add a `compose.yml`, Dockerfile, or Kong route for a containerized Blender.
- **Do not** add a MinIO FUSE mount.
- **Do not** fork the add-on onto an unpinned/`main` branch; any binding change is a pinned, reviewed patch.
- **Do not** build a "Composer service" — the vision-LLM ↔ MCP ↔ render-verify agent loop and its product rules stay **consumer-side** (the LiteLLM "substrate, not agent" division). That is a separate, far larger ticket.

## 9. Acceptance criteria for the future implementation ticket

When the spike returns **GO**, a follow-up implementation ticket may:

- [ ] Add `container` to `BLENDER_MCP_SOURCE` (→ `container | localhost | disabled`) and convert `services/blender-mcp/` from a virtual manifest to a real container family (`compose.yml`, pinned image, `runtime_sc` for the new source), preserving the existing `localhost` behaviour byte-for-byte.
- [ ] Ship the pinned add-on config/patch that binds the `9876` socket to a container-routable interface, reachable as `blender-mcp:9876`; **no Kong route, no host-published port by default**.
- [ ] Prove the in-network client contract: execute code, import a GLB from MinIO, take a viewport screenshot, and trigger a CPU render written to a MinIO-persisted path — with automated tests (mocked where hardware/GPU-less capture cannot run in CI; a marked, optional live test for the real round-trip).
- [ ] Persist scene/session state across container restart via the §6 contract (no FUSE).
- [ ] Enforce every §5 threat-model control; keep `BLENDER_MCP_SOURCE` default `disabled`; document the arbitrary-code-execution surface and the in-network-only rule in `services/blender-mcp/README.md`.
- [ ] Regenerate the standard new-source surfaces (`.env.example`, manifest/topology, three-surface docs, source-configuration matrix, discovery/permutation/fragment tests) — see the "new source-service gates" for the full list.

## 10. Where it lives & follow-ups

- **This evaluation:** `docs/strategy/blender-mcp-container-source-evaluation.md` (this file).
- **Future implementation ticket:** to be opened only after a GO verdict, referencing §9.
- **Related:** #333 (creative-media epic, parent), [#407 asset-baker](https://github.com/thekaveh/atlas/blob/main/services/asset-baker/README.md) (deterministic headless sibling), and the existing host bridge at [`services/blender-mcp/README.md`](https://github.com/thekaveh/atlas/blob/main/services/blender-mcp/README.md).

## 11. Recommendation

Run the §4 spike as an isolated, non-shipping harness on Linux amd64 against pinned revisions. Gate any `container` source strictly on the §7 thresholds, with **viewport screenshot correctness under GPU-less capture** as the single most decisive signal. Keep the shipped service exactly as it is today — virtual, `localhost | disabled`, default `disabled`, out of Kong — until that evidence exists. Keep the Composer agent loop consumer-side; Atlas provides the drivable-Blender substrate, not the agent.
