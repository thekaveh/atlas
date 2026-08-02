# 9.2. Contributing — adding or modifying a service

The stack uses a **per-service folder layout** under `services/<name>/`. Each service family (one or more co-lifecycled containers) owns:

- `service.yml` — manifest: env vars, source variants, image refs, dependencies, plus per-source bootstrapper runtime data under `runtime_sc:` / `runtime_adaptive:` / `runtime_deps:`
- `compose.yml` — Docker Compose fragment for that family's containers
- optional subdirectories holding Dockerfiles, init scripts, source code, configuration files, snapshots, etc. — see the **Subdirectory naming convention** below

The thin top-level `docker-compose.yml` merges fragments via Compose's native `include:` directive (requires Compose ≥ v2.20.3; v2.26+ recommended).

<a id="1-tldr--the-60-second-checklist"></a>

## 1. TL;DR — the 60-second checklist

A maintainer who already understands the stack can land a new service in under an hour by following this list. Each step links to the relevant deep-dive section.

- [ ] **Study the candidate service's upstream docs** (license, default port, API shape, runtime deps) → [Pre-flight](#4-pre-flight--study-the-candidate-service)
- [ ] **Pick a folder flavor** → [Decision 1](#5-decision-1--folder-flavor-container-virtual-or-doc-only)
- [ ] **Pick a category** → [Decision 2](#6-decision-2--category)
- [ ] **Pick your sources** → [Decision 3](#7-decision-3--source-variants)
- [ ] **Write `services/<name>/service.yml`** → [Mechanics](#11-mechanics--putting-it-all-together)
- [ ] **Write `services/<name>/compose.yml`** (only if folder flavor = container) → [Mechanics](#11-mechanics--putting-it-all-together)
- [ ] **Add the `include:` line to `docker-compose.yml`** (only if you wrote a compose fragment)
- [ ] **Register CLI key in `source_mapping`** → [Mechanics — source_override_manager registration](#114-bootstrapperutilssource_override_managerpy--register-the-cli-key). Without this the wizard silently skips your service.
- [ ] **Add the new folder to the relevant track(s) in `bootstrapper/tracks.yml`** (source-configurable services only). A configurable service absent from a named track's `services:` list is force-disabled (`*_SOURCE=disabled`) there — it only runs under `--track all`. Always-on infra and the always-prompted LLM/Prometheus/Grafana tier are exempt.
- [ ] **Run the root-safe regen, lint, and required-check checklist** → [After you save the files](#12-after-you-save-the-files--regen--lint-commands-in-order), then [CI gates](#134-ci-gates-that-run-on-every-push)
- [ ] **Update audit-script allowlists** if your service has hard deps → [Audit-script + CI implications](#13-audit-script--ci-implications)
- [ ] **Commit and push.** CI gates the change with four required jobs: manifest-lint+pytest, compose-equivalence+permutation matrix, docs-drift+audit-scripts, and build-validation.

If you're new to this codebase, read Decisions 1–6 in sequence; the Qdrant worked example illustrates each one.

## 2. The six decisions you have to make

| # | Decision | Default if you're unsure | Drill-down |
|---|----------|--------------------------|------------|
| 1 | Folder flavor | `container` | [Decision 1](#5-decision-1--folder-flavor-container-virtual-or-doc-only) |
| 2 | Category | the category of the service you're most similar to | [Decision 2](#6-decision-2--category) |
| 3 | Source variants | `container` + `disabled` (minimum); add `localhost` if users might run this themselves | [Decision 3](#7-decision-3--source-variants) |
| 4 | Port allocation | nothing — it's auto-assigned. Just declare `<NAME>_PORT` in the env block. | [Decision 4](#8-decision-4--port-allocation) |
| 5 | Dependencies | the manifest of the closest sibling in your category, to preserve display order | [Decision 5](#9-decision-5--dependencies-depends_onrequired--optional) |
| 6 | Adaptive / hooks | none — start with declarative `runtime_sc`, escalate to a Python helper only when YAML can't express it | [Decision 6](#10-decision-6--adaptive-behavior--when-to-write-a-hook) |

> **Worked example throughout:** Adding **Qdrant**, a self-hosted vector database. (Qdrant is real software but NOT currently in the stack — Weaviate is our vector DB. Used here purely as an instructional example, not a proposal.)

## 3. Adding a new service

The full walkthrough is the six **Decision** sections below — they cover
folder flavor, category, source variants, port allocation, dependencies,
and adaptive behavior with a Qdrant-as-example thread running through.

If you already know the moving parts, the [TL;DR — 60-second checklist](#1-tldr--the-60-second-checklist)
condenses it to one block, and the canonical regen + lint chain lives at
[After you save the files](#12-after-you-save-the-files--regen--lint-commands-in-order)
(root-safe regen/lint steps — running fewer mandatory steps trips the
byte-equivalence test or docs-drift gate in CI).

> **First time adding a service?** Start with the [Pre-flight study](#4-pre-flight--study-the-candidate-service) below — it lists the upstream-doc questions whose answers feed every later decision.

<a id="4-pre-flight--study-the-candidate-service"></a>

## 4. Pre-flight — study the candidate service

Before you touch any manifest, spend 15–30 minutes with the candidate service's upstream docs. The six decisions below all depend on facts you'll find there. The wrong answer to "what port does it listen on?" or "does it speak the OpenAI API?" cascades into a wrong category, wrong sources, wrong compose mapping, wrong Kong route — every later step.

### 4.1. Research checklist — what to extract from upstream docs

| Question | Where to find it | Why it matters downstream |
|---|---|---|
| One-line elevator pitch | Upstream README / project homepage | Drives `label:` in your manifest + the wizard prompt hint |
| Protocol exposed (REST/HTTP, gRPC, WebSocket, custom binary) | Upstream API docs | Determines whether Kong can proxy it + which existing services can call it |
| OpenAI-API-compatible? | Upstream "API compatibility" docs | If yes → likely belongs behind LiteLLM. If no → direct integration. |
| Default in-container listen port(s) | Upstream `Dockerfile` / `docker-compose` example / `docker run` examples | Compose `ports: <host>:<container>` mapping (Decision 4) |
| Published container images (CPU/GPU/MLX, arch tags) | Docker Hub / GHCR / quay.io page | Decision 3 — which `container-*` source variants to offer |
| License | LICENSE file in upstream repo | Compatibility check. Apache 2.0, MIT, BSD-3 are fine; AGPL / SSPL / source-available licenses require explicit maintainer review before adoption. |
| Runtime dependencies (DB? cache? object store? files?) | Upstream "Configuration" / "Deployment" docs | Decision 5 — `depends_on.required` entries AND whether existing stack services can satisfy them (no need to add a fresh Postgres if Supabase Postgres works). |
| Healthcheck endpoint | Upstream `Dockerfile` `HEALTHCHECK` or operational docs | For the compose fragment's `healthcheck:` block |
| Managed cloud version available? | Upstream project website | Currently moot — `external` source variants are paused stack-wide (see Decision 3). Note the answer for the future auth design. |
| Common host-install footprint (do users already run this themselves?) | Project ecosystem knowledge / Reddit / HN | Decide if `localhost` source variant is worth offering |
| Configuration style (env vars, mounted YAML, both) | Upstream "Configuration" docs | Drives `runtime_sc.<key>.environment` vs. `volumes:` mounts in compose |
| GPU passthrough required? | Upstream "Hardware requirements" / README | If yes → split `container-gpu` from `container-cpu`, set `runtime: nvidia` in compose |
| Persistent state (writes to disk vs. fully stateless) | Upstream "Storage" / "Persistence" docs | Determines whether you need a named volume in compose |

### 4.2. Integration discovery — how does this fit our stack?

Once you understand the candidate, scan our existing service manifests (run `grep -l "^data_flow:" services/*/service.yml`) to identify integration points:

- **Upstream callers (who in our stack would call this new service).** Run `grep -l "^data_flow:" services/*/service.yml` and skim each service's `data_flow.calls` list. Which existing services would benefit from calling this new one? (E.g., a new vector DB → Backend, n8n, JupyterHub, possibly Hermes Agent.) These become entries in those EXISTING manifests' `data_flow.calls` lists — NOT in your new service's `depends_on`. (See [Decision 5](#9-decision-5--dependencies-depends_onrequired--optional) for why `data_flow.calls` is separate from `depends_on`.)
- **Downstream callees (what this service calls).** Does the candidate make outbound calls to anything we already run? Most app-tier services touch Supabase (auth/storage), LiteLLM (LLM access), and Redis (caching). These would be entries in YOUR new service's `data_flow.calls`.
- **Source-variant precedents.** Find the closest existing service that ships similar source variants and use its manifest as a template:
  - New vector DB → `services/weaviate/service.yml`
  - New LLM gateway / engine → `services/litellm/`, `services/ollama/`
  - New STT/TTS engine → `services/parakeet/`, `services/speaches/`, plus the aggregator pattern in `services/stt-provider/` + `services/tts-provider/`
  - New app-tier UI → `services/open-webui/`, `services/jupyterhub/`
  - New cloud-API toggle (not a container) → `services/cloud-providers/service.yml`

### 4.3. Worked example — Qdrant pre-flight

| Question | Qdrant answer | Source |
|---|---|---|
| Elevator pitch | Open-source vector database with HTTP+gRPC APIs and built-in clustering. | qdrant.tech |
| Protocol | REST/HTTP on 6333, gRPC on 6334. | Qdrant API docs |
| OpenAI-compatible? | No — Qdrant has its own REST API. Backend would call it directly. | API reference |
| Default port | 6333 (HTTP). | Upstream `docker-compose.yml` |
| Container images | `qdrant/qdrant:vX.Y.Z` (Docker Hub). CPU-only public image; GPU support is built in but isn't a separate image tag. | hub.docker.com/r/qdrant/qdrant |
| License | Apache 2.0 (verified) | LICENSE file in repo |
| Runtime deps | Self-contained — writes its own storage to a mounted volume. No external DB or cache. | Qdrant "Storage" docs |
| Healthcheck | `GET /healthz` returns 200 when ready. | Qdrant operational docs |
| Managed cloud? | Yes — Qdrant Cloud. Worth offering `external` once the stack-wide auth design lands. | cloud.qdrant.io |
| Host install common? | Less common than Postgres/Weaviate for typical users; offer `localhost` for flexibility but expect rare use. | Ecosystem knowledge |
| Config style | Env vars (`QDRANT__SERVICE__GRPC_PORT`, …) + optional `config.yaml` volume mount. | Qdrant "Configuration" docs |
| GPU passthrough? | No. | — |
| Persistent state | Yes — writes to `/qdrant/storage` (mount a named volume). | Qdrant "Storage" docs |

**Integration discovery for Qdrant.** Backend and n8n already call Weaviate; Qdrant would be a sibling vector-DB option for users who prefer it. JupyterHub users running RAG experiments might want a second vector store for A/B comparisons. No service in our stack would be a downstream caller — Qdrant is a leaf vector store. → On Qdrant's manifest, `data_flow.calls: []`. Integration entries appear in `services/{backend,n8n,jupyterhub}/service.yml`'s `data_flow.calls` lists (added in those manifests, not in Qdrant's).

**Source-variant precedent.** Weaviate is the closest sibling — same category (`data`), same role (vector DB), similar source-variant shape. Open `services/weaviate/service.yml` side-by-side while drafting `services/qdrant/service.yml`.

With pre-flight complete, the six decisions become near-mechanical — proceed to [Decision 1](#5-decision-1--folder-flavor-container-virtual-or-doc-only).

<a id="5-decision-1--folder-flavor-container-virtual-or-doc-only"></a>

## 5. Decision 1 — Folder flavor: container, virtual, or doc-only

Three legitimate flavors of folder live under `services/`. Pick the right one before writing anything else.

| Flavor | `service.yml`? | `virtual: true`? | `compose.yml`? | Examples in this repo |
|---|---|---|---|---|
| **Container service** | yes | absent / false | yes | most services — backend, supabase, ollama, weaviate, … |
| **Virtual manifest** | yes | `true` | no | `cloud-providers/` (LiteLLM-routed APIs), `globals/` (project + branding env), `tts-provider/` (engine selector) |
| **Doc-only folder** | no | n/a | no | `stt-provider/`, `doc-processor/`, `multi2vec-clip/` — aggregator docs + diagrams for a role whose engines live in sibling folders |

**Flowchart:**

1. Does it run as a container with its own image? → **container**.
2. Does it own env vars / `SOURCE` toggles but with no compose fragment of its own? → **virtual**. Set `virtual: true`; omit `compose.yml`. The validator enforces this.
3. Is it documentation-only — aggregating other manifests under one user-facing role? → **doc-only**. No `service.yml`, no `compose.yml`, just `README.md` + `architecture.svg` / `architecture.html`.

> **Worked example — Qdrant:** Qdrant ships as a container image (`qdrant/qdrant:v1.12.0`), exposes a real HTTP API, and has its own env vars → **container flavor**.

**Common mistakes:**
- Adding a virtual manifest with a compose fragment — the schema validator will reject it. Either remove `compose.yml` (if no container runs) or unset `virtual: true` and keep the compose fragment (container flavor).
- Adding a doc-only folder when the role has env vars to manage — use a virtual manifest instead.

<a id="6-decision-2--category"></a>

## 6. Decision 2 — Category

Every manifest declares one of six categories. The category drives two things: the wizard block your row renders in, and the port-slot block your service draws from.

| Category | Wizard block | Services currently in this category | When to pick |
|---|---|---|---|
| `infra` | Infra | Kong, globals, Prometheus, Grafana, Ray, backup, cloudflared | Gateways, project-wide config, observability, prod-readiness |
| `data` | Data | Supabase, Redis, MinIO, Neo4j, Weaviate (+ `multi2vec-clip` as a Weaviate sub-module), Spark | Databases, caches, object storage |
| `llm` | LLM Core | LiteLLM, Ollama, cloud-providers, TEI Reranker | LLM gateways / engines |
| `media` | Media | ComfyUI, parakeet, speaches, chatterbox, docling, searxng, tts-provider | Multimodal AI (image / audio / doc / search) |
| `agents` | Agents & Workflows | Hermes, n8n, openclaw, Airflow, LightRAG | Programmable AI agents, workflow runners |
| `apps` | Apps & UIs | Backend, Open WebUI, JupyterHub, Local Deep Researcher, Zeppelin | User-facing UIs |

**Effects of the category:**
- **Wizard placement.** Categories render in fixed order (`infra` → `data` → `llm` → `media` → `agents` → `apps`). Within a category, services follow topological order (driven by `depends_on.required`).
- **Port-slot block.** Each category gets its own port-offset range — see [Decision 4](#8-decision-4--port-allocation).
- **Architecture-diagram clustering.** The full-stack diagram at `docs/diagrams/architecture.svg` (hand-authored via the architecture-diagram skill) groups services by category band. Per-service architecture diagrams under `services/<name>/architecture.svg` use the same category palette but cluster the call graph instead.

> **Worked example — Qdrant:** Qdrant is a vector database. Its closest siblings in the stack are Weaviate and Supabase (which are also `data`-tier). → **`category: data`**.

**How to pick when you're unsure:** find the most-similar existing service and use its category. If your service genuinely doesn't fit any of the six, that's a design conversation, not a category decision — open an issue first.

<a id="7-decision-3--source-variants"></a>

## 7. Decision 3 — Source variants

Every user-configurable service has an `<SVC>_SOURCE` env var. The wizard reads its values from the `sources.options` block in your manifest.

**Standard option names** (use these; don't invent new ones unless your service genuinely needs them):

| Option | Meaning | When to offer it |
|---|---|---|
| `container` | Run as a Docker container alongside the stack | Always, for container-flavor services |
| `container-cpu` / `container-gpu` | Split when the container has CPU/GPU variants | When you publish a GPU variant of the image |
| `localhost` | Connect to a user-managed instance on the host | When users typically already have this software installed locally (e.g. Ollama, ComfyUI) |
| `external` | Connect to a remote URL | **Currently disabled stack-wide** pending an authenticated-remote design (API keys, bearer tokens, mTLS). Do NOT add new `external` source variants until the auth design ships — see the `external`/`api` retirement entry in `docs/CHANGELOG.md`. |
| `api` | Use a hosted cloud API (no container) | Retired as a service source value. Cloud providers now use `CLOUD_*_SOURCE=enabled|disabled` plus provider API keys. |
| `disabled` | Excluded from compose entirely | Always — every optional service must support this |
| `<engine>-*` | Engine-specific sub-variants | For aggregator services that pick from multiple engines (STT/TTS) |

**Implications of your choices:**

- **Locked vs. user-choice.** A service with only one source variant is "locked" — the wizard skips its prompt entirely. The `_is_locked` helper in `bootstrapper/services/topology.py` enforces this. Services like Backend, Kong, LiteLLM are locked because they're always-on.
- **`requires:` per option.** Use `requires: [<ENV_VAR>]` on a source option to declare prerequisite env vars (e.g. `localhost` typically requires `<SVC>_LOCALHOST_PORT`).
- **`profiles:` per option (deployment-profile gating).** Add `profiles: [default]` to any source option that is dev-only and unreachable on a remote prod host — `localhost` variants in particular, since `host.docker.internal` doesn't point anywhere useful on a managed server. Omitting `profiles:` means "offered in every profile" (the right choice for `container` / `disabled`). The annotation drives two things: the wizard hides non-matching options under `--profile prod`, and `bootstrapper/services/source_validator.py::validate_sources_for_profile` rejects them with an explicit error. Note the manifest-validator lint only fires when a service has **no** prod-eligible option left, so a single unannotated `container` option keeps CI green — you must add `profiles: [default]` to each dev-only option yourself. The matching Python predicate is `option_in_profile` in `bootstrapper/services/manifests.py`. The profile *bundles* themselves live in `bootstrapper/profiles.yml` (#755) — platform-defined `default`/`prod` maps of `sources`/`env`/`host_bind_ip` that `apply_profile_overrides` applies declaratively at start; consumers override individual fields via `profile_overrides:` in `atlas.consumer.yml` (see [deployment/reusing-atlas.md §6.1](deployment/reusing-atlas.md)).
- **`<SVC>_LOCALHOST_PORT` as the single source of truth.** If you offer `localhost`, declare a `<SVC>_LOCALHOST_PORT` env var (integer string, defaulting to the upstream's standard host port). The URL is then derived at compose-render time and Kong-config-generation time as `http://host.docker.internal:${<SVC>_LOCALHOST_PORT:-<default>}`. Both the in-container consumers (`runtime_sc.<svc>.localhost.environment`) AND the Kong route generator (`bootstrapper/utils/kong_config_generator.py`) MUST read the same PORT var so the two paths agree on where the localhost upstream lives. The wizard surfaces an inline integer textbox on the `localhost` row so users can override it without editing `.env`. See PR #10 + the localhost-port-override CHANGELOG entry under [Unreleased] for the design rationale; the symmetry rule is captured in [Common gotchas](#14-common-gotchas--anti-patterns) below.
- **`runtime_sc` slice per source.** Every source variant declared in `sources.options` should have a matching `runtime_sc.<key>.<source>` slice with `scale`, `environment`, `deploy`, `extra_hosts`. The manifest validator does NOT currently check coverage — a missing slice silently scales that source to 0 — so add a slice for every option you declare.
- **`auto_prefer:` (opt-in `<SVC>_SOURCE: auto` support, #753).** An ordered preference list on the `sources:` block that lets consumers commit `<SVC>_SOURCE: auto` in `atlas.consumer.yml`: the resolver picks the first entry whose `requires_capability` holds on the host (`apple_silicon` / `nvidia_gpu` / `host_ollama`, probed by `bootstrapper/services/host_capabilities.py`) and whose option the active profile offers. The last entry must be unconditional (no `requires_capability`) — the terminal fallback, typically the CPU container — and every id must be one of `options[].id`; both are lint-enforced (`_check_auto_prefer_integrity`). Omit `auto_prefer` for services where `auto` makes no sense (they then resolve to `default` with a warning). See `services/comfyui/service.yml` and `services/ollama/service.yml` for the reference shapes.

> **Worked example — Qdrant:** Most users won't already run Qdrant locally, so `container` is the primary path. We offer three variants — `external` is deliberately omitted per the stack-wide moratorium noted above:
> - `container` — default, scale=1
> - `localhost` — `QDRANT_LOCALHOST_PORT` defaults to `"6333"` (Qdrant's standard host port); the URL is derived at compose-render time as `http://host.docker.internal:6333`
> - `disabled` — scale=0

<a id="8-decision-4--port-allocation"></a>

## 8. Decision 4 — Port allocation

**The bootstrapper auto-assigns ports. You do not pick a port.**

**How it works:**
- `BASE_PORT` (default `63000`) is the bottom of the port range. Users can override with `./start.sh --base-port 64000`.
- `CATEGORY_SLOTS` in `bootstrapper/services/topology.py` assigns each category an `(offset, block_size)` tuple. Current values:

  | Category | Offset | Block size | Resolved range with default `BASE_PORT=63000` |
  |---|---:|---:|---|
  | `infra` | 0 | 10 | 63000-63009 |
  | `data` | 10 | 30 | 63010-63039 |
  | `llm` | 40 | 10 | 63040-63049 |
  | `media` | 50 | 20 | 63050-63069 |
  | `agents` | 70 | 20 | 63070-63089 |
  | `apps` | 90 | 20 | 63090-63109 |

- Within each category block, services consume slots in **topological order** (driven by `depends_on.required` — see Decision 5). Multi-port services (e.g. Supabase's 9 containers, Weaviate's HTTP + gRPC pair, MinIO's API + Console pair) get a contiguous run.
- A category-overflow lint trips if you blow past your block. Fixes: move manifests to a different category (rare), or extend the block size in `CATEGORY_SLOTS` (also rare — coordinate with maintainers).

**How to declare a port:**

In your `env:` block, declare `name: <SVC>_PORT` with NO `default:` line. Convention is a single-line comment:

```yaml
env:
  - name: QDRANT_PORT
    # default removed — computed by services/topology.py slot allocator
```

The `services/env_assembler.py` regen step emits the resolved port into `.env.example`. Never hand-edit `.env.example` — it's a generated artifact (byte-equivalence-tested in CI).

> **Worked example — Qdrant:** Qdrant declares `QDRANT_PORT` with no default. Topology slots it into the `data` block at the next free offset, determined by where it lands in the topo sort relative to its siblings (Supabase microservices, Redis, MinIO, Neo4j, Weaviate). The exact number is auto-resolved at every regen — don't pin it.

<a id="9-decision-5--dependencies-depends_onrequired--optional"></a>

## 9. Decision 5 — Dependencies (`depends_on.required` / `optional`)

This is the most nuanced decision. The field `depends_on.required` in `service.yml` has **two semantics that get conflated**:

**Runtime semantics (intended):** "These services must be up before this one boots." Maps to the corresponding `depends_on:` block in your `compose.yml` fragment.

**Display-ordering semantics (overloaded):** The bootstrapper uses `depends_on.required` as the canonical-order backbone for the wizard's row order and the overview-box display. Within a category, services sort topologically; an edge from A → B means A appears AFTER B in the same category's wizard block.

### 9.1. Why this matters — the footgun

Kong's manifest used to list 19 services in `required` because Kong **proxies** to them — but Kong doesn't need them to **boot** (only Supabase + Redis). Trimming the list to its real boot dependencies was correct.

But trimming `litellm` from `ollama.depends_on.required` correctly removed a fake runtime edge — and broke a UI ordering test (`test_row_order_stability.py`), because `ollama` was relying on that edge to pin its position in the wizard's LLM block. Removing it shifted port slots throughout the LLM and media blocks.

### 9.2. Current convention (codified in manifest comments in commit `d98bc5a`)

- Use `required` for **genuine bootstrap blockers** AND for **cross-category display-ordering pins**.
- Comment any non-runtime edge inline as a display-ordering pin so readers don't try to "fix" it. Example from `services/ollama/service.yml`:

  ```yaml
  depends_on:
    # `litellm` is listed here NOT because the ollama container calls
    # LiteLLM (the call direction is the opposite), but because the
    # bootstrapper uses `depends_on.required` as the canonical-order
    # backbone for the wizard / overview-box display. […]
    required:
      - supabase
      - litellm
  ```

- A future schema change may add a separate `display_order:` field that takes over the ordering job; until then, comment the intent.

### 9.3. Things to avoid

- **Don't** list every service you *call* in `required`. Use `data_flow.calls` for that (it drives the architecture diagram and the per-service README's Dependencies & Integrations block — not topology).
- **Don't** depend on virtual aggregates (`globals`, `cloud-providers`) in `required`. They have no runtime presence; the audit removed phantom `globals` edges from `supabase` and `docling`.
- **Don't** list `optional` deps that compose doesn't enforce. The `optional` list is documentation; if compose doesn't gate on it, it has no effect.
- **Don't** list a depends-on for a service that's source-replaceable (`localhost`, `disabled`, or a future authenticated remote mode). The audit script `scripts/check-compose-source-deps.py` enforces this; SOURCE-replaceable consumers should reach their target via endpoint env vars + runtime readiness checks, not via compose `depends_on`.

> **Worked example — Qdrant:** Qdrant's only data-tier sibling that always runs is Supabase (the substrate the bootstrapper provisions before any other service starts). Listing it as a required dep pins Qdrant's position in the data block topologically. → `required: [supabase]`, `optional: []`.

### 9.4. `data_flow.calls` is separate

The `data_flow.calls` field is a runtime call graph that drives the architecture diagram and the per-service README's Dependencies & Integrations block. It is **independent** of `depends_on`. Use it to describe which services this one calls at runtime in the request path (excluding init-time bootstrap calls).

<a id="10-decision-6--adaptive-behavior--when-to-write-a-hook"></a>

## 10. Decision 6 — Adaptive behavior + when to write a hook

Most services express their per-source behavior with declarative `runtime_sc` YAML. The synthesizer in `bootstrapper/services/sc_synthesizer.py` concatenates all manifests' `runtime_sc` slices into the runtime service-config dict the bootstrapper consumes.

**Declarative example** (suffices for ~90% of services):

```yaml
runtime_sc:
  qdrant:
    container:
      scale: 1
      environment:
        QDRANT_LOG_LEVEL: info
      deploy: {}
      extra_hosts: []
    localhost:
      scale: 0
      environment:
        QDRANT_ENDPOINT: http://host.docker.internal:${QDRANT_LOCALHOST_PORT:-6333}
      deploy: {}
      extra_hosts:
        - host.docker.internal:host-gateway
    disabled:
      scale: 0
      environment: {}
      deploy: {}
      extra_hosts: []
```

### 10.1. When you need a `_generate_<svc>_config()` Python helper

Write a helper in `bootstrapper/services/service_config.py` and wire it into `generate_service_environment()` ONLY when one of these is true:

1. **Multi-input SOURCE dependencies.** Your output depends on more than one `<SVC>_SOURCE` value. Example: `_generate_stt_provider_config` and `_generate_tts_provider_config` cooperate via a `shared_env` dict — STT runs first and writes `SPEACHES_SCALE`; TTS reads STT's output and avoids double-scheduling Speaches when both roles pick a Speaches variant.
2. **Derived / aggregated state.** You need to compute env vars from a set of toggles. Example: `_generate_cloud_providers_config` reads three `CLOUD_*_SOURCE` toggles + their API keys and emits `LITELLM_ENABLED_PROVIDERS` as a comma-separated string.
3. **Runtime-computed values.** You need an env var whose value depends on another service's port, computed at runtime from `BASE_PORT`.

For everything else, stay declarative. Adding a hook means writing Python, adding a unit test for it, and giving future maintainers an extra place to read.

> **Worked example — Qdrant:** Single SOURCE, no cross-service dependencies, no derived state. → **No hook needed.** The declarative `runtime_sc` covers all four sources.

### 10.2. `runtime_adaptive` and `runtime_deps`

Two adjacent fields that occasionally apply:

- **`runtime_adaptive`** — for services like `backend` that adapt their behavior based on which upstream services are enabled. Declares `adapts_to:` (a list of provider keys) and `environment_adaptation:` (env vars conditionally set when those providers are active). See `services/backend/service.yml` for the reference pattern.
- **`runtime_deps`** — declares optional runtime dependencies (services this one calls only if they're enabled). Drives the info-message shown to the user during the wizard.

Use these only if your service is genuinely adaptive. Today eight manifests declare `runtime_adaptive` (backend, comfyui, hermes, jupyterhub, lightrag, n8n, ollama, weaviate); backend is the most heavily adaptive and the canonical reference. Don't reach for these fields by default — start with declarative `runtime_sc` and only escalate when the adaptive behavior is non-trivial.

<a id="11-mechanics--putting-it-all-together"></a>

## 11. Mechanics — putting it all together

After making the six decisions, you write two files. Here's the full result for Qdrant. Line callouts (`# ← Decision N`) point back to the decision section that explains the choice.

### 11.1. `services/qdrant/service.yml`

```yaml
# services/qdrant/service.yml — Qdrant vector database
name: qdrant
label: "Qdrant (vector database)"
category: data                                # ← Decision 2
docs: services/qdrant/README.md

containers:
  - qdrant

images:
  - var: QDRANT_IMAGE
    default: "qdrant/qdrant:v1.12.0"
    container: qdrant

sources:                                      # ← Decision 3
  var: QDRANT_SOURCE
  default: disabled                           # off by default; user opts in
  options:
    - id: container
      label: "Container"
    - id: localhost
      label: "Host (existing Qdrant)"
      profiles: [default]                      # dev-only: hidden in the prod wizard, rejected by --profile prod
    - id: disabled
      label: "Disabled"
    # NOTE: `external` is deliberately omitted per the stack-wide moratorium
    # on `external` source variants — see Decision 3 above and the
    # `Removed (breaking)` section of docs/CHANGELOG.md.

env:
  - name: QDRANT_SOURCE
    default: disabled
  - name: QDRANT_PORT                         # ← Decision 4 (no default — auto-assigned)
    # default removed — computed by services/topology.py slot allocator
  - name: QDRANT_LOCALHOST_PORT
    default: "6333"
    description: "Host port for the qdrant-localhost source variant. URL is derived at compose-render time as http://host.docker.internal:6333. Same var consumed by Kong's qdrant.localhost route."
  - name: QDRANT_ENDPOINT
    auto_managed: true
  - name: QDRANT_SCALE
    auto_managed: true

depends_on:                                   # ← Decision 5
  required:
    - supabase
  optional: []

exports: []

rows:
  - display_name: "Qdrant"
    source_var: QDRANT_SOURCE
    port_var: QDRANT_PORT
    scale_var: QDRANT_SCALE
    alias: qdrant.localhost
    description: "Vector database (Qdrant)."
    localhost_endpoint_var: QDRANT_ENDPOINT
    localhost_port_var: QDRANT_LOCALHOST_PORT

runtime_sc:                                   # ← Decision 6 (declarative, no hook)
  qdrant:
    container:
      scale: 1
      environment:
        QDRANT_ENDPOINT: http://qdrant:6333
      deploy: {}
      extra_hosts: []
    localhost:
      scale: 0
      environment:
        QDRANT_ENDPOINT: http://host.docker.internal:${QDRANT_LOCALHOST_PORT:-6333}
      deploy: {}
      extra_hosts: [host.docker.internal:host-gateway]
    disabled:
      scale: 0
      environment:
        QDRANT_ENDPOINT: ''
      deploy: {}
      extra_hosts: []

data_flow:
  calls: []
```

### 11.2. `services/qdrant/compose.yml`

```yaml
# services/qdrant/compose.yml — Qdrant vector database
services:
  qdrant:
    image: ${QDRANT_IMAGE}
    container_name: ${PROJECT_NAME}-qdrant
    restart: unless-stopped
    deploy:
      replicas: ${QDRANT_SCALE:-0}
    ports:
      - "${QDRANT_PORT}:6333"
    volumes:
      - qdrant-data:/qdrant/storage
    healthcheck:
      test: ["CMD", "wget", "-q", "-O-", "http://localhost:6333/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    networks:
      - backend-network

volumes:
  qdrant-data:
    name: ${PROJECT_NAME}-qdrant-data
    driver: local
```

### 11.3. `docker-compose.yml` — one new include line

Add to the top-level `include:` block in the `# Data tier` section:

```yaml
  # Data tier
  - services/supabase/compose.yml
  - services/redis/compose.yml
  - services/minio/compose.yml
  - services/neo4j/compose.yml
  - services/qdrant/compose.yml              # ← new
```

<a id="114-bootstrapperutilssource_override_managerpy--register-the-cli-key"></a>

### 11.4. `bootstrapper/utils/source_override_manager.py` — register the CLI key

This is a **mandatory registration step** that's easy to forget. `SourceOverrideManager.source_mapping` is a hardcoded dict; the wizard's `ServiceDiscovery.discover()` filters every service through it. **A service NOT in this mapping is silently dropped from the wizard** — the user never sees a prompt for it.

For a single-container family (most services), add ONE entry:

```python
self.source_mapping = {
    # … existing entries …
    'qdrant_source': 'QDRANT_SOURCE',          # ← new
}
```

For a multi-container family (head + worker, or app + init), the runtime_sc top-level key drives discovery. Map the "main" container's `<key>_source` to the family's actual env var. Example: Ray has `ray-head` and `ray-worker` containers in runtime_sc but a single `RAY_SOURCE` env var — so the mapping is `'ray_head_source': 'RAY_SOURCE'` (the worker has no entry → filtered out, mirroring how `comfyui-init` / `hermes-init` are skipped).

The CLI flag binding in `bootstrapper/start.py` (`@click.option('--qdrant-source', …)` + the `source_args` dict) uses the family-level `qdrant_source` key — different from the discovery key for multi-container families. If your service is multi-container, you'll have TWO entries in `source_mapping` pointing to the SAME env var (one for CLI plumbing, one for discovery).

The pinning test `bootstrapper/tests/test_wizard_app_discovery.py::test_source_mapping_includes_app_service_flags` enforces this — add your service's CLI key to the assertion list so future regressions fail loudly.

<a id="12-after-you-save-the-files--regen--lint-commands-in-order"></a>

## 12. After you save the files — regen + lint commands in order

Run these root-safe commands from the repository root:

```bash
# 1. Regenerate .env.example from manifests
uv run --project bootstrapper python -m services.env_assembler

# 2. Regenerate README.md TOPOLOGY block (auto-includes the new row)
uv run --project bootstrapper python -m tools.generate_readme_topology

# 3. (top-level architecture diagram — hand-authored; no regen step)
#    Update docs/diagrams/architecture.svg by hand via the
#    architecture-diagram skill if your service materially changes the
#    full-stack topology (new category band, new always-on tier, etc.).

# 4. Lint — fails if any of steps 1-3 were skipped
uv run --project bootstrapper python -m tools.validate_fragments

# 5. (Optional, recommended for new manifests) Regen per-service README + diagram
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen qdrant
# After this, services/qdrant/{README.md, architecture.svg, architecture.html} exist.
```

**When to re-run each step:**

- **`env_assembler`** — after any change to a manifest's `env:` block, port allocation, or source variants.
- **`generate_readme_topology`** — after any change to a manifest's `rows:`, `display_name`, `category`, or `alias`.
- **Top-level `docs/diagrams/architecture.svg`** — hand-authored; touch ONLY when a service is added/removed at the band-level (new category, new gateway, etc.). Routine `data_flow.calls` edits flow into per-service diagrams via `bootstrapper.docs.regen`.
- **`validate_fragments`** — always, as the final check before committing.
- **`docs.regen`** — only after creating a new service, or after editing `data_flow.calls` on an existing service. The drift gate in CI (`bootstrapper.docs.regen --all --check`) catches stale per-service READMEs/SVGs/HTMLs.

**No external prerequisites.** Graphviz used to be required for the top-level diagram regen; that step is retired now that the diagram is hand-authored via the architecture-diagram skill.

**Atlas hero art:** regenerate with `python bootstrapper/scripts/generate_logo.py` (needs `pip install pillow` + `chafa` on PATH); commit the refreshed `bootstrapper/ui/textual/assets/atlas_hero_*.json`. Not gated in CI.

<a id="13-audit-script--ci-implications"></a>

## 13. Audit-script + CI implications

After adding a service, check these allowlists. Skipping them means CI fails on the next push.

### 13.1. `scripts/check-compose-source-deps.py` — two allowlists

- **`REQUIRED_DEPENDS_ON`** — set of `(service, dependency)` tuples that MUST appear in compose `depends_on`. Add entries here if your compose fragment hard-depends on `litellm`, `redis`, `supabase-db`, `weaviate-init`, etc. The script fails CI if your manifest claims a hard dep that compose doesn't enforce, OR vice versa.
- **`FORBIDDEN_OPTIONAL_DEPENDS_ON`** — set of edges that MUST NOT exist (depending on a SOURCE-replaceable service via `depends_on` is unsafe because that service may not run as a container). Add entries here if you have an intentional exception with documented justification.

### 13.2. `scripts/check-kong-routes.py` — baseline-default audit

The script runs the Kong route generator against `.env.example` defaults (in a tmp working dir) and verifies the resulting routes match a hardcoded `EXPECTED_HOST_ROUTES` table at the top of the script. If your service publishes a `*.localhost` alias AND its source variant is on-by-default (i.e. `<SVC>_SOURCE`'s default value renders a route), add an entry to `EXPECTED_HOST_ROUTES` mapping the host to the expected upstream URL. Services that are off by default need no entry.

### 13.3. `.github/dependabot.yml` — `directories:` list

If your service ships a `requirements.txt` / `pyproject.toml` in a `build/` or `provider/` subdirectory, add the path to the `directories:` list of the `pip` ecosystem block. **Memory note:** all active manifests must be enumerated; omitted paths drop from scan coverage and silent vulnerabilities accumulate.

### 13.4. CI gates that run on every push

The `.github/workflows/services-lint.yml` workflow runs the four jobs below.
All four are required status checks in the live `gitflow` ruleset:

| Job | What it catches |
|---|---|
| **Manifest lint + unit tests** | `validate_fragments` lint + 1,300+ pytest tests + the backend's own pytest suite (`services/backend/app/app/tests/`). Catches: manifest schema violations, dependency cycles, env-example drift, category overflow, backend route regressions. |
| **Compose merge + byte-equivalence + source-permutation matrix** | Renders `docker compose config` for the merged fragment list + verifies it matches the golden baseline + tests every source variant of every service. Catches: compose-syntax errors, source-permutation regressions. |
| **Docs drift + audit scripts** | `regen --all --check` + `make docs-check` + the remaining audits (`check_doc_links` — including `#anchor` fragment validation, `check-compose-source-deps`, `check-docs-drift`, `check-kong-routes`, `validate_research_schema`, `check-track-membership`) + a `uv lock --locked` gate for the docling localhost provider. Catches: stale per-service docs, three-surface drift, cross-surface links, missing local assets, missing `REQUIRED_DEPENDS_ON` entries, Kong route default drift, broken links/anchors, research-schema violations, stale provider locks, and track-membership omissions. |
| **Build-validation** | `docker buildx build` for every local non-GPU Compose build context plus every `services/*/init/Dockerfile` context; GPU provider builds are intentionally excluded for runner size/time. Catches: unsatisfiable pip pins, broken Dockerfiles, and init-image drift. Runs on every workflow execution and is required. |

Run the equivalent of the CI jobs locally before pushing:

```bash
uv run --project bootstrapper pytest bootstrapper/tests -q                         # job 1 + 2 (minus byte-equivalence)
(cd services/backend/app && uv run --python 3.12 --with-requirements app/requirements.txt --with-requirements app/requirements-dev.txt python -m pytest app/tests -q)  # job 1 backend tests
uv run --project bootstrapper python -m tools.validate_fragments                  # job 1 lint
docker compose --env-file .env.example -f docker-compose.yml config -q            # job 2 merge check
make docs-check                                                                    # job 3 three-surface contracts + strict build
uv run --project bootstrapper python scripts/check_doc_links.py                   # job 3 link check
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check     # job 3 docs drift
uv run --project bootstrapper python scripts/check-docs-drift.py                  # job 3 docs structural audit
uv run --project bootstrapper python scripts/check-compose-source-deps.py         # job 3 deps audit
uv run --project bootstrapper python scripts/check-kong-routes.py                 # job 3 kong audit
uv run --project bootstrapper python scripts/validate_research_schema.py --all    # job 3 research schema
uv run --project bootstrapper python scripts/check-track-membership.py            # job 3 track coverage
(cd services/docling/provider/localhost && uv lock --locked)                      # job 3 docling lock
# job 4 (required Build-validation): docker buildx build over every local non-GPU
# build context plus every services/*/init/Dockerfile. The full, current list
# lives in the build matrix in .github/workflows/services-lint.yml — treat that
# workflow file as the source of truth, not this doc. Build contexts validated
# in CI include services/asset-worker/app, services/asset-baker/app,
# services/backend/app, and others — representative example:
docker buildx build --load -f services/backend/app/Dockerfile services/backend/app/
```

### 13.5. Model-picker CLI flags: the four-seam rule

A `--<svc>-models`-style flag (anything persisted via the wizard's
user-model-selection path) needs FOUR coordinated edits, not two:
1. the `@click.option` declaration in `start.py`,
2. the `source_mapping` / collector-dict entry,
3. the `user_model_selections[KEY] = kwarg` assignment in `main()`,
4. the `wizard_screen` bucket lambda in `integration.py`.
Missing seam 4 silently drops the flag's value. AST-based seam-parity tests
(`tests/test_user_model_selections_seam_parity.py`,
`tests/test_wizard_app_discovery.py`) guard all four — extend them when
adding a picker.

<a id="14-common-gotchas--anti-patterns"></a>

## 14. Common gotchas + anti-patterns

> Also note: `.env` image pins are auto-refreshed from the manifests at
> startup (`_refresh_image_pins_from_manifests()`, shipped 2026-06-07) —
> don't hand-edit `*_IMAGE` values in a user `.env` expecting them to
> stick across manifest bumps.

Distilled from real audit findings — each entry cites the commit, PR, or memory note it came from.

### 14.1. Dependency-list gotchas

`depends_on.required` doubles as a runtime-boot gate and the wizard's display-order backbone — see [Decision 5](#9-decision-5--dependencies-depends_onrequired--optional) for the full footgun and convention. In short: don't drop a cross-category edge just because it isn't a real runtime dependency (it may be pinning wizard display order), don't depend on virtual aggregates like `globals`/`cloud-providers`/`tts-provider` (no container, no compose), and make sure manifest deps and compose `depends_on` agree in both directions — `scripts/check-compose-source-deps.py` enforces the latter.

### 14.2. URL / localhost handling

`<SVC>_LOCALHOST_PORT` is the single source of truth for localhost URLs — see [Decision 3](#7-decision-3--source-variants) for the full rule. The gotcha: the in-container consumer, the Kong route generator, and the wizard's inline-input widget must all read the *same* PORT var, or the paths silently disagree about where the localhost upstream lives.

- **Kong routes fronting browser SPAs need `preserve_host: True`.** Without it the SPA emits unreachable redirect URLs containing the internal Docker hostname.

### 14.3. Init-container patterns

- **Init containers use vanilla `alpine:latest` + inline `apk add`.** Don't ship a Dockerfile that builds a custom init image — the global `alpine:latest` tag gets clobbered.
- **TTS/STT engine in-container ports are NOT all 8000.** Parakeet, Speaches, Docling listen on `8000`; Chatterbox listens on `4123`. Don't assume.

### 14.4. Regen / test gotchas

- **`.env.example` is byte-equivalence-tested.** After any manifest change affecting env vars or port allocation, regen `.env.example` or `test_env_example_consistency` will fail CI.
- **`test_fragment_equivalence` is sensitive to Compose-version defaults.** Don't regenerate the golden baseline reflexively when this test fails. Extend `_strip_volatile_defaults` in the test fixture instead.
- **`docker compose config` needs `.env` to render properly.** In CI, the audit-scripts job copies `.env.example` to `.env` before running the source-deps audit. Locally, you have a real `.env` so this is invisible — but if you remove your `.env`, the audit script's fallback to raw parsing of the top-level `docker-compose.yml` (which is an `include:`-only shell) produces spurious "missing required core dependency" failures.

### 14.5. Topology / category gotchas

- **A new service in a near-full category block can trip the category-overflow lint.** The `data` block is 30 slots and `media` is 20; Supabase alone uses 7. Check current utilization before assuming there's room.
- **Renaming a `row.display_name` breaks tests that hardcode it.** `test_wizard_app_discovery.py` has an `EXPECTED_DISCOVERED` frozenset; update it when renaming.

### 14.6. Wizard discovery gotchas

A service missing from `SourceOverrideManager.source_mapping` is silently dropped from the wizard — no error, the prompt just never appears. See [§11.4](#114-bootstrapperutilssource_override_managerpy--register-the-cli-key) for the registration mechanics, including the two-entry pattern multi-container families need.

## 15. Subdirectory naming convention

Each service folder can hold additional subdirectories beyond `service.yml` and `compose.yml`. Use these well-known names so navigation stays predictable across the stack:

| Subdir | Holds | Example |
|---|---|---|
| `app/` | Source code for an app the manifest builds (the manifest's primary container is **this** code) | `services/backend/app/` (FastAPI source) |
| `build/` | Dockerfile + build inputs when the manifest builds a container from scratch | `services/jupyterhub/build/`, `services/neo4j/build/`, `services/local-deep-researcher/build/` |
| `init/` | Scripts + templates bind-mounted into a `<service>-init` sidecar container that prepares state before the main container starts | `services/n8n/init/`, `services/hermes/init/`, `services/comfyui/init/`, `services/minio/init/`, `services/weaviate/init/` |
| `catalog-init/` | *(Historical — no longer used.)* Was a second init sidecar that UPSERTed a service-owned catalog table in Postgres before the main init ran, gating the downstream `*-init` / `*-pull` containers via `depends_on: service_completed_successfully`. | Both `services/litellm/catalog-init/` (wrote `public.llms`) and `services/comfyui/catalog-init/` (wrote `public.comfyui_models`) were removed. Model catalogs are now per-service `models.yaml` files resolved at bootstrapper start (`model_resolver` / `comfyui_resolver`) and emitted to `volumes/litellm/` + `volumes/comfyui/`; the `public.llms` and `public.comfyui_models` tables were dropped. |
| `pull/` | Same as `init/` but the sidecar is named `<service>-pull` (only ollama) | `services/ollama/pull/` |
| `config/` | Read-only configuration files bind-mounted into the main container at runtime | `services/searxng/config/` (settings.yml) |
| `db/` | Database snapshots + SQL migration scripts | `services/supabase/db/` (snapshot/ + scripts/) |
| `provider/` | Multiple host/container providers for a single capability the manifest exposes via a SOURCE variable (one engine = one subfolder of `provider/`) | `services/parakeet/provider/{gpu,mlx,whisper-cpp,shared}`, `services/docling/provider/{gpu,localhost,shared}`, `services/tts-provider/provider/localhost` |
| `extras/` | User-managed bind-mounted data exposed inside the running container (tools, functions, workflows you can edit on the host) | `services/open-webui/extras/{tools,functions,workflows}` |
| `workflows-stage/` | Example workflow JSON staged for **manual** import via the n8n UI (the init sidecar installs community nodes but does not auto-import workflows) | `services/n8n/workflows-stage/` |

**Family folders.** Some manifests own a family of related containers (e.g. supabase = 9 containers, n8n = main + worker + init). Each family folder gets a brief `README.md` listing the containers it ships and pointing at their definition in `compose.yml`. The fragment's `services:` keys are the authoritative container list; the manifest's `containers:` must match 1:1 (the manifest_validator enforces this — see "Validator rules" below).

## 16. Modifying an existing service

- Env var default change → edit the manifest's `env:` block. Re-run the lint.
- Image version bump → edit the manifest's `images[].default`. The compose fragment references the var, so no compose change is needed.
- New container in the family → add to `containers:` in the manifest AND to `services:` in the fragment.
- New source variant → add to `sources.options[]` in the manifest.

### 16.1. Common modification flows

Short walk-throughs for the modifications you'll do most often:

- **Add a new source variant to an existing service.** Edit `sources.options` + `runtime_sc.<key>` to include the new source. Regen `.env.example`. The source-permutation matrix in CI will exercise every variant — make sure your new variant has a valid `runtime_sc` slice.
- **Rename a service's display name (`rows[].display_name`).** Update the row, then regen the README topology block. Search the test suite for the old name — `bootstrapper/tests/test_wizard_app_discovery.py::EXPECTED_DISCOVERED` is the most common dependency.
- **Bump a container image version.** Edit `images[].default` only. The compose fragment uses `${X_IMAGE}` interpolation, so nothing else changes. Don't forget to test the new image locally before committing.
- **Split a service family into multiple manifests.** Non-trivial. The supabase manifest (9 containers in one family) is the reference pattern; consult it before splitting.

## 17. Cross-referencing sections in service READMEs

Service READMEs follow a numbered convention (`## 1. Overview`, `## 2. Access`, …). The "Dependencies & Integrations" block sits at whatever section number N the README's structure places it — typically 5, but 7/9/12/14 in READMEs with extra pre-Deps content. The `bootstrapper/docs/regen.py` tool detects N and emits matching subsection numbering (`### N.1` through `### N.6`) inside the block.

**Never link to a sub-section by number across services.** "See section 5.4 in the backend README" breaks the moment the target README adds a new pre-Deps section and shifts to 6.4. Always reference by heading text instead: "See *Future — Missing pair integrations* in the backend README."

## 18. Schema cheatsheet

> This section is a quick field reference. For guidance on which fields apply when (and why), see Decisions 1–6 above.

```yaml
name: myservice
label: "My service (human-readable)"
category: apps                          # infra | data | llm | media | agents | apps
docs: services/myservice/README.md        # optional
virtual: false                          # true for env-only manifests like cloud-providers

containers:                             # may be [] when virtual: true
  - myservice

images:
  - var: MYSERVICE_IMAGE
    default: "myorg/myservice:1.0.0"
    container: myservice

sources:                                # OPTIONAL; omit for single-source services
  var: MYSERVICE_SOURCE
  default: container
  auto_prefer:                          # OPTIONAL; enables `MYSERVICE_SOURCE: auto` (#753).
    - id: container                     #   Ordered; last entry must be unconditional.
  options:
    - id: container
      label: "Container"
    - id: disabled
      label: "Disabled"

# Per-source runtime data — what the bootstrapper actually consumes.
# The synthesizer (bootstrapper/services/sc_synthesizer.py) concatenates
# this block (plus all other manifests' runtime_sc blocks) into the dict
# that ConfigParser.load_yaml_config() returns.
runtime_sc:
  myservice:                             # the key matches a name service_config.py reads
    container:
      scale: 1
      environment:
        MYSERVICE_ENDPOINT: "http://myservice:8080"
      deploy: {}
      extra_hosts: []
    disabled:
      scale: 0
      environment:
        MYSERVICE_ENDPOINT: ""
      deploy: {}
      extra_hosts: []

env:
  - name: MYSERVICE_SOURCE
    default: container
  - name: MYSERVICE_PORT
    default: 63099
  - name: MYSERVICE_API_KEY
    default: ""
    secret: true                        # default never echoed into .env.example
  - name: MYSERVICE_SCALE
    auto_managed: true                  # computed by service_config.py from source value
  - name: MYSERVICE_ENDPOINT
    auto_managed: true

depends_on:                             # logical deps (compose-level lives in compose.yml)
  required: []
  optional: []

exports:                                # documents the cross-service env-var contract
  - name: MYSERVICE_ENDPOINT
    consumers: [backend, n8n]

rows:
  - display_name: "My Service"
    source_var: MYSERVICE_SOURCE
    port_var: MYSERVICE_PORT
    scale_var: MYSERVICE_SCALE
    alias: myservice.localhost
    description: "Short description shown in the wizard subtitle."
    # OPTIONAL — inline numeric input mounted on the source prompt. Used by
    # Prometheus (TSDB retention days). The wizard
    # renders a SecondaryNumberInput widget next to the source picker so
    # the user picks source AND numeric refinement in one keystroke
    # sequence — no follow-up cascade step.
    secondary_number:
      env_var: MYSERVICE_KNOB
      label: "Knob value"
      default: "10"
      visible_when_source: ["container"]   # hide for `disabled`
      min: 1
      max: 100
```

## 19. Validator rules (what the lint catches)

1. **schema check** — every `service.yml` matches `bootstrapper/schemas/service.schema.json`
2. **duplicate_env_var** — exactly one manifest owns each env-var name
3. **duplicate_container** — exactly one manifest owns each container name
4. **unknown_dependency** — `depends_on.required/optional` references a known manifest
5. **undeclared_export** — every `exports[].name` is declared in `env:` OR written by some `runtime_sc.<key>.<source>.environment`
6. **undeclared_source_var** — the SOURCE var itself is declared in `env:`
7. **unknown_consumer** — every `exports[].consumers` entry is a known manifest

## 20. Byte-equivalence

`bootstrapper/tests/test_fragment_equivalence.py` asserts that
`docker compose -f docker-compose.yml config` matches the golden baseline at
`bootstrapper/tests/fixtures/rendered_config_baseline.yml`. If you change a
fragment's compose shape, you'll either need to update the baseline (after
confirming the change is intentional) or restore byte-equivalence.

```bash
# Inspect drift
docker compose --env-file .env -f docker-compose.yml config > /tmp/actual.yml
diff bootstrapper/tests/fixtures/rendered_config_baseline.yml /tmp/actual.yml

# Refresh baseline (only when the drift is intentional)
docker compose --env-file .env -f docker-compose.yml config > \
    bootstrapper/tests/fixtures/rendered_config_baseline.yml
```


## 21. `services/_user/` overlay slot (downstream submodule consumers)

Downstream forks that consume this repo as a git submodule may want to layer
additional services without modifying the upstream tree. Drop them under
`services/_user/<name>/`:

```
services/
├── _user/                    # gitignored upstream; tracked downstream
│   └── my-extra-service/
│       └── compose.yml       # required — auto-merged & launched (see below)
│                             # (a service.yml is optional; the core loader
│                             #  skips _-prefixed dirs, so it's only for your
│                             #  own tooling)
├── supabase/
├── ollama/
└── …
```

**Auto-launch (Phase 1):** at startup the bootstrapper discovers every
`services/_user/*/compose.yml` and appends it to the `docker compose`
invocation (`-f docker-compose.yml -f services/_user/<name>/compose.yml …` —
see `DockerManager._compose_file_args`), so overlay services come up and down
with the core stack. When no overlay exists the invocation is unchanged
(default file auto-discovery), so the core stack and the byte-equivalence
baseline are unaffected.

An overlay service is a **self-contained Compose fragment**: it brings its own
image, host ports, and environment, and joins the shared network
(`networks: { backend-network: { name: ${PROJECT_NAME}-network, external: true } }`).
It is intentionally NOT wired into the wizard, the topology port-allocator, or
the generated `.env.example` — manage its image/ports/env directly in the
fragment (use `${HOST_BIND_IP:-}` on published ports to inherit `--profile prod`
localhost binding). The default manifest loader
(`bootstrapper.services.manifests.load_manifests`) still skips `_`-prefixed
directories, so a `_user/<name>/service.yml` remains invisible to the core
manifest pipeline; a downstream wrapper can still call
`load_manifests(services_dir / "_user")` if it wants the manifest model.

This slot is reserved by convention; the upstream `.gitignore` excludes
`services/_user/` so the directory never leaks into a fork's PRs. See
[deployment/reusing-atlas.md §6.1](deployment/reusing-atlas.md) for the
consumer-facing walkthrough.


## 22. Documentation-only manifest fields

A few fields on `service.yml` are accepted by the schema but not yet consumed
by any operational code. They exist for clarity and future use:

- `images[].notes` — free-form note on what the image is used for. Not read
  by any Python code.
- `docs:` — pointer to the service's `services/<name>/README.md`. Useful
  for grep, but no Python imports it.
- `exports[]` — declares the env-var contract this service offers to other
  services. The cross-manifest validator (`bootstrapper/services/manifest_validator.py`)
  checks closure (every consumer name resolves) but does NOT check that the
  exported value is actually produced at runtime.

Treat these as documentation. Setting them helps future readers; omitting
them never breaks anything.
