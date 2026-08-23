"""
Synthesize the legacy bootstrapper/service-configs.yml dict shape from manifests.

The bootstrapper used to load `bootstrapper/service-configs.yml` directly. After
the configuration-modularization refactor, each manifest at
`services/<name>/service.yml` owns its per-service slice of that data under
`runtime_sc:`, `runtime_adaptive:`, and `runtime_deps:` blocks (plus the
globals manifest owns the `runtime_dependency_tiers:` block).

`synthesize_legacy(manifests)` concatenates those slices into the exact same
dict shape consumers (`service_config.py`, `source_validator.py`,
`dependency_manager.py`, `wizard/model/state_builder.py`, `wizard/llm_steps.py`) expect.
The legacy YAML file is now deleted; this function is the operational source.

`runtime_sc` is the single source of truth for per-source runtime data
(scale, environment, deploy, extra_hosts). The wizard-facing `sources:`
block contains only the id/label/requires for each option — the env-var
assignments (formerly under `sources.options[].effects`) were dropped in
favor of `runtime_sc` so there is no parallel data to drift.
"""

from __future__ import annotations

from typing import Iterable

from services.manifests import Manifest


def synthesize_legacy(manifests: Iterable[Manifest]) -> dict:
    """Build the legacy service-configs.yml-shaped dict by concatenating
    per-manifest runtime blocks.

    Returns a dict with four top-level keys: `source_configurable`,
    `adaptive_services`, `dependencies` (tiers), `service_dependencies`.
    """
    source_configurable: dict[str, dict] = {}
    adaptive_services: dict[str, dict] = {}
    service_dependencies: dict[str, dict] = {}
    dependency_tiers: dict[str, list] = {}

    for m in manifests:
        # Collect sc slices
        for sc_key, source_variants in (m.runtime_sc or {}).items():
            if sc_key in source_configurable:
                raise ValueError(
                    f"sc_synthesizer: duplicate source_configurable key '{sc_key}' "
                    f"(also claimed by an earlier manifest). Last seen: {m.name}."
                )
            source_configurable[sc_key] = source_variants

        # Collect adaptive slices
        for adapt_key, adaptation in (m.runtime_adaptive or {}).items():
            if adapt_key in adaptive_services:
                raise ValueError(
                    f"sc_synthesizer: duplicate adaptive_services key '{adapt_key}' "
                    f"(also claimed by an earlier manifest). Last seen: {m.name}."
                )
            adaptive_services[adapt_key] = adaptation

        # Collect service_dependencies slices
        for dep_key, dep_spec in (m.runtime_deps or {}).items():
            if dep_key in service_dependencies:
                raise ValueError(
                    f"sc_synthesizer: duplicate service_dependencies key '{dep_key}' "
                    f"(also claimed by an earlier manifest). Last seen: {m.name}."
                )
            service_dependencies[dep_key] = dep_spec

        # Globals manifest contributes the tier ordering
        if m.runtime_dependency_tiers:
            if dependency_tiers:
                raise ValueError(
                    "sc_synthesizer: runtime_dependency_tiers declared by more than "
                    f"one manifest (also seen on {m.name}). Only the globals manifest "
                    "should declare it."
                )
            dependency_tiers = dict(m.runtime_dependency_tiers)

    return {
        "source_configurable": source_configurable,
        "adaptive_services": adaptive_services,
        "dependencies": dependency_tiers,
        "service_dependencies": service_dependencies,
    }
