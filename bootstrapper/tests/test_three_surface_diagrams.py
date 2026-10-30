import hashlib
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

import pytest

from scripts.docs.manifest import load_manifest
from scripts.docs.render_diagrams import (
    diagram_source_fingerprint,
    extract_svg,
    png_source_fingerprint,
    render_all,
    svg_to_png,
)


ROOT = Path(__file__).resolve().parents[2]


def test_extract_svg_sanitizes_html_entities_for_xml() -> None:
    html = "<html><svg><text>Atlas &middot; AI &amp; Data</text></svg></html>"

    svg = extract_svg(html)

    assert svg == "<svg><text>Atlas · AI &amp; Data</text></svg>"


def test_extract_svg_rejects_missing_inline_svg() -> None:
    with pytest.raises(ValueError, match="inline SVG"):
        extract_svg("<html><body>No diagram</body></html>")


@pytest.mark.parametrize("kind", ["page", "diagram"])
def test_direct_diagram_render_rejects_escaping_ids_before_any_output(tmp_path, kind) -> None:
    from scripts.docs.manifest import DiagramEntry, Manifest, ManifestError, Section

    master = tmp_path / "diagram.html"
    master.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    page_id = "../escaped" if kind == "page" else "overview"
    diagram_id = "../../escaped" if kind == "diagram" else "overview"
    manifest = Manifest(
        ("repo", "site", "wiki"), "baked", page_id,
        (Section(page_id, "1", "Overview", "README.md"),),
        (DiagramEntry(diagram_id, "diagram.html"),),
    )
    site = tmp_path / "projection" / "site" / "assets" / "img"
    png = tmp_path / "committed" / "img"

    with pytest.raises(ManifestError, match="ID.*slug"):
        render_all(manifest, tmp_path, site, png, check_png=True)

    assert not (tmp_path / "projection").exists()
    assert not (tmp_path / "committed").exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["diagram.html"]


def test_extract_svg_normalizes_host_html_indentation() -> None:
    html = (
        "<div>\n"
        "  <svg xmlns=\"http://www.w3.org/2000/svg\">\n"
        "    <rect width=\"20\" height=\"20\"/>\n"
        "  </svg>\n"
        "</div>\n"
    )

    svg = extract_svg(html)

    assert "\n  <rect" in svg
    assert svg.endswith("\n</svg>")


def test_svg_to_png_writes_png_signature(tmp_path: Path) -> None:
    output = tmp_path / "diagram.png"

    svg_to_png(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
        '<rect width="20" height="20" fill="#020617"/></svg>',
        output,
        width=40,
    )

    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert png_source_fingerprint(output) == diagram_source_fingerprint(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
        '<rect width="20" height="20" fill="#020617"/></svg>',
        width=40,
    )


def test_default_png_export_is_high_resolution_with_matching_provenance(tmp_path: Path) -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"><rect width="20" height="10" fill="#020617"/></svg>'
    output = tmp_path / "diagram.png"

    svg_to_png(svg, output)

    assert struct.unpack(">II", output.read_bytes()[16:24]) == (3600, 1800)
    assert png_source_fingerprint(output) == diagram_source_fingerprint(svg, width=3600)
    assert diagram_source_fingerprint(svg) == diagram_source_fingerprint(svg, width=3600)


def test_platform_html_and_svg_masters_stay_synchronized() -> None:
    html_master = (ROOT / "docs" / "diagrams" / "architecture.html").read_text(
        encoding="utf-8"
    )
    svg_master = (ROOT / "docs" / "diagrams" / "architecture.svg").read_text(
        encoding="utf-8"
    )

    assert extract_svg(html_master) == extract_svg(svg_master)


def test_platform_diagram_includes_vllm_metal_as_a_litellm_upstream() -> None:
    for name in ("architecture.html", "architecture.svg"):
        master = (ROOT / "docs" / "diagrams" / name).read_text(encoding="utf-8")
        assert "vLLM Metal" in master
        assert "managed Apple-silicon host" in master
        assert "M 305 574 C 390 525, 490 525, 575 574" in master
    html = (ROOT / "docs" / "diagrams" / "architecture.html").read_text(
        encoding="utf-8"
    )
    assert "Source diagram:" not in html
    assert "architecture-diagram</code> skill" not in html


def test_platform_consumer_routes_end_inside_the_litellm_node() -> None:
    def route_points(route: str) -> set[tuple[int, int]]:
        coordinates = [
            tuple(map(int, point))
            for point in re.findall(r"(?:M|L) (\d+) (\d+)", route)
        ]
        covered: set[tuple[int, int]] = set()
        for (x1, y1), (x2, y2) in zip(coordinates, coordinates[1:]):
            if x1 == x2:
                covered.update((x1, y) for y in range(min(y1, y2), max(y1, y2) + 1))
            else:
                assert y1 == y2, "consumer routes must stay orthogonal"
                covered.update((x, y1) for x in range(min(x1, x2), max(x1, x2) + 1))
        return covered

    for name in ("architecture.html", "architecture.svg"):
        master = (ROOT / "docs" / "diagrams" / name).read_text(encoding="utf-8")
        routes = {}
        for source in ("Open WebUI", "Backend API", "n8n + Workers"):
            route = re.search(
                rf'<(?:line|path) data-source="{re.escape(source)}" '
                r'data-target="LiteLLM Gateway"[^>]*/>',
                master,
            )
            assert route, f"missing {source} -> LiteLLM route in {name}"
            endpoints = re.findall(
                r'(?:x2="|L )(\d+)(?:" y2="| )(\d+)', route.group()
            )
            assert endpoints, route.group()
            x, y = map(int, endpoints[-1])
            assert 75 <= x <= 305 and 548 <= y <= 600, route.group()
            routes[source] = route.group()
        backend_points = route_points(routes["Backend API"])
        n8n_points = route_points(routes["n8n + Workers"])
        assert backend_points.isdisjoint(n8n_points)
        clearance = min(
            abs(x1 - x2) + abs(y1 - y2)
            for x1, y1 in backend_points
            for x2, y2 in n8n_points
        )
        assert clearance >= 12


def test_platform_diagram_keeps_control_plane_out_of_kong_request_paths() -> None:
    svg = (ROOT / "docs" / "diagrams" / "architecture.svg").read_text(
        encoding="utf-8"
    )

    assert "./start.sh wizard" not in svg
    assert "HTTP APIs via Kong" in svg


def test_platform_diagram_does_not_route_kong_to_loopback_only_zeppelin() -> None:
    svg = (ROOT / "docs" / "diagrams" / "architecture.svg").read_text(
        encoding="utf-8"
    )

    assert 'x1="1200" y1="295" x2="1200" y2="308"' not in svg


def test_platform_diagram_routes_kong_to_each_displayed_agent() -> None:
    svg = (ROOT / "docs" / "diagrams" / "architecture.svg").read_text(
        encoding="utf-8"
    )

    for x in (140, 390, 640, 890, 1140):
        assert f'x1="{x}" y1="390" x2="{x}" y2="428"' in svg


def test_standalone_exports_have_svg_namespace_without_rewriting_pngs(tmp_path: Path) -> None:
    manifest = load_manifest(ROOT / "docs" / "manifest.yaml", ROOT)
    png_dir = ROOT / "docs" / "diagrams" / "img"
    before = {
        entry.id: (
            hashlib.sha256((png_dir / f"{entry.id}.png").read_bytes()).hexdigest(),
            (png_dir / f"{entry.id}.png").stat().st_mode,
        )
        for entry in manifest.diagrams
    }

    render_all(manifest, ROOT, tmp_path, png_dir, check_png=True)

    invalid = [
        entry.id
        for entry in manifest.diagrams
        if ET.parse(tmp_path / f"{entry.id}.svg").getroot().tag
        != "{http://www.w3.org/2000/svg}svg"
    ]
    assert invalid == []
    assert before == {
        entry.id: (
            hashlib.sha256((png_dir / f"{entry.id}.png").read_bytes()).hexdigest(),
            (png_dir / f"{entry.id}.png").stat().st_mode,
        )
        for entry in manifest.diagrams
    }

def _poster_contains(point, box):
    x, y, width, height = box
    return x <= point[0] <= x + width and y <= point[1] <= y + height


def _poster_component_box(svg, label):
    node, = (item for item in svg.findall(".//{*}text") if "".join(item.itertext()) == label)
    point = float(node.get("x")), float(node.get("y"))
    candidates = [
        tuple(float(rect.get(key)) for key in ("x", "y", "width", "height"))
        for rect in svg.findall(".//{*}rect[@rx='6']")
    ]
    boxes = {box for box in candidates if _poster_contains(point, box)}
    assert len(boxes) == 1
    return boxes.pop()


def _poster_arrow_endpoints(svg):
    routes = []
    for element in svg.findall(".//*[@marker-end]"):
        if element.tag.rsplit("}", 1)[-1] == "line":
            points = [
                (float(element.get("x1")), float(element.get("y1"))),
                (float(element.get("x2")), float(element.get("y2"))),
            ]
        else:
            coordinates = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", element.get("d", ""))]
            points = list(zip(coordinates[::2], coordinates[1::2]))
        if points:
            routes.append((points[0], points[-1]))
    return routes


@pytest.mark.parametrize("name", ["architecture.html", "architecture.svg"])
@pytest.mark.parametrize("case", [
    ("JupyterHub", "Hermes Agent", "OpenClaw"),
    ("Backend API", "Weaviate", "Neo4j"),
])
def test_platform_current_arrows_reach_implemented_targets(name, case):
    master = ROOT / "docs" / "diagrams" / name
    svg = ET.fromstring(extract_svg(master.read_text(encoding="utf-8")))
    source, target, unsupported = (_poster_component_box(svg, label) for label in case)
    destinations = [
        end for start, end in _poster_arrow_endpoints(svg)
        if _poster_contains(start, source)
    ]

    assert any(_poster_contains(point, target) for point in destinations), case
    assert not any(_poster_contains(point, unsupported) for point in destinations), case



def _perspective_svg(slug):
    from bootstrapper.docs.sitegen.model import load_docs_model
    from bootstrapper.docs.sitegen.pages import architecture_pages

    masters = architecture_pages(load_docs_model(ROOT))
    return ET.fromstring(extract_svg(masters[ROOT / "docs" / "architecture" / f"{slug}.html"]))


def _perspective_connector(svg, source, target):
    connector, = (
        item for item in svg.findall(".//*[@marker-end]")
        if (item.get("data-source"), item.get("data-target")) == (source, target)
    )
    if connector.tag.rsplit("}", 1)[-1] == "line":
        points = [tuple(float(connector.get(key)) for key in pair)
                  for pair in (("x1", "y1"), ("x2", "y2"))]
    else:
        assert connector.tag.rsplit("}", 1)[-1] == "polyline"
        values = list(map(float, re.findall(r"-?\d+(?:\.\d+)?", connector.get("points"))))
        points = list(zip(values[::2], values[1::2]))
    return connector, points


def _perspective_box_polygon(box, padding=0):
    x, y, width, height = box
    left, top = x - padding, y - padding
    right, bottom = x + width + padding, y + height + padding
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


def _perspective_polygons_overlap(first, second):
    # Separating axes work for both connector segments and convex marker triangles.
    for polygon in (first, second):
        for start, end in zip(polygon, polygon[1:] + polygon[:1]):
            axis = end[1] - start[1], start[0] - end[0]
            first_projection = [x * axis[0] + y * axis[1] for x, y in first]
            second_projection = [x * axis[0] + y * axis[1] for x, y in second]
            if max(first_projection) <= min(second_projection) or max(second_projection) <= min(first_projection):
                return False
    return True


def _perspective_marker_polygon(svg, connector, points):
    from math import hypot

    marker_id = connector.get("marker-end").removeprefix("url(#").removesuffix(")")
    marker, = svg.findall(f".//{{*}}marker[@id='{marker_id}']")
    assert marker.get("orient") == "auto" and marker.get("viewBox") is None
    scale = float(connector.get("stroke-width", "1")) if marker.get("markerUnits", "strokeWidth") == "strokeWidth" else 1
    ref_x, ref_y = float(marker.get("refX", "0")), float(marker.get("refY", "0"))
    start, end = points[-2:]
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = hypot(dx, dy)
    ux, uy = dx / length, dy / length
    polygon, = marker.findall("{*}polygon")
    values = list(map(float, re.findall(r"-?\d+(?:\.\d+)?", polygon.get("points"))))
    return [
        (end[0] + scale * ((x - ref_x) * ux - (y - ref_y) * uy),
         end[1] + scale * ((x - ref_x) * uy + (y - ref_y) * ux))
        for x, y in zip(values[::2], values[1::2])
    ]


def _perspective_glyph_box(svg, label, tmp_path):
    from copy import deepcopy
    from PIL import Image

    isolated = ET.Element(svg.tag, svg.attrib)
    isolated.append(deepcopy(label))
    output = tmp_path / "label.png"
    svg_to_png(ET.tostring(isolated, encoding="unicode"), output)
    with Image.open(output) as image:
        bounds = image.getchannel("A").getbbox()
        scale = image.width / float(svg.get("viewBox").split()[2])
    assert bounds is not None
    left, top, right, bottom = (value / scale for value in bounds)
    return left, top, right - left, bottom - top


def _perspective_backing_box(svg, text, glyph_box):
    from cairosvg.colors import color

    left, top, width, height = glyph_box
    corners = ((left, top), (left + width, top + height))
    candidates = []
    # Descendant order is SVG paint order even when labels use nested groups.
    for node in svg.iter():
        if node is text:
            break
        if node.tag.rsplit("}", 1)[-1] != "rect":
            continue
        fill = node.get("fill", "black")
        if fill.startswith("url("):
            continue
        alpha = color(fill)[3] * float(node.get("opacity", "1")) * float(node.get("fill-opacity", "1"))
        if alpha != 1:
            continue
        box = tuple(float(node.get(key, "0")) for key in ("x", "y", "width", "height"))
        if all(_poster_contains(point, box) for point in corners):
            candidates.append(box)
    assert candidates, ("no opaque backing covers label glyphs", text.text, glyph_box)
    return min(candidates, key=lambda box: box[2] * box[3])


_PERSPECTIVE_CLEARANCE_CASES = [
    ("security-auth-secrets-boundary", "Runtime Secrets", "Protected APIs"),
    ("service-admission-workflow", "compose.yml", "CI"),
]


@pytest.mark.parametrize("slug,source,target", _PERSPECTIVE_CLEARANCE_CASES)
def test_perspective_connectors_clear_all_opaque_cards(slug, source, target):
    svg = _perspective_svg(slug)
    _, points = _perspective_connector(svg, source, target)
    cards = svg.findall(".//{*}rect[@rx='6'][@fill='#0f172a']")
    assert cards
    for card in cards:
        box = tuple(float(card.get(key)) for key in ("x", "y", "width", "height"))
        for start, end in zip(points, points[1:]):
            assert not _perspective_polygons_overlap([start, end], _perspective_box_polygon(box)), (source, target, box, start, end)


@pytest.mark.parametrize("slug,source,target", _PERSPECTIVE_CLEARANCE_CASES)
def test_perspective_complete_markers_clear_card_strokes(slug, source, target):
    svg = _perspective_svg(slug)
    connector, points = _perspective_connector(svg, source, target)
    triangle = _perspective_marker_polygon(svg, connector, points)
    cards = svg.findall(".//{*}rect[@rx='6'][@stroke]")
    assert cards
    for card in cards:
        box = tuple(float(card.get(key)) for key in ("x", "y", "width", "height"))
        half_stroke = float(card.get("stroke-width", "1")) / 2
        assert not _perspective_polygons_overlap(triangle, _perspective_box_polygon(box, half_stroke)), (source, target, box, triangle)


@pytest.mark.parametrize("label", ["invoke", "normalize", "synthesize", "filter", "generate", "assemble", "launch"])
def test_lifecycle_label_glyphs_and_backings_clear_cards(label, tmp_path):
    svg = _perspective_svg("bootstrapper-lifecycle")
    text, = (node for node in svg.findall(".//{*}text") if "".join(node.itertext()) == label)
    glyph_box = _perspective_glyph_box(svg, text, tmp_path)
    backing_box = _perspective_backing_box(svg, text, glyph_box)
    cards = svg.findall(".//{*}rect[@rx='6'][@stroke]")
    assert cards
    for card in cards:
        card_box = tuple(float(card.get(key)) for key in ("x", "y", "width", "height"))
        padding = float(card.get("stroke-width", "1")) / 2 + 2
        for envelope in (backing_box, glyph_box):
            assert not _perspective_polygons_overlap(_perspective_box_polygon(envelope), _perspective_box_polygon(card_box, padding)), (label, envelope, card_box)
