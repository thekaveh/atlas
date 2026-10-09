"""Tests for _merged_comfyui_options() — the wizard step's option provider."""
from __future__ import annotations

from pathlib import Path

import pytest

from wizard.comfyui_steps import _merged_comfyui_options, build_comfyui_steps
from utils.comfyui_library import ComfyUILibraryEntry


def _entry(name: str, **over) -> ComfyUILibraryEntry:
    d = dict(
        name=name, family="X", category="checkpoint", size_gb=1.0,
        url=f"https://e.com/{name}.safetensors", sha256=None,
        target_dir="checkpoints", min_vram_gb=None, cpu_supported=True,
        requires_custom_node=(), popularity=10, source="curated", pulled=False,
    )
    d.update(over)
    return ComfyUILibraryEntry(**d)


def test_catalog_entries_appear_as_options():
    catalog = [_entry("sdxl-base-1.0"), _entry("sd15")]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(),
    )
    names = {o.value for o in options}
    assert {"sdxl-base-1.0", "sd15"} <= names


def test_pulled_flag_renders_badge():
    catalog = [_entry("a")]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names={"a"},
        default_selected=set(),
    )
    o = next(o for o in options if o.value == "a")
    # The pulled badge surfaces in hint or in a badges list — check both.
    rendered = (o.hint or "") + " " + " ".join(getattr(o, "badges", ()) or [])
    assert "[pulled]" in rendered


def test_sidecar_entries_in_custom_group():
    catalog = [_entry("a")]
    sidecar = [_entry("custom-x", source="custom")]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=sidecar, pulled_names=set(),
        default_selected=set(),
    )
    custom_opt = next(o for o in options if o.value == "custom-x")
    assert getattr(custom_opt, "group", None) == "Custom"


def test_default_selected_entries_pre_checked():
    catalog = [_entry("a"), _entry("b")]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected={"a"},
    )
    selected = {o.value for o in options if getattr(o, "checked", False)}
    assert selected == {"a"}


def test_unknown_default_selected_skipped_with_warning(capsys):
    catalog = [_entry("a")]
    _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected={"nonexistent"},
    )
    captured = capsys.readouterr()
    assert "nonexistent" in (captured.err + captured.out)


def test_requires_custom_node_renders_warning():
    catalog = [_entry("flux", requires_custom_node=("ComfyUI-GGUF",))]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(),
    )
    o = next(o for o in options if o.value == "flux")
    rendered = (o.hint or "") + " " + " ".join(getattr(o, "badges", ()) or [])
    assert "ComfyUI-GGUF" in rendered


def test_sorted_by_popularity():
    catalog = [_entry("low", popularity=1), _entry("hi", popularity=99)]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(),
    )
    values = [o.value for o in options]
    assert values.index("hi") < values.index("low")


def test_min_vram_badge_when_hardware_short():
    catalog = [_entry("big", min_vram_gb=12.0, cpu_supported=False)]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(), gpu_mem_gb=8.0,  # less than min
    )
    o = next(o for o in options if o.value == "big")
    rendered = (o.hint or "") + " " + " ".join(getattr(o, "badges", ()) or [])
    assert "12" in rendered  # the VRAM threshold appears somewhere


def test_cpu_only_badge_when_no_gpu():
    catalog = [_entry("gpu-only", cpu_supported=False)]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(), gpu_mem_gb=None,  # no GPU
    )
    o = next(o for o in options if o.value == "gpu-only")
    rendered = (o.hint or "") + " " + " ".join(getattr(o, "badges", ()) or [])
    assert "GPU" in rendered or "gpu" in rendered.lower()


def test_sidecar_name_colliding_with_catalog_dedups_sidecar_wins():
    """When catalog and sidecar both have an entry named X, only ONE row
    surfaces, and it's the sidecar's (source='custom'). This avoids the
    confusing UX of two rows with the same name but different family
    badges. The dedupe is keyed on `name`; insertion order in by_name
    puts sidecar last so sidecar wins.
    """
    catalog = [_entry("collide", source="curated", family="Curated")]
    sidecar = [_entry("collide", source="custom", family="MyCustom")]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=sidecar, pulled_names=set(),
        default_selected=set(),
    )
    matches = [o for o in options if o.value == "collide"]
    assert len(matches) == 1, "duplicate name should dedupe to 1 row"
    assert matches[0].group == "Custom"  # sidecar's group wins


# ── skip-predicate behavior ───────────────────────────────────────────
# Mirrors Ollama's picker: the model step shows for ALL non-disabled
# sources (container-cpu / container-gpu / localhost) and only skips
# when COMFYUI_SOURCE=disabled. Earlier versions wrongly skipped for
# localhost. See PR following #18.

def _picker_step(env_vars: dict):
    """Build the ComfyUI picker step and return it for skip-predicate inspection."""
    steps = build_comfyui_steps(env_vars=env_vars, warn=lambda _msg: None)
    assert len(steps) == 1, "build_comfyui_steps should return exactly one step"
    return steps[0]


@pytest.mark.parametrize("source,expected_skip", [
    ("container-cpu", False),
    ("container-gpu", False),
    ("localhost",     False),  # picker shows for localhost
    ("disabled",      True),   # only `disabled` skips
    ("",              True),   # treat empty as disabled (safe default)
])
def test_skip_predicate_mirrors_ollama_for_all_sources(source, expected_skip):
    """The wizard step skips ONLY for COMFYUI_SOURCE=disabled (or empty).
    For container/localhost sources the picker shows — same shape as
    Ollama's `_merged_ollama_options` showing for any source that starts
    with `ollama-`.
    """
    step = _picker_step(env_vars={"COMFYUI_SOURCE": source})
    assert step.skip_if_prev is not None, "step must declare a skip_if_prev predicate"
    # The predicate looks at the prior step's selection dict. Simulate
    # both lookup paths: by env-var key AND by step-title fallback.
    sel_by_env = {"COMFYUI_SOURCE": source}
    sel_by_title = {"ComfyUI  ·  source": source}
    sel_empty = {}
    for sel in (sel_by_env, sel_by_title, sel_empty):
        assert step.skip_if_prev(sel) is expected_skip, (
            f"skip_if_prev({sel!r}) returned wrong value for source={source!r}"
        )


# ─── Filter-chip badge case ──────────────────────────────────────────
# The filter-chip widget lowercases active-tag values
# (multiselect_filter_chips.py:132 → ``t.strip().lower()``) before
# comparing them against ``opt.badges`` for visibility filtering
# (prompt_panel.py:975 → ``if tag not in opt.badges``). The group-name
# badge added by _merged_comfyui_options must therefore be lowercase,
# or every non-ALL chip filters every row out — the symptom seen in
# the post-PR-#20 wizard screenshot. Mirrors Ollama's lowercase badge
# convention ("embedding", "thinking", "vision" — never "Embedding").

def test_group_badge_is_lowercase_for_filter_chip_matching():
    catalog = [
        _entry("img-x",   category="lora"),         # → Image group
        _entry("ctrl-y",  category="controlnet"),   # → Image-edit group
        _entry("vid-z",   category="video_model"),  # → Video group
        _entry("voice-a", category="voice_model"),  # → Audio group
        _entry("mesh-b",  category="mesh_model"),   # → 3D group
    ]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(),
    )
    badges_by_value = {o.value: list(o.badges) for o in options}
    expected_lower = {
        "img-x":   "image",
        "ctrl-y":  "image-edit",
        "vid-z":   "video",
        "voice-a": "audio",
        "mesh-b":  "3d",
    }
    for value, expected in expected_lower.items():
        assert badges_by_value[value][0] == expected, (
            f"{value}: first badge must be lowercase {expected!r} for filter-chip "
            f"matching; got {badges_by_value[value][0]!r}. "
            f"Mismatch → filter chip excludes every row."
        )


# ─── Family grouping (HF entries) ──────────────────────────────────
# Two or more HF entries sharing a leading-letters family root
# (TRELLIS, Pi, Hunyuan, …) collapse into one expandable parent row.
# civitai numeric IDs and curated entries always stay flat. A family
# of one stays flat. Each variant inside the parent's leaf_details
# carries its own size + badges so the panel renders the leaf rows
# with real per-variant data.

def _hf(name: str, **over) -> ComfyUILibraryEntry:
    return _entry(name, source="huggingface", **over)


def test_hf_entries_sharing_family_root_collapse_to_one_parent():
    catalog = [
        _hf("microsoft--TRELLIS-image-large", category="mesh_model"),
        _hf("microsoft--TRELLIS.2-4B",        category="mesh_model"),
        _hf("gqk--TRELLIS-image-large-fork",  category="mesh_model"),
    ]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(),
    )
    # One parent row covering all three TRELLIS variants.
    parents = [o for o in options if o.value.startswith("family:")]
    flats = [o for o in options if not o.value.startswith("family:")]
    assert len(parents) == 1, f"expected 1 family parent, got {len(parents)}"
    parent = parents[0]
    assert parent.value == "family:TRELLIS"
    assert set(parent.sizes) == {
        "microsoft--TRELLIS-image-large",
        "microsoft--TRELLIS.2-4B",
        "gqk--TRELLIS-image-large-fork",
    }
    # Each variant has its own leaf_details entry with badges.
    for name in parent.sizes:
        assert name in parent.leaf_details
        label, badges = parent.leaf_details[name]
        assert label == name
        assert badges, f"variant {name} should carry badges (group / category / size / …)"
    # No flat TRELLIS rows duplicating the parent.
    assert not any("TRELLIS" in f.value for f in flats)


def test_single_family_member_stays_flat():
    catalog = [_hf("solo--MyModel-1B", category="checkpoint")]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(),
    )
    # Singleton "family of 1" must stay as a flat row, not a parent.
    assert all(not o.value.startswith("family:") for o in options)
    assert any(o.value == "solo--MyModel-1B" for o in options)


def test_civitai_entries_never_group():
    catalog = [
        _entry("civitai-100", source="civitai", category="lora"),
        _entry("civitai-200", source="civitai", category="lora"),
        _entry("civitai-300", source="civitai", category="lora"),
    ]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(),
    )
    # civitai entries are independent IDs — never collapse.
    assert all(not o.value.startswith("family:") for o in options)
    assert len([o for o in options if o.value.startswith("civitai-")]) == 3


def test_curated_entries_never_group():
    catalog = [
        _entry("vae-ft-mse-840000",   source="curated", category="vae"),
        _entry("vae-some-other-thing", source="curated", category="vae"),
    ]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(),
    )
    # Curated entries are hand-picked — leave them flat for clarity.
    # Non-empty guard: a vacuous `all(...)` over [] would pass even if curated
    # entries silently vanished.
    assert len([o for o in options if o.value.startswith("vae-")]) == 2
    assert all(not o.value.startswith("family:") for o in options)


def test_family_parent_pre_checked_when_any_variant_in_default_selected():
    catalog = [
        _hf("microsoft--TRELLIS-image-large", category="mesh_model"),
        _hf("microsoft--TRELLIS.2-4B",        category="mesh_model"),
    ]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[],
        pulled_names=set(),
        default_selected={"microsoft--TRELLIS.2-4B"},
    )
    parent = next(o for o in options if o.value == "family:TRELLIS")
    assert parent.checked is True, (
        "family parent should aggregate-check when any variant is in defaults"
    )


def test_family_parent_first_badge_is_lowercase_group_for_filter_match():
    catalog = [
        _hf("yyfz233--Pi3",  category="mesh_model"),
        _hf("yyfz233--Pi3X", category="mesh_model"),
    ]
    options = _merged_comfyui_options(
        catalog=catalog, sidecar=[], pulled_names=set(),
        default_selected=set(),
    )
    parent = next(o for o in options if o.value == "family:Pi")
    # The lowercased group must remain the first badge so the filter
    # chips work on the parent row (same invariant as flat rows).
    assert parent.badges[0] == "3d", parent.badges
    # And the family root + variant count belong in the LABEL, not
    # duplicated as a badge — the panel renders the label as the
    # row's primary text and any badge would be visual noise.
    assert "Pi" in parent.label
    assert "2 variants" in parent.label


@pytest.mark.parametrize("env_source,selected_source,expected_skip", [
    # User enables ComfyUI in the wizard while .env still says disabled:
    # the picker MUST show (regression: env was consulted first, so the
    # user never saw the model picker for the service they just enabled).
    ("disabled", "container-gpu", False),
    # User disables ComfyUI in the wizard while .env still says container:
    # the picker MUST skip (regression: it showed, and wrote
    # COMFYUI_USER_MODELS for a disabled service).
    ("container-cpu", "disabled", True),
])
def test_skip_predicate_prefers_live_selection_over_stale_env(
    env_source, selected_source, expected_skip,
):
    step = _picker_step(env_vars={"COMFYUI_SOURCE": env_source})
    sel = {"ComfyUI  ·  source": selected_source}
    assert step.skip_if_prev(sel) is expected_skip


def test_a_remembered_selection_renders_as_a_checked_row(tmp_path, monkeypatch):
    """The step built rows from the live scrape only; a selection only the
    remembered file resolves got no row, and confirming the step removed it
    from COMFYUI_USER_MODELS (2026-10-08 run, cycle 3; gap in #1448)."""
    import dataclasses
    import json

    from utils import comfyui_library, comfyui_resolver

    remembered = _entry("owner--hf-model", source="huggingface")
    path = tmp_path / "selected-library-entries.json"
    path.write_text(json.dumps([dataclasses.asdict(remembered)]))
    monkeypatch.setattr(comfyui_resolver, "_default_remembered_path", lambda: path)
    monkeypatch.setattr(comfyui_library, "assemble_wizard_catalog", lambda: [_entry("civitai-1", source="civitai")])
    step = build_comfyui_steps(
        env_vars={"COMFYUI_SOURCE": "container-cpu", "COMFYUI_USER_MODELS": "owner--hf-model",
                  "COMFYUI_CUSTOM_MODELS_FILE": str(tmp_path / "none.yaml")},
        warn=lambda _msg: None,
    )[0]
    options = step.options_provider({})
    assert "owner--hf-model" in [o.value for o in options]
    # Pre-checked: the panel keeps a default only when it maps to a row.
    assert "owner--hf-model" in step.default_values


def test_an_unresolvable_saved_name_keeps_a_row_and_the_reason_is_shown(tmp_path, monkeypatch):
    """A saved name in neither the catalog, the sidecar nor the remembered
    file had no row, so Enter removed it from .env. A corrupt remembered
    file reported only to stderr, which the TUI discards (cycle 37)."""
    from utils import comfyui_library, comfyui_resolver

    path = tmp_path / "selected-library-entries.json"
    path.write_text("{not json")
    monkeypatch.setattr(comfyui_resolver, "_default_remembered_path", lambda: path)
    monkeypatch.setattr(comfyui_library, "assemble_wizard_catalog", lambda: [_entry("civitai-1", source="civitai")])
    warnings: list[str] = []
    step = build_comfyui_steps(
        env_vars={"COMFYUI_SOURCE": "container-cpu", "COMFYUI_USER_MODELS": "civitai-1,gone-model",
                  "COMFYUI_CUSTOM_MODELS_FILE": str(tmp_path / "none.yaml")},
        warn=warnings.append,
    )[0]
    options = step.options_provider({})
    values = [o.value for o in options]
    assert values.count("gone-model") == 1 and values.count("civitai-1") == 1
    assert "saved" in next(o for o in options if o.value == "gone-model").badges
    assert any("ignoring unreadable" in w for w in warnings), warnings
