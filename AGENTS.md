# AGENTS.md

This file provides guidance to coding agents (e.g. Codex, Claude Code) when working with code in this repository.

## Project Overview

Atlas (formerly GenAI Vanilla Stack) is a self-hosted, source-configurable engineering platform that orchestrates 30+ containerized services with Docker Compose. Its tracks (`gen-ai-eng` / `gen-ai-rag` / `gen-ai-creative` / `ml-eng` / `data-eng` / `trading` / `all`) cover generative AI, RAG, creative AI, ML engineering and data engineering.

Services include:

- LLM inference: Ollama, plus cloud-provider passthroughs through LiteLLM.
- Chat UI: Open WebUI. Workflow and DAG automation: n8n, Airflow.
- Vector and graph databases: Weaviate, Neo4j. Distributed compute: Ray, Spark.
- Notebooks: JupyterHub, Zeppelin. Object storage: MinIO. Observability: Prometheus, Grafana.

Each service can run as a container, on localhost or be disabled, with CPU/GPU variants where the service supports them.

## Editing Rules

When editing existing files, preserve all existing functionality. Never remove output statements, color settings, progress indicators, or UI elements unless explicitly asked. Before saving edits, mentally diff against the original to check for regressions.

## Workflow Conventions

After generating a report or output to a file, always display a summary or the full content to the user without being asked.

## Documentation Skills

Use the three-surface documentation skills for Atlas docs work:

- `three-surface-docs` — use when creating, fixing, or extending the synchronized in-repo docs, generated MkDocs `.io` site, and GitHub wiki pipeline. Load its `reference.md` before implementation work that changes the pipeline shape, generated surfaces, wiki publishing, MkDocs config, manifest behavior, diagram propagation, or cross-surface link rewriting.
- `three-surface-docs-audit` — use for read-only audits of documentation health. Use it before releases, after docs changes and when docs CI is red. It checks that README, repo docs, generated site and wiki stay self-contained and in sync. Present findings before making fixes.

When you create or materially change a docs architecture diagram, use the `architecture-diagram` skill for the masters. Keep the generated diagram assets in sync on every required surface.

## Code Review

When performing code audits or reviews, always present findings before making changes. Wait for user approval before implementing fixes.

## Docker / Infrastructure

Do not change Docker images, base configurations, or architectural decisions unless explicitly requested. When fixing issues, make minimal targeted changes rather than swapping out components.

## UI Development

For TUI/CLI visual work: after each change, describe exactly what changed visually. Never overhaul the entire UI when a targeted fix is requested. Preserve the user's aesthetic choices.

## Git Workflow

`main` and `develop` are protected by the `gitflow` ruleset: pull request required, no force-push, no branch deletion, and four required `services-lint` checks. Every change lands via a pull request with those checks green:

- `Manifest lint + unit tests`
- `Compose merge + byte-equivalence + source-permutation matrix`
- `Docs drift + audit scripts`
- `Build-validation (Dockerfile + requirements.txt installability)`

Build validation runs on every workflow run and is a required check in the live `gitflow` ruleset. Despite its name, it builds no local Dockerfile. It verifies the pinned remote build contexts against their reviewed base-image digests and scans changed remote images. Local Dockerfile and requirements breakage shows in `Final-image scan (local Compose and init images)`, which is not a required check.

When an upstream floating tag (`nginx:alpine`, `node:20`, `python:3.12-slim`) is republished, build validation fails on every pull request. It passes again after you refresh the reviewed digest in three places: `.container-scan-exclusions.yml`, the `test_container_security.py` fixture and the row in `docs/maintenance/external-contract-ledger.md`.

`Manifest lint + unit tests` is an aggregate gate (job `required-lint`, `if: always()`). It succeeds only when four parallel jobs all succeed:

- `lint`: the bootstrapper suite with the container-backed integration tests, manifest lint, shell lint, title and changelog checks, and the Backend suite. Container cleanup always runs.
- `unit-fast`: the whole bootstrapper suite without a Docker daemon (the early red signal).
- `python-floor`: the full suite on Python 3.10.
- `component-tests`: the MCP and asset API suites.

A new push to a pull request cancels that pull request's superseded `services-lint` run. Runs on `main` and `develop` are never cancelled.

The ruleset also gives the repository admin role an always-on bypass (`bypass_mode: always`). For an admin, these rules are the required workflow, not a mechanical guarantee. Inspect the live rule with `gh api repos/thekaveh/atlas/rulesets`.

Strict mode is enabled, so each PR branch must be up to date with that PR's target branch before merge becomes available. Conversation-resolution is required.

Gitflow integration uses two PRs:

1. Branch (typically a dedicated git worktree) → push → PR to `develop` → required checks → squash merge.
2. Cut `release/<slug>-to-main` off `origin/main` and run `git merge --no-ff origin/develop`. Prove `git diff origin/develop HEAD` is empty. Push, open a PR to `main`, wait for the required checks, and merge with a **merge commit**. This keeps `develop` an ancestor of `main`; a squash here makes the next develop→main PR conflict. Never attempt `git push origin main` or `develop`. Inspect the live rule with `gh api repos/thekaveh/atlas/rulesets`.

## Key Commands

```bash
# Start the stack (interactive wizard on first run)
./start.sh

# Start with specific service configuration (SOURCE values live in .env;
# use the CLI flag — a shell-env prefix does not configure the bootstrapper)
./start.sh --llm-provider-source ollama-container-gpu

# CLI flag form: any source, stack, model or key flag skips the whole wizard
# (--track, --profile, --project and --consumer alone still open it)
./start.sh --llm-provider-source ollama-container-gpu --comfyui-source container-gpu

# Switch base port to avoid conflicts (all service ports recompute from this)
./start.sh --base-port 64000

# Set up *.localhost hosts entries (needed for Kong wildcard routing)
./start.sh --setup-hosts

# Stop services
./stop.sh

# Stop and remove all volumes (cold start)
./stop.sh --cold

# Clean /etc/hosts entries
./stop.sh --clean-hosts
```

## Architecture

### SOURCE-Based Configuration System

The central design pattern: each service has a `*_SOURCE` env var (in `.env`) controlling its deployment mode:
- `container` / service-specific container variants — runs in Docker
- `localhost` / service-specific localhost variants — connects to a host-running instance
- `disabled` — excluded from compose
- `none` — LLM-provider mode for cloud-only operation through LiteLLM

Legacy `external` and `api` source values are retired. Cloud providers are configured with `CLOUD_*_SOURCE=enabled|disabled` plus their API keys, while `LLM_PROVIDER_SOURCE=none` disables local Ollama when using only cloud passthroughs.

**Adaptive services** (backend, open-webui) auto-configure their features based on which upstream services are enabled.

### Tracks

`bootstrapper/tracks.yml` defines named profiles (`gen-ai-rag`, `gen-ai-eng`,
`gen-ai-creative`, `ml-eng`, `data-eng`, `trading`, `all`). Each track lists a subset of
source-configurable services the wizard should prompt for; out-of-track services
are force-disabled (`*_SOURCE=disabled`) at the end of the flow.

The wizard always *prompts* for a small set on every track: LLM Engine,
Prometheus, Grafana and the cloud-provider keys. Track-skip filtering does not
apply to them. Prometheus and Grafana still ship **disabled by default**:
always-prompted does not mean always-running. The locked, always-running tier
is Supabase + Kong + Redis + LiteLLM + Backend.

- Pass `--track <key>` to pre-select on the CLI.
- Pass `--list-tracks` to print the registry and exit.
- Explicit `--<svc>-source` flags override the track with an advisory warning.

Source of truth: `bootstrapper/tracks.yml` + `bootstrapper/tracks.py` (registry
loader + predicates). The wizard step builder in
`bootstrapper/ui/textual/integration.py` consumes them via `_make_track_skip`.

### Bootstrapper (`/bootstrapper/`)

Python-based orchestration layer that:
1. Loads each `services/<name>/service.yml` manifest and synthesizes the runtime service-config dict
2. Generates dynamic Kong gateway routes
3. Manages port assignments (all derived from `BASE_PORT` in `.env`; per-category slot allocator in `services/topology.py`)
4. Builds and executes `docker compose` commands

Key modules:
- `start.py` — main entry point, `AtlasStarter` class. Routes to the Textual TUI when the terminal can host it; falls back to a linear stdout flow otherwise.
- `stop.py` — shutdown with optional volume cleanup
- `core/config_parser.py` — env parsing + manifest synthesis entry point; exports `DEFAULT_BASE_PORT` (the single source for the 63000 default base port; consumed by `start.py` and the Textual wizard). `load_yaml_config()` returns the synthesized dict by delegating to `services/manifests.py` + `services/sc_synthesizer.py`.
- `core/support_bundle.py` — the redacted, versioned support bundle (#1057) behind `./start.sh doctor --bundle PATH` and `./start.sh --support-bundle PATH`: best-effort `Redactor`, offline, time-bounded `run_checks`, allowlisted `build_bundle`, and a preview-then-write `export`. `AtlasStarter.build_support_bundle` feeds it the doctor checks and consumer env origins; the linear flow and the Textual launch screen each call it on a failed start.
- `core/docker_manager.py` — compose execution; `execute_compose_command` for the linear flow, `stream_compose` for line-by-line piping into the Textual log pane
- `services/topology.py` — single source of truth for service rows (category, deps, aliases, port defaults, display name, description). `get_topology()` accessor; `build_topology()` for tests with synthetic services dirs.
- `services/manifests.py` — loads `services/<name>/service.yml` files into `Manifest` dataclasses (env vars, source variants, runtime_sc slices).
- `services/manifest_validator.py` — schema + cross-manifest invariants (alias uniqueness, cycle detection, category-overflow, engine-orphan lints).
- `services/sc_synthesizer.py` — concatenates per-manifest `runtime_sc:` slices into the legacy service-config dict shape (source_configurable, adaptive_services, dependencies, service_dependencies).
- `services/env_assembler.py` — generates `.env.example` from manifests' env-var declarations + topology port defaults.
- `services/source_validator.py` — SOURCE validation
- `services/migrations/` — frozen, ordered env-file migrations. The chain
  currently runs v1 through v5; [`start.py::run_port_migration`](bootstrapper/start.py)
  is the source of truth for the complete sequence, gates, and current purpose
  of each version. Add a new versioned module instead of editing a migration
  that existing `.env` files may already have stamped.
- `ui/textual/integration.py` — public entry points. `run_setup_flow` runs the interactive wizard, pipeline and log streaming in one Textual app. `run_launch_flow` is CLI-flag mode: it skips the wizard and opens the launch screen with the user's overrides applied.
- `ui/textual/screens/wizard_screen.py` — `WizardScreen` hosts the wizard prompts, then transitions in-place to the launch phase (service-table + log pane + filter chips)
- `ui/textual/widgets/` — Textual widgets composed by `WizardScreen` (prompt panel, service table, info / brand panels, log pane + filter chips, command summary, footer bar)
- `ui/textual/palette.py`, `ui/textual/theme.css` — colors and Textual CSS for the app
- `ui/term_caps.py` — `is_tui_capable(no_tui_flag)` helper used by `start.py` to decide between the Textual app and the linear flow
- `wizard/model/` — the Wizard Model layer: `state.py`, `state_builder.py`,
  `service_discovery.py` and the domain rules `cloud_rules.py` and
  `llm_rules.py`. It may not import `vmx` or `textual`, at module scope or
  deferred. Both the Textual wizard and the `--no-tui` linear flow use it.
  `state_builder.all_services()` is the single source of truth for service
  definitions. Both the Textual `ServiceTable` and the `--no-tui`
  `build_pre_launch_summary_table` read it.
- `wizard/model/service_discovery.py` supplies the metadata (display name,
  description, options) that `ui/textual/integration.py` uses to build the
  wizard prompt steps. The track force-disable rule is not in the model layer:
  it lives only in `tracks.py` (`synthesize_track_source_args`), which the
  wizard calls from `_selections_to_args`.
- `wizard/viewmodel/` and `wizard/view/` do not exist yet (#535). The
  viewmodel layer will hold the VMx ViewModels and may never import `textual`.
  `wizard/view/` is where `ui/textual/*` moves, not a second copy; it may never
  import `wizard.model` directly (see
  `docs/superpowers/specs/2026-08-23-wizard-mvvm-vmx-design.md`). Until then,
  `ui/textual/` is the view, and it imports `wizard.model` directly at six
  allowlisted sites. That is deliberate, tracked debt, not a lint gap.
- `utils/kong_config_generator.py` — dynamic Kong route generation (the `kong-dynamic.yml` it emits is regenerated at every startup; do NOT edit by hand)
- `generate_supabase_keys.py` (and `.sh` sibling) — runs at startup only when all three Supabase keys are blank, generating JWT keys into `.env` (no `.env` snapshot is taken)

`bootstrapper/tests/test_wizard_layer_boundaries.py` enforces the layer
direction `view -> viewmodel -> model` as far as each layer exists:

- `wizard/model/**` must not import `vmx` or `textual`.
- The `wizard/viewmodel/` check is an explicit skip until the directory exists.
- `ui/textual/` may import `wizard.model` only at the six allowlisted sites. A
  tripwire test fails when `wizard/view/` is created, so the check moves there.
- `core/linear_startup.py` must not import `vmx`, so the `--no-tui` path is
  VMx-free by structure, not by convention.

`start.sh` and `stop.sh` are thin wrappers that prefer `uv run` and fall back to system Python. The bootstrapper can also be invoked directly with its dependencies available, e.g. `uv run --project bootstrapper python bootstrapper/start.py [flags]` (a bare system `python` lacks `click` and the other dependencies). `--no-tui` bypasses the Textual TUI and runs the linear stdout flow (used by CI, non-TTY shells, and very narrow terminals).

Dependencies are managed via `uv` (with a pip fallback) and declared in `bootstrapper/pyproject.toml`, including the Python-version markers needed at the supported Python `>=3.10` floor. Treat that file and `bootstrapper/uv.lock` as the dependency source of truth rather than duplicating the inventory here.

**Brand customization.** `BRAND_*` env vars in `.env` configure the wizard's brand panel and info box. They set brand name, tagline, version, author, author email, license and repo URL. Defaults are Atlas; forks can rebrand by setting these. See the `BRAND_*` block in `.env.example`.

### Backend (`services/backend/app/app/`)

FastAPI service at [`services/backend/app/app/main.py`](services/backend/app/app/main.py).
Its runtime integrations are declared by the Backend manifest's
[`data_flow.calls`](services/backend/service.yml), rather than duplicated here.
Runtime dependencies live in
[`requirements.txt`](services/backend/app/app/requirements.txt); the image uses
the adjacent locked requirements file through
[`services/backend/app/Dockerfile`](services/backend/app/Dockerfile).

There is no supported standalone Backend server mode; run the service through
Compose. The unit suite at
[`services/backend/app/app/tests/`](services/backend/app/app/tests/) runs locally
without a live Atlas stack by using test doubles. Its explicitly opt-in live
smokes self-skip unless their `ATLAS_*` endpoint variables are set. The Backend
suite is separate from the bootstrapper suite.

### Docker Compose (`docker-compose.yml`)

Thin ~90-line top-level shell that merges per-service compose fragments via the native `include:` directive. Each fragment under `services/<name>/compose.yml` owns its containers; cross-fragment `depends_on` and merged top-level `volumes:` work via Compose v2.20+ (v2.26+ recommended).

### Service Init Containers

Many services have dedicated init containers for first-run setup, under `services/<name>/init/` or a sibling such as `services/ollama/pull/`. They pull Ollama models, seed databases, import n8n workflows and configure Weaviate schemas.

### Per-service manifest (`services/<name>/service.yml`)

Each service family owns one manifest with:
- `containers:` — list of containers in the family
- `env:` — env vars with descriptions, defaults, port slots
- `sources:` — source variants (container, container-cpu, container-gpu, localhost, disabled, …)
- `category:` — one of `infra | data | llm | media | agents | apps` (drives wizard ordering + UI color)
- `depends_on:` — soft (display order) and required (startup ordering)
- `runtime_sc:` — per-source scale/environment/deploy/extra_hosts slice consumed by `sc_synthesizer.py`
- `runtime_adaptive:` — per-container environment and host adaptation owned by
  service manifests and synthesized by `services/sc_synthesizer.py`; current
  multi-upstream examples are [`backend`](services/backend/service.yml) and
  [`jupyterhub`](services/jupyterhub/service.yml). Inspect manifests for the
  complete current owner set instead of maintaining a parallel list here.
- `runtime_deps:` — cross-family dependency hints
- `data_flow.calls:` — runtime call graph (drives the architecture diagram + Dependencies & Integrations block)

See `bootstrapper/schemas/service.schema.json` for the full schema, `docs/CONTRIBUTING-services.md` for the walkthrough.

`bootstrapper/services/manifests.py::_is_service_dir` requires `service.yml` to exist. Two flavors of "no-container" folder live under `services/`:

- **Doc-only folders (no `service.yml`):** `services/stt-provider/`, `services/doc-processor/`, `services/multi2vec-clip/`. Host aggregate documentation + diagrams for the wizard-facing role; the manifest loader silently skips them.
- **Virtual manifests (`virtual: true`, no `compose.yml`):** `services/tts-provider/`, `services/cloud-providers/`, `services/globals/`, `services/blender-mcp/` (host-only Blender bridge), `services/fal/` (FAL cloud media provider), `services/docling-lightrag-adapter/`, `services/vllm-metal/`. They own SOURCE / env-var declarations consumed by the bootstrapper but don't run as containers. The compose include list skips virtual manifests.

### Per-service documentation (`services/<name>/README.md`)

Each `services/<name>/` is the single source of truth for that service: manifest, compose fragment, init scaffolding, README, and the two regenerated diagrams (`architecture.svg`, `architecture.html`). There is exactly one folder per service.

Service READMEs use hierarchical numbered sections (`## 1. Overview`, `## 2. Access`, `## 3. Configuration`, `## 4. Architecture & wiring`, `## N. Dependencies & Integrations`, `## N+1. Troubleshooting`, …). The Dependencies & Integrations block sits at position N in the README's section order: typically 5, but 7/9/12/14 for READMEs with extra earlier sections. The regen tool reads N from the existing heading and emits matching subsections (`### N.1` through `### N.6`).

The Dependencies & Integrations block is **auto-generated** by `bootstrapper/docs/regen.py`. It contains:
- `### N.1 Current — Upstream` and `### N.2 Current — Downstream` tables (from `data_flow.calls` in the manifests)
- `### N.3 Architecture diagram` (embeds `./architecture.svg`)
- `### N.4 / N.5 / N.6` Future-* subsections (user-authored Phase C content, preserved across regen passes by `_render_section_with_future`). One exception: an `N.5 Future — Candidate new services` bullet that links a `docs/research/candidates/<slug>.md` record whose `lifecycle` is `shipped` or `rejected` is dropped. The placeholder line replaces the block if nothing is left.

After changing a `data_flow.calls` list, re-run `PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen <service>` (or `--all`). `--all` also runs `merge_research`. That regenerates `docs/research/integration-matrix.md` and normalizes each `docs/research/candidates/*.md` front matter (sorted keys, reconciled `referenced-by`), so a candidate's lifecycle change leaves every derived index in one pass. The drift gate in `bootstrapper/tests/test_docs_drift.py` enforces that the committed READMEs/SVGs/HTMLs, the matrix and the candidate front matter all match what regen would produce. After adding or editing a research record, run `regen --all`.

## Configuration

- `.env.example` — variable template (auto-regenerated from manifests by `services/env_assembler.py`; byte-equivalence enforced by `tests/test_env_assembler.py`)
- `services/<name>/service.yml` — per-service manifest (env vars, sources, runtime slices)
- `services/topology.py` — single source of truth for service rows + port slot allocator
- Kong gateway routes are dynamically generated at startup

## Port Convention

All ports are calculated as offsets from `BASE_PORT` (default 63000). Service ports are defined in `.env.example`.

## Testing

`bootstrapper/tests/` holds more than 7,000 pytest tests. They cover manifest validation, env-example consistency, the docs-drift gate, the diagram renderer, the deps section writer, Kong config generation and bootstrapper-internal data flow. Many of the backup, restore, and database-role suites drive real containers, so a full pass needs a working Docker daemon and dominates the runtime. Run from the repo root:

```bash
uv run --project bootstrapper pytest bootstrapper/tests -q                          # full suite (~40 min)
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_drift.py          # drift gate alone
uv run --project bootstrapper pytest bootstrapper/tests/test_manifests.py -v        # single file, verbose
uv run --project bootstrapper pytest bootstrapper/tests -k weaviate                 # filter by name
```

The Backend's runtime and test dependencies are separate. Run its default,
non-live suite under Python 3.12 with both requirement sets and the tested lock
constraint:

```bash
cd services/backend/app/app
BACKEND_TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$BACKEND_TEST_ROOT"' EXIT
BACKEND_TEST_VENV="$BACKEND_TEST_ROOT/venv"
uv venv --python 3.12 "$BACKEND_TEST_VENV"
VIRTUAL_ENV="$BACKEND_TEST_VENV" uv pip install \
  -r requirements.txt -r requirements-dev.txt -c requirements-test-locked.txt
env -u ATLAS_TEST_REDIS_URL -u ATLAS_COMFYUI_LIVE_ENDPOINT \
  -u ATLAS_TEI_RERANKER_LIVE_ENDPOINT \
  "$BACKEND_TEST_VENV/bin/python" -m pytest tests/ -q -W error
```

This suite is not collected by the bootstrapper tests. Opt-in live smokes remain
skipped unless their documented `ATLAS_*` endpoint variables are configured.

### Audit scripts (`scripts/`)

Operational lint scripts that run outside pytest:

```bash
make docs-check                                                               # three-surface contracts + strict build + wiki dry run
uv run --project bootstrapper python -m scripts.notebook_reproducibility      # notebook source cleanliness
uv run --project bootstrapper python scripts/check_doc_links.py                # internal markdown link validator (incl. empty-label and [ref]: links)
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check  # docs drift gate (exit 2 on drift)
uv run --project bootstrapper python scripts/check-docs-drift.py               # docs structure audit
uv run --project bootstrapper python scripts/check-compose-source-deps.py      # compose depends_on lint (.env.example only; edges into SOURCE-replaceable families)
uv run --project bootstrapper python scripts/check-kong-routes.py              # Kong route audit (hosts + every route's paths, strip_path, preserve_host, plugins)
uv run --project bootstrapper python scripts/validate_research_schema.py --all # docs/research/ schema check
uv run --project bootstrapper python scripts/check-track-membership.py         # track coverage audit
uv run --project bootstrapper python -m scripts.docs.license_inventory --check  # supply-chain license inventory vs image/model pins
uv run --project bootstrapper python scripts/lint_review_ticket.py < body.md     # review-ticket minimum bar; exits 1 on R1/R3
(cd services/docling/provider/localhost && uv lock --locked)                   # docling localhost provider lock
uv run --project bootstrapper python scripts/refresh-local-deep-researcher-lock.py --check  # Local Deep Researcher lock
uv run --project bootstrapper python -m scripts.check_runtime_locks            # compiled service runtime locks
uv tool install pip-audit==2.10.0                                               # pinned vulnerability-audit tool
uv run --project bootstrapper python -m scripts.audit_runtime_locks            # runtime vulnerability audit
uv run --project bootstrapper python -m scripts.check_test_locks               # Backend test lock matches the runtime lock
uv run --project bootstrapper python scripts/compile_comfyui_custom_node_locks.py --check  # ComfyUI custom-node locks
uv run --project bootstrapper python scripts/check_comfyui_custom_node_overlays.py         # ComfyUI custom-node overlays
uv run --project bootstrapper python -m scripts.container_security            # container-scan policy and image inventory
```

CI also runs these gates, which are not `scripts/` audits:

```bash
(cd bootstrapper && uv run python -m tools.validate_fragments)   # "Lint manifests": every service.yml and the README TOPOLOGY block
git ls-files -z '*.sh' | xargs -0 shellcheck -x                  # shell lint over every tracked script
(cd bootstrapper && uv run pytest tests/test_fragment_equivalence.py tests/test_source_permutations.py)  # needs docker compose
```

## Linting / Type-checking

No formatter, linter, or type-checker is configured for the bootstrapper or backend. The bootstrapper's PEP 735 `[dependency-groups].dev` in `bootstrapper/pyproject.toml` owns its test and documentation-build tooling; do not introduce additional quality tools without being asked.
