from pathlib import Path
import re

import pytest

from scripts.docs.render_diagrams import (
    diagram_source_fingerprint,
    extract_svg,
    png_source_fingerprint,
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
