"""Tests for the curated allowlist (in-module) + bundled fallback (JSON)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.comfyui_library import (
    list_curated,
    list_fallback,
    ComfyUILibraryEntry,
    VALID_CATEGORIES,
    CATEGORY_TARGET_DIR,
)


_FALLBACK_PATH = Path(__file__).parent.parent / "utils" / "data" / "comfyui_catalog_fallback.json"


def test_curated_covers_noisy_categories():
    """Categories where HF search is noisy must have curated entries."""
    entries = list_curated()
    noisy = {"vae", "ipadapter", "instantid", "upscaler",
             "embedding", "clip", "motion_lora", "audio_model"}
    by_category: dict[str, list[ComfyUILibraryEntry]] = {}
    for e in entries:
        by_category.setdefault(e.category, []).append(e)
    for cat in noisy:
        # At least one curated entry per noisy category — the curated list is
        # the source of truth for these, since HF tags don't surface them well.
        assert cat in by_category and len(by_category[cat]) >= 1, \
            f"curated allowlist missing for {cat}"


def test_curated_all_have_valid_target_dir():
    # target_dir defaults to CATEGORY_TARGET_DIR[category]. The schema allows a
    # per-entry override when a loader reads a different directory than the logical
    # category (e.g. Hunyuan3D-2 is a mesh_model whose dit checkpoint ships to
    # `checkpoints` for ImageOnlyCheckpointLoader). Overrides are pinned by name so
    # a mislabeled entry still fails on every non-override entry.
    overrides = {"hunyuan3d-2": "checkpoints"}
    for e in list_curated():
        expected = overrides.get(e.name, CATEGORY_TARGET_DIR[e.category])
        assert e.target_dir == expected
        assert e.source == "curated"


def test_fallback_file_exists():
    assert _FALLBACK_PATH.is_file()


def test_fallback_loads_to_entries():
    entries = list_fallback()
    assert len(entries) >= 20, "fallback should have ~30 entries"
    for e in entries:
        assert isinstance(e, ComfyUILibraryEntry)
        assert e.source == "fallback"
        assert e.category in VALID_CATEGORIES


def test_fallback_covers_every_display_group():
    from utils.comfyui_library import CATEGORY_DISPLAY_GROUPS
    entries = list_fallback()
    by_category = {e.category for e in entries}
    for group, cats in CATEGORY_DISPLAY_GROUPS.items():
        if group == "All":
            continue
        assert by_category & cats, \
            f"fallback has no entries in display group {group}"


def test_fallback_json_is_valid_schema_v1():
    raw = json.loads(_FALLBACK_PATH.read_text())
    assert raw["schema_version"] == 1
    assert isinstance(raw["entries"], list)
    for d in raw["entries"]:
        for f in ("name", "family", "category", "size_gb", "url", "target_dir"):
            assert f in d, f"fallback entry missing {f}: {d.get('name')}"
