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
- **Honest status:** the consumer paths above work today; services dropped into the `services/_user/` overlay now **launch automatically** (see [§6.1](#61-extending-the-stack-via-services_user)); and the repo is **tagged** for submodule pinning. See [§7 Readiness](#7-readiness).

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
| **`BASE_PORT`** | Moves the entire host-published port block (default `63000`). `./start.sh --base-port 64000`. Does not affect in-network addresses. | `.env` / flag |
| **`BRAND_*`** | Rebrands the wizard/banner (name, tagline, author, repo URL, license) — make Atlas present as your platform. | `.env` (`BRAND_*` block) |
| **`*_SOURCE`** | Enable/disable each service or pick its backend (`container` / `container-gpu` / `localhost` / `disabled`). LLMs use `ollama-container-*` / `ollama-localhost` / `none`; cloud providers toggle via the separate `CLOUD_*_SOURCE` vars. Disable what your showcase doesn't use. | `.env` / `--<svc>-source` |
| **`--track`** | Start a curated subset (`gen-ai-rag`, `gen-ai-eng`, `gen-ai-creative`, `ml-eng`, `data-eng`, `trading`, `all`). `--track gen-ai-rag` is the natural fit for a RAG showcase. | flag |
| **`services/_user/` overlay** | Drop your own co-located service into `services/_user/<name>/compose.yml` (gitignored upstream, so it never leaks into Atlas PRs); the bootstrapper auto-merges and launches it. | [§6.1](#61-extending-the-stack-via-services_user) |
| **`services/supabase/db/_user/` SQL slot** | Add downstream-owned Supabase schema, seed, view, grant, or extension SQL that runs after Atlas-owned database initialization. | [§6.2](#62-adding-supabase-sql-via-the-user-migration-slot) |
| **`BACKEND_PLUGINS_DIR` plugin seam** | Mount a directory of FastAPI route packages into the backend app to add your own API routes — no fork of `services/backend/` required. | [§6.3](#63-adding-backend-api-routes-via-the-plugin-seam) |

Full source/customization matrix: [source-configuration.md](source-configuration.md).

User overlays use normal `.env` syntax (`KEY=value`, quoted values, and whitespace-prefixed inline comments). The merge order is deterministic: `.env.example` baseline → generated or existing `.env` → sibling `.env.user` → `ATLAS_ENV_USER_FILE` → explicit CLI flags such as `--project` or `--<svc>-source`. Both overlays are merged on every start, including `--cold`, before missing keys are backfilled from `.env.example`.

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

### 6.1 Extending the stack via `services/_user/`

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

---

## 7. Readiness

| Capability | Status |
|------------|--------|
| Standalone + shared-network consumer (Method A) | **Ready** |
| Git submodule (Method B) | **Ready** ([submodule-usage.md](submodule-usage.md)) |
| Customization: `PROJECT_NAME` / `BASE_PORT` / `BRAND_*` / `*_SOURCE` / `--track` | **Ready** |
| Multiple isolated Atlas stacks on one host | **Ready** (distinct `PROJECT_NAME` + `BASE_PORT`) |
| `services/_user/` overlay **auto-launch** | **Ready** — drop `services/_user/<name>/compose.yml` and the bootstrapper merges + launches it (see [§6.1](#61-extending-the-stack-via-services_user)). |
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
