from __future__ import annotations

from .model import DocsModel, ServicePage
from .rendering import csv_or_dash, table


def _service_link(service: ServicePage) -> str:
    return f"[{service.name}]({service.name}.md)"


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


def service_catalog(model: DocsModel) -> str:
    """Render the live canonical service catalog."""
    categories = sorted({service.category for service in model.services})
    sections: list[str] = [
        "# Service Catalog",
        "",
        "## 1. Services by Category",
        "",
        "The *Support* column comes from each family's manifest `support:` block. "
        "Tiers are `stable`, `experimental`, `community` and `unsupported`; selectable is not the same as validated. "
        "A family is `stable` only with cited qualification evidence at a release tag.",
        "",
    ]

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
                    service.support_tier,
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
                    ["Service", "Title", "Support", "Tracks", "SOURCE", "Default", "Values", "Start order"],
                    rows,
                ),
                "",
            ]
        )

    return "\n".join(sections).rstrip() + "\n"
