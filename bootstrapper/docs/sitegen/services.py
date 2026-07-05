from __future__ import annotations

from pathlib import Path

from .model import DocsModel, ServicePage
from .rendering import csv_or_dash, table


def _service_link(service: ServicePage) -> str:
    return f"[{service.name}]({service.name}.md)"


def _readme_link(model: DocsModel, service: ServicePage) -> str:
    repo_relative = service.readme.relative_to(model.root).as_posix()
    return f"[{repo_relative}](https://github.com/thekaveh/atlas/blob/main/{repo_relative})"


def _diagram_line(service: ServicePage) -> str:
    if service.diagram_svg and service.diagram_html:
        return (
            f"- Diagram SVG: [`services/{service.name}/architecture.svg`]"
            f"(https://github.com/thekaveh/atlas/blob/main/services/{service.name}/architecture.svg)\n"
            f"- Diagram HTML: [`services/{service.name}/architecture.html`]"
            f"(https://github.com/thekaveh/atlas/blob/main/services/{service.name}/architecture.html)"
        )
    return "- Diagram: not generated for this service family."


def _profile(model: DocsModel, service: ServicePage) -> str:
    source_values = csv_or_dash(service.source_values)
    required = csv_or_dash(service.required_dependencies)
    optional = csv_or_dash(service.optional_dependencies)
    runtime_calls = csv_or_dash(service.runtime_calls)
    aliases = csv_or_dash(service.kong_aliases)
    ports = csv_or_dash(service.port_vars)
    tracks = csv_or_dash(service.track_keys)
    source_rows = [
        [surface.var or "none", surface.default or "none", csv_or_dash(surface.values)]
        for surface in service.source_surfaces
    ] or [[service.source_var or "none", service.source_default or "none", source_values]]

    return f"""# {service.title}

## 1. Overview

`{service.name}` is an Atlas service family in the `{service.category}` category. Its implementation and service-owned documentation live under `services/{service.name}/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `{service.category}`
- Kind: `{service.kind}`
- Tracks: `{tracks}`

## 4. Access

- Kong aliases: `{aliases}`
- Port variables: `{ports}`

## 5. Configuration

- SOURCE variables: `{csv_or_dash(surface.var for surface in service.source_surfaces) or 'none'}`
- Default SOURCE values: `{csv_or_dash(surface.default for surface in service.source_surfaces) or 'none'}`
- Available SOURCE values: `{csv_or_dash(value for surface in service.source_surfaces for value in surface.values) or source_values}`

## 6. Dependencies And Topology

- Required dependencies: `{required}`
- Optional dependencies: `{optional}`
- Runtime calls: `{runtime_calls}`

## 7. Source Values

{table(["SOURCE Variable", "Default", "Values"], source_rows)}

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `{runtime_calls}`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

{_diagram_line(service)}

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: {_readme_link(model, service)}
- Public docs home: [{model.public_url}]({model.public_url})
"""


def service_pages(model: DocsModel) -> dict[Path, str]:
    pages: dict[Path, str] = {}
    docs = model.root / "docs" / "site" / "services"
    categories = sorted({service.category for service in model.services})
    sections: list[str] = ["# Service Catalog", "", "## 1. Service Catalog", ""]

    for index, category in enumerate(categories, start=1):
        rows = []
        for service in [svc for svc in model.services if svc.category == category]:
            source_vars = csv_or_dash(surface.var for surface in service.source_surfaces) or "none"
            source_defaults = csv_or_dash(surface.default for surface in service.source_surfaces) or "none"
            source_values = (
                csv_or_dash(value for surface in service.source_surfaces for value in surface.values)
                or csv_or_dash(service.source_values)
            )
            rows.append(
                [
                    _service_link(service),
                    service.title,
                    csv_or_dash(service.track_keys),
                    source_vars,
                    source_defaults,
                    source_values,
                    csv_or_dash(service.required_dependencies),
                ]
            )
        sections.extend(
            [
                f"### 1.{index}. {category}",
                "",
                table(
                    ["Service", "Title", "Tracks", "SOURCE", "Default", "Values", "Dependencies"],
                    rows,
                ),
                "",
            ]
        )

    pages[docs / "index.md"] = "\n".join(sections).rstrip() + "\n"
    for service in model.services:
        pages[docs / f"{service.name}.md"] = _profile(model, service)
    return pages
