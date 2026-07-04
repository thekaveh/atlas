"""Tests for bootstrapper.docs.diagram_renderer — clustered layout."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

SERVICES_DIR = REPO_ROOT / "services"
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_renders_svg_with_focus_label():
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg
    g = build_doc_graph("hermes", SERVICES_DIR)
    svg = render_svg(g)
    assert svg.startswith("<svg")
    assert "HERMES" in svg


def test_renders_html_uses_local_monospace_stack():
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_html
    g = build_doc_graph("hermes", SERVICES_DIR)
    html = render_html(g)
    assert "JetBrains Mono" in html
    assert "JetBrains+Mono" not in html
    assert "fonts.googleapis.com" not in html
    assert "<svg" in html


def test_svg_byte_deterministic():
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg
    g = build_doc_graph("hermes", SERVICES_DIR)
    a = render_svg(g)
    b = render_svg(g)
    assert a == b


def test_svg_is_well_formed_xml_across_services():
    """Every doc folder's SVG parses as well-formed XML."""
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg

    for svc in ("hermes", "kong", "litellm", "redis", "stt-provider",
                "tts-provider", "ollama", "weaviate", "minio", "supabase"):
        svg = render_svg(build_doc_graph(svc, SERVICES_DIR))
        try:
            ET.fromstring(svg)
        except ET.ParseError as exc:
            raise AssertionError(f"{svc}: malformed SVG — {exc}") from None


def test_focus_box_has_glow_filter():
    """The focus box uses a glow effect (filter or shadow)."""
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg
    g = build_doc_graph("hermes", SERVICES_DIR)
    svg = render_svg(g)
    assert "feGaussianBlur" in svg or "stdDeviation" in svg or "drop-shadow" in svg.lower()


def test_clusters_grouped_by_category():
    """Hermes upstream has services in 'llm' + 'media' categories — both
    categories should appear as cluster headers in the SVG."""
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg
    g = build_doc_graph("hermes", SERVICES_DIR)
    svg = render_svg(g)
    # Match the category HEADER text element specifically (>llm<, >media<), not
    # any substring — the `litellm` pill contains "llm" and would satisfy a
    # naive `"llm" in svg`, making this guard vacuous. ">llm<" is not a substring
    # of ">litellm<".
    assert ">llm<" in svg, "expected an 'llm' category cluster header"
    assert ">media<" in svg, "expected a 'media' category cluster header"


def test_no_required_sublabel():
    """Pills no longer carry the old 'required' sublabel."""
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg
    g = build_doc_graph("hermes", SERVICES_DIR)
    svg = render_svg(g)
    assert "required" not in svg.lower()


def test_empty_lane_placeholder():
    """A focus with no upstream renders the lane with an empty placeholder."""
    from docs.deps_resolver import DepGraph
    from docs.diagram_renderer import render_svg
    g = DepGraph(focus="lonely", category="infra", port_var=None, source="single")
    svg = render_svg(g)
    # Assert the real placeholder text. A naive `"none" in svg.lower()` is
    # vacuous — every SVG carries a background grid `<path fill="none" .../>`,
    # so it would pass even if the placeholder were removed.
    assert "— none —" in svg


def test_one_edge_per_cluster_not_per_pill():
    """For kong (15+ downstream), edge count is bounded by cluster count (≤6),
    not by individual pill count."""
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg
    g = build_doc_graph("kong", SERVICES_DIR)
    svg = render_svg(g)
    line_count = svg.count("<line")
    assert line_count <= 12  # 6 upstream lanes max + 6 downstream lanes max


def test_bidirectional_annotation():
    """Bidirectional edges (Hermes↔LiteLLM) get a bidirectional marker, not a
    duplicated arrow."""
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg
    g = build_doc_graph("hermes", SERVICES_DIR)
    svg = render_svg(g)
    assert "↔" in svg or "bidirectional" in svg.lower()


def test_summary_cards_in_html():
    """The HTML wrapper includes three summary cards."""
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_html
    g = build_doc_graph("hermes", SERVICES_DIR)
    html = render_html(g)
    assert "Calls" in html
    assert "Consumers" in html
    assert "Categories" in html


def test_svg_matches_golden_snapshot():
    """Hermes is the snapshot — must match committed fixture."""
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg

    g = build_doc_graph("hermes", SERVICES_DIR)
    rendered = render_svg(g)
    golden = (FIXTURE_DIR / "hermes.architecture.svg").read_text()
    assert rendered == golden, (
        "Hermes SVG drift. To accept new output:\n"
        "  PYTHONPATH=bootstrapper python -c \"from docs.deps_resolver import build_doc_graph; "
        "from docs.diagram_renderer import render_svg; from pathlib import Path; "
        "Path('bootstrapper/tests/fixtures/hermes.architecture.svg').write_text("
        "render_svg(build_doc_graph('hermes', Path('services'))))\"\n"
    )
