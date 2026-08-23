"""Wizard Model layer — no module-scope vmx or textual imports (#535).

Everything here is consumed by BOTH the Textual wizard and the --no-tui
linear flow. Nothing in this package may import ``vmx`` or ``textual`` at
module scope; ``tests/test_wizard_layer_boundaries.py`` enforces that via
a static AST scan.

One documented exception: ``llm_rules.selected_llm_source`` does a
deferred, function-scope import of ``wizard.llm_steps`` (a ViewModel
module) to reach the ``LLM_ENGINE_TITLE`` constant. That import runs at
call time, not at package-import time, so the static layer check does not
see it — but calling ``selected_llm_source`` pulls ``textual`` into
``sys.modules`` transitively. This is deliberate for now; moving the
constant into the Model layer is Pass 3 work.
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
