"""DepGraph → HTML+SVG renderer — clustered-by-category layout.

Visual design (see git log around 2026-05-22 for the design doc; the
``docs/superpowers/`` tree was retired afterwards):
- 3-lane layout: upstream | focus | downstream.
- Each non-focus lane groups services into category clusters.
- One edge per cluster (not per pill).
- Focus box has a glow (filter blur + stroke).
- Empty lanes show an italic "— none —" placeholder.
- Legend bar + 3 summary cards below the diagram.

Output is byte-deterministic for the same DepGraph.
"""

from __future__ import annotations

import html as html_mod
import sys
from collections import OrderedDict
from pathlib import Path
from string import Template

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "bootstrapper"))

from services.topology import CATEGORY_COLORS, CATEGORY_FILLS  # noqa: E402

from .deps_resolver import DepEdge, DepGraph  # noqa: E402

TEMPLATE_DIR = Path(__file__).parent / "templates"

# ───── Geometry ──────────────────────────────────────────────────────────
LANE_W = 240
LANE_GAP = 60
FOCUS_W = 200
FOCUS_H = 70
PILL_H = 22
PILL_GAP = 4
CLUSTER_PADDING_Y = 8
CLUSTER_HEADER_H = 16
CLUSTER_GAP = 10
LANE_HEADER_Y = 36
LANE_TOP_Y = 64
LEGEND_H = 28
CARDS_H = 56
WIDTH = LANE_W + LANE_GAP + FOCUS_W + LANE_GAP + LANE_W + 120  # 880

CATEGORY_ORDER = ("infra", "data", "llm", "media", "agents", "apps", "external")


def render_svg(graph: DepGraph) -> str:
    """Render the architecture SVG. Pure function of graph state."""
    up_clusters = _cluster_by_category(graph.upstream)
    down_clusters = _cluster_by_category(graph.downstream)

    up_height = _clusters_height(up_clusters)
    down_height = _clusters_height(down_clusters)
    body_height = max(up_height, down_height, FOCUS_H + 40)
    total_height = LANE_TOP_Y + body_height + LEGEND_H + 20

    parts: list[str] = []
    # Match the architecture-diagram skill's design system: JetBrains Mono on
    # the root SVG, slate-950 background painted BEFORE the grid pattern so
    # the dark canvas reads consistently when the SVG is embedded inline
    # without the surrounding HTML wrapper.
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {total_height}" '
        f'font-family="\'JetBrains Mono\', \'Fira Code\', Menlo, Consolas, monospace">'
    )
    parts.append(_defs(graph))
    parts.append(f'<rect width="{WIDTH}" height="{total_height}" fill="#020617"/>')
    parts.append(f'<rect width="{WIDTH}" height="{total_height}" fill="url(#grid)"/>')

    # Lane headers
    parts.append(_text(60 + LANE_W // 2, LANE_HEADER_Y, "Upstream (calls)",
                       size=10, color="#94a3b8", anchor="middle", weight=600, letter_spacing=0.08))
    focus_x = 60 + LANE_W + LANE_GAP
    parts.append(_text(focus_x + FOCUS_W // 2, LANE_HEADER_Y, "Focus",
                       size=10, color="#94a3b8", anchor="middle", weight=600, letter_spacing=0.08))
    down_x = focus_x + FOCUS_W + LANE_GAP
    parts.append(_text(down_x + LANE_W // 2, LANE_HEADER_Y, "Downstream (consumers)",
                       size=10, color="#94a3b8", anchor="middle", weight=600, letter_spacing=0.08))

    # Edges drawn first (behind clusters)
    parts.extend(_edges(graph, up_clusters, down_clusters, body_height))

    # Clusters
    parts.append(_render_lane(60, LANE_TOP_Y, LANE_W, up_clusters, "upstream"))
    parts.append(_render_lane(down_x, LANE_TOP_Y, LANE_W, down_clusters, "downstream"))

    # Focus box (centered vertically in body)
    focus_y = LANE_TOP_Y + (body_height - FOCUS_H) // 2
    parts.append(_focus_box(focus_x, focus_y, FOCUS_W, FOCUS_H, graph))

    # Legend
    legend_y = LANE_TOP_Y + body_height + 10
    parts.append(_legend(WIDTH // 2, legend_y))

    parts.append("</svg>")
    return "\n".join(parts)


def render_html(graph: DepGraph) -> str:
    tmpl = Template((TEMPLATE_DIR / "architecture.html.tmpl").read_text(encoding="utf-8"))
    svg = render_svg(graph)
    cat_color = CATEGORY_COLORS.get(graph.category, "#94a3b8")
    n_calls = len(graph.upstream)
    n_consumers = len(graph.downstream)
    n_categories = len({e.other_category for e in graph.downstream})
    return tmpl.substitute(
        focus=graph.focus,
        subtitle=f"category: {graph.category} · source: {graph.source}",
        cat_color=cat_color,
        svg=svg,
        n_required=n_calls,         # "Calls" — template still uses these var names
        n_optional=n_consumers,     # "Consumers"
        n_consumers=n_categories,   # "Categories served"
        footer=f"Regenerate: python -m bootstrapper.docs.regen {graph.focus}",
    )


# ───── Internal helpers ──────────────────────────────────────────────────


def _cluster_by_category(edges: tuple[DepEdge, ...]) -> "OrderedDict[str, list[DepEdge]]":
    """Group edges by other_category. Preserves CATEGORY_ORDER ordering.
    Returns empty OrderedDict if edges is empty."""
    grouped: dict[str, list[DepEdge]] = {}
    for e in edges:
        grouped.setdefault(e.other_category, []).append(e)
    return OrderedDict(
        (cat, sorted(grouped[cat], key=lambda x: x.other))
        for cat in CATEGORY_ORDER
        if cat in grouped
    )


def _cluster_height(pills: list[DepEdge]) -> int:
    """Height of a cluster containing N pills (in 2-column packed grid)."""
    rows = (len(pills) + 1) // 2 if len(pills) > 1 else 1
    return CLUSTER_PADDING_Y + CLUSTER_HEADER_H + rows * (PILL_H + PILL_GAP) + CLUSTER_PADDING_Y


def _clusters_height(clusters: "OrderedDict[str, list[DepEdge]]") -> int:
    if not clusters:
        return 80  # empty-placeholder height
    return sum(_cluster_height(pills) for pills in clusters.values()) + (len(clusters) - 1) * CLUSTER_GAP


def _defs(graph: DepGraph) -> str:
    focus_color = CATEGORY_COLORS.get(graph.category, "#94a3b8")
    return f"""<defs>
  <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
    <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/>
  </pattern>
  <marker id="arrowhead" markerWidth="9" markerHeight="6" refX="8" refY="3" orient="auto">
    <polygon points="0 0, 9 3, 0 6" fill="#64748b"/>
  </marker>
  <filter id="focus-glow" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="6"/>
    <feFlood flood-color="{focus_color}" flood-opacity="0.6"/>
    <feComposite in2="SourceAlpha" operator="in"/>
    <feMerge>
      <feMergeNode/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
</defs>"""


def _focus_box(x: int, y: int, w: int, h: int, graph: DepGraph) -> str:
    color = CATEGORY_COLORS.get(graph.category, "#94a3b8")
    fill = CATEGORY_FILLS.get(graph.category, "rgba(30, 41, 59, 0.5)")
    cx = x + w // 2
    # Two-rect pattern (per the architecture-diagram skill): opaque
    # slate-900 backdrop, then the category-themed semi-transparent fill on
    # top so any arrow drawn behind the box is fully masked.
    return (
        f'<g class="focus" filter="url(#focus-glow)">'
        f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#0f172a"/>'
        f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
        f'        fill="{fill}" stroke="{color}" stroke-width="1.5"/>'
        f'  <text x="{cx}" y="{y + 28}" fill="white" font-size="15" font-weight="700" '
        f'        text-anchor="middle">{html_mod.escape(graph.focus.upper())}</text>'
        f'  <text x="{cx}" y="{y + 48}" fill="#94a3b8" font-size="10" '
        f'        text-anchor="middle">{html_mod.escape(graph.category)} · {html_mod.escape(graph.source)}</text>'
        f'</g>'
    )


def _render_lane(x: int, y: int, w: int, clusters: "OrderedDict[str, list[DepEdge]]", direction: str) -> str:
    if not clusters:
        # Empty placeholder
        return (
            f'<g><rect x="{x}" y="{y + 20}" width="{w}" height="60" rx="6" '
            f'fill="none" stroke="#1e293b" stroke-width="1" stroke-dasharray="3,3"/>'
            f'<text x="{x + w // 2}" y="{y + 56}" fill="#475569" font-size="10" '
            f'font-style="italic" text-anchor="middle">— none —</text></g>'
        )

    parts: list[str] = ['<g>']
    cy = y
    cluster_tmpl = Template((TEMPLATE_DIR / "cluster.tmpl").read_text(encoding="utf-8"))
    for cat, pills in clusters.items():
        ch = _cluster_height(pills)
        color = CATEGORY_COLORS.get(cat, "#94a3b8")
        fill = CATEGORY_FILLS.get(cat, "rgba(30, 41, 59, 0.5)")
        parts.append(cluster_tmpl.substitute(
            x=x, y=cy, w=w, h=ch,
            stroke=color,
            header_x=x + 10, header_y=cy + 14,
            count_x=x + w - 10,
            category=html_mod.escape(cat),
            count=str(len(pills)),
        ))
        # Pills inside the cluster
        pill_top = cy + CLUSTER_PADDING_Y + CLUSTER_HEADER_H
        pill_w = (w - 24) // 2
        for i, p in enumerate(pills):
            row = i // 2
            col = i % 2
            px = x + 8 + col * (pill_w + 4)
            py = pill_top + row * (PILL_H + PILL_GAP)
            parts.append(_pill(px, py, pill_w, PILL_H, p.other, color, fill))
        cy += ch + CLUSTER_GAP

    parts.append('</g>')
    return "\n".join(parts)


def _pill(x: int, y: int, w: int, h: int, label: str, stroke: str, fill: str) -> str:
    """Category-themed pill: opaque slate-900 backdrop + semi-transparent
    fill matching the cluster's category, per the architecture-diagram
    skill's component-box pattern.
    """
    return (
        f'<g><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="#0f172a"/>'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
        f'<text x="{x + w // 2}" y="{y + h // 2 + 4}" fill="white" font-size="10" '
        f'text-anchor="middle">{html_mod.escape(label)}</text></g>'
    )


def _edges(graph: DepGraph, up_clusters: "OrderedDict[str, list[DepEdge]]",
          down_clusters: "OrderedDict[str, list[DepEdge]]", body_height: int) -> list[str]:
    """One edge per cluster. Edge connects focus side to cluster header."""
    parts: list[str] = []
    focus_x = 60 + LANE_W + LANE_GAP
    focus_y_center = LANE_TOP_Y + body_height // 2

    # Upstream: cluster → focus (arrow points right)
    cy = LANE_TOP_Y
    for cat, pills in up_clusters.items():
        ch = _cluster_height(pills)
        bidirectional = any(p.bidirectional for p in pills)
        x1 = 60 + LANE_W
        y1 = cy + CLUSTER_PADDING_Y + CLUSTER_HEADER_H // 2
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{focus_x}" y2="{focus_y_center}" '
            f'stroke="#64748b" stroke-width="1.5" marker-end="url(#arrowhead)"/>'
        )
        if bidirectional:
            parts.append(
                f'<line x1="{focus_x}" y1="{focus_y_center + 6}" x2="{x1}" y2="{y1 + 6}" '
                f'stroke="#64748b" stroke-width="1.5" marker-end="url(#arrowhead)"/>'
            )
            parts.append(
                f'<text x="{(x1 + focus_x) // 2}" y="{(y1 + focus_y_center) // 2 - 4}" '
                f'fill="#94a3b8" font-size="9" text-anchor="middle">two-way</text>'
            )
        cy += ch + CLUSTER_GAP

    # Downstream: focus → cluster (arrow points right)
    down_x = focus_x + FOCUS_W + LANE_GAP
    cy = LANE_TOP_Y
    for cat, pills in down_clusters.items():
        ch = _cluster_height(pills)
        x2 = down_x
        y2 = cy + CLUSTER_PADDING_Y + CLUSTER_HEADER_H // 2
        parts.append(
            f'<line x1="{focus_x + FOCUS_W}" y1="{focus_y_center}" x2="{x2}" y2="{y2}" '
            f'stroke="#64748b" stroke-width="1.5" marker-end="url(#arrowhead)"/>'
        )
        cy += ch + CLUSTER_GAP

    return parts


def _legend(cx: int, y: int) -> str:
    # Legend draws from CATEGORY_COLORS so it stays in sync with the
    # cluster strokes + the rest of the stack's category palette.
    items = [(CATEGORY_COLORS[c], c) for c in
             ("infra", "data", "llm", "media", "agents", "apps")]
    item_w = 80
    total_w = item_w * len(items)
    start_x = cx - total_w // 2
    parts = [f'<g class="legend"><line x1="{start_x - 60}" y1="{y - 4}" x2="{cx + total_w // 2 + 60}" y2="{y - 4}" stroke="#1e293b" stroke-width="1"/>']
    for i, (color, name) in enumerate(items):
        ix = start_x + i * item_w
        parts.append(f'<circle cx="{ix + 6}" cy="{y + 8}" r="4" fill="{color}"/>')
        parts.append(_text(ix + 16, y + 11, name, size=9, color="#94a3b8", anchor="start"))
    parts.append('</g>')
    return "\n".join(parts)


def _text(x: int, y: int, text: str, *,
          size: int = 11, weight: int = 400, color: str = "#fff",
          anchor: str = "start", letter_spacing: float = 0.0) -> str:
    ls = f' letter-spacing="{letter_spacing}em"' if letter_spacing else ""
    return (
        f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}"{ls}>'
        f'{html_mod.escape(text)}</text>'
    )
