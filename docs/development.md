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
├── services/                  # 56 service.yml manifests + 3 doc-only folders (representative subset shown below; see services/ for the full list)
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
├── docs/                      # User, service, deployment, diagram, and planning docs
│   ├── CONTRIBUTING-services.md  # How to add a new service to the modular layout
│   └── …
├── scripts/                   # Top-level utility scripts (e.g. migration helpers)
├── docker-compose.yml         # ~90-line thin shell — include: list pulling each fragment
├── .env.example               # Configuration template (auto-generated from manifests via env_assembler; byte-equivalence enforced by tests)
├── start.sh / stop.sh         # Entry points
└── .github/workflows/         # CI: services-lint (manifest lint+tests, compose byte-equiv+source-permutation, docs-drift+audits, build-validation)
```

Top-level is intentionally minimal: `bootstrapper/`, `docs/`, `scripts/`, `services/`. Every service lives entirely under its `services/<name>/` folder — init scripts, source code, build context, config files — so opening a service folder shows everything that defines it.
