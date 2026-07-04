"""Generated MkDocs and GitHub Wiki publishing layer for Atlas."""

from .model import DocsModel, ServicePage, SourceSurface, TrackPage, load_docs_model
from .mkdocs_config import build_mkdocs_config
from .rendering import csv_or_dash, numbered_nav, table
from .theme import atlas_css, binary_copy_artifacts, theme_artifacts

__all__ = [
    "DocsModel",
    "ServicePage",
    "SourceSurface",
    "TrackPage",
    "load_docs_model",
    "build_mkdocs_config",
    "csv_or_dash",
    "numbered_nav",
    "table",
    "atlas_css",
    "binary_copy_artifacts",
    "theme_artifacts",
]
