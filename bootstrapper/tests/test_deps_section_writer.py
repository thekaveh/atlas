"""Tests for bootstrapper.docs.deps_section_writer."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

SERVICES_DIR = REPO_ROOT / "services"
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_section_for_hermes_matches_golden():
    """Hermes deps section is byte-stable against committed fixture."""
    from docs.deps_section_writer import render_section
    from docs.deps_resolver import build_doc_graph
    g = build_doc_graph("hermes", SERVICES_DIR)
    rendered = render_section(g)
    golden = (FIXTURE_DIR / "hermes.deps_section.md").read_text()
    assert rendered == golden, "Hermes deps section drift — update the fixture."


def test_section_contains_canonical_headings():
    from docs.deps_section_writer import render_section
    from docs.deps_resolver import build_doc_graph
    g = build_doc_graph("hermes", SERVICES_DIR)
    text = render_section(g)
    for heading in (
        "## 5. Dependencies & Integrations",
        "### 5.1. Current — Upstream",
        "### 5.2. Current — Downstream",
        "### 5.3. Architecture diagram",
        "### 5.4. Future — Missing pair integrations",
        "### 5.5. Future — Candidate new services",
        "### 5.6. Future — Unused features in this service",
    ):
        assert heading in text


def test_section_uses_two_column_table():
    """New table shape is Service | Category (only 2 columns)."""
    from docs.deps_section_writer import render_section
    from docs.deps_resolver import build_doc_graph
    g = build_doc_graph("hermes", SERVICES_DIR)
    text = render_section(g)
    assert "| Service | Category |" in text
    assert "| Service | Type | Mechanism" not in text


def test_section_emits_empty_table_placeholder():
    """A graph with no upstream emits the explicit `_No upstream calls._` line."""
    from docs.deps_section_writer import render_section
    from docs.deps_resolver import DepGraph
    g = DepGraph(focus="kong", category="infra", port_var=None, source="single")
    text = render_section(g)
    assert "_No upstream calls._" in text
    assert "_No downstream consumers._" in text


def test_generated_section_does_not_expose_maintainer_instructions():
    """Reader-facing service docs must not expose generator implementation details."""
    from docs.deps_resolver import build_doc_graph
    from docs.deps_section_writer import render_section
    from pathlib import Path

    services_root = Path(__file__).resolve().parents[2] / "services"
    graph = build_doc_graph("stt-provider", services_root)
    text = render_section(graph, position=5)
    assert "Auto-generated section" not in text
    assert "bootstrapper.docs.regen" not in text


def test_generated_section_uses_surface_neutral_diagram_wording():
    from docs.deps_resolver import build_doc_graph
    from docs.deps_section_writer import render_section

    text = render_section(build_doc_graph("hermes", SERVICES_DIR))
    assert "Open the full-size diagram" in text
    assert "interactive HTML diagram" not in text


def _synthetic_graph(upstream=(), downstream=()):
    from docs.deps_resolver import DepGraph
    return DepGraph(focus="probe", category="apps", port_var=None, source="container",
                    upstream=tuple(upstream), downstream=tuple(downstream))


def test_status_column_appears_only_for_qualified_edges():
    """#1273: a table gains a Status column only when it holds an optional or
    planned edge, conditions are shown, a pipe cannot break the table, and a
    table holding a planned row says what planned means."""
    from docs.deps_resolver import DepEdge
    from docs.deps_section_writer import render_section

    redis = DepEdge("redis", "upstream", other_category="data")
    plain = render_section(_synthetic_graph([redis]))
    qualified = render_section(_synthetic_graph(
        [redis, DepEdge("ray", "upstream", other_category="infra",
                        status="optional", condition="RAY_SOURCE=a|b")],
        [DepEdge("kong", "downstream", other_category="infra", status="planned")],
    ))

    from docs.deps_section_writer import _PLANNED_NOTE

    assert (
        "| Service | Category |\n|---|---|\n| redis | data |" in plain,
        "Status" in plain,
        _PLANNED_NOTE in plain,
        qualified.count(_PLANNED_NOTE),
        "| redis | data | current |" in qualified,
        "| ray | infra | optional: RAY_SOURCE=a\\|b |" in qualified,
        "| kong | infra | planned |" in qualified,
    ) == (True, False, False, 1, True, True, True)

