from __future__ import annotations

from .model import DocsModel
from .rendering import numbered_nav


def build_mkdocs_config(model: DocsModel) -> dict:
    service_pages = [{service.name: f"site/services/{service.name}.md"} for service in model.services]
    nav_items = [
        {"Overview": "index.md"},
        {"Quick Start": "site/quick-start.md"},
        {"Core Concepts": "site/core-concepts.md"},
        {"Tracks": "site/tracks.md"},
        {"Service Catalog": [{"Index": "site/services/index.md"}, {"Services": service_pages}]},
        {
            "Architecture": [
                {"Overview": "site/architecture/index.md"},
                {"Diagram Catalog": "architecture/README.md"},
                {"Platform Overview": "architecture/platform-overview.md"},
                {"Bootstrapper Lifecycle": "architecture/bootstrapper-lifecycle.md"},
                {"SOURCE Model": "architecture/source-configuration-model.md"},
                {"Track Matrix": "architecture/track-selection-matrix.md"},
                {"Network Routing": "architecture/network-routing-topology.md"},
                {"RAG Flow": "architecture/data-rag-flow.md"},
                {"LLM Flow": "architecture/llm-provider-flow.md"},
                {"Lakehouse Flow": "architecture/data-engineering-lakehouse-flow.md"},
                {"Observability Flow": "architecture/observability-flow.md"},
                {"Security Boundary": "architecture/security-auth-secrets-boundary.md"},
                {"Service Admission": "architecture/service-admission-workflow.md"},
            ]
        },
        {"Configuration": "site/configuration.md"},
        {"Operations": "site/operations.md"},
        {"Development": "site/development.md"},
        {
            "Reference": [
                {"Index": "site/reference/index.md"},
                {"SOURCE Values": "site/reference/source-values.md"},
                {"Environment Variables": "site/reference/env-vars.md"},
                {"Ports And Routes": "site/reference/ports-routes.md"},
                {"Tracks": "site/reference/tracks.md"},
                {"Service Dependencies": "site/reference/service-dependencies.md"},
                {"Manifest Fields": "site/reference/manifest-fields.md"},
            ]
        },
    ]
    return {
        "site_name": "Atlas Documentation",
        "site_description": "Atlas self-hosted AI, data, and engineering platform documentation",
        "site_url": model.public_url,
        "repo_url": "https://github.com/thekaveh/atlas",
        "repo_name": "thekaveh/atlas",
        "edit_uri": "edit/main/docs/",
        "docs_dir": "docs",
        "site_dir": "site",
        "strict": True,
        "exclude_docs": "README.md\nwiki/*.md",
        "extra_css": ["assets/stylesheets/atlas.css"],
        "validation": {
            "links": {
                "anchors": "ignore",
                "not_found": "warn",
                "unrecognized_links": "ignore",
                "absolute_links": "ignore",
            },
            "nav": {"omitted_files": "ignore"},
        },
        "theme": {
            "name": "material",
            "language": "en",
            "features": [
                "navigation.sections",
                "navigation.indexes",
                "navigation.top",
                "search.suggest",
                "search.highlight",
                "content.code.copy",
                "toc.follow",
            ],
            "palette": [
                {
                    "scheme": "slate",
                    "primary": "custom",
                    "accent": "custom",
                    "toggle": {"icon": "material/weather-sunny", "name": "Switch to light mode"},
                },
                {
                    "scheme": "default",
                    "primary": "custom",
                    "accent": "custom",
                    "toggle": {"icon": "material/weather-night", "name": "Switch to dark mode"},
                },
            ],
        },
        "nav": numbered_nav(nav_items),
    }
