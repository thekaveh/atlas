#!/usr/bin/env python3
"""Generate the Atlas MkDocs site, diagram catalog, and wiki export.

The generated docs site is a navigation/publishing layer over Atlas' existing
source-of-truth docs. Per-service READMEs and per-service architecture diagrams
remain owned by their service folders and by ``bootstrapper.docs.regen``.
"""

from __future__ import annotations

import argparse
import html
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bootstrapper"))

from docs.sitegen.mkdocs_config import build_mkdocs_config  # noqa: E402
from docs.sitegen.model import DocsModel, load_docs_model  # noqa: E402
from docs.sitegen.pages import reference_pages, static_pages  # noqa: E402
from docs.sitegen.rendering import table  # noqa: E402
from docs.sitegen.services import service_pages  # noqa: E402
from docs.sitegen.theme import copy_artifacts, theme_artifacts  # noqa: E402
from docs.sitegen.wiki import wiki_pages  # noqa: E402


DOCS = ROOT / "docs"
SERVICES = ROOT / "services"
PUBLIC_URL = "https://thekaveh.github.io/atlas/"
GITHUB_BLOB_URL = "https://github.com/thekaveh/atlas/blob/main"
HOME = DOCS / "index.md"
SITE = DOCS / "site"
ARCH = DOCS / "architecture"
WIKI = DOCS / "wiki"
THEME_HERO_IMAGE = DOCS / "assets" / "images" / "atlas-source.png"


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


def _copy_or_check_binary(source: Path, target: Path, check: bool) -> int:
    existing = target.read_bytes() if target.exists() else b""
    expected = source.read_bytes()
    if existing == expected:
        return 0
    if check:
        print(f"DRIFT: {_rel(target)}")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return 0


def _theme_css() -> str:
    return """:root {
  --atlas-bg: #020617;
  --atlas-bg-panel: #07111f;
  --atlas-bg-panel-2: #0b1728;
  --atlas-ink: #e5f4ff;
  --atlas-ink-strong: #f8fbff;
  --atlas-muted: #9fb7cc;
  --atlas-soft: #17324a;
  --atlas-line: #24445f;
  --atlas-blue: #60a5fa;
  --atlas-sky: #38bdf8;
  --atlas-cyan: #0ea5e9;
  --atlas-electric: #7dd3fc;
}

html {
  background: #020617;
}

html, body {
  color: var(--atlas-ink);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

body {
  min-height: 100vh;
  background:
    linear-gradient(115deg, rgba(14, 165, 233, 0.16), transparent 30rem),
    linear-gradient(180deg, #07111f 0, #020617 28rem, #020617 100%);
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background:
    linear-gradient(rgba(56, 189, 248, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(56, 189, 248, 0.04) 1px, transparent 1px);
  background-size: 72px 72px;
  mask-image: linear-gradient(to bottom, black, transparent 72%);
}

.navbar {
  background: rgba(2, 6, 23, 0.88) !important;
  border-bottom: 1px solid rgba(96, 165, 250, 0.22);
  box-shadow: 0 14px 42px rgba(0, 0, 0, 0.32);
  backdrop-filter: saturate(160%) blur(18px);
}

.navbar .container {
  max-width: 1480px;
}

.navbar-brand {
  color: var(--atlas-ink-strong) !important;
  font-weight: 800;
  letter-spacing: 0;
  margin-right: 2rem;
  white-space: nowrap;
}

.navbar-brand::before {
  content: '';
  display: inline-block;
  width: 0.72rem;
  height: 0.72rem;
  margin-right: 0.58rem;
  border-radius: 3px;
  background: linear-gradient(135deg, var(--atlas-electric), var(--atlas-cyan));
  box-shadow: 0 0 22px rgba(56, 189, 248, 0.58);
}

.navbar-dark .navbar-nav .nav-link {
  color: #adc4d8;
  font-size: 0.9rem;
  font-weight: 650;
  padding-left: 0.55rem;
  padding-right: 0.55rem;
  white-space: nowrap;
}

.navbar-dark .navbar-nav .nav-link:hover,
.navbar-dark .navbar-nav .nav-link:focus,
.navbar-dark .navbar-nav .nav-link.active {
  color: #f8fbff;
}

.navbar-collapse {
  overflow-x: auto;
  scrollbar-width: none;
}

.navbar-collapse::-webkit-scrollbar {
  display: none;
}

.navbar-nav {
  flex-wrap: nowrap;
}

.navbar-toggler {
  border: 1px solid rgba(96, 165, 250, 0.28);
}

.dropdown-menu {
  background: rgba(7, 17, 31, 0.98);
  border: 1px solid rgba(96, 165, 250, 0.24);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.42);
}

.dropdown-item {
  color: #c8d9e8;
}

.dropdown-item:hover,
.dropdown-item:focus {
  color: #f8fbff;
  background: rgba(14, 165, 233, 0.16);
}

body > .container {
  max-width: 1480px;
  margin-top: 2.5rem;
  margin-bottom: 5rem;
  padding: 3.2rem 3.6rem;
  background: linear-gradient(180deg, rgba(7, 17, 31, 0.76), rgba(2, 6, 23, 0.9));
  border: 1px solid rgba(96, 165, 250, 0.18);
  border-radius: 8px;
  box-shadow: 0 18px 70px rgba(0, 0, 0, 0.34);
}

.row {
  align-items: flex-start;
}

.col-md-9 {
  flex: 0 0 78%;
  max-width: 78%;
}

.col-md-3 {
  flex: 0 0 22%;
  max-width: 22%;
  border-left: 1px solid rgba(96, 165, 250, 0.16);
}

h1, h2, h3, h4 {
  color: var(--atlas-ink-strong);
  font-weight: 760;
  letter-spacing: 0;
}

h1 {
  max-width: 1050px;
  margin-bottom: 1.1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid rgba(96, 165, 250, 0.22);
  font-size: clamp(2.45rem, 3.6vw, 4.45rem);
  line-height: 1.02;
}

h2 {
  margin-top: 3rem;
  padding-top: 0.95rem;
  border-top: 1px solid rgba(96, 165, 250, 0.18);
}

a {
  color: var(--atlas-sky);
  text-decoration-thickness: 0.08em;
  text-underline-offset: 0.2em;
}

a:hover,
a:focus {
  color: var(--atlas-electric);
}

p, li, td {
  color: var(--atlas-muted);
  line-height: 1.72;
}

strong {
  color: #f8fbff;
}

.homepage img[alt='Atlas block-art platform view'] {
  display: block;
  width: min(100%, 1180px);
  margin: 1.8rem 0 2.2rem;
  border: 1px solid rgba(96, 165, 250, 0.22);
  border-radius: 8px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.38);
}

.col-md-3 .navbar-nav,
.bs-sidebar {
  font-size: 0.92rem;
}

.bs-sidebar .nav > li > a {
  color: #91a9bd;
}

.bs-sidebar .nav > li > a:hover,
.bs-sidebar .nav > li > a:focus {
  color: #f8fbff;
}

code, pre, kbd {
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

pre, code {
  background-color: #07111f;
  border: 1px solid rgba(96, 165, 250, 0.2);
  color: #d8edff;
  border-radius: 8px;
}

pre {
  padding: 1rem 1.15rem;
}

table {
  width: 100%;
  border: 1px solid rgba(96, 165, 250, 0.2);
  background: rgba(7, 17, 31, 0.76);
  border-radius: 8px;
  overflow: hidden;
}

thead th {
  background: #0b1728;
  color: #dff6ff;
  font-size: 0.82rem;
  letter-spacing: 0;
  text-transform: uppercase;
}

tbody tr:nth-child(odd) {
  background: rgba(7, 17, 31, 0.66);
}

tbody tr:nth-child(even) {
  background: rgba(2, 6, 23, 0.72);
}

blockquote {
  border-left: 4px solid var(--atlas-sky);
  color: #bdd2e4;
  background: rgba(14, 165, 233, 0.1);
  padding: 0.9rem 1rem;
}

.footer {
  color: #7890a5;
}

.modal-content {
  background: #07111f;
  border: 1px solid rgba(96, 165, 250, 0.22);
}

.form-control {
  color: #e5f4ff;
  background-color: #020617;
  border-color: rgba(96, 165, 250, 0.28);
}

@media (max-width: 992px) {
  body > .container {
    padding: 1.6rem;
  }

  .col-md-9,
  .col-md-3 {
    flex: 0 0 100%;
    max-width: 100%;
  }

  .navbar-collapse {
    max-height: 70vh;
    overflow-y: auto;
  }

  .navbar-nav {
    flex-wrap: wrap;
  }
}

@media (max-width: 767.98px) {
  body > .container {
    margin-top: 1rem;
    padding: 1.1rem;
    border-left: 0;
    border-right: 0;
    border-radius: 0;
  }

  .bs-sidebar,
  .col-md-3 {
    display: none;
  }
}

.wy-nav-content,
.md-content {
  background: transparent;
}
"""


def _diagram_html(slug: str, title: str, description: str, nodes: list[str]) -> str:
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
            arrows.append(f'<line x1="{prev_x}" y1="{prev_y}" x2="{box_x}" y2="{y + 29}" stroke="#64748b" stroke-width="1.5" marker-end="url(#arrowhead)"/>')
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; background: #020617; color: #e2e8f0; font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px; }}
    .frame {{ border: 1px solid #1e293b; border-radius: 14px; background: #020617; padding: 20px; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 16px; }}
    .card {{ border: 1px solid #1e293b; border-radius: 8px; padding: 12px; background: #0f172a; }}
    .card h3 {{ align-items: center; color: #e2e8f0; display: flex; font-size: 13px; gap: 8px; margin: 0 0 8px; }}
    .card h3::before, .signal {{ background: #22d3ee; border-radius: 999px; box-shadow: 0 0 18px rgba(34, 211, 238, 0.74); content: ''; display: inline-block; height: 8px; width: 8px; }}
    .card p {{ color: #94a3b8; font-size: 12px; line-height: 1.5; margin: 0; }}
    .title {{ align-items: center; display: flex; gap: 10px; }}
    footer {{ color: #64748b; font-size: 11px; margin-top: 16px; }}
  </style>
</head>
<body>
  <main>
    <h1 class="title"><span class="signal"></span>{html.escape(title)}</h1>
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
      <div class="card"><h3>Source files</h3><p>Manifests, topology, tracks, and documentation sources.</p></div>
      <div class="card"><h3>Update trigger</h3><p>Service, routing, SOURCE, track, or architecture changes.</p></div>
      <div class="card"><h3>Diagram ID</h3><p>{html.escape(slug)}</p></div>
    </div>
    <footer>Generated for Atlas documentation using the architecture-diagram design system.</footer>
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

## 1. Diagram

[Open the interactive diagram](./{slug}.html).

## 2. Source Files

- `services/*/service.yml`
- `bootstrapper/tracks.yml`
- `services/topology.py`
- `docs/deployment/source-configuration.md`

## 3. Update Rule

Update this page and `{slug}.html` when the represented architecture surface
changes. Use the `architecture-diagram` design system: dark slate background,
JetBrains Mono, split perspectives, readable labels, and no overloaded mega-diagram.
"""
        rows.append([f"[{title}]({slug}.md)", description])
    pages[ARCH / "README.md"] = "# Architecture Diagram Catalog\n\n## 1. Generated Diagram Index\n\nGenerated catalog of split Atlas architecture perspectives.\n\n" + table(["Diagram", "Purpose"], rows)
    return pages


def build_artifacts() -> dict[Path, str]:
    model = load_docs_model(ROOT)
    artifacts: dict[Path, str] = {}
    artifacts[ROOT / "mkdocs.yml"] = yaml.safe_dump(build_mkdocs_config(model), sort_keys=False)
    artifacts.update(theme_artifacts(ROOT))
    artifacts.update(static_pages(model))
    artifacts.update(service_pages(model))
    artifacts.update(reference_pages(model))
    artifacts.update(_diagram_pages())
    artifacts.update(wiki_pages(model))
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if generated docs are stale.")
    args = parser.parse_args()

    drift = 0
    for path, content in sorted(build_artifacts().items(), key=lambda item: str(item[0])):
        drift += _write_or_check(path, content, args.check)
    for source, target in copy_artifacts(ROOT):
        drift += _copy_or_check_binary(source, target, args.check)
    return 2 if drift and args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
