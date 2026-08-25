"""Topology engine unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest


_SYNTHETIC_CAPABILITY_YAML = (
    "capabilities:\n"
    "  - name: Synthetic topology contract\n"
    "    status: supported\n"
    "    verification: tested\n"
    "    note: Tests exercise this synthetic topology manifest.\n"
)


def _write_manifest(services_root: Path, name: str, body: str) -> None:
    (services_root / name).mkdir(parents=True, exist_ok=True)
    (services_root / name / "service.yml").write_text(body)


def test_topology_is_frozen():
    """Topology is declared ``frozen=True`` so downstream code can rely on
    its dataclass fields being immutable. Mutating a field must raise
    ``FrozenInstanceError``."""
    from dataclasses import FrozenInstanceError

    from services.topology import Topology
    t = Topology(canonical_order=[], category_of={}, port_defaults={}, rows=[], aliases=[])
    with pytest.raises(FrozenInstanceError):
        t.canonical_order = []  # type: ignore[misc]


from services.manifests import DependsOn, Manifest


def _manifest(name, category, requires=None, rows=None):
    """Helper: build a minimal Manifest for unit tests."""
    return Manifest(
        name=name,
        label=name,
        category=category,
        env=[],
        depends_on=DependsOn(required=list(requires or []), optional=[]),
        rows=list(rows or []),
    )


def test_topo_sort_lex_tiebreaker():
    """Equal-rank manifests sort alphabetically."""
    from services.topology import _topo_sort
    manifests = [
        _manifest("zulu", "data"),
        _manifest("alpha", "data"),
        _manifest("mike", "data"),
    ]
    order = _topo_sort(manifests)
    assert order == ["alpha", "mike", "zulu"]


def test_topo_sort_respects_deps():
    """A depends_on B means B comes first."""
    from services.topology import _topo_sort
    manifests = [
        _manifest("alpha", "data", requires=["zulu"]),
        _manifest("zulu", "data"),
    ]
    order = _topo_sort(manifests)
    assert order == ["zulu", "alpha"]


def test_topo_sort_cycle_raises():
    """Cycle in deps triggers TopologyError with cycle path."""
    from services.topology import _topo_sort, TopologyError
    manifests = [
        _manifest("a", "data", requires=["b"]),
        _manifest("b", "data", requires=["a"]),
    ]
    with pytest.raises(TopologyError, match="cycle"):
        _topo_sort(manifests)


def test_canonical_order_groups_by_category():
    """Topo order is partitioned by category in fixed display order."""
    from services.topology import _canonical_order
    manifests = [
        _manifest("z-app", "apps"),
        _manifest("a-data", "data"),
        _manifest("k-llm", "llm"),
    ]
    topo = ["a-data", "k-llm", "z-app"]
    out = _canonical_order(manifests, topo)
    assert out == ["a-data", "k-llm", "z-app"]  # data → llm → apps


def test_canonical_order_apps_after_agents():
    """Apps category sorts AFTER agents (specs §display order).

    Input order is the REVERSE of the expected output, so this test
    proves ``_canonical_order`` actually re-sorts by category rather
    than just passing the input through.
    """
    from services.topology import _canonical_order
    manifests = [
        _manifest("foo-app", "apps"),
        _manifest("bar-agent", "agents"),
    ]
    topo = ["foo-app", "bar-agent"]  # reverse of expected; force re-sort
    out = _canonical_order(manifests, topo)
    assert out == ["bar-agent", "foo-app"]


# ────────────────────────────────────────────────────────────────────────────
# _is_locked: a manifest is locked when there's no source choice to offer.
#   - No sources block      → locked (always-on infra, e.g. redis)
#   - Exactly one option    → locked (single-variant; nothing to pick)
#   - Two or more options   → not locked (the wizard must ask)
# ────────────────────────────────────────────────────────────────────────────


def test_is_locked_no_sources_block_is_locked():
    """Manifest with no ``sources:`` block is locked (no user choice)."""
    from services.topology import _is_locked
    m = _manifest("redis", "data")  # no sources
    assert _is_locked(m) is True


def test_is_locked_single_option_is_locked():
    """A ``sources`` block with exactly one option is still locked —
    the wizard has nothing to ask the user."""
    from services.manifests import Manifest, SourceOption, SourcesBlock
    from services.topology import _is_locked
    m = Manifest(
        name="single",
        label="Single",
        category="data",
        env=[],
        sources=SourcesBlock(
            var="SINGLE_SOURCE",
            default="container",
            options=[SourceOption(id="container", label="Container")],
        ),
    )
    assert _is_locked(m) is True


def test_is_locked_two_options_is_unlocked():
    """Two or more options means the wizard has a real choice to present."""
    from services.manifests import Manifest, SourceOption, SourcesBlock
    from services.topology import _is_locked
    m = Manifest(
        name="multi",
        label="Multi",
        category="data",
        env=[],
        sources=SourcesBlock(
            var="MULTI_SOURCE",
            default="container",
            options=[
                SourceOption(id="container", label="Container"),
                SourceOption(id="external", label="External"),
            ],
        ),
    )
    assert _is_locked(m) is False


def test_build_topology_end_to_end(tmp_path):
    """A small two-manifest fixture builds a complete Topology."""
    services_root = tmp_path / "services"
    _write_manifest(services_root, "alpha-infra",
        "name: alpha-infra\n"
        "label: A\n"
        "category: infra\n"
        "env:\n"
        "  - name: ALPHA_PORT\n"
        + _SYNTHETIC_CAPABILITY_YAML
        + "rows:\n"
        "  - display_name: Alpha\n"
        "    source_var: ALPHA_SOURCE\n"
        "    port_var: ALPHA_PORT\n"
        "    alias: alpha.localhost\n"
    )
    _write_manifest(services_root, "beta-data",
        "name: beta-data\n"
        "label: B\n"
        "category: data\n"
        "env:\n"
        "  - name: BETA_PORT\n"
        + _SYNTHETIC_CAPABILITY_YAML
        + "depends_on:\n"
        "  required: [alpha-infra]\n"
        "  optional: []\n"
        "rows:\n"
        "  - display_name: Beta\n"
        "    source_var: BETA_SOURCE\n"
        "    port_var: BETA_PORT\n"
    )

    from services.topology import build_topology
    t = build_topology(services_root, base_port=63000)
    assert t.canonical_order == ["alpha-infra", "beta-data"]
    assert t.category_of["alpha-infra"] == "infra"
    assert t.category_of["beta-data"] == "data"
    assert t.port_defaults == {"ALPHA_PORT": 63000, "BETA_PORT": 63010}
    assert [r.display_name for r in t.rows] == ["Alpha", "Beta"]
    assert t.aliases == ["alpha.localhost"]
