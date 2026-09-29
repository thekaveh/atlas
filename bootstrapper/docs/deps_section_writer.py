"""DepGraph → markdown deps section (simplified for data-flow model)."""

from __future__ import annotations

from .deps_resolver import DepEdge, DepGraph


def render_section(graph: DepGraph, position: int = 5) -> str:
    """Render the canonical 'Dependencies & Integrations' section.

    Output is byte-deterministic for the same DepGraph and ``position``. The
    Future-* subsections emit placeholders; the regen tool splices in any
    user-authored Future content found in the existing README.

    The ``position`` parameter is the top-level section number (e.g. 5 in
    ``## 5. Dependencies & Integrations``). Subsections are numbered
    ``position.1`` through ``position.6``. Defaults to 5, the canonical slot
    in the standardized README layout.
    """

    lines: list[str] = []
    lines.append(f"## {position}. Dependencies & Integrations")
    lines.append("")
    # Current — Upstream
    lines.append(f"### {position}.1. Current — Upstream (this service calls)")
    lines.append("")
    if graph.upstream:
        lines.extend(_table(graph.upstream))
    else:
        lines.append("_No upstream calls._")
    lines.append("")

    # Current — Downstream
    lines.append(f"### {position}.2. Current — Downstream (services that call this)")
    lines.append("")
    if graph.downstream:
        lines.extend(_table(graph.downstream))
    else:
        lines.append("_No downstream consumers._")
    lines.append("")

    # Diagram embed
    lines.append(f"### {position}.3. Architecture diagram")
    lines.append("")
    lines.append(f"![{graph.focus} architecture](./architecture.svg)")
    lines.append("")
    lines.append("[Open the full-size diagram](./architecture.html) for a full-screen view.")
    lines.append("")

    # Future-* placeholders
    for heading in (
        f"### {position}.4. Future — Missing pair integrations",
        f"### {position}.5. Future — Candidate new services",
        f"### {position}.6. Future — Unused features in this service",
    ):
        lines.append(heading)
        lines.append("")
        lines.append("_No high-confidence opportunities identified._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


_PLANNED_NOTE = "_Rows marked planned are documented or intended, not wired yet._"


def _table(edges: tuple[DepEdge, ...]) -> list[str]:
    """A Service/Category table. It gains a Status column only when an edge is
    optional or planned (#1273), so an all-current table renders as before."""
    qualified = any(e.status != "current" for e in edges)
    lines = (
        ["| Service | Category | Status |", "|---|---|---|"]
        if qualified
        else ["| Service | Category |", "|---|---|"]
    )
    if any(e.status == "planned" for e in edges):
        lines = [_PLANNED_NOTE, ""] + lines
    for e in edges:
        cells = [f"{e.other}{' ↔' if e.bidirectional else ''}", e.other_category]
        if qualified:
            status = f"{e.status}: {e.condition}" if e.condition else e.status
            cells.append(status.replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    return lines
