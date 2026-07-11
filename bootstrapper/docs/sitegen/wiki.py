from __future__ import annotations

from pathlib import Path

from .model import DocsModel
from .pages import ARCHITECTURE_PERSPECTIVES
from .rendering import csv_or_dash, table
from .services import (
    _comfyui_krea2_section,
    _comfyui_managed_mps_section,
    _litellm_capability_section,
)


def _source_vars(service) -> str:
    return csv_or_dash(surface.var for surface in service.source_surfaces)


def _source_defaults(service) -> str:
    return csv_or_dash(surface.default for surface in service.source_surfaces)


def _source_values(service) -> str:
    values: list[str] = []
    for surface in service.source_surfaces:
        values.extend(surface.values)
    return csv_or_dash(dict.fromkeys(values))


def _site(path: str) -> str:
    return f"https://thekaveh.github.io/atlas/{path.lstrip('/')}"


def wiki_pages(model: DocsModel) -> dict[Path, str]:
    wiki = model.root / "docs" / "wiki"
    service_count = len(model.services)
    source_count = sum(len(service.source_surfaces) for service in model.services)
    categories = sorted({service.category for service in model.services})
    category_rows = [
        [
            category,
            str(len([service for service in model.services if service.category == category])),
            csv_or_dash(service.name for service in model.services if service.category == category),
        ]
        for category in categories
    ]
    service_rows = [
        [
            service.name,
            service.category,
            csv_or_dash(service.track_keys),
            _source_vars(service) or "none",
            _source_values(service) or "none",
            csv_or_dash(service.required_dependencies),
        ]
        for service in model.services
    ]
    service_category_rows = [
        [
            service.name,
            service.title,
            service.category,
            service.kind,
            csv_or_dash(service.track_keys),
            _source_vars(service) or "none",
        ]
        for service in model.services
    ]
    source_rows = [
        [surface.var, service.name, surface.default, csv_or_dash(surface.values)]
        for service in model.services
        for surface in service.source_surfaces
    ]
    dependency_rows = [
        [
            service.name,
            csv_or_dash(service.required_dependencies),
            csv_or_dash(service.optional_dependencies),
            csv_or_dash(service.runtime_calls),
        ]
        for service in model.services
    ]
    route_rows = [
        [
            service.name,
            csv_or_dash(service.port_vars),
            csv_or_dash(service.kong_aliases),
        ]
        for service in model.services
        if service.port_vars or service.kong_aliases
    ]
    env_rows = [
        [env.name, service.name, env.default, env.description or "-"]
        for service in model.services
        for env in service.env_vars
    ]
    track_rows = [
        [track.key, track.description, track.services_display]
        for track in model.tracks
    ]
    diagram_rows = [
        [slug, title, description]
        for slug, (title, description, _nodes) in ARCHITECTURE_PERSPECTIVES.items()
    ]
    return {
        wiki / "Home.md": f"""# Atlas Documentation

Generated from the MkDocs source model. Do not hand-edit the live wiki; run `uv run --project bootstrapper python scripts/export-docs-wiki.py --check` to verify drift.

## 1. Start Here

- [Overview](Overview)
- [Quick Start](Quick-Start)
- [Core Concepts](Core-Concepts)
- [Tracks](Tracks)
- [Services](Services)
- [Architecture](Architecture)
- [Configuration](Configuration)
- [Operations](Operations)
- [Development](Development)
- [Reference](Reference)

## 2. What Atlas Covers

- Service families: `{service_count}`
- Tracks: `{len(model.tracks)}`
- SOURCE-configurable surfaces: `{source_count}`
- Primary entrypoint: Kong and the Atlas root dashboard
- Runtime model: Docker Compose fragments generated from manifests, topology, tracks, and SOURCE selections

## 3. Public Site

The full documentation site is published at [{model.public_url}]({model.public_url}).

## 4. Editing Rule

Update the repository source files, then regenerate this wiki export. The live GitHub Wiki should be treated as a published mirror, not as the source of truth.

## 5. Local Verification

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py --check
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check-docs-site.py
```
""",
        wiki / "_Sidebar.md": "# Atlas Documentation\n\n- [1. Home](Home)\n- [2. Overview](Overview)\n- [3. Quick Start](Quick-Start)\n- [4. Core Concepts](Core-Concepts)\n- [5. Tracks](Tracks)\n- [6. Services](Services)\n- [7. Architecture](Architecture)\n- [8. Configuration](Configuration)\n- [9. Operations](Operations)\n- [10. Development](Development)\n- [11. Reference](Reference)\n",
        wiki / "Overview.md": f"""# Overview

## 1. Platform Model

Atlas is a self-hosted, source-configurable platform for AI, RAG, creative workflows, notebooks, automation, observability, and data engineering.

The bootstrapper turns repository-owned metadata into a runnable Docker Compose project. Service manifests describe each service family, topology rows define ports and aliases, tracks narrow the wizard to workflow-specific choices, and generated Kong routes expose browser-friendly local entrypoints.

## 2. Documentation Model

The public site and wiki are generated from the same model:

- `services/<name>/service.yml`
- `services/<name>/README.md`
- `services/topology.py`
- `bootstrapper/tracks.yml`
- generated architecture diagrams
- deployment and reference documents

## 3. Category Summary

{table(["Category", "Count", "Services"], category_rows)}

## 4. Navigation

- Public site home: [{model.public_url}]({model.public_url})
- Services: [{_site("site/services/")}]({_site("site/services/")})
- Architecture: [{_site("site/architecture/")}]({_site("site/architecture/")})
- Reference: [{_site("site/reference/")}]({_site("site/reference/")})
""",
        wiki / "Quick-Start.md": """# Quick Start

## 1. Launch

Run Atlas from the repository root:

```bash
./start.sh
```

The wizard prompts for the track, SOURCE values, base port, hosts setup, and final launch confirmation.

## 2. Common Launch Variants

```bash
./start.sh --track gen-ai-rag
./start.sh --track data-eng
./start.sh --base-port 64000
./start.sh --setup-hosts
./start.sh --no-tui
```

## 3. Hosts And Gateway

Run `./start.sh --setup-hosts` when you want Kong `*.localhost` aliases. Without that step, direct localhost ports still work for services that expose them.

## 4. First Places To Open

- Atlas root dashboard: `http://localhost:63000`
- Kong-hosted service aliases: see [Reference](Reference)
- Service-specific docs: see [Services](Services)

## 5. Stop And Reset

```bash
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
```
""",
        wiki / "Core-Concepts.md": """# Core Concepts

## 1. SOURCE Values

SOURCE variables choose how Atlas obtains a service. Common values include `container`, `localhost`, `disabled`, and service-specific variants such as CPU/GPU modes or cloud-provider enablement.

SOURCE choices are stored in `.env`, surfaced through the setup wizard, and consumed by the bootstrapper when it synthesizes Compose configuration.

Typical SOURCE behavior:

- `container` runs the service inside the Atlas Compose project.
- `localhost` connects Atlas to a host-managed service.
- `disabled` excludes the service from the active Compose graph.
- `none` is used where a local provider is intentionally absent.
- Cloud providers use provider-specific enablement flags and API keys.

## 2. Tracks

Tracks select workflow-oriented groups of services. Out-of-track services are force-disabled unless the user explicitly overrides them with a SOURCE flag.

The track system keeps first launch manageable. A RAG user should not have to answer every data-engineering service prompt, and a data-engineering user should not have to enable creative-AI services.

## 3. Manifests

Each manifest owns service metadata, environment variables, SOURCE choices, dependencies, runtime slices, adaptive-service behavior, and data-flow calls.

Manifest fields feed generated `.env.example`, the docs site, wiki tables, route references, and CI validation.

## 4. Topology

The topology registry defines category, ports, aliases, display names, descriptions, and dependency shape used by the wizard and generated references.

Topology is where service categories, port assignment, and Kong alias visibility become consistent across the UI, docs, and generated routes.

## 5. Gateway Routing

Kong exposes the main local entrypoint and generated service aliases. Direct ports remain available for selected service UIs and APIs.

The root dashboard is the preferred entrypoint for humans. Direct service ports are still documented because they matter for smoke tests, local development, and troubleshooting.

## 6. User Overlays

Atlas starts from `.env.example`, writes or preserves the active `.env`, then merges user-owned overlays before backfilling missing keys and applying CLI flags. The sibling `.env.user` file is useful for local checkout-owned values. `ATLAS_ENV_USER_FILE` points at a parent-owned overlay outside the Atlas checkout and is the preferred submodule-consumer pattern.

Overlay precedence is `.env.example` baseline, generated or existing `.env`, sibling `.env.user`, `ATLAS_ENV_USER_FILE`, then explicit flags such as `--project` and `--<svc>-source`. Both overlays are merged on every start, including `--cold`. Relative `ATLAS_ENV_USER_FILE` values resolve against the directory that invoked `start.sh`.

## 7. Hosted Media Gateway

The backend exposes `POST /media/generate` and `GET /media/operations/{operation_id}` as the provider-neutral hosted media surface. Requests dispatch by `provider`, `modality`, and `model`; the initial registry supports `provider=fal` with `modality=image`.

Provider API keys stay in the backend environment, and responses normalize status, artifacts, cost, license, and provenance for downstream consumers.

## 8. RAG Chunking Gateway

The backend exposes `POST /api/chunk` as the shared Chonkie-powered text-splitting surface for RAG ingestion clients. The endpoint supports token, recursive, and semantic strategies and returns stable character offsets plus strategy metadata so n8n workflows, notebooks, and future ingestion services can share one chunking contract.

JupyterHub also installs Chonkie for exploratory notebook work, including `13_chonkie_chunking.ipynb`. Production workflows should still call the Backend endpoint instead of each service adding its own Chonkie dependency.

## 9. RAG Evaluation Gateway

The backend exposes `POST /api/rag/evaluate` as the shared Ragas-powered quality-evaluation surface for supplied RAG question, answer, context, and optional reference records. The endpoint supports faithfulness, answer relevancy, context precision, and context recall metrics while routing evaluator calls through Atlas LiteLLM configuration.

JupyterHub also installs Ragas for exploratory evaluation work, including `14_ragas_evaluation.ipynb`. Production workflows should call the Backend endpoint so n8n, notebooks, and future ingestion jobs share one metric contract without each service carrying its own evaluator package.

## 10. Adaptive Services

Backend and Open WebUI adapt to whichever upstream services are enabled. This keeps the stack useful when a user chooses a smaller track or disables optional services.

Adaptive behavior prevents broken integrations from appearing when their upstream service is disabled.

## 11. Generated Documentation

Service READMEs remain the service-owned source of truth. The `.io` site and wiki are generated publishing layers that keep navigation, tables, and references synchronized.

The docs generator reads the same model used by tests, so docs drift becomes visible before merge.

## 12. Init Companions

Some services use init containers or first-run scaffolding for schema setup, bucket creation, workflow import, model pulls, or catalog bootstrapping.

Init companions should be documented with the service they prepare and represented in dependency/topology notes when they affect startup order.

## 13. Service Categories

Service categories describe the role of the service family in Atlas. They also influence wizard grouping, generated references, and visual grouping in the docs.

Current categories include infra, data, llm, media, agents, apps, and aggregate/doc-only surfaces.
"""
        + _litellm_capability_section(model, 14),
        wiki / "Tracks.md": "# Tracks\n\n## 1. Track Matrix\n\n"
        + table(["Track", "Description", "Services"], track_rows)
        + "\n\n## 2. Selection Behavior\n\n- `all` means no track filtering.\n- Explicit CLI SOURCE flags override track defaults with a warning.\n- Prometheus, Grafana, cloud keys, and the LLM engine stay globally prompted, while their defaults can still be disabled.\n\n## 3. Wizard Behavior\n\n- Track selection happens before service SOURCE prompts.\n- Out-of-track source-configurable services are skipped and force-disabled.\n- Explicit command-line SOURCE flags are preserved even when they cross track boundaries.\n- Locked core services remain part of the runtime foundation.\n\n## 4. Adding Or Changing Tracks\n\n- Update `bootstrapper/tracks.yml`.\n- Confirm every listed service has manifest/topology coverage.\n- Regenerate docs and wiki output.\n- Run track-membership and docs-site checks before opening a PR.\n",
        wiki / "Services.md": "# Services\n\n## 1. Service Catalog\n\n"
        + table(["Service", "Category", "Tracks", "SOURCE", "Values", "Dependencies"], service_rows)
        + "\n\n## 2. Category Catalog\n\n"
        + table(["Service", "Title", "Category", "Kind", "Tracks", "SOURCE"], service_category_rows)
        + "\n\n## 3. SOURCE Surface Summary\n\n"
        + table(["SOURCE", "Service", "Default", "Values"], source_rows)
        + "\n\n## 4. Dependency Summary\n\n"
        + table(["Service", "Required", "Optional", "Runtime Calls"], dependency_rows)
        + "\n"
        + _comfyui_krea2_section(model, 5)
        + _comfyui_managed_mps_section(model, 6),
        wiki / "Architecture.md": """# Architecture

## 1. Stack Shape

Atlas routes browser and API traffic through Kong, composes services through Docker Compose fragments, and adapts application services based on enabled upstreams.

The architecture is intentionally split into focused diagrams instead of one overloaded mega-diagram. Each diagram covers one operating perspective and points back to the source files that should change with it.

## 2. Generated Diagram Catalog

"""
        + table(["Slug", "Title", "Purpose"], diagram_rows)
        + """

## 3. Service Diagrams

Per-service diagrams live beside each service README under `services/<name>/architecture.svg` and `services/<name>/architecture.html`.

## 4. Update Rule

When a manifest, topology row, track, SOURCE value, route, or data-flow call changes, regenerate the docs and diagrams before merging.

## 5. Dependency Topology

Atlas distinguishes required dependencies, optional dependencies, and runtime call relationships.

- Required dependencies affect startup ordering and service viability.
- Optional dependencies describe integrations that should be enabled when present.
- Runtime calls describe how services communicate after launch.
- Kong aliases describe browser-facing and API-facing local access.

## 6. Diagram Responsibilities

Architecture diagrams should explain a bounded perspective, use readable labels, and avoid turning into a service inventory.

Per-service diagrams belong with service READMEs. Platform-level diagrams belong in the architecture catalog.

## 7. Related Pages

- [Services](Services)
- [Configuration](Configuration)
- [Reference](Reference)
""",
        wiki / "Configuration.md": f"""# Configuration

## 1. Environment

`.env.example` is generated from manifests and topology defaults. `.env` stores local runtime choices and generated secrets.

## 2. SOURCE Flags

Wizard selections can also be passed as `./start.sh --<service>-source <value>` flags. Use [Reference](Reference) for the generated SOURCE matrix.

## 3. Ports

All ports derive from `BASE_PORT`, whose default is `63000`. Change it with:

```bash
./start.sh --base-port 64000
```

## 4. Hosts

Use `./start.sh --setup-hosts` for Kong `*.localhost` aliases. Use `./stop.sh --clean-hosts` to remove Atlas-managed host entries.

## 5. Generated Surface Count

- SOURCE surfaces: `{source_count}`
- Environment variables: `{len(env_rows)}`
- Services with ports or aliases: `{len(route_rows)}`

## 6. Safe Editing Rules

- Change manifests before changing generated references.
- Regenerate `.env.example` when env declarations or port slots change.
- Regenerate docs when SOURCE values, tracks, aliases, or dependencies change.
- Keep local secrets in `.env`, not in tracked docs or manifests.

## 7. Troubleshooting Configuration

- Use a different `BASE_PORT` when ports collide.
- Re-run hosts setup after changing aliases.
- Prefer SOURCE flags for repeatable launch scripts.
- Check generated reference tables before assuming a service exposes a port.
""",
        wiki / "Operations.md": """# Operations

## 1. Runtime Commands

```bash
./start.sh
./start.sh --consumer ./atlas.consumer.yml
./start.sh env backfill
./start.sh compose validate
./start.sh --consumer ./atlas.consumer.yml compose validate
./start.sh doctor
./start.sh doctor --format json
./start.sh --consumer ./atlas.consumer.yml doctor --format json
./start.sh endpoints export --format env
./start.sh endpoints export --format json
./start.sh --no-tui --detach
./start.sh --no-tui --detach --json
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
```

## 2. Automation

Use `./start.sh --no-tui --detach` for scripted bring-up. The alias
`--no-follow` is equivalent. Atlas runs the normal start pipeline, waits for
Compose health gates, prints a per-service status summary, and exits instead of
following logs. Add `--json` for machine-readable status in CI or parent-repo
wrappers.

## 3. Headless Validation

Use `./start.sh env backfill` after updating an Atlas submodule pin. It
preserves existing values, appends newly introduced `.env.example` keys, fills
blank values only when the new example carries a non-blank default, and reports
the affected keys by source section. Then run `./start.sh --consumer
./atlas.consumer.yml compose validate` to validate the assembled stack,
including manifest-declared external overlays and back-compatible
`services/_user/<name>/compose.yml` overlays. Exit code `0` means the env
backfill or Compose validation succeeded; `compose validate` returns Compose's
failing status code when validation fails.

## 4. Consumer Doctor

Use `./start.sh --consumer ./atlas.consumer.yml doctor` for consumer CI
preflight before starting containers. The doctor runs an extensible check
registry for consumer manifest validation, Compose validation, `_user` overlay
env references, plugin directories, plugin.yml manifest + declared-env
validation, model sidecars, endpoint reporting, and tracked-file cleanliness.
Docker-dependent checks are marked skipped when Docker is unavailable;
Docker-free checks still run. Use `--format json` for CI parsing. Any failed
check exits non-zero.

## 5. Endpoint Contract Export

Use `./start.sh endpoints export --format env|json` to emit a stable,
machine-readable consumer endpoint contract: canonical, distinct
container/host/Kong/public endpoints and active SOURCE modes per
consumer-relevant service, plus every per-consumer `ATLAS_STORE_*` storage
field. The field names are a compatibility contract. Output is secret-free by
default (infra secrets are `${VAR}` references); `--with-secrets` resolves only
consumer-scoped credentials and refuses stdout (requires `--output PATH`).
Output is deterministic and byte-stable. See
[reusing-atlas.md §6.5](https://github.com/thekaveh/atlas/blob/main/docs/deployment/reusing-atlas.md).

## 6. Backend Plugin Manifest

A backend plugin mounted under `BACKEND_PLUGINS_DIR` may ship an optional
`plugin.yml` (`plugin_manifest_version: 1`) declaring a typed, validated
contract: `name`, `route_prefix`, `health_path`/`docs_url`, `auth:
inherit|open|key-auth`, and typed/`default`/`required`/`secret` `env`. Absent →
the plugin loads exactly as before. A present-but-malformed manifest skips only
that plugin with a structured error and leaves others healthy; duplicate names,
overlapping prefixes, and prefixes shadowing a built-in backend route are
rejected before mounting. Declared env is validated at startup and by the
consumer doctor (required-missing / enum / type warnings, secrets masked as
`***`). `GET /plugins` returns the inventory. Per-plugin `auth` composes into
route-level Kong policies so `key-auth`/`open` apply per prefix without weakening
unrelated backend routes; base Atlas (no plugins) emits the historical single
backend route unchanged. See
[reusing-atlas.md §6.3.1](https://github.com/thekaveh/atlas/blob/main/docs/deployment/reusing-atlas.md#631-declaring-a-typed-plugin-contract-with-pluginyml).

## 7. Launch Flow

The Textual UI handles the wizard, service summary, launch confirmation, and streamed Compose logs. Non-TTY shells use the linear fallback.

## 8. Verification Commands

```bash
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check_doc_links.py
```

## 9. Reset Behavior

Use `./stop.sh --cold` when a service needs a fresh volume state. Use the normal stop path when preserving local state matters.

## 10. Gateway Behavior

Kong aliases depend on hosts setup and generated route configuration. Direct ports remain useful for local smoke tests and troubleshooting.
""",
        wiki / "Development.md": """# Development

## 1. Service Admission

Add or change services through manifests, compose fragments, topology rows, docs regeneration, diagrams, and CI checks.

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

## 3. Required Source Files

- `services/<name>/service.yml`
- `services/<name>/compose.yml` when the service runs containers
- `services/<name>/README.md`
- `services/topology.py`
- generated `.env.example`

## 4. Documentation Rules

Service READMEs use hierarchical numbered sections. Dependencies and Integrations blocks are generated by `bootstrapper.docs.regen`.

## 5. Local Gates

```bash
uv run --project bootstrapper python scripts/generate-docs-site.py --check
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper pytest bootstrapper/tests -q
```

## 6. Merge Rule

Main is protected. Land changes through pull requests and wait for required checks before merge.

## 7. Review Focus

- Does the service have the right category and track membership?
- Are ports allocated through topology rather than ad hoc values?
- Are dependencies represented in both startup and runtime docs?
- Does the wizard present the service at the right moment?
- Are disabled/default SOURCE values conservative?

## 8. Generated Docs Rule

Do not hand-edit generated docs-site or wiki output as a shortcut. Patch the generator or source model, regenerate, and commit the resulting artifacts.
""",
        wiki / "Reference.md": "# Reference\n\n## 1. Generated References\n\nThe public site publishes SOURCE values, environment variables, ports, routes, tracks, dependencies, and manifest-field references.\n\n## 2. SOURCE Values\n\n"
        + table(["SOURCE", "Service", "Default", "Values"], source_rows)
        + "\n\n## 3. Environment Variables\n\n"
        + table(["Variable", "Service", "Default", "Description"], env_rows)
        + "\n\n## 4. Tracks\n\n"
        + table(["Track", "Description", "Services"], track_rows)
        + "\n\n## 5. Service Dependencies\n\n"
        + table(["Service", "Required", "Optional", "Runtime Calls"], dependency_rows)
        + "\n\n## 6. Ports And Routes\n\n"
        + table(["Service", "Port Variables", "Kong Aliases"], route_rows),
    }
