from pathlib import Path

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
