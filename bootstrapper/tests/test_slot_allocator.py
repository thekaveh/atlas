"""Slot allocator unit tests."""

from __future__ import annotations

import pytest


from services.manifests import Manifest, DependsOn, EnvVarDecl


def _manifest_with_ports(name, category, port_vars, requires=None):
    return Manifest(
        name=name,
        label=name,
        category=category,
        env=[EnvVarDecl(name=v) for v in port_vars],
        depends_on=DependsOn(required=list(requires or []), optional=[]),
    )


def test_slot_allocator_assigns_within_category_block():
    """Data manifests get ports in the 63010-63039 range; LLM in 63040-63049."""
    from services.topology import _allocate_slots
    manifests = [
        _manifest_with_ports("redis", "data", ["REDIS_PORT"]),
        _manifest_with_ports("litellm", "llm", ["LITELLM_PORT"], requires=["redis"]),
    ]
    canonical = ["redis", "litellm"]
    defaults = _allocate_slots(manifests, canonical, base_port=63000)
    assert defaults["REDIS_PORT"] == 63010
    assert defaults["LITELLM_PORT"] == 63040


def test_slot_allocator_multi_port_manifest_contiguous():
    """A manifest with multiple port vars gets a contiguous block."""
    from services.topology import _allocate_slots
    manifests = [
        _manifest_with_ports("kong", "infra", ["KONG_HTTP_PORT", "KONG_HTTPS_PORT"]),
    ]
    defaults = _allocate_slots(manifests, ["kong"], base_port=63000)
    assert defaults["KONG_HTTP_PORT"] == 63000
    assert defaults["KONG_HTTPS_PORT"] == 63001


def test_slot_allocator_overflow_raises():
    """More than 10 infra port vars exceeds the 10-slot infra block."""
    from services.topology import _allocate_slots, TopologyError
    too_many = [f"VAR_{i}_PORT" for i in range(11)]
    manifests = [_manifest_with_ports("bad", "infra", too_many)]
    with pytest.raises(TopologyError, match="infra block full"):
        _allocate_slots(manifests, ["bad"], base_port=63000)


def test_slot_allocator_ignores_non_port_env_vars():
    """Only env var names ending in _PORT are slotted."""
    from services.topology import _allocate_slots
    manifests = [
        _manifest_with_ports("demo", "data", ["DEMO_PORT", "DEMO_SECRET", "DEMO_SOURCE"]),
    ]
    defaults = _allocate_slots(manifests, ["demo"], base_port=63000)
    assert defaults == {"DEMO_PORT": 63010}


def test_slot_allocator_skips_base_port():
    """BASE_PORT is the allocator's anchor, not an allocatable slot.

    Without the skip, BASE_PORT would consume infra slot 0 (= base_port + 0
    = 63000) and push the first real infra port (KONG_HTTP_PORT) to slot 1
    (63001). With the skip, BASE_PORT is absent from the returned mapping
    and KONG_HTTP_PORT lands at slot 0 (63000).
    """
    from services.topology import _allocate_slots
    manifests = [
        _manifest_with_ports("globals", "infra", ["BASE_PORT"]),
        _manifest_with_ports("kong", "infra", ["KONG_HTTP_PORT", "KONG_HTTPS_PORT"]),
    ]
    defaults = _allocate_slots(
        manifests, ["globals", "kong"], base_port=63000
    )
    assert "BASE_PORT" not in defaults
    assert defaults["KONG_HTTP_PORT"] == 63000
    assert defaults["KONG_HTTPS_PORT"] == 63001
