"""Wizard Model layer — VMx-free, Textual-free (#535).

Everything here is consumed by BOTH the Textual wizard and the --no-tui
linear flow. Nothing in this package may import ``vmx`` or ``textual``;
``tests/test_wizard_layer_boundaries.py`` enforces that.
"""

from __future__ import annotations

from wizard.model.state import (
    AppState,
    CloudApiEntry,
    ConsumerEntry,
    ServiceEntry,
)
from wizard.model.state_builder import (
    alias_for,
    all_cloud_apis,
    all_services,
    build_app_state,
    cloud_api_status_text,
    lookup_service_meta,
    resolve_localhost_port,
    resolve_port,
    service_extras,
)
from wizard.model.service_discovery import (
    CLOUD_PROVIDER_KEYS,
    ServiceDiscovery,
    ServiceInfo,
)

__all__ = [
    "AppState",
    "CLOUD_PROVIDER_KEYS",
    "CloudApiEntry",
    "ConsumerEntry",
    "ServiceDiscovery",
    "ServiceEntry",
    "ServiceInfo",
    "alias_for",
    "all_cloud_apis",
    "all_services",
    "build_app_state",
    "cloud_api_status_text",
    "lookup_service_meta",
    "resolve_localhost_port",
    "resolve_port",
    "service_extras",
]
