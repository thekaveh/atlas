from __future__ import annotations

import os
from pathlib import Path

from .model import DocsModel
from .rendering import table


def _asset_href(page: Path, docs_root: Path, asset: Path) -> str:
    return os.path.relpath(docs_root / asset, page.parent).replace(os.sep, "/")


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

<div class="atlas-hero">
  <img src="{_asset_href(home, docs, model.hero_image)}" alt="Atlas platform source map">
</div>

Atlas is a self-hosted, source-configurable engineering platform for generative AI, RAG, creative AI, ML engineering, and data engineering workloads. The stack is composed through Docker Compose, a Python bootstrapper, service manifests, SOURCE values, tracks, and a Kong-fronted access model.

<div class="atlas-poster">
  <img src="{_asset_href(home, docs, model.poster_image)}" alt="Atlas poster overview">
</div>

<div class="atlas-screenshot">
  <img src="{_asset_href(home, docs, model.wizard_screenshot)}" alt="Atlas setup wizard running the launch phase">
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
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
```

## 2. Health And Logs

The launch phase streams Docker Compose output through the Textual UI. The same command path works without the TUI in non-interactive environments.
""",
        docs / "site" / "development.md": """# Development

## 1. Service Admission

Adding a service requires a manifest, compose fragment when applicable, topology row, docs regeneration, route checks, and CI validation.

## 2. Required Docs Checks

```bash
uv run --project bootstrapper python scripts/check-docs-site.py
uv run --project bootstrapper python scripts/export-docs-wiki.py --check
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
