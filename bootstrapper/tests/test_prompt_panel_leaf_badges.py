"""
Unit tests for the per-leaf ``[pulled]`` / ``[library]`` status badge
rendered under an expanded Ollama-family parent in the wizard.

Before the fix, ``_leaf_render_data`` ran inherited badges through
``_inherited_leaf_badges`` which strips status tags under the comment
*"every leaf of a [library] parent is library; the user already sees
that on the parent right above"*. That assumption is wrong: a family
like ``qwen3.8`` whose host has ``qwen3.8:custom-quant``
pulled but not ``qwen3.8:27b``/``35b``/etc. has mixed-status leaves.
The fix added a per-leaf status computation that consults
``opt.pulled_variants`` directly.

These tests exercise ``_leaf_render_data`` on a stub ``PromptPanel``
instance — the method only reads ``self._variant_cache`` and the
inputs we pass, so we don't need a full Textual app context. The same
behaviour is verified end-to-end against a live host Ollama in
``test_live_catalog_sync.py``.
"""

from __future__ import annotations

import pytest

from ui.textual.widgets.prompt_panel import (
    PromptOption,
    PromptPanel,
    _VisibleRow,
)


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


class _PanelStub:
    """Minimal stand-in for PromptPanel that satisfies ``_leaf_render_data``'s
    self-attribute reads. The real class wires Textual reactive
    plumbing in ``__init__`` that we don't need (and can't easily
    bring up in a headless unit test)."""
    _variant_cache: dict = {}

    # Bind the method so we can call it on our stub.
    _leaf_render_data = PromptPanel._leaf_render_data


@pytest.fixture
def panel():
    return _PanelStub()


def _qwen_parent_option(*, pulled_variants=frozenset()):
    """Build the qwen3.8 family parent PromptOption as the wizard's
    options_provider would produce it for an ollama-localhost run.
    ``pulled_variants`` carries the per-tag host state."""
    badges = ["thinking", "tools"]
    badges.insert(
        0, "pulled" if pulled_variants else "library",
    )
    return PromptOption(
        value="qwen3.8",
        label="qwen3.8",
        hint="frontier dense + MoE model — strong reasoning / coding",
        badges=badges,
        pulls=1_300_000,
        sizes=("27b", "35b"),
        pulled_variants=pulled_variants,
    )


def _leaf_row(tag):
    return _VisibleRow(
        kind="leaf", abs_idx=0,
        parent_value="qwen3.8", variant=tag,
    )


# ────────────────────────────────────────────────────────────────────────────
# Per-leaf status badge — the screenshot fix
# ────────────────────────────────────────────────────────────────────────────


def test_leaf_carrying_a_pulled_tag_renders_pulled_status(panel):
    """A variant whose tag is in ``opt.pulled_variants`` renders
    ``[pulled]`` even when the parent family is in the library."""
    opt = _qwen_parent_option(
        pulled_variants=frozenset({"custom-quant"}),
    )
    _, _, leaf_badges = panel._leaf_render_data(
        _leaf_row("custom-quant"), opt,
    )
    assert "pulled" in leaf_badges, (
        f"Leaf custom-quant is on the host (in pulled_variants) — "
        f"must render [pulled]. Got: {leaf_badges}"
    )
    assert "library" not in leaf_badges, (
        f"A leaf rendered as [pulled] must NOT also carry [library]. "
        f"Got: {leaf_badges}"
    )


def test_sibling_leaf_not_on_host_renders_library_status(panel):
    """A sibling variant of the same family whose tag is NOT in
    ``opt.pulled_variants`` must render ``[library]``, NOT the
    pulled status the family carries. This is the inverse of the
    above and the crux of the per-leaf bug: previously every leaf
    of a pulled family inherited ``[pulled]`` (after the family
    badge fix) which was equally wrong."""
    opt = _qwen_parent_option(
        pulled_variants=frozenset({"custom-quant"}),
    )
    _, _, leaf_badges = panel._leaf_render_data(_leaf_row("27b"), opt)
    assert "library" in leaf_badges, (
        f"Leaf 27b is NOT on the host — must render [library]. "
        f"Got: {leaf_badges}"
    )
    assert "pulled" not in leaf_badges


def test_mixed_status_within_one_family_is_independently_rendered(panel):
    """End-to-end: render every leaf under a family that has SOME
    pulled, SOME not. The pulled set ``{latest, custom-quant}``
    must light up exactly those two leaves; every other variant must
    stay ``[library]``."""
    opt = _qwen_parent_option(
        pulled_variants=frozenset({"latest", "custom-quant"}),
    )
    leaves_to_check = (
        ("latest", "pulled"),
        ("27b", "library"),
        ("35b", "library"),
        ("27b-coding-mxfp8", "library"),
        ("custom-quant", "pulled"),
        ("35b-a3b-coding-nvfp4", "library"),
    )
    for tag, expected in leaves_to_check:
        _, _, badges = panel._leaf_render_data(_leaf_row(tag), opt)
        assert expected in badges, (
            f"Leaf {tag!r} expected to render [{expected}]; got {badges}"
        )
        other = "library" if expected == "pulled" else "pulled"
        assert other not in badges, (
            f"Leaf {tag!r} should NOT carry [{other}] (mixed status "
            f"would confuse the picker). Got: {badges}"
        )


def test_leaf_carries_no_status_when_pulled_variants_is_empty(panel):
    """When the upstream Ollama isn't reachable (or this is a
    container-mode wizard run), the options_provider leaves
    ``pulled_variants`` empty. In that state ``_leaf_render_data``
    must NOT emit ``[pulled]`` or ``[library]`` on the leaf — the
    parent's aggregate status is the only signal and per-leaf
    status would be a wild guess."""
    opt = _qwen_parent_option(pulled_variants=frozenset())
    _, _, badges = panel._leaf_render_data(_leaf_row("27b"), opt)
    assert "pulled" not in badges
    assert "library" not in badges


# ────────────────────────────────────────────────────────────────────────────
# _rebuild_visible — pulled tag must appear as a leaf row even when it's
# absent from both the listing-page sizes and the detail-page variant
# cache. Previously, ``qwen3.8:custom-quant`` was silently checked
# but invisible because the fallback loop iterated ``(_LATEST_TAG, *sizes)``
# without consulting ``opt.pulled_variants``.
# ────────────────────────────────────────────────────────────────────────────


class _RebuildStub:
    """Minimal stand-in for PromptPanel exposing only the state that
    ``_rebuild_visible`` reads. As with ``_PanelStub`` we sidestep
    Textual's reactive plumbing — the method is a pure list-builder
    on top of these attributes."""

    _rebuild_visible = PromptPanel._rebuild_visible

    def __init__(self, step, *, expanded=frozenset(), cache=None, loading=frozenset()):
        self._step = step
        self._filter_tag = "all"
        self._search_query = ""
        self._expanded = set(expanded)
        self._variant_cache = dict(cache or {})
        self._variant_loading = set(loading)


def _fake_step(options):
    """Stand-in for PromptStep accepting whatever attributes the test
    setup needs. _rebuild_visible only reads ``options``."""
    class _Step:
        pass
    s = _Step()
    s.options = options
    return s


def test_rebuild_surfaces_pulled_tag_missing_from_listing_and_detail():
    """The original screenshot bug: a user pulled
    ``qwen3.8:custom-quant`` (a community/custom build that is
    NOT on ollama.com's listing or detail page). With the parent
    expanded and the detail-page cache empty, the fallback path used
    to iterate only ``(latest, 27b, 35b)`` and silently drop the
    custom tag — even though it was checked and would land in the
    catalog CSV. After the fix, the pulled tag is appended as a leaf
    row so the user can see what's on their host."""
    opt = _qwen_parent_option(
        pulled_variants=frozenset({"latest", "custom-quant"}),
    )
    stub = _RebuildStub(_fake_step([opt]), expanded={"qwen3.8"})
    rows = stub._rebuild_visible()
    leaves = [r.variant for r in rows if r.kind == "leaf"]
    assert "custom-quant" in leaves, (
        "Pulled tag absent from listing/detail page must still render "
        f"as a leaf row. Got leaves: {leaves}"
    )
    # Listing-page sizes still show too (so library siblings remain
    # available for selection).
    for canonical in ("27b", "35b"):
        assert canonical in leaves


def test_rebuild_dedupes_pulled_tag_that_overlaps_listing():
    """When a pulled tag is also in the listing-page sizes (e.g. the
    user pulled exactly ``qwen3.8:27b``), it must appear once, not
    twice, in the leaf list."""
    opt = _qwen_parent_option(pulled_variants=frozenset({"27b"}))
    stub = _RebuildStub(_fake_step([opt]), expanded={"qwen3.8"})
    rows = stub._rebuild_visible()
    leaves = [r.variant for r in rows if r.kind == "leaf"]
    assert leaves.count("27b") == 1, (
        f"27b should appear exactly once when listed AND pulled. "
        f"Got: {leaves}"
    )


def test_rebuild_uses_detail_cache_then_appends_unlisted_pulled_tags():
    """When the detail-page fetch has completed and populated the
    cache, the cache drives the leaf order — and any pulled tag NOT in
    the cache (still: community / custom builds) is appended so the
    host's real inventory is fully represented."""
    from ui.textual.widgets.prompt_panel import _VisibleRow  # noqa: F401
    from utils.ollama_library import OllamaVariant

    detail = [
        OllamaVariant(
            tag=t, size_label="—", context_label="256K",
            input_modalities=("Text",), updated="just now",
        )
        for t in ("latest", "27b", "35b", "27b-mlx", "35b-mlx")
    ]
    opt = _qwen_parent_option(
        pulled_variants=frozenset({"latest", "custom-quant"}),
    )
    stub = _RebuildStub(
        _fake_step([opt]),
        expanded={"qwen3.8"},
        cache={"qwen3.8": detail},
    )
    rows = stub._rebuild_visible()
    leaves = [r.variant for r in rows if r.kind == "leaf"]
    # Detail-page tags come first, in detail order.
    assert leaves[:5] == ["latest", "27b", "35b", "27b-mlx", "35b-mlx"], (
        f"Detail-page tags must drive leaf order. Got: {leaves}"
    )
    # Pulled tag missing from the detail page is appended.
    assert "custom-quant" in leaves[5:], (
        f"Custom pulled tag must still appear when detail page omits "
        f"it. Got: {leaves}"
    )


def test_rebuild_allows_expansion_when_only_pulled_variants_distinguish():
    """A family with a single listing-page size but a pulled custom
    variant must still be expandable. Before the fix the gate was
    ``len(opt.sizes) >= 2``, hiding the custom tag entirely."""
    opt = PromptOption(
        value="customfam",
        label="customfam",
        hint="",
        badges=["pulled"],
        pulls=0,
        sizes=("8b",),  # only one listing-page size — old gate would block
        pulled_variants=frozenset({"8b-q4_K_M-custom"}),
    )
    stub = _RebuildStub(_fake_step([opt]), expanded={"customfam"})
    rows = stub._rebuild_visible()
    leaves = [r.variant for r in rows if r.kind == "leaf"]
    assert "8b-q4_K_M-custom" in leaves, (
        f"Single-size family with a custom pulled tag must still be "
        f"expandable. Got leaves: {leaves}"
    )
