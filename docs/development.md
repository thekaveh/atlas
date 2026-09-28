# 9.1. Development

## 1. Service Admission

Adding a service requires a manifest, compose fragment when applicable, topology row, docs regeneration, route checks, and CI validation.

## 2. Parent-Repo Consumer Layout

Submodule consumers should keep project-owned overlays, branding, wrapper scripts, and secret references in the parent repository while `infra/` remains a pinned Atlas checkout. The recommended shape is:

- `atlas.consumer.yml` in the parent repository.
- `compose/<name>-overlay.yml` in the parent repository and referenced from `compose_overlays`.
- `backend/plugins/` (each package optionally declaring a typed `plugin.yml`) and model sidecars referenced from the manifest when needed.
- `scripts/start-infra.sh` as the parent-owned launcher that force-sets `PROJECT_NAME`, `BRAND_*`, and required `*_SOURCE` values.

Use `./infra/start.sh --consumer ./atlas.consumer.yml` so Atlas can validate
paths, merge env values, include external Compose overlays without symlinks,
and list registered consumers in the launch overview. Do not rely on "set only
if absent" helpers for critical `*_SOURCE` keys. Atlas's `.env.example`
intentionally contains defaults, so project wiring should force-set required
values in the manifest/env overlay or pass explicit `--<service>-source` flags.
Explicit source flags override `--track`, which is how consumers request an
extra service outside a track or disable a service the track would normally
prompt for.

Existing integrations that still use the back-compatible `_user` discovery
slot can keep `scripts/setup-overlay.sh` as the idempotent wrapper that creates
`infra/services/_user/<name>/compose.yml` before start; new integrations should
prefer the manifest.

Parent-owned object-storage consumers should declare a `storage:` block in `atlas.consumer.yml` (Atlas compiles it, generates scoped credentials once, writes the `minio-init` overlay, and exports stable per-store `ATLAS_STORE_<KEY>_*` fields — internal vs public-read endpoints, region, and credential references). Under the hood this compiles to `MINIO_EXTRA_CONSUMERS`, for example `daydreams:MINIO_BUCKET_DAYDREAMS:MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY`, which `_user` overlays may still set directly; the hook creates the extra bucket and scoped MinIO service account without forking Atlas. Presign browser GETs against the **public** endpoint (never rewrite a signed URL) using boto3 `endpoint_url=<public>` or the reference presigner `bootstrapper/utils/s3_presign.py`.

Before committing a parent consumer update, verify the `infra/` submodule status is clean except for ignored `.env`, `.env.user`, `_user` slots, and runtime volumes; the parent pins a specific Atlas commit or tag; and overlays remain parent-owned.

## 3. Required Docs Checks

Pull-request titles must be Conventional Commits subjects (`type(scope)!: summary`) and the generated block at the top of the changelog's Unreleased section must match its recorded range; the required lint job runs `scripts/release_notes.py --check-title` and `--check-changelog` (see [Releasing](operations/releasing.md) §6).

`make docs-check` also enforces the critical-page contract in `docs/critical-pages.yaml`: the security, prerequisites, support, release, and recovery pages it names must be declared in the manifest, reachable from the documentation map, and render each listed section with a body on the repo, site, and wiki surfaces. The contract names pages by manifest id and sections by stable title only; it never copies policy prose. Its `external_references` list records community destinations (such as the issue tracker) that a page may point to; they are permitted references, not substitutes for a self-contained page.

`bootstrapper/tests/test_support_route.py` keeps every advertised first-party support destination live: reader-facing pages may not link GitHub Discussions while the feature is disabled on the repository, and the README, documentation map, and troubleshooting guide must route bugs, questions, and security reports separately. A maintainer who enables and moderates Discussions removes that guard in the same change that relinks it.

`scripts/check_doc_links.py` validates relative Markdown links and raw HTML `<a href>` / `<img src>` targets against the repository tree, so canonical pages must link files GitHub can open (`quick-start/index.md`, never the site's `quick-start/`); the generated surfaces translate those targets themselves.

```bash
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper python scripts/check-docs-drift.py
make docs-check
uv run --project bootstrapper python -m scripts.notebook_reproducibility
uv run --project bootstrapper python scripts/check-compose-source-deps.py
uv run --project bootstrapper python scripts/check-kong-routes.py
uv run --project bootstrapper python scripts/validate_research_schema.py --all
uv run --project bootstrapper python scripts/check-track-membership.py
(cd services/docling/provider/localhost && uv lock --locked)
```

### 3.1. Documentation build assets

The site build downloads external assets. The Material `privacy` plugin, which `scripts/docs/build_docs.py` enables so rendered pages make no third-party requests (#841), self-hosts the theme fonts and the Mermaid bundle by fetching them during `mkdocs build` and caching them under `.cache/plugin/privacy` (gitignored). `docs/external-assets.yaml` records all 20 of them: the Google Fonts stylesheet, 18 versioned font files, and `mermaid@11` from unpkg, each with its URL and, where the URL pins the content, a sha256.

**Policy: online-only on a cold cache, offline from a warm one.** A clean checkout needs network access to `fonts.googleapis.com`, `fonts.gstatic.com` and `unpkg.com` for its first build; every later build reuses the cache and fetches nothing, which is also how CI runs (its `actions/cache` step is keyed on the files that decide the asset set, including the inventory). The two alternatives were rejected:

- **Vendoring** would commit about 3.8 MB of binaries (the Mermaid bundle alone is 3.6 MB), require replacing Material's font loading with hand-written `@font-face` rules, and still leave the Mermaid URL, which mkdocs-material's own JavaScript bundle chooses, to be tracked by hand on every theme upgrade.
- **A seeded cache** would need a script that writes files where the plugin expects them, but those paths are plugin-internal (the stylesheet is stored as `css.<hash>.css` behind a symlink) and can change with any mkdocs-material release; the seed step would itself be online, so it would add a second fetcher without removing the network requirement.

What makes the online requirement acceptable is that it is now explicit and checked:

- `make docs-build`, `make docs-check` and `make docs-serve` run `python -m scripts.docs.external_assets --preflight` before MkDocs. A cached asset needs no network. A missing one is probed, and if it cannot be fetched the build stops there with the asset's name and URL, rather than a bare `Aborted with 1 warnings in strict mode` after the build.
- `make docs-assets-verify` diffs the cache against the inventory: missing, unexpected and checksum-mismatched files. After deleting `.cache/plugin/privacy` and running `make docs-check`, it proves the inventory is still the complete list.
- After changing the theme fonts or upgrading mkdocs-material, rebuild from a cold cache, run `uv run --project bootstrapper python -m scripts.docs.external_assets --print-inventory`, and review the difference against `docs/external-assets.yaml` before committing it.

The built pages themselves make no third-party requests: the plugin rewrites every reference to the self-hosted copy.

## 4. Repository layout

The top-level repository layout is as follows, with `services/` limited to a representative subset (see `services/` for the full list):

```
atlas/
├── bootstrapper/              # Python startup, SOURCE parsing, port/Kong generation, wizard
│   ├── services/              # Manifest loader, validator, env_assembler, hooks, sc_synthesizer
│   ├── schemas/               # JSON Schemas for service.yml manifests
│   ├── tests/                 # 2,300+ tests (loader, validator, byte-equiv, source-permutation, hooks)
│   ├── tools/                 # validate_fragments CLI lint
│   └── start.py / stop.py     # Entry points
├── services/                  # 57 service.yml manifests + 3 doc-only folders (representative subset shown below; see services/ for the full list)
│   ├── globals/               # Project-wide vars (PROJECT_NAME, BASE_PORT, BRAND_*, tier ordering)
│   ├── supabase/              # supabase-db, db-init, meta, storage, auth, api, realtime, studio
│   │   ├── service.yml        # Manifest: env vars, source variants, deps, runtime_sc slice
│   │   ├── compose.yml        # Compose fragment for the family
│   │   └── db/                # SQL init scripts + snapshots (bind-mounted into supabase-db-init)
│   ├── litellm/               # LiteLLM gateway + init
│   │   ├── service.yml
│   │   ├── compose.yml
│   │   ├── init/              # litellm-init Dockerfile + scripts (config.yaml renderer)
│   │   └── models.yaml        # Curated cloud-provider model catalog (per-service SoT)
│   ├── ollama/                # ollama + ollama-pull (pull/ scripts); models.yaml = Ollama catalog SoT
│   ├── redis/                 # Redis cache/queue substrate (AOF persistence, shared by n8n/Kong/LiteLLM/owui/LightRAG)
│   ├── weaviate/              # weaviate + multi2vec-clip + weaviate-init
│   ├── comfyui/               # comfyui + comfyui-init (init/ scripts); models.yaml + custom-models.yaml = ComfyUI catalog SoT
│   ├── n8n/                   # n8n + n8n-worker + n8n-init (with init/ assets, workflows-stage/)
│   ├── open-webui/            # open-web-ui + open-webui-init (with extras/ tools+functions)
│   ├── hermes/                # hermes + hermes-init (with init/ scripts & templates)
│   ├── minio/                 # minio + minio-init (with init/ bucket provisioning scripts)
│   ├── backend/               # FastAPI backend (with app/ source code)
│   ├── jupyterhub/            # JupyterHub (with build/ Dockerfile + notebooks)
│   ├── neo4j/                 # Neo4j (with build/ Dockerfile + scripts)
│   ├── parakeet/              # STT engine (parakeet-gpu, with provider/ source code)
│   ├── speaches/              # Unified TTS+STT engine (CPU/GPU)
│   ├── chatterbox/            # TTS engine (GPU, voice cloning)
│   ├── tts-provider/          # Virtual manifest — TTS source selector (with provider/ host notes)
│   ├── docling/               # Document processor (with provider/ source code)
│   ├── searxng/               # SearXNG (with config/ settings.yml)
│   ├── local-deep-researcher/ # Research agent (with build/ Dockerfile)
│   ├── lightrag/              # LightRAG graph-RAG server + init (opt-in via LIGHTRAG_SOURCE)
│   ├── tei-reranker/          # TEI reranker (opt-in via TEI_RERANKER_SOURCE)
│   ├── openclaw/              # OpenClaw agent gateway + init
│   ├── kong/                  # Kong API gateway
│   ├── ray/                   # Ray distributed-compute substrate (head + workers)
│   ├── prometheus/            # Metrics scraper + TSDB (with config/ scrape jobs, opt-in via PROMETHEUS_SOURCE)
│   ├── grafana/               # Observability dashboards + unified alerting (with config/ provisioning, opt-in via GRAFANA_SOURCE)
│   ├── spark/                 # Apache Spark standalone cluster — master + worker + history + init (opt-in via SPARK_SOURCE)
│   ├── zeppelin/              # Apache Zeppelin Spark-first notebook UI (opt-in via ZEPPELIN_SOURCE; gated on Spark)
│   ├── airflow/               # Apache Airflow 3.x DAG orchestrator (with build/ Dockerfile + dags/, opt-in via AIRFLOW_SOURCE)
│   ├── cloud-providers/       # Virtual manifest — OpenAI/Anthropic/OpenRouter toggles
│   ├── stt-provider/          # Doc-only — aggregate STT provider documentation
│   ├── doc-processor/         # Doc-only — aggregate doc-processor documentation
│   ├── multi2vec-clip/        # Doc-only — aggregate multi2vec-clip documentation (container ships inside weaviate/)
│   └── _user/                 # (Gitignored) downstream submodule consumers' overlay slot
├── docs/                      # User, service, operations, diagram, and planning docs
│   ├── CONTRIBUTING-services.md  # How to add a new service to the modular layout
│   └── …
├── scripts/                   # Top-level utility scripts (e.g. migration helpers)
├── docker-compose.yml         # ~90-line thin shell — include: list pulling each fragment
├── .env.example               # Configuration template (auto-generated from manifests via env_assembler; byte-equivalence enforced by tests)
├── start.sh / stop.sh         # Entry points
└── .github/workflows/         # CI: services-lint (manifest lint+tests, compose byte-equiv+source-permutation, docs-drift+audits, build-validation)
```

Top-level is intentionally minimal: `bootstrapper/`, `docs/`, `scripts/`, `services/`. Every service lives entirely under its `services/<name>/` folder — init scripts, source code, build context, config files — so opening a service folder shows everything that defines it.
