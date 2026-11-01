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

The fix touched both loops, so the regression test runs both branches: the
fallback (empty detail cache) and the cached ollama.com detail page.
"""

from __future__ import annotations

import pytest

from ui.textual.widgets.prompt_panel import (
    FILTER_ALL_KEY,
    PromptOption,
    PromptPanel,
)
from utils.ollama_library import OllamaVariant


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


@pytest.mark.parametrize(
    "cache",
    [
        {},
        {"qwen3.6": [OllamaVariant("latest", "1GB", "8K", ("Text",), "today")]},
    ],
    ids=["fallback", "detail"],
)
def test_expanding_a_family_with_pulled_variants_keeps_later_options(cache):
    """`fallback` runs the listing-size loop; `detail` runs the detail-cache loop.

    In both, the pulled "8b" reaches the pulled-tag loop, which is where the
    shadowing rebound `tag`.
    """
    options = [
        _family("qwen3.6", pulled=frozenset({"8b"})),
        _family("llama3.1"),
        _family("mistral"),
        _family("gemma3"),
    ]
    panel = _PanelStub(options, expanded={"qwen3.6"})
    panel._variant_cache = dict(cache)

    rows = panel._rebuild_visible()
    parents = [r.parent_value or "" for r in rows if r.kind == "parent"]

    assert parents == ["qwen3.6", "llama3.1", "mistral", "gemma3"], (
        f"options after the expanded family vanished; parents seen: {parents}"
    )


def test_an_unexpanded_list_is_unaffected():
    options = [_family("qwen3.6", pulled=frozenset({"8b"})), _family("llama3.1")]
    panel = _PanelStub(options, expanded=set())

    parents = [r.parent_value or "" for r in panel._rebuild_visible() if r.kind == "parent"]
    assert {"qwen3.6", "llama3.1"} <= set(parents)
