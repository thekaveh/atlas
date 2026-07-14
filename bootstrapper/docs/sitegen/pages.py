from __future__ import annotations

import html
import os
from pathlib import Path

from .model import DocsModel
from .rendering import csv_or_dash, table


ARCHITECTURE_PERSPECTIVES: dict[str, tuple[str, str, list[str]]] = {
    "platform-overview": (
        "Atlas Platform Overview",
        "User entrypoints, Kong, apps, agents, LLM core, data stores, and cloud-provider boundaries.",
        ["Clients", "Kong", "Apps", "Agents", "LLM Core", "Data Stores", "Cloud Providers"],
    ),
    "bootstrapper-lifecycle": (
        "Bootstrapper Lifecycle",
        "How start.sh flows through env loading, migrations, manifest synthesis, track filtering, Kong generation, compose assembly, and launch logs.",
        ["start.sh", "Env Load", "Migrations", "Manifests", "Tracks", "Kong Routes", "Compose", "Logs"],
    ),
    "source-configuration-model": (
        "SOURCE Configuration Model",
        "Container, localhost, disabled, none, cloud-provider enablement, and adaptive-service behavior.",
        ["SOURCE Var", "container", "localhost", "disabled", "none", "cloud enabled", "adaptive apps"],
    ),
    "track-selection-matrix": (
        "Track Selection Matrix",
        "How Atlas tracks map to service families and force-disable out-of-track services.",
        ["Tracks", "Wizard", "Service Families", "Enabled", "Force Disabled", "Overrides"],
    ),
    "network-routing-topology": (
        "Network And Routing Topology",
        "Host ports, Kong aliases, direct service ports, backend-network-only services, and localhost-mode boundaries.",
        ["Browser", "*.localhost", "Kong", "Direct Ports", "Backend Network", "localhost mode"],
    ),
    "data-rag-flow": (
        "Data And RAG Flow",
        "Ingestion, document processing, object storage, vector and graph stores, backend APIs, Open WebUI, and tool/MCP-adjacent flows.",
        ["Ingestion", "Doc Processing", "MinIO", "Weaviate", "Neo4j", "Backend", "Open WebUI"],
    ),
    "llm-provider-flow": (
        "LLM Provider Flow",
        "Ollama, LiteLLM, cloud passthroughs, Open WebUI, backend, MCP/tool access, and trace hooks.",
        ["Open WebUI", "Backend", "LiteLLM", "Ollama", "Cloud LLMs", "Tools", "Tracing"],
    ),
    "data-engineering-lakehouse-flow": (
        "Data Engineering Lakehouse Flow",
        "MinIO, Iceberg REST, Spark, JupyterHub, Zeppelin, Airflow, Trino, Redpanda, Jenkins, and init containers.",
        ["MinIO", "Iceberg REST", "Spark", "JupyterHub", "Zeppelin", "Airflow", "Trino", "Redpanda"],
    ),
    "observability-flow": (
        "Observability Flow",
        "Prometheus, Grafana, Langfuse, OpenTelemetry Collector, Tempo, Loki, and service instrumentation boundaries.",
        ["Services", "OTel Collector", "Prometheus", "Grafana", "Langfuse", "Tempo", "Loki"],
    ),
    "security-auth-secrets-boundary": (
        "Security, Auth, And Secrets Boundary",
        "Supabase, Kong, service auth notes, API keys, local secrets, cloud keys, and intentionally unauthenticated local surfaces.",
        ["Supabase", "Kong", "API Keys", "Local Secrets", "Cloud Keys", "Unauthenticated Local"],
    ),
    "service-admission-workflow": (
        "Service Admission Workflow",
        "Manifest, compose fragment, topology row, env assembler, docs regeneration, diagrams, tests, and CI drift gates.",
        ["service.yml", "compose.yml", "Topology", ".env.example", "Docs Regen", "Diagrams", "CI"],
    ),
}

ARCHITECTURE_INTERPRETATIONS: dict[str, str] = {
    "platform-overview": (
        "Clients enter through Kong or a deliberately published direct port. "
        "Application and agent services consume the shared LLM and data layers; "
        "LiteLLM keeps local inference and cloud-provider credentials behind one "
        "OpenAI-compatible boundary."
    ),
    "bootstrapper-lifecycle": (
        "Startup is an ordered configuration pipeline. Atlas loads and migrates "
        "the environment, synthesizes manifests, applies track decisions, and "
        "generates routes before Compose receives the final service graph. A "
        "failure before launch must not be reported as a running stack."
    ),
    "source-configuration-model": (
        "A service's SOURCE value selects its deployment mode, not merely an "
        "image variant. Container modes create Compose workloads, localhost modes "
        "redirect consumers to the host, and disabled modes remove workloads. The "
        "LLM-specific `none` mode leaves LiteLLM available for cloud-only routing."
    ),
    "track-selection-matrix": (
        "Tracks reduce the wizard to a workflow-oriented service set. Services "
        "outside that set are force-disabled after prompting, while an explicit "
        "CLI source override remains authoritative and is reported to the operator."
    ),
    "network-routing-topology": (
        "Kong provides stable `*.localhost` entrypoints while published ports "
        "support direct host access. Internal-only traffic remains on the Compose "
        "backend network. Localhost modes cross the container boundary through "
        "the configured host gateway instead of starting a duplicate workload."
    ),
    "data-rag-flow": (
        "The Backend coordinates ingestion: processors extract source material, "
        "MinIO preserves objects, Weaviate stores vector representations, and "
        "Neo4j stores graph relationships. Open WebUI and tool callers consume "
        "that assembled retrieval surface through Backend APIs."
    ),
    "llm-provider-flow": (
        "Open WebUI, Backend routes, agents, and tools call LiteLLM rather than "
        "binding to a provider. LiteLLM dispatches to local Ollama or enabled cloud "
        "providers and exposes one model catalog. Tracing observes requests without "
        "becoming part of the inference data path."
    ),
    "data-engineering-lakehouse-flow": (
        "MinIO is the object data plane and Iceberg REST owns table metadata. "
        "Spark and Trino execute against that shared lakehouse; JupyterHub, "
        "Zeppelin, and Airflow submit interactive or scheduled work, while "
        "Redpanda supplies streaming inputs."
    ),
    "observability-flow": (
        "Metrics, traces, logs, and LLM telemetry follow separate collection paths. "
        "Prometheus scrapes metrics, the OpenTelemetry Collector forwards traces, "
        "Loki stores logs, and Grafana correlates those stores; Langfuse remains the "
        "LLM-specific request and evaluation surface."
    ),
    "security-auth-secrets-boundary": (
        "Supabase identities and scoped service credentials protect Backend data "
        "planes, while Kong applies gateway policy at published aliases. Generated "
        "local secrets and cloud keys stay in runtime configuration. Any deliberately "
        "unauthenticated local port remains an operator-trusted development boundary."
    ),
    "service-admission-workflow": (
        "A service enters Atlas through one declarative chain: its manifest owns "
        "SOURCE values and metadata, Compose owns workloads, topology owns placement "
        "and ports, and the env/docs generators project those records. Drift and "
        "integration tests prevent a partial service definition from landing."
    ),
}


def _asset_href(page: Path, docs_root: Path, asset: Path) -> str:
    return os.path.relpath(docs_root / asset, page.parent).replace(os.sep, "/")


def _inline_code_csv(values: list[str]) -> str:
    clean = [value for value in values if value]
    if not clean:
        return "`-`"
    return ", ".join(f"`{value}`" for value in clean)


def static_pages(model: DocsModel) -> dict[Path, str]:
    docs = model.root / "docs"
    service_count = len(model.services)
    source_count = sum(1 for service in model.services if service.source_var)
    home = docs / "index.md"
    overview = docs / "site" / "overview.md"
    tracks = docs / "site" / "tracks.md"
    architecture = docs / "site" / "architecture" / "index.md"
    track_rows = [
        [track.key, track.description, track.services_display]
        for track in model.tracks
    ]
    category_rows = []
    categories = sorted({service.category for service in model.services})
    for category in categories:
        services = [service.name for service in model.services if service.category == category]
        category_rows.append([category, str(len(services)), ", ".join(services) if services else "-"])

    return {
        home: f"""# Atlas Documentation

<div class="md-content--atlas-wide"></div>

<div class="atlas-home">
  <section class="atlas-home__hero">
    <div class="atlas-home__copy">
      <p class="atlas-kicker">Source-configurable local AI, data, and engineering stack</p>
      <p>Atlas is a self-hosted engineering platform for generative AI, RAG, creative AI, ML engineering, and data engineering workloads. Docker Compose fragments, service manifests, SOURCE values, tracks, and Kong routes combine into one configurable local platform.</p>
      <div class="atlas-home__actions">
        <a href="site/quick-start/">Quick Start</a>
        <a href="site/services/">Service Catalog</a>
        <a href="site/architecture/">Architecture</a>
      </div>
    </div>
    <figure class="atlas-home__media">
      <img src="{_asset_href(home, docs, model.poster_image)}" alt="Atlas platform poster">
    </figure>
  </section>
</div>

## 1. Start Here

- [Quick Start](site/quick-start.md)
- [Core Concepts](site/core-concepts.md)
- [Service Catalog](site/services/index.md)
- [Architecture](site/architecture/index.md)
- [Reference](site/reference/index.md)

## 2. Documentation Scope

This site indexes {service_count} service families, {len(model.tracks)} tracks, and {source_count} SOURCE-configurable surfaces from repository-owned source files.

## 3. Publication Surfaces

- Public site: [{model.public_url}]({model.public_url})
- GitHub Wiki export source: [docs/wiki/Home.md](https://github.com/thekaveh/atlas/blob/main/docs/wiki/Home.md)
- Source repository: [thekaveh/atlas](https://github.com/thekaveh/atlas)

## 4. Setup Surface

<div class="atlas-screenshot">
  <img src="{_asset_href(home, docs, model.wizard_screenshot)}" alt="Atlas setup wizard running the launch phase">
</div>
""",
        docs / "site" / "quick-start.md": """# Quick Start

## 1. Launch Atlas

Run `./start.sh` from the repository root. The setup wizard walks through track selection, service SOURCE choices, base-port selection, host aliases, and the launch summary.

## 2. Common Paths

```bash
./start.sh
./start.sh --track gen-ai-rag
./start.sh --track data-eng
./start.sh --base-port 64000
./start.sh --setup-hosts
```

## 3. First Services To Visit

Use the Atlas root dashboard at `http://localhost:63000` after launch. Direct service URLs and Kong aliases are listed in the generated service catalog and ports reference.
""",
        docs / "site" / "core-concepts.md": """# Core Concepts

## 1. SOURCE Values

Each configurable service has a SOURCE variable that controls whether Atlas runs it in Docker, connects to a localhost instance, disables it, or uses a service-specific mode.

## 2. Tracks

Tracks select the subset of services needed for a workflow and force-disable out-of-track services unless the user explicitly overrides them.

## 3. Manifests

Each manifest owns service metadata, env vars, source options, dependencies, runtime slices, and data-flow calls.

## 4. Gateway Access

Kong provides the main local entrypoint and generated aliases. Direct ports remain available for services that expose their own UI or API.

## 5. User Overlays

Atlas starts from `.env.example`, writes or preserves the active `.env`, then merges user-owned overlays before backfilling missing keys and applying CLI flags. The sibling `.env.user` file is useful for local checkout-owned values. `ATLAS_ENV_USER_FILE` points at a parent-owned overlay outside the Atlas checkout and is the preferred submodule-consumer pattern.

Overlay precedence is `.env.example` baseline, generated or existing `.env`, sibling `.env.user`, `ATLAS_ENV_USER_FILE`, then explicit flags such as `--project` and `--<svc>-source`. Both overlays are merged on every start, including `--cold`. Relative `ATLAS_ENV_USER_FILE` values resolve against the directory that invoked `start.sh`.

## 6. Hosted Media Gateway

The backend exposes `POST /media/generate` and `GET /media/operations/{operation_id}` as the provider-neutral hosted media surface. Requests dispatch by `provider`, `modality`, and `model`; the initial registry supports `provider=fal` with `modality=image`. Provider API keys stay in the backend environment, and responses normalize status, artifacts, cost, license, and provenance for downstream consumers.

## 7. RAG Chunking Gateway

The backend exposes `POST /api/chunk` as the shared Chonkie-powered text-splitting surface for RAG ingestion clients. The endpoint supports token, recursive, and semantic strategies and returns stable character offsets plus strategy metadata so n8n workflows, notebooks, and future ingestion services can share one chunking contract.

JupyterHub also installs Chonkie for exploratory notebook work, including `13_chonkie_chunking.ipynb`. Production workflows should still call the Backend endpoint instead of each service adding its own Chonkie dependency.

## 8. RAG Evaluation Gateway

The backend exposes `POST /api/rag/evaluate` as the shared Ragas-powered quality-evaluation surface for supplied RAG question, answer, context, and optional reference records. The endpoint supports faithfulness, answer relevancy, context precision, and context recall metrics while routing evaluator calls through Atlas LiteLLM configuration.

JupyterHub also installs Ragas for exploratory evaluation work, including `14_ragas_evaluation.ipynb`. Production workflows should call the Backend endpoint so n8n, notebooks, and future ingestion jobs share one metric contract without each service carrying its own evaluator package.
""",
        overview: f"""# Overview

## 1. Platform Model

Atlas combines local-first infrastructure, AI services, data services, workflow automation, notebooks, and observability behind a generated runtime configuration layer.

![Atlas poster overview]({_asset_href(overview, docs, model.poster_image)})

## 2. Service Families

Service families live under `services/<name>/` and own their manifest, compose fragment, README, initialization scaffolding, and generated diagrams.

## 3. Generated Documentation

The `.io` site and GitHub Wiki are generated from service manifests, tracks, topology, README files, and diagram assets.
""",
        tracks: "# Tracks\n\n## 1. Track Matrix\n\n"
        + table(["Track", "Description", "Services"], track_rows),
        architecture: f"""# Architecture

## 1. System Shape

Atlas is organized around a bootstrapper, service manifests, generated Kong routes, Docker Compose fragments, and SOURCE-aware adaptive services.

![Atlas top-level architecture]({_asset_href(architecture, docs, model.top_level_diagram)})

## 2. Diagram Catalog

Start with the platform overview, then use the focused architecture pages for bootstrapper lifecycle, SOURCE behavior, tracks, routing, RAG, LLMs, lakehouse, observability, security, and service admission.

## 3. Per-Service Diagrams

Generated service diagrams live beside each service README under `services/<name>/architecture.svg` and `services/<name>/architecture.html`.
""",
        docs / "site" / "configuration.md": """# Configuration

## 1. Environment Files

`.env.example` is generated from service manifests and topology defaults. `.env` stores the local runtime choices.

## 2. SOURCE Overrides

Every SOURCE value can be selected through the wizard or passed as a CLI flag such as `--weaviate-source localhost`.

## 3. Ports

Ports are derived from `BASE_PORT` and service-specific slots. Change the base with `./start.sh --base-port 64000`.
""",
        docs / "site" / "operations.md": """# Operations

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
consumer-relevant service (Backend, LiteLLM, ComfyUI, Ollama, MinIO, Weaviate,
Neo4j, n8n, Redis, Supabase), plus every per-consumer `ATLAS_STORE_*` storage
field. The field names are a compatibility contract. Output is secret-free by
default (infra secrets are `${VAR}` references); `--with-secrets` resolves only
consumer-scoped credentials and refuses stdout (requires `--output PATH`).
Output is deterministic and byte-stable, so parent wrappers can diff it across
runs. See [reusing-atlas.md §6.5](https://github.com/thekaveh/atlas/blob/main/docs/deployment/reusing-atlas.md).

## 6. Backend Plugin Manifest

A backend plugin package mounted under `BACKEND_PLUGINS_DIR` may ship an optional
`plugin.yml` (`plugin_manifest_version: 1`) declaring a typed, validated
contract: `name`, `route_prefix`, `health_path`/`docs_url`, `auth:
inherit|open|key-auth`, and typed/`default`/`required`/`secret` `env`. Absent →
the plugin loads exactly as before (backward compatible). A present-but-malformed
manifest skips only that plugin with a structured error and leaves others
healthy; duplicate names, overlapping prefixes, and prefixes shadowing a built-in
backend route are rejected before mounting. Declared env is validated at startup
and by the consumer doctor (required-missing / enum / type warnings, secrets
masked as `***`). `GET /plugins` returns the resulting inventory. Per-plugin
`auth` composes into route-level Kong policies so `key-auth`/`open` apply per
prefix without weakening unrelated backend routes; base Atlas (no plugins) emits
the historical single backend route unchanged. See
[reusing-atlas.md §6.3.1](https://github.com/thekaveh/atlas/blob/main/docs/deployment/reusing-atlas.md#631-declaring-a-typed-plugin-contract-with-pluginyml).

## 7. Health And Logs

The launch phase streams Docker Compose output through the Textual UI. The same command path works without the TUI in non-interactive environments.
""",
        docs / "site" / "development.md": """# Development

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
PYTHONPATH=bootstrapper uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper python scripts/check-docs-drift.py
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
uv run --project bootstrapper python scripts/check-compose-source-deps.py
uv run --project bootstrapper python scripts/check-kong-routes.py
uv run --project bootstrapper python scripts/validate_research_schema.py --all
uv run --project bootstrapper python scripts/check-track-membership.py
(cd services/docling/provider/localhost && uv lock --locked)
```
""",
        docs / "site" / "reference" / "index.md": """# Reference

## 1. Generated References

- [SOURCE values](source-values.md)
- [Environment variables](env-vars.md)
- [Ports and routes](ports-routes.md)
- [Tracks](tracks.md)
- [Service dependencies](service-dependencies.md)
- [Manifest fields](manifest-fields.md)

## 2. Category Summary

"""
        + table(["Category", "Count", "Services"], category_rows),
    }


def _architecture_diagram_html(
    title: str,
    description: str,
    interpretation: str,
    nodes: list[str],
) -> str:
    boxes = []
    arrows = []
    palette = [
        ("rgba(8, 51, 68, 0.4)", "#22d3ee", "frontend"),
        ("rgba(6, 78, 59, 0.4)", "#34d399", "backend"),
        ("rgba(76, 29, 149, 0.4)", "#a78bfa", "data"),
        ("rgba(120, 53, 15, 0.3)", "#fbbf24", "cloud"),
        ("rgba(136, 19, 55, 0.4)", "#fb7185", "security"),
        ("rgba(251, 146, 60, 0.3)", "#fb923c", "bus"),
        ("rgba(30, 41, 59, 0.5)", "#94a3b8", "generic"),
    ]
    x = 70
    for idx, node in enumerate(nodes):
        y = 135 + (idx % 2) * 120
        box_x = x + idx * 120
        fill, stroke, role = palette[idx % len(palette)]
        boxes.append(
            f'<rect x="{box_x}" y="{y}" width="105" height="60" rx="6" fill="#0f172a"/>'
            f'<rect x="{box_x}" y="{y}" width="105" height="60" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f'<text x="{box_x + 52}" y="{y + 25}" fill="white" font-size="11" font-weight="600" text-anchor="middle">{html.escape(node)}</text>'
            f'<text x="{box_x + 52}" y="{y + 42}" fill="#94a3b8" font-size="9" text-anchor="middle">{role}</text>'
        )
        if idx:
            prev_x = x + (idx - 1) * 120 + 105
            prev_y = 164 + ((idx - 1) % 2) * 120
            arrows.append(
                f'<line x1="{prev_x}" y1="{prev_y}" x2="{box_x}" y2="{y + 29}" stroke="#64748b" stroke-width="1.5" marker-end="url(#arrowhead)"/>'
            )
    # viewBox width: accommodate the rightmost box (box_x = 70 + (n-1)*120,
    # width 105) plus right padding. Capped at 1000 so ≤7-node perspectives
    # keep their existing geometry (no diff); 8+ widen so no box clips.
    width = max(1000, 70 + max(0, len(nodes) - 1) * 120 + 105 + 70)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; background: #020617; color: #e2e8f0; font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px; }}
    .frame {{ border: 1px solid #1e293b; border-radius: 14px; background: #020617; padding: 20px; }}
    .interpretation {{ border-left: 3px solid #22d3ee; margin-top: 20px; padding: 2px 0 2px 16px; }}
    .interpretation h2 {{ color: #e2e8f0; font-size: 15px; margin: 0 0 8px; }}
    .interpretation p {{ color: #94a3b8; font-size: 13px; line-height: 1.6; margin: 0; }}
    .signal {{ background: #22d3ee; border-radius: 999px; box-shadow: 0 0 18px rgba(34, 211, 238, 0.74); content: ''; display: inline-block; height: 8px; width: 8px; }}
    .title {{ align-items: center; display: flex; gap: 10px; }}
  </style>
</head>
<body>
  <main>
    <h1 class="title"><span class="signal"></span>{html.escape(title)}</h1>
    <p>{html.escape(description)}</p>
    <div class="frame">
      <svg viewBox="0 0 {width} 420" role="img" aria-label="{html.escape(title)}">
        <defs>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/>
          </pattern>
          <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" fill="#64748b"/>
          </marker>
        </defs>
        <rect width="{width}" height="420" fill="#020617"/>
        <rect width="{width}" height="420" fill="url(#grid)"/>
        {''.join(arrows)}
        {''.join(boxes)}
      </svg>
    </div>
    <section class="interpretation">
      <h2>How to read this view</h2>
      <p>{html.escape(interpretation)}</p>
    </section>
  </main>
</body>
</html>"""


def architecture_pages(model: DocsModel) -> dict[Path, str]:
    arch = model.root / "docs" / "architecture"
    pages: dict[Path, str] = {}
    rows = []
    for slug, (title, description, nodes) in ARCHITECTURE_PERSPECTIVES.items():
        interpretation = ARCHITECTURE_INTERPRETATIONS[slug]
        pages[arch / f"{slug}.html"] = _architecture_diagram_html(
            title, description, interpretation, nodes
        )
        pages[arch / f"{slug}.md"] = f"""# {title}

{description}

## 1. Diagram

[Open the interactive diagram](./{slug}.html).

## 2. How To Read This View

{interpretation}

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## 4. Maintenance

Regenerate this page and `{slug}.html` after changing a represented service,
route, SOURCE mode, track, dependency, or data-flow boundary.
"""
        rows.append([f"[{title}]({slug}.md)", description])
    catalog = (
        "# Architecture Diagram Catalog\n\n## 1. Generated Diagram Index\n\n"
        "Generated catalog of split Atlas architecture perspectives.\n\n"
        + table(["Diagram", "Purpose"], rows)
    )
    pages[arch / "README.md"] = catalog
    pages[arch / "index.md"] = catalog
    return pages


def reference_pages(model: DocsModel) -> dict[Path, str]:
    ref = model.root / "docs" / "site" / "reference"
    source_rows = []
    for service in model.services:
        for surface in service.source_surfaces:
            source_rows.append(
                [surface.var, service.name, surface.default, csv_or_dash(surface.values)]
            )
    deps_rows = [
        [
            service.name,
            csv_or_dash(service.required_dependencies),
            csv_or_dash(service.optional_dependencies),
            csv_or_dash(service.runtime_calls),
        ]
        for service in model.services
    ]
    track_rows = [
        [track.key, track.description, track.services_display]
        for track in model.tracks
    ]
    env_rows = []
    for service in model.services:
        for env_var in service.env_vars:
            env_rows.append(
                [
                    env_var.name,
                    service.name,
                    env_var.default,
                    env_var.description or "-",
                ]
            )
    route_doc = "[Deployment route reference](../../deployment/ports-and-routes.md#2-kong-hostnames)"
    ports_rows = [
        [
            service.name,
            service.category,
            _inline_code_csv(service.port_vars),
            _inline_code_csv(service.kong_aliases),
            route_doc,
        ]
        for service in model.services
        if service.port_vars or service.kong_aliases
    ]
    return {
        ref / "source-values.md": "# SOURCE Values\n\n## 1. Generated Source Matrix\n\n"
        + table(["SOURCE", "Service", "Default", "Values"], source_rows),
        ref / "env-vars.md": "# Environment Variables\n\n## 1. Generated Environment Matrix\n\n"
        + table(["Variable", "Service", "Default", "Description"], env_rows),
        ref / "ports-routes.md": "# Ports And Routes\n\n## 1. Generated Ports And Routes Matrix\n\n"
        "Generated summary of model-backed service port variables and Kong aliases. "
        "Use the deployment route reference for browser-facing hostname details and route behavior.\n\n"
        + table(
            ["Service", "Category", "Port Variables", "Kong Aliases", "Route Docs"],
            ports_rows,
        ),
        ref / "tracks.md": "# Track Reference\n\n## 1. Generated Track Matrix\n\n"
        + table(["Track", "Description", "Services"], track_rows),
        ref / "service-dependencies.md": "# Service Dependencies\n\n## 1. Generated Dependency Matrix\n\n"
        + table(["Service", "Required", "Optional", "Runtime Calls"], deps_rows),
        ref / "manifest-fields.md": "# Manifest Fields\n\n## 1. Manifest Schema Quick Reference\n\nGenerated manifest schema quick reference.\n\n"
        + table(
            ["Field", "Purpose"],
            [
                ["containers", "Container names in the service family"],
                ["env", "Environment variables owned by the manifest"],
                ["sources", "SOURCE var, default, and allowed values"],
                ["category", "Topology category and wizard grouping"],
                ["depends_on", "Required and optional logical dependencies"],
                ["runtime_sc", "Per-source runtime scale/env/deploy slices"],
                ["data_flow.calls", "Runtime call graph used by docs and diagrams"],
            ],
        ),
    }
