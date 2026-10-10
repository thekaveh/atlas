# NVIDIA OpenShell Evaluation

Generated on 2026-10-10 as an exploratory evaluation. No tracking issue exists yet; open one before any implementation starts.

This is an **evaluation artifact**. It records what NVIDIA OpenShell is, whether and how it fits Atlas, which existing Atlas services it can integrate with, and what Atlas can learn from the OpenShell repository's organization, CLI, TUI and onboarding. It ships no implementation: no manifest, compose fragment, or wizard step is added. It follows the other records in this directory, such as [`blender-mcp-container-source-evaluation.md`](./blender-mcp-container-source-evaluation.md) and [`infisical-secrets-manager-evaluation.md`](./infisical-secrets-manager-evaluation.md).

Snapshot studied:

| Repository | Revision | Notes |
|---|---|---|
| [`NVIDIA/OpenShell`](https://github.com/NVIDIA/OpenShell) | `eeba0e79` (2026-10-10) | Latest stable tag `v0.1.3` (2026-10-09); newest pre-release `v0.1.4-pre.2`. Apache-2.0. |
| [`NVIDIA/NemoClaw`](https://github.com/NVIDIA/NemoClaw) | `fcbbab0` (2026-10-09) | NVIDIA's reference stack that runs OpenClaw, Hermes and LangChain Deep Agents inside OpenShell sandboxes. Apache-2.0, labelled alpha. |
| Atlas | `15615ba` (2026-10-09) | `main`. |

Upstream paths below are relative to the OpenShell repository at that revision unless prefixed `NemoClaw:`. Both repositories were read, not executed. No OpenShell gateway or sandbox was run for this evaluation, so runtime behavior comes from upstream docs and code, and unverified claims are marked as such.

## 1. Decision

**CONDITIONAL GO.** OpenShell is the strongest match Atlas has found for its largest documented security gap. Atlas runs autonomous agents (OpenClaw, Hermes, TrueForge) and arbitrary code (JupyterHub, n8n Code nodes, Airflow DAGs, Backend plugins) with no sandbox, and most of those services hold the stack-wide `LITELLM_MASTER_KEY`. OpenShell adds deny-by-default egress, per-binary L7 rules, credentials the agent never sees, a human approval loop for new access, and a formal policy prover, under Apache-2.0.

It should enter Atlas in a specific shape:

1. **Integrate OpenShell directly, not NemoClaw.** NemoClaw pins OpenShell to exactly `0.0.116` (`NemoClaw:nemoclaw-blueprint/blueprint.yaml`) and is built on the `inference.local` route that OpenShell `0.1.0` removed (`docs/upgrade/0-1-0.mdx`).
2. **Start host-managed, not containerized.** Ship an opt-in, disabled-by-default `openshell` service whose first source is `localhost` (an operator-installed gateway). A `container` source requires the gateway to mount `/var/run/docker.sock`, which Atlas has refused everywhere so far. That source waits for an explicit risk decision or for the rootless Podman driver (Section 5.3).
3. **Linux first.** Sandboxes need a Linux 6.2+ kernel with Landlock ABI 3 and Docker 28+. On macOS, Docker Desktop must have host networking enabled and Enhanced Container Isolation turned off. Upstream lists WSL2 as experimental.
4. **Run an isolated spike before any manifest change** (Section 7.1). OpenShell's network model (Section 5.1) and lifecycle model (Section 5.5) differ from every service Atlas ships today.
5. **Position it as agent governance, not as the ROADMAP's microVM sandbox.** The Docker and Podman drivers share the host kernel, and the microVM driver is experimental, so OpenShell does not yet meet the ROADMAP's Firecracker bar for hostile LLM-generated code (Section 4.3).

Independently of the runtime decision, Atlas can adopt a set of practices from the OpenShell and NemoClaw repositories in its repo layout, CLI, TUI and wizard (Section 8). Most are small.

## 2. What OpenShell is

OpenShell describes itself as "the safe, private runtime for fleets of autonomous AI agents" (`README.md`). It was announced at GTC in March 2026, tagged `v0.1.0` on 2026-09-25, and is the open-source runtime of NVIDIA's Open Agent Safety Platform. The runtime needs no NVIDIA hardware. The platform's hardware-backed Sentry component targets BlueField DPUs and is irrelevant to Atlas.

### 2.1. Components

| Component | Role |
|---|---|
| Gateway (`openshell-gateway`) | Control plane: sandbox lifecycle, policy and provider storage, authentication, the policy prover, and relays for SSH, exec and port forwarding. One Rust binary. SQLite by default; PostgreSQL through `OPENSHELL_DB_URL`. |
| Supervisor | The trusted side of each sandbox. Holds the gateway JWT and provider credentials, checks every connection against policy (embedded OPA/Rego), injects credentials, and opens every approved upstream connection itself. |
| Sandbox runtime | A capability-free binary inside the workload, next to the agent. It applies Landlock, intercepts every TCP connect and DNS query with seccomp user notification, and hands them to the supervisor. It "never makes policy decisions" (`docs/about/architecture.mdx`). |
| Compute driver | Builds the isolation boundary: Docker, Podman, Kubernetes, a libkrun microVM (experimental), and Windows MXC (coming). |

The workload's only egress is an authenticated HTTP/2 channel to its supervisor. If the supervisor disconnects, the sandbox freezes the agent. Every credential the gateway issues is bound to one sandbox and one run of it.

### 2.2. Enforcement

- **Filesystem:** Landlock read-only and read-write path lists, fixed at sandbox creation.
- **Process:** a non-root identity only, no Linux capabilities, `no-new-privileges`, and a PID limit. Syscall filtering is internal and not configurable by policy.
- **Network:** each rule pairs endpoints (host and port) with binaries (the real executable path, hash-pinned on first use). Each endpoint can add L7 inspection: `rest` (method, path, query), `websocket`, `graphql`, `mcp` (method and tool name), `json-rpc`, or plain `tcp`. `enforcement: audit` is the default; `enforce` blocks. Loopback, link-local and cloud-metadata addresses can never be authorized, and private IPs need an exact hostname or `allowed_ips`.
- **Credentials:** a provider binds a stored secret to endpoints. The agent sees only a placeholder such as `openshell:resolve:env:KEY`. The supervisor substitutes the real value in headers, query strings, paths, or opt-in request bodies, and can re-sign AWS SigV4 requests. TLS is intercepted with a per-sandbox CA that the workload is configured to trust.
- **Middleware:** optional gRPC services that inspect or rewrite requests and responses before credentials are added (`proto/supervisor_middleware.proto`, labelled a research preview).

### 2.3. Policy lifecycle

- Network rules reload live (`openshell policy update` or `policy set`, with `--wait` and `--dry-run`). Filesystem and process changes need a new sandbox. Revisions can be listed and rolled back.
- When OpenShell blocks a connection, it drafts a narrow allow rule. With the advisor setting on, agents can also propose rules through an in-sandbox API at `http://policy.local`.
- Operators approve or reject drafts with `openshell rule approve` and `rule reject`, in the `openshell term` TUI, or over gRPC.
- The **prover** encodes a policy, its credentials and a binary capability registry as a Z3 SMT model, then runs reachability queries (`crates/openshell-prover`). It flags new credentialed reach, L7 bypass, link-local reach and capability expansion, and any finding blocks auto-approval. `openshell-prover check candidate.yaml --boundary boundary.yaml` runs offline, so it can run in CI. It does not yet model GraphQL, MCP or WebSocket rules.

### 2.4. Runtimes and host requirements

| Driver | Status | How the boundary is built |
|---|---|---|
| Docker | Supported | The workload container has `network_mode: none`. A separate supervisor container uses `network_mode: host` with all capabilities dropped and a read-only root filesystem. They talk over an authenticated Unix socket on a driver-owned volume (`crates/openshell-driver-docker/src/lib.rs`). |
| Podman | Supported (rootless, cgroups v2) | Same shape as Docker. |
| Kubernetes | Supported (Helm) | Supervisor pod plus a NetworkPolicy; needs a CNI that enforces it. |
| VM (libkrun) | Experimental | Guest without a network device, vsock to the supervisor. GPU through VFIO passthrough. |
| MXC (Windows) | Coming soon | Not applicable to Atlas. |

Hard requirements (`docs/about/support-matrix.mdx`): Linux with Landlock ABI 3 ("introduced in Linux 6.2, with Landlock enabled") and nested seccomp user notification, both probed at runtime and fail-closed; Docker 28.0+; glibc 2.28+ for the gateway binary. An Ubuntu 22.04 GA kernel (5.15) does not qualify. macOS on Apple Silicon works through Docker Desktop, but "Docker Desktop must have host networking enabled, and it cannot use Enhanced Container Isolation" (`docs/how-it-works/sandboxes/runtimes.mdx`). GPUs use CDI, so the NVIDIA Container Toolkit's CDI setup must exist before the gateway starts.

Footprint: local use needs no Kubernetes; 0.0.x releases embedded k3s and 0.1.x does not. Compressed `0.1.3` amd64 images are about 53 MB for the gateway, 20 MB for the supervisor and 4.5 MB for the sandbox runtime, measured from the GHCR manifests during this evaluation. Each sandbox is two containers and two volumes. The default workload image is `nvcr.io/nvidia/base/ubuntu:24.04` from NGC. No CPU or memory figures are published.

### 2.5. APIs, SDKs and extension points

- **Control plane:** gRPC only (`proto/openshell.proto`, 83 RPCs). The only plain HTTP routes are health, metrics, auth and a WebSocket tunnel. Package installs listen on `https://127.0.0.1:17670` with mTLS.
- **SDKs:**
  - Python (`uv add openshell`): create, exec and `exec_python`, but no curated policy-approval API.
  - TypeScript.
  - Go: the most complete, including draft approval.
  - Rust.
- **Authentication:** mTLS client certificates, OIDC (Keycloak is the reference identity provider), edge JWT behind Cloudflare Access, or unauthenticated on loopback for development. Workspaces separate sandboxes, providers and policies between teams.
- **Extension points,** all gRPC services:
  - supervisor middleware;
  - gateway interceptors, which can authenticate, patch, validate and observe calls such as `CreateSandbox` and rule approval;
  - compute drivers;
  - credential drivers: database, Kubernetes Secrets and Vault.

  There are no HTTP webhooks: RFC 0010 considered and rejected them.
- **No MCP server** for OpenShell itself. Agents learn the CLI through four public skills under `skills/`.

### 2.6. Observability

- **Logs:** OCSF v1.8.0 events for every network decision, process event and policy change, as shorthand lines or OCSF JSON. The gateway can write a JSONL file; supervisors write OCSF JSON to stderr. Older schema versions (1.1 and 1.3) are available for Splunk and Security Lake. Loki is not mentioned upstream.
- **Traces:** OTLP over gRPC, traces only. Supervisors export to the same endpoint as the gateway, so that endpoint must be reachable from the host network.
- **Metrics:** Prometheus `/metrics` on a separate listener. It is unauthenticated and off by default. Series include `openshell_server_grpc_requests_total`, `openshell_server_supervisor_sessions` and `openshell_ocsf_log_*`.
- **Telemetry:** on by default, sent to `events.telemetry.data.nvidia.com` (`crates/openshell-core/src/telemetry.rs`). `OPENSHELL_TELEMETRY_ENABLED=false` on the gateway turns it off and propagates to supervisors.

### 2.7. Maturity

- **Cadence:** about 11 commits a day; 115 `v0.0.x` tags, then four stable `v0.1.x` releases in two weeks plus 22 pre-release tags. RFC 0014 sets weekly stable releases with support for the current and previous minor line, and declares protobuf `v1` Stable.
- **Breaking history:** 0.0.x to 0.1.0 is not an in-place upgrade. Every sandbox must be recreated, provider profiles re-imported and `gateway.toml` migrated, and `inference.local` plus the `openshell inference` commands were removed. Gateway, supervisor, drivers, CLI and SDK must all run the same release.
- **Experimental surfaces:** the VM driver, WSL2, MXC, supervisor middleware and managed provider files.
- **Upstream docs drift:** some pages and examples still describe the pre-0.1 network model.
  - `docs/how-it-works/gateways/container-deployment.mdx` requires a same-path bind mount that the code no longer uses.
  - `examples/transparent-tcp-redis` and `examples/private-ip-routing` describe a bridge network that the Docker driver no longer creates.
  - The hand-written man page omits several commands.
- **Ecosystem lag:** NemoClaw, NVIDIA's own consumer, still pins `0.0.116`.
- **Community signal:** third-party coverage of 0.1.x reported about 537 open issues and noted that Experimental interfaces can change in a patch release. This was not independently verified.

## 3. The gap in Atlas

### 3.1. No sandbox anywhere

A repo-wide search finds no Landlock, seccomp, AppArmor, gVisor or sandbox-runner configuration. Several manifests already state the gap in their `capabilities:` contracts:

| Service | Capability and note | Status |
|---|---|---|
| `openclaw` | Untrusted agent command isolation: "Atlas configures no mandatory sandbox runner or per-channel authorization policy." | `not-supported` |
| `hermes` | Untrusted autonomous tool isolation: "no per-user sandbox or policy engine for untrusted agent execution." | `not-supported` |
| `trueforge` | Git-backed skills and code sandbox execution: "no sandbox provider (upstream supports Daytona only)." | `not-supported` |
| `airflow` | Untrusted DAG code isolation: DAGs "execute unsandboxed with Airflow Connections holding MinIO root, LiteLLM master, Neo4j administrator" credentials. | `not-supported` |
| `zeppelin` | Interpreter process isolation and HA: no per-user sandboxing. | `not-supported` |
| `backend` | Plugins: Atlas "installs and imports operator-supplied Python code at Backend startup without sandboxing". | `partial` |

Container hardening (`cap_drop: ALL`, `no-new-privileges`, `read_only`, `pids_limit`) exists only on `crawl4ai`, `tika`, the docling adapter and cAdvisor. No agent service has it, and every container shares one flat bridge network.

### 3.2. Credential sprawl

Most LLM consumers receive the master key: airflow, backend, celery, hermes, jupyterhub, lightrag, local-deep-researcher, n8n, open-webui, openclaw, verba and weaviate. Only TrueForge mints a scoped LiteLLM virtual key (`services/trueforge/init/scripts/init.mjs`). Hermes and OpenClaw can also receive cloud-provider keys directly, bypassing LiteLLM. The LiteLLM manifest records "Per-service gateway credentials and budgets" as `not-supported`.

Under OpenShell, a sandboxed agent would never hold the master key. It would hold a placeholder, and the supervisor would attach a scoped virtual key only to requests bound for LiteLLM.

### 3.3. An existing roadmap slot

`docs/ROADMAP.md` Section 2.4.1 keeps "E2B (self-hosted) — Firecracker sandbox for untrusted code" as the remaining cross-cutting Tier 2 item. Its named consumers are Hermes, Open WebUI, Backend, Hummingbot, Windmill and n8n. The same section rejects Daytona because "kernel-sharing is *not* sufficient for untrusted code". OpenShell competes for that slot and partly fills it (Section 4.3).

## 4. Pros and cons

### 4.1. Advantages

1. **Closes the documented isolation gap** for agent harnesses without rewriting them: an existing agent image runs unchanged inside a sandbox.
2. **Credential isolation by construction.** Agents never hold real keys, and a credential works only at the endpoints its provider is bound to. This is the first mechanism that could take `LITELLM_MASTER_KEY`, cloud keys, GitHub tokens and messaging-bot tokens out of agent processes.
3. **Deny-by-default egress with L7 precision,** including allow and deny rules per MCP tool. That suits Atlas's `mcp-servers` service and the curated MCP package.
4. **Policy that grows with human review.** Blocked requests become reviewable drafts, agents can propose rules, and the prover blocks risky auto-approvals. Atlas gets an approval workflow without building one.
5. **Formal policy checks in CI.** `openshell-prover check --boundary` could pin a boundary for every Atlas-shipped policy preset as a test.
6. **Security-grade audit trail.** OCSF events per decision, OTLP traces and Prometheus metrics can feed Atlas's existing Loki, Tempo, Prometheus and Grafana.
7. **Driver-agnostic.** One policy model covers Docker, Podman, Kubernetes and microVM. That matches the ROADMAP's long-term Kubernetes and multi-tenant direction, and the VM driver is a path to the microVM bar.
8. **Light and permissively licensed.** Apache-2.0, about 80 MB of images, SQLite, no Kubernetes for local use, and GPU support through CDI.
9. **Aligned with the agent ecosystem.** Upstream ships example profiles for Claude Code, Codex, Copilot and Cursor, plus tutorials for OpenCode and Pi. NemoClaw shows that OpenClaw and Hermes, both already Atlas services, run inside it.
10. **Programmable.** Python, TypeScript, Go and Rust SDKs let Backend, JupyterHub notebooks or a new MCP server create and drive sandboxes.

### 4.2. Disadvantages and risks

1. **A containerized gateway needs the Docker socket.** The reference compose file mounts `/var/run/docker.sock` and runs as UID 0 (`deploy/docker/docker-compose.yml`). Atlas treats that as root-equivalent and has refused it so far (Section 4.3).
2. **The network model does not use Compose DNS.** Workloads have no network, and supervisors use host networking. A sandbox cannot reach `litellm:4000`; it must use `host.openshell.internal:<published port>`, which the Docker driver pins to host loopback. Policies must therefore follow `BASE_PORT`, and services Atlas never publishes (Loki, Tempo, the OTel collector) are unreachable from sandboxes.
3. **Narrower host support than Atlas.** Atlas runs on Linux, macOS and WSL2. OpenShell needs Linux 6.2+ with Landlock, Docker 28+, and Docker Desktop host networking on macOS, and it treats WSL2 as experimental.
4. **Kernel-sharing isolation.** On the Docker and Podman drivers a sandbox is a hardened container, but still a container. The microVM driver is experimental.
5. **Fast, breaking cadence.** Weekly releases, lockstep versions across gateway, supervisor, CLI and SDK, and a 0.0-to-0.1 migration that was not in-place. Every Atlas pin bump needs a plan for recreating sandboxes.
6. **Lifecycle outside Compose.** The gateway creates sandboxes as sibling containers. `./stop.sh` and `./stop.sh --cold` would neither stop nor remove them, the service table would not show them, and Kong would not route to them.
7. **gRPC-only control plane, mTLS by default.** There is no REST surface for n8n or simple HTTP clients, and Atlas has no precedent for routing gRPC through Kong.
8. **Telemetry on by default.** This conflicts with Atlas's self-hosted privacy posture unless Atlas always sets `OPENSHELL_TELEMETRY_ENABLED=false`.
9. **Upstream docs drift.** Several examples describe a removed network model, so behavior must be verified rather than read off a page.
10. **New operator burden.** Policies, providers and approvals are a new surface to operate. Without good defaults, users will either approve everything or abandon the agent.

### 4.3. Fit against existing Atlas decisions

| Atlas decision | Evidence | Effect on OpenShell |
|---|---|---|
| No Docker socket mounts | Tests assert there is none (`bootstrapper/tests/test_jenkins_service.py`); cAdvisor points at a deliberately absent socket; `docs/ROADMAP.md` Section 2.3.2 rejects Docker MCP Gateway because "a compromise of the Gateway grants effective root-equivalent control of the host's Docker daemon". | A `container` source needs a documented risk decision or the rootless Podman driver. A host-installed gateway also drives Docker, but as an operator choice outside Atlas, like the `blender-mcp` host sources. |
| MicroVM bar for untrusted code | ROADMAP Section 2.4.1: "kernel-sharing is *not* sufficient for untrusted code". | The Docker and Podman drivers do not meet this bar. Position OpenShell as agent egress and credential governance, and revisit the microVM claim when the VM driver leaves Experimental. Hostile-code execution, such as LLM-generated trading strategies, still points to a microVM. |
| Loopback binds by default | `HOST_BIND_IP` defaults to `127.0.0.1:` in every profile. | Helps: host-networked supervisors can reach loopback-published Atlas ports through `host.openshell.internal`. Unverified on Docker Desktop. |
| `.env` is the bootstrap authority | `docs/strategy/infisical-secrets-manager-evaluation.md` | OpenShell's provider store becomes a second secret store. Atlas should keep generating scoped keys in `.env` and push them into OpenShell providers, never the reverse. |
| Self-hosted, single-tenant posture | `SECURITY.md` Section 3 | Disable telemetry and keep the gateway on loopback. Workspaces and OIDC stay optional. |
| Vault-compatible secrets on the watchlist | `docs/research/candidates/openbao.md` (`deferred`) | OpenShell's `vault` credential driver would be a concrete consumer, which strengthens the OpenBao case later. |

## 5. Deployment shape inside Atlas

### 5.1. The network reality

```text
Atlas compose project                        Host network namespace
(backend-network)
---------------------------                  ------------------------------------------
litellm:4000      -- published -->  127.0.0.1:63040  <--+
mcp-servers:8000  -- published -->  127.0.0.1:63078  <--+-- supervisor container
searxng:8080      -- published -->  127.0.0.1:63056  <--+   (network_mode: host)
otel-collector, loki, tempo: not published                   policy check + credential
                                                              injection, then dials out
                                                                   |
                                                       authenticated Unix socket
                                                                   |
                                                         workload container
                                                         (network_mode: none)
                                                         agent addresses
                                                         host.openshell.internal:<port>
```

What follows from this layout:

- **Reachability.** A sandbox reaches an Atlas service only through a port Atlas publishes on the host, addressed as `host.openshell.internal:<port>`. With the default `BASE_PORT`, LiteLLM is `http://host.openshell.internal:63040/v1`.
- **Ports must be generated.** Ports derive from `BASE_PORT`, so Atlas must generate OpenShell profiles and policies from `services/topology.py`, the same way it generates Kong routes. A static file would break on `--base-port`.
- **Unpublished services are unreachable.** Services with no host port (Loki, Tempo, the OTel collector, Prometheus) cannot be reached from sandboxes unless Atlas publishes a port. Supervisor trace export needs the collector's OTLP gRPC port published on loopback.
- **Atlas services need a route to the gateway.** Services that call the gateway (Backend, JupyterHub) can use its service name only if it runs in Compose. Otherwise the host gateway must listen on an interface containers can reach, protected by mTLS.

### 5.2. Option A: host-managed gateway (recommended first)

The operator installs OpenShell with the upstream installer, which sets up a systemd user unit on Linux or a Homebrew service on macOS. Atlas adds `services/openshell/` as a `virtual: true` manifest, following `services/blender-mcp` and `services/vllm-metal`. An illustrative sketch, not a schema-complete manifest:

```yaml
# Illustrative only; not shipped.
name: openshell
label: "OpenShell (agent sandbox gateway)"
category: agents          # the infra port band is full
virtual: true
containers: []
sources:
  var: OPENSHELL_SOURCE
  default: disabled
  options:
    - id: disabled
      label: "Disabled"
    - id: localhost
      label: "Host (existing OpenShell gateway on this machine)"
      profiles: [default]
env:
  - name: OPENSHELL_SOURCE
    default: disabled
  - name: OPENSHELL_GATEWAY_ENDPOINT
    default: "https://127.0.0.1:17670"
  - name: OPENSHELL_LITELLM_VIRTUAL_KEY
    default: ""
    secret: true
    description: "Scoped LiteLLM key stored in the OpenShell provider store; sandboxes only ever see a placeholder."
capabilities:
  - name: "Sandboxed agent execution"
    status: partial
    verification: untested
    note: "Linux 6.2+ hosts with Landlock ABI 3 only; Docker Desktop requires host networking."
```

Atlas's role in this option:

1. **Preflight** in `./start.sh doctor`: Landlock ABI, Docker version, Docker Desktop host networking, gateway reachability and the telemetry setting. The wizard shows the source as unavailable, with the reason, on hosts that fail.
2. **Generated assets** built from the topology:
   - an `atlas-litellm` provider profile pointing at `host.openshell.internal:${LITELLM_PORT}` (Section 6.1);
   - a scoped LiteLLM virtual key, following the `trueforge-init` pattern;
   - policy presets for Atlas services such as SearXNG, the MCP servers and Weaviate, each checked in tests with `openshell-prover check --boundary`.
3. **An idempotent apply step** that calls the OpenShell CLI or Python SDK (`profile import`, `provider create` or `provider update`) on start.
4. **Observability wiring** where it is reachable: a Prometheus scrape job, which needs the gateway's metrics listener reachable from containers; a Grafana dashboard; and a published loopback OTLP port for traces.

A `managed-localhost` source could later start and stop the gateway binary through `bootstrapper/services/managed_host.py`. Because the upstream installer already registers a service manager, the plain `localhost` source comes first.

### 5.3. Option B: gateway in Compose (conditional)

Base it on `deploy/docker/docker-compose.yml`:
- a gateway container, plus a `generate-certs` init one-shot;
- `command: []`, so `gateway.toml` wins over the image's default CLI flags;
- the Docker socket mount and a persistent state volume;
- one loopback port whose published and internal numbers match, because the host-networked supervisor dials `127.0.0.1:<bind port>`.

**Gains:** Atlas controls the version pin, Backend and JupyterHub reach the gateway by service name, Prometheus scrapes it on `backend-network`, and gateway state can live in Supabase Postgres, where the `backup` service covers it.

**Costs:** the socket mount, UID 0, and a gateway that can create arbitrary containers on the host.

Evaluate two variants before accepting the socket:

- **Rootless Podman driver.** On hosts running rootless Podman, the gateway does not get root-equivalent access. Atlas already detects Podman (`bootstrapper/utils/system.py::detect_container_runtime`). Its supervisor also uses host networking.
- **A Docker socket proxy** that allows only the API calls the driver needs. This narrows the risk without removing it, and whether the driver works through such a proxy is untested.

### 5.4. Option C: NemoClaw (not recommended)

NemoClaw is a host installer with its own state (`~/.nemoclaw`), its own agent images, managed vLLM, and a 7,900-line install script. It pins OpenShell `0.0.116` and routes inference through the removed `inference.local`, so adopting it now means inheriting a migration NVIDIA has not done yet.

Its policy data is reusable. The presets in `NemoClaw:nemoclaw-blueprint/policies/presets/`, the restricted, balanced, open and personal tiers, and the per-channel messaging policies for OpenClaw and Hermes are Apache-2.0 and close to what Atlas would need (Section 6.2).

### 5.5. Lifecycle mismatches to solve

- **Stop and cold stop.** `./stop.sh --cold` removes project volumes but would leave sandbox containers and their `openshell-channel-*` volumes behind. Atlas needs a pre-stop step that deletes Atlas-labelled sandboxes, or a documented ownership boundary.
- **Service table and health.** Sandboxes are not Compose services. The launch screen needs a separate sandboxes view, or one `openshell` row with a sandbox count.
- **Ingress.** The gateway exposes sandbox services as `<sandbox>--<service>.openshell.localhost`, accepting plaintext only from loopback. Routing them through Kong needs TLS plus gateway authentication.
- **Upgrades.** A pin bump must move the gateway, supervisor and sandbox-runtime images and the CLI together, and a minor bump can require recreating sandboxes.

## 6. Integration with existing Atlas services

Effort: S is days, M is one to two weeks, L is longer. Confidence reflects how much of the path upstream documents.

### 6.1. Model access

| Atlas service | Mechanism | Effort | Confidence |
|---|---|---|---|
| LiteLLM | A custom, endpoint-bearing provider profile on `host.openshell.internal:${LITELLM_PORT}`, with a scoped virtual key as the bearer credential, limited to `/v1/**`. The client gets `OPENAI_BASE_URL` and a placeholder key. | S | Medium-high: the Ollama variant is documented; LiteLLM itself is untested |
| Ollama | The documented credential-less `ollama-openai` profile. Prefer going through LiteLLM so usage stays accounted. | S | High |
| vLLM Metal (host) | Same pattern on its host port. | S | Medium |
| Langfuse | No direct integration needed: sandbox traffic still flows through LiteLLM, whose callbacks keep tracing it. | None | High |

Sketch of the LiteLLM profile, modelled on `providers/openai.yaml` and the Ollama example in `docs/how-it-works/inference.mdx`. Untested:

```yaml
id: atlas-litellm
display_name: Atlas LiteLLM
description: Atlas LiteLLM OpenAI-compatible gateway on the host
category: inference
inference_capable: true
credentials:
  - name: api_key
    description: Scoped LiteLLM virtual key, never the master key
    env_vars: [ATLAS_LITELLM_API_KEY]
    required: true
    auth_style: bearer
    header_name: authorization
endpoints:
  - host: host.openshell.internal
    port: 63040            # generated from LITELLM_PORT
    path: /v1/**
    protocol: rest
    access: read-write
    enforcement: enforce
binaries: [/usr/bin/curl, /usr/local/bin/curl, /usr/bin/python3, /sandbox/.venv/**]
```

### 6.2. Agent runtimes

| Atlas service | Mechanism | Effort | Confidence |
|---|---|---|---|
| Coding agents (new capability) | Run Claude Code, Codex or OpenCode in sandboxes against Atlas's LiteLLM. This is OpenShell's best-documented path and the cheapest first consumer. | S | High |
| OpenClaw | A new `openshell` source variant that runs the upstream image as a sandbox: the `atlas-litellm` provider, messaging-bot tokens as providers (NemoClaw's `credential_binding` pattern), and NemoClaw's channel policies. Alternative: OpenClaw's own OpenShell plugin (`mode: remote`, sketched in `rfc/0011-multi-player-design`). | M-L | Low-medium |
| Hermes | Same approach as OpenClaw. NemoClaw's Hermes image and `policy-additions.yaml` are the reference. The dashboard and API become sandbox service exposures. | M-L | Low-medium |
| TrueForge | A sandbox provider for skills and code execution. Upstream supports only Daytona, so this needs an upstream adapter or a Backend-hosted shim. | L | Low |

### 6.3. Code execution surfaces

| Atlas service | Mechanism | Effort | Confidence |
|---|---|---|---|
| Backend | The Python SDK (`SandboxClient`, `exec_python`) behind a "run this code safely" API. Backend could also host interceptor or middleware gRPC services. | M | High |
| JupyterHub | (a) SDK calls from notebooks that run untrusted cells in a sandbox. (b) A spawner that runs the notebook server itself in a sandbox (`examples/jupyter-sandbox`). | a: S, b: L | a: high, b: medium |
| Open WebUI | A code interpreter backed by a sandboxed Jupyter (option b above) instead of in-browser Pyodide. | M | Medium |
| n8n | No REST surface. Call Backend's wrapper from an HTTP node, or the `openshell` CLI. For approval notifications, a gateway interceptor with a `post_commit` binding on proposal submission could post to an n8n webhook, which fans out to Slack, Telegram or email. | M | Medium |
| Airflow, Zeppelin | Not a fit today: their executors and interpreters run in-process. | None | n/a |

### 6.4. Data and tool services

| Atlas service | Mechanism | Effort | Confidence |
|---|---|---|---|
| MCP servers | `protocol: mcp` rules with per-tool allow and deny on `host.openshell.internal:${MCP_SERVERS_PORT}`. For sandboxed agents, this covers the manifest's per-consumer MCP authorization gap. A new MCP server wrapping the OpenShell SDK would let any MCP client create sandboxes. | S for rules, M for the server | Medium-high |
| SearXNG, Crawl4AI | A "research" preset that denies the open internet and allows only Atlas's SearXNG and Crawl4AI, so web access is mediated and logged. | S | Medium |
| Weaviate, Neo4j | REST and GraphQL L7 rules for Weaviate, Bolt as `tcp`, with read-only presets. | S-M | Medium |
| Redis | A `protocol: tcp` rule. Agents cannot propose TCP rules, so an operator must add it. | S | Medium |
| MinIO | An endpointless S3 profile with SigV4 re-signing, or an STS refresh strategy. Agents would get a scoped bucket without holding MinIO keys. | M | Low-medium: documented only for AWS hosts; plain-HTTP signing untested |
| Supabase Postgres | Gateway state in Postgres (`OPENSHELL_DB_URL`), so the `backup` service covers it. Only worthwhile for the `container` source. | S | Medium |

### 6.5. Operations and security

| Atlas service | Mechanism | Effort | Confidence |
|---|---|---|---|
| Prometheus | A scrape job for the gateway's metrics listener. The scrape-target count is pinned by `bootstrapper/tests/test_observability_config.py` and quoted in three docs. | S | High |
| Grafana | A dashboard for denials, approvals, supervisor sessions and gRPC errors, using `openshell_*` metrics and Loki queries over OCSF. | S | High |
| Loki | The gateway's OCSF JSONL file, read by the OTel collector's `filelog` receiver. Supervisor OCSF goes to container stderr, which needs a Docker log shipper that Atlas does not have. | M | Medium |
| OTel collector, Tempo | `[openshell.gateway.otlp] endpoint` (gRPC, traces only). Supervisors export from the host network, so the collector's port 4317 needs a loopback host port. | M | Medium-low |
| Kong | Keep the gateway off Kong at first. gRPC and `*.openshell.localhost` exposures need TLS and gateway authentication. | M | Low-medium |
| Authentik or Keycloak | OIDC for gateway users once the SSO pilot ships (`docs/strategy/authentik-sso-pilot-evaluation.md`). | M | Medium |
| OpenBao (deferred) | The Vault credential driver. | M | Medium |
| LLM guard (new) | Supervisor middleware wrapping a PII or prompt-injection filter in front of LLM endpoints. RFC 0009 names Presidio and NeMo Anonymizer as targets. A research preview upstream. | M | Medium |

## 7. Plan

### 7.1. Phase 0: isolated spike on Linux amd64

A throwaway harness outside the repo, never merged. Pin an exact OpenShell release and set `OPENSHELL_TELEMETRY_ENABLED=false` throughout.

| # | Check | Evidence of a pass |
|---|---|---|
| 1 | Host qualifies: Landlock ABI 3, seccomp user notification, Docker 28+ | Gateway preflight output |
| 2 | A default sandbox starts with telemetry disabled | Sandbox shell; no telemetry egress in gateway logs |
| 3 | A sandbox reaches LiteLLM at `host.openshell.internal:63040` with a placeholder key | Completion returned; OCSF allow event; `env` in the sandbox shows only the placeholder |
| 4 | A request to a disallowed host or path is blocked | OCSF deny event and a drafted rule |
| 5 | An MCP rule on `mcp-servers` allows one tool and denies another | Allowed call succeeds; denied tool returns 403 |
| 6 | Changing `BASE_PORT` and regenerating the profile keeps check 3 passing | Check 3 repeated after regeneration |
| 7 | Atlas Prometheus scrapes gateway metrics, and traces reach Tempo | Scrape target up; trace visible in Grafana |
| 8 | The upstream OpenClaw or Hermes image runs as a sandbox with its UI exposed | Dashboard reachable; agent completes a tool call through LiteLLM |
| 9 | `./stop.sh --cold` interaction | Documented residue and a working cleanup command |
| 10 | Docker Desktop on macOS with host networking on | Checks 3 and 5 repeated; failures recorded |
| 11 | Overhead | Sandbox create time, supervisor memory, and LLM request latency through the supervisor compared with a direct call |

Go: checks 1 to 7 pass on Linux. Failures in check 10 do not block; they define the support matrix.

### 7.2. Phase 1: host-managed `openshell` service

Deliver Option A (Section 5.2):
- the virtual manifest;
- doctor checks;
- generated profiles and presets with prover-boundary tests;
- the LiteLLM virtual key;
- the Prometheus job and Grafana dashboard.

Add the service to the `gen-ai-eng` track, the track that already holds Hermes, OpenClaw and TrueForge. Touch points, derived from the last agent service added (TrueForge):
- the manifest;
- `source_override_manager.py` source mapping, plus a `--openshell-source` flag in `start.py`;
- `service_config.py`;
- `key_generator.py`;
- `tracks.yml`;
- the regenerated `.env.example`;
- the service README via `bootstrapper.docs.regen`;
- `docs/manifest.yaml`, `docs/services.md`, `docs/tracks.md` and the operations reference pages;
- a row in `docs/reference/license-inventory.yaml`;
- `test_wizard_app_discovery.py`, `test_env_forwarding_contract.py` and `test_manifests.py`.

Confirm whether a new `agents` service shifts existing agent ports in the `services/topology.py` slot allocator.

### 7.3. Phase 2: first consumers

- Sandboxed coding agents (Claude Code, Codex, OpenCode) on Atlas's LiteLLM, documented as a recipe.
- A Backend "safe exec" endpoint on the Python SDK.
- MCP tool-level rules for `mcp-servers`.
- Approval notifications through an interceptor and an n8n webhook.

### 7.4. Phase 3: sandboxed OpenClaw and Hermes

An `openshell` source variant for `openclaw` and `hermes` that runs the upstream image as a sandbox. It adapts NemoClaw's channel and tier policy data, puts messaging-bot tokens behind providers, and adds a posture step to the wizard (restricted, balanced, open). Once it is verified, update both manifests' isolation capability from `not-supported`.

### 7.5. Phase 4: containerized gateway and beyond

Decide on Option B: a documented Docker-socket risk decision, rootless Podman, or not at all. Revisit the ROADMAP's microVM slot when the VM driver leaves Experimental. Consider the Kubernetes driver only when Atlas takes on Kubernetes.

### 7.6. Quick win that needs no OpenShell

Give each agent and code-execution service its own scoped LiteLLM virtual key with a model allowlist, following `services/trueforge/init/scripts/init.mjs`. Start with openclaw, hermes, n8n and jupyterhub. This shrinks the blast radius today, and it becomes the credential OpenShell injects later.

## 8. What Atlas can learn from the OpenShell repository

### 8.1. How OpenShell is organized

- **Code:** a Rust workspace of 38 crates split by layer (CLI, server, sandbox, supervisor and its network/process halves, policy, prover, drivers, OCSF, OTel, SDK, TUI).
- **API contract:** `proto/` is the contract, guarded by Buf lint and breaking-change checks.
- **Packaging and docs:** `deploy/` holds Docker, Helm, deb, rpm, man pages and SBOM tooling. `docs/` is Fern MDX with a navigation file, and `rfc/` holds numbered design proposals with lifecycle states.
- **Skills:** `skills/` holds public, installable agent skills; `.agents/skills/` holds contributor skills, and `.claude/skills` is a symlink to it.
- **Tasks:** `mise.toml` plus `tasks/*.toml` define every command contributors and CI run.
- **Operator manuals:** `CI.md` and `TESTING.md` are manuals for the CI gates and test tiers.
- **Tests:** end-to-end suites per runtime live under `e2e/`, and a driver-agnostic conformance scenario list lives in `crates/openshell-conformance`.

### 8.2. Repository and process practices

Items marked with a dagger (†) add a quality tool, which `AGENTS.md` says needs an explicit request.

| # | Practice | OpenShell evidence | Atlas target | Effort |
|---|---|---|---|---|
| 1 | **Keep required agent skills in the repo, in one tree every harness reads.** `AGENTS.md` tells agents to use `three-surface-docs`, `three-surface-docs-audit` and `architecture-diagram`, but the repo contains only `.agents/skills/audit/SKILL.md`, and there is no `.claude/skills` link or `CLAUDE.md`. If those skills live in a personal skills directory, cloud sessions and other contributors cannot load them. | `.claude/skills -> ../.agents/skills/`; `CLAUDE.md` is `@AGENTS.md` | `.agents/skills/`, a symlink, and a test that every skill `AGENTS.md` names exists | S-M |
| 2 | **Public operator skills** that work without a checkout, such as `atlas-cli` and `debug-atlas-stack`, each with a symptom table and a reporting checklist | `skills/openshell-cli`, `skills/debug-openshell-cluster` | `skills/` at the root, through the docs pipeline | M |
| 3 | **One command surface that CI also calls.** Today `Makefile` covers docs only, and the 20-plus audit commands are spelled out by hand in both `AGENTS.md` and `services-lint.yml`. | `mise.toml` with namespaced `tasks/*.toml` and aggregate `ci`, `lint`, `test` tasks | Namespaced `make` targets (`lint`, `test-fast`, `audit`, `ci`) and a self-listing `make help`; not mise | M |
| 4 | **Compatibility check against the latest tag,** the Atlas equivalent of Buf's breaking-change check: `.env.example` names, `*_SOURCE` value sets, endpoint-export fields and CLI flags | `buf.yaml` `FILE` breaking rules; release qualification | `scripts/check_reuse_contract.py`, enforcing `docs/operations/releasing.md` | M |
| 5 | **A lifecycle for design docs:** front matter with `state: draft, review, accepted, rejected, implemented, superseded`, plus a "which artifact when" table | `rfc/README.md`, `rfc/0000-template/` | `docs/superpowers/specs/` front matter and a `docs/superpowers/README.md`, validated like `docs/research/` | S-M |
| 6 | **Issue forms and a PR template.** None exist in `.github/` today. | `.github/ISSUE_TEMPLATE/*.yml` with required fields; `blank_issues_enabled: false` | Forms asking for track, `*_SOURCE` values and `./start.sh doctor --bundle` output | S |
| 7 | **An operator manual for CI, stable required-check names, and an observation mode for new gates** | `CI.md`; `required-ci-gates.yml` | `docs/operations/ci.md` and one aggregate gate context | S |
| 8 | **Named conformance scenarios run against a live stack,** reused as a release canary. No Atlas workflow brings up a full stack today. | `crates/openshell-conformance`; `release-canary.yml` | A label-gated `./start.sh --no-tui` smoke lane with a named scenario list | M-L |
| 9 | **SBOMs** to close license-inventory items R-PY and R-BASE † | `tasks/sbom.toml`, `deploy/sbom/resolve_licenses.py` | `scripts/supply_chain/`, advisory first | M |
| 10 | **Workflow security lint,** informational first † | `workflow-security.yml` (actionlint, zizmor) | `.github/workflows/` | S |
| 11 | **A short `AGENTS.md`:** move fast-changing detail into skills and docs rather than deleting it | "It is injected into your context on every interaction" (`AGENTS.md`) | Move the Pass-1 debt accounting, migration chain and regen mechanics out | M |
| 12 | **Mark generated files** so GitHub collapses them in diffs | `.gitattributes` `linguist-generated` | `.env.example`, `docs/research/integration-matrix.md`, `services/*/architecture.{svg,html}` | S |

### 8.3. The shell: CLI

How OpenShell's shell works for a user:
- **Getting into a sandbox.** `openshell sandbox create -- claude` provisions a sandbox, shows a spinner checklist driven by gateway events, and drops the user into the agent. `sandbox connect` attaches over SSH through a `ProxyCommand` that tunnels via the gateway; there is no direct network path to the sandbox.
- **Working with a sandbox.** `exec`, `forward`, `upload` and `download` (which honor `.gitignore`) and `--editor vscode` cover the rest. Commands fall back to the last-used sandbox and print which one they picked.
- **Approvals.** A blocked request shows up as a pending rule that `openshell rule approve` resolves.
- **Consistency.** Every read command accepts `-o table|yaml|json`. Help is grouped with examples. Completions are dynamic and query live sandbox names. One color policy (`--color`, `NO_COLOR`, `FORCE_COLOR`, `TERM=dumb`) is applied to every output library and tested on pipes. Prompts appear only on a TTY; anywhere else the error names the exact command to run.

Ideas for `./start.sh`, ranked:

| # | Idea | Atlas target | Effort |
|---|---|---|---|
| 1 | `./start.sh status [-o json]` and `./start.sh attach`, which reopens the launch screen in observe mode. Today a user who quits the TUI is pointed at `docker compose logs -f`. | `bootstrapper/ui/textual/integration.py` (`run_attach_flow`), `start.py` | M-L |
| 2 | Remediation-first doctor: a structured `fix` command in each result, rendered as "fix: ..." and carried into JSON and the support bundle | `_doctor_result` in `bootstrapper/start.py`, `core/support_bundle.py` | S-M |
| 3 | A uniform `-o/--output` flag taking `table` or `json`, keeping `--json` and `--format` as aliases | `start.py` root, `doctor`, `endpoints export` | S-M |
| 4 | Grouped root help with examples, with `--*-source` flags grouped by manifest category, plus a help-contract test | `AtlasStartGroup` in `start.py` | S-M |
| 5 | A CLI reference generated from the click tree, under the existing drift gate. OpenShell's hand-written man page has drifted. | `docs/reference/cli.md`, `bootstrapper.docs.regen` | S-M |
| 6 | Shell completions, with dynamic `--track` values from `tracks.yml` | `./start.sh completions <shell>` for bash, zsh and fish | S-M |
| 7 | Confirmation for `./stop.sh --cold` on a TTY, skipped with `--yes`; the TUI already needs two presses | `bootstrapper/stop.py` | S |
| 8 | `./start.sh logs [service...] --level --follow`, after moving `_classify_compose_line` out of the view | `bootstrapper/core/`, `wizard_screen.py` | S-M |
| 9 | One color policy, tested on pipes | Rich consoles and click in the bootstrapper | S-M |
| 10 | Three progress modes for `--no-tui`: an interactive checklist, timestamped CI lines, and silence under JSON | `core/linear_startup.py`, `core/launch_outcome.py` | S-M |

### 8.4. The TUI (`openshell term`)

`crates/openshell-tui` is a Ratatui dashboard:
- **Structure:** one `App` state struct of about 4,500 lines, with pure `draw` functions per view.
- **Event loop:** key handlers set `pending_*` intent flags, and the event loop turns them into async gRPC calls with 5-second timeouts. A 2-second tick refreshes lists. Results are tagged with the gateway and workspace that requested them, and stale ones are dropped.
- **Logs:** a k9s-style follow/pause log viewer.
- **Approvals:** a "Network Rules" panel with a pending badge and `a`, `x` and `A` keys to approve, reject or approve all.
- **Shell:** it opens a shell by pausing input, leaving the alternate screen, running `ssh`, then restoring and redrawing.

Lessons for Atlas's Textual app and the planned MVVM layers:

1. **Tag async results with their request context and drop stale ones.** Write this down as a ViewModel rule for Pass 2 of the MVVM work, covering track and base-port changes that land while workers are still running.
2. **Add service-row actions in the launch screen:** `o` opens the service URL from the endpoints contract, and `x` opens a shell in the container. Use Textual's `App.suspend()` around `docker compose exec`, the same pause-run-restore pattern.
3. **Add k9s-style follow and pause** to the log pane.
4. **Do not copy** the monolithic `App`, boolean intent flags, or artificial minimum display delays (`MIN_CREATING_DISPLAY`). Atlas's terminal-capability gate (`bootstrapper/ui/term_caps.py`) and its interactive Pilot tests are already ahead of OpenShell's render-only tests and missing TTY check.

### 8.5. Onboarding and the wizard

OpenShell has no wizard. Onboarding is a non-interactive installer followed by one command. It is idempotent: an existing gateway registration is replaced. Its safety comes from a refuse-unless-acknowledged guard for breaking upgrades, and a verification loop that prints the last 80 journal lines and the exact fix when the gateway does not come up.

NemoClaw has a full wizard:
- **Structure:** eight numbered steps over a 13-state machine.
- **Express mode:** a one-question `[Y/n]` preset chosen from the detected platform.
- **Review and abort:** a review screen with edit actions, and an honest abort message that says what was and was not stored.
- **Resume:** a secret-free checkpoint with `--resume`.
- **Non-interactive mode:** one environment variable per prompt, with every defaulted answer echoed.
- **Event stream:** `--events=jsonl`, a versioned event stream for CI and coding agents.
- **Provider validation:** real tool-calling and streaming requests.
- **Policy choice:** a tier picker plus presets.
- **Secrets:** masked prompts with format patterns.
- **Progress and errors:** a heartbeat during long phases, and port-conflict reports that name the owning process and the exact override.
- **Coding-agent starter prompt:** "Ask exactly one question at a time" and never ask for keys in chat.

Ideas for Atlas's wizard, ranked by value for effort:

| # | Idea | Atlas target | Effort |
|---|---|---|---|
| 1 | Echo every defaulted answer in `--no-tui` with its source (flag, environment or default) instead of one generic "using default" line | `bootstrapper/core/linear_startup.py`, `start.py` | S |
| 2 | Before choosing Hermes or OpenClaw default models, check candidates for tool calling and streaming with a real request; today `init-hermes.sh` picks by name | `bootstrapper/wizard/model/llm_rules.py`, `services/hermes/init/scripts/init-hermes.sh` | M |
| 3 | A one-question express preset from detected hardware (NVIDIA GPU, Apple Silicon, CPU only) offering a recommended track and sources | `bootstrapper/wizard/model/state_builder.py`, near the track prompt | M |
| 4 | A secret-free wizard checkpoint with `--resume` after an interrupted run | `bootstrapper/wizard/model/state.py`, `run_setup_flow` | M |
| 5 | Format checks and "where to find this key" help for secret prompts (`sk-ant-`, `sk-or-`, `nvapi-`) | `bootstrapper/wizard/model/cloud_rules.py`, `widgets/prompt_panel.py` | S |
| 6 | An abort message stating exactly what was and was not written (`.env`, volumes) | `ui/textual/screens/wizard_screen.py` | S |
| 7 | A heartbeat ("still working on X, N s") during long pulls, paused while a prompt is open | `bootstrapper/core/docker_manager.py` | S |
| 8 | Port-conflict reports that name the owning process and the exact `--base-port` or `*_PORT` override | `bootstrapper/core/port_manager.py` | S |
| 9 | Offer to use a key already exported in the user's shell when `.env` lacks it, similar to OpenShell's `--from-existing` | `bootstrapper/wizard/model/cloud_rules.py`, step builder | S-M |
| 10 | `--events=jsonl` start events on stdout, with human output on stderr | `start.py`, `core/launch_outcome.py` | M |
| 11 | A coding-agent starter prompt doc that maps every choice to a `./start.sh` flag | docs tree, linked from `AGENTS.md` | S |
| 12 | One structured readiness report shared by `doctor` and wizard gating, for example GPU or CDI health deciding `*-container-gpu` | `start.py` doctor, `core/support_bundle.py` | L |
| 13 | An agent posture step (restricted, balanced, open) when `openshell` is enabled | depends on Phase 3 | L |

### 8.6. Where Atlas is already ahead

- **Wizard depth.** Track picker, per-service sources with support tiers, a consequence review, a decisions page, and a live command summary that teaches the equivalent CLI. OpenShell's TUI forms show no CLI equivalent.
- **Diagnostics.** 25 doctor checks with JSON output, and an offline, allowlisted, redacted support bundle with a preview before writing. OpenShell's `doctor` is hidden and only checks Docker.
- **Output discipline.** Exactly one JSON document on stdout, even when argument parsing fails.
- **Drift gates as tests.** `regen --check`, byte-equivalence of `.env.example`, and the docs content lint. OpenShell relies on skills and a style guide, and it shows drift: stale examples, a stale man page, and diverged reviewer personas.
- **Scanner exceptions and licensing.** Exceptions need an owner and an expiry, and the license inventory covers models as well as images.
- **Do not adopt:**
  - mise or Nix: Atlas is uv plus Docker;
  - OS packaging and a `curl | sh` installer;
  - phone-home telemetry;
  - multi-organization governance: vouching, DCO bots, maintainer approval gates and merge queues;
  - OpenShell's rule against mentioning AI agents in commits.

## 9. Open questions

- Do host-networked supervisors reach `127.0.0.1`-published Atlas ports on Docker Desktop for macOS, and on Podman?
- Does static placeholder substitution work over plain `http://` to LiteLLM? The content-guard example implies yes.
- Can middleware and interceptors be reached when the gateway runs in a bridge network and the supervisors run on the host network?
- Can Kong carry the gateway's gRPC API, and is that worth it compared with keeping the gateway on loopback?
- Does adding `openshell` to the `agents` port band shift existing agent ports?
- What are the real CPU and memory costs per sandbox?
- What are the license terms of the NGC default workload image?
- Does the OpenShell plugin for OpenClaw, sketched in RFC 0011, exist in the OpenClaw release Atlas pins (`2026.6.10`)? NemoClaw targets `2026.9.2`.
- What are the upstream defaults for n8n task runners and the Hermes terminal backend, which Atlas does not set?

## 10. Sources

Upstream repositories at the revisions listed in the header:

- OpenShell: `README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `CI.md`, `TESTING.md`, `docs/about/architecture.mdx`, `docs/about/support-matrix.mdx`, `docs/how-it-works/**`, `docs/extensibility/**`, `docs/observability/**`, `docs/upgrade/0-1-0.mdx`, `deploy/docker/docker-compose.yml`, `deploy/docker/gateway.toml`, `crates/openshell-driver-docker/`, `crates/openshell-supervisor-network/`, `crates/openshell-prover/`, `crates/openshell-cli/`, `crates/openshell-tui/`, `install.sh`, `providers/`, `examples/`, `rfc/`, `skills/`, `.agents/skills/`, `mise.toml`, `tasks/`, `.github/`.
- NemoClaw: `README.md`, `nemoclaw-blueprint/`, `agents/`, `src/lib/onboard/`, `scripts/install.sh`, `docs/`.

External coverage, consulted through search results only:

- [NVIDIA OpenShell documentation](https://docs.nvidia.com/openshell/latest/index.html)
- [NVIDIA NemoClaw overview](https://docs.nvidia.com/nemoclaw/0.0.43/about/overview.html)
- [NVIDIA technical blog: Run autonomous, self-evolving agents more safely with NVIDIA OpenShell](https://developer.nvidia.com/blog/run-autonomous-self-evolving-agents-more-safely-with-nvidia-openshell)
- [Developers Digest: NVIDIA OpenShell makes agent sandboxes a policy layer](https://www.developersdigest.tech/blog/nvidia-openshell-agent-sandbox)
- [andrew.ooo: OpenShell review](https://andrew.ooo/posts/openshell-review-nvidia-kernel-enforced-agent-sandbox/)
- [The Next Web: Nvidia turns OpenClaw into an enterprise platform with NemoClaw](https://thenextweb.com/news/nvidia-nemoclaw-openclaw-enterprise-security)
