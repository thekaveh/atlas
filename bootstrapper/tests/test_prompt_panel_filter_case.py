"""Filter-chip visibility filter must match badges case-insensitively.

multiselect_filter_chips.py:132 lowercases every input tag, so the
ACTIVE chip key reaches ``_rebuild_visible`` already lowercased
(prompt_panel.py:972 → ``tag = (...).strip().lower()``). Badges on
options come from arbitrary upstream code paths that may emit Title-
Case strings — e.g. ComfyUI's group names "Image" / "Image-edit" /
"Video" / "Audio" / "3D" / "Custom". The line-975 ``if tag not in
opt.badges`` comparison was case-sensitive, so the lowercased active
tag never matched a Title-Case badge and the chip filtered every row
out. This test pins case-insensitive comparison as a
defense-in-depth guard regardless of what badges any future picker
emits.
"""
from __future__ import annotations

import pytest

from ui.textual.widgets.prompt_panel import FILTER_ALL_KEY, PromptOption, PromptPanel


class _FilterStub:
    """Stand-in for PromptPanel that exposes only the state
    ``_rebuild_visible`` reads. Mirrors _RebuildStub in
    test_prompt_panel_leaf_badges.py — _rebuild_visible is a pure
    list-builder over these attributes, so the stub keeps the test
    hermetic (no Textual event loop)."""

    _rebuild_visible = PromptPanel._rebuild_visible

    def __init__(self, options, *, active_tag: str):
        class _Step:
            pass
        self._step = _Step()
        self._step.options = options
        self._filter_tag = active_tag
        self._search_query = ""
        self._expanded = set()
        self._variant_cache = {}
        self._variant_loading = set()


@pytest.mark.parametrize("active_tag,badge_form,should_match", [
    # The widget emits an already-lowercased active tag. Badges may be
    # any case — the filter must normalize both sides before comparison.
    ("image",      "Image",      True),
    ("image-edit", "Image-edit", True),
    ("video",      "Video",      True),
    ("audio",      "Audio",      True),
    ("3d",         "3D",         True),
    # Sanity: a non-matching badge still doesn't match (no false positives).
    ("image",      "Video",      False),
])
def test_filter_chip_match_is_case_insensitive(active_tag, badge_form, should_match):
    """Repro for the post-PR-#20 wizard screenshot: every ComfyUI filter
    chip showed `0 / N` rows because the chip-active tag was lowercase
    while the group badge was Title-Case. After the defense-in-depth
    fix in prompt_panel.py the comparison normalizes case on both sides."""
    options = [
        PromptOption(value="m1", label="m1", hint="",
                     badges=[badge_form, "lora", "0.05GB"]),
    ]
    stub = _FilterStub(options, active_tag=active_tag)
    rows = stub._rebuild_visible()
    if should_match:
        assert len(rows) == 1, (
            f"active_tag={active_tag!r} badge={badge_form!r} → "
            f"expected 1 visible row, got {len(rows)}. "
            f"Filter comparison must be case-insensitive."
        )
    else:
        assert len(rows) == 0, (
            f"active_tag={active_tag!r} badge={badge_form!r} → "
            f"expected 0 visible rows (no false positives), got {len(rows)}."
        )


def _query_stub(options, query: str) -> _FilterStub:
    stub = _FilterStub(options, active_tag=FILTER_ALL_KEY)
    stub._search_query = query
    return stub


def _comfyui_family():
    """A ComfyUI family parent as comfyui_steps._family_option builds it:
    synthetic ``family:<root>`` value, member catalog names in ``sizes``
    and ``leaf_details``."""
    members = ("tencent--Hunyuan3D-2", "tencent--HunyuanImage-2.1")
    return PromptOption(
        value="family:Hunyuan", label="Hunyuan  ·  2 variants", hint="3d",
        badges=["3d"], sizes=members,
        leaf_details={name: (name, ("3d",)) for name in members},
    )


@pytest.mark.parametrize("query", ["hunyuan", "hunyuan3d", "image-2.1", "tencent"])
def test_name_search_finds_a_comfyui_family_by_member_name(query):
    """Typing a member's own catalog name, or part of it, keeps its family
    parent visible; the synthetic ``family:<root>`` value alone hid it."""
    rows = _query_stub([_comfyui_family()], query)._rebuild_visible()
    assert [r.parent_value for r in rows if r.kind == "parent"] == ["family:Hunyuan"]


def test_name_search_still_hides_a_non_matching_comfyui_family():
    assert _query_stub([_comfyui_family()], "trellis")._rebuild_visible() == []


def test_name_search_does_not_match_ollama_size_tags():
    """Ollama ``sizes`` are parameter counts, not names: ``8b`` must not
    pull in every family that offers an 8b variant."""
    ollama = PromptOption(value="qwen3", label="qwen3", hint="",
                          badges=["library"], sizes=("8b", "14b"))
    assert _query_stub([ollama], "8b")._rebuild_visible() == []
    rows = _query_stub([ollama], "qwen")._rebuild_visible()
    assert [r.parent_value for r in rows if r.kind == "parent"] == ["qwen3"]


def test_filter_chips_do_not_outlive_their_multiselect_step():
    """A model picker's chip row must not stay on later step kinds, where
    clicking a chip or pressing ``f`` does nothing."""
    import asyncio

    from textual.app import App, ComposeResult

    from ui.textual.widgets.prompt_panel import PromptStep

    panel = PromptPanel()

    class _App(App):
        def compose(self) -> ComposeResult:
            yield panel

    def _step(kind: str, **extra) -> PromptStep:
        return PromptStep(title=f"T-{kind}", step_index=1, step_total=1,
                          heading=kind, kind=kind, **extra)

    models = _step(
        "multiselect", filter_tags=("embedding", "thinking"),
        options=[PromptOption(value="qwen3", label="qwen3", badges=["thinking"])],
    )
    later = [
        _step("options", options=[PromptOption(value="a", label="A")],
              default_value="a"),
        _step("text"),
        _step("number", default_value="1"),
        _step("secret"),
    ]

    async def scenario():
        seen = []
        async with _App().run_test(size=(120, 40)) as pilot:
            panel.load_step(models)
            await pilot.pause()
            seen.append(("multiselect", len(panel._filter_slot.children),
                         panel._filter_chips is not None))
            for step in later:
                panel.load_step(models)
                await pilot.pause()
                panel.load_step(step)
                await pilot.pause()
                seen.append((step.kind, len(panel._filter_slot.children),
                             panel._filter_chips is not None))
        return seen

    seen = asyncio.run(scenario())
    assert seen[0] == ("multiselect", 1, True), "the models step shows its chips"
    assert seen[1:] == [(kind, 0, False) for kind in ("options", "text", "number", "secret")]
