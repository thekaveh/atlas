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
        "MinIO, Iceberg REST, Spark, JupyterHub, Zeppelin, Airflow, Trino, and Redpanda.",
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
        ["Clients", "Kong", "Supabase Auth", "Service APIs", "Local Secrets", "Cloud Keys", "Local-only UIs"],
    ),
    "service-admission-workflow": (
        "Service Admission Workflow",
        "Manifest, compose fragment, topology row, env assembler, docs regeneration, diagrams, tests, and CI drift gates.",
        ["service.yml", "compose.yml", "Topology", ".env.example", "Docs Regen", "Diagrams", "CI"],
    ),
}

ARCHITECTURE_EDGES: dict[str, list[tuple[str, str, str]]] = {
    "platform-overview": [
        ("Clients", "Kong", "HTTP"),
        ("Kong", "Apps", "routes"),
        ("Kong", "Agents", "routes"),
        ("Apps", "LLM Core", "inference"),
        ("Agents", "LLM Core", "inference"),
        ("Apps", "Data Stores", "state"),
        ("Agents", "Data Stores", "context"),
        ("LLM Core", "Cloud Providers", "passthrough"),
    ],
    "bootstrapper-lifecycle": [
        ("start.sh", "Env Load", "invoke"),
        ("Env Load", "Migrations", "normalize"),
        ("Migrations", "Manifests", "synthesize"),
        ("Manifests", "Tracks", "filter"),
        ("Tracks", "Kong Routes", "generate"),
        ("Kong Routes", "Compose", "assemble"),
        ("Compose", "Logs", "launch"),
    ],
    "source-configuration-model": [
        ("SOURCE Var", "container", "selects"),
        ("SOURCE Var", "localhost", "selects"),
        ("SOURCE Var", "disabled", "selects"),
        ("SOURCE Var", "none", "LLM only"),
        ("none", "cloud enabled", "pairs with"),
        ("container", "adaptive apps", "configures"),
        ("localhost", "adaptive apps", "configures"),
        ("disabled", "adaptive apps", "removes"),
        ("cloud enabled", "adaptive apps", "configures"),
    ],
    "track-selection-matrix": [
        ("Tracks", "Wizard", "narrows"),
        ("Wizard", "Service Families", "prompts"),
        ("Service Families", "Enabled", "selected"),
        ("Service Families", "Force Disabled", "out of track"),
        ("Overrides", "Enabled", "authoritative"),
    ],
    "network-routing-topology": [
        ("Browser", "*.localhost", "hostname"),
        ("*.localhost", "Kong", "gateway"),
        ("Browser", "Direct Ports", "direct"),
        ("Kong", "Backend Network", "routes"),
        ("Direct Ports", "Backend Network", "publishes"),
        ("Backend Network", "localhost mode", "host gateway"),
    ],
    "data-rag-flow": [
        ("Ingestion", "Doc Processing", "extract"),
        ("Ingestion", "MinIO", "objects"),
        ("Doc Processing", "Weaviate", "vectors"),
        ("Doc Processing", "Neo4j", "graph"),
        ("MinIO", "Backend", "artifacts"),
        ("Weaviate", "Backend", "retrieve"),
        ("Neo4j", "Backend", "relationships"),
        ("Backend", "Open WebUI", "API"),
    ],
    "llm-provider-flow": [
        ("Open WebUI", "LiteLLM", "chat"),
        ("Backend", "LiteLLM", "inference"),
        ("Tools", "LiteLLM", "inference"),
        ("LiteLLM", "Ollama", "local"),
        ("LiteLLM", "Cloud LLMs", "passthrough"),
        ("LiteLLM", "Tracing", "telemetry"),
    ],
    "data-engineering-lakehouse-flow": [
        ("JupyterHub", "Spark", "interactive"),
        ("Zeppelin", "Spark", "interactive"),
        ("Airflow", "Spark", "scheduled"),
        ("Redpanda", "Spark", "stream"),
        ("Spark", "Iceberg REST", "catalog"),
        ("Trino", "Iceberg REST", "catalog"),
        ("Spark", "MinIO", "objects"),
        ("Trino", "MinIO", "objects"),
    ],
    "observability-flow": [
        ("Services", "OTel Collector", "OTLP"),
        ("Services", "Prometheus", "metrics"),
        ("Services", "Langfuse", "LLM traces"),
        ("OTel Collector", "Tempo", "traces"),
        ("Prometheus", "Grafana", "query"),
        ("Tempo", "Grafana", "query"),
        ("Loki", "Grafana", "query"),
    ],
    "security-auth-secrets-boundary": [
        ("Clients", "Kong", "gateway"),
        ("Kong", "Supabase Auth", "identity"),
        ("Kong", "Service APIs", "routes"),
        ("Local Secrets", "Service APIs", "inject"),
        ("Cloud Keys", "Service APIs", "provider auth"),
        ("Clients", "Local-only UIs", "loopback"),
    ],
    "service-admission-workflow": [
        ("service.yml", "Topology", "declares"),
        ("service.yml", ".env.example", "generates"),
        ("service.yml", "Docs Regen", "generates"),
        ("compose.yml", "CI", "validates"),
        ("Topology", "CI", "validates"),
        (".env.example", "CI", "drift gate"),
        ("Docs Regen", "Diagrams", "renders"),
        ("Diagrams", "CI", "drift gate"),
    ],
}

ARCHITECTURE_LAYOUTS: dict[str, dict[str, tuple[int, int]]] = {
    "platform-overview": {
        "Clients": (50, 240), "Kong": (250, 240),
        "Apps": (470, 100), "Agents": (470, 380),
        "LLM Core": (720, 100), "Data Stores": (720, 380),
        "Cloud Providers": (980, 100),
    },
    "bootstrapper-lifecycle": {
        node: (40 + index * 175, 230)
        for index, node in enumerate(ARCHITECTURE_PERSPECTIVES["bootstrapper-lifecycle"][2])
    },
    "source-configuration-model": {
        "SOURCE Var": (50, 240), "container": (300, 40),
        "localhost": (300, 170), "disabled": (300, 300),
        "none": (300, 430), "cloud enabled": (590, 430),
        "adaptive apps": (900, 240),
    },
    "track-selection-matrix": {
        "Tracks": (50, 240), "Wizard": (270, 240),
        "Service Families": (500, 240), "Enabled": (790, 110),
        "Force Disabled": (790, 370), "Overrides": (500, 30),
    },
    "network-routing-topology": {
        "Browser": (50, 240), "*.localhost": (280, 90),
        "Direct Ports": (280, 390), "Kong": (520, 90),
        "Backend Network": (770, 240), "localhost mode": (1020, 390),
    },
    "data-rag-flow": {
        "Ingestion": (50, 240), "Doc Processing": (280, 100),
        "MinIO": (280, 390), "Weaviate": (550, 40),
        "Neo4j": (550, 190), "Backend": (810, 240),
        "Open WebUI": (1060, 240),
    },
    "llm-provider-flow": {
        "Open WebUI": (50, 60), "Backend": (50, 240),
        "Tools": (50, 420), "LiteLLM": (390, 240),
        "Ollama": (700, 100), "Cloud LLMs": (700, 380),
        "Tracing": (1000, 240),
    },
    "data-engineering-lakehouse-flow": {
        "JupyterHub": (40, 30), "Zeppelin": (40, 160),
        "Airflow": (40, 290), "Redpanda": (40, 420),
        "Spark": (390, 210), "Trino": (390, 420),
        "Iceberg REST": (720, 150), "MinIO": (720, 360),
    },
    "observability-flow": {
        "Services": (50, 240), "OTel Collector": (310, 70),
        "Prometheus": (310, 240), "Langfuse": (310, 410),
        "Tempo": (650, 70), "Loki": (650, 200),
        "Grafana": (980, 240),
    },
    "security-auth-secrets-boundary": {
        "Clients": (50, 240), "Kong": (300, 100),
        "Local-only UIs": (300, 390), "Supabase Auth": (610, 30),
        "Service APIs": (610, 240), "Local Secrets": (930, 100),
        "Cloud Keys": (930, 370),
    },
    "service-admission-workflow": {
        "service.yml": (50, 100), "compose.yml": (50, 400),
        "Topology": (340, 30), ".env.example": (340, 210),
        "Docs Regen": (340, 390), "Diagrams": (650, 390),
        "CI": (970, 240),
    },
}

_NODE_KINDS = {
    "Clients": "frontend", "Browser": "frontend", "Open WebUI": "frontend",
    "JupyterHub": "frontend", "Zeppelin": "frontend",
    "Apps": "backend", "Agents": "backend", "Backend": "backend",
    "Services": "backend", "Service APIs": "backend", "adaptive apps": "backend",
    "Doc Processing": "backend", "Ingestion": "backend",
    "Data Stores": "data", "MinIO": "data", "Weaviate": "data",
    "Neo4j": "data", "Iceberg REST": "data", "Supabase Auth": "data",
    "Cloud Providers": "cloud", "Cloud LLMs": "cloud", "Cloud Keys": "cloud",
    "cloud enabled": "cloud", "Kong": "security", "API Keys": "security",
    "Local Secrets": "security", "disabled": "security", "none": "security",
    "Local-only UIs": "security", "Redpanda": "bus",
}

ARCHITECTURE_INTERPRETATIONS: dict[str, str] = {
    "platform-overview": (
        "Direct published ports bypass Kong deliberately, for host tools that "
        "can't use the `*.localhost` gateway. All model traffic — local and "
        "cloud — is funneled through LiteLLM so credentials and routing live in "
        "exactly one place (see [LLM provider flow](./llm-provider-flow.md))."
    ),
    "bootstrapper-lifecycle": (
        "Each stage gates the next; a failure in any stage before Compose must "
        "abort the run rather than report a partially launched stack. Env "
        "loading also applies the chained env-file migrations "
        "(`bootstrapper/services/migrations/`) before manifests are synthesized."
    ),
    "source-configuration-model": (
        "SOURCE selects a deployment mode, not just an image variant — the same "
        "value gates Compose scale, env wiring, and Kong route generation "
        "together. `none` is unique to the LLM provider family; no other "
        "service exposes it."
    ),
    "track-selection-matrix": (
        "An explicit CLI `--<svc>-source` override always wins over track "
        "selection and is reported to the operator as an advisory warning. "
        "SOURCE values declared in a consumer manifest's `env.values` also "
        "survive the track force-disable step — only implicit track defaults "
        "get overridden."
    ),
    "network-routing-topology": (
        "Localhost-mode services don't get a duplicate container on the "
        "backend network; the configured host gateway address is what lets "
        "in-network callers reach the host-run process instead."
    ),
    "data-rag-flow": (
        "Backend isn't the only writer into these stores: LightRAG writes "
        "directly to Neo4j over Bolt and to Supabase pgvector, and other MinIO "
        "consumers (the Iceberg pipeline, asset-worker) hold their own scoped "
        "IAM credentials and write directly too — only Backend's own "
        "ingestion path is pictured here, not every producer."
    ),
    "llm-provider-flow": (
        "Disabling a cloud provider in `.env` doesn't error — the model "
        "resolver silently produces zero catalog entries for it. Ollama "
        "models get two aliases (`ollama/<name>` and the bare name); either "
        "works. Tracing observes requests out-of-band and isn't part of the "
        "inference call path, so a tracing-backend outage doesn't affect "
        "completions."
    ),
    "data-engineering-lakehouse-flow": (
        "Iceberg REST's catalog metadata lives in Supabase Postgres via a "
        "JDBC catalog, not a Hive metastore — if `CATALOG_URI` isn't pointed "
        "at `jdbc:postgresql://supabase-db:5432/iceberg`, the base image "
        "silently falls back to a local SQLite catalog and metadata vanishes "
        "on restart. Trino runs single-coordinator, no worker scaling, by "
        "design. Spark still starts with `ICEBERG_REST_SOURCE=disabled` for "
        "ML-only use; only lakehouse SQL fails."
    ),
    "observability-flow": (
        "Langfuse is deliberately outside the OTel path: LiteLLM emits "
        "Langfuse traces via its own `success_callback`, not through the "
        "Collector, because Langfuse is the LLM-behavior layer while "
        "Prometheus/Grafana stay the infrastructure-metrics layer. Only "
        "backend and LiteLLM OTLP traces currently reach Tempo via the "
        "Collector; Loki log export isn't wired up yet."
    ),
    "security-auth-secrets-boundary": (
        "Not every surface sits behind Supabase auth: Backend's `/health`, "
        "`/ready`, `/metrics`, and API-doc routes are intentionally public "
        "(no bearer token) — don't publish them beyond the intended network "
        "boundary. Kong's own Admin API (8001) is loopback-only, reachable "
        "via `docker exec`, never published. JupyterHub is explicitly "
        "operator-trusted, with direct database and service access rather "
        "than a policy gate."
    ),
    "service-admission-workflow": (
        "`manifest_validator.py`'s fragment check is what actually blocks a "
        "partial landing: `missing_fragment` for a non-virtual manifest with "
        "no `compose.yml`, `unexpected_fragment` for a virtual manifest that "
        "ships one anyway, and `fragment_container_drift` when the "
        "manifest's `containers[]` disagrees with the compose file's "
        "`services:` keys. `tools/validate_fragments.py` runs this in CI and "
        "separately checks `.env.example` drift and the README `TOPOLOGY` "
        "block."
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

The backend exposes `POST /media/generate`, `GET /media/operations/{operation_id}`, and `POST /media/operations/{operation_id}/cancel` as the provider-neutral hosted media surface. Requests dispatch by `provider`, `modality`, and `model`; the registry supports `provider=fal` with `modality=image` and `modality=image_to_3d` (verified TRELLIS, Hunyuan3D, Tripo, and Rodin endpoints), and `provider=comfyui` with `modality=image` (the managed/local ComfyUI host, #519). Provider API keys stay in the backend environment, responses normalize status, artifacts, cost, license, and provenance, and cancellation retains reserved spend until provider polling proves a terminal outcome.

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
    edges: list[tuple[str, str, str]],
    positions: dict[str, tuple[int, int]],
) -> str:
    boxes = []
    arrows = []
    labels = []
    palette = {
        ("rgba(8, 51, 68, 0.4)", "#22d3ee", "frontend"),
        ("rgba(6, 78, 59, 0.4)", "#34d399", "backend"),
        ("rgba(76, 29, 149, 0.4)", "#a78bfa", "data"),
        ("rgba(120, 53, 15, 0.3)", "#fbbf24", "cloud"),
        ("rgba(136, 19, 55, 0.4)", "#fb7185", "security"),
        ("rgba(251, 146, 60, 0.3)", "#fb923c", "bus"),
        ("rgba(30, 41, 59, 0.5)", "#94a3b8", "generic"),
    }
    palette_by_kind = {role: (fill, stroke) for fill, stroke, role in palette}
    box_width = 140
    for node in nodes:
        box_x, y = positions[node]
        role = _NODE_KINDS.get(node, "generic")
        fill, stroke = palette_by_kind[role]
        boxes.append(
            f'<rect x="{box_x}" y="{y}" width="{box_width}" height="60" rx="6" fill="#0f172a"/>'
            f'<rect x="{box_x}" y="{y}" width="{box_width}" height="60" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            f'<text x="{box_x + box_width / 2}" y="{y + 25}" fill="white" font-size="11" font-weight="600" text-anchor="middle">{html.escape(node)}</text>'
            f'<text x="{box_x + box_width / 2}" y="{y + 43}" fill="#94a3b8" font-size="9" text-anchor="middle">{role}</text>'
        )
    for source, target, label in edges:
        source_x, source_y = positions[source]
        target_x, target_y = positions[target]
        if source_x <= target_x:
            x1, x2 = source_x + box_width, target_x
        else:
            x1, x2 = source_x, target_x + box_width
        y1, y2 = source_y + 30, target_y + 30
        label_x = (x1 + x2) / 2
        label_y = (y1 + y2) / 2 - 5
        arrows.append(
            f'<line data-source="{html.escape(source)}" data-target="{html.escape(target)}" '
            f'x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#64748b" '
            f'stroke-width="1.5" marker-end="url(#arrowhead)"/>'
        )
        label_width = max(40, len(label) * 5 + 12)
        labels.append(
            f'<rect x="{label_x - label_width / 2}" y="{label_y - 10}" '
            f'width="{label_width}" height="14" rx="3" fill="#020617"/>'
            f'<text x="{label_x}" y="{label_y}" fill="#cbd5e1" font-size="8" '
            f'text-anchor="middle">{html.escape(label)}</text>'
        )
    width = max(1000, max(box_x for box_x, _ in positions.values()) + box_width + 60)
    height = max(560, max(y for _, y in positions.values()) + 60 + 60)
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
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
        <defs>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/>
          </pattern>
          <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" fill="#64748b"/>
          </marker>
        </defs>
        <rect width="{width}" height="{height}" fill="#020617"/>
        <rect width="{width}" height="{height}" fill="url(#grid)"/>
        {''.join(arrows)}
        {''.join(labels)}
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
            title,
            description,
            interpretation,
            nodes,
            ARCHITECTURE_EDGES[slug],
            ARCHITECTURE_LAYOUTS[slug],
        )
        pages[arch / f"{slug}.md"] = f"""# {title}

{description}

## 1. Diagram

[Open the interactive diagram](./{slug}.html).

## 2. Notes

{interpretation}

## 3. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `bootstrapper/services/topology.py`
- `docs/deployment/source-configuration.md`

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
