# Reusing Atlas as Infrastructure

How to use Atlas as the backing infrastructure / platform for another project — for example a RAG-showcase app that needs Weaviate + Neo4j + an LLM gateway + object storage without standing those up itself.

This page is the **overview and decision guide**. It answers: *can I reuse Atlas, which method should I pick, is it ready, how do I wire my project to it, and how do I customize it?* For the full step-by-step of the Git-submodule method specifically, see [submodule-usage.md](submodule-usage.md).

---

## 1. TL;DR

- **Yes, Atlas is designed to be reused** as shared infra for other projects. The whole stack is namespaced by `PROJECT_NAME`, its ports move as a block via `BASE_PORT`, every service is toggleable via `*_SOURCE`, and all containers share one Docker network (`${PROJECT_NAME}-network`) that your project can join.
- **Two methods are ready today:**
  - **A — Standalone + shared network** (recommended when one Atlas instance backs *several* of your projects): run Atlas on its own; your project is a *separate* repo / Compose project that joins `${PROJECT_NAME}-network` and calls services by their Docker DNS name (or through Kong).
  - **B — Git submodule** (recommended when your project *ships and deploys Atlas together with it*): vendor Atlas into your repo under `infra/` and run it from there. Fully documented in [submodule-usage.md](submodule-usage.md).
- **Customization needs no fork:** `PROJECT_NAME`, `BASE_PORT`, `BRAND_*`, per-service `*_SOURCE`, and `--track` cover the common cases.
- **Honest status:** the consumer paths above work today; services dropped into the `services/_user/` overlay now **launch automatically** (see [§6.1.1](#611-back-compatible-services_user-overlay-slot)); and the repo is **tagged** for submodule pinning. See [§7 Readiness](#7-readiness).

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

## 3. Method A — Standalone + shared network (the RAG-showcase walkthrough)

Atlas runs as its own stack. Your RAG project is a separate Compose project that attaches to Atlas's network and addresses services by container DNS name. Nothing in your app repo needs to know Atlas's internals beyond the service hostnames.

### 3.1 Step 1 — Run Atlas with a known `PROJECT_NAME`

```bash
# In your Atlas checkout
./start.sh --llm-provider-source none --cloud-openai-source enabled --openai-api-key sk-...   # cloud LLMs, no local GPU
# (or any track/source combination your showcase needs, e.g. --track gen-ai-rag)
```

`PROJECT_NAME` (default `atlas`) determines the shared network name: **`${PROJECT_NAME}-network`** (e.g. `atlas-network`). Set it in Atlas's `.env` if you want a non-default name.

### 3.2 Step 2 — Join Atlas's network from your project

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

### 3.3 Service addresses (inside the shared network)

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

### 3.4 Going through Kong instead (single entry point)

If you'd rather not depend on individual service hostnames, route through Kong — Atlas's gateway — at `kong-api-gateway:8000`. Supabase REST is path-routed (`/rest/v1/...`); browser-facing services are host-routed (`<service>.localhost`). The Kong patterns, including the auth headers, are documented in [submodule-usage.md §6.2](submodule-usage.md#62-pattern-2-kong-gateway-as-single-entry-point).

---

## 4. Method B — Git submodule

Vendor Atlas into your repo and run it from a subdirectory — best when your project and its infra ship as one versioned, reproducible unit.

```bash
git submodule add https://github.com/thekaveh/atlas infra
cd infra && git checkout v0.1.0       # pin to a release tag — see releasing.md
cp .env.example .env                   # set PROJECT_NAME to your project
./start.sh
```

Pin the submodule to a release **tag** rather than tracking `main`, so infra upgrades are explicit, reviewable commits — see [releasing.md](releasing.md) for the tag convention. This is the same shared-network model as Method A (your app joins `${PROJECT_NAME}-network`), with the difference that Atlas's source lives inside your repo at a pinned commit. The **complete** guide — directory layout, `.gitignore`, custom env-file location, integration patterns, contributing upstream, CI/CD, multiple stacks, troubleshooting — is [submodule-usage.md](submodule-usage.md).

---

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
| **`BASE_PORT`** | Moves the entire host-published port block (default `63000`). `./start.sh --base-port 64000`. Does not affect in-network addresses. | `.env` / flag |
| **`BRAND_*`** | Rebrands the wizard/banner (name, tagline, author, repo URL, license) — make Atlas present as your platform. | `.env` (`BRAND_*` block) |
| **`*_SOURCE`** | Enable/disable each service or pick its backend (`container` / `container-gpu` / `localhost` / `disabled`). LLMs use `ollama-container-*` / `ollama-localhost` / `none`; cloud providers toggle via the separate `CLOUD_*_SOURCE` vars. Disable what your showcase doesn't use. | `.env` / `--<svc>-source` |
| **`--track`** | Start a curated subset (`gen-ai-rag`, `gen-ai-eng`, `gen-ai-creative`, `ml-eng`, `data-eng`, `trading`, `all`). `--track gen-ai-rag` is the natural fit for a RAG showcase. Explicit `--<service>-source` flags override track membership, which lets parent wrappers request one extra service outside the track or disable a track-prompted service. | flag |
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

### 6.1 Registering a parent project with `atlas.consumer.yml`

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
  repo_url: "https://github.com/example/rag-showcase"
env:
  file: ./atlas.env.user
  values:
    WEAVIATE_MEMORY_LIMIT: 4g
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

### 6.1.1 Back-compatible `services/_user/` overlay slot

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

### 6.1.2 Adding parent-owned MinIO buckets

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

### 6.1.3 Scripted bring-up for automation

For CI, cron, or parent-repo wrapper scripts, use the non-interactive detached
path instead of backgrounding `start.sh` and killing it after a hand-written
health poll:

```bash
./start.sh --no-tui --detach
```

`--detach` is also available as `--no-follow`. It runs the normal Atlas start
pipeline, starts Compose in detached mode with its health wait enabled, prints a
per-service status summary, and exits with `0` only when the final status
summary is healthy. Add `--json` when a parent script needs machine-readable
status:

```bash
./start.sh --no-tui --detach --json
```

### 6.1.4 Headless submodule upgrade validation

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
printing Compose's raw stderr for debugging.

Exit codes:

- `env backfill` exits `0` when the env file is already current or was updated
  successfully, and `1` if the backfill write fails.
- `compose validate` exits `0` when Compose accepts the assembled stack, and
  otherwise exits with Compose's failing status code.

### 6.1.5 Consumer doctor for CI preflight

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

### 6.2 Adding Supabase SQL via the user migration slot

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

### 6.3 Adding backend API routes via the plugin seam

The FastAPI backend exposes a **generic plugin seam** so you can mount your own API routes *into* it without forking `services/backend/`. On startup the backend calls `load_plugins(app)`, which scans `$BACKEND_PLUGINS_DIR` (default `/app/plugins`). An optional shared `$BACKEND_PLUGINS_DIR/requirements.txt` is installed first; then, for each immediate subdirectory that is an importable Python package exposing a module-level `router` (a FastAPI `APIRouter`), that plugin package's own optional `requirements.txt` is installed before the package is imported and included into the running app. A plugin whose requirements fail to install is logged with the requirements path and pip output, then skipped before import; a shared requirements failure skips plugin loading for that startup. The seam is a **no-op when the directory doesn't exist** (so base Atlas is unaffected), and a plugin that fails to import is logged and skipped — one bad plugin never crashes the backend.

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

#### 6.3.1 Declaring a typed plugin contract with `plugin.yml`

A plugin package MAY ship an optional **`plugin.yml`** next to its `__init__.py`. Absent → the plugin loads exactly as above (fully backward compatible). Present → it declares a **versioned, typed, validated contract** so operators can *see* what is mounted and what env it needs, a missing/typo'd var surfaces as a startup **diagnostic** instead of a runtime 500, and per-plugin Kong auth has a place to live:

```yaml
# my-plugins/tableau/plugin.yml
plugin_manifest_version: 1
name: tableau                       # unique, kebab-case
route_prefix: /tableau             # must not overlap another plugin or a built-in route
health_path: /tableau/health
docs_url: https://github.com/thekaveh/tableau
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
- **Fail-fast, isolated.** A present-but-malformed `plugin.yml` does **not** degrade to manifest-less loading — that one plugin is **skipped** with a structured error and the others stay healthy. Duplicate plugin names, overlapping prefixes, and prefixes that shadow a built-in backend route (`api`, `comfyui`, `documents`, `health`, `jobs`, `media`, `memory`, `plugins`, `research`, `storage`, `workflows`) are rejected before mounting.
- **Per-plugin Kong auth.** `auth: key-auth` puts Kong key-auth on that plugin's `route_prefix`; `auth: open` opts a prefix out even when the backend default (`BACKEND_KONG_AUTH`) is `key-auth`; `auth: inherit` follows the default. Atlas composes these into route-level Kong policies so an `open` prefix is not weakened by a `key-auth` default and vice versa (base Atlas, with no plugins, emits the historical single backend route unchanged). Per-prefix `key-auth` reuses the `BACKEND_KONG_API_KEY` credential; distinct per-prefix credentials remain a future extension.

The `plugin_manifest_version` is a hard-pinned contract version — a manifest built for a version this backend does not understand is skipped rather than mis-read. The canonical schema is [`bootstrapper/schemas/plugin.schema.json`](https://github.com/thekaveh/atlas/blob/main/bootstrapper/schemas/plugin.schema.json).

#### 6.3.2 Exposing plugin models to LiteLLM with `litellm_models`

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

**What the contract enforces:**

- **Ownership is derived from the manifest, not spoofable.** Every generated row is stamped with `model_info.atlas_owner: <consumer>`. An explicit `owner:` may only restate the consumer's own name — claiming another's is rejected. A removed manifest removes **only that consumer's** rows on the next start; stack rows and sibling-consumer rows are untouched.
- **Approved endpoints only.** `api_base` is resolved against an allowlist of in-network Atlas endpoint templates — currently `${ATLAS_BACKEND_INTERNAL}` (`http://backend:8000`, where §6.3 plugins mount). It must resolve to a **clean base URL** (`scheme://host/path`): an arbitrary external host, an unapproved `${...}` template, leftover unresolved interpolation, userinfo credentials (`user:pass@…`), or **any query string or fragment** (the usual carrier for `?authorization=…`, `?api_key=…`, `#token`) is **rejected at load**. So a generated LiteLLM row can never exfiltrate to an off-stack host or carry a secret into the config file or startup log.
- **Secrets stay references.** Use `api_key_var` (an `UPPER_SNAKE` env var name), never a literal `api_key`. Atlas renders `api_key: os.environ/<VAR>` (the same form as the stack `hermes-agent` row) and generates a compose overlay that passes that var into the `litellm` container so it resolves at request time. The secret **value** appears in no generated file, overlay, log, or doctor output.
- **Unique, non-reserved aliases.** Aliases are globally unique across all consumers (one generated config) and may not shadow a stack-owned alias — that reserved set is **catalog-derived**: the runtime rows (`hermes-agent`, `lightrag`) *and* every YAML-catalog model name (`gpt-4o`, `nomic-embed-text`, …), rejected up front. As a last-line defense, `litellm-init` also **skips** any consumer row whose alias collides with an already-rendered stack model, so a stack model can never be silently hijacked (LiteLLM would otherwise load-balance duplicate `model_name`s across both endpoints).
- **Preflight cross-check.** [`./start.sh doctor`](#615-consumer-doctor-for-ci-preflight) validates the block and cross-checks each backend-hosted model's first route segment against the declared `plugin.yml` `route_prefix`es (§6.3.1) — a model pointing at a route no plugin serves surfaces as an advisory warning rather than a dead `/v1/models` entry.

This is exactly how a RAG-showcase-style consumer retires a bespoke `register_models.py`: declare the approaches and flavor aliases in the manifest and let Atlas own the LiteLLM wiring.

#### 6.3.3 Seeding n8n workflows with `n8n_workflows`

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
- **Activation without a restart, when possible.** When `N8N_API_KEY` is set, the seed activates each workflow through the n8n public API (checking the HTTP status and warning on a non-2xx) so its production webhook registers on the **running** instance. Without a key the workflow is still imported and its active state persisted; the production webhook then registers on the next n8n restart (the doctor surfaces this).
- **Best-effort, never aborts launch.** A per-workflow import/activation failure is logged and isolated — the `n8n-seed` container always exits 0 — so one bad consumer workflow can't fail a `docker compose up --wait`.

Downstream payoff: `rag-showcase` deletes its `import:workflow` + activate + restart + `/healthz`-poll sequence from `scripts/start-all.sh` and declares the workflow in the manifest.

#### 6.3.4 Declaring RAG ingestion profiles with `rag_ingestion_profiles`

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

On `./start.sh`, the bootstrapper validates + normalizes each profile, hashes it into a stable **`revision`**, writes the gitignored `volumes/backend/rag-ingestion-profiles.json`, and generates a compose overlay that bind-mounts that file into the backend and sets `RAG_INGESTION_PROFILES_FILE`. The backend exposes an async job API to submit ingestions headlessly:

```bash
# Submit (async when the Celery tier is enabled, else runs in-request); returns an ingestion id.
curl -XPOST "$BACKEND_URL/api/rag/ingestions" -H 'content-type: application/json' \
     -d '{"profile":"showcase-default"}'
# Poll machine-readable status (phases, counts, timing, per-file errors).
curl "$BACKEND_URL/api/rag/ingestions/<ingestion_id>"
```

**What the contract enforces / provides:**

- **No arbitrary host paths.** A `mount` corpus is a relative path resolved **under the backend corpus root** (`RAG_INGESTION_CORPUS_ROOT`, default `/app/corpus`); an absolute path, a `~`, or a `..` segment is rejected at load and again at runtime. The only other input mode is a MinIO bucket/prefix.
- **Observable phases.** The job records `discover → parse → chunk → embed → vector_write → lightrag_upload → drain → finalize`, each with status, counts, timing, and a note; a `GET` returns the full machine-readable record.
- **Actionable failures.** A per-file parse failure is recorded (file, phase, upstream service, HTTP status/body) and **isolated** — other files still ingest. A capability failure or a drain timeout fails the job with a clear message.
- **Capability-gated targets.** Each `vector_target`/`graph_target` declares `on_unavailable: fail | skip`. When the backend's SOURCE is disabled (its endpoint env var is empty), Atlas either fails the job or records a visible **skip** — never silently degrades. `./start.sh doctor` warns up front when an `on_unavailable: fail` target's backend is unset.
- **Idempotent, namespaced, no duplicate writes.** The ingestion key is **consumer + profile + revision + corpus digest**: an identical re-submit returns the existing job without re-running it. Weaviate classes are namespaced `{collection_prefix}_{profile}` (collisions rejected at load), and objects use a deterministic id so a re-run upserts rather than duplicates. A profile edit flips the `revision`, forcing a fresh ingestion.
- **Drain with a timeout.** When a LightRAG target sets `wait_for_extraction: true`, Atlas polls the extraction pipeline until idle or `timeout_seconds`, then finalizes (or fails on timeout).

Live ingestion against running Docling/Tika/Weaviate/LightRAG is an **optional** live test; the unit suite validates the contract, the phase state machine, capability semantics, idempotency, and path safety with fake upstreams.

Downstream payoff: `rag-showcase` keeps owning its datasets, comparison reports, and approach-specific plugins, while Atlas owns the repeatable ingestion lifecycle across documents, vector stores, and LightRAG.

#### 6.3.5 Declaring LightRAG query profiles with `lightrag_query_profiles`

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
      enable_rerank: false                     # true is rejected until the #415 adapter lands
    - name: graph-local-wide
      mode: local
      top_k: 30                                # chunk_top_k / max_total_tokens omitted → env default
      query_llm_model: gpt-4o                  # optional model references (a LiteLLM alias / handle)
      litellm_alias: graph-rag-local-wide      # optional: surface this flavor as a LiteLLM model
```

On `./start.sh`, the bootstrapper validates + normalizes each profile, hashes it into a stable **`revision`**, writes the gitignored `volumes/backend/lightrag-query-profiles.json`, and generates a compose overlay that bind-mounts that file into the backend and sets `LIGHTRAG_QUERY_PROFILES_FILE`. A backend plugin reads the registry to resolve a flavor by name; `./start.sh doctor` reports the registered profiles (and warns when profiles are declared but `LIGHTRAG_ENDPOINT` is unset, so flavors can't yet be served).

**How this differs from role-specific model settings.** The `LIGHTRAG_EXTRACT_*` / `LIGHTRAG_KEYWORD_*` / `LIGHTRAG_QUERY_*` env vars pick **which model runs each LightRAG role** for the single deployment-wide default — one active configuration at a time. A query profile is a **named, per-query flavor** you select at call time; many coexist, so you can compare modes/retrieval bounds across the same corpus without editing Atlas-tracked env. Profiles never replace those env defaults — they layer on top of them (see precedence below).

**What the contract enforces / provides:**

- **Supported modes only.** `mode` is required and must be one of `local | global | hybrid | mix | naive` (LightRAG's `QueryParam.mode`). There is no `LIGHTRAG_QUERY_MODE` env var — mode is runtime-selected — so the profile always states it explicitly.
- **Bounded positive integers.** `top_k`, `chunk_top_k`, and `max_total_tokens` are optional; a present value must be a strictly-positive integer within a sane cap (a YAML boolean or float is rejected). An **omitted** bound is left out of the registry so the backend inherits the deployment `LIGHTRAG_QUERY_*` default.
- **Precedence.** The compiled registry carries an explicit `precedence: [request, profile, service_env_default]` contract: a per-request query parameter overrides the profile, which overrides the service env default. That is how an omitted bound resolves at runtime.
- **Rerank stays off until an adapter exists.** `enable_rerank: true` is **rejected at load** — LightRAG's built-in rerank clients and TEI's `/rerank` payload are incompatible, so a rerank-on profile would fail at query time. It becomes valid once the compatible adapter endpoint (#415) is active; a profile must never point directly at TEI.
- **Namespaced + collision-free.** Profile names are globally unique across consumers (rejected at load), and ownership is manifest-derived (a spoofed `owner` is rejected). A removed manifest drops exactly its own profiles next start, so a **deployment with no profiles stays byte- and behavior-compatible** with the single-default LightRAG.
- **No secrets.** The registry contains only flavor knobs and model-name **references** — never credentials.
- **Optional LiteLLM alias (opt-in, not coupled).** A profile that sets `litellm_alias` also emits a consumer-owned [`litellm_models`](#632-exposing-plugin-models-to-litellm-with-litellm_models) row pointing at the backend's profile-aware OpenAI route, so the flavor appears as a selectable model in Open WebUI / LiteLLM. The alias shares the global LiteLLM alias namespace (reserved + cross-consumer collisions rejected). A profile with no `litellm_alias` generates no row.

Downstream payoff: `rag-showcase` moves its graph-RAG flavor definitions out of bespoke code/config into a reusable Atlas profile contract — comparable, documentable, and visible to Open WebUI users.

### 6.4 Consuming auto-managed endpoint variables

Atlas's bootstrapper computes a set of **auto-managed endpoint variables** in `.env` that resolve to the correct internal URL for whichever `*_SOURCE` mode is active. Downstream consumers (whether Method A standalone or Method B submodule) should bridge these into their own service variables rather than hard-coding a URL.

| Variable | Resolved from | Example value (container source) | Example value (localhost source) |
|----------|---------------|----------------------------------|----------------------------------|
| `COMFYUI_ENDPOINT` | `COMFYUI_SOURCE` | `http://comfyui:18188` | `http://host.docker.internal:8000` |
| `OLLAMA_ENDPOINT` | `LLM_PROVIDER_SOURCE` | `http://ollama:11434` | `http://host.docker.internal:11434` |
| `LITELLM_BASE_URL` | `LLM_PROVIDER_SOURCE` | `http://litellm:4000/v1` | `http://host.docker.internal:63004/v1` |
| `MINIO_ENDPOINT` | `MINIO_SOURCE` | `http://minio:9000` | `http://host.docker.internal:63020` |

**Consumer-bridging pattern.** In your overlay Compose fragment or `services/_user/` service, bridge the auto-managed endpoint into your service's own variable using a three-level fallback:

```yaml
# services/_user/my-app/compose.yml
services:
  my-app:
    environment:
      # Own override → Atlas's computed endpoint → hard-coded in-network default
      MY_COMFYUI_URL: ${MY_COMFYUI_URL:-${COMFYUI_ENDPOINT:-http://comfyui:18188}}
      MY_LITELLM_URL: ${MY_LITELLM_URL:-${LITELLM_BASE_URL:-http://litellm:4000/v1}}
```

This ensures your consumer works transparently across all `*_SOURCE` values (container, localhost, etc.) without per-source branching. The same pattern applies to `OLLAMA_ENDPOINT`, `MINIO_ENDPOINT`, and any future auto-managed endpoint Atlas adds.

### 6.5 Exporting the endpoint contract (`endpoints export`)

For non-Python consumers (web/desktop shells, devservers) and submodule parents
that would otherwise hand-grep `.env`, Atlas emits a **stable, machine-readable
endpoint contract**:

```bash
./start.sh endpoints export --format env    # KEY=value on stdout
./start.sh endpoints export --format json   # JSON object on stdout
```

The field **names are a compatibility contract** — a rename is a breaking change.
For every consumer-relevant service (Backend/Kong, LiteLLM, ComfyUI, Ollama,
MinIO, Weaviate, Neo4j, n8n, Redis, Supabase) it emits the active SOURCE mode and
each applicable URL as a **distinct named field**:

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

## 7. Readiness

| Capability | Status |
|------------|--------|
| Standalone + shared-network consumer (Method A) | **Ready** |
| Git submodule (Method B) | **Ready** ([submodule-usage.md](submodule-usage.md)) |
| Customization: `PROJECT_NAME` / `BASE_PORT` / `BRAND_*` / `*_SOURCE` / `--track` | **Ready** |
| Multiple isolated Atlas stacks on one host | **Ready** (distinct `PROJECT_NAME` + `BASE_PORT`) |
| `services/_user/` overlay **auto-launch** | **Ready** — drop `services/_user/<name>/compose.yml` and the bootstrapper merges + launches it (see [§6.1.1](#611-back-compatible-services_user-overlay-slot)). |
| Semver release tags for submodule pinning | **Ready** — the repo is tagged `vMAJOR.MINOR.PATCH`; pin your submodule to a tag (see [releasing.md](releasing.md)). |
| Published images / pip package | **Not supported** (see §5) |

The first two rows were Phase 1 of the production-readiness & reuse roadmap — now implemented (see the [Phase 1 design](../superpowers/specs/2026-06-21-phase1-reuse-mechanics-design.md)). Remaining roadmap items (Infisical secrets, centralized logging, image signing, deeper hardening) are Phase 2+.

---

## 8. See also

- [submodule-usage.md](submodule-usage.md) — complete Git-submodule guide (layout, integration patterns, CI/CD, troubleshooting)
- [source-configuration.md](source-configuration.md) — every `*_SOURCE` variable and what it does
- [ports-and-routes.md](ports-and-routes.md) — authoritative port + Kong-hostname mapping
- [releasing.md](releasing.md) — version-tag convention for pinning a submodule
- [Production readiness & reuse roadmap](../superpowers/specs/2026-06-20-production-readiness-and-reuse-roadmap-design.md) — the strategy/assessment behind this guide
