# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Atlas (formerly GenAI Vanilla Stack) is a self-hosted, source-configurable engineering platform orchestrating 30+ containerized services via Docker Compose. It spans generative AI, RAG, creative AI, ML engineering, and data engineering workloads via the tracks system (`gen-ai-eng` / `gen-ai-rag` / `gen-ai-creative` / `ml-eng` / `data-eng` / `all`). Services include LLM inference (Ollama + cloud-provider passthroughs via LiteLLM), chat UI (Open WebUI), workflow + DAG automation (n8n + Airflow), vector + graph DBs (Weaviate + Neo4j), distributed compute (Ray + Spark), notebooks (JupyterHub + Zeppelin), object storage (MinIO), observability (Prometheus + Grafana) — all configurable for container, localhost, or disabled modes, with CPU/GPU variants where a service supports them.

## Editing Rules

When editing existing files, preserve all existing functionality. Never remove output statements, color settings, progress indicators, or UI elements unless explicitly asked. Before saving edits, mentally diff against the original to check for regressions.

## Workflow Conventions

After generating a report or output to a file, always display a summary or the full content to the user without being asked.

## Code Review

When performing code audits or reviews, always present findings before making changes. Wait for user approval before implementing fixes.

## Docker / Infrastructure

Do not change Docker images, base configurations, or architectural decisions unless explicitly requested. When fixing issues, make minimal targeted changes rather than swapping out components.

## UI Development

For TUI/CLI visual work: after each change, describe exactly what changed visually. Never overhaul the entire UI when a targeted fix is requested. Preserve the user's aesthetic choices.

## Git Workflow

`main` is protected — **direct push to main is rejected even for the repo owner** (admin enforcement is on). Every change must land via a pull request with all four `services-lint` CI checks green:

- `Manifest lint + unit tests`
- `Compose merge + byte-equivalence + source-permutation matrix`
- `Docs drift + audit scripts`
- `Build-validation (Dockerfile + requirements.txt installability)`

Strict mode is enabled, so the PR branch must be up to date with main before merge becomes available. Conversation-resolution is required.

Integration flow: branch (typically a worktree under `.Codex/worktrees/<name>`) → push the branch → `gh pr create --base main` → wait for the 4 checks → `gh pr merge --squash --delete-branch` (squash preserves the linear history the repo prefers). Never attempt `git push origin main` — GitHub rejects it with `GH006: Protected branch update failed`. Inspect the live rule with `gh api repos/thekaveh/atlas/branches/main/protection`.

## Key Commands

```bash
# Start the stack (interactive wizard on first run)
./start.sh

# Start with specific service configuration (SOURCE values live in .env;
# use the CLI flag — a shell-env prefix does not configure the bootstrapper)
./start.sh --llm-provider-source ollama-container-gpu

# Equivalent CLI flag form (skips the wizard for the flags you set)
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
`gen-ai-creative`, `ml-eng`, `data-eng`, `all`). Each track lists a subset of
source-configurable services the wizard should prompt for; out-of-track services
are force-disabled (`*_SOURCE=disabled`) at the end of the flow. A small set of
services the wizard always *prompts* for (LLM Engine + Prometheus + Grafana +
cloud-provider keys) is exempt from track-skip filtering and applies to every
track — note that Prometheus and Grafana still ship **disabled by default**;
being always-prompted does not mean always-running. The genuinely locked,
always-running tier is Supabase + Kong + Redis + LiteLLM + Backend.

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
- `core/docker_manager.py` — compose execution; `execute_compose_command` for the linear flow, `stream_compose` for line-by-line piping into the Textual log pane
- `services/topology.py` — single source of truth for service rows (category, deps, aliases, port defaults, display name, description). `get_topology()` accessor; `build_topology()` for tests with synthetic services dirs.
- `services/manifests.py` — loads `services/<name>/service.yml` files into `Manifest` dataclasses (env vars, source variants, runtime_sc slices).
- `services/manifest_validator.py` — schema + cross-manifest invariants (alias uniqueness, cycle detection, category-overflow, engine-orphan lints).
- `services/sc_synthesizer.py` — concatenates per-manifest `runtime_sc:` slices into the legacy service-config dict shape (source_configurable, adaptive_services, dependencies, service_dependencies).
- `services/env_assembler.py` — generates `.env.example` from manifests' env-var declarations + topology port defaults.
- `services/source_validator.py` — SOURCE validation
- `services/migrations/` — chained env-file migrations (`migration_v1.py` port-layout, `migration_v2.py` URL→PORT, `migration_v3.py` COMFYUI model-set schema; each gated by its own sentinel).
- `ui/textual/integration.py` — public entry points `run_setup_flow` (interactive wizard + pipeline + log streaming, all in one Textual app) and `run_launch_flow` (CLI-flag mode: skip the wizard, jump to the launch screen with the user's overrides applied)
- `ui/textual/screens/wizard_screen.py` — `WizardScreen` hosts the wizard prompts, then transitions in-place to the launch phase (service-table + log pane + filter chips)
- `ui/textual/widgets/` — Textual widgets composed by `WizardScreen` (prompt panel, service table, info / brand panels, log pane + filter chips, command summary, footer bar)
- `ui/textual/palette.py`, `ui/textual/theme.css` — colors and Textual CSS for the app
- `ui/state.py`, `ui/state_builder.py` — framework-agnostic data model + builder; `state_builder.all_services()` is the single source of truth for service definitions, consumed by both the Textual `ServiceTable` and the `--no-tui` `build_pre_launch_summary_table`
- `ui/term_caps.py` — `is_tui_capable(no_tui_flag)` helper used by `start.py` to decide between the Textual app and the linear flow
- `wizard/service_discovery.py` — service metadata (display name, description, options) consumed by `ui/textual/integration.py` to build the wizard prompt steps
- `utils/kong_config_generator.py` — dynamic Kong route generation (the `kong-dynamic.yml` it emits is regenerated at every startup; do NOT edit by hand)
- `generate_supabase_keys.py` (and `.sh` sibling) — auto-runs at startup, generates Supabase JWT keys into `.env`

`start.sh` and `stop.sh` are thin wrappers that prefer `uv run` and fall back to system Python. The bootstrapper can also be invoked directly: `python bootstrapper/start.py [flags]` or `python bootstrapper/stop.py`. `--no-tui` bypasses the Textual TUI and runs the linear stdout flow (used by CI, non-TTY shells, and very narrow terminals).

Dependencies managed via `uv` (falls back to pip). Config: `bootstrapper/pyproject.toml` — current deps are `pyyaml`, `rich`, `click`, `requests`, `urllib3`, `jsonschema`, `textual` (the TUI framework). Python `>=3.10`.

**Brand customization.** The wizard's brand panel and info box (brand name, tagline, version, author, author email, license, repo URL) is configurable via `BRAND_*` env vars in `.env`. Defaults are Atlas; forks can rebrand by setting these. See the `BRAND_*` block in `.env.example`.

### Backend (`/backend/`)

FastAPI service at `services/backend/app/app/main.py`. Orchestrates AI services and connects to PostgreSQL (Supabase), Weaviate, n8n, ComfyUI, LiteLLM (which fronts Ollama), and Ray. Dependencies in `services/backend/app/app/requirements.txt`, uses `uv pip install` in Docker.

No standalone dev mode — backend runs only inside Docker because it depends on its upstream services being up. For local iteration, edit and rebuild the `backend` service via compose. A small pytest suite lives at `services/backend/app/app/tests/` (Ray client/routes); it needs the backend's own dependencies and is not part of the bootstrapper suite.

### Docker Compose (`docker-compose.yml`)

Thin ~70-line top-level shell that merges per-service compose fragments via the native `include:` directive. Each fragment under `services/<name>/compose.yml` owns its containers; cross-fragment `depends_on` and merged top-level `volumes:` work via Compose v2.20+ (v2.26+ recommended).

### Service Init Containers

Many services have dedicated init containers (under `services/<name>/init/` or sibling subfolders like `services/litellm/catalog-init/`) that handle first-run setup: pulling Ollama models, seeding databases, importing n8n workflows, configuring Weaviate schemas.

### Per-service manifest (`services/<name>/service.yml`)

Each service family owns one manifest with:
- `containers:` — list of containers in the family
- `env:` — env vars with descriptions, defaults, port slots
- `sources:` — source variants (container, container-cpu, container-gpu, localhost, disabled, …)
- `category:` — one of `infra | data | llm | media | agents | apps` (drives wizard ordering + UI color)
- `depends_on:` — soft (display order) and required (startup ordering)
- `runtime_sc:` — per-source scale/environment/deploy/extra_hosts slice consumed by `sc_synthesizer.py`
- `runtime_adaptive:` — adaptive-service behavior (backend, open-webui)
- `runtime_deps:` — cross-family dependency hints
- `data_flow.calls:` — runtime call graph (drives the architecture diagram + Dependencies & Integrations block)

See `bootstrapper/schemas/service.schema.json` for the full schema, `docs/CONTRIBUTING-services.md` for the walkthrough.

`bootstrapper/services/manifests.py::_is_service_dir` requires `service.yml` to exist. Two flavors of "no-container" folder live under `services/`:

- **Doc-only folders (no `service.yml`):** `services/stt-provider/`, `services/doc-processor/`, `services/multi2vec-clip/`. Host aggregate documentation + diagrams for the wizard-facing role; the manifest loader silently skips them.
- **Virtual manifests (`virtual: true`, no `compose.yml`):** `services/tts-provider/`, `services/cloud-providers/`, `services/globals/`. They own SOURCE / env-var declarations consumed by the bootstrapper but don't run as containers. The compose include list skips virtual manifests.

### Per-service documentation (`services/<name>/README.md`)

Each `services/<name>/` is the single source of truth for that service: manifest, compose fragment, init scaffolding, README, and the two regenerated diagrams (`architecture.svg`, `architecture.html`). The old `docs/services/<name>/` mirror was retired — there's now exactly one folder per service.

Service READMEs use hierarchical numbered sections (`## 1. Overview`, `## 2. Access`, `## 3. Configuration`, `## 4. Architecture & wiring`, `## N. Dependencies & Integrations`, `## N+1. Troubleshooting`, …). The Dependencies & Integrations block sits at whatever position N the README's section order places it (typically 5, but 7/9/12/14 for READMEs with extra pre-Deps content) — the regen tool reads N from the existing heading and emits matching subsection numbering (`### N.1` through `### N.6`).

The Dependencies & Integrations block is **auto-generated** by `bootstrapper/docs/regen.py`. It contains:
- `### N.1 Current — Upstream` and `### N.2 Current — Downstream` tables (from `data_flow.calls` in the manifests)
- `### N.3 Architecture diagram` (embeds `./architecture.svg`)
- `### N.4 / N.5 / N.6` Future-* subsections (user-authored Phase C content, preserved across regen passes by `_render_section_with_future`)

After changing a `data_flow.calls` list, re-run `PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen <service>` (or `--all`). The drift gate in `bootstrapper/tests/test_docs_drift.py` enforces that committed READMEs/SVGs/HTMLs match what regen would produce.

## Configuration

- `.env.example` — variable template (auto-regenerated from manifests by `services/env_assembler.py`; byte-equivalence enforced by `tests/test_env_assembler.py`)
- `services/<name>/service.yml` — per-service manifest (env vars, sources, runtime slices)
- `services/topology.py` — single source of truth for service rows + port slot allocator
- Kong gateway routes are dynamically generated at startup

## Port Convention

All ports are calculated as offsets from `BASE_PORT` (default 63000). Service ports are defined in `.env.example`.

## Testing

`bootstrapper/tests/` holds 1,300+ pytest tests covering manifest validation, env-example consistency, the docs-drift gate, the diagram renderer, the deps section writer, Kong config generation, and bootstrapper-internal data flow. Run from the repo root:

```bash
uv run --project bootstrapper pytest bootstrapper/tests -q                          # full suite (~60 sec)
uv run --project bootstrapper pytest bootstrapper/tests/test_docs_drift.py          # drift gate alone
uv run --project bootstrapper pytest bootstrapper/tests/test_manifests.py -v        # single file, verbose
uv run --project bootstrapper pytest bootstrapper/tests -k weaviate                 # filter by name
```

`pytest-asyncio` is declared in `services/backend/app/app/requirements.txt`; the backend's own small suite (`services/backend/app/app/tests/`) runs only in an environment with the backend dependencies installed — it is not collected by the bootstrapper suite.

### Audit scripts (`scripts/`)

Operational lint scripts that run outside pytest:

```bash
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check  # docs drift gate (exit 2 on drift)
uv run --project bootstrapper python scripts/check_doc_links.py                # internal markdown link validator
uv run --project bootstrapper python scripts/check-docs-drift.py               # docs structure audit
uv run --project bootstrapper python scripts/check-docs-site.py                # MkDocs strict build
uv run --project bootstrapper python scripts/export-docs-wiki.py --check       # wiki export drift
uv run --project bootstrapper python scripts/check-compose-source-deps.py      # compose depends_on lint
uv run --project bootstrapper python scripts/check-kong-routes.py              # Kong route generator audit
uv run --project bootstrapper python scripts/validate_research_schema.py --all # docs/research/ schema check
uv run --project bootstrapper python scripts/check-track-membership.py         # track coverage audit
(cd services/docling/provider/localhost && uv lock --locked)                   # docling localhost provider lock
```

## Linting / Type-checking

None configured. `bootstrapper/pyproject.toml` declares only pytest in its PEP 735 `[dependency-groups].dev`; there is no formatter, linter, or type-checker for the bootstrapper or the backend. Don't introduce one without being asked.
