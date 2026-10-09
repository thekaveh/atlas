# 7.6. Reusing Atlas as Infrastructure

This page tells you how to use Atlas as the infrastructure for another project. An example is a RAG app that needs Weaviate, Neo4j, an LLM gateway and object storage.

The page is the overview and decision guide. It covers the choice of method, readiness, wiring your project, and customization. The step-by-step guide for the Git-submodule method is [Using Atlas as a Git submodule](submodule-usage.md).

---

## 1. TL;DR

- **Atlas is designed for reuse.** `PROJECT_NAME` namespaces the stack, `BASE_PORT` moves all host ports as one block, and `*_SOURCE` turns each service on or off. All containers share one Docker network, `${PROJECT_NAME}-network`, which your project can join.
- **Two methods are ready:**
  - **A — Standalone + shared network.** Use it when one Atlas instance serves several projects. Atlas runs on its own. Your project is a separate Compose project that joins `${PROJECT_NAME}-network` and calls services by Docker DNS name or through Kong.
  - **B — Git submodule.** Use it when your project ships and deploys Atlas with it. Atlas lives in your repo under `infra/` and runs from there.
- **You do not need a fork.** `PROJECT_NAME`, `BASE_PORT`, `BRAND_*`, `*_SOURCE`, `--track` and the consumer manifest cover the common cases.
- Status of each capability: [§8 Readiness](#8-readiness). Full operator journey: [§7](#7-consumer-adoption-runbook-the-full-journey).

---

## 2. Choose your reuse method

| Method | Use it when… | Ready? | Detail |
|--------|--------------|--------|--------|
| **A. Standalone + shared network** | One Atlas instance is shared infra for one or more *separate* project repos, and your app must not depend on Atlas internals. | **Yes** | [§3](#3-method-a--standalone--shared-network-the-rag-showcase-walkthrough) |
| **B. Git submodule** | Your project clones and deploys *with* a pinned copy of Atlas (one repo, one deploy unit, a reproducible version). | **Yes** | [§4](#4-method-b--git-submodule), [Using Atlas as a Git submodule](submodule-usage.md) |
| **C. Template / fork** | You must change Atlas's structure. | Works; you own the merge cost | [§5](#5-method-c--template--fork-and-why-not-published-images) |
| **D. Published images / pip package** | You want `docker pull atlas/...` or `pip install atlas` without the repo. | **Not supported** | [§5](#5-method-c--template--fork-and-why-not-published-images) |

**Rule of thumb:** an app that *talks to* the infra uses Method A. A product that *bundles* the infra uses Method B.

---

<a id="3-method-a--standalone--shared-network-the-rag-showcase-walkthrough"></a>

## 3. Method A — Standalone + shared network (the RAG-showcase walkthrough)

Atlas runs as its own stack. Your RAG project is a separate Compose project that joins Atlas's network and calls services by container DNS name. Your app needs to know only the service hostnames.

### 3.1. Step 1 — Run Atlas with a known `PROJECT_NAME`

```bash
# In your Atlas checkout
./start.sh --llm-provider-source none --cloud-openai-source enabled --openai-api-key sk-...   # cloud LLMs, no local GPU
# (or any track/source combination your showcase needs, e.g. --track gen-ai-rag)
```

`PROJECT_NAME` (default `atlas`) sets the shared network name: **`${PROJECT_NAME}-network`** (for example `atlas-network`). To use another name, pass `--project <name>`; Atlas saves it to `.env`.

### 3.2. Step 2 — Join Atlas's network from your project

In your project's `docker-compose.yml`, declare Atlas's network as **external** and attach your service to it:

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

Inside `${PROJECT_NAME}-network`, use the **Compose service name** and the **container port**. These addresses do not change with `BASE_PORT`, which moves only the host-published ports.

| Service | In-network address | Notes |
|---------|--------------------|-------|
| **Kong** (API gateway / single entry) | `kong-api-gateway:8000` (HTTPS `:8443`) | Route everything through here if you prefer one entry point |
| **LiteLLM** (LLM gateway, OpenAI-compatible) | `litellm:4000` | `POST http://litellm:4000/v1/chat/completions`; auth with `LITELLM_MASTER_KEY` |
| **Weaviate** (vector DB) | `weaviate:8080` (gRPC `weaviate:50051`) | |
| **Neo4j** (graph DB) | `neo4j-graph-db:7687` (Bolt), `:7474` (HTTP) | auth from `GRAPH_DB_AUTH` |
| **Supabase Postgres** | `supabase-db:5432` | REST, Auth and Storage go through Kong: see [submodule guide §6.2](submodule-usage.md#62-pattern-2-kong-gateway-for-routed-services) |
| **MinIO** (S3-compatible) | `minio:9000` (console `:9001`) | creds `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| **Redis** | `redis:6379` | auth `REDIS_PASSWORD` |
| **n8n** (workflows) | `n8n:5678` | |
| **Open WebUI** (chat UI) | `open-web-ui:8080` | |
| **Backend** (FastAPI orchestrator) | `backend:8000` | |

The generated [Ports and Routes reference](../reference/ports-routes.md) and `.env.example` hold the current port variables and Kong hostnames. [Ports and Routes](ports-and-routes.md) describes route behaviour.

### 3.4. Going through Kong instead (single entry point)

To avoid depending on service hostnames, send Kong-enabled services through `kong-api-gateway:8000`. Supabase REST is path-routed (`/rest/v1/...`). Browser-facing services with declared routes are host-routed (`<service>.localhost`). Direct TCP services still use their exported endpoint contracts. The Kong patterns and auth headers are in [submodule guide §6.2](submodule-usage.md#62-pattern-2-kong-gateway-for-routed-services).

---

<a id="4-method-b--git-submodule"></a>

## 4. Method B — Git submodule

Vendor Atlas into your repo and run it from a subdirectory. Use this method when your project and its infra ship as one versioned, reproducible unit.

```bash
git submodule add https://github.com/thekaveh/atlas infra
cd infra && git checkout <reviewed-main-commit>      # pin a reviewed commit (see §8)
./start.sh --project myproject --base-port auto     # or commit both in atlas.consumer.yml (§7.2)
```

Pin the submodule to a reviewed `main` commit, not to a moving branch, so each infra upgrade is an explicit commit. The only release tag, `v0.1.0`, predates the consumer manifest, so do not pin it ([§8](#8-readiness), [Releasing & version tags](releasing.md)). Your app joins `${PROJECT_NAME}-network` as in Method A.

The [submodule guide](submodule-usage.md) covers directory layout, `.gitignore`, the custom env-file location, integration patterns, contributing upstream, CI/CD, multiple stacks and troubleshooting.

### 4.1. Stand up a consumer from scratch, the ordered walkthrough

This walkthrough takes an empty repo to a running, isolated, reproducible Atlas-backed stack. Each step is runnable. If you use the older `_user/`-symlink layout, see the [migration guide](submodule-usage.md#421-migrating-to-atlasconsumeryml).

**1. Vendor and pin.** Pin Atlas to a reviewed `main` commit, so each infra upgrade is an explicit pointer bump:

```bash
git submodule add https://github.com/thekaveh/atlas infra
cd infra && git checkout <reviewed-main-commit> && cd ..
git add .gitmodules infra && git commit -m "vendor atlas@<sha>"
```

Bump the pin on a regular schedule, then run your CI gates again (step 8). A stale pin misses upstream fixes, including security fixes. A moving pin makes builds irreproducible.

**A pin bump rebuilds stale images.** `./start.sh` records the Atlas commit, the resolved build inputs and the set of built services in `.atlas-build-state`. If any of them changed, the start adds `--build` before it recreates containers. Details and limits: [§7.6](#76-upgrades-warm-starts-rebuild-stale-local-images).

**2. Write `atlas.consumer.yml`.** One committed manifest declares how you extend Atlas. Pass it with `--consumer` or `ATLAS_CONSUMER_MANIFEST` (step 4). An example:

```yaml
# atlas.consumer.yml (parent repo root)
name: myproject
project_name: myproject                 # Docker resource namespace (step 3)
profile: dev                            # default environment bundle; dev aliases default
brand:
  name: MyProject
  tagline: "MyProject on Atlas"
env:
  file: ./atlas.env.user                 # optional flat .env overlay (same syntax as .env: `export KEY=` and a BOM are accepted)
  values:
    BASE_PORT: auto                      # durable free block — distinct per consumer, stable across restarts
    COMFYUI_SOURCE: auto                 # durable host-adaptive source — MPS on Apple Silicon, container-gpu on NVIDIA, container-cpu elsewhere
    LLM_PROVIDER_SOURCE: auto            # host Ollama → ollama-localhost; NVIDIA → ollama-container-gpu; else ollama-container-cpu
    FAL_SOURCE:                          # key-gated: enabled iff the key is present
      enabled_if_env: FAL_API_KEY
      else: disabled
compose_overlays:
  - ./compose/myproject-overlay.yml      # external overlay; no symlink into infra/
backend_plugins:
  - ./backend/plugins                    # mounted into the backend plugin seam (step 6)
# Optional blocks: storage, litellm_models, n8n_workflows, rag_ingestion_profiles,
# lightrag_query_profiles, managed_host_services, model_sidecars, custom_nodes, blender_mcp
```

Unknown top-level keys and reserved names (stack model aliases, built-in buckets, the n8n id `plan`) are rejected. Per-key contract: [Consumer Manifest Reference](../reference/consumer-manifest.md).

**3. Isolation.** `project_name` isolates container, volume and network names. It does not isolate host ports, so each stack on one host needs its own `BASE_PORT`. The default is `63000`, which a bare `atlas` checkout binds. `doctor` warns when a non-default project uses it. Choose one:

- **`BASE_PORT: auto` in the manifest (recommended).** On first start, Atlas picks the first free 100-port block. It skips `63000` and blocks that another running stack uses. It keeps that block across restarts.
- **A fixed value**, such as `BASE_PORT: "63100"`, when every host must use the same ports.
- **`--base-port auto` on the command line**, for one run. It picks a new block every time it is passed.

How the allocator probes ports: [§7.4](#74-run-multiple-atlas-instances-on-one-host).

**4. Preflight, then start.** Each step guards the next:

```bash
export ATLAS_CONSUMER_MANIFEST="$PWD/atlas.consumer.yml"   # from the parent repo root
cd infra
./start.sh env backfill                                  # fill any new .env keys from .env.example
./start.sh compose validate                              # assert the merged compose is well-formed
./start.sh doctor --format json                          # manifest, base-port, unpullable-model and Redis AOF lints
./start.sh [--track <k>] [--detach]                      # project, BASE_PORT and sources come from the manifest
```

`doctor`, `compose validate` and the start read the manifest only when you pass `--consumer` or set `ATLAS_CONSUMER_MANIFEST`. Atlas does not find `atlas.consumer.yml` on its own. A manifest named by `ATLAS_CONSUMER_MANIFEST` is validated before any `.env` write, the same as `--consumer`.

- `env backfill` adds keys that are new in `.env.example`. A new `*_PORT` goes on your `BASE_PORT` block, not the default block.
- An `export KEY=` line counts as present. Every Atlas `.env` writer rewrites such a line in place and keeps `export`; it does not append a second assignment.
- `compose validate` finds overlay and manifest errors before containers start. `doctor` reports contract, port and provisioning problems. `--detach` exits after the health checks.

Before the first start, `doctor` and `compose validate` write the manifest values into `.env`. Derived keys and later refreshes: [Consumer Manifest Reference §3.4](../reference/consumer-manifest.md#34-manifest-values-and-derived-keys-in-env).

**5. Consume endpoints.** Host-side code (a devserver, a desktop app) reads the exported contract. In-container plugins use Compose service DNS directly.

```bash
./infra/start.sh endpoints export --format env > atlas-endpoints.env   # ATLAS_* KEY=value
```

Host tools read `ATLAS_<SVC>_HOST_ENDPOINT` (for example `ATLAS_MINIO_HOST_ENDPOINT`). In-network containers use the service name (`http://minio:9000`). Do not hard-code `localhost:<port>`, because it moves with `BASE_PORT`. With `BASE_PORT: auto`, export after the first start. Full field list: [§6.5](#65-exporting-the-endpoint-contract-endpoints-export).

**6. Backend plugin seam.** Atlas mounts each `backend_plugins` directory into the backend at `/app/plugins`. A plugin that serves an OpenAI-compatible route can appear as a LiteLLM model ([`litellm_models`](../reference/consumer-manifest.md#9-litellm_models)). A plugin gets object storage from the exported scoped variables (`ATLAS_STORE_<CONSUMER>_<STORE>_*`). Do not hand-wire `MINIO_PUBLIC_ENDPOINT` or a published MinIO port. Details: [§6.3](#63-adding-backend-api-routes-via-the-plugin-seam) and [Consumer Manifest Reference §7](../reference/consumer-manifest.md#7-storage).

**7. Teardown.**

```bash
./infra/stop.sh --project myproject --consumer ./atlas.consumer.yml          # stop this stack's containers
./infra/stop.sh --project myproject --consumer ./atlas.consumer.yml --cold   # also remove this project's volumes (data loss)
```

`--cold` removes the project's named volumes (databases, MinIO, model caches). Omit it to keep data across restarts. Pass the same manifest that you started with, by `--consumer` or `ATLAS_CONSUMER_MANIFEST`. Without it, volumes declared only in the manifest's `compose_overlays` are not removed.

If a manifest's overlays cannot load or fail validation, `./start.sh --cold` fails its cleanup step and does not rotate secrets. The surviving overlay volumes would otherwise keep the old credentials. The same applies when Docker cannot list the project's volumes after `down`, because the removal is not confirmed.

**8. CI drift gates.** Run the [preflight and CI gates](#614-preflight-and-ci-gates) in consumer CI on every pin bump. Also check that your `.env.user` and manifest overlay still apply after a cold cycle. Fail CI when the pinned Atlas is far behind `main` (pin freshness).

**9. Common footguns.**

- **Default-port collision.** A non-default project on `BASE_PORT=63000` collides with a bare `atlas` checkout. Set `BASE_PORT: auto` in the manifest (step 3).
- **Declared models on host sources.** `managed-localhost-mps` downloads the declared ComfyUI set, and `ollama-localhost` pulls declared tags onto the host daemon, at every start. An unmanaged ComfyUI `localhost` install is not provisioned; `doctor` names what is missing.
- **Host Ollama evicts its own models.** On `ollama-localhost`, a run that uses more models than `OLLAMA_MAX_LOADED_MODELS` reloads them in a loop, with no error. The fix is in [§6.6](#66-host-ollama-sizing-for-multi-model-ingest-ollama-localhost).
- **Committed-value clobber.** A manifest value applies again on every start and replaces a temporary `.env` edit. Keep human-tuned values (for example model lists) host-local. Commit only identity (`project_name`, `BASE_PORT`). See [§7.2](#72-pin-instance-identity-in-the-manifest-not-just-env).

**Complete worked example — a minimal consumer repo:**

```
myproject/
├── .gitmodules                       # pins infra/ to a reviewed Atlas commit
├── atlas.consumer.yml                # the manifest above
├── atlas.env.user                    # optional flat overlay (gitignored: secrets)
├── compose/
│   └── myproject-overlay.yml         # external overlay (no symlink into infra/)
├── backend/plugins/myproject-rag/    # backend plugin (→ /app/plugins)
├── n8n/myproject-flow.workflow.json  # seeded workflow (n8n_workflows)
├── scripts/start.sh                  # thin launcher (the step-4 command)
├── src/                              # your application code
└── infra/                            # Atlas submodule @ pinned commit
```

`scripts/start.sh` only wraps the step-4 launch. It does not symlink into `infra/`, change `.env` or run `docker restart` on Atlas containers.

---

<a id="5-method-c--template--fork-and-why-not-published-images"></a>

## 5. Method C — Template / fork (and why not published images)

- **Template / fork.** Clone Atlas, remove what you do not need, and own the result. You get full control, but you merge upstream changes by hand. Use it only when you must change Atlas's structure.
- **Published images / pip package (not supported).** There is no `atlas/...` image set and no `pip install atlas` package. The bootstrapper reads `services/<name>/service.yml` manifests and generates Compose from them, so it needs the repo layout. Use Method A or B.

---

## 6. Customizing Atlas for your project (no fork required)

| Knob | What it does | Where |
|------|--------------|-------|
| **`PROJECT_NAME`** | Compose project name. It prefixes containers, volumes and the network (`${PROJECT_NAME}-network`). `./start.sh` and `./stop.sh` both read it, so stop removes what start launched. `--project` / `-p` overrides it and saves it to `.env`; the wizard also asks for it. See the letter-case note below the table. | `.env` / `-p` |
| **`.env.user`** | Optional user-owned overlay beside the active `.env`. Atlas merges it into `.env` on every start, before backfill and CLI flags. Use it for downstream-only keys that must survive `.env` regeneration. | `.env.user` |
| **`ATLAS_ENV_USER_FILE`** | Optional external overlay, for config that lives in the parent repo. It applies after `.env.user`, so it wins on duplicate keys. CLI flags such as `--project` still win last. | shell env var |
| **`atlas.consumer.yml`** | Parent-owned manifest: project name, branding, env overlays, external Compose overlays, plugin roots, model sidecars and more. Pass it with `--consumer` or `ATLAS_CONSUMER_MANIFEST`. | [§6.1](#61-registering-a-parent-project-with-atlasconsumeryml), [reference](../reference/consumer-manifest.md) |
| **`BASE_PORT`** | Moves the host-published port block (default `63000`). It does not change in-network addresses. Host ports are not project-scoped, so a second stack on one host needs its own `BASE_PORT`. Use `BASE_PORT: auto` in the manifest for a durable free block, or `--base-port 64000`. Use `--base-port auto` only for a one-off run. | manifest / `.env` / flag ([§7.4](#74-run-multiple-atlas-instances-on-one-host)) |
| **`BRAND_*`** | Rebrands the wizard and banner (name, tagline, author, repo URL, license). | `.env` (`BRAND_*` block) |
| **`*_SOURCE`** | Turns each service on or off, or picks its backend (`container` / `container-gpu` / `localhost` / `disabled`). LLMs use `ollama-container-*` / `ollama-localhost` / `none`. Cloud providers use the separate `CLOUD_*_SOURCE` variables. | `.env` / `--<svc>-source` |
| **`--track`** | Starts a curated subset (`gen-ai-rag`, `gen-ai-eng`, `gen-ai-creative`, `ml-eng`, `data-eng`, `trading`, `all`). `--track gen-ai-rag` suits a RAG showcase. Explicit `--<service>-source` flags override track membership. A `*_SOURCE` in the manifest's `env.values` also keeps that service on outside the track. A CLI flag beats both. | flag |
| **`services/_user/` overlay** | Older local discovery slot for co-located services. New parent repos use `atlas.consumer.yml`, which needs no symlinks. | [§6.1.1](#611-back-compatible-services_user-overlay-slot) |
| **`MINIO_EXTRA_CONSUMERS`** | Adds parent-owned MinIO buckets and scoped service-account credentials without a fork of `init-minio.sh`. | [§6.1.2](#612-adding-parent-owned-minio-buckets) |
| **`services/supabase/db/_user/` SQL slot** | Adds downstream-owned Supabase SQL that runs after Atlas's own database setup. | [§6.2](#62-adding-supabase-sql-via-the-user-migration-slot) |
| **`BACKEND_PLUGINS_DIR` plugin seam** | Mounts FastAPI route packages into the backend, without a fork of `services/backend/`. | [§6.3](#63-adding-backend-api-routes-via-the-plugin-seam) |

Full source and customization matrix: [SOURCE Configuration Guide](source-configuration.md).

**`PROJECT_NAME` letter case.** If `.env` already names the same project in another letter case, Atlas keeps the stored spelling, for `./stop.sh -p` too. Every Compose command Atlas runs makes `PROJECT_NAME` name the same project as its `-p`. A shell-exported value, or a `--cold --project <new>`, that names a different project is replaced. So `down --volumes` cannot reach another project's volumes. A hand-edited `MyStack` (project `mystack`) keeps its spelling, so existing `MyStack-*` volumes stay in use.

**Overlay syntax.** Overlays use `.env` syntax: `KEY=value`, quoted values and whitespace-prefixed inline comments. Compose expands `$name` and backslash escapes in unquoted and double-quoted values, so `pa$word` reaches containers as `pa`.

Atlas therefore single-quotes a value it writes when the value has a backslash or a `$` that is not a `${VAR}` reference. A `$$` also stays literal. Atlas refuses a value that also contains a single quote or ends in a backslash. Quote such values the same way in a hand-edited `.env`.

**Merge order.** Atlas merges on every start, including `--cold`. The order is `.env.example` → generated or existing `.env` → `.env.user` → `ATLAS_ENV_USER_FILE` → `atlas.consumer.yml` env values → explicit CLI flags such as `--project` or `--<svc>-source`. It then fills missing keys from `.env.example`.

For the older `services/_user/<name>/compose.yml` symlink layout, see the [parent-repo consumer reference layout](submodule-usage.md#42-parent-repo-consumer-reference-layout). New consumers register through the manifest; to move an existing layout, see [Migrating to atlas.consumer.yml](submodule-usage.md#421-migrating-to-atlasconsumeryml).

Use `ATLAS_ENV_USER_FILE` for parent-owned config that the consuming project tracks or templates:

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

Use absolute paths in CI and wrapper scripts. A relative `ATLAS_ENV_USER_FILE` resolves against the directory that ran `start.sh`. A direct Python run resolves it against the Python process's working directory. If the file is missing or unreadable, Atlas prints a warning and continues without it. If no overlay sets `PROJECT_NAME`, a cold start keeps the previous valid value, so a later `./stop.sh` still targets the same stack.

### 6.1. Registering a parent project with `atlas.consumer.yml`

New parent repos commit one `atlas.consumer.yml` beside their overlays, plugins and model sidecars. Pass it from the parent repo:

```bash
./infra/start.sh --consumer ./atlas.consumer.yml --no-tui --detach
./infra/start.sh --consumer ./atlas.consumer.yml compose validate
./infra/start.sh --consumer ./atlas.consumer.yml doctor --format json
```

Relative paths in the manifest resolve from the manifest's directory, not from the Atlas checkout. [§4.1 step 2](#41-stand-up-a-consumer-from-scratch-the-ordered-walkthrough) shows an example manifest. Every key, its fields and its validation rules are in the [Consumer Manifest Reference](../reference/consumer-manifest.md).

**Validation and merge rules.** Atlas validates the declared paths before Compose runs. It merges manifest env values into `.env` and appends external overlays to the Compose command, with no symlinks into the submodule. The launch overview lists registered consumers.

- To load several manifests, repeat `--consumer`, or set `ATLAS_CONSUMER_MANIFEST` to `os.pathsep`-separated paths.
- List-valued model declarations merge by ordered union. Atlas fails validation on scalar conflicts, such as two different `PROJECT_NAME` values, instead of last-wins.
- Duplicate YAML mapping keys at any depth are invalid and fail with their key name. Standard YAML merge inheritance works, including an explicit key overriding a merged default.

Value-level rules (key-gated values, YAML typing, the `auto` source sentinel, deployment profiles, the Blender pool size) are in [Consumer Manifest Reference §3–§4 and §14](../reference/consumer-manifest.md#3-env).

#### 6.1.1. Back-compatible `services/_user/` overlay slot

To add your own service *into* the Atlas stack, put a Compose fragment at `services/_user/<name>/compose.yml`. The service then starts and stops with `./start.sh` / `./stop.sh` and shares the stack's network. At launch, the bootstrapper adds every `services/_user/*/compose.yml` to the Compose command (`-f docker-compose.yml -f services/_user/<name>/compose.yml …`). Upstream gitignores `services/_user/`, so your additions never enter an Atlas PR.

A `_user/` service is a **self-contained Compose fragment**. It brings its own image, host ports and environment, and joins the shared network:

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
      - "${HOST_BIND_IP-127.0.0.1:}8090:8090"      # choose a free host port yourself
    networks:
      - backend-network

networks:
  backend-network:
    name: ${PROJECT_NAME}-network
    external: true
```

**Scope.** Overlay services launch, but Atlas's wizard, port allocator and generated `.env.example` do not know them. You manage their image, ports and env in the fragment. Use `${HOST_BIND_IP-127.0.0.1:}` on published ports to keep Atlas's loopback default. To keep the service in its own repo, use Method A.

#### 6.1.2. Adding parent-owned MinIO buckets

**Preferred: the `storage:` block.** Declare object stores in `atlas.consumer.yml`. Atlas provisions the bucket and a scoped credential, writes the `minio-init` overlay and exports `ATLAS_STORE_<CONSUMER>_<STORE>_*` fields, with no Compose override. Fields, naming rules, the exported contract and presigned-URL rules: [Consumer Manifest Reference §7](../reference/consumer-manifest.md#7-storage).

**Underlying grammar (for `_user` overlays).** A `_user` service that needs object storage extends the existing `minio-init` service from a parent-owned overlay and passes `MINIO_EXTRA_CONSUMERS`. Do not fork `services/minio/init/scripts/init-minio.sh`. Each entry uses the built-in consumers' grammar:

```text
CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR[:EXTRA_BUCKET_VAR,...]
```

A DayDreams-style overlay keeps the bucket name and credentials in the parent repo:

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

Put `MINIO_BUCKET_DAYDREAMS`, `MINIO_DAYDREAMS_ACCESS_KEY` and `MINIO_DAYDREAMS_SECRET_KEY` in `.env.user` or `ATLAS_ENV_USER_FILE`. On every `minio-init` run, Atlas creates the bucket, writes a named policy, and creates or refreshes a service account with the built-in consumers' scoped policy. Separate several entries with spaces. Comma-separated extra bucket variables after the fourth field give one consumer a small named bucket set.

#### 6.1.3. Scripted bring-up for automation

For CI, cron or parent-repo wrapper scripts, use the detached path. Do not background `start.sh` and kill it after a hand-written health poll:

```bash
./start.sh --no-tui --detach
```

`--detach` (alias `--no-follow`) runs the normal start, waits for health, prints a per-service status summary and exits. It exits `0` only when the summary is healthy. It starts only the enabled services (after tracks, overrides and consumer overlays), so a broken build of a disabled service cannot stop it.

A service still in its health start period (`health=starting`) is polled again for up to about 120 s before it counts as failed. A real failure (unhealthy, exited non-zero, or still starting after that window) names the service and its exit code. Add `--json` for machine-readable status:

```bash
./start.sh --no-tui --detach --json
```

When the start reaches the summary, the payload is `{"ok", "services", "converged_after_grace", "not_started"}`:

- `not_started` lists services left out of `up` because their image build failed. The start still counts as a success, so check it when completeness matters.
- `converged_after_grace` is `true` when the stack became healthy only inside the grace window.
- If the final status poll fails, `ok` is `false`, and the payload has an `error` string and empty `services`.
- If the start stops earlier, the payload is `{"ok": false, "exit_code": N}`, and stderr names the services. Causes are invalid input, a failed setup step or a failed `up`. Check `ok` before you read other keys.

Managed host runtimes and their teardown: [§7.5](#75-coexist-with-host-run-services-localhost-sources).

<a id="614-headless-submodule-upgrade-validation"></a>
<a id="615-consumer-doctor-for-ci-preflight"></a>

#### 6.1.4. Preflight and CI gates

Run this sequence when you bump the pin, and as the parent repo's preflight before product tests. It finds new `.env.example` keys, invalid overlays and contract drift without the interactive wizard:

```bash
export ATLAS_CONSUMER_MANIFEST="$PWD/atlas.consumer.yml"   # from the parent repo root
git -C infra fetch
git -C infra checkout <atlas-sha>
cd infra
./start.sh env backfill
./start.sh compose validate
./start.sh doctor --format json
./start.sh --no-tui --detach
./start.sh endpoints assert --require ATLAS_LITELLM_HOST_ENDPOINT,ATLAS_MINIO_HOST_ENDPOINT
```

`doctor` and `compose validate` check the manifest only when `ATLAS_CONSUMER_MANIFEST` is set or `--consumer` is passed. Without one, the `consumer-manifests` check validates nothing.

- **`env backfill`** is additive and idempotent. It keeps existing values and fills a blank value only when `.env.example` now has a non-blank default. It prints the keys it added or filled, grouped by section.
- **`compose validate`** runs `docker compose config -q` on the assembled stack, including every `services/_user/<name>/compose.yml`. It rewrites common missing-variable failures into a service and variable summary, then prints Compose's raw stderr.
- **`doctor`** does not start containers. It checks manifest validity, Compose, `_user` overlay env references, plugin directories, model sidecars, consumer endpoints and tracked-file cleanliness of the Atlas checkout. Docker checks report `skipped` when Docker is unavailable.
- **`endpoints assert`** fails when a field your code reads is missing ([§6.5](#65-exporting-the-endpoint-contract-endpoints-export)).

**Manifest values in `.env`.** Before the first start, `doctor` and `compose validate` write the manifest values into `.env`, so the compose files resolve. Later refreshes and the four derived keys: [Consumer Manifest Reference §3.4](../reference/consumer-manifest.md#34-manifest-values-and-derived-keys-in-env).

Exit codes:

- `env backfill` exits `0` when `.env` is current or was updated, and `1` when the write fails. With no env file it writes nothing, says so on stderr and exits `0` (`./start.sh` creates the file).
- `compose validate` exits `0` when Compose accepts the stack. Otherwise it exits with Compose's status code.
- `doctor` exits non-zero when any check reports `fail`.

`--format json` is for consumer CI; text output is for local debugging. It writes **pure JSON to stdout**: the `📦 Using …` banner and all other text go to stderr. So it pipes directly to `jq`:

```bash
./start.sh doctor --format json 2>/dev/null | jq .ok
```

### 6.2. Adding Supabase SQL via the user migration slot

Put project-owned SQL files under `services/supabase/db/_user/`. `supabase-db-init` runs them in lexical order after Atlas's own scripts in `services/supabase/db/scripts/`. A failing file stops `supabase-db-init`, so dependent services do not start. Write idempotent SQL with numbered names such as `10-project-schema.sql`. The full contract is in [Supabase §2.4](../../services/supabase/README.md#24-downstream-user-migrations) and `services/supabase/db/_user/README.md`.

### 6.3. Adding backend API routes via the plugin seam

The FastAPI backend has a **generic plugin seam**: you can mount your own API routes into it without a fork of `services/backend/`.

At startup the backend scans each root in `$BACKEND_PLUGINS_DIR` (default `/app/plugins`). Each subdirectory that is an importable package with a module-level `router` (a FastAPI `APIRouter`) mounts. A plugin that fails to install or import is logged and skipped; one bad plugin never crashes the backend. The seam does nothing when the directory does not exist, so base Atlas is unaffected. Loading, dependency and naming rules: [Consumer Manifest Reference §8.1](../reference/consumer-manifest.md#81-loading-rules).

**Apply plugin changes by recreating the backend.** Run `./start.sh --consumer <manifest>` again. A bare `docker compose up -d --force-recreate backend` targets the wrong Compose project and drops the overlay that mounts the plugins. The backend's auto-reloader is off by default, so git churn in a bind-mounted plugin tree does not restart it. Set `BACKEND_DEV_RELOAD=true` only while you edit plugin source.

To mount plugins without a manifest, extend Atlas's `backend` service from your parent Compose:

```yaml
# your parent docker-compose.yml — overlay onto Atlas's backend
services:
  backend:
    volumes:
      - ./my-plugins:/app/plugins:ro
    environment:
      BACKEND_PLUGINS_DIR: /app/plugins   # the default; shown for clarity
```

Each plugin is a package directory that exposes `router`:

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

Give the package a unique name that no backend module uses, and give its router a literal prefix ([naming and path rules](../reference/consumer-manifest.md#81-loading-rules)).

Your routes are served at `backend:8000` in-network, or through Kong at `api.localhost/...`. Backend-side description: [Backend API §4](../../services/backend/README.md).

#### 6.3.1. Declaring a typed plugin contract with `plugin.yml`

A plugin may ship an optional `plugin.yml` next to its `__init__.py`. It declares the route prefix, health and docs paths, Kong auth mode, timeouts, buffering and typed env. `GET /plugins` then lists the plugin, and `./start.sh doctor` checks its env before launch. Contract: [Consumer Manifest Reference §8.2](../reference/consumer-manifest.md#82-pluginyml).

#### 6.3.2. Exposing plugin models to LiteLLM with `litellm_models`

A plugin that serves an OpenAI-compatible route can appear as a LiteLLM model in `/v1/models`, with no registration script. Atlas writes the rows on every start; do not restart LiteLLM or call its admin API. Contract: [Consumer Manifest Reference §9](../reference/consumer-manifest.md#9-litellm_models).

#### 6.3.3. Seeding n8n workflows with `n8n_workflows`

Atlas imports, activates and readiness-checks a consumer's n8n workflows on every start, through an Atlas-owned `n8n-seed` container. It replaces a hand-written import, restart and poll script. Contract: [Consumer Manifest Reference §10](../reference/consumer-manifest.md#10-n8n_workflows).

#### 6.3.4. Declaring RAG ingestion profiles with `rag_ingestion_profiles`

A consumer with a RAG corpus declares ingestion profiles, and Atlas owns the job lifecycle: discover → parse → chunk → embed → vector-store write → LightRAG upload → drain → finalize. Submit and poll jobs at `/api/rag/ingestions`. Contract, corpus mounts and limits: [Consumer Manifest Reference §11](../reference/consumer-manifest.md#11-rag_ingestion_profiles).

#### 6.3.5. Declaring LightRAG query profiles with `lightrag_query_profiles`

A query profile is a named bundle of LightRAG per-query settings, such as "graph-rag local k=30". A plugin or an Open WebUI model alias selects it by name. Contract: [Consumer Manifest Reference §12](../reference/consumer-manifest.md#12-lightrag_query_profiles).

**LightRAG roles on native bindings.** To run the EXTRACT role on native Ollama, set `LIGHTRAG_EXTRACT_LLM_MODEL`, `LIGHTRAG_EXTRACT_LLM_BINDING=ollama`, its `_BINDING_HOST` and `LIGHTRAG_EXTRACT_LLM_BINDING_API_KEY`. LightRAG needs a key for a role on its own binding, and Ollama ignores it, so any string such as `ollama` works. Atlas caps that role's output with `LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_PREDICT` (default `4096`) and `LIGHTRAG_EXTRACT_OLLAMA_LLM_NUM_CTX` (default `16384`).

A native binding does not send a model's catalog `request_defaults`, so Atlas decides each role's transport at start. `doctor`'s `lightrag-role-transport` check names each role's transport. Sizing, timeouts and transport rules: [LightRAG §3](../../services/lightrag/README.md#3-configuration).

### 6.4. Consuming auto-managed endpoint variables

The bootstrapper computes **auto-managed endpoint variables** in `.env`. Each resolves to the right internal URL for the active `*_SOURCE`. Consumers (Method A or B) should map these into their own variables instead of hard-coding a URL.

| Variable | Resolved from | Example value (container source) | Example value (localhost source) |
|----------|---------------|----------------------------------|----------------------------------|
| `COMFYUI_ENDPOINT` | `COMFYUI_SOURCE` | `http://comfyui:18188` | `http://host.docker.internal:8000` |
| `OLLAMA_ENDPOINT` | `LLM_PROVIDER_SOURCE` | `http://ollama:11434` | `http://host.docker.internal:11434` |
| `LITELLM_BASE_URL` | locked (always-on; does not vary by source) | `http://litellm:4000` | n/a (locked — no localhost mode) |
| `MINIO_ENDPOINT` | `MINIO_SOURCE` | `http://minio:9000` | n/a (`container`/`disabled` only — no localhost mode) |

A localhost-source value always names `host.docker.internal`, on Linux too. Docker Desktop resolves that name. On Linux Docker and Podman, a container resolves it only with `extra_hosts: ["host.docker.internal:${HOST_GATEWAY_IP}"]`. Every Atlas container that reads such a value has that mapping; add it to your own services.

`LITELLM_BASE_URL` has **no path suffix**. LiteLLM's OpenAI-compatible routes are under `/v1` (for example `${LITELLM_BASE_URL}/v1/chat/completions`), so append `/v1` in your client.

**Bridging pattern.** In your overlay or `services/_user/` service, map the endpoint into your own variable with a three-level fallback:

```yaml
# services/_user/my-app/compose.yml
services:
  my-app:
    environment:
      # Own override → Atlas's computed endpoint → hard-coded in-network default
      MY_COMFYUI_URL: ${MY_COMFYUI_URL:-${COMFYUI_ENDPOINT:-http://comfyui:18188}}
      MY_LITELLM_URL: ${MY_LITELLM_URL:-${LITELLM_BASE_URL:-http://litellm:4000}}  # append /v1 for OpenAI routes
```

Your service then works with every `*_SOURCE` value, with no per-source branches. The same pattern applies to `OLLAMA_ENDPOINT`, `MINIO_ENDPOINT` and later auto-managed endpoints.

### 6.5. Exporting the endpoint contract (`endpoints export`)

Atlas emits a **stable, machine-readable endpoint contract** for non-Python consumers (web and desktop shells, devservers) and submodule parents. It replaces grepping `.env` and hand-kept URL-rewrite bridges.

```bash
./start.sh endpoints export --format env    # KEY=value on stdout
./start.sh endpoints export --format json   # JSON object on stdout
```

The field **names are a compatibility contract**: a rename is a breaking change. The services are Backend/Kong, LiteLLM, ComfyUI, Asset Worker, Ollama, MinIO, Weaviate, Neo4j, n8n, Redis and Supabase. For each, it emits the active SOURCE and each URL as a separate field:

| Field | Meaning |
|---|---|
| `ATLAS_<SVC>_SOURCE` | active SOURCE mode (a `disabled` service emits only this) |
| `ATLAS_<SVC>_CONTAINER_ENDPOINT` | in-network URL (e.g. `http://minio:9000`) |
| `ATLAS_<SVC>_HOST_ENDPOINT` | host URL (e.g. `http://localhost:63020`) |
| `ATLAS_<SVC>_KONG_ENDPOINT` | Kong `*.localhost` route (when exposed) |
| `ATLAS_<SVC>_PUBLIC_ENDPOINT` | browser-facing public read base (MinIO presigned reads) |

It also emits `ATLAS_KONG_GATEWAY` and every `ATLAS_STORE_*` field from the [storage contract](#612-adding-parent-owned-minio-buckets). Host and Kong URLs follow `BASE_PORT`. The internal and public MinIO endpoints differ; sign presigned URLs against `ATLAS_MINIO_PUBLIC_ENDPOINT`.

**Host-process sources.** `ATLAS_<SVC>_HOST_ENDPOINT` uses the port the host process serves on, not the Compose published port. Container sources use their published port.

- `COMFYUI_SOURCE=managed-localhost-mps` → `http://localhost:8188` (`COMFYUI_MPS_LOCALHOST_PORT`)
- `COMFYUI_SOURCE=localhost` → `http://localhost:8000` (`COMFYUI_LOCALHOST_PORT`)
- `LLM_PROVIDER_SOURCE=ollama-localhost` → `http://localhost:11434` (`OLLAMA_LOCALHOST_PORT`)

**Source-specific fields.** Under a managed Metal ComfyUI source, the export adds `ATLAS_COMFYUI_OUTPUT_DIR` and `ATLAS_COMFYUI_INPUT_DIR` (absolute host paths). Directory layout: [ComfyUI §10](../../services/comfyui/README.md). Under a Blender host source, it adds `ATLAS_BLENDER_MCP_HOST_ENDPOINT` with a `tcp://` scheme (a raw socket, not HTTP). A managed pool of more than one instance also adds `ATLAS_BLENDER_MCP_HOST_ENDPOINTS`: every instance, comma-separated, instance 0 first. Client contract: [Blender MCP](../../services/blender-mcp/README.md).

**Resolved values.** The exporter expands Compose-style `${VAR}` and `${VAR:-default}` references stored in `.env`. For example, a host-source `COMFYUI_ENDPOINT` of `http://host.docker.internal:${COMFYUI_MPS_LOCALHOST_PORT:-8188}` is expanded. The output never holds a `${…}` literal, except for secrets.

**Secrets.** By default the output has **no secret values**. Infra secrets (for example the Redis password inside `REDIS_URL`) stay `${VAR}` references. `--with-secrets` resolves only consumer-scoped credentials (the storage keys), never infra secrets. It refuses stdout and needs `--output PATH`:

```bash
# Submodule parent: capture the contract next to the parent app on every bring-up
(cd infra && ./start.sh endpoints export --format env --output ../atlas-consumer.env)
```

Output is deterministic for the same inputs, so a consumer can diff it across runs.

**Before the first start.** With `BASE_PORT: auto` and no allocated block, `endpoints export` exits `3` and tells you to start the stack first. `--allow-unresolved` exports anyway, with default-block ports. Use it only for templates and sample files, never to connect.

**Guard against contract drift (`endpoints assert`).** A later Atlas pin could rename or drop a field. Your code would then read `None` and fall back to a broken default with no failing test. Assert the fields you read, against the pinned submodule:

```bash
# Fails (exit 1) if any listed field is absent from the current contract:
./start.sh endpoints assert --require ATLAS_LITELLM_HOST_ENDPOINT,ATLAS_MINIO_HOST_ENDPOINT,ATLAS_COMFYUI_HOST_ENDPOINT

# No --require: list the available field names (machine-readable):
./start.sh endpoints assert --format json
```

Run the `--require` form, with the exact fields your code reads, in consumer CI ([§6.1.4](#614-preflight-and-ci-gates)).

### 6.6. Host Ollama sizing for multi-model ingest (`ollama-localhost`)

A graph-RAG ingest uses several Ollama models in one run: an EXTRACT model, an embedding model and a KEYWORD/QUERY model. With Ollama's defaults, the large models evict each other between calls. `ollama ps` shows models cycling through `Stopping…`, and extraction stalls with no error. Two settings control this: `OLLAMA_MAX_LOADED_MODELS` (how many stay loaded) and `OLLAMA_KEEP_ALIVE` (how long, default 5m). Background: [Ollama §3](../../services/ollama/README.md#3-configuration).

**`ollama-localhost`.** You own the host daemon, so Atlas cannot set these. Set `OLLAMA_MODELS_RESIDENT_MIN` in `.env` to the number of models one run uses (for example `3`). `./start.sh doctor` then reads the daemon's real values and fails the `ollama-residency` check before a long run. The check prints the `launchctl` command to fix it.

**Set for the run, then revert (macOS):**

```bash
# Before the run — keep the ingest model-set resident. Size to YOUR set +
# free RAM (4 covered extract 37 GB + embed + keyword 29 GB on a 192 GB host):
launchctl setenv OLLAMA_MAX_LOADED_MODELS 4
launchctl setenv OLLAMA_KEEP_ALIVE -1        # "UNTIL: Forever" — no idle unload
osascript -e 'quit app "Ollama"' && open -a Ollama   # restart to pick it up
ollama ps                                     # the set holds at UNTIL: Forever
```

```bash
# After the run — revert so you don't pin model RAM indefinitely:
launchctl unsetenv OLLAMA_MAX_LOADED_MODELS
launchctl unsetenv OLLAMA_KEEP_ALIVE
osascript -e 'quit app "Ollama"' && open -a Ollama   # defaults (KEEP_ALIVE=5m, evict-on-pressure) return
```

**Know the RAM cost.** `-1` keeps every loaded model in memory until you revert and restart the daemon. For a 37 GB extract model plus a 29 GB keyword model, that is about 66 GB. On a unified-memory host, it competes with ComfyUI and vLLM-Metal. Set `OLLAMA_MAX_LOADED_MODELS` to your ingest set and free RAM, not higher.

**Container sources.** For `ollama-container-*`, set the same limits in `.env`, then restart: `OLLAMA_MAX_LOADED_MODELS` (default `2`) and `OLLAMA_KEEP_ALIVE` (default empty, which is Ollama's 5m). The `ollama-residency` doctor check covers only `ollama-localhost`.

### 6.7. Declaring your own managed host process (`managed_host_services`)

A Metal or MLX service cannot get the GPU through a Linux container on macOS. If your project needs one (for example an MLX segmentation service), declare it under `managed_host_services`. Atlas then manages its venv, start, stop and health checks, with no fork and no hand-made pid files.

`./start.sh` and `./stop.sh` do not start or stop a declared service, and neither does `--stop-managed-hosts`. Use `./start.sh managed-host list|preflight|install|start|status|health|stop|remove <name>`. Fields, safety constraints and the commands: [Consumer Manifest Reference §13](../reference/consumer-manifest.md#13-managed_host_services).

---

## 7. Consumer adoption runbook (the full journey)

Sections 3–6 describe the mechanisms. This runbook puts them in order and adds the operating behaviours that consumers learned from incidents. For a new repo, follow the [§4.1 walkthrough](#41-stand-up-a-consumer-from-scratch-the-ordered-walkthrough); this section is the day-2 reference.

### 7.1. The journey in order

1. **Register** a manifest: [§6.1](#61-registering-a-parent-project-with-atlasconsumeryml).
2. **Pin identity** (`project_name`, `BASE_PORT: auto`) in the manifest: [§7.2](#72-pin-instance-identity-in-the-manifest-not-just-env).
3. **Select sources** once, or commit `auto`: [§7.3](#73-select-sources-once-keep-env-as-the-source-of-truth). Gate a paid provider with [`enabled_if_env`](../reference/consumer-manifest.md#32-key-gated-values).
4. **Validate** headlessly: [§6.1.4](#614-preflight-and-ci-gates). `doctor` also flags a default-`63000` squat and declared models that cannot be pulled under a `*-localhost` source.
5. **Start** with `./start.sh --consumer …`. LiteLLM models and n8n workflows apply on start; do not script a restart or an admin-API call.
6. **Export and assert** endpoints: [§6.5](#65-exporting-the-endpoint-contract-endpoints-export).
7. **Operate**: multiple instances (§7.4), host services (§7.5), upgrades (§7.6), verification (§7.7).

### 7.2. Pin instance identity in the manifest, not just `.env`

`PROJECT_NAME` and `BASE_PORT` are your instance's identity. `.env` is machine-local and disposable. `./start.sh --cold` regenerates it from `.env.example`; `./stop.sh --cold` removes volumes but keeps `.env`. `PROJECT_NAME` survives the regeneration, because Atlas saves the previous value. A non-default `BASE_PORT` does not: it resets to `63000`, and the stack then collides with any parallel stack ([§7.4](#74-run-multiple-atlas-instances-on-one-host)).

Commit identity in the manifest. Atlas applies `env.values` to `.env` on every start, warm and cold, before it resolves ports:

```yaml
# atlas.consumer.yml
project_name: tableau          # top-level key → PROJECT_NAME
env:
  values:
    BASE_PORT: auto            # or a fixed value such as "63100"; re-applied every start
```

**Do not commit machine-specific values.** A manifest value applies again on every start and replaces temporary operator changes. OS-specific paths (for example `COMFYUI_MPS_MODELS_PATH`) belong in `.env` or `.env.user`. For source selections, commit the `auto` sentinel; it resolves per host and keeps a non-default override ([the `auto` sentinel](../reference/consumer-manifest.md#33-the-auto-source-sentinel)). Keep other committed values to identity and branding that must be the same on every machine.

### 7.3. Select sources once; keep `.env` as the source of truth

Atlas saves every `--<svc>-source` flag to `.env`. When the flag changes a saved value, the run prints a warning with the old and new values. A launcher that passes `--comfyui-source "${COMFYUI_SOURCE:-container-cpu}"` therefore resets a hand-set `managed-localhost-mps` to the CPU container on every restart. Details: [SOURCE Configuration Guide §2.1](source-configuration.md#21-cli-source-flags-persist-to-env-consumer-wrapper-trap).

**Rule:** pass source flags on the first run only. After that, edit `.env` or the manifest, not the launch command. Better, commit `COMFYUI_SOURCE: auto` / `LLM_PROVIDER_SOURCE: auto`: every start resolves the right source for the host, and no flag is needed ([the `auto` sentinel](../reference/consumer-manifest.md#33-the-auto-source-sentinel)).

`managed-localhost-mps` is a valid `--comfyui-source` value. Its lifecycle (preflight, install, provision, start, status, stop) and `COMFYUI_MPS_*` variables are in [ComfyUI §10](../../services/comfyui/README.md). Atlas downloads declared `COMFYUI_USER_MODELS` into `COMFYUI_MPS_MODELS_PATH` on start. The `unpullable-models` doctor lint passes when the host tree matches the declared catalog.

`BLENDER_MCP_SOURCE=managed-localhost` works the same way: it provisions the pinned add-on and runs headless Blender, on loopback only. Lifecycle: `./start.sh blender-mcp …`; see [Blender MCP](../../services/blender-mcp/README.md).

### 7.4. Run multiple Atlas instances on one host

Container names, networks and volumes are `${PROJECT_NAME}-*`, so they are isolated per project. Host ports are fixed offsets from `BASE_PORT` and have no project namespace. A second instance on one host therefore needs its own `project_name` **and** its own `BASE_PORT`, never the default `63000`.

For committed consumers that run side by side, set `BASE_PORT: auto` in each manifest. Atlas gives each consumer its own free `BASE_PORT+0..99` block. Blocks are 100 ports apart, which is the topology's highest port offset plus one.

The allocator skips `63000` and any block another running stack uses, and keeps the block across restarts. Three consumers started in turn get blocks such as `20000`, `20100` and `20200`. A cold start resolves again and still skips used blocks.

The port probe checks only services that this run enables. It applies this run's `--<svc>-source` flags, the manifest's `*_SOURCE` values and the wizard's answers. After the profile, the manifest and the flags write their sources, Atlas probes the chosen block again. If an enabled service finds its port taken, Atlas uses the next free block.

```bash
# Committed consumer — project, BASE_PORT: auto and sources come from the manifest:
./start.sh --consumer ./atlas.consumer.yml

# Ad-hoc stack — resolve a free block fresh at launch:
./start.sh --project daydreams --base-port auto
```

Do not add `--base-port auto` to a routine start command. It resolves a new block each time, skips the block the stack already holds, and moves the ports on every warm restart.

**If two stacks share a base port**, their host ports interleave. Container names stay isolated, so everything looks healthy. But binds are partial, and traffic crosses instances without warning. For example, `atlas-consumer.env` records `LITELLM :63040` while the *other* instance's LiteLLM answers there.

Both front ends refuse a block whose enabled services' ports are taken ("Port conflicts detected", exit 1). Ports of services disabled in this instance are not probed, so choose a distinct base per instance up front. On a warm start that first stops this instance's containers, the check waits up to 15 seconds for Docker to release their ports. Port topology: [Ports and Routes](ports-and-routes.md).

### 7.5. Coexist with host-run services (localhost sources)

When the host already runs Ollama, ComfyUI or Blender, point Atlas at it instead of starting a duplicate:

| Source flag | Effect | Host prerequisite |
|---|---|---|
| `--llm-provider-source ollama-localhost` | No `*-ollama` container; LiteLLM upstream → `host.docker.internal:11434`; catalog auto-imported from the host's `/api/tags` | Host Ollama on `:11434` ([SOURCE Configuration Guide §4.1.1](source-configuration.md)) |
| `--comfyui-source localhost` | No `*-comfyui` container; endpoint → `host.docker.internal:${COMFYUI_LOCALHOST_PORT:-8000}` | Host ComfyUI on `COMFYUI_LOCALHOST_PORT` |
| `--comfyui-source managed-localhost-mps` | Atlas-managed Metal-native ComfyUI process on `${COMFYUI_MPS_LOCALHOST_PORT:-8188}` | macOS / Apple Silicon; [ComfyUI §10](../../services/comfyui/README.md) |

**Warning:** the default `--llm-provider-source ollama-container-cpu` starts a containerized Ollama **next to** the host's Ollama. The two load models twice and compete for GPU and RAM. On a host that runs Ollama, use `ollama-localhost`.

**Shared managed-host runtimes.** Three sources run a native host-global process, not a container: ComfyUI-MPS (`COMFYUI_SOURCE=managed-localhost-mps`), vLLM Metal (`VLLM_METAL_SOURCE=managed-localhost`) and headless Blender MCP (`BLENDER_MCP_SOURCE=managed-localhost`). They listen on fixed loopback ports and serve every Atlas consumer on the machine. They are not Compose resources, so `docker compose down` never touches them.

A project-scoped stop therefore leaves them running:

```bash
./stop.sh --project consumer-b        # containers for consumer-b only; host-global runtimes untouched
```

When one is running, `stop.sh` prints a notice that names the opt-in flag. To stop all three, pass `--stop-managed-hosts`. This affects **every** consumer that uses them, and the output says so:

```bash
./stop.sh --stop-managed-hosts        # also stop ComfyUI-MPS / vLLM-Metal / Blender MCP
```

Standard and `--cold` stops follow the same rule. So a loop that resets one consumer with `./stop.sh --project <name>` (with or without `--cold`) does not interrupt another consumer. Services you declare in `managed_host_services` are not covered; use `./start.sh managed-host stop <name>` ([§6.7](#67-declaring-your-own-managed-host-process-managed_host_services)).

### 7.6. Upgrades: warm starts rebuild stale local images

A warm `./start.sh` always recreates containers (`--force-recreate`). It adds `--build` when its gitignored `.atlas-build-state` marker no longer matches one of these:

- the Atlas commit (a submodule pin bump);
- the resolved local-image build inputs and dependencies (for example a same-commit `MLFLOW_IMAGE` change);
- the enabled or explicit Compose target set (a service that the previous build left out).

Release archives without Git metadata still track build inputs and targets. A targeted build records only its own target set, so building other services cannot mark a disabled service fresh. An unchanged warm restart skips the build.

**Dockerfile edits are not detected.** The marker does not hash source files, so an uncommitted Dockerfile or build-context edit does not trigger a rebuild. Rebuild only that service, for example `docker compose -p <project> build --no-cache <svc>`, then run `./start.sh`.

**Warning:** do not use `./start.sh --cold` to pick up a Dockerfile edit. It rebuilds without cache, but it also **deletes all of the project's volumes** and regenerates `.env` and its secrets. Use it only for a full reset.

### 7.7. Post-launch verification

Trust `docker ps`, not the banner. Run this check for `<project>` at base port `<BASE>`, from `infra/` with `ATLAS_CONSUMER_MANIFEST` set:

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

Run the last two in consumer CI on every pin bump, so an upstream change fails your build instead of degrading the running consumer ([§6.1.4](#614-preflight-and-ci-gates)).

---

## 8. Readiness

| Capability | Status |
|------------|--------|
| Standalone + shared-network consumer (Method A) | **Ready** |
| Git submodule (Method B) | **Ready** ([Using Atlas as a Git submodule](submodule-usage.md)) |
| Customization: `PROJECT_NAME` / `BASE_PORT` / `BRAND_*` / `*_SOURCE` / `--track` | **Ready** |
| Multiple isolated Atlas stacks on one host | **Ready** (distinct `PROJECT_NAME` + `BASE_PORT`; see [§7.4](#74-run-multiple-atlas-instances-on-one-host)) |
| `services/_user/` overlay **auto-launch** | **Ready**: put a fragment at `services/_user/<name>/compose.yml`, and the bootstrapper merges and launches it ([§6.1.1](#611-back-compatible-services_user-overlay-slot)). |
| Release tags for submodule pinning | **Not current.** One tag exists (`v0.1.0`, June 2026), and it predates the consumer manifest. Pin a reviewed `main` commit until a newer tag is cut ([Releasing & version tags](releasing.md)). |
| Published images / pip package | **Not supported** (see §5) |

---

## 9. See also

- [Using Atlas as a Git submodule](submodule-usage.md): Git-submodule mechanics, the legacy `_user` layout, `.gitignore`, integration patterns, CI/CD, troubleshooting.
- [SOURCE Configuration Guide](source-configuration.md): every `*_SOURCE` variable and what it does.
- [Ports and Routes reference](../reference/ports-routes.md): generated port and Kong-hostname mapping. [Ports and Routes](ports-and-routes.md): route behaviour.
- [Releasing & version tags](releasing.md): the tag convention.
