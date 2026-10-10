# 10.8. Consumer Manifest Reference

This page is the per-key contract for `atlas.consumer.yml` and for the backend plugin manifest `plugin.yml`. For the walkthrough, the configuration layers and the operating commands, see [Reusing Atlas as Infrastructure](../operations/reusing-atlas.md).

---

## 1. Top-level keys

Pass the manifest with `--consumer` or `ATLAS_CONSUMER_MANIFEST`. Atlas does not find `atlas.consumer.yml` on its own. Relative paths in the manifest resolve from the manifest's directory, not from the Atlas checkout. Loading several manifests, merge order and conflict rules: [Reusing Atlas §6.1](../operations/reusing-atlas.md#61-registering-a-parent-project-with-atlasconsumeryml).

Atlas accepts exactly these keys. An unknown or misspelled key (for example `compose_overlay`) fails validation, and `doctor`'s `consumer-manifests` check fails.

| Key | Purpose | Section |
|---|---|---|
| `name`, `project_name` | Consumer id; Compose project name (`PROJECT_NAME`). | [§2](#2-identity-name-project_name-brand) |
| `brand` | `BRAND_*` values. | [§2](#2-identity-name-project_name-brand) |
| `env` | `file` (flat overlay) and `values` (plain, key-gated or `auto`). | [§3](#3-env) |
| `profile`, `profile_overrides` | Default deployment profile and per-profile overrides. | [§4](#4-profile-profile_overrides) |
| `compose_overlays` | External Compose files appended to the Compose command. | [§5](#5-compose_overlays) |
| `model_sidecars`, `custom_nodes` | Extra ComfyUI and Ollama models; extra ComfyUI nodes. | [§6](#6-model_sidecars-custom_nodes) |
| `storage` | Parent-owned MinIO buckets. | [§7](#7-storage) |
| `backend_plugins` | Plugin roots for the backend seam. | [§8](#8-backend_plugins-and-pluginyml) |
| `litellm_models` | Plugin routes as LiteLLM models. | [§9](#9-litellm_models) |
| `n8n_workflows` | Seeded n8n workflows. | [§10](#10-n8n_workflows) |
| `rag_ingestion_profiles` | RAG ingestion jobs. | [§11](#11-rag_ingestion_profiles) |
| `lightrag_query_profiles` | Named LightRAG query flavors. | [§12](#12-lightrag_query_profiles) |
| `managed_host_services` | Your own host processes. | [§13](#13-managed_host_services) |
| `blender_mcp` | Size of the managed Blender pool. | [§14](#14-blender_mcp) |

Unknown keys inside `brand`, `env`, `model_sidecars`, `custom_nodes`, `storage`, `blender_mcp` and each `storage.buckets` entry are rejected the same way.

### 1.1. Reserved names

Atlas rejects a manifest that breaks one of these rules:

- A `litellm_models` alias must not shadow a stack-owned model (`hermes-agent`, `lightrag`, `fal-image`, `tei-rerank`) or any catalog model name.
- n8n workflow ids are namespaced `atlas-consumer-<id>`. The id `plan` is reserved for the seed plan.
- A storage bucket must not reuse a built-in name. Built-in names include `comfyui`, `backend`, `n8n`, `jupyter`, `mlflow`, `lakehouse`, `spark-history`, `raw-assets`, `asset-worker` and `asset-baker`. Every default `MINIO_BUCKET_*` / `ASSET_*_MINIO_BUCKET` value in `.env.example` is also reserved.
- A store's generated `MINIO_BUCKET_<KEY>`, `MINIO_<KEY>_ACCESS_KEY` and `MINIO_<KEY>_SECRET_KEY` names must not match a stack variable or another store's. For example, consumer `asset` with store `baker` would alias asset-baker's credentials.
- A `managed_host_services` name must not match a stack host runtime or an exported service name ([§13](#13-managed_host_services)).

---

## 2. Identity: `name`, `project_name`, `brand`

- **`name`** is the consumer id. Atlas uses it for ownership stamps, storage ids and generated variable names.
- **`project_name`** sets `PROJECT_NAME`, the Docker resource namespace. Atlas validates it at load.
- **`brand`** sets `BRAND_*` values. Allowed keys: `name`, `tagline`, `version`, `author`, `author_email`, `license`, `repo_url`, `logo_file`.

Commit identity in the manifest, not in `.env`: [Reusing Atlas §7.2](../operations/reusing-atlas.md#72-pin-instance-identity-in-the-manifest-not-just-env).

---

## 3. `env`

- **`file`** names a flat overlay with `.env` syntax. `export KEY=` lines and a BOM are accepted.
- **`values`** maps variable names to plain values, key-gated values ([§3.2](#32-key-gated-values)) or the `auto` source sentinel ([§3.3](#33-the-auto-source-sentinel)).

Atlas applies `env.values` to `.env` on every start, warm and cold, before it resolves ports. A `*_SOURCE` in `env.values` keeps that service on outside the selected track; a CLI flag beats it.

### 3.1. YAML typing

Atlas parses `env.values` as YAML 1.1. Unquoted `yes`, `no`, `on`, `off`, `true` and `false` are booleans, written to `.env` as `true` / `false`. Quote a value whose YAML type would change its text: `"0755"` (else octal 493), `"1.10"` (else 1.1), `"12:30"` (else 750).

### 3.2. Key-gated values

An `env.values` entry can enable a paid provider only when its key is present, with no wrapper script:

```yaml
env:
  values:
    FAL_SOURCE:
      enabled_if_env: FAL_API_KEY   # env var name, read from the invoking shell
      then: enabled                 # optional; value when the var is set+non-empty (default "enabled")
      else: disabled                # required; value when the var is unset/empty
```

The gate reads `FAL_API_KEY` from the environment when `./start.sh` runs. A blank value counts as absent. A missing `else`, an unknown key, or an env-var name that does not match `^[A-Z][A-Z0-9_]*$` fails validation.

### 3.3. The `auto` source sentinel

A `<SVC>_SOURCE` entry may be `auto`. It resolves once, before source validation, to the best source for this host, and then stays:

```yaml
env:
  values:
    COMFYUI_SOURCE: auto        # Apple Silicon → managed-localhost-mps; NVIDIA → container-gpu; else container-cpu
    LLM_PROVIDER_SOURCE: auto   # host Ollama → ollama-localhost; NVIDIA → ollama-container-gpu; else ollama-container-cpu
```

- **Durable keep.** `auto` keeps a concrete, valid, non-default value already in `.env`, such as a prior `auto` result or an explicit `--<svc>-source` override. The active profile must offer that value; otherwise `auto` resolves again. An override to the service default cannot be told apart from a cold regeneration, so it resolves again. To pin the default, commit the concrete id instead of `auto`.
- **Platform-adaptive.** Resolution follows the service manifest's ordered `sources.auto_prefer` list, matched against a host probe (`apple_silicon`, `nvidia_gpu`, `host_ollama`). Only options offered under the active `--profile` count. A service without `auto_prefer` falls back to its default with a warning.
- **Cold-regen safe.** A regenerated `.env` resolves again for this host. One committed manifest is right on a Metal Mac, an NVIDIA box and Linux CI.
- `doctor`'s `auto-sources` check reports each result and the capability that matched.

### 3.4. Manifest values and derived keys in `.env`

Before the first start, `doctor` and `compose validate` write the manifest values into `.env`, so the compose files resolve. An overlay that uses `${BACKEND_PLUGINS_DIR}` therefore validates on a fresh checkout.

After a start (which records `ATLAS_PROFILE_APPLIED`), they write only keys that `.env` does not have and the four derived keys. The derived keys are `BACKEND_PLUGINS_DIR`, `COMFYUI_CUSTOM_MODELS_FILE`, `COMFYUI_CUSTOM_NODES_FILE` and `OLLAMA_CUSTOM_MODELS`. A launch-time `--base-port`, `-p` or `--<svc>-source` therefore keeps its effect.

Do not also set a derived key in `env.values`: Atlas stops with an error that names both places.

Atlas records the derived keys it wrote in `ATLAS_DERIVED_KEYS`. When a later start with a manifest no longer derives a key (for example, you removed `model_sidecars.ollama`), Atlas blanks it. Without a manifest, nothing is blanked. A value from `.env.user` or `ATLAS_ENV_USER_FILE` is never blanked. A derived key that you edit by hand in `.env` stays Atlas-owned and is cleared with its source.

---

## 4. `profile`, `profile_overrides`

`bootstrapper/profiles.yml` ships two bundles: `default` (alias `dev`) and `prod`. Each names `sources`, `env` values and `host_bind_ip`. The manifest can name its default profile and override bundle fields. It cannot define new profile names.

```yaml
profile: dev                          # this project's default environment
profile_overrides:
  dev:
    sources: { comfyui: auto }        # delegate to the auto resolver
    env: { WEAVIATE_MEMORY_LIMIT: 4g }
  prod:
    sources: { comfyui: container-gpu }
```

- With no `--profile` flag, `./start.sh` uses the manifest's `profile:`. `--profile prod` turns on Prometheus and Grafana and sets log rotation.
- A CLI flag in this run beats a profile source. So does a non-empty `*_SOURCE` in the manifest's `env`, `.env.user` or `ATLAS_ENV_USER_FILE`.
- A profile's `env` cannot set a `*_SOURCE` key; use `sources`.
- `doctor`'s `profile` check shows the active bundle and where each value comes from.

Field rules and switch behaviour: [SOURCE Configuration Guide §9](../operations/source-configuration.md).

---

## 5. `compose_overlays`

A list of external Compose files. Atlas validates the paths before Compose runs and appends the files to the Compose command, with no symlinks into the submodule. Pass the same manifest to `./stop.sh`; otherwise `--cold` does not remove volumes declared only in these overlays.

---

## 6. `model_sidecars`, `custom_nodes`

```yaml
model_sidecars:
  comfyui:
    - ./models/comfyui-custom-models.yaml
  ollama:
    - llama3.2:latest
custom_nodes:                         # extra ComfyUI workflow nodes; merged into COMFYUI_CUSTOM_NODES_FILE
  comfyui:
    - ./comfyui/comfyui-krea2edit.yaml
```

- `model_sidecars.comfyui` lists ComfyUI model-catalog files. `model_sidecars.ollama` lists Ollama model tags. Both feed derived keys ([§3.4](#34-manifest-values-and-derived-keys-in-env)).
- List-valued model declarations from several manifests merge by ordered union.
- Atlas parses each `custom_nodes.comfyui` file strictly at load. A malformed node entry, such as a bad SHA, a non-GitHub repo or an unsafe name, fails validation.

---

## 7. `storage`

Declare object stores in the manifest. Atlas provisions everything, with no Compose override and no URL rewriting:

```yaml
# atlas.consumer.yml
name: daydreams
storage:
  buckets:
    - name: artifacts              # store handle (unique per consumer)
      bucket: daydreams-artifacts  # optional; default "<consumer>-<name>"
```

| Field | Required | Notes |
|---|---|---|
| `name` | yes | Store handle, `[a-z0-9][a-z0-9-]*`, unique per consumer. |
| `bucket` | no | Defaults to `<consumer>-<name>`. |
| `extra_buckets` | no | More bucket names that share the store's credential. |

Bucket names must follow S3 rules: 3 to 63 characters, lowercase alphanumeric with hyphens or dots, no `..`, `.-` or `-.`, and not IP-formatted. They must not collide with built-in buckets or other consumers' buckets ([§1.1](#11-reserved-names)).

Atlas compiles each store to the `MINIO_EXTRA_CONSUMERS` grammar ([Reusing Atlas §6.1.2](../operations/reusing-atlas.md#612-adding-parent-owned-minio-buckets)). It generates a scoped service-account credential once and keeps it across restarts. It writes the `minio-init` overlay (gitignored `volumes/minio/consumer-storage.compose.yml`) and exports these fields per store:

```text
ATLAS_STORE_DAYDREAMS_ARTIFACTS_BUCKET=daydreams-artifacts
ATLAS_STORE_DAYDREAMS_ARTIFACTS_INTERNAL_ENDPOINT=http://minio:9000     # write path
ATLAS_STORE_DAYDREAMS_ARTIFACTS_PUBLIC_ENDPOINT=http://localhost:${MINIO_PORT}  # browser read base
ATLAS_STORE_DAYDREAMS_ARTIFACTS_REGION=us-east-1
ATLAS_STORE_DAYDREAMS_ARTIFACTS_ACCESS_KEY_VAR=MINIO_DAYDREAMS_ARTIFACTS_ACCESS_KEY  # reference, not a raw secret
ATLAS_STORE_DAYDREAMS_ARTIFACTS_SECRET_KEY_VAR=MINIO_DAYDREAMS_ARTIFACTS_SECRET_KEY
```

Sign presigned GET URLs against the public endpoint (`…_PUBLIC_ENDPOINT`). Never sign against `minio:9000` and then rewrite the host, because that breaks the signature. Use boto3 with `endpoint_url=<public>`, or the reference presigner `bootstrapper/utils/s3_presign.py::presign_get_url`. See [MinIO §6](../../services/minio/README.md#6-consumer-integration-recipe).

---

## 8. `backend_plugins` and `plugin.yml`

`backend_plugins` lists plugin roots. Atlas mounts each into the backend at `/app/plugins` and sets `BACKEND_PLUGINS_DIR`. Setup and an example plugin: [Reusing Atlas §6.3](../operations/reusing-atlas.md#63-adding-backend-api-routes-via-the-plugin-seam).

### 8.1. Loading rules

At startup the backend calls `load_plugins(app)`. It scans each root in `$BACKEND_PLUGINS_DIR` (default `/app/plugins`; several roots are separated by `os.pathsep`):

- It first installs an optional shared `requirements.txt` in the root. If that fails, Atlas skips every plugin in that root for this start.
- A plugin is each subdirectory that is an importable Python package with a module-level `router` (a FastAPI `APIRouter`). Atlas installs its optional `requirements.txt`, imports it and adds the router to the app.
- A plugin whose requirements fail is logged with the requirements path and pip output, then skipped. A plugin that fails to import is logged and skipped. One bad plugin never crashes the backend.
- Requirements install into a writable plugin site (`pip --target $BACKEND_PLUGINS_SITE_DIR`, default `/tmp/atlas-plugins-site`). The seam creates it and puts it first on `sys.path`. The image runs as `appuser` without `$HOME`, so you need no tmpfs or `PYTHONUSERBASE` workaround.
- The seam empties that site at each start, so a version change does not leave two `*.dist-info` copies. It empties a custom `BACKEND_PLUGINS_SITE_DIR` only when it holds the `.atlas-plugin-site` marker. The seam writes the marker into an empty directory; a custom site that already holds packages gets a warning instead.
- The seam does nothing when the directory does not exist, so base Atlas is unaffected.

**Package names.** Atlas imports the package directory name as a top-level module. It must be unique across all plugin roots and must not match a module the backend imports (for example `observability`, `rag_ingestion` or `redis`). A name that matches an already-imported module is skipped, with status `skipped` in the inventory. A name that matches a library the backend imports later shadows that library, so avoid those names too.

**Paths without `plugin.yml`.** Plugins mount before the built-in routes. So a plugin without `plugin.yml` is skipped when a route's first path segment is a reserved built-in name or a path parameter (for example `@router.get("/{slug}")`). Give its router a literal prefix.

### 8.2. `plugin.yml`

A plugin package may ship an optional `plugin.yml` next to its `__init__.py`. Without it, the plugin loads as in §8.1. With it, the plugin declares a versioned, typed contract. Operators can see what is mounted and which env it needs. A missing variable shows as a startup diagnostic instead of a runtime 500. Per-plugin Kong auth and upstream timeouts also live here.

```yaml
# my-plugins/tableau/plugin.yml
plugin_manifest_version: 1
name: tableau                       # unique, kebab-case
route_prefix: /tableau             # must not overlap another plugin or a built-in route; no `.`/`..` segments
health_path: /tableau/health
docs_url: https://example.com
auth: key-auth                     # inherit | open | key-auth
connect_timeout: 120000            # optional; milliseconds
write_timeout: 120000              # optional; milliseconds
read_timeout: 900000               # optional; milliseconds
request_buffering: false           # optional; stream uploads to the plugin
depends_on: [litellm, weaviate]    # optional; dependency services
env:
  - name: TABLEAU_EXECUTION
    type: enum
    values: [fake, comfyui]
    default: comfyui
  - name: LITELLM_MASTER_KEY
    required: true                 # missing → startup + doctor warning
    secret: true                   # masked everywhere (inventory, doctor, logs)
```

- **Inventory.** `GET /plugins` lists each mounted plugin. It shows name, route prefix, health and docs paths, auth mode, timeouts, buffering flags (`kong_route`) and declared env. It also shows load status (`loaded` / `skipped` / `error`). Secret values show as `***`; variable names and flags are visible. `/plugins` needs a Backend service token (for example `BACKEND_INTERNAL_API_TOKEN`), whatever `BACKEND_KONG_AUTH` is.
- **Validation.** The seam validates declared env at boot, and `./start.sh doctor` checks it again before launch. Both report missing required variables and enum or type mismatches by plugin and variable name. Secret values are never printed.
- **Malformed manifest.** A malformed `plugin.yml` does not fall back to manifest-less loading. That plugin is not loaded (status `error`, with the validation message). The other plugins stay healthy.
- **Conflicts.** Atlas rejects duplicate plugin names, overlapping prefixes, and prefixes that shadow a reserved built-in route name, before mounting. It skips a plugin whose router has a path outside its declared `route_prefix`. The schema lists the reserved names. A manifest-less plugin cannot mount a path under another plugin's prefix, and a manifest cannot declare a prefix over such a path. A path parameter such as `/up/{name}` counts from its literal start.
- **Route types.** Unless `auth` is `open`, a plugin router may hold only FastAPI routes (`@router.get`, `@router.websocket`, …). Atlas refuses a Starlette `router.add_route` or `add_websocket_route` handler, because the auth dependency cannot apply to it.
- **Auth.** `auth: key-auth` puts Kong key-auth on the plugin's `route_prefix`, and FastAPI checks the same `BACKEND_KONG_API_KEY`, so the direct port cannot bypass it. `auth: open` is an explicit public opt-out. `auth: inherit`, and plugins without a manifest, use the Backend identity boundary. Distinct per-prefix credentials are not supported yet.
- **Path safety.** Atlas rejects a `route_prefix` or `health_path` with a `.` or `..` segment or a trailing newline. Kong normalizes paths on load, so `/x/../api` would become an open `/api`.
- **Kong timeouts.** `connect_timeout`, `write_timeout` and `read_timeout` are optional integers in milliseconds, from `1` to `2147483646`. An omitted read or write timeout gets the backend's timeout, at least 3,630,000 ms. An omitted connect timeout keeps Kong's 60,000 ms. A plugin that sets a timeout gets its own Kong backend service; auth still applies per prefix.
- **Streaming.** `request_buffering: false` and `response_buffering: false` (Kong's default is `true`) make Kong stream bodies. The plugin can then refuse an oversized upload after the first bytes. A plugin that sets either flag also gets its own Kong service.
- **Body limit.** With `request_buffering: false`, the backend's 16 MiB body limit does not apply under the plugin's prefix. The plugin must enforce its own limit. The backend checks `inherit` or `key-auth` before it reads the body. Read `request.stream()` and count the bytes, because a `Body`, `Form` or `File` parameter reads the whole body first.

For `key-auth` HTTP requests, send the key in the `apikey` header. The header wins over a query parameter:

```bash
curl -H "apikey: ${BACKEND_KONG_API_KEY}" \
  http://api.localhost:${KONG_HTTP_PORT}/plugin-prefix/health   # default 63000
```

Browser WebSocket APIs cannot set headers, so those clients may send `apikey` as a query parameter in the handshake. URL-encode the value. Atlas redacts that value from Uvicorn HTTP and WebSocket logs, and Kong's access log omits query strings. Do not record full WebSocket URLs in your own client logs or telemetry.

`plugin_manifest_version` is a fixed contract version, currently `1`. A manifest with another version is not loaded (status `error`), not misread. The canonical schema is `bootstrapper/schemas/plugin.schema.json`.

---

## 9. `litellm_models`

A plugin that serves an OpenAI-compatible route can appear as a LiteLLM model. Open WebUI, n8n, the backend and other LiteLLM clients then find it in `/v1/models`, with no registration script:

```yaml
# atlas.consumer.yml
name: rag-showcase
backend_plugins:
  - ./backend/plugins            # serves /graph-rag, /vanilla-rag, … (§8)
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

Atlas never calls the LiteLLM admin API. On `./start.sh`, the bootstrapper resolves each row and writes the gitignored `volumes/litellm/consumer-models.yaml`. `litellm-init` then appends those rows to `config.yaml`, after the stack-owned and catalog rows.

**No reload is needed.** Every `./start.sh` recreates the stack with `docker compose up --force-recreate`. LiteLLM waits for `litellm-init` (`service_completed_successfully`) before it boots. So on every start, cold or warm, your aliases appear in `/v1/models`. Do not `docker restart` LiteLLM or call its admin API from your launcher.

- **Ownership.** Rows are stamped `model_info.atlas_owner: <consumer>`. A removed manifest removes only that consumer's rows.
- **`api_base`** must resolve to a clean URL on an allowlist of in-network Atlas endpoints (only `${ATLAS_BACKEND_INTERNAL}`, that is `http://backend:8000`). An external host, userinfo credentials, a query string or a fragment is rejected at load.
- **Secrets.** Declare them by `api_key_var` (an env var name), never a literal `api_key`. The value never appears in a generated file, log or doctor output. A row without `api_key_var` sends the dummy key `sk-noauth`, never the stack's `OPENAI_API_KEY`.
- `api_key_var` must not name a variable the LiteLLM container already sets (for example `DATABASE_URL` or `OPENAI_API_KEY`), because the overlay would replace it.
- **Aliases** must be globally unique and must not shadow a stack-owned or catalog model name ([§1.1](#11-reserved-names)).
- `./start.sh doctor` checks each model's route against the plugin's `plugin.yml` `route_prefix` ([§8.2](#82-pluginyml)).

A RAG-showcase-style consumer uses this block in place of a bespoke `register_models.py`.

---

## 10. `n8n_workflows`

Atlas can import, activate and readiness-check a consumer's n8n workflows, in place of a hand-written import, restart and poll script:

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
          probe: true                         # call this webhook after import; default false
```

On `./start.sh`, the bootstrapper normalizes each workflow JSON. It sets the activation policy and strips the runtime-state fields `staticData` and `pinData`. It writes the gitignored `volumes/n8n/consumer-workflows/` and a `plan.json`, and adds an overlay that runs an Atlas-owned `n8n-seed` container. After n8n is healthy, the seed imports each workflow with `n8n import:workflow`.

- **Ids.** Each `id` (at most 21 characters, because n8n ids are limited to 36) imports as `atlas-consumer-<id>`. A new start updates the workflow in place. It never creates a duplicate and never overwrites a workflow built by hand in the UI.
- **Removal.** A removed manifest or workflow drops only its own generated JSON on the next start. With `N8N_API_KEY` set, the seed also deactivates and deletes an undeclared `atlas-consumer-*` workflow, so no live webhook is orphaned. After the last workflow is removed, every start runs the seed with an empty plan. This continues until you declare a workflow again or delete `volumes/n8n/consumer-workflows/`.
- **Credentials** may only be referenced by an `{id, name}` mapping. A raw secret or a credential payload with extra keys is rejected. Generated files and seed logs never hold workflow content.
- **Validation.** Malformed JSON, an invalid `active` or `version`, a checksum mismatch and duplicate webhook routes are rejected at load.
- **Probes.** After import, the seed calls only the webhooks that set `probe: true`. It logs a warning when the status is not `expect_status` (default `200`). Other webhooks are used only to detect duplicate routes. Set `probe: true` on a `POST` webhook only when a call is safe, because it can trigger side effects.
- **API key warning.** `./start.sh doctor` warns when an active workflow declares webhooks and `N8N_API_KEY` is not set.
- **Seed bounds.** Each seed HTTP request times out after `N8N_SEED_HTTP_TIMEOUT_MS` (default `10000`). Each n8n CLI command times out after `N8N_SEED_COMMAND_TIMEOUT_MS` (default `120000`). The seed drops an HTTP response larger than `N8N_SEED_MAX_RESPONSE_BYTES` (default `1048576`).
- **No API key.** n8n Community Edition cannot activate a workflow over its API without a key. Atlas then restarts the n8n container once after seeding, to register the webhook. A failed import or activation is logged per workflow, and the seed container always exits 0.

---

## 11. `rag_ingestion_profiles`

A consumer with a RAG corpus can declare ingestion profiles. Atlas then owns the ingestion lifecycle: discover → parse → chunk → embed → vector-store write → LightRAG upload → drain → finalize.

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
      parser_order: [docling, tika, plain_text]  # first parser that succeeds wins; plain_text (appended last) decodes text files only
      chunker: { strategy: recursive, chunk_size: 700, overlap: 120 }  # Chonkie strategy
      vector_targets:
        - { backend: weaviate, collection_prefix: RagShowcase, on_unavailable: fail }
      graph_targets:
        - { backend: lightrag, mode: upload_documents, wait_for_extraction: true, timeout_seconds: 3600, on_unavailable: skip }
```

Accepted values: `parser_order` entries `docling`, `tika`, `crawl4ai`, `plain_text`; `chunker.strategy` `token`, `recursive`, `semantic`; vector backend `weaviate`; graph backend `lightrag` with mode `upload_documents`.

On `./start.sh`, the bootstrapper validates and normalizes each profile and hashes it into a stable `revision`. It writes the gitignored `volumes/backend/rag-ingestion-profiles.json` and mounts it into Backend and Celery at a reserved internal path. Both services get the same `RAG_INGESTION_PROFILES_FILE`, Redis state URL, upstream endpoints and resource limits. A MinIO corpus bucket must also be in the same consumer's `storage.buckets`. Atlas then injects only that store's credential variable names into both services.

Submit and poll jobs through the backend API:

```bash
# Submit (async when the Celery tier is enabled, else runs in-request); returns an ingestion id.
curl -XPOST "$BACKEND_URL/api/rag/ingestions" -H 'content-type: application/json' \
     -d '{"profile":"showcase-default"}'
# Poll machine-readable status (phases, counts, timing, per-file errors).
curl "$BACKEND_URL/api/rag/ingestions/<ingestion_id>"
```

You own mounted corpora. Mount the same read-only host directory at `RAG_INGESTION_CORPUS_ROOT` in both services, so the corpus does not change when Celery is on:

```yaml
services:
  backend:
    volumes:
      - ./corpus:/app/corpus:ro
  celery-worker:
    volumes:
      - ./corpus:/app/corpus:ro
```

- **Corpus paths.** A `mount` corpus must be a relative path under `RAG_INGESTION_CORPUS_ROOT` (default `/app/corpus`). An absolute path, `~` or a `..` segment is rejected. A MinIO corpus must name a store the same consumer declared. A missing path is never an empty corpus: submission returns HTTP 400, and a worker without the backend's mount fails the job.
- **Bounds.** Discovery stops at `RAG_INGESTION_MAX_FILE_BYTES` (default 100 MiB), `RAG_INGESTION_MAX_CORPUS_BYTES` (default 1 GiB) and `RAG_INGESTION_MAX_FILES` (default 10,000), before content is held in memory.
- **Chunk limit.** Chunking holds about 20–25 bytes of memory per character, so Atlas chunks a document only up to 20,000,000 characters. A longer document is a per-file chunk error, and its earlier vectors are kept.
- **Parsing.** Parsers run in `parser_order`. The `plain_text` entry that Atlas appends last accepts only text (UTF-8, Windows-1252, or BOM-marked UTF-16/32). A binary file that Docling and Tika failed on is a per-file error. Its earlier vectors are kept, and it is never indexed as raw bytes.
- **Targets.** A profile may declare at most one `graph_target` (Atlas has one LightRAG endpoint). Each target declares `on_unavailable: fail | skip`, so a disabled backend fails or skips visibly. A `wait_for_extraction: true` graph target polls LightRAG until idle or `timeout_seconds`, then finalizes.
- **Phases.** Each job records its phases (`discover → parse → chunk → embed → vector_write → lightrag_upload → drain → finalize`). Errors are isolated per file, so one bad file does not fail the batch.
- **Idempotency.** The job key is consumer + profile + revision + corpus fingerprint + embedding model. A resubmit of unchanged content returns the existing job. Changed content or a changed `LITELLM_EMBEDDING_MODEL` creates a new job.
- **Leases.** Each execution holds an owner-fenced Redis lease (`RAG_INGESTION_EXECUTION_LEASE_SECONDS`, default 30), so duplicate deliveries and lost workers cannot write twice. The lease is per ingestion, not per profile. Two jobs of one profile can run at once, so let one finish before you submit the next.
- **Embedding identity.** Each Weaviate class records its embedding model and vector dimension. A class without that record is adopted when its vectors have the current size. Otherwise, the first ingestion after a model or size change drops the class and writes the whole corpus again, so two models never mix.
- **Rebuild window.** During that rebuild, queries see only the objects written so far. If it fails, the class stays partial until a later run succeeds. Sources that failed in the rebuilding run have no vectors; the job note names them.
- **Class names.** Profile names that map to one class (`a-b` and `a.b` both become `{prefix}_a_b`) are rejected at load. Object ids include the profile name.

**Time limits.** With the Celery tier on, one task runs the whole job, with its own soft and hard limits. When the soft limit expires, the job is recorded `failed`, so a resubmit starts a new job.

Atlas refuses a submission (HTTP 400, naming both values) when the graph targets' `timeout_seconds` add up to the soft limit or more. Parsing, embedding and writing use the same time, so raise both limits for large corpora. Defaults and rules: [Celery §3](../../services/celery/README.md#3-configuration).

Job listing (paged by `X-Atlas-Next-Cursor`), the shared Redis state store and the non-durable `BACKEND_STATE_STORE_MODE=memory` mode are described in [Backend API §4](../../services/backend/README.md).

The unit suite checks the contract, phases, idempotency and path safety with fake upstreams. Live ingestion against Docling, Tika, Weaviate and LightRAG is an optional live test. The field-level contract is in the backend's ingestion module and `app/app/tests/test_rag_ingestion.py`.

---

## 12. `lightrag_query_profiles`

Each profile is a named bundle of LightRAG per-query settings. Open WebUI users can then pick "graph-rag local k=30" or "graph-rag hybrid k=10", and the app does not hard-code them.

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
      enable_rerank: false                     # true requires LIGHTRAG_RERANK_ADAPTER_ENABLED=true
    - name: graph-local-wide
      mode: local
      top_k: 30                                # chunk_top_k / max_total_tokens omitted → env default
      query_llm_model: gpt-4o                  # optional model references (a LiteLLM alias / handle)
      litellm_alias: graph-rag-local-wide      # optional: surface this flavor as a LiteLLM model
```

On `./start.sh`, the bootstrapper validates and normalizes each profile and hashes it into a stable `revision`. It writes the gitignored `volumes/backend/lightrag-query-profiles.json`. It mounts that file into the backend at `/atlas-consumer-config/lightrag-query-profiles.json` and sets `LIGHTRAG_QUERY_PROFILES_FILE` to it. A plugin reads the registry to resolve a flavor by name. `doctor` lists the profiles and warns when profiles exist but `LIGHTRAG_SOURCE` is disabled.

**Profiles and role settings.** The `LIGHTRAG_EXTRACT_*`, `LIGHTRAG_KEYWORD_*` and `LIGHTRAG_QUERY_*` env vars set the one deployment-wide default model for each LightRAG role. A query profile is a named flavor chosen per query, and many can exist. Profiles do not replace the env defaults; they add to them.

- **Mode and bounds.** `mode` is required (`local | global | hybrid | mix | naive`). `top_k` and `chunk_top_k` are optional positive integers up to 10,000; `max_total_tokens` goes up to 2,000,000. Precedence is request, then profile, then the `LIGHTRAG_QUERY_*` env default.
- **Model references.** `query_llm_model` and `embedding_model` are model handles (a LiteLLM alias or `provider/model`), never secrets.
- **Rerank.** `enable_rerank: true` is rejected at load unless `LIGHTRAG_RERANK_ADAPTER_ENABLED=true`, in `.env` or in a manifest's `env`. Reranking also needs `LIGHTRAG_SOURCE` and `TEI_RERANKER_SOURCE` enabled; `doctor`'s `lightrag-rerank-adapter` check warns when one is disabled. Why LightRAG needs the backend adapter: [Backend API §5.1](../../services/backend/README.md).
- **Ownership.** Profile names are globally unique, and a removed manifest drops only its own profiles. With no profiles, LightRAG keeps its single default. The registry holds only settings and model names, never credentials.
- **`litellm_alias`** also emits a consumer-owned [`litellm_models`](#9-litellm_models) row, so the flavor is a selectable model. Atlas does not serve that row yet. It points at `http://backend:8000/chat/completions`, which no stack route answers, so the alias returns 404 until a consumer plugin serves that path (#1453).

---

## 13. `managed_host_services`

A Metal or MLX service cannot get the GPU through a Linux container on macOS. That is why Atlas runs ComfyUI-MPS, vLLM-Metal and the Blender MCP bridge as host processes. Declare your own (for example an MLX segmentation service) here, and Atlas manages its lifecycle, with no fork and no hand-made pid files.

```yaml
# atlas.consumer.yml
managed_host_services:
  - name: sam3-segment            # [a-z0-9][a-z0-9-]* — owns ~/.atlas/<name>
    workdir: services/sam3        # must resolve INSIDE your consumer root
    command: python -m sam3_service   # argv; never run through a shell
    port: 8799
    venv:
      python: "3.13"              # bare version → the `python3.13` binary
      metal: true                 # preflight refuses off macOS
      requirements: services/sam3/requirements.txt
    health:
      path: /health               # a declared path implies an HTTP probe; must start with /
      expect_json: { status: ok }
```

| Field | Required | Notes |
|-------|----------|-------|
| `name` | yes | `[a-z0-9][a-z0-9-]*`. Becomes `~/.atlas/<name>` and `ATLAS_<NAME>_HOST_ENDPOINT`, so two loaded consumers cannot share one. Reserved: `comfyui-mps`, `vllm-metal`, `blender-mcp`, and the export's service names (`backend`, `litellm`, `comfyui`, `asset-worker`, `ollama`, `minio`, `weaviate`, `neo4j`, `n8n`, `redis`, `supabase`). The state directory is per user, so separate consumer repos on one machine also need distinct names. |
| `command` | yes | String (POSIX-split) or list. Argv, **not** a shell line. |
| `port` | yes | 1–65535. |
| `workdir` | no | Defaults to the manifest's directory. Must resolve inside the consumer root. |
| `bind` | no | Defaults to `127.0.0.1`. |
| `env` | no | Extra environment for the process. A boolean is written as `true` or `false`; `null` is rejected. |
| `venv` | no | `python`, `metal`, `requirements`, `packages`. A leading `python`/`python3` in `command` is rewritten to the venv interpreter. |
| `install` | no | Extra argv steps run after dependency install. |
| `health` | no | `kind` (`tcp`/`http`), `path`, `expect_json`, `timeout`. Defaults to a TCP port knock. `timeout` (default 5s) also bounds each readiness probe during `start`, capped by the time left in the start wait. |
| `allow_remote` | no | Required to bind anything other than loopback. |

> **Three deliberate constraints.** (1) A declared command is argv passed straight to `subprocess`, never a shell. A semicolon in a value is text, not a second command. (2) A non-loopback `bind` is refused without `allow_remote: true`, because these processes have no authentication. (3) `workdir` and `venv.requirements` must resolve inside your consumer root, because Atlas executes what this block declares.

> **`metal: true` does not install a Metal wheel.** It is a preflight guard that fails off macOS. Put the torch or MLX pin in your own `requirements` file, where it is visible and reviewable.

`./start.sh` and `./stop.sh` do not start or stop a declared service, and neither does `--stop-managed-hosts`. Use the `managed-host` commands:

```bash
./start.sh managed-host list                    # what is declared, and its endpoint var
./start.sh managed-host preflight sam3-segment  # read-only: bind, venv, command, port
./start.sh managed-host install   sam3-segment  # create the venv, install deps
./start.sh managed-host start     sam3-segment  # spawn, wait for the port to open; restarts a process launched on another port or bind
./start.sh managed-host status    sam3-segment  # pid / running / port-open
./start.sh managed-host health    sam3-segment  # run the declared probe
./start.sh managed-host stop      sam3-segment  # SIGTERM, then SIGKILL after a grace window
./start.sh managed-host remove    sam3-segment  # stop + delete ~/.atlas/sam3-segment
```

`./start.sh doctor` has a `managed-host-services` row that preflights each declared service. A pid file whose pid now belongs to a younger process shows as a `pid-file` warning. `start` replaces that record without signalling the process. The endpoint contract gains `ATLAS_SAM3_SEGMENT_HOST_ENDPOINT` ([Reusing Atlas §6.5](../operations/reusing-atlas.md#65-exporting-the-endpoint-contract-endpoints-export)). Its scheme follows the probe: an `http` probe exports `http://`, a `tcp` probe exports `tcp://`.

---

## 14. `blender_mcp`

`blender_mcp: {instances: N}` (1 to 16) sizes the Atlas-managed headless Blender pool under `BLENDER_MCP_SOURCE=managed-localhost`. It sets `BLENDER_MCP_INSTANCES`. Instance `i` listens on `BLENDER_MCP_LOCALHOST_PORT + i`. Atlas starts and health-checks every instance, and at the next start stops instances above a smaller N.

`./stop.sh --stop-managed-hosts` stops them all. A consumer reads the instances from `ATLAS_BLENDER_MCP_HOST_ENDPOINTS` ([Reusing Atlas §6.5](../operations/reusing-atlas.md#65-exporting-the-endpoint-contract-endpoints-export)). Omitting the block, or `instances: 1`, keeps the single bridge.
