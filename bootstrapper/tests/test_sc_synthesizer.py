"""Tests for sc_synthesizer.synthesize_legacy() duplicate-key invariants.

synthesize_legacy() concatenates each manifest's runtime_sc / runtime_adaptive /
runtime_deps / runtime_dependency_tiers slices into the legacy service-config dict
consumed by service_config.py, source_validator.py, dependency_manager.py,
wizard/model/state_builder.py, and wizard/llm_steps.py. It raises ValueError on four
duplicate-key conditions that would otherwise silently let a later manifest
overwrite an earlier one (silent data corruption).

manifest_validator.py checks duplicate env / container / alias keys but NOT
duplicate runtime_sc / adaptive / deps keys, so these ValueErrors are the SOLE
guard for those slices — and until these tests all four branches were
0%-covered (a future refactor loosening the dict-merge could remove the guard
with nothing failing).
"""
from __future__ import annotations

import pytest

from services.manifests import Manifest
from services.sc_synthesizer import synthesize_legacy


def _manifest(name: str, **runtime) -> Manifest:
    """Minimal Manifest with only the runtime_* slices populated."""
    return Manifest(name=name, label=name, category="apps", env=[], **runtime)


def test_duplicate_source_configurable_key_raises() -> None:
    a = _manifest("svc-a", runtime_sc={"shared": {"scale": 1}})
    b = _manifest("svc-b", runtime_sc={"shared": {"scale": 2}})
    with pytest.raises(ValueError, match=r"duplicate source_configurable key 'shared'"):
        synthesize_legacy([a, b])


def test_duplicate_adaptive_services_key_raises() -> None:
    a = _manifest("svc-a", runtime_adaptive={"backend": {"feature": True}})
    b = _manifest("svc-b", runtime_adaptive={"backend": {"feature": False}})
    with pytest.raises(ValueError, match=r"duplicate adaptive_services key 'backend'"):
        synthesize_legacy([a, b])


def test_duplicate_service_dependencies_key_raises() -> None:
    a = _manifest("svc-a", runtime_deps={"core": {"required": []}})
    b = _manifest("svc-b", runtime_deps={"core": {"required": ["x"]}})
    with pytest.raises(ValueError, match=r"duplicate service_dependencies key 'core'"):
        synthesize_legacy([a, b])


def test_duplicate_dependency_tiers_raises() -> None:
    a = _manifest("globals", runtime_dependency_tiers={"infra": [], "data": []})
    b = _manifest("other", runtime_dependency_tiers={"infra": []})
    with pytest.raises(ValueError, match=r"runtime_dependency_tiers declared by more than one"):
        synthesize_legacy([a, b])


def test_happy_path_concatenates_distinct_keys() -> None:
    a = _manifest(
        "svc-a",
        runtime_sc={"a_sc": 1},
        runtime_adaptive={"a_ad": 2},
        runtime_deps={"a_dep": 3},
    )
    b = _manifest("svc-b", runtime_sc={"b_sc": 4})
    g = _manifest("globals", runtime_dependency_tiers={"infra": [], "data": []})
    out = synthesize_legacy([a, b, g])
    assert out["source_configurable"] == {"a_sc": 1, "b_sc": 4}
    assert out["adaptive_services"] == {"a_ad": 2}
    assert out["service_dependencies"] == {"a_dep": 3}
    assert out["dependencies"] == {"infra": [], "data": []}
