"""Tests for bootstrapper.docs.diagram_renderer — clustered layout."""

from __future__ import annotations

import sys
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

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
    assert "two-way" in svg.lower()
    assert "↔" not in svg


def test_summary_cards_in_html():
    """The HTML wrapper includes three summary cards."""
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_html
    g = build_doc_graph("hermes", SERVICES_DIR)
    html = render_html(g)
    assert "Calls" in html
    assert "Consumers" in html
    assert "Categories" in html


def test_every_service_svg_is_landscape():
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg

    for service_dir in sorted(SERVICES_DIR.iterdir()):
        if not (service_dir / "README.md").is_file():
            continue
        svg = render_svg(build_doc_graph(service_dir.name, SERVICES_DIR))
        match = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
        assert match is not None, service_dir.name
        width, height = map(int, match.groups())
        assert width > height, f"{service_dir.name}: {width}x{height} is not landscape"


def test_public_html_omits_regeneration_instructions():
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_html

    html = render_html(build_doc_graph("hermes", SERVICES_DIR))
    assert "Regenerate:" not in html
    assert "bootstrapper.docs.regen" not in html


def test_renderer_docstring_describes_current_docs_tree():
    from docs import diagram_renderer

    assert "retired afterwards" not in (diagram_renderer.__doc__ or "")


def test_long_service_labels_shrink_to_fit_fixed_boxes():
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg

    adapter = render_svg(
        build_doc_graph("docling-lightrag-adapter", SERVICES_DIR)
    )
    assert 'font-size="12" font-weight="700"' in adapter

    lightrag = render_svg(build_doc_graph("lightrag", SERVICES_DIR))
    assert (
        'font-size="7" text-anchor="middle">docling-lightrag-adapter</text>'
        in lightrag
    )


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


def test_optional_and_planned_pills_are_dashed_and_legended():
    """#1273: optional and planned pills get distinct dashed outlines, a
    cluster with no current edge gets a dashed arrow, and the legend gains a
    key; an all-current diagram carries none of these."""
    from docs.deps_resolver import DepEdge, DepGraph
    from docs.diagram_renderer import STATUS_DASHES, render_svg

    def graph(*extra_up, down=()):
        return DepGraph(
            focus="probe", category="apps", port_var=None, source="container",
            upstream=(DepEdge("redis", "upstream", other_category="data"), *extra_up),
            downstream=tuple(down),
        )

    plain = render_svg(graph())
    qualified = render_svg(graph(
        DepEdge("ray", "upstream", other_category="infra", status="optional"),
        down=[DepEdge("kong", "downstream", other_category="infra", status="planned")],
    ))
    dashes = [f'stroke-dasharray="{STATUS_DASHES[s]}"' for s in ("optional", "planned")]

    assert (
        [plain.count(d) for d in dashes], ">optional<" in plain, ">planned<" in plain,
        [qualified.count(d) for d in dashes], ">optional<" in qualified, ">planned<" in qualified,
    ) == ([0, 0], False, False, [3, 3], True, True)


def test_pointer_doc_subtitle_fits_focus_box():
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg

    svg = ET.fromstring(render_svg(build_doc_graph("multi2vec-clip", SERVICES_DIR)))
    ns = {"svg": "http://www.w3.org/2000/svg"}
    focus = svg.find("svg:g[@class='focus']", ns)
    box = focus.find("svg:rect", ns)
    subtitle = focus.findall("svg:text", ns)[-1]
    half_width = len(subtitle.text) * float(subtitle.attrib["font-size"]) * 0.62 / 2
    center = float(subtitle.attrib["x"])
    left = float(box.attrib["x"])
    right = left + float(box.attrib["width"])
    assert left < center - half_width < center + half_width < right


def _line_crosses_box(line, box):
    x1, y1, x2, y2 = (float(line.get(k)) for k in ("x1", "y1", "x2", "y2"))
    left, top, right, bottom = box
    enter, leave = 0.0, 1.0
    for origin, delta, low, high in ((x1, x2 - x1, left, right), (y1, y2 - y1, top, bottom)):
        if delta == 0:
            if not low <= origin <= high:
                return False
            continue
        near, far = sorted(((low - origin) / delta, (high - origin) / delta))
        enter, leave = max(enter, near), min(leave, far)
    return enter <= leave


def _opaque_rect_covers_box(rect, box):
    left, top, right, bottom = box
    x, y = float(rect.get("x", 0)), float(rect.get("y", 0))
    return (
        rect.get("fill") == "#020617"
        and float(rect.get("opacity", 1)) == float(rect.get("fill-opacity", 1)) == 1
        and x <= left - 1 and y <= top - 1
        and x + float(rect.get("width")) >= right + 1
        and y + float(rect.get("height")) >= bottom + 1
    )


def _label_box(label):
    x, y, size = (float(label.get(k)) for k in ("x", "y", "font-size"))
    half_width = len(label.text) * size * 0.62 / 2
    return (x - half_width, y - size, x + half_width, y + 2)


def _label_backings(elements, label):
    first_connector = next((i for i, e in enumerate(elements) if e.get("marker-end")), -1)
    index = elements.index(label)
    box = _label_box(label)
    return [(i, e) for i, e in enumerate(elements)
            if first_connector < i < index and e.tag.endswith("}rect")
            and _opaque_rect_covers_box(e, box)]


def _annotation_is_clear(elements, label):
    box = _label_box(label)
    crossing = [i for i, e in enumerate(elements)
                if e.tag.endswith("}line") and _line_crosses_box(e, box)]
    if not crossing:
        return True
    return any(i > max(crossing) for i, _ in _label_backings(elements, label))


@pytest.mark.parametrize("service", ["backend", "grafana", "hermes", "kong", "lightrag", "litellm", "n8n", "prometheus"])
def test_two_way_annotations_have_clear_glyph_space(service):
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg

    elements = list(ET.fromstring(render_svg(build_doc_graph(service, SERVICES_DIR))).iter())
    labels = [e for e in elements if e.tag.endswith("}text") and e.text == "two-way"]
    assert labels, service
    for label in labels:
        assert _annotation_is_clear(elements, label), (service, label.attrib)


def _marker_vertices(line, marker):
    x1, y1, x2, y2 = (float(line.get(k)) for k in ("x1", "y1", "x2", "y2"))
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    scale = float(line.get("stroke-width", 1)) if marker.get("markerUnits", "strokeWidth") == "strokeWidth" else 1
    ref_x, ref_y = float(marker.get("refX", 0)), float(marker.get("refY", 0))
    coordinates = iter(map(float, re.split(r"[\s,]+", marker[0].get("points"))))
    points = zip(coordinates, coordinates)
    return [(x2 + scale * ((x - ref_x) * dx - (y - ref_y) * dy) / length,
             y2 + scale * ((x - ref_x) * dy + (y - ref_y) * dx) / length)
            for x, y in points]


def _polygons_overlap(first, second):
    for polygon in (first, second):
        for start, end in zip(polygon, polygon[1:] + polygon[:1]):
            nx, ny = end[1] - start[1], start[0] - end[0]
            projections = [[nx * x + ny * y for x, y in points] for points in (first, second)]
            if max(projections[0]) <= min(projections[1]) or max(projections[1]) <= min(projections[0]):
                return False
    return True


def _rect_vertices(rect):
    x, y, w, h = (float(rect.get(k)) for k in ("x", "y", "width", "height"))
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _assert_two_way_marker_clearance(root):
    elements = list(root.iter())
    marker = root.find("{http://www.w3.org/2000/svg}defs/{http://www.w3.org/2000/svg}marker")
    triangles = [(i, _marker_vertices(e, marker)) for i, e in enumerate(elements) if e.get("marker-end")]
    for label in (e for e in elements if e.text == "two-way"):
        for backing_index, rect in _label_backings(elements, label):
            for marker_index, triangle in triangles:
                assert marker_index >= backing_index or not _polygons_overlap(_rect_vertices(rect), triangle), (label.attrib, triangle)


@pytest.mark.parametrize("service", ["backend", "grafana", "hermes", "kong", "lightrag", "litellm", "n8n", "prometheus"])
def test_two_way_backings_preserve_complete_arrowheads(service):
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg

    _assert_two_way_marker_clearance(ET.fromstring(render_svg(build_doc_graph(service, SERVICES_DIR))))


def test_horizontal_two_way_annotation_clears_both_arrowheads():
    from docs.deps_resolver import DepEdge, DepGraph
    from docs.diagram_renderer import render_svg

    graph = DepGraph(
        focus="probe", category="apps", port_var=None, source="container",
        upstream=(
            *(DepEdge(name, "upstream", other_category="infra") for name in ("a", "b", "c")),
            DepEdge("peer", "upstream", other_category="data", bidirectional=True),
            DepEdge("d", "upstream", other_category="llm"),
        ),
        downstream=(DepEdge("peer", "downstream", other_category="data", bidirectional=True),),
    )
    root = ET.fromstring(render_svg(graph))
    elements = list(root)
    label = next(e for e in elements if e.text == "two-way")
    assert any(e.get("marker-end") and e.get("x1") == "360" and e.get("y1") == e.get("y2")
               for e in elements)
    assert _annotation_is_clear(elements, label)
    _assert_two_way_marker_clearance(root)


def test_marker_clearance_allows_non_adjacent_grouped_annotation():
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg

    root = ET.fromstring(render_svg(build_doc_graph("hermes", SERVICES_DIR)))
    label = next(e for e in root if e.text == "two-way")
    position = list(root).index(label)
    root.insert(position, ET.Element("{http://www.w3.org/2000/svg}g"))
    _assert_two_way_marker_clearance(root)
    root.remove(label)
    group = ET.Element("{http://www.w3.org/2000/svg}g")
    group.append(label)
    root.insert(position + 1, group)
    assert _annotation_is_clear(list(root.iter()), label)
    _assert_two_way_marker_clearance(root)


def test_marker_clearance_allows_routed_clear_annotation_without_backing():
    from docs.deps_resolver import build_doc_graph
    from docs.diagram_renderer import render_svg

    root = ET.fromstring(render_svg(build_doc_graph("grafana", SERVICES_DIR)))
    backing = next(e for e in root if e.get("width") == "44" and e.get("height") == "14")
    root.remove(backing)
    label = next(e for e in root if e.text == "two-way")
    assert _annotation_is_clear(list(root), label)
    _assert_two_way_marker_clearance(root)
