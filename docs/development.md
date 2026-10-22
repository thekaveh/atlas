# 9.1. Development

For setup, a first test and the pull-request workflow, start with the [contributing guide](../CONTRIBUTING.md). This page covers service admission, consumer layout, documentation checks, repository layout, review tickets and an optional local authorization evaluation.

## 1. Service Admission

To add a service, follow [Adding a service](CONTRIBUTING-services.md): manifest, compose fragment when applicable, topology row, docs regeneration, route checks and CI validation.

## 2. Parent-Repo Consumer Layout

This section summarizes the layout for contributors. The operator guides are [Reusing Atlas](operations/reusing-atlas.md) and [Submodule Usage](operations/submodule-usage.md).

Submodule consumers keep project-owned overlays, branding, wrapper scripts and secret references in the parent repository. `infra/` stays a pinned Atlas checkout. The recommended shape is:

- `atlas.consumer.yml` in the parent repository.
- `compose/<name>-overlay.yml` in the parent repository and referenced from `compose_overlays`.
- `backend/plugins/` (each package optionally declaring a typed `plugin.yml`) and model sidecars referenced from the manifest when needed.
- `scripts/start-infra.sh` as the parent-owned launcher that force-sets `PROJECT_NAME`, `BRAND_*`, and required `*_SOURCE` values.

Use `./infra/start.sh --consumer ./atlas.consumer.yml`. Atlas then validates
paths, merges env values, includes external Compose overlays without symlinks,
and lists registered consumers in the launch overview.

Do not rely on "set only if absent" helpers for critical `*_SOURCE` keys.
Atlas's `.env.example` contains defaults on purpose. Force-set required values
in the manifest or env overlay, or pass explicit `--<service>-source` flags.
Explicit source flags override `--track`. Use them to add a service outside a
track, or to disable a service the track would prompt for.

Existing integrations on the back-compatible `_user` discovery slot can keep
`scripts/setup-overlay.sh`. That idempotent wrapper creates
`infra/services/_user/<name>/compose.yml` before start. New integrations should
use the manifest.

Parent-owned object-storage consumers declare a `storage:` block in `atlas.consumer.yml`. Atlas compiles it, generates scoped credentials once and writes the `minio-init` overlay. It exports stable per-store `ATLAS_STORE_<KEY>_*` fields: internal and public-read endpoints, region and credential references.

The block compiles to `MINIO_EXTRA_CONSUMERS`, for example `daydreams:MINIO_BUCKET_DAYDREAMS:MINIO_DAYDREAMS_ACCESS_KEY:MINIO_DAYDREAMS_SECRET_KEY`. `_user` overlays can still set it directly. The hook creates the extra bucket and a scoped MinIO service account without forking Atlas. Presign browser GETs against the **public** endpoint, and never rewrite a signed URL. Use boto3 `endpoint_url=<public>` or the reference presigner `bootstrapper/utils/s3_presign.py`.

Before you commit a parent consumer update, verify three things:

- `infra/` is clean except for ignored `.env`, `.env.user`, `_user` slots and runtime volumes.
- The parent pins a specific Atlas commit or tag.
- Overlays stay parent-owned.

## 3. Required Docs Checks

Pull-request titles must be Conventional Commits subjects (`type(scope)!: summary`). The generated block at the top of the changelog's Unreleased section must match its recorded range. The `lint` job (*Bootstrapper and Backend suites (with containers)*, required through the *Manifest lint + unit tests* gate) runs `scripts/release_notes.py --check-title` and `--check-changelog`. See [Releasing](operations/releasing.md) §6.

`make docs-check` also enforces the critical-page contract in `docs/critical-pages.yaml`. It names the security, prerequisites, support, release and recovery pages. Each must be in the manifest and reachable from the documentation map. Each listed section must render with a body on the repo, site and wiki surfaces. The contract names pages by manifest id and sections by stable title; it never copies policy prose.

The contract's `external_references` list records community destinations, such as the issue tracker, that a page may link. They do not replace a self-contained page.

`bootstrapper/tests/test_support_route.py` keeps every advertised first-party support destination live. Reader-facing pages may not link GitHub Discussions while the feature is disabled on the repository. The README, documentation map and troubleshooting guide must route bugs, questions and security reports separately. A maintainer who enables and moderates Discussions removes that guard in the same change that relinks it.

`scripts/check_doc_links.py` checks targets against the repository tree. It covers relative Markdown links (including empty-label links and reference-style definitions) and raw HTML `<a href>` / `<img src>`. Canonical pages must therefore link files GitHub can open (`quick-start/index.md`, never the site's `quick-start/`). The generated surfaces translate those targets themselves.

```bash
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
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

The Material `privacy` plugin, enabled by `scripts/docs/build_docs.py`, self-hosts the theme fonts and the Mermaid bundle. It downloads them during `mkdocs build` and caches them in `.cache/plugin/privacy` (gitignored). The built pages make no third-party requests.

`docs/external-assets.yaml` lists all 20 assets: the Google Fonts stylesheet, 18 versioned font files and `mermaid@11` from unpkg. Each entry has its URL and, when the URL pins the content, a sha256.

**Network policy.** The first build from a clean checkout needs `fonts.googleapis.com`, `fonts.gstatic.com` and `unpkg.com`. Later builds use the cache and fetch nothing. CI caches the same directory, keyed on `scripts/docs/build_docs.py`, the stylesheets and the inventory.

- `make docs-build`, `make docs-check` and `make docs-serve` run `python -m scripts.docs.external_assets --preflight` before MkDocs. If a missing asset cannot be fetched, the build stops and names the asset and its URL.
- `make docs-assets-verify` compares the cache with the inventory. It reports missing, unexpected and checksum-mismatched files.
- After you change the theme fonts or upgrade mkdocs-material, delete `.cache/plugin/privacy` and rebuild. Then run `uv run --project bootstrapper python -m scripts.docs.external_assets --print-inventory`. Review the difference against `docs/external-assets.yaml` before you commit it.

**Mermaid floats.** mkdocs-material's JavaScript requests `https://unpkg.com/mermaid@11/dist/mermaid.min.js`. unpkg serves the newest 11.x release for that URL, so its inventory entry has `sha256: null`. A test requires a sha256 for every URL that names an exact version, and for no other URL. If mkdocs-material pins an exact Mermaid version, pin the inventory entry too.

The assets are not vendored, and the cache is not seeded. Vendoring adds about 3.8 MB of binaries. The cache paths are internal to the plugin and can change with any mkdocs-material release.

### 3.2. Heading numbers and symbols

`scripts/check-docs-drift.py` checks every tracked Markdown file except `AGENTS.md`, `.agents/` and `bootstrapper/tests/fixtures/`. Two rules apply outside fenced code blocks:

- **Numbered headings.** Every `##` to `######` heading carries its hierarchical number, for example `## 1.` and `### 1.1.`. This includes the CHANGELOG, the ROADMAP, provider notes under `services/*/provider/` and research one-pagers. A research one-pager keeps its schema field name after the number, for example `## 1. Headline`.
- **No decorative symbols.** The gate rejects the status and tree glyphs listed in `_DECORATIVE_SYMBOLS` in `scripts/docs/heading_quality.py`. Write words such as `Warning:` instead. Put tree diagrams and literal terminal output in fenced code blocks.

To fix the numbers in every tracked file, run `python scripts/number-markdown-headings.py`. It rewrites files in place.

### 3.3. Prose length

The `prose_length` probe of `scripts/check-docs-drift.py` applies STE length limits. It checks the README and every hand-written page in `docs/manifest.yaml` except the CHANGELOG and the ROADMAP:

- A sentence has at most 25 words. A numbered step has at most 20 words.
- A paragraph, list item or table cell has at most 75 words and 6 sentences.
- In `services/*/service.yml`, an env `description` has at most 40 words and no `#NNN` issue number. Each sentence of a capability `note` has at most 25 words.

`.docs-prose-baseline.json` records the current violation count of each file. A count must not go up. When your change lowers a count, run `python scripts/check-docs-drift.py --write-prose-baseline` and commit the new baseline. The command refuses to raise a count unless you add `--allow-prose-increase`. A paragraph that contains `<!-- lint-ok -->` is exempt.

### 3.4. Refresh local previews

Canonical repository sources are authoritative. `generated/`, `site/` and `mkdocs.yml` are disposable outputs.

From the repository root, refresh before opening generated output:

```bash
make docs-build
make docs-wiki
make docs-check
```

This sequence refreshes manifest-derived canonical references, builds the site, exports the wiki and checks local links.

For an interactive site preview, run `make docs-serve` after the refresh. That target regenerates site input but does not refresh manifest-derived canonical references.

## 4. Repository layout

The top-level repository layout is as follows, with `services/` limited to a representative subset (see `services/` for the full list):

```
atlas/
├── bootstrapper/              # Python startup, SOURCE parsing, port/Kong generation, wizard
│   ├── services/              # Manifest loader, validator, env_assembler, hooks, sc_synthesizer
│   ├── schemas/               # JSON Schemas for service.yml manifests
│   ├── tests/                 # 7,000+ tests (loader, validator, byte-equiv, source-permutation, hooks)
│   ├── tools/                 # validate_fragments manifest lint, generate_readme_topology
│   └── start.py / stop.py     # Entry points
├── services/                  # 58 service.yml manifests + 3 doc-only folders (representative subset shown below; see services/ for the full list)
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
│   ├── redis/                 # Redis cache/queue substrate (AOF persistence; used by LiteLLM, n8n, Open WebUI, LightRAG, Backend and others; not Kong)
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
├── examples/                  # Optional recipes, outside default runtime images
├── scripts/                   # Audit, docs-build, lock and release-notes tooling
├── docker-compose.yml         # ~90-line thin shell — include: list pulling each fragment
├── .env.example               # Configuration template (auto-generated from manifests via env_assembler; byte-equivalence enforced by tests)
├── start.sh / stop.sh         # Entry points
└── .github/workflows/         # CI: services-lint (manifest lint+tests, compose byte-equiv+source-permutation, docs-drift+audits, build-validation)
```

Top-level code and tooling live in `bootstrapper/`, `scripts/` and `services/`; documentation lives in `docs/`. Optional recipes live in `examples/`, outside default runtime images. Each service owns its init scripts, source, build context and configuration under `services/<name>/`.

## 5. Machine-generated review tickets

A review run files tickets that carry an `<!-- atlas-review:DATE:ID -->` marker. Each one must meet a minimum bar, so the next person can check its claims without redoing the review.

The canonical sections are **Summary**, **Context**, **Acceptance criteria** and **Evidence**. **Context** holds verified, cited facts in at most 80 words. Each acceptance criterion is checkable and states its check. **Evidence** is a `Location | What it shows` table. **Scope** and **Dependencies** appear only when they have something to say.

`scripts/lint_review_ticket.py` checks a body read on stdin:

| Rule | Checks | Blocks |
|---|---|---|
| R1 | Every evidence location names a line (`#L<n>` or `path:line`), or its caption starts `whole-file:` and says why | yes |
| R3 | Any `F<n>` or `FIX-<n>` in prose, other than the ticket's own ID, has an issue link right next to it | yes |
| R2 | No generic evidence caption; each says what its location shows | no |
| R4, R5 | No "none identified" boilerplate, and no section that restates the specification | no |
| R6 | A `Basis: proposal` ticket is not titled `fix(` | no |
| R7, R8 | No criterion that points at an undefined "documented" artifact or names nothing checkable | no |
| R9 | Plain words, no filler, and the length budgets (Context 80 words, ticket 400, sentences 25 on average and 40 at most) | no |
| E1 | With `--repo-root`, each evidence link still resolves (the file exists and the line is inside it) | no |

Only R1 and R3 block, because they make a ticket unworkable: a reader cannot follow its evidence, or its references lead nowhere. The rest are warnings.

```bash
uv run --project bootstrapper python scripts/lint_review_ticket.py --title "fix(scope): summary" < body.md
```

A review run pipes each drafted body through it before filing. On GitHub, `.github/workflows/review-ticket-lint.yml` runs it whenever a marked issue is opened or edited. A blocking result gets one comment listing the violations and the `review-quality:needs-revision` label. Fixing the body removes the label and updates the same comment.

## 6. Optional local authorization evaluation

The optional recipe in `examples/promptfoo-security/` exercises the Backend's ownership primitive with four synthetic cases. It uses Promptfoo 0.124.1 without a model, running Atlas stack or viewer. Fixture response text has no authority; the report compares it with actual authorization decisions and appended events.

### 6.1. Supported setup

Use macOS ARM64, Node ≥22.22.0 with npm, Python 3.12 and `uv`. The qualifier also requires `/usr/bin/sandbox-exec` and `/usr/bin/otool`. The walkthrough was qualified with Node 26.12.0 and Homebrew Python 3.12.8. The qualifier checks the actual selected runtimes and refuses execution when its containment checks fail. Other operating systems have no supported launcher in this recipe.

Dependencies stay outside Atlas runtime images and environments. The npm lock includes platform-specific optional bindings needed for the CLI's startup database. The qualified macOS installation contained 613 packages and occupied approximately 1.14 GB. Allow extra space for the package cache and Python environment.

Acquire dependencies before creating fixture outputs. Run from the Atlas repository root:

```bash
RECIPE="$(pwd)/examples/promptfoo-security"
RECIPE_WORK="$(mktemp -d "${TMPDIR:-/tmp}/atlas-promptfoo.XXXXXXXX")"
mkdir "$RECIPE_WORK/npm" "$RECIPE_WORK/home" "$RECIPE_WORK/tmp"
cp "$RECIPE/package.json" "$RECIPE/package-lock.json" "$RECIPE_WORK/npm/"
: > "$RECIPE_WORK/empty-npmrc"
env -i PATH="$PATH" HOME="$RECIPE_WORK/home" TMPDIR="$RECIPE_WORK/tmp" \
  npm ci --prefix "$RECIPE_WORK/npm" --ignore-scripts --omit=dev \
  --include=optional --no-audit --no-fund \
  --userconfig /dev/null --globalconfig "$RECIPE_WORK/empty-npmrc"
python3.12 -I -S -B -m venv --without-pip "$RECIPE_WORK/auth"
env -i PATH="$PATH" HOME="$RECIPE_WORK/home" TMPDIR="$RECIPE_WORK/tmp" \
  UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never \
  uv pip install --python "$RECIPE_WORK/auth/bin/python" \
  --require-hashes --only-binary :all: --no-deps \
  -r "$RECIPE/requirements-auth-macos-arm64-py312.lock"
```

These installation commands contact public package registries. npm lifecycle scripts and Python source builds are disabled. The Python lock selects only the authorization module's eleven dependencies; it does not install the full Backend.

### 6.2. Qualify and run

The qualifier creates a new private output directory and a separate generated-input directory. It discovers runtime library paths and creates a local Seatbelt profile for those selected paths. Keep both directories until inspecting the report.

```bash
python3.12 -I -B "$RECIPE/qualify_macos.py" \
  --node "$(command -v node)" \
  --python "$RECIPE_WORK/auth/bin/python" \
  --install-root "$RECIPE_WORK/npm" \
  --output-base "$RECIPE_WORK/run"
```

Qualification first proves that disposable loopback TCP, UDP and DNS targets are reachable. It then requires the same operations to fail inside containment, including child processes. It also checks Python pipes, the unchanged Backend module import and contained CLI startup. No external resolver, provider or real credentials are used.

The command prints the generated bindings path. Pass that exact path to the runner:

```bash
python3.12 -I -B "$RECIPE/run_local.py" \
  "$RECIPE_WORK/run.inputs/bindings.json"
```

The runner verifies the qualified profile, inputs and dependency inventories before running either evaluation. It uses the same private write boundary and denies network access. There is no unsandboxed fallback. A changed runtime, failed qualification or provider error stops the walkthrough. Start with a fresh private workspace after a failed run. Keep the failed workspace for diagnosis; do not reset its budget or reuse its evaluation directory.

### 6.3. Read the results

| Fixture | Actual ownership outcome | Appended events |
|---|---|---|
| Alice requests Alice's event with refusal text | Allowed for Alice | 1 |
| Alice requests Bob's event with hostile text | Denied | 0 |
| A trusted service delegates Bob's event | Allowed for Bob | 1 |
| A deliberately vulnerable fixture bypasses ownership | Allowed for Bob; assertion fails | 1 |

The vulnerable branch exists only in the synthetic helper. It changes no Backend policy. Its expected result remains denial with zero events, so the observed bypass must produce an actual failed CLI assertion.

The protected evaluation must exit 0 with three passes. The negative control must exit 100 with one specific assertion failure and no provider errors. The wrapper succeeds only after validating those results, four unique admissions and the independently written event and authorization files.

Results live under `$RECIPE_WORK/run/evaluation/`. Its `results/` directory contains the report, raw CLI JSON, logs and bounded helper diagnostics. The sibling `effects.jsonl` and `authorization.jsonl` retain the separate physical event and decision audits. The report pairs each deterministic response text with its authorization decision, resolved owner and event count. Local success code 200 is a fixture label; denial code 403 comes from the actual ownership exception. Neither represents an HTTP request.

Across both evaluations, a shared counter admits at most four unique cases before authorization. Retries, duplicates and a fifth attempt fail the walkthrough. CLI concurrency is one, with bounded deadlines, output and local storage checks. These checks are not an operating-system memory or disk quota.

### 6.4. Limits and retention

This exercise does not establish model robustness, bearer authentication or per-consumer MCP isolation. It demonstrates the real ownership primitive and an intentionally failing authorization control. Model refusal text is fixed test data, not a generated safety guarantee.

No viewer starts. The [community viewer](https://www.promptfoo.dev/docs/usage/self-hosting/) has no built-in authentication, SSO or team RBAC. It uses local SQLite, and upstream does not recommend production use. The CLI also initializes its private database before parsing flags; `--no-write` only suppresses later evaluation persistence.

Telemetry, update checks, sharing and remote generation are disabled. Kernel containment enforces the no-network evaluation boundary. Local results need no Langfuse, MLflow or hosted upload.

Retain the synthetic report and failed attempts for review. Remove only the exact private workspace you created when finished; preserve reports elsewhere first if needed. Never substitute broad cleanup globs or run this fixture against production identities, tools or services.
