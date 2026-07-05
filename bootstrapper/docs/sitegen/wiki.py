from __future__ import annotations

from pathlib import Path

from .model import DocsModel
from .rendering import csv_or_dash, table


def wiki_pages(model: DocsModel) -> dict[Path, str]:
    wiki = model.root / "docs" / "wiki"
    service_rows = [
        [
            service.name,
            service.category,
            csv_or_dash(service.track_keys),
            service.source_var or "none",
            csv_or_dash(service.source_values),
            csv_or_dash(service.required_dependencies),
        ]
        for service in model.services
    ]
    track_rows = [
        [track.key, track.description, track.services_display]
        for track in model.tracks
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

## 2. Public Site

The full documentation site is published at [{model.public_url}]({model.public_url}).
""",
        wiki / "_Sidebar.md": "# Atlas Documentation\n\n- [1. Home](Home)\n- [2. Overview](Overview)\n- [3. Quick Start](Quick-Start)\n- [4. Core Concepts](Core-Concepts)\n- [5. Tracks](Tracks)\n- [6. Services](Services)\n- [7. Architecture](Architecture)\n- [8. Configuration](Configuration)\n- [9. Operations](Operations)\n- [10. Development](Development)\n- [11. Reference](Reference)\n",
        wiki / "Overview.md": "# Overview\n\n## 1. Platform Model\n\nAtlas is a self-hosted, source-configurable platform for AI, data, automation, notebooks, and observability.\n\n## 2. Source Model\n\nThe site and wiki are generated from service manifests, tracks, topology, README files, and diagram assets.\n",
        wiki / "Quick-Start.md": "# Quick Start\n\n## 1. Launch\n\nRun `./start.sh` and choose a track in the setup wizard.\n\n## 2. Hosts\n\nRun `./start.sh --setup-hosts` when you want Kong `*.localhost` aliases.\n",
        wiki / "Core-Concepts.md": "# Core Concepts\n\n## 1. SOURCE Values\n\nSOURCE variables choose container, localhost, disabled, or service-specific modes.\n\n## 2. Tracks\n\nTracks select workflow-oriented groups of services.\n\n## 3. Manifests\n\nManifests define service metadata, environment variables, dependencies, and runtime wiring.\n",
        wiki / "Tracks.md": "# Tracks\n\n## 1. Track Matrix\n\n" + table(["Track", "Description", "Services"], track_rows),
        wiki / "Services.md": "# Services\n\n## 1. Service Catalog\n\n" + table(["Service", "Category", "Tracks", "SOURCE", "Values", "Dependencies"], service_rows),
        wiki / "Architecture.md": "# Architecture\n\n## 1. Diagram Catalog\n\nThe public site contains the full architecture catalog and per-service diagram links.\n\n## 2. Stack Shape\n\nAtlas routes browser and API traffic through Kong, composes services through Docker Compose fragments, and adapts application services based on enabled upstreams.\n",
        wiki / "Configuration.md": "# Configuration\n\n## 1. Environment\n\n`.env.example` is generated from manifests and topology defaults. `.env` stores local choices.\n\n## 2. SOURCE Flags\n\nWizard selections can also be passed as `./start.sh --<service>-source <value>` flags.\n",
        wiki / "Operations.md": "# Operations\n\n## 1. Commands\n\nUse `./start.sh`, `./stop.sh`, `./stop.sh --cold`, and `./stop.sh --clean-hosts` from the repository root.\n",
        wiki / "Development.md": "# Development\n\n## 1. Service Admission\n\nAdd or change services through manifests, compose fragments, topology, docs regeneration, and CI checks.\n",
        wiki / "Reference.md": "# Reference\n\n## 1. Generated References\n\nThe public site publishes SOURCE values, environment variables, ports, routes, tracks, dependencies, and manifest-field references.\n",
    }
