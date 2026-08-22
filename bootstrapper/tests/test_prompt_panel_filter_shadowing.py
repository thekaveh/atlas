"""Expanding one option must not remove the options below it.

`_rebuild_visible` binds `tag` to the ACTIVE FILTER CHIP and reads it, per
option, in the badge test at the top of its loop. Two inner loops that splice
in host-pulled Ollama variants reused `tag` as their loop target, so after the
first expanded family with a non-empty `pulled_variants` the name held a
variant string (e.g. "8b") — and every remaining option failed the badge match
and disappeared, while the chip row still read ALL.

Reachable on any `ollama-*` source: `pulled_variants` is populated from
`/api/tags`, and `_sort_key` floats pulled families to the top, so the first
family a user expands is exactly the one that triggers it.
"""

from __future__ import annotations

from ui.textual.widgets.prompt_panel import (
    FILTER_ALL_KEY,
    PromptOption,
    PromptPanel,
)


class _PanelStub:
    """Enough of PromptPanel for `_rebuild_visible`, without Textual."""

    _variant_cache: dict = {}
    _variant_loading: set = set()
    _search_query = ""
    _filter_tag = FILTER_ALL_KEY

    _rebuild_visible = PromptPanel._rebuild_visible

    def __init__(self, options, expanded):
        self._step = type("Step", (), {"options": options})()
        self._expanded = expanded
        self._variant_cache = {}
        self._variant_loading = set()


def _family(value, *, pulled=frozenset()):
    """A family parent as the ollama step builds it.

    `is_expandable` is `len(sizes) >= 2 or bool(pulled_variants)`, so give
    every family two sizes — that keeps expansion independent of whether the
    host has pulled anything, which is exactly the variable this test isolates.
    """
    return PromptOption(
        value=value,
        label=value,
        badges=["library"],
        sizes=("8b", "14b"),
        pulled_variants=pulled,
    )


def test_expanding_a_family_with_pulled_variants_keeps_later_options():
    options = [
        _family("qwen3.6", pulled=frozenset({"8b"})),
        _family("llama3.1"),
        _family("mistral"),
        _family("gemma3"),
    ]
    panel = _PanelStub(options, expanded={"qwen3.6"})

    rows = panel._rebuild_visible()
    parents = [r.parent_value or "" for r in rows if r.kind == "parent"]

    assert "llama3.1" in parents, (
        f"options after the expanded family vanished; parents seen: {parents}"
    )
    assert {"llama3.1", "mistral", "gemma3"} <= set(parents)


def test_the_active_filter_chip_survives_an_expansion():
    """The clobbered name is the filter key itself."""
    options = [_family("qwen3.6", pulled=frozenset({"8b", "14b"})), _family("llama3.1")]
    panel = _PanelStub(options, expanded={"qwen3.6"})

    panel._rebuild_visible()

    assert panel._filter_tag == FILTER_ALL_KEY


def test_a_pulled_leaf_carries_its_own_variant_not_the_filter_key():
    """The loop body used the same shadowed name for the leaf's variant."""
    options = [_family("qwen3.6", pulled=frozenset({"8b", "14b"}))]
    panel = _PanelStub(options, expanded={"qwen3.6"})

    leaves = [r for r in panel._rebuild_visible() if r.kind == "leaf"]
    variants = {r.variant for r in leaves}

    # "latest" is the synthetic head row the panel adds to every Ollama
    # expansion — legitimate. What must never appear is the filter key.
    assert variants <= {"8b", "14b", "latest"}, (
        f"a leaf carried a value that is not one of its variants: {variants}"
    )
    assert FILTER_ALL_KEY not in variants


def test_an_unexpanded_list_is_unaffected():
    options = [_family("qwen3.6", pulled=frozenset({"8b"})), _family("llama3.1")]
    panel = _PanelStub(options, expanded=set())

    parents = [r.parent_value or "" for r in panel._rebuild_visible() if r.kind == "parent"]
    assert {"qwen3.6", "llama3.1"} <= set(parents)
