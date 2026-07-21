# 8.6. Reusing Atlas as Infrastructure

How to use Atlas as the backing infrastructure / platform for another project — for example a RAG-showcase app that needs Weaviate + Neo4j + an LLM gateway + object storage without standing those up itself.

This page is the **overview and decision guide**. It answers: *can I reuse Atlas, which method should I pick, is it ready, how do I wire my project to it, and how do I customize it?* For the full step-by-step of the Git-submodule method specifically, see [submodule-usage.md](submodule-usage.md).

---

## 1. TL;DR

- **Yes, Atlas is designed to be reused** as shared infra for other projects. The whole stack is namespaced by `PROJECT_NAME`, its ports move as a block via `BASE_PORT`, every service is toggleable via `*_SOURCE`, and all containers share one Docker network (`${PROJECT_NAME}-network`) that your project can join.
- **Two methods are ready today:**
  - **A — Standalone + shared network** (recommended when one Atlas instance backs *several* of your projects): run Atlas on its own; your project is a *separate* repo / Compose project that joins `${PROJECT_NAME}-network` and calls services by their Docker DNS name (or through Kong).
  - **B — Git submodule** (recommended when your project *ships and deploys Atlas together with it*): vendor Atlas into your repo under `infra/` and run it from there. Fully documented in [submodule-usage.md](submodule-usage.md).
- **Customization needs no fork:** `PROJECT_NAME`, `BASE_PORT`, `BRAND_*`, per-service `*_SOURCE`, and `--track` cover the common cases.
- **Honest status:** the consumer paths above work today; services dropped into the `services/_user/` overlay now **launch automatically** (see [§6.1.1](#611-back-compatible-services_user-overlay-slot)); and the repo is **tagged** for submodule pinning. See the [§7 consumer runbook](#7-consumer-adoption-runbook-the-full-journey) and [§8 Readiness](#8-readiness).

---

## 2. Choose your reuse method

| Method | Use it when… | Ready? | Detail |
|--------|--------------|--------|--------|
| **A. Standalone + shared network** | One Atlas instance is shared infra across one or more *separate* project repos; you want your app decoupled from Atlas internals. | **Yes** | [§3](#3-method-a--standalone--shared-network-the-rag-showcase-walkthrough) |
| **B. Git submodule** | Your project should clone/deploy *with* a pinned copy of Atlas (single repo, single deploy unit, reproducible version). | **Yes** | [submodule-usage.md](submodule-usage.md) + [§4](#4-method-b--git-submodule) |
| **C. Template / fork** | You need to diverge structurally from upstream Atlas. | Works, but you own the merge cost | [§5](#5-method-c--template--fork-and-why-not-published-images) |
| **D. Published images / pip package** | You want `docker pull atlas/...` or `pip install atlas` without the repo. | **Not supported** | [§5](#5-method-c--template--fork-and-why-not-published-images) |

**Rule of thumb:** building a showcase / app that *talks to* infra → **Method A**. Shipping a product that *bundles* the infra → **Method B**.

---

<a id="3-method-a--standalone--shared-network-the-rag-showcase-walkthrough"></a>

## 3. Method A — Standalone + shared network (the RAG-showcase walkthrough)

Atlas runs as its own stack. Your RAG project is a separate Compose project that attaches to Atlas's network and addresses services by container DNS name. Nothing in your app repo needs to know Atlas's internals beyond the service hostnames.

### 3.1. Step 1 — Run Atlas with a known `PROJECT_NAME`

```bash
# In your Atlas checkout
./start.sh --llm-provider-source none --cloud-openai-source enabled --openai-api-key sk-...   # cloud LLMs, no local GPU
# (or any track/source combination your showcase needs, e.g. --track gen-ai-rag)
```

`PROJECT_NAME` (default `atlas`) determines the shared network name: **`${PROJECT_NAME}-network`** (e.g. `atlas-network`). Set it in Atlas's `.env` if you want a non-default name.

### 3.2. Step 2 — Join Atlas's network from your project

In your RAG project's `docker-compose.yml`, declare Atlas's network as **external** and attach your service to it:

```yaml
# your-rag-project/docker-compose.yml
services:
  rag-app:
    build: .
    environment:
      # Address Atlas services by their in-network DNS name (see §3.3)
      WEAVIATE_URL: "http://weaviate:8080"
      NEO4J_URI: "bolt://neo4j-graph-db:7687"
      OPENAI_BASE_URL: "http://litellm:4000/v1"   # LiteLLM gateway (OpenAI-compatible)
      S3_ENDPOINT: "http://minio:9000"
    networks:
      - atlas

networks:
  atlas:
    external: true
    name: atlas-network        # = ${PROJECT_NAME}-network from your Atlas .env
```

Start Atlas first, then your project:

```bash
(cd /path/to/atlas && ./start.sh)      # infra up
docker compose up -d                    # your RAG app joins atlas-network
```

### 3.3. Service addresses (inside the shared network)

Within `${PROJECT_NAME}-network`, reach each service by its **compose service name** on its **container port** (these are stable and independent of `BASE_PORT`, which only affects host-published ports):

| Service | In-network address | Notes |
|---------|--------------------|-------|
| **Kong** (API gateway / single entry) | `kong-api-gateway:8000` (HTTPS `:8443`) | Route everything through here if you prefer one entry point |
| **LiteLLM** (LLM gateway, OpenAI-compatible) | `litellm:4000` | `POST http://litellm:4000/v1/chat/completions`; auth with `LITELLM_MASTER_KEY` |
| **Weaviate** (vector DB) | `weaviate:8080` (gRPC `weaviate:50051`) | |
| **Neo4j** (graph DB) | `neo4j-graph-db:7687` (Bolt), `:7474` (HTTP) | auth from `GRAPH_DB_AUTH` |
| **Supabase Postgres** | `supabase-db:5432` | REST/Auth/Storage are exposed via Kong — see [submodule-usage.md §6.2](submodule-usage.md#62-pattern-2-kong-gateway-as-single-entry-point) |
| **MinIO** (S3-compatible) | `minio:9000` (console `:9001`) | creds `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| **Redis** | `redis:6379` | auth `REDIS_PASSWORD` |
| **n8n** (workflows) | `n8n:5678` | |
| **Open WebUI** (chat UI) | `open-web-ui:8080` | |
| **Backend** (FastAPI orchestrator) | `backend:8000` | |

The authoritative, always-current port list (host-published ports + Kong hostnames) is [ports-and-routes.md](ports-and-routes.md) and the generated `.env.example`.

### 3.4. Going through Kong instead (single entry point)

If you'd rather not depend on individual service hostnames, route through Kong — Atlas's gateway — at `kong-api-gateway:8000`. Supabase REST is path-routed (`/rest/v1/...`); browser-facing services are host-routed (`<service>.localhost`). The Kong patterns, including the auth headers, are documented in [submodule-usage.md §6.2](submodule-usage.md#62-pattern-2-kong-gateway-as-single-entry-point).

---

<a id="4-method-b--git-submodule"></a>

## 4. Method B — Git submodule

Vendor Atlas into your repo and run it from a subdirectory — best when your project and its infra ship as one versioned, reproducible unit.

```bash
git submodule add https://github.com/thekaveh/atlas infra
cd infra && git checkout v0.1.0       # pin to a release tag — see releasing.md
cp .env.example .env                   # set PROJECT_NAME to your project
./start.sh
```

Pin the submodule to a release **tag** or a reviewed `main`-ancestor commit rather than tracking `main`, so infra upgrades are explicit, reviewable commits — see [releasing.md](releasing.md) for the tag convention. This is the same shared-network model as Method A (your app joins `${PROJECT_NAME}-network`), with the difference that Atlas's source lives inside your repo at a pinned commit. The **complete** reference — directory layout, `.gitignore`, custom env-file location, integration patterns, contributing upstream, CI/CD, multiple stacks, troubleshooting — is [submodule-usage.md](submodule-usage.md).

### 4.1. Stand up a consumer from scratch — the ordered walkthrough

The canonical greenfield path: empty repo → a running, isolated, reproducible
Atlas-backed stack. Each step is runnable; deep dives are linked per step.
(Already on the older `_user/`-symlink layout? Use the
[migration guide](submodule-usage.md#42-parent-repo-consumer-reference-layout).)

**1. Vendor + pin.** Vendor Atlas and pin it to a specific reviewed commit (a
release tag or a `main`-ancestor SHA) so every infra upgrade is an explicit,
reviewable pointer bump:

```bash
git submodule add https://github.com/thekaveh/atlas infra
cd infra && git checkout <atlas-tag-or-main-ancestor-sha> && cd ..
git add .gitmodules infra && git commit -m "vendor atlas@<sha>"
```

Re-pin on a cadence: bump the submodule pointer to a newer reviewed commit, then
re-run your CI gates (step 8). A stale pin misses upstream fixes (including
security fixes); a moving pin makes builds irreproducible.

**Stale local images are rebuilt automatically after a pin bump (#506).** An
in-place submodule upgrade changes the Atlas source but leaves your previously
built local images (`<project>-backend:local`, etc.) untouched — and
`docker compose up --force-recreate` recreates *containers* from those stale
images, so without a rebuild the backend can run last week's code against this
week's compose/env (e.g. a pre-Celery image → `ModuleNotFoundError`). Atlas now
detects this: it records the source commit its local images were last built at
(in a gitignored `.atlas-build-state` marker) and, when the commit has changed,
a normal `./start.sh` adds `--build` so stale images rebuild **before**
containers are recreated. The three paths behave as expected:

- **Fresh clone** — no marker yet → images build on first start.
- **Unchanged restart** — commit matches the marker → no rebuild, fast start (buildkit still caches, so a rebuild that does run only touches contexts that actually changed).
- **Submodule upgrade** — commit differs → stale local images rebuild automatically; no manual `docker compose build` needed.

(Uncommitted local edits to a Dockerfile under the same commit aren't auto-detected — rebuild those with `./start.sh --cold` or an explicit `docker compose build`.)

**2. Author `atlas.consumer.yml`.** One committed manifest is the single source
of truth for how you extend Atlas — consumed with `--consumer` (step 4). A
complete example:

```yaml
# atlas.consumer.yml (parent repo root)
name: myproject
project_name: myproject                 # Docker resource namespace (step 3)
profile: dev                            # default environment bundle (#755); dev aliases default
brand:
  name: MyProject
  tagline: "MyProject on Atlas"
env:
  file: ./atlas.env.user                 # optional flat .env overlay
  values:
    BASE_PORT: auto                      # durable free block — distinct per consumer, stable across restarts
    COMFYUI_SOURCE: auto                 # durable host-adaptive source — MPS on Apple Silicon, container-gpu on NVIDIA, container-cpu elsewhere
    LLM_PROVIDER_SOURCE: auto            # host Ollama if installed, else container
    FAL_SOURCE:                          # key-gated: enabled iff the key is present
      enabled_if_env: FAL_API_KEY
      else: disabled
compose_overlays:
  - ./compose/myproject-overlay.yml      # external overlay; no symlink into infra/
backend_plugins:
  - ./backend/plugins                    # mounted into the backend plugin seam (step 6)
litellm_models:                          # expose plugin routes as LiteLLM models
  version: 1
  models:
    - name: myproject-rag                # globally-unique, non-reserved alias
      api_base: "${ATLAS_BACKEND_INTERNAL}/myproject-rag/v1"
      api_key_var: MYPROJECT_API_KEY
n8n_workflows:                           # seed + activate workflows
  version: 1
  workflows:
    - id: myproject-flow                 # namespaced to atlas-consumer-myproject-flow
      path: ./n8n/myproject-flow.workflow.json
      active: "true"
storage:                                 # parent-owned MinIO buckets + scoped creds
  buckets:
    - name: assets                       # bucket defaults to "<consumer>-<name>"
```

**Reserved-namespace rules:** litellm aliases may not shadow a stack-owned model
(runtime `hermes-agent`/`lightrag` + every catalog model name); n8n ids are
namespaced `atlas-consumer-<id>`; a storage bucket may **not** be named `backend`
(a built-in). Unknown top-level keys are rejected. See
[§6.1](#61-registering-a-parent-project-with-atlasconsumeryml) for the full key
reference.

**3. Isolation — distinct project + non-default port.** `project_name` isolates
Docker **resource names** (container/volume/network are `<name>-…`); it does
**not** namespace host ports. So a second stack on one host also needs a distinct
**`BASE_PORT`**, never the default `63000` a bare `atlas` checkout binds (`doctor`
warns if a non-default project is left on it). Three ways, in order of preference:

- **`BASE_PORT: auto` in the manifest (recommended, esp. for several consumers on
  one host).** Atlas reserves the first wholly-free `BASE_PORT+0..N` block — one
  that skips `63000` **and** any block already in use by another running stack —
  then **persists it and keeps it** across restarts. Consumers started in turn
  each get a **distinct, stable** block with no numbers to coordinate. (Details:
  [§7.4](#74-run-multiple-atlas-instances-on-one-host).)
- **A fixed non-default number** (e.g. `BASE_PORT: "63100"`) when you want a
  specific, identical port on every host.
- **`--base-port auto` at launch** — the one-off form: resolves a free block
  **fresh** each time it's passed (good for a quick relocation; for a durable pin
  prefer the manifest `auto` above).

**4. Startup ordering.** Run the preflights, then launch — each step guards the
next:

```bash
cd infra
./start.sh env backfill                                  # fill any new .env keys from .env.example
./start.sh compose validate                              # assert the merged compose is well-formed
./start.sh doctor --format json                          # consumer-manifest + base-port + unpullable-model lints
./start.sh --consumer "$(pwd)/../atlas.consumer.yml" \
  --project myproject [--track <k>] [--detach]   # BASE_PORT + project come from the manifest
```

`env backfill` keeps `.env` complete across pin bumps; `compose validate` catches
overlay/manifest errors before any container starts; `doctor` surfaces
contract/port/provisioning problems; `--detach` exits after the health gates.

**5. Consuming endpoints.** Your host-side code (a devserver, a desktop app)
reads the exported contract; in-container plugins use compose service DNS
directly.

```bash
./infra/start.sh endpoints export --format env > atlas-endpoints.env   # ATLAS_* KEY=value
```

Host tools read `ATLAS_<SVC>_HOST_ENDPOINT` (e.g. `ATLAS_MINIO_HOST_ENDPOINT`);
in-network containers use the service name (`http://minio:9000`). Never hard-code
a `localhost:<port>` — it moves with `BASE_PORT`. Full field list:
[§6.5](#65-exporting-the-endpoint-contract-endpoints-export).

**6. Backend plugin seam.** Each `backend_plugins` dir is mounted into the backend
at `/app/plugins`; a plugin serving an OpenAI-compatible route can be surfaced as
a LiteLLM model (step 2's `litellm_models`). A plugin sources object storage from
the **exported scoped vars** (`ATLAS_STORE_<store>_*`), never a hand-wired
`MINIO_PUBLIC_URL` or a published MinIO port. Details:
[§6.3](#63-adding-backend-api-routes-via-the-plugin-seam).

**7. Teardown.**

```bash
./infra/stop.sh --project myproject          # stop this stack's containers
./infra/stop.sh --project myproject --cold   # also remove this project's volumes (data loss)
```

`--cold` removes the project's named volumes (DB, MinIO, model caches) — a clean
slate; omit it to preserve data across restarts.

**8. CI drift gates.** Wire these into your consumer CI so an Atlas pin bump can't
break you silently:

```bash
./infra/start.sh doctor --format json                    # manifest + lints
./infra/start.sh endpoints assert --require \
  ATLAS_LITELLM_HOST_ENDPOINT,ATLAS_MINIO_HOST_ENDPOINT   # contract fields you read (#723)
```

Plus assert your `.env.user`/manifest overlay still applies after a cold cycle,
and fail CI if the pinned Atlas is far behind `main` (pin-freshness).

**9. Common footguns.**

- **Default-port collision.** Leaving `BASE_PORT=63000` under a non-default
  project collides with a bare `atlas` checkout — host ports aren't
  project-scoped. Use `--base-port auto` (`doctor` warns otherwise).
- **Declared models on host sources.** `model_sidecars.ollama` /
  `COMFYUI_USER_MODELS` now provision on host sources too: `managed-localhost-mps`
  downloads the resolved ComfyUI set (#754) and `ollama-localhost` pulls the
  declared tags onto the host daemon (#757) — both idempotent, at every start.
  Only an **unmanaged** ComfyUI `localhost` install remains hands-off; `doctor`
  names anything declared-but-missing.
- **Committed-value clobber.** A value committed in the manifest re-applies every
  start and overwrites an operator's temporary `.env` edit — keep human-tuned
  values (e.g. model lists) host-local, and commit only identity (`project_name`,
  `BASE_PORT`).

**Complete worked example — a minimal consumer repo:**

```
myproject/
├── .gitmodules                       # pins infra/ to a reviewed Atlas commit
├── atlas.consumer.yml                # the manifest above
├── atlas.env.user                    # optional flat overlay (gitignored: secrets)
├── compose/
│   └── myproject-overlay.yml         # external overlay (no symlink into infra/)
├── backend/plugins/myproject-rag/    # backend plugin (→ /app/plugins)
├── n8n/myproject-flow.workflow.json  # seeded workflow
├── scripts/start.sh                  # thin launcher (the step-4 command)
├── src/                              # your application code
└── infra/                            # Atlas submodule @ pinned commit
```

`scripts/start.sh` is a thin wrapper around the step-4 launch — no symlinking into
`infra/`, no `.env` mutation, no `docker restart` of Atlas containers. That is the
whole integration.

---

<a id="5-method-c--template--fork-and-why-not-published-images"></a>

## 5. Method C — Template / fork (and why not published images)

- **Template / fork:** Clone Atlas, rip out what you don't need, and own it. Full control, but you inherit the cost of merging upstream changes by hand. Reasonable only if you need to diverge structurally.
- **Published images / pip package (not supported):** There is no `atlas/...` image set or `pip install atlas` artifact. The bootstrapper assumes the repo layout (it reads `services/<name>/service.yml` manifests and generates compose from them), so Atlas is consumed *as a repo*, not as a dependency. If a packaged distribution is ever needed it would be new work; today, use Method A or B.

---

## 6. Customizing Atlas for your project (no fork required)

| Knob | What it does | Where |
|------|--------------|-------|
| **`PROJECT_NAME`** | The Docker Compose project name — prefixes every container, volume, and the network (`${PROJECT_NAME}-network`), and is the `docker compose -p` namespace. **Both `./start.sh` and `./stop.sh` read it**, so stop tears down exactly what start launched. The key to isolation between stacks. Override per-run with `./start.sh --project <name>` / `-p` (persists back to `.env`); the wizard also prompts for it. | `.env` / `-p` |
| **`.env.user`** | Optional user-owned overlay beside the active `.env`. On every start, Atlas merges `.env.user` values into `.env` before backfill and CLI flags. Use it for local downstream-only keys that must survive `.env` regeneration without adding them to upstream `.env.example`. | `.env.user` |
| **`ATLAS_ENV_USER_FILE`** | Optional external user-owned overlay. Use this when Atlas is a submodule and the persistent project config should live in the parent repo instead of inside the Atlas checkout. The external file is applied after sibling `.env.user`, so it wins on duplicate keys; `--project` and other CLI flags still win last. | shell env var |
| **`atlas.consumer.yml`** | Parent-owned one-file registration for project name, branding, env overlays, external Compose overlays, backend plugin roots, and model sidecars. Pass it with `./start.sh --consumer ./atlas.consumer.yml` or `ATLAS_CONSUMER_MANIFEST`. | [§6.1](#61-registering-a-parent-project-with-atlasconsumeryml) |
| **`BASE_PORT`** | Moves the entire host-published port block (default `63000`). `./start.sh --base-port 64000`, or **`--base-port auto`** to pick the first wholly-free block automatically. Does not affect in-network addresses. **Host ports are not project-scoped**, so a second Atlas stack on the same host needs a distinct `BASE_PORT` — reusing one causes silent cross-instance traffic ([§7.4](#74-run-multiple-atlas-instances-on-one-host)). | `.env` / flag |
| **`BRAND_*`** | Rebrands the wizard/banner (name, tagline, author, repo URL, license) — make Atlas present as your platform. | `.env` (`BRAND_*` block) |
| **`*_SOURCE`** | Enable/disable each service or pick its backend (`container` / `container-gpu` / `localhost` / `disabled`). LLMs use `ollama-container-*` / `ollama-localhost` / `none`; cloud providers toggle via the separate `CLOUD_*_SOURCE` vars. Disable what your showcase doesn't use. | `.env` / `--<svc>-source` |
| **`--track`** | Start a curated subset (`gen-ai-rag`, `gen-ai-eng`, `gen-ai-creative`, `ml-eng`, `data-eng`, `trading`, `all`). `--track gen-ai-rag` is the natural fit for a RAG showcase. Explicit `--<service>-source` flags override track membership, and so do SOURCE vars **declared in the manifest's `env.values`** (#783) — a committed `MINIO_SOURCE: container` survives an out-of-track selection instead of being force-disabled, so consumers can request one extra service outside the track declaratively, with no wrapper flag. An explicit CLI flag still beats both. | flag |
| **`services/_user/` overlay** | Back-compatible local discovery slot for co-located services. Prefer `atlas.consumer.yml` for new parent repos so overlays can stay outside the Atlas checkout without symlinks. | [§6.1.1](#611-back-compatible-services_user-overlay-slot) |
| **`MINIO_EXTRA_CONSUMERS`** | Add parent-owned MinIO buckets and scoped service-account credentials for `_user` or manifest-declared services without forking Atlas's `init-minio.sh`. | [§6.1.2](#612-adding-parent-owned-minio-buckets) |
| **`services/supabase/db/_user/` SQL slot** | Add downstream-owned Supabase schema, seed, view, grant, or extension SQL that runs after Atlas-owned database initialization. | [§6.2](#62-adding-supabase-sql-via-the-user-migration-slot) |
| **`BACKEND_PLUGINS_DIR` plugin seam** | Mount a directory of FastAPI route packages into the backend app to add your own API routes — no fork of `services/backend/` required. | [§6.3](#63-adding-backend-api-routes-via-the-plugin-seam) |

Full source/customization matrix: [source-configuration.md](source-configuration.md).

User overlays use normal `.env` syntax (`KEY=value`, quoted values, and whitespace-prefixed inline comments). The merge order is deterministic: `.env.example` baseline → generated or existing `.env` → sibling `.env.user` → `ATLAS_ENV_USER_FILE` → `atlas.consumer.yml` env values → explicit CLI flags such as `--project` or `--<svc>-source`. Overlays and consumer manifests are merged on every start, including `--cold`, before missing keys are backfilled from `.env.example`.

For submodule consumers that need a repeatable parent-repo shape, use the
reference layout in [submodule-usage.md §4.2](submodule-usage.md#42-parent-repo-consumer-reference-layout). It shows the parent-owned `atlas.consumer.yml` pattern, parent-owned Compose overlays, force-set source/branding wrappers, and the validation checklist used by RAG-showcase-style and DayDreams-style consumers. The older `services/_user/<name>/compose.yml` symlink slot remains supported for existing integrations, but new consumers should register through the manifest.

Use `ATLAS_ENV_USER_FILE` for parent-owned config that should be tracked or templated by the consuming project:

```bash
# In the parent project
cat > atlas.env.user <<'EOF'
PROJECT_NAME=myshowcase
BRAND_NAME=My Showcase
OLLAMA_CUSTOM_MODELS=llama3.1:8b
WEAVIATE_MEMORY_LIMIT=2g
EOF

ATLAS_ENV_USER_FILE="$PWD/atlas.env.user" ./infra/start.sh
```

Absolute paths are safest in CI and wrapper scripts. Relative `ATLAS_ENV_USER_FILE` values are resolved against the directory that invoked `start.sh`; direct Python invocations resolve them against the Python process working directory. If the file is missing or unreadable, Atlas prints a warning and continues without applying that overlay. If no overlay provides `PROJECT_NAME`, cold start preserves the previous valid value so a later `./stop.sh` still targets the same stack namespace.

### 6.1. Registering a parent project with `atlas.consumer.yml`

For new parent repositories, commit one `atlas.consumer.yml` beside the
parent-owned overlay, env overlay, plugin directory, and model sidecars. Pass it
to Atlas from the parent repo:

```bash
./infra/start.sh --consumer ./atlas.consumer.yml --no-tui --detach
./infra/start.sh --consumer ./atlas.consumer.yml compose validate
./infra/start.sh --consumer ./atlas.consumer.yml doctor --format json
```

Relative paths in the manifest resolve from the manifest directory, not from
the Atlas checkout. The manifest can declare:

```yaml
name: rag-showcase
project_name: ragshowcase
brand:
  name: RAG Showcase
  tagline: "Atlas-backed retrieval playground"
env:
  file: ./atlas.env.user
  values:
    WEAVIATE_MEMORY_LIMIT: 4g
    FAL_SOURCE:                       # key-gated: enabled iff FAL_API_KEY is set
      enabled_if_env: FAL_API_KEY
      else: disabled
compose_overlays:
  - ./compose/rag-showcase-overlay.yml
backend_plugins:
  - ./backend/plugins
model_sidecars:
  comfyui:
    - ./models/comfyui-custom-models.yaml
  ollama:
    - llama3.2:latest
litellm_models:                       # expose plugin routes as LiteLLM models (§6.3.2)
  version: 1
  models:
    - name: graph-rag
      api_base: "${ATLAS_BACKEND_INTERNAL}/graph-rag/v1"
      api_key_var: RAG_SHOWCASE_API_KEY
n8n_workflows:                        # seed + activate n8n workflows (§6.3.3)
  version: 1
  workflows:
    - id: adaptive-rag
      path: ./n8n/adaptive-rag.workflow.json
      active: "true"
```

Atlas validates the declared paths before Compose runs, merges manifest env
values into `.env`, appends external Compose overlays to the assembled
`docker compose` command without writing symlinks into the submodule, and lists
registered consumers in the launch overview. Multiple manifests may be supplied
by repeating `--consumer` or by setting `ATLAS_CONSUMER_MANIFEST` with
`os.pathsep`-separated paths. List-valued model declarations merge by ordered
union; scalar conflicts such as different `PROJECT_NAME` values fail during
validation instead of silently last-wins.

An `env.values` entry may be **key-gated** instead of a plain scalar, so a
consumer can enable a paid provider only when its key is present — declaratively,
with no wrapper script:

```yaml
env:
  values:
    FAL_SOURCE:
      enabled_if_env: FAL_API_KEY   # env var name, read from the invoking shell
      then: enabled                 # optional; value when the var is set+non-empty (default "enabled")
      else: disabled                # required; value when the var is unset/empty
```

The gate reads `FAL_API_KEY` from the environment at `./start.sh` time (a blank
value counts as absent). Malformed forms — a missing `else`, an unknown key, or a
non-`^[A-Z][A-Z0-9_]*$` env-var name — fail validation up front.

A `<SVC>_SOURCE` entry may also be the **`auto` sentinel** — the source-selection
analog of `BASE_PORT: auto` (#753). It resolves once, before source validation,
to the best source for **this** host and is then durable:

```yaml
env:
  values:
    COMFYUI_SOURCE: auto        # Apple Silicon → managed-localhost-mps; NVIDIA → container-gpu; else container-cpu
    LLM_PROVIDER_SOURCE: auto   # host Ollama installed → ollama-localhost; else container
```

- **Durable keep.** A concrete, valid, *non-default* value already in `.env` — a
  prior `auto` resolution or an explicit `--<svc>-source` override — is kept
  as-is; `auto` never clobbers it. (To durably force the service *default* on a
  host where `auto` would pick otherwise, commit the concrete id instead of
  `auto`.)
- **Platform-adaptive.** Resolution follows the service manifest's ordered
  `sources.auto_prefer` list, matched against a host-capability probe
  (`apple_silicon`, `nvidia_gpu`, `host_ollama`), restricted to options offered
  under the active `--profile`. Services without `auto_prefer` fall back to
  their default with a warning.
- **Cold-regen safe.** A regenerated `.env` re-resolves host-correctly instead
  of silently reverting to `container-cpu` — the same committed manifest is
  right on a Metal Mac, an NVIDIA box, and Linux CI.
- `./start.sh … doctor` reports each resolution and the capability that matched
  (the `auto-sources` check).

**Deployment profiles — one switch for the whole environment (#755).** Beyond
per-var selection, a consumer can select a named **environment bundle**. Atlas
ships two declarative bundles in `bootstrapper/profiles.yml` — `default` (alias
`dev`) and `prod` — each naming per-profile `sources` (a concrete id or
`auto`), `env` values (limits/logging knobs), and `host_bind_ip`. The manifest
may name its default environment and override individual bundle fields
(override-only — consumers cannot define new profile names):

```yaml
profile: dev                          # this project's default environment
profile_overrides:
  dev:
    sources: { comfyui: auto }        # delegate to the #753 resolver
    env: { WEAVIATE_MEMORY_LIMIT: 4g }
  prod:
    sources: { comfyui: container-gpu }
```

`./start.sh` with no `--profile` flag uses the manifest's `profile:`;
`--profile prod` selects the whole prod set in one switch (loopback bind,
observability ON, log rotation, prod sources). Semantics per field: a profile's
`sources` are asserted on every start of that profile **except** when that
service's source was set by an explicit CLI flag this run (operator wins);
`env` values apply only when unset (an operator-set value is kept with a
notice); switching profiles resets the prior profile's asserted sources to
their service defaults (no residue), while a same-profile restart never resets
anything — tracked via the `ATLAS_PROFILE_APPLIED` marker in `.env`. The
`profile` doctor check reports the effective bundle and the precedence tier
each managed value currently comes from.

Unknown or typo'd **top-level** keys are rejected with a clear error naming the
offending key and the allowed set — a manifest that misspells `compose_overlays`
as `compose_overlay` (or `model_sidecars` as `model_sidecar`) fails validation
and `./start.sh … doctor` reports the `consumer-manifests` check as failed,
instead of silently dropping the block and surfacing later as mysterious runtime
404s. The allowed top-level keys are exactly those shown above: `name`,
`project_name`, `profile`, `profile_overrides`, `brand`, `env`,
`compose_overlays`, `backend_plugins`, `model_sidecars`, `storage`,
`litellm_models`, `n8n_workflows`, `rag_ingestion_profiles`, and
`lightrag_query_profiles`.

#### 6.1.1. Back-compatible `services/_user/` overlay slot

To add your own service *into* the Atlas stack (so it starts/stops with `./start.sh` / `./stop.sh` and shares the stack's network), drop a Compose fragment at `services/_user/<name>/compose.yml`. On launch the bootstrapper discovers every `services/_user/*/compose.yml` and merges it into the `docker compose` invocation (`-f docker-compose.yml -f services/_user/<name>/compose.yml …`), so your service comes up alongside the core stack. The `services/_user/` slot is gitignored upstream, so your additions never appear in an Atlas PR.

A `_user/` overlay service is a **self-contained Compose fragment**: it brings its own image, host ports, and environment, and joins the shared network. Example:

```yaml
# services/_user/rag-indexer/compose.yml
services:
  rag-indexer:
    image: myorg/rag-indexer:1.2.0
    container_name: ${PROJECT_NAME}-rag-indexer
    restart: unless-stopped
    environment:
      WEAVIATE_URL: "http://weaviate:8080"
      OPENAI_BASE_URL: "http://litellm:4000/v1"
    ports:
      - "${HOST_BIND_IP:-}8090:8090"      # choose a free host port yourself
    networks:
      - backend-network

networks:
  backend-network:
    name: ${PROJECT_NAME}-network
    external: true
```

**Scope note:** overlay services *launch*, but they are intentionally **not** wired into Atlas's wizard, topology port-allocator, or generated `.env.example` — you manage their image/ports/env directly in the fragment (use `${HOST_BIND_IP:-}` on published ports to inherit the `--profile prod` localhost-binding behavior). If you'd rather keep your service in its *own* repo entirely, use Method A instead (it joins the same network from outside).

#### 6.1.2. Adding parent-owned MinIO buckets

**Preferred (declarative): the `storage:` block.** Register object stores in
your `atlas.consumer.yml` and Atlas provisions everything — no compose override,
no endpoint reverse-engineering, no URL rewriting:

```yaml
# atlas.consumer.yml
name: daydreams
storage:
  buckets:
    - name: artifacts              # store handle (unique per consumer)
      bucket: daydreams-artifacts  # optional; default "<consumer>-<name>"
```

Atlas compiles each store to the `MINIO_EXTRA_CONSUMERS` grammar below, generates
a scoped service-account credential once (persisted across restarts), writes the
`minio-init` overlay for you (gitignored `volumes/minio/consumer-storage.compose.yml`),
and exports stable per-store fields for your app to consume:

```text
ATLAS_STORE_DAYDREAMS_ARTIFACTS_BUCKET=daydreams-artifacts
ATLAS_STORE_DAYDREAMS_ARTIFACTS_INTERNAL_ENDPOINT=http://minio:9000     # write path
ATLAS_STORE_DAYDREAMS_ARTIFACTS_PUBLIC_ENDPOINT=http://localhost:${MINIO_PORT}  # browser read base
ATLAS_STORE_DAYDREAMS_ARTIFACTS_REGION=us-east-1
ATLAS_STORE_DAYDREAMS_ARTIFACTS_ACCESS_KEY_VAR=MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY  # reference, not a raw secret
ATLAS_STORE_DAYDREAMS_ARTIFACTS_SECRET_KEY_VAR=MINIO_DAYDREAMS_ARTIFACTS_SECRET_KEY
```

Bucket names are validated (S3 rules) and collision-checked against built-in
buckets and across consumers. **Browser-safe presigning:** sign presigned GET
URLs against the **public** endpoint (`…_PUBLIC_ENDPOINT`) — never sign against
`minio:9000` and rewrite the host, which invalidates the signature. Use boto3
with `endpoint_url=<public>` or the dependency-free reference presigner
`bootstrapper/utils/s3_presign.py::presign_get_url`. See
[services/minio/README.md §6.1–6.2](https://github.com/thekaveh/atlas/blob/main/services/minio/README.md).

**Underlying grammar (still supported for `_user` overlays).** If your `_user`
service needs object storage, do not fork
`services/minio/init/scripts/init-minio.sh`. Instead, extend the existing
`minio-init` service from the parent-owned overlay and pass
`MINIO_EXTRA_CONSUMERS`. Each entry uses the same grammar as Atlas's built-in
consumers:

```text
CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR[:EXTRA_BUCKET_VAR,...]
```

A DayDreams-style overlay can keep the bucket name and credentials in the
parent repo:

```yaml
# services/_user/daydreams/compose.yml
services:
  minio-init:
    environment:
      MINIO_EXTRA_CONSUMERS: "daydreams:MINIO_BUCKET_DAYDREAMS:MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY"
      MINIO_BUCKET_DAYDREAMS: ${MINIO_BUCKET_DAYDREAMS:-daydreams-artifacts}
      MINIO_DAYDREAMS_ACCESS_KEY: ${MINIO_DAYDREAMS_ACCESS_KEY}
      MINIO_DAYDREAMS_SECRET_KEY: ${MINIO_DAYDREAMS_SECRET_KEY}
```

Place the referenced `MINIO_BUCKET_DAYDREAMS`,
`MINIO_DAYDREAMS_ACCESS_KEY`, and `MINIO_DAYDREAMS_SECRET_KEY` values in
`.env.user` or `ATLAS_ENV_USER_FILE`. On every `minio-init` run, Atlas creates
the extra bucket, writes a named inspectable policy, and creates or refreshes a
service account with the same inline scoped policy used by built-in consumers.
Multiple entries may be separated by spaces; comma-separated extra bucket vars
after the fourth field grant one consumer access to a small named bucket set.

#### 6.1.3. Scripted bring-up for automation

For CI, cron, or parent-repo wrapper scripts, use the non-interactive detached
path instead of backgrounding `start.sh` and killing it after a hand-written
health poll:

```bash
./start.sh --no-tui --detach
```

`--detach` is also available as `--no-follow`. It runs the normal Atlas start
pipeline, starts Compose in detached mode with its health wait enabled, prints a
per-service status summary, and exits with `0` only when the final status
summary is healthy. Startup targets only the **enabled** services from the
rendered Compose projection (derived from the resolved configuration — tracks,
overrides, and consumer overlays included), so a broken local build belonging
to a disabled/out-of-track service can no longer abort the bring-up; the
selected target set (and the excluded disabled services) is echoed for
debugging (#504). A nonzero Compose `up --wait` return is re-inspected before
failing: when every service is running/healthy and every one-shot init exited
`0`, it is classified as the known benign one-shot race and startup continues
to the final summary; genuine failures still fail and name the offending
service and exit code (#508). A service whose healthcheck is still in its start
period (`health=starting`) at that moment is treated as **convergent-pending**,
not failed: the still-starting rows are re-polled within a bounded grace window
(up to ~120 s) before classifying, so a stack that is merely mid-probe no longer
false-fails with `[ERROR] … starting` lines or a nonzero exit (#677/#681).
Genuine failures (unhealthy, exited-nonzero, or still starting after the grace
window) still fail loudly and name the service. Add `--json` when a parent
script needs machine-readable status:

```bash
./start.sh --no-tui --detach --json
```

The JSON payload includes a `converged_after_grace` boolean — `true` when the
start converged only after re-polling still-`starting` rows through the grace
window, so automation can tell a health race apart from a first-pass-healthy
start.

**Shared managed-host runtimes and teardown.** Some sources run a **native
host-global process** rather than a container: Apple-Silicon/Metal ComfyUI
(`COMFYUI_SOURCE=managed-localhost-mps`) and vLLM Metal
(`VLLM_METAL_SOURCE=managed-localhost`). These listen on a fixed loopback port
and are shared by **every** Atlas consumer on the machine — they are not
Compose-project resources, so `docker compose down` never touches them. Because
multiple concurrent consumers (disjoint project names + base-port ranges) can
share one such runtime, a project-scoped stop leaves it running by default:

```bash
./stop.sh --project consumer-b        # containers for consumer-b only; host-global runtimes untouched
```

If a managed host process is detected running, `stop.sh` prints an advisory
naming the explicit opt-in. To deliberately tear the host-global runtimes down
(ComfyUI-MPS **and** vLLM-Metal), pass `--stop-managed-hosts` — this affects
**all** consumers using them and is reported as such:

```bash
./stop.sh --stop-managed-hosts        # also stop the host-global ComfyUI-MPS / vLLM-Metal processes
```

Standard and `--cold` stops follow the same rule. This makes unattended
multi-consumer cold-reset loops safe: repeatedly resetting one consumer with a
bare `./stop.sh --project <name>` (optionally `--cold`) will not interrupt
another consumer sharing a managed host runtime.

#### 6.1.4. Headless submodule upgrade validation

When a parent repository pins Atlas as an `infra/` submodule, upgrade the pin
with a headless validation pass before starting the stack. This catches newly
introduced `.env.example` keys and invalid Compose overlays without entering the
interactive wizard:

```bash
git -C infra fetch
git -C infra checkout <atlas-sha>
cd infra
./start.sh env backfill
./start.sh --consumer ../atlas.consumer.yml compose validate
./start.sh --consumer ../atlas.consumer.yml doctor
./start.sh compose validate
./start.sh doctor
./start.sh --no-tui --detach
```

`./start.sh env backfill` is additive and idempotent. It preserves existing
values, fills blank values only when `.env.example` now provides a non-blank
default, and prints the keys it added or filled grouped by their source section.
`./start.sh compose validate` runs the assembled `docker compose config -q`
path, including every `services/_user/<name>/compose.yml` overlay, and rewrites
common missing-variable failures into a service and variable summary before
printing Compose's raw stderr for debugging. Before validating, it materializes
the consumer manifest's derived env (`BACKEND_PLUGINS_DIR`,
`COMFYUI_CUSTOM_MODELS_FILE`, `OLLAMA_CUSTOM_MODELS`) into `.env` — the same
values a full start writes — so an overlay that interpolates
`${BACKEND_PLUGINS_DIR}` validates on a fresh checkout that has never started
(no manual `export` workaround needed). `doctor` does the same before running
its checks.

Exit codes:

- `env backfill` exits `0` when the env file is already current or was updated
  successfully, and `1` if the backfill write fails.
- `compose validate` exits `0` when Compose accepts the assembled stack, and
  otherwise exits with Compose's failing status code.

#### 6.1.5. Consumer doctor for CI preflight

Use the consumer doctor as the parent repository's Atlas-generic preflight
before product-specific tests:

```bash
cd infra
./start.sh env backfill
./start.sh doctor --format json
./start.sh --consumer ../atlas.consumer.yml doctor --format json
./start.sh --no-tui --detach
```

`./start.sh doctor` does not start containers. It runs a registry of preflight
checks for the assembled consumer integration: consumer manifest validation,
Compose validation, `_user` overlay environment references, plugin directory
sanity, model sidecar parsing, consumer endpoint reporting, and tracked-file
cleanliness for the Atlas
checkout. Checks that require Docker are reported as `skipped` when Docker is
unavailable; Docker-free checks still run. Text output is intended for local
debugging, while `--format json` is intended for consumer CI. The command exits
non-zero when any check reports `fail`.

`--format json` emits **pure JSON on stdout** — the `📦 Using …` dependency-manager
banner from the shell dispatcher goes to stderr — so it pipes directly to `jq`
with no extraction shim:

```bash
./start.sh doctor --format json 2>/dev/null | jq .ok
```

### 6.2. Adding Supabase SQL via the user migration slot

To layer project-owned database objects onto Atlas's managed Supabase instance,
place SQL files under `services/supabase/db/_user/`. The `supabase-db-init`
container runs Atlas-owned SQL from `services/supabase/db/scripts/` first, then
runs user SQL from `_user/` in lexical order. The slot is mounted read-only in
the init container and is optional; a fresh Atlas checkout starts normally with
no user SQL files.

Use numbered file names such as `10-project-schema.sql` and
`20-seed-reference-data.sql`. User SQL should be idempotent because the init
runner may be re-executed against an existing volume. Prefer `CREATE ... IF NOT
EXISTS`, guarded `ALTER TABLE`, and conflict-safe seed statements. A failing
user SQL file fails `supabase-db-init`, which prevents dependent services from
starting against a half-prepared database.

See `services/supabase/db/_user/README.md` and
`services/supabase/README.md` for the service-level contract.

### 6.3. Adding backend API routes via the plugin seam

The FastAPI backend exposes a **generic plugin seam** so you can mount your own API routes *into* it without forking `services/backend/`. On startup the backend calls `load_plugins(app)`, which scans `$BACKEND_PLUGINS_DIR` (default `/app/plugins`). An optional shared `$BACKEND_PLUGINS_DIR/requirements.txt` is installed first; then, for each immediate subdirectory that is an importable Python package exposing a module-level `router` (a FastAPI `APIRouter`), that plugin package's own optional `requirements.txt` is installed before the package is imported and included into the running app. A plugin whose requirements fail to install is logged with the requirements path and pip output, then skipped before import; a shared requirements failure skips plugin loading for that startup. Requirements install into a **writable plugin site** (`pip --target $BACKEND_PLUGINS_SITE_DIR`, default `/tmp/atlas-plugins-site`) that the seam pre-creates and prepends to `sys.path` before any plugin import — the image runs as `appuser` with root-owned site-packages and no `$HOME`, so untargeted installs would fail with `EACCES` (#559); no consumer-side tmpfs/`PYTHONUSERBASE` workaround is needed. The seam is a **no-op when the directory doesn't exist** (so base Atlas is unaffected), and a plugin that fails to import is logged and skipped — one bad plugin never crashes the backend.

Plugins are installed and imported **at backend startup**, so **apply plugin changes by recreating the backend** (`docker compose up -d --force-recreate backend`) — a restart, not a hot reload, is the correct pickup path. The backend's dev auto-reloader is off by default (#679): with your `backend_plugins` dir bind-mounted, host-side git churn in that tree (a checkout, branch switch, or rebase) must **not** restart the running backend — that would kill in-flight requests and, under rapid churn, crash-loop the container. Set `BACKEND_DEV_RELOAD=true` only when you are actively editing plugin source and want live reloads.

To use it, mount a plugins directory into the backend container and (optionally) point `BACKEND_PLUGINS_DIR` at it. With a submodule/overlay layout you extend Atlas's `backend` service from your parent Compose:

```yaml
# your parent docker-compose.yml — overlay onto Atlas's backend
services:
  backend:
    volumes:
      - ./my-plugins:/app/plugins:ro
    environment:
      BACKEND_PLUGINS_DIR: /app/plugins   # the default; shown for clarity
```

Each plugin is a package directory exposing `router`:

```
my-plugins/
  requirements.txt     # optional shared dependencies for all plugins
  rag_routes/
    __init__.py          # exposes `router = APIRouter(prefix="/rag", ...)`
    requirements.txt     # optional; pip-installed before the package is imported
```

```python
# my-plugins/rag_routes/__init__.py
from fastapi import APIRouter

router = APIRouter(prefix="/rag", tags=["rag"])

@router.get("/health")
def health():
    return {"ok": True}
```

Your routes are then served by the same backend — reachable at `backend:8000` in-network, or via Kong at `api.localhost/...`. This is the recommended way to add backend endpoints (e.g. a `/rag` surface) for a downstream showcase without maintaining a fork. See [`services/backend/README.md` §4](https://github.com/thekaveh/atlas/blob/main/services/backend/README.md) for the backend-side description.

#### 6.3.1. Declaring a typed plugin contract with `plugin.yml`

A plugin package MAY ship an optional **`plugin.yml`** next to its `__init__.py`. Absent → the plugin loads exactly as above (fully backward compatible). Present → it declares a **versioned, typed, validated contract** so operators can *see* what is mounted and what env it needs, a missing/typo'd var surfaces as a startup **diagnostic** instead of a runtime 500, and per-plugin Kong auth has a place to live:

```yaml
# my-plugins/tableau/plugin.yml
plugin_manifest_version: 1
name: tableau                       # unique, kebab-case
route_prefix: /tableau             # must not overlap another plugin or a built-in route
health_path: /tableau/health
docs_url: https://example.com
auth: key-auth                     # inherit | open | key-auth
env:
  - name: TABLEAU_EXECUTION
    type: enum
    values: [fake, comfyui]
    default: comfyui
  - name: LITELLM_MASTER_KEY
    required: true                 # missing → startup + doctor warning
    secret: true                   # masked everywhere (inventory, doctor, logs)
```

A RAG-shaped plugin declares its dependency endpoints and role/model/flavor files the same way:

```yaml
# backend_plugins/rag/plugin.yml
plugin_manifest_version: 1
name: rag
route_prefix: /rag
health_path: /rag/health
auth: inherit
depends_on: [litellm, weaviate, lightrag, n8n]
env:
  - name: RAG_ROLES_FILE
    required: true
  - name: RAG_MODELS_FILE
    required: true
  - name: RAG_FLAVORS_FILE
    required: true
```

**What the contract buys you:**

- **Inventory.** `GET /plugins` on the backend lists every mounted plugin — name, route prefix, health/docs, auth policy, declared env (secret values masked as `***`), and load status (`loaded` / `skipped` / `error`). Secret *values* are never exposed, but env-var names/flags are; `/plugins` is served under the backend route, so it inherits `BACKEND_KONG_AUTH` (open in local-dev default, gated once you set `key-auth`).
- **Startup + preflight validation.** The seam validates declared env at boot, and [`./start.sh doctor`](#615-consumer-doctor-for-ci-preflight) re-validates it before launch: required-but-missing and enum/type mismatches are reported by plugin + var name. Secret values are never echoed.
- **Fail-fast, isolated.** A present-but-malformed `plugin.yml` does **not** degrade to manifest-less loading — that one plugin is **skipped** with a structured error and the others stay healthy. Duplicate plugin names, overlapping prefixes, and prefixes that shadow a built-in backend route are rejected before mounting. The reserved set is the backend's `RESERVED_ROUTE_PREFIXES` (17 names): `api`, `comfyui`, `docs`, `documents`, `health`, `jobs`, `lightrag`, `media`, `memory`, `metrics`, `openapi.json`, `plugins`, `ready`, `redoc`, `research`, `storage`, `workflows`.
- **Per-plugin gateway and application auth.** `auth: key-auth` puts Kong key-auth on that plugin's `route_prefix` and validates the same `BACKEND_KONG_API_KEY` inside FastAPI, preventing direct-port bypass. `auth: open` is an explicit public opt-out. `auth: inherit`, and plugins without a manifest, use the Backend application identity boundary. Atlas composes the matching Kong policy per prefix; distinct per-prefix credentials remain a future extension.

The `plugin_manifest_version` is a hard-pinned contract version — a manifest built for a version this backend does not understand is skipped rather than mis-read. The canonical schema is [`bootstrapper/schemas/plugin.schema.json`](https://github.com/thekaveh/atlas/blob/main/bootstrapper/schemas/plugin.schema.json).

#### 6.3.2. Exposing plugin models to LiteLLM with `litellm_models`

A backend plugin (§6.3) that serves an OpenAI-compatible route can surface that route as a **first-class model in LiteLLM** — so Open WebUI, n8n, the backend, and any other LiteLLM consumer discover it through `/v1/models` with **no registration script**. Declare a versioned `litellm_models` block in `atlas.consumer.yml`:

```yaml
# atlas.consumer.yml
name: rag-showcase
backend_plugins:
  - ./backend/plugins            # serves /graph-rag, /vanilla-rag, … (§6.3)
litellm_models:
  version: 1
  models:
    - name: graph-rag                                   # the LiteLLM alias / model_name
      api_base: "${ATLAS_BACKEND_INTERNAL}/graph-rag/v1"  # approved Atlas endpoint template
      api_key_var: RAG_SHOWCASE_API_KEY                 # a secret *reference* (env var NAME)
      description: Graph RAG over Neo4j
      tags: [rag, graph]
      model_info:
        mode: chat
    - name: vanilla-rag
      api_base: "${ATLAS_BACKEND_INTERNAL}/vanilla-rag/v1"
```

Because LiteLLM's config is regenerated from YAML + env on every start, Atlas **merges owned model rows into the declarative config before LiteLLM boots** — it never calls the LiteLLM admin API. On `./start.sh`, the bootstrapper resolves each row, writes the gitignored `volumes/litellm/consumer-models.yaml`, and `litellm-init` appends those rows to `config.yaml` after the stack rows (`hermes-agent`, `lightrag`) and catalog models.

**No consumer-side reload is needed.** Every `./start.sh` recreates the stack with `docker compose up --force-recreate`, and the LiteLLM server waits for `litellm-init` (`service_completed_successfully`) before it boots. So on *every* start — cold or warm — LiteLLM is recreated reading the freshly-merged `config.yaml`, and your declared aliases appear in `/v1/models` immediately. **Do not** `docker restart` the LiteLLM container or hit its admin API from your own launcher to "pick up" model changes — that's redundant with the recreate-on-start contract.

**What the contract enforces:**

- **Ownership is derived from the manifest, not spoofable.** Every generated row is stamped with `model_info.atlas_owner: <consumer>`. An explicit `owner:` may only restate the consumer's own name — claiming another's is rejected. A removed manifest removes **only that consumer's** rows on the next start; stack rows and sibling-consumer rows are untouched.
- **Approved endpoints only.** `api_base` is resolved against an allowlist of in-network Atlas endpoint templates — currently `${ATLAS_BACKEND_INTERNAL}` (`http://backend:8000`, where §6.3 plugins mount). It must resolve to a **clean base URL** (`scheme://host/path`): an arbitrary external host, an unapproved `${...}` template, leftover unresolved interpolation, userinfo credentials (`user:pass@…`), or **any query string or fragment** (the usual carrier for `?authorization=…`, `?api_key=…`, `#token`) is **rejected at load**. So a generated LiteLLM row can never exfiltrate to an off-stack host or carry a secret into the config file or startup log.
- **Secrets stay references.** Use `api_key_var` (an `UPPER_SNAKE` env var name), never a literal `api_key`. Atlas renders `api_key: os.environ/<VAR>` (the same form as the stack `hermes-agent` row) and generates a compose overlay that passes that var into the `litellm` container so it resolves at request time. The secret **value** appears in no generated file, overlay, log, or doctor output.
- **Unique, non-reserved aliases.** Aliases are globally unique across all consumers (one generated config) and may not shadow a stack-owned alias — that reserved set is **catalog-derived**: the runtime rows (`hermes-agent`, `lightrag`) *and* every YAML-catalog model name (`gpt-4o`, `nomic-embed-text`, …), rejected up front. As a last-line defense, `litellm-init` also **skips** any consumer row whose alias collides with an already-rendered stack model, so a stack model can never be silently hijacked (LiteLLM would otherwise load-balance duplicate `model_name`s across both endpoints).
- **Preflight cross-check.** [`./start.sh doctor`](#615-consumer-doctor-for-ci-preflight) validates the block and cross-checks each backend-hosted model's first route segment against the declared `plugin.yml` `route_prefix`es (§6.3.1) — a model pointing at a route no plugin serves surfaces as an advisory warning rather than a dead `/v1/models` entry.

This is exactly how a RAG-showcase-style consumer retires a bespoke `register_models.py`: declare the approaches and flavor aliases in the manifest and let Atlas own the LiteLLM wiring.

#### 6.3.3. Seeding n8n workflows with `n8n_workflows`

A consumer that ships n8n workflows (e.g. an adaptive-RAG webhook flow) can have Atlas **import, activate, and readiness-check** them instead of hand-writing an import/restart/poll script. Declare a versioned `n8n_workflows` block in `atlas.consumer.yml`:

```yaml
# atlas.consumer.yml
name: rag-showcase
n8n_workflows:
  version: 1
  workflows:
    - id: adaptive-rag                        # stable, consumer-scoped idempotency key
      path: ./n8n/adaptive-rag.workflow.json  # resolved relative to the manifest
      active: "true"                          # fromJson | "true" | "false"
      checksum: "sha256:…"                    # optional integrity pin
      required_webhooks:
        - path: /webhook/adaptive-rag
          method: GET
          expect_status: 200
          probe: true                         # GET/HEAD probes are safe; POST needs explicit probe: true
```

On `./start.sh`, the bootstrapper normalizes each workflow JSON to an **Atlas-namespaced id** `atlas-consumer-<id>` (baking in the activation policy and stripping the runtime-state carriers `staticData`/`pinData`), writes the gitignored `volumes/n8n/consumer-workflows/` + a `plan.json`, and generates a compose overlay that runs an Atlas-owned **`n8n-seed`** container (the n8n image, sharing the n8n schema). After n8n is healthy, the seed imports each workflow with `n8n import:workflow` — **keyed by the namespaced `atlas-consumer-<id>`, so re-running startup updates the workflow in place and never creates a duplicate active workflow**. (The seeder uses **node**, not `wget`, for its API calls: the n8n image is Alpine/BusyBox and its `wget` has no `--method`.)

**What the contract enforces:**

- **Stable, owned, unique ids — auto-namespaced.** `id` is the idempotency key; it is globally unique across consumers and ownership is manifest-derived (a spoofed `owner:` is rejected). The declared `id` is the identity you see in logs, but the **imported DB id is the reserved `atlas-consumer-<id>`** — Atlas owns that id namespace, so a seeded workflow **can never overwrite a workflow an operator built by hand in the UI** (or a stack workflow) even if the ids look the same; you no longer have to hand-namespace to stay clear of user workflows. A removed manifest — or a removed single workflow — drops **only** its own generated artifacts on the next start, and (when `N8N_API_KEY` is set) the seed **reconciles**: any `atlas-consumer-*` workflow no longer declared is deactivated + deleted so a since-removed entry doesn't orphan a live webhook. Another consumer's or a user's workflows are never touched.
- **Credentials never in the workflow JSON.** A node may reference a credential by a `{id, name}` mapping only; a raw string/list value (an inline secret) or a mapping with extra keys (an embedded credential `data` payload) is **rejected at load**, as are the runtime-state carriers `staticData`/`pinData` (stripped during normalization). The generated `plan.json` and overlay carry only ids/paths/statuses — never the workflow body — and the seed logs never print workflow content.
- **Validated up front.** Malformed JSON, a missing file, an `active`/`version` outside the allowed set, a checksum mismatch, and duplicate webhook routes are all rejected at load; [`./start.sh doctor`](#615-consumer-doctor-for-ci-preflight) re-checks the files and warns when an **effectively-active** workflow (respecting `fromJson`) declares webhooks but `N8N_API_KEY` is unset.
- **Webhook readiness (opt-in).** Declared webhooks are probed for readiness after import. A `GET`/`HEAD` probe is safe to issue; a **`POST` probe is opt-in** (`probe: true`) because it can trigger workflow side effects — a POST webhook without it is tracked for route-collision detection but never called.
- **Activation needs no consumer action — with or without an API key.** When `N8N_API_KEY` is set, the seed activates each workflow through the n8n public API (checking the HTTP status and warning on a non-2xx) so its production webhook registers on the **running** instance — no restart. Without a key, n8n CE can't activate over the API: the seed persists `active=true` via `n8n publish:workflow`, but n8n only registers a production webhook when the **server** restarts (a live `active=true` write is ignored by the running process — empirically verified on `n8nio/n8n:2.28.2`). So Atlas performs that one restart of the n8n container after seeding, and the webhook registers with **no consumer-side `docker restart` or manual `publish:workflow`**. (`doctor` still warns if an effectively-active workflow declares webhooks and no key is set, so you know the restart path is in play.)
- **Best-effort, never aborts launch.** A per-workflow import/activation failure is logged and isolated — the `n8n-seed` container always exits 0 — so one bad consumer workflow can't fail a `docker compose up --wait`.

Downstream payoff: `rag-showcase` deletes its `import:workflow` + activate + restart + `/healthz`-poll sequence from `scripts/start-all.sh` and declares the workflow in the manifest.

#### 6.3.4. Declaring RAG ingestion profiles with `rag_ingestion_profiles`

A consumer that ships a RAG corpus (documents to parse, chunk, embed, and load into a vector store + LightRAG) can declare a versioned `rag_ingestion_profiles` block and have **Atlas own the repeatable ingestion lifecycle** — discover → parse → chunk → embed → vector-store write → LightRAG upload → drain → finalize — instead of hand-writing the orchestration and readiness logic around the same Atlas services.

```yaml
# atlas.consumer.yml
name: rag-showcase
rag_ingestion_profiles:
  version: 1
  profiles:
    - name: showcase-default                 # stable, globally-unique profile id
      corpus:
        source: mount                         # mount | minio — NEVER an arbitrary host path
        path: corpus/raw                       # mount: relative, under the backend corpus root
        # bucket / prefix                      # minio: the object prefix to ingest
      parser_order: [docling, tika, plain_text]  # first parser that succeeds wins; plain_text is the always-available fallback
      chunker: { strategy: recursive, chunk_size: 700, overlap: 120 }  # Chonkie strategy
      vector_targets:
        - { backend: weaviate, collection_prefix: RagShowcase, on_unavailable: fail }
      graph_targets:
        - { backend: lightrag, mode: upload_documents, wait_for_extraction: true, timeout_seconds: 3600, on_unavailable: skip }
```

On `./start.sh`, the bootstrapper validates + normalizes each profile, hashes it into a stable **`revision`**, writes the gitignored `volumes/backend/rag-ingestion-profiles.json`, and generates a compose overlay that bind-mounts that file into both Backend and Celery at the reserved internal contract path `/atlas-consumer-config/rag-ingestion-profiles.json`. Both services receive the same `RAG_INGESTION_PROFILES_FILE`, Redis state URL, upstream endpoints, and resource limits. For a MinIO corpus, the bucket must also be declared under the same consumer's `storage.buckets`; Atlas compiles that store's access/secret **variable names** into the profile and injects only those scoped credential references into both services. (The reserved `/atlas-consumer-config/` directory sits *outside* the `/app` source bind — Docker Desktop/VirtioFS rejects nested single-file mountpoints inside a bound directory, #533.) The backend exposes an async job API to submit ingestions headlessly:

```bash
# Submit (async when the Celery tier is enabled, else runs in-request); returns an ingestion id.
curl -XPOST "$BACKEND_URL/api/rag/ingestions" -H 'content-type: application/json' \
     -d '{"profile":"showcase-default"}'
# Poll machine-readable status (phases, counts, timing, per-file errors).
curl "$BACKEND_URL/api/rag/ingestions/<ingestion_id>"
```

Mounted corpora remain operator-owned. Mount the same read-only host directory at `RAG_INGESTION_CORPUS_ROOT` in both execution services so enabling Celery does not change the visible corpus:

```yaml
services:
  backend:
    volumes:
      - ./corpus:/app/corpus:ro
  celery-worker:
    volumes:
      - ./corpus:/app/corpus:ro
```

**What the contract enforces / provides:**

- **No arbitrary host paths or credentials.** A `mount` corpus is a relative path resolved under the shared execution root (`RAG_INGESTION_CORPUS_ROOT`, default `/app/corpus`); an absolute path, a `~`, or a `..` segment is rejected at load and again at runtime. A MinIO bucket/prefix must match a store declared by the same consumer, and the generated runtime profile carries scoped env-var references rather than secret values or root credentials.
- **Bounded corpus discovery.** Mounted files and MinIO objects are read in bounded chunks. Atlas rejects a file over `RAG_INGESTION_MAX_FILE_BYTES` (default 100 MiB), a discovery pass over `RAG_INGESTION_MAX_CORPUS_BYTES` (default 1 GiB aggregate), or a corpus over `RAG_INGESTION_MAX_FILES` (default 10,000) before retaining unbounded content in Backend memory. Tune these manifest-owned limits for a trusted larger corpus instead of disabling the boundary.
- **Exact parser ordering.** Each `parser_order` entry invokes that parser specifically: `docling` does not silently consume the `tika` slot, and `tika` bypasses Docling. Unsupported or unavailable parsers advance to the next declared entry. Discovery and Chonkie execution are offloaded from the API event loop when the Celery tier is disabled.
- **Observable phases.** The job records `discover → parse → chunk → embed → vector_write → lightrag_upload → drain → finalize`, each with status, counts, timing, and a note; a `GET` returns the full machine-readable record.
- **Actionable, redacted failures.** A per-file parse failure records the file, phase, upstream service, and safe status/message and is **isolated** so other files still ingest. Raw Docling/Tika bodies and transport details stay in Backend logs and never enter the public job record. A capability failure or a drain timeout fails the job with a clear message.
- **Capability-gated targets.** Each `vector_target`/`graph_target` declares `on_unavailable: fail | skip`. When the backend's SOURCE is disabled (its endpoint env var is empty), Atlas either fails the job or records a visible **skip** — never silently degrades. `./start.sh doctor` warns up front when an `on_unavailable: fail` target's backend is unset.
- **Idempotent, leased, namespaced, no duplicate writes.** The ingestion key is **consumer + profile + revision + corpus fingerprint**: mounted files are content-hashed and MinIO objects use stable object-version metadata, so changing content at the same path creates a fresh job while an identical re-submit returns the existing one. The store claims that key atomically across concurrent submitters. Each execution then acquires an owner-fenced Redis lease before phase side effects and renews it while running; duplicate Celery deliveries retry after the lease window, and stale owners cannot save. Configure the 10–300 second window with `RAG_INGESTION_EXECUTION_LEASE_SECONDS` (default 30). A broker dispatch failure records a failed job and releases its key for retry; cancellation and terminal status remain monotonic when API and worker updates race. Weaviate classes are namespaced `{collection_prefix}_{profile}` (collisions rejected at load), and objects use a deterministic id so a re-run upserts rather than duplicates. A profile edit also flips the `revision`, forcing a fresh ingestion.
- **Immutable submitted definition.** The durable job stores and returns the effective corpus and complete normalized profile snapshot at submission. Registry regeneration, a later profile revision, or removal of a submitted corpus override cannot change queued work. A confirmed Weaviate create conflict triggers a full replacement of the deterministic object, so changed content updates in place and unrelated schema, property, or vector validation failures remain visible. A post-write profile reconciliation removes stale higher-index chunks and objects for sources absent from the submitted corpus.
- **Fenced execution and independent retries.** Losing a renewable execution lease cancels the stale worker's active async phase and reschedules the job after the lease window. Lease-contention retries are unbounded and do not consume the separate three-retry exponential-backoff budget for transient upstream failures; exhausting that budget records a terminal failed ingestion so a corrected resubmission can create fresh work. LightRAG uploads derive a stable source name from document path and content, making retries idempotent and preventing equal basenames in different directories from colliding.
- **Drain with a timeout.** When a LightRAG target sets `wait_for_extraction: true`, Atlas polls the extraction pipeline until idle or `timeout_seconds`, then finalizes (or fails on timeout).

Live ingestion against running Docling/Tika/Weaviate/LightRAG is an **optional** live test; the unit suite validates the contract, the phase state machine, capability semantics, idempotency, and path safety with fake upstreams.

Downstream payoff: `rag-showcase` keeps owning its datasets, comparison reports, and approach-specific plugins, while Atlas owns the repeatable ingestion lifecycle across documents, vector stores, and LightRAG.

#### 6.3.5. Declaring LightRAG query profiles with `lightrag_query_profiles`

A consumer that runs **side-by-side graph-RAG evaluations** (or wants named UI flavors) can declare a versioned `lightrag_query_profiles` block. Each profile is a **named LightRAG query flavor** — a bundle of the per-query knobs a caller selects by name — so Open WebUI users pick "graph-rag local k=30" versus "graph-rag hybrid k=10" without the downstream app hard-coding mode/top-k/token-budget choices.

```yaml
# atlas.consumer.yml
name: rag-showcase
lightrag_query_profiles:
  version: 1
  profiles:
    - name: graph-hybrid-default              # stable, globally-unique profile id
      mode: hybrid                             # local | global | hybrid | mix | naive
      top_k: 10                                # bounded positive ints; omit → inherit env default
      chunk_top_k: 5
      max_total_tokens: 12000
      enable_rerank: false                     # true requires LIGHTRAG_RERANK_ADAPTER_ENABLED=true (#415 adapter)
    - name: graph-local-wide
      mode: local
      top_k: 30                                # chunk_top_k / max_total_tokens omitted → env default
      query_llm_model: gpt-4o                  # optional model references (a LiteLLM alias / handle)
      litellm_alias: graph-rag-local-wide      # optional: surface this flavor as a LiteLLM model
```

On `./start.sh`, the bootstrapper validates + normalizes each profile, hashes it into a stable **`revision`**, writes the gitignored `volumes/backend/lightrag-query-profiles.json`, and generates a compose overlay that bind-mounts that file into the backend at `/atlas-consumer-config/lightrag-query-profiles.json` (the same reserved contract directory as §6.3.4) and sets `LIGHTRAG_QUERY_PROFILES_FILE` to it. A backend plugin reads the registry to resolve a flavor by name; `./start.sh doctor` reports the registered profiles (and warns when profiles are declared but `LIGHTRAG_ENDPOINT` is unset, so flavors can't yet be served).

**How this differs from role-specific model settings.** The `LIGHTRAG_EXTRACT_*` / `LIGHTRAG_KEYWORD_*` / `LIGHTRAG_QUERY_*` env vars pick **which model runs each LightRAG role** for the single deployment-wide default — one active configuration at a time. A query profile is a **named, per-query flavor** you select at call time; many coexist, so you can compare modes/retrieval bounds across the same corpus without editing Atlas-tracked env. Profiles never replace those env defaults — they layer on top of them (see precedence below).

**What the contract enforces / provides:**

- **Supported modes only.** `mode` is required and must be one of `local | global | hybrid | mix | naive` (LightRAG's `QueryParam.mode`). There is no `LIGHTRAG_QUERY_MODE` env var — mode is runtime-selected — so the profile always states it explicitly.
- **Bounded positive integers.** `top_k`, `chunk_top_k`, and `max_total_tokens` are optional; a present value must be a strictly-positive integer within a sane cap (a YAML boolean or float is rejected). An **omitted** bound is left out of the registry so the backend inherits the deployment `LIGHTRAG_QUERY_*` default.
- **Precedence.** The compiled registry carries an explicit `precedence: [request, profile, service_env_default]` contract: a per-request query parameter overrides the profile, which overrides the service env default. That is how an omitted bound resolves at runtime.
- **Rerank requires the backend adapter (#415).** `enable_rerank: true` is **rejected at load** unless the deployment opts the LightRAG rerank adapter in with `LIGHTRAG_RERANK_ADAPTER_ENABLED=true` (and `TEI_RERANKER_SOURCE` enabled). LightRAG's built-in rerank clients POST `{query, documents}` while TEI's `/rerank` expects `{query, texts}`, so a profile must never point directly at TEI — it reranks through the backend adapter route (`POST /lightrag/rerank`), which translates the two shapes. With the flag off, a rerank-on profile fails fast at load rather than 5xx-ing at query time. See [`services/backend/README.md` §5.1](https://github.com/thekaveh/atlas/blob/main/services/backend/README.md#51-lightrag--tei-rerank-adapter-post-lightragrerank-415). The gate reads the **effective** flag: you can enable the adapter in the **same manifest** via `env.file` / `env.values` (`LIGHTRAG_RERANK_ADAPTER_ENABLED=true`) and declare `enable_rerank: true` together — no pre-editing Atlas's ignored `.env` on a fresh checkout — because a consumer manifest env value overrides the base `.env` per Atlas's documented env merge order. A rerank profile still fails clearly when the merged flag is false or absent (#654).
- **Namespaced + collision-free.** Profile names are globally unique across consumers (rejected at load), and ownership is manifest-derived (a spoofed `owner` is rejected). A removed manifest drops exactly its own profiles next start, so a **deployment with no profiles stays byte- and behavior-compatible** with the single-default LightRAG.
- **No secrets.** The registry contains only flavor knobs and model-name **references** — never credentials.
- **Optional LiteLLM alias (opt-in, not coupled).** A profile that sets `litellm_alias` also emits a consumer-owned [`litellm_models`](#632-exposing-plugin-models-to-litellm-with-litellm_models) row pointing at the backend's profile-aware OpenAI route, so the flavor appears as a selectable model in Open WebUI / LiteLLM. The alias shares the global LiteLLM alias namespace (reserved + cross-consumer collisions rejected). A profile with no `litellm_alias` generates no row.

Downstream payoff: `rag-showcase` moves its graph-RAG flavor definitions out of bespoke code/config into a reusable Atlas profile contract — comparable, documentable, and visible to Open WebUI users.

### 6.4. Consuming auto-managed endpoint variables

Atlas's bootstrapper computes a set of **auto-managed endpoint variables** in `.env` that resolve to the correct internal URL for whichever `*_SOURCE` mode is active. Downstream consumers (whether Method A standalone or Method B submodule) should bridge these into their own service variables rather than hard-coding a URL.

| Variable | Resolved from | Example value (container source) | Example value (localhost source) |
|----------|---------------|----------------------------------|----------------------------------|
| `COMFYUI_ENDPOINT` | `COMFYUI_SOURCE` | `http://comfyui:18188` | `http://host.docker.internal:8000` |
| `OLLAMA_ENDPOINT` | `LLM_PROVIDER_SOURCE` | `http://ollama:11434` | `http://host.docker.internal:11434` |
| `LITELLM_BASE_URL` | locked (always-on; does not vary by source) | `http://litellm:4000` | n/a (locked — no localhost mode) |
| `MINIO_ENDPOINT` | `MINIO_SOURCE` | `http://minio:9000` | n/a (`container`/`disabled` only — no localhost mode) |

`LITELLM_BASE_URL` is the base URL with **no path suffix** — LiteLLM's OpenAI-compatible routes live under `/v1` (e.g. `${LITELLM_BASE_URL}/v1/chat/completions`), so append `/v1` in your client.

**Consumer-bridging pattern.** In your overlay Compose fragment or `services/_user/` service, bridge the auto-managed endpoint into your service's own variable using a three-level fallback:

```yaml
# services/_user/my-app/compose.yml
services:
  my-app:
    environment:
      # Own override → Atlas's computed endpoint → hard-coded in-network default
      MY_COMFYUI_URL: ${MY_COMFYUI_URL:-${COMFYUI_ENDPOINT:-http://comfyui:18188}}
      MY_LITELLM_URL: ${MY_LITELLM_URL:-${LITELLM_BASE_URL:-http://litellm:4000}}  # append /v1 for OpenAI routes
```

This ensures your consumer works transparently across all `*_SOURCE` values (container, localhost, etc.) without per-source branching. The same pattern applies to `OLLAMA_ENDPOINT`, `MINIO_ENDPOINT`, and any future auto-managed endpoint Atlas adds.

### 6.5. Exporting the endpoint contract (`endpoints export`)

For non-Python consumers (web/desktop shells, devservers) and submodule parents
that would otherwise hand-grep `.env`, Atlas emits a **stable, machine-readable
endpoint contract**:

```bash
./start.sh endpoints export --format env    # KEY=value on stdout
./start.sh endpoints export --format json   # JSON object on stdout
```

The field **names are a compatibility contract** — a rename is a breaking change.
For every consumer-relevant service (Backend/Kong, LiteLLM, ComfyUI, Asset Worker,
Ollama, MinIO, Weaviate, Neo4j, n8n, Redis, Supabase) it emits the active SOURCE
mode and each applicable URL as a **distinct named field**:

| Field | Meaning |
|---|---|
| `ATLAS_<SVC>_SOURCE` | active SOURCE mode (a `disabled` service emits only this) |
| `ATLAS_<SVC>_CONTAINER_ENDPOINT` | in-network URL (e.g. `http://minio:9000`) |
| `ATLAS_<SVC>_HOST_ENDPOINT` | host URL (e.g. `http://localhost:63020`) |
| `ATLAS_<SVC>_KONG_ENDPOINT` | Kong `*.localhost` route (when exposed) |
| `ATLAS_<SVC>_PUBLIC_ENDPOINT` | browser-facing public read base (MinIO presigned reads) |

Plus `ATLAS_KONG_GATEWAY` and every per-consumer `ATLAS_STORE_*` field from the
[storage contract](#612-adding-parent-owned-minio-buckets) (#404). Host/Kong URLs
track `BASE_PORT`, and the internal vs public MinIO endpoints are distinct — sign
presigned URLs against `ATLAS_MINIO_PUBLIC_ENDPOINT`.

`ATLAS_<SVC>_HOST_ENDPOINT` is **source-aware** (#643): for host-process sources
the exporter renders the port the host process actually serves on, not the
compose *published* port (which is dead — no container listens — or unset there).
So `COMFYUI_SOURCE=managed-localhost-mps` → `http://localhost:8188`
(`COMFYUI_MPS_LOCALHOST_PORT`), `COMFYUI_SOURCE=localhost` → `http://localhost:8000`
(`COMFYUI_LOCALHOST_PORT`), and `LLM_PROVIDER_SOURCE=ollama-localhost` →
`http://localhost:11434` (`OLLAMA_LOCALHOST_PORT`, a field that was previously
omitted entirely). Container sources keep rendering their published port.

**Guard against contract drift in your CI (`endpoints assert`).** The export
field **names** are a stable compatibility contract, but a future Atlas pin bump
could rename or drop one — and your consumer would then silently read `None` and
fall back to a broken default with no failing test. Assert the fields you depend
on against the pinned submodule, so a rename fails loudly:

```bash
# Fails (exit 1) if any listed field is absent from the current contract:
./start.sh endpoints assert --require ATLAS_LITELLM_HOST_ENDPOINT,ATLAS_MINIO_HOST_ENDPOINT,ATLAS_COMFYUI_HOST_ENDPOINT

# No --require: list the available field names (machine-readable):
./start.sh endpoints assert --format json
```

Run the `--require` form (with the exact fields your code reads) in consumer CI
against your configured stack — it's the recommended drift gate for a vendored
Atlas.

All non-secret exported values are **fully resolved** — the exporter expands any
compose-style `${VAR}` / `${VAR:-default}` interpolation stored in `.env` (e.g. a
host-source `COMFYUI_ENDPOINT` of `http://host.docker.internal:${COMFYUI_MPS_LOCALHOST_PORT:-8188}`)
so the artifact never carries a `${…}` literal a consumer would have to
interpolate itself (#646). Secrets remain `${VAR}` references by design — resolve
consumer-scoped ones with `--with-secrets`.

A few source-specific fields are emitted only when meaningful: under
`COMFYUI_SOURCE=managed-localhost-mps` the export includes
`ATLAS_COMFYUI_OUTPUT_DIR` (the native host process's image-output directory,
`$COMFYUI_MPS_STATE_DIR/ComfyUI/output`, tilde-expanded to an absolute path at
export time so consumers can open it programmatically without shell expansion),
so consumers reading generated images off disk don't hardcode the internal
layout — and its **`ATLAS_COMFYUI_INPUT_DIR`** twin (#758,
`$COMFYUI_MPS_STATE_DIR/ComfyUI/input`) for consumers that *stage* images into
ComfyUI (img2img / img2mesh drivers). Both are omitted for the container and
localhost sources (the dirs are a Docker volume / the user's own ComfyUI
install). Under a Blender host source (`localhost` or `managed-localhost`,
#759) the export includes
**`ATLAS_BLENDER_MCP_HOST_ENDPOINT`** (#758) — note the **`tcp://`** scheme:
the Blender MCP add-on serves a raw TCP socket, not HTTP, so the value is
`tcp://localhost:$BLENDER_MCP_LOCALHOST_PORT` and must be dialed with a socket
client, not an HTTP library.

**Secrets.** By default the output contains **no secret values** — infra secrets
(e.g. the Redis password inside `REDIS_URL`) are emitted as `${VAR}` references.
`--with-secrets` resolves **only consumer-scoped credentials** (the storage
access/secret keys), never infra secrets, and **refuses stdout** — it requires an
explicit `--output PATH`:

```bash
# Submodule parent: capture the contract next to the parent app on every bring-up
(cd infra && ./start.sh endpoints export --format env --output ../atlas-consumer.env)
```

Output is deterministic and byte-stable for the same inputs, so a consumer's
overlay doctor can diff it across runs. This replaces per-consumer `.env`
grepping and the hand-maintained endpoint/URL-rewrite bridges.

---

## 7. Consumer adoption runbook (the full journey)

Sections 3–6 give you the *mechanisms*; this runbook composes them into the one
journey every downstream product walks — **declare a manifest → pin instance
identity → select sources → validate → start → export endpoints → operate
day-2** — and calls out four operational behaviors that consumers (Tableau,
DayDreams) each learned from a real incident. It links to the per-topic docs
rather than repeating them.

### 7.1. The journey in order

> **New consumer, empty repo?** The [§4.1 ordered walkthrough](#41-stand-up-a-consumer-from-scratch--the-ordered-walkthrough) runs this journey end-to-end with copy-pasteable commands. This runbook is the day-2 reference for the same steps and the operational behaviors behind them.

1. **Register** a parent manifest — [§6.1](#61-registering-a-parent-project-with-atlasconsumeryml) (`atlas.consumer.yml`).
2. **Pin identity** (`PROJECT_NAME` + a distinct `BASE_PORT`) durably in the manifest — §7.2 — never the default `63000`; `doctor` warns if a non-default project is left on it. Set **`BASE_PORT: auto`** in the manifest to have Atlas reserve a distinct free block per consumer and keep it stable across restarts (best for several consumers on one host, §7.4), or commit a fixed number. (`--base-port auto` at launch is the one-off, resolve-fresh form.)
3. **Select sources** once (`container` / `localhost` / `managed-localhost-mps` / `ollama-localhost` / `none`) — §7.3, [source-configuration.md](source-configuration.md). Gate a paid provider on its key with the manifest's key-gated [`enabled_if_env`](#61-registering-a-parent-project-with-atlasconsumeryml) form instead of a wrapper script.
4. **Validate** headlessly — `env backfill` + `compose validate` + `doctor` ([operations.md](../operations.md); [§6.1.4](#614-headless-submodule-upgrade-validation) / [§6.1.5](#615-consumer-doctor-for-ci-preflight)). `doctor` also lints the default-`63000` squat and **declared-but-unpullable** model provisioning (`model_sidecars.ollama` / `COMFYUI_USER_MODELS` under a `*-localhost` source).
5. **Start** — `./start.sh --consumer … --base-port auto` (first run may pass source flags; see §7.3). Your consumer LiteLLM models are discoverable in `/v1/models` on start with **no `docker restart`**, and declared n8n workflows activate even **without an `N8N_API_KEY`** — Atlas performs any restart the webhook needs. Do **not** script an admin-API call or a container restart to "pick up" model/workflow changes.
6. **Export + assert endpoints** for your app — [§6.5](#65-exporting-the-endpoint-contract-endpoints-export). Add **`endpoints assert --require …`** to your CI so an Atlas pin bump can't silently drop a field your code reads.
7. **Operate day-2** — multi-instance isolation (§7.4), host-service coexistence (§7.5), upgrades (§7.6), verification (§7.7).

### 7.2. Pin instance identity in the manifest, not just `.env`

`PROJECT_NAME` and `BASE_PORT` are your instance's identity. `.env` is
**machine-local and disposable** — a cold start (`./stop.sh --cold` /
`./start.sh --cold`) regenerates it from `.env.example`. `PROJECT_NAME` survives
that regeneration (Atlas re-persists the previous value), but a non-default
**`BASE_PORT` resets to the `63000` default** unless something re-supplies it —
so an instance that carried its port block only in `.env` silently loses it on
the next cold start and collides with any parallel stack (§7.4).

The durable fix: commit identity in the consumer manifest's **`env.values`**
block, which is re-applied to `.env` on **every** start (warm and cold), before
ports are resolved:

```yaml
# atlas.consumer.yml
project_name: tableau          # top-level key → PROJECT_NAME
env:
  values:
    BASE_PORT: "63000"         # re-applied every start; survives cold .env regen
```

**Inverse rule — do NOT put machine-specific *scalars* in `env.values`.** A
manifest scalar re-applies every start and clobbers temporary operator switches,
so OS-specific paths (e.g. `COMFYUI_MPS_MODELS_PATH`) belong in `.env` (or
`.env.user`), not the committed manifest. For **source selections** the right
committed form is the **`auto` sentinel** (§6.1): `COMFYUI_SOURCE: auto`
resolves per host and never clobbers a **non-default** operator override (an
override to the service default id is indistinguishable from a cold regen and
re-resolves — commit the concrete id to pin the default; §6.1) — unlike a
committed concrete id, which re-applies every start. Keep concrete `env.values` to the
identity and branding that should be identical on every machine.

### 7.3. Select sources once; keep `.env` as the source of truth

Every `--<svc>-source` CLI flag is **persisted into `.env` as an explicit
override, silently** — there is no "you changed a previously-set value" warning.
That turns an innocent wrapper script into a footgun: a launcher that runs

```bash
./start.sh --comfyui-source "${COMFYUI_SOURCE:-container-cpu}"   # re-asserts every start
```

re-applies `container-cpu` on **every** restart, silently reverting a
hand-configured `COMFYUI_SOURCE=managed-localhost-mps` and demoting image
generation from Metal to an unusable CPU container with no error anywhere
(observed live, 2026-07-13).

**Rule:** pass source flags on **first run only**; treat `.env` as the source of
truth thereafter (edit `.env` or the manifest, not the launch command).
`managed-localhost-mps` is now a valid `--comfyui-source` value, so it can be
selected on first run or set directly in `.env`; its managed lifecycle
(preflight / install / **provision** / start / status / stop, `COMFYUI_MPS_*`
vars) is documented in the ComfyUI service README §10
(`services/comfyui/README.md`). Declared `COMFYUI_USER_MODELS` are
**auto-provisioned** into `COMFYUI_MPS_MODELS_PATH` on start (#754) — no manual
weight staging; the `unpullable-models` doctor lint passes once the host tree
satisfies the declared catalog. The Blender bridge has the same managed shape:
`BLENDER_MCP_SOURCE=managed-localhost` provisions the pinned add-on and runs
headless Blender (lifecycle `./start.sh blender-mcp …`; loopback-only; see
`services/blender-mcp/README.md`).

**Better: skip the first-run flags entirely with `auto`.** Committing
`COMFYUI_SOURCE: auto` / `LLM_PROVIDER_SOURCE: auto` in the manifest (§6.1)
removes the ritual: every start — including after a cold `.env` regen — resolves
the host-correct source, keeps a prior resolution, and honors any explicit
**non-default** `--<svc>-source` override durably (an override to the service
default id is indistinguishable from a cold regen and re-resolves — commit the
concrete id to pin the default; §6.1). The wrapper-script footgun above cannot
happen, because there is no flag to re-assert.

### 7.4. Run multiple Atlas instances on one host

**Container names, networks, and volumes are `${PROJECT_NAME}-*` —
project-isolated. Host-published ports are not: they are fixed offsets from
`BASE_PORT` and carry no project namespace.** So a second instance on the same
host **must** have **both** a distinct `project_name` and a distinct `BASE_PORT`
(both persist to `.env`), never the default `63000`.

For **committed consumers that run side by side** (the common case — several
Atlas-backed products on one dev box), set **`BASE_PORT: auto` in each
`atlas.consumer.yml`**. Atlas reserves a distinct free `BASE_PORT+0..99` block per
consumer (the allocator steps by 100 — the topology's max port offset is 99) —
skipping `63000` **and** any block whose ports are already in use by
another running stack — then **persists it and keeps it** across restarts. So
three consumers started in turn each land on their own **durable** block
(`20000`, `20100`, `20200`, …) with no numbers to coordinate and no drift on a
warm restart. A cold start re-resolves, still skipping occupied blocks. For an
**ad-hoc** stack, pass **`--base-port auto`** at launch (resolves a free block
*fresh* each run). Either way you never hand-pick a number or squat the default;
`doctor` warns if a non-default project is left on `63000`.

```bash
# Committed consumer — BASE_PORT: auto in the manifest gives each a distinct,
# durable block; the launcher needs no port flag:
./start.sh --consumer ./atlas.consumer.yml --project daydreams \
  --llm-provider-source ollama-localhost --comfyui-source localhost

# Ad-hoc stack — resolve a free block fresh at launch:
./start.sh --project daydreams --base-port auto \
  --llm-provider-source ollama-localhost --comfyui-source localhost

# ...or pin an explicit distinct base port:
./start.sh --project daydreams --base-port 64000 \
  --llm-provider-source ollama-localhost --comfyui-source localhost
```

**Failure mode if you reuse a base port** (distinct `PROJECT_NAME`, same
`BASE_PORT`): the two stacks interleave on one host-port range. Container names
stay isolated, so everything *looks* healthy — but you get partial binds and
**silent cross-instance traffic**: the exported `atlas-consumer.env` records,
say, `LITELLM :63040` while the *other* instance's LiteLLM is what answers there.
It is not a clean error. The interactive wizard's conflict check catches this;
scripted / non-interactive launches do not, so choose a distinct base per
instance up front. Port topology: [ports-and-routes.md](ports-and-routes.md).

### 7.5. Coexist with host-run services (`*-localhost` sources)

When the host already runs Ollama, ComfyUI, or Blender, point Atlas at them
instead of starting duplicates:

| Source flag | Effect | Host prerequisite |
|---|---|---|
| `--llm-provider-source ollama-localhost` | No `*-ollama` container; LiteLLM upstream → `host.docker.internal:11434`; catalog auto-imported from the host's `/api/tags` | Host Ollama on `:11434` ([source-configuration.md](source-configuration.md) §4.1.1.3) |
| `--comfyui-source localhost` | No `*-comfyui` container; endpoint → `host.docker.internal:${COMFYUI_LOCALHOST_PORT:-8000}` | Host ComfyUI on `COMFYUI_LOCALHOST_PORT` |
| `--comfyui-source managed-localhost-mps` | Atlas-managed Metal-native ComfyUI process on `${COMFYUI_MPS_LOCALHOST_PORT:-8188}` | macOS / Apple Silicon; ComfyUI README §10 |

**Warning:** the default `--llm-provider-source ollama-container-cpu` starts a
containerized Ollama **next to** the host's Ollama, double-loading models and
contending for GPU/RAM on single-GPU machines. On a host that already runs
Ollama, prefer `ollama-localhost`.

### 7.6. Upgrades — warm starts do not rebuild local images

A warm `./start.sh` recreates containers (`--force-recreate`) but **never
rebuilds** them; the local image build (`compose build --no-cache`) runs **only
on `--cold`**. So after you advance your Atlas submodule pin, a plain warm
restart keeps running the **stale** locally-built images (backend, asset-worker,
…) with no indication — e.g. a backend fix silently absent because the old image
is still live (this bit the `up_axis` fix in #524, ignored by the pre-bump image
without error).

**After moving the pin, rebuild:** cold-start (`./stop.sh --cold && ./start.sh`)
or rebuild the affected local-build services with
`docker compose build --no-cache <svc>` before a warm start. Tracked as #506.

### 7.7. Post-launch verification

Trust `docker ps`, not the banner. A copy-pasteable check for `<project>` at
base port `<BASE>`:

```bash
docker ps --filter "name=<project>-"          # every container prefixed with your project
# expect: every published host port in <BASE>..<BASE>+99
#         zero <project>-ollama* containers when using ollama-localhost
#         the OTHER instance's containers untouched
./start.sh endpoints export --format env      # ATLAS_*_HOST_ENDPOINT ports match <BASE>
./start.sh doctor --format json               # manifest + base-port + unpullable-model lints (0 warn)
./start.sh endpoints assert --require \
  ATLAS_LITELLM_HOST_ENDPOINT,ATLAS_MINIO_HOST_ENDPOINT   # the export fields your code reads
```

**Wire the last two into consumer CI.** `doctor` (manifest validity + the
default-port / unpullable-model lints) and `endpoints assert --require <the
fields you read>` are the standing drift gates: run them against your configured
stack on every pin bump so an upstream change fails your build loudly instead of
degrading the running consumer. See [§4.1 step 8](#41-stand-up-a-consumer-from-scratch--the-ordered-walkthrough).

**Known cosmetic caveat.** A `--detach` / non-TTY start can print
`[ERROR] <svc>: starting, exit code 0` and `Failed to start some services` while
the containers report **healthy** seconds later — the launcher takes a single
`compose ps` snapshot and treats `Health=starting` as failure. Until the
classifier is fixed, verify with `docker ps` before trusting that banner; a
genuine failure shows a non-zero exit code or a container that never reaches
`healthy`.

---

## 8. Readiness

| Capability | Status |
|------------|--------|
| Standalone + shared-network consumer (Method A) | **Ready** |
| Git submodule (Method B) | **Ready** ([submodule-usage.md](submodule-usage.md)) |
| Customization: `PROJECT_NAME` / `BASE_PORT` / `BRAND_*` / `*_SOURCE` / `--track` | **Ready** |
| Multiple isolated Atlas stacks on one host | **Ready** (distinct `PROJECT_NAME` + `BASE_PORT` — see [§7.4](#74-run-multiple-atlas-instances-on-one-host)) |
| `services/_user/` overlay **auto-launch** | **Ready** — drop `services/_user/<name>/compose.yml` and the bootstrapper merges + launches it (see [§6.1.1](#611-back-compatible-services_user-overlay-slot)). |
| Semver release tags for submodule pinning | **Ready** — the repo is tagged `vMAJOR.MINOR.PATCH`; pin your submodule to a tag (see [releasing.md](releasing.md)). |
| Published images / pip package | **Not supported** (see §5) |

The first two rows were Phase 1 of the production-readiness & reuse roadmap — now implemented (see the [Phase 1 design](../superpowers/specs/2026-06-21-phase1-reuse-mechanics-design.md)). Remaining roadmap items (Infisical secrets, centralized logging, image signing, deeper hardening) are Phase 2+.

---

## 9. See also

- [submodule-usage.md](submodule-usage.md) — complete Git-submodule guide (layout, integration patterns, CI/CD, troubleshooting)
- [source-configuration.md](source-configuration.md) — every `*_SOURCE` variable and what it does
- [ports-and-routes.md](ports-and-routes.md) — authoritative port + Kong-hostname mapping
- [releasing.md](releasing.md) — version-tag convention for pinning a submodule
- [Production readiness & reuse roadmap](../superpowers/specs/2026-06-20-production-readiness-and-reuse-roadmap-design.md) — the strategy/assessment behind this guide
