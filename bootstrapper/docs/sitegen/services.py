from __future__ import annotations

from pathlib import Path

import yaml

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


def _dedupe_stable(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _source_summary_defaults(service: ServicePage) -> str:
    return csv_or_dash(_dedupe_stable([surface.default for surface in service.source_surfaces]))


def _source_summary_values(service: ServicePage) -> str:
    return csv_or_dash(
        _dedupe_stable(
            [value for surface in service.source_surfaces for value in surface.values]
            or list(service.source_values)
        )
    )


def _comfyui_krea2_section(model: DocsModel, section_number: int) -> str:
    catalog_path = model.root / "services" / "comfyui" / "models.yaml"
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    entries = [entry for entry in catalog["models"] if entry.get("family") == "Krea 2"]
    if not entries:
        return ""

    bundle_rows: list[list[str]] = []
    artifact_rows: list[list[str]] = []
    restrictions: list[str] = []
    for entry in entries:
        label = f"Krea 2 {entry['variant']}"
        bundle_rows.append(
            [
                label,
                f"`{entry['name']}`",
                str(entry["precision"]),
                f"{entry['size_gb']:.3f} GB",
                f"{entry['min_ram_gb']:.0f} GB",
                f"{entry['min_vram_gb']:.0f} GB",
            ]
        )
        for artifact in entry["files"]:
            artifact_rows.append(
                [
                    label,
                    artifact["role"],
                    f"`{artifact['target_dir']}/{artifact['filename']}`",
                    f"{artifact['size_bytes']:,}",
                    f"`{artifact['sha256']}`",
                ]
            )
        for restriction in entry["license_restrictions"]:
            if restriction not in restrictions:
                restrictions.append(restriction)

    license_name = entries[0]["license_name"]
    license_url = entries[0]["license_url"]
    restriction_lines = "\n".join(f"- {item}" for item in restrictions)
    return f"""
## {section_number}. Krea 2 Curated Bundles

Atlas provides separate Krea 2 Turbo and Krea 2 RAW BF16 selections. Each logical bundle uses the same pinned Qwen3-VL 4B text encoder and Qwen-Image VAE; the generated download plan retrieves those shared target files once when both bundles are selected.

### {section_number}.1 Bundle Matrix

{table(["Bundle", "Catalog ID", "Precision", "Disk", "RAM", "VRAM"], bundle_rows)}

### {section_number}.2 Pinned Artifacts

{table(["Bundle", "Role", "Target", "Bytes", "SHA-256"], artifact_rows)}

Every artifact URL is pinned to Hugging Face revision `8038ce89b91b042141541ad0fa51b985ca262c5f`.

### {section_number}.3 Workflow

The API-ready example is `services/comfyui/workflows/krea2-turbo-api.json`. It uses only ComfyUI core nodes with `CLIPLoader` type `krea2`, 8 steps, CFG 1.0, Euler sampling, the simple scheduler, `ConditioningZeroOut`, and a 1024 by 1024 latent. Atlas pins ComfyUI `v0.27.0`, which includes the core Krea 2 support introduced in `v0.26.0`.

### {section_number}.4 License And Operations

Model weights use the [{license_name}]({license_url}). Operators must review the authoritative license before deployment:

{restriction_lines}
- The license does not state a seat-count threshold; do not apply the previously reported 50-seat limit.

The 1024-square generation check is an opt-in `live` pytest and is not part of generic CI.

Container sources default `COMFYUI_MEMORY_LIMIT` to a 40 GB hard ceiling. Docker does not reserve that memory; smaller workloads consume only what they need, while Krea 2 can exceed the former 4 GB limit.
"""


def _litellm_capability_section(model: DocsModel, section_number: int) -> str:
    del model
    return f"""
## {section_number}. Model Capability Contract

Atlas catalog entries can carry `metadata_version: 1` metadata so adapter selection and downstream model assignment do not depend on model-name guesses. The contract is provider-neutral and travels with each typed catalog entry through resolution into LiteLLM `model_info`.

### {section_number}.1 Fields

| Field | Purpose |
|---|---|
| `kind` | Distinguishes `chat` from `embedding` models. |
| `adapter` | Selects the LiteLLM provider adapter, including `ollama_chat` and `ollama`. |
| `capabilities` | Declares chat, embedding, tools, vision, reasoning, and structured-output support. |
| `request_defaults` | Applies model-specific defaults such as `think: false`; defaults are never imposed on every chat model. |
| `recommended_roles` | Recommends consumer roles: `extract`, `keyword`, `query`, `judge`, `embedding`, and `vision`. |
| `dim` | Records the output dimension required for embedding compatibility checks. |

### {section_number}.2 Resolution And Compatibility

Curated metadata is authoritative. Metadata-free custom or live-discovered models remain compatible through a conservative, visibly warned fallback heuristic. Embedding entries require `dim`, cannot carry chat request defaults, and are emitted with LiteLLM `mode: embedding` plus `output_vector_size`.

LightRAG and other consumers can inspect the namespaced `atlas_model_metadata` block to map models to roles such as `extract` and `query` without hard-coding a provider, model family, or hardware assumption.

Retrieve the detailed records from authenticated `GET /v1/model/info`; the compatibility-oriented `GET /v1/models` response does not expose the complete `model_info` payload. Select a role deterministically by filtering for `inferred: false`, the required `kind` or capability, and a matching `recommended_roles` value. Apply an explicit operator preference when configured. Otherwise use lexical `(provider, catalog_name, model_name)` order as a provider-neutral fallback. For Ollama's dual aliases, deduplicate rows by `(provider, catalog_name)` and retain the operator's preferred alias.
"""


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

    profile = f"""# {service.title}

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
- Default SOURCE values: `{_source_summary_defaults(service) or 'none'}`
- Available SOURCE values: `{_source_summary_values(service) or source_values}`

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
    if service.name == "comfyui":
        profile += _comfyui_krea2_section(model, 12)
    if service.name == "litellm":
        profile += _litellm_capability_section(model, 12)
    return profile


def service_pages(model: DocsModel) -> dict[Path, str]:
    pages: dict[Path, str] = {}
    docs = model.root / "docs" / "site" / "services"
    categories = sorted({service.category for service in model.services})
    sections: list[str] = ["# Service Catalog", "", "## 1. Service Catalog", ""]

    for index, category in enumerate(categories, start=1):
        rows = []
        for service in [svc for svc in model.services if svc.category == category]:
            source_vars = csv_or_dash(surface.var for surface in service.source_surfaces) or "none"
            source_defaults = _source_summary_defaults(service) or "none"
            source_values = _source_summary_values(service) or csv_or_dash(service.source_values)
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
