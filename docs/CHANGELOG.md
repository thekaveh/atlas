# Changelog

All notable changes to Atlas (formerly GenAI Vanilla) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — 2026-07-14 — Startup and API safety boundaries

- **Project-scoped cold cleanup** — `start.sh --cold` and `stop.sh --cold` now remove only the active Atlas Compose project's containers, orphans, and named volumes. Cleanup failures stop startup before secret rotation and propagate a nonzero exit status instead of falling through to a partial launch.
- **Managed-host lifecycle rollback** — native ComfyUI MPS and vLLM Metal processes now start at the launch boundary under an ownership lock, roll back only when the current invocation created them and the stack fails to converge, and remain discoverable by `stop.sh` after their SOURCE selection changes. Native teardown failures propagate a nonzero stop status.
- **Readiness and private runtime files** — Neo4j now publishes a real Cypher health check for dependents that require `service_healthy`; generated `.env` and endpoint-export files are written atomically with owner-only permissions.
- **Ray job API authorization** — every Backend `/api/ray` route now requires the auto-generated `RAY_JOB_API_TOKEN` as an HTTP bearer token, including calls made through the direct Backend port. Ray's native unauthenticated dashboard, GCS, and client ports are bound to loopback by default.
- **Non-destructive permission recovery** — an unwritable bind-mount directory is never deleted as a repair strategy; Atlas preserves its contents and reports the ownership command needed before retrying.
- **Truthful privileged hosts cleanup** — `stop.sh --clean-hosts` elevates only the hosts-file mutation and exits nonzero when the requested cleanup fails, while the repository workflow continues to reject whole-process sudo execution.
- **Pinned Local Deep Researcher runtime** — Local Deep Researcher now materializes a manifest-owned full upstream commit SHA, verifies the upstream `uv.lock` digest, and synchronizes a private environment from Atlas' committed hash-pinned runtime lock. Restarts reuse the detached checkout without pulling a mutable branch or resolving either project or LangGraph CLI dependencies again.
- **Locked CI environments** — all three `services-lint` jobs now synchronize bootstrapper dependencies with `--locked`, so tests and audit scripts fail on lock drift instead of silently resolving or modifying a different dependency graph.
- **CLI/manifest parity** — `--comfyui-source` now accepts the manifest-supported `managed-localhost-mps` mode, Prometheus retention enforces the same 1–365 day range as the wizard, and `--track` help includes the shipped trading profile.
- **Effective optional-service gates** — Cloudflare Tunnel container mode now fails validation before startup when its named-tunnel token is blank, and the on-demand backup entrypoint rejects backup/restore runs while `BACKUP_SOURCE=disabled` instead of treating enabled and disabled identically.
- **Graph Builder optional configuration** — Diffbot and Google Cloud options are now manifest-owned and namespaced; GCS caching has project/bucket wiring, ADC credentials mount read-only into the backend, and incomplete enabled GCP configurations fail validation before Compose.
- **Collision-safe launch logs** — concurrent wizard sessions now create distinct owner-only `/tmp/atlas-launch-<timestamp>-<unique>.log` files atomically instead of truncating the same second-level path.
- **Backend application identity boundary** — protected Backend routes now require a verified Supabase user JWT or a generated service credential on direct and Kong paths. User-owned memory, research, media operations, and spend reads are subject-bound; operator routes require the trusted internal token; JupyterHub receives a separate stateless-only token; Ray and LightRAG retain their dedicated tokens.
- **First-party caller authentication** — bundled Open WebUI tools and seeded n8n workflows use separate route-family-scoped bearers, while Chonkie and Ragas notebooks attach the stateless-only notebook bearer. Plugin `inherit`/manifest-less routes enforce Backend identity, declared routers cannot escape their route prefix, `key-auth` is validated in-app as well as at Kong, and only explicit `open` plugins remain public.
- **Backend owner synchronization** — idempotent trigger/backfill paths synchronize both `auth.users` and valid Open WebUI user UUIDs into `public.users`, making authenticated subjects valid research and memory foreign-key owners without overwriting Auth-owned profiles. Supabase Auth deletion removes its owner and cascades owned records; Open WebUI deletion preserves an owner that remains backed by Supabase Auth.
- **Atomic RAG ingestion state** — concurrent submissions now atomically claim a content-sensitive corpus fingerprint, cancellation and terminal states survive stale worker writes, Redis drops expired list members, and a broker dispatch failure records a retryable failed job instead of leaving a permanently deduplicating pending record.
- **Durable media and research operations** — hosted-media polling and cancellation state now uses Redis with atomic first-terminal-writer semantics across Backend replicas and restarts. Research releases database connections before remote waits, conditionally claims pending sessions, and transactionally stores results only while the session remains running, so cancellation cannot be overwritten by late work.
- **Bounded provider inputs** — Asset Worker, Asset Baker, Docling, and Parakeet now stream multipart inputs to bounded temporary files, enforce the same limits on MinIO references, and keep blocking conversion/inference work off async API loops. Asset Worker subprocesses have explicit timeouts, and Docling conversion failures return errors instead of successful Markdown that downstream ingestion could index.
- **Durable provider and worker retries** — every FAL SDK operation now honors `FAL_TIMEOUT_SECONDS`; RAG Redis access is offloaded from async routes; and transient RAG-ingestion or memory-consolidation failures reach Celery's bounded retry policy without leaving terminal or misleading job state.
- **Executable examples and documentation hygiene** — bundled n8n research workflows now use the pinned node-field contract, preserve item context without execution-global mutation, and connect terminal/error branches. Notebook inventory, Python syntax, and direct-import declarations are gated; Python and Scala Spark examples both verify MinIO; public documentation no longer exposes publication mechanics, stale static service counts, or confirmed dead external references.
- **Security dependency baselines** — patched Python multipart, Requests, Pillow, Click, PyArrow, NLTK, Pydantic Settings, SoupSieve, FastAPI, Starlette, Uvicorn, NeMo, ONNX, Protobuf, and related transitive floors are now explicit at each owning runtime. Regression tests prevent the service manifests and lockfiles from returning to vulnerable ranges.
- **Airflow 3.3 security release** — the Airflow family now uses `apache/airflow:3.3.0` on both architectures. Provider installation follows upstream's supported two-stage model instead of reapplying the core constraints file, allowing Spark 4.1.2 and patched provider transitives to resolve together.
- **Ray 2.56 runtime alignment** — Ray server images and Backend/Jupyter clients now move together on the 2.56 line, closing unsafe WebDataset deserialization paths without introducing client/server skew.
- **Backend JWT verification** — Backend identity validation now uses PyJWT and requires `sub` plus `exp`; the verification-only HS256 path no longer pulls the signing-side-channel dependency inherited through `python-jose`.
- **Notebook and speech ML baselines** — JupyterHub now installs the supported PyTorch 2.11 CPU trio and matching PyG wheel set, while Parakeet GPU uses NeMo 2.7.3 with patched ONNX and Protobuf floors. Residual upstream model-loader advisories are limited to operator-selected trusted model artifacts and are documented beside the compatibility pins.
- **Truthful operational readiness** — Backend now separates cheap `/health` liveness from `/ready` probes for PostgreSQL, Redis, and LiteLLM, and its container health check uses readiness. Asset Worker, Asset Baker, Docling, and Parakeet no longer report healthy before their required executable, processor, or model is available. The OpenTelemetry Collector validates its mounted config on every health probe, while explicitly enabled but incomplete Backend tracing fails startup instead of silently disabling spans.
- **Current Backend test contract** — Backend upload rejections now use Starlette's current `HTTP_413_CONTENT_TOO_LARGE` symbol while preserving HTTP 413 behavior. The required CI job installs the Backend's owned requirements instead of a stale inline subset, so identity and async tests collect with PyJWT, `pytest-asyncio`, and the current FastAPI `httpx2` test client.
- **Bounded RAG corpus discovery** — mounted and MinIO-backed RAG ingestion now enforce manifest-owned per-file, aggregate-byte, and file-count limits while reading in bounded chunks. Oversize metadata is rejected before download and unknown-size streams remain bounded, preventing corpus discovery from allocating arbitrary Backend memory.
- **Correct Development documentation navigation** — the generated site now publishes the Diagram Catalog as the explicit 9.4 child page instead of allowing MkDocs to mistake its canonical `README.md` source for the Development section index.
- **RAG parser and runtime boundaries** — ingestion now calls the real keyword-only document-extraction contract, honors each declared Docling/Tika parser slot exactly, offloads synchronous corpus and Chonkie work from the API event loop, and applies finite Redis connect/read deadlines to shared RAG and media operation state.
- **Transactional memory extraction** — LangMem extraction now releases PostgreSQL during LiteLLM calls, validates model output shape, commits facts and session completion atomically under a per-user quota lock, and records terminal failures. Recall and summarization no longer pin database connections while awaiting model responses.
- **Truthful startup controls and verification** — the Textual launch UI now permits detachment only after Compose startup and required init jobs finish, verifies Redpanda and Zeppelin one-shot initialization, uses readable secondary text contrast, and keeps Airflow's opt-in build-validation target aligned with the runtime 3.3.0 image.
- **Complete optional operations controls** — Backup and Cloudflare Tunnel now have matching CLI and wizard SOURCE controls, and backup/restore package, database, archive, and S3 commands enforce a configurable positive deadline instead of waiting forever.
- **Bounded hosted-media inputs** — the Backend rejects oversized media request streams before route parsing, rejects oversized inline images before base64 decoding, and checks source and conditioned image dimensions before Pillow allocation. Media budget configuration now fails closed for malformed booleans, stores, caps, retention, and missing durable-store credentials.
- **Compensated media submission** — hosted-media submission now preflights shared operation storage before paid provider work, retries post-submit persistence, and attempts provider cancellation when durable state cannot be recorded. Recovery responses preserve the provider operation id and retain spend reservations for uncancelled work requiring manual reconciliation.
- **Leased research execution** — research session creation and its first log are atomic, running work maintains database heartbeats, and every Backend replica terminalizes abandoned pending/running rows after a configurable lease. Graceful shutdown records interrupted local tasks as failed before process exit.
- **Authenticated n8n bootstrap removal** — required community packages now install from an exact committed npm lock into n8n's shared user folder before the web/worker runtime starts. Initialization no longer calls an authenticated internal REST route without credentials or treats authorization failures as successful installation.

### Fixed — 2026-07-13 — Synchronized three-surface documentation

- **Canonical publication pipeline (#606 / #607)** — repository Markdown, the MkDocs `.io` site, and the native GitHub wiki now derive from one ordered manifest with strict drift, self-containment, local-link, diagram, and notebook-source checks. The public hierarchy covers 102 pages, 59 service guides, and 71 synchronized architecture diagrams.
- **Native wiki HTML links (#613)** — HTML hero actions are now rewritten to their manifest-derived numbered wiki pages while the MkDocs site retains its pretty URLs. The shared link model audits Markdown and HTML links and images, and the docs gate rejects missing wiki-local targets, including dotted hierarchical slugs.
- **Promotion sync (#615)** — `develop` records the protected `main` documentation promotion as an ancestor before the wiki-link correction is promoted, preserving strict GitFlow ordering without changing the verified documentation content.

### Added — 2026-07-10 — Consumer manifest registration

- **Consumer manifest (#399)** — parent repositories can now register Atlas integrations with `atlas.consumer.yml` via `./start.sh --consumer <path>` or `ATLAS_CONSUMER_MANIFEST`, declaring project branding/env, external Compose overlays, backend plugin roots, and ComfyUI/Ollama model sidecars without symlinking into `services/_user/`. Compose validation, the consumer doctor, launch summaries, and docs now understand the manifest contract; list-valued model entries merge by ordered union while scalar conflicts fail validation.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the consumer manifest work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-10 — Consumer overlay doctor

- **Consumer doctor (#401)** — `./start.sh doctor` now runs headless consumer preflight checks without starting containers, with text output for local debugging and `--format json` for CI. The first registry includes Compose validation, `_user` overlay env-reference validation, plugin directory sanity, model sidecar parsing, endpoint reporting, and tracked-file cleanliness.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the consumer doctor work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-10 — Headless consumer validation commands

- **Headless env and compose checks (#397)** — `./start.sh env backfill` now exposes the additive `.env` backfill path without entering the startup flow, and `./start.sh compose validate` runs the assembled Compose config validation including `services/_user` overlays. Both commands are non-interactive and documented for parent-repo submodule upgrade scripts.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the headless bootstrapper command work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-10 — Non-following start for automation

- **Detached start mode (#398)** — `./start.sh --no-tui --detach` (alias `--no-follow`) now runs the normal linear start pipeline, waits for Compose health gates, prints a final per-service status summary, and exits instead of tailing `docker compose logs -f`. `--json` emits the detached status summary in machine-readable form for parent-repo wrappers and CI.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the detached start work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-10 — MinIO parent-owned consumer buckets

- **Extensible MinIO bucket provisioning (#409)** — `minio-init` now accepts `MINIO_EXTRA_CONSUMERS`, a parent-owned `CONSUMER:BUCKET_VAR:ACCESS_VAR:SECRET_VAR[:EXTRA_BUCKET_VAR,...]` declaration that lets `_user` overlays provision their own buckets and scoped service-account credentials without forking Atlas's init script. The reuse, submodule, service, `.io`, and wiki docs now show the DayDreams-style overlay pattern.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the MinIO extra-consumer bucket work, preserving strict-mode branch ordering while keeping the documentation record current.

### Documentation — 2026-07-10 — Consumer reference layout

- **Parent-repo consumer layout (#421)** — documented the RAG-showcase/DayDreams-style submodule pattern: parent-owned Compose overlays symlinked into `services/_user/`, force-set project SOURCE/branding wrappers, explicit track override behavior, what-lives-where ownership, and a validation checklist for clean consumer repos.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the consumer-layout documentation work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-10 — Ragas RAG evaluation surface

- **Backend Ragas evaluation API (#378)** — `POST /api/rag/evaluate` now exposes Atlas-owned Ragas evaluation for supplied question, answer, retrieved-context, and optional reference records. The endpoint supports faithfulness, answer relevancy, context precision, and context recall metrics while routing evaluator calls through the existing LiteLLM gateway.
- **JupyterHub exploratory Ragas surface (#378)** — the JupyterHub image now installs Ragas and ships `14_ragas_evaluation.ipynb` for opt-in local metric experiments plus calls to the Backend `/api/rag/evaluate` runtime contract.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the Ragas evaluation work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-10 — Chonkie RAG chunking surface

- **Backend Chonkie chunking API (#375)** — `POST /api/chunk` now exposes Atlas-owned token, recursive, and semantic text splitting backed by Chonkie. Responses include ordered chunks, stable character offsets, optional token counts, and strategy metadata so n8n and downstream ingestion workflows can call the Backend instead of importing Chonkie directly.
- **JupyterHub exploratory Chonkie surface (#375)** — the JupyterHub image now installs Chonkie and ships `13_chonkie_chunking.ipynb` for comparing token, recursive, optional semantic chunking, and the Backend `/api/chunk` runtime contract.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the Chonkie chunking work, preserving strict-mode branch ordering while keeping the documentation record current.
- **Ancestry sync:** the final `main` → `develop` synchronization for this promotion is merged with merge-commit semantics so `develop` records the protected `main` tip as an ancestor before the develop-to-main PR is merged.

### Added — 2026-07-10 — External consumer env overlay

- **External env overlay for submodule consumers (#396)** — `ATLAS_ENV_USER_FILE` now points Atlas at a parent-owned `.env` overlay outside the checkout. Atlas applies sibling `.env.user` first, then the external overlay, then CLI flags, on every start including `--cold`; missing or unreadable external files warn and continue so wrapper scripts can fail gracefully.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the external-overlay work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-09 — Asset worker glTF post-processing service

- **Asset worker glTF/GLB post-processing (#343)** — new disabled-by-default `asset-worker` media service for upload- or MinIO-reference-based model post-processing. The service normalizes uploaded assets upright to the ground plane, scales by requested height or width target, runs the pinned glTF-Transform CLI with Draco/Meshopt/texture options, persists artifacts to MinIO when configured, and exposes deterministic SHA256 artifact metadata plus a local fallback download route.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the asset-worker work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-09 — Hosted media gateway foundation

- **Media gateway operation model (#339)** — the backend now exposes `POST /media/generate` and `GET /media/operations/{operation_id}` as the provider-neutral hosted-media surface. The first registry entry supports FAL image generation, keeps provider keys backend-only, returns normalized provider/model/modality/artifact/cost/license/provenance fields, and preserves `POST /comfyui/generate` as the simple-image compatibility route.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the media gateway work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-09 — ComfyUI pinned core + custom-node provisioning

- **ComfyUI runtime foundation (#334)** — the ai-dock runtime now exposes a pinned upstream `COMFYUI_REF=v0.9.2` with `COMFYUI_AUTO_UPDATE=true`, keeping the ComfyUI core version explicit without changing container families. The bootstrapper also writes `active-custom-nodes.tsv` from selected models' `requires_custom_node` values, but only after mapping them through `services/comfyui/custom-nodes.yaml`; the AI-Dock provisioning hook clones those allowlisted GitHub repos into the `comfyui-custom-nodes` volume at full commit SHAs and installs declared requirements through the ComfyUI Python environment.
- **Custom-node safety contract:** node auto-install is allowlist-only, repo URLs must be GitHub HTTPS `.git` URLs, refs must be full 40-character commit SHAs, and unknown catalog requirements warn instead of cloning arbitrary code.
- **Promotion sync:** `develop` was merge-synced with the current `main` tip before promoting the ComfyUI runtime work, preserving strict-mode branch ordering while keeping the documentation record current.

### Added — 2026-07-09 — ComfyUI model-bundle catalog foundation

- **ComfyUI multi-file model bundles (#336)** — catalog entries can now declare `files:` so one wizard selection expands into multiple download rows with per-file category, filename, SHA256, target directory, precision, and variant metadata. The generated manifest records `bundle_id` / `bundle_file_role`, the init downloader honors explicit target directories while preserving backward-compatible TSV rows, and custom sidecar YAML supports the same bundle shape.
- **Promotion sync:** `develop` was ancestry-synced with the current `main` tip before promoting the ComfyUI bundle work, preserving strict-mode branch ordering while keeping the documentation record current.

### Fixed — 2026-07-07 — overnight maintenance: init quoting, bootstrapper/backend hardening, latent SQL abort

Broad verify-and-fix pass. Headline fixes:

- **psql init quoting (Critical):** `init-label-studio.sh`, `init-mlflow.sh` (copy-pasted from each other), and `init-langfuse.sh` used psql `:'var'` inside `-c`/`-tAc` strings — but psql only interpolates `:'var'` in SCRIPT input (stdin / `-f`), never inside `-c`. The literal `:'var'` shipped to the server raised a syntax error and (under `set -eu`) aborted the init container, so label-studio and mlflow could never provision their Postgres role/database, and langfuse's database-existence check never matched (createdb ran unconditionally and failed on any restart with a persistent volume). Converted to the stdin/`printf` pattern already used in `init-airflow.sh` / `init-iceberg-rest.sh`. Added `test_init_scripts_psql_quoting.py` — a regression guard, since `bash -n` / `shellcheck` cannot catch this psql-semantic class.
- **Bootstrapper error handling:** `main()`'s catch-all over a ~620-line try body now prints the traceback to stderr (was a bare `str(e)`, making incidents undebuggable); the prod profile no longer silently resets operator-customized `LOG_MAX_SIZE` / `LOG_MAX_FILE` (now warns when resetting a divergent value); the no-TUI `--track` path warns when track-contract synthesis fails instead of swallowing.
- **Backend resilience:** `plugin_seam`'s import-time pip-install is now bounded by `BACKEND_PLUGINS_PIP_TIMEOUT_SECONDS` (was the sole unbounded external call in the backend); the stale `ray_client` auth comment is refreshed (`BACKEND_KONG_AUTH=key-auth` now covers `backend-api`); dead `except` handlers in `research_client.cancel_research` removed.
- **SQL:** removed `ALTER SYSTEM SET wal_level` wrapped in a `DO` block in `07-functions.sql` — illegal inside a transaction block, a latent abort (only dead today because the supabase image pre-sets `wal_level=logical`).
- **Dead code:** dropped a redundant guard in `topology._allocate_slots` and a no-op `.split("?")` in `comfyui_resolver._filename_from_url`.

Deferred (need a product/architecture decision; rationale in the run report): the `public.users` FK is unsatisfiable (no population path — breaks memory/research inserts for any real `user_id`); the wiki↔docs-site prose templates have diverged; backend `requirements.txt` has no lockfile (posture decision); several asyncpg connection-held-across-LLM-call sites in the backend.

### Added — 2026-07-05 — FAL cloud media provider + backend Kong auth + user Supabase migration slot

Backfilling `[Unreleased]` for the 2026-07-05 merges that were missing from this log:

- **FAL cloud media provider (#331)** — new `fal` virtual manifest + `fal_media_client` exposing image generation through the FAL cloud API.
- **Optional backend Kong auth (#330 / #322)** — `BACKEND_KONG_AUTH=key-auth` (opt-in, default disabled) adds key-auth + ACL to the `backend-api` gateway route, gating the Ray job-submission and storage-upload surfaces.
- **User Supabase migration slot (#329)** — `db-init-runner` applies a `_user` SQL mount alphabetically after the Atlas scripts, so downstream forks can ship custom migrations without forking the init image.

### Fixed — 2026-07-05 — backend plugin requirements + install-error surfacing + env-overlay cold-start preservation

- **Backend plugin requirements loading (#326)** and **fail-clearly on plugin dependency install errors (#327)** — the plugin seam loads shared + per-plugin `requirements.txt` and raises a typed `PluginRequirementsInstallError` instead of crashing opaquely at import time.
- **Preserve user env overlays across cold start (#328)** — the `.env.user` overlay survives `stop.sh --cold` / fresh bootstrapper regeneration.

### Added — 2026-07-03 — Atlas root dashboard

- **Kong root now serves Atlas instead of Supabase Studio.** The bare gateway root (`http://localhost:${KONG_HTTP_PORT}`) returns a generated Atlas service directory with SOURCE state, track context, direct/Kong links, auth notes, warnings, and browser-side reachability probes. Supabase Studio remains available through its explicit `supabase-studio.localhost` route and keeps the existing Kong basic-auth gate.

### Fixed — 2026-07-02 — overnight maintenance hardening

- **Startup and init resilience:** `./start.sh --setup-hosts` now requests privilege only for the hosts-file write instead of asking operators to run the whole launcher under `sudo`, `local-deep-researcher` bounds each LiteLLM health-poll attempt with `curl --max-time`, `lightrag-init` fails fast when no chat model can be resolved, and the launcher waits for one-shot init containers so failures surface before the stack is reported ready.
- **Credential and configuration hygiene:** Neo4j and Supabase volume-baked password rotation stays guarded after volumes exist, secret manifest rows now carry descriptions in `.env.example`, and LightRAG role/base model resolution remains documented and tested.
- **Docs and examples:** MinIO release-pin notes now distinguish the Docker-published pin from GitHub-only upstream releases, duplicate roadmap headings were removed, and all bundled Jupyter notebooks now declare nbformat 4.5+ cell IDs with a regression test.

### Changed — 2026-06-29 — SearXNG: thin `use_default_settings` override instead of a forked settings.yml

- Replaced the ~2800-line forked `services/searxng/config/settings.yml` (a full copy of SearXNG's defaults incl. the ~250-engine list) with a ~40-line **thin override** built on `use_default_settings`. The fork drifted from the pinned image on every bump — engines lost their modules (`Cannot load engine …: FileNotFoundError`) or changed config APIs (`Engine setup was not successful`), spamming startup errors (this is the durable fix for the searxng follow-up from the #176 cleanup, which had removed 8 dead-module engines but left 7 config-drift ones). The override now carries only what the stack needs: `search.formats: [html, json]` (JSON is off in the default; the backend/n8n/LDR/Hermes all use the JSON API) and `use_default_settings.engines.remove: [ahmia, torch]` (Tor-only engines that need a Tor proxy we don't wire). Everything else — full engine list with correct per-engine config, `server.limiter: false`, `valkey.url: false`, `enable_metrics: true`, and the `secret_key` placeholder — is inherited from the image and stays version-matched on future bumps. `SEARXNG_SECRET` is applied via the SearXNG-native env var (compose), not written into the file (the README's old "bootstrapper writes SEARXNG_SECRET into settings.yml" note was wrong and is corrected). Verified live: SearXNG boots with **0** engine-load errors (was 21+) and the JSON search API returns results. README updated.

### Fixed — 2026-06-28 — post-launch service error-log cleanup (realtime crash + 3 noisy services)

A full audit of a freshly-launched stack surfaced one crash-loop and three sources of recurring error logs; all fixed and verified live:

- **supabase-realtime crash-loop** (the headline). The supabase set bump (#170) moved `supabase/realtime` `v2.33.72 → v2.112.0`, and the newer image does an **unconditional** `System.fetch_env!("METRICS_JWT_SECRET")` at boot (`runtime.exs` — it signs the JWT guarding the Prometheus `/metrics` endpoint). The var wasn't in our compose, so realtime crash-looped with `(System.EnvError) ... "METRICS_JWT_SECRET" ... is not set`. Fix: `METRICS_JWT_SECRET: ${SUPABASE_JWT_SECRET}` (same posture as `JWT_SECRET`/`SECRET_KEY_BASE`; no new `.env` var). The new `GEN_RPC_*` cert vars are only fetched when SSL inter-node RPC is enabled (`GEN_RPC_SSL_SERVER_PORT`), which we don't set, so they're not required. This was the runtime-compat risk flagged when #170 merged; storage-api/gotrue/postgres-meta/studio came up healthy on their bumps.
- **redis-exporter false "unhealthy"** — `oliver006/redis_exporter` is a distroless image (no shell / no `wget`/`curl`), so the `wget --spider` container healthcheck could never run (`exec: "wget": not found`) → permanent "unhealthy" (a pre-existing bug, not introduced by the version bump). Removed the unusable healthcheck; Prometheus scrapes `:9121/metrics` directly as the real liveness signal, and nothing `depends_on` its health.
- **postgres-exporter scrape-error spam** (~every 5s) — on `supabase/postgres` 17.6 a GUC row has a NULL `short_desc`, and the exporter's `pg_settings` collector can't map NULL→string (`error scraping dsn … converting NULL to string is unsupported`) while still serving all other `pg_*` metrics. Set `PG_EXPORTER_DISABLE_SETTINGS_METRICS: "true"` (the low-value settings-as-metrics export) to silence it.
- **searxng dead-engine load failures** at startup — our forked `settings.yml` listed **8 engines whose modules were dropped from the pinned image** (`svgrepo`, `ask`, `cppreference`, `livespace`, `mullvad_leta`, `podcastindex`, `searchcode_code`, `seekr`, `stract` → `FileNotFoundError: …/engines/<name>.py`, "set engine to inactive"). Removed those 11 dead entries (some modules had multiple entries). **Known remaining (separate follow-up):** 7 more engines (`wikicommons`×4, `piped.music`, `torch`, `ahmia`) still error at startup because their *config API* changed in the pinned version (e.g. `wikicommons` now needs `wc_search_type`, `piped.music` needs `frontend_url`) — searxng self-disables them and keeps working. The durable fix is to stop shipping a full forked `settings.yml` and switch to `use_default_settings: true` + a thin override (inherits correct per-engine config from the image, ends the recurring drift); tracked as a focused follow-up rather than piecemeal engine edits.

(Benign, left as-is: cadvisor/node-exporter `/etc/machine-id` + udev notices on macOS Docker, SLF4J `StaticLoggerBinder` warnings, uvicorn's `uvicorn.error` *logger name*, lightrag's startup-only "relation does not exist" lazy check-then-create probes, grafana's `xychart already registered` notice, zeppelin's default-XML notice.)

### Fixed — 2026-06-28 — lightrag-init `password authentication failed` — pipeline ordering (the real "stale volume" cause)

- **Root cause of the recurring `FATAL: password authentication failed for user "supabase_admin"` (lightrag-init exit 1), which the launcher mis-reported as a "stale supabase-db volume".** `generate_service_configuration` bakes `SUPABASE_DB_PASSWORD` (and `GRAPH_DB_PASSWORD` / `REDIS_PASSWORD`) into the derived `LIGHTRAG_PG_URI` / `LIGHTRAG_NEO4J_PASSWORD` / `LIGHTRAG_REDIS_URI` connection values — but in both the Textual and linear launch flows it ran **before** `generate_encryption_keys` rotated those passwords. So on a cold start (or any first-run placeholder→real upgrade) the URI carried the *stale* password while the Postgres volume was freshly `initdb`'d with the *new* one → auth failed against a perfectly fresh volume. This was NOT a stale volume; wiping/`--cold` didn't help because the ordering re-created the mismatch every run.
- **Fix:** moved `Validate Supabase keys` + `Generate encryption keys` ahead of `Generate service configuration` (and the Kong/LiteLLM config steps) in both flows, so every secret is finalized before any step derives a connection string from it. Added a source-order regression guard (`test_secret_gen_before_config_gen.py`) for both flows. Recovery after pulling this fix is a normal (non-cold) `./start.sh` — `generate_service_configuration` rewrites `LIGHTRAG_PG_URI` to match the existing volume's password; no wipe required.

### Fixed — 2026-06-28 — Cold start now regenerates the volume-baked DB passwords

- On a cold start the data volumes are wiped, but `generate_missing_keys` rotated the three **volume-baked** DB passwords — `SUPABASE_DB_PASSWORD`, `SUPABASE_DB_APP_PASSWORD`, `GRAPH_DB_PASSWORD` (neo4j) — with a hardcoded `force=False`, ignoring the cold flag that every other regenerated secret honors. The cold path only re-randomized them indirectly (via `setup_env_file` resetting `.env` to the placeholder), so a `.env` that retained a real-but-stale value could survive a cold start and then drift from the freshly `initdb`'d volume → `FATAL: password authentication failed for user "supabase_admin"` (which cascades to `lightrag-init` and every other DB client). Fix: pass `force=force_regenerate` for those three, coupling the password regen to the cold volume wipe so a cold start always yields a consistent fresh (password, volume) pair. Non-cold behavior is unchanged — real values still stick and the existing-volume guard still applies. (Recovering an *already*-drifted volume still needs a one-time `./start.sh --cold` or `docker volume rm <project>-supabase-db-data`, since a baked password can't be changed by rewriting `.env`.) The wizard's **Cold start · rebuild** step now spells this out — its subtitle and the per-option hints note that a cold start regenerates the Supabase + Neo4j DB passwords to match the fresh data.

### Fixed — 2026-06-28 — Launch pipeline crash on the embedding dimension warning (`'function' object has no attribute 'print'`)

- The Textual launch flow swaps `starter.banner` for a `_NullBanner` to suppress stdout while inside the app. Its `__getattr__` returned a bare `lambda` for undefined attributes, so `starter.banner.console.print(...)` raised `'function' object has no attribute 'print'`. This crashed the **"Apply user model selections"** pipeline step whenever a non-768-dim embedding default triggered the dimension warning (`start.py` `apply_user_model_selections`). Fix: `_NullBanner.__getattr__` now returns a chain-swallowing `_NullSink`, so `banner.console.print(...)` — and any `banner.<attr>.<method>(...)` chain reached under the NullBanner (17 such `self.banner.console.*` call sites exist) — is a safe no-op. (The dimension warning is still surfaced at selection time in the embedding step's heading/subtitle.)

### Fixed — 2026-06-28 — Wizard "default for chat" no longer lists embedding models

- The LLM-defaults **chat / content** picker (`LITELLM_DEFAULT_MODEL`) was offering embedding models (e.g. `nomic-embed-text:latest`, `mxbai-embed-large:latest`) as chat-default candidates. Two root causes: the catalog capability lookup was **tag-sensitive** (the curated catalog stores `nomic-embed-text` bare but the wizard selects `nomic-embed-text:latest`, so the tagged form missed the catalog and fell through to a content-only default), and **non-catalog** models (`mxbai-embed-large` isn't in the curated catalog) were synthesized as content-only. Fix: `_classify`/`_description` now match the catalog tag-insensitively (exact name OR family root on both sides), and a shared `model_resolver.looks_like_embedding()` heuristic classifies catalog-unknown models so embedding models are routed to the embedding picker and kept out of the chat/vision pickers. `model_resolver._synthesize` uses the same heuristic, so the runtime default-model resolver agrees with the wizard (it previously synthesized any catalog-unknown embedding model as `content=8`). A regression test reproduces the exact reported selection set.

### Changed — 2026-06-28 — Image-pin sweep: kill moving tags, refresh stale pins (#168 / #169 / #170)

- **Moving `:latest` / floating tags pinned to concrete versions (#168).** Closed the reproducibility hole behind the ollama drift incident (a volume-preserving reset reused a months-old cached `ollama/ollama:latest` that could no longer run `qwen35moe` or pull the 1536-dim embedder): `ollama/ollama` → `0.30.11`, `n8nio/n8n` → `2.28.2`, `dyrnq/open-webui` → `v0.6.32`, `searxng/searxng` → `2026.6.28-357662d86`, `nousresearch/hermes-agent` → `v2026.6.19`, `ghcr.io/openclaw/openclaw` → `2026.6.10`, the seven `alpine:latest` init images → `3.24.1`, plus the floating partials `python:3.12`→`3.12.13`, `python:3.11-slim`→`3.11.15-slim`, `redis:7.2-alpine`→`7.2.14-alpine`, `postgres:17/15-alpine`→`17.10/15.18-alpine`, and `multi2vec-clip` → the `…ViT-B-32-1.5.1` build. Each pin equals what a fresh `latest` pull returned that day — reproducibility, not a behavioral upgrade. (`CHATTERBOX_IMAGE:gpu` and `TEI_RERANKER_CPU_ARM64_IMAGE:cpu-arm64-latest` stay floating — upstream publishes no version-pinnable variant.)
- **Stale concrete pins refreshed + jupyter base migrated off Docker Hub (#169).** Same-major / patch-minor bumps: weaviate `1.27.5`→`1.38.2` (aligns with the merged `weaviate-client>=4.22`), neo4j `5.19.0`→`5.26.27` (5.26 LTS), kong `3.9.0`→`3.9.3`, zeppelin `0.12.0`→`0.12.1`, cloudflared `2025.8.1`→`2026.6.1`, node-exporter `v1.8.2`→`v1.11.1`, cadvisor `v0.49.1`→`v0.55.1`, redis-exporter `v1.62.0`→`v1.86.0`, postgres-exporter `v0.18.1`→`v0.19.1`, lightrag `v1.5.0`→`v1.5.4`. The two GPU base tags upstream no longer builds were moved forward: docling `pytorch/pytorch:2.5.1-cuda12.4-…` → `2.12.1-cuda12.6-cudnn9-runtime`, parakeet `nvcr.io/nvidia/pytorch:25.01-py3` → `26.06-py3`. The jupyterhub base migrated from the Docker-Hub `jupyter/datascience-notebook:python-3.11` (frozen since 2023) to the maintained `quay.io/jupyter/datascience-notebook:python-3.11.10` — same Python 3.11 line, maintained registry.
- **Supabase tier bumped as one coordinated set (#170).** supabase/postgres `17.4.1.016`→`17.6.1.139`, postgres-meta `v0.88.9`→`v0.96.6`, storage-api `v1.22.7`→`v1.61.5`, gotrue `v2.171.0`→`v2.191.0`, realtime `v2.33.72`→`v2.112.0`, studio off the moving `:latest` to `2026.06.22-sha-2207d7f`. postgrest (`v12`→`v14`) was intentionally held (breaking major). The dockerized real-Postgres seed harness was bumped to 17.6.1.139 and re-verified: seed-rows golden byte-unchanged, schema golden regenerated (the only delta is base-image evolution — 17.6 no longer pre-creates the still-installable `pg_graphql` in the default DB; some role ownership moved to `supabase_admin`); `seed_harness._normalize` now strips pg_dump ≥ 17.5's randomized `\restrict`/`\unrestrict` nonce lines. **Runtime caveat:** the harness validates `supabase/postgres` + our SQL only, not the storage-api/gotrue/realtime boot-against-17.6 path — smoke-test the stack before production reliance.
- **Held by decision (not regressions):** breaking majors postgrest v14, prometheus v3, grafana 13, redis 8; and litellm (no newer `-stable` build than `v1.83.14-stable.patch.2`).

### Changed — 2026-06-28 — Batched safe Dependabot dependency-floor bumps (#167)

- Combined six non-breaking backend/jupyterhub version-floor bumps into one PR to avoid the strict-mode merge cascade across the same-file Dependabot PRs: backend `asyncpg>=0.31.0`, `weaviate-client>=4.22.0`, `hiredis>=3.4.0`, `pytest>=9.1.1`, `pytest-asyncio>=1.4.0`, and jupyterhub `weaviate-client>=4.22.0`. The major-bump Dependabot PRs (langchain 0.x→1.x, numpy 1.x→2.x) and the docling lock-regeneration (#151) were left open as deliberate, validated follow-ups.

### Added — 2026-06-27 — Brand-overridable block-art logo (`BRAND_LOGO_FILE`)

- The ASCII block-art lockup (wizard brand panel + `--no-tui` banner) is now part of the `BRAND_*` rebranding contract: set `BRAND_LOGO_FILE` to a text file (wide rows, optional `---` separator, then a compact fallback) to override the hardcoded "ATLAS" art. Empty = built-in ATLAS, byte-for-byte. Both render surfaces resolve through the new `bootstrapper/utils/brand_logo.py`, so a custom lockup stays in parity across the TUI and the linear banner (the parity test now covers a custom lockup on both paths). The image-derived globe splash (`atlas_hero.py`) is out of scope and stays the Atlas hero. Docs: `docs/quick-start/interactive-setup-wizard.md` §15.1.

### Changed — 2026-06-27 — Model catalog source-of-truth: Supabase DB → per-service YAML

- The LLM and ComfyUI model catalogs moved out of the `public.llms` / `public.comfyui_models` Postgres tables (both **dropped** via guarded decommission migrations `15-decommission-llms.sql` / `16-decommission-comfyui-models.sql`) into per-service `services/{ollama,litellm,comfyui}/models.yaml`. DB-free resolvers (`bootstrapper/utils/model_resolver.py` / `comfyui_resolver.py`) compute the active set at startup; the `litellm-catalog-init` and `comfyui-catalog-init` sidecars were removed.
- New wizard step picks the default model per role (content / embeddings / vision). Embedding entries declare their output dimension via `dim:`; the picker auto-selects the model whose `dim` matches the backend `memory_facts vector(768)` column (`MEMORY_FACTS_EMBEDDING_DIM`) and warns on a mismatch. The Supabase seed scripts were repartitioned per-service (`services/supabase/db/scripts/`).

### Fixed — 2026-06-27 — Model-SoT follow-ups

- **ComfyUI custom-models sidecar read again:** the host-side resolver falls back to the repo path `services/comfyui/custom-models.yaml` instead of the dead `/custom-models.yaml` container path, so operator-authored custom models are no longer silently dropped.
- **`docker compose up` no longer aborts on Docker 29.x / macOS:** `litellm-init` mounts the model YAMLs into `/atlas-models` (via `ATLAS_MODELS_DIR`) instead of nesting them under the read-only `/catalog` bind mount (runc could not create a mountpoint inside a `:ro` mount).
- **Supabase password drift guard:** `SUPABASE_DB_PASSWORD` rotation is skipped (with a warning) when the project's `supabase-db-data` volume already exists, preventing `.env` from drifting out of sync with the initdb-baked `supabase_admin` role password (`password authentication failed for user "supabase_admin"`).
- **lightrag-init Postgres race:** `lightrag-init` polls the Postgres endpoint until it accepts connections before running its pgvector migration — the SOURCE-safe readiness gate (supabase-db is source-replaceable, so a hard compose `depends_on` is intentionally avoided) — instead of racing supabase-db readiness.
- **`litellm-init` import regression:** `init.py` now adds the `/catalog` dir to `sys.path` when exec-loading the catalog modules, so `model_resolver`'s container-context loose imports (`import llm_catalog`, `from cloud_providers import …`) resolve. Previously `litellm-init` aborted the whole stack with `ModuleNotFoundError: No module named 'llm_catalog'` (the bootstrapper venv has `utils` as a package, so the loose fallback — and the bug — only triggered inside the container). Covered by a new subprocess regression test that exercises the loose-import path.
- **`local-deep-researcher` vestigial DB wiring removed:** the `public.llms` query was deleted in the model-SoT move, but the dead supabase wiring it left behind (`DATABASE_URL`, `psycopg2-binary`/`postgresql-client`, `depends_on: supabase-db-init`, `data_flow.calls: supabase`) remained and crash-looped stale images. LDR is now correctly **DB-free** — the `research_*` tables are persisted by the backend, which calls the LangGraph server over HTTP.
- **`ollama-pull` retries transient pull failures:** each model pull now retries up to 3 times with linear backoff before logging the terminal (non-fatal) ERROR, so a transient registry/network blip on a default model (e.g. `qwen3-embedding:0.6b`) self-heals instead of leaving the model unpulled until the next `compose up`.

### Added — 2026-06-21 — Phase 1 reuse mechanics (`services/_user/` auto-launch + release tags)

- **`services/_user/` overlay services now launch.** The bootstrapper discovers every `services/_user/<name>/compose.yml` and merges it into the `docker compose` invocation (`DockerManager._compose_file_args`: `-f docker-compose.yml -f services/_user/<name>/compose.yml …`), so a downstream consumer's co-located services come up/down with the stack. When no overlay exists the invocation is unchanged — default file auto-discovery preserved, byte-equivalence baseline unaffected. Overlay services are self-contained Compose fragments (own image/ports/env, joined to the shared network); they are intentionally not wired into the wizard/topology/`.env.example`.
- **Release/version-tag convention** for pinning a vendored Atlas: semver `vMAJOR.MINOR.PATCH` documented in `docs/deployment/releasing.md`, with the first tag `v0.1.0`.
- Docs: new `docs/deployment/releasing.md`; `reusing-atlas.md` readiness rows flipped to **Ready** + a §6.1 extension walkthrough; `CONTRIBUTING-services.md` §21 updated. Phase 1 design: `docs/superpowers/specs/2026-06-21-phase1-reuse-mechanics-design.md`.

### Added — 2026-06-20 — Cloudflare Tunnel service (`cloudflared`)

- New `services/cloudflared/` service, **disabled by default** (`CLOUDFLARED_SOURCE=disabled`). Set `CLOUDFLARED_SOURCE=container` + provide `CLOUDFLARE_TUNNEL_TOKEN` to run an outbound Cloudflare Tunnel daemon that terminates TLS at the Cloudflare edge and proxies to Kong — no inbound ports opened. Egress-only (no Kong route); requires a named tunnel configured in the Cloudflare Zero Trust dashboard.

### Added — 2026-06-20 — On-demand backup runner (`backup`)

- New `services/backup/` one-shot runner, **disabled by default** (`BACKUP_SOURCE=disabled`; never long-running). Invoke with `docker compose run --rm backup`. Dumps the Supabase Postgres database (`pg_dump`) and snapshots the critical named volumes (supabase-storage, graph-db, weaviate) to S3-compatible storage — on-box MinIO by default, `BACKUP_S3_ALIAS_URL` for offsite. Ships `restore-postgres.sh` for recovery drills.

### Added — 2026-06-20 — Secrets hygiene guard + cross-OS doc accuracy

- **Placeholder-secret coverage guard** — a test (`test_no_unrotated_nonempty_secret_defaults`) now asserts every `secret: true` manifest default is either empty (generated-when-absent) or a registered `KeyGenerator.PLACEHOLDER_DEFAULTS` literal (rotated-when-placeholder). It caught and registered a previously-unregistered composite, `GRAPH_DB_AUTH`.
- **Prod-launch secrets gate** — `KeyGenerator.assert_no_placeholders_remaining()` refuses to launch under `--profile prod` if any managed secret in `.env` still equals its shipped placeholder. It runs after key generation (so a fresh `./start.sh --profile prod` auto-rotates first and passes) and fails loud if `.env` is unreadable. A rotation-consistency test proves every gated placeholder is actually rotated by `generate_missing_keys()`.
- **Cross-OS doc accuracy** — corrected the README's overstated "works on all OS" claim: Atlas runs natively on Linux and macOS (Intel/Apple Silicon) and on Windows via WSL2 / Git Bash (the `start.sh`/`stop.sh` entrypoints are POSIX shell; there is no native PowerShell/cmd wrapper).

### Added — 2026-06-20 — Production-hardening profile (`--profile prod`)

A new deployment profile consolidates all production-hardening behaviors under a
single flag and a matching wizard step:

- **`--profile prod` CLI flag / wizard profile step** — selects the prod profile
  for the current run. The wizard surfaces this as a dedicated step; the CLI flag
  skips the wizard entirely for that choice.
- **`HOST_BIND_IP` localhost binding** — all published ports are prefixed with
  `${HOST_BIND_IP:-}`, which resolves to the empty string in dev (no-op, byte-
  identical compose output) and to `127.0.0.1` under prod, so no service socket
  is reachable from outside the host.
- **Per-service resource limits** — the heavy compute + data services (Airflow,
  ComfyUI, Hermes, LightRAG, Neo4j, Ray, Spark, TEI Reranker, Weaviate, Zeppelin)
  declare `*_MEMORY_LIMIT` and `*_CPU_LIMIT` values as always-on `.env` defaults
  wired directly into their `compose.yml` `deploy.resources.limits` (applied
  unconditionally, independent of `--profile prod`). These are OOM fences, not
  reservations; the GPU/compute services default to scale 0 and are enabled per
  track, so the sum of all default limits intentionally exceeds a 32 GB host —
  size and enable per your track. Lighter always-on services (Backend, n8n,
  LiteLLM, etc.) currently run unbounded.
- **JSON-file log rotation** — `LOG_MAX_SIZE` and `LOG_MAX_FILE` control the
  Docker json-file log driver's `max-size` and `max-file` options, wired into
  every service's `compose.yml` fragment as always-on `.env` defaults
  (independent of `--profile prod`).
- **Observability defaulted on** — Prometheus and Grafana are promoted from
  their default-disabled state to default-on when the prod profile is active.
- **Declarative `profiles:` source metadata** — each source option in
  `service.yml` now carries an optional `profiles:` list. The wizard
  filters out sources not listed for the active profile (hiding dev-only
  localhost sources in prod), and the CLI validator rejects them with an
  explicit error.

### Changed — 2026-06-20 — Atlas startup artwork gated behind a master switch (off by default)

- **`feature_flags.splash_enabled()`** is a new single source of truth that
  gates ALL Atlas startup block-art: the TUI opening splash overlay
  (`AtlasSplash`) and the linear / `--no-tui` printed hero banner
  (`BannerDisplay.show_hero`). Both call sites check it first.
- **Disabled by default** — the block-art reproduction read too coarse at real
  terminal sizes. Nothing was removed: flip `_SPLASH_DEFAULT` to `True` (or set
  `ATLAS_SPLASH=1` at runtime) to re-enable both paths. The existing
  suppressors (`--no-splash` / `ATLAS_NO_SPLASH`, terminal-width,
  artwork-present) still apply on top when enabled.
- The `atlas_poster_*.json` grids remain committed (latent) so re-enabling is a
  one-line change.

### Added — 2026-06-18 — Atlas brand identity: logo, wizard splash, poster

Building on the rename, the project gained a visual identity (PRs #116–#119):

- **Atlas logo + brand assets** under `assets/` (`atlas-poster.png`,
  `atlas-profile.png`, `atlas-social-preview.png`, `atlas-source.png`),
  used for the README hero poster and the GitHub social/profile images.
- **Wizard splash** — a centered poster over a navy overlay shown on
  `./start.sh`, rendered via `textual-image` with an `AtlasHero` block-art
  fallback on terminals that can't display inline images.
- **Terminal hero art** for the linear (`--no-tui`) path.
- **Splash suppression** — the new `--no-splash` flag and `ATLAS_NO_SPLASH`
  environment variable skip the splash for non-interactive / fast launches.

### Changed — 2026-06-16 — Project renamed: GenAI Vanilla → Atlas

This release renames the project end-to-end. Every variant of the old
name (`GenAI Vanilla`, `GenAI Vanilla Stack`, `genai-vanilla`,
`genai-*` container prefix, `GENAI_ENV_FILE` env var, `GenAIStack*`
Python classes) flips to **Atlas** / **atlas**. The hand-drawn block-
art title in the Textual TUI and the linear (`--no-tui`) banner was
also hand-redrawn as **ATLAS** while preserving the existing color
gradients (`#74A6F4 → #0A1A55` vertical on the TUI; 15-color
`color(17) → color(195)` horizontal on the linear full banner).

User-visible changes that need action:

- **Container prefix flipped `genai-* → atlas-*`.** `PROJECT_NAME` default
  is now `atlas`, so every `${PROJECT_NAME}-<svc>` template across 33
  services renders as `atlas-<svc>`. Old containers and volumes named
  `genai-*` are orphaned on the next pull. To migrate:
    * Easiest path — accept fresh state: `./stop.sh --cold` then
      `./start.sh`.
    * Preserve existing data — rename volumes per service:
      ```bash
      for svc in supabase-db n8n-data jupyterhub-data weaviate-data redis-data ...; do
        docker volume create atlas-$svc
        docker run --rm \
          -v genai-$svc:/from \
          -v atlas-$svc:/to \
          alpine cp -a /from/. /to/
      done
      ```
    * Then `./start.sh` boots against the new atlas-prefixed volumes.

- **`GENAI_ENV_FILE` env var renamed to `ATLAS_ENV_FILE`.** The old name
  is still honored as a deprecated alias with a one-shot stderr warning
  per process; it will be removed in a future major release. Submodule
  users should flip the variable name in their wrapper scripts.

- **Grafana folder name changed `'genai-vanilla' / 'GenAI Vanilla' →
  'atlas' / 'Atlas'`.** Existing Grafana volumes will see the 7 starter
  dashboards appear under a NEW empty "Atlas" folder, while the old
  "GenAI Vanilla" folder is left behind with the previous dashboards
  inside (until you manually move or delete them).

- **GitHub repo URL flipped to `github.com/thekaveh/atlas`** in 18
  places (BRAND_REPO_URL default, README clone instruction, Open WebUI
  tool author_urls, etc.). Until the GitHub repo is renamed on the
  remote, these links 404; GitHub auto-forwards old→new for many
  months after a rename, so timing is non-urgent.

- **Python class + method names: `GenAIStackStarter → AtlasStarter`,
  `GenAIStackStopper → AtlasStopper`, `HostsManager.get_genai_hosts →
  get_atlas_hosts`** (and the private `_genai_hosts_from_topology`
  variant). Anyone importing these from a parent project must flip the
  identifier.

- **Python package names flipped**: `bootstrapper/pyproject.toml`
  `genai-bootstrapper → atlas-bootstrapper`;
  `services/docling/provider/localhost/pyproject.toml`
  `genai-doc-processor-localhost → atlas-doc-processor-localhost`. Both
  `uv.lock` files regenerated.

- **Launch log path template**: `/tmp/genai-vanilla-launch-*.log →
  /tmp/atlas-launch-*.log`. Old log files survive but new launches
  write to the new path.

- **JupyterHub Neo4j MERGE node**: notebook
  `03_neo4j_graphs.ipynb` creates a graph node named `'Atlas'` on next
  run. Existing graphs from prior runs still carry the
  `'GenAI Vanilla Stack'` node; merge with a one-shot cypher:
  ```cypher
  MATCH (s:System {name: 'GenAI Vanilla Stack'}) SET s.name = 'Atlas'
  ```

Internals (no operator action required):
- Block art `_LOGO_ROWS` in `block_logo.py` redrawn; `_GRADIENT` and
  render pipeline unchanged. Linear `get_ascii_art_full()` is now a
  single 6-row ATLAS strip; the 15-color gradient applies per-character.
- `BRAND_NAME` default in `services/globals/service.yml` →
  `"Atlas"`; `.env.example` regenerated.
- Architecture HTML template `<title>` flips `(genai-vanilla)` →
  `(atlas)`; 33 `services/*/architecture.html` regenerated.
- JSON Schema `$id` URLs `https://genai-vanilla/schemas/...` →
  `https://atlas/schemas/...` (internal identifiers; never resolved
  over HTTP).
- Test baseline `bootstrapper/tests/fixtures/rendered_config_baseline.yml`
  regenerated to reflect the new `atlas-*` container/volume/network names.
- Historical CHANGELOG entries below this section deliberately preserved
  (they describe the project as it was named at the time).

### Fixed — 2026-06-16 overnight maintenance pass

- **`./start.sh` usage errors now exit with click's conventional code.** The
  `--spark-workers` range check raised `click.UsageError` from inside the broad
  startup try-block, so the catch-all `except Exception` swallowed it:
  `./start.sh --spark-workers 99` printed "Unexpected error during startup" and
  exited 1 instead of click's usage error with exit 2. `click.ClickException`
  is now re-raised ahead of the generic handler (covers any future inline
  `UsageError`, not just `--spark-workers`).
- **JupyterHub starter-notebook cross-references.** `00_environment_check.ipynb`
  "Next Steps" pointed at the renamed `01_ollama_basics.ipynb` (now
  `01_litellm_basics.ipynb` — all LLM access goes through the LiteLLM gateway)
  and stopped at notebook 06, omitting the shipped `07_ray_cluster` and
  `08_scala_basics`. The `startup.sh` welcome README listed only 00-06. Both now
  match the actual `notebooks/` directory.
- **Backend resilience.** File uploads no longer run storage3's blocking I/O on
  the event loop (offloaded via `asyncio.to_thread`), so a slow/large upload
  can't stall every other in-flight request; the n8n HTTP client is closed on
  app shutdown; failed Weaviate vector deletes are logged instead of silently
  swallowed; and `/research/start` validates `user_id` like its sibling routes
  (clean 400 instead of an opaque 500).
- **CHANGELOG accuracy.** Corrected the rename-sweep tautology "(formerly Atlas)"
  → "(formerly GenAI Vanilla)", and the upload-size note that implied
  `MAX_UPLOAD_BYTES` is `.env`-overridable (it is a code default, not wired into
  the backend's compose/`.env.example`).

### Fixed — 2026-06-15

- **supabase-db-init: `storage.objects.path_tokens` backfill (CRITICAL, PR #105):**
  `services/supabase/db/scripts/04-storage.sql` switched to
  `CREATE TABLE IF NOT EXISTS storage.objects (…, path_tokens text[] GENERATED
  ALWAYS AS …)` to stop wiping data on every boot, but the `IF NOT EXISTS`
  guard meant volumes created before the `path_tokens` column was added kept
  their old shape — the new column never landed and the next-line GIN-index
  `CREATE INDEX … (path_tokens)` crashed `supabase-db-init` with exit 3, taking
  the whole stack down at `./start.sh`. Now pairs the CREATE with an idempotent
  `ALTER TABLE … ADD COLUMN IF NOT EXISTS path_tokens text[] GENERATED ALWAYS
  AS …`; no-op on fresh installs, backfills the column on existing volumes
  before the index attempt.
- **supabase-db-init: `public.llms` schema-drift backfill:** same class as
  PR #105. The CREATE statement in `05-public-tables.sql` accreted 5 new
  columns (`description`, `size_gb`, `context_window`, `api_key`,
  `api_endpoint`) across 2025-07-06 and 2025-08-24, plus type-changed 4 others
  (`vision`/`content`/`structured_content`/`embeddings`: boolean → integer for
  the confidence-gradient rework). Pre-mid-2025 volumes never received any of
  it. `05a-public-tables-migrations.sql` now appends idempotent
  `ADD COLUMN IF NOT EXISTS` for the 5 new columns and an
  `information_schema`-guarded `ALTER COLUMN TYPE integer USING
  (CASE WHEN col THEN 1 ELSE 0 END)` for the 4 boolean columns (legacy true→1,
  false→0). Without this, `llm-catalog-init`'s UPSERT writes (e.g.
  `content=8, structured_content=5`) hard-failed on every old volume.
- **supabase-db-init: `public.memory_facts` / `memory_sessions` /
  `memory_consolidation_log` `user_id` schema-drift backfill:** commit
  6e33a48 (2026-04-27) changed `user_id` from `VARCHAR(255) REFERENCES
  public."user"(id)` → `UUID REFERENCES public.users(id)`. The old Open WebUI
  legacy table `public."user"` no longer exists in supabase-db, leaving the FK
  dangling on volumes created in the 12-day window before that commit. Every
  memory write from `memory_store.py::_to_uuid` failed silently. New
  `10a-langmem-migrations.sql` detects the legacy VARCHAR shape via
  `information_schema`, drops the dangling FK, ALTERs the column to `uuid`,
  re-attaches the FK to `public.users(id)`; idempotent on already-migrated
  installs.
- **`SupabaseKeyGenerator.update_env_file` atomic write:** the method used a
  non-atomic `open('w') + write()` for the most security-critical secrets in
  the stack (`SUPABASE_JWT_SECRET`, `ANON_KEY`, `SERVICE_KEY`). A crash or
  SIGTERM mid-write truncated `.env` and there was no recovery path short of
  regeneration. Switched to the tmp + `os.replace` + `chmod` pattern already
  used by `KeyGenerator.update_env_key` and `SourceOverrideManager`.
- **`/storage/upload` bounded buffering:** the backend handler called
  `await file.read()` with no size limit, buffering arbitrarily large uploads
  into RAM and OOMing the worker on a single multi-GB POST. Now reads in 1 MiB
  chunks bounded by `MAX_UPLOAD_BYTES` (a 100 MiB code default); fails
  cleanly with HTTP 413 when exceeded.
- **Local Deep Researcher fallback hardening:**
  `services/local-deep-researcher/build/scripts/init-config.py` wrote
  `local_llm = "ollama/qwen3.6:latest"` when `public.llms` had no active
  content row — unroutable in cloud-only setups (Ollama disabled) AND not a
  real Ollama model id. PR #88 had already removed the same anti-pattern from
  `memory_service`; this was the second site. Now mirrors the memory_service
  resolution order: `LITELLM_DEFAULT_MODEL` if set, else exit non-zero with a
  clear pointer telling the operator to activate a content row or set the env
  var. Surfaces misconfiguration at compose-up instead of at first /research
  request.

### Changed — 2026-06-15

- **Backend hot-reload via `uvicorn[standard] --reload` (PR #104):** the
  backend `Dockerfile` CMD now passes `--reload`; `services/backend/app/app/
  requirements.txt` swaps bare `uvicorn>=0.27.0` → `uvicorn[standard]>=0.27.0`
  so `watchfiles` (reload-watcher), `uvloop`, `httptools`, and `websockets`
  ship by default. The compose fragment already bind-mounted `./app/app:/app`,
  so host-side edits to backend code are picked up without a
  `docker compose build backend` — only `requirements.txt` changes need a
  full rebuild now.
- **JupyterHub `thekaveh-nnx[lm]==0.2.0` restored (PR #104):** the
  `nnx-pytorch` package was renamed to `thekaveh-nnx`; the old distribution is
  deprecated. The `[lm]` extra pulls the language-model deps the 28-of-29
  ml-lab notebooks that `import nnx` need. Pin verified upstream on PyPI.
- **README + CONTRIBUTING test-count refresh:** README's project-structure
  comment and `docs/CONTRIBUTING-services.md`'s CI-table row both bumped to
  `900+` (suite is at 907 + 3 skipped).
- **`services/backend/README.md` dev-loop note:** §1 now reflects PR #104's
  hot-reload behavior (edit-in-place via the bind-mount; rebuild only for
  `requirements.txt` changes) instead of the stale "edit + force-recreate"
  guidance.

### Fixed — 2026-06-14 overnight maintenance pass (18 commits, passes 1-42)

- **Dependabot ignore: `groq` (HIGH):** the `services/backend/app/app/requirements.txt`
  pin `groq>=0.30.0,<1` keeps groq inside the `langchain-groq>=0.1.5` window —
  Dependabot's previous group bump to groq 1.4.0 silently broke the backend
  docker build (langchain-groq couldn't resolve). Without an ignore entry the
  doomed bump retries every weekly cadence; PR #87 only fixed the pin, not the
  retry loop.
- **Dependabot ignore: 7 Airflow providers:** PR #87 relaxed
  `apache-airflow-providers-{amazon,postgres,redis,common-sql,neo4j,openai,fab}`
  back to the floors listed in upstream Airflow `constraints-3.2.2/constraints-3.12.txt`.
  Each weekly Dependabot bump above those floors produces a PR that can't be
  installed against the constraints file — same shape as PR #47 (spark provider).
  Lift each entry when the Airflow version itself bumps.
- **`stop.sh` sudo guard:** `stop.sh` also shells through `bootstrapper/_run.sh`,
  so `sudo ./stop.sh` would write root-owned files into the same `.venv` /
  `__pycache__` paths PR #87's `start.sh` guard was added to prevent. Mirror
  guard now refuses to run as root with the same exit-2 message and pointer
  at `docs/TROUBLESHOOTING.md` for recovery.
- **Tracks seam-parity test:** `test_tracks.py::test_every_track_service_resolves_via_source_override_manager`
  asserts every service listed in `bootstrapper/tracks.yml` has a matching
  entry in `SourceOverrideManager.source_mapping` (after `normalize_service_key`
  folding for family aliases / runtime_sc divergences). Without this guard a
  future tracks.yml edit could add a service without a CLI seam, silently
  leaking it into every restricted track. Same shipping-class risk as
  `project_post_merge_env_staleness.md` class A, one rung up the tree.
- **Typing hygiene:** `compute_always_on(config_parser)` in `bootstrapper/tracks.py`
  now annotates `config_parser: Any` (was a bare untyped param on a public API,
  inconsistent with the module's otherwise-strict typing). Import added; no
  behavior change.
- **Duplicate-track-key guard:** the runtime reject at `tracks.py:205-206` was
  untested. `test_load_tracks_duplicate_key_raises` locks it. The schema
  (`bootstrapper/schemas/tracks.schema.json`) also gains `uniqueItems: true`
  on the tracks array — catches exact-copy-paste duplicates one rung earlier,
  before the runtime guard runs.
- **`_run.sh` missing-Python error:** the dispatcher used to print
  *"Using system Python (install uv for better dependency management)"* and
  then `exec python3 …`, so an environment without `python3` got an unhelpful
  shell-level "command not found" right after a message that implied `uv`
  would fix things. New branch explicitly `command -v python3` checks before
  the exec, exits 127 with install pointers for both `uv` and `python3`.
- **`open-webui/init` python pin alignment:** the init-tier Dockerfiles for
  `litellm/init` and `comfyui/catalog-init` both pin to `python:3.12.7-slim`
  for reproducibility, but `open-webui/init` was on the floating `3.12-slim`
  tag and silently rolled forward. Pin aligned, comment refreshed (it
  previously claimed parity that wasn't actually there).
- **Init-script stdout is now line-buffered (HIGH):** `open-webui/init`'s
  `register-tools.py` (20 print sites) and `register-functions.py` (13),
  `lightrag/init`'s `resolve-models.py` (6), and
  `local-deep-researcher/build`'s `init-config.py` (16) all printed
  without `flush=True`. In init containers stdout is pipe-attached and
  block-buffered by default — a script that crashes or is killed
  mid-run drops its progress trail silently, the same blind-spot class
  as the PR #67 `register-tools.py` SyntaxError that hid for 24 hours.
  Each script now runs `sys.stdout.reconfigure(line_buffering=True)`
  at the top (mirrors the `flush=True`-everywhere pattern litellm-init
  already used). `lightrag/init`'s `resolve-models.py` was the most
  load-bearing — its KEY=VALUE output is sourced by the shell, and a
  silently-truncated emit produced an empty `EMBEDDING_MODEL` at
  runtime.
- **New buffering guard:** `test_init_script_stdout_is_line_buffered`
  walks every `services/*/init/scripts/*.py` AST and asserts either
  `sys.stdout.reconfigure(line_buffering=True)` at module top *or*
  `flush=True` on every `print()` call. Test suite grew 902 → 906.
- **Doc-only folder skip guard:** `test_doc_only_folders_are_skipped_by_real_manifest_load`
  pins `services/{stt-provider, doc-processor, multi2vec-clip}/` —
  the three aggregator folders that ship README + diagrams but no
  `service.yml` — against the real `load_manifests()` output. A future
  `_is_service_dir` refactor that started loading them as manifests
  would break this real-repo test instead of slipping through (the
  existing synthetic-folder tests cover the predicate in isolation but
  don't pin it to the actual on-disk layout).

Stopped at 10 consecutive zero-issue passes (passes 33-42); no
MAX_PASSES cap hit. Test suite at 907 passed + 3 skipped; all 6
audit scripts and the docs-drift gate exit 0.

### Fixed — 2026-06-13 overnight maintenance pass (15 commits, passes 1-50)

- **Hermes capability wiring (HIGH):** `service_config.py` now emits
  `TTS_INTERNAL_URL`, `STT_INTERNAL_URL`, `COMFYUI_INTERNAL_URL`, and
  `SEARXNG_INTERNAL_URL` whenever Hermes is in container mode and the
  respective provider source is enabled. Until now only
  `LIGHTRAG_INTERNAL_URL` was wired; the other four fell through the
  compose `${VAR:-}` fallback to empty, and `init-hermes.sh`'s
  `strip_block` silently omitted the TTS / STT / image-generation /
  web-search capability blocks from `config.yaml`. Hermes ran without
  those capabilities even when their upstream providers were on. Five
  new regression tests in `test_hermes_n8n_backend_adapts_to_lightrag.py`
  (4 enabled-emit + 1 disabled-blank) lock the contract in.
- README localhost-source docs surface the Hermes dashboard port
  (`HERMES_LOCALHOST_DASHBOARD_PORT=63029`) alongside the API port —
  previously only the API was documented despite Kong's
  `hermes.localhost` route fronting the dashboard.
- Dependabot ignore list adds `apache-airflow-providers-apache-spark`
  (PR #47 was closed unmerged because the 6.0.0 bump dropped the bundled
  `pyspark` dependency). Without the ignore entry the doomed bump
  retries on every weekly cadence.
- Pydantic hygiene: `MemoryFact.metadata` now uses
  `Field(default_factory=dict)` instead of the bare `{}` literal,
  matching the project's preferred convention.
- `CONTRIBUTING-services.md` TL;DR said "four-command regen + lint
  chain" but §12 lists five; LICENSE copyright year extended
  `2025` → `2025-2026`. README test-count claim refreshed from
  `800+` to `840+`.
- README §4.1 service table inflated TEI Reranker's default model id
  to the namespaced upstream form `mixedbread-ai/mxbai-rerank-base-v1`
  (bare `mxbai-rerank-base-v1` wouldn't pull from HuggingFace).
- README + 9 service READMEs + quick-start troubleshooting + the
  expected-startup-warnings table aligned to the `${PROJECT_NAME}-<svc>`
  container-name convention. Non-default-`PROJECT_NAME` deployments now
  have working cut-paste docs throughout. The warning table keeps the
  literal `atlas-` prefix (it documents actual log output) but the new
  note at §1 tells readers to substitute their own `PROJECT_NAME`.
- README §6 `./stop.sh --cold` block now warns that the `docker system
  prune -f --volumes` step is host-wide — it also prunes unused
  images / volumes belonging to OTHER docker projects on the same host.
- Textual wizard's first paint now mirrors `state_builder.build_app_state()`
  when `KONG_HTTP_PORT` is blank in `.env`: `_build_steps_and_rows` now
  uses `'63000'` as the alias-port fallback instead of empty string,
  so the TUI and `--no-tui` paths render the same alias_port for every
  Kong-aliased service. Regression test in `test_blank_base_port.py`.
- Test hygiene: `test_adapts_to_includes_lightrag` now declares
  parametrize `ids=` so pytest failures point at the failing
  (service, container, env-var) tuple by name instead of
  `svc0-container0-expected_env_var0`.
- Prometheus README §1 reworded "All three lifecycle together" (verbed
  noun) to "All three share a single lifecycle" — same scaling
  semantic, cleaner prose.

### Fixed — 2026-06-11 overnight maintenance passes 56-62 (7 commits)

- n8n queue mode: the worker now exports metrics too (5 mirrored
  `N8N_METRICS*` vars + a dedicated `n8n-worker:5678` scrape job) —
  execution-data counters are written worker-side, so the Grafana panels
  were blind to them; scrape-target counts updated to 13 across docs.
- Supabase: documented the auth-less pg-meta host publish
  (`SUPABASE_META_PORT`, SQL as `supabase_admin`) with a firewall/remove
  guidance note; submodule guide's `PROJECT_NAME=… ./start.sh` example
  replaced with the working `.env`-based flow (a shell prefix splits the
  compose project name from fragment interpolation).
- README: project tree gained redis/lightrag/tei-reranker entries; the
  architecture-diagram summary now names the full optional set.
- Port rewrite: `VAR=63002 ` (trailing whitespace, no comment) was
  silently skipped by the rewrite regex — fixed + regression test.
- Dropped the dead `GRAPH_DB_HOST` declaration (zero consumers and its
  `graph-db` default never matched the real `neo4j-graph-db` hostname).
- `--setup-hosts` cycles no longer accumulate blank lines in /etc/hosts
  (boundary normalized; a read failure now aborts instead of risking a
  truncated hosts file); backend README lists the full route surface
  (`/workflows`, `/comfyui/*`, `/api/ray/*` were undocumented).
- Stale-comment sweep: ollama wizard-step docstrings (three-step era),
  Kong "10 localhost routes" count, and the MinIO console-port docstring
  (63019, not 63018) refreshed.

### Fixed — 2026-06-11 overnight maintenance passes 50-55 (5 commits)

- Backend: `cancel_research` now accepts PENDING sessions (the
  insert→RUNNING race window previously left a live background task
  uncancellable); memory-service initialization is lock-guarded so the
  Weaviate delete-and-recreate collection heal can never race itself.
- Textual wizard: worker failures during the setup phase surface as
  error toasts (previously `_write_status` no-op'd with no log pane and
  the error vanished); the confirm-step launch worker gained the same
  `exit_on_error=False` handling as the auto-launch path.
- New validator rule `runtime_sc_missing_variant`: a main runtime_sc
  slice missing a declared source option is now a CI-blocking lint
  (previously `get_service_config()` silently returned `{}` and
  consumers fell back to hardcoded defaults).
- `stop.sh`: the `--clean-hosts` banner no longer claims success when
  the hosts-file edit failed (e.g. without sudo); `--cold`'s global
  `docker system prune -f --volumes` step is now disclosed in `--help`
  and the README (scoping it is a tracked follow-up).
- n8n: `N8N_METRICS_INCLUDE_EXECUTION_DATA_METRICS=true` wired
  (compose + runtime_sc + baseline) — the Grafana execution-data panel
  was permanently "No data" because the flag is default-off upstream.
- Zeppelin: the seeded note and README used `%jdbc(postgres)` prefix
  syntax that Zeppelin 0.12 removed; flow rewritten around a dedicated
  `%postgres` interpreter. OPENCLAW_ENDPOINT documented as a
  forward-looking hook (no consumer wired today).

### Fixed — 2026-06-11 overnight maintenance passes 46-49 (5 commits)

- **Backfill vs migrations (HIGH):** `backfill_missing_env_vars()` no
  longer pre-seeds keys the migration chain owns. Previously it spliced
  `BOOTSTRAPPER_PORT_LAYOUT_VERSION` (and migration v2's `*_LOCALHOST_PORT`
  targets / v3's COMFYUI model vars) from `.env.example` into legacy
  `.env` files *before* `run_port_migration()` inspected them — stamping
  the file as already-migrated and silently dropping the user's legacy
  port and model-set customizations. Six regression tests including an
  end-to-end legacy-env chain.
- Dockerfile hygiene: jupyterhub/backend ARG defaults now match the
  manifests' image pins (CI build-validation previously built a different
  base than production); coursier pinned to v2.1.24; LDR's psycopg2-binary
  pinned to the repo-wide 2.9.9.
- Blank `BOOTSTRAPPER_PORT_LAYOUT_VERSION=` lines now count as unmigrated
  and are stamped in place (previously: skipped by the digit-only regex
  and duplicated on stamp).
- Docs: weaviate module-list truth in source-configuration.md,
  CONTRIBUTING category table (5/6 rows were missing services) + manifest
  counts, `ray.localhost` restored to the alias list, openclaw opt-in
  framing in the redis research row.
- **Localhost-port collision class (MED):** five `*_LOCALHOST_PORT`
  defaults were stale pre-migration literals sitting on ports OTHER
  services now publish (docling/63021=Neo4j Browser, parakeet/63022=Redis,
  openclaw/63024=Spark UI, whisper-cpp/63025=Spark History,
  chatterbox/63027=Weaviate gRPC). Re-defaulted onto each family's freed
  slot (63040/63042/63042/63044/63065) across manifests, wizard wiring,
  Kong generator, localhost validator, service_config, tests, and docs.
  Existing `.env` files keep their old values (migration-v4 candidate —
  see Known follow-ups).
- Test suite: default-assertions made hermetic (no longer read the live
  repo `.env`); stale `_KNOWN_NO_CONSUMER` exclusions dropped; new
  cross-seam guard pins every hardcoded localhost-port fallback literal
  to `.env.example`. Suite grew 826 → 839.

### Fixed — 2026-06-11 overnight maintenance passes 40-45 (6 commits)

- Neo4j backup tooling: `auto_restore.sh` / `restore.sh` no longer print
  "restored successfully" when `neo4j-admin database load` fails — failures
  now exit non-zero with a partial-state warning (container startup
  behavior unchanged); `ollama` pull.sh adds `PGPORT` to its required-env
  check.
- Audit scripts: `validate_research_schema.py --all` and the docs-drift
  SOURCE-matrix check fail loudly when their target globs match nothing
  instead of passing vacuously.
- Manifests: dropped Spark's unwired `supabase` data-flow edge (no JDBC
  driver or env wiring ships — same user-driven rule as Zeppelin's litellm
  exclusion) and its dead `prometheus` optional dep; documented MinIO's
  `supabase` entry as ordering-only slot pinning.
- Textual UI: `ctrl+q` hint relabeled "quit" (setup-phase quit never
  saved); removed the dead `on_complete` hook and `_refresh_topology`
  shim; refreshed stale PresentationApp/palette/wizard comments. The
  `--no-tui` linear flow now clears stale shell-exported `*_PORT` vars
  before port configuration, matching the TUI pipeline.
- Backend research surface: documented the `research_client.py` ↔
  LangGraph protocol mismatch (client targets bespoke `/research/*` +
  `/health`; `langgraph dev` serves `/ok` + `/threads` + `/runs`) as a
  known gap in all three claim sites; porting the client is a tracked
  follow-up.
- Env migrations: fresh `.env` files now ship sentinel
  `BOOTSTRAPPER_PORT_LAYOUT_VERSION=3` (no spurious v2+v3 first-run pass
  with backup-file noise), migration v3 honors its append-if-absent
  contract for `COMFYUI_CUSTOM_MODELS_FILE`, and migration v2 recognizes
  quoted URL values.

### Fixed — 2026-06-10 overnight maintenance passes 35-39 (6 commits)

- Redis README cluster closure: the per-database index map now matches
  compose reality everywhere (n8n BullMQ on db `/0`, LightRAG + Open WebUI
  sharing `/2`, JupyterHub on `/3`), Local Deep Researcher research routes
  documented as existing, and the backend's injected-but-unread `REDIS_URL`
  framed truthfully across all four cross-referencing READMEs.
- Backend README intro rewritten from the aspirational "fans out to every
  data-tier" hub claim to the actual runtime call list (Supabase, Weaviate,
  LiteLLM, ComfyUI, n8n, Ray, Local Deep Researcher); `adapts_to` list
  completed with `ray` + `lightrag`.
- Open WebUI README: removed the false "Weaviate is wired" premise from the
  hybrid-search future item and corrected the consumer boilerplate (only
  Kong + its init container consume Open WebUI).
- Hermes README: example pin `nousresearch/hermes-agent:0.13.0` (a tag that
  does not exist upstream — the registry publishes only `latest`/`sha-*`)
  corrected to `latest`; n8n README's bidirectional "agent can call a
  workflow" claim reduced to the one wired direction.
- LiteLLM README: "Prompt caching (Redis)" no longer listed as an unused
  feature (response caching is already enabled stack-wide); future item now
  points at the genuinely-unused semantic cache + per-key TTL controls.
- All `redis-cli` doc examples now carry `-a "$REDIS_PASSWORD"`
  (`requirepass` is on) and use `--scan` instead of `KEYS`.

### Fixed — 2026-06-10 overnight maintenance pass 34 (1 commit)

- README long-tail factual sweep (the last seven never-audited service
  docs): weaviate's ports corrected (63026/63027 — it listed Redis's
  63022/63023) along with its Kong route and module lists;
  local-deep-researcher's "no Kong route / no backend API" claims
  inverted (both exist); redis's database map matched to reality (n8n
  queue on db 0, JupyterHub on db 3, Kong on default 0); backend's
  required deps now include litellm and the right Hermes port (8642);
  ray's shm_size 8gb; spark documents its local S3A-enabled build;
  openclaw's deep-health command uses the real entrypoint. The full
  code remainder pool (12 widgets, utils/core internals, wizard
  sections) was read end-to-end the same pass — clean.

### Fixed — 2026-06-10 overnight maintenance passes 32-33 (2 commits)

- The dead `WEAVIATE_LITELLM_BASE_URL` chain removed (generated into
  `.env` with the same wrong `/v1` suffix, consumed by nothing,
  described falsely); stacks deployed before the `/v1/v1` fix get
  their broken Memory collection deleted and recreated at backend
  startup (Weaviate 1.27.5 forbids vectorizer-config updates, and the
  class could never store vectors anyway); reorg_user_env's
  backup-safety check honors `GENAI_ENV_FILE`; a dead langgraph.json
  that contradicted the runtime graph registration removed; linear
  banner taglines now honor `BRAND_TAGLINE`.

### Fixed — 2026-06-10 overnight maintenance passes 27-31 (5 commits)

- **Weaviate-backed memory inserts/searches 404'd on every call** — the
  collection's `text2vec-openai.baseURL` carried a `/v1` suffix that
  Weaviate's openai module joins `/v1/embeddings` onto
  (`/v1/v1/embeddings`); suffix dropped, and weaviate-init's dead
  `DEFAULT_OPENAI_BASE_URL` export (same wrong suffix, read by nothing)
  removed.
- Parakeet-GPU timestamps read NeMo's real `.timestamp` field (the
  pass-26 fix used the nonexistent `.timestep`); docling upload
  handlers gained the same filename guards.
- /etc/hosts handling is now comment-aware and address-anchored in BOTH
  directions — a commented-out `# 127.0.0.1 alias` no longer counts as
  present, and removal spares commented lines and the user's
  hyphenated lookalikes (regression tests cover both paths).
- Submodule-usage examples now show Kong's REAL routing (Supabase REST
  path-routed on the gateway root; everything else host-routed) and
  the right `SUPABASE_API_PORT`; `.env` rewrites in key_generator and
  migration_v3 are atomic + mode-preserving (backups included).
- searxng's trusted-proxy claim now matches limiter.toml; hosts-check
  and kong-consumer test nits.

### Fixed — 2026-06-10 overnight maintenance pass 26 (1 commit)

- **Every parakeet-GPU transcription request 500'd** — NeMo's RNNT/TDT
  decoder returns `List[Hypothesis]` even without `return_hypotheses`,
  and the handler passed the dataclass to `len()`; text is now
  extracted defensively (mirroring the MLX sibling) and timestamps are
  actually requested at `transcribe()` time and read from NeMo's
  `.timestamp` field, so the advanced endpoint returns real timing
  data.
- Docling chunking clamps caller-supplied `chunk_size`/`chunk_overlap`
  (an overlap ≥ size made the chunk loop never advance — unbounded
  memory growth from one bad form value), in both the shared and
  localhost copies.

### Fixed — 2026-06-10 overnight maintenance passes 21-25 (4 commits)

- **Neo4j backup/restore never worked on the shipped 5.19 image** —
  `database dump --output-name` doesn't exist (every backup failed AND
  `set -e` aborted before `neo4j start`, leaving the DB stopped), and
  community editions have no `database restore` subcommand at all. The
  scripts now dump via `--to-path` + rename, restore via `database load
  --from-stdin`, and restart Neo4j through an EXIT trap even on
  failure; the README's 4.x-isms (NEO4J_dbms_memory_*, `db.indexes()`,
  "incremental backups", wrong volume name, phantom APOC) corrected.
- node-exporter now passes `--path.rootfs=/rootfs` — without it the
  filesystem metrics the containers-and-host dashboard graphs described
  the exporter's own overlay mount, not the host disks the bind exists
  to expose.
- The ollama.com variant scraper accepts `M`-suffixed context windows
  (`10M context window` rows — the llama4 class — were silently
  dropped to coarse sizes).
- Earlier in this span: a health-probe DB connection leak closed
  (close-in-finally), the silently-no-op'd keys help-text edit landed
  for real, and the whole resource-close pattern class was exhaustively
  swept (23 sites verified safe).

### Fixed — 2026-06-10 overnight maintenance pass 20 (1 commit)

- The regen tool's doc-only boilerplate variant now also covers
  AGGREGATE folders: stt-provider / doc-processor READMEs stopped
  citing a `service.yml` they don't have (they now point at the member
  manifests that actually carry the edges); `services/comfyui/empty/`
  is committed so container-mode runs stop creating it root-owned at
  runtime; an airflow troubleshooting bullet stopped referencing the
  `OpenAIOperator` class the same README explains doesn't exist.

### Fixed — 2026-06-10 overnight maintenance pass 19 (1 commit)

- The host-run Docling localhost server loaded `.env` from the wrong
  directory (three parents instead of five — the load silently
  no-op'd) and bound the container-mode `DOC_PROCESSOR_PORT` instead of
  the stack's `DOCLING_LOCALHOST_PORT` contract (it only worked because
  the fallback happened to match); its README taught the wrong var.
- multi2vec-clip README: module lists now include the
  `text2vec-ollama`/`generative-ollama` pair (following the old disable
  snippet verbatim would have dropped them) and the env story correctly
  credits compose interpolation, not weaviate-init; the regen tool
  gained a doc-only boilerplate variant so pointer docs stop citing a
  `service.yml` they themselves say doesn't exist.

### Fixed — 2026-06-10 overnight maintenance passes 16-18 (3 commits)

- Hermes context-window guidance replaced a fabricated `ollama
  --ctx-size` flag (no such flag upstream) with the real paths:
  `OLLAMA_CONTEXT_LENGTH` on the server or `/set parameter num_ctx` +
  `/save <model>` in the REPL; the "defaults to 4096" claim updated to
  current upstream behavior (VRAM-dependent 4k/32k/256k). Test-suite
  hygiene: validator tests moved onto the shared env fixture; passes
  14-15 were zero-finding verification sweeps.

### Fixed — 2026-06-10 overnight maintenance pass 13 (1 commit)

- The speaches GPU-image rewrite now honors a shell-exported
  `SPEACHES_GPU_IMAGE` (the pin refresher's documented override path) —
  pass 12's version consulted only `.env`, losing exported pins and, in
  the no-.env-line case, silently falling back to the CPU image again.

### Fixed — 2026-06-10 overnight maintenance pass 12 (1 commit)

- The image-pin refresher now also covers pins declared as plain env
  vars (`SPEACHES_GPU_IMAGE` would otherwise go stale in user `.env`s
  on every cuda bump — and the pass-11 fix had duplicated its literal);
  the two LightRAG/TEI wizard-port test rows that pass 11's docstring
  bump promised are actually in the table now; a swept-in `.pyc` is
  untracked and the over-broad `!*` in the bundled-data gitignore
  scoped; a non-integer `LIGHTRAG_EMBEDDING_DIM` now warns before
  falling back to auto-probe.

### Fixed — 2026-06-10 overnight maintenance pass 11 (1 commit)

- **`speaches-container-gpu` actually runs the CUDA image now** — the
  compose fragment interpolates `${SPEACHES_IMAGE}` under both
  profiles, and nothing ever wired `SPEACHES_GPU_IMAGE` in despite
  three docs claiming "the speaches-gpu profile selects it" (the
  manifest's own description admitted "not yet wired"). The generator
  now resolves the winning profile's image; gpu→cpu switches self-heal
  via the pin refresher.
- **`LIGHTRAG_LLM_MODEL` / `LIGHTRAG_EMBEDDING_MODEL` /
  `LIGHTRAG_EMBEDDING_DIM` are honored** — the README told users to set
  them, but lightrag-init never received nor read them (and its WARN
  advised overriding via a var nothing consumed). The dim override now
  defaults to empty = auto-probe (a hardcoded 768 default would have
  silently bypassed the probe for non-768 models).
- LightRAG and TEI Reranker `localhost` options gained the inline
  port widget every other localhost-capable service already had; the
  cloud-providers registry docstring stopped overclaiming start.py's
  imports; three never-consumed `COMFYUI_*_PATH` vars removed from the
  manifest/.env.example.

### Fixed — 2026-06-10 overnight maintenance pass 9 (1 commit)

- Clearing log filters while the source popup is open no longer gets
  silently reverted by the popup's stale snapshot on dismiss; one
  garbled docstring from pass 8 rewritten whole; hermes config template
  comment now cites the `ollama/`-prefixed id LiteLLM actually
  publishes.

### Fixed — 2026-06-10 overnight maintenance pass 8 (1 commit)

- The atomic `.env` write clamps the tmp file's mode BEFORE secrets are
  written (no umask-default window beside a 0600 `.env`); two LiteLLM
  docstrings corrected to match actual behavior (a missing config stub
  is always written; non-container custom Ollama models are registered
  with a warning, not ignored).

### Fixed — 2026-06-10 overnight maintenance pass 7 (1 commit)

- The `--no-tui` banner now honors the `BRAND_*` rebranding knobs (it
  hardcoded the upstream credits while the Textual wizard rebranded);
  the atomic `.env` write preserves the file's mode, cleans up its tmp
  sibling on failure, and `.env.tmp` is gitignored; the launch-time
  skip-prune moved to a module-level helper so its regression test
  binds to production code instead of an inline replica.

### Fixed — 2026-06-10 overnight maintenance pass 6 (1 commit)

- Pre-launch command summary no longer shows flags from steps the user
  later hid via Back-navigation (matches what launch actually
  persists); the SOURCE-override `.env` rewrite is now atomic
  (tmp + `os.replace` — a crash mid-write used to truncate `.env`);
  launch-time prune gains a regression test; a migration docstring
  stopped overstating its STT involvement.

### Fixed — 2026-06-10 overnight maintenance pass 5 (2 commits)

- **Both seeded n8n research workflows were broken end-to-end**: the
  weekly scheduler sent `user_id: "system_scheduler"` (backend casts to
  UUID → 500 on every run, forever); the SearXNG research workflow's
  model-lookup compared the integer `content` column to a boolean (no
  such Postgres operator → node always errored) and its AI-summary node
  read `$env.LITELLM_BASE_URL` / `LITELLM_API_KEY`, which were never in
  the n8n containers' env (URL rendered "undefined/…"). All three
  fixed; the LiteLLM vars are now injected into n8n AND n8n-worker
  (queue mode) with the runtime_sc dual-write.
- Curated OpenRouter id corrected to `anthropic/claude-sonnet-4.6`
  (OpenRouter serves the dot form; the hyphen form is Anthropic-direct
  only — verified against the live models API).
- A stale picker commit no longer persists after Back-navigating and
  disabling the owning service (skip-hidden steps are pruned at
  launch).
- `services/docling/provider/localhost/requirements.txt` removed — it
  duplicated pyproject+uv.lock (the actual install path), and
  Dependabot bumps to it alone would silently re-drift the pins the
  lock-gate can't see.
- redis fragment header no longer claims the fragment is unreferenced.

### Fixed — 2026-06-10 overnight maintenance pass 4 (2 commits)

- **`--base-port` runs now persist `BASE_PORT` itself** — the port
  rewriter updated every `*_PORT` but never the anchor, so the very
  next flagless run (which preserves `.env`'s `BASE_PORT` since pass 1)
  read the stale 63000 and silently reverted the whole custom layout.
- **Upgrading an old `.env` with `COMFYUI_MODEL_SET` now activates real
  models** — migration_v3 translated to catalog-phantom names
  (`sd15-pruned-emaonly` / `sdxl-base-1.0` exist nowhere), so
  catalog-init activated only the VAEs and every seeded workflow failed
  at render. The SD1.5/SDXL-base checkpoints now live in the curated
  catalog layer (present regardless of scrape outcome) and the
  translation emits their real names.
- ComfyUI `[pulled]` badges now also match the catalog `filename`
  column (civitai/sidecar downloads were never recognized on re-runs);
  an explicit deselect-all in the ComfyUI picker now clears
  `COMFYUI_USER_MODELS` like the Ollama picker; the consolidation-log
  tense map accepts both tense forms; the dead per-source
  `COMFYUI_ARGS`/`AUTO_UPDATE` keys left in `runtime_sc` are gone;
  `DASHBOARD_PASSWORD`'s `.env.example` description now documents the
  auto-rotation; Studio auth nuance documented (Kong route gated,
  direct port open).

### Fixed — 2026-06-10 overnight maintenance pass 3 (5 commits)

- **CRITICAL (self-caught): the pass-2 airflow quote-safety fix broke
  airflow-init on every boot** — psql performs `:'var'` interpolation
  only in script input, never inside `-c` strings, so both role
  statements errored under `set -e`. Statements now pipe via stdin
  (quote-safe AND functional; verified empirically against a live
  Postgres).
- **Ollama model picker no longer duplicates every pulled model**: the
  "pulled-but-not-in-library" bucket compared tagged names
  (`qwen3.6:latest`) against bare library families (`qwen3.6`), so each
  normal pull also surfaced as a bogus "(local model, not in public
  library)" row. The picker's `/api/tags` probe also now honors
  `OLLAMA_LOCALHOST_PORT` (the 5th consumer site of the localhost-port
  symmetry rule).
- **`./stop.sh --clean-hosts` no longer deletes the user's own
  /etc/hosts entries** — removal matched substrings, so a personal
  `127.0.0.1 my-n8n.localhost` line vanished because it *contains* a
  stack alias. Now whole-token comparison.
- **Memory consolidation log recorded every merge as "superseded"** —
  the action guard compared past-tense values against the LLM
  contract's present-tense vocabulary.
- Smaller correctness: migration_v3 no longer drops the user's
  COMFYUI model-set translation when the old line carried an inline
  comment; a list-rooted ComfyUI sidecar YAML no longer crashes the
  wizard/catalog-init (warn + ignore per its never-raises contract);
  `http.client.HTTPException` (IncompleteRead etc.) is now caught at
  all six catalog/scrape fetchers; the service.yml schema rejects
  typo'd keys inside `runtime_sc.<container>.<source>` blocks
  (previously silently dropped).
- Refactors (output-verified): the five uniform SPA Kong routes
  (prometheus/spark-master/spark-history/airflow/zeppelin) collapsed
  into one data-driven table — generated config byte-identical across
  4 SOURCE permutations; capture-free helpers hoisted out of the
  285-line `build_ollama_steps`; four duplicated test env-splice loops
  replaced by a shared `env_with_overrides` conftest factory.
- Docs/CI: CONTRIBUTING's CI-gates section now documents all four jobs
  + the four-seam picker-flag rule; minio image note drops a
  placeholder CVE id; `.gitignore` sheds two dead personal-scratch
  entries; kong/comfyui READMEs lose claims invalidated this run.

### Fixed — 2026-06-10 overnight maintenance pass 2 (6 commits)

- **`storage.objects` was dropped and recreated on EVERY `docker compose
  up`** (04-storage.sql) — all Supabase Storage object metadata (ComfyUI
  uploads included) silently vanished on each restart, and storage-api's
  own migration ledger stayed marked applied so its later columns never
  came back. Now `CREATE TABLE IF NOT EXISTS` like every sibling table.
- **Grafana dashboards re-verified against the PINNED upstream versions**
  (the previous fix validated against upstream master): kong.json's four
  panels all used Kong 2.x metric names that don't exist in kong:3.9.0
  (`kong_http_requests_total` / `kong_request_latency_ms_bucket` /
  `kong_bandwidth_bytes{direction}` now); both Weaviate app-tier panels
  used master-only `weaviate_module_*` metrics absent from 1.27.5
  (unprefixed `requests_total{api}` / `queries_durations_ms_bucket`);
  the n8n uptime fix had replaced a correct prefixed name with an
  unprefixed one (`n8n_process_start_time_seconds` is right —
  prom-client default metrics ARE prefixed); litellm failed-requests
  grouped by labels that don't exist (`requested_model` /
  `exception_class` now). Datasource provisioning gains an explicit
  `uid: Prometheus` matching every panel ref; `GF_SERVER_ROOT_URL` now
  carries the Kong port.
- **Supabase Studio is now actually behind the documented credential
  gate**: the Kong dashboard route shipped with only CORS — no
  basic-auth, no ACL — while README/.env promised
  `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` protection (the consumer +
  auto-rotated password existed; the route just never used them).
- **Pass-1 regressions caught by an adversarial diff review and fixed**:
  the localhost-validator port conversion fed a string port into
  `socket.connect_ex` (Neo4j probe always failed even with a live
  listener) and used blank-value-unsafe `dict.get`; `GET /workflows`
  would have flipped its wire format to camelCase (validation-only
  aliases now); a degraded model-fetch's KEEP sentinel leaked into the
  command summary and could wrongly flip a cloud provider's overview
  state.
- **local-deep-researcher could be configured against model ids LiteLLM
  never serves** — its init prefixed every provider (`openai/gpt-…`,
  `openrouter/openrouter/…`); only Ollama rows carry a prefixed alias.
  Same family: LightRAG's default-chat fallback picked the first
  /v1/models entry, which is typically an embeddings-only route — now
  filters out embedding/agent/self entries.
- **Six seeded workflows/tools referenced checkpoint filenames the
  download pipeline never produces** (`sd_v1-5_pruned_emaonly` vs the
  catalog's `v1-5-pruned-emaonly`, `sdxl_base_1.0` vs `sd_xl_base_1.0`)
  — every seeded ComfyUI workflow failed at render even with the model
  installed. Civitai catalog entries also gain a real `filename` (their
  download URLs have none, so files landed extension-less where ComfyUI
  never lists them).
- **LightRAG's Neo4j migration never applied while logging OK** — the
  whole multi-statement cypher file went up as a single tx statement
  (guaranteed syntax error) and Neo4j reports errors inside an HTTP 200
  body the script never read. Now split per-statement + errors[] gate.
  Its pgvector meta table also gains the PK that made `ON CONFLICT DO
  NOTHING` a no-op (one new row per boot, with self-heal for existing
  installs).
- **CI hardening**: backend's pytest suite now runs in the required
  `Manifest lint + unit tests` check (it previously ran nowhere); the
  docling localhost provider gets a `uv lock --locked` gate (no
  Dockerfile → build-validation can't see its pins); all GitHub Actions
  are SHA-pinned; `check_doc_links.py` now validates `#anchor`
  fragments against GitHub heading slugs (and immediately caught a dead
  `{#launch-log}` kramdown anchor GitHub never supported);
  `check-kong-routes.py` now covers all 17 default-emitted hosts (was
  9). hermes-init's model dedup no longer hides direct-API cloud
  entries when OpenRouter twins exist; ollama-pull's wait is bounded
  and pull errors inside HTTP-200 NDJSON are surfaced; n8n
  community-package checks parse n8n's `{"data": …}` envelope;
  memory-table RLS policies now actually scope to `service_role`
  (`USING (true)` + default-privilege grants had left authenticated
  PostgREST callers full CRUD on all memories).
- **Init hardening + dead-chain removals** (same commit as the CI
  gates): airflow-init's role statements switched to quote-safe psql
  `:'pw'` interpolation (NOTE: this introduced the regression pass 3's
  first bullet fixes — `-c` strings don't interpolate); openclaw's
  inline config patcher got `set -e` + tmp-file writes (a missing jq
  used to truncate openclaw.json to 0 bytes); db-init-runner's DB wait
  is bounded (300s); minio-init now refreshes service-account secrets +
  policies on re-runs (rotations used to silently never propagate); the
  dead `IS_LOCAL_COMFYUI` chain, unread `WEBUI_ADMIN_*` container env,
  and two never-called legacy methods in the research streaming tool
  were removed.
- Docs: zeppelin README no longer claims `%spark` works without the
  Spark-Connect setup (the image ships no Spark distro) and its starter
  notebook uses `spark.version` (no `sc` under Connect); comfyui README
  stops claiming the bootstrapper injects `--force-fp16`/`AUTO_UPDATE`
  per source (all static via `.env`); searxng's "Redis is wired"
  claims corrected everywhere (`valkey.url: false`, dependency is
  slot-pinning only); n8n README's Hermes→n8n inverse path is
  webhook-based (no execute endpoint exists); ROADMAP counts corrected
  to 32 families / 62 containers.

### Fixed — 2026-06-10 overnight maintenance pass 1 (18 commits)

- **`N8N_SOURCE=disabled` never disabled n8n.** `N8N_SCALE` was read from
  `.env` with the manifest value as a mere dict-default; the key always
  exists, so the source was never consulted and n8n/n8n-worker/n8n-init
  all started anyway. Scale now derives from the manifest per source,
  and the dependency manager's auto-disable now zeroes worker/init
  scales too (it previously left both running against a dead main) and
  no longer sticks after the violated dependency is re-enabled.
- **`DOC_PROCESSOR_SOURCE=docling-container-gpu` wiped the
  speaches/parakeet/chatterbox compose profiles** — the doc-processor
  generator rebuilt `COMPOSE_PROFILES` from a dict that never contains
  that key instead of stacking onto the shared tally, so enabling
  Docling-GPU silently excluded the active STT/TTS containers. The
  pipeline now owns `COMPOSE_PROFILES` end-to-end (seeded empty each
  run, so stale profiles from since-disabled sources also clear) and
  the var is declared auto-managed in the globals manifest.
- **`GENAI_ENV_FILE` was half-wired**: all four `docker compose` argv
  builders hardcoded `--env-file=.env` (compose silently ran against
  the wrong file), `KeyGenerator` wrote generated secrets to the
  repo-root `.env`, and a relative path resolved against CWD (differs
  between the uv launcher and the system-python fallback). All seams
  now honor the resolved path.
- **Multiple invalid `*_SOURCE` values reported "✅ All SOURCE values
  are valid" while exiting 1** — the per-value validator reset the
  shared error list on every call, so only the last variable's errors
  survived.
- **Kong's n8n route emitted a literal `${KONG_HTTP_PORT}` into
  `X-Forwarded-Host`** (Kong DB-less config does no env interpolation),
  so n8n baked the unexpanded token into webhook/editor URLs served via
  `n8n.localhost`. The port is now resolved at generation time.
- **`./start.sh --no-tui` (and any non-TTY run) silently reset a custom
  port layout** — the linear flow fell straight to base port 63000
  instead of preserving the `BASE_PORT` already configured in `.env`,
  rewriting every `*_PORT` and leaving `.env` self-inconsistent. It now
  mirrors the TUI's read-from-.env fallback.
- **Wizard (TUI) fixes**: the ComfyUI model picker now honors the source
  you just selected instead of the stale pre-wizard `.env` value (it
  used to hide after enabling ComfyUI, and show for a just-disabled
  one); "No — exit without starting" on the final confirm actually
  exits (was a silent no-op); launch-phase crashes surface in the log
  pane instead of freezing the UI silently; a failed model-catalog
  fetch no longer lets a single Enter wipe your saved
  `OLLAMA_USER_MODELS` CSV; ComfyUI filter chips no longer desync from
  the row filter on `f`-cycling; the `[pulled]` badge scan now resolves
  the real `<project>-comfyui-models` volume mountpoint via
  `docker volume inspect` instead of scanning a host path that never
  exists.
- **Backend API**: `/storage/upload` called storage3 methods that don't
  exist (every upload 500'd — now uses the per-bucket `from_()` API);
  `GET /workflows` returned n8n's `{data: …}` envelope raw (failed
  response validation — now unwrapped, with camelCase timestamp
  aliases); `POST /workflows/{id}/execute` removed (n8n's public API
  v1 has no such endpoint — the route could never succeed);
  `/comfyui/cancel/{id}` now deletes queued prompts via `POST /queue`
  and only interrupts when the prompt is actually running (it used to
  abort whatever was running); plus 400/404 correctness on
  research-session listing, ComfyUI model CRUD, and image fetch, and
  JSONB metadata decoding on memory update. `N8N_API_KEY` is now a
  declared (empty-by-default) env var passed to the backend — n8n CE
  only issues keys via its UI, and the `/workflows` endpoints 401
  without one.
- **`RAY_ADDRESS` never reached any container** — declared only in
  `runtime_adaptive` (which writes `.env`, not container env), so every
  `/api/ray/*` backend route 503'd and notebook 07 reported "Ray is
  disabled" even with Ray enabled. Now injected via the backend and
  jupyterhub compose environment blocks (+ `RAY_DASHBOARD_URL`
  declared).
- **n8n queue-mode workers were missing every workflow-facing env var**
  (`STT_ENDPOINT`, `TTS_ENDPOINT`, `DOCLING_ENDPOINT`, `WEAVIATE_URL`,
  Hermes/LightRAG endpoints, `GENERIC_TIMEZONE`) — and the stack
  defaults to queue mode, so `$env.*` resolved empty exactly where
  workflows actually execute. Mirrored into the worker block.
- **JupyterHub notebooks**: 02_langchain_rag crashed at cell 1
  (`langchain-openai` was never installed — now pinned, plus an
  explicit `openai` pin that was previously only transitive);
  00_environment_check's PostgreSQL probe always printed ❌ under
  SQLAlchemy 2.x (raw-string `execute` — now `text()`), its "Ollama"
  probe actually hit LiteLLM with an endpoint LiteLLM doesn't serve,
  and its HTTP checker treated 404/500 responses as ✅.
- **`stop.sh` always exited 0** even when `docker compose down` failed
  (undetectable to scripts/CI) and told users to restart with a
  nonexistent `./start.py`.
- **Security**: docling 2.93.0 → 2.94.0 (CVE-2026-47214, 3 high
  alerts) + starlette 1.0.0 → 1.2.1 in the docling localhost-provider
  lock; 3 phantom Dependabot alerts on the retired
  `tts-provider/localhost/` path dismissed as `not_used`.
- **`.env` parsing is now quote-aware**: `PASSWORD="ab#cd"` was
  silently read as `ab` (any `#` truncated the value); quoted hashes
  are data, and unquoted hashes only start a comment after whitespace.
- **Localhost-port override symmetry completed**: the localhost
  validator now reads `OLLAMA_LOCALHOST_PORT` / `COMFYUI_LOCALHOST_PORT`
  / `WEAVIATE_LOCALHOST_PORT` / `NEO4J_LOCALHOST_BOLT_PORT` like every
  other consumer, instead of probing hardcoded ports and warning
  falsely on overridden setups.
- searxng's compose no longer gates startup on redis (`valkey.url:
  false` — pure coupling; the manifest keeps a slot-pinning entry).

### Changed — 2026-06-10 overnight maintenance pass 1

- `data_flow.calls` corrected across five manifests (and all per-service
  README §Deps tables + diagrams regenerated): local-deep-researcher
  +supabase (its init reads `public.llms` over psycopg2); jupyterhub
  now mirrors the env surface its notebooks actually use (+comfyui,
  +n8n, +backend, +searxng; −minio which had no env and no notebook);
  open-webui now models its real edges (+supabase app DB, +redis
  websocket manager, +backend extras tools; −weaviate and −searxng,
  which have no wiring today and stay documented as Future pairs);
  backend −lightrag (env passed but unread); prometheus +grafana
  (the scrape job existed, the mirror didn't).
- Documentation: hierarchical numbered headings enforced across 10
  guides (CONTRIBUTING-services, troubleshooting, the four deployment
  docs, diagrams/research/services READMEs, SECURITY) with anchors
  rewritten; fabricated `admin@example.com / changeme123` credentials
  replaced with the real auth story (Kong basic-auth + auto-rotated
  `DASHBOARD_PASSWORD`; n8n first-visit owner setup); `.env.example`
  provenance corrected everywhere (it is generated from manifests —
  never hand-edit); supabase README per-service ports fixed (5
  off-by-one entries); troubleshooting volume names fixed
  (`atlas-supabase-db-data`, not `atlas_supabase_db_data`);
  source-matrix rows added for `RAY/AIRFLOW/SPARK/ZEPPELIN_SOURCE`;
  wizard-guide "5a" heading renumbered into a clean 1–18 sequence;
  docs hub now links the research-corpus guide and superpowers
  plans/specs; test counts updated to 800+.

### Fixed — Critical bugs caught by the 2026-06-08 overnight audit

- **`services/open-webui/init/scripts/register-tools.py:create_admin_user`
  shipped with a duplicate `timeout=30` keyword argument**, raising
  `SyntaxError: keyword argument repeated: timeout` at module-import
  time on every open-webui-init container boot since PR #67. The
  function's broad `except Exception` swallowed the SyntaxError as a
  generic "Signup request failed", so the admin user silently never got
  created — open-webui-init's 60-attempt retry loop then exited 1 with
  "No admin user found". Fix removes the duplicate kwarg.
- **`services/grafana/config/provisioning/dashboards/*.json` shipped
  with 12 metric names that don't exist in upstream LiteLLM / n8n /
  postgres-exporter / Weaviate / Prometheus**. Every affected panel
  rendered "No data" indefinitely. Verified firsthand against canonical
  source files (LiteLLM `prometheus.py`, n8n `prometheus-metrics.service.ts`,
  postgres-exporter `pg_stat_user_tables.go`, Weaviate monitoring docs,
  Prometheus config docs):
  - litellm.json: 4 panels — `litellm_requests_total`,
    `litellm_total_tokens`, `litellm_request_latency_bucket`,
    `litellm_failed_requests_metric` corrected to the upstream
    `_metric` / `litellm_proxy_*` / `litellm_request_total_latency_metric_bucket`
    names.
  - n8n.json: complete rewrite (5 panels) — upstream emits
    `n8n_workflow_execution_duration_seconds`, `n8n_active_workflow_count`,
    `n8n_execution_data_writes_total`, not `n8n_workflow_executions_total`
    / `n8n_active_workflows` / `n8n_total_workflows` / `n8n_process_start_time_seconds`.
  - postgres-redis.json: `pg_relation_size_bytes` → `pg_stat_user_tables_table_size_bytes`.
  - app-tier.json: `weaviate_queries_total` + `weaviate_objects_total`
    → `weaviate_module_requests_total` + `weaviate_module_request_duration_seconds_bucket`;
    `minio_bucket_usage_total_bytes` (only at `/metrics/bucket` which
    we don't scrape) → `minio_cluster_usage_total_bytes`.
  - stack-overview.json: `up{stack="atlas"}` → `up`. The Prometheus
    docs explicitly note `global.external_labels` only apply to
    `remote_write`/federation/Alertmanager, NEVER to locally-scraped TSDB
    series — the selector matched zero series. Panel title also updated to
    drop "Hermes" (Hermes ships no `/metrics`).
- **`services/lightrag/service.yml::runtime_adaptive.lightrag-init.failure_mode`
  contract was wrong** — declared "lightrag-init exits non-zero; LightRAG
  container does not start" when LiteLLM is unreachable, but
  `resolve-models.py:42` catches URLError + JSONDecodeError and returns
  `[]`, then `main()` falls back to env-var defaults / hardcoded
  `ollama/nomic-embed-text` + dim=768 and exits 0. Realigned to
  "lightrag-init logs warning, falls back to env-var defaults; LightRAG
  starts but every chat/embed call 502s until LiteLLM becomes reachable".

### Fixed — Init container resilience (7 unbounded loops)

- `services/weaviate/init/scripts/init-weaviate.sh:18` —
  `until psql ... do sleep 5; done` had no upper bound; a persistently
  unreachable Supabase DB would hang weaviate-init forever. Bounded to
  300s (mirrors n8n / minio patterns).
- `services/hermes/init/scripts/init-hermes.sh:100` — curl to LiteLLM
  `/v1/models` gained `--max-time 15`. Previously a LiteLLM-side stall
  blocked hermes-init for the OS default TCP timeout (~75s).
- `services/comfyui/init/scripts/download_models.sh:88` — wget gained
  `--timeout=30 --tries=3` so a stalled HF/civitai mirror doesn't hang
  a multi-GB download.
- `services/n8n/init/scripts/install-nodes.sh` — 4 curl sites missing
  `--max-time` (readiness probes capped at 5s, GET community-packages
  at 15s, POST install at 120s).
- `bootstrapper/utils/system.py` — 3 `subprocess.run` sites
  (`docker version`, `docker network inspect`, `docker run --rm alpine`)
  + the generic `run_command()` helper gained explicit `timeout=` (10s
  / 60s) with `subprocess.TimeoutExpired` added to the except clauses.

### Fixed — Documentation drift (MinIO ports, TEI memory guide)

- `services/minio/README.md:12-13` + `docs/ROADMAP.md:63` — both files
  advertised the MinIO admin console on `63018` and S3 API on `63017`,
  contradicting `.env.example`'s `MINIO_PORT=63018` (S3 API) and
  `MINIO_CONSOLE_PORT=63019` (console). User-facing instructions now
  match.
- `services/tei-reranker/README.md:98` — CPU memory guidance still
  quoted BGE-reranker-v2-m3 needing ~3 GB; updated to mxbai-rerank-base-v1
  (~1.5 GB) which has been the default since 2026-06-07.

### Fixed — Build & supply-chain hygiene

- `services/{litellm/init,litellm/catalog-init,comfyui/catalog-init}/Dockerfile`
  — patch-version pinned `FROM python:3.12-slim → python:3.12.7-slim`
  (floating tags admit moving targets without operator visibility).
  comfyui/catalog-init also gained pinned `requests==2.32.3` and
  `PyYAML==6.0.2` for the same reason.
- `bootstrapper/pyproject.toml` — migrated `[tool.uv].dev-dependencies`
  → PEP 735 `[dependency-groups].dev`. The old table is deprecated and
  uv warns on every invocation. CI workflow updated to
  `uv sync --group dev`.
- `.github/dependabot.yml` — added `torchao` to the torch+PyG ignore
  list. torchao tracks torch's minor version (PyTorch ecosystem); an
  auto-bump would silently break against the current `torch==2.4.1` pin.
- `bootstrapper/services/dependency_manager.py:245` — narrow second
  `except Exception` on .env-rewrite path → `except OSError`. PR #67
  narrowed line 221 but missed this parallel block.

### Tests — Structural regression guards

- `bootstrapper/tests/test_init_scripts_compile.py` — parametrised
  `py_compile` over every `services/*/init/scripts/*.py` + parametrised
  `bash -n` over every `*.sh` + AST-walk for duplicate kwargs. Closes
  the gap that let the open-webui-init SyntaxError ship.
- `bootstrapper/tests/test_dockerfile_pins.py` — every
  `services/**/Dockerfile`'s non-ARG FROM must use a digest or a
  patch-version-pinned tag (major.minor.patch prefix). Locks the Pass 1
  pin posture in CI.
- `bootstrapper/tests/test_pyproject_dependency_groups.py` — guards
  PEP 735 `[dependency-groups].dev` contract; fails if a future edit
  re-introduces deprecated `[tool.uv].dev-dependencies`.

### Docs — Top-level architecture diagram refreshed

`docs/diagrams/architecture.svg` (and its `architecture.html` standalone
view) refreshed to reflect the current 33-service stack. Eight services
shipped since the diagram was last hand-authored were absent:
**LightRAG** + **TEI Reranker** (2026-06-05, PR #62), **Apache Airflow**
+ **Apache Spark** + **Apache Zeppelin** (2026-06-05, PR #35), and
**Ray** + **Prometheus** + **Grafana** (earlier in 2026).

Layout additions: Zeppelin joins APPS (5 cards), LightRAG + Airflow
join AGENTS (5), TEI Reranker joins LLM CORE (4), Spark sits beside
Ray in DISTRIBUTED COMPUTE (2), and a new OBSERVABILITY band carries
Prometheus + Grafana. ViewBox grew from 1400×1100 to 1400×1240 to
host the new bands without compressing the existing topology.

The README's embedded diagram updates transparently (GitHub renders
the SVG inline). The corresponding "Known follow-up" entry under
[Unreleased] is removed.

### Security — Auto-rotate 8 weak credential placeholders on first launch

`.env.example` shipped publicly-known defaults for 8 credential vars
that survived a clean `cp .env.example .env && ./start.sh` boot
unchanged. The worst was `N8N_ENCRYPTION_KEY=your-random-encryption-key`
(n8n AES-encrypts every saved workflow credential under it, so saved
API keys / OAuth tokens were recoverable from the on-disk SQLite blob
by anyone reading the public repo). Others: `SUPABASE_DB_PASSWORD=password`,
`SUPABASE_DB_APP_PASSWORD=app_password`, `GRAPH_DB_PASSWORD=neo4j_password`
(Neo4j; also rewrites the composite `GRAPH_DB_AUTH=neo4j/<password>`),
`REDIS_PASSWORD=redis_password`, `DASHBOARD_PASSWORD=kong_password`
(Kong admin), `OPEN_WEB_UI_ADMIN_PASSWORD=admin`, `OPEN_WEB_UI_SECRET_KEY=secret`.

`bootstrapper/utils/key_generator.py` now carries a `PLACEHOLDER_DEFAULTS`
dict and a `_is_placeholder_or_empty()` helper; per-rotator
`generate_and_update_*` methods upgrade the placeholder on first launch
and preserve any operator-supplied real value (rotating mid-run would
lock out the existing database/role/user — destructive). The aggregator
in `generate_missing_keys()` wires all 8 rotators alongside the existing
LiteLLM / Hermes / Airflow / Grafana / MinIO / SearxNG generators.

Operator action: hand-edited `.env` files with custom values are left
alone. Fresh installs (or any `.env` still carrying a placeholder)
will rotate to a random value on the next `./start.sh`.

### Fixed — start.py cold-start port-clear + TUI launch flag pass-through

Two latent bootstrapper holes surfaced by the overnight audit loop:

1. `unset_port_environment_variables` was missing 9 port slots added
   by PR #29 / PR #35 (`RAY_DASHBOARD_PORT`, `RAY_CLIENT_PORT`,
   `RAY_GCS_PORT`, `SPARK_MASTER_UI_PORT`, `SPARK_HISTORY_PORT`,
   `AIRFLOW_PORT`, `ZEPPELIN_PORT`, `PROMETHEUS_PORT`, `GRAFANA_PORT`).
   Cold-start with a custom `--base-port` would have shell-export-shadowed
   the freshly-computed slot for any of these services with a stale value.

2. The TUI-launch flow's `stack_options` carried `cloud_user_models` and
   `ollama_user_models` filters but had no catch-all bucket for scalar
   env-write flags (`COMFYUI_CUSTOM_MODELS_FILE`, `RAY_WORKER_COUNT`,
   `PROMETHEUS_RETENTION_DAYS`, `SPARK_WORKER_COUNT`). On the
   `./start.sh --flag <value>` path under a TUI-capable terminal, all
   four flags were silently dropped (they only worked under `--no-tui`).
   New `user_env_writes` bucket carries the residual unfiltered keys
   through to the same `apply_user_model_selections` pipeline.

### Fixed — LightRAG init resilience + open-webui init timeouts

- `services/lightrag/init/scripts/resolve-models.py` embed-dim probe
  no longer swallows `Exception` — narrowed to
  `(URLError, JSONDecodeError, KeyError, IndexError)`. The wide swallow
  silently fell back to `dim=768` against a 1024-dim store on transient
  failures, then every runtime insert failed with "dimension mismatch"
  with no log trail.
- `services/lightrag/init/scripts/init-lightrag.sh` writes
  `resolve-models.py` output to `/app/data/.env.tmp` then `mv`
  atomically — the plain `>` redirect truncated the destination BEFORE
  python ran, so a script crash left the file empty and lightrag
  booted with no `LLM_MODEL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM`.
- `services/open-webui/init/scripts/register-{tools,functions}.py`
  picked up missing `requests.get`/`requests.post` timeouts (10s/30s),
  `psycopg2.connect(connect_timeout=5)` to bound the TCP-handshake
  worst case, and a `try/finally` pattern around DB cursor+conn so a
  restart loop doesn't leak one connection per attempt.

### Fixed — Documentation post-migration drift sweep

After PR #29/PR #35/PR #47 port reshuffles, ~25 stale port literals
remained scattered across READMEs (root README, services/n8n/README.md,
services/openclaw/README.md, services/neo4j/README.md, services/redis/README.md,
services/supabase/README.md, docs/deployment/submodule-usage.md,
docs/quick-start/troubleshooting.md, docs/deployment/source-configuration.md)
and ROADMAP.md carried wrong Kong-route shape + ports for the shipped
LightRAG + TEI Reranker entries. Stale `external`/`api` source-variant
references in README, source-configuration.md, and wizard-guide were
also scrubbed.

### Fixed — Manifest data_flow.calls gap for LightRAG / TEI Reranker

`services/kong/service.yml::data_flow.calls` was missing `lightrag` +
`tei-reranker` despite live Kong routes; `services/hermes/service.yml`,
`services/n8n/service.yml`, `services/backend/service.yml` each had
`runtime_adaptive.adapts_to lightrag` with compose passing the env
vars, but the manifest's `data_flow.calls` had no matching row — so
the auto-generated §5.2 / §6.2 tables and per-service architecture
diagrams omitted the edge. Filled all four gaps + regenerated docs
and the hermes byte-equivalence golden fixtures.

### Fixed — LightRAG three small drift bugs

- `LIGHTRAG_RERANK_BINDING_HOST` manifest declaration aligned with
  `service_config.py`'s imperative `/rerank` append (the two sources
  of truth had drifted).
- `LIGHTRAG_DOC_STATUS_STORAGE` default unified to `RedisDocStatusStorage`
  across `service.yml` / `compose.yml` fallback / README (three-way
  split was using `RedisKVStorage` in two of them).
- `services/hermes/service.yml::runtime_adaptive.hermes-init.environment_adaptation`
  was missing `LIGHTRAG_API_KEY` — the compose env block + init script
  + template all read it, but the manifest under-specified the
  cross-service contract.

### Fixed — Narrow broad except clauses in 3 bootstrapper modules

`bootstrapper/utils/hosts_manager.py` (6 sites),
`bootstrapper/core/docker_manager.py` (4 sites), and
`bootstrapper/services/dependency_manager.py` (2 sites) all carried
bare `except Exception` blocks that silently absorbed real bugs
(malformed regex, attribute typos, KeyError) alongside the intended
OS-level failures. Narrowed each to its actual failure surface
(`OSError`, `UnicodeDecodeError`, `subprocess.SubprocessError`,
`psycopg2.Error`) so future regressions in these modules surface
loudly instead of being silently absorbed into safe-default returns.
Behavioral diff: previously-masked TypeError / AttributeError / etc.
now propagate.

### Tests — Regression-guard additions

- `tests/test_lightrag_manifest_imperative_parity.py` (new):
  asserts both ends of the `LIGHTRAG_RERANK_BINDING_HOST` contract end
  in `/rerank` so a future manifest edit can't silently drift from the
  imperative emitter in `bootstrapper/services/service_config.py`.
- `tests/test_user_model_selections_seam_parity.py::test_tui_launch_carries_user_env_writes_bucket`
  tightened: was a loose AST walk accepting any Dict literal with a
  `user_env_writes` key; now requires the key live on the specific
  `Assign(targets=[Name('stack_options')])` Dict AND its value be a
  `DictComp` over `user_model_selections.items()`. Stub assignments now
  fail loudly.
- `tests/test_lightrag_litellm_registration.py`: stub `psycopg2` /
  `psycopg2.extras` in `sys.modules` before exec_module so the 3
  lightrag_model_entry tests run in any bootstrapper venv (matched the
  established pattern from `test_catalog_init_auto_import.py`).
  Previously these tests silently failed locally — CI's resolved dep
  tree pulled psycopg2 transitively, masking the breakage.

### Fixed — CI hygiene

- Top-level `permissions: contents: read` on `.github/workflows/services-lint.yml`
  (no job needs write scopes; principle-of-least-privilege).
- Path-filter expanded with `LICENSE` and `.gitattributes` to prevent
  required-checks deadlock on a config-only PR (the same class of bug
  PR #48 hit on `.github/dependabot.yml`).
- `services/open-webui/init/Dockerfile` bumped `python:3.11-slim` →
  `python:3.12-slim` (psycopg2-binary 2.9.9 ships cp312 wheels; the
  3.11 pin was no longer load-bearing) and pinned
  `requests==2.32.3`/`psycopg2-binary==2.9.9`/`PyJWT==2.10.1` against
  upstream-regression surprise.
- `services/docling/provider/gpu/Dockerfile` ARG default aligned to
  `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` (the manifest pin).

### Added (LightRAG service)
- New `services/lightrag/` manifest: graph-augmented RAG server pinned to `ghcr.io/hkuds/lightrag:v1.5.0`. Default `disabled`.
- Storage adapts to Supabase pgvector, Neo4j, Redis with in-process fallback when any backend source is `disabled`.
- Registered with LiteLLM as the `lightrag` model (Ollama-shim served via OpenAI adapter); reachable transitively by open-webui, openclaw, n8n, hermes, backend, local-deep-researcher, jupyterhub.
- Wired into `runtime_adaptive` of hermes/n8n/backend for direct calls.
- Kong route `lightrag.localhost` with `preserve_host: True` (WebUI SPA).
- Init container resolves LLM/embedding model + dim from LiteLLM `/v1/models` at boot.
- RAG-Anything is NOT added — subsumed by LightRAG v1.5.0's multimodal pipeline.

### Added (TEI Reranker service)
- New `services/tei-reranker/` manifest: HF text-embeddings-inference running BAAI/bge-reranker-v2-m3. Default `disabled`. Four source variants: `container-cpu`, `container-gpu`, `localhost`, `disabled`.
- Reusable by compatible consumers through its REST endpoint. Direct LightRAG-to-TEI reranking is disabled by default because their rerank payload shapes are incompatible without an adapter.
- Kong route `rerank.localhost`.

### Added — Apache Airflow + Apache Spark cluster + Apache Zeppelin (data / apps / agents bands)

Three new services added in a single coordinated landing as the stack's
compute / orchestration tier. Spec at
[`docs/superpowers/specs/2026-06-04-airflow-spark-zeppelin-design.md`](superpowers/specs/2026-06-04-airflow-spark-zeppelin-design.md);
plan at
[`docs/superpowers/plans/2026-06-04-airflow-spark-zeppelin.md`](superpowers/plans/2026-06-04-airflow-spark-zeppelin.md).

- **Spark cluster** (`SPARK_SOURCE=disabled|container`) — Apache Spark
  4.1.2 in standalone mode, 5-container family: 1 master + N workers
  (default 2, range 1-8 via the new `--spark-workers` flag mirroring
  Ray's `--ray-worker-count`) + history server + dedicated `spark-connect`
  gRPC sidecar (runs `start-connect-server.sh` against the master — the
  upstream-supported path for binding Spark Connect on port 15002) +
  one-shot `spark-init` that creates the `spark-history` MinIO bucket
  using the `minio/mc` image. Web UI at `spark.localhost`, history at
  `spark-history.localhost`. Clients reach Spark Connect at
  `sc://spark-connect:15002` (backend-network only).

- **Zeppelin notebook** (`ZEPPELIN_SOURCE=disabled|container`) —
  Apache Zeppelin 0.12.0 with pre-configured Spark / SQL (JDBC to
  Supabase Postgres) / Shell / Markdown interpreters. **Hard-gated on
  `SPARK_SOURCE != disabled`** — Zeppelin without Spark refuses to
  start with an actionable error from `_generate_zeppelin_config`. Web
  UI at `zeppelin.localhost`. Starter notebook ships at
  `services/zeppelin/notebooks/spark_basics.zpln` exercising Spark +
  S3A + JDBC.

- **Apache Airflow** (`AIRFLOW_SOURCE=disabled|container`) — Apache
  Airflow 3.2.2 (LocalExecutor), 4-container family: `airflow-webserver`
  (api-server: UI + REST API), `airflow-scheduler`, `airflow-dag-processor`
  (REQUIRED standalone service in Airflow 3.x — the scheduler no longer
  parses DAG files in-process; without it, no DAGs ever load), and the
  one-shot `airflow-init`. Wired with `apache-airflow-providers-openai`
  for LiteLLM integration. Bundled providers: apache-spark, amazon
  (MinIO via custom endpoint), postgres, redis, common-sql, weaviate,
  neo4j, openai, fab. (LangChain chains run via PythonOperator +
  langchain-openai; there is no published apache-airflow-providers-
  langchain package.) Metadata DB
  lives in a new `airflow` database on Supabase Postgres (created
  idempotently by `airflow-init`). 7 Airflow Connections seeded —
  3 unconditional (`postgres_supabase`, `litellm_default`,
  `redis_default` — all 3 sibling services are always-on or locked
  source) and 4 gated on the matching sibling source: `spark_default`
  (`SPARK_SOURCE=container`), `minio_default` (`MINIO_SOURCE=container`),
  `weaviate_default` (`WEAVIATE_SOURCE=container`), `neo4j_default`
  (`NEO4J_GRAPH_DB_SOURCE=container`). Sample
  `example_etl_with_llm` DAG ships in `services/airflow/dags/`. Web UI
  + REST API at `airflow.localhost`. Hermes → Airflow integration via
  the REST API is documented in the per-service README §6.

**Cross-stack integration coverage** (per the spec's integration matrix):

- Spark: MinIO (s3a), Supabase Postgres (JDBC), Kong (preserve_host on
  both Web UI + History UI).
- Zeppelin: Spark (interpreter), MinIO (via Spark), Supabase Postgres
  (JDBC), Kong.
- Airflow: Supabase Postgres (metadata + user conn), Spark (sample DAG
  uses `PythonOperator` + Spark Connect at `sc://spark-connect:15002`;
  `SparkSubmitOperator` available via the bundled provider for user DAGs),
  MinIO (S3Hook), LiteLLM (LangChain/OpenAI operators), Redis (RedisHook),
  Weaviate, Neo4j. Hermes → Airflow REST trigger pattern documented.

**Wizard additions**: 3 new source steps in the appropriate category
bands (data / apps / agents). Spark's source step carries a
`SecondaryNumberInput` widget for `SPARK_WORKER_COUNT` (1-8) mirroring
Ray's worker-count widget. New CLI flags: `--spark-source`,
`--spark-workers N`, `--zeppelin-source`, `--airflow-source`.

**Defaults**: all three services default to `disabled` matching the
heavyweight-services convention (Ray, Prometheus, Grafana). Opt in via
wizard or CLI flag. Estimated memory footprint with all three enabled:
~7-9 GB additional RAM.

**4 new bootstrapper-generated secrets** for Airflow:
`AIRFLOW_FERNET_KEY` (Connection-password encryption),
`AIRFLOW_SECRET_KEY` (Airflow 3.x `AIRFLOW__API__SECRET_KEY` — inter-process payload signing), `AIRFLOW_ADMIN_PASSWORD`,
`AIRFLOW_DB_PASSWORD`. All `force=False` in `generate_missing_keys()`
because rotating any of them mid-run breaks something.

**Known follow-ups (deferred from this PR):**

- **Spark × Prometheus + Grafana** — spec §5.1 marked this CRITICAL-opt-in
  (JMX exporter sidecar + scrape job + a starter `spark.json` Grafana
  dashboard) but the wiring did not ship in this PR. cAdvisor's
  container-level metrics cover the gap in the existing dashboards until
  the JMX integration lands. Tracked separately. See
  [`services/spark/README.md`](https://github.com/thekaveh/atlas/blob/main/services/spark/README.md) §4.
- **Spark × Supabase Postgres JDBC pre-wiring** — spec §5.1 listed
  `spark.jdbc.postgres.url` env-var pre-config on the master as CRITICAL
  (config only). Users wire JDBC manually today via `--jars postgresql.jar`
  + a `jdbc:postgresql://supabase-db:5432/...` URL per job. See
  [`services/spark/README.md`](https://github.com/thekaveh/atlas/blob/main/services/spark/README.md) §4.
- **Zeppelin JDBC interpreter auto-binding** — the `ZEPPELIN_JDBC_POSTGRES_*`
  env vars are injected but Zeppelin doesn't auto-bind them to a JDBC
  interpreter profile. Users do a one-time UI setup
  (Interpreter → JDBC → `+ Create` → `postgres` group). See
  [`services/zeppelin/README.md`](https://github.com/thekaveh/atlas/blob/main/services/zeppelin/README.md) §4.
- **Airflow `postgres_supabase` Connection uses admin credentials** —
  intentionally seeds with `SUPABASE_DB_USER` / `SUPABASE_DB_PASSWORD`
  (superuser) until the prerequisite `SUPABASE_DB_APP_USER` Postgres
  role is actually created by `supabase-db-init` (it's declared in
  `.env.example` but the create-role script is missing). Least-privilege
  migration tracked separately. User DAGs that need fine-grained access
  should create their own Connection objects.
- **Airflow × Prometheus + Grafana** — Airflow 3.x has no built-in
  `/metrics` endpoint; the canonical path is StatsD → statsd_exporter
  → Prometheus. The PR ships none of the three (no statsd_exporter
  sidecar, no `AIRFLOW__METRICS__STATSD_*` env vars on
  webserver/scheduler/dag-processor, no scrape job in
  `services/prometheus/config/prometheus.yml`). `airflow`'s
  `depends_on.optional` was scrubbed of the dead-promise `prometheus`
  entry to avoid auto-generated diagrams showing an edge that doesn't
  exist.
### Added — Scala sample notebook + VS Code remote-Jupyter verification flow

Three follow-ups to the original Scala-kernels + VS Code wiring (PR #30):

- **`services/jupyterhub/build/notebooks/08_scala_basics.ipynb`** — a new
  Scala 3 sample notebook (10 cells) showing basic syntax, `import $ivy`
  dependency loading via Almond, a `java.net.http.HttpClient` call into
  the LiteLLM gateway, and Scala-3-only features (enums + extension
  methods). Demonstrates that the kernels actually work end-to-end and
  gives users a template to crib from.
- **JupyterHub README §11 verification steps** — added a `jupyter
  kernelspec list` smoke-test, an explicit rebuild recipe
  (`docker compose up jupyterhub --build --no-deps -d`) for users whose
  running container predates the Almond layer, and a one-liner
  `jupyter run --kernel=scala3` smoke-test that confirms the kernel is
  actually reachable without opening JupyterLab.
- **JupyterHub README §10.5 / §10.6 troubleshooting + screenshots
  scaffolding** — added three new troubleshooting entries (Scala
  kernels missing from picker → rebuild; no kernels listed → token
  suffix missing; output in wrong notebook → restart kernel),
  a new §10.6 listing four reference screenshots
  (`services/jupyterhub/docs/screenshots/{01..04}-vscode-*.png`), and a
  §10.7 with capture instructions for first-time setup. Screenshots
  directory ships with a `README.md` explaining the layout but no PNGs
  yet — users capture them on their own machines per §10.7.

### Assessed — OmniVoice TTS engine (skipped pending upstream readiness)

`docs/research/candidates/omnivoice.md` (new) records a feasibility
assessment of [omnivoice.app](https://omnivoice.app) +
[k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice) as a potential
fifth TTS engine alongside Speaches's Kokoro+Piper and Chatterbox. The
hosted SaaS has no public developer API; the OSS reference
implementation is CLI/Python only with no FastAPI wrapper or published
Docker image and would make this repo the upstream wrapper maintainer
against a fast-moving 0.1.x library. The only genuine differentiator
over Chatterbox is OmniVoice's **600+ language coverage** (vs Kokoro 8,
Piper 30+, Chatterbox 23). Recorded as deferred under
`services/tts-provider/README.md` §9.5 with three concrete re-evaluate
triggers (SaaS API published / community wrapper appears / Speaches
adds OmniVoice as a backend).

### Fixed — Drop unreachable JupyterHub + Hermes Prometheus scrape jobs

`config/prometheus.yml` shipped scrape jobs targeting `jupyterhub:8000`
and `hermes:8000`. Both were broken:

- **JupyterHub** — the container EXPOSEs `8888`, not `8000`. Even with
  the right port, the image we ship is single-user `jupyter/datascience-notebook`
  (not real multi-user JupyterHub), which has no built-in `/metrics`
  endpoint. The historical `/hub/metrics` path only works on real
  multi-user JupyterHub.
- **Hermes** — listens on `8642` (API) / `9119` (dashboard), not 8000.
  Also a third-party `nousresearch/hermes-agent` image with no `/metrics`
  endpoint; instrumenting it would require forking upstream.

Removed both scrape jobs from `config/prometheus.yml` with inline
comments documenting why they're deferred. Removed the three JupyterHub
panels (active users, running servers, spawn-duration p95) from the
`app-tier` Grafana dashboard since they could never have data; retitled
the dashboard to "App tier (Weaviate + MinIO)" and dropped the
`jupyterhub` tag. Updated `services/prometheus/README.md` §4 (14 → 12
targets, with a Deferred note) and the top-level README §3.4 narrative.

JupyterHub metrics return when the multi-user spec ships; Hermes
metrics return when upstream instrumentation lands.

### Fixed — Wrong access ports in top-level README

Five URLs in `README.md`'s Quick Start access block and §4.1 Service
Overview table quoted the wrong host port — readers would 404 or hit a
sibling service. `.env.example` is the canonical source for all five:

- Supabase Studio: `localhost:63016` → `localhost:63017`
  (63016 is `SUPABASE_REALTIME_PORT`)
- MinIO Console: `localhost:63018` → `localhost:63019`
  (63018 is `MINIO_PORT`, the S3 API)
- Neo4j Browser: `localhost:63020` → `localhost:63021`
  (63020 is `GRAPH_DB_PORT`, the bolt protocol port)
- MinIO Console narrative also referenced the S3 API as `:63017`;
  corrected to `:63018`.

Each wrong value appeared in BOTH the Quick Start block and the §4.1
table; this commit aligns both with `.env.example`'s pins.

### Fixed — Observability follow-ups: cAdvisor socket, Grafana provisioning + 11.4 bump

Four startup-noise / functional cleanups against the observability bundle
(PR #29), surfaced once the bind-mount fix from PR #31 let the stack
actually launch:

- **cAdvisor lost Docker-socket access on Docker Desktop.** The compose
  fragment mounted `/var/run:/var/run:ro` (whole directory). On Docker
  Desktop, `/var/run/docker.sock` on the host is a *symlink* to
  `/Users/<you>/.docker/run/docker.sock`; the symlink survives the bind
  but its target isn't reachable inside the container, so cAdvisor
  logged `Cannot connect to the Docker daemon at unix:///var/run/docker.sock`
  and silently dropped all per-container metrics. Replaced the whole-
  directory mount with the canonical
  `/var/run/docker.sock:/var/run/docker.sock:ro` — Docker Desktop's
  daemon resolves the symlink at mount time, and the same form is
  portable to Linux Docker where the path is the real socket.

- **Grafana `provisioning/plugins/` directory was missing.** Grafana
  scans all four standard provisioning subdirs (`datasources/`,
  `dashboards/`, `alerting/`, `plugins/`) at startup and errors loudly
  on any that are absent. Added an empty `plugins/.gitkeep` so the dir
  exists in git.

- **Grafana `provisioning/alerting/.gitkeep` produced a warn every
  startup.** Grafana enumerates files with `.yaml` / `.yml` / `.json`
  suffixes in each provisioning dir and warns about anything else.
  Replaced `.gitkeep` with `placeholder.yml` containing the minimal
  `apiVersion: 1` stub so the dir stays non-empty without tripping the
  scanner. Real alert provisioning can later replace the placeholder.

- **Grafana bumped 11.3.0 → 11.4.3.** 11.3.x has a known bug where
  `autoMigrateXYChartPanel` (a feature flag Grafana itself enables by
  default) collides with the bundled `xychart` core panel — logs every
  startup as `Could not register plugin pluginId=xychart error="plugin
  xychart is already registered"`. Upstream fixed it in
  [grafana/grafana#93540](https://github.com/grafana/grafana/pull/93540),
  shipping in 11.4. Bump the default in `services/grafana/service.yml`
  (and the rendered `.env.example`) to the latest 11.4 patch.

### Fixed — Prometheus + Grafana bind-mount paths produced doubled sources

`services/prometheus/compose.yml` and `services/grafana/compose.yml` (both
added by the PR #29 observability bundle) declared their config bind-mount
sources as `./services/<svc>/config/...` — written as if Compose would
resolve them from the repo root. Compose v2's `include:` directive
resolves relative paths in an included fragment from the **fragment's own
directory**, so the actual resolved path was
`services/<svc>/services/<svc>/config/...` (the path doubled). On the
first launch Docker auto-created the missing source as a directory; the
second launch then failed with `not a directory` because the mount
target expects a file.

Rewrote the four affected volume entries (two in each fragment) to
`./config/...`, removed the stray `services/prometheus/services/` tree
that Docker had auto-created, and regenerated the byte-equivalence
baseline in `bootstrapper/tests/fixtures/rendered_config_baseline.yml`.

**Prevention:** added `bootstrapper/tests/test_fragment_bind_sources.py`
— a static check that walks every `services/*/compose.yml`, resolves
each relative bind-mount source against the fragment's directory, and
fails if the resolved path contains the literal doubled marker
`services/<X>/services/<X>/` (where `<X>` is the fragment's own folder
name). This is the exact PR #29 regression class and the structural
pattern can never be correct, so the check has zero false-positive
surface against fragments that legitimately mount runtime-generated
paths (litellm config, neo4j/supabase snapshot dirs, kong dynamic
config). Runs in the existing "Manifest lint + unit tests" CI job with
no Docker daemon. Verified to fail on the buggy form during development
(prometheus fragment temporarily reverted) and emits an actionable
error naming the fragment, offending raw source, and resolved path.

### Added — Scala kernels in JupyterHub + VS Code remote-Jupyter wiring

The JupyterHub container now ships three kernels and is configured for
remote-kernel access from VS Code on the developer's host machine.

**Scala kernels (Almond):**
- Two new kernels installed at image build time via Coursier — `scala213`
  (Scala 2.13.16) and `scala3` (Scala 3.4.3), both running on Almond
  0.14.5 over OpenJDK 17. Pick from JupyterLab's launcher or VS Code's
  kernel-picker. Toolchain footprint ≈ 600 MB; drop the relevant
  Dockerfile blocks if you don't need Scala.
- Pinned via `ALMOND_VERSION` / `ALMOND_SCALA_2_VERSION` /
  `ALMOND_SCALA_3_VERSION` Dockerfile build args so future bumps are
  explicit and rebuild predictably.

**VS Code remote-Jupyter:**
- `services/jupyterhub/compose.yml` adds three `--ServerApp.*` flags to
  the container command — `allow_origin=*`, `allow_remote_access=True`,
  `disable_check_xsrf=False`. Token auth still gates every request; the
  origin allowlist can be tightened via the new `JUPYTER_ALLOW_ORIGIN`
  env var.
- Full operator walkthrough lives at
  `services/jupyterhub/README.md` § 10 (Connecting from VS Code). The
  flow: install Microsoft's Jupyter extension, copy `JUPYTERHUB_TOKEN`
  from `.env`, paste `http://localhost:63081/?token=<TOKEN>` into the
  "Existing Jupyter Server" prompt. VS Code then offers the new
  kernels via its kernel-picker.

### Changed — JupyterHub `requirements.txt`

- **Removed `nnx-pytorch`** from the ml-lab support block. The 28-of-29
  ml-lab notebooks that `import nnx` will not run until the package is
  restored. Supporting libraries (`python-louvain`, `nltk`, `spacy`,
  `torchao`, `prettytable`) stay so non-nnx notebooks keep working.

### Added — observability bundle (Prometheus + Grafana)

New paired bundle in the `infra` band giving full-stack metrics observability
out of the box. Both services default to `disabled` — opt in with
`--prometheus-source container --grafana-source container` or the wizard.

**New services:**
- **`services/prometheus/`** — metrics scraper + TSDB with bundled `node-exporter`
  (host metrics) and `cAdvisor` (container metrics) as co-lifecycled containers.
  Default retention: 7 days, user-configurable at wizard time via the new inline
  `secondary_number` row schema. Static scrape config shipped with 14 targets
  initially; later trimmed to 12 (see the JupyterHub + Hermes Fixed entry above).
- **`services/grafana/`** — observability UI + unified alerting. Pre-provisions
  the Prometheus datasource and 7 starter dashboards: Stack Overview, LiteLLM
  (per-model tokens / spend / latency), Kong (per-route req rate / latency /
  bandwidth), Postgres + Redis, Containers + Host, n8n (workflow executions),
  and App tier (Weaviate + MinIO; JupyterHub panels dropped in a follow-up
  alongside the unreachable scrape jobs). Admin password
  (`GRAFANA_ADMIN_PASSWORD`) auto-generated on first run via
  `generate_grafana_admin_password()` — same posture as LiteLLM's master key.

**Sidecar exporters** (embedded in existing manifest families, scale 1↔0 with
`PROMETHEUS_SOURCE`):
- `postgres-exporter` (in `services/supabase/`) — reads `pg_stat_*` views,
  auto-discovers every Supabase database.
- `redis-exporter` (in `services/redis/`) — Redis memory, ops/sec, hit ratio.

**Cross-stack `/metrics` enablement** (always on; sit unscraped when
`PROMETHEUS_SOURCE=disabled`):
- **Kong** — global Prometheus plugin via `kong_config_generator`, Status API
  on `:8100` (internal-only), `KONG_STATUS_LISTEN=0.0.0.0:8100`.
- **LiteLLM** — `'prometheus'` added to `litellm_settings.callbacks` (shared
  via `bootstrapper/utils/litellm_settings.py` so both the host stub and the
  init-script render). `PROMETHEUS_MULTIPROC_DIR=/tmp/litellm_metrics` + tmpfs
  required for the multi-worker (4 uvicorn) layout.
- **Weaviate** — `PROMETHEUS_MONITORING_ENABLED=true`; metrics on port 2112
  (internal-only `expose`).
- **n8n** — `N8N_METRICS=true` + prefix / workflow-id labels.
- **MinIO** — `MINIO_PROMETHEUS_AUTH_TYPE=public` (no JWT needed).
- **Backend** — `prometheus-fastapi-instrumentator>=7.0.0` middleware; emits
  standard `http_request_duration_seconds` / `http_requests_total` series.
- **JupyterHub** — originally shipped expecting built-in `/hub/metrics`, but the
  image is single-user `jupyter/datascience-notebook` (not multi-user JupyterHub)
  and has no built-in metrics surface; the scrape job was dropped in the
  follow-up Fixed entry above. Returns when the multi-user spec ships.

**Deliberate exclusions:** Ollama (LiteLLM gateway already emits per-call
request/token/cost — direct scraping would duplicate); Neo4j Community
(metrics are Enterprise-only); ComfyUI, SearXNG, OpenClaw (no native
`/metrics` today). cAdvisor covers container-level resources for all of these.
Hermes is a third-party container without a `/metrics` endpoint; its scrape
job was dropped in the follow-up Fixed entry above and returns when upstream
instrumentation lands.

**Bootstrapper plumbing:**
- `PROMETHEUS_SOURCE` / `GRAFANA_SOURCE` CLI flags + `source_mapping` entries.
- `--prometheus-retention-days` CLI flag (default 7; wizard prompts via the
  new `secondary_number` row-schema field).
- `_generate_prometheus_config()` — cross-manifest scale arithmetic hook that
  writes `PROMETHEUS_SCALE`, `NODE_EXPORTER_SCALE`, `CADVISOR_SCALE`,
  `POSTGRES_EXPORTER_SCALE`, and `REDIS_EXPORTER_SCALE` from a single SOURCE
  value (matches the `_generate_stt_provider_config` pattern).
- `_generate_grafana_config()` — scale + endpoint resolution.
- `generate_prometheus_service()` / `generate_grafana_service()` in the Kong
  route generator. Both routes use `preserve_host: True` (Grafana is an SPA
  that builds redirects from the Host header).
- TUI `_TAG_BY_KEY` entries for `prometheus`, `node-exporter`, `cadvisor`,
  `grafana`, `postgres-exporter`, `redis-exporter`.
- `services.schema.json` extended with optional `secondary_number` on rows.

**Audit + tests:**
- `check-compose-source-deps.py::REQUIRED_DEPENDS_ON` adds
  `(postgres-exporter, supabase-db)`, `(redis-exporter, redis)`,
  `(grafana, prometheus)`.
- `test_wizard_app_discovery::EXPECTED_DISCOVERED` adds `Prometheus` and
  `Grafana`; the `source_mapping` flag assertion adds the matching CLI keys.
- `test_deps_resolver::test_kong_fronted_services_in_upstream` updated —
  Kong's downstream is now `{prometheus}` because Prom scrapes Kong's Status
  API.
- `rendered_config_baseline.yml` regenerated for the new compose shape
  (~825 lines `.env.example`; +275 lines baseline).
- Per-service docs (READMEs + architecture diagrams) regenerated via
  `bootstrapper.docs.regen --all`.

### Removed (breaking) — `external` source variants stack-wide

Source variants `external` (ComfyUI), `ollama-external` (Ollama), and
`ray-external` (Ray) and their associated env vars `COMFYUI_EXTERNAL_URL`,
`LLM_PROVIDER_EXTERNAL_URL`, and `RAY_EXTERNAL_ADDRESS` are removed pending
a stack-wide authenticated-remote design. Each `external` variant today is
just a URL with no associated authentication design (API keys, bearer
tokens, mTLS); shipping more `external` slots in new manifests would
compound that gap. A future spec will reintroduce authenticated remote
endpoints across the stack with a coherent auth model.

**User-side migration.** Users with `RAY_SOURCE=ray-external`,
`COMFYUI_SOURCE=external`, or `LLM_PROVIDER_SOURCE=ollama-external` in
their `.env` must switch to `container` (or `disabled`, or `none` for
the LLM provider). On bootstrap, `start.py` now detects these legacy
values, prints a pointer to this entry, and exits with status 2 — no
silent fallback to a different source.

**Plumbing impact.** Removed: `--ray-external-address` CLI flag,
`COMFYUI_EXTERNAL_URL`/`LLM_PROVIDER_EXTERNAL_URL`/`RAY_EXTERNAL_ADDRESS`
env vars, the `ray-external` branch in `_generate_ray_config`,
`external`-related code paths in `kong_config_generator.generate_comfyui_service`,
the `RAY_EXTERNAL_ADDRESS_TITLE` wizard step, and the `external`-flavored
test fixtures in `tests/conftest.py`. `services/litellm/catalog-init`'s
host-side auto-import path now applies only to `ollama-localhost`.

### ComfyUI model picker — localhost/external coverage (follow-up to PR #17)

The "ComfyUI · models" wizard step previously only fired for
`container-cpu` / `container-gpu`, contradicting the Ollama-mirror
design that was the goal of PR #17. The fix lets the step show for
every non-`disabled` source (container-cpu / container-gpu / localhost
/ external) — exactly matching how Ollama's picker shows for any
`ollama-*` source. For `localhost` / `external`, `comfyui-init`
(the wget container) now scales to 0 so the picker's selection is
DB-only — the user populates their host ComfyUI install's models
directory themselves, same as `ollama pull <name>` for Ollama
localhost. `comfyui-catalog-init` still scales to 1 for all
non-disabled sources so `public.comfyui_models` (the table the
backend `/comfyui/db/models` endpoint reads, consumed by Open WebUI +
n8n) gets the active set populated regardless of where ComfyUI is
actually running. Six parametrized regression tests pin the new
predicate.

### ComfyUI model picker

Added a new wizard step ("ComfyUI · models") that lets users pick
from a curated catalog of popular models across Image, Image-edit,
Video, Audio, and 3D categories, sourced live from Hugging Face +
civitai with a bundled fallback for offline. The wizard UI mirrors
the Ollama picker: filter chips (`f`), name search (`/` or `Tab`),
space-to-toggle, enter-to-confirm, and green `[pulled]` badges for
models already on disk. Selection persists as `COMFYUI_USER_MODELS`
(comma-separated catalog names) in `.env`; CLI flag
`--comfyui-models` accepts the same CSV. A
`--comfyui-custom-models-file` flag (default
`services/comfyui/custom-models.yaml`) allows sidecar YAML additions
that surface with a `[Custom]` family badge and are ingested into the
DB on the next start.

The init pipeline was rewritten to mirror the Ollama pattern. A new
`comfyui-catalog-init` container UPSERTs the curated allowlist +
sidecar YAML into `public.comfyui_models` on every `docker compose up`
and flips `active = true` for names in `COMFYUI_USER_MODELS` /
`custom-models.yaml`. `comfyui-init` now queries
`SELECT … FROM public.comfyui_models WHERE active = true` via psql and
downloads each active model via wget (with optional SHA256
verification), replacing the previous `COMFYUI_MODEL_SET`-based
bucket-selector + wget-by-set logic. The `public.comfyui_models` schema
was extended additively (`family`, `target_dir`, `sha256`,
`min_vram_gb`, `cpu_supported`, `requires_custom_node`, `popularity`,
`source` — all `ADD COLUMN IF NOT EXISTS`); backend
`/comfyui/db/models` routes continue to work. (The `notes` field on
sidecar entries is a wizard-side display field, surfaced in the wizard
subtitle from `custom-models.yaml`; not persisted to the DB.) Migration script at
`services/supabase/db/scripts/12-extend-comfyui-models.sql`.

`COMFYUI_MODEL_SET` is retired. The bootstrapper's migration v3
auto-translates existing values on first run (`minimal`/`sd15` →
SD 1.5 + VAE; `sdxl` → SDXL base + VAE; `full` → all four), takes a
`.env.backup.<timestamp>` before any rewrite, and bumps
`BOOTSTRAPPER_PORT_LAYOUT_VERSION` to 3. The four hardcoded ComfyUI
model rows previously in `services/supabase/db/scripts/08-seed-data.sql`
are removed; those models now arrive via `comfyui-catalog-init`'s
curated allowlist.

**Wizard selection persistence.** `wizard_screen.py`'s "Apply user
model selections" step now unpacks `comfyui_user_models` from
`stack_options` alongside `cloud_user_models` / `ollama_user_models` —
before this fix the dict-merge silently dropped wizard-driven ComfyUI
selections on confirm. A new seam-parity test
(`test_wizard_screen_consumes_comfyui_user_models`) guards against
this regression class.

**Known follow-ups:**

- *Custom-node auto-install.* Required nodes are surfaced as `⚠ <node>`
  warning badges only; users install manually. A future ticket will
  integrate ComfyUI-Manager's `cm-cli` for one-click install.
- *Disk pre-flight hard block.* The status header turns yellow/red on
  projected fill but does not block confirm. A future ticket will gate
  the wizard on `df` checks.

**Architecture note (not a follow-up — this PR is what closed it):** Both
pickers now share the same `public.{llms,comfyui_models}` +
`*-catalog-init` + DB-backed pull architecture. Custom-model surface
differs (Ollama: CSV in `.env`; ComfyUI: sidecar YAML) because ComfyUI
lacks an upstream registry that resolves models by name. The earlier
"pipeline divergence" concern is resolved.

### 2026-05-28 third-pass audit (follow-up to PR #12 / #13)

A third convergence audit ran the night PR #13 merged. 16 verification
iterations dispatched 3 parallel-domain audit subagents on iter-1 +
single-agent narrow probes on subsequent iters, surfacing ~32 new
findings on top of the ~280 from PR #11 and ~80 from PR #12. The fix
pass landed in this PR; the residual deferrals are unchanged from the
PR #12 Known-follow-ups block beneath this entry.

Highlights:

- **Correctness:** `dependency_manager` now exposes both
  `_SCALE_VAR_MAPPING` and `_SOURCE_VAR_MAPPING` as class-level
  constants (read + write paths read from the same source — a latent
  hermes / openclaw-gateway auto-resolve gap is closed); env-file
  rewriters in `source_override_manager.py` + `service_config.py`
  switched to a `lambda _m, r=replacement: r` form so `re.sub` no
  longer interprets `\1` / `\g<name>` in the replacement (silent
  corruption if an env value ever contained a literal backslash);
  `update_memory`'s embedding-update branch stopped opening a
  redundant second asyncpg connection. Four more init scripts had the
  PR #12-class `set -e` + `var=\$(psql/grep …)` anti-pattern
  (`init-weaviate.sh` × 2 sites, `ollama-pull/pull.sh`,
  `comfyui-init/download_models.sh`,
  `local-deep-researcher/docker-entrypoint.sh`) — fallback branches
  unreachable; each gets the canonical `|| var=""` suffix.

- **Brand customization parity:** `services/globals/service.yml` now
  declares `BRAND_AUTHOR_EMAIL` (was consumed at the call site but
  missing from the manifest) and corrects `BRAND_LICENSE` from `MIT`
  to `Apache License 2.0`. `bootstrapper/ui/state.py::AppState` aligned
  4 stale brand-field fallbacks (`brand_name` / `tagline` / `version`
  / `repo_url`) with the manifest defaults so a user blanking a
  `BRAND_*` value hits the same string the manifest ships. A new
  drift-gate test (`test_appstate_brand_defaults_match_globals_manifest`)
  catches future drift between the two layers at CI time.

- **Dead deps + dead code:** dropped `dspy>=2.4.6` + `dspy-ai>=2.4.6`
  + `aioredis>=2.0.1` from `services/backend/app/app/requirements.txt`
  (none imported anywhere); cleared 5 unused imports across
  `main.py` / `research_service.py` / `deps_section_writer.py` /
  `regen.py` / `research_subagent_prompt.py`; updated one stale
  code-reference comment in `generate_readme_topology.py` (pointed
  at the retired `generate_architecture_diagram.py`).

- **Documentation hygiene:** main `README.md` + `docs/diagrams/architecture.html`
  alt text harmonized so both surfaces describe the SVG identically;
  `docs/CHANGELOG.md` `[Unreleased]` Known-follow-ups preamble bumped
  from "four classes" to "three classes" (architecture-diagram skill
  rewrite closed by PR #13); 8 stale `docs/scripts/check-…` references
  in descriptive bullets flipped to `scripts/check-…`; `docs/README.md`
  gained Contributors / Architecture-diagrams / Cross-service-research
  sub-sections so the docs hub mirrors the project-root README §9 hub;
  `docs/quick-start/interactive-setup-wizard.md` BRAND example block
  synced to match `.env.example` (was missing `BRAND_AUTHOR_EMAIL` and
  diverging from the canonical defaults on 4 other lines);
  `services/tts-provider/provider/localhost/README.md` override
  example port `63041` (collided with COMFYUI_PORT) replaced with
  `9000`.

- **Audit-script hygiene:** `scripts/check-compose-source-deps.py` +
  `scripts/check-docs-drift.py` gained `Exit codes:` paragraphs in
  their module docstrings, matching the convention already established
  in the other three scripts. `.gitignore` `.audit/` rule deduplicated
  (the three audit-pass PRs each appended their own copy).

### Architecture diagrams — skill-driven rewrite

The top-level architecture diagram (`docs/diagrams/architecture.svg`) is now
hand-authored through the `architecture-diagram` skill — JetBrains Mono on a slate-950
background, category palette of cyan / emerald / violet / amber / rose /
orange / slate, layered topological flow from external clients down
through Kong → Apps → Agents → LLM Core → Media → Data → Ray. The
previous Graphviz pipeline (`docs/diagrams/architecture.dot` +
`bootstrapper/tools/generate_architecture_diagram.py`) is retired
alongside the Graphviz prerequisite from the contributor docs.

Per-service diagrams (`services/<name>/architecture.{svg,html}`) keep
their auto-regenerated workflow via `bootstrapper.docs.regen`, but their
renderer migrated to the same design system:
`bootstrapper/services/topology.py::CATEGORY_COLORS` now exposes the
skill palette (`#fb7185` rose / `#a78bfa` violet / `#fbbf24` amber /
`#fb923c` orange / `#34d399` emerald / `#22d3ee` cyan) and a sibling
`CATEGORY_FILLS` dict carries the matching `rgba(..., 0.3–0.4)`
semi-transparent fills the skill uses for component boxes.
`bootstrapper/docs/diagram_renderer.py` now stamps `font-family` on the
root SVG, paints a `#020617` background before the grid, and renders
both pills and the focus box with the two-rect (opaque backdrop +
themed fill) pattern.

All 21 per-service SVG + HTML files were regenerated against the new
renderer; the hermes golden snapshot under
`bootstrapper/tests/fixtures/hermes.architecture.svg` was refreshed.

This closes the `Architecture-diagram skill rewrite` item that was
deferred in the 2026-05-27 audit's `Known follow-ups` block.

### 2026-05-27 overnight audit (second pass — follow-up to PR #11)

A second convergence audit ran the night PR #11 merged. 14 verification
iterations dispatched ~14 parallel domain audits and surfaced ~80
genuine findings on top of the ~280 from PR #11. The fix pass landed
in this PR; the residual ~16 follow-up findings (smaller-scope
ergonomic / refactor / wizard-info items that risked behaviour
changes outside this PR's scope) are recorded in the Known
follow-ups block beneath this entry.

Highlights of what landed:

- **Correctness:** asyncpg JSONB strings now decoded in
  `research_service.get_research_result` / `get_research_logs` (used
  to 500 on real data — fixed via the same `json.loads-if-str`
  pattern memory_service already had). The two duplicate
  `execute_workflow` FastAPI handlers in `backend/app/main.py` were
  shadowing each other at module scope; renamed to `execute_n8n_workflow`
  / `execute_comfyui_workflow`. Sync Ray-SDK calls in the async
  `/api/ray/*` handlers were blocking the event loop; wrapped via
  `asyncio.to_thread`. UUID validation added to the four
  `/research/{session_id}/*` endpoints. Init-script `set -e` was
  silently aborting on the first failed `curl` in `ollama-pull` and
  `n8n-init` (the explicit "continue on failure" branches were
  unreachable); wrapped each curl with `|| curl_exit_code=$?`.

- **Data-flow.calls schema alignment:** `services/ollama/service.yml`
  and `services/neo4j/service.yml` carried init-time bootstrap edges
  (supabase + litellm) in `data_flow.calls`, contradicting the
  schema's "runtime in the request path; init-time excluded"
  description. Dropped them. `services/open-webui/service.yml` was
  understating its surface area; added `comfyui`, `stt-provider`,
  `tts-provider`, `doc-processor`, `local-deep-researcher`, `weaviate`
  to match what `runtime_deps.optional` already listed.

- **Stale port literals + fallbacks:** TTS/STT aggregator READMEs and
  the deeper provider/* sub-READMEs cited 63022/63023/63026/63027
  defaults; the post-port-layout-v1 values are 63042/63044/63046/63045.
  Compose-fragment `${X_PORT:-NNNNN}` fallbacks had drifted in
  speaches, parakeet, parakeet/mlx api_server, chatterbox, hermes, and
  openclaw. The JupyterHub welcome README hardcoded `localhost:63009`
  for Supabase Studio (now 63016).

- **Documentation hub:** README.md §9 was rebuilt as a four-tier index
  (First-time users / Operators / Contributors / Release history) so
  SECURITY.md, CONTRIBUTING-services.md, the deployment/* + quick-start/*
  guides, and the research integration matrix are discoverable from the
  main README. `docs/README.md` gained the missing Ray service entry.
  README §2.1 stopped framing Ray as "always-on" (it defaults to
  `RAY_SOURCE=disabled`).

- **Naming normalization:** 20+ sites across 10 files normalized from
  `OpenWebUI` / `Open-WebUI` to canonical `Open WebUI` (HTTP `User-Agent`
  values left as-is). 5 `litellm-init/init.py` file-path comments
  updated to the post-modularization path; the `_LITELLM_INIT_SENTINEL`
  string was left intact for upgrade-detection compatibility.

- **Audit-script + CI hardening:** `scripts/check-compose-source-deps.py`
  now falls back to `.env.example` when `.env` is absent and exits 2
  with stderr surface when `docker compose config` fails (previously
  silently produced wrong-answer output). `scripts/check_doc_links.py`
  now scans `services/<name>/README.md` by default (the primary doc
  location since the 2026-05-22 retirement of `docs/services/`).
  `.github/workflows/services-lint.yml` `pull_request.paths:` now
  includes root-level README.md / SECURITY.md / start.sh / stop.sh /
  .gitignore.

- **CHANGELOG hygiene:** Reordered so `[Unreleased]` sits above
  `[3.0.0]` per Keep-a-Changelog convention. Corrected the
  "engine READMEs removed" claim (they exist as pointer stubs);
  the god-class-refactor figures (LOC + method counts) were stale.
  Pointed retired remediation reports' history-only location explicitly
  via `git show <SHA>:docs/security/<file>` commands in SECURITY.md.

### Known follow-ups (deferred from the 2026-05-27 repo-wide audit pass)

The cleanup PR documented at the top of this section deliberately defers three classes of work — each large enough to deserve its own plan rather than a drive-by fix:

- **Backend test coverage.** `services/backend/app/app/` has ~3,700 LOC of production Python across `main.py` (33 FastAPI endpoints), `memory_service.py`, `research_service.py`, `comfyui_client.py`, `n8n_client.py`, `memory_store.py`, `research_client.py`. Only the `ray_routes` / `ray_client` surfaces have tests. Smoke-level TestClient suites for the memory / research / comfyui / workflow endpoint families are tracked for a follow-up.
- **Bootstrapper utility test gaps.** `bootstrapper/utils/{localhost_validator,key_generator,llm_catalog,cloud_models,supabase_keys}.py`, `bootstrapper/core/docker_manager.py`, and `bootstrapper/services/source_validator.py` have zero unit tests. The drift gates + integration tests cover them transitively, but no isolated unit coverage exists. Adding targeted tests is tracked separately.
- **Bootstrapper god-class refactors.** `bootstrapper/start.py::GenAIStackStarter` (~1,800 LOC, 31 methods), the 14 near-identical `_generate_<svc>_config` methods in `bootstrapper/services/service_config.py`, and the 10 `generate_<svc>_service` methods in `bootstrapper/utils/kong_config_generator.py` are flagged for table-driven consolidation in a separate refactor plan. The current code paths are all tested and correct; these are maintenance-debt items, not bugs.

### Added — Ray distributed-compute cluster

- New `services/ray/` family with head + worker containers, dashboard at `ray.localhost`, RAY_SOURCE source-variant pattern.
- Wizard wires Ray worker count inline via the SecondaryNumberInput widget on the source step.
- Backend `/api/ray/*` endpoints (submit/status/stop/cluster-status) gated on RAY_ADDRESS — return 503 when Ray is disabled.
- JupyterHub picks up `ray[client]` dep + seeded `07_ray_cluster.ipynb` notebook.
- Hermes Agent + Backend agents can dispatch compute jobs to the cluster (future integration; Ray exposes only via Backend REST today).

### Changed — Localhost port override (URL → PORT migration)

- Replaced the 7 per-service `<SVC>_LOCALHOST_URL` env vars with `<SVC>_LOCALHOST_PORT` integer vars; the URL is derived at compose-render time as `http://host.docker.internal:${<SVC>_LOCALHOST_PORT:-<default>}`.
- 3 newly-overridable services (Ollama, Neo4j HTTP + Bolt, Weaviate) gain dedicated LOCALHOST_PORT env vars.
- Wizard adds an inline integer textbox per localhost source row using the SecondaryNumberInput widget; the override propagates symmetrically through `.env`, runtime_sc, Kong routes, and the wizard's service-table.
- New migration `bootstrapper/services/migrations/migration_v2.py` rewrites users' existing `.env` files (gated by `BOOTSTRAPPER_PORT_LAYOUT_VERSION` 1→2).
- Pre-launch summary surfaces port collisions as warnings (warn-don't-block).

> **Path-reference note:** entries written before the per-service
> configuration-modularization change below reference top-level directory
> names (`hermes-init/`, `litellm-init/`, `llm-catalog-init/`,
> `comfyui-init/`, `n8n-init/`, `weaviate-init/`, `minio-init/`,
> `ollama-pull/`, `open-webui-init/`, `searxng/`, `stt-provider/`,
> `tts-provider/`, `doc-processor/`, `graph-db/`, `backend/`,
> `local-deep-researcher/`, `jupyterhub/`, `n8n/`, `open-webui/`,
> `supabase/`). After this refactor they live under their owning service's
> manifest folder, e.g. `services/litellm/init/scripts/init.py`,
> `services/n8n/init/`, `services/supabase/db/`. The original wording is
> preserved to keep the historical record honest; use `git log --follow`
> on the new path to trace the move.
>
> **Naming:** the doc-processing surface uses three names that all refer
> to the same thing — the retired top-level dir was `doc-processor/`,
> the post-refactor folder is `services/docling/`, the env-var selector
> is `DOC_PROCESSOR_SOURCE` (chooses between `docling-container-gpu` /
> `docling-localhost`), and the human-facing virtual-service docs live
> at `services/doc-processor/README.md`. The `doc-processor` name is the
> stable public API; `docling` is the single engine implementing it.

### Changed (Documentation consolidation — service docs live with their services, hierarchical numbering, Phase C Future content)

- **Service docs moved alongside their services.** Every per-service README, architecture SVG, and architecture HTML moved from `docs/services/<name>/` to `services/<name>/`. The `docs/services/` directory is retired entirely. Each service folder is now the single source of truth for that service: manifest (`service.yml`), compose fragment (`compose.yml`), any `init/` scaffolding, and the human-facing `README.md` + diagrams sit in one place.
- **Three doc-only folders introduced** for the aggregate doc-folders without a single-manifest owner: `services/stt-provider/`, `services/doc-processor/`, `services/multi2vec-clip/`. The manifest loader skips dirs without `service.yml` (`_is_service_dir` now requires the file), so these doc-only folders are invisible to the bootstrapper.
- **Constituent engine READMEs reduced to pointer stubs.** `services/parakeet/README.md`, `services/speaches/README.md`, `services/chatterbox/README.md`, `services/docling/README.md` previously duplicated the user-facing description from their aggregator (STT-provider / TTS-provider / doc-processor). They now each contain a single "Engine quick reference" section + a pointer link to the aggregator + the auto-regenerated Dependencies & Integrations block — kept around because each owns a `service.yml` and is in scope of the drift gate, but no longer authoritative for user-facing prose.
- **Hierarchical section numbering.** Every service README uses `## N. <Title>` for top-level sections and `### N.M <Title>` for subsections. The `## Dependencies & Integrations` section keeps its position-driven numbering — the regen tool detects whatever number the section sits at (5 in the canonical 6-section layout, but READMEs with more pre-deps content can have it at 7, 9, 14, etc.) and emits matching `### N.1` … `### N.6` subsections.
- **`bootstrapper/docs/regen.py` learned to preserve Phase C Future content.** The auto-block (`## N. Dependencies & Integrations` + `### N.1` Current Upstream + `### N.2` Current Downstream + `### N.3` Architecture diagram + `### N.4-6` Future placeholders) is regenerated from manifests on every run, BUT any user-authored content under `### N.4 Future — Missing pair integrations`, `### N.5 Future — Candidate new services`, and `### N.6 Future — Unused features in this service` is preserved across regen passes. New helper `_render_section_with_future` extracts the existing Future bodies before re-rendering and splices them back in.
- **Phase C content populated in all 21 service READMEs.** Each of the three Future-* subsections in every service doc now lists concrete bullets (pair integrations to wire, candidate new services to add, unused upstream features to pursue) sourced from the Phase B research artifacts under `docs/research/rows/<svc>.md` and `docs/research/candidates/<slug>.md`. The seven previously-thin docs (backend, comfyui, local-deep-researcher, multi2vec-clip, n8n, redis, searxng) were rewritten to Hermes-grade depth (≥150 lines each, all canonical sections present).
- **`docs/` aggressively trimmed.** Removed entirely: `docs/services/` (moved into `services/`), `docs/superpowers/` (planning-history artefacts whose value lives in git log — note: later reinstated as the active feature-track spec/plan home, see docs/README.md §1.7), `docs/security/` (completed Dependabot remediation reports — paper trail preserved in git history). Moved: `docs/scripts/*.py` → `scripts/` (these are operational scripts, not docs). What remains under `docs/`: CHANGELOG, ROADMAP, CONTRIBUTING-services, top-level README, the canonical deployment/ and quick-start/ subdirs, the stack-wide diagrams/, images/, and the Phase B research/ corpus referenced from every service doc.
- **Path rewrites.** All cross-doc references to `docs/services/<X>.md` or `docs/services/<X>/README.md` repointed to `services/<X>/README.md`. The `_AGGREGATE_DOC_FOLDERS` mapping in `bootstrapper/docs/deps_resolver.py` is unchanged — it's still the source of truth for doc-folder ↔ manifest aggregation. Service manifests' `docs:` fields updated to point at the new `services/<X>/README.md` location. Constituent engine manifests (parakeet, speaches, chatterbox, docling) point to their aggregate doc folder.
- **Migration tooling retired.** `scripts/migrate_docs_to_folders.py` (the one-shot `docs/services/<X>.md` → `docs/services/<X>/README.md` migration helper from a previous restructure) and its test `bootstrapper/tests/test_doc_migration.py` are removed — both were one-shot artefacts of completed migrations.

### Changed (Architecture diagrams — data-flow model + clustered layout)

- Architecture diagrams under `services/<name>/` now render the **data-flow** model (runtime "X calls Y" edges) instead of the bootstrap-dep model. Source of truth is a new optional `data_flow.calls` field per `services/<name>/service.yml`.
- Diagram layout redesigned: services in the upstream and downstream lanes group by category (infra / data / llm / media / agents / apps) into mini-clusters; one edge per cluster (not per pill); focus box gains a category-colored glow; legend bar + 3 summary cards below.
- Deps-section tables in each README simplified to `Service | Category` (the old Type / Mechanism / Failure mode columns no longer have data in the data-flow model).
- `depends_on.required`, `runtime_adaptive.adapts_to`, `runtime_deps.optional`, and `doc_extras.diagram.extra_consumers` remain in manifests (still used by the compose layer) but the diagram resolver no longer reads them.
- Spec: diagram-refresh design (2026-05-22) — `docs/superpowers/` was retired; see git log for the design doc and the commits around that date.

### Added (Cross-service deps + diagrams — Phase B research)

- Added 21 per-service integration-research files under `docs/research/rows/<service>.md` (missing-pair integrations, candidate new services, per-service feature gaps).
- Added 32 candidate one-pagers under `docs/research/candidates/<slug>.md`.
- Added generated master index at `docs/research/integration-matrix.md` (re-build with `python -m bootstrapper.docs.merge_research`).
- New tooling: `scripts/validate_research_schema.py` (schema validator), `bootstrapper/docs/merge_research.py` (merge + index generator), `bootstrapper/docs/research_subagent_prompt.py` (programmatic Phase B subagent prompt builder).
- Phase C (content authoring) is next — see the cross-service deps + diagrams design (2026-05-16); `docs/superpowers/` was retired, consult git log for the doc.

### Added (Cross-service deps + diagrams — Phase A foundations)
- Migrated `services/<name>.md` → `services/<name>/README.md` (per-service folders).
- Added standardized **Dependencies & Integrations** subsection to every service README, with Current (manifest-derived) tables and Future (placeholder) subsections.
- Added per-service architecture diagrams (`architecture.html` + `architecture.svg`) under each service folder, generated from manifests via `python -m bootstrapper.docs.regen`.
- Added CI drift gate (`bootstrapper/tests/test_docs_drift.py`) that fails when committed deps sections or diagrams diverge from manifest state.
- Added internal-link validator (`scripts/check_doc_links.py`) covering README, CHANGELOG, and the whole `docs/` tree.
- New optional manifest fields: `runtime_adaptive.<container>.failure_mode` (string) and `doc_extras.diagram.extra_consumers` (list of service names).
- Cross-service deps + diagrams research/authoring (Phases B & C) deferred — see the cross-service deps + diagrams design (2026-05-16); `docs/superpowers/` was retired, consult git log for the doc.

### Added (Dependency vulnerability monitoring)
- **`.github/dependabot.yml`** — weekly pip + GitHub Actions scans on every active manifest (`bootstrapper/`, `services/backend/app/`, `services/jupyterhub/build/`, `services/docling/provider/{gpu,localhost}/`, `services/parakeet/provider/{gpu,mlx}/`). Alerts grouped by ecosystem to reduce PR noise. `directories:` deliberately enumerates ALL active manifests so an omission doesn't silently drop coverage from the scan.
- **`SECURITY.md` threat model** — published threat tiers, supported versions, and the responsible-disclosure address. Aligns with the dependabot scan-coverage list.
- **Bulk-dismiss tooling** — operators triaging stale alerts on deleted/moved manifests can use the GitHub REST API with `reason=not_used`; the `docs/security/2026-05-14-dependabot-remediation-report.md` captures the playbook from the May 2026 cleanup (77 alerts triaged, 62 phantom dismissals).

### Added (LiteLLM Kong alias for the admin dashboard)
- **Kong route `litellm.localhost` → `http://litellm:4000/`** — added to `bootstrapper/utils/kong_config_generator.py::generate_litellm_service()` and wired into `get_adaptive_services()`. Always-on (LiteLLM is mandatory; no SOURCE variation, no dashboard-disable toggle). The same alias exposes `/ui/` (admin dashboard with per-model spend, key/team management, request logs), `/v1/*` (proxy API), and `/spend/*` (raw usage telemetry rollups) — Kong routes the entire LiteLLM surface, not just the dashboard path.
- **`litellm.localhost` added to** `bootstrapper/utils/hosts_manager.py::GENAI_HOSTS` so `./start.sh --setup-hosts` writes the `/etc/hosts` entry.
- **Wizard service box** now shows `http://litellm.localhost:${KONG_HTTP_PORT}` in the URL column on the LiteLLM row (was `—` before). Wired via a single `"LiteLLM": "litellm.localhost"` line in `bootstrapper/ui/state_builder.py::_HOST_ALIAS`; downstream rendering (`integration.py` → `service_table.py`) picks it up automatically.
- **Auto-redirect `/` → `/ui/` on the LiteLLM alias** — LiteLLM serves Swagger UI at its root and the admin dashboard at `/ui/`. A bare visit to `http://litellm.localhost:${KONG_HTTP_PORT}/` would otherwise land on Swagger, which is not what operators reaching for the alias expect. A `pre-function` Lua snippet on the Kong route short-circuits the request with a 302 to `/ui/` only when the path is exactly `/`; `/v1/*`, `/spend/*`, and `/openapi.json` fall through to the upstream unchanged. Requires `pre-function` in `KONG_PLUGINS` (already allowlisted in `services/kong/compose.yml`). Operators who want Swagger UI directly can still reach it at the direct port `http://localhost:${LITELLM_PORT}/`.
- **`preserve_host: True` on the LiteLLM Kong route** — without this, Kong rewrites the `Host` header from `litellm.localhost:${KONG_HTTP_PORT}` (the browser's URL) to `litellm:4000` (the internal upstream). LiteLLM's SPA reads the `Host` header when constructing the SSO login-redirect URL, so it embedded the internal Docker hostname, producing a `Location: http://litellm:4000/ui/login/...` that the browser cannot resolve. Setting `preserve_host: True` makes LiteLLM see the real browser-facing hostname and build correct redirects. Same pattern n8n's route uses.
- **Admin-dashboard login credentials made explicit** — modern LiteLLM versions retired the "master key alone authenticates the UI" fallback; without explicit `UI_USERNAME` + `UI_PASSWORD`, `/v2/login` raises `ProxyException`. Compose now sets `UI_USERNAME: ${LITELLM_UI_USERNAME:-admin}` and `UI_PASSWORD: ${LITELLM_MASTER_KEY}` (reusing the auto-generated master key so operators don't have to remember a second secret). New env `LITELLM_UI_USERNAME` added to `.env.example` and `services/litellm/service.yml`. Login is `admin` / `${LITELLM_MASTER_KEY}` by default; override the username via `.env`.

### Added (MinIO Kong alias for the admin console)
- **Kong route `minio.localhost` → `http://minio:9001/`** — added to `bootstrapper/utils/kong_config_generator.py::generate_minio_service()` and wired into the route orchestrator alongside the other host-aliased services. Gated on `MINIO_SOURCE != disabled`. Uses `preserve_host: True` so the MinIO console SPA constructs login/session URLs against the browser's real hostname instead of the internal `minio:9001` (same pattern n8n / Hermes / LiteLLM use). The S3 API at port 9000 is deliberately NOT aliased — S3 clients use full URLs with explicit ports anyway, and Kong proxying introduces unhelpful preserve-host complications for the S3-signature workflow.
- **`minio.localhost` added to** `bootstrapper/utils/hosts_manager.py::GENAI_HOSTS` (so `./start.sh --setup-hosts` writes the `/etc/hosts` entry) and `bootstrapper/ui/state_builder.py::_HOST_ALIAS` (so the wizard service-box shows `http://minio.localhost:${KONG_HTTP_PORT}` on the MinIO row alongside the direct port). The cross-surface agreement test in `test_kong_and_hosts_wiring.py` enforces the parity automatically.
- **`scripts/check-kong-routes.py::EXPECTED_HOST_ROUTES`** gained the new entry so the audit script enforces the route's continued presence.
- **docs**: `services/minio/README.md` got an expanded "Endpoints" table covering the new alias + the preserve-host plumbing rationale; `docs/deployment/ports-and-routes.md` gained the Kong column on the MinIO Console row; `services/kong/README.md` added the dynamic-route bullet + curl example; `services/minio/README.md` got a new `## Access` section; root `README.md` got the alias row in the service table and a quick-start hint.

### Added (Tests for Ollama-LiteLLM-wizard catalog-sync invariants)
- **4 new test files / 19 regression tests** that codify the recent Ollama-discovery bugs as a permanent guard:
  - `bootstrapper/tests/test_wizard_ollama_options.py` (5 tests) — exercises the wizard's `_merged_ollama_options` closure with mocked `/api/tags` + library scrape. Asserts: (a) every host-pulled tag lands in the family's `pulled_variants`; (b) family parent's `[pulled]` badge fires when ANY tag is on host (the "bare family name in pulled_set" bug); (c) bucket-1 fallback for tags whose family isn't in `ollama.com/library` at all; (d) options carry enough info for pre-check seeding.
  - `bootstrapper/tests/test_prompt_panel_leaf_badges.py` (4 tests) — exercises `PromptPanel._leaf_render_data` on a stub. Asserts: per-leaf `[pulled]`/`[library]` reflects `opt.pulled_variants` independently of the parent's status; mixed-status leaves within one family render correctly; empty `pulled_variants` leaves the leaf status-less (fallback to parent).
  - `bootstrapper/tests/test_live_catalog_sync.py` (3 tests, skip-aware) — integration: queries the live host Ollama `/api/tags` and the live LiteLLM `/v1/models`, asserts every host model is published by LiteLLM (auto-import fix), no phantom Ollama models in LiteLLM that aren't on the host or declared in `OLLAMA_USER_MODELS`/`OLLAMA_CUSTOM_MODELS`, AND runs the wizard's actual `options_provider` against the live host to confirm `pulled_variants` matches `/api/tags` reality. Skips cleanly when the stack isn't up.
  - `bootstrapper/tests/test_catalog_init_auto_import.py` (7 tests) — unit tests for `services/litellm/catalog-init/scripts/sync-catalog.py::_fetch_ollama_tags`. Loads the script via `importlib.util.spec_from_file_location` with `psycopg2` stubbed in `sys.modules` so the test runs without the catalog-init container's deps. Covers: happy path, alternate `model` field name, empty upstream, unreachable upstream, malformed JSON, empty URL short-circuit, garbage-entry tolerance.
- Tests are wired into the existing pytest infrastructure; total suite count grew from 126 to **145** (all passing).

### Fixed (Wizard Ollama-models pre-check + per-variant pulled badge)
- **Per-variant `[pulled]` badge under a [library] parent** — the wizard's Ollama-models step computed leaf badges via `_inherited_leaf_badges`, which strips status tags (`pulled`, `library`, `legacy`) under the assumption *"every leaf of a [library] parent is library; the user already sees that on the parent right above"*. That assumption fails when only some specific tags of a family are pulled — e.g. a host with `qwen3.6:35b-a3b-coding-mxfp8` pulled but not `qwen3.6:27b`/`35b`/etc. The family's parent gets `[library]` but the one pulled tag should render `[pulled]` to match reality. Fix: added `pulled_variants: frozenset[str]` to `PromptOption` (populated by the wizard's `_merged_ollama_options` from `/api/tags`), and made `_leaf_render_data` emit per-leaf status (`[pulled]` when `tag in opt.pulled_variants`, else `[library]`). Family parents now show `[pulled]` whenever ANY tag of that family is on the host (was: only when the bare family name itself appeared in `/api/tags`, which it never does).
- **Wizard auto-pre-checks every pulled host model** — `PromptPanel._load_step` for multiselect now seeds `_checked_values` from each option's `pulled_variants` in addition to the static `default_values`. Mirrors the runtime `OLLAMA_AUTO_IMPORT_LOCAL_MODELS` behaviour so the wizard UI tells the same story as `public.llms` will after confirmation. The post-confirm CSV is still the final word — operators who want a model hidden can uncheck it before pressing Enter.

### Added (Ollama auto-import for host-side sources)
- **`llm-catalog-init` auto-imports every model on the host's Ollama** when `LLM_PROVIDER_SOURCE=ollama-localhost` or `ollama-external`. The catalog-init container queries the upstream's `/api/tags` at boot and unions the result with `OLLAMA_USER_MODELS`, so any `ollama pull <name>` you do on the host propagates to `public.llms` → LiteLLM → every consumer on the next `./start.sh` — no wizard re-run required. This makes the host's Ollama instance the authoritative source for which models the stack exposes, instead of relying on the wizard's multiselect to be re-run every time the host catalog changes. Container sources (`ollama-container-*`) skip auto-import because their upstream is populated FROM `OLLAMA_USER_MODELS` by `ollama-pull` (querying it would be circular).
- **`OLLAMA_AUTO_IMPORT_LOCAL_MODELS` env var** (default: `true`) added to `services/ollama/service.yml` + `.env.example`. Set to `false` to keep strict wizard-only control of which models are exposed — useful when you have private fine-tunes on the host that shouldn't be exposed across every stack consumer.
- **`llm-catalog-init` now reaches `host.docker.internal`** — added `extra_hosts: ["host.docker.internal:${HOST_GATEWAY_IP}"]` to the catalog-init container so the new `/api/tags` query works for `ollama-localhost`. The container also receives `LITELLM_OLLAMA_UPSTREAM` (same env-var litellm-init consumes for its rendering), so `ollama-external` is supported through the same code path.
- **`_fetch_ollama_tags()` helper** in `services/litellm/catalog-init/scripts/sync-catalog.py` mirrors `bootstrapper/utils/ollama_discovery.py::list_pulled_models` in shape and failure mode (empty list on any error), so the two sites — the wizard's option list and the catalog's auto-import — fail consistently against the same `/api/tags` endpoint.

### Fixed (log-stream cleanup)
- **Kong DNS error noise during stack restart** — Kong's default `KONG_DNS_NOT_FOUND_TTL=30s` made it cache "name not found" verdicts for half a minute, so an active websocket retry loop (e.g. an Open WebUI tab reconnecting during `./start.sh`) flooded the Kong log with DNS errors until the cache expired. `services/kong/compose.yml` now sets `KONG_DNS_NOT_FOUND_TTL=1` and `KONG_DNS_STALE_TTL=4`, so Kong picks up newly-registered service containers within ~1s instead of ~30s. Error window dropped from 37 seconds / ~18 entries to single-digit retries.
- **Searxng `missing config file: /etc/searxng/limiter.toml`** — Searxng's bot-detection module wants an explicit `limiter.toml` next to `settings.yml`. Without it, Searxng logs the warning on every boot. Added `services/searxng/config/limiter.toml` using the current upstream schema (`[botdetection]` / `botdetection.trusted_proxies`, not the deprecated `[real_ip]` form). Trusted proxies set to Docker bridge subnets only (172.16/12, 192.168/16, 10.0/8); deliberately NOT including 127.0.0.0/8 so Searxng's own loopback healthcheck doesn't trip the X-Forwarded-For warning every check.
- **Searxng Wikidata 403 spam at boot** — Wikidata's SPARQL endpoint rate-limits aggressively and returns 403 with 24-hour suspension on initial engine probe from a new IP. The Searxng wikidata engine eagerly probes the endpoint at `init()`, which fired before any `disabled: true` flag was honored (the disable check is for query-time, not init-time). Removed the engine block entirely from `services/searxng/config/settings.yml`. DuckDuckGo's infobox covers the same UX role. Block-removal commentary inline so operators can restore the engine if they have a dedicated Wikidata arrangement.
- **Searxng X-Forwarded-For "fires once per boot" log line** — this is *not* fixable from outside Searxng. The `log_error_only_once()` call in `/usr/local/searxng/searx/botdetection/trusted_proxies.py:141` is gated to fire exactly once per worker lifetime on the first request that lacks both `X-Forwarded-For` and `X-Real-IP`. Searxng's own internal startup probe (granian's warm-up) hits the worker with no headers before the user's first browser request arrives, so the error always fires once at boot regardless of upstream proxy config. The logger then silences itself for the rest of the container's lifetime. Documented as expected boot-noise; no functional impact.
- **`scripts/check-kong-routes.py::EXPECTED_HOST_ROUTES`** gained the new entry so the audit script enforces the route's continued presence.
- **docs**: `services/litellm/README.md` got a new `## Access` table; `docs/deployment/ports-and-routes.md` gained the Kong column on the LiteLLM row; `services/kong/README.md` added the dynamic-route bullet + curl example; `services/litellm/README.md` got a matching `## Access` table for the service-folder reader; root `README.md` got the alias row in the service table.

### Fixed (LiteLLM gateway: empty chat responses, broken tool calls, duplicate Hermes provider)
- **Ollama chat completions returned empty `content`** — every Ollama model was registered in LiteLLM's `model_list` as `model: ollama/<name>`, which makes LiteLLM hit Ollama's `/api/generate` endpoint. That endpoint (a) does not support tool calls, (b) flattens multi-turn message history into a single prompt, and (c) silently drops the Ollama-native `think` parameter. So any thinking-capable model (qwen3, gpt-oss, deepseek-r1) got cut off mid-`<think>` block and returned empty `content`. Hermes Agent, Open WebUI's chat surface, n8n's LLM nodes, and the backend's agentic paths were all affected. Fix: `services/litellm/init/scripts/init.py::render_model_list` now writes `model: ollama_chat/<name>` for chat models (uses `/api/chat`, which supports tool calls, multi-turn, vision payloads, and the `think` param) and keeps `model: ollama/<name>` only for embedding models (the `/v1/embeddings` route refuses the `ollama_chat/` adapter). Detection is name-based: any catalog model with `"embed"` in its name is an embedding model. Additionally, `think: false` is set on every chat entry so thinking models always populate `content` rather than the side-channel `reasoning` field; consumers that want the trace can opt back in per-request with `"think": true`. See `services/litellm/README.md` → "Ollama adapter choice" and "Thinking models".
- **Hermes Agent registered LiteLLM twice in its provider picker** — `services/hermes/init/templates/config.yaml.tmpl` declared the gateway via both `model.provider: custom` + `base_url: http://litellm:4000/v1` AND a named `custom_providers[] = {name: litellm, base_url: http://litellm:4000/v1}` entry. Hermes's `get_compatible_custom_providers()` dedupe path did not collapse the inline anonymous entry against the named one, so the provider picker showed two `litellm` rows — one with the default model bound, the second orphaned at "0 models". Fix: kept the inline `model.provider: custom` block (Hermes's documented enum is `auto | openrouter | nous | codex | custom` — there's no `litellm` enum value) and emptied `custom_providers`. Future skills that need to address LiteLLM by an explicit named alias can add it back under a non-colliding name (e.g. `litellm-aux`).

### Changed (Per-service configuration modularization)
- **Monolithic `docker-compose.yml` retired** — the 1,425-line file split into per-service fragments under `services/<name>/compose.yml` merged at the top level via native Docker Compose `include:` directive. The new root `docker-compose.yml` is a 55-line shell. Requires Compose v2.20+ (v2.26+ recommended). Byte-equivalent rendering preserved across the full 36-container stack via the golden baseline at `bootstrapper/tests/fixtures/rendered_config_baseline.yml`.
- **`bootstrapper/service-configs.yml` deleted** — each service's runtime data (source variants, adaptive bindings, dependency declarations) now lives in its manifest at `services/<name>/service.yml` under `runtime_sc:`, `runtime_adaptive:`, `runtime_deps:` blocks; the stack-wide tier ordering moved to `services/globals/service.yml` under `runtime_dependency_tiers:`. A new `bootstrapper/services/sc_synthesizer.py` concatenates these slices into the dict shape consumers (`service_config.py`, `source_validator.py`, `dependency_manager.py`, `ui/state_builder.py`, `wizard/llm_steps.py`) used to load from YAML. `ConfigParser.load_yaml_config()` now calls the synthesizer.
- **Each service is now a folder** (`services/<name>/`) containing `service.yml` (manifest — env vars, source variants, image refs, dependencies, plus per-source bootstrapper runtime data under `runtime_sc:`) and `compose.yml` (Compose fragment). 24 manifests total — 21 container-backed + 3 virtual (cloud-providers, tts-provider, globals). Schema-validated against `bootstrapper/schemas/service.schema.json`.
- **`docs/CONTRIBUTING-services.md`** documents how to add a new service.

### Added (config modularization safety net)
- **`bootstrapper/services/manifest_validator.py`** — 8 cross-manifest checks (duplicate env vars, duplicate containers, dangling dependencies, undeclared exports/effects, source-var consistency, unknown consumer references). Runs in CI.
- **`bootstrapper/services/env_assembler.py`** — pure-function .env.example assembler from manifests (library-only).
- **`bootstrapper/tools/validate_fragments.py`** — `python -m tools.validate_fragments` CLI entry.
- **`bootstrapper/tests/`** — 110+ tests: loader, cross-manifest validator, env assembler, validate_fragments CLI, fragment-equivalence (byte-equiv vs golden baseline), source-permutation matrix, env-example consistency (manifest ↔ .env.example parity), backfill interplay (manifest change → backfill → user .env propagation).
- **`.github/workflows/services-lint.yml`** — three CI jobs: `lint` (manifest validator + unit tests), `compose-equivalence` (rendered byte-equiv + source-permutation matrix), and `audit-scripts` (docs drift + doc-links + compose-source-deps + Kong routes + research-schema).
- **`scripts/check-compose-source-deps.py`** updated to render compose via `docker compose config` so it sees the merged shape rather than only the thin include shell.

### Added (Hermes Agent — auto-pick default model, embedded Chat tab, dual Ollama aliases in LiteLLM)
- **`hermes-init` auto-picks `HERMES_DEFAULT_MODEL` when blank** — without a default, Hermes's rendered `config.yaml` had `model.default: null`, every dispatch 500'd, and Open WebUI's `hermes-agent` proxy route returned errors that looked like "Hermes can't see any models". The init script now queries `http://litellm:4000/v1/models` and picks the first match from a priority list (`ollama/qwen3.6:latest` → `claude-sonnet-4-6` → `claude-opus-4-7` → `gpt-5` → `gpt-5-codex` → `gpt-5-mini` → first-non-`hermes-agent` fallback). Cheapest-local-first, then big-context-cloud. Choice is logged in the init log for traceability. Operator override via `HERMES_DEFAULT_MODEL` in `.env` is preserved verbatim (auto-pick only fires when blank).
- **Ollama models now registered under bare model_name in LiteLLM (in addition to the prefixed form)** — `litellm-init/scripts/init.py:render_model_list` was emitting `model_name: ollama/{name}` for Ollama rows while cloud providers used bare names (`gpt-5`, not `openai/gpt-5`). When a client like Hermes Agent strips the `ollama/` prefix on outbound (treating it as a provider hint) and forwards `qwen3.6:latest` to LiteLLM, the gateway 400'd with `Invalid model name`. Each Ollama row now emits **two** `model_list` entries pointing at the same upstream — `ollama/{name}` (kept for backwards compat: `backend`'s `LITELLM_EMBEDDING_MODEL=ollama/nomic-embed-text`, `weaviate-init`'s `/shared/weaviate-config.env`) plus bare `{name}` (for prefix-stripping clients). Both names route through the same `litellm_params`, so latency/spend tracking stays single-counted.
- **Embedded Chat tab in the Hermes dashboard** — set `HERMES_DASHBOARD_TUI=1` (now the default) to expose the upstream-supported `/chat` route + `/ws/chat` WebSocket inside the dashboard, with a PTY-backed `hermes --tui` session as the backing terminal. Users can talk to the agent directly from the web UI without round-tripping through Open WebUI / curl. Documented in [upstream docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard); the `ptyprocess` extra ships in `nousresearch/hermes-agent:latest`. Flip to `0` in `.env` for a read-only dashboard.

### Added (Ollama wizard step — search box, MLX badge, cloud-only filter, tag column alignment)
- **Inline search box** above the filter-chip row on the Ollama models step: a 1-cell `Input` (placeholder `Tab or /  to filter models by name…`) that narrows the visible list by case-insensitive substring match against the model name. `Tab`, mouse click, and `/` all focus it; `Tab`, `Enter`, or `Esc` return focus to the option list. The substring filter STACKS with the chip filter — both must match for a row to render. Lives as a persistent `Input` on `PromptPanel` (mounted once, display toggled) to dodge a `DuplicateIds` race on the splash → real-options re-render that the lazy-fetch flow triggers for `options_provider` steps. Focus is explicitly parked on the option list immediately after mount so a freshly-mounted Input never swallows a Space/`j`/`k` the user thought was driving the cursor; the input lights up in bold cyan-on-tinted-bg when it actually has focus.
- **Keystroke routing while search is focused** — `WizardScreen.check_action` whitelists `back` (Esc), `quit_wizard` (Ctrl+Q), `move` (arrow keys), and `toggle_search_focus` (Tab); every other priority-binding (`f`, `a`, `e`, `w`, `i`, `space`, vim-style `j`/`k`) is suppressed so the keystrokes land in the Input as text. The `j`/`k` bindings were split off into a new `vim_move` action specifically so they can be suppressed independently of the arrow-key `move` action. Enter on the focused search input unfocuses (via `PromptPanel.on_input_submitted`) instead of confirming the whole step.
- **`[mlx]` capability badge** — Apple-Silicon-optimised MLX variants are now flagged per-leaf in the variant tree. New parser regex `_VARIANT_MLX_RE` in `bootstrapper/utils/ollama_library.py` detects the upstream `border-neutral-600 … MLX` chip on each variant block of `ollama.com/library/{model}`; `OllamaVariant.mlx: bool` plus the existing `capabilities` property surface the tag. `mlx` is added to `_PER_VARIANT_CAPS` in `prompt_panel.py` so it stays per-variant (NOT inherited from parent to all leaves) since only specific quant tags carry it.
- **Capability column alignment** — capability tags now render in a fixed canonical column order (`embedding · thinking · vision · tools · audio · mlx`) with reserved per-slot widths; absent tags reserve their column so the same capability lands at the same horizontal position across every visible row. Status tags (`pulled` / `library` / `legacy` / `default`) follow with variable width. Start column is computed dynamically by `PromptPanel._mount_visible_rows` — it walks the visible row set, finds the longest prefix+label content, and passes that column to every `OptionRow` so even outlier-length variants like `qwen3.6:35b-a3b-coding-mxfp8 (38GB · 256K ctx)` keep the tag block flush with shorter siblings. Narrow terminals (< 100 cells for parents, < 130 for leaves) fall back to inline variable-width tags to avoid pushing the pull-count column off-screen.
- **Ollama Cloud-exclusive models filtered out** — the live listing-page scrape now flags entries that carry the `cloud` chip AND publish no `x-test-size` variants (e.g. `glm-5`, `minimax-m2`, `kimi-k2`, `deepseek-v4-pro`, …). These cannot be `ollama pull`-ed, so the wizard drops them from the multiselect before render and writes `[info/ollama-fetch] excluded N cloud-only Ollama Cloud model(s) — not pullable: …` to the session log. Hybrid models that publish both cloud and pullable local variants (`gemma3`, `gpt-oss`, `qwen3-coder`, `deepseek-v3.1`, …) keep `cloud_only=False` and remain in the list with their local variants intact. New field `OllamaLibraryEntry.cloud_only: bool`; new regex `_CLOUD_BADGE_RE`; filter applied in `bootstrapper/wizard/llm_steps.py:_fetch_ollama_options`.

### Added (env-file backfill helper)
- **`backfill_missing_env_vars()` on `GenAIStackStarter`** — appends keys present in `.env.example` but missing from the user's `.env`, preserving every existing value. Catches the upstream-merge case where new services land in `.env.example` (MinIO, Hermes, Speaches, Chatterbox, Whisper.cpp) but the user's pre-existing `.env` predates the merge; without backfill, `docker compose up` failed with `service "minio" has neither an image nor a build context specified` because `${MINIO_IMAGE}` was empty. Preserves the source file's section organisation — missing vars are emitted under their original `# === SECTION ===` heading with their immediate context comments intact, ordered by where they appear in `.env.example`. Idempotent; called four times (once at every entry to the `setup_env_file` pipeline + a final defensive call before `docker compose up` so any intermediate write that drops keys is recovered).

### Fixed (service startup)
- **`speaches` restart loop** — the `PRELOAD_MODELS` env in `docker-compose.yml` was a comma-separated CSV (`hexgrad/Kokoro-82M,Systran/faster-distil-whisper-large-v3`), but Speaches types the field as `list[str]` on a `pydantic_settings.BaseSettings` model whose `EnvSettingsSource` decodes complex fields via `json.loads`. The CSV blew up with `JSONDecodeError: Expecting value`. Switched to a JSON-array literal `'[]'` (empty) — the names Speaches expects in `PRELOAD_MODELS` are internal `executor_registry` ids (e.g. `kokoro`), not the HuggingFace ids we keep in `SPEACHES_TTS_MODEL`/`SPEACHES_STT_MODEL` (those go on the request, not the preload). Empty preload matches the existing "lazy-loads on first /v1/audio/* request" comment; users wanting preload can edit the line directly with registry ids.
- **`hermes` healthcheck failing** — the historic `wget -q -O- http://127.0.0.1:8642/v1/models …` probe exited `wget: not found`, and the obvious python fallback hit `python: not found` (the image only ships `python3`). Verified the image actually does ship `curl` (the previous compose comment was wrong on both counts). Switched to `curl --fail --silent --show-error -H "Authorization: Bearer $$API_SERVER_KEY" http://127.0.0.1:8642/v1/models`. Container now `(healthy)`.
- **`hermes-init` was a no-op** — the compose block mounted `./hermes-init/scripts:/scripts:ro` + `./hermes-init/templates:/templates:ro` against an `alpine:latest` image but had **no `entrypoint` or `command`** — the container started, found nothing to run, exited 0 in ~150ms, and `docker compose ps` reported `Exited (0)` looking exactly like a successful init. `/opt/data/config.yaml` was never rendered; `hermes` then fell back to its image's bundled default (`provider: openrouter, default: anthropic/claude-opus-4.7`) and 401-spammed the log indefinitely because `OPENROUTER_API_KEY` was empty. Added `entrypoint: ["/scripts/init-hermes.sh"]` matching the existing `weaviate-init` / `openclaw-init` pattern. The script now actually runs, `/opt/data/config.yaml` renders against the LiteLLM-routed template, no more 401s.
- **`local-deep-researcher` flapping unhealthy on first launch** — the Dockerfile's `HEALTHCHECK --start-period=60s` expired while the entrypoint was still cloning the upstream repo and `uv pip install`-ing 72 packages (numpy, lxml, langchain, langgraph, openai, …) — routinely 2-5 minutes on a clean machine. Bumped to `--start-period=300s`; subsequent restarts hit the cached venv and pass in <10s, so the higher ceiling costs nothing in steady state.
- **`supabase-realtime` libcluster spam every 5 seconds** — the upstream `supabase/realtime:v2.33.72` image hardcodes `Cluster.Strategy.DNSPoll` (the `fly6pn` topology) and ignored our `LIBCLUSTER_STRATEGY` / `LIBCLUSTER_TOPOLOGIES` overrides. With no `DNS_NODES` env set, libcluster logged `query or basename param is invalid: query: nil` on a 5-second cadence. Pointing `DNS_NODES` at the container's own hostname created a different warning (`unable to connect to :realtime@<container-ip>` — Erlang node mismatch). Final fix: `DNS_NODES: supabase-realtime-noop.invalid` — the `.invalid` TLD (RFC 6761) returns NXDOMAIN, libcluster's empty-peer-list path is silent, the env var is set so libcluster considers itself "configured". Dropped the two ineffective `LIBCLUSTER_*` vars.
- **`n8n` / `n8n-worker` migration race** — both containers were depending on `supabase-db-init: service_completed_successfully` but not on each other, so both started concurrently and both ran TypeORM migrations against the shared `n8n` schema. One container would lose on `CreateWorkflowHistoryTable1692967111175` with `duplicate key value violates unique constraint "pg_type_typname_nsp_index"`, retry, succeed; while n8n recovered automatically the boot logs printed scary `error running database migrations` lines on every cold start. Added a healthcheck to `n8n` (`wget -qO- http://127.0.0.1:5678/healthz`, 15s interval / 90s start_period) and changed `n8n-worker.depends_on.n8n` from `service_started` to `service_healthy` so the worker waits for n8n's migration phase to finish before starting its own.

### Added (MinIO artifact-tier object storage)
- **MinIO object storage**: S3-compatible artifact-tier storage service with five pre-provisioned buckets (`comfyui`, `backend`, `n8n`, `jupyter`, `docling`) and scoped service-account credentials surfaced as `MINIO_<NAME>_ACCESS_KEY` / `MINIO_<NAME>_SECRET_KEY` in `.env`. Admin console at `http://localhost:63031`; S3 API at `http://localhost:63030`. Consumer code is unchanged in this release; each consumer integration ships in a dedicated follow-up. Pinned to the dated Docker Hub release tag `minio/minio:RELEASE.2025-09-07T16-13-09Z`; note that the upstream service-account-CVE fix `RELEASE.2025-10-15T17-29-55Z` is published on GitHub only and not yet on Docker Hub — operators handling untrusted credentials should rebuild from source or pin a later tag once available.
- **`minio-init` provisioner**: one-shot container running `minio/mc` that creates buckets, named IAM policies (`<consumer>-policy`), and service accounts on every `./start.sh`. Idempotent — re-runs are no-ops.
- **Bootstrapper integration**: `MINIO_PORT=63030` / `MINIO_CONSOLE_PORT=63031` registered in `PortManager.PORT_MAPPING` (recomputed correctly under `--base-port`); `KeyGenerator` extended with `MINIO_ROOT_PASSWORD` + 10 per-consumer service-account credentials (idempotent — hand-edits stick); `--minio-source [container|disabled]` Click flag plumbed through `SourceOverrideManager`; wizard surfaces MinIO as a DATA-tier service via the manifest at `services/minio/service.yml` (synthesized by `bootstrapper/services/sc_synthesizer.py`) plus display-name / description / tag registrations.

### Added (Hermes Agent runtime)
- **New `hermes` service** (`nousresearch/hermes-agent:latest` — upstream publishes only `latest` + immutable `sha-<commit>` tags, no semver; production should pin to a specific sha per `services/hermes/README.md`) — programmable AI agent runtime by Nous Research. Promoted from `docs/ROADMAP.md` Tier 2 to shipped. Container by default (3 SOURCE modes: `container`, `localhost`, `disabled`), ~2-4 GB RAM, no GPU. File-based persistence under `/opt/data` (`hermes-data` named volume) — no Postgres / Redis dependency. OpenAI-compatible API on port 8642 → host `63028`; web dashboard on 9119 → host `63029`, Kong-aliased as `hermes.localhost`.
- **New `hermes-init` companion** — renders `/opt/data/config.yaml` from environment before Hermes starts. Wires LiteLLM (`http://litellm:4000/v1`) for reasoning, Speaches / Chatterbox / Parakeet via OpenAI-compatible base-URL overrides for voice (`TTS_ENDPOINT` / `STT_ENDPOINT`), ComfyUI via a skill-override file at `/opt/data/skills/creative-comfyui-host-override.md`, and SearXNG for web search. Empty endpoint → block omitted from `config.yaml` (graceful degradation when a dependency is disabled). Bootstraps deps via inline `apk add` then re-execs under bash (matches openclaw-init / weaviate-init convention).
- **`hermes-agent` registered in the LiteLLM model_list** — `litellm-init/scripts/init.py` appends a `hermes-agent` row pointing at `${HERMES_ENDPOINT}/v1` when `HERMES_SOURCE != disabled`. Consequence: Open WebUI, n8n, backend, jupyterhub, openclaw all see the new model automatically with no per-consumer wiring.
- **`HERMES_ENDPOINT` + `HERMES_API_KEY` plumbed to consumers** — backend, n8n, jupyterhub, openclaw-gateway env blocks for direct API / webhook access (LiteLLM-routed `hermes-agent` model is the default surface).
- **Bootstrapper integration** — new `services/hermes/service.yml` manifest (`container` / `localhost` / `disabled` sources + cross-deps on stt_provider / tts_provider / comfyui / searxng for init-time URL wiring, all under `runtime_sc:` / `runtime_adaptive:` / `runtime_deps:` blocks; synthesized into the legacy dict shape by `bootstrapper/services/sc_synthesizer.py`), `_generate_hermes_config()` in `bootstrapper/services/service_config.py` (mirror of `_generate_openclaw_config()`), `HERMES_ENDPOINT` in `bootstrapper/utils/endpoint_vars.py`, CLI flag `--hermes-source`, port-clear list, localhost validator, source override manager, dependency manager scale/source mappings, wizard tile (`bootstrapper/ui/state_builder.py`), service discovery name/description, hosts manager (`hermes.localhost` written by `--setup-hosts`), log-pane TOOL tag, `HERMES_API_KEY` auto-generation (32-byte URL-safe token, idempotent like LITELLM_MASTER_KEY).
- **Kong route `hermes.localhost` → `http://hermes:9119`** — added to `bootstrapper/utils/kong_config_generator.py:generate_hermes_service()`. Gated on `HERMES_SOURCE != disabled` AND `HERMES_DASHBOARD_ENABLED=true`.
- **Audit script extensions** — `scripts/check-compose-source-deps.py` now enforces `(hermes, litellm)` and `(hermes-init, litellm)` `depends_on` pairs; `scripts/check-kong-routes.py` enforces the `hermes.localhost → http://hermes:9119/` route.
- **docs**: new `services/hermes/README.md` (full service doc), updated `docs/README.md`, `README.md` (5 OpenClaw parallels), `docs/deployment/ports-and-routes.md` (+rows for 63028/63029 and `hermes.localhost`), `docs/deployment/source-configuration.md` (table rows + dedicated subsection), `docs/quick-start/interactive-setup-wizard.md` (wizard table row), `services/kong/README.md` (route + curl example), `services/ollama/README.md` (LiteLLM consumer list), `services/litellm/README.md` / `services/openclaw/README.md` / `services/open-webui/README.md` (cross-references), `docs/ROADMAP.md` (marks Tier-2 entry as shipped, corrects the wrong Supabase-dependency claim — Hermes is file-based).
- **runtime verification**: pulled and booted `nousresearch/hermes-agent:latest` (multi-arch — `linux/amd64` + `linux/arm64`); image is **~5.66 GB** on disk; OpenAI-compatible API responds at `/v1/models` with the bundled `hermes-agent` model id; 87 default skills sync into `~/.hermes/skills/` on every start; entrypoint refuses `HERMES_UID=0` (default `10000` is safe).

### Added (Ollama multi-select enrichments — capability tags, sizes, recency-bucket sort, filter chips)
- **Capability tag badges on every Ollama row** — `[embedding]`, `[thinking]`, `[vision]`, `[tools]`, `[audio]`. Scraped from each model card's `x-test-capability` spans on `ollama.com/library`. Curated catalog `embeddings` (plural) aliases to the live-scrape `embedding` (singular) so a row never shows both.
- **Single-select filter chip row** above the multi-select: `Filter  [ALL]  embedding  thinking  vision  tools  audio`. Click a chip to narrow the list; click `ALL` to reset. View-only — rows checked under one filter survive switching to another. New widget `bootstrapper/ui/textual/widgets/multiselect_filter_chips.py`; new fields `PromptStep.filter_tags`, `PromptPanel._filter_tag` / `_visible_indices`.
- **Approximate disk-size column** — every variant rendered as Q4_K_M footprint (`8b → 4.8GB`, `70b → 42GB`, `0.6b → 360MB`, `270m → 162MB`) via `option_row._approx_size`. Computed from Ollama's published parameter count (`params × 0.6 bytes/param` rule of thumb; real downloads are ±10–15% of the figure shown). On narrow terminals the column compresses to the first three variants + `…`, then drops entirely below the pull-count column.
- **Pull count column** — right-aligned, muted, formatted `K`/`M`/`B` (e.g. `114.2M`). Sourced from each card's `x-test-pull-count` span.
- **Two-bucket recency sort** — models updated within 365 days come first, sorted by total pulls descending; everything older gets a muted `[legacy]` badge and drops below in the same sort. This demotes year-old hits (`llama3.1` at 114M pulls) below newer-but-popular models (`deepseek-r1`, `gemma3`, `qwen3`). Threshold lives at `llm_steps._LEGACY_THRESHOLD_DAYS = 365`. `updated X ago` annotation appears in the hint line.
- **`OllamaLibraryEntry` dataclass replaces the names-only scrape** — `name`, `capabilities`, `sizes`, `pulls`, `updated`, `age_days`. Parser anchors on Alpine.js `x-test-*` test attributes (stable). `list_library_models()` removed (no callers).

### Added (wizard rework — DB-driven model_list, live model lists, multi-select prompts)
- **`public.llms` is now the single source of truth for the LiteLLM `model_list`**. Removed the hardcoded model lists in `bootstrapper/utils/litellm_config_generator.py`; the bootstrapper now writes only a stub `volumes/litellm/config.yaml` with empty `model_list`. The real config is rendered on every `docker compose up` by `litellm-init/scripts/init.py` from `SELECT … FROM public.llms WHERE active = true`.
- **`llm-catalog-init` container** (`llm-catalog-init/Dockerfile` + `scripts/sync-catalog.py`, python:3.12-slim): runs between `supabase-db-init` and `ollama-pull`/`litellm-init`. UPSERTs the curated catalog from `bootstrapper/utils/llm_catalog.py` and applies wizard / `.env`-driven model selections (`OPENAI_USER_MODELS`, `ANTHROPIC_USER_MODELS`, `OPENROUTER_USER_MODELS`, `OLLAMA_USER_MODELS`, `OLLAMA_CUSTOM_MODELS`). Pre-flight check verifies the `(provider, name)` unique constraint exists.
- **`bootstrapper/utils/llm_catalog.py`**: single source of truth for curated cloud + Ollama catalog. Each entry carries capability flags (`content`, `structured_content`, `vision`, `embeddings`), `context_window`, `default_active`. Cloud catalog includes gpt-5 family, claude-4.x line, and OpenRouter aggregator routes.
- **Schema migration** `supabase/db/scripts/05a-public-tables-migrations.sql`: drop `llms_name_key`, add composite `llms_provider_name_key UNIQUE (provider, name)` so models with the same bare name across providers can coexist.
- **Live cloud model fetch** in the wizard:
  - OpenAI `/v1/models` with the user's key + per-provider filter (see `bootstrapper/utils/cloud_models.py` for the current allow/deny lists; the filter is maintained there as new model families ship — DALL-E, Whisper, TTS, fine-tunes and snapshot variants are excluded).
  - Anthropic `/v1/models` with `x-api-key` — uses `display_name` for label, dedups snapshots.
  - OpenRouter `/api/v1/models` (no auth) — sorted alphabetically by label, capped at 50 entries to keep the picker usable.
  - All three fall back to `CLOUD_CATALOG` on network/auth/timeout/empty failure.
- **Live Ollama library scrape** of `https://ollama.com/library` (~230 entries) via `bootstrapper/utils/ollama_library.py`. Available for **every** ollama-* source (localhost, external, container) so users can browse and register additional models regardless of upstream type. Falls back to `OLLAMA_DEFAULT_CATALOG` on failure.
- **Live Ollama upstream discovery**: for `ollama-localhost` / `ollama-external`, `bootstrapper/utils/ollama_discovery.py` queries `${LITELLM_OLLAMA_UPSTREAM}/api/tags` to list models already pulled on the user's host.
- **Three new wizard prompt kinds** in `bootstrapper/ui/textual/widgets/prompt_panel.py`:
  - `kind="secret"` — masked `Input(password=True)`, with `<KEEP>` / `<CLEAR>` sentinels for re-runs (existing key + Enter = keep current; type `clear` to remove). Live "✓ N chars entered" counter via `on_input_changed` so the user can confirm a paste landed even when the dots scrolled out of view.
  - `kind="multiselect"` — checkbox list with `[✓]` / `[ ]` indicators. Space toggles the focused row; Enter confirms. Comma-joined CSV value. Optional `options_provider` for lazy/live fetch with `⏳ Fetching X models…` splash + worker + cache + back-invalidation.
  - `kind="text"` — free-text input with the same `<KEEP>` / `<CLEAR>` sentinels (used for `OLLAMA_CUSTOM_MODELS` so an empty Enter on re-run doesn't silently wipe an existing value).
- **CLI flags**: `--openai-models`, `--anthropic-models`, `--openrouter-models`, `--ollama-models`, `--ollama-custom-models` (comma-separated). Imply matching `--cloud-*-source=enabled` when paired with the corresponding `--*-api-key`.
- **`/tmp/atlas-launch-<YYYYMMDDTHHMMSS>.log`** — every wizard launch tees pipeline + docker compose output to this file. The session log is now opened at wizard start (not launch), so cloud `/v1/models` fetch failures during the setup phase are persisted too. On `docker compose up` non-zero exit, automatically captures `docker compose logs --tail=200` for every service. Path written as the first line in the wizard's log pane (`📝 session log: /tmp/atlas-launch-<…>.log`).
- **Per-service color-coded log pane**: `LogPane._write_record` now matches the compose `<container>   | <body>` pattern and colors the container-name prefix using `palette.color_for_source(rec.source)`. Hash-based fallback (md5) gives every service — including ones not in the curated `SOURCE_COLORS` map (jupyterhub, openclaw, local-deep-researcher, etc.) — a stable distinct hue.
- **Stack-overview Cloud APIs sub-section**: `bootstrapper/ui/textual/widgets/info_box.py:CloudApisRow` shows OpenAI / Anthropic / OpenRouter status (`enabled · key set ✓`, `disabled`, `enabled · key MISSING ⚠`) below the services grid. Footer count line gains a `N cloud apis on` segment.
- **Validator auto-disable for `enabled+empty-key`**: `services/source_validator.py:_enforce_cloud_keys_present` flips `CLOUD_*_SOURCE=enabled` back to `disabled` when the matching `*_API_KEY` is empty (with a warning), guarding against unusable launch state from hand-edited .env or CLI-flag misuse.
- **Cloud `/v1/models` fallback diagnostics**: `bootstrapper/utils/cloud_models.py` now accepts an `on_warn` callback. The wizard registers a sink (`integration._set_wizard_warn_sink`) that routes failures into `_safe_log` so they land in both the log pane and `/tmp/atlas-launch-*.log` — e.g. `[warn/openai-fetch] live /v1/models failed — falling back to catalog (cause: HTTP 401 Unauthorized)`. Distinguishes empty-key, transport, JSON, missing-`data[]`, and post-filter empty-set failures.

### Changed (wizard rework)
- **Wizard step ordering**: cloud secret + multi-select pairs (OpenAI / Anthropic / OpenRouter) are spliced **immediately after the LLM Engine + Ollama steps**, not after every other service-source step. New flow: base port → ComfyUI → LLM Engine → Ollama variants → cloud key+models pairs → other services → cold/hosts/confirm.
- **Cloud providers re-classified as APIs, not services**: removed from `bootstrapper/ui/state_builder.py:_SERVICES`. They no longer appear in the services grid, footer counts, or no-TUI pre-launch summary table — instead they render in their own "Cloud APIs" block.
- **Catalog mount path**: `llm-catalog-init` mounts `./bootstrapper/utils:/catalog:ro` (sibling to `/scripts`) instead of layering `llm_catalog.py` inside the `/scripts:ro` mount. Avoids a Docker file-on-dir overlay edge case that silently broke first-run launches.
- **`docker compose` flags in wizard launch**: `--ansi=always` → `--ansi=never`. The animated TTY-based progress is incompatible with our Popen-piped stdout (compose reports `failed to get console: provided file is not a console` and exits 1). Per-service coloring is now synthesized client-side via `palette.color_for_source` instead of relying on compose's embedded ANSI codes.

### Fixed (wizard regressions discovered + fixed during this round)
- **`DuplicateIds` crash on consecutive secret prompts** (`prompt_panel.py`): widgets were re-mounted per step but `Container.remove_children()` is async — the previous step's `Input(id="secret-input")` was still in the node list when the next step's mount tried to register the same id. Switched to widget-reuse: persistent `_number_input` / `_secret_input` / hint Statics created once and re-shown per step.
- **`NameError: PromptOption is not defined`** in `wizard_screen.py:_load_current_step` splash branch: missing import, now added.
- **`'NoneType' object has no attribute '__dict__'`** in `llm-catalog-init`'s `load_catalog()`: Python 3.12's `@dataclass` decorator (with `from __future__ import annotations`) calls `dataclasses._is_type` → `sys.modules.get(cls.__module__)`. Module loaded via `importlib.util.spec_from_file_location()` wasn't registered. Fix: `sys.modules["llm_catalog"] = module` before `exec_module()`.
- **Live logs not updating in the wizard pane** after launch: `_run_compose` was on the main async event loop, but `_safe_log` used `self.app.call_from_thread(...)` (designed for worker threads). Calling `call_from_thread` from the same thread silently failed to deliver UI updates. `_safe_log` now checks `threading.current_thread() is threading.main_thread()` and uses a direct `_log_pane.write_log` call when on the main thread, `call_from_thread` from workers.
- **`docker compose up` output not in launch log**: `_run_compose` and `_run_command` wrote directly to `_log_pane.write_log()` instead of routing through `_safe_log()`, so the tee path was bypassed. Fixed.
- **Multi-select state lost on back-then-forward** navigation: `_load_current_step` always rebuilt `default_values` from `original.default_values` instead of honoring the user's prior selection in `self._selections`. Fixed.
- **Text-step empty Enter destroyed existing value**: an empty input on a step with a non-empty `default_value` returned `""` instead of a `<KEEP>` sentinel, silently wiping `OLLAMA_CUSTOM_MODELS` on re-runs. Now uses the same keep-current sentinel as the secret step.
- **`will_run_wizard` ignored `--*-models` flags**: passing `--openai-models gpt-5` alone (no source flag) triggered the wizard, silently overriding the CLI value. The detection now also considers `user_model_selections` and `cloud_api_keys`.
- **Cloud provider "enabled but unusable" on CLI-flag mode**: `--openai-api-key sk-…` without `--openai-models` left zero rows active in `public.llms` (cloud entries default to `default_active=False`). `apply_cloud_selection` now activates the catalog's `default_active=True` set when the user enables a provider with no model override.
- **`skip_if_prev` could crash the wizard**: any exception in the predicate would propagate. Both forward (`_load_current_step`) and backward (`action_back`) navigation now catch exceptions and treat them as "don't skip".
- **`OLLAMA_PULL_SCALE` ran for host-side Ollama upstreams**: `service_config.py` set the scale to `1` whenever `LLM_PROVIDER_SOURCE != 'none'`, so `ollama-pull` would attempt `/api/pull` against the user's `ollama-localhost` / `ollama-external` instance — surprising behaviour, and contradicted the `.env.example` text + `services/ollama/README.md`. Restricted to `ollama-container-*` only. (Subsequent change registers host-side custom Ollama rows in `public.llms` with a warning that the operator must `ollama pull` themselves; see the Changed section below.)
- **`litellm-init` torn-write hazard**: a crash between writing the sentinel header and `yaml.safe_dump` left a sentinel-marked but body-less `config.yaml`, which `litellm_config_generator._is_litellm_init_managed` would *preserve* on subsequent runs — persisting a broken config indefinitely. `write_config` now writes to `config.yaml.tmp` and `os.replace()`s atomically.
- **Backend memory extraction broken in cloud-only setups**: `MemoryService._get_extraction_model` queried `WHERE provider='ollama'`, so cloud-only setups (Ollama rows deactivated) fell through to the hardcoded `ollama/qwen3.6:latest` — a model not in LiteLLM's model_list, causing extraction to fail. Now queries all providers; per-provider name mapping mirrors `litellm-init/scripts/init.py:render_model_list`.
- **Wizard could record inert cloud model selections**: when `CLOUD_*_SOURCE=disabled` in .env but the API key was already set, the secret-step's "keep current" sentinel let the multi-select render → user picked models → `_selections_to_args` recorded `*_USER_MODELS` but didn't enable the source → `llm-catalog-init` then deactivated everything for that provider. Skip predicate now consults the .env source state; auto-promotes to `enabled` when the user proceeds past a SECRET_KEEP step that already has a key.
- **Stale async fetch worker pollutes provider cache**: a slow `/v1/models` worker dispatched before the user pressed Esc → changed key → revisited the step would write its (now-stale) options into the cache the user had just invalidated. Added a generation token bumped by `action_back`; workers compare-then-write and silently drop on mismatch.
- **Setup-phase wizard warnings dropped from session log**: the launch-log file was only opened during the setup→launch transition, so cloud `/v1/models` fetch failures during the wizard's setup phase were silently lost — contradicting troubleshooting docs that promised the file captured everything. Tee now opens at wizard start; the announce-in-pane line moves to the launch transition (when the pane exists). The file is also closed on setup-phase quit.
- **Migration constraint check could falsely no-op**: `05a-public-tables-migrations.sql` and `sync-catalog.py:verify_constraint` checked only `pg_constraint.conname`; if any other table somehow had the same constraint name, the guard would skip the ALTER. Both call sites now scope by `conrelid = 'public.llms'::regclass`.

### Changed
- **Validator side effects split off**: `SourceValidator.validate_all_sources()` is now read-only. The auto-disable-cloud-providers-with-missing-keys behaviour moved to `enforce_runtime_invariants()` — `start.py` calls both, but pure-tooling callers (linters, dry-runs) can validate without mutating .env.
- **Cloud APIs overview live-updates on multi-select 0-selection**: unchecking every model in a cloud provider's multi-select now flips the matching Cloud APIs row to `disabled` immediately (matching the `_selections_to_args` policy that treats empty CSV as "user wants this provider off"), instead of waiting until launch to surprise the user.
- **Command summary covers cloud + Ollama selections**: `--cloud-X-source enabled/disabled`, `--X-api-key <set>` (sanitized; never the raw key), `--X-models N selected (...)`, `--ollama-models`, `--ollama-custom-models`. The "equivalent CLI" preview is equivalent again.
- **Better Ollama-discovery UX on failure**: the unified Ollama multiselect's options provider no longer returns an empty list silently. On `/api/tags` failure or empty result, surfaces a placeholder row explaining what went wrong and routes the diagnostic through the same launch-log sink the cloud steps use.
- **Custom Ollama models on host-side upstreams**: previously dropped silently with a warning. Now registered + active in `public.llms` with a loud warning that the operator must `ollama pull <name>` themselves on the host (since `ollama-pull` doesn't run for host-side sources). Matches the wizard's catalog-multiselect behaviour for localhost/external.
- **Stale-actives warning on Ollama upstream switch**: switching `LLM_PROVIDER_SOURCE` (e.g. container → localhost) without supplying `OLLAMA_USER_MODELS` now warns about every preserved active row that may not exist on the new upstream.
- **`volumes/api/kong-dynamic.yml` is now a pure runtime artifact** (`.gitignore`d, regenerated on every `./start.sh`). Direct `docker compose up` from a clean checkout is unsupported — `services/kong/README.md` updated; `scripts/check-kong-routes.py` was rewritten to invoke the kong generator against `.env.example` in a tmp dir and validate that, instead of reading the user's runtime file.
- **`bootstrapper/utils/cloud_providers.py`** — single source of truth for cloud LLM provider tuples (display name, source var, API key var, enabled flag var). Replaces three separate per-shape lists in `state_builder.py`, `source_validator.py`, and `service_config.py`.
- **Source-aware secret-step hints** — cloud key prompts now show distinct wording for `enabled+key` ("Enter keeps enabled"), `disabled+key` ("Enter enables with saved key"), and `disabled+no-key` ("Press Enter (empty) to leave disabled"). Driven by a new optional `secret_keep_hint` field on `PromptStep`.
- **Live-discovered cloud / Ollama models now insert if missing** — `llm-catalog-init/scripts/sync-catalog.py:insert_live_only` adds rows for selections that aren't in the curated catalog with provider-specific generic capability defaults (`LIVE_DEFAULTS`). Per-provider routing in `litellm-init` is unchanged. Logs report `N requested, M matched in catalog, K inserted as live-only`.
- **`MemoryService` raises instead of silent fallback** — `_get_extraction_model` previously returned `ollama/qwen3.6:latest` on any DB error, which is unroutable in cloud-only setups. Now logs the underlying exception and raises `RuntimeError` with a clear message about setting `LITELLM_DEFAULT_MODEL` or activating a content row.
- **Multi-select hidden-defaults guard** — `prompt_panel.py` now intersects `default_values` with the visible option set when loading a multi-select step. Previously, default values not present in the live-fetched options stayed invisibly checked and leaked into the saved CSV at confirm.
- **Compose `${VAR-default}` semantics** — `docker-compose.yml` switched both `LITELLM_OLLAMA_UPSTREAM` substitutions from `${VAR:-default}` to `${VAR-default}` (no colon) so an explicit empty value reaches the LiteLLM container as `""`. The `:-` form silently substituted the default for empty too, breaking the documented "none → empty → no Ollama upstream" semantic.
- **OpenRouter ID double-prefix guard** — `cloud_models.py:list_openrouter_models` now skips the `openrouter/` prefix when the upstream's `id` already starts with it, preventing `openrouter/openrouter/...` if the API response shape ever changes.
- **Cloud APIs overview live-updates on auto-promote** — when the user proceeds past a `disabled+key` cloud secret step (SECRET_KEEP) and the multiselect renders, `_apply_secret_step_to_cloud_apis` and `_apply_models_step_to_cloud_apis` now flip the overview row to "enabled" immediately so the live state mirrors the launch outcome. Also extracted `_refresh_info_panel` to consolidate four duplicated update blocks.
- **Single unified Ollama models picker with `[pulled]` / `[library]` badges** — replaces the previous two-page `pulled` + `library` split, which produced two near-duplicate multi-select pages for `ollama-localhost` / `ollama-external` users (the library was a strict superset of `/api/tags`). Now: container modes show the library scrape only; localhost/external show a merged view where `[pulled]` rows are on disk on the user's upstream and `[library]` rows are catalog entries that need a manual `ollama pull`. Step constants `OLLAMA_LIVE_TITLE` and `OLLAMA_CATALOG_TITLE` collapse into a single `OLLAMA_MODELS_TITLE`.
- **Multi-select scrolling fix** — the option-list container is now a `VerticalScroll` with `max-height: 18` and the focused row is `scroll_visible()`'d after every `move()`. Previously a 230-entry library scrape grew the panel past the viewport and pressing Down moved the cursor off-screen invisibly.

### Deferred / known limitations
- **`utils/cloud_models.py --check` self-test CLI** — useful for triage; not shipped this round.

### Cleanup
- **Dropped 7 non-default Ollama catalog entries** (`llama3.3`, `llama3.2`, `mistral-small`, `phi4`, `qwen3.6:7b`, `deepseek-r1`, `mxbai-embed-large`). Superseded by the live `ollama.com/library` scrape; never sat in `default_active`. `OLLAMA_DEFAULT_CATALOG` now contains only the default-active trio.
- **Removed dead `_changed_count` method** (`wizard_screen.py`) — defined but never called from anywhere. Pre-existing dead code.
- **Removed dead `ensure-litellm-db.sh`** — replaced by `litellm-init/scripts/init.py`.
- **Removed `LITELLM_INIT_IMAGE` from `.env.example`** — no longer used since `litellm-init` builds from a Dockerfile instead of `image:`.
- **Splash label deduplication** — wizard's `⏳ Fetching <provider> models…` splash now strips the redundant " Cloud" suffix from cloud provider names.

### Added
- **LiteLLM Gateway** (mandatory core service): always-on OpenAI-compatible front door for every LLM provider. Pinned image `ghcr.io/berriai/litellm:v1.83.14-stable.patch.2`, listening on port 63012 (the slot formerly held by Ollama). Persistence on a dedicated `litellm` database in the existing Supabase Postgres (Prisma migrations run automatically); response caching + rate-limit state in Redis.
  - **Wizard model**: LiteLLM is a locked tile (no source toggle). A separate **LLM Engine** tile single-selects the local Ollama upstream (`ollama-container-cpu`, `ollama-container-gpu`, `ollama-localhost`, `ollama-external`, `none`). Three new **Cloud APIs** (OpenAI / Anthropic / OpenRouter) appear in a dedicated overview block rather than as service tiles — each is a secret-input + multiselect pair that toggles the corresponding provider in LiteLLM's `model_list`. Bootstrapper refuses to start when no upstream is configured (engine=`none` + every cloud provider disabled).
  - **CLI flags**: `--llm-provider-source` enum dropped `api`/`disabled`, added `none`. New flags `--cloud-openai-source`, `--cloud-anthropic-source`, `--cloud-openrouter-source` (each `enabled`/`disabled`).
  - **Master key**: bootstrapper auto-generates `LITELLM_MASTER_KEY` (`sk-…`) on first start and never overwrites it on subsequent runs.
  - **Documented backup**: Portkey AI Gateway (Apache-2.0) — switch path noted in `services/litellm/README.md`.
- **`vllm-container-gpu` upstream** is deferred to a follow-up plan (tracked in ROADMAP).
### Changed (LiteLLM migration)
- **Consumer env-var rename (breaking)**: every service that talks to an LLM now reads `LITELLM_BASE_URL` + `LITELLM_API_KEY`. The legacy `OLLAMA_BASE_URL` / `OLLAMA_ENDPOINT` env vars are removed from all consumer compose blocks (`open-web-ui`, `backend`, `n8n`, `n8n-worker`, `n8n-init`, `jupyterhub`, `local-deep-researcher`, `openclaw-gateway`, `weaviate-init`, `weaviate`).
- **`LLM_PROVIDER_PORT` renamed to `LITELLM_PORT`** (same default 63012). `bootstrapper/core/port_manager.py` and `.env.example` updated.
- **Backend memory service refactored** (`backend/app/memory_service.py`, `memory_store.py`): switched from Ollama's native `/api/generate` + `/api/embeddings` to LiteLLM's OpenAI-compatible `/v1/chat/completions` + `/v1/embeddings`. All 7 Weaviate collection schemas migrated from `text2vec-ollama` (with `apiEndpoint`) to `text2vec-openai` (with `baseURL` pointed at LiteLLM). New helper `_litellm_complete()` consolidates the chat completion call sites.
- **Local Deep Researcher** now uses the OpenAI-compatible LangGraph client pointed at LiteLLM (`init-config.py` writes `llm_provider=openai`; entrypoint healthchecks `LITELLM_BASE_URL/health/liveliness`).
- **Weaviate** default vectorizer is now `text2vec-openai` (LiteLLM-backed). `text2vec-ollama` is left enabled for backward-compat with un-migrated collections.
- **n8n research workflow** (`searxng-research-workflow.json`) now POSTs to `${LITELLM_BASE_URL}/v1/chat/completions` with `Authorization: Bearer …`; response parsing handles OpenAI's `choices[].message.content` shape.
- **JupyterHub startup script and notebooks** rewritten to expose `LITELLM_BASE_URL` / `LITELLM_API_KEY` and `OPENAI_API_BASE` / `OPENAI_API_KEY` (so the `openai` Python SDK and LangChain OpenAI clients work unchanged). `01_ollama_basics.ipynb` renamed to `01_litellm_basics.ipynb`. The deeper `ollama.chat` / `ChatOllama` examples in `01_litellm_basics.ipynb` and `02_langchain_rag.ipynb` still need a content rewrite to use `OpenAI()` / `ChatOpenAI()` clients (env vars are correct, code samples need a follow-up pass).
- **`check-compose-source-deps.py`** gained 12 new `REQUIRED_DEPENDS_ON` tuples enforcing that every LLM consumer hard-depends on `litellm`. Ollama remains in `FORBIDDEN_OPTIONAL_DEPENDS_ON` (still source-replaceable).
- **Migration note for existing users**: bump your `.env` by either copying the new `.env.example` or running `./start.sh --cold`. `OLLAMA_ENDPOINT` is gone; `LLM_PROVIDER_PORT` becomes `LITELLM_PORT` (same value).
- **OpenClaw AI Agent**: AI agent for messaging platforms (WhatsApp, Telegram, Discord, etc.)
  - Connects to messaging apps for AI-powered chat, file management, and task automation
  - Web dashboard for administration at `openclaw.localhost`
  - LLM integration: inherits stack's Ollama endpoint, supports Anthropic/OpenAI API keys
  - SOURCE options: `container`, `localhost` (Node.js 22+), `disabled` (default)
  - CLI option: `--openclaw-source [container|localhost|disabled]`
  - Default ports: 63024 (gateway, offset +24), 63025 (bridge, offset +25)
  - Kong routing via `openclaw.localhost` subdomain
- **JupyterHub Data Science IDE**: Interactive Jupyter Lab environment with pre-configured AI/ML libraries
  - 7 sample notebooks demonstrating all service integrations (Ollama, Weaviate, Neo4j, Supabase, ComfyUI, n8n, SearxNG)
  - Pre-installed libraries: Ollama, LangChain, LlamaIndex, Transformers, Weaviate client, Neo4j driver, and more
  - Kong routing support via `jupyter.localhost` domain
  - Persistent workspace with Docker volumes (`jupyterhub-data`)
  - Adaptive service that auto-configures based on available AI services
  - CLI option: `--jupyterhub-source [container|disabled]`
  - Default port: 63048 (offset +48 from base port)
  - Environment check notebook for service connectivity verification
- **Textual-based bootstrapper TUI**: A single Textual app (`bootstrapper/ui/textual/`) now owns the entire interactive experience — wizard prompts, the CLI-flag launch screen, the pre-launch pipeline (apply overrides → validate → ports → kong → supabase keys → hosts → encryption → localhost), and the live `docker compose` build / up / verify / `logs -f` stream — all rendered in one screen with a pinned info-box, a service overview, and a bordered log pane with filter chips. Press `ctrl+q` to detach (the stack keeps running). `--no-tui` falls back to a linear stdout flow for CI / non-TTY shells.
- **Brand customization via `BRAND_*` env vars**: The wizard's brand panel and info-box title / subtitle metadata (brand name, tagline, version, author, author email, license, repo URL) is overridable via `BRAND_NAME`, `BRAND_TAGLINE`, `BRAND_VERSION`, `BRAND_AUTHOR`, `BRAND_AUTHOR_EMAIL`, `BRAND_LICENSE`, `BRAND_REPO_URL` in `.env`. Defaults are Atlas; forks can rebrand without code changes.
- **Always-on Supabase services in the bootstrapper overview**: `Supabase Auth`, `Supabase API`, `Supabase Realtime`, `Supabase Storage`, and `Supabase Meta` are now surfaced as rows in both the Textual `ServiceTable` and the `--no-tui` summary table, alongside Supabase DB and Studio.
- **`scripts/check-compose-source-deps.py`**: Preventative linter that verifies `docker-compose.yml` does not declare hard `depends_on` edges from any service to a SOURCE-replaceable provider, and that core `depends_on` edges are still in place.
- **`scripts/check-kong-routes.py`**: Preventative linter that verifies the Kong route generator (`bootstrapper/utils/kong_config_generator.py`) produces the documented default routes for `comfyui.localhost`, `n8n.localhost`, `search.localhost`, `jupyter.localhost`, `api.localhost`, and `chat.localhost`. (Initially validated a checked-in Kong fallback file; rewritten later in this same release to invoke the generator against `.env.example` in a tmp dir — see the matching entry under `### Changed`. Both entries describe the same checker; the file is now generated-only.)
- **`docs/deployment/ports-and-routes.md`**: Canonical reference for `BASE_PORT` math, every service's direct localhost URL, and Kong host routes.
- **Per-service documentation expansion** under `services/`: `backend.md`, `comfyui.md`, `local-deep-researcher.md`, `multi2vec-clip.md`, `n8n.md`, `ollama.md`, `open-webui.md`, `redis.md`, `searxng.md`, `weaviate.md` now have their own pages alongside the existing in-depth docs.
- **ROADMAP additions**: Tier 1 — unified LLM gateway (LiteLLM, or equivalent) and per-service configuration modularization. Tier 2 — Hermes Agent (Nous Research's programmable agent runtime, with Open WebUI integration link).
- New documentation structure under `/docs/`, ROADMAP.md, and this CHANGELOG.

### Changed
- **Loosened `depends_on` edges for SOURCE-replaceable providers**: `n8n`, `n8n-worker`, and `jupyterhub` no longer hard-depend on `weaviate` (`jupyterhub` also no longer hard-depends on `ollama` or `neo4j-graph-db`); `weaviate` no longer hard-depends on `multi2vec-clip`. `n8n` / `n8n-worker` / `jupyterhub` now depend on `supabase-db-init` instead of `supabase-db`. Optional consumers use `WEAVIATE_URL` (and equivalent endpoint env vars) plus runtime readiness checks instead of static compose dependencies — the stack still starts when those providers are disabled, localhost-backed, or externalized.
- **Weaviate module configuration now `.env`-driven**: `WEAVIATE_ENABLE_MODULES` and `CLIP_INFERENCE_API` are exposed in `.env.example` and consumed by the Weaviate compose service. Disabling the CLIP provider no longer requires editing `docker-compose.yml` — set `MULTI2VEC_CLIP_SOURCE=disabled`, drop `multi2vec-clip` from `WEAVIATE_ENABLE_MODULES`, and clear `CLIP_INFERENCE_API`.
- **Service-definition consolidation**: `bootstrapper/ui/state_builder.all_services()` is the single source of truth for the canonical service list, consumed by both the Textual `ServiceTable` and the `--no-tui` summary table. No duplicated inline service tables.
- **Single `DEFAULT_BASE_PORT`**: Lives in `bootstrapper/core/config_parser.py`; `start.py` and the wizard import the same constant.
- **README.md restructuring** for better usability and new documentation organization / navigation.
- **Architecture diagrams updated** to include JupyterHub and other recently added services.

### Removed
- **Legacy Rich-based bootstrapper UI** (the Rich `Live` + `readchar` wizard, the `Textual` post-wizard log app, and all of their supporting modules): `bootstrapper/ui/presentation_app.py`, `bootstrapper/ui/log_stream_app.py`, `bootstrapper/ui/select_widget.py`, `bootstrapper/ui/number_widget.py`, `bootstrapper/ui/status_ribbon.py`, `bootstrapper/ui/log_pane.py`, `bootstrapper/ui/info_box.py`, `bootstrapper/ui/palette.py`, `bootstrapper/ui/logo.py`, and `bootstrapper/wizard/tui_wizard.py`. The `ATLAS_USE_LEGACY_WIZARD=1` env-var fallback that briefly let users opt back into the Rich Live wizard during the migration is also gone.
- **Earlier obsolete bootstrapper modules folded into the wizard rebuild**: `wizard/interactive_wizard.py`, `wizard/prompts.py`, `wizard/ui_renderer.py`, `utils/scroll_pin.py`, `utils/ansi_filter.py`, `ui/services_poller.py`, `ui/confirm_widget.py`. Pruned dead methods (`up_with_build`, `set_service_state`, `apply_service_snapshot`, `clear_status`, `prompt_confirm`), dead palette helpers (`style_for_service_state`, `dot_for_service_state`, `DOT_STARTING`, `DOT_OFF`, `DOT_UNHEALTHY`, `COLOR_STARTING`), and unused state constants / `ServiceEntry` fields (`SERVICE_STATE_*`, `GROUP_*`, `CATEGORY_*`, `state`, `group`, `category`, `is_default_source`, `endpoints`).

### Fixed
- **Kong route generator now honors `COMFYUI_LOCALHOST_URL`**: `bootstrapper/utils/kong_config_generator.py` previously hardcoded `http://host.docker.internal:8000/` for the `comfyui-api` route under `COMFYUI_SOURCE=localhost`, ignoring any `.env` override. It now parses `COMFYUI_LOCALHOST_URL` and uses its host:port for both the Kong service URL and the localhost reachability probe (matching the openclaw generator's per-service env-var pattern).
- **LiteLLM crash-loop on first launch via the TUI**: the wizard pipeline (`bootstrapper/ui/textual/screens/wizard_screen.py`) never called `generate_litellm_configuration`, so `volumes/litellm/config.yaml` was never written before `docker compose up`. Docker's bind-mount then created an empty *directory* at the source path, and the LiteLLM container died with `IsADirectoryError: '/app/config.yaml'`. The wizard's `steps` list now runs the generator right after Kong (matching the linear `start.py` flow), and `LiteLLMConfigGenerator.write_config` self-heals: if the destination already exists as an empty directory, it `rmdir`s it and writes a real file. Non-empty directories raise a clear error rather than silently no-oping.
- **Supabase keys now auto-generate on first launch without `--cold`**: `bootstrapper/start.py:validate_supabase_keys` previously generated missing JWT keys only on cold start, leaving fresh-clone users with an opaque "Missing Supabase keys" error on no-flag `./start.sh`. It now auto-generates whenever all three of `SUPABASE_JWT_SECRET` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_KEY` are blank — the fresh-clone case. Mixed state (some set, some blank) is detected and refused with a directive to run `./bootstrapper/generate_supabase_keys.sh`, since the generator HMAC-signs the anon and service keys with the JWT secret and silently rewriting all three would clobber hand-pasted values. Cold start is unaffected (it wipes `.env` first, so its keys come back via the same all-blank path).

### Dependencies
- Added `textual >= 0.85` — owns the entire wizard / launch / log-streaming experience.
- Removed `readchar` (was used by the now-deleted Rich Live prompt widgets).
- Removed `InquirerPy` (replaced earlier in this `[Unreleased]` cycle).
- Bumped `requires-python` from `>=3.8` to `>=3.10` (Textual minimum and current LTS floor; the intermediate `>=3.9` bump landed first then was tightened to `>=3.10` when the dependency upgrade pass below required it).

## [3.0.0] - 2026-05-15 (Topology-Driven Ordering & Port Layout v1)

**Visual:** every service row in the setup wizard now leads with a thin category-color bar; six categories (Infra, Data, LLM Core, Media, Agents & Workflows, Apps & UIs) explained in a legend below the grid. Unanswered configurable services show a yellow ◌ placeholder ("pending") instead of guessing their port/source/alias before you've picked them.

**Ordering:** display order — and the wizard's question sequence — is now derived from each `service.yml`'s `depends_on:` and `category:` fields. The hand-edited `services/_order.yml` has been retired.

**Port renumbering:** default ports are computed from a per-category slot allocator, not hand-edited per manifest. On first start after this upgrade, your existing `.env` is auto-rewritten with the new defaults (a backup is taken to `.env.backup.<timestamp>`). User-customized port values (i.e., not matching the old default) are preserved untouched. Pass `--no-port-migrate` if you want to opt out of the rewrite.

To roll back: `cp .env.backup.<timestamp> .env && sed -i '' '/BOOTSTRAPPER_PORT_LAYOUT_VERSION/d' .env` (or simply delete the sentinel line so the migration re-applies on next start).

**Aliases:** eight new `*.localhost` aliases — studio, graph, weaviate, ollama, stt, tts, docling, research. Total alias count goes from 10 to 18. Run `--setup-hosts` to add them to `/etc/hosts`. Each alias works in both container and host-install (`-localhost`) modes — Kong proxies through `host.docker.internal` to the user's host port when the source is `-localhost` (Kong's compose now declares `extra_hosts: ["host.docker.internal:${HOST_GATEWAY_IP}"]` so this works on Linux Docker too). `*-external` sources don't get a Kong route — LiteLLM forwards those itself.

**Internals:** eight scattered metadata constants across `bootstrapper/` (`_SERVICES`, `_HOST_ALIAS`, `DISPLAY_NAME_OVERRIDES`, `SERVICE_DESCRIPTIONS`, `LOCKED_SERVICES`, `LOCALHOST_ENDPOINT_VARS`, `GENAI_HOSTS`, `services/_order.yml`) have collapsed into manifest fields. Adding a new service is now a one-folder operation.

## [2.0.0] - 2025-08-31 (Python Migration & Modular Architecture)

### Added

#### Python migration
- **Cross-platform Python bootstrapper**: Complete migration from Bash to Python for start/stop scripts
- **UV package manager support**: Automatic detection and use of UV for better dependency management
- **Enhanced error handling**: Better error messages and recovery mechanisms
- **Consistent behavior**: Same functionality across Windows, macOS, and Linux

#### Dynamic Kong configuration
- **Intelligent routing**: Kong routes dynamically generated based on SOURCE values
- **Health checking**: Automatic localhost service availability checking
- **Adaptive configuration**: Routes automatically removed for disabled services
- **No manual configuration**: Replaced static kong.yml/kong-local.yml files

#### CLI SOURCE overrides
- **Command-line configuration**: Override .env settings via CLI arguments
- **Temporary sessions**: CLI overrides don't modify .env file
- **All SOURCE types supported**: Complete CLI coverage for all service sources
- **Usage examples**: CLI documentation with common patterns

#### Enhanced service management
- **ComfyUI-init for all sources**: Model downloading for both container and localhost setups
- **Better dependency resolution**: Automatic service dependency management
- **Improved startup order**: Cold start cleanup moved to proper execution phase

### Changed

#### Project structure
- **Reorganized bootstrapper**: New `bootstrapper/` directory with Python modules
- **Service utilities**: `bootstrapper/utils/kong_config_generator.py` for dynamic configuration
- **Moved scripts**: `generate_supabase_keys.sh` relocated to `bootstrapper/`
- **Modular architecture**: Clear separation of concerns in codebase

#### Kong gateway
- **Dynamic route generation**: Routes created based on active services
- **SOURCE-aware**: Different routing strategies for container/localhost/external sources
- **WebSocket support**: Proper WebSocket routing for realtime services
- **Authentication handling**: Dynamic auth configuration per service

#### Service configuration
- **SOURCE system refinement**: Clear documentation of which services support localhost
- **Localhost support clarification**: Only Ollama, ComfyUI, and Weaviate support localhost SOURCE
- **Container-only services**: N8N, SearxNG, Open WebUI, Backend API are container-only
- **External URL support**: Proper handling of external service configurations

### Fixed

#### Startup issues
- **Cold start port conflicts**: Fixed cleanup order to occur before port checking
- **Service initialization**: ComfyUI-init now runs for localhost ComfyUI setups
- **Port management**: Better handling of port conflicts and base port configuration

#### Integration issues
- **Kong routing**: Fixed localhost service routing through Kong gateway
- **Service discovery**: Proper health checking for localhost services
- **Cross-service communication**: Improved service-to-service connectivity

#### Documentation
- **Corrected SOURCE support**: Fixed incorrect localhost support claims
- **Updated examples**: All examples reflect new dynamic configuration approach
- **Consistent terminology**: Standardized language throughout documentation

### Removed

#### Obsolete files
- **Static Kong configuration**: Removed `volumes/api/kong.yml` and `volumes/api/kong-local.yml`
- **Dual configuration approach**: Eliminated the "relic" dual Kong config system
- **Manual route configuration**: Removed need for manual Kong route management

#### Cleanup
- **Unnecessary Kong routes**: Removed routes for Weaviate and Neo4j (not user-facing)
- **Duplicate documentation**: Consolidated multiple sections about same services
- **Outdated references**: Removed references to legacy Bash-only approach

## [1.5.0] - 2025-07-29 (Service Integration & Workflow Enhancement)

### Added

#### n8n workflow automation
- **Complete n8n integration**: Workflow automation with queue management
- **Redis queue backend**: Distributed task processing with n8n-worker
- **Pre-built workflows**: Ready-to-use AI workflow templates
- **Kong gateway routing**: Access via n8n.localhost subdomain

#### ComfyUI image generation
- **Full ComfyUI integration**: AI image generation with workflow support
- **Multiple deployment options**: Container CPU/GPU and localhost support
- **Model management**: Automatic model downloading and caching
- **API integration**: REST API access and workflow execution

#### SearxNG privacy search
- **Privacy-focused search**: Local search aggregation without tracking
- **Multiple search engines**: Aggregated results from various sources
- **API access**: Programmatic search capabilities for AI workflows
- **Rate limiting**: Built-in protection against abuse

#### Open WebUI enhancement
- **Research tools integration**: AI-powered research capabilities
- **ComfyUI tool integration**: Direct image generation from chat
- **Multi-LLM support**: Support for various LLM providers
- **Custom tool development**: Framework for adding new AI tools

### Changed

#### Architecture improvements
- **Service modularity**: Better separation between services
- **Docker network optimization**: Improved inter-service communication
- **Volume management**: More efficient data persistence
- **Resource allocation**: Better memory and CPU management

#### Configuration enhancement
- **Environment-based scaling**: Services scale based on SOURCE configuration
- **Dependency management**: Automatic service dependency resolution
- **Health monitoring**: Better service health checking and recovery

### Fixed

#### Bug fixes
- **Service startup order**: Fixed dependency-based startup sequencing
- **Memory management**: Resolved OOM issues with large models
- **Network connectivity**: Fixed inter-service communication issues
- **Volume permissions**: Resolved file permission problems

## [1.0.0] - 2025-04-26 (Initial Release)

### Added

#### Core foundation
- **Supabase ecosystem**: Complete database, auth, and storage solution
- **Kong API Gateway**: Centralized API management and routing
- **Ollama integration**: Local LLM inference with CPU/GPU support
- **Docker Compose architecture**: Complete containerized environment

#### Database services
- **PostgreSQL**: Primary database with Supabase extensions
- **Neo4j**: Graph database for relationship modeling
- **Redis**: Caching and session management
- **Real-time subscriptions**: WebSocket-based live data updates

#### Authentication and security
- **Supabase Auth**: Complete authentication system
- **JWT token management**: Secure API access tokens
- **Role-based access**: User roles and permissions
- **API key authentication**: Service-to-service security

#### Development tools
- **Supabase Studio**: Database management interface
- **Environment configuration**: Flexible .env-based setup
- **Docker orchestration**: Multi-service container management
- **Development scripts**: Easy start/stop scripts

### Infrastructure

#### Container architecture
- **Service isolation**: Each component in dedicated container
- **Network segmentation**: Proper Docker networking
- **Volume persistence**: Data persistence across restarts
- **Resource management**: Memory and CPU optimization

#### Configuration management
- **Environment variables**: Centralized configuration
- **Service discovery**: Automatic service registration
- **Port management**: Configurable port assignments
- **Cross-platform support**: Works on macOS, Linux, and Windows

---

## Migration Guide

### From 1.x to 2.0 (Python Migration)

**Required Actions:**
1. **Update start/stop usage**: New CLI arguments available
2. **Check SOURCE configurations**: Verify localhost support for your services
3. **Update hosts file**: Run `./start.sh --setup-hosts` for *.localhost domains
4. **Review Kong routes**: Routes now generated dynamically

**Optional Improvements:**
- Install UV package manager for better dependency management
- Use new CLI SOURCE overrides for easier configuration
- Leverage new troubleshooting documentation

**Breaking Changes:**
- Static `kong.yml` files no longer used (automatically migrated)
- Some services no longer support localhost SOURCE (see documentation)
- `generate_supabase_keys.sh` moved to `bootstrapper/` directory

### Compatibility Notes

- **Environment files**: Existing `.env` files remain compatible
- **Data volumes**: All data preserved across updates
- **Service APIs**: No changes to service endpoints or functionality
- **Docker images**: Updated but backward compatible

---

## Acknowledgments

### Contributors
- Core development team
- Community contributors
- Beta testers and early adopters

### Special Thanks
- Open source projects that make this stack possible
- Community feedback and feature requests
- Documentation contributors and reviewers

---

*For more details on any release, see the corresponding [GitHub release](https://github.com/thekaveh/atlas/releases) or [documentation](https://github.com/thekaveh/atlas/blob/main/docs/README.md).*
