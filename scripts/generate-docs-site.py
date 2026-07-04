#!/usr/bin/env python3
"""Generate the Atlas MkDocs site, diagram catalog, and wiki export.

The generated docs site is a navigation/publishing layer over Atlas' existing
source-of-truth docs. Per-service READMEs and per-service architecture diagrams
remain owned by their service folders and by ``bootstrapper.docs.regen``.
"""

from __future__ import annotations

import argparse
import html
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bootstrapper"))

from services.manifests import Manifest, load_manifests  # noqa: E402


DOCS = ROOT / "docs"
SERVICES = ROOT / "services"
SITE = DOCS / "site"
ARCH = DOCS / "architecture"
WIKI = DOCS / "wiki"


@dataclass(frozen=True)
class ServiceDoc:
    name: str
    title: str
    category: str
    kind: str
    readme: Path
    source_var: str
    source_values: list[str]


DIAGRAMS: dict[str, tuple[str, str, list[str]]] = {
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


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _write_or_check(path: Path, content: str, check: bool) -> int:
    content = content.rstrip() + "\n"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing == content:
        return 0
    if check:
        print(f"DRIFT: {_rel(path)}")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return 0


def _manifest_docs() -> list[ServiceDoc]:
    manifests = {manifest.name: manifest for manifest in load_manifests(SERVICES)}
    readme_dirs = {
        path.name: path
        for path in SERVICES.iterdir()
        if path.is_dir()
        and not path.name.startswith(("_", "."))
        and (path / "README.md").exists()
    }
    docs: list[ServiceDoc] = []
    for name in sorted(set(manifests) | set(readme_dirs)):
        path = SERVICES / name
        readme = path / "README.md"
        manifest = manifests.get(name)
        if manifest is None:
            docs.append(ServiceDoc(name, name, "aggregate", "doc-only", readme, "", []))
            continue
        source_var = manifest.sources.var if manifest.sources else ""
        values = [option.id for option in manifest.sources.options] if manifest.sources else []
        kind = "virtual" if manifest.virtual else "container"
        docs.append(ServiceDoc(name, manifest.label, manifest.category, kind, readme, source_var, values))
    return docs


def _tracks() -> list[dict]:
    return yaml.safe_load((ROOT / "bootstrapper" / "tracks.yml").read_text(encoding="utf-8"))["tracks"]


def _mkdocs_nav(services: list[ServiceDoc]) -> dict:
    service_pages = [{svc.name: f"site/services/{svc.name}.md"} for svc in services]
    diagram_pages = [{title: f"architecture/{slug}.md"} for slug, (title, _, _) in DIAGRAMS.items()]
    return {
        "site_name": "Atlas Documentation",
        "site_description": "Atlas self-hosted AI, data, and engineering platform documentation",
        "docs_dir": "docs",
        "site_dir": "site",
        "strict": True,
        "not_in_nav": "**/*.md",
        "validation": {
            "links": {
                "anchors": "ignore",
                "not_found": "ignore",
                "unrecognized_links": "ignore",
                "absolute_links": "ignore",
            },
            "nav": {"omitted_files": "ignore"},
        },
        "nav": [
            {"Home": "site/index.md"},
            {"Overview": "site/overview.md"},
            {"Quick Start": "site/quick-start.md"},
            {"Architecture": "site/architecture/index.md"},
            {"Architecture Diagrams": diagram_pages},
            {"Services": "site/services/index.md"},
            {"Service Index": "site/services/index.md"},
            {"Service Pages": service_pages},
            {"Tracks": "site/tracks.md"},
            {"Configuration": "site/configuration.md"},
            {"Operations": "site/operations.md"},
            {"Development": "site/development.md"},
            {"Reference": "site/reference/index.md"},
            {"SOURCE Reference": "site/reference/source-values.md"},
            {"Env Var Reference": "site/reference/env-vars.md"},
            {"Ports And Routes": "site/reference/ports-routes.md"},
            {"Track Reference": "site/reference/tracks.md"},
            {"Service Dependencies": "site/reference/service-dependencies.md"},
            {"Manifest Fields": "site/reference/manifest-fields.md"},
            {"Wiki Export": "wiki/Home.md"},
        ],
        "theme": {"name": "mkdocs", "navigation_depth": 3},
    }


def _table(headers: list[str], rows: Iterable[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(out)


def _static_pages(services: list[ServiceDoc]) -> dict[Path, str]:
    service_count = len(services)
    manifests = load_manifests(SERVICES)
    source_vars = sum(1 for manifest in manifests if manifest.sources)
    tracks = _tracks()
    pages: dict[Path, str] = {}
    pages[SITE / "index.md"] = f"""# Atlas Documentation

Atlas is a self-hosted, source-configurable engineering platform for AI, RAG,
creative AI, ML, and data-engineering workloads.

This MkDocs site is the publishable navigation layer for the repo's existing
documentation. It indexes {service_count} service families, {len(tracks)} tracks,
and {source_vars} SOURCE-configurable surfaces while preserving service READMEs
as the per-service source of truth.

## Start Here

- [Overview](overview.md)
- [Quick Start](quick-start.md)
- [Service Index](services/index.md)
- [Architecture](architecture/index.md)
- [Reference](reference/index.md)
"""
    pages[SITE / "overview.md"] = """# Overview

Atlas is organized around SOURCE values, tracks, manifests, generated docs, and
Kong-fronted service URLs. The stack can run local containers, connect to host
services, disable optional services, or route cloud LLM providers through
LiteLLM without changing application code.

Primary source files:

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `.env.example`
- `docs/deployment/source-configuration.md`
"""
    pages[SITE / "quick-start.md"] = """# Quick Start

Run `./start.sh`, choose a track, and use `./start.sh --setup-hosts` when you
want the Kong `*.localhost` aliases. Common paths:

- `./start.sh --track gen-ai-eng`
- `./start.sh --track gen-ai-rag`
- `./start.sh --track data-eng`
- `./start.sh --base-port 64000`

See the existing [interactive wizard guide](../quick-start/interactive-setup-wizard.md)
and [troubleshooting guide](../quick-start/troubleshooting.md).
"""
    pages[SITE / "architecture" / "index.md"] = """# Architecture

Atlas architecture is documented in split perspectives rather than one
overloaded diagram. Start with the platform overview, then use the focused
diagrams for bootstrapper, SOURCE, routing, RAG, LLM, lakehouse, observability,
security, and service admission flows.

The high-level diagram catalog lives under `docs/architecture/`. Per-service
diagrams remain generated under each `services/<name>/` folder.
"""
    pages[SITE / "configuration.md"] = """# Configuration

Configuration is driven by `.env`, `.env.example`, SOURCE values, and manifest
defaults. `.env.example` is generated from manifests and topology port defaults.

Key references:

- [SOURCE configuration](reference/source-values.md)
- [Environment variables](reference/env-vars.md)
- [Ports and routes](reference/ports-routes.md)
"""
    pages[SITE / "operations.md"] = """# Operations

Common commands:

```bash
./start.sh
./start.sh --setup-hosts
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
```

Operational references include startup warnings, release notes, backup/restore,
and CI gates.
"""
    pages[SITE / "development.md"] = """# Development

Adding or changing a service requires manifest, compose, topology, docs, route,
and test updates. Use:

```bash
PYTHONPATH=bootstrapper python -m bootstrapper.docs.regen --all --check
python scripts/check-docs-site.py
python scripts/export-docs-wiki.py --check
```

See [Adding a service](../CONTRIBUTING-services.md).
"""
    pages[SITE / "tracks.md"] = """# Tracks

Tracks constrain the wizard to the services needed for a workflow and
force-disable out-of-track services unless explicitly overridden.

""" + _table(["Track", "Description", "Services"], ([t["key"], t.get("description", ""), ", ".join(t["services"])] for t in tracks))
    pages[SITE / "reference" / "index.md"] = """# Reference

- [SOURCE values](source-values.md)
- [Environment variables](env-vars.md)
- [Ports and routes](ports-routes.md)
- [Tracks](tracks.md)
- [Service dependencies](service-dependencies.md)
- [Manifest fields](manifest-fields.md)
"""
    return pages


def _service_pages(services: list[ServiceDoc]) -> dict[Path, str]:
    pages: dict[Path, str] = {}
    groups = {
        "container": [svc for svc in services if svc.kind == "container"],
        "virtual": [svc for svc in services if svc.kind == "virtual"],
        "doc-only": [svc for svc in services if svc.kind == "doc-only"],
    }
    rows = []
    for svc in services:
        rows.append([
            f"[{svc.name}](../services/{svc.name}.md)",
            svc.title,
            svc.category,
            svc.kind,
            svc.source_var or "-",
        ])
        pages[SITE / "services" / f"{svc.name}.md"] = f"""# {svc.title}

Generated service-site entry for `{svc.name}`.

- Category: `{svc.category}`
- Kind: `{svc.kind}`
- SOURCE variable: `{svc.source_var or 'none'}`
- SOURCE values: `{', '.join(svc.source_values) if svc.source_values else 'none'}`
- Source README: `services/{svc.name}/README.md`

The service README remains the source of truth for detailed setup, architecture,
and troubleshooting. This page exists so MkDocs navigation can index every
manifest-backed, virtual, and doc-only service family deterministically.
"""
    pages[SITE / "services" / "index.md"] = f"""# Service Index

Generated from `services/*/service.yml` and `services/*/README.md`.

## Service Families

{_table(["Service", "Title", "Category", "Kind", "SOURCE"], rows)}

## Virtual manifests

Virtual manifests are configuration surfaces without a compose container:
{', '.join(sorted(svc.name for svc in groups['virtual']))}.

## Doc-only service folders

Doc-only service folders are aggregate documentation surfaces without their own
`service.yml`: {', '.join(sorted(svc.name for svc in groups['doc-only']))}.
"""
    return pages


def _reference_pages(services: list[ServiceDoc]) -> dict[Path, str]:
    manifests = load_manifests(SERVICES)
    pages: dict[Path, str] = {}
    source_rows = []
    env_rows = []
    deps_rows = []
    field_rows = [
        ["containers", "Container names in the service family"],
        ["env", "Environment variables owned by the manifest"],
        ["sources", "SOURCE var, default, and allowed values"],
        ["category", "Topology category and wizard grouping"],
        ["depends_on", "Required and optional logical dependencies"],
        ["runtime_sc", "Per-source runtime scale/env/deploy slices"],
        ["data_flow.calls", "Runtime call graph used by docs/diagrams"],
    ]
    for manifest in manifests:
        if manifest.sources:
            source_rows.append([
                manifest.sources.var,
                manifest.name,
                manifest.sources.default,
                ", ".join(option.id for option in manifest.sources.options),
            ])
        for env in manifest.env:
            env_rows.append([env.name, manifest.name, str(env.default), env.description.replace("|", "/")])
        deps_rows.append([
            manifest.name,
            ", ".join(manifest.depends_on.required) or "-",
            ", ".join(manifest.depends_on.optional) or "-",
            ", ".join(manifest.data_flow.get("calls", [])) or "-",
        ])
    pages[SITE / "reference" / "source-values.md"] = "# SOURCE Values\n\nGenerated from manifests.\n\n" + _table(["SOURCE", "Service", "Default", "Values"], source_rows)
    pages[SITE / "reference" / "env-vars.md"] = "# Environment Variables\n\nGenerated from manifest env declarations.\n\n" + _table(["Variable", "Service", "Default", "Description"], env_rows)
    pages[SITE / "reference" / "ports-routes.md"] = "# Ports And Routes\n\nGenerated index entry. Canonical route details remain in `docs/deployment/ports-and-routes.md`.\n\nSee [ports-and-routes.md](../../deployment/ports-and-routes.md)."
    pages[SITE / "reference" / "tracks.md"] = "# Track Reference\n\nGenerated from `bootstrapper/tracks.yml`.\n\n" + _table(["Track", "Services"], ([t["key"], ", ".join(t["services"])] for t in _tracks()))
    pages[SITE / "reference" / "service-dependencies.md"] = "# Service Dependencies\n\nGenerated from manifest dependency and data-flow fields.\n\n" + _table(["Service", "Required", "Optional", "Runtime Calls"], deps_rows)
    pages[SITE / "reference" / "manifest-fields.md"] = "# Manifest Fields\n\nGenerated manifest schema quick reference.\n\n" + _table(["Field", "Purpose"], field_rows)
    return pages


def _diagram_html(slug: str, title: str, description: str, nodes: list[str]) -> str:
    boxes = []
    arrows = []
    x = 70
    for idx, node in enumerate(nodes):
        y = 135 + (idx % 2) * 120
        box_x = x + idx * 120
        boxes.append(f'<rect x="{box_x}" y="{y}" width="105" height="58" rx="6" fill="#0f172a"/><rect x="{box_x}" y="{y}" width="105" height="58" rx="6" fill="rgba(30, 41, 59, 0.5)" stroke="#22d3ee" stroke-width="1.5"/><text x="{box_x + 52}" y="{y + 31}" fill="white" font-size="10" text-anchor="middle">{html.escape(node)}</text>')
        if idx:
            prev_x = x + (idx - 1) * 120 + 105
            prev_y = 164 + ((idx - 1) % 2) * 120
            arrows.append(f'<line x1="{prev_x}" y1="{prev_y}" x2="{box_x}" y2="{y + 29}" stroke="#64748b" stroke-width="1.5" marker-end="url(#arrowhead)"/>')
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    body {{ margin: 0; background: #020617; color: #e2e8f0; font-family: 'JetBrains Mono', monospace; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px; }}
    .frame {{ border: 1px solid #1e293b; border-radius: 14px; background: #020617; padding: 20px; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }}
    .card {{ border: 1px solid #1e293b; border-radius: 8px; padding: 12px; background: #0f172a; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <p>{html.escape(description)}</p>
    <div class="frame">
      <svg viewBox="0 0 1000 420" role="img" aria-label="{html.escape(title)}">
        <defs>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/>
          </pattern>
          <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" fill="#64748b"/>
          </marker>
        </defs>
        <rect width="1000" height="420" fill="#020617"/>
        <rect width="1000" height="420" fill="url(#grid)"/>
        {''.join(arrows)}
        {''.join(boxes)}
      </svg>
    </div>
    <div class="cards">
      <div class="card"><strong>Source files</strong><br/>Manifests, topology, tracks, docs.</div>
      <div class="card"><strong>Update trigger</strong><br/>Architecture or routing changes.</div>
      <div class="card"><strong>Diagram ID</strong><br/>{html.escape(slug)}</div>
    </div>
  </main>
</body>
</html>"""


def _diagram_pages() -> dict[Path, str]:
    pages: dict[Path, str] = {}
    rows = []
    for slug, (title, description, nodes) in DIAGRAMS.items():
        pages[ARCH / f"{slug}.html"] = _diagram_html(slug, title, description, nodes)
        pages[ARCH / f"{slug}.md"] = f"""# {title}

{description}

[Open the interactive diagram](./{slug}.html).

## Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## Update Rule

Update this page and `{slug}.html` when the represented architecture surface
changes. Use the `architecture-diagram` design system: dark slate background,
JetBrains Mono, split perspectives, readable labels, and no overloaded mega-diagram.
"""
        rows.append([f"[{title}]({slug}.md)", description])
    pages[ARCH / "README.md"] = "# Architecture Diagram Catalog\n\nGenerated catalog of split Atlas architecture perspectives.\n\n" + _table(["Diagram", "Purpose"], rows)
    return pages


def _wiki_pages(services: list[ServiceDoc]) -> dict[Path, str]:
    service_links = "\n".join(f"- [{svc.name}](../site/services/{svc.name}.md)" for svc in services)
    return {
        WIKI / "Home.md": """# Atlas Documentation

Generated from the MkDocs source pages. Do not copy/paste-edit this wiki export
by hand; run `python scripts/export-docs-wiki.py` from the repo root.

- [Overview](../site/overview.md)
- [Quick Start](../site/quick-start.md)
- [Service Index](../site/services/index.md)
- [Reference](../site/reference/index.md)
""",
        WIKI / "_Sidebar.md": "# Atlas Documentation\n\n- [Home](Home.md)\n- [Services](../site/services/index.md)\n- [Reference](../site/reference/index.md)\n",
        WIKI / "Services.md": "# Services\n\nGenerated wiki service index.\n\n" + service_links,
    }


def build_artifacts() -> dict[Path, str]:
    services = _manifest_docs()
    artifacts: dict[Path, str] = {}
    artifacts[ROOT / "mkdocs.yml"] = yaml.safe_dump(_mkdocs_nav(services), sort_keys=False)
    artifacts.update(_static_pages(services))
    artifacts.update(_service_pages(services))
    artifacts.update(_reference_pages(services))
    artifacts.update(_diagram_pages())
    artifacts.update(_wiki_pages(services))
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if generated docs are stale.")
    args = parser.parse_args()

    drift = 0
    for path, content in sorted(build_artifacts().items(), key=lambda item: str(item[0])):
        drift += _write_or_check(path, content, args.check)
    return 2 if drift and args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
