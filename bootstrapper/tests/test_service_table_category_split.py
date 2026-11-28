"""Category-aware column split tests for ServiceTable.

The 2-column layout used to split the canonical-ordered row list at the
midpoint blindly — which broke the visual rule "same category = adjacent
rows" whenever the midpoint fell inside a category. The most visible
case was LLM Core (LiteLLM + LLM Engine) straddling the column gutter.

The split now aligns to the nearest category boundary.
"""

from __future__ import annotations

from ui.textual.widgets.service_table import ServiceRow, _category_aware_split


def _row(name: str, category: str) -> ServiceRow:
    return ServiceRow(name=name, category=category)


def test_split_aligns_to_category_boundary():
    """27 rows / 2 cols — the split point is the category boundary
    nearest the midpoint, not the literal half index."""
    rows = (
        [_row("kong", "infra")]
        + [_row(f"sup-{i}", "data") for i in range(7)]
        + [_row("redis", "data"), _row("neo4j", "data"),
           _row("silo", "data"), _row("weaviate", "data"),
           _row("clip", "data")]
        + [_row("litellm", "llm"), _row("ollama", "llm")]
        + [_row("comfy", "media"), _row("stt", "media"), _row("tts", "media"),
           _row("doc", "media"), _row("searx", "media")]
        + [_row("hermes", "agents"), _row("n8n", "agents"), _row("openclaw", "agents")]
        + [_row("backend", "apps"), _row("openwebui", "apps"),
           _row("jupyter", "apps"), _row("ldr", "apps")]
    )
    assert len(rows) == 27

    left, right = _category_aware_split(rows, cols=2)
    # The midpoint is 13.5; the boundary nearest is index 13 (end of data).
    assert len(left) == 13
    assert len(right) == 14

    # Categorical assertion: every category appears in exactly ONE column.
    left_cats = {r.category for r in left}
    right_cats = {r.category for r in right}
    assert not (left_cats & right_cats), (
        f"category split across columns: {left_cats & right_cats}"
    )

    # LiteLLM + LLM Engine are now adjacent at the top of the right column.
    assert right[0].name == "litellm"
    assert right[1].name == "ollama"


def test_split_falls_back_to_even_when_no_categories():
    """If no row has a category (or all share one), the split falls back
    to the legacy even split so layout still works."""
    rows = [_row(f"x-{i}", "") for i in range(6)]
    left, right = _category_aware_split(rows, cols=2)
    assert len(left) == 3
    assert len(right) == 3


def test_split_single_column_is_passthrough():
    rows = [_row(f"x-{i}", "data") for i in range(5)]
    (only,) = _category_aware_split(rows, cols=1)
    assert only == rows


def test_split_single_row_is_passthrough():
    rows = [_row("x", "data")]
    groups = _category_aware_split(rows, cols=2)
    assert groups == [rows]


def test_split_two_categories_only():
    """If there's exactly one internal boundary, that's the split — even
    if it's nowhere near the midpoint."""
    rows = (
        [_row(f"i-{i}", "infra") for i in range(2)]
        + [_row(f"d-{i}", "data") for i in range(8)]
    )
    left, right = _category_aware_split(rows, cols=2)
    assert {r.category for r in left} == {"infra"}
    assert {r.category for r in right} == {"data"}


def test_real_topology_split_keeps_llm_core_together():
    """End-to-end check against the real manifest set: LiteLLM and LLM
    Engine must end up in the same column."""
    from services.topology import get_topology

    t = get_topology()
    rows = [
        ServiceRow(name=r.display_name, category=r.category)
        for r in t.rows
    ]
    groups = _category_aware_split(rows, cols=2)

    def column_of(name: str) -> int:
        for ci, g in enumerate(groups):
            if any(r.name == name for r in g):
                return ci
        raise AssertionError(f"{name} not found in any column")

    assert column_of("LiteLLM") == column_of("LLM Engine")
